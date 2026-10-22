#include <dlfcn.h>
#include <fastdds/rtps/transport/TransportInterface.h>
#include <poll.h>

#include <algorithm>
#include <chimaera_dds_transport/transport.hpp>
#include <chrono>
#include <condition_variable>
#include <fastdds/rtps/common/LocatorList.hpp>
#include <filesystem>
#include <iostream>
#include <mutex>

#include "protocol.hpp"

using namespace std::chrono_literals;
namespace wire = chimaera_dds::wire;
namespace rtps = eprosima::fastdds::rtps;

namespace
{
void require(bool value, const char * message)
{
  if (!value) throw std::runtime_error(message);
}

// Interpose socket calls only in this test process. Pause a worker before its
// first write, without adding hooks or polling to the production transport.
struct WriteGate
{
  std::mutex mutex;
  std::condition_variable ready;
  unsigned port{};
  int fd{-1}, fail_fd{-1};
  bool armed{}, entered{}, released{}, partial_failure{};
} gate;

struct Pause
{
  explicit Pause(unsigned port, bool partial_failure = false)
  {
    std::lock_guard lock(gate.mutex);
    gate.port = port;
    gate.fd = gate.fail_fd = -1;
    gate.armed = true;
    gate.entered = gate.released = false;
    gate.partial_failure = partial_failure;
  }
  void entered()
  {
    std::unique_lock lock(gate.mutex);
    require(gate.ready.wait_for(lock, 2s, [] { return gate.entered; }), "worker did not write");
  }
  void release()
  {
    std::lock_guard lock(gate.mutex);
    gate.armed = false;
    gate.released = true;
    gate.ready.notify_all();
  }
  ~Pause() { release(); }
};

std::string failing_path;
unsigned connect_count{};

struct Backend
{
  bool existed{std::getenv("CHIMAERA_DDS_BACKEND") != nullptr};
  std::string previous{existed ? std::getenv("CHIMAERA_DDS_BACKEND") : ""};
  explicit Backend(bool m5ops)
  {
    require(!::setenv("CHIMAERA_DDS_BACKEND", m5ops ? "m5ops" : "socket", 1), "set backend");
  }
  ~Backend()
  {
    if (existed)
      ::setenv("CHIMAERA_DDS_BACKEND", previous.c_str(), 1);
    else
      ::unsetenv("CHIMAERA_DDS_BACKEND");
  }
};

struct Runtime
{
  std::string path;
  int listener{-1};
  std::array<int, 2> peers{{-1, -1}};
  std::unique_ptr<rtps::TransportInterface> transport;
  rtps::SendResourceList senders;

  explicit Runtime(std::string socket) : path(std::move(socket))
  {
    listener = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    const auto address = wire::address(path);
    require(
      listener >= 0 &&
        !::bind(listener, reinterpret_cast<const sockaddr *>(&address), sizeof(address)) &&
        !::listen(listener, 4),
      "create fake runtime");
    chimaera_dds::Descriptor descriptor(path, 1);
    transport.reset(descriptor.create_transport());
  }
  ~Runtime()
  {
    // Join workers while their peer fds still exist, including empty-CV waits.
    transport.reset();
    for (auto fd : peers)
      if (fd >= 0) ::close(fd);
    if (listener >= 0) ::close(listener);
    ::unlink(path.c_str());
  }
  void accept(unsigned index)
  {
    pollfd descriptor{listener, POLLIN, 0};
    require(::poll(&descriptor, 1, 2000) == 1, "missing runtime connection");
    peers[index] = ::accept4(listener, nullptr, nullptr, SOCK_CLOEXEC);
    require(peers[index] >= 0, "accept runtime connection");
    timeval timeout{2, 0};
    ::setsockopt(peers[index], SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
  }
  void init(unsigned sockets = 2)
  {
    require(transport->init(), "initialize transport");
    for (unsigned i = 0; i < sockets; ++i) accept(i);
    require(
      transport->OpenOutputChannel(senders, wire::local(2)) && senders.size() == 1,
      "create send resource");
  }
  bool send(unsigned port, const std::vector<uint8_t> & body)
  {
    // Serial calls model the participant-wide send mutex in Fast DDS 2.6.12.
    // A callback waiting on the paused bulk worker hangs this test.
    rtps::LocatorList destinations;
    destinations.push_back(wire::local(2, port));
    rtps::Locators begin(destinations.begin()), end(destinations.end());
    return senders.front()->send(
      body.data(), uint32_t(body.size()), &begin, &end, std::chrono::steady_clock::now());
  }
  std::vector<uint8_t> receive(unsigned index)
  {
    std::array<uint8_t, wire::header_bytes> prefix;
    require(wire::receive_all(peers[index], prefix.data(), prefix.size()), "missing frame header");
    std::vector<uint8_t> frame(wire::length(prefix.data(), prefix.size()));
    std::memcpy(frame.data(), prefix.data(), prefix.size());
    require(
      wire::receive_all(peers[index], frame.data() + prefix.size(), frame.size() - prefix.size()),
      "missing frame payload");
    return frame;
  }
  void empty(unsigned index)
  {
    pollfd descriptor{peers[index], POLLIN, 0};
    require(::poll(&descriptor, 1, 0) == 0, "unaccepted frame reached socket");
  }
};

constexpr unsigned bulk_port = chimaera_dds::bulk_port_bit | 31U;
constexpr unsigned control_port = 45;

void byte_limit_and_ownership(const std::string & root)
{
  Runtime runtime(root + "/bytes");
  runtime.init();
  Pause pause(bulk_port);
  // Seven of these fit in 64 MiB; eight do not. The first is in flight.
  std::vector<uint8_t> body(8 * 1024 * 1024 + 64, 1);
  std::memcpy(body.data(), "RTPS", 4);
  require(runtime.send(bulk_port, body), "first bulk frame refused");
  pause.entered();
  for (unsigned i = 2; i <= 7; ++i) {
    body.back() = uint8_t(i);
    require(runtime.send(bulk_port, body), "bulk burst refused below byte limit");
  }
  body.back() = 8;
  require(!runtime.send(bulk_port, body), "in-flight bytes not charged to byte limit");
  std::fill(body.begin(), body.end(), 0xff);  // Invalidate every borrowed source byte.
  const std::vector<uint8_t> control(64, 9);
  require(runtime.send(control_port, control), "bulk byte pressure blocked control callback");
  require(runtime.receive(0).back() == 9, "control worker blocked behind bulk");
  pause.release();
  for (unsigned i = 1; i <= 7; ++i) {
    const auto frame = runtime.receive(1);
    require(
      frame[5] == wire::data && frame.size() == body.size() + wire::header_bytes &&
        wire::locator(frame.data() + 28).port == bulk_port && frame.back() == i &&
        !std::memcmp(frame.data() + wire::header_bytes, "RTPS", 4) &&
        frame[wire::header_bytes + 4] == 1,
      "owned bulk bytes or FIFO changed");
  }
  require(runtime.send(bulk_port, control), "byte credit did not recover");
  require(runtime.receive(1).back() == 9, "recovered byte credit lost frame");
  runtime.empty(1);
}

void record_limit(const std::string & root)
{
  Runtime runtime(root + "/records");
  runtime.init();
  Pause pause(bulk_port);
  std::vector<uint8_t> body(64, 1);
  require(runtime.send(bulk_port, body), "first small frame refused");
  pause.entered();
  for (unsigned i = 2; i <= 128; ++i) {
    body.back() = uint8_t(i);
    require(runtime.send(bulk_port, body), "record refused below limit");
  }
  body.back() = 129;
  require(!runtime.send(bulk_port, body), "in-flight frame not charged to record limit");
  require(runtime.send(control_port, body), "bulk record pressure consumed control credit");
  require(runtime.receive(0).back() == 129, "independent control record missing");
  pause.release();
  for (unsigned i = 1; i <= 128; ++i)
    require(runtime.receive(1).back() == i, "record FIFO or refusal changed");
  require(runtime.send(bulk_port, body), "record credit did not recover");
  require(runtime.receive(1).back() == 129, "recovered record missing");
  runtime.empty(1);
}

void metadata_fifo(const std::string & root)
{
  Runtime runtime(root + "/metadata");
  runtime.init();
  Pause pause(bulk_port);
  const auto locator = wire::local(1, bulk_port);
  require(runtime.transport->OpenInputChannel(locator, nullptr, 0), "open not enqueued");
  pause.entered();
  require(runtime.transport->CloseInputChannel(locator), "close not enqueued");
  require(runtime.transport->OpenInputChannel(locator, nullptr, 0), "reopen not enqueued");
  require(runtime.send(bulk_port, {1, 2, 3}), "data not enqueued after reopen");
  require(runtime.transport->CloseInputChannel(locator), "final close not enqueued");
  pause.release();
  for (auto type : {wire::open, wire::close, wire::open, wire::data, wire::close})
    require(runtime.receive(1)[5] == type, "Open/Close/data FIFO changed");
  runtime.empty(0);
  runtime.empty(1);
}

void metadata_refusal(const std::string & root, bool close)
{
  Runtime runtime(root + (close ? "/close-full" : "/open-full"));
  runtime.init();
  const auto locator = wire::local(1, bulk_port);
  if (close) {
    require(runtime.transport->OpenInputChannel(locator, nullptr, 0), "open before close test");
    require(runtime.receive(1)[5] == wire::open, "missing initial open");
  }
  Pause pause(bulk_port);
  const std::vector<uint8_t> body(64, 1);
  require(runtime.send(bulk_port, body), "first frame refused");
  pause.entered();
  for (unsigned i = 1; i < 128; ++i)
    require(runtime.send(bulk_port, body), "metadata setup filled early");
  require(
    close ? !runtime.transport->CloseInputChannel(locator)
          : !runtime.transport->OpenInputChannel(locator, nullptr, 0),
    "full metadata FIFO accepted registration");
  require(!runtime.transport->IsInputChannelOpen(locator), "metadata failure left local channel");
  require(!runtime.send(control_port, body), "metadata failure did not stop transport");
  runtime.transport->shutdown();  // Wakes the empty control writer as well.
}

void shutdown_and_write_failure(const std::string & root, bool fail)
{
  Runtime runtime(root + (fail ? "/write-failure" : "/shutdown"));
  runtime.init();
  Pause pause(bulk_port, fail);
  require(runtime.send(bulk_port, std::vector<uint8_t>(4096, 1)), "enqueue before stop failed");
  pause.entered();
  if (fail) {
    pause.release();
    std::array<uint8_t, 17> partial;
    require(
      wire::receive_all(runtime.peers[1], partial.data(), partial.size()), "no partial write");
    uint8_t byte;
    require(::recv(runtime.peers[1], &byte, 1, 0) == 0, "failed stream was not retired");
    require(!runtime.send(control_port, {1}), "write failure left other lane running");
  }
  runtime.transport->shutdown();
  runtime.transport->shutdown();
  require(!runtime.send(control_port, {1}), "send accepted after shutdown");
}

void partial_initialization(const std::string & root)
{
  Runtime runtime(root + "/init-failure");
  failing_path = runtime.path;
  connect_count = 0;
  const auto initialized = runtime.transport->init();
  failing_path.clear();
  require(!initialized && connect_count == 2, "second connection failure not propagated");
  runtime.accept(0);
  uint8_t byte;
  require(::recv(runtime.peers[0], &byte, 1, 0) == 0, "partial initialization leaked first socket");
}
}  // namespace

extern "C" ssize_t send(int fd, const void * bytes, std::size_t size, int flags)
{
  static const auto real_send =
    reinterpret_cast<ssize_t (*)(int, const void *, std::size_t, int)>(::dlsym(RTLD_NEXT, "send"));
  {
    std::unique_lock lock(gate.mutex);
    if (gate.fail_fd == fd) {
      gate.fail_fd = -1;
      errno = EIO;
      return -1;
    }
    const auto * data = static_cast<const uint8_t *>(bytes);
    if (
      gate.armed && size >= wire::header_bytes && !std::memcmp(data, "CDDS", 4) &&
      wire::locator(data + 28).port == gate.port) {
      gate.armed = false;
      gate.entered = true;
      gate.fd = fd;
      gate.ready.notify_all();
      gate.ready.wait(lock, [] { return gate.released; });
      if (gate.partial_failure) {
        gate.fail_fd = fd;
        size = 17;
      }
    }
  }
  return real_send(fd, bytes, size, flags);
}

extern "C" int shutdown(int fd, int how) noexcept
{
  static const auto real_shutdown =
    reinterpret_cast<int (*)(int, int)>(::dlsym(RTLD_NEXT, "shutdown"));
  const auto result = real_shutdown(fd, how);
  std::lock_guard lock(gate.mutex);
  if (gate.fd == fd) {
    // Mirror the real socket's cancellation for a write paused in this hook.
    gate.released = true;
    gate.ready.notify_all();
  }
  return result;
}

extern "C" int connect(int fd, const sockaddr * address, socklen_t size)
{
  static const auto real_connect =
    reinterpret_cast<int (*)(int, const sockaddr *, socklen_t)>(::dlsym(RTLD_NEXT, "connect"));
  if (
    !failing_path.empty() && address->sa_family == AF_UNIX &&
    failing_path == reinterpret_cast<const sockaddr_un *>(address)->sun_path &&
    ++connect_count == 2) {
    errno = EACCES;
    return -1;
  }
  return real_connect(fd, address, size);
}

int main()
{
  try {
    const auto root = (std::filesystem::temp_directory_path() /
                       ("chimaera-host-async-" + std::to_string(::getpid())))
                        .string();
    std::filesystem::create_directories(root);
    {
      Backend backend(true);
      byte_limit_and_ownership(root);
      record_limit(root);
      metadata_fifo(root);
      metadata_refusal(root, false);
      metadata_refusal(root, true);
      shutdown_and_write_failure(root, false);
      shutdown_and_write_failure(root, true);
      partial_initialization(root);
    }
    {
      Backend backend(false);
      Runtime runtime(root + "/legacy");
      runtime.init(1);
      require(runtime.send(control_port, {1, 2, 3}), "legacy send failed");
      require(runtime.receive(0).back() == 3, "legacy synchronous frame changed");
      pollfd descriptor{runtime.listener, POLLIN, 0};
      require(::poll(&descriptor, 1, 0) == 0, "legacy created a second socket");
    }
    std::filesystem::remove_all(root);
    return 0;
  } catch (const std::exception & error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
