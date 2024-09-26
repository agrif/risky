import contextlib
import math

import amaranth as am

import amaranth_soc.csr

import risky.memory

class CSRBus(amaranth_soc.csr.Signature):
    def __init__(self, addr_width):
        super().__init__(addr_width=addr_width, data_width=8)

class Peripheral(am.lib.wiring.Component):
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
            'bus': am.lib.wiring.In(CSRBus(addr_width=self.addr_width)),
        }

        signature_with_bus.update(signature)
        super().__init__(signature_with_bus)

    @contextlib.contextmanager
    def register_builder(self):
        builder = amaranth_soc.csr.Builder(addr_width=self.addr_width, data_width=8)

        yield builder

        self.csr_bridge = amaranth_soc.csr.Bridge(builder.as_memory_map())
        self.bus.memory_map = self.csr_bridge.bus.memory_map

    def elaborate_registers(self, platform, m):
        m.submodules.csr_bridge = self.csr_bridge
        am.lib.wiring.connect(m, am.lib.wiring.flipped(self.bus), self.csr_bridge.bus)
