import atexit
import curses
import sys
import threading
import traceback
import queue

import amaranth as am

import risky.soc
import risky.test

class Plain(risky.test.Simulated):
    def __init__(self, sources, cycles=None, boot=True):
        self.sources = sources
        self.cycles = cycles
        self.boot = boot

        super().__init__()

    def construct(self):
        dut = risky.soc.Soc.with_autodetect(self.clk_freq, *self.sources)
        return dut

    def input_queue(self):
        q = queue.Queue()

        curses.initscr()
        atexit.register(curses.endwin)

        curses.cbreak()
        curses.noecho()

        stdin = open(sys.stdin.fileno(), 'rb')

        def input_thread():
            while True:
                q.put(stdin.read(1))

        thread = threading.Thread(target=input_thread, daemon=True)
        thread.start()

        return q

    async def testbench(self, ctx):
        # wrap an inner function
        # because curses screws with tracebacks
        try:
            await self.testbench_inner(ctx)
        except Exception as e:
            try:
                curses.endwin()
            except Exception:
                pass
            traceback.print_exc()

    async def send_data(self, ctx, data, baud=115200):
        divisor = (self.dut.clk_freq + (baud // 2)) // baud

        cycles = 0
        for b in data:
            bits = [1 if bit == '1' else 0 for bit in '{:08b}'.format(b)]
            bits.reverse()
            bits = [0] + bits + [1]

            for bit in bits:
                ctx.set(self.dut.rx, bit)

                # use ctx.tick() here and not ctx.delay to count ticks
                # but also because we run the simulator too close to baud
                # for the uart to reliably work
                await ctx.tick().repeat(divisor)
                cycles += divisor

        return cycles

    async def testbench_inner(self, ctx):
        cycle = 0

        inq = self.input_queue()
        ctx.set(self.dut.rx, 1)

        if self.boot:
            # wait until the bootloader is alive
            while ctx.get(self.dut.tx) > 0:
                await ctx.tick()
                cycle += 1

            # send boot command
            cycle += await self.send_data(ctx, b'b\n')

        while self.cycles is None or cycle < self.cycles:
            await ctx.tick()
            cycle += 1

            if not inq.empty():
                c = inq.get()
                cycle += await self.send_data(ctx, c)
