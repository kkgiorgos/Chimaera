#pragma once

#include <chimaera/gem5_controller.hpp>

namespace chimaera {

// Mailboxes are consumed/published only at completed simulator boundaries.
// Packets contain complete application frames; consecutive small frames may
// share a packet. Images (including atomic stereo) retain their own buffers.
class DmaHostController final : public HostController {
public:
    // Count may be enlarged for RTPS bootstrap; the 32 MiB byte limit remains.
    explicit DmaHostController(Gem5TimingController& timing,
        std::string h2g = "/tmp/chimaera_dma_h2g",
        std::string g2h = "/tmp/chimaera_dma_g2h", std::size_t pending_messages = 1024);
    ~DmaHostController() override;
    void submit(Message data, std::string_view key = {}) override;
    std::optional<Message> take() override;
    ControllerResult step(Duration interval) override;
    ControllerResult stop() override;
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

enum class DmaCompletionMode {
    worker,
    caller,
};

class DmaGuestController final : public GuestController {
public:
    // Construct before workbegin: allocate/map the pool outside the timing ROI.
    // The notifier wakes an application wait set; it never executes callbacks.
    // Caller mode reads completions in run_next(), without a helper thread.
    // Its caller must wait on completion_fd() and its own application wakeup.
    // pending_messages bounds the outgoing count independently of its byte cap.
    explicit DmaGuestController(std::function<void()> notify,
        std::string device = "/dev/chimaera-dma",
        std::string module = {}, std::string module_options = {},
        std::size_t pending_messages = 1024,
        DmaCompletionMode completion_mode = DmaCompletionMode::worker);
    ~DmaGuestController() override;
    Message allocate(std::size_t size);
    void submit(Message data, std::string_view key = {}) override;
    std::optional<Message> take() override;
    // Service completions and initiate queued output without any idle sleep.
    ControllerResult run_next() override;
    bool initialized() const noexcept;
    bool has_work() const;
    // Borrowed descriptor, valid until destruction in caller mode only. Poll it
    // for readiness; run_next() remains the sole consumer of driver records.
    int completion_fd() const;
    std::string statistics() const;
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
} // namespace chimaera
