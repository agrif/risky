import amaranth as am

import risky.clockworks
import risky.soc

class Demo(am.Elaboratable):
    def __init__(self, sources):
        super().__init__()
        self.sources = sources

    def elaborate(self, platform):
        m = am.Module()

        freq = platform.default_clk_frequency
        #m.submodules.clockworks = clockworks = risky.clockworks.Clockworks('slow', platform.default_clk_frequency, 1_000_000)
        #m.domains += clockworks.domain
        #freq = clockworks.out_freq

        soc = risky.soc.Soc.with_autodetect(freq, *self.sources)
        #soc = am.DomainRenamer('slow')(soc)
        m.submodules.soc = soc

        debugreg = soc.output.output[0]
        
        for i in debugreg:
            try:
                led = platform.request('led', i.start)
            except am.build.ResourceError:
                break
            m.d.comb += led.o.eq(i)

        uart = platform.request('uart')
        m.d.comb += [
            soc.rx.eq(uart.rx.i),
            uart.tx.o.eq(soc.tx),
        ]

        spi = platform.request('spi_flash_1x')
        m.d.comb += [
            spi.cs.o.eq(soc.spi_cs),
            spi.clk.o.eq(soc.sclk),
            spi.copi.o.eq(soc.copi),
            soc.cipo.eq(spi.cipo.i),
        ]

        return m
