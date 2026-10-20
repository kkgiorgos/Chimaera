// Standalone device validation. This is a transport microbenchmark, not a ROS
// end-to-end latency measurement. Build with the custom libm5 and -no-pie.
#include <gem5/chimaera_dma.h>
#include <gem5/m5ops.h>
#include <m5_mmap.h>

#include <chrono>
#include <cstring>
#include <fcntl.h>
#include <iostream>
#include <poll.h>
#include <sstream>
#include <stdexcept>
#include <string>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <thread>
#include <unistd.h>
#include <vector>

void require(bool condition, const char* error) {
    if (!condition) throw std::runtime_error(std::string(error) + ": " + std::strerror(errno));
}

int main(int argc, char** argv) {
    try {
        const std::string mode = argc > 1 ? argv[1] : "native";
        require(mode == "native" || mode == "timing" || mode == "kvm", "invalid mode");
        const bool native = mode == "native";
        int fd = -1;
        chdma_info info{CHDMA_ABI_VERSION, 4, 8, 8 * 1024 * 1024, CHDMA_PAGE_BYTES, 0};
        std::vector<void*> mappings;
        std::vector<unsigned char> native_tx, native_rx, native_intermediate;
        unsigned char *source, *destination;
        if (native) {
            native_tx.resize(info.slot_bytes);
            native_rx.resize(info.slot_bytes);
            native_intermediate.resize(info.slot_bytes);
            source = native_tx.data(); destination = native_rx.data();
        } else {
            if (argc > 2) {
                const auto module = open(argv[2], O_RDONLY | O_CLOEXEC);
                require(module >= 0, "open kernel module");
                // One RX/TX buffer suffices for serialized round-trip tests.
                // Keep correctness checks affordable under a detailed CPU;
                // production pool defaults and ownership semantics are intact.
                const auto loaded = syscall(SYS_finit_module, module,
                                            "receive_slots=1 transmit_slots=1", 0);
                const auto error = errno;
                close(module);
                errno = error;
                require(loaded == 0 || errno == EEXIST, "load kernel module");
            }
            m5op_addr = 0xffff0000;
            map_m5_mem();
            fd = open("/dev/chimaera-dma", O_RDWR | O_CLOEXEC | O_NONBLOCK);
            require(fd >= 0, "open DMA device");
            require(ioctl(fd, CHDMA_IOC_INFO, &info) == 0 && info.abi_version == CHDMA_ABI_VERSION,
                    "device ABI");
            for (unsigned slot = 0; slot < info.rx_slots + info.tx_slots; ++slot) {
                auto* memory = mmap(nullptr, info.slot_bytes, PROT_READ | PROT_WRITE, MAP_SHARED,
                                    fd, static_cast<off_t>(slot) * info.page_bytes);
                require(memory != MAP_FAILED, "map DMA buffer");
                mappings.push_back(memory);
            }
            auto second = open("/dev/chimaera-dma", O_RDWR | O_CLOEXEC | O_NONBLOCK);
            require(second < 0 && errno == EBUSY, "exclusive device ownership");
            chdma_submit invalid{info.rx_slots + info.tx_slots, 1};
            require(ioctl(fd, CHDMA_IOC_RX, &invalid) < 0 && errno == EINVAL,
                    "reject invalid RX slot");
            invalid = {info.rx_slots, info.slot_bytes + 1};
            require(ioctl(fd, CHDMA_IOC_TX, &invalid) < 0 && errno == EINVAL,
                    "reject oversized TX");
            auto* invalid_mapping = mmap(nullptr, info.slot_bytes - info.page_bytes,
                PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
            require(invalid_mapping == MAP_FAILED && errno == EINVAL, "reject partial mapping");
            source = static_cast<unsigned char*>(mappings[info.rx_slots]);
            destination = static_cast<unsigned char*>(mappings[0]);
        }
        for (unsigned i = 0; i < info.slot_bytes; ++i) source[i] = i % 251;
        auto post_rx = [&] {
            chdma_submit receive{0, info.slot_bytes};
            require(ioctl(fd, CHDMA_IOC_RX, &receive) == 0, "post RX buffer");
        };
        if (!native) {
            post_rx();
            m5_work_begin_addr(0, 0);
            // KVM can execute ahead of its workbegin exit. Never execute an
            // instruction m5 operation until the host confirms the CPU switch.
            for (;;) {
                char data[96]{};
                const auto bytes = m5_read_file_addr(data, 95, 0);
                if (std::string(data, bytes).find("CHIMAERA_TIME_ORIGIN_NS ") == 0) break;
                std::this_thread::sleep_for(std::chrono::milliseconds(1));
            }
        }
        auto now = [&]() -> std::uint64_t {
            if (!native) return mode == "timing" ? m5_rpns() : m5_rpns_addr();
            return std::chrono::duration_cast<std::chrono::nanoseconds>(
                std::chrono::steady_clock::now().time_since_epoch()).count();
        };
        std::ostringstream output;
        output << "{\"mode\":\"" << mode << "\",\"kernel_abi\":" << info.abi_version
               << ",\"slot_bytes\":" << info.slot_bytes << ",\"samples\":[";
        bool first = true;
        std::uint64_t sample_offset = 0;
        for (const unsigned bytes : {0U, 63U, 64U, 4096U, 4097U,
                                     640U * 480 * 3, 2U * 640 * 480 * 3,
                                     2U * 640 * 480 * 3 + 137,
                                     8U * 640 * 480 * 3}) {
            require(bytes + 16 <= info.slot_bytes, "buffer too small for image and guard validation");
            for (unsigned repeat = 0; repeat < 2; ++repeat) {
                if (!native) {
                    const auto stage = std::to_string(bytes) + " " + std::to_string(repeat) + "\n";
                    require(m5_write_file_addr(const_cast<char*>(stage.data()), stage.size(), 0,
                                              "chimaera-dma-stage.txt") == stage.size(), "export stage");
                }
                if (bytes) {
                    source[0] = repeat + 252;
                    source[bytes - 1] = repeat + 252;
                }
                // Dirty the adjacent RX cache line and verify a partial DMA
                // write preserves bytes beyond the submitted payload.
                std::memset(destination + bytes, 0xa5, 16);
                const auto start = now();
                if (native) {
                    std::memcpy(native_intermediate.data(), source, bytes);
                    std::memcpy(destination, native_intermediate.data(), bytes);
                } else {
                    chdma_submit transmit{info.rx_slots, bytes};
                    require(ioctl(fd, CHDMA_IOC_TX, &transmit) == 0, "submit TX");
                    bool received = false, transmitted = false;
                    while (!received || !transmitted) {
                        pollfd wait{fd, POLLIN, 0};
                        require(poll(&wait, 1, 10000) > 0 && !(wait.revents & (POLLERR | POLLHUP)),
                                "completion interrupt timeout/error");
                        chdma_completion done{};
                        const auto count = read(fd, &done, sizeof(done));
                        require(count == sizeof(done), "read completion");
                        require(!(done.events & CHDMA_IRQ_ERROR), "device error");
                        if (done.events & CHDMA_IRQ_RX) {
                            require(done.slot == 0 && done.length == bytes && !received,
                                    "RX completion cookie/length");
                            received = true;
                        }
                        if (done.events & CHDMA_IRQ_TX) {
                            require(done.slot == info.rx_slots && !transmitted, "TX completion cookie");
                            transmitted = true;
                        }
                    }
                }
                const auto end = now();
                require(std::memcmp(source, destination, bytes) == 0, "payload mismatch");
                for (unsigned i = 0; i < 16; ++i)
                    require(destination[bytes + i] == 0xa5, "DMA overwrote receive guard");
                if (!first) output << ',';
                first = false;
                output << "{\"bytes\":" << bytes << ",\"repeat\":" << repeat
                       << ",\"roundtrip_ns\":" << end - start << ",\"verified\":true}";
                if (!native) {
                    const auto sample = "{\"bytes\":" + std::to_string(bytes) +
                        ",\"repeat\":" + std::to_string(repeat) + ",\"roundtrip_ns\":" +
                        std::to_string(end - start) + ",\"verified\":true}\n";
                    require(m5_write_file_addr(const_cast<char*>(sample.data()), sample.size(),
                            sample_offset, "chimaera-dma-samples.jsonl") == sample.size(), "export sample");
                    sample_offset += sample.size();
                }
                if (bytes) {
                    source[0] = 0;
                    source[bytes - 1] = (bytes - 1) % 251;
                }
                if (!native) post_rx();
            }
        }
        if (!native) {
            auto stage = std::string("RESET\n");
            m5_write_file_addr(stage.data(), stage.size(), 0, "chimaera-dma-stage.txt");
            // Reset after a TX doorbell may race descriptor/payload DMA. The
            // reset completion must imply quiescence before unmapping/freeing.
            chdma_submit transmit{info.rx_slots, 640U * 480 * 3};
            require(ioctl(fd, CHDMA_IOC_TX, &transmit) == 0, "submit before reset");
            require(ioctl(fd, CHDMA_IOC_RESET) == 0, "reset in-flight transfer");
            for (auto* memory : mappings) require(munmap(memory, info.slot_bytes) == 0, "unmap DMA");
            require(close(fd) == 0, "close DMA device");
            stage = "REOPEN\n";
            m5_write_file_addr(stage.data(), stage.size(), 0, "chimaera-dma-stage.txt");
            // Reopen/reset verifies there are no stale cookies or page owners.
            fd = open("/dev/chimaera-dma", O_RDWR | O_CLOEXEC | O_NONBLOCK);
            require(fd >= 0, "reopen DMA device after reset");
            require(close(fd) == 0, "close reopened device");
        }
        output << "],\"integrity\":\"pass\",\"reset_reopen\":\"" << (native ? "n/a" : "pass") << "\"}\n";
        const auto result = output.str();
        std::cout << result << std::flush;
        if (!native) {
            require(m5_write_file_addr(const_cast<char*>(result.data()), result.size(), 0,
                                      "chimaera-dma-probe.json") == result.size(), "export result");
            m5_work_end_addr(0, 0);
        }
        return 0;
    } catch (const std::exception& error) {
        if (m5_mem) {
            const auto reason = std::string(error.what()) + '\n';
            m5_write_file_addr(const_cast<char*>(reason.data()), reason.size(), 0,
                               "chimaera-dma-error.txt");
        }
        std::cerr << "CHIMAERA_DMA_PROBE_FAILED " << error.what() << '\n' << std::flush;
        return 1;
    }
}
