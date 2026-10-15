// SPDX-License-Identifier: MIT
// Standalone regression: retry envelopes may be recreated at the same address,
// while logical requests must retain separate timing and attribution metadata.
#include <cassert>
#include <iostream>
#include <new>
#include <unordered_map>
#include "chimaera/memory_workload.hh"
using namespace gem5;
using namespace gem5::chimaera;
struct Envelope
{
    RequestPtr request;
};
int
main()
{
    alignas(Envelope) unsigned char storage[sizeof(Envelope)];
    std::unordered_map<Envelope *, Tick> legacy;
    auto first = std::make_shared<Request>();
    first->setExtension(std::make_shared<DramReadAttempt>(10));
    first->setExtension(std::make_shared<MemoryWorkloadTag>(1));
    auto *packet = new (storage) Envelope{first};
    legacy.emplace(packet, 10);
    packet->~Envelope(); // Ruby refuses and destroys the transport envelope.
    packet = new (storage) Envelope{first};
    assert(packet->request->getExtension<DramReadAttempt>()->first == 10);
    assert(memoryWorkload(packet->request) == 1);
    packet->~Envelope();
    auto second = std::make_shared<Request>();
    second->setExtension(std::make_shared<DramReadAttempt>(100));
    second->setExtension(std::make_shared<MemoryWorkloadTag>(2));
    packet = new (storage) Envelope{second};
    legacy.emplace(packet, 100);
    // Reproduces the old bug deterministically: different request, stale time.
    assert(legacy.at(packet) == 10);
    assert(packet->request->getExtension<DramReadAttempt>()->first == 100);
    assert(memoryWorkload(packet->request) == 2);
    packet->~Envelope();
    std::cout << "REQUEST_IDENTITY_OK: reproduced legacy timestamp alias; "
                 "logical requests stay distinct\n";
}
