#include <chimaera/queue_manager.hpp>

#include <array>
#include <atomic>
#include <chrono>
#include <future>
#include <iostream>
#include <stdexcept>
#include <thread>

using namespace chimaera;
using namespace std::chrono_literals;

void check(bool condition) {
    if (!condition) throw std::runtime_error("test check failed");
}

template<class Exception, class Function>
void throws(Function function) {
    try { function(); }
    catch (const Exception&) { return; }
    throw std::runtime_error("expected exception was not thrown");
}

QueueMessage message(unsigned value) {
    return {static_cast<std::byte>(value >> 8), static_cast<std::byte>(value & 255)};
}

void round_trip() {
    const std::array config{QueueChannel{42, 2}, QueueChannel{7, 1}};
    QueueSerializer sender(config);
    QueueDeserializer receiver(config);
    check(sender.try_enqueue(42, {}));
    check(sender.try_enqueue(42, message(255)));
    check(!sender.try_enqueue(42, message(3)));
    check(sender.try_enqueue(7, message(256)));
    auto first = sender.serialize();
    check(sender.size(42) == 0);
    check(sender.try_enqueue(42, message(4)));
    check(receiver.deserialize(first));
    check(receiver.try_dequeue(42) == QueueMessage{});
    check(receiver.try_dequeue(42) == message(255));
    check(!receiver.try_dequeue(42));
    check(receiver.try_dequeue(7) == message(256));
    check(receiver.deserialize(sender.serialize()));
    check(receiver.try_dequeue(42) == message(4));
    const QueueBundle empty{std::byte{'C'}, std::byte{'Q'}, std::byte{'M'}, std::byte{1},
        std::byte{0}, std::byte{0}, std::byte{0}, std::byte{0},
        std::byte{0}, std::byte{0}, std::byte{0}, std::byte{0}};
    check(sender.serialize() == empty);
    check(receiver.deserialize(empty));
    receiver.close();
    receiver.close();
    check(!receiver.deserialize(empty));
    check(!receiver.wait_dequeue(7));
    throws<std::out_of_range>([&] { (void)sender.try_enqueue(999, {}); });
    throws<std::out_of_range>([&] { (void)receiver.try_dequeue(999); });
    const std::array duplicate{QueueChannel{1, 1}, QueueChannel{1, 2}};
    const std::array zero{QueueChannel{1, 0}};
    throws<std::invalid_argument>([&] { QueueSerializer invalid(duplicate); });
    throws<std::invalid_argument>([&] { QueueDeserializer invalid(zero); });
    QueueSerializer no_channels({});
    QueueDeserializer no_receivers({});
    check(no_receivers.deserialize(no_channels.serialize()));
}

void malformed() {
    const std::array config{QueueChannel{1, 10}};
    QueueSerializer sender(config);
    QueueDeserializer receiver(config);
    check(sender.try_enqueue(1, message(123)));
    check(sender.try_enqueue(1, message(456)));
    const auto valid = sender.serialize();
    for (std::size_t length = 0; length < valid.size(); ++length) {
        throws<std::invalid_argument>([&] { (void)receiver.deserialize(std::span(valid).first(length)); });
        check(receiver.size(1) == 0);
    }
    for (auto index : {0U, 3U, 4U, 15U, 16U, 29U}) {
        auto bad = valid;
        bad[index] = std::byte{255};
        throws<std::invalid_argument>([&] { (void)receiver.deserialize(bad); });
        check(receiver.size(1) == 0);
    }
    auto trailing = valid;
    trailing.push_back(std::byte{0});
    throws<std::invalid_argument>([&] { (void)receiver.deserialize(trailing); });
    check(receiver.size(1) == 0);
}

void backpressure_and_close() {
    const std::array tx_config{QueueChannel{1, 3}, QueueChannel{2, 1}};
    const std::array rx_config{QueueChannel{1, 1}, QueueChannel{2, 1}};
    QueueSerializer sender(tx_config);
    QueueDeserializer receiver(rx_config);
    for (unsigned i = 0; i < 3; ++i) check(sender.try_enqueue(1, message(i)));
    const auto bundle = sender.serialize();
    auto producer = std::async(std::launch::async, [&] { return receiver.deserialize(bundle); });
    check(receiver.wait_dequeue(1) == message(0));
    check(receiver.wait_dequeue(1) == message(1));
    check(receiver.wait_dequeue(1) == message(2));
    check(producer.get());

    auto waiting_consumer = std::async(std::launch::async, [&] { return receiver.wait_dequeue(2); });
    auto blocked_producer = std::async(std::launch::async, [&] { return receiver.deserialize(bundle); });
    const auto deadline = std::chrono::steady_clock::now() + 2s;
    while (!receiver.size(1) && std::chrono::steady_clock::now() < deadline) std::this_thread::yield();
    check(receiver.size(1) == 1);
    check(blocked_producer.wait_for(10ms) == std::future_status::timeout);
    receiver.close();
    check(!blocked_producer.get());
    check(!waiting_consumer.get());
    check(receiver.wait_dequeue(1) == message(0));
    check(!receiver.wait_dequeue(1));
}

void concurrent_snapshots() {
    const std::array config{QueueChannel{1, 8}, QueueChannel{2, 8}};
    QueueSerializer sender(config);
    QueueDeserializer receiver(config);
    constexpr unsigned total = 4000;
    auto produce = [&](ChannelId id) {
        for (unsigned i = 0; i < total; ++i)
            while (!sender.try_enqueue(id, message(i))) std::this_thread::yield();
    };
    std::thread first(produce, 1);
    std::thread second(produce, 2);
    std::array<unsigned, 2> received{};
    while (received[0] < total || received[1] < total) {
        check(receiver.deserialize(sender.serialize()));
        for (unsigned channel = 1; channel <= 2; ++channel) {
            while (auto item = receiver.try_dequeue(channel)) {
                check(*item == message(received[channel - 1]));
                ++received[channel - 1];
            }
        }
    }
    first.join();
    second.join();
    check(sender.size(1) == 0 && sender.size(2) == 0);
}

// A message in channel 2 is always preceded by one in channel 1. An atomic
// snapshot can never cumulatively contain more channel-2 than channel-1 items.
void snapshot_boundary() {
    const std::array config{QueueChannel{1, 32}, QueueChannel{2, 32}};
    QueueSerializer sender(config);
    QueueDeserializer receiver(config);
    constexpr unsigned total = 3000;
    std::thread producer([&] {
        for (unsigned i = 0; i < total; ++i) {
            while (!sender.try_enqueue(1, message(i))) std::this_thread::yield();
            while (!sender.try_enqueue(2, message(i))) std::this_thread::yield();
        }
    });
    std::array<unsigned, 2> received{};
    while (received[1] < total) {
        check(receiver.deserialize(sender.serialize()));
        for (unsigned id = 1; id <= 2; ++id)
            while (receiver.try_dequeue(id)) ++received[id - 1];
        check(received[0] >= received[1]);
    }
    producer.join();
    check(received[0] == total);
}

int main() {
    try {
        round_trip();
        malformed();
        backpressure_and_close();
        concurrent_snapshots();
        snapshot_boundary();
        std::cout << "All queue manager tests passed\n";
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
