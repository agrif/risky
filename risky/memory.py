import collections
import contextlib
import dataclasses
import math
import struct

import amaranth as am
import amaranth.lib.memory
import amaranth.lib.enum

import amaranth_soc.wishbone
import amaranth_soc.csr.wishbone

def unpack_data(width, data):
    try:
        fmt = {
            8: 'B',
            16: 'H',
            32: 'I',
            64: 'Q',
        }[width]
    except KeyError:
        raise ValueError('unsupported data width: {}'.format(width))

    width_bytes = width // 8

    fmt = '<' + fmt
    while len(data) % width_bytes != 0:
        data += b'\0'

    return list(x[0] for x in struct.iter_unpack(fmt, data))

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

class CSRMap(MemoryComponent):
    def __init__(self, addr_width=24, alignment=0):
        super().__init__(addr_width=addr_width - 2)

        self.components = collections.OrderedDict()
        self.decoder = amaranth_soc.csr.Decoder(addr_width=addr_width, data_width=8, alignment=alignment)

    def align_to(self, alignment):
        self.decoder.align_to(alignment)

    def add(self, name, component, addr=None):
        self.decoder.add(component.bus, name=name, addr=addr)
        self.components[name] = component
        return component

    def finish_bridge(self):
        self.bridge = amaranth_soc.csr.wishbone.WishboneCSRBridge(self.decoder.bus, data_width=self.bus.data_width)
        self.bus.memory_map = self.bridge.wb_bus.memory_map

    def elaborate(self, platform):
        m = am.Module()

        m.submodules._decoder = self.decoder
        m.submodules._bridge = self.bridge
        am.lib.wiring.connect(m, am.lib.wiring.flipped(self.bus), self.bridge.wb_bus)

        for name, c in self.components.items():
            m.submodules[name] = c

        return m

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

    def add_rom(self, name, depth, addr=None, init=b''):
        parts = self.bus.data_width // self.bus.granularity
        word_depth = (depth + parts - 1) // parts

        init_words = unpack_data(self.bus.data_width, init)

        rom = Rom(depth=word_depth, init=init_words)
        return self.add(name, rom, addr=addr)

    def add_ram(self, name, depth, addr=None, init=b''):
        parts = self.bus.data_width // self.bus.granularity
        word_depth = (depth + parts - 1) // parts

        init_words = unpack_data(self.bus.data_width, init)

        ram = Ram(depth=word_depth, init=init_words)
        return self.add(name, ram, addr=addr)

    @contextlib.contextmanager
    def add_peripherals(self, name, **kwargs):
        submap = CSRMap(**kwargs)

        yield submap

        submap.finish_bridge()
        self.add(name, submap)

    def elaborate(self, platform):
        m = am.Module()

        m.submodules._decoder = self.decoder
        am.lib.wiring.connect(m, am.lib.wiring.flipped(self.bus), self.decoder.bus)

        for name, c in self.components.items():
            m.submodules[name] = c

        return m

    @dataclasses.dataclass
    class ResourceNode:
        path: list[str]
        start: int
        end: int
        offset: int

        resource: am.lib.wiring.Component = None
        children: collections.OrderedDict = dataclasses.field(default_factory=collections.OrderedDict)

        def walk(self, topdown=True):
            children = self.children.copy()
            if topdown:
                yield (self, children)
            for n in children.values():
                yield from n.walk(topdown=topdown)
            if not topdown:
                yield (self, children)

        @property
        def size(self):
            return self.end - self.start

        @property
        def name(self):
            return self.path[-1] if self.path else None

        @property
        def c_type(self):
            size = self.size
            if size == 1:
                return 'uint8_t'
            elif size == 2:
                return 'uint16_t'
            elif size == 4:
                return 'uint32_t'
            else:
                return None

        @property
        def memory_x_access(self):
            if self.resource:
                return getattr(self.resource, 'memory_x_access', None)

            accesses = set(n.memory_x_access for n in self.children.values())
            if len(accesses) == 1:
                access, = accesses
                return access
            return None

    def get_resource_tree(self):
        tree = {}
        for r in self.bus.memory_map.all_resources():
            path = [str(p) for part in r.path for p in part]
            leaf = tree
            for i, part in enumerate(path[:-1]):
                _, leaf = leaf.setdefault(part, (path[:i + 1], {}))
            leaf[path[-1]] = (path, r)

        def reify_tree(path, t):
            if isinstance(t, dict):
                children = []
                start = None
                end = None
                for k, (subpath, subtree) in t.items():
                    r = reify_tree(subpath, subtree)
                    if start is None or r.start < start:
                        start = r.start
                    if end is None or r.end > end:
                        end = r.end

                    children.append((k, r))

                # update offset
                for _, n in children:
                    n.offset = n.start - start

                children.sort(key=lambda t: t[1].start)
                children = collections.OrderedDict(children)

                return self.ResourceNode(path=path, start=start, end=end, offset=start, children=children)
            else:
                return self.ResourceNode(path=path, start=t.start, end=t.end, offset=t.start, resource=t.resource)

        return reify_tree([], tree)

    def __getitem__(self, addr):
        r = self.bus.memory_map.decode_address(addr)
        if not r:
            raise KeyError('address 0x{:08x} does not map to a component'.format(addr))

        if not isinstance(r, MemoryComponent):
            raise KeyError('address 0x{:08x} does not support simulation reads'.format(addr))

        info = self.bus.memory_map.find_resource(r)
        internal_addr = addr - info.start
        return r[internal_addr]

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
        return self.memory.data[addr >> (self.bus.memory_map.addr_width - self.bus.addr_width)]

    def set_data(self, data):
        data_words = unpack_data(self.bus.data_width, data)
        self.memory.data.init = data_words

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
        return self.memory.data[addr >> (self.bus.memory_map.addr_width - self.bus.addr_width)]

    def set_data(self, data):
        data_words = unpack_data(self.bus.data_width, data)
        self.memory.data.init = data_words
