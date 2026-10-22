#pragma once

#include <chimaera_dds_transport/packet.hpp>
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
  // In m5ops mode byte_limit applies independently to each lane's local fanout
  // and remote output (four budgets); legacy mode retains one shared budget.
  Router(std::string socket, unsigned side, std::size_t byte_limit = 64 * 1024 * 1024);
  ~Router();
  Router(const Router &) = delete;
  Router & operator=(const Router &) = delete;
  std::optional<Packet> take();
  // Offer shared copies before releasing outgoing ownership/credit. In m5ops
  // mode visit control then bulk; refusal stops only that lane. The callback
  // must not reenter this Router. Returns the number of accepted packets.
  std::size_t transfer_outgoing(const std::function<bool(Packet)> & receiver);
  // Atomic local-fanout admission in m5ops mode. On capacity refusal no queue
  // changes; the caller must retain its original packet/loan for a later retry.
  bool try_receive(Packet packet);
  void receive(Packet packet);
  // Copying fallback for callers that cannot retain their input's storage.
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
