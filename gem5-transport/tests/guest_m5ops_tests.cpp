// Link against fake m5ops so backend selection can be tested on the host.
#include <chimaera/gem5_controller.hpp>
#include <gem5/m5ops.h>
#include <m5_mmap.h>
#include "../src/controller_protocol.hpp"
#include "../src/framing.hpp"
#include <cstring>
#include <deque>
#include <stdexcept>

using namespace chimaera;
extern "C" { void* m5_mem = nullptr; }
namespace {
std::deque<Message> received;
std::vector<bool> calls;
bool short_send = false;
void expect(bool condition) { if (!condition) throw std::runtime_error("m5ops test failed"); }
std::uint64_t send(bool instruction, void*, std::uint64_t size) {
    calls.push_back(instruction);
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
struct App : DataProducer, DataConsumer {
    int taken = 0;
    std::optional<Message> take() override { ++taken; return std::nullopt; }
    void submit(Message) override { throw std::runtime_error("unexpected callback"); }
};
}
extern "C" {
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
    // A delayed KVM exit can produce epoch-zero polls. No instruction op or
    // application callback is allowed until a switched host publishes epoch 1.
    m5_mem = &address;
    for (unsigned epoch : {0, 1, 2})
        enqueue(control::encode({control::Kind::reply, epoch, Duration(1), {}}));
    App app;
    Gem5GuestController controller(app, app, GuestM5Ops::instruction, true);
    expect(controller.run_next().ok());
    expect(app.taken == 1);
    expect(calls == std::vector<bool>({false, false, false, false,
                                     false, false, false, false,
                                     true, true, true, true}));
    expect(received.empty());
}
