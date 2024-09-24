import math

import amaranth as am

class Clockworks(am.Elaboratable):
    def __init__(self, name, in_freq, out_freq):
        super().__init__()

        self.in_freq = in_freq

        # divide by twos because we toggle output clk,
        # so we need two toggles for one clock
        self.divisor = (int(in_freq) // 2) // out_freq
        self.out_freq = (in_freq / self.divisor) / 2

        self.count = am.Signal(am.Shape.cast(range(self.divisor)).width + 1)
        self.domain = am.ClockDomain(name)

    def elaborate(self, platform):
        m = am.Module()

        m.domains += self.domain

        with m.If(self.count[-1]):
            # -2 because we count from divisor - 2 to -1, inclusive
            # which is divisor counts total
            m.d.sync += self.count.eq(self.divisor - 2)
            m.d.sync += self.domain.clk.eq(~self.domain.clk)
        with m.Else():
            m.d.sync += self.count.eq(self.count - 1)

        m.d.comb += self.domain.rst.eq(am.ResetSignal())

        return m
