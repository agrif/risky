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
    def __init__(self, sources, cycles=None):
        self.sources = sources
        self.cycles = cycles

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

    async def testbench_inner(self, ctx):
        cycle = 0
        inq = self.input_queue()

        ctx.set(self.dut.rx, 1)

        baud = 115200
        delay = am.Period(s=1 / baud)

        while self.cycles is None or cycle < self.cycles:
            await ctx.tick()
            cycle += 1

            if not inq.empty():
                c = inq.get()

                bits = [1 if b == '1' else 0 for b in '{:08b}'.format(ord(c))]
                bits.reverse()
                bits = [0] + bits + [1]

                # FIXME count cycles in this
                for b in bits:
                    ctx.set(self.dut.rx, b)
                    await ctx.delay(delay)
