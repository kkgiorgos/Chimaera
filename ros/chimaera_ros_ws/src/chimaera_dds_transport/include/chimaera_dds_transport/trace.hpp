#pragma once

#include <fastdds/rtps/common/SampleIdentity.h>

#include <cstdint>
#include <fastdds/dds/publisher/DataWriter.hpp>
#include <string>

namespace chimaera_dds::trace
{
constexpr uint64_t unset = UINT64_MAX;
// Disabled unless CHIMAERA_TRACE=1. Only configured application routes are recorded.
bool enabled(const char * topic);
bool enabled();
uint64_t now();
std::string identity(const eprosima::fastrtps::rtps::SampleIdentity & sample);
std::string next_identity();
void record(
  const char * event, const char * topic, const std::string & id, uint64_t time,
  const char * boundary = "rmw");
bool write(eprosima::fastdds::dds::DataWriter * writer, void * data, const char * topic);
void take(const char * topic, const eprosima::fastrtps::rtps::SampleIdentity & sample);
// The timestamp is captured at transport entry/arrival, before DDS delivery or
// trace formatting. Export after the measured operation using the borrowed data.
struct ReceiveInfo
{
  uint32_t batch_size{};
  bool dispatched{true}, eligible{}, candidate{};
};
void packet(
  const char * event, const uint8_t * bytes, std::size_t size, uint64_t time, uint32_t port,
  const ReceiveInfo * receive = nullptr);
// Coordinator owns this process-shared logical clock; participants only read it.
void create_clock(const std::string & path);
void set_clock(uint64_t nanoseconds);
}  // namespace chimaera_dds::trace
