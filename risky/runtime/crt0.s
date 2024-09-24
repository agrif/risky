    .section .header
    .globl _reset_vector
_reset_vector:
    j _start
    ebreak

    .section .text

_start:
    # initialize our pointers
    la sp, _stack_start

    # careful to turn off relax or else this becomes mv gp, gp
    .option push
    .option norelax
    la gp, __global_pointer$
    .option pop

    # zero-init bss
    la a0, __sbss
    la a1, __ebss
    bge a0, a1, end_init_bss
loop_init_bss:
    sw zero, 0(a0)
    addi a0, a0, 4
    blt a0, a1, loop_init_bss
end_init_bss:

    # copy data section to load address
    la a0, __sidata
    la a1, __sdata
    la a2, __edata
    bge a1, a2, end_init_data
loop_init_data:
    lw a3, 0(a0)
    sw a3, 0(a1)
    addi a0, a0, 4
    addi a1, a1, 4
    blt a1, a2, loop_init_data
end_init_data:

    # call in to main
    call main

    # if main returns, halt
    ebreak
