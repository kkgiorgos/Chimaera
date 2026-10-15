// SPDX-License-Identifier: MIT
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include <gem5/m5ops.h>
#include <sched.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/wait.h>
#include <unistd.h>
int
main(void)
{
    int gate[2];
    if (pipe(gate)) {
        return 6;
    }
    for (unsigned role = 1; role <= 4; ++role) {
        pid_t child = fork();
        if (child < 0) {
            return 1;
        }
        if (child == 0) {
            close(gate[1]);
            char ready;
            if (read(gate[0], &ready, 1) != 1) {
                _exit(7);
            }
            m5_chimaera_test(0x524f534452414dULL, role);
            const size_t words = 65536;
            volatile uint64_t *data = static_cast<volatile uint64_t *>(
                malloc(words * sizeof(uint64_t)));
            if (!data) {
                _exit(2);
            }
            for (size_t i = 0; i < words; ++i) {
                data[i] = i + role;
            }
            uint64_t sum = 0;
            for (int pass = 0; pass < 3; ++pass) {
                cpu_set_t affinity;
                CPU_ZERO(&affinity);
                CPU_SET(pass % 2, &affinity);
                if (sched_setaffinity(0, sizeof(affinity), &affinity)) {
                    _exit(3);
                }
                for (size_t i = 0; i < words; i += 8) {
                    sum += data[i];
                }
            }
            const uint64_t count = words / 8;
            const uint64_t expected =
                3 * (8 * count * (count - 1) / 2 + role * count);
            if (sum != expected) {
                _exit(4);
            }
            m5_chimaera_test(0x444d0000ULL + role, sum);
            printf("NODE_DRAM_PROBE_ROLE=%u SUM=%lu\n", role, sum);
            fflush(stdout);
            _exit(sum ? 0 : 4);
        }
    }
    close(gate[0]);
    if (write(gate[1], "go!!", 4) != 4) {
        return 8;
    }
    close(gate[1]);
    for (int i = 0; i < 4; ++i) {
        int status;
        if (wait(&status) < 0 || !WIFEXITED(status) || WEXITSTATUS(status)) {
            return 5;
        }
    }
    puts("NODE_DRAM_PROBE_OK");
    m5_chimaera_test(0x444dffffULL, 1);
    m5_work_end(0, 0);
    return 0;
}
