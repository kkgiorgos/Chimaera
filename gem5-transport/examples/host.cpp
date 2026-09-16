#include <chimaera/host_transport.hpp>
#include "interactive.hpp"

int main() {
    chimaera::HostTransport transport;
    return chimaera::example::run_interactive(transport, "host");
}
