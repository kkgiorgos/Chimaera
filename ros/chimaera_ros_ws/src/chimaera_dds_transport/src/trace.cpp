#include <dlfcn.h>
#include <fastdds/rtps/common/WriteParams.h>
#include <fcntl.h>
#include <json-c/json.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

#include <chimaera_dds_transport/trace.hpp>
#include <chrono>
#include <cstdlib>
#include <fstream>
#include <iomanip>
#include <map>
#include <mutex>
#include <set>
#include <sstream>
#include <stdexcept>

namespace chimaera_dds::trace
{
namespace
{
constexpr uint64_t magic = 0x434c42434c4f434bULL;
const char * env(const char * name)
{
  const auto p = std::getenv(name);
  return p ? p : "";
}
bool active()
{
  static const bool value = std::string(env("CHIMAERA_TRACE")) == "1";
  return value;
}
std::mutex mutex;
uint64_t * writer_clock{};
struct State
{
  std::set<std::string> topics;
  std::string side, directory, filename;
  std::ofstream output;
  uint64_t offset{}, counter{};
  uint64_t * clock{};
  void * library{};
  uint64_t (*read_time)(){};
  uint64_t (*export_file)(void *, uint64_t, uint64_t, const char *){};
  uint64_t origin{unset};
  uint64_t nonce = std::chrono::steady_clock::now().time_since_epoch().count() ^ uint64_t(getpid());
  State()
  {
    if (!active()) return;
    side = env("CHIMAERA_TRACE_SIDE");
    if (side != "host" && side != "guest")
      throw std::runtime_error("trace requires host/guest side");
    const auto routes =
      *env("CHIMAERA_TRACE_ROUTES") ? env("CHIMAERA_TRACE_ROUTES") : env("CHIMAERA_DDS_ROUTES");
    auto root = json_object_from_file(routes);
    json_object * list{};
    if (
      !root || !json_object_object_get_ex(root, "topics", &list) ||
      !json_object_is_type(list, json_type_array)) {
      if (root) json_object_put(root);
      throw std::runtime_error("trace requires a valid route file");
    }
    for (size_t i = 0; i < json_object_array_length(list); ++i) {
      auto route = json_object_array_get_idx(list, i);
      for (const auto key : {"host_topic", "guest_topic"}) {
        json_object * topic{};
        if (json_object_object_get_ex(route, key, &topic))
          topics.insert(json_object_get_string(topic));
      }
    }
    json_object_put(root);
    filename = "chimaera-trace-" + side + "-" + std::to_string(getpid()) + ".jsonl";
    if (side == "guest") {
      library = dlopen(env("CHIMAERA_TRACE_CLOCK_LIBRARY"), RTLD_NOW | RTLD_LOCAL);
      if (!library) throw std::runtime_error("cannot load trace gem5 clock library");
      read_time = reinterpret_cast<decltype(read_time)>(dlsym(library, "chimaera_gem5_time_ns"));
      export_file =
        reinterpret_cast<decltype(export_file)>(dlsym(library, "chimaera_gem5_write_file"));
      if (!read_time || !export_file)
        throw std::runtime_error("trace clock/export ABI unavailable; rebuild runtime");
    } else {
      directory = env("CHIMAERA_TRACE_DIR");
      if (directory.empty()) throw std::runtime_error("trace requires CHIMAERA_TRACE_DIR");
      output.open(directory + "/" + filename, std::ios::out | std::ios::trunc);
      if (!output) throw std::runtime_error("cannot create trace output");
    }
  }
  ~State()
  {
    if (clock) munmap(clock, 16); /* Clock DSO lives until process exit. */
  }
};
State & state()
{
  static State value;
  return value;
}
std::string quoted(const char * text)
{
  auto value = json_object_new_string(text);
  std::string result = json_object_to_json_string_ext(value, JSON_C_TO_STRING_PLAIN);
  json_object_put(value);
  return result;
}
}  // namespace
bool enabled(const char * topic) { return active() && state().topics.count(topic); }
uint64_t now()
{
  if (!active()) return unset;
  std::lock_guard lock(mutex);
  auto & s = state();
  if (s.side == "guest") {
    if (s.origin == unset) {
      std::ifstream file(env("CHIMAERA_TRACE_ORIGIN_FILE"));
      if (!(file >> s.origin)) {
        s.origin = unset;
        return unset;
      }
    }
    const auto time = s.read_time();
    return time >= s.origin ? time - s.origin : unset;
  }
  if (!s.clock) {
    const int fd = open(env("CHIMAERA_TRACE_CLOCK_FILE"), O_RDONLY | O_CLOEXEC);
    if (fd < 0) return unset;  // Coordinator has not initialized yet.
    struct stat info
    {
    };
    if (fstat(fd, &info) || info.st_size != 16) {
      close(fd);
      throw std::runtime_error("invalid trace clock");
    }
    auto memory = mmap(nullptr, 16, PROT_READ, MAP_SHARED, fd, 0);
    close(fd);
    if (memory == MAP_FAILED) throw std::runtime_error("cannot map trace clock");
    s.clock = static_cast<uint64_t *>(memory);
    if (s.clock[0] != magic) throw std::runtime_error("invalid trace clock magic");
  }
  return __atomic_load_n(s.clock + 1, __ATOMIC_ACQUIRE);
}
std::string identity(const eprosima::fastrtps::rtps::SampleIdentity & sample)
{
  std::ostringstream out;
  out << sample.writer_guid() << ':' << sample.sequence_number().high << ':'
      << sample.sequence_number().low;
  return out.str();
}
std::string next_identity()
{
  std::lock_guard lock(mutex);
  auto & s = state();
  return std::to_string(s.nonce) + ":" + std::to_string(++s.counter);
}
void record(
  const char * event, const char * topic, const std::string & id, uint64_t time,
  const char * boundary)
{
  if (!enabled(topic)) return;
  std::lock_guard lock(mutex);
  auto & s = state();
  std::ostringstream row;
  row << "{\"version\":1,\"event\":" << quoted(event) << ",\"topic\":" << quoted(topic)
      << ",\"id\":" << quoted(id.c_str()) << ",\"side\":" << quoted(s.side.c_str())
      << ",\"boundary\":" << quoted(boundary) << ",\"time_ns\":";
  if (time == unset)
    row << "null";
  else
    row << time;
  row << "}\n";
  const auto bytes = row.str();
  if (s.side == "guest") {
    const auto count =
      s.export_file(const_cast<char *>(bytes.data()), bytes.size(), s.offset, s.filename.c_str());
    if (count != bytes.size()) throw std::runtime_error("incomplete Chimaera trace export");
    s.offset += count;
  } else {
    s.output << bytes;
    s.output.flush();
    if (!s.output) throw std::runtime_error("Chimaera trace write failed");
  }
}
bool write(eprosima::fastdds::dds::DataWriter * writer, void * data, const char * topic)
{
  if (!enabled(topic)) return writer->write(data);
  const auto started = now();
  eprosima::fastrtps::rtps::WriteParams parameters;
  const auto success = writer->write(data, parameters);
  if (success) record("publish", topic, identity(parameters.sample_identity()), started);
  return success;
}
void take(const char * topic, const eprosima::fastrtps::rtps::SampleIdentity & sample)
{
  if (enabled(topic)) record("take", topic, identity(sample), now());
}
void create_clock(const std::string & path)
{
  if (path.empty()) return;
  const int fd = open(path.c_str(), O_RDWR | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
  if (fd < 0) throw std::runtime_error("trace clock already exists or cannot be created: " + path);
  if (ftruncate(fd, 16)) {
    close(fd);
    throw std::runtime_error("cannot size trace clock");
  }
  auto memory = mmap(nullptr, 16, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
  close(fd);
  if (memory == MAP_FAILED) throw std::runtime_error("cannot create shared trace clock");
  writer_clock = static_cast<uint64_t *>(memory);
  writer_clock[0] = magic;
  set_clock(unset);
}
void set_clock(uint64_t nanoseconds)
{
  if (!writer_clock) return;
  // Delayed Gazebo statistics must not roll back the guest completion boundary.
  auto previous = __atomic_load_n(writer_clock + 1, __ATOMIC_ACQUIRE);
  while ((previous == unset || nanoseconds > previous) &&
         !__atomic_compare_exchange_n(
           writer_clock + 1, &previous, nanoseconds, false, __ATOMIC_RELEASE, __ATOMIC_ACQUIRE)) {
  }
}
}  // namespace chimaera_dds::trace
