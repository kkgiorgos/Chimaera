#include <gem5/chimaera_m5_checkpoint.hh>
#include <cassert>
#include <sstream>
using namespace chimaera_m5_checkpoint;
int main() {
    assert(chimaera_rtps_rebind::discovery_port(
        {1, 25150, 25160, CHM5_APPLICATION_PORT_BIT | 11}, CHM5_APPLICATION_PORT_BIT) == 25150);
    assert(!chimaera_rtps_rebind::discovery_port(
        {1, CHM5_APPLICATION_PORT_BIT | 11}, CHM5_APPLICATION_PORT_BIT));
    Geometry geometry{16, 64, 64, 64 * 1024 * 1024};
    State original;
    chimaera_rtps_rebind::Prefix prefix{}; prefix.fill(17);
    chimaera_rtps_rebind::Peer peer;
    std::copy_n("CDDS",4,peer.header.begin()); peer.header[12]=1; peer.sequence=14;
    original.hostPeers.emplace(prefix,peer);
    original.packetSequence = 9; original.admissionEpoch = 3;
    original.nextGeneration = 7; original.irqPending = 1; original.irqAsserted = true;
    auto& c = original.contexts[0];
    c.active = true; c.generation = 7; c.notification = 12; c.process = 41;
    c.slotBytes = CHM5_PAGE_BYTES; c.channels.insert(CHM5_APPLICATION_PORT_BIT | 11);
    c.slots.resize(3);
    for (unsigned i = 0; i < 3; ++i) c.slots[i].pages = {0x100000 + i * CHM5_PAGE_BYTES};
    c.slots[0].state = SlotState::loaned; c.slots[0].sequence = 8;
    c.slots[1].state = SlotState::ready; c.slots[1].sequence = 9;
    c.ready.push_back(1);
    std::vector<uint8_t> bytes(80); std::copy_n("CDDS", 4, bytes.begin());
    bytes[4] = 1; bytes[5] = 3; bytes[12] = 1;
    bytes[48] = 28; std::copy_n("RTPS", 4, bytes.begin() + 52);
    Packet packet{std::make_shared<const std::vector<uint8_t>>(bytes), 3, 9};
    c.pending.push_back(packet);
    original.lanes[0].restoredIngress.push_back(packet);
    original.lanes[0].restoredEgress.push_back(packet);
    original.lanes[0].outgoing.push_back(packet);
    std::ostringstream out(std::ios::binary); Archive writer(out); transfer(writer, original, geometry);
    const auto saved = out.str();
    State restored;
    std::istringstream in(saved, std::ios::binary); Archive reader(in); transfer(reader, restored, geometry);
    assert(restored.hostPeers.at(prefix).sequence == 14);
    assert(restored.contexts[0].slots[0].state == SlotState::loaned);
    assert(restored.contexts[0].ready.front() == 1);
    assert(restored.contexts[0].notification == 12);
    assert(restored.contexts[0].queuedBytes == 80);
    assert(restored.lanes[0].outgoingBytes == 80);
    assert(*restored.lanes[0].restoredIngress[0].frame == bytes);
    for (const auto& bad : {saved.substr(0, saved.size() - 1), saved + "junk", std::string(8, '\xff')}) {
        bool rejected = false;
        try { State state; std::istringstream input(bad); Archive archive(input); transfer(archive, state, geometry); }
        catch (const std::runtime_error&) { rejected = true; }
        assert(rejected);
    }
    bool rejected = false;
    try { State state; std::istringstream input(saved); Archive archive(input); transfer(archive, state, {8,64,64,geometry.bytes}); }
    catch (const std::runtime_error&) { rejected = true; }
    assert(rejected);
    original.contexts[0].slots[2].pages = original.contexts[0].slots[0].pages;
    rejected = false;
    try { std::ostringstream output; Archive archive(output); transfer(archive, original, geometry); }
    catch (const std::runtime_error&) { rejected = true; }
    assert(rejected);
}
