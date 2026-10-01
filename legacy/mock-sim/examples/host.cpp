#include <mock_sim/mock_host_transport.hpp>

#include "interactive.hpp"

#include <exception>
#include <iostream>

using namespace std;
using namespace mock_sim;
using namespace example;

int run(Transport& transport) {
    return run_interactive(transport, "host");
}

int main(int argc, char* argv[]) {
    if (argc != 2) {
        cerr << "Usage: " << argv[0] << " ENDPOINT\n";
        return 2;
    }
    try {
        MockHostTransport transport(argv[1]);
        return run(transport);
    } catch (const exception& error) {
        cerr << "host: " << error.what() << '\n';
        return 1;
    }
}
