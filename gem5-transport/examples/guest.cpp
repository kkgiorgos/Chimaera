#include <chimaera/guest_transport.hpp>
#include <m5_mmap.h>
#include "interactive.hpp"

int main() {
    m5op_addr = 0xFFFF0000;
    map_m5_mem();
    chimaera::GuestTransport transport;
    const int result = chimaera::example::run_interactive(transport, "guest");
    unmap_m5_mem();
    return result;
}
