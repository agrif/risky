import contextlib

import amaranth as am

import risky.compiler
import risky.cpu
import risky.memory
import risky.peripherals.gpio
import risky.peripherals.uart

class Soc(am.lib.wiring.Component):
    tx: am.lib.wiring.Out(1)

    def __init__(self, clk_freq, cpu=None, memory_contents=[]):
        super().__init__()

        if cpu is None:
            cpu = risky.cpu.Cpu([
                risky.cpu.Zicsr(),
                risky.cpu.Zicntr(),
            ])

        self.cpu = cpu
        self.memory = risky.memory.MemoryMap()
        self.output = risky.peripherals.gpio.Output(1)
        self.uart = risky.peripherals.uart.Uart(clk_freq)

        # 64K rom
        self.rom = self.memory.add_rom('rom', 0x0000_0000, 0x0001_0000 - 1, init=memory_contents)
        # 32K ram
        self.memory.add_ram('ram', 0x1000_0000, 0x8000 - 1)

        # peripherals
        self.memory.add('uart', 0x2000_0000, 0x10 - 1, self.uart)
        self.memory.add_rom('clk_freq', 0x2000_0010, 0x4 - 1, init=[int(clk_freq)])
        self.memory.add('output', 0x2000_0014, 0x4 - 1, self.output)

    def set_rom(self, contents):
        self.rom.memory.data.init = contents

    @contextlib.contextmanager
    def compiler(self, **kwargs):
        our_kwargs = dict(march=self.cpu.march)
        our_kwargs.update(kwargs)
        compiler = risky.compiler.Compiler(**our_kwargs)
        with compiler as c:
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
