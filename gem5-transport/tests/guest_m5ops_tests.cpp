// Link against fake m5ops so backend selection can be tested on the host.
#include <chimaera/gem5_controller.hpp>
#include <gem5/m5ops.h>
#include <m5_mmap.h>
#include "../src/controller_protocol.hpp"
#include "../src/framing.hpp"
#include <cstring>
#include <deque>
#include <stdexcept>
#include <time.h>

using namespace chimaera;
extern "C" { void* m5_mem = nullptr; }
namespace {
std::deque<Message> received;
std::vector<bool> calls;
std::vector<Message> sent;
struct Wait { Duration duration; std::size_t sent_frames; std::size_t remaining_frames; };
std::vector<Wait> waits;
bool short_send = false;
void expect(bool condition) { if (!condition) throw std::runtime_error("m5ops test failed"); }
std::uint64_t send(bool instruction, void* buffer, std::uint64_t size) {
    calls.push_back(instruction);
    sent.emplace_back(static_cast<std::byte*>(buffer), static_cast<std::byte*>(buffer) + size);
    return short_send ? size - 1 : size;
}
std::uint64_t recv(bool instruction, void* buffer, std::uint64_t size) {
    calls.push_back(instruction);
    expect(!received.empty() && received.front().size() == size);
    std::memcpy(buffer, received.front().data(), size);
    received.pop_front();
    return size;
}
void enqueue(Message data) {
    auto header = detail::encode(data.size());
    received.emplace_back(header.begin(), header.end());
    if (!data.empty()) received.push_back(std::move(data));
}

}
extern "C" {
// Capture guest waits without host wall-clock timing assertions. The snapshots
// distinguish sleeping before the next send from sleeping after a reply.
int __wrap_nanosleep(const struct timespec* requested, struct timespec*) {
    waits.push_back({std::chrono::seconds(requested->tv_sec) +
                     std::chrono::nanoseconds(requested->tv_nsec),
                     sent.size(), received.size()});
    return 0;
}
uint64_t m5_chimaera_send(void* b, uint64_t s) { return send(true, b, s); }
uint64_t m5_chimaera_send_addr(void* b, uint64_t s) { return send(false, b, s); }
uint64_t m5_chimaera_recv(void* b, uint64_t s) { return recv(true, b, s); }
uint64_t m5_chimaera_recv_addr(void* b, uint64_t s) { return recv(false, b, s); }
}
int main() {
    GuestTransport address;
    expect(!address.send({}).ok() && !address.receive().ok());
    expect(calls.empty());
    GuestTransport instruction(GuestM5Ops::instruction);
    const Message data{std::byte{1}, std::byte{2}};
    enqueue(data);
    expect(instruction.send(data).ok());
    expect(instruction.receive().data == data);
    expect(calls == std::vector<bool>({true, true, true, true}));
    short_send = true;
    expect(!instruction.send(data).ok());
    const auto failed_calls = calls.size();
    expect(!instruction.send(data).ok() && calls.size() == failed_calls);
    short_send = false;
    calls.clear();
    // Pending startup uses address ops and retains application messages. Ready
    // supplies the poll duration once; the first batch uses instruction ops.
    m5_mem = &address;
    sent.clear();
    enqueue(control::encode_startup(Duration{}));
    enqueue(control::encode_startup(Duration(3)));
    enqueue(control::encode({data, {}}));
    enqueue(control::encode({data}));
    Gem5GuestController controller(GuestM5Ops::instruction, true);
    expect(!controller.take());
    controller.submit(Message{std::byte{9}}, "state");
    controller.submit(data, "state");
    expect(controller.run_next().ok());
    // No polling wait before delivering the first application reply.
    expect(waits.size() == 1 && waits[0].duration == control::bootstrap_delay);
    expect(waits[0].sent_frames == 2 && waits[0].remaining_frames == 6);
    expect(controller.take() == data);
    expect(controller.take() == Message{});
    expect(!controller.take());
    expect(received.size() == 2);
    expect(controller.run_next().ok());
    expect(waits.size() == 2 && waits[1].duration == Duration(3));
    expect(waits[1].sent_frames == 6 && waits[1].remaining_frames == 2);
    expect(controller.take() == data);
    expect(!controller.take());
    expect(sent.size() == 8);
    expect(sent[1] == control::startup_request() && sent[3] == control::startup_request());
    expect(control::decode(sent[5]) == control::Batch{data});
    expect(control::decode(sent[7]).empty());
    expect(calls == std::vector<bool>({false, false, false, false,
                                     false, false, false, false,
                                     true, true, true, true,
                                     true, true, true, true}));
    expect(received.empty());
    // Configuration cannot reappear inside a running data session.
    enqueue(control::encode_startup(Duration(7)));
    expect(!controller.run_next().ok());
    expect(waits.size() == 3 && waits[2].duration == Duration(3));
    const auto failed = calls.size();
    expect(!controller.run_next().ok() && calls.size() == failed);
    expect(waits.size() == 3);
    bool rejected = false;
    try { controller.submit(data); } catch (const std::runtime_error&) { rejected = true; }
    expect(rejected);
    // A startup version mismatch fails before any instruction op or batch send.
    calls.clear();
    sent.clear();
    auto old_version = control::encode_startup(Duration(3));
    old_version[7] = std::byte{'1'};
    enqueue(std::move(old_version));
    Gem5GuestController mismatch(GuestM5Ops::instruction, true);
    mismatch.submit(data);
    expect(!mismatch.run_next().ok());
    expect(calls == std::vector<bool>({false, false, false, false}));
    expect(sent.size() == 2 && sent[1] == control::startup_request());
}
