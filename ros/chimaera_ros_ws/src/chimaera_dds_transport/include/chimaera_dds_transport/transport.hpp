#pragma once

#include <fastdds/rtps/transport/TransportDescriptorInterface.h>

#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <fastdds/dds/domain/qos/DomainParticipantQos.hpp>
#include <fastdds/dds/publisher/qos/DataWriterQos.hpp>
#include <fastdds/dds/subscriber/qos/DataReaderQos.hpp>
#include <string>

namespace chimaera_dds
{
constexpr int transport_kind = 0x434844;
constexpr unsigned max_packet_bytes = 1024 * 1024;
constexpr unsigned cdds_header_bytes = 52;
constexpr unsigned m5ops_max_frame_bytes = 9 * 1024 * 1024;
// Leave room for CDR/RTPS headers around an 8 MiB application payload while
// keeping the complete CDDS frame inside the driver's 9 MiB bulk RX slot.
constexpr unsigned m5ops_max_packet_bytes = m5ops_max_frame_bytes - cdds_header_bytes;
constexpr uint32_t bulk_port_bit = 0x40000000U;
constexpr uint32_t application_port_bit = 0x20000000U;

inline bool m5ops_backend()
{
  const auto backend = std::getenv("CHIMAERA_DDS_BACKEND");
  return backend && std::strcmp(backend, "m5ops") == 0;
}
inline unsigned configured_max_packet_bytes()
{
  return m5ops_backend() ? m5ops_max_packet_bytes : max_packet_bytes;
}

struct Descriptor final : eprosima::fastdds::rtps::TransportDescriptorInterface
{
  explicit Descriptor(std::string socket, unsigned side);
  eprosima::fastdds::rtps::TransportInterface * create_transport() const override;
  uint32_t min_send_buffer_size() const override { return maxMessageSize; }
  std::string socket;
  unsigned side;
  bool m5ops;
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
