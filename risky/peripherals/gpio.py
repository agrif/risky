import amaranth as am
import amaranth_soc.csr
import amaranth_soc.csr.wishbone

import risky.csr

class Output(risky.csr.Peripheral):
    class Register(amaranth_soc.csr.Register, access='rw'):
        value: amaranth_soc.csr.Field(amaranth_soc.csr.action.RW, 32)

    def __init__(self, depth = 1):
        super().__init__(depth)

        with self.register_builder() as b:
            self.regs = [b.add('{}'.format(i), self.Register()) for i in range(depth)]

        self.output = am.Array([am.Signal(32, name='output{}'.format(i)) for i in range(depth)])

    def elaborate(self, platform):
        m = am.Module()

        self.elaborate_registers(platform, m)

        for reg, out in zip(self.regs, self.output):
            m.d.comb += out.eq(reg.f.value.data)

        return m

