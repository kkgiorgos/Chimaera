#include <chimaera/gem5_controller.hpp>
#include <gem5/m5ops.h>
#include <m5_mmap.h>
#include "controller_console.hpp"
#include "guest_channels.hpp"
using namespace chimaera;
using namespace chimaera::controller_example;

int main(int argc, char** argv) {
    if (argc != 1) {
        std::cerr << "Usage: " << argv[0] << "\nAll guest channels are managed by this console.\n";
        return 1;
    }
    m5op_addr = 0xFFFF0000;
    map_m5_mem();
    int status = 0;
    try {
        GuestChannels channels;
        Gem5GuestController controller(channels, channels);
        std::cout << "Guest controller ready. Receiving channels 1 and 2 automatically.\n"
                     "Commands: send CHANNEL [TEXT]. Quit from the host.\n" << std::flush;
        // The boot-to-controller config pauses here before opening its timing socket.
        m5_work_begin_addr(0, 0);
        while (true) {
            const auto result = controller.run_next();
            require(result);
            if (result.state == ControllerState::stopped) break;
        }
    } catch (const std::exception& error) {
        std::cerr << "Guest controller: " << error.what() << '\n';
        status = 1;
    }
    unmap_m5_mem();
    return status;
}
