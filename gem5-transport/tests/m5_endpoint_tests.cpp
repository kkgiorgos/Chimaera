// Compile m5_endpoint.cpp directly with these m5/syscall stubs. No production
// libm5 assembly is linked or executed. The real driver/IRQ path needs the
// parent's full-system validation; do not run this before the firstcatch gate.
#include <chimaera/m5_endpoint.hpp>
#include <gem5/chimaera_m5.h>
#include <gem5/m5ops.h>
#include <m5_mmap.h>

#include <algorithm>
#include <array>
#include <cerrno>
#include <cstdarg>
#include <cstring>
#include <deque>
#include <fcntl.h>
#include <functional>
#include <map>
#include <poll.h>
#include <source_location>
#include <stdexcept>
#include <sys/eventfd.h>
#include <sys/mman.h>
#include <system_error>
#include <unistd.h>
#include <vector>

using namespace chimaera;
extern "C" {
void* m5_mem = nullptr;
int __real_open(const char*, int, ...);
int __real_close(int);
ssize_t __real_read(int, void*, size_t);
void* __real_mmap(void*, size_t, int, int, int, off_t);
int __real_munmap(void*, size_t);
int __real_ioctl(int, unsigned long, ...);
}
namespace {
void expect(bool value, std::source_location at = std::source_location::current()) {
    if (!value) throw std::runtime_error("m5 endpoint regression at line " + std::to_string(at.line()));
}
template<class F> void error(int code, F&& action) {
    bool rejected = false;
    try { action(); } catch (const std::system_error& e) {
        expect(e.code().value() == code);
        rejected = true;
    }
    expect(rejected);
}
struct Completion { std::uint32_t slot, length; std::uint64_t sequence; };
struct Call { bool data, address; std::uint64_t command; chm5_request request; };
struct Context {
    int fd;
    chm5_bind binding;
    std::vector<std::byte*> buffers;
    std::deque<Completion> ready;
    std::map<unsigned, std::uint64_t> loans;
    std::deque<int> submit_errors;
    std::vector<Message> accepted;
    std::vector<Call> calls;
    std::vector<std::string> lifecycle;
    std::vector<Completion> released;
    std::uint64_t notification{};
    std::uint32_t arm_status{}, arm_error{};
    int release_error{}, channel_error{};
    bool wake_before_arm{}, closed{}, model_closed{};
};
struct Driver {
    std::map<unsigned, Context> contexts;
    std::map<int, unsigned> fds;
    std::function<void(chm5_bind&)> alter_binding;
    unsigned next_context{}, opens{}, maps{}, unmaps{}, closes{}, reads{}, mmap_fail_at{};
    int open_error{}, ioctl_error{}, confirm_error{};
    bool disagree_bytes{}, disagree_slots{};
    Context& lane(M5Lane lane) {
        for (auto it = contexts.rbegin(); it != contexts.rend(); ++it)
            if (it->second.binding.lane == static_cast<unsigned>(lane) && !it->second.closed)
                return it->second;
        throw std::runtime_error("no live test lane");
    }
    void reset() {
        for (const auto& [id, context] : contexts) {
            expect(context.closed);
            for (auto* bytes : context.buffers) expect(bytes == nullptr);
        }
        *this = Driver{};
    }
} driver;
Message pattern(std::size_t size, unsigned seed) {
    Message bytes(size);
    for (std::size_t i = 0; i < size; ++i) bytes[i] = std::byte((i + seed) % 251);
    return bytes;
}
void enqueue(Context& context, unsigned slot, const Message& bytes, std::uint64_t sequence) {
    expect(!context.closed && !context.model_closed);
    expect(slot < context.buffers.size() && context.buffers[slot]);
    expect(bytes.size() <= context.binding.slot_bytes && !context.loans.contains(slot));
    for (const auto& pending : context.ready) expect(pending.slot != slot);
    std::memcpy(context.buffers[slot], bytes.data(), bytes.size());
    if (context.ready.empty()) {
        std::uint64_t one = 1;
        expect(::write(context.fd, &one, sizeof(one)) == sizeof(one));
    }
    context.ready.push_back({slot, static_cast<unsigned>(bytes.size()), sequence});
    ++context.notification;
}
bool readable(int fd) {
    pollfd descriptor{fd, POLLIN, 0};
    const auto count = ::poll(&descriptor, 1, 0);
    expect(count >= 0);
    return count == 1 && (descriptor.revents & POLLIN);
}
std::uint64_t negative(int code) { return static_cast<std::uint64_t>(-std::int64_t(code)); }
std::uint64_t invoke(bool data, bool address, std::uint64_t command, void* value, std::uint64_t size) {
    expect(size == sizeof(chm5_request) && value);
    auto& request = *static_cast<chm5_request*>(value);
    auto it = driver.contexts.find(request.context);
    expect(it != driver.contexts.end());
    auto& context = it->second;
    expect(!context.closed && !context.model_closed);
    expect(request.version == CHM5_ABI_VERSION && request.size == size);
    expect(request.lane == context.binding.lane && request.process == static_cast<unsigned>(::getpid()));
    expect(request.generation == context.binding.generation);
    context.calls.push_back({data, address, command, request});
    if (!data) {
        switch (command) {
        case CHM5_CTL_BIND:
            expect(context.buffers.size() == context.binding.slots);
            if (driver.confirm_error) return negative(driver.confirm_error);
            request.slot = context.binding.slots + driver.disagree_slots;
            request.length = context.binding.slot_bytes + driver.disagree_bytes;
            return 0;
        case CHM5_CTL_OPEN_CHANNEL:
        case CHM5_CTL_CLOSE_CHANNEL:
            return negative(context.channel_error);
        case CHM5_CTL_ARM:
            request.sequence = context.notification;
            request.status = context.arm_status | (context.ready.empty() ? 0 : CHM5_STATUS_RX);
            request.error = context.arm_error;
            return 0; // ARM never consumes readiness or a completion.
        case CHM5_CTL_CLOSE:
            expect(context.loans.empty());
            context.model_closed = true;
            context.lifecycle.push_back("model-close");
            return 0;
        default: throw std::runtime_error("unexpected endpoint control command");
        }
    }
    switch (command) {
    case CHM5_DATA_SUBMITV: {
        expect(request.bytes0 == 52);
        if (!context.submit_errors.empty()) {
            const int result = context.submit_errors.front();
            context.submit_errors.pop_front();
            if (result) return result > 4095 ? static_cast<std::uint64_t>(result) : negative(result);
        }
        const auto* header = reinterpret_cast<const std::byte*>(request.address0);
        const auto* body = reinterpret_cast<const std::byte*>(request.address1);
        Message accepted(header, header + request.bytes0);
        if (request.bytes1) accepted.insert(accepted.end(), body, body + request.bytes1);
        context.accepted.push_back(std::move(accepted)); // Synchronous model copy.
        return 0;
    }
    case CHM5_DATA_REAP_BATCH: {
        expect(request.bytes0 > 0 && request.bytes0 % sizeof(chm5_completion) == 0);
        if (context.ready.empty()) {
            if (context.wake_before_arm) {
                context.wake_before_arm = false;
                enqueue(context, 0, pattern(100, 19), 0x123456789ULL);
            }
            return negative(EAGAIN);
        }
        auto* output = reinterpret_cast<chm5_completion*>(request.address0);
        request.length = std::min<std::size_t>(context.ready.size(),
            request.bytes0 / sizeof(chm5_completion));
        for (unsigned index = 0; index < request.length; ++index) {
            const auto completion = context.ready.front();
            context.ready.pop_front();
            output[index] = {completion.slot, completion.length, completion.sequence, 42};
            expect(context.loans.emplace(completion.slot, completion.sequence).second);
        }
        if (context.ready.empty()) {
            std::uint64_t count;
            expect(__real_read(context.fd, &count, sizeof(count)) == sizeof(count));
        }
        return 0;
    }
    case CHM5_DATA_RELEASE_BATCH: {
        expect(request.bytes0 > 0 && request.bytes0 % sizeof(chm5_completion) == 0);
        const auto* entries = reinterpret_cast<const chm5_completion*>(request.address0);
        const auto count = request.bytes0 / sizeof(chm5_completion);
        for (unsigned index = 0; index < count; ++index) {
            const auto& entry = entries[index];
            expect(context.loans.at(entry.slot) == entry.packet_sequence);
            context.released.push_back({entry.slot, 0, entry.packet_sequence});
            context.loans.erase(entry.slot);
        }
        context.lifecycle.push_back("release-batch");
        return negative(context.release_error);
    }
    case CHM5_DATA_REAP: {
        if (context.ready.empty()) {
            if (context.wake_before_arm) {
                context.wake_before_arm = false;
                enqueue(context, 0, pattern(100, 19), 0x123456789ULL);
            }
            return negative(EAGAIN);
        }
        const auto completion = context.ready.front();
        context.ready.pop_front();
        if (context.ready.empty()) {
            std::uint64_t count;
            expect(__real_read(context.fd, &count, sizeof(count)) == sizeof(count));
        }
        request.slot = completion.slot;
        request.length = completion.length;
        request.packet_sequence = completion.sequence;
        // Invalid completions are injected without creating a real loan.
        if (completion.slot < context.buffers.size() && completion.length <= context.binding.slot_bytes)
            expect(context.loans.emplace(completion.slot, completion.sequence).second);
        return 0;
    }
    case CHM5_DATA_RELEASE: {
        const auto loan = context.loans.find(request.slot);
        expect(loan != context.loans.end() && loan->second == request.packet_sequence);
        context.released.push_back({request.slot, 0, request.packet_sequence});
        context.lifecycle.push_back("release");
        context.loans.erase(loan);
        return negative(context.release_error);
    }
    default: throw std::runtime_error("unexpected endpoint data command");
    }
}
} // namespace

extern "C" {
int __wrap_open(const char* path, int flags, ...) {
    if (std::strcmp(path, "test-m5") != 0) {
        if (flags & O_CREAT) {
            va_list args; va_start(args, flags); const auto mode = va_arg(args, int); va_end(args);
            return __real_open(path, flags, mode);
        }
        return __real_open(path, flags);
    }
    expect(flags == (O_RDWR | O_NONBLOCK | O_CLOEXEC));
    ++driver.opens;
    if (driver.open_error) { errno = driver.open_error; return -1; }
    const int fd = ::eventfd(0, EFD_NONBLOCK | EFD_CLOEXEC);
    expect(fd >= 0);
    const auto id = driver.next_context++;
    chm5_bind binding{};
    binding.context = id;
    binding.process = ::getpid();
    binding.slots = 2;
    binding.slot_bytes = 2 * CHM5_PAGE_BYTES;
    binding.generation = 0x100000000ULL + id + 1;
    Context context{};
    context.fd = fd;
    context.binding = binding;
    driver.contexts.emplace(id, std::move(context));
    driver.fds.emplace(fd, id);
    return fd;
}
int __wrap_ioctl(int fd, unsigned long command, ...) {
    va_list args; va_start(args, command); auto* value = va_arg(args, void*); va_end(args);
    const auto found = driver.fds.find(fd);
    if (found == driver.fds.end()) return __real_ioctl(fd, command, value);
    expect(command == CHM5_IOC_BIND);
    if (driver.ioctl_error) { errno = driver.ioctl_error; return -1; }
    auto& context = driver.contexts.at(found->second);
    auto& binding = *static_cast<chm5_bind*>(value);
    expect(binding.version == CHM5_ABI_VERSION && binding.lane <= CHM5_BULK);
    context.binding.version = binding.version;
    context.binding.lane = binding.lane;
    binding = context.binding;
    if (driver.alter_binding) driver.alter_binding(binding);
    return 0;
}
void* __wrap_mmap(void* address, size_t size, int protection, int flags, int fd, off_t offset) {
    const auto found = driver.fds.find(fd);
    if (found == driver.fds.end()) return __real_mmap(address, size, protection, flags, fd, offset);
    auto& context = driver.contexts.at(found->second);
    expect(size == context.binding.slot_bytes && protection == (PROT_READ | PROT_WRITE));
    expect(address == nullptr && flags == MAP_SHARED && offset >= 0 && offset % CHM5_PAGE_BYTES == 0);
    const auto slot = static_cast<unsigned>(offset / CHM5_PAGE_BYTES);
    expect(slot < context.binding.slots && slot == context.buffers.size());
    ++driver.maps;
    if (driver.mmap_fail_at == driver.maps) { errno = ENOMEM; return MAP_FAILED; }
    auto* bytes = __real_mmap(nullptr, size, protection, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    expect(bytes != MAP_FAILED);
    context.buffers.push_back(static_cast<std::byte*>(bytes));
    return bytes;
}
int __wrap_munmap(void* address, size_t size) {
    for (auto& [id, context] : driver.contexts) {
        for (auto*& bytes : context.buffers) {
            if (bytes != address) continue;
            expect(!context.closed && context.model_closed && context.loans.empty());
            expect(size == context.binding.slot_bytes);
            bytes = nullptr;
            ++driver.unmaps;
            context.lifecycle.push_back("unmap");
            return __real_munmap(address, size);
        }
    }
    return __real_munmap(address, size);
}
int __wrap_close(int fd) {
    const auto found = driver.fds.find(fd);
    if (found != driver.fds.end()) {
        auto& context = driver.contexts.at(found->second);
        expect(!context.closed && context.loans.empty());
        for (auto* bytes : context.buffers) expect(bytes == nullptr);
        context.closed = true;
        context.lifecycle.push_back("fd-close");
        ++driver.closes;
        driver.fds.erase(found);
    }
    return __real_close(fd);
}
ssize_t __wrap_read(int fd, void* buffer, size_t size) {
    if (driver.fds.contains(fd)) {
        ++driver.reads;
        throw std::runtime_error("endpoint read its borrowed poll-only fd");
    }
    return __real_read(fd, buffer, size);
}
uint64_t m5_chimaera_control(uint64_t command, void* request, uint64_t size) {
    return invoke(false, false, command, request, size);
}
uint64_t m5_chimaera_control_addr(uint64_t command, void* request, uint64_t size) {
    return invoke(false, true, command, request, size);
}
uint64_t m5_chimaera_data(uint64_t command, void* request, uint64_t size) {
    return invoke(true, false, command, request, size);
}
uint64_t m5_chimaera_data_addr(uint64_t command, void* request, uint64_t size) {
    return invoke(true, true, command, request, size);
}
} // extern "C"

namespace {
void send_spans_and_error_contract(GuestM5Ops mode) {
    driver.reset();
    {
        M5Endpoint endpoint(M5Lane::bulk, "test-m5", mode);
        auto& context = driver.lane(M5Lane::bulk);
        auto header = pattern(52, 1), body = pattern(7000, 2);
        const auto saved_header = header, saved_body = body;
        context.submit_errors = {EAGAIN, ENOBUFS, 0, EINVAL, 8192};
        for (unsigned i = 0; i < 2; ++i) {
            expect(!endpoint.send(header, body));
            expect(context.accepted.empty() && header == saved_header && body == saved_body);
        }
        expect(endpoint.send(header, body));
        expect(context.accepted.size() == 1);
        Message expected = saved_header;
        expected.insert(expected.end(), saved_body.begin(), saved_body.end());
        // Calls must reference the original spans, not staged copies. A caller
        // may reuse both spans immediately after success or a rejected retry.
        for (const auto& call : context.calls) {
            if (!call.data || call.command != CHM5_DATA_SUBMITV) continue;
            expect(call.request.address0 == reinterpret_cast<std::uintptr_t>(header.data()));
            expect(call.request.address1 == reinterpret_cast<std::uintptr_t>(body.data()));
            expect(call.request.bytes0 == 52 && call.request.bytes1 == body.size());
        }
        header[0] = body[0] = std::byte{255};
        expect(context.accepted.front() == expected);
        error(EINVAL, [&] { endpoint.send(header, body); });
        error(EPROTO, [&] { endpoint.send(header, body); }); // Unexpected positive result.
        const auto calls = context.calls.size();
        error(EMSGSIZE, [&] { endpoint.send(std::span(header).first(51), body); });
        const auto oversized_body = Message(CHM5_MAX_BYTES - 51);
        error(EMSGSIZE, [&] { endpoint.send(header, oversized_body); });
        expect(context.calls.size() == calls);
        // Empty payload must pass its zero length without dereferencing it.
        expect(endpoint.send(header, {}));
        expect(context.accepted.back() == header);
        endpoint.open_channel(77 | m5_bulk_port_bit);
        expect(!context.calls.back().data && context.calls.back().command == CHM5_CTL_OPEN_CHANNEL);
        expect(context.calls.back().request.port == (77 | CHM5_BULK_PORT_BIT));
        endpoint.close_channel(77 | m5_bulk_port_bit);
        expect(context.calls.back().command == CHM5_CTL_CLOSE_CHANNEL);
        expect(context.calls.back().request.port == (77 | CHM5_BULK_PORT_BIT));
        context.channel_error = EACCES;
        error(EACCES, [&] { endpoint.open_channel(88 | m5_bulk_port_bit); });
        error(EACCES, [&] { endpoint.close_channel(88 | m5_bulk_port_bit); });
        for (const auto& call : context.calls) expect(call.address == (mode == GuestM5Ops::address));
    }
    expect(driver.closes == 1 && driver.maps == driver.unmaps && driver.reads == 0);
}

void loans_keep_pool_and_fd_until_final_slice() {
    driver.reset();
    auto endpoint = std::make_unique<M5Endpoint>(M5Lane::control, "test-m5");
    auto& context = driver.lane(M5Lane::control);
    const auto original = pattern(5000, 3);
    enqueue(context, 1, original, 0x100000042ULL);
    enqueue(context, 0, pattern(100, 4), 0x100000043ULL);
    expect(readable(endpoint->completion_fd()));
    auto loan = endpoint->take();
    expect(loan && loan->data() == context.buffers[1] && *loan == original);
    expect(readable(endpoint->completion_fd())); // One queued record remains.
    auto second = endpoint->take();
    expect(second && second->data() == context.buffers[0]);
    expect(!readable(endpoint->completion_fd()));
    Message independent = *loan;
    auto slice = loan->slice(100, 4000);
    auto moved = std::move(*loan);
    loan.reset();
    moved = Message{};
    expect(context.released.empty()); // The shared slice retains slot 1.
    second.reset();
    expect(context.released.size() == 1 && context.released[0].slot == 0);
    expect(context.released[0].sequence == 0x100000043ULL);
    enqueue(context, 0, pattern(100, 5), 0x100000044ULL);
    second = endpoint->take();
    second.reset();
    expect(context.released.size() == 2 && context.released[1].sequence == 0x100000044ULL);
    const auto fd = endpoint->completion_fd();
    endpoint.reset();
    expect(driver.closes == 0 && driver.unmaps == 0 && driver.fds.contains(fd));
    expect(!context.model_closed && independent == original);
    expect(slice == Message(original.begin() + 100, original.begin() + 4100));
    slice = Message{};
    expect(context.released.size() == 3 && context.released[2].slot == 1);
    expect(context.released[2].sequence == 0x100000042ULL);
    expect(context.lifecycle == std::vector<std::string>({
        "release", "release", "release", "model-close", "unmap", "unmap", "fd-close"}));
    expect(driver.closes == 1 && driver.unmaps == 2 && independent == original);
}

void batch_loans_and_empty_reap_race() {
    driver.reset();
    auto endpoint = std::make_unique<M5Endpoint>(M5Lane::control, "test-m5");
    auto& context = driver.lane(M5Lane::control);
    context.wake_before_arm = true;
    const auto calls = context.calls.size();
    expect(endpoint->take_batch().empty());
    expect(context.calls.size() == calls + 1);
    expect(context.calls.back().command == CHM5_DATA_REAP_BATCH);
    expect(readable(endpoint->completion_fd())); // Arrival just after the empty check.
    auto single = endpoint->take_batch();
    expect(single.size() == 1 && single[0] == pattern(100, 19));
    expect(context.loans.size() == 1); // No wait to fill the two-slot pool.
    single.clear();
    expect(context.released.size() == 1);
    enqueue(context, 1, pattern(5000, 3), 0x100000042ULL);
    enqueue(context, 0, pattern(100, 4), 0x100000043ULL);
    auto batch = endpoint->take_batch();
    expect(batch.size() == 2 && context.loans.size() == 2);
    expect(batch[0].data() == context.buffers[1] && batch[1].data() == context.buffers[0]);
    auto retained = batch[1].slice(10, 20);
    batch.clear();
    endpoint.reset();
    expect(context.released.size() == 1 && driver.closes == 0 && driver.unmaps == 0);
    expect(retained == pattern(100, 4).slice(10, 20));
    retained = Message{};
    expect(context.released.size() == 3 && context.loans.empty());
    expect(context.released[1].sequence == 0x100000042ULL);
    expect(context.released[2].sequence == 0x100000043ULL);
    expect(driver.closes == 1 && driver.unmaps == 2);
    expect(context.lifecycle == std::vector<std::string>({
        "release-batch", "release-batch", "model-close", "unmap", "unmap", "fd-close"}));
}

void independent_lanes_and_wake_before_arm() {
    driver.reset();
    {
        M5Endpoint control(M5Lane::control, "test-m5");
        auto bulk = std::make_unique<M5Endpoint>(M5Lane::bulk, "test-m5");
        auto& c = driver.lane(M5Lane::control);
        auto& b = driver.lane(M5Lane::bulk);
        expect(control.completion_fd() != bulk->completion_fd());
        expect(c.binding.context != b.binding.context && c.binding.generation != b.binding.generation);
        expect(c.buffers[0] != b.buffers[0] && c.buffers[1] != b.buffers[1]);
        c.wake_before_arm = true;
        const auto b_calls = b.calls.size();
        expect(!control.take()); // REAP was empty; arrival happens before ARM.
        expect(c.calls.size() == 3 && c.calls[1].data && c.calls[1].command == CHM5_DATA_REAP);
        expect(!c.calls[2].data && c.calls[2].command == CHM5_CTL_ARM);
        expect(c.notification == 1 && readable(control.completion_fd()));
        expect(b.calls.size() == b_calls && !readable(bulk->completion_fd()));
        auto arrived = control.take();
        expect(arrived && *arrived == pattern(100, 19));
        expect(!readable(control.completion_fd()));
        enqueue(b, 1, pattern(4000, 10), 41);
        auto bulk_loan = bulk->take();
        expect(bulk_loan && *bulk_loan == pattern(4000, 10));
        bulk.reset();
        expect(driver.closes == 0);
        bulk_loan.reset();
        expect(driver.closes == 1 && b.closed && !c.closed);
        arrived.reset();
        expect(c.released.back().sequence == 0x123456789ULL);
        expect(control.send(pattern(52, 11), pattern(100, 12)));
        expect(c.accepted.size() == 1 && b.accepted.empty());
        for (const auto status : {CHM5_STATUS_ERROR, CHM5_STATUS_SHUTDOWN}) {
            c.arm_status = status;
            c.arm_error = 0;
            error(ESHUTDOWN, [&] { control.take(); });
            c.arm_error = ESTALE;
            error(ESTALE, [&] { control.take(); });
        }
    }
    expect(driver.closes == 2 && driver.unmaps == driver.maps && driver.reads == 0);
}

void malformed_completions_and_release_failure() {
    driver.reset();
    {
        M5Endpoint endpoint(M5Lane::control, "test-m5");
        auto& context = driver.lane(M5Lane::control);
        for (const auto completion : {Completion{2, 100, 1}, Completion{0, 8193, 2}}) {
            // Queue a record whose descriptor points beyond the registered pool.
            std::uint64_t one = 1;
            expect(::write(context.fd, &one, sizeof(one)) == sizeof(one));
            context.ready.push_back(completion);
            error(EPROTO, [&] { endpoint.take(); });
            expect(context.loans.empty() && context.released.empty());
        }
        enqueue(context, 1, pattern(72, 2), 0x200000001ULL);
        auto loan = endpoint.take();
        expect(loan.has_value());
        context.release_error = ESTALE;
        loan.reset(); // Release cannot throw through a Message destructor.
        const auto calls = context.calls.size();
        error(ESTALE, [&] { endpoint.take(); });
        error(ESTALE, [&] { endpoint.send(pattern(52, 1), pattern(20, 2)); });
        error(ESTALE, [&] { endpoint.open_channel(77); });
        error(ESTALE, [&] { endpoint.close_channel(77); });
        expect(context.calls.size() == calls); // Failed generation is sticky.
    }
    expect(driver.closes == 1 && driver.maps == driver.unmaps);
}

void construction_validation_and_cleanup() {
    driver.reset();
    for (void* mapping : {static_cast<void*>(nullptr), MAP_FAILED}) {
        m5_mem = mapping;
        error(ENXIO, [] { M5Endpoint endpoint(M5Lane::control, "test-m5"); });
        expect(driver.opens == 0);
    }
    bool invalid = false;
    try { M5Endpoint endpoint(static_cast<M5Lane>(2), "test-m5", GuestM5Ops::instruction); }
    catch (const std::invalid_argument&) { invalid = true; }
    expect(invalid && driver.opens == 0);
    // Detailed instruction mode is independent of the libm5 address mapping.
    { M5Endpoint endpoint(M5Lane::control, "test-m5", GuestM5Ops::instruction); }
    m5_mem = &driver;
    driver.reset();
    driver.open_error = ENOENT;
    error(ENOENT, [] { M5Endpoint endpoint(M5Lane::control, "test-m5"); });
    expect(driver.closes == 0 && driver.maps == 0);
    driver.reset();
    driver.ioctl_error = EBUSY;
    error(EBUSY, [] { M5Endpoint endpoint(M5Lane::control, "test-m5"); });
    expect(driver.closes == 1 && driver.maps == 0);
    const std::vector<std::function<void(chm5_bind&)>> invalid_bindings{
        [](auto& b) { ++b.version; }, [](auto& b) { ++b.process; },
        [](auto& b) { b.lane = CHM5_BULK; }, [](auto& b) { b.generation = 0; },
        [](auto& b) { b.slots = 0; }, [](auto& b) { b.slots = CHM5_MAX_SLOTS + 1; },
        [](auto& b) { b.slot_bytes = 0; }, [](auto& b) { b.slot_bytes = CHM5_MAX_BYTES + CHM5_PAGE_BYTES; },
        [](auto& b) { --b.slot_bytes; }
    };
    for (const auto& alter : invalid_bindings) {
        driver.reset();
        driver.alter_binding = alter;
        error(EPROTO, [] { M5Endpoint endpoint(M5Lane::control, "test-m5"); });
        expect(driver.closes == 1 && driver.maps == 0);
    }
    for (const auto slot : {1U, 2U}) {
        driver.reset();
        driver.mmap_fail_at = slot;
        error(ENOMEM, [] { M5Endpoint endpoint(M5Lane::control, "test-m5"); });
        expect(driver.closes == 1 && driver.unmaps == slot - 1);
    }
    for (unsigned fault = 0; fault < 3; ++fault) {
        driver.reset();
        driver.confirm_error = fault == 0 ? EPROTONOSUPPORT : 0;
        driver.disagree_bytes = fault == 1;
        driver.disagree_slots = fault == 2;
        error(fault == 0 ? EPROTONOSUPPORT : EPROTO,
            [] { M5Endpoint endpoint(M5Lane::control, "test-m5"); });
        expect(driver.closes == 1 && driver.maps == driver.unmaps);
    }
    driver.reset();
}
} // namespace

int main() {
    m5_mem = &driver;
    send_spans_and_error_contract(GuestM5Ops::address);
    send_spans_and_error_contract(GuestM5Ops::instruction);
    loans_keep_pool_and_fd_until_final_slice();
    batch_loans_and_empty_reap_race();
    independent_lanes_and_wake_before_arm();
    malformed_completions_and_release_failure();
    construction_validation_and_cleanup();
}
