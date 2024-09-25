import dataclasses

import amaranth as am
import amaranth.lib.memory
import amaranth.lib.enum

import amaranth_soc.wishbone

def mask_from_sel(sel):
    return am.Cat(*(am.Mux(bit, 0xff, 0) for bit in sel))

class MemoryBus(amaranth_soc.wishbone.Signature):
    def __init__(self):
        super().__init__(addr_width=30, data_width=32, granularity=8)

class MemoryComponent(am.lib.wiring.Component):
    bus: am.lib.wiring.In(MemoryBus())

    def __init__(self, depth):
        super().__init__()
        self.depth = depth

    def __getitem__(self, addr):
        raise RuntimeError('memory component {} does not support simulation access'.format(self.__class__.__name__))

class MemoryMap(MemoryComponent):
    @dataclasses.dataclass
    class Entry:
        name: str
        base: int
        mask: int
        component: MemoryComponent

    def __init__(self):
        super().__init__(0)
        self.entries = []

    def add(self, name, base, mask, component):
        if base & 0x3 > 0 or mask & 0x3 != 0x3:
            raise ValueError('base and mask must be word-aligned')

        # convert to word addressing
        base = base >> 2
        mask = mask >> 2

        if bin(mask + 1).count('1') != 1:
            raise ValueError('mask must have all ones in LSBs')

        if base & ~mask != base:
            raise ValueError('base and mask incompatible')

        if component.depth > mask + 1:
            raise ValueError('component too large for mask')

        for other in self.entries:
            other_above = other.base > base + mask
            other_below = base > other.base + other.mask
            if not (other_above or other_below):
                raise ValueError('conflict with map {!r} from 0x{:08x} - 0x{:08x}'.format(other.name, other.base << 2, ((other.base + other.mask) << 2) + 0x3))

        self.entries.append(self.Entry(
            name=name,
            base=base,
            mask=mask,
            component=component,
        ))

        self.depth = max(e.base + e.mask + 1 for e in self.entries)

    def add_rom(self, name, base, mask, init=[]):
        rom = Rom(depth=(mask + 1) >> 2, init=init)
        self.add(name, base, mask, rom)
        return rom

    def add_ram(self, name, base, mask, init=[]):
        ram = Ram(depth=(mask + 1) >> 2, init=init)
        self.add(name, base, mask, ram)
        return ram

    def elaborate(self, platform):
        m = am.Module()

        # do all the stuff that is always on
        for e in self.entries:
            m.submodules[e.name] = e.component

            m.d.comb += [
                e.component.bus.adr.eq(self.bus.adr & e.mask),
                e.component.bus.dat_w.eq(self.bus.dat_w),
            ]

        # latch the read output address so it stays put even if adr changes
        read_adr = am.Signal(self.bus.adr.shape())

        # handy start to if / elif / else chain
        with m.If(0):
            pass
        for e in self.entries:
            with m.Elif(self.bus.adr & ~e.mask == e.base):
                m.d.comb += [
                    e.component.bus.sel.eq(self.bus.sel),
                    e.component.bus.cyc.eq(self.bus.cyc),
                    e.component.bus.stb.eq(self.bus.stb),
                    e.component.bus.we.eq(self.bus.we),
                    self.bus.ack.eq(e.component.bus.ack),
                ]

                # save this address to set dat_r later
                with m.If(self.bus.cyc & self.bus.stb & self.bus.ack & ~self.bus.we):
                    m.d.sync += read_adr.eq(self.bus.adr)
        with m.Else():
            # don't stall, just yield garbage
            m.d.comb += self.bus.ack.eq(self.bus.cyc & self.bus.stb)

        # use saved address to set dat_r
        with m.If(0):
            pass
        for e in self.entries:
            with m.Elif(read_adr & ~e.mask == e.base):
                m.d.comb += self.bus.dat_r.eq(e.component.bus.dat_r)

        return m

    def __getitem__(self, addr):
        addr = (addr >> 2) << 2
        for e in self.entries:
            if ((addr >> 2) & ~e.mask) == e.base:
                return e.component[addr & e.mask]

        raise KeyError('address 0x{:08x} does not map to a component'.format(addr))

# unfortunately quartus does not infer memory with byte enables correctly
# so we must fake one with an async read + sync write
class Ram(MemoryComponent):
    class RamState(am.lib.enum.Enum):
        READ = 0
        WRITE = 1

    def __init__(self, init=[], depth=None):
        if depth is None:
            depth = len(init)

        super().__init__(depth)

        self.memory = am.lib.memory.Memory(shape=32, depth=depth, init=init)
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

            self.bus.ack.eq(do_read | ((state == self.RamState.WRITE) & do_write)),
        ]

        with m.Switch(state):
            with m.Case(self.RamState.READ):
                with m.If(do_write):
                    m.d.sync += state.eq(self.RamState.WRITE)
            with m.Case(self.RamState.WRITE):
                m.d.sync += state.eq(self.RamState.READ)

        return m

    def __getitem__(self, addr):
        return self.memory.data[addr >> 2]

class Rom(MemoryComponent):
    def __init__(self, init=[], depth=None):
        if depth is None:
            depth = len(init)

        super().__init__(depth)

        self.memory = am.lib.memory.Memory(shape=32, depth=depth, init=init)
        self.depth = depth

    def elaborate(self, platform):
        m = am.Module()

        m.submodules.memory = self.memory

        read = self.memory.read_port(domain='sync')

        do_write = self.bus.cyc & self.bus.we & self.bus.stb
        do_read = self.bus.cyc & ~self.bus.we & self.bus.stb

        m.d.comb += [
            read.addr.eq(self.bus.adr),
            self.bus.ack.eq(do_read | do_write),
            self.bus.dat_r.eq(read.data),
            read.en.eq(do_read),
        ]

        return m

    def __getitem__(self, addr):
        return self.memory.data[addr >> 2]
