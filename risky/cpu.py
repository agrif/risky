import collections

import amaranth as am
import amaranth.lib.data
import amaranth.lib.enum

from risky.instruction import Instruction, Reg, Op, Funct3Branch, Funct3Alu, Funct7Alu, Funct3Mem, Funct3Csr
import risky.memory

class Extension:
    march = None

    def prepare(self, cpu):
        pass

    def elaborate_pre(self, platform, cpu, m):
        pass

    def execute(self, platform, cpu, m):
        pass

    def elaborate_post(self, platform, cpu, m):
        pass

    @property
    def debug_traces(self):
        return []

class State(am.lib.enum.Enum):
    FETCH_INSTR = 0
    EXECUTE = 1

class Cpu(am.lib.wiring.Component):
    bus: am.lib.wiring.Out(risky.memory.MemoryBus())

    xlen = 32
    march_base = 'rv32i'

    def __init__(self, extensions=[]):
        super().__init__()

        # core state
        self.state = am.Signal(State)
        self.pc = am.Signal(self.xlen)
        self.instr = am.Signal(Instruction)

        # re-usable pc adder
        self.pc_plus_inc = am.Signal(self.xlen)
        self.pc_inc = am.Signal(self.xlen)

        # set high by recognized instructions
        self.is_valid_instruction = am.Signal(1)
        # used by unit tests
        self.assert_unknown_instructions = False

        # register file
        regs = []
        for i, r in enumerate(Reg):
            assert r.value == i
            regs.append(am.Signal(self.xlen, name='x{}_{}'.format(i, r.name.lower())))
        self.regs = am.Array(regs)

        # hold values of rs1 / rs2 from instr
        self.rs1 = am.Signal(self.xlen)
        self.rs2 = am.Signal(self.xlen)

        # holds the value to store into rd, with an enable signal
        self.rd = am.Signal(self.xlen)
        self.rd_write_en = am.Signal(1)

        # our alu
        self.alu = Alu(self.xlen)

        self.extensions = collections.OrderedDict()
        for extension in extensions:
            self.extensions[extension.__class__.__name__] = extension
        for extension in self.extensions.values():
            extension.prepare(self)

    @property
    def march_parts(self):
        letters = []
        words = []
        for extension in self.extensions.values():
            m = extension.march
            if not m:
                continue
            elif len(m) == 1:
                letters.append(m)
            else:
                words.append(m)

        letters.sort()
        words.sort()

        return [self.march_base] + letters + words

    @property
    def march(self):
        base, *rest = self.march_parts
        for r in rest:
            if len(r) == 1:
                base += r
            else:
                base += '_' + r

        return base

    def elaborate(self, platform):
        m = am.Module()

        m.submodules.alu = self.alu

        # set up reasonable combinatoric defaults to prevent
        # too much value changing
        m.d.comb += [
            self.bus.adr.eq(self.pc[2:]),
            self.bus.sel.eq(0b1111),

            self.pc_inc.eq(4),

            self.rd.eq(self.alu.out),

            self.alu.in1.eq(self.rs1),
            self.alu.in2.eq(self.rs2),
            self.alu.op.eq(AluOp.ADD),
            self.alu.shift_amount.eq(self.instr.rs2),
        ]

        # re-usable pc adder
        m.d.comb += self.pc_plus_inc.eq(self.pc + self.pc_inc)

        # write to rd if enabled
        with m.If(self.rd_write_en):
            # only write to non-zero registers
            with m.If(self.instr.rd != Reg.ZERO):
                m.d.sync += self.regs[self.instr.rd].eq(self.rd)

        for v in self.extensions.values():
            v.elaborate_pre(platform, self, m)

        # core state machine
        with m.Switch(self.state):
            with m.Case(State.FETCH_INSTR):
                m.d.comb += [
                    self.bus.adr.eq(self.pc[2:]),
                    self.bus.cyc.eq(1),
                    self.bus.stb.eq(1),
                    self.bus.sel.eq(0b1111),
                ]

                with m.If(self.bus.ack):
                    instr = Instruction(self.bus.dat_r)
                    m.d.sync += [
                        self.instr.eq(instr),
                        self.rs1.eq(self.regs[instr.rs1]),
                        self.rs2.eq(self.regs[instr.rs2]),
                        self.state.eq(State.EXECUTE),
                    ]

            with m.Case(State.EXECUTE):
                # default to advancing to next instruction,
                # unless something else overwrites this
                m.d.sync += [
                    self.pc.eq(self.pc_plus_inc),
                    self.state.eq(State.FETCH_INSTR),
                ]

                self.execute(platform, m)
                for v in self.extensions.values():
                    v.execute(platform, self, m)

                with m.If(~self.is_valid_instruction):
                    # FIXME this should be a trap, but for now it's an assertion
                    info = am.Format('!! bad instruction: pc = 0x{:08x}, 0x{:08x}', self.pc, self.instr.as_value())
                    if self.assert_unknown_instructions:
                        m.d.sync += am.Assert(False, info)
                    else:
                        m.d.sync += am.Print(info)

        for v in self.extensions.values():
            v.elaborate_post(platform, self, m)

        return m

    def execute(self, platform, m):
        with m.Switch(self.instr.op):
            with m.Case(Op.LUI):
                self.valid_instruction(platform, m)
                m.d.comb += [
                    self.rd_write_en.eq(1),
                    self.rd.eq(self.instr.imm_u),
                ]

            with m.Case(Op.AUIPC):
                self.valid_instruction(platform, m)
                m.d.comb += [
                    self.rd_write_en.eq(1),

                    self.alu.in1.eq(self.pc),
                    self.alu.in2.eq(self.instr.imm_u),
                    self.alu.op.eq(AluOp.ADD),
                    self.rd.eq(self.alu.out),
                ]

            with m.Case(Op.JAL):
                self.valid_instruction(platform, m)
                m.d.comb += [
                    self.rd_write_en.eq(1),
                    self.rd.eq(self.pc + 4),

                    self.pc_inc.eq(self.instr.imm_j)
                ]

            with m.Case(Op.JALR):
                with m.If(self.instr.funct3.as_value() == 000):
                    self.valid_instruction(platform, m)

                    m.d.comb += [
                        self.rd_write_en.eq(1),
                        self.rd.eq(self.pc_plus_inc),

                        self.alu.in1.eq(self.rs1),
                        self.alu.in2.eq(self.instr.imm_i),
                        self.alu.op.eq(AluOp.ADD),
                    ]

                    # careful: LSB needs to be set to 0
                    m.d.sync += self.pc.eq(am.Cat(0, self.alu.out[1:]))

            with m.Case(Op.BRANCH):
                # common to all branches is comparing rs1 and rs2
                m.d.comb += [
                    self.alu.in1.eq(self.rs1),
                    self.alu.in2.eq(self.rs2),
                ]

                with m.Switch(self.instr.funct3.branch):
                    with m.Case(Funct3Branch.EQ):
                        self.valid_instruction(platform, m)
                        m.d.comb += self.alu.op.eq(AluOp.EQ),
                        with m.If(self.alu.out[0]):
                            m.d.comb += self.pc_inc.eq(self.instr.imm_b)

                    with m.Case(Funct3Branch.NE):
                        self.valid_instruction(platform, m)
                        m.d.comb += self.alu.op.eq(AluOp.EQ),
                        with m.If(~self.alu.out[0]):
                            m.d.comb += self.pc_inc.eq(self.instr.imm_b)

                    with m.Case(Funct3Branch.LT):
                        self.valid_instruction(platform, m)
                        m.d.comb += self.alu.op.eq(AluOp.LT),
                        with m.If(self.alu.out[0]):
                            m.d.comb += self.pc_inc.eq(self.instr.imm_b)

                    with m.Case(Funct3Branch.GE):
                        self.valid_instruction(platform, m)
                        m.d.comb += self.alu.op.eq(AluOp.LT),
                        with m.If(~self.alu.out[0]):
                            m.d.comb += self.pc_inc.eq(self.instr.imm_b)

                    with m.Case(Funct3Branch.LTU):
                        self.valid_instruction(platform, m)
                        m.d.comb += self.alu.op.eq(AluOp.LTU),
                        with m.If(self.alu.out[0]):
                            m.d.comb += self.pc_inc.eq(self.instr.imm_b)

                    with m.Case(Funct3Branch.GEU):
                        self.valid_instruction(platform, m)
                        m.d.comb += self.alu.op.eq(AluOp.LTU),
                        with m.If(~self.alu.out[0]):
                            m.d.comb += self.pc_inc.eq(self.instr.imm_b)

            with m.Case(Op.LOAD):
                # all of these load from rs1 + imm_i
                dest = self.rs1 + self.instr.imm_i
                m.d.comb += [
                    self.bus.adr.eq(dest[2:]),
                    self.bus.cyc.eq(1),
                    self.bus.stb.eq(1),
                ]

                with m.If(self.bus.ack):
                    # do the load and continue
                    data = self.bus.dat_r

                    # shift data
                    shift = am.Mux(
                        self.instr.funct3.mem == Funct3Mem.WORD,
                        0,
                        dest[:2],
                    )
                    shift &= am.Mux(
                        self.instr.funct3.mem.matches(Funct3Mem.HALF, Funct3Mem.HALF_U),
                        0b10,
                        0b11,
                    )
                    data = data >> (shift << 3)

                    # mask data by sel
                    mask = am.Cat(
                        *(sel.replicate(8) for sel in self.bus.sel),
                    ) >> (shift << 3)
                    data &= mask

                    # sign extend data
                    sign = am.Mux(
                        self.instr.funct3.mem.matches(Funct3Mem.BYTE),
                        data[7],
                        am.Mux(
                            self.instr.funct3.mem.matches(Funct3Mem.HALF),
                            data[15],
                            0,
                        ),
                    )
                    data |= (~mask) & sign.replicate(self.xlen)

                    m.d.comb += [
                        # set rd to our loaded data
                        self.rd.eq(data),
                        self.rd_write_en.eq(1),
                    ]

                with m.Else():
                    # stall until ack
                    m.d.comb += self.pc_inc.eq(0)
                    m.d.sync += self.state.eq(State.EXECUTE)

                with m.Switch(self.instr.funct3.mem):
                    with m.Case(Funct3Mem.BYTE):
                        self.valid_instruction(platform, m)
                        sel = 1 << dest[:2]
                        m.d.comb += self.bus.sel.eq(sel)

                    with m.Case(Funct3Mem.HALF):
                        self.valid_instruction(platform, m)
                        sel = 0b11 << (dest[:2] & 0b10)
                        m.d.comb += self.bus.sel.eq(sel)

                    with m.Case(Funct3Mem.WORD):
                        self.valid_instruction(platform, m)
                        sel = 0b1111
                        m.d.comb += self.bus.sel.eq(sel)

                    with m.Case(Funct3Mem.BYTE_U):
                        self.valid_instruction(platform, m)
                        sel = 1 << dest[:2]
                        m.d.comb += self.bus.sel.eq(sel)

                    with m.Case(Funct3Mem.HALF_U):
                        self.valid_instruction(platform, m)
                        sel = 0b11 << (dest[:2] & 0b10)
                        m.d.comb += self.bus.sel.eq(sel)

            with m.Case(Op.STORE):
                # all of these write rs2 to rs1 + imm_s
                src = self.rs2
                dest = self.rs1 + self.instr.imm_s
                m.d.comb += [
                    self.bus.adr.eq(dest[2:]),
                    self.bus.cyc.eq(1),
                    self.bus.stb.eq(1),
                    self.bus.we.eq(1),
                ]

                # wait here until ack
                with m.If(~self.bus.ack):
                    m.d.comb += self.pc_inc.eq(0)
                    m.d.sync += self.state.eq(State.EXECUTE)

                with m.Switch(self.instr.funct3.mem):
                    with m.Case(Funct3Mem.BYTE):
                        self.valid_instruction(platform, m)
                        byte = am.Cat(*(src[:8] for _ in range(4)))
                        m.d.comb += [
                            self.bus.dat_w.eq(byte),
                            self.bus.sel.eq(1 << dest[:2]),
                        ]

                    with m.Case(Funct3Mem.HALF):
                        self.valid_instruction(platform, m)
                        half = am.Cat(*(src[:16] for _ in range(2)))
                        m.d.comb += [
                            self.bus.dat_w.eq(half),
                            self.bus.sel.eq(0b11 << (dest[:2] & 0b10)),
                        ]

                    with m.Case(Funct3Mem.WORD):
                        self.valid_instruction(platform, m)
                        m.d.comb += [
                            self.bus.dat_w.eq(src),
                            self.bus.sel.eq(0b1111),
                        ]

            with m.Case(Op.OP_IMM):
                # by default, excepting shifts, these operate on
                # rs1 and imm_i
                # all of them store into rd
                m.d.comb += [
                    self.alu.in1.eq(self.rs1),
                    self.alu.in2.eq(self.instr.imm_i),
                    self.rd.eq(self.alu.out),
                    self.rd_write_en.eq(1),
                ]

                with m.Switch(self.instr.funct3.alu):
                    with m.Case(Funct3Alu.ADD_SUB):
                        self.valid_instruction(platform, m)
                        m.d.comb += self.alu.op.eq(AluOp.ADD)
                    with m.Case(Funct3Alu.LT):
                        self.valid_instruction(platform, m)
                        m.d.comb += self.alu.op.eq(AluOp.LT)
                    with m.Case(Funct3Alu.LTU):
                        self.valid_instruction(platform, m)
                        m.d.comb += self.alu.op.eq(AluOp.LTU)
                    with m.Case(Funct3Alu.XOR):
                        self.valid_instruction(platform, m)
                        m.d.comb += self.alu.op.eq(AluOp.XOR)
                    with m.Case(Funct3Alu.OR):
                        self.valid_instruction(platform, m)
                        m.d.comb += self.alu.op.eq(AluOp.OR)
                    with m.Case(Funct3Alu.AND):
                        self.valid_instruction(platform, m)
                        m.d.comb += self.alu.op.eq(AluOp.AND)

                    # shifts are a bit special

                    with m.Case(Funct3Alu.SHIFT_L):
                        with m.If(self.instr.funct7.alu == Funct7Alu.NORMAL):
                            self.valid_instruction(platform, m)
                            m.d.comb += [
                                self.alu.op.eq(AluOp.SHIFT_LL),
                                self.alu.shift_amount.eq(self.instr.rs2),
                            ]

                    with m.Case(Funct3Alu.SHIFT_R):
                        with m.If(self.instr.funct7.alu == Funct7Alu.NORMAL):
                            self.valid_instruction(platform, m)
                            m.d.comb += [
                                self.alu.op.eq(AluOp.SHIFT_RL),
                                self.alu.shift_amount.eq(self.instr.rs2),
                            ]
                        with m.Elif(self.instr.funct7.alu == Funct7Alu.ALT):
                            self.valid_instruction(platform, m)
                            m.d.comb += [
                                self.alu.op.eq(AluOp.SHIFT_RA),
                                self.alu.shift_amount.eq(self.instr.rs2),
                            ]

            with m.Case(Op.OP):
                # by default these operate on rs1 and rs2 and store into rd
                m.d.comb += [
                    self.alu.in1.eq(self.rs1),
                    self.alu.in2.eq(self.rs2),
                    self.rd.eq(self.alu.out),
                    self.rd_write_en.eq(1),
                ]

                with m.Switch(self.instr.funct3.alu):
                    with m.Case(Funct3Alu.ADD_SUB):
                        with m.If(self.instr.funct7.alu == Funct7Alu.NORMAL):
                            self.valid_instruction(platform, m)
                            m.d.comb += self.alu.op.eq(AluOp.ADD)
                        with m.Elif(self.instr.funct7.alu == Funct7Alu.ALT):
                            self.valid_instruction(platform, m)
                            m.d.comb += self.alu.op.eq(AluOp.SUB)

                    with m.Case(Funct3Alu.SHIFT_L):
                        with m.If(self.instr.funct7.alu == Funct7Alu.NORMAL):
                            self.valid_instruction(platform, m)
                            m.d.comb += [
                                self.alu.op.eq(AluOp.SHIFT_LL),
                                self.alu.shift_amount.eq(self.rs2),
                            ]

                    with m.Case(Funct3Alu.LT):
                        with m.If(self.instr.funct7.alu == Funct7Alu.NORMAL):
                            self.valid_instruction(platform, m)
                            m.d.comb += self.alu.op.eq(AluOp.LT)

                    with m.Case(Funct3Alu.LTU):
                        with m.If(self.instr.funct7.alu == Funct7Alu.NORMAL):
                            self.valid_instruction(platform, m)
                            m.d.comb += self.alu.op.eq(AluOp.LTU)

                    with m.Case(Funct3Alu.XOR):
                        with m.If(self.instr.funct7.alu == Funct7Alu.NORMAL):
                            self.valid_instruction(platform, m)
                            m.d.comb += self.alu.op.eq(AluOp.XOR)

                    with m.Case(Funct3Alu.SHIFT_R):
                        with m.If(self.instr.funct7.alu == Funct7Alu.NORMAL):
                            self.valid_instruction(platform, m)
                            m.d.comb += [
                                self.alu.op.eq(AluOp.SHIFT_RL),
                                self.alu.shift_amount.eq(self.rs2),
                            ]
                        with m.Elif(self.instr.funct7.alu == Funct7Alu.ALT):
                            self.valid_instruction(platform, m)
                            m.d.comb += [
                                self.alu.op.eq(AluOp.SHIFT_RA),
                                self.alu.shift_amount.eq(self.rs2),
                            ]

                    with m.Case(Funct3Alu.OR):
                        with m.If(self.instr.funct7.alu == Funct7Alu.NORMAL):
                            self.valid_instruction(platform, m)
                            m.d.comb += self.alu.op.eq(AluOp.OR)

                    with m.Case(Funct3Alu.AND):
                        with m.If(self.instr.funct7.alu == Funct7Alu.NORMAL):
                            self.valid_instruction(platform, m)
                            m.d.comb += self.alu.op.eq(AluOp.AND)

            # FIXME FENCE, FENCE.TSO, PAUSE

            with m.Case(Op.SYSTEM):
                with m.Switch(self.instr):
                    with m.Case(0b000000000000_00000_000_00000_1110011):
                        # FIXME ecall
                        pass
                    with m.Case(0b000000000001_00000_000_00000_1110011):
                        # stall on ebreak
                        self.valid_instruction(platform, m)
                        m.d.comb += self.pc_inc.eq(0)

    def valid_instruction(self, platform, m):
        m.d.comb += self.is_valid_instruction.eq(1)

    @property
    def debug_traces(self):
        t = {}

        t['memory'] = [
            self.bus.adr << 2,
            self.bus.cyc,
            self.bus.stb,
            self.bus.we,
            self.bus.sel,
            self.bus.dat_r,
            self.bus.dat_w,
            self.bus.ack,
        ]

        t['state'] = [
            self.pc,
            self.state,
            {'instr': self.instr},
            self.is_valid_instruction,
        ]

        t['registers'] = [r for r in self.regs]

        t['alu'] = [
            self.alu.in1,
            self.alu.in2,
            self.alu.op,
            self.alu.shift_amount,
            self.alu.out,
        ]

        exts = {}
        t['extensions'] = exts
        for ext in self.extensions.values():
            ext_traces = ext.debug_traces
            if ext_traces:
                exts[ext.__class__.__name__] = ext_traces

        return t

class AluOp(am.lib.enum.Enum):
    ADD = 0
    SUB = 1
    SHIFT_LL = 2
    SHIFT_RL = 3
    SHIFT_RA = 4
    LT = 5
    LTU = 6
    EQ = 7
    XOR = 8
    OR = 9
    AND = 10

class Alu(am.lib.wiring.Component):
    def __init__(self, xlen):
        super().__init__({
            'in1': am.lib.wiring.In(xlen),
            'in2': am.lib.wiring.In(xlen),
            'op': am.lib.wiring.In(AluOp),
            # not in2 simply so that it can be narrower
            'shift_amount': am.lib.wiring.In(range(xlen)),
            'out': am.lib.wiring.Out(xlen),
        })

        self.xlen = xlen
        self.minus = am.Signal(xlen + 1)
        self.plus = am.Signal(xlen)
        self.eq = am.Signal(1)
        self.ltu = am.Signal(1)
        self.lt = am.Signal(1)

    def elaborate(self, platform):
        m = am.Module()

        # set up our intermediates
        m.d.comb += [
            self.minus.eq(self.in1.as_unsigned() - self.in2.as_unsigned()),
            self.plus.eq(self.in1 + self.in2),
            self.eq.eq(self.minus[:-1] == 0),
            self.ltu.eq(self.minus[-1]),
            self.lt.eq(am.Mux(self.in1[-1] ^ self.in2[-1], self.in1[-1], self.minus[-1])),
        ]

        # shared shifter for ll / rl / ra
        shift_in = am.Mux(self.op == AluOp.SHIFT_LL, self.in1[::-1], self.in1)
        rightshift = am.Cat(shift_in, (self.op == AluOp.SHIFT_RA) & shift_in[-1]).as_signed() >> self.shift_amount
        leftshift = rightshift[:-1][::-1]

        # ok, now push the right one to out
        with m.Switch(self.op):
            with m.Case(AluOp.ADD):
                m.d.comb += self.out.eq(self.plus)
            with m.Case(AluOp.SUB):
                m.d.comb += self.out.eq(self.minus)
            with m.Case(AluOp.SHIFT_LL):
                m.d.comb += self.out.eq(leftshift)
            with m.Case(AluOp.SHIFT_RL):
                m.d.comb += self.out.eq(rightshift)
            with m.Case(AluOp.SHIFT_RA):
                m.d.comb += self.out.eq(rightshift)
            with m.Case(AluOp.LT):
                m.d.comb += self.out.eq(self.lt)
            with m.Case(AluOp.LTU):
                m.d.comb += self.out.eq(self.ltu)
            with m.Case(AluOp.EQ):
                m.d.comb += self.out.eq(self.eq)
            with m.Case(AluOp.XOR):
                m.d.comb += self.out.eq(self.in1 ^ self.in2)
            with m.Case(AluOp.OR):
                m.d.comb += self.out.eq(self.in1 | self.in2)
            with m.Case(AluOp.AND):
                m.d.comb += self.out.eq(self.in1 & self.in2)

        return m

class Zicsr(Extension):
    march = 'zicsr'

    def prepare(self, cpu):
        self.csr_addr = am.Signal(12)
        self.csr_read_en = am.Signal(1)
        self.csr_read_data = am.Signal(cpu.xlen)
        self.csr_write_en = am.Signal(1)
        self.csr_write_data = am.Signal(cpu.xlen)

        self.uimm = am.Signal(cpu.xlen)
        self.modify = am.Signal(1)
        self.setbits = am.Signal(1)
        self.new = am.Signal(cpu.xlen)

    def elaborate_pre(self, platform, cpu, m):
        # some defaults
        m.d.comb += [
            self.csr_addr.eq(cpu.instr.imm_i.as_unsigned()),
        ]

        # rs1 interpreted as an immediate value, 0-extended to xlen
        m.d.comb += self.uimm.eq(cpu.instr.rs1.as_value().as_unsigned())

        # combined mini-alu for register set/reset
        old = am.Mux(self.modify, self.csr_read_data, 0)
        m.d.comb += self.new.eq(cpu.rs1)
        m.d.comb += self.csr_write_data.eq(
            am.Mux(
                self.setbits,
                self.new | old,
                ~self.new & old,
            )
        )

    def valid_csr(self, platform, cpu, m, read=True, write=True):
        if read and write:
            with m.If(self.csr_read_en | self.csr_write_en):
                cpu.valid_instruction(platform, m)
        elif read:
            with m.If(self.csr_read_en & ~self.csr_write_en):
                cpu.valid_instruction(platform, m)
        elif write:
            with m.If(self.csr_write_en & ~self.csr_read_en):
                cpu.valid_instruction(platform, m)
        else:
            raise ValueError('csr must be readable or writeable or both')
        

    def execute(self, platform, cpu, m):
        # we require the csr implementations to call self.valid_csr
        with m.If(cpu.instr.op == Op.SYSTEM):
            with m.Switch(cpu.instr.funct3.csr):
                with m.Case(Funct3Csr.RW):
                    m.d.comb += [
                        # no read side effects if rd is zero
                        self.csr_read_en.eq(cpu.instr.rd != Reg.ZERO),
                        cpu.rd_write_en.eq(1),
                        cpu.rd.eq(self.csr_read_data),

                        self.csr_write_en.eq(1),
                        self.modify.eq(0),
                        self.setbits.eq(1),
                        self.new.eq(cpu.rs1),
                    ]

                with m.Case(Funct3Csr.RS):
                    m.d.comb += [
                        self.csr_read_en.eq(1),
                        cpu.rd_write_en.eq(1),
                        cpu.rd.eq(self.csr_read_data),

                        # no write side effects if rs1 is zero
                        self.csr_write_en.eq(cpu.instr.rs1 != Reg.ZERO),
                        self.modify.eq(1),
                        self.setbits.eq(1),
                        self.new.eq(cpu.rs1),
                    ]

                with m.Case(Funct3Csr.RC):
                    m.d.comb += [
                        self.csr_read_en.eq(1),
                        cpu.rd_write_en.eq(1),
                        cpu.rd.eq(self.csr_read_data),

                        # no write side effects if rs1 is zero
                        self.csr_write_en.eq(cpu.instr.rs1 != Reg.ZERO),
                        self.modify.eq(1),
                        self.setbits.eq(0),
                        self.new.eq(cpu.rs1),
                    ]

                with m.Case(Funct3Csr.RWI):
                    m.d.comb += [
                        # no read side effects if rd is zero
                        self.csr_read_en.eq(cpu.instr.rd != Reg.ZERO),
                        cpu.rd_write_en.eq(1),
                        cpu.rd.eq(self.csr_read_data),

                        self.csr_write_en.eq(1),
                        self.modify.eq(0),
                        self.setbits.eq(1),
                        self.new.eq(self.uimm),
                    ]

                with m.Case(Funct3Csr.RSI):
                    m.d.comb += [
                        self.csr_read_en.eq(1),
                        cpu.rd_write_en.eq(1),
                        cpu.rd.eq(self.csr_read_data),

                        # no write side effects if uimm is zero
                        self.csr_write_en.eq(self.uimm != 0),
                        self.modify.eq(1),
                        self.setbits.eq(1),
                        self.new.eq(self.uimm),
                    ]

                with m.Case(Funct3Csr.RCI):
                    m.d.comb += [
                        self.csr_read_en.eq(1),
                        cpu.rd_write_en.eq(1),
                        cpu.rd.eq(self.csr_read_data),

                        # no write side effects if uimm is zero
                        self.csr_write_en.eq(self.uimm != 0),
                        self.modify.eq(1),
                        self.setbits.eq(0),
                        self.new.eq(self.uimm),
                    ]

    @property
    def debug_traces(self):
        return [
            self.csr_addr,
            self.csr_read_en,
            self.csr_read_data,
            self.csr_write_en,
            self.csr_write_data,
        ]

class Zicntr(Extension):
    # some gcc don't support this, and it also doesn't really matter
    #march = 'zicntr'

    def prepare(self, cpu):
        try:
            self.csr = cpu.extensions['Zicsr']
        except KeyError:
            raise RuntimeError('Zicntr requires Zicsr')

        self.cycle = am.Signal(64)
        self.time = self.cycle # a valid implementation of time
        self.instret = am.Signal(64)

    def elaborate_pre(self, platform, cpu, m):
        # cycle is easy
        m.d.sync += self.cycle.eq(self.cycle + 1)

        # we'll cheat and increment instret on every good instruction fetch
        # technically, some instructions never retire. so, FIXME
        with m.If(cpu.state.matches(State.FETCH_INSTR) & cpu.bus.ack):
            m.d.sync += self.instret.eq(self.instret + 1)

        # these registers are read-only
        with m.Switch(self.csr.csr_addr):
            with m.Case(0xc00):
                # cycle
                self.csr.valid_csr(platform, cpu, m, write=False)
                m.d.comb += self.csr.csr_read_data.eq(self.cycle)
            with m.Case(0xc01):
                # time
                self.csr.valid_csr(platform, cpu, m, write=False)
                m.d.comb += self.csr.csr_read_data.eq(self.time)
            with m.Case(0xc02):
                # instret
                self.csr.valid_csr(platform, cpu, m, write=False)
                m.d.comb += self.csr.csr_read_data.eq(self.instret)

            # only do high side if needed
            if cpu.xlen < 64:
                with m.Case(0xc80):
                    # cycleh
                    self.csr.valid_csr(platform, cpu, m, write=False)
                    m.d.comb += self.csr.csr_read_data.eq(self.cycle[32:])
                with m.Case(0xc81):
                    # timeh
                    self.csr.valid_csr(platform, cpu, m, write=False)
                    m.d.comb += self.csr.csr_read_data.eq(self.time[32:])
                with m.Case(0xc82):
                    # instret
                    self.csr.valid_csr(platform, cpu, m, write=False)
                    m.d.comb += self.csr.csr_read_data.eq(self.instret[32:])

    @property
    def debug_traces(self):
        return [
            self.cycle,
            self.instret,
        ]
