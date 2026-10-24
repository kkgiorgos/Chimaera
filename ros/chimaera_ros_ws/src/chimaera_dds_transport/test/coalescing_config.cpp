#include <unistd.h>

#include <chimaera_dds_transport/transport.hpp>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>

using namespace chimaera_dds;
using namespace eprosima::fastdds::dds;
void require(bool value, const char * reason)
{
  if (!value) throw std::runtime_error(reason);
}
int main(int argc, char **)
{
  const bool host = argc > 1;
  const auto path = std::filesystem::temp_directory_path() /
                    ("chimaera-coalescing-" + std::to_string(getpid()) + ".json");
  try {
    std::ofstream out(path);
    out << R"({"topics":[)";
    for (int i = 0; i < 7; ++i) {
      if (i) out << ',';
      out << "{\"host_topic\":\"/state" << i << "\",\"guest_topic\":\"/state" << i
          << "\",\"type\":\"sensor_msgs/msg/" << (i == 3 ? "Image" : "JointState")
          << "\",\"direction\":\"" << (i == 6 ? "guest_to_host" : "host_to_guest")
          << "\",\"coalesce\":" << (i == 1 ? "false" : "true")
          << (i == 4 ? ",\"coalesce_group\":\"stereo\"" : "")
          << ",\"qos\":{\"history\":\"keep_last\",\"depth\":" << (i == 5 ? 2 : 1)
          << ",\"reliability\":\"" << (i == 2 ? "reliable" : "best_effort")
          << "\",\"durability\":\"volatile\"}}";
    }
    out << "]}";
    out.close();
    setenv("CHIMAERA_DDS_SOCKET", "/tmp/unused", 1);
    setenv("CHIMAERA_DDS_SIDE", host ? "host" : "guest", 1);
    setenv("CHIMAERA_DDS_BACKEND", "m5ops", 1);
    setenv("CHIMAERA_DDS_ROUTES", path.c_str(), 1);
    for (int i = 0; i < 7; ++i) {
      const auto topic = "/state" + std::to_string(i);
      DataReaderQos reader;
      DataWriterQos writer;
      reader.reliability().kind = BEST_EFFORT_RELIABILITY_QOS;
      writer.reliability().kind = BEST_EFFORT_RELIABILITY_QOS;
      reader.durability().kind = VOLATILE_DURABILITY_QOS;
      writer.durability().kind = VOLATILE_DURABILITY_QOS;
      uint32_t port;
      if (host != (i == 6)) {
        configure_writer(topic.c_str(), writer);
        port = writer.endpoint().unicast_locator_list.begin()->port;
      } else {
        configure_reader(topic.c_str(), reader);
        port = reader.endpoint().unicast_locator_list.begin()->port;
      }
      require(coalesce_latest(port) == (!host && i == 0), "route coalescing eligibility changed");
    }
    bool refused = false;
    try {
      if (host) {
        DataWriterQos qos;
        qos.reliability().kind = RELIABLE_RELIABILITY_QOS;
        qos.durability().kind = VOLATILE_DURABILITY_QOS;
        configure_writer("/state0", qos);
      } else {
        DataReaderQos qos;
        qos.reliability().kind = RELIABLE_RELIABILITY_QOS;
        qos.durability().kind = VOLATILE_DURABILITY_QOS;
        configure_reader("/state0", qos);
      }
    } catch (const std::invalid_argument &) {
      refused = true;
    }
    require(refused, "reliable endpoint accepted on a best-effort coalescing route");
    refused = false;
    try {
      if (host) {
        DataWriterQos qos;
        qos.reliability().kind = BEST_EFFORT_RELIABILITY_QOS;
        qos.durability().kind = TRANSIENT_LOCAL_DURABILITY_QOS;
        configure_writer("/state0", qos);
      } else {
        DataReaderQos qos;
        qos.reliability().kind = BEST_EFFORT_RELIABILITY_QOS;
        qos.durability().kind = TRANSIENT_LOCAL_DURABILITY_QOS;
        configure_reader("/state0", qos);
      }
    } catch (const std::invalid_argument &) {
      refused = true;
    }
    require(refused, "durable endpoint accepted on a volatile coalescing route");
    require(!coalesce_latest(1), "discovery port was coalescible");
    std::filesystem::remove(path);
    std::cout << "Explicit latest-state route, QoS and direction policy passed\n";
  } catch (const std::exception & error) {
    std::filesystem::remove(path);
    std::cerr << error.what() << '\n';
    return 1;
  }
}
