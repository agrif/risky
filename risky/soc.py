import contextlib
import io
import xml.etree.ElementTree as ET

import amaranth as am

import amaranth_soc.csr

import risky.compiler
import risky.cpu
import risky.memory
import risky.csr
import risky.old_cpu
import risky.ormux_cpu
import risky.peripherals.gpio
import risky.peripherals.spi
import risky.peripherals.uart

class Info(risky.csr.Peripheral):
    class Constant(amaranth_soc.csr.Register, access='r'):
        def __init__(self):
            super().__init__(
                amaranth_soc.csr.Field(amaranth_soc.csr.action.R, 32),
            )

    def __init__(self, clk_freq_hz, baud=115200):
        super().__init__(depth=8)

        self.clk_freq_hz = int(clk_freq_hz)
        self.std_baud = int((self.clk_freq_hz + (baud // 2)) // baud) - 1

        with self.register_builder() as b:
            self.reg_clk_freq = b.add('clk_freq', self.Constant())
            self.reg_std_baud = b.add('std_baud', self.Constant())

    def elaborate(self, platform):
        m = am.Module()

        self.elaborate_registers(platform, m)

        m.d.comb += [
            self.reg_clk_freq.f.r_data.eq(self.clk_freq_hz),
            self.reg_std_baud.f.r_data.eq(self.std_baud),
        ]

        return m

class Soc(am.lib.wiring.Component):
    rx: am.lib.wiring.In(1)
    tx: am.lib.wiring.Out(1)

    spi_cs: am.lib.wiring.Out(1)
    sclk: am.lib.wiring.Out(1)
    copi: am.lib.wiring.Out(1)
    cipo: am.lib.wiring.In(1)

    def __init__(self, clk_freq, cpu=None, memory_contents=b''):
        super().__init__()

        self.clk_freq = clk_freq

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

        # 4K bootloader rom
        self.bootloader = self.memory.add_rom('bootloader', 4 * 1024)
        # 32K rom
        self.rom = self.memory.add_ram('rom', 32 * 1024, init=memory_contents)
        # 8K ram
        self.memory.add_ram('ram', 8 * 1024)

        # peripherals
        with self.memory.add_peripherals('io', addr_width=16, alignment=8) as p:
            self.uart = p.add('uart', risky.peripherals.uart.Peripheral())
            p.add('info', Info(clk_freq))
            self.output = p.add('leds', risky.peripherals.gpio.Output(1))
            self.spi = p.add('spi', risky.peripherals.spi.Peripheral())

        with self.compiler(bootloader=True) as c:
            c.add(c.copy_runtime_file('bootloader.c'))
            elf = c.link()

            #elf.dump('bootloader.elf')
            #elf.dump_flat('bootloader.bin')
            #elf.dump_disassemble('bootloader.dump')

            self.bootloader.set_data(elf.flat)

    def set_rom(self, contents):
        self.rom.set_data(contents)

    def elaborate(self, platform):
        m = am.Module()

        m.submodules.cpu = self.cpu
        m.submodules.memory = self.memory

        am.lib.wiring.connect(m, self.cpu.bus, self.memory.bus)

        m.d.comb += [
            self.uart.rx.eq(self.rx),
            self.tx.eq(self.uart.tx),

            self.spi_cs.eq(self.spi.cs),
            self.sclk.eq(self.spi.sclk),
            self.copi.eq(self.spi.copi),
            self.spi.cipo.eq(self.cipo),
        ]

        return m

    @property
    def debug_traces(self):
        t = self.cpu.debug_traces.copy()

        t['uart'] = [
            self.uart.rx,
            self.uart.tx,
        ]

        t['output'] = [o for o in self.output.output]

        t['spi'] = [
            self.spi.cs,
            self.spi.sclk,
            self.spi.copi,
            self.spi.cipo,
        ]

        return t

    @contextlib.contextmanager
    def compiler(self, bootloader=False, **kwargs):
        our_kwargs = dict(march=self.cpu.march)
        our_kwargs.update(kwargs)
        compiler = risky.compiler.Compiler(**our_kwargs)
        with compiler as c:
            c.include_source('memory.x', self.generate_memory_x(bootloader=bootloader))
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

    def generate_memory_x(self, bootloader=False):
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

        memory_x += '\n'

        code_region = 'ROM'
        if bootloader:
            code_region = 'BOOTLOADER'

        memory_x += 'REGION_ALIAS("REGION_TEXT", {});\n'.format(code_region)
        memory_x += 'REGION_ALIAS("REGION_RODATA", {});\n'.format(code_region)
        memory_x += 'REGION_ALIAS("REGION_DATA", RAM);\n'
        memory_x += 'REGION_ALIAS("REGION_BSS", RAM);\n'
        memory_x += 'REGION_ALIAS("REGION_HEAP", RAM);\n'
        memory_x += 'REGION_ALIAS("REGION_STACK", RAM);\n'

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

    def generate_svd(self):
        root = ET.Element('device')
        ns = {'xs': 'http://www.w3.org/2001/XMLSchema-instance'}
        doc = ET.ElementTree(root)

        def namespaced(name, tag):
            return ET.QName(ns[name], tag)

        def add_text(tag, name, text):
            child = ET.SubElement(tag, name)
            child.text = str(text)
            return child

        def identify_name(name):
            try:
                int(name)
                name = 'V_' + name
            except ValueError:
                pass

            return name

        root.attrib[namespaced('xs', 'noNamespaceSchemaLocation')] = 'CMSIS-SVD.xsd'
        root.attrib['schemaVersion'] = '1.1'

        add_text(root, 'name', 'risky')
        add_text(root, 'version', '1.0')
        add_text(root, 'description', 'Risky RISCV Core')

        add_text(root, 'addressUnitBits', 8)
        add_text(root, 'width', 32)

        cpu = ET.SubElement(root, 'cpu')
        add_text(cpu, 'name', 'other')
        add_text(cpu, 'revision', 'r{}p{}'.format(0, 0))
        add_text(cpu, 'endian', 'little')
        add_text(cpu, 'mpuPresent', 'false')
        fpu = 'f' in self.cpu.march_parts
        add_text(cpu, 'fpuPresent', 'true' if fpu else 'false')
        add_text(cpu, 'nvicPrioBits', 0)
        add_text(cpu, 'vendorSystickConfig', 'true')

        peripherals = ET.SubElement(root, 'peripherals')
        tree = self.memory.get_resource_tree().children['io']

        for subtree in tree.children.values():
            p = ET.SubElement(peripherals, 'peripheral')

            add_text(p, 'name', identify_name(subtree.name))
            add_text(p, 'baseAddress', '0x{:08x}'.format(subtree.start))

            registers = ET.SubElement(p, 'registers')
            for reginfo, _ in subtree.walk():
                if not reginfo.resource:
                    continue

                reg = ET.SubElement(registers, 'register')

                leafname = '_'.join(reginfo.path[len(subtree.path):])
                add_text(reg, 'name', identify_name(leafname))
                add_text(reg, 'addressOffset', '0x{:x}'.format(reginfo.start - subtree.start))
                add_text(reg, 'size', 8 * reginfo.size)
                if reginfo.c_type:
                    add_text(reg, 'dataType', reginfo.c_type)

                if isinstance(reginfo.resource, amaranth_soc.csr.Register):
                    accessmap = dict(
                        r = 'read-only',
                        w = 'write-only',
                        rw = 'read-write',
                    )
                    access = accessmap.get(reginfo.resource.element.access.value)
                    if access:
                        add_text(reg, 'access', accessmap[reginfo.resource.element.access.value])
                    fields = None
                    field_start = 0
                    for fn, fv in reginfo.resource:
                        if not fn:
                            # whole register is field
                            break

                        if fields is None:
                            fields = ET.SubElement(reg, 'fields')

                        field_size = fv.port.shape.width
                        field_end = field_start + field_size

                        field = ET.SubElement(fields, 'field')

                        add_text(field, 'name', identify_name('_'.join(fn)))
                        add_text(field, 'bitRange', '[{}:{}]'.format(field_end - 1, field_start))
                        access = accessmap.get(fv.port.signature.access.value)
                        if access:
                            add_text(field, 'access', access)

                        field_start = field_end

        ET.indent(doc)
        with io.BytesIO() as f:
            doc.write(f, encoding='utf-8', xml_declaration=True)
            f.write(b'\n')
            return f.getvalue().decode('utf-8')
