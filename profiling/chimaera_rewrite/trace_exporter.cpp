// Diagnostic helper: export after the source window using gem5 time.
// Core runtime binaries remain unchanged. Run as root inside the guest.
#include <gem5/m5ops.h>
#include <m5_mmap.h>
#include <sys/inotify.h>
#include <sys/utsname.h>
#include <unistd.h>

#include <cstdint>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <iterator>
#include <stdexcept>
#include <string>

namespace {
void export_file(const char *source, const char *destination) {
    std::ifstream stream(source, std::ios::binary);
    if (!stream) throw std::runtime_error("Cannot open diagnostic file");
    std::string bytes((std::istreambuf_iterator<char>(stream)), {});
    if (bytes.empty()) throw std::runtime_error("Refusing an empty diagnostic export");
    size_t offset = 0;
    while (offset < bytes.size()) {
        const auto count = m5_write_file_addr(bytes.data() + offset,
            bytes.size() - offset, offset, destination);
        if (!count || count > bytes.size() - offset)
            throw std::runtime_error("Incomplete diagnostic export");
        offset += count;
    }
}
}

int main() {
    try {
        // Capture userspace versions before workbegin, outside the measured ROI.
        const int packages = std::system(
            "dpkg-query -W ros-humble-fastrtps ros-humble-rmw-fastrtps-cpp "
            "ros-humble-rclcpp > /dev/shm/chimaera-guest-packages.txt 2>&1");
        const int notify = inotify_init1(IN_CLOEXEC);
        if (notify < 0 || inotify_add_watch(notify, "/tmp", IN_MOVED_TO | IN_CLOSE_WRITE) < 0)
            throw std::runtime_error("Cannot watch simulation origin");
        uint64_t origin = 0;
        while (true) {
            std::ifstream stream("/tmp/chimaera-sim-time-origin.ns");
            if (stream >> origin) break;
            alignas(inotify_event) char events[4096];
            if (read(notify, events, sizeof(events)) <= 0)
                throw std::runtime_error("Origin notification failed");
        }
        close(notify);
        m5op_addr = 0xffff0000;
        map_m5_mem();
        uint64_t now;
        do {
            now = m5_rpns_addr();
            if (now < origin) throw std::runtime_error("Invalid simulation origin");
            if (now - origin < 700000000) usleep(1000);
        } while (now - origin < 700000000);
        export_file("/dev/shm/chimaera-image-trace.log", "chimaera-image-trace.log");
        export_file("/dev/shm/chimaera-guest-packages.txt", "chimaera-guest-packages.txt");
        utsname system{};
        if (uname(&system)) throw std::runtime_error("uname failed");
        {
            std::ofstream stream("/dev/shm/chimaera-trace-export.json");
            stream << "{\"origin_ns\":" << origin << ",\"export_elapsed_ns\":"
                   << now - origin << ",\"package_query_status\":" << packages
                   << ",\"guest_kernel\":\"" << system.release << "\"}\n";
        }
        export_file("/dev/shm/chimaera-trace-export.json", "chimaera-trace-export.json");
        unmap_m5_mem();
        return 0;
    } catch (const std::exception &error) {
        std::cerr << "Trace export failed: " << error.what() << '\n';
        return 1;
    }
}
