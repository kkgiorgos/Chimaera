#include <chimaera/channel_service.hpp>
#include <array>
#include <chrono>
#include <future>
#include <iostream>
#include <stdexcept>
#include <sys/wait.h>
#include <unistd.h>
using namespace chimaera;
void expect(bool value) { if (!value) throw std::runtime_error("test assertion failed"); }
Message bytes(const char* s) { auto b = std::as_bytes(std::span(std::string_view(s))); return {b.begin(), b.end()}; }
int main(int argc, char** argv) {
    try {
        if (argc == 3) {
            ChannelClient one(argv[2], 1), two(argv[2], 2);
            expect(one.send(bytes("first")));
            expect(one.send({}));
            expect(!one.send(bytes("full")));
            expect(two.send(bytes("isolated")));
            return 0;
        }
        const auto prefix = "/tmp/chimaera-channels-test-" + std::to_string(getpid());
        const std::array channels{QueueChannel{1, 2}, QueueChannel{2, 2}};
        ChannelService host(prefix + "-h", channels), guest(prefix + "-g", channels);
        // A real separately exec'd process attaches to the parent's service.
        const auto pid = fork();
        expect(pid >= 0);
        if (pid == 0) { execl(argv[0], argv[0], "client", (prefix + "-h").c_str(), nullptr); _exit(127); }
        int status;
        expect(waitpid(pid, &status, 0) == pid && WIFEXITED(status) && WEXITSTATUS(status) == 0);
        auto bundle = host.take(); expect(bundle.has_value()); guest.submit(std::move(*bundle));
        expect(!host.take());
        ChannelClient one(prefix + "-g", 1), two(prefix + "-g", 2);
        expect(two.receive() == bytes("isolated"));
        expect(one.receive() == bytes("first"));
        expect(one.receive() == Message{});
        expect(!one.receive());
        expect(one.send(bytes("reply")));
        host.submit(*guest.take());
        ChannelClient host_one(prefix + "-h", 1);
        expect(host_one.receive() == bytes("reply"));
        bool rejected = false;
        try { ChannelClient(prefix + "-h", 3).send({}); } catch (const std::runtime_error&) { rejected = true; }
        expect(rejected);
        rejected = false;
        try { host_one.send(Message(4097)); } catch (const std::invalid_argument&) { rejected = true; }
        expect(rejected);
        rejected = false;
        try { ChannelService duplicate(prefix + "-h", channels); } catch (const std::runtime_error&) { rejected = true; }
        expect(rejected); expect(host_one.send(bytes("still alive")));
        guest.submit(*host.take());
        expect(one.receive() == bytes("still alive"));
        Message maximum(4096, std::byte{0xff});
        expect(host_one.send(maximum)); guest.submit(*host.take()); expect(one.receive() == maximum);
        // Full incoming queue blocks publication, while IPC consumers stay live.
        QueueSerializer producer(channels);
        expect(producer.try_enqueue(1, bytes("a"))); expect(producer.try_enqueue(1, bytes("b")));
        guest.submit(producer.serialize());
        expect(producer.try_enqueue(1, bytes("c")));
        auto pending = std::async(std::launch::async, [&] { guest.submit(producer.serialize()); });
        expect(pending.wait_for(std::chrono::milliseconds(50)) == std::future_status::timeout);
        expect(one.receive() == bytes("a"));
        pending.get();
        expect(one.receive() == bytes("b")); expect(one.receive() == bytes("c"));
        expect(producer.try_enqueue(1, {})); expect(producer.try_enqueue(1, {}));
        guest.submit(producer.serialize()); expect(producer.try_enqueue(1, {}));
        auto closing = std::async(std::launch::async, [&] {
            try { guest.submit(producer.serialize()); return false; } catch (const std::runtime_error&) { return true; }
        });
        guest.close(); expect(closing.get());
        std::cout << "Channel service integration passed\n";
    } catch (const std::exception& e) { std::cerr << e.what() << '\n'; return 1; }
}
