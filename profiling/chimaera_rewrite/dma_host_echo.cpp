// Boundary-controlled host mailbox echo for end-to-end device validation.
#include <gem5/chimaera_mailbox.hh>
#include <iostream>
#include <stdexcept>
#include <string>

int main(int argc, char** argv) {
    try {
        if (argc != 3) throw std::runtime_error("expected H2G and G2H mailbox paths");
        chimaera_mailbox::Mailbox h2g(argv[1], true), g2h(argv[2], true);
        std::cout << "READY" << std::endl;
        std::string command;
        while (std::getline(std::cin, command)) {
            if (command == "QUIT") return 0;
            if (command != "PUMP") throw std::runtime_error("unknown boundary command");
            unsigned count = 0;
            chimaera_mailbox::Mailbox::View message{};
            while (g2h.peek(message)) {
                if (!h2g.write(message.data, message.size)) break;
                g2h.pop();
                ++count;
            }
            std::cout << count << std::endl;
        }
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << std::endl;
        return 1;
    }
}
