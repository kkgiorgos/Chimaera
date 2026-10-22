// Raw CDDS boundary regressions. This peer emulates the paused timing server;
// it never launches gem5 or a guest. Run only after the parent's firstcatch gate.
#include <chimaera/m5_host_controller.hpp>
#include <gem5/chimaera_mailbox.hh>
#include <gem5/chimaera_m5.h>

#include <array>
#include <atomic>
#include <cerrno>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <functional>
#include <poll.h>
#include <source_location>
#include <stdexcept>
#include <sys/socket.h>
#include <sys/un.h>
#include <thread>
#include <unistd.h>
#include <vector>

using namespace chimaera;
using Mailbox = chimaera_mailbox::Mailbox;
using namespace std::chrono_literals;
namespace {
void expect(bool value, std::source_location at = std::source_location::current()) {
    if (!value) throw std::runtime_error("m5 host regression at line " + std::to_string(at.line()));
}
template<class F> void rejects(F&& action) {
    bool rejected = false;
    try { action(); } catch (const std::runtime_error&) { rejected = true; }
    expect(rejected);
}
void word(Message& bytes, std::size_t offset, std::uint32_t value) {
    for (unsigned i = 0; i < 4; ++i) bytes[offset + i] = std::byte(value >> (8 * i));
}
Message frame(bool bulk, unsigned seed, std::size_t body_bytes = 20) {
    expect(body_bytes >= 20);
    Message bytes(52 + body_bytes);
    std::memcpy(bytes.data(), "CDDS", 4);
    bytes[4] = std::byte{1};
    bytes[5] = std::byte{3};
    // Source port deliberately suggests the opposite lane: only destination
    // port decides admission. Payload size is also independent of the lane.
    word(bytes, 8, bulk ? 111 : (111 | CHM5_BULK_PORT_BIT));
    bytes[12] = std::byte{1};
    word(bytes, 16, 42);
    word(bytes, 28, 222 | (bulk ? CHM5_BULK_PORT_BIT : 0));
    bytes[32] = std::byte{2};
    word(bytes, 36, 43);
    word(bytes, 48, body_bytes);
    std::memcpy(bytes.data() + 52, "RTPS", 4);
    for (std::size_t i = 56; i < bytes.size(); ++i)
        bytes[i] = std::byte((i + seed) % 251);
    return bytes;
}
std::optional<Message> read(Mailbox& ring) {
    Mailbox::View view{};
    if (!ring.peek(view)) return std::nullopt;
    Message result(reinterpret_cast<const std::byte*>(view.data),
                   reinterpret_cast<const std::byte*>(view.data) + view.size);
    ring.pop();
    return result;
}
void write(Mailbox& ring, const Message& bytes) { expect(ring.write(bytes.data(), bytes.size())); }

struct Directory {
    std::string path;
    Directory() {
        char name[] = "/tmp/chimaera-m5-host-XXXXXX";
        expect(::mkdtemp(name) != nullptr);
        path = name;
    }
    ~Directory() { std::filesystem::remove_all(path); }
};
struct Fd {
    int value;
    ~Fd() { if (value >= 0) ::close(value); }
};
// Every expected command has a bounded accept/read/write. Destruction cancels
// the peer even when an assertion fails before the script has completed.
class TimingPeer {
public:
    struct Exchange { std::string command; std::function<std::string()> reply; };
private:
    Fd listener_{::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0)};
    std::jthread worker_;
    std::exception_ptr failure_;
    std::atomic<unsigned> commands_{};
public:
    explicit TimingPeer(const std::string& path) {
        expect(listener_.value >= 0);
        sockaddr_un address{};
        address.sun_family = AF_UNIX;
        expect(path.size() < sizeof(address.sun_path));
        std::memcpy(address.sun_path, path.c_str(), path.size() + 1);
        expect(::bind(listener_.value, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == 0);
        expect(::listen(listener_.value, 4) == 0);
    }
    ~TimingPeer() { worker_.request_stop(); if (worker_.joinable()) worker_.join(); }
    unsigned commands() const { return commands_.load(); }
    void start(std::vector<Exchange> script) {
        worker_ = std::jthread([this, script = std::move(script)](std::stop_token stop) {
            try {
                for (const auto& exchange : script) {
                    const auto deadline = std::chrono::steady_clock::now() + 2s;
                    while (true) {
                        if (stop.stop_requested()) return;
                        expect(std::chrono::steady_clock::now() < deadline);
                        pollfd descriptor{listener_.value, POLLIN, 0};
                        const auto ready = ::poll(&descriptor, 1, 20);
                        if (ready < 0 && errno == EINTR) continue;
                        expect(ready >= 0);
                        if (ready) break;
                    }
                    Fd client{::accept(listener_.value, nullptr, nullptr)};
                    expect(client.value >= 0);
                    timeval limit{2, 0};
                    expect(::setsockopt(client.value, SOL_SOCKET, SO_RCVTIMEO, &limit, sizeof(limit)) == 0);
                    expect(::setsockopt(client.value, SOL_SOCKET, SO_SNDTIMEO, &limit, sizeof(limit)) == 0);
                    std::string command;
                    do {
                        char byte;
                        expect(command.size() < 128 && ::recv(client.value, &byte, 1, 0) == 1);
                        command += byte;
                    } while (command.back() != '\n');
                    expect(command == exchange.command + "\n");
                    ++commands_;
                    const auto response = exchange.reply() + "\n";
                    std::size_t offset = 0;
                    while (offset < response.size()) {
                        const auto sent = ::send(client.value, response.data() + offset,
                            response.size() - offset, MSG_NOSIGNAL);
                        expect(sent > 0);
                        offset += sent;
                    }
                }
            } catch (...) { failure_ = std::current_exception(); }
        });
    }
    void finish() { worker_.join(); if (failure_) std::rethrow_exception(failure_); }
};
struct Rings {
    std::array<std::unique_ptr<Mailbox>, 2> input, output;
    explicit Rings(const std::string& base) {
        for (unsigned i = 0; i < 2; ++i) {
            const std::string suffix = i ? ".bulk" : ".control";
            input[i] = std::make_unique<Mailbox>(base + "/h2g" + suffix, false, 16);
            output[i] = std::make_unique<Mailbox>(base + "/g2h" + suffix, false, 16);
        }
    }
    void empty() {
        for (unsigned i = 0; i < 2; ++i) expect(!read(*input[i]) && !read(*output[i]));
    }
};
TimingPeer::Exchange ingress(std::function<void()> check = [] {}) {
    return {"CHIMAERA_INGRESS", [check] { check(); return "OK INGRESS"; }};
}
TimingPeer::Exchange step(unsigned index) {
    return {"STEP_TICKS 2000000", [index] {
        return "OK " + std::to_string(index * 2000000ULL) + " " +
            std::to_string((index + 1) * 2000000ULL);
    }};
}
TimingPeer::Exchange egress() { return {"CHIMAERA_EGRESS", [] { return "EGRESS 0"; }}; }
TimingPeer::Exchange quit() { return {"QUIT", [] { return "BYE"; }}; }

void lane_pressure_and_pending_coalescing() {
    Directory directory;
    const auto& base = directory.path;
    Gem5TimingController timing(base + "/time", 2s);
    {
        M5HostController host(timing, base + "/h2g", base + "/g2h", 1);
        Rings rings(base);
        rings.empty(); // No startup envelope in any of the four rings.
        for (unsigned i = 0; i < 16; ++i) write(*rings.input[1], frame(true, i));
        host.submit(frame(true, 20), "state");
        // Bound replacement while the bulk mailbox is full; no unbounded
        // accumulation and the independent control queue still has its credit.
        for (unsigned i = 0; i < 4096; ++i) host.submit(frame(true, 21), "state");
        const auto control = frame(false, 71, 80 * 1024);
        TimingPeer peer(base + "/time");
        host.submit(control, "state"); // Same key must not coalesce across lanes.
        rejects([&] { host.submit(frame(true, 22)); });
        rejects([&] { host.submit(frame(false, 72)); });
        expect(!host.take() && !read(*rings.input[0])); // submit is not admission.
        expect(!host.step(Duration{}).ok());
        expect(!host.step(-1ns).ok());
        expect(!host.step(1h + 1ns).ok());
        expect(!read(*rings.input[0]) && peer.commands() == 0);
        peer.start({
            ingress([&] {
                expect(read(*rings.input[0]) == control && !read(*rings.input[0]));
                for (unsigned i = 0; i < 16; ++i) expect(read(*rings.input[1]) == frame(true, i));
                expect(!read(*rings.input[1]));
            }), step(0), egress(),
            ingress([&] {
                expect(read(*rings.input[1]) == frame(true, 21));
                expect(read(*rings.input[0]) == frame(false, 73));
                expect(!read(*rings.input[0]) && !read(*rings.input[1]));
            }), step(1), egress(), quit()
        });
        expect(host.step(2us).ok());
        host.submit(frame(false, 73));
        expect(host.step(2us).ok());
        expect(!host.take() && timing.elapsed_ticks() == 4000000);
        expect(host.stop().state == ControllerState::stopped);
        expect(host.stop().state == ControllerState::stopped);
        expect(host.step(2us).state == ControllerState::stopped);
        rejects([&] { host.submit(frame(false, 1)); });
        peer.finish();
        expect(peer.commands() == 7);
    }
    for (const auto* name : {"h2g.control", "h2g.bulk", "g2h.control", "g2h.bulk"})
        expect(!std::filesystem::exists(base + "/" + name));
}

void paused_egress_drains_all_and_owns_payloads() {
    Directory directory;
    const auto& base = directory.path;
    Gem5TimingController timing(base + "/time", 2s);
    M5HostController host(timing, base + "/h2g", base + "/g2h");
    Rings rings(base);
    unsigned control = 0, bulk = 0;
    auto flush = [&] {
        // A full lane must be drained between egress commands to make progress.
        while (control < 40) {
            auto bytes = frame(false, control);
            if (!rings.output[0]->write(bytes.data(), bytes.size())) break;
            ++control;
        }
        while (bulk < 20) {
            auto bytes = frame(true, bulk, 128 * 1024);
            if (!rings.output[1]->write(bytes.data(), bytes.size())) break;
            ++bulk;
        }
        return "EGRESS " + std::to_string(60 - control - bulk);
    };
    TimingPeer peer(base + "/time");
    peer.start({ingress(), step(0), {"CHIMAERA_EGRESS", flush},
        {"CHIMAERA_EGRESS", flush}, {"CHIMAERA_EGRESS", flush},
        ingress(), step(1), {"CHIMAERA_EGRESS", [&] {
            // Reuse every ring slot, including storage behind the held loans.
            for (unsigned i = 0; i < 16; ++i) {
                write(*rings.output[0], frame(false, i + 100));
                write(*rings.output[1], frame(true, i + 100, 128 * 1024));
            }
            return "EGRESS 0";
        }}, quit()});
    expect(!host.take() && host.step(2us).ok());
    std::vector<Message> held;
    while (auto bytes = host.take()) held.push_back(std::move(*bytes));
    expect(held.size() == 60);
    // Control queued on a later flush still precedes bulk from an earlier one;
    // each individual lane retains FIFO order without loss or duplication.
    for (unsigned i = 0; i < 40; ++i) expect(held[i] == frame(false, i));
    for (unsigned i = 0; i < 20; ++i) expect(held[40 + i] == frame(true, i, 128 * 1024));
    auto snapshots = held; // Deep copies expose aliasing after mailbox reuse.
    expect(host.step(2us).ok());
    for (unsigned lane = 0; lane < 2; ++lane)
        for (unsigned i = 0; i < 16; ++i)
            expect(host.take() == frame(lane, i + 100, lane ? 128 * 1024 : 20));
    expect(!host.take() && held == snapshots);
    expect(host.stop().state == ControllerState::stopped);
    peer.finish();
}

void rejects_bad_submissions_without_poisoning_queue() {
    Directory directory;
    const auto& base = directory.path;
    Gem5TimingController timing(base + "/time", 2s);
    M5HostController host(timing, base + "/h2g", base + "/g2h", 1);
    Rings rings(base);
    TimingPeer peer(base + "/time");
    const auto valid = frame(false, 1);
    auto truncated = valid;
    truncated.resize(71);
    rejects([&] { host.submit(truncated); });
    for (const auto offset : {0, 4, 5, 6, 7, 48, 52}) {
        auto bad = valid;
        bad[offset] ^= std::byte{0x7f};
        rejects([&] { host.submit(std::move(bad)); });
    }
    host.submit(valid); // Invalid input must neither occupy nor poison the queue.
    rings.empty();
    peer.start({ingress([&] {
        expect(read(*rings.input[0]) == valid && !read(*rings.input[0]));
        expect(!read(*rings.input[1]));
    }), step(0), egress(), quit()});
    expect(host.step(2us).ok());
    expect(host.stop().state == ControllerState::stopped);
    peer.finish();
}

void four_image_burst_exceeds_old_shared_byte_limit() {
    Directory directory;
    const auto& base = directory.path;
    Gem5TimingController timing(base + "/time", 2s);
    M5HostController host(timing, base + "/h2g", base + "/g2h");
    Rings rings(base);
    constexpr std::size_t image_bytes = 8 * 1024 * 1024;
    TimingPeer peer(base + "/time");
    // Four payloads exactly fill 32 MiB before accounting for CDDS headers.
    // Both ingress and egress must accept the entire framed burst.
    for (unsigned i = 0; i < 4; ++i) host.submit(frame(true, i, image_bytes));
    host.submit(frame(false, 9));
    peer.start({ingress([&] {
        expect(read(*rings.input[0]) == frame(false, 9));
        for (unsigned i = 0; i < 4; ++i)
            expect(read(*rings.input[1]) == frame(true, i, image_bytes));
        expect(!read(*rings.input[0]) && !read(*rings.input[1]));
    }), step(0), {"CHIMAERA_EGRESS", [&] {
        for (unsigned i = 0; i < 4; ++i) write(*rings.output[1], frame(true, i + 10, image_bytes));
        write(*rings.output[0], frame(false, 10));
        return "EGRESS 0";
    }}, quit()});
    expect(host.step(2us).ok());
    expect(host.take() == frame(false, 10));
    for (unsigned i = 0; i < 4; ++i)
        expect(host.take() == frame(true, i + 10, image_bytes));
    expect(!host.take());
    expect(host.stop().state == ControllerState::stopped);
    peer.finish();
}

void capacity_pressure_retains_records_and_other_lane_progress() {
    Directory directory;
    const auto& base = directory.path;
    Gem5TimingController timing(base + "/time", 2s);
    // Use the public count limit to exercise recoverable capacity pressure
    // without allocating over 640 MiB just to exhaust the per-lane byte limit.
    M5HostController host(timing, base + "/h2g", base + "/g2h", 2);
    Rings rings(base);
    TimingPeer peer(base + "/time");
    peer.start({ingress(), step(0), {"CHIMAERA_EGRESS", [&] {
        write(*rings.output[0], frame(false, 1));
        write(*rings.output[1], frame(true, 1));
        return "EGRESS 0";
    }}, ingress(), step(1), {"CHIMAERA_EGRESS", [&] {
        write(*rings.output[0], frame(false, 2));
        write(*rings.output[0], frame(false, 3));
        write(*rings.output[1], frame(true, 2));
        return "EGRESS 0";
    }}, ingress(), step(2), {"CHIMAERA_EGRESS", [&] {
        write(*rings.output[0], frame(false, 4));
        write(*rings.output[1], frame(true, 3));
        return "EGRESS 0";
    }}, ingress(), step(3), egress(), quit()});
    expect(host.step(2us).ok());
    expect(host.step(2us).ok());
    std::vector<Message> delivered;
    auto control_only = [&](const Message& bytes) {
        if ((bytes[31] & std::byte{0x40}) != std::byte{}) return false;
        delivered.push_back(bytes);
        return true;
    };
    expect(host.transfer_received(control_only) == 2);
    expect(delivered == std::vector<Message>({frame(false, 1), frame(false, 2)}));
    // The blocked bulk receiver retains both owned records; the full control
    // queue left its third accepted record in the ring until credits returned.
    expect(host.step(2us).ok());
    expect(host.transfer_received(control_only) == 2);
    expect(delivered == std::vector<Message>({
        frame(false, 1), frame(false, 2), frame(false, 3), frame(false, 4)}));
    Mailbox::View retained{};
    expect(rings.output[1]->peek(retained) && retained.size == frame(true, 3).size());
    expect(host.take() == frame(true, 1));
    expect(host.take() == frame(true, 2));
    expect(!host.take());
    expect(host.step(2us).ok());
    expect(host.take() == frame(true, 3));
    expect(!host.take() && !read(*rings.output[0]) && !read(*rings.output[1]));
    expect(host.stop().state == ControllerState::stopped);
    peer.finish();
}

void recoverable_outgoing_credits_are_independent() {
    Directory directory;
    const auto& base = directory.path;
    Gem5TimingController timing(base + "/time", 2s);
    M5HostController host(timing, base + "/h2g", base + "/g2h", 1);
    Rings rings(base);
    TimingPeer peer(base + "/time");
    const auto held = frame(true, 2);
    expect(host.try_submit(frame(true, 1)));
    expect(!host.try_submit(held) && held == frame(true, 2));
    expect(host.try_submit(frame(false, 1)));
    expect(!host.try_submit(frame(false, 2)));
    peer.start({ingress([&] {
        expect(read(*rings.input[0]) == frame(false, 1));
        expect(read(*rings.input[1]) == frame(true, 1));
        expect(!read(*rings.input[0]) && !read(*rings.input[1]));
    }), step(0), egress(), ingress([&] {
        expect(read(*rings.input[1]) == held && !read(*rings.input[1]));
        expect(!read(*rings.input[0]));
    }), step(1), egress(), quit()});
    expect(host.step(2us).ok());
    expect(host.try_submit(held));
    expect(host.step(2us).ok());
    expect(host.stop().state == ControllerState::stopped);
    peer.finish();
}

void shared_receive_view_survives_refusal_and_controller_destruction() {
    Directory directory;
    const auto& base = directory.path;
    const auto expected = frame(true, 17, 128 * 1024);
    Message retained;
    const std::byte* first = nullptr;
    {
        Gem5TimingController timing(base + "/time", 2s);
        M5HostController host(timing, base + "/h2g", base + "/g2h");
        Rings rings(base);
        TimingPeer peer(base + "/time");
        peer.start({ingress(), step(0), {"CHIMAERA_EGRESS", [&] {
            write(*rings.output[1], expected);
            return "EGRESS 0";
        }}, quit()});
        expect(host.step(2us).ok());
        expect(host.transfer_received([&](Message view) {
            first = view.data();
            retained = std::move(view);
            return false;
        }) == 0);
        expect(retained == expected);
        expect(host.transfer_received([&](Message view) {
            expect(view.data() == first); // Retry shares the original allocation.
            retained = std::move(view);
            return true;
        }) == 1);
        expect(!host.take());
        expect(host.stop().state == ControllerState::stopped);
        peer.finish();
    }
    expect(retained.data() == first && retained == expected);
}

void persistent_output_failure(Message bad, unsigned wrong_ring) {
    Directory directory;
    const auto& base = directory.path;
    Gem5TimingController timing(base + "/time", 2s);
    M5HostController host(timing, base + "/h2g", base + "/g2h");
    Rings rings(base);
    TimingPeer peer(base + "/time");
    peer.start({ingress(), step(0), {"CHIMAERA_EGRESS", [&] {
        write(*rings.output[0], frame(false, 1));
        write(*rings.output[wrong_ring], bad);
        return "EGRESS 0";
    }}, quit()});
    const auto result = host.step(2us);
    expect(result.state == ControllerState::failed && !result.message.empty());
    // Already accepted records remain owned even if a following record fails.
    expect(host.take() == frame(false, 1) && !host.take());
    Mailbox::View retained{};
    expect(rings.output[wrong_ring]->peek(retained) && retained.size == bad.size());
    expect(!std::memcmp(retained.data, bad.data(), bad.size()));
    const auto count = peer.commands();
    const auto again = host.step(2us);
    expect(again.state == ControllerState::failed && again.message == result.message);
    rejects([&] { host.submit(frame(false, 2)); });
    expect(peer.commands() == count); // No simulator progress after failure.
    expect(host.stop().state == ControllerState::stopped);
    peer.finish();
}

void rejected_boundary_commands() {
    for (const auto& response : {"OK", "EGRESS", "OTHER 0", "EGRESS 0 trailing"}) {
        Directory directory;
        const auto& base = directory.path;
        TimingPeer peer(base + "/time");
        Gem5TimingController timing(base + "/time", 2s);
        M5HostController host(timing, base + "/h2g", base + "/g2h");
        peer.start({ingress(), step(0), {"CHIMAERA_EGRESS", [=] { return response; }}, quit()});
        expect(host.step(2us).state == ControllerState::failed);
        expect(host.step(2us).state == ControllerState::failed);
        expect(host.stop().state == ControllerState::stopped);
        peer.finish();
    }
    Directory directory;
    const auto& base = directory.path;
    TimingPeer peer(base + "/time");
    Gem5TimingController timing(base + "/time", 2s);
    M5HostController host(timing, base + "/h2g", base + "/g2h");
    peer.start({{"CHIMAERA_INGRESS", [] { return "REJECTED"; }}, quit()});
    expect(host.step(2us).state == ControllerState::failed);
    expect(host.stop().state == ControllerState::stopped); // No STEP_TICKS allowed.
    peer.finish();
}

void nonprogressing_egress_is_bounded() {
    Directory directory;
    const auto& base = directory.path;
    Gem5TimingController timing(base + "/time", 2s);
    M5HostController host(timing, base + "/h2g", base + "/g2h");
    TimingPeer peer(base + "/time");
    std::vector<TimingPeer::Exchange> script{ingress(), step(0)};
    // A corrupt peer cannot hold step() forever by claiming that output remains
    // while never publishing a frame. No simulation or wall-clock sleep here.
    for (unsigned i = 0; i < 1024; ++i)
        script.push_back({"CHIMAERA_EGRESS", [] { return "EGRESS 1"; }});
    script.push_back(quit());
    peer.start(std::move(script));
    const auto result = host.step(2us);
    expect(result.state == ControllerState::failed);
    expect(result.message == "m5ops paused egress failed to drain");
    expect(host.step(2us).message == result.message && !host.take());
    expect(host.stop().state == ControllerState::stopped);
    peer.finish();
}
} // namespace

int main() {
    lane_pressure_and_pending_coalescing();
    paused_egress_drains_all_and_owns_payloads();
    rejects_bad_submissions_without_poisoning_queue();
    four_image_burst_exceeds_old_shared_byte_limit();
    capacity_pressure_retains_records_and_other_lane_progress();
    recoverable_outgoing_credits_are_independent();
    shared_receive_view_survives_refusal_and_controller_destruction();
    auto bad = frame(false, 1);
    bad[48] ^= std::byte{1};
    persistent_output_failure(std::move(bad), 0);
    bad = frame(false, 1);
    bad.resize(71);
    persistent_output_failure(std::move(bad), 0);
    bad = frame(false, 1);
    bad[52] = std::byte{};
    persistent_output_failure(std::move(bad), 0);
    persistent_output_failure(frame(true, 1), 0);
    persistent_output_failure(frame(false, 1), 1);
    rejected_boundary_commands();
    nonprogressing_egress_is_bounded();
}
