import amaranth as am
import amaranth.lib.enum
import amaranth.lib.data

# page numbers from RISC-V Vol. 1, 2024-04-11

# page 553
class Op(am.lib.enum.Enum, shape=7):
    LOAD      = 0b00_000_11
    #LOAD_FP   = 0b00_001_11
    #CUSTOM_0  = 0b00_010_11
    MISC_MEM  = 0b00_011_11
    OP_IMM    = 0b00_100_11
    AUIPC     = 0b00_101_11
    #OP_IMM_32 = 0b00_110_11

    STORE     = 0b01_000_11
    #STORE_FP  = 0b01_001_11
    #CUSTOM_1  = 0b01_010_11
    #AMO       = 0b01_011_11
    OP        = 0b01_100_11
    LUI       = 0b01_101_11
    #OP_32     = 0b01_110_11

    #MADD      = 0b10_000_11
    #MSUB      = 0b10_001_11
    #NMSUB     = 0b10_010_11
    #NMADD     = 0b10_011_11
    #OP_FP     = 0b10_100_11
    #OP_V      = 0b10_101_11
    #CUSTOM_2  = 0b10_110_11

    BRANCH    = 0b11_000_11
    JALR      = 0b11_001_11
    #RESERVED  = 0b11_010_11
    JAL       = 0b11_011_11
    SYSTEM    = 0b11_100_11
    #OP_VE     = 0b11_101_11
    #CUSTOM_3  = 0b11_110_11

class Reg(am.lib.enum.Enum, shape=5):
    ZERO = 0
    RA = 1
    SP = 2
    GP = 3
    TP = 4
    T0, T1, T2 = range(5, 8)
    S0, S1 = range(8, 10)
    A0, A1, A2, A3, A4, A5, A6, A7 = range(10, 18)
    S2, S3, S4, S5, S6, S7, S8, S9, S10, S11 = range(18, 28)
    T3, T4, T5, T6 = range(28, 32)

class Funct3Alu(am.lib.enum.Enum, shape=3):
    ADD_SUB = 0b000
    SHIFT_L = 0b001
    LT      = 0b010
    LTU     = 0b011
    XOR     = 0b100
    SHIFT_R = 0b101
    OR      = 0b110
    AND     = 0b111

class Funct3Branch(am.lib.enum.Enum, shape=3):
    EQ  = 0b000
    NE  = 0b001
    LT  = 0b100
    GE  = 0b101
    LTU = 0b110
    GEU = 0b111

class Funct3Mem(am.lib.enum.Enum, shape=3):
    BYTE = 0b000
    HALF = 0b001
    WORD = 0b010

    BYTE_U = 0b100
    HALF_U = 0b101

# Zicsr extension
class Funct3Csr(am.lib.enum.Enum, shape=3):
    RW = 0b001
    RS = 0b010
    RC = 0b011
    RWI = 0b101
    RSI = 0b110
    RCI = 0b111

class Funct3(am.lib.data.Union):
    raw: am.unsigned(3)
    alu: Funct3Alu
    branch: Funct3Branch
    mem: Funct3Mem

    # Zicsr extension
    csr: Funct3Csr

class Funct7Alu(am.lib.enum.Enum, shape=7):
    NORMAL = 0b0000000
    ALT    = 0b0100000

class Funct7(am.lib.data.Union):
    raw: am.unsigned(7)
    alu: Funct7Alu

# page 554
class Instruction(am.lib.data.Struct):
    # default to a nop
    op: Op = Op.OP_IMM
    rd: Reg = Reg.ZERO
    funct3: Funct3 = {'alu': Funct3Alu.ADD_SUB}
    rs1: Reg = Reg.ZERO
    rs2: Reg = 0
    funct7: Funct7 = {'raw': 0}

    @property
    def imm_i(self):
        imm_11_0 = am.Cat(self.rs2, self.funct7)
        return imm_11_0.as_signed()

    @property
    def imm_s(self):
        imm_11_5 = self.funct7.raw
        imm_4_0 = self.rd.as_value()
        return am.Cat(imm_4_0, imm_11_5).as_signed()

    @property
    def imm_b(self):
        imm_12__10_5 = self.funct7.raw
        imm_4_1__11 = self.rd.as_value()

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
