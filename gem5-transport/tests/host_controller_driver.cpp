#include <chimaera/gem5_controller.hpp>
#include <deque>
#include <iostream>
#include <stdexcept>

using namespace chimaera;
struct App : DataProducer, DataConsumer {
    std::deque<Message> queued;
    std::optional<Message> take() override {
        if (queued.empty()) return std::nullopt;
        auto message = std::move(queued.front());
        queued.pop_front();
        return message;
    }
    std::string_view coalescing_key(const Message& message) const noexcept override {
        return !message.empty() && message.front() == std::byte{'c'} ? "clock" : "";
    }
    void queue(const std::string& text) {
        const auto bytes = std::as_bytes(std::span(text));
        queued.emplace_back(bytes.begin(), bytes.end());
    }
    void submit(Message) override { throw std::runtime_error("unexpected guest message"); }
};
int main(int argc, char** argv) {
    if (argc != 4) return 2;
    try {
        App app;
        Gem5TimingController timing(argv[1], std::chrono::seconds(3));
        Gem5HostController host(timing, app, app, argv[2], argv[3]);
        auto step = [&] {
            auto result = host.step(std::chrono::milliseconds(1), std::chrono::microseconds(100));
            if (!result.ok()) throw std::runtime_error(result.message);
        };
        for (unsigned i = 0; i < 1100; ++i) {
            app.queue("c:" + std::to_string(i));
            if (i == 10 || i == 1000) app.queue("s:" + std::to_string(i));
            step();
        }
        if (timing.elapsed_ticks() != 1100ULL * 1000000000)
            throw std::runtime_error("timing stopped advancing while guest polls were withheld");
        std::cout << "READY\n" << std::flush;
        std::string command;
        while (std::getline(std::cin, command) && command == "step") {
            step();
            std::cout << "STEPPED\n" << std::flush;
        }
        if (host.stop().state != ControllerState::stopped)
            throw std::runtime_error("controller failed to stop");
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
