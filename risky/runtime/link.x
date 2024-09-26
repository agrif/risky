INCLUDE memory.x

/* memory.x should include a definition like:

MEMORY
{
    // NOTE 1 K = 1 KiB = 1024 bytes
    ROM (xr) : ORIGIN = 0x00000000, LENGTH = 64K
    RAM (rw) : ORIGIN = 0x10000000, LENGTH = 32K
}
*/

EXTERN(_reset_vector);
ENTRY(_reset_vector);

SECTIONS
{
    PROVIDE(_ram_start = ORIGIN(RAM));
    PROVIDE(_ram_end = ORIGIN(RAM) + LENGTH(RAM));
    PROVIDE(_stack_start = _ram_end);

    /* use gp for io by default */
    /* FIXME use it for sbss, sdata, etc. */
    /* FIXME why do I need -1 here, -0x800 should be in 12-bit range... */
    PROVIDE(__global_pointer$ = 0x20000000 + 0x800 - 1);

    .text : ALIGN(4)
    {
        . = ALIGN(4);

        __sheader = .;
        /* header must go first */
        KEEP(*(.header .header.*));
        . = ALIGN(4);
        __eheader = .;

        __stext = .;
        *(.text .text.*);

        . = ALIGN(4);
    } > ROM

    . = ALIGN(4);
    __etext = .;

    .rodata : ALIGN(4)
    {
        . = ALIGN(4);
        __srodata = .;
        *(.rodata .rodata.*);
        *(.srodata .srodata.*);

        . = ALIGN(4);
    } > ROM

    . = ALIGN(4);
    __erodata = .;

    .data : ALIGN(4)
    {
        . = ALIGN(4);
        __sdata = .;
        *(.data .data.*);
        *(.sdata .sdata.*);

        . = ALIGN(4);
    } > RAM AT>ROM

    . = ALIGN(4);
    __edata = .;

    __sidata = LOADADDR(.data);

    .bss (NOLOAD) : ALIGN(4)
    {
        . = ALIGN(4);
        __sbss = .;
        *(.bss .bss.*);
        *(.sbss .sbss.*);
        *(COMMON);

        . = ALIGN(4);
    } > RAM

    . = ALIGN(4);
    __ebss = .;

    .uninit (NOLOAD) : ALIGN(4)
    {
        . = ALIGN(4);
        __suninit = .;
        *(.uninit .uninit.*);

        . = ALIGN(4);
    } > RAM

    . = ALIGN(4);
    __euninit = .;

    PROVIDE(__sheap = .);
}

/* some sanity checks */

ASSERT(_reset_vector == 0, "
BUG(link.x): reset vector not at address 0");

ASSERT(__sdata % 4 == 0 && __edata % 4 == 0, "
BUG(link.x): .data is not 4-byte aligned");

ASSERT(__sidata % 4 == 0, "
BUG(link.x): the LMA of .data is not 4-byte aligned");

ASSERT(__sbss % 4 == 0 && __ebss % 4 == 0, "
BUG(link.x): .bss is not 4-byte aligned");

ASSERT(__sheap % 4 == 0, "
ERROR(link.x): start of heap is not 4-byte aligned");

ASSERT(_stack_start % 4 == 0, "
ERROR(link.x): start of stack is not 4-byte aligned");
