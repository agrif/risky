import amaranth as am
import amaranth.lib.enum

import amaranth_soc.csr
import amaranth_soc.csr.wishbone

import risky.csr

# https://gist.github.com/olofk/e91fba2572396f55525f8814f05fb33d
class Uart(risky.csr.Peripheral):
    class TxState(amaranth.lib.enum.Enum):
        IDLE = 0
        START = 1
        BITS = 2
        STOP = 3

    class Control(amaranth_soc.csr.Register, access='r'):
        tx_ready: amaranth_soc.csr.Field(amaranth_soc.csr.action.R, 1)

    class Rx(amaranth_soc.csr.Register, access='r'):
        data: amaranth_soc.csr.Field(amaranth_soc.csr.action.R, 8)

    class Tx(amaranth_soc.csr.Register, access='w'):
        data: amaranth_soc.csr.Field(amaranth_soc.csr.action.W, 8)

    def __init__(self, clk_freq_hz, baud_rate=115200):
        super().__init__(depth=1, signature={
            'tx': am.lib.wiring.Out(1),
        })

        self.clock_freq_hz = int(clk_freq_hz)
        self.baud_rate = int(baud_rate)
        self.output = am.Signal(8)

        with self.register_builder() as b:
            self.control = b.add('control', self.Control())
            self.rx_reg = b.add('rx', self.Rx())
            self.tx_reg = b.add('tx', self.Tx())

    def elaborate(self, platform):
        m = am.Module()

        self.elaborate_registers(platform, m)

        # control bits
        tx_ready = self.control.f.tx_ready.r_data

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

        # tx register
        with m.If(self.tx_reg.f.data.w_stb & tx_ready):
            char = self.tx_reg.f.data.w_data
            m.d.sync += am.Print(am.Format('{:c}', char), end='')
            m.d.sync += self.output.eq(char)
            m.d.sync += tx_bits.eq(7) # one less than total bits

        return m
