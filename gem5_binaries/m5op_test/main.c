#include <stdint.h>
#include <stdio.h>
#include <gem5/m5ops.h>
#include <m5_mmap.h>

int main(void)
{
    printf("before custom m5op\n");

    m5op_addr = 0xFFFF0000;   // x86 magic address
    map_m5_mem();

    m5_chimaera_test_addr(3, 12345);

    unmap_m5_mem();

    // m5_bridge_notify(3, 12345);

    printf("after custom m5op\n");
    return 0;
}
