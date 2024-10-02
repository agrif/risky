import contextlib

import amaranth as am

import amaranth_soc.csr

import risky.compiler
import risky.cpu
import risky.memory
import risky.csr
import risky.old_cpu
import risky.ormux_cpu
import risky.peripherals.gpio
import risky.peripherals.uart

class Info(risky.csr.Peripheral):
    class ClkFreq(amaranth_soc.csr.Register, access='r'):
        def __init__(self):
            super().__init__(
                amaranth_soc.csr.Field(amaranth_soc.csr.action.R, 32),
            )

    def __init__(self, clk_freq_hz):
        super().__init__(depth=4)

        self.clk_freq_hz = int(clk_freq_hz)

        with self.register_builder() as b:
            self.reg_clk_freq = b.add('clk_freq', self.ClkFreq())

    def elaborate(self, platform):
        m = am.Module()

        self.elaborate_registers(platform, m)

        m.d.comb += self.reg_clk_freq.f.r_data.eq(self.clk_freq_hz)

        return m

class Soc(am.lib.wiring.Component):
    tx: am.lib.wiring.Out(1)

    def __init__(self, clk_freq, cpu=None, memory_contents=b''):
        super().__init__()

        if cpu is None:
            #cpu = risky.old_cpu.Cpu()
            cpu = risky.ormux_cpu.Cpu([
                risky.ormux_cpu.Zicsr,
                risky.ormux_cpu.Zicntr,
            ])
            #cpu = risky.cpu.Cpu([
            #    risky.cpu.Zicsr(),
            #    risky.cpu.Zicntr(),
            #])

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
        self.rom.set_data(contents)

    def elaborate(self, platform):
        m = am.Module()

        m.submodules.cpu = self.cpu
        m.submodules.memory = self.memory

        am.lib.wiring.connect(m, self.cpu.bus, self.memory.bus)

        m.d.comb += [
            self.tx.eq(self.uart.tx),
        ]

        return m

    @contextlib.contextmanager
    def compiler(self, **kwargs):
        our_kwargs = dict(march=self.cpu.march)
        our_kwargs.update(kwargs)
        compiler = risky.compiler.Compiler(**our_kwargs)
        with compiler as c:
            c.include_source('memory.x', self.generate_memory_x())
            c.include_source('risky.h', self.generate_header())

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

        soc.set_rom(elf.flat)

        return soc

    def generate_memory_x(self):
        memory_x = 'MEMORY\n{\n'
        for n, children in self.memory.get_resource_tree().walk():
            if n.memory_x_access:
                children.clear()
                memory_x += '    {} ({}) : ORIGIN = 0x{:x}, LENGTH = {}\n'.format(
                    '_'.join(n.path).upper(),
                    n.memory_x_access,
                    n.start,
                    n.size,
                )
        memory_x += '}\n'
        return memory_x

    def generate_header(self):
        h = ''
        h += '#ifndef __RISKY_H_INCLUDED\n'
        h += '#define __RISKY_H_INCLUDED\n\n'

        h += '#if !defined(__ASSEMBLER__)\n'
        h += '#include <stdint.h>\n'
        h += '#endif\n\n'

        for n, _ in self.memory.get_resource_tree().walk():
            if not n.path:
                continue

            name = '_'.join(n.path).upper()
            parent = '_'.join(n.path[:-1]).upper()

            leaf = '_ADDR' if n.resource else '_BASE'

            def define(name, fmt, *args, **kwargs):
                nonlocal h
                h += '#define {:<40} '.format(name) + fmt.format(*args, **kwargs) + '\n'

            if len(n.path) == 1:
                define(name + leaf, '0x{:08x}', n.start)
            else:
                define(name + leaf, '({}_BASE + 0x{:x})', parent, n.offset)

            define(name + '_SIZE', '0x{:x}', n.size)
            if n.resource and n.c_type:
                define(name, '(*(volatile {} *){}{})', n.c_type, name, leaf)
                if isinstance(n.resource, amaranth_soc.csr.Register):
                    field_start = 0
                    for fn, fv in n.resource:
                        if not fn:
                            # whole register is field
                            break

                        fieldname = name + '_' + '_'.join(fn).upper()
                        field_size = fv.port.shape.width
                        field_end = field_start + field_size

                        h += '\n'
                        define(fieldname + '_SHIFT', '{}', field_start)
                        define(fieldname + '_WIDTH', '{}', field_size)
                        define(fieldname + '_MASK', '(((1 << {0}_WIDTH) - 1) << {0}_SHIFT)', fieldname)

                        field_start = field_end
            h += '\n'

        h += '#endif /* __RISKY_H_INCLUDED */\n'
        return h
