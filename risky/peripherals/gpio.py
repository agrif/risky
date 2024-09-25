import amaranth as am

import risky.memory

class Output(risky.memory.MemoryComponent):
    def __init__(self, depth = 1):
        super().__init__(depth)

        self.regs = am.Array([am.Signal(32, name='reg{}'.format(i)) for i in range(depth)])

    def elaborate(self, platform):
        m = am.Module()

        do_write = self.bus.cyc & self.bus.we & self.bus.stb
        do_read = self.bus.cyc & ~self.bus.we & self.bus.stb
        m.d.comb += self.bus.ack.eq(do_read | do_write)

        mask = risky.memory.mask_from_sel(self.bus.sel)
        reg = self.regs[self.bus.adr]
        with m.If(do_write):
            m.d.sync += reg.eq((reg & ~mask) | (self.bus.dat_w & mask))
        with m.If(do_read):
            m.d.sync += self.bus.dat_r.eq(reg)

        return m

