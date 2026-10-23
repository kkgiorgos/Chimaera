#include <gem5/chimaera_m5.h>
#include <algorithm>
#include <array>
#include <cerrno>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

extern "C" void chimaera_copy_test_enable(uint64_t (*)(bool, uint64_t, chm5_request&));
extern "C" void chimaera_middleware_copy_scope(int);

namespace {
unsigned calls{};
bool fault{};
void check(bool condition) {
    if (!condition) std::abort();
}
uint64_t backend(bool data, uint64_t command, chm5_request& request) {
    check(data && request.version == CHM5_ABI_VERSION && request.size == sizeof(request));
    ++calls;
    auto* destination = reinterpret_cast<void*>(request.address1);
    if (fault) {
        std::memset(destination, 0x73, request.bytes0 / 2);
        return uint64_t(-int64_t(EFAULT));
    }
    if (command == CHM5_DATA_FUNCTIONAL_COPY)
        std::memcpy(destination, reinterpret_cast<const void*>(request.address0), request.bytes0);
    else {
        check(command == CHM5_DATA_FUNCTIONAL_FILL);
        std::memset(destination, request.length, request.bytes0);
    }
    return 0;
}
} // namespace

int main() {
    chimaera_copy_test_enable(backend);
    for (const auto size : std::array<std::size_t, 6>{0, 16, 65535, 65536, 65537, 8 * 1024 * 1024 + 77}) {
        for (const auto offset : std::array<std::size_t, 3>{0, 3, 4095}) {
            std::vector<unsigned char> source(size + offset + 32), destination(size + offset + 32, 0x5a);
            for (std::size_t index = 0; index < source.size(); ++index)
                source[index] = static_cast<unsigned char>(index * 31 + index / 251);
            auto expected = destination;
            // Application copies outside a middleware scope must remain native.
            const auto before = calls;
            std::memcpy(destination.data() + offset, source.data() + offset, size);
            check(calls == before);
            std::copy_n(source.begin() + offset, size, expected.begin() + offset);
            check(destination == expected);

            chimaera_middleware_copy_scope(1);
            std::memset(destination.data() + offset, 0xb7, size);
            chimaera_middleware_copy_scope(-1);
            std::fill_n(expected.begin() + offset, size, 0xb7);
            check(destination == expected);
            check(calls == before + (size >= 65536 ? 1 : 0));

            // A nested scope stays enabled after its inner scope closes.
            chimaera_middleware_copy_scope(1);
            chimaera_middleware_copy_scope(1);
            chimaera_middleware_copy_scope(-1);
            std::memcpy(destination.data() + offset, source.data() + offset, size);
            chimaera_middleware_copy_scope(-1);
            std::copy_n(source.begin() + offset, size, expected.begin() + offset);
            check(destination == expected);
        }
    }
    std::vector<unsigned char> source(128 * 1024, 0xa3), destination(source.size(), 0);
    fault = true;
    chimaera_middleware_copy_scope(1);
    std::memcpy(destination.data(), source.data(), source.size());
    chimaera_middleware_copy_scope(-1);
    check(destination == source); // A partial model write must not corrupt fallback.
    fault = false;

    for (bool backwards : {false, true}) {
        auto actual = source;
        for (std::size_t index = 0; index < actual.size(); ++index) actual[index] = index % 251;
        auto expected = actual;
        const auto from = backwards ? 0 : 17;
        const auto to = backwards ? 17 : 0;
        const auto size = actual.size() - 17;
        // Compute the reference before enabling a scope.
        std::memmove(expected.data() + to, expected.data() + from, size);
        const auto before = calls;
        chimaera_middleware_copy_scope(1);
        std::memmove(actual.data() + to, actual.data() + from, size);
        chimaera_middleware_copy_scope(-1);
        check(actual == expected && calls == before);
    }
    chimaera_copy_test_enable(nullptr);
    std::puts("Functional copy scope, sizes, page alignment, overlap, and partial-failure fallback passed");
}
