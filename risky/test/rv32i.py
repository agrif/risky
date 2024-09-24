import risky.test

class LUI(risky.test.ProgramTest):
    PROGRAM = """
    lui a0, 1
    one:

    lui a1, 0xfffff
    fs:

    lui a2, 0xabcde
    letters:
    """

    CHECKPOINTS = [
        ('one', {'a0': 1 << 12}),
        ('fs', {'a1': 0xfffff << 12}),
        ('letters', {'a2': 0xabcde << 12}),
    ]

class AUIPC(risky.test.ProgramTest):
    PROGRAM = """
    one_pc:
    auipc a0, 1
    one:

    fs_pc:
    auipc a1, 0xfffff
    fs:

    letters_pc:
    auipc a2, 0xabcde
    letters:
    """

    async def testbench(self, ctx):
        await self.advance_until(ctx, 'one')
        self.assert_eq(ctx, 'a0', self.symbols['one_pc'] + (1 << 12))

        await self.advance_until(ctx, 'fs')
        self.assert_eq(ctx, 'a1', 0xffff_ffff & (self.symbols['fs_pc'] + (0xfffff << 12)))

        await self.advance_until(ctx, 'letters')
        self.assert_eq(ctx, 'a2', 0xffff_ffff & (self.symbols['letters_pc'] + (0xabcde << 12)))

class JAL(risky.test.ProgramTest):
    PROGRAM = """
    first:
    jal a0, second

    .org 0x100

    second:
    jal a1, first
    """

    async def testbench(self, ctx):
        await self.advance_until(ctx, 'second')
        self.assert_eq(ctx, 'a0', self.symbols['first'] + 4)

        await self.advance_until(ctx, 'first')
        self.assert_eq(ctx, 'a1', self.symbols['second'] + 4)

class JALR(risky.test.ProgramTest):
    PROGRAM = """
    j first

    .org 0x80
    first:
    la a3, (second - 4)
    first_pc:
    jalr a0, 4(a3)

    .org 0x100
    second:
    la a3, (first + 5)
    second_pc:
    jalr a1, -4(a3)
    """

    async def testbench(self, ctx):
        await self.advance_until(ctx, 'second')
        self.assert_eq(ctx, 'a0', self.symbols['first_pc'] + 4)

        await self.advance_until(ctx, 'first')
        self.assert_eq(ctx, 'a1', self.symbols['second_pc'] + 4)

class BEQ(risky.test.ProgramTest):
    PROGRAM = """
    li a0, 3
    li a1, -2
    loop_a:
    addi a0, a0, -1
    beq a0, a1, end_a
    j loop_a
    end_a:

    li a0, -2
    li a1, 3
    loop_b:
    addi a0, a0, 1
    beq a0, a1, end_b
    j loop_b
    end_b:
    """

    CHECKPOINTS = [
        ('loop_a', {'a0': 3, 'a1': (1 << 32) - 2}),
        ('loop_a', {'a0': 2, 'a1': (1 << 32) - 2}),
        ('loop_a', {'a0': 1, 'a1': (1 << 32) - 2}),
        ('loop_a', {'a0': 0, 'a1': (1 << 32) - 2}),
        ('loop_a', {'a0': (1 << 32) - 1, 'a1': (1 << 32) - 2}),
        ('end_a', {'a0': (1 << 32) - 2, 'a1': (1 << 32) - 2}),

        ('loop_b', {'a0': (1 << 32) - 2, 'a1': 3}),
        ('loop_b', {'a0': (1 << 32) - 1, 'a1': 3}),
        ('loop_b', {'a0': 0, 'a1': 3}),
        ('loop_b', {'a0': 1, 'a1': 3}),
        ('loop_b', {'a0': 2, 'a1': 3}),
        ('end_b', {'a0': 3, 'a1': 3}),
    ]

class BNE(risky.test.ProgramTest):
    PROGRAM = """
    li a0, 3
    li a1, -2
    loop_a:
    addi a0, a0, -1
    bne a0, a1, loop_a
    end_a:

    li a0, -2
    li a1, 3
    loop_b:
    addi a0, a0, 1
    bne a0, a1, loop_b
    end_b:
    """

    CHECKPOINTS = [
        ('loop_a', {'a0': 3, 'a1': (1 << 32) - 2}),
        ('loop_a', {'a0': 2, 'a1': (1 << 32) - 2}),
        ('loop_a', {'a0': 1, 'a1': (1 << 32) - 2}),
        ('loop_a', {'a0': 0, 'a1': (1 << 32) - 2}),
        ('loop_a', {'a0': (1 << 32) - 1, 'a1': (1 << 32) - 2}),
        ('end_a', {'a0': (1 << 32) - 2, 'a1': (1 << 32) - 2}),

        ('loop_b', {'a0': (1 << 32) - 2, 'a1': 3}),
        ('loop_b', {'a0': (1 << 32) - 1, 'a1': 3}),
        ('loop_b', {'a0': 0, 'a1': 3}),
        ('loop_b', {'a0': 1, 'a1': 3}),
        ('loop_b', {'a0': 2, 'a1': 3}),
        ('end_b', {'a0': 3, 'a1': 3}),
    ]

class BLT(risky.test.ProgramTest):
    PROGRAM = """
    li a0, -5
    li a1, -2
    loop_a:
    addi a0, a0, 1
    blt a0, a1, loop_a
    end_a:

    li a0, -2
    li a1, 3
    loop_b:
    addi a0, a0, 1
    blt a0, a1, loop_b
    end_b:

    li a0, 2
    li a1, -3
    loop_c:
    addi a0, a0, -1
    blt a0, a1, loop_c
    end_c:
    """

    CHECKPOINTS = [
        ('loop_a', {'a0': (1 << 32) - 5, 'a1': (1 << 32) - 2}),
        ('loop_a', {'a0': (1 << 32) - 4, 'a1': (1 << 32) - 2}),
        ('loop_a', {'a0': (1 << 32) - 3, 'a1': (1 << 32) - 2}),
        ('end_a', {'a0': (1 << 32) - 2, 'a1': (1 << 32) - 2}),

        ('loop_b', {'a0': (1 << 32) - 2, 'a1': 3}),
        ('loop_b', {'a0': (1 << 32) - 1, 'a1': 3}),
        ('loop_b', {'a0': 0, 'a1': 3}),
        ('loop_b', {'a0': 1, 'a1': 3}),
        ('loop_b', {'a0': 2, 'a1': 3}),
        ('end_b', {'a0': 3, 'a1': 3}),

        ('loop_c', {'a0': 2, 'a1': (1 << 32) - 3}),
        ('end_c', {'a0': 1, 'a1': (1 << 32) - 3}),
    ]

class BGE(risky.test.ProgramTest):
    PROGRAM = """
    li a0, 5
    li a1, 2
    loop_a:
    addi a0, a0, -1
    bge a0, a1, loop_a
    end_a:

    li a0, 2
    li a1, -3
    loop_b:
    addi a0, a0, -1
    bge a0, a1, loop_b
    end_b:

    li a0, -3
    li a1, 2
    loop_c:
    addi a0, a0, 1
    bge a0, a1, loop_c
    end_c:
    """

    CHECKPOINTS = [
        ('loop_a', {'a0': 5, 'a1': 2}),
        ('loop_a', {'a0': 4, 'a1': 2}),
        ('loop_a', {'a0': 3, 'a1': 2}),
        ('loop_a', {'a0': 2, 'a1': 2}),
        ('end_a', {'a0': 1, 'a1': 2}),

        ('loop_b', {'a0': 2, 'a1': (1 << 32) - 3}),
        ('loop_b', {'a0': 1, 'a1': (1 << 32) - 3}),
        ('loop_b', {'a0': 0, 'a1': (1 << 32) - 3}),
        ('loop_b', {'a0': (1 << 32) - 1, 'a1': (1 << 32) - 3}),
        ('loop_b', {'a0': (1 << 32) - 2, 'a1': (1 << 32) - 3}),
        ('loop_b', {'a0': (1 << 32) - 3, 'a1': (1 << 32) - 3}),
        ('end_b', {'a0': (1 << 32) - 4, 'a1': (1 << 32) - 3}),

        ('loop_c', {'a0': (1 << 32) - 3, 'a1': 2}),
        ('end_c', {'a0': (1 << 32) - 2, 'a1': 2}),
    ]

class BLTU(risky.test.ProgramTest):
    PROGRAM = """
    li a0, -5
    li a1, -2
    loop_a:
    addi a0, a0, 1
    bltu a0, a1, loop_a
    end_a:

    li a0, -2
    li a1, 3
    loop_b:
    addi a0, a0, 1
    bltu a0, a1, loop_b
    end_b:

    li a0, 2
    li a1, -3
    loop_c:
    addi a0, a0, -1
    bltu a0, a1, loop_c
    end_c:
    """

    CHECKPOINTS = [
        ('loop_a', {'a0': (1 << 32) - 5, 'a1': (1 << 32) - 2}),
        ('loop_a', {'a0': (1 << 32) - 4, 'a1': (1 << 32) - 2}),
        ('loop_a', {'a0': (1 << 32) - 3, 'a1': (1 << 32) - 2}),
        ('end_a', {'a0': (1 << 32) - 2, 'a1': (1 << 32) - 2}),

        ('loop_b', {'a0': (1 << 32) - 2, 'a1': 3}),
        ('end_b', {'a0': (1 << 32) - 1, 'a1': 3}),

        ('loop_c', {'a0': 2, 'a1': (1 << 32) - 3}),
        ('loop_c', {'a0': 1, 'a1': (1 << 32) - 3}),
        ('loop_c', {'a0': 0, 'a1': (1 << 32) - 3}),
        ('end_c', {'a0': (1 << 32) - 1, 'a1': (1 << 32) - 3}),
    ]

class BGEU(risky.test.ProgramTest):
    PROGRAM = """
    li a0, 5
    li a1, 2
    loop_a:
    addi a0, a0, -1
    bgeu a0, a1, loop_a
    end_a:

    li a0, 2
    li a1, -3
    loop_b:
    addi a0, a0, -1
    bgeu a0, a1, loop_b
    end_b:

    li a0, -3
    li a1, 2
    loop_c:
    addi a0, a0, 1
    bgeu a0, a1, loop_c
    end_c:
    """

    CHECKPOINTS = [
        ('loop_a', {'a0': 5, 'a1': 2}),
        ('loop_a', {'a0': 4, 'a1': 2}),
        ('loop_a', {'a0': 3, 'a1': 2}),
        ('loop_a', {'a0': 2, 'a1': 2}),
        ('end_a', {'a0': 1, 'a1': 2}),

        ('loop_b', {'a0': 2, 'a1': (1 << 32) - 3}),
        ('end_b', {'a0': 1, 'a1': (1 << 32) - 3}),

        ('loop_c', {'a0': (1 << 32) - 3, 'a1': 2}),
        ('loop_c', {'a0': (1 << 32) - 2, 'a1': 2}),
        ('loop_c', {'a0': (1 << 32) - 1, 'a1': 2}),
        ('end_c', {'a0': 0, 'a1': 2}),
    ]

class LB(risky.test.ProgramTest):
    PROGRAM = """
    la t0, ptr

    lb a0, -4(t0)
    lb a1, -3(t0)
    lb a2, -2(t0)
    lb a3, -1(t0)
    a:
    
    lb a0, 0(t0)
    lb a1, 1(t0)
    lb a2, 2(t0)
    lb a3, 3(t0)
    b:

    .byte 0x12, 0x34, 0xd6, 0xf8
    ptr:
    .byte 0x92, 0xb4, 0x56, 0x78
    """

    CHECKPOINTS = [
        ('a', {
            'a0': 0x12,
            'a1': 0x34,
            'a2': 0xffff_ffd6,
            'a3': 0xffff_fff8,
        }),
        ('b', {
            'a0': 0xffff_ff92,
            'a1': 0xffff_ffb4,
            'a2': 0x56,
            'a3': 0x78,
        }),
    ]

class LH(risky.test.ProgramTest):
    PROGRAM = """
    la t0, ptr

    lh a0, -4(t0)
    lh a1, -2(t0)
    a:
    
    lh a0, 0(t0)
    lh a1, 2(t0)
    b:

    .byte 0x12, 0x34, 0xd6, 0xf8
    ptr:
    .byte 0x92, 0xb4, 0x56, 0x78
    """

    CHECKPOINTS = [
        ('a', {
            'a0': 0x3412,
            'a1': 0xffff_f8d6,
        }),
        ('b', {
            'a0': 0xffff_b492,
            'a1': 0x7856,
        }),
    ]

class LW(risky.test.ProgramTest):
    PROGRAM = """
    la t0, ptr

    lw a0, -4(t0)
    a:
    
    lw a0, 0(t0)
    b:

    .byte 0x12, 0x34, 0xd6, 0xf8
    ptr:
    .byte 0x92, 0xb4, 0x56, 0x78
    """

    CHECKPOINTS = [
        ('a', {
            'a0': 0xf8d63412,
        }),
        ('b', {
            'a0': 0x7856b492,
        }),
    ]

class LBU(risky.test.ProgramTest):
    PROGRAM = """
    la t0, ptr

    lbu a0, -4(t0)
    lbu a1, -3(t0)
    lbu a2, -2(t0)
    lbu a3, -1(t0)
    a:
    
    lbu a0, 0(t0)
    lbu a1, 1(t0)
    lbu a2, 2(t0)
    lbu a3, 3(t0)
    b:

    .byte 0x12, 0x34, 0xd6, 0xf8
    ptr:
    .byte 0x92, 0xb4, 0x56, 0x78
    """

    CHECKPOINTS = [
        ('a', {
            'a0': 0x12,
            'a1': 0x34,
            'a2': 0xd6,
            'a3': 0xf8,
        }),
        ('b', {
            'a0': 0x92,
            'a1': 0xb4,
            'a2': 0x56,
            'a3': 0x78,
        }),
    ]

class LHU(risky.test.ProgramTest):
    PROGRAM = """
    la t0, ptr

    lhu a0, -4(t0)
    lhu a1, -2(t0)
    a:
    
    lhu a0, 0(t0)
    lhu a1, 2(t0)
    b:

    .byte 0x12, 0x34, 0xd6, 0xf8
    ptr:
    .byte 0x92, 0xb4, 0x56, 0x78
    """

    CHECKPOINTS = [
        ('a', {
            'a0': 0x3412,
            'a1': 0xf8d6,
        }),
        ('b', {
            'a0': 0xb492,
            'a1': 0x7856,
        }),
    ]

class SB(risky.test.ProgramTest):
    PROGRAM = """
    li a0, 0x12345678
    li a1, 0x9abcdef0
    la a2, ptr
    a:

    sb a0, -8(a2)
    sb a0, -3(a2)
    sb a0, 2(a2)
    sb a0, 7(a2)
    b:

    sb a1, -7(a2)
    sb a1, -3(a2)
    sb a1, 1(a2)
    sb a1, 5(a2)
    c:

    .section .bss
    dest0:
    .word 0
    dest1:
    .word 0
    ptr:
    dest2:
    .word 0
    dest3:
    .word 0
    """

    CHECKPOINTS = [
        ('a', {'a0': 0x1234_5678, 'a1': 0x9abc_def0}),
        ('b', {
            'memory.dest0': 0x0000_0078,
            'memory.dest1': 0x0000_7800,
            'memory.dest2': 0x0078_0000,
            'memory.dest3': 0x7800_0000,
        }),
        ('c', {
            'memory.dest0': 0x0000_f078,
            'memory.dest1': 0x0000_f000,
            'memory.dest2': 0x0078_f000,
            'memory.dest3': 0x7800_f000,
        }),
    ]

class SH(risky.test.ProgramTest):
    PROGRAM = """
    li a0, 0x12345678
    li a1, 0x9abcdef0
    la a2, ptr
    a:

    sh a0, -8(a2)
    sh a0, -2(a2)
    sh a0, 0(a2)
    sh a0, 6(a2)
    b:

    sh a1, -8(a2)
    sh a1, -4(a2)
    sh a1, 2(a2)
    sh a1, 6(a2)
    c:

    .section .bss
    dest0:
    .word 0
    dest1:
    .word 0
    ptr:
    dest2:
    .word 0
    dest3:
    .word 0
    """

    CHECKPOINTS = [
        ('a', {'a0': 0x1234_5678, 'a1': 0x9abc_def0}),
        ('b', {
            'memory.dest0': 0x0000_5678,
            'memory.dest1': 0x5678_0000,
            'memory.dest2': 0x0000_5678,
            'memory.dest3': 0x5678_0000,
        }),
        ('c', {
            'memory.dest0': 0x0000_def0,
            'memory.dest1': 0x5678_def0,
            'memory.dest2': 0xdef0_5678,
            'memory.dest3': 0xdef0_0000,
        }),
    ]

class SW(risky.test.ProgramTest):
    PROGRAM = """
    li a0, 0x12345678
    li a1, 0x9abcdef0
    la a2, ptr
    a:

    sw a0, -4(a2)
    sw a1, 0(a2)
    b:

    sw a0, 0(a2)
    sw a1, -4(a2)
    c:

    .section .bss
    dest0:
    .word 0
    ptr:
    dest1:
    .word 0
    """

    CHECKPOINTS = [
        ('a', {'a0': 0x1234_5678, 'a1': 0x9abc_def0}),
        ('b', {
            'memory.dest0': 0x1234_5678,
            'memory.dest1': 0x9abc_def0,
        }),
        ('c', {
            'memory.dest0': 0x9abc_def0,
            'memory.dest1': 0x1234_5678,
        }),
    ]

class ADDI(risky.test.ProgramTest):
    PROGRAM = """
    addi a0, zero, 22
    addi a1, zero, 5
    addi a2, zero, -10

    a:
    addi a4, a0, 5
    addi a5, a1, -2
    addi a6, a2, 12
    addi a7, a2, -5

    b:
    """

    CHECKPOINTS = [
        ('a', {'a0': 22, 'a1': 5, 'a2': (1 << 32) - 10}),
        ('b', {'a4': 27, 'a5': 3, 'a6': 2, 'a7': (1 << 32) - 15}),
    ]

class SLTI(risky.test.ProgramTest):
    PROGRAM = """
    li a0, -5
    a:

    slti a1, a0, 5
    slti a2, a0, -1
    slti a3, a0, -7

    b:
    """

    CHECKPOINTS = [
        ('a', {'a0': (1 << 32) - 5}),
        ('b', {'a1': 1, 'a2': 1, 'a3': 0}),
    ]

class SLTIU(risky.test.ProgramTest):
    PROGRAM = """
    li a0, -5
    a:

    sltiu a1, a0, 5
    sltiu a2, a0, -1
    sltiu a3, a0, -7

    b:
    """

    CHECKPOINTS = [
        ('a', {'a0': (1 << 32) - 5}),
        ('b', {'a1': 0, 'a2': 1, 'a3': 0}),
    ]

class XORI(risky.test.ProgramTest):
    PROGRAM = """
    li a0, 0x7ff
    a:

    xori a1, a0, 0x2aa
    xori a2, a0, 0x555
    xori a3, a0, 0x7ff

    b:
    """

    CHECKPOINTS = [
        ('a', {'a0': 0x7ff}),
        ('b', {'a1': 0x7ff ^ 0x2aa, 'a2': 0x7ff ^ 0x555, 'a3': 0x7ff ^ 0x7ff}),
    ]

class ORI(risky.test.ProgramTest):
    PROGRAM = """
    li a0, 0x0f0
    a:

    ori a1, a0, 0x2aa
    ori a2, a0, 0x555
    ori a3, a0, 0x7ff

    b:
    """

    CHECKPOINTS = [
        ('a', {'a0': 0x0f0}),
        ('b', {'a1': 0x0f0 | 0x2aa, 'a2': 0x0f0 | 0x555, 'a3': 0x0f0 | 0x7ff}),
    ]

class ANDI(risky.test.ProgramTest):
    PROGRAM = """
    li a0, 0x70f
    a:

    andi a1, a0, 0x2aa
    andi a2, a0, 0x555
    andi a3, a0, 0x7ff

    b:
    """

    CHECKPOINTS = [
        ('a', {'a0': 0x70f}),
        ('b', {'a1': 0x70f & 0x2aa, 'a2': 0x70f & 0x555, 'a3': 0x70f & 0x7ff}),
    ]

class SLLI(risky.test.ProgramTest):
    PROGRAM = """
    li a0, 0x7a5
    a:

    slli a1, a0, 2
    slli a2, a0, 5
    slli a3, a0, 30

    b:
    """

    CHECKPOINTS = [
        ('a', {'a0': 0x7a5}),
        ('b', {'a1': 0x7a5 << 2, 'a2': 0x7a5 << 5, 'a3': (0x7a5 << 30) & 0xffff_ffff}),
    ]

class SRLI(risky.test.ProgramTest):
    PROGRAM = """
    li a0, 0x8a500000
    a:

    srli a1, a0, 2
    srli a2, a0, 5
    srli a3, a0, 30

    b:
    li a0, 0x7a500000
    c:

    srli a1, a0, 2
    srli a2, a0, 5
    srli a3, a0, 30

    d:
    """

    basea = 0x8a500000
    baseb = 0x7a500000
    CHECKPOINTS = [
        ('a', {'a0': basea}),
        ('b', {'a1': basea >> 2, 'a2': basea >> 5, 'a3': basea >> 30}),
        ('c', {'a0': baseb}),
        ('d', {'a1': baseb >> 2, 'a2': baseb >> 5, 'a3': baseb >> 30}),
    ]

class SRAI(risky.test.ProgramTest):
    PROGRAM = """
    li a0, 0x8a500000
    a:

    srai a1, a0, 2
    srai a2, a0, 5
    srai a3, a0, 30

    b:
    li a0, 0x7a500000
    c:

    srai a1, a0, 2
    srai a2, a0, 5
    srai a3, a0, 30

    d:
    """

    basea = 0x8a500000
    baseb = 0x7a500000
    CHECKPOINTS = [
        ('a', {'a0': basea}),
        ('b', {
            'a1': (basea >> 2) | (0b11 << 30),
            'a2': (basea >> 5) | (0b11111 << 27),
            'a3': (basea >> 30) | (0xffff_fffc),
        }),
        ('c', {'a0': baseb}),
        ('d', {'a1': baseb >> 2, 'a2': baseb >> 5, 'a3': baseb >> 30}),
    ]

class ADD(risky.test.ProgramTest):
    PROGRAM = """
    li a0, 22
    li a1, 5
    li a2, -10

    li t0, 5
    li t1, -2
    li t2, 12
    li t3, -5

    a:
    add a4, a0, t0
    add a5, a1, t1
    add a6, a2, t2
    add a7, a2, t3

    b:
    """

    CHECKPOINTS = [
        ('a', {'a0': 22, 'a1': 5, 'a2': (1 << 32) - 10, 't0': 5, 't1': (1 << 32) - 2, 't2': 12, 't3': (1 << 32) - 5}),
        ('b', {'a4': 27, 'a5': 3, 'a6': 2, 'a7': (1 << 32) - 15}),
    ]

class SUB(risky.test.ProgramTest):
    PROGRAM = """
    li a0, 22
    li a1, 5
    li a2, -10

    li t0, 5
    li t1, -2
    li t2, 12
    li t3, -5

    a:
    sub a4, a0, t0
    sub a5, a1, t1
    sub a6, a2, t2
    sub a7, a2, t3

    b:
    """

    CHECKPOINTS = [
        ('a', {'a0': 22, 'a1': 5, 'a2': (1 << 32) - 10, 't0': 5, 't1': (1 << 32) - 2, 't2': 12, 't3': (1 << 32) - 5}),
        ('b', {'a4': 17, 'a5': 7, 'a6': (1 << 32) - 22, 'a7': (1 << 32) - 5}),
    ]

class SLL(risky.test.ProgramTest):
    PROGRAM = """
    li a0, 0x7a5

    li t1, 2
    li t2, 5
    li t3, 30
    a:

    sll a1, a0, t1
    sll a2, a0, t2
    sll a3, a0, t3

    b:
    """

    CHECKPOINTS = [
        ('a', {'a0': 0x7a5, 't1': 2, 't2': 5, 't3': 30}),
        ('b', {'a1': 0x7a5 << 2, 'a2': 0x7a5 << 5, 'a3': (0x7a5 << 30) & 0xffff_ffff}),
    ]

class SLT(risky.test.ProgramTest):
    PROGRAM = """
    li a0, -5

    li t1, 5
    li t2, -1
    li t3, -7
    a:

    slt a1, a0, t1
    slt a2, a0, t2
    slt a3, a0, t3

    b:
    """

    CHECKPOINTS = [
        ('a', {'a0': (1 << 32) - 5, 't1': 5, 't2': (1 << 32) - 1, 't3': (1 << 32) - 7}),
        ('b', {'a1': 1, 'a2': 1, 'a3': 0}),
    ]

class SLTU(risky.test.ProgramTest):
    PROGRAM = """
    li a0, -5

    li t1, 5
    li t2, -1
    li t3, -7
    a:

    sltu a1, a0, t1
    sltu a2, a0, t2
    sltu a3, a0, t3

    b:
    """

    CHECKPOINTS = [
        ('a', {'a0': (1 << 32) - 5, 't1': 5, 't2': (1 << 32) - 1, 't3': (1 << 32) - 7}),
        ('b', {'a1': 0, 'a2': 1, 'a3': 0}),
    ]

class XOR(risky.test.ProgramTest):
    PROGRAM = """
    li a0, 0x7ff

    li t1, 0x2aa
    li t2, 0x555
    li t3, 0x7ff
    a:

    xor a1, a0, t1
    xor a2, a0, t2
    xor a3, a0, t3

    b:
    """

    CHECKPOINTS = [
        ('a', {'a0': 0x7ff, 't1': 0x2aa, 't2': 0x555, 't3': 0x7ff}),
        ('b', {'a1': 0x7ff ^ 0x2aa, 'a2': 0x7ff ^ 0x555, 'a3': 0x7ff ^ 0x7ff}),
    ]

class SRL(risky.test.ProgramTest):
    PROGRAM = """
    li a0, 0x8a500000

    li t1, 2
    li t2, 5
    li t3, 30
    a:

    srl a1, a0, t1
    srl a2, a0, t2
    srl a3, a0, t3

    b:
    li a0, 0x7a500000
    c:

    srl a1, a0, t1
    srl a2, a0, t2
    srl a3, a0, t3

    d:
    """

    basea = 0x8a500000
    baseb = 0x7a500000
    CHECKPOINTS = [
        ('a', {'a0': basea, 't1': 2, 't2': 5, 't3': 30}),
        ('b', {'a1': basea >> 2, 'a2': basea >> 5, 'a3': basea >> 30}),
        ('c', {'a0': baseb, 't1': 2, 't2': 5, 't3': 30}),
        ('d', {'a1': baseb >> 2, 'a2': baseb >> 5, 'a3': baseb >> 30}),
    ]

class SRA(risky.test.ProgramTest):
    PROGRAM = """
    li a0, 0x8a500000

    li t1, 2
    li t2, 5
    li t3, 30
    a:

    sra a1, a0, t1
    sra a2, a0, t2
    sra a3, a0, t3

    b:
    li a0, 0x7a500000
    c:

    sra a1, a0, t1
    sra a2, a0, t2
    sra a3, a0, t3

    d:
    """

    basea = 0x8a500000
    baseb = 0x7a500000
    CHECKPOINTS = [
        ('a', {'a0': basea, 't1': 2, 't2': 5, 't3': 30}),
        ('b', {
            'a1': (basea >> 2) | (0b11 << 30),
            'a2': (basea >> 5) | (0b11111 << 27),
            'a3': (basea >> 30) | (0xffff_fffc),
        }),
        ('c', {'a0': baseb, 't1': 2, 't2': 5, 't3': 30}),
        ('d', {'a1': baseb >> 2, 'a2': baseb >> 5, 'a3': baseb >> 30}),
    ]

class OR(risky.test.ProgramTest):
    PROGRAM = """
    li a0, 0x0f0

    li t1, 0x2aa
    li t2, 0x555
    li t3, 0x7ff
    a:

    or a1, a0, t1
    or a2, a0, t2
    or a3, a0, t3

    b:
    """

    CHECKPOINTS = [
        ('a', {'a0': 0x0f0, 't1': 0x2aa, 't2': 0x555, 't3': 0x7ff}),
        ('b', {'a1': 0x0f0 | 0x2aa, 'a2': 0x0f0 | 0x555, 'a3': 0x0f0 | 0x7ff}),
    ]

class AND(risky.test.ProgramTest):
    PROGRAM = """
    li a0, 0x70f

    li t1, 0x2aa
    li t2, 0x555
    li t3, 0x7ff
    a:

    and a1, a0, t1
    and a2, a0, t2
    and a3, a0, t3

    b:
    """

    CHECKPOINTS = [
        ('a', {'a0': 0x70f, 't1': 0x2aa, 't2': 0x555, 't3': 0x7ff}),
        ('b', {'a1': 0x70f & 0x2aa, 'a2': 0x70f & 0x555, 'a3': 0x70f & 0x7ff}),
    ]

# FIXME FENCE, FENCE.TSO
# FIXME PAUSE
# FIXME ECALL
# FIXME EBREAK
