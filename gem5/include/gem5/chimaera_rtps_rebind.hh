/* SPDX-License-Identifier: BSD-3-Clause */
#ifndef GEM5_CHIMAERA_RTPS_REBIND_HH
#define GEM5_CHIMAERA_RTPS_REBIND_HH
#include <algorithm>
#include <array>
#include <cstdint>
#include <cstring>
#include <limits>
#include <map>
#include <optional>
#include <set>
#include <stdexcept>
#include <vector>
namespace chimaera_rtps_rebind {
using Prefix = std::array<uint8_t, 12>;
struct Peer { std::array<uint8_t, 52> header{}; uint64_t sequence{}; };
using Peers = std::map<Prefix, Peer>;
inline std::optional<uint32_t> discovery_port(const std::set<uint32_t>& channels,
                                               uint32_t applicationBit) {
    // Port 1 carries ROS graph data; the ordered built-in metatraffic ports
    // follow it. Application topic locators occupy the high port range.
    for (auto port : channels)
        if (port > 1 && !(port & applicationBit)) return port;
    return std::nullopt;
}
inline uint32_t word(const uint8_t* p, bool little = true) {
    uint32_t value = 0;
    for (unsigned i = 0; i < 4; ++i) value |= uint32_t(p[i]) << ((little ? i : 3-i) * 8);
    return value;
}
// Retain only the bounded SPDP identity/sequence, never application payloads.
inline void remember(const std::vector<uint8_t>& frame, Peers& peers) {
    if (frame.size() < 72 || std::memcmp(frame.data()+52, "RTPS", 4)) return;
    for (size_t offset = 72; offset + 4 <= frame.size();) {
        const auto* sub = frame.data()+offset;
        const bool little = sub[1] & 1;
        const auto length = little ? unsigned(sub[2]) | unsigned(sub[3]) << 8 :
                                    unsigned(sub[3]) | unsigned(sub[2]) << 8;
        const size_t size = length ? length : frame.size()-offset-4;
        if (size > frame.size()-offset-4) return;
        // DATA body: extraFlags, inlineQosOffset, readerId, writerId, writerSN.
        if (sub[0] == 0x15 && size >= 20 && !std::memcmp(sub+12, "\x00\x01\x00\xc2", 4)) {
            Prefix prefix; std::copy_n(frame.data()+60, 12, prefix.begin());
            auto found = peers.find(prefix);
            if (found == peers.end()) {
                if (peers.size() >= 128) throw std::runtime_error("Too many checkpoint DDS peers");
                found = peers.emplace(prefix, Peer{}).first;
                std::copy_n(frame.data(), 52, found->second.header.begin());
            }
            const auto sequence = uint64_t(word(sub+16, little)) << 32 | word(sub+20, little);
            if (sequence >= uint64_t(std::numeric_limits<int64_t>::max()))
                throw std::runtime_error("Invalid checkpoint SPDP sequence");
            found->second.sequence = std::max(found->second.sequence, sequence);
        }
        offset += 4+size;
    }
}
// Standard RTPS SPDP dispose/unregister, with inline participant key and status.
// Its sequence follows every announcement admitted before the checkpoint.
inline std::vector<uint8_t> dispose(const Prefix& prefix, const Peer& peer) {
    std::vector<uint8_t> frame(128);
    std::copy(peer.header.begin(), peer.header.end(), frame.begin());
    std::fill(frame.begin()+32, frame.begin()+48, 0); // Discovery broadcast.
    frame[48]=76; frame[49]=frame[50]=frame[51]=0;
    auto* rtps=frame.data()+52;
    std::memcpy(rtps,"RTPS",4);rtps[4]=2;rtps[5]=3;rtps[6]=1;rtps[7]=15;
    std::copy(prefix.begin(),prefix.end(),rtps+8);
    auto* data=rtps+20;
    data[0]=0x15;data[1]=3;data[2]=52; // DATA, little-endian + inline QoS.
    data[6]=16; // Offset to inline QoS after writerSN.
    std::memcpy(data+8,"\x00\x01\x00\xc7",4);
    std::memcpy(data+12,"\x00\x01\x00\xc2",4);
    const auto sequence=peer.sequence+1;
    for(unsigned i=0;i<4;++i){data[16+i]=uint8_t(sequence>>(32+8*i));data[20+i]=uint8_t(sequence>>(8*i));}
    data[24]=0x70;data[26]=16; // PID_KEY_HASH, participant GUID.
    std::copy(prefix.begin(),prefix.end(),data+28);
    data[42]=1;data[43]=0xc1;
    data[44]=0x71;data[46]=4;data[51]=3; // PID_STATUS_INFO: disposed/unregistered.
    data[52]=1; // PID_SENTINEL.
    return frame;
}
} // namespace chimaera_rtps_rebind
#endif
