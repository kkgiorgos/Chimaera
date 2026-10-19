#pragma once

#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

#include <cstdint>
#include <limits>
#include <stdexcept>
#include <string>

namespace chimaera_latency
{
// Linux/x86 process-shared, aligned, lock-free clock. Python creates each file
// exclusively for one run. No clock update crosses DDS or Ignition Transport.
class SharedClock
{
public:
  static constexpr uint64_t magic = 0x434c42434c4f434bULL;
  static constexpr uint64_t unset = std::numeric_limits<uint64_t>::max();
  explicit SharedClock(const std::string & path, bool writer = false) : writer_(writer)
  {
    static_assert(__atomic_always_lock_free(sizeof(uint64_t), nullptr));
    const int fd = open(path.c_str(), writer ? O_RDWR : O_RDONLY);
    if (fd < 0) throw std::runtime_error("Cannot open benchmark clock: " + path);
    struct stat info
    {
    };
    if (fstat(fd, &info) != 0 || info.st_size != 16) {
      close(fd);
      throw std::runtime_error("Invalid benchmark clock size: " + path);
    }
    memory_ = mmap(nullptr, 16, writer ? PROT_READ | PROT_WRITE : PROT_READ, MAP_SHARED, fd, 0);
    close(fd);
    if (memory_ == MAP_FAILED) throw std::runtime_error("Cannot map benchmark clock: " + path);
    if (*static_cast<uint64_t *>(memory_) != magic) {
      munmap(memory_, 16);
      memory_ = MAP_FAILED;
      throw std::runtime_error("Invalid benchmark clock magic: " + path);
    }
  }
  ~SharedClock()
  {
    if (memory_ != MAP_FAILED) munmap(memory_, 16);
  }
  SharedClock(const SharedClock &) = delete;
  SharedClock & operator=(const SharedClock &) = delete;
  uint64_t now() const
  {
    return __atomic_load_n(static_cast<uint64_t *>(memory_) + 1, __ATOMIC_ACQUIRE);
  }
  void set(uint64_t value)
  {
    if (!writer_) throw std::logic_error("Read-only benchmark clock");
    __atomic_store_n(static_cast<uint64_t *>(memory_) + 1, value, __ATOMIC_RELEASE);
  }

private:
  bool writer_;
  void * memory_{MAP_FAILED};
};
}  // namespace chimaera_latency
