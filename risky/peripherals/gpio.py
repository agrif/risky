import amaranth as am

import risky.memory

class Output(risky.memory.MemoryComponent):
    def __init__(self, depth = 1):
        super().__init__(depth)

        self.regs = am.Array([am.Signal(32, name='reg{}'.format(i)) for i in range(depth)])

    def elaborate(self, platform):
        m = am.Module()

        mask = risky.memory.mask_from_en(self.bus.write_en)
        reg = self.regs[self.bus.addr]
        with m.If(self.bus.write_en.any()):
            m.d.sync += reg.eq((reg & ~mask) | (self.bus.write_data & mask))
        with m.If(self.bus.read_en):
            m.d.sync += self.bus.read_data.eq(reg)

        return m

