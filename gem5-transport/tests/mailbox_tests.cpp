#include <gem5/chimaera_mailbox.hh>

#include <array>
#include <filesystem>
#include <sched.h>
#include <source_location>
#include <stdexcept>
#include <sys/wait.h>
#include <vector>

using chimaera_mailbox::Mailbox;

void expect(bool condition, const std::source_location location = std::source_location::current()) {
    if (!condition) throw std::runtime_error("mailbox invariant failed at line " +
        std::to_string(location.line()));
}
template<class Function> void rejects(Function function) {
    bool rejected = false;
    try { function(); } catch (const std::runtime_error&) { rejected = true; }
    expect(rejected);
}

int main() {
    char directory[] = "/tmp/chimaera-mailbox-XXXXXX";
    expect(mkdtemp(directory));
    const auto path = std::string(directory) + "/ring";
    constexpr unsigned capacity = 640 * 480 * 3;
    {
        Mailbox producer(path, true, 2, capacity);
        Mailbox consumer(path, false, 2, capacity);
        rejects([&] { Mailbox collision(path, true, 2, capacity); });
        rejects([&] { Mailbox mismatch(path, false, 1, capacity); });
        Mailbox::View view{};
        expect(!consumer.peek(view));
        rejects([&] { consumer.pop(); });
        rejects([&] { producer.reserve(capacity + 1); });
        auto* pending = producer.reserve(3);
        expect(pending);
        pending[0] = 4; pending[1] = 5; pending[2] = 6;
        expect(!consumer.peek(view)); // Uncommitted DMA bytes are invisible.
        producer.commit(3);
        expect(consumer.peek(view) && view.size == 3);
        const auto* received = view.data;
        expect(consumer.peek(view) && view.data == received);
        expect(view.data[0] == 4 && view.data[2] == 6);
        expect(producer.write(nullptr, 0));
        const auto snapshot = consumer.snapshot();
        expect(snapshot.size() == 2 && snapshot[0] == std::vector<std::uint8_t>({4, 5, 6}) &&
               snapshot[1].empty());
        expect(consumer.peek(view) && view.data == received); // Snapshot returns no credit.
        expect(!producer.reserve(1));
        rejects([&] { producer.commit(1); });
        consumer.pop();
        expect(consumer.peek(view) && view.size == 0);
        consumer.pop();
        expect(consumer.snapshot().empty());

        // Repeated full RGB frames across a real process boundary, with both
        // slots wrapping under concurrent publication and delayed consumption.
        const auto child = fork();
        expect(child >= 0);
        if (child == 0) {
            try {
                Mailbox reader(path, false, 2, capacity);
                for (unsigned frame = 0; frame < 40; ++frame) {
                    Mailbox::View received{};
                    while (!reader.peek(received)) sched_yield();
                    expect(received.size == capacity);
                    for (unsigned i = 0; i < capacity; ++i)
                        expect(received.data[i] == (i + frame) % 251);
                    reader.pop();
                }
                _exit(0);
            } catch (...) { _exit(1); }
        }
        for (unsigned frame = 0; frame < 40; ++frame) {
            std::uint8_t* target;
            while (!(target = producer.reserve(capacity))) sched_yield();
            for (unsigned i = 0; i < capacity; ++i) target[i] = (i + frame) % 251;
            producer.commit(capacity);
        }
        int status{};
        expect(waitpid(child, &status, 0) == child && WIFEXITED(status) && WEXITSTATUS(status) == 0);
        expect(!consumer.peek(view));

        // Detect corrupt metadata before handing a view to the consumer.
        expect(producer.write(nullptr, 0));
        expect(consumer.peek(view));
        auto* length = reinterpret_cast<std::uint32_t*>(const_cast<std::uint8_t*>(view.data) - 64);
        *length = capacity + 1;
        rejects([&] { consumer.peek(view); });
        rejects([&] { consumer.snapshot(); });
        *length = 0;
        consumer.pop();
    }
    expect(!std::filesystem::exists(path));
    std::filesystem::remove(directory);
}
