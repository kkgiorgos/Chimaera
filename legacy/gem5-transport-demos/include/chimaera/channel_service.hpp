#pragma once
#include <chimaera/controller.hpp>
#include <chimaera/queue_manager.hpp>
#include <memory>

namespace chimaera {
// Application-owned local IPC broker and queue-manager adapter.
// Clients can attach/detach independently. One consumer per channel is recommended;
// multiple consumers compete for FIFO messages (there is no broadcast).
// Payloads are limited to 4096 bytes. Full incoming queues apply backpressure
// until a client drains them. Stop/close before joining a blocked application thread.
class ChannelService final {
public:
    ChannelService(std::string socket, std::span<const QueueChannel> channels);
    ~ChannelService();
    std::optional<Message> take();
    void submit(Message bundle);
    // Called by the application loop; the controller never calls this adapter.
    void exchange(DataController& controller) {
        while (auto message = controller.take()) submit(std::move(*message));
        while (auto message = take()) controller.submit(std::move(*message));
    }
    void close();
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

class ChannelClient {
public:
    ChannelClient(std::string socket, ChannelId channel);
    // False means full; caller retains the message and may retry.
    bool send(std::span<const std::byte> message);
    std::optional<Message> receive();
private:
    std::string socket_;
    ChannelId channel_;
    Message request(unsigned char operation, std::span<const std::byte> payload);
};
}
