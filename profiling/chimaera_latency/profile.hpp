#pragma once

// Diagnostic overlay only. No wire changes and no file I/O during measurement.
#include <atomic>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <functional>
#include <mutex>
#include <sstream>
#include <span>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

namespace chimaera::profile {
constexpr uint64_t unset = UINT64_MAX;
struct Row {
    const char* stage;
    char event;
    uint64_t id, sim, os, sequence, scheduled, bytes;
    int stream, direction;
};
struct State {
    std::atomic<bool> active{false};
    std::atomic<uint64_t> next{0};
    std::mutex mutex;
    std::vector<Row> rows;
    std::function<uint64_t()> clock;
    std::function<void(const std::string&)> writer;
    const char* side = "unset";
    uint64_t dropped = 0;
};
inline State& state() { static State value; return value; }
inline void initialize(const char* side) {
    auto& s = state();
    s.side = side;
    if (const auto* enabled = std::getenv("CHIMAERA_PROFILE"); enabled && std::string_view(enabled) == "1") {
        s.rows.reserve(1000000);
        s.active = true;
    }
}
inline uint64_t os_now() {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count();
}
inline void record(const char* stage, char event = 'I', uint64_t id = 0,
                   int stream = -1, int direction = -1, uint64_t sequence = unset,
                   uint64_t scheduled = unset, uint64_t bytes = 0) {
    auto& s = state();
    if (!s.active.load(std::memory_order_relaxed)) return;
    std::lock_guard lock(s.mutex);
    if (!s.active.load(std::memory_order_relaxed) || !s.clock) return;
    const auto os = os_now();
    const auto sim = s.clock();
    if (sim == unset) return;
    if (s.rows.size() == 1000000) { ++s.dropped; return; }
    s.rows.push_back({stage, event, id, sim, os, sequence, scheduled, bytes, stream, direction});
}
class Scope {
    const char* stage_;
    uint64_t id_;
public:
    explicit Scope(const char* stage) : stage_(stage), id_(state().next.fetch_add(1)) {
        record(stage_, 'B', id_);
    }
    ~Scope() { record(stage_, 'E', id_); }
};
inline uint64_t little(const std::byte* data, size_t count) {
    uint64_t value = 0;
    for (size_t i = 0; i < count; ++i) value |= uint64_t(std::to_integer<unsigned>(data[i])) << (8*i);
    return value;
}
inline uint32_t big(const std::byte* data) {
    uint32_t value = 0;
    for (size_t i = 0; i < 4; ++i) value = (value << 8) | std::to_integer<unsigned>(data[i]);
    return value;
}
inline void payload(const char* stage, std::string_view key, std::span<const std::byte> data) {
    if (!state().active.load(std::memory_order_relaxed)) return;
    const int direction = key.find("host_to_guest") != key.npos ? 0 : 1;
    if (key.find("UInt8MultiArray") != key.npos) {
        // The synthetic envelope follows the CDR encapsulation and empty layout.
        for (size_t offset = 0; offset + 40 <= data.size() && offset < 64; ++offset) {
            if (little(data.data() + offset, 8) == 0x3141544144424c43ULL) {
                record(stage, 'I', 0, 0, direction, little(data.data()+offset+8, 8),
                       little(data.data()+offset+16, 8), data.size());
                return;
            }
        }
        return;
    }
    constexpr std::string_view names[] = {"", "/ball/left_image/", "/ball/right_image/",
        "/ball/joint_states/", "/ball/effort_command/", "/ball/clock/"};
    for (int stream = 1; stream <= 5; ++stream) {
        if (key.find(names[stream]) == key.npos) continue;
        if (data.size() < 12 || std::to_integer<unsigned>(data[1]) != 1)
            throw std::runtime_error("Profiler requires little-endian CDR with a header stamp");
        const auto scheduled = little(data.data()+4, 4)*1000000000ULL + little(data.data()+8, 4);
        record(stage, 'I', 0, stream, direction, unset, scheduled, data.size());
        return;
    }
}
inline void frame(const char* stage, std::span<const std::byte> data) {
    if (!state().active.load(std::memory_order_relaxed) || data.size() < 8) return;
    const auto length = big(data.data()+4);
    if (length > data.size()-8) return;
    if (std::memcmp(data.data(), "CBP1", 4) == 0) {
        frame(stage, data.subspan(8, length));
        frame(stage, data.subspan(8+length));
    } else if (std::memcmp(data.data(), "CBR1", 4) == 0) {
        payload(stage, std::string_view(reinterpret_cast<const char*>(data.data()+8), length),
                data.subspan(8+length));
    }
}
template<class Batch> void frames(const char* stage, const Batch& batch) {
    if (!state().active.load(std::memory_order_relaxed)) return;
    for (const auto& message : batch) frame(stage, message);
}
inline void finish() {
    auto& s = state();
    if (!s.active.exchange(false)) return;
    std::lock_guard lock(s.mutex);
    std::ostringstream output;
    output << "side,stage,event,id,sim_ns,os_ns,stream,direction,sequence,scheduled_ns,bytes\n";
    for (const auto& r : s.rows)
        output << s.side << ',' << r.stage << ',' << r.event << ',' << r.id << ',' << r.sim << ','
               << r.os << ',' << r.stream << ',' << r.direction << ',' << r.sequence << ','
               << r.scheduled << ',' << r.bytes << '\n';
    if (s.dropped) throw std::runtime_error("Profiler buffer overflow");
    if (s.writer) s.writer(output.str());
    else if (const auto* path = std::getenv("CHIMAERA_PROFILE_OUTPUT")) {
        std::ofstream file(path);
        file << output.str();
        if (!file) throw std::runtime_error("Cannot write host profile");
    }
}
} // namespace chimaera::profile
