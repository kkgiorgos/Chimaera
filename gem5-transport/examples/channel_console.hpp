#pragma once
#include <chimaera/channel_service.hpp>
#include <charconv>
#include <cerrno>
#include <poll.h>
#include <unistd.h>
#include <iostream>
#include <string_view>

inline int channel_console(int argc, char** argv) {
    if (argc != 4) {
        std::cerr << "Usage: " << argv[0] << " --channel ID SOCKET\n";
        return 1;
    }
    try {
        chimaera::ChannelId id{};
        std::string_view value(argv[2]);
        auto [end, error] = std::from_chars(value.data(), value.data() + value.size(), id);
        if (error != std::errc{} || end != value.data() + value.size())
            throw std::invalid_argument("invalid channel ID");
        chimaera::ChannelClient client(argv[3], id);
        std::cout << "Channel " << id
                  << ". Receiving automatically. Commands: send [TEXT], quit\n" << std::flush;
        std::string buffered;
        bool quit = false;
        auto command = [&](const std::string& line) {
            if (line == "quit" || line == "q") { quit = true; return; }
            if (line == "send" || line.starts_with("send ")) {
                auto text = std::string_view(line).substr(line.size() > 4 ? 5 : 4);
                std::cout << (client.send(std::as_bytes(std::span(text))) ? "Queued\n" : "Queue full; retry\n");
            } else std::cout << "Commands: send [TEXT], quit. Receiving automatically.\n";
            std::cout << std::flush;
        };
        while (!quit) {
            // Bound each drain so sustained traffic cannot starve console input.
            int received = 0;
            for (; received < 32; ++received) {
                auto data = client.receive();
                if (!data) break;
                std::cout << "Received " << data->size() << " bytes: ";
                for (auto byte : *data) std::cout << static_cast<char>(byte);
                std::cout << '\n' << std::flush;
            }
            pollfd input{STDIN_FILENO, POLLIN, 0};
            const auto ready = ::poll(&input, 1, received == 32 ? 0 : 20);
            if (ready < 0 && errno == EINTR) continue;
            if (ready < 0 || (input.revents & (POLLERR | POLLNVAL)))
                throw std::runtime_error("cannot poll channel console input");
            if (!ready) continue;
            char bytes[4096];
            const auto count = ::read(STDIN_FILENO, bytes, sizeof(bytes));
            if (count < 0 && (errno == EINTR || errno == EAGAIN)) continue;
            if (count < 0) throw std::runtime_error("cannot read channel console input");
            if (count == 0) {
                if (!buffered.empty()) command(buffered);
                break;
            }
            buffered.append(bytes, static_cast<std::size_t>(count));
            if (buffered.size() > 1024 * 1024)
                throw std::runtime_error("channel console input exceeds 1 MiB");
            while (!quit) {
                const auto newline = buffered.find('\n');
                if (newline == std::string::npos) break;
                auto line = buffered.substr(0, newline);
                buffered.erase(0, newline + 1);
                if (!line.empty() && line.back() == '\r') line.pop_back();
                command(line);
            }
        }
        return 0;
    } catch (const std::exception& e) {
        std::cerr << "Channel client: " << e.what() << '\n';
        return 1;
    }
}
