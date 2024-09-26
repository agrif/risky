import contextlib
import math

import amaranth as am

import amaranth_soc.csr

import risky.memory

class Peripheral(risky.memory.MemoryComponent):
    @contextlib.contextmanager
    def register_builder(self):
        extra = int(math.ceil(math.log2(self.bus.data_width // 8)))
        builder = amaranth_soc.csr.Builder(addr_width=self.addr_width + extra, data_width=8)

        yield builder

        self.csr_bridge = amaranth_soc.csr.Bridge(builder.as_memory_map())
        self.wb_bridge = amaranth_soc.csr.wishbone.WishboneCSRBridge(self.csr_bridge.bus, data_width=self.bus.data_width)

        self.bus.memory_map = self.wb_bridge.wb_bus.memory_map

    def elaborate_registers(self, platform, m):
        m.submodules.csr_bridge = self.csr_bridge
        m.submodules.wb_bridge = self.wb_bridge
        am.lib.wiring.connect(m, am.lib.wiring.flipped(self.bus), self.wb_bridge.wb_bus)
