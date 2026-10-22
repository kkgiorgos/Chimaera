#include <fastdds/rtps/transport/UDPTransportDescriptor.h>
#include <fastdds/rtps/transport/shared_mem/SharedMemTransportDescriptor.h>
#include <json-c/json.h>

#include <algorithm>
#include <chimaera_dds_transport/transport.hpp>
#include <cstdio>
#include <cstdlib>
#include <fastdds/dds/log/Log.hpp>
#include <fstream>
#include <map>
#include <memory>
#include <stdexcept>

#include "protocol.hpp"

namespace chimaera_dds
{
namespace
{
struct Route
{
  std::string wire;
  std::string type;
  bool publisher;
  uint32_t port{1};
};
// Hash the canonical wire topic rather than its side-specific ROS alias. Both
// peers compute the same locator, independent of JSON ordering or process ID.
uint32_t route_port(const std::string & topic, bool bulk)
{
  uint32_t hash = 2166136261U;
  for (const auto value : topic) {
    hash ^= static_cast<uint8_t>(value);
    hash *= 16777619U;
  }
  // Keep application ports above discovery/graph ports, and reserve the bulk bit.
  return application_port_bit | (hash & 0x1fffffffU) | (bulk ? bulk_port_bit : 0U);
}
struct Configuration
{
  std::string socket;
  unsigned side{};
  bool m5ops{m5ops_backend()};
  std::map<std::string, Route> routes;
  Configuration()
  {
    const auto path = std::getenv("CHIMAERA_DDS_SOCKET");
    if (!path || !*path) return;
    socket = path;
    const auto role = std::getenv("CHIMAERA_DDS_SIDE");
    if (!role || (std::string(role) != "host" && std::string(role) != "guest"))
      throw std::invalid_argument("CHIMAERA_DDS_SIDE must be host or guest");
    side = std::string(role) == "host" ? 1 : 2;
    const auto filename = std::getenv("CHIMAERA_DDS_ROUTES");
    if (!filename) throw std::invalid_argument("CHIMAERA_DDS_ROUTES is required");
    std::unique_ptr<json_object, decltype(&json_object_put)> root(
      json_object_from_file(filename), json_object_put);
    json_object * topics = nullptr;
    if (
      !root || !json_object_object_get_ex(root.get(), "topics", &topics) ||
      !json_object_is_type(topics, json_type_array))
      throw std::invalid_argument("invalid DDS route configuration");
    std::map<uint32_t, std::pair<std::string, std::string>> ports;
    std::map<std::string, std::pair<std::string, uint32_t>> wire_topics;
    for (std::size_t i = 0; i < json_object_array_length(topics); ++i) {
      auto item = json_object_array_get_idx(topics, i);
      auto string = [item](const char * key) {
        json_object * value = nullptr;
        if (
          !json_object_object_get_ex(item, key, &value) ||
          !json_object_is_type(value, json_type_string))
          throw std::invalid_argument("missing DDS route field");
        return std::string(json_object_get_string(value));
      };
      const auto host = string("host_topic"), guest = string("guest_topic");
      const auto direction = string("direction");
      const auto type = string("type");
      const auto separator = type.rfind('/');
      if (separator == std::string::npos || separator == 0 || separator + 1 == type.size())
        throw std::invalid_argument("invalid DDS route message type");
      std::string dds_type;
      for (char value : type.substr(0, separator))
        dds_type += value == '/' ? "::" : std::string(1, value);
      dds_type += "::dds_::" + type.substr(separator + 1) + "_";
      if (direction != "host_to_guest" && direction != "guest_to_host")
        throw std::invalid_argument("invalid DDS route direction");
      const auto name = side == 1 ? host : guest;
      const bool publisher = (side == 1) == (direction == "host_to_guest");
      bool bulk = type == "sensor_msgs/msg/Image" || type == "sensor_msgs/msg/CompressedImage";
      if (m5ops) {
        json_object * transport_class = nullptr;
        if (json_object_object_get_ex(item, "transport_class", &transport_class)) {
          if (!json_object_is_type(transport_class, json_type_string))
            throw std::invalid_argument("DDS route transport_class must be control or bulk");
          const std::string value(
            json_object_get_string(transport_class), json_object_get_string_len(transport_class));
          if (value != "control" && value != "bulk")
            throw std::invalid_argument("DDS route transport_class must be control or bulk");
          bulk = value == "bulk";
        }
      }
      const auto port = m5ops ? route_port(host, bulk) : 1U;
      if (m5ops) {
        // One wire topic must retain its type and lane across local aliases.
        // In particular, a bulk override must also carry its RTPS metadata.
        const auto [flow, new_flow] = wire_topics.emplace(host, std::make_pair(dds_type, port));
        if (!new_flow && flow->second != std::make_pair(dds_type, port))
          throw std::invalid_argument("DDS wire-topic type or transport_class conflict: " + host);
        const auto identity = std::make_pair(host, dds_type);
        const auto [entry, inserted] = ports.emplace(port, identity);
        if (!inserted && entry->second != identity)
          throw std::invalid_argument("DDS wire-topic locator collision: " + host);
      }
      if (
        name.empty() || name[0] != '/' || host.empty() || host[0] != '/' ||
        !routes.emplace(name, Route{host, dds_type, publisher, port}).second)
        throw std::invalid_argument("invalid or duplicate DDS route");
    }
  }
};
const Configuration & config()
{
  static const Configuration value;
  return value;
}
void configure_local_transports(eprosima::fastdds::dds::DomainParticipantQos & qos)
{
  using eprosima::fastdds::rtps::SharedMemTransportDescriptor;
  using eprosima::fastdds::rtps::UDPTransportDescriptor;
  auto & transports = qos.transport().user_transports;
  bool local_only = !qos.transport().use_builtin_transports && !transports.empty();
  std::shared_ptr<SharedMemTransportDescriptor> shared;
  for (const auto & transport : transports) {
    if (auto udp = std::dynamic_pointer_cast<UDPTransportDescriptor>(transport)) {
      local_only = local_only && !udp->interfaceWhiteList.empty() &&
                   std::all_of(
                     udp->interfaceWhiteList.begin(), udp->interfaceWhiteList.end(),
                     [](const std::string & address) {
                       return address == "127.0.0.1" || address == "::1" || address == "lo";
                     });
    } else if (auto shm = std::dynamic_pointer_cast<SharedMemTransportDescriptor>(transport)) {
      if (!shared || shm->segment_size() > shared->segment_size()) shared = shm;
    } else {
      local_only = false;
    }
  }
  const bool m5ops = config().m5ops;
  if (!m5ops && (!local_only || !shared)) return;
  // A participant's smallest transport limit determines RTPS fragmentation.
  // Local UDP would split a camera sample into many DMA completions. Keep SHM
  // for ordinary local endpoints and the Chimaera transport for routed peers.
  auto local = shared ? std::make_shared<SharedMemTransportDescriptor>(*shared)
                      : std::make_shared<SharedMemTransportDescriptor>();
  const auto maximum = configured_max_packet_bytes();
  local->max_message_size(maximum);
  const auto segment = 4 * (m5ops ? m5ops_max_frame_bytes : max_packet_bytes);
  local->segment_size(std::max(local->segment_size(), segment));
  qos.transport().use_builtin_transports = false;
  transports = {std::move(local)};
}
void announce(const char * topic, bool publisher, bool removed = false)
{
  const auto & configuration = config();
  std::vector<uint8_t> body{uint8_t(unsigned(publisher) | (removed ? 0x80 : 0))};
  body.insert(body.end(), topic, topic + std::strlen(topic));
  const auto packet = wire::packet(
    wire::endpoint, wire::local(configuration.side), wire::local(configuration.side), body.data(),
    body.size());
  const auto address = wire::address(configuration.socket);
  const auto fd = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
  if (fd < 0) throw std::runtime_error("cannot create DDS endpoint registration socket");
  timeval timeout{5, 0};
  ::setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
  const bool success =
    !::connect(fd, reinterpret_cast<const sockaddr *>(&address), sizeof(address)) &&
    wire::send_all(fd, packet.data(), packet.size());
  ::close(fd);
  if (!success) throw std::runtime_error("cannot register DDS endpoint with runtime");
}
template <class Qos>
void configure_endpoint(const char * topic, bool publisher, Qos & qos)
{
  const auto & configuration = config();
  if (configuration.socket.empty()) return;
  const auto route = configuration.routes.find(topic);
  // RMW graph associations are control metadata, shared by all ROS nodes.
  const bool graph = std::strcmp(topic, "ros_discovery_info") == 0;
  if (!graph && (route == configuration.routes.end() || route->second.publisher != publisher))
    return;
  qos.endpoint().unicast_locator_list.push_back(
    wire::local(configuration.side, graph ? 1U : route->second.port));
}
}  // namespace
void configure_participant(eprosima::fastdds::dds::DomainParticipantQos & qos)
{
  const auto & configuration = config();
  if (configuration.socket.empty()) return;
  configure_local_transports(qos);
  qos.transport().user_transports.push_back(
    std::make_shared<Descriptor>(configuration.socket, configuration.side));
  // Host wall time can advance for minutes while the guest is paused in a
  // detailed simulation. Explicit participant disposal/session teardown owns
  // discovery lifetime; wall-time lease expiry must not remove a paused peer.
  qos.wire_protocol().builtin.discovery_config.leaseDuration = eprosima::fastrtps::c_TimeInfinite;
  if (std::getenv("CHIMAERA_DDS_DIAGNOSTICS"))
    eprosima::fastdds::dds::Log::SetVerbosity(eprosima::fastdds::dds::Log::Info);
  if (std::getenv("CHIMAERA_DDS_DIAGNOSTICS"))
    std::fprintf(
      stderr, "Chimaera DDS participant: side=%u socket=%s\n", configuration.side,
      configuration.socket.c_str());
  // Include the transport in SPDP bootstrap; packet admission remains owned by
  // the simulator coordinator. A zero address denotes discovery broadcast.
  wire::Locator peer;
  peer.kind = transport_kind;
  qos.wire_protocol().builtin.initialPeersList.push_back(peer);
}
std::string wire_topic(const char * topic, const std::string & dds_type)
{
  const auto & configuration = config();
  const auto route = configuration.routes.find(topic);
  if (route != configuration.routes.end() && !dds_type.empty() && route->second.type != dds_type)
    throw std::invalid_argument(
      "DDS endpoint type differs from configured route: " + std::string(topic));
  return route == configuration.routes.end() ? topic : route->second.wire;
}
std::string local_topic(const std::string & topic)
{
  for (const auto & [local, route] : config().routes) {
    if (route.wire == topic) return local;
  }
  return topic;
}
void configure_writer(const char * topic, eprosima::fastdds::dds::DataWriterQos & qos)
{
  configure_endpoint(topic, true, qos);
}
void configure_reader(const char * topic, eprosima::fastdds::dds::DataReaderQos & qos)
{
  configure_endpoint(topic, false, qos);
}
void endpoint_created(const char * topic, bool publisher)
{
  const auto & configuration = config();
  const auto route = configuration.routes.find(topic);
  if (route != configuration.routes.end() && route->second.publisher == publisher)
    announce(topic, publisher);
}
void endpoint_destroyed(const char * topic, bool publisher) noexcept
{
  try {
    const auto & configuration = config();
    const auto route = configuration.routes.find(topic);
    if (route != configuration.routes.end() && route->second.publisher == publisher)
      announce(topic, publisher, true);
  } catch (const std::exception & error) {
    std::fprintf(stderr, "Chimaera DDS endpoint disposal: %s\n", error.what());
  }
}
}  // namespace chimaera_dds
