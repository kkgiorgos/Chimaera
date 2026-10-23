/* SPDX-License-Identifier: BSD-3-Clause */
#ifndef GEM5_CHIMAERA_M5_CHECKPOINT_HH
#define GEM5_CHIMAERA_M5_CHECKPOINT_HH

#include <algorithm>
#include <array>
#include <deque>
#include <istream>
#include <limits>
#include <memory>
#include <ostream>
#include <set>
#include <stdexcept>
#include <string>
#include <type_traits>
#include <vector>
#include "gem5/chimaera_m5.h"
#include "gem5/chimaera_rtps_rebind.hh"

// The format contains values and owned bytes only: never host pointers, mapped
// mailbox files, file descriptors, ThreadContexts or borrowed guest buffers.
namespace chimaera_m5_checkpoint {
using Frame = std::shared_ptr<const std::vector<uint8_t>>;
struct Packet { Frame frame; uint64_t epoch{}, sequence{}; };
enum class SlotState { posted, ready, loaned };
struct Slot {
    std::vector<uint64_t> pages;
    SlotState state{SlotState::posted};
    uint32_t length{};
    uint64_t epoch{}, sequence{};
};
struct Context {
    uint64_t generation{}, notification{};
    uint32_t process{}, lane{}, slotBytes{}, error{};
    bool active{}, shutdown{}, waitAdmission{};
    std::set<uint32_t> channels;
    std::vector<Slot> slots;
    std::deque<uint32_t> ready;
    std::deque<Packet> pending;
    size_t queuedBytes{};
};
struct Lane {
    std::deque<Packet> outgoing, unmatched;
    // Unconsumed external ring contents, before their internal queue suffix.
    std::deque<Packet> restoredIngress, restoredEgress;
    size_t outgoingBytes{}, unmatchedBytes{};
};
struct Geometry { uint64_t slots{}, records{}, ingress{}, bytes{}; };
struct State {
    std::array<Context, CHM5_MAX_CONTEXTS> contexts;
    std::array<Lane, 2> lanes;
    uint64_t irqPending{}, nextGeneration{}, admissionEpoch{}, packetSequence{};
    bool irqAsserted{};
    chimaera_rtps_rebind::Peers hostPeers;
};
class Archive {
    std::istream* input{};
    std::ostream* output{};
public:
    explicit Archive(std::istream& in) : input(&in) {}
    explicit Archive(std::ostream& out) : output(&out) {}
    bool reading() const { return input; }
    template<class T> void number(T& value, uint64_t maximum = std::numeric_limits<T>::max()) {
        static_assert(std::is_integral_v<T> && std::is_unsigned_v<T>);
        uint64_t word = input ? 0 : uint64_t(value);
        for (unsigned shift = 0; shift < 64; shift += 8) {
            if (input) {
                const auto byte = input->get();
                if (byte == std::char_traits<char>::eof()) throw std::runtime_error("Truncated m5ops checkpoint");
                word |= uint64_t(uint8_t(byte)) << shift;
            } else output->put(char(word >> shift));
        }
        if (word > maximum || word > std::numeric_limits<T>::max())
            throw std::runtime_error("Invalid m5ops checkpoint bound");
        value = T(word);
        if (output && !*output) throw std::runtime_error("Cannot write m5ops checkpoint");
    }
    void flag(bool& value) { unsigned word = value; number(word, 1); value = word; }
    void bytes(std::vector<uint8_t>& bytes, size_t maximum) {
        size_t size = bytes.size(); number(size, maximum);
        if (input) { bytes.resize(size); input->read(reinterpret_cast<char*>(bytes.data()), size); }
        else output->write(reinterpret_cast<const char*>(bytes.data()), size);
        if ((input && !*input) || (output && !*output)) throw std::runtime_error("Incomplete m5ops checkpoint payload");
    }
    void finish() {
        if (input && input->peek() != std::char_traits<char>::eof())
            throw std::runtime_error("Trailing m5ops checkpoint data");
        if (output) { output->flush(); if (!*output) throw std::runtime_error("Cannot flush m5ops checkpoint"); }
    }
};
inline void packets(Archive& archive, std::deque<Packet>& queue, size_t records,
                    size_t maximumBytes, unsigned lane, uint64_t lastSequence) {
    size_t count = queue.size(); archive.number(count, records);
    if (archive.reading()) queue.resize(count);
    size_t total = 0;
    for (auto& packet : queue) {
        archive.number(packet.epoch); archive.number(packet.sequence, lastSequence);
        auto bytes = archive.reading() ? std::vector<uint8_t>{} : *packet.frame;
        archive.bytes(bytes, std::min<size_t>(CHM5_MAX_BYTES, maximumBytes - total));
        if (bytes.size() < 72 || std::string(bytes.begin(), bytes.begin() + 4) != "CDDS" ||
            bytes[4] != 1 || bytes[5] != 3 || (bytes[12] != 1 && bytes[12] != 2) ||
            bytes[6] || bytes[7] || bytes[32] > 2 ||
            std::string(bytes.begin() + 52, bytes.begin() + 56) != "RTPS" ||
            (uint32_t(bytes[48]) | uint32_t(bytes[49]) << 8 | uint32_t(bytes[50]) << 16 |
             uint32_t(bytes[51]) << 24) != bytes.size() - 52 ||
            bool(bytes[31] & 0x40) != bool(lane))
            throw std::runtime_error("Invalid m5ops checkpoint frame");
        total += bytes.size();
        if (archive.reading()) packet.frame = std::make_shared<const std::vector<uint8_t>>(std::move(bytes));
    }
}
inline void transfer(Archive& archive, State& state, Geometry geometry) {
    uint64_t version = 2; archive.number(version, 2);
    if (version != 2) throw std::runtime_error("Unsupported m5ops checkpoint version");
    for (auto expected : {geometry.slots, geometry.records, geometry.ingress, geometry.bytes}) {
        auto actual = expected; archive.number(actual);
        if (actual != expected) throw std::runtime_error("Incompatible m5ops checkpoint geometry");
    }
    archive.number(state.irqPending); archive.number(state.nextGeneration);
    archive.number(state.admissionEpoch); archive.number(state.packetSequence); archive.flag(state.irqAsserted);
    size_t peers = state.hostPeers.size(); archive.number(peers, 128);
    auto savedPeers = state.hostPeers;
    if (archive.reading()) state.hostPeers.clear();
    auto peer = savedPeers.begin();
    for (size_t index = 0; index < peers; ++index) {
        chimaera_rtps_rebind::Prefix prefix{};
        chimaera_rtps_rebind::Peer value;
        if (!archive.reading()) { prefix = peer->first; value = peer++->second; }
        for (auto& byte : prefix) archive.number(byte);
        for (auto& byte : value.header) archive.number(byte);
        archive.number(value.sequence, uint64_t(std::numeric_limits<int64_t>::max()) - 1);
        if (std::memcmp(value.header.data(), "CDDS", 4) || value.header[12] != 1 ||
            (archive.reading() && !state.hostPeers.emplace(prefix, value).second))
            throw std::runtime_error("Invalid checkpoint host discovery identity");
    }
    std::set<uint64_t> uniquePages;
    for (auto& c : state.contexts) {
        archive.number(c.generation, state.nextGeneration); archive.number(c.notification);
        archive.number(c.process); archive.number(c.lane, 1); archive.number(c.slotBytes, CHM5_MAX_BYTES);
        archive.number(c.error); archive.flag(c.active); archive.flag(c.shutdown); archive.flag(c.waitAdmission);
        size_t count = c.channels.size(); archive.number(count, CHM5_MAX_CHANNELS);
        auto channels = c.channels;
        if (archive.reading()) channels.clear();
        auto entry = c.channels.begin();
        for (size_t index = 0; index < count; ++index) {
            uint32_t channel = archive.reading() ? 0 : *entry++;
            archive.number(channel);
            if (bool(channel & CHM5_BULK_PORT_BIT) != bool(c.lane) ||
                (archive.reading() && !channels.insert(channel).second))
                throw std::runtime_error("Invalid m5ops checkpoint channel");
        }
        c.channels = std::move(channels);
        count = c.slots.size(); archive.number(count, CHM5_MAX_SLOTS);
        if (archive.reading()) c.slots.resize(count);
        if (c.active && (!c.process || !c.generation || !count || !c.slotBytes || c.slotBytes % CHM5_PAGE_BYTES))
            throw std::runtime_error("Invalid active m5ops checkpoint context");
        for (auto& slot : c.slots) {
            size_t pages = slot.pages.size(); archive.number(pages, CHM5_MAX_BYTES / CHM5_PAGE_BYTES);
            if (pages != c.slotBytes / CHM5_PAGE_BYTES) throw std::runtime_error("Invalid m5ops checkpoint pages");
            if (archive.reading()) slot.pages.resize(pages);
            for (auto& page : slot.pages) {
                archive.number(page);
                if (page % CHM5_PAGE_BYTES || !uniquePages.insert(page).second)
                    throw std::runtime_error("Overlapping m5ops checkpoint pages");
            }
            unsigned kind = unsigned(slot.state); archive.number(kind, 2); slot.state = SlotState(kind);
            archive.number(slot.length, c.slotBytes); archive.number(slot.epoch, state.admissionEpoch);
            archive.number(slot.sequence, state.packetSequence);
        }
        count = c.ready.size(); archive.number(count, c.slots.size());
        if (archive.reading()) c.ready.resize(count);
        std::set<uint32_t> ready;
        for (auto& id : c.ready) {
            archive.number(id);
            if (id >= c.slots.size() || c.slots[id].state != SlotState::ready || !ready.insert(id).second)
                throw std::runtime_error("Invalid m5ops checkpoint completion queue");
        }
        for (size_t id = 0; id < c.slots.size(); ++id)
            if ((c.slots[id].state == SlotState::ready) != bool(ready.count(id)))
                throw std::runtime_error("Incomplete m5ops checkpoint completion queue");
        packets(archive, c.pending, geometry.records, geometry.bytes, c.lane, state.packetSequence);
        c.queuedBytes = 0;
        for (const auto& p : c.pending) c.queuedBytes += p.frame->size();
        if (!c.active && (!c.slots.empty() || !c.channels.empty() || !c.pending.empty()))
            throw std::runtime_error("Inactive m5ops checkpoint owns resources");
    }
    for (unsigned index = 0; index < state.lanes.size(); ++index) {
        auto& lane = state.lanes[index];
        packets(archive, lane.outgoing, geometry.records, geometry.bytes, index, state.packetSequence);
        packets(archive, lane.unmatched, geometry.records, geometry.bytes, index, state.packetSequence);
        packets(archive, lane.restoredIngress, geometry.slots, geometry.slots * CHM5_MAX_BYTES, index, state.packetSequence);
        packets(archive, lane.restoredEgress, geometry.slots, geometry.slots * CHM5_MAX_BYTES, index, state.packetSequence);
        lane.outgoingBytes = lane.unmatchedBytes = 0;
        for (const auto& p : lane.outgoing) lane.outgoingBytes += p.frame->size();
        for (const auto& p : lane.unmatched) lane.unmatchedBytes += p.frame->size();
    }
    archive.finish();
}
} // namespace chimaera_m5_checkpoint
#endif
