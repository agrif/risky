import sys

import amaranth as am
import amaranth.sim

import risky.compiler
import risky.instruction
import risky.soc

class Simulated:
    clk_freq = 1_000_000

    def __init__(self):
        self.dut = self.construct()
        self.sim = am.sim.Simulator(self.dut)
        self.sim.add_clock(1 / self.clk_freq)
        self.sim.add_testbench(self.testbench)

    def run(self, output=None):
        if output:
            with self.sim.write_vcd(output):
                self.sim.run()
        else:
            self.sim.run()

    def construct(self):
        raise NotImplementedError

    async def testbench(self, ctx):
        pass

class UnitTest(Simulated):
    HEADER = """
    .globl _reset_vector
    _reset_vector:
    """

    TIMEOUT = 100

    PROGRAM = None

    CHECKPOINTS = []

    @property
    def name(self):
        return self.__class__.__name__

    @classmethod
    def iter_tests(cls):
        for subclass in cls.__subclasses__():
            if subclass.PROGRAM:
                yield subclass()

    def construct(self):
        dut = risky.soc.Soc(self.clk_freq)
        dut.cpu.assert_unknown_instructions = True

        with dut.compiler(runtime=False, optimize=False) as c:
            c.add_source('s', self.HEADER + '\n' + self.PROGRAM)
            self.elf = c.link()

        dut.set_rom(self.elf.flat_words)
        self.symbols = self.elf.symbols()

        self._setup_renames()

        return dut

    def _setup_renames(self):
        self.renames = dict()

        for (i, r) in enumerate(risky.instruction.Reg):
            assert i == r.value
            self.renames[r.name.lower()] = 'regs.{}'.format(i)

    def lookup(self, ctx, name):
        name = self.renames.get(name, name)
        attr = self.dut.cpu

        parts = name.split('.')

        if parts[0] == 'memory':
            return self.lookup_memory(ctx, *parts[1:])

        for part in parts:
            n = None
            try:
                n = int(part)
            except ValueError:
                pass

            if n is None:
                attr = getattr(attr, part)
            else:
                attr = attr[n]

        value = ctx.get(attr)
        return value

    def lookup_memory(self, ctx, name):
        addr = None
        try:
            addr = int(name)
        except ValueError:
            addr = self.symbols[name]

        value = ctx.get(self.dut.memory[addr])
        return value

    async def advance_until(self, ctx, addr_or_symbol, max_ticks=None):
        if max_ticks is None:
            max_ticks = self.TIMEOUT

        if isinstance(addr_or_symbol, str):
            try:
                addr = self.symbols[addr_or_symbol]
            except KeyError:
                raise RuntimeError('unknown symbol: {}'.format(addr_or_symbol))
        else:
            addr = addr_or_symbol

        for _ in range(max_ticks):
            pc = ctx.get(self.dut.cpu.pc)
            state = ctx.get(self.dut.cpu.state)

            # always tick at least once, even if we hit a checkpoint
            await ctx.tick()

            if pc == addr and state == risky.cpu.State.FETCH_INSTR.value:
                return

        if isinstance(addr_or_symbol, str):
            name = addr_or_symbol
        else:
            name = hex(addr_or_symbol)
        raise RuntimeError('timeout waiting for checkpoint {}'.format(name))

    async def testbench(self, ctx):
        for addr_or_symbol, checks in self.CHECKPOINTS:
            await self.advance_until(ctx, addr_or_symbol)
            self.test_checkpoint(ctx, checks)

    def test_checkpoint(self, ctx, checks):
        for name, value in checks.items():
            self.assert_eq(ctx, name, value)

    def assert_eq(self, ctx, name, value):
        real = self.lookup(ctx, name)
        if value != real:
            raise RuntimeError('bad value for {} (expected {}, got {})'.format(name, hex(value), hex(real)))
