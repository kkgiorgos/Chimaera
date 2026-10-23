// Experimental guest-only middleware memory-cost boundary. Preload this library
// in both comparison arms, enabling functional copies only in the offloaded arm.
// Application computation outside RMW/transport scopes keeps ordinary accesses.
#include <gem5/chimaera_m5.h>
#include <gem5/m5ops.h>
#include <m5_mmap.h>
#include <rmw/rmw.h>

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <dlfcn.h>
#include <limits>
#include <sys/mman.h>
#include <unistd.h>

namespace {
using Copy = void* (*)(void*, const void*, std::size_t);
using Fill = void* (*)(void*, int, std::size_t);
Copy native_copy{}, native_move{};
Fill native_fill{};
bool enabled{};
thread_local unsigned scope_depth{};
thread_local bool submitting{};
constexpr std::size_t threshold = 64 * 1024;
std::atomic<uint64_t> copy_calls{}, fill_calls{}, copy_bytes{}, fill_bytes{}, fallbacks{};
#ifdef CHIMAERA_COPY_TESTING
using TestCall = uint64_t (*)(bool, uint64_t, chm5_request&);
TestCall test_call{};
#endif

template<typename Function>
Function next(const char* name) {
    auto* address = ::dlsym(RTLD_NEXT, name);
    if (!address) {
        std::fprintf(stderr, "Chimaera functional copies: missing %s\n", name);
        ::_exit(78);
    }
    return reinterpret_cast<Function>(address);
}

uint64_t call(bool data, uint64_t command, chm5_request& request) {
#ifdef CHIMAERA_COPY_TESTING
    if (test_call) return test_call(data, command, request);
#endif
    std::atomic_thread_fence(std::memory_order_seq_cst);
    const auto result = (data ? m5_chimaera_data_addr : m5_chimaera_control_addr)(
        command, &request, sizeof(request));
    std::atomic_thread_fence(std::memory_order_seq_cst);
    return result;
}

__attribute__((constructor)) void initialize() {
    native_copy = next<Copy>("memcpy");
    native_move = next<Copy>("memmove");
    native_fill = next<Fill>("memset");
    const auto* setting = std::getenv("CHIMAERA_FUNCTIONAL_MIDDLEWARE_COPIES");
    if (!setting || !*setting || (setting[0] == '0' && !setting[1])) return;
    if (setting[0] != '1' || setting[1]) {
        std::fputs("CHIMAERA_FUNCTIONAL_MIDDLEWARE_COPIES must be 0 or 1\n", stderr);
        ::_exit(78);
    }
    m5op_addr = 0xFFFF0000;
    map_m5_mem();
    if (!m5_mem || m5_mem == MAP_FAILED) ::_exit(78);
    chm5_request request{};
    request.version = CHM5_ABI_VERSION;
    request.size = sizeof(request);
    if (call(false, CHM5_CTL_QUERY, request) ||
        !(request.status & CHM5_FEATURE_FUNCTIONAL_COPIES)) {
        std::fputs("Simulator lacks experimental functional-copy support\n", stderr);
        ::_exit(78);
    }
    enabled = true;
    std::fputs("Chimaera: functional bulk middleware copies enabled (changed memory-cost model)\n", stderr);
}

__attribute__((destructor)) void summarize() {
    if (enabled) std::fprintf(stderr,
        "Chimaera functional copies: copies=%llu copy_bytes=%llu fills=%llu fill_bytes=%llu fallbacks=%llu\n",
        static_cast<unsigned long long>(copy_calls.load()),
        static_cast<unsigned long long>(copy_bytes.load()),
        static_cast<unsigned long long>(fill_calls.load()),
        static_cast<unsigned long long>(fill_bytes.load()),
        static_cast<unsigned long long>(fallbacks.load()));
}

bool eligible(std::size_t count) {
    return enabled && scope_depth && !submitting && count >= threshold && count <= CHM5_MAX_BYTES;
}

bool offload(void* destination, const void* source, std::size_t count, int value, bool fill) {
    const auto dst = reinterpret_cast<uintptr_t>(destination);
    const auto src = reinterpret_cast<uintptr_t>(source);
    const auto maximum = std::numeric_limits<uintptr_t>::max();
    if (dst > maximum - count || (!fill && src > maximum - count)) return false;
    // Overlapping memmove remains native. The model's COPY explicitly requires
    // nonoverlap, including because prefault stores must not change later input.
    if (!fill && src < dst + count && dst < src + count) return false;
    submitting = true;
    // Functional proxies do not resolve demand/COW faults for userspace. Dirty
    // one byte of every destination page with its final value first. These few
    // guest instructions/accesses remain modeled and allocate ordinary pages.
    auto* output = static_cast<volatile unsigned char*>(destination);
    const auto* input = static_cast<const volatile unsigned char*>(source);
    std::size_t offset = 0;
    while (offset < count) {
        output[offset] = fill ? static_cast<unsigned char>(value) : input[offset];
        offset += CHM5_PAGE_BYTES - ((dst + offset) % CHM5_PAGE_BYTES);
    }
    chm5_request request{};
    request.version = CHM5_ABI_VERSION;
    request.size = sizeof(request);
    request.address0 = src;
    request.address1 = dst;
    request.bytes0 = count;
    request.length = static_cast<unsigned char>(value);
    const auto result = call(true, fill ? CHM5_DATA_FUNCTIONAL_FILL : CHM5_DATA_FUNCTIONAL_COPY, request);
    submitting = false;
    if (result) {
        ++fallbacks;
        return false; // Redo the entire operation natively after any partial write.
    }
    if (fill) {
        ++fill_calls;
        fill_bytes += count;
    } else {
        ++copy_calls;
        copy_bytes += count;
    }
    return true;
}

void* early_copy(void* destination, const void* source, std::size_t count, bool backwards = false) {
    auto* output = static_cast<volatile unsigned char*>(destination);
    const auto* input = static_cast<const volatile unsigned char*>(source);
    for (std::size_t index = 0; index < count; ++index) {
        const auto offset = backwards ? count - index - 1 : index;
        output[offset] = input[offset];
    }
    return destination;
}
struct Scope {
    Scope() { if (enabled) ++scope_depth; }
    ~Scope() { if (enabled) --scope_depth; }
};
} // namespace

#ifdef CHIMAERA_COPY_TESTING
extern "C" void chimaera_copy_test_enable(TestCall backend) {
    test_call = backend;
    enabled = backend != nullptr;
}
#endif

extern "C" void chimaera_middleware_copy_scope(int delta) {
    if (!enabled) return;
    if (delta == 1) ++scope_depth;
    else if (delta == -1 && scope_depth) --scope_depth;
    else std::abort();
}

extern "C" void* memcpy(void* destination, const void* source, std::size_t count) {
    if (eligible(count) && offload(destination, source, count, 0, false)) return destination;
    return native_copy ? native_copy(destination, source, count) : early_copy(destination, source, count);
}
extern "C" void* memmove(void* destination, const void* source, std::size_t count) {
    if (eligible(count) && offload(destination, source, count, 0, false)) return destination;
    return native_move ? native_move(destination, source, count) : early_copy(destination, source, count,
        reinterpret_cast<uintptr_t>(destination) > reinterpret_cast<uintptr_t>(source));
}
extern "C" void* memset(void* destination, int value, std::size_t count) {
    if (eligible(count) && offload(destination, nullptr, count, value, true)) return destination;
    if (native_fill) return native_fill(destination, value, count);
    auto* output = static_cast<volatile unsigned char*>(destination);
    for (std::size_t index = 0; index < count; ++index) output[index] = static_cast<unsigned char>(value);
    return destination;
}

extern "C" rmw_ret_t rmw_publish(const rmw_publisher_t* publisher, const void* message,
                                 rmw_publisher_allocation_t* allocation) {
    static auto original = next<decltype(&rmw_publish)>("rmw_publish");
    Scope scope;
    return original(publisher, message, allocation);
}
extern "C" rmw_ret_t rmw_take(const rmw_subscription_t* subscription, void* message, bool* taken,
                              rmw_subscription_allocation_t* allocation) {
    static auto original = next<decltype(&rmw_take)>("rmw_take");
    Scope scope;
    return original(subscription, message, taken, allocation);
}
extern "C" rmw_ret_t rmw_take_with_info(const rmw_subscription_t* subscription, void* message,
    bool* taken, rmw_message_info_t* info, rmw_subscription_allocation_t* allocation) {
    static auto original = next<decltype(&rmw_take_with_info)>("rmw_take_with_info");
    Scope scope;
    return original(subscription, message, taken, info, allocation);
}
extern "C" rmw_ret_t rmw_publish_serialized_message(const rmw_publisher_t* publisher,
    const rmw_serialized_message_t* message, rmw_publisher_allocation_t* allocation) {
    static auto original = next<decltype(&rmw_publish_serialized_message)>("rmw_publish_serialized_message");
    Scope scope;
    return original(publisher, message, allocation);
}
