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
    def with_sources(cls, clk_freq, *fnames):
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

    @classmethod
    def with_elf(cls, clk_freq, elfname):
        soc = cls(clk_freq)

        elf = risky.compiler.ElfData.from_file(elfname)
        soc.set_rom(elf.flat)

        return soc

    @classmethod
    def with_binaries(cls, clk_freq, *binnames):
        soc = cls(clk_freq)

        data = b''
        for binname in binnames:
            with open(binname, 'rb') as f:
                data += f.read()

        soc.set_rom(data)
        return soc

    @classmethod
    def with_autodetect(cls, clk_freq, *fnames):
        # try elf first, it's the most easy to id
        elf = True
        try:
            fname, *_ = fnames
            risky.compiler.ElfData.from_file(fname)
        except Exception:
            elf = False

        if elf:
            if len(fnames) > 1:
                raise ValueError('can only load at most one ELF file')
            print('loading ELF:', *fnames)
            return cls.with_elf(clk_freq, *fnames)

        # are the files all valid utf-8?
        # not the best test, but it'll do
        sources = True
        try:
            for fname in fnames:
                with open(fname, 'r', encoding='utf-8') as f:
                    f.read()
        except Exception as e:
            sources = False

        if sources:
            print('loading sources:', *fnames)
            return cls.with_sources(clk_freq, *fnames)

        # just load them raw
        print('loading binaries:', *fnames)
        return cls.with_binaries(clk_freq, *fnames)

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
