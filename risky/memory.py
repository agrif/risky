import collections
import math

import amaranth as am
import amaranth.lib.memory
import amaranth.lib.enum

import amaranth_soc.wishbone

class MemoryBus(amaranth_soc.wishbone.Signature):
    def __init__(self, addr_width=30):
        super().__init__(addr_width=addr_width, data_width=32, granularity=8)

class MemoryComponent(am.lib.wiring.Component):
    memory_x_access = None

    def __init__(self, depth=None, addr_width=None, signature={}):
        if depth is None and addr_width is None:
            raise ValueError('must specify one of depth, addr_width')
        if depth is None:
            depth = 1 << addr_width
        if addr_width is None:
            addr_width = int(math.ceil(math.log2(depth)))

        self.depth = depth
        self.addr_width = addr_width

        # add bus to it, but let it override bus if it does
        signature_with_bus = {
            'bus': am.lib.wiring.In(MemoryBus(addr_width=self.addr_width)),
        }

        signature_with_bus.update(signature)
        super().__init__(signature_with_bus)

        # create a default memory map
        # careful -- memory map is addressed in self.bus.granularity
        extra = int(math.ceil(math.log2(self.bus.data_width // self.bus.granularity)))
        self.bus.memory_map = amaranth_soc.memory.MemoryMap(addr_width=self.addr_width + extra, data_width=self.bus.granularity)
        self.bus.memory_map.add_resource(self, name='data', size=self.depth * (1 << extra))

    def __getitem__(self, addr):
        raise RuntimeError('memory component {} does not support simulation access'.format(self.__class__.__name__))

class MemoryMap(MemoryComponent):
    def __init__(self, addr_width=30, alignment=0):
        super().__init__(addr_width=addr_width)
        self.components = collections.OrderedDict()
        self.decoder = amaranth_soc.wishbone.Decoder(addr_width=self.bus.addr_width, data_width=self.bus.data_width, granularity=self.bus.granularity, alignment=alignment)
        self.bus.memory_map = self.decoder.bus.memory_map

    def align_to(self, alignment):
        self.decoder.align_to(alignment)

    def add(self, name, component, addr=None):
        self.decoder.add(component.bus, name=name, addr=addr)
        self.components[name] = component
        return component

    def add_rom(self, name, depth, addr=None, init=[]):
        parts = self.bus.data_width // self.bus.granularity
        word_depth = (depth + parts - 1) // parts

        rom = Rom(depth=word_depth, init=init)
        return self.add(name, rom, addr=addr)

    def add_ram(self, name, depth, addr=None, init=[]):
        parts = self.bus.data_width // self.bus.granularity
        word_depth = (depth + parts - 1) // parts

        ram = Ram(depth=word_depth, init=init)
        return self.add(name, ram, addr=addr)

    def elaborate(self, platform):
        m = am.Module()

        m.submodules += self.decoder
        am.lib.wiring.connect(m, am.lib.wiring.flipped(self.bus), self.decoder.bus)

        for name, c in self.components.items():
            m.submodules[name] = c

        return m

    def generate_memory_x(self):
        memory_x = 'MEMORY\n{\n'
        for submap, name, (start, end, _) in self.bus.memory_map.windows():
            name, = name
            component = self.components.get(name)
            access = getattr(component, 'memory_x_access')
            if access is None:
                continue

            length = max(r.end for r in submap.all_resources())
            memory_x += '    {} ({}) : ORIGIN = 0x{:x}, LENGTH = {}\n'.format(
                name.upper(),
                access,
                start,
                length,
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

        for r in self.bus.memory_map.all_resources():
            name = '_'.join('_'.join(str(p) for p in part) for part in r.path)
            name = name.upper()

            size = r.end - r.start
            typ = None
            if size == 1:
                typ = 'uint8_t'
            elif size == 2:
                typ = 'uint16_t'
            elif size == 4:
                typ = 'uint32_t'

            h += '#define {:<40} 0x{:08x}\n'.format(name + '_ADDR', r.start)
            h += '#define {:<40} 0x{:08x}\n'.format(name + '_SIZE', size)
            if typ:
                h += '#define {0:<40} (*(volatile {1} *){0}_ADDR)\n'.format(name, typ)
            # FIXME fields
            h += '\n'

        h += '#endif /* __RISKY_H_INCLUDED */\n'
        return h

    def __getitem__(self, addr):
        addr = (addr >> 2) << 2
        for e in self.entries:
            if ((addr >> 2) & ~e.mask) == e.base:
                return e.component[addr & e.mask]

        raise KeyError('address 0x{:08x} does not map to a component'.format(addr))

# unfortunately quartus does not infer memory with byte enables correctly
# so we must fake one with an async read + sync write
class Ram(MemoryComponent):
    memory_x_access = 'rwx'

    class RamState(am.lib.enum.Enum):
        READ = 0
        WRITE = 1

    def __init__(self, init=[], depth=None):
        if depth is None:
            depth = len(init)

        super().__init__(depth=depth)

        self.memory = am.lib.memory.Memory(shape=self.bus.data_width, depth=depth, init=init)
        self.depth = depth

    def elaborate(self, platform):
        m = am.Module()

        m.submodules.memory = self.memory

        read = self.memory.read_port(domain='sync')
        write = self.memory.write_port(domain='sync')

        state = am.Signal(self.RamState)
        do_write = self.bus.cyc & self.bus.we & self.bus.stb
        do_read = self.bus.cyc & ~self.bus.we & self.bus.stb

        mask = am.Cat(*(am.Mux(bit, 0xff, 0) for bit in self.bus.sel))
        internal_write_data = (read.data & ~mask) | (self.bus.dat_w & mask)

        m.d.comb += [
            read.addr.eq(self.bus.adr),
            self.bus.dat_r.eq(read.data),
            read.en.eq(do_read | ((state == self.RamState.READ) & do_write)),

            write.addr.eq(self.bus.adr),
            write.data.eq(internal_write_data),
            write.en.eq((state == self.RamState.WRITE) & do_write),
        ]

        # default to 0
        m.d.sync += self.bus.ack.eq(0)

        with m.If(do_read & ~self.bus.ack):
            m.d.sync += self.bus.ack.eq(1)

        with m.Switch(state):
            with m.Case(self.RamState.READ):
                with m.If(do_write):
                    m.d.sync += [
                        state.eq(self.RamState.WRITE),
                        self.bus.ack.eq(1),
                    ]
            with m.Case(self.RamState.WRITE):
                m.d.sync += state.eq(self.RamState.READ)

        return m

    def __getitem__(self, addr):
        return self.memory.data[addr >> 2]

class Rom(MemoryComponent):
    memory_x_access = 'rx'

    def __init__(self, init=[], depth=None):
        if depth is None:
            depth = len(init)

        super().__init__(depth=depth)

        self.memory = am.lib.memory.Memory(shape=self.bus.data_width, depth=depth, init=init)
        self.depth = depth

    def elaborate(self, platform):
        m = am.Module()

        m.submodules.memory = self.memory

        read = self.memory.read_port(domain='sync')

        do_write = self.bus.cyc & self.bus.we & self.bus.stb
        do_read = self.bus.cyc & ~self.bus.we & self.bus.stb

        m.d.comb += [
            read.addr.eq(self.bus.adr),
            self.bus.dat_r.eq(read.data),
            read.en.eq(do_read),
        ]

        m.d.sync += self.bus.ack.eq((do_read | do_write) & ~self.bus.ack)

        return m

    def __getitem__(self, addr):
        return self.memory.data[addr >> 2]
