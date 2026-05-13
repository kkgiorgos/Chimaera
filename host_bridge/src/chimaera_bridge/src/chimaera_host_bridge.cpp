#include <cstddef>
#include <cstdlib>
#include <unistd.h>
#include <cstdio>
#include <cstdint>
#include <sys/types.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <string.h>

#define handle_error(msg) \
    do { perror(msg); exit(EXIT_FAILURE); } while (0)


static constexpr const char* SOCKET_PATH = "/tmp/chimaera_g2h.sock";

union size_msg_t {
    char c[8];
    uint64_t l;
};

// Util Function to read a chunk into the buffer
int read_plus(int fd, char *buff, size_t buff_size) {
    ssize_t rcnt;
    if ((rcnt = read(fd, buff, buff_size)) == -1) {
        handle_error("read");
    }
    return rcnt;
}

int main(int argc, char ** argv)
{
    (void) argc;
    (void) argv;

    // Remove stale socket file if it exists.
    unlink(SOCKET_PATH);

    int server_fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (server_fd == -1) {
        handle_error("socket");
    }

    sockaddr_un addr{};
    addr.sun_family = AF_UNIX;
    strncpy(addr.sun_path, SOCKET_PATH, sizeof(addr.sun_path) - 1);

    if (bind(server_fd, (struct sockaddr *) &addr, sizeof(addr)) == -1) {
        handle_error("bind");
    }

    if (listen(server_fd, 16) == -1) {
        handle_error("listen");
    }

    printf("Server Listening on %s\n", SOCKET_PATH);

    while (true) {
        int client_fd = accept(server_fd, nullptr, nullptr);
        if (client_fd == -1) {
            perror("accept");
            continue;
        }

        printf("Client connected\n");

        size_msg_t msg_size;
        msg_size.l = 0;
        read_plus(client_fd, msg_size.c, 8);

        printf("Expecting %ld bytes\n", msg_size.l);

        char *buf = new char[msg_size.l];

        size_t rcnt = -1, total = 0;
        while (total != msg_size.l) {
            rcnt = read_plus(client_fd, buf + total, msg_size.l - total);
            total += rcnt;
            if (rcnt == 0) {
                break;
            }
        }

        if (total != msg_size.l) {
            fprintf(stderr, "Couldn't read the full message: read %ld out of %ld bytes.\n", total, msg_size.l);
            exit(EXIT_FAILURE);
        }

        printf("Client disconnected\n");

        printf("Received %ld bytes\n", total);

        printf("First byte: 0x%x\n", buf[0]);
        printf("Last byte: 0x%x\n", buf[total - 1]);

        printf("\n\n");

        delete [] buf;

        close(client_fd);
    }

    return 0;
}
