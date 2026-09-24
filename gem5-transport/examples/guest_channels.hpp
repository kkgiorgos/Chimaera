#pragma once
#include <chimaera/controller.hpp>
#include <chimaera/queue_manager.hpp>
#include <array>
#include <charconv>
#include <cerrno>
#include <iostream>
#include <poll.h>
#include <unistd.h>

namespace chimaera::controller_example {
// All application callbacks run on the guest controller thread. No guest IPC
// clients or worker threads are needed to manage the demo's two channels.
class GuestChannels final : public DataProducer, public DataConsumer {
public:
    explicit GuestChannels(int input = STDIN_FILENO, std::ostream& output = std::cout)
        : input_(input), output_(output), outgoing_(outgoing_channels), incoming_(incoming_channels) {}

    std::optional<Message> take() override {
        // Read only ready bytes; a partial command must never stop guest polling.
        for (int attempt = 0; !eof_ && attempt < 4; ++attempt) {
            pollfd descriptor{input_, POLLIN, 0};
            const auto ready = ::poll(&descriptor, 1, 0);
            if (ready < 0 && errno == EINTR) continue;
            if (ready < 0 || (descriptor.revents & (POLLERR | POLLNVAL)))
                throw std::runtime_error("cannot poll guest console");
            if (!ready) break;
            char bytes[4096];
            const auto count = ::read(input_, bytes, sizeof(bytes));
            if (count < 0 && (errno == EINTR || errno == EAGAIN)) continue;
            if (count < 0) throw std::runtime_error("cannot read guest console");
            if (count == 0) eof_ = true;
            else buffered_.append(bytes, static_cast<std::size_t>(count));
            if (buffered_.size() > 1024 * 1024)
                throw std::runtime_error("guest console input exceeds 1 MiB");
            while (true) {
                const auto newline = buffered_.find('\n');
                if (newline == std::string::npos && !(eof_ && !buffered_.empty())) break;
                auto line = buffered_.substr(0, newline);
                buffered_.erase(0, newline == std::string::npos ? buffered_.size() : newline + 1);
                if (!line.empty() && line.back() == '\r') line.pop_back();
                command(line);
            }
        }
        for (auto channel : outgoing_channels)
            if (outgoing_.size(channel.id)) return outgoing_.serialize();
        return std::nullopt;
    }

    void submit(Message bundle) override {
        // A ChannelService snapshot contains at most 4096 records. Reserve that
        // capacity per incoming channel and drain every bundle synchronously,
        // so deserialization never waits for this same thread to consume data.
        if (bundle.size() < 12) throw std::invalid_argument("truncated channel bundle");
        std::uint64_t count = 0;
        for (int i = 4; i < 12; ++i) count = (count << 8) | std::to_integer<unsigned>(bundle[i]);
        if (count > 4096) throw std::invalid_argument("guest channel bundle exceeds 4096 messages");
        if (!incoming_.deserialize(bundle)) throw std::runtime_error("guest channels closed");
        for (auto channel : incoming_channels) {
            while (auto data = incoming_.try_dequeue(channel.id)) {
                output_ << "[channel " << channel.id << "] Received " << data->size() << " bytes: ";
                for (auto byte : *data) output_ << static_cast<char>(byte);
                output_ << '\n';
            }
        }
        output_ << std::flush;
    }
private:
    void command(std::string_view line) {
        if (line.empty()) return;
        if (!line.starts_with("send ")) {
            output_ << "Commands: send CHANNEL [TEXT] (channels 1, 2). Quit from the host.\n" << std::flush;
            return;
        }
        line.remove_prefix(5);
        const auto separator = line.find(' ');
        const auto id_text = line.substr(0, separator);
        ChannelId id{};
        auto [end, error] = std::from_chars(id_text.data(), id_text.data() + id_text.size(), id);
        if (error != std::errc{} || end != id_text.data() + id_text.size() || (id != 1 && id != 2)) {
            output_ << "Choose channel 1 or 2: send CHANNEL [TEXT]\n" << std::flush;
            return;
        }
        const auto text = separator == std::string_view::npos ? std::string_view{} : line.substr(separator + 1);
        if (text.size() > 4096) {
            output_ << "Message exceeds 4096 bytes\n" << std::flush;
            return;
        }
        output_ << "[channel " << id << "] "
                << (outgoing_.try_enqueue(id, std::as_bytes(std::span(text))) ? "Queued\n" : "Queue full; retry\n")
                << std::flush;
    }
    inline static constexpr std::array outgoing_channels{QueueChannel{1, 128}, QueueChannel{2, 128}};
    inline static constexpr std::array incoming_channels{QueueChannel{1, 4096}, QueueChannel{2, 4096}};
    int input_;
    std::ostream& output_;
    bool eof_ = false;
    std::string buffered_;
    QueueSerializer outgoing_;
    QueueDeserializer incoming_;
};
}
