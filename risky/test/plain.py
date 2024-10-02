import risky.soc
import risky.test

class Plain(risky.test.Simulated):
    def __init__(self, sources, cycles=None):
        self.sources = sources
        self.cycles = cycles

        super().__init__()

    def construct(self):
        dut = risky.soc.Soc.with_autodetect(self.clk_freq, *self.sources)
        return dut

    async def testbench(self, ctx):
        if self.cycles is not None:
            await ctx.tick().repeat(self.cycles)
        else:
            while True:
                await ctx.tick().repeat(100)
