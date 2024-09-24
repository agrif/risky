# https://github.com/BrunoLevy/learn-fpga/blob/master/FemtoRV/TUTORIALS/FROM_BLINKER_TO_RISCV/README.md

import amaranth as am
import amaranth.lib.data
import amaranth.lib.enum

import risky.memory

class OpView(am.lib.enum.EnumView):
    def is_alu(self):
        return (self == Op.ALU_REG) | (self == Op.ALU_IMM)

    def is_jump(self):
        return (self == Op.JALR) | (self == Op.JAL)

    def is_load_immediate(self):
        return (self == Op.LUI) | (self == Op.AUIPC)

class Op(am.lib.enum.Enum, shape=7, view_class=OpView):
    ALU_REG = 0b0110011
    ALU_IMM = 0b0010011
    BRANCH  = 0b1100011
    JALR    = 0b1100111
    JAL     = 0b1101111
    AUIPC   = 0b0010111
    LUI     = 0b0110111
    LOAD    = 0b0000011
    STORE   = 0b0100011
    SYSTEM  = 0b1110011

Reg = am.unsigned(5)
Funct7 = am.unsigned(7)

class AluOp(am.lib.enum.Enum, shape=3):
    ADD_SUB = 0b000
    SHIFT_L = 0b001
    CMP_S   = 0b010
    CMP_U   = 0b011
    XOR     = 0b100
    SHIFT_R = 0b101
    OR      = 0b110
    AND     = 0b111

class BranchOp(am.lib.enum.Enum, shape=3):
    EQ  = 0b000
    NE  = 0b001
    LT  = 0b100
    GE  = 0b101
    LTU = 0b110
    GEU = 0b111

class Funct3(am.lib.data.Union):
    alu: AluOp
    branch: BranchOp

# RISC-V Vol. 1, page 130
class Instruction(am.lib.data.Struct):
    op: Op = Op.ALU_REG
    rd: Reg
    funct3: Funct3
    rs1: Reg
    rs2: Reg
    funct7: Funct7

    @property
    def imm_i(self):
        imm_11_0 = am.Cat(self.rs2, self.funct7)
        return imm_11_0.as_signed()

    @property
    def imm_s(self):
        imm_11_5 = self.funct7
        imm_4_0 = self.rd
        return am.Cat(imm_4_0, imm_11_5).as_signed()

    @property
    def imm_b(self):
        imm_12__10_5 = self.funct7
        imm_4_1__11 = self.rd

        imm_12 = imm_12__10_5[-1]
        imm_10_5 = imm_12__10_5[:-1]
        imm_4_1 = imm_4_1__11[1:]
        imm_11 = imm_4_1__11[0]
        return am.Cat(0, imm_4_1, imm_10_5, imm_11, imm_12).as_signed()

    @property
    def imm_u(self):
        imm_31_12 = am.Cat(self.funct3, self.rs1, self.rs2, self.funct7)
        return (imm_31_12 << 12).as_signed()

    @property
    def imm_j(self):
        imm_20__10_1__11__19_12 = am.Cat(self.funct3, self.rs1, self.rs2, self.funct7)

        imm_19_12 = imm_20__10_1__11__19_12[:8]
        imm_11 = imm_20__10_1__11__19_12[8]
        imm_10_1 = imm_20__10_1__11__19_12[9:19]
        imm_20 = imm_20__10_1__11__19_12[19]
        return am.Cat(0, imm_10_1, imm_11, imm_19_12, imm_20).as_signed()

class State(am.lib.enum.Enum):
    FETCH_INSTR = 0
    WAIT_INSTR = 1
    FETCH_REGS = 2
    EXECUTE = 3
    LOAD = 4
    WAIT_DATA = 5
    STORE = 6

class Cpu(am.lib.wiring.Component):
    bus: am.lib.wiring.Out(risky.memory.MemoryBus())

    march = 'rv32i_zicsr'

    def __init__(self):
        super().__init__()
        self.state = am.Signal(State)
        self.pc = am.Signal(32)
        self.instr = am.Signal(Instruction)
        self.regs = am.Array([am.Signal(32, name='x{}'.format(i)) for i in range(32)])

    def elaborate(self, platform):
        m = am.Module()

        # cycle counter
        cycle = am.Signal(64)
        m.d.sync += cycle.eq(cycle + 1)

        # register values holding
        rs1 = am.Signal(32)
        rs2 = am.Signal(32)

        # ALU inputs and outputs
        alu_in1 = rs1
        # note: JALR uses the imm_i side of this!
        alu_in2 = am.Mux((self.instr.op == Op.ALU_REG) | (self.instr.op == Op.BRANCH), rs2, self.instr.imm_i)
        alu_out = am.Signal(32)
        shamt = am.Mux(self.instr.op == Op.ALU_REG, rs2[:5], self.instr.rs2)

        # alu shared logic
        alu_minus = am.Signal(33)
        m.d.comb += alu_minus.eq(alu_in1[:32].as_unsigned() - alu_in2[:32].as_unsigned())
        alu_plus = alu_in1 + alu_in2
        eq = (alu_minus[:32] == 0)
        ltu = alu_minus[32]
        lt = am.Mux(alu_in1[31] ^ alu_in2[31], alu_in1[31], alu_minus[32])

        # alu shared shifter
        shifter_in = am.Mux(self.instr.funct3.alu == AluOp.SHIFT_L, alu_in1[::-1], alu_in1)
        shifter = am.Cat(shifter_in, self.instr.funct7[5] & shifter_in[31]).as_signed() >> shamt
        leftshift = shifter[:32][::-1]

        # ALU logic
        with m.Switch(self.instr.funct3.alu):
            with m.Case(AluOp.ADD_SUB):
                with m.If(self.instr.funct7[5] & (self.instr.op == Op.ALU_REG)):
                    # SUB
                    m.d.comb += alu_out.eq(alu_minus)
                with m.Else():
                    # ADD
                    m.d.comb += alu_out.eq(alu_plus)
            with m.Case(AluOp.SHIFT_L):
                m.d.comb += alu_out.eq(leftshift)
            with m.Case(AluOp.CMP_S):
                m.d.comb += alu_out.eq(lt)
            with m.Case(AluOp.CMP_U):
                m.d.comb += alu_out.eq(ltu)
            with m.Case(AluOp.XOR):
                m.d.comb += alu_out.eq(alu_in1 ^ alu_in2)
            with m.Case(AluOp.SHIFT_R):
                m.d.comb += alu_out.eq(shifter)
            with m.Case(AluOp.OR):
                m.d.comb += alu_out.eq(alu_in1 | alu_in2)
            with m.Case(AluOp.AND):
                m.d.comb += alu_out.eq(alu_in1 & alu_in2)

        # whether to take a branch (if it's a branch)
        take_branch = am.Signal(1)
        with m.Switch(self.instr.funct3.branch):
            with m.Case(BranchOp.EQ):
                m.d.comb += take_branch.eq(eq)
            with m.Case(BranchOp.NE):
                m.d.comb += take_branch.eq(~eq)
            with m.Case(BranchOp.LT):
                m.d.comb += take_branch.eq(lt)
            with m.Case(BranchOp.GE):
                m.d.comb += take_branch.eq(~lt)
            with m.Case(BranchOp.LTU):
                m.d.comb += take_branch.eq(ltu)
            with m.Case(BranchOp.GEU):
                m.d.comb += take_branch.eq(~ltu)
            with m.Default():
                m.d.comb += take_branch.eq(0)

        # where to go next
        next_pc = am.Signal(32)
        pc_plus_4 = self.pc + 4
        pc_plus_imm = self.pc + am.Mux(
            self.instr.op == Op.JAL,
            self.instr.imm_j,
            am.Mux(
                self.instr.op == Op.AUIPC,
                self.instr.imm_u,
                self.instr.imm_b,
            ),
        )

        with m.Switch(self.instr.op):
            with m.Case(Op.JAL):
                m.d.comb += next_pc.eq(pc_plus_imm)
            with m.Case(Op.JALR):
                m.d.comb += next_pc.eq(am.Cat(0, alu_plus[1:]))
            with m.Case(Op.BRANCH):
                with m.If(take_branch):
                    m.d.comb += next_pc.eq(pc_plus_imm)
                with m.Else():
                    m.d.comb += next_pc.eq(pc_plus_4)
            with m.Default():
                m.d.comb += next_pc.eq(pc_plus_4)

        # state machine
        instret = am.Signal(64)
        with m.Switch(self.state):
            with m.Case(State.FETCH_INSTR):
                m.d.sync += self.state.eq(State.WAIT_INSTR)
            with m.Case(State.WAIT_INSTR):
                with m.If(~self.bus.wait):
                    m.d.sync += self.instr.eq(self.bus.read_data)
                    m.d.sync += self.state.eq(State.FETCH_REGS)
                    m.d.sync += instret.eq(instret + 1)
            with m.Case(State.FETCH_REGS):
                m.d.sync += rs1.eq(self.regs[self.instr.rs1])
                m.d.sync += rs2.eq(self.regs[self.instr.rs2])
                m.d.sync += self.state.eq(State.EXECUTE)
            with m.Case(State.EXECUTE):
                with m.If((self.instr.op != Op.SYSTEM) | (self.instr.funct3.as_value() != 0b000)):
                    # ebreak
                    m.d.sync += self.pc.eq(next_pc)

                with m.If(self.instr.op == Op.LOAD):
                    m.d.sync += self.state.eq(State.LOAD)
                with m.Elif(self.instr.op == Op.STORE):
                    m.d.sync += self.state.eq(State.STORE)
                with m.Else():
                    m.d.sync += self.state.eq(State.FETCH_INSTR)
            with m.Case(State.LOAD):
                m.d.sync += self.state.eq(State.WAIT_DATA)
            with m.Case(State.WAIT_DATA):
                with m.If(~self.bus.wait):
                    m.d.sync += self.state.eq(State.FETCH_INSTR)
            with m.Case(State.STORE):
                with m.If(~self.bus.wait):
                    m.d.sync += self.state.eq(State.FETCH_INSTR)

        # csr reads
        csr_data = am.Signal(32)
        with m.Switch(self.instr.imm_i.as_unsigned()):
            # cycle[h]
            with m.Case(0xC00):
                m.d.comb += csr_data.eq(cycle[:32])
            with m.Case(0xC80):
                m.d.comb += csr_data.eq(cycle[32:])
            # time[h]
            with m.Case(0xC01):
                m.d.comb += csr_data.eq(cycle[:32])
            with m.Case(0xC81):
                m.d.comb += csr_data.eq(cycle[32:])
            # instret[h]
            with m.Case(0xC02):
                m.d.comb += csr_data.eq(instret[:32])
            with m.Case(0xC82):
                m.d.comb += csr_data.eq(instret[32:])

        # loading
        loadstore_addr = rs1 + am.Mux(self.instr.op == Op.STORE, self.instr.imm_s, self.instr.imm_i)
        load_w = self.bus.read_data
        load_h = am.Mux(loadstore_addr[1], load_w[16:], load_w[:16])
        load_b = am.Mux(loadstore_addr[0], load_h[8:], load_h[:8])

        # FIXME funct3 views
        mem_byte_access = (self.instr.funct3.as_value()[:2] == 0b00)
        mem_halfword_access = (self.instr.funct3.as_value()[:2] == 0b01)
        load_sign = (~self.instr.funct3.as_value()[2]) & am.Mux(
            mem_byte_access,
            load_b[-1],
            load_h[-1],
        )
        load_data = am.Mux(
            mem_byte_access,
            am.Cat(load_b, load_sign).as_signed(),
            am.Mux(
                mem_halfword_access,
                am.Cat(load_h, load_sign).as_signed(),
                load_w,
            ),
        )

        # storing
        m.d.comb += [
            self.bus.write_data[0:8].eq(rs2[0:8]),
            self.bus.write_data[8:16].eq(am.Mux(loadstore_addr[0], rs2[0:8], rs2[8:16])),
            self.bus.write_data[16:24].eq(am.Mux(loadstore_addr[1], rs2[0:8], rs2[16:24])),
            self.bus.write_data[24:32].eq(
                am.Mux(
                    loadstore_addr[0],
                    rs2[0:8],
                    am.Mux(
                        loadstore_addr[1],
                        rs2[8:16],
                        rs2[24:32],
                    ),
                )
            )
        ]

        store_wmask = am.Signal(4)
        with m.If(mem_byte_access):
            m.d.comb += store_wmask.eq(1 << loadstore_addr[:2])
        with m.Elif(mem_halfword_access):
            m.d.comb += store_wmask.eq(0b11 << (loadstore_addr[:2] & 0b10))
        with m.Else():
            m.d.comb += store_wmask.eq(0b1111)

        # drive memory read
        m.d.comb += self.bus.addr.eq(am.Mux(
            (self.state == State.WAIT_INSTR) | (self.state == State.FETCH_INSTR),
            self.pc,
            loadstore_addr,
        ).as_unsigned() >> 2)
        m.d.comb += self.bus.read_en.eq((self.state == State.FETCH_INSTR) | (self.state == State.LOAD))
        m.d.comb += self.bus.write_en.eq(am.Mux(self.state == State.STORE, store_wmask, 0))

        # write to destination register
        write_back_data = am.Signal(32)
        write_back_en = am.Signal(1)
        with m.If(write_back_en):
            # only write to non-zero registers
            with m.If(self.instr.rd != 0):
                m.d.sync += self.regs[self.instr.rd].eq(write_back_data)

        # write
        with m.Switch(self.instr.op):
            with m.Case(Op.JAL, Op.JALR):
                m.d.comb += write_back_data.eq(pc_plus_4)
            with m.Case(Op.LUI):
                m.d.comb += write_back_data.eq(self.instr.imm_u)
            with m.Case(Op.AUIPC):
                m.d.comb += write_back_data.eq(pc_plus_imm)
            with m.Case(Op.LOAD):
                m.d.comb += write_back_data.eq(load_data)
            with m.Case(Op.SYSTEM):
                with m.If(self.instr.funct3.as_value() == 0b010):
                    # CSRRS, read only for now
                    m.d.comb += write_back_data.eq(csr_data)
            with m.Default():
                m.d.comb += write_back_data.eq(alu_out)

        m.d.comb += write_back_en.eq((self.state == State.WAIT_DATA) | am.Cat(
            self.state == State.EXECUTE,
            self.instr.op != Op.BRANCH,
            self.instr.op != Op.STORE,
            self.instr.op != Op.LOAD,
        ).all())

        return m
