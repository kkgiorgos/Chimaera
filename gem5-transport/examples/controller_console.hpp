#pragma once

#include <chimaera/controller.hpp>
#include <deque>
#include <iostream>
#include <stdexcept>
#include <string_view>

namespace chimaera::controller_example {

inline std::optional<chimaera::Message> parse_send(std::string_view line) {
    if (line != "send" && !line.starts_with("send ")) return std::nullopt;
    const auto text = line.size() > 4 ? line.substr(5) : std::string_view{};
    const auto bytes = std::as_bytes(std::span(text));
    return chimaera::Message(bytes.begin(), bytes.end());
}

inline void print_message(std::string_view action,
                          std::string_view peer, const chimaera::Message& data) {
    std::cout << action << ' ' << peer
              << " (" << data.size() << " bytes): ";
    for (auto byte : data) std::cout << static_cast<char>(byte);
    std::cout << '\n' << std::flush;
}

class Queue final : public chimaera::DataProducer {
public:
    std::deque<chimaera::Message> messages;
    std::optional<chimaera::Message> take() override {
        if (messages.empty()) return std::nullopt;
        auto message = std::move(messages.front());
        messages.pop_front();
        print_message("Sending to", "guest", message);
        return message;
    }
};

class Display final : public chimaera::DataConsumer {
public:
    explicit Display(std::string_view peer) : peer_(peer) {}
    void submit(chimaera::Message data) override {
        print_message("Received from", peer_, data);
    }
private:
    std::string_view peer_;
};

inline void require(chimaera::ControllerResult result) {
    if (result.state == chimaera::ControllerState::failed)
        throw std::runtime_error(result.message);
}

} // namespace chimaera::controller_example
