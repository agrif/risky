import contextlib

import amaranth as am

import amaranth_soc.csr

import risky.compiler
import risky.cpu
import risky.memory
import risky.csr
import risky.peripherals.gpio
import risky.peripherals.uart

class Info(risky.csr.Peripheral):
    class ClkFreq(amaranth_soc.csr.Register, access='r'):
        value: amaranth_soc.csr.Field(amaranth_soc.csr.action.R, 32)

    def __init__(self, clk_freq_hz):
        super().__init__(depth=4)

        self.clk_freq_hz = int(clk_freq_hz)

        with self.register_builder() as b:
            self.reg_clk_freq = b.add('clk_freq', self.ClkFreq())

    def elaborate(self, platform):
        m = am.Module()

        self.elaborate_registers(platform, m)

        m.d.comb += self.reg_clk_freq.f.value.r_data.eq(self.clk_freq_hz)

        return m

class Soc(am.lib.wiring.Component):
    tx: am.lib.wiring.Out(1)

    def __init__(self, clk_freq, cpu=None, memory_contents=[]):
        super().__init__()

        if cpu is None:
            #import risky.old_cpu
            #cpu = risky.old_cpu.Cpu()
            cpu = risky.cpu.Cpu([
                risky.cpu.Zicsr(),
                risky.cpu.Zicntr(),
            ])

        self.cpu = cpu
        self.memory = risky.memory.MemoryMap(alignment=28)

        # 32K rom
        self.rom = self.memory.add_rom('rom', 32 * 1024, init=memory_contents)
        # 8K ram
        self.memory.add_ram('ram', 8 * 1024)

        # peripherals
        with self.memory.add_peripherals('io', addr_width=16, alignment=8) as p:
            self.uart = p.add('uart', risky.peripherals.uart.Peripheral())
            p.add('info', Info(clk_freq))
            self.output = p.add('leds', risky.peripherals.gpio.Output(1))

    def set_rom(self, contents):
        self.rom.memory.data.init = contents

    @contextlib.contextmanager
    def compiler(self, **kwargs):
        our_kwargs = dict(march=self.cpu.march)
        our_kwargs.update(kwargs)
        compiler = risky.compiler.Compiler(**our_kwargs)
        with compiler as c:
            c.include_source('memory.x', self.memory.generate_memory_x())
            c.include_source('risky.h', self.memory.generate_header())

            yield c

    @classmethod
    def with_source_files(cls, clk_freq, *fnames):
        soc = cls(clk_freq)

        with soc.compiler() as c:
            for fname in fnames:
                c.add(fname)

            elf = c.link()

        #elf.dump('debug.elf')
        #elf.dump_flat('debug.bin')
        #elf.dump_disassemble('debug.dump')

        soc.set_rom(elf.flat_words)

        return soc

    def elaborate(self, platform):
        m = am.Module()

        m.submodules.cpu = self.cpu
        m.submodules.memory = self.memory

        am.lib.wiring.connect(m, self.cpu.bus, self.memory.bus)

        m.d.comb += [
            self.tx.eq(self.uart.tx),
        ]

        return m
