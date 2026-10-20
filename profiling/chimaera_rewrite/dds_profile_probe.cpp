#include <fastdds/dds/domain/DomainParticipantFactory.hpp>
#include <fastdds/dds/domain/qos/DomainParticipantQos.hpp>
#include <fastdds/rtps/transport/shared_mem/SharedMemTransportDescriptor.h>
#include <iostream>
#include <cstdlib>
int main(int argc, char **argv) {
    using namespace eprosima::fastdds;
    if (argc != 2) return 2;
    setenv("FASTRTPS_DEFAULT_PROFILES_FILE", argv[1], 1);
    auto *factory = dds::DomainParticipantFactory::get_instance();
    if (factory->load_profiles() !=
        eprosima::fastrtps::types::ReturnCode_t::RETCODE_OK) return 3;
    const auto &qos = factory->get_default_participant_qos();
    const auto &transport = qos.transport();
    if (transport.use_builtin_transports || transport.user_transports.size() != 1) return 4;
    const auto shm = std::dynamic_pointer_cast<rtps::SharedMemTransportDescriptor>(
        transport.user_transports[0]);
    if (!shm) return 5;
    std::cout << "{\"builtin_transports\":false,\"transport_count\":1,\"max_message_bytes\":"
              << shm->max_message_size() << ",\"segment_bytes\":" << shm->segment_size()
              << "}\n";
    return shm->max_message_size() == 4194304 && shm->segment_size() == 16777216 ? 0 : 6;
}
