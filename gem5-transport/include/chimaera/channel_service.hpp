#pragma once
#include <chimaera/controller.hpp>
#include <chimaera/queue_manager.hpp>
#include <memory>

namespace chimaera {
// Local IPC broker and queue-manager adapter. One controller owns each service.
// Clients can attach/detach independently. One consumer per channel is recommended;
// multiple consumers compete for FIFO messages (there is no broadcast).
// Payloads are limited to 4096 bytes. Full incoming queues apply backpressure
// until a client drains them. Stop/close before joining a blocked controller.
class ChannelService final : public DataProducer, public DataConsumer {
public:
    ChannelService(std::string socket, std::span<const QueueChannel> channels);
    ~ChannelService() override;
    std::optional<Message> take() override;
    void submit(Message bundle) override;
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
