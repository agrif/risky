import collections
import functools
import operator

import amaranth as am
import amaranth.lib.enum

from risky.instruction import Instruction, Reg, Op, Funct3Alu, Funct7Alu, Funct3Branch, Funct3Mem, Funct3Csr
import risky.memory

class State(am.lib.enum.Enum):
    FETCH = 0
    EXECUTE = 1

class MemBus(am.lib.wiring.Signature):
    name = 'mem'

    def __init__(self, xlen):
        super().__init__(risky.memory.MemoryBus().flip().members)

    def __eq__(self, other):
        return self.members == other.members

class AluBus(am.lib.wiring.Signature):
    name = 'alu'

    def __init__(self, xlen):
        super().__init__({
            # alu -> instruction
            'out': am.lib.wiring.Out(xlen),

            # instruction -> alu
            'in1': am.lib.wiring.In(xlen),
            'in2': am.lib.wiring.In(xlen),
            'op': am.lib.wiring.In(Funct3Alu),
            'alt': am.lib.wiring.In(1),
        })

    def __eq__(self, other):
        return self.members == other.members

class CsrBus(am.lib.wiring.Signature):
    name = 'csr'

    def __init__(self, xlen):
        super().__init__({
            # cpu -> csr
            'adr': am.lib.wiring.Out(12),
            'r_stb': am.lib.wiring.Out(1),
            'w_data': am.lib.wiring.Out(xlen),
            'w_stb': am.lib.wiring.Out(1),

            # csr -> cpu
            'valid': am.lib.wiring.In(1),
            'r_data': am.lib.wiring.In(xlen),
        })

    def __eq__(self, other):
        return self.members == other.members

class InstrBus(am.lib.wiring.Signature):
    name = 'instr'

    def __init__(self, xlen):
        super().__init__(dict(
            # cpu -> instruction
            execute = am.lib.wiring.Out(1),
            pc = am.lib.wiring.Out(xlen),
            instr = am.lib.wiring.Out(Instruction),

            rs1 = am.lib.wiring.Out(xlen),
            rs2 = am.lib.wiring.Out(xlen),

            pc_next = am.lib.wiring.Out(xlen),
            pc_jump = am.lib.wiring.Out(xlen),
            stalled = am.lib.wiring.Out(1),

            # instruction -> cpu
            # (all *must* be 0 unless valid asserted, uniquely)
            valid = am.lib.wiring.In(1),

            rd_data = am.lib.wiring.In(xlen),
            rd_stb = am.lib.wiring.In(1),

            j_addr = am.lib.wiring.In(xlen),
            j_rel = am.lib.wiring.In(1),
            j_en = am.lib.wiring.In(1),

            wait = am.lib.wiring.In(1),
        ))

    def __eq__(self, other):
        return self.members == other.members

class BusConnectedComponent(am.lib.wiring.Component):
    busses = [InstrBus]

    def __init__(self, xlen, signature={}):
        self.xlen = xlen

        our_signature = {}
        for bus in self.busses:
            our_signature[bus.name + '_bus'] = am.lib.wiring.In(bus(xlen))
        our_signature.update(signature)

        super().__init__(our_signature)

    @property
    def ib(self):
        return self.instr_bus

class InstructionComponent(BusConnectedComponent):
    def elaborate(self, platform):
        m = am.Module()

        self.always(platform, m)
        with m.If(self.ib.valid & self.ib.execute):
            self.execute(platform, m)

        return m

    def always(self, platform, m):
        raise NotImplementedError

    def execute(self, platform, m):
        raise NotImplementedError

class OneHotMux(am.lib.wiring.Elaboratable):
    def __init__(self, signature):
        super().__init__()

        self.bus_signature = signature
        self.bus = am.lib.wiring.flipped(signature.create())
        self.controller_bus = None
        self.subbusses = []

    def _find_matching_busses(self, component, signature):
        candidates = []
        if component.signature == signature:
            candidates.append(component)
        else:
            for name, kind in component.signature.members.items():
                if kind.is_signature and kind.signature == signature:
                    candidates.append(getattr(component, name))

        return candidates

    def add(self, component):
        added = False

        candidates = self._find_matching_busses(component, self.bus.signature)
        if len(candidates) > 1:
            raise ValueError('component has ambiguous matching busses for {}'.format(self.bus.signature))

        if len(candidates) > 0:
            bus, = candidates
            self.add_bus(bus)
            added = True

        candidates = self._find_matching_busses(component, self.bus_signature)
        if len(candidates) > 1:
            raise ValueError('component has ambiguous matching controllers for {}'.format(self.bus_signature))

        if len(candidates) > 0:
            bus, = candidates
            self.add_controller_bus(bus)
            added = True

        if not added:
            raise ValueError('no matching busses found for {}'.format(self.bus.signature))

        return component

    def add_bus(self, bus):
        if bus.signature != self.bus.signature:
            raise ValueError('bus signatures do not match {}'.format(self.bus.signature))

        self.subbusses.append(bus)
        return bus

    def add_controller_bus(self, controller_bus):
        if controller_bus.signature != self.bus_signature:
            raise ValueError('controller bus signature does not match {}'.format(self.bus_signature))

        if self.controller_bus is not None:
            raise RuntimeError('one hot mux for {} cannot have more than one controller'.format(self.bus_signature))

        self.controller_bus = controller_bus
        return controller_bus

    def add_from(self, components):
        for component in components:
            a = self._find_matching_busses(component, self.bus.signature)
            b = self._find_matching_busses(component, self.bus_signature)
            if len(a) + len(b) > 0:
                self.add(component)
        return components

    @classmethod
    def forward(cls, output_bus, components):
        controller_bus = am.lib.wiring.flipped(output_bus)
        mux = cls(controller_bus.signature)
        mux.add_controller_bus(controller_bus)
        mux.add_from(components)
        return mux

    def elaborate(self, platform):
        m = am.Module()

        # helper to get a raw value signal from a name off a bus
        def raw_sig(bus, path):
            first, *rest = path
            obj = getattr(bus, first)
            for item in rest:
                if isinstance(item, int):
                    obj = obj[item]
                else:
                    obj = getattr(obj, item)

            if hasattr(obj, 'as_value'):
                return obj.as_value()
            return obj

        for path, kind, _ in self.bus.signature.flatten(self.bus):
            if kind.flow == am.lib.wiring.Flow.In:
                # inputs to us are simply propogated to the subbusses
                for bus in self.subbusses:
                    m.d.comb += raw_sig(bus, path).eq(raw_sig(self.bus, path))
            elif kind.flow == am.lib.wiring.Flow.Out:
                # outputs are one-hot multiplexed
                # components on the bus promise to only assert anything
                # if valid is also asserted, and valid is *unique* per instr

                m.d.comb += raw_sig(self.bus, path).eq(
                    functools.reduce(
                        operator.or_,
                        (raw_sig(bus, path) for bus in self.subbusses),
                    ),
                )

        if self.controller_bus is None:
            raise RuntimeError('one hot mux for {} needs a controller'.format(self.bus_signature))
        am.lib.wiring.connect(m, self.controller_bus, self.bus)

        return m

class Extension(BusConnectedComponent):
    name = None
    march = None

    def __init__(self, cpu, signature={}):
        super().__init__(cpu.xlen, signature=signature)

    @property
    def debug_traces(self):
        return []

    def forward_busses(self, platform, m, components):
        for bus in self.busses:
            m.submodules[bus.name + '_mux'] = OneHotMux.forward(getattr(self, bus.name + '_bus'), components)

class Cpu(am.lib.wiring.Component):
    xlen = 32

    def __init__(self, extensions=[]):
        super().__init__({
            'bus': am.lib.wiring.Out(risky.memory.MemoryBus()),
        })

        # core state
        self.state = am.Signal(State)
        self.pc = am.Signal(self.xlen)
        self.instr = am.Signal(Instruction)

        # register file
        regs = []
        for i, r in enumerate(Reg):
            assert r.value == i
            regs.append(am.Signal(self.xlen, name='x{}_{}'.format(i, r.name.lower())))

        self.regs = am.Array(regs)

        # stores the values of rs1 / rs2 from instruction fetch
        self.rs1 = am.Signal(self.xlen)
        self.rs2 = am.Signal(self.xlen)

        # ALU
        self.alu = Alu(self.xlen)

        # instruction bus
        self.instr_bus = InstrBus(self.xlen).create()

        # internal mem bus
        self.mem_bus = MemBus(self.xlen).flip().create()

        # base instructions
        self.base = Rv32i(self)

        # get extensions set up
        self.extensions = collections.OrderedDict()
        for ext in extensions:
            if ext.name is None:
                raise RuntimeError('extension has no name')
            self.extensions[ext.name] = ext(self)

        # used by unit tests
        self.assert_unknown_instructions = False

    @property
    def march_base(self):
        return self.base.march

    @property
    def march_parts(self):
        letters = []
        words = []
        for ext in self.extensions.values():
            m = ext.march
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

    @property
    def ib(self):
        return self.instr_bus

    def elaborate(self, platform):
        m = am.Module()

        # add our extensions and busses
        # the idea is to accumulate busses from the submodules, then
        # wire them up (where up to one submodule can be host)

        m.submodules[self.base.name] = self.base
        m.submodules.alu = self.alu
        for ext in self.extensions.values():
            m.submodules[ext.name] = ext

        bus_components = [self.base, self.alu] + list(self.extensions.values())

        busses = {InstrBus, AluBus, MemBus}
        for ext in self.extensions.values():
            for bus in ext.busses:
                busses.add(bus)

        for bus in busses:
            bus = bus(self.xlen)
            name = bus.name + '_mux'
            m.submodules[name] = mux = OneHotMux(bus)
            mux.add_from(bus_components)

            # special busses

            if isinstance(bus, InstrBus):
                mux.add_controller_bus(self.instr_bus)

            if isinstance(bus, MemBus):
                mux.add_bus(self.mem_bus)
                mux.add_controller_bus(am.lib.wiring.flipped(self.bus))

        # connect instruction bus
        m.d.comb += [
            # outputs
            self.ib.execute.eq(self.state.matches(State.EXECUTE)),
            self.ib.pc.eq(self.pc),
            self.ib.instr.eq(self.instr),

            self.ib.rs1.eq(self.rs1),
            self.ib.rs2.eq(self.rs2),

            self.ib.pc_next.eq(self.pc + 4),
            self.ib.pc_jump.eq(am.Mux(self.ib.j_rel, self.pc + self.ib.j_addr, self.ib.j_addr)),
            self.ib.stalled.eq(self.ib.wait),
        ]

        # core state machine
        with m.Switch(self.state):
            with m.Case(State.FETCH):
                m.d.comb += [
                    self.mem_bus.adr.eq(self.pc[2:]),
                    self.mem_bus.sel.eq(0b1111),
                    self.mem_bus.cyc.eq(1),
                    self.mem_bus.stb.eq(1),
                ]

                with m.If(self.bus.ack):
                    instr = Instruction(self.mem_bus.dat_r)
                    m.d.sync += [
                        # read register values
                        self.rs1.eq(self.regs[instr.rs1]),
                        self.rs2.eq(self.regs[instr.rs2]),

                        # execute instruction
                        self.instr.eq(instr),
                        self.state.eq(State.EXECUTE),
                    ]

            with m.Case(State.EXECUTE):
                # go to next instruction
                with m.If(~self.ib.wait):
                    m.d.sync += self.state.eq(State.FETCH)
                    with m.If(self.ib.j_en):
                        m.d.sync += self.pc.eq(self.ib.pc_jump)
                    with m.Else():
                        m.d.sync += self.pc.eq(self.ib.pc_next)

                # check instruction is valid
                with m.If(~self.ib.valid):
                    # this should be a trap, but for now it's an assertion
                    info = am.Format('!! bad instruction: pc = 0x{:08x}, 0x{:08x}', self.pc, self.instr.as_value())
                    if self.assert_unknown_instructions:
                        m.d.sync += am.Assert(False, info)
                    else:
                        m.d.sync += am.Print(info)

        # writeback to rd
        with m.If(self.ib.rd_stb):
            # only write to non-zero registers
            with m.If(~self.instr.rd.matches(Reg.ZERO)):
                m.d.sync += self.regs[self.instr.rd].eq(self.ib.rd_data)

        return m

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
            self.ib.valid,
        ]

        t['registers'] = [r for r in self.regs]

        t['alu'] = [
            self.alu.in1,
            self.alu.in2,
            self.alu.op,
            self.alu.alt,
            self.alu.out,
        ]

        exts = {}
        t['extensions'] = exts
        for ext in self.extensions.values():
            ext_traces = ext.debug_traces
            if ext_traces:
                exts[ext.name] = ext_traces

        return t

class Rv32i(Extension):
    march = 'rv32i'
    name = 'rv32i'

    busses = [InstrBus, AluBus, MemBus]

    def elaborate(self, platform):
        m = am.Module()

        instructions = [
            self.LUI(self.xlen),
            self.AUIPC(self.xlen),
            self.JAL(self.xlen),
            self.JALR(self.xlen),
            self.Load(self.xlen),
            self.Store(self.xlen),
            self.Branch(self.xlen),
            self.OpImm(self.xlen),
            self.Op(self.xlen),

            self.EBREAK(self.xlen),
        ]

        for instr in instructions:
            m.submodules[instr.__class__.__name__.lower()] = instr

        self.forward_busses(platform, m, instructions)

        return m

    class LUI(InstructionComponent):
        def always(self, platform, m):
            with m.If(self.ib.instr.op.matches(Op.LUI)):
                m.d.comb += self.ib.valid.eq(1)

        def execute(self, platform, m):
            m.d.comb += [
                self.ib.rd_data.eq(self.ib.instr.imm_u),
                self.ib.rd_stb.eq(1),
            ]

    class AUIPC(InstructionComponent):
        def always(self, platform, m):
            with m.If(self.ib.instr.op.matches(Op.AUIPC)):
                m.d.comb += self.ib.valid.eq(1)

        def execute(self, platform, m):
            m.d.comb += [
                self.ib.j_addr.eq(self.ib.instr.imm_u),
                self.ib.j_rel.eq(1),

                self.ib.rd_data.eq(self.ib.pc_jump),
                self.ib.rd_stb.eq(1),
            ]

    class JAL(InstructionComponent):
        def always(self, platform, m):
            with m.If(self.ib.instr.op.matches(Op.JAL)):
                m.d.comb += self.ib.valid.eq(1)

        def execute(self, platform, m):
            m.d.comb += [
                self.ib.rd_data.eq(self.ib.pc_next),
                self.ib.rd_stb.eq(1),

                self.ib.j_addr.eq(self.ib.instr.imm_j),
                self.ib.j_rel.eq(1),
                self.ib.j_en.eq(1),
            ]

    class JALR(InstructionComponent):
        def always(self, platform, m):
            with m.If(self.ib.instr.op.matches(Op.JALR)):
                with m.If(self.ib.instr.funct3.as_value() == 0):
                    m.d.comb += self.ib.valid.eq(1)

        def execute(self, platform, m):
            dest = self.ib.rs1 + self.ib.instr.imm_i

            m.d.comb += [
                self.ib.rd_data.eq(self.ib.pc_next),
                self.ib.rd_stb.eq(1),

                # careful: LSB set to 0
                self.ib.j_addr.eq(am.Cat(0, dest[1:])),
                self.ib.j_en.eq(1),
            ]

    class Branch(InstructionComponent):
        busses = [InstrBus, AluBus]

        def always(self, platform, m):
            with m.If(self.ib.instr.op.matches(Op.BRANCH)):
                with m.If(self.ib.instr.funct3.branch.matches(
                        Funct3Branch.EQ,
                        Funct3Branch.NE,
                        Funct3Branch.LT,
                        Funct3Branch.GE,
                        Funct3Branch.LTU,
                        Funct3Branch.GEU,
                )):
                    m.d.comb += self.ib.valid.eq(1)

        def execute(self, platform, m):
            m.d.comb += [
                self.alu_bus.in1.eq(self.ib.rs1),
                self.alu_bus.in2.eq(self.ib.rs2),

                self.ib.j_addr.eq(self.ib.instr.imm_b),
                self.ib.j_rel.eq(1),
            ]

            with m.Switch(self.ib.instr.funct3.branch):
                with m.Case(Funct3Branch.EQ):
                    m.d.comb += [
                        self.alu_bus.op.eq(Funct3Alu.ADD_SUB),
                        self.alu_bus.alt.eq(1),
                    ]
                    with m.If(~self.alu_bus.out.any()):
                        m.d.comb += self.ib.j_en.eq(1)

                with m.Case(Funct3Branch.NE):
                    m.d.comb += [
                        self.alu_bus.op.eq(Funct3Alu.ADD_SUB),
                        self.alu_bus.alt.eq(1),
                    ]
                    with m.If(self.alu_bus.out.any()):
                        m.d.comb += self.ib.j_en.eq(1)

                with m.Case(Funct3Branch.LT):
                    m.d.comb += self.alu_bus.op.eq(Funct3Alu.LT)
                    with m.If(self.alu_bus.out[0]):
                        m.d.comb += self.ib.j_en.eq(1)

                with m.Case(Funct3Branch.GE):
                    m.d.comb += self.alu_bus.op.eq(Funct3Alu.LT)
                    with m.If(~self.alu_bus.out[0]):
                        m.d.comb += self.ib.j_en.eq(1)

                with m.Case(Funct3Branch.LTU):
                    m.d.comb += self.alu_bus.op.eq(Funct3Alu.LTU)
                    with m.If(self.alu_bus.out[0]):
                        m.d.comb += self.ib.j_en.eq(1)

                with m.Case(Funct3Branch.GEU):
                    m.d.comb += self.alu_bus.op.eq(Funct3Alu.LTU)
                    with m.If(~self.alu_bus.out[0]):
                        m.d.comb += self.ib.j_en.eq(1)

    class Load(InstructionComponent):
        busses = [InstrBus, MemBus]

        def always(self, platform, m):
            with m.If(self.ib.instr.op.matches(Op.LOAD)):
                with m.If(self.ib.instr.funct3.mem.matches(
                        Funct3Mem.BYTE,
                        Funct3Mem.HALF,
                        Funct3Mem.WORD,
                        Funct3Mem.BYTE_U,
                        Funct3Mem.HALF_U,
                )):
                    m.d.comb += self.ib.valid.eq(1)

        def execute(self, platform, m):
            dest = self.ib.rs1 + self.ib.instr.imm_i
            m.d.comb += [
                self.mem_bus.adr.eq(dest[2:]),
                self.mem_bus.cyc.eq(1),
                self.mem_bus.stb.eq(1),
            ]

            with m.Switch(self.ib.instr.funct3.mem):
                with m.Case(Funct3Mem.BYTE, Funct3Mem.BYTE_U):
                    m.d.comb += self.mem_bus.sel.eq(1 << dest[:2])

                with m.Case(Funct3Mem.HALF, Funct3Mem.HALF_U):
                    m.d.comb += self.mem_bus.sel.eq(0b11 << (dest[:2] & 0b10))

                with m.Default():
                    m.d.comb += self.mem_bus.sel.eq(0b1111)

            with m.If(self.mem_bus.ack):
                # load the data
                data = self.mem_bus.dat_r

                # shift data
                shift = am.Mux(
                        self.ib.instr.funct3.mem == Funct3Mem.WORD,
                        0,
                        dest[:2],
                    )
                shift &= am.Mux(
                    self.ib.instr.funct3.mem.matches(Funct3Mem.HALF, Funct3Mem.HALF_U),
                    0b10,
                    0b11,
                )
                data = data >> (shift << 3)

                # mask data by sel
                mask = am.Mux(
                    self.ib.instr.funct3.mem.matches(Funct3Mem.BYTE, Funct3Mem.BYTE_U),
                    0x0000_00ff,
                    am.Mux(
                        self.ib.instr.funct3.mem.matches(Funct3Mem.HALF, Funct3Mem.HALF_U),
                        0x0000_ffff,
                        0xffff_ffff,
                    ),
                )
                data &= mask

                # sign extend data
                sign = am.Mux(
                    self.ib.instr.funct3.mem.matches(Funct3Mem.BYTE),
                    data[7],
                    am.Mux(
                        self.ib.instr.funct3.mem.matches(Funct3Mem.HALF),
                        data[15],
                        0,
                    ),
                )
                data |= (~mask) & sign.replicate(self.xlen)

                m.d.comb += [
                    # writeback our loaded data
                    self.ib.rd_data.eq(data),
                    self.ib.rd_stb.eq(1),
                ]

            with m.Else():
                # wait until ack
                m.d.comb += self.ib.wait.eq(1)

    class Store(InstructionComponent):
        busses = [InstrBus, MemBus]

        def always(self, platform, m):
            with m.If(self.ib.instr.op.matches(Op.STORE)):
                with m.If(self.ib.instr.funct3.mem.matches(
                        Funct3Mem.BYTE,
                        Funct3Mem.HALF,
                        Funct3Mem.WORD,
                )):
                    m.d.comb += self.ib.valid.eq(1)

        def execute(self, platform, m):
            src = self.ib.rs2
            dest = self.ib.rs1 + self.ib.instr.imm_s

            m.d.comb += [
                self.mem_bus.adr.eq(dest[2:]),
                self.mem_bus.cyc.eq(1),
                self.mem_bus.stb.eq(1),
                self.mem_bus.we.eq(1),
            ]

            # wait here until ack
            with m.If(~self.mem_bus.ack):
                m.d.comb += self.ib.wait.eq(1)

            # this is fiddled with in the cases later
            m.d.comb += self.mem_bus.dat_w.eq(am.Cat(src[:16], src[:16]))

            with m.Switch(self.ib.instr.funct3.mem):
                with m.Case(Funct3Mem.BYTE):
                    m.d.comb += [
                        self.mem_bus.dat_w[8:16].eq(src[:8]),
                        self.mem_bus.dat_w[24:32].eq(src[:8]),
                        self.mem_bus.sel.eq(1 << dest[:2]),
                    ]

                with m.Case(Funct3Mem.HALF):
                    m.d.comb += [
                        self.mem_bus.sel.eq(0b11 << (dest[:2] & 0b10)),
                    ]

                with m.Default():
                    m.d.comb += [
                        self.mem_bus.dat_w[16:32].eq(src[16:32]),
                        self.mem_bus.sel.eq(0b1111),
                    ]

    class OpImm(InstructionComponent):
        busses = [InstrBus, AluBus]

        def always(self, platform, m):
            with m.If(self.ib.instr.op.matches(Op.OP_IMM)):
                with m.Switch(self.ib.instr.funct3.alu):
                    with m.Case(Funct3Alu.SHIFT_L):
                        with m.If(self.ib.instr.funct7.alu.matches(Funct7Alu.NORMAL)):
                            m.d.comb += self.ib.valid.eq(1)

                    with m.Case(Funct3Alu.SHIFT_R):
                        with m.If(self.ib.instr.funct7.alu.matches(Funct7Alu.NORMAL)):
                            m.d.comb += self.ib.valid.eq(1)
                        with m.Elif(self.ib.instr.funct7.alu.matches(Funct7Alu.ALT)):
                            m.d.comb += self.ib.valid.eq(1)

                    with m.Default():
                        m.d.comb += self.ib.valid.eq(1)

        def execute(self, platform, m):
            m.d.comb += [
                self.alu_bus.in1.eq(self.ib.rs1),
                self.alu_bus.in2.eq(self.ib.instr.imm_i),
                self.alu_bus.op.eq(self.ib.instr.funct3.alu),

                self.ib.rd_data.eq(self.alu_bus.out),
                self.ib.rd_stb.eq(1),
            ]

            with m.If(self.ib.instr.funct3.alu.matches(Funct3Alu.SHIFT_L, Funct3Alu.SHIFT_R)):
                m.d.comb += [
                    self.alu_bus.alt.eq(~self.ib.instr.funct7.alu.matches(Funct7Alu.NORMAL)),
                    self.alu_bus.in2.eq(self.ib.instr.rs2),
                ]

    class Op(InstructionComponent):
        busses = [InstrBus, AluBus]

        def always(self, platform, m):
            with m.If(self.ib.instr.op.matches(Op.OP)):
                with m.If(self.ib.instr.funct3.alu.matches(Funct3Alu.ADD_SUB, Funct3Alu.SHIFT_R)):
                    with m.If(self.ib.instr.funct7.alu.matches(Funct7Alu.NORMAL)):
                        m.d.comb += self.ib.valid.eq(1)

                    with m.Elif(self.ib.instr.funct7.alu.matches(Funct7Alu.ALT)):
                        m.d.comb += self.ib.valid.eq(1)

                with m.Else():
                    with m.If(self.ib.instr.funct7.alu.matches(Funct7Alu.NORMAL)):
                        m.d.comb += self.ib.valid.eq(1)

        def execute(self, platform, m):
            m.d.comb += [
                self.alu_bus.in1.eq(self.ib.rs1),
                self.alu_bus.in2.eq(self.ib.rs2),
                self.alu_bus.op.eq(self.ib.instr.funct3.alu),
                self.alu_bus.alt.eq(~self.ib.instr.funct7.alu.matches(Funct7Alu.NORMAL)),

                self.ib.rd_data.eq(self.alu_bus.out),
                self.ib.rd_stb.eq(1),
            ]

    class EBREAK(InstructionComponent):
        def always(self, platform, m):
            with m.If(self.ib.instr.as_value() == 0b000000000001_00000_000_00000_1110011):
                m.d.comb += self.ib.valid.eq(1)

        def execute(self, platform, m):
            # wait here forever
            m.d.comb += [
                self.ib.wait.eq(1),
            ]

class Alu(am.lib.wiring.Component):
    def __init__(self, xlen):
        super().__init__(AluBus(xlen))

        self.xlen = xlen
        self.minus = am.Signal(xlen + 1)
        self.plus = am.Signal(xlen)
        self.ltu = am.Signal(1)
        self.lt = am.Signal(1)

    def elaborate(self, platform):
        m = am.Module()

        # set up our intermediates
        m.d.comb += [
            self.minus.eq(self.in1.as_unsigned() - self.in2.as_unsigned()),
            self.plus.eq(self.in1 + self.in2),
            self.ltu.eq(self.minus[-1]),
            self.lt.eq(am.Mux(self.in1[-1] ^ self.in2[-1], self.in1[-1], self.minus[-1])),
        ]

        # shared shifter for ll / rl / ra
        shift_in = am.Mux(self.op.matches(Funct3Alu.SHIFT_L), self.in1[::-1], self.in1)
        rightshift = am.Cat(shift_in, self.alt & shift_in[-1]).as_signed() >> (self.in2 & (self.xlen - 1))
        leftshift = rightshift[:-1][::-1]

        # ok, now push the right one to out
        with m.Switch(self.op):
            with m.Case(Funct3Alu.ADD_SUB):
                with m.If(self.alt):
                    m.d.comb += self.out.eq(self.minus)
                with m.Else():
                    m.d.comb += self.out.eq(self.plus)
            with m.Case(Funct3Alu.SHIFT_L):
                m.d.comb += self.out.eq(leftshift)
            with m.Case(Funct3Alu.SHIFT_R):
                m.d.comb += self.out.eq(rightshift)
            with m.Case(Funct3Alu.LT):
                m.d.comb += self.out.eq(self.lt)
            with m.Case(Funct3Alu.LTU):
                m.d.comb += self.out.eq(self.ltu)
            with m.Case(Funct3Alu.XOR):
                m.d.comb += self.out.eq(self.in1 ^ self.in2)
            with m.Case(Funct3Alu.OR):
                m.d.comb += self.out.eq(self.in1 | self.in2)
            with m.Case(Funct3Alu.AND):
                m.d.comb += self.out.eq(self.in1 & self.in2)

        return m

class Zicsr(Extension):
    march = 'zicsr'
    name = 'zicsr'

    def __init__(self, cpu):
        super().__init__(cpu, signature={
            'csr_bus': am.lib.wiring.Out(CsrBus(cpu.xlen)),
        })

        self.uimm = am.Signal(cpu.xlen)
        self.modify = am.Signal(1)
        self.setbits = am.Signal(1)
        self.new = am.Signal(cpu.xlen)

    def elaborate(self, platform):
        m = am.Module()

        m.d.comb += [
            self.csr_bus.adr.eq(self.ib.instr.imm_i.as_unsigned()),

            # rs1 interpreted as an immediate value, 0-extended to xlen
            self.uimm.eq(self.ib.instr.rs1.as_value().as_unsigned()),
        ]

        # mini-alu for register set/reset
        old = am.Mux(self.modify, self.csr_bus.r_data, 0)
        m.d.comb += [
            self.new.eq(self.ib.rs1),
            self.csr_bus.w_data.eq(
                am.Mux(
                    self.setbits,
                    self.new | old,
                    ~self.new & old,
                )
            )
        ]

        with m.If(self.ib.instr.op.matches(Op.SYSTEM)):
            with m.If(self.ib.instr.funct3.csr.matches(
                    Funct3Csr.RW,
                    Funct3Csr.RS,
                    Funct3Csr.RC,
                    Funct3Csr.RWI,
                    Funct3Csr.RSI,
                    Funct3Csr.RCI,
            )):
                # FIXME writes to read-only registers should be invalid
                m.d.comb += self.ib.valid.eq(self.csr_bus.valid)

                with m.If(~self.csr_bus.valid & self.ib.execute):
                    info = am.Format('!! bad csr: funct3 = 0b{:03b}, addr = 0x{:03x}', self.ib.instr.funct3.as_value(), self.csr_bus.adr)
                    m.d.sync += am.Print(info)

        with m.If(self.ib.valid & self.ib.execute):
            m.d.comb += [
                self.ib.rd_data.eq(self.csr_bus.r_data),
                self.ib.rd_stb.eq(1),
            ]

            with m.Switch(self.ib.instr.funct3.csr):
                with m.Case(Funct3Csr.RW):
                    m.d.comb += [
                        # no read side effects if rd is zero
                        self.csr_bus.r_stb.eq(self.ib.instr.rd != Reg.ZERO),

                        self.csr_bus.w_stb.eq(1),
                        self.setbits.eq(1),
                    ]

                with m.Case(Funct3Csr.RS):
                    m.d.comb += [
                        self.csr_bus.r_stb.eq(1),

                        # no write side effects if rs1 is zero
                        self.csr_bus.w_stb.eq(self.ib.instr.rs1 != Reg.ZERO),
                        self.modify.eq(1),
                        self.setbits.eq(1),
                    ]

                with m.Case(Funct3Csr.RC):
                    m.d.comb += [
                        self.csr_bus.r_stb.eq(1),

                        # no write side effects if rs1 is zero
                        self.csr_bus.w_stb.eq(self.ib.instr.rs1 != Reg.ZERO),
                        self.modify.eq(1),
                    ]

                with m.Case(Funct3Csr.RWI):
                    m.d.comb += [
                        # no read side effects if rd is zero
                        self.csr_bus.r_stb.eq(self.ib.instr.rd != Reg.ZERO),

                        self.csr_bus.w_stb.eq(1),
                        self.setbits.eq(1),
                        self.new.eq(self.uimm),
                    ]

                with m.Case(Funct3Csr.RSI):
                    m.d.comb += [
                        self.csr_bus.r_stb.eq(1),

                        # no write side effects if uimm is zero
                        self.csr_bus.w_stb.eq(self.uimm != 0),
                        self.modify.eq(1),
                        self.setbits.eq(1),
                        self.new.eq(self.uimm),
                    ]

                with m.Case(Funct3Csr.RCI):
                    m.d.comb += [
                        self.csr_bus.r_stb.eq(1),

                        # no write side effects if uimm is zero
                        self.csr_bus.w_stb.eq(self.uimm != 0),
                        self.modify.eq(1),
                        self.new.eq(self.uimm),
                    ]

        return m

    @property
    def debug_traces(self):
        return [
            self.csr_bus.adr,
            self.csr_bus.r_stb,
            self.csr_bus.r_data,
            self.csr_bus.w_stb,
            self.csr_bus.w_data,
            self.csr_bus.valid,
        ]

class Zicntr(Extension):
    # some gcc don't support this, and it also doesn't really matter
    #march = 'zicntr'
    name = 'zicntr'

    busses = [InstrBus, CsrBus]

    def __init__(self, cpu):
        super().__init__(cpu)

        self.cycle = am.Signal(64)
        self.time = self.cycle # a valid implementation of time
        self.instret = am.Signal(64)

    def elaborate(self, platform):
        m = am.Module()

        # cycle is easy
        m.d.sync += self.cycle.eq(self.cycle + 1)

        # instret is also easy enough
        with m.If(self.ib.execute & ~self.ib.stalled):
            m.d.sync += self.instret.eq(self.instret + 1)

        with m.Switch(self.csr_bus.adr):
            with m.Case(0xc00):
                # cycle
                m.d.comb += [
                    self.csr_bus.valid.eq(1),
                    self.csr_bus.r_data.eq(self.cycle),
                ]
            with m.Case(0xc01):
                # time
                m.d.comb += [
                    self.csr_bus.valid.eq(1),
                    self.csr_bus.r_data.eq(self.time),
                ]
            with m.Case(0xc02):
                # instret
                m.d.comb += [
                    self.csr_bus.valid.eq(1),
                    self.csr_bus.r_data.eq(self.instret),
                ]

            if self.xlen < 64:
                with m.Case(0xc80):
                    # cycleh
                    m.d.comb += [
                        self.csr_bus.valid.eq(1),
                        self.csr_bus.r_data.eq(self.cycle[self.xlen:]),
                    ]
                with m.Case(0xc81):
                    # timeh
                    m.d.comb += [
                        self.csr_bus.valid.eq(1),
                        self.csr_bus.r_data.eq(self.time[self.xlen:]),
                    ]
                with m.Case(0xc82):
                    # instreth
                    m.d.comb += [
                        self.csr_bus.valid.eq(1),
                        self.csr_bus.r_data.eq(self.instret[self.xlen:]),
                    ]

        return m

    @property
    def debug_traces(self):
        return [
            self.cycle,
            self.instret,
        ]
