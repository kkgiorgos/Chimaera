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

using namespace std;


#define handle_error(msg) \
    do { perror(msg); exit(EXIT_FAILURE); } while (0)


static constexpr const char* SOCKET_PATH = "/tmp/chimaera_g2h.sock";

union size_msg_t {
    char c[8];
    uint64_t l;
};

// Util function to write the buffer
void write_plus(int fd, char *buff, size_t len) {
    size_t idx;
    ssize_t wcnt;

    for (idx = 0; idx < len; idx += wcnt) {
        if ((wcnt = write(fd, buff + idx, len - idx)) == -1) {
            handle_error("write");
        }
    }
}

int main(int argc, char* argv[]) {
    size_msg_t len;
    len.l = 0;
    len.l = 10;

    if (argc == 2) {
        len.l = atoi(argv[1]);
    }

    char *buf = new char[len.l];

    int fd = open("/dev/random", O_RDONLY);
    read(fd, buf, len.l);
    close(fd);


    //-----------------

    fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd == -1) {
        handle_error("socket");
    }

    sockaddr_un addr{};
    addr.sun_family = AF_UNIX;
    strncpy(addr.sun_path, SOCKET_PATH, sizeof(addr.sun_path) - 1);

    if (connect(fd, (struct sockaddr *) &addr, sizeof(addr)) == -1) {
        handle_error("connect");
    }

    write_plus(fd, len.c, 8);
    write_plus(fd, buf, len.l);

    printf("Sent %ld bytes\n", len.l);
    printf("First byte: 0x%x\n", buf[0]);
    printf("Last byte: 0x%x\n", buf[len.l - 1]);

    close(fd);

    delete [] buf;

    return 0;
}
