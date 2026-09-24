#pragma once

#include <mock_sim/controller.hpp>
#include <deque>
#include <iostream>
#include <stdexcept>
#include <string_view>

namespace controller_example {

inline constexpr auto default_endpoint = "/tmp/mock-controller-demo";

inline std::optional<mock_sim::Message> parse_send(std::string_view line) {
    if (line != "send" && !line.starts_with("send ")) return std::nullopt;
    const auto text = line.size() > 4 ? line.substr(5) : std::string_view{};
    const auto bytes = std::as_bytes(std::span(text));
    return mock_sim::Message(bytes.begin(), bytes.end());
}

inline void print_message(std::size_t step, std::string_view action,
                          std::string_view peer, const mock_sim::Message& data) {
    std::cout << "[step " << step << "] " << action << ' ' << peer
              << " (" << data.size() << " bytes): ";
    for (auto byte : data) std::cout << static_cast<char>(byte);
    std::cout << '\n' << std::flush;
}

class Queue final : public mock_sim::DataProducer {
public:
    explicit Queue(const std::size_t& step) : step_(step) {}
    std::deque<mock_sim::Message> messages;
    std::optional<mock_sim::Message> take() override {
        if (messages.empty()) return std::nullopt;
        auto message = std::move(messages.front());
        messages.pop_front();
        print_message(step_, "Sending to", "guest", message);
        return message;
    }
private:
    const std::size_t& step_;
};

class Display final : public mock_sim::DataConsumer {
public:
    Display(std::string_view peer, const std::size_t& step) : peer_(peer), step_(step) {}
    void submit(mock_sim::Message data) override {
        print_message(step_, "Received from", peer_, data);
    }
private:
    std::string_view peer_;
    const std::size_t& step_;
};

inline void require(mock_sim::ControllerResult result) {
    if (result.state == mock_sim::ControllerState::failed)
        throw std::runtime_error(result.message);
}

} // namespace controller_example
