#pragma once

#include <fastdds/rtps/transport/TransportDescriptorInterface.h>

#include <fastdds/dds/domain/qos/DomainParticipantQos.hpp>
#include <fastdds/dds/publisher/qos/DataWriterQos.hpp>
#include <fastdds/dds/subscriber/qos/DataReaderQos.hpp>
#include <string>

namespace chimaera_dds
{
constexpr int transport_kind = 0x434844;
constexpr unsigned max_packet_bytes = 1024 * 1024;

struct Descriptor final : eprosima::fastdds::rtps::TransportDescriptorInterface
{
  explicit Descriptor(std::string socket, unsigned side);
  eprosima::fastdds::rtps::TransportInterface * create_transport() const override;
  uint32_t min_send_buffer_size() const override { return maxMessageSize; }
  std::string socket;
  unsigned side;
};

// These hooks are called by the pinned rmw_fastrtps overlay. Without
// CHIMAERA_DDS_SOCKET the original RMW behavior is preserved.
void configure_participant(eprosima::fastdds::dds::DomainParticipantQos & qos);
std::string wire_topic(const char * topic, const std::string & dds_type = {});
std::string local_topic(const std::string & topic);
void configure_writer(const char * topic, eprosima::fastdds::dds::DataWriterQos & qos);
void configure_reader(const char * topic, eprosima::fastdds::dds::DataReaderQos & qos);
void endpoint_created(const char * topic, bool publisher);
void endpoint_destroyed(const char * topic, bool publisher) noexcept;
}  // namespace chimaera_dds
