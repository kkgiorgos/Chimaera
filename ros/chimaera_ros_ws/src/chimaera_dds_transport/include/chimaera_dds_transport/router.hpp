#pragma once

#include <cstddef>
#include <cstdint>
#include <functional>
#include <memory>
#include <optional>
#include <string>
#include <vector>

namespace chimaera_dds
{
// One router per simulator side owns local client sockets. Only the simulation
// coordinator moves packets between take() and receive() at causal boundaries.
class Router
{
public:
  Router(std::string socket, unsigned side, std::size_t byte_limit = 64 * 1024 * 1024);
  ~Router();
  Router(const Router &) = delete;
  Router & operator=(const Router &) = delete;
  std::optional<std::vector<uint8_t>> take();
  void receive(const void * data, std::size_t size);
  bool endpoint_ready(const std::string & topic, bool publisher) const;
  void check() const;
  void set_notify(std::function<void()> notify);
  std::size_t pending() const;

private:
  struct Impl;
  std::unique_ptr<Impl> impl_;
};
}  // namespace chimaera_dds
