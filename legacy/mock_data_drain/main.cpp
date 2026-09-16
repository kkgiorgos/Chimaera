#include <unistd.h>
#include <sys/types.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <cstring>
#include <cstdio>
#include <cstdint>
#include <cstdlib>
#include <cstddef>
#include <errno.h>

using namespace std;


#define handle_error(msg) \
    do { perror(msg); exit(EXIT_FAILURE); } while (0)


static constexpr const char* SOCKET_PATH = "/tmp/chimaera_h2g.sock";

void read_exact(int fd, char *buff, size_t len)
{
    size_t total = 0;
    while (total != len) {
        ssize_t rcnt;
        if ((rcnt = read(fd, buff + total, len - total)) == -1) {
            if (errno == EINTR) {
                continue;
            }
            handle_error("read");
        }
        if (rcnt == 0) {
            fprintf(stderr, "Couldn't read the full message: read %ld out of %ld bytes.",
                    total,
                    len);
            exit(EXIT_FAILURE);
        }
        total += rcnt;
    }
}

int main(int argc, char* argv[]) {

    uint64_t len = 4096;

    if (argc == 2) {
        len = atoi(argv[1]);
    }

    len = 4096;

    char *buf = new char[len];

    int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd == -1) {
        handle_error("socket");
    }

    sockaddr_un addr{};
    addr.sun_family = AF_UNIX;
    strncpy(addr.sun_path, SOCKET_PATH, sizeof(addr.sun_path) - 1);

    if (connect(fd, (struct sockaddr *) &addr, sizeof(addr)) == -1) {
        handle_error("connect");
    }

    read_exact(fd, buf, len);

    printf("Received %ld bytes\n", len);
    printf("First byte: 0x%x\n", buf[0]);
    printf("Last byte: 0x%x\n", buf[len - 1]);

    close(fd);

    delete [] buf;

    return 0;
}
