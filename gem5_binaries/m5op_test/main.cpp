#include <unistd.h>
#include <sys/types.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <cstring>
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <gem5/m5ops.h>
#include <m5_mmap.h>

int main(int argc, char* argv[])
{
    printf("before custom m5op\n");

    m5op_addr = 0xFFFF0000;   // x86 magic address
    map_m5_mem();

    uint64_t len = 10;

    if (argc == 2) {
        len = atoi(argv[1]);
    }

    char *buf = new char[len];

    int fd = open("/dev/random", O_RDONLY);
    read(fd, buf, len);

    printf("First byte: 0x%x\n", buf[0]);

    printf("before custom m5op\n");
    m5_chimaera_test_addr(1, 1);
    m5_work_begin_addr(0, 0);
    uint64_t res = m5_chimaera_send_addr(buf, len);
    // m5_work_end_addr(0, 0);
    m5_chimaera_test_addr(1, 2);
    printf("after custom m5op\n");

    printf("Sent %lu bytes\n", res);

    delete [] buf;

    unmap_m5_mem();

    return 0;
}
