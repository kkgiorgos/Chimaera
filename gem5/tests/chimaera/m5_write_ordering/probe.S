#include <gem5/asm/generic/m5ops.h>

// PROBE_OP selects either M5OP_CHIMAERA_RECV or M5OP_READ_FILE.
.section .data
.balign 64
buffer:
    .quad 0

.section .text
.global _start
_start:
    // An older load warms the zero-filled buffer while retirement waits for
    // the cache miss. O3 can issue the younger load before a merely
    // non-speculative m5 instruction executes, retaining the old zero.
    movq buffer(%rip), %r12
    leaq buffer(%rip), %rdi
    movq $8, %rsi
    xorl %edx, %edx  // m5_read_file offset, unused by chimaera_recv
    .byte 0x0f, 0x04
    .word PROBE_OP
    movq buffer(%rip), %r13
    bswap %r13

    // The host writes the big-endian controller header value 3. Exit 1 if
    // the subsequent load observes stale data, or 0 on correct ordering.
    xorl %edi, %edi
    cmpq $3, %r13
    setne %dil
    movq $60, %rax
    syscall
.section .note.GNU-stack,"",@progbits
