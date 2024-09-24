import amaranth as am
import amaranth.lib.enum

import risky.memory

# https://gist.github.com/olofk/e91fba2572396f55525f8814f05fb33d
class Uart(risky.memory.MemoryComponent):
    tx: am.lib.wiring.Out(1)

    class TxState(amaranth.lib.enum.Enum):
        IDLE = 0
        START = 1
        BITS = 2
        STOP = 3

    def __init__(self, clk_freq_hz, baud_rate=115200):
        super().__init__(3)

        self.clock_freq_hz = int(clk_freq_hz)
        self.baud_rate = int(baud_rate)

        self.control = am.Signal(32)
        self.input = am.Signal(32)
        self.output = am.Signal(32)

    def elaborate(self, platform):
        m = am.Module()

        # control bits
        tx_ready = self.control[0]

        divisor = self.clock_freq_hz // self.baud_rate
        count = am.Signal(am.Shape.cast(range(divisor)).width + 1)
        tx_state = am.Signal(self.TxState)
        tx_bits = am.Signal(4)

        # set count to divisor - 2, count down, baud ticks on count[-1]
        # (divisor - 2 because we count down up to and including -1)
        baud = count[-1]
        with m.If(baud):
            m.d.sync += count.eq(divisor - 2)
        with m.Else():
            m.d.sync += count.eq(count - 1)

        # idle state means tx is ready
        m.d.comb += tx_ready.eq((tx_state == self.TxState.IDLE) & ~tx_bits.any())

        # tx state machine
        with m.Switch(tx_state):
            with m.Case(self.TxState.IDLE):
                m.d.comb += self.tx.eq(1)
                with m.If(baud & tx_bits.any()):
                    m.d.sync += tx_state.eq(self.TxState.START)

            with m.Case(self.TxState.START):
                m.d.comb += self.tx.eq(0)
                with m.If(baud):
                    m.d.sync += tx_state.eq(self.TxState.BITS)

            with m.Case(self.TxState.BITS):
                m.d.comb += self.tx.eq(self.output[0])
                with m.If(baud):
                    with m.If(tx_bits.any()):
                        m.d.sync += self.output.eq(self.output >> 1)
                        m.d.sync += tx_bits.eq(tx_bits - 1)
                    with m.Else():
                        m.d.sync += tx_state.eq(self.TxState.STOP)

            with m.Case(self.TxState.STOP):
                m.d.comb += self.tx.eq(1)
                with m.If(baud):
                    m.d.sync += tx_state.eq(self.TxState.IDLE)

        # memory reads
        with m.If(self.bus.read_en):
            with m.Switch(self.bus.addr):
                with m.Case(0):
                    m.d.sync += self.bus.read_data.eq(self.control)
                with m.Case(1):
                    m.d.sync += self.bus.read_data.eq(self.input)
                with m.Case(2):
                    m.d.sync += self.bus.read_data.eq(self.output)

        # memory writes
        with m.If(self.bus.write_en.any()):
            mask = risky.memory.mask_from_en(self.bus.write_en)
            with m.Switch(self.bus.addr):
                with m.Case(0):
                    pass
                with m.Case(1):
                    pass
                with m.Case(2):
                    with m.If(tx_ready):
                        char = self.bus.write_data & mask
                        m.d.sync += am.Print(am.Format('{:c}', char), end='')
                        m.d.sync += self.output.eq(char)
                        m.d.sync += tx_bits.eq(7) # one less than total bits

        return m
