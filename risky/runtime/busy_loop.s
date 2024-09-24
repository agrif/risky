    .equ WAIT_BITS, 21
    #.equ WAIT_BITS, 6

    .globl busy_loop
busy_loop:
    li a1, 1
    slli a1, a1, WAIT_BITS
0:
    addi a1, a1, -1
    bnez a1, 0b
    ret
