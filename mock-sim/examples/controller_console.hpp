#pragma once

#include <mock_sim/controller.hpp>
#include <chimaera/queue_manager.hpp>
#include <array>
#include <iostream>
#include <stdexcept>
#include <string_view>

namespace controller_example {

inline constexpr auto default_endpoint = "/tmp/mock-controller-demo";

inline constexpr std::array channels{
    chimaera::QueueChannel{1, 64}, chimaera::QueueChannel{2, 64}};

struct SendCommand {
    chimaera::ChannelId channel;
    mock_sim::Message data;
};

inline std::optional<SendCommand> parse_send(std::string_view line) {
    if (!line.starts_with("send ")) return std::nullopt;
    line.remove_prefix(5);
    if (line.empty() || (line[0] != '1' && line[0] != '2') ||
        (line.size() > 1 && line[1] != ' ')) return std::nullopt;
    const auto channel = static_cast<chimaera::ChannelId>(line[0] - '0');
    const auto text = line.size() > 1 ? line.substr(2) : std::string_view{};
    const auto bytes = std::as_bytes(std::span(text));
    return SendCommand{channel, {bytes.begin(), bytes.end()}};
}

inline void print_message(std::size_t step, chimaera::ChannelId channel,
                          std::string_view action, std::string_view peer,
                          const mock_sim::Message& data) {
    std::cout << "[step " << step << "][channel " << channel << "] "
              << action << ' ' << peer << " (" << data.size() << " bytes): ";
    for (auto byte : data) std::cout << static_cast<char>(byte);
    std::cout << '\n' << std::flush;
}

class Queue final : public mock_sim::DataProducer {
public:
    Queue(std::string_view peer, const std::size_t& step) : peer_(peer), step_(step) {}
    bool enqueue(const SendCommand& command) {
        return outgoing_.try_enqueue(command.channel, command.data);
    }
    std::optional<mock_sim::Message> take() override {
        if (outgoing_.size(1) == 0 && outgoing_.size(2) == 0) return std::nullopt;
        auto bundle = outgoing_.serialize();
        // Decode the snapshot for per-message logging at controller handoff.
        chimaera::QueueDeserializer snapshot(channels);
        if (!snapshot.deserialize(bundle)) throw std::runtime_error("snapshot closed");
        for (const auto channel : channels)
            while (auto message = snapshot.try_dequeue(channel.id))
                print_message(step_, channel.id, "Sending to", peer_, *message);
        return bundle;
    }
private:
    chimaera::QueueSerializer outgoing_{channels};
    std::string_view peer_;
    const std::size_t& step_;
};

class Display final : public mock_sim::DataConsumer {
public:
    Display(std::string_view peer, const std::size_t& step) : peer_(peer), step_(step) {}
    void submit(mock_sim::Message data) override {
        // Each snapshot contains at most one queue depth per channel. Drain it
        // before the next callback so deserialize never waits on this thread.
        if (!incoming_.deserialize(data)) throw std::runtime_error("incoming queues closed");
        for (const auto channel : channels)
            while (auto message = incoming_.try_dequeue(channel.id))
                print_message(step_, channel.id, "Received from", peer_, *message);
    }
private:
    chimaera::QueueDeserializer incoming_{channels};
    std::string_view peer_;
    const std::size_t& step_;
};

inline void require(mock_sim::ControllerResult result) {
    if (result.state == mock_sim::ControllerState::failed)
        throw std::runtime_error(result.message);
}

} // namespace controller_example
