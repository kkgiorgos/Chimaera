#include <mock_sim/mock_guest_transport.hpp>

#include "interactive.hpp"

#include <exception>
#include <iostream>

using namespace std;
using namespace mock_sim;
using namespace example;

int run(mock_sim::Transport& transport) {
    return run_interactive(transport, "guest");
}

int main(int argc, char* argv[]) {
    if (argc != 2) {
        cerr << "Usage: " << argv[0] << " ENDPOINT\n";
        return 2;
    }
    try {
        MockGuestTransport transport(argv[1]);
        return run(transport);
    } catch (const std::exception& error) {
        cerr << "guest: " << error.what() << '\n';
        return 1;
    }
}
