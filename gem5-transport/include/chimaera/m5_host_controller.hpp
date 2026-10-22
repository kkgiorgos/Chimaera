#pragma once

#include <chimaera/gem5_controller.hpp>

namespace chimaera {
// Four independent raw-CDDS mailbox rings. Only step() admits guest input and
// exposes guest output, at the same paused simulator boundaries as DMA.
class M5HostController final : public HostController, public BackpressureController {
public:
    explicit M5HostController(Gem5TimingController& timing,
        std::string h2g = "/tmp/chimaera_dma_h2g",
        std::string g2h = "/tmp/chimaera_dma_g2h", std::size_t pending_messages = 8192);
    ~M5HostController() override;
    void submit(Message data, std::string_view key = {}) override;
    bool try_submit(Message data) override;
    std::optional<Message> take() override;
    std::size_t transfer_received(
        const std::function<bool(Message)>& receiver) override;
    ControllerResult step(Duration interval) override;
    ControllerResult stop() override;
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
} // namespace chimaera
