import amaranth as am
import amaranth.lib.enum
import amaranth.lib.fifo

import amaranth_soc.csr

import risky.csr

class Unbuffered(am.lib.wiring.Component):
    rx: am.lib.wiring.In(1)
    tx: am.lib.wiring.Out(1)

    # load up with (number of clk cycles per bit) - 1
    divisor: am.lib.wiring.In(32, init=-1)
    divisor_stb: am.lib.wiring.In(1)

    rx_data: am.lib.wiring.Out(8)
    rx_stb: am.lib.wiring.Out(1)

    tx_ready: am.lib.wiring.Out(1)
    tx_data: am.lib.wiring.In(8)
    tx_stb: am.lib.wiring.In(1)

    class State(amaranth.lib.enum.Enum):
        IDLE = 0
        START = 1
        BITS = 2
        STOP = 3

    def elaborate(self, platform):
        m = am.Module()

        # clock divider
        count = am.Signal(33)
        baud = count[-1]
        reset_count = am.Signal(1)

        with m.If(baud | self.divisor_stb):
            m.d.comb += reset_count.eq(1)

        # divisor - 1 is (clk cycles per bit) - 2
        # -2 because we pass through 0 *and* -1 before resetting
        m.d.sync += count.eq(am.Mux(reset_count, self.divisor, count) - 1)

        # tx load
        tx_shift = am.Signal(8)
        tx_bits = am.Signal(range(8))
        with m.If(self.tx_ready & self.tx_stb):
            m.d.sync += [
                tx_shift.eq(self.tx_data),
                tx_bits.eq(7), # one less than total data bits

                am.Print(am.Format('{:c}', self.tx_data), end=''),
            ]

        # tx state machine
        tx_state = am.Signal(self.State)
        with m.Switch(tx_state):
            with m.Case(self.State.IDLE):
                m.d.comb += [
                    self.tx.eq(1),
                    self.tx_ready.eq(~tx_bits.any()),
                ]
                with m.If(baud & tx_bits.any()):
                    m.d.sync += tx_state.eq(self.State.START)

            with m.Case(self.State.START):
                m.d.comb += self.tx.eq(0)
                with m.If(baud):
                    m.d.sync += tx_state.eq(self.State.BITS)

            with m.Case(self.State.BITS):
                m.d.comb += self.tx.eq(tx_shift[0])
                with m.If(baud):
                    with m.If(tx_bits.any()):
                        m.d.sync += [
                            tx_shift.eq(tx_shift >> 1),
                            tx_bits.eq(tx_bits - 1),
                        ]
                    with m.Else():
                        m.d.sync += tx_state.eq(self.State.STOP)

            with m.Case(self.State.STOP):
                m.d.comb += self.tx.eq(1)
                with m.If(baud):
                    m.d.sync += tx_state.eq(self.State.IDLE)

        # rx state
        rx_state = am.Signal(self.State)

        with m.If(self.divisor_stb):
            m.d.sync += rx_state.eq(self.State.IDLE)

        # rx clock divider
        half_count = am.Signal(33)
        half_baud = half_count[-1]
        reset_half_count = am.Signal(1)

        with m.If(half_baud | self.divisor_stb | rx_state.matches(self.State.IDLE)):
            m.d.comb += reset_half_count.eq(1)

        # count half-baud edges
        half_edge = am.Signal(1)
        with m.If(half_baud):
            m.d.sync += half_edge.eq(~half_edge)

        # middle of the bit
        mid_bit = half_baud & ~half_edge

        # divisor + 2 is (clk cycles per bit) + 1
        # so (divisor + 2) >> 1 is (clk cycles per bit + 1) / 2
        # (recall: (a + 1) / 2 is a/2, rounded)
        # and ((divisor + 2) >> 1) - 2 == (divisor >> 1) - 1
        # so (divisor >> 1) - 1 is ((clk cycles per bit + 1) / 2) - 2
        # -2 because we pass through 0 *and* -1 before resetting
        # this resets twice per (clk cycles per bit) cycles
        # fiddly bit to deal with odd (clk cycles per bit)
        half_reset_value = am.Mux(~half_edge, self.divisor >> 1, (self.divisor >> 1) - ~self.divisor[0])
        m.d.sync += half_count.eq(am.Mux(reset_half_count, half_reset_value, half_count) - 1)

        # synchronize input
        rx_safe = am.Signal(1)
        m.submodules.rx_sync = amaranth.lib.cdc.FFSynchronizer(i=self.rx, o=rx_safe, init=1)

        # count bits we need to read
        rx_bits = am.Signal(range(8))

        # rx state machine
        with m.Switch(rx_state):
            with m.Case(self.State.IDLE):
                with m.If(~rx_safe):
                    # candidate start bit
                    m.d.sync += [
                        half_edge.eq(0),
                        rx_state.eq(self.State.START),
                    ]

            with m.Case(self.State.START):
                # look for start bit
                with m.If(mid_bit):
                    with m.If(~rx_safe):
                        # found a start bit, start shifting in bits
                        m.d.sync += [
                            rx_bits.eq(7), # one less than number to read
                            rx_state.eq(self.State.BITS),
                        ]
                    with m.Else():
                        # no start bit, reset
                        m.d.sync += rx_state.eq(self.State.IDLE)

            with m.Case(self.State.BITS):
                # shift in bits
                with m.If(mid_bit):
                    m.d.sync += [
                        self.rx_data.eq(am.Cat((self.rx_data >> 1)[:-1], rx_safe)),
                        rx_bits.eq(rx_bits - 1),
                    ]

                    with m.If(~rx_bits.any()):
                        m.d.sync += rx_state.eq(self.State.STOP)

            with m.Case(self.State.STOP):
                # look for stop bit
                with m.If(mid_bit):
                    with m.If(rx_safe):
                        # found a stop bit, strobe read
                        # rx_safe is registered to clock, so this is stable
                        m.d.comb += self.rx_stb.eq(1)
                    with m.Else():
                        # no stop bit, possibly an error condition
                        pass

                    # in either case, reset
                    m.d.sync += rx_state.eq(self.State.IDLE)

        return m

class Buffered(am.lib.wiring.Component):
    def __init__(self, depth):
        super().__init__({
            'rx': am.lib.wiring.In(1),
            'tx': am.lib.wiring.Out(1),

            # load up with (number of clk cycles per bit) - 1
            'divisor': am.lib.wiring.In(32, init=-1),
            'divisor_stb': am.lib.wiring.In(1),

            'rx_ready': am.lib.wiring.Out(1),
            'rx_data': am.lib.wiring.Out(8),
            'rx_stb': am.lib.wiring.In(1),
            'rx_level': am.lib.wiring.Out(range(depth + 1)),

            'tx_ready': am.lib.wiring.Out(1),
            'tx_data': am.lib.wiring.In(8),
            'tx_stb': am.lib.wiring.In(1),
            'tx_level': am.lib.wiring.Out(range(depth + 1)),
        })

        self.depth = depth

    def elaborate(self, platform):
        m = am.Module()

        m.submodules.unbuffered = unbuffered = Unbuffered()

        # buffered FIFOs as 1 clock cycle latency is nothing compared to baud
        m.submodules.rx_fifo = rx_fifo = am.lib.fifo.SyncFIFOBuffered(width=8, depth=self.depth)
        m.submodules.tx_fifo = tx_fifo = am.lib.fifo.SyncFIFOBuffered(width=8, depth=self.depth)

        m.d.comb += [
            self.tx.eq(unbuffered.tx),
            unbuffered.rx.eq(self.rx),

            unbuffered.divisor.eq(self.divisor),
            unbuffered.divisor_stb.eq(self.divisor_stb),

            # rx fifo input
            rx_fifo.w_data.eq(unbuffered.rx_data),
            rx_fifo.w_en.eq(unbuffered.rx_stb & rx_fifo.w_rdy),

            # rx fifo output
            self.rx_ready.eq(rx_fifo.r_rdy),
            self.rx_data.eq(rx_fifo.r_data),
            rx_fifo.r_en.eq(self.rx_stb),

            # tx fifo input
            tx_fifo.w_data.eq(self.tx_data),
            self.tx_ready.eq(tx_fifo.w_rdy),
            tx_fifo.w_en.eq(self.tx_stb),

            # tx fifo output
            unbuffered.tx_data.eq(tx_fifo.r_data),
            unbuffered.tx_stb.eq(tx_fifo.r_rdy & unbuffered.tx_ready),
            tx_fifo.r_en.eq(unbuffered.tx_stb),

            # levels
            self.rx_level.eq(rx_fifo.level),
            self.tx_level.eq(tx_fifo.level),
        ]

        return m

class Peripheral(risky.csr.Peripheral):
    class FifoInfo(amaranth_soc.csr.Register, access='r'):
        ready: amaranth_soc.csr.Field(amaranth_soc.csr.action.R, 1)
        level: amaranth_soc.csr.Field(amaranth_soc.csr.action.R, 6)
        empty: amaranth_soc.csr.Field(amaranth_soc.csr.action.R, 1)
        full: amaranth_soc.csr.Field(amaranth_soc.csr.action.R, 1)
        max: amaranth_soc.csr.Field(amaranth_soc.csr.action.R, 7)

    class Baud(amaranth_soc.csr.Register, access='rw'):
        def __init__(self):
            super().__init__(
                amaranth_soc.csr.Field(amaranth_soc.csr.action.RW, 32, init=-1),
            )

    class Rx(amaranth_soc.csr.Register, access='r'):
        def __init__(self):
            super().__init__(
                amaranth_soc.csr.Field(amaranth_soc.csr.action.R, 8),
            )

    class Tx(amaranth_soc.csr.Register, access='w'):
        def __init__(self):
            super().__init__(
                amaranth_soc.csr.Field(amaranth_soc.csr.action.W, 8),
            )

    def __init__(self, fifo_depth=8):
        if fifo_depth > (1 << 6): # 64
            raise ValueError('fifo_depth cannot be more than {}'.format(1 << 6))
        elif fifo_depth < 0:
            raise ValueError('fifo_depth must be at least 0')

        self.fifo_depth = fifo_depth

        super().__init__(depth=10, signature={
            'rx': am.lib.wiring.In(1),
            'tx': am.lib.wiring.Out(1),
        })

        with self.register_builder() as b:
            self.rx_control = b.add('rx_control', self.FifoInfo())
            self.tx_control = b.add('tx_control', self.FifoInfo())
            self.baud = b.add('baud', self.Baud())
            self.rx_reg = b.add('rx', self.Rx())
            self.tx_reg = b.add('tx', self.Tx())

    def elaborate(self, platform):
        m = am.Module()

        self.elaborate_registers(platform, m)

        if self.fifo_depth > 0:
            m.submodules.device = device = Buffered(self.fifo_depth)

            rx_ready = device.rx_ready
            rx_stb = device.rx_stb
            rx_data = device.rx_data

            m.d.comb += [
                self.rx_control.f.level.r_data.eq(device.rx_level),
                self.rx_control.f.empty.r_data.eq(~device.rx_level.any()),
                self.rx_control.f.full.r_data.eq(device.rx_level == self.fifo_depth),

                self.tx_control.f.level.r_data.eq(device.tx_level),
                self.tx_control.f.empty.r_data.eq(~device.tx_level.any()),
                self.tx_control.f.full.r_data.eq(device.tx_level == self.fifo_depth),
            ]
        else:
            m.submodules.device = device = Unbuffered()

            rx_ready = am.Signal(1)
            rx_stb = am.Signal(1)
            rx_data = am.Signal(8)

            m.d.comb += [
                self.rx_control.f.level.r_data.eq(~rx_ready),
                self.rx_control.f.empty.r_data.eq(rx_ready),
                self.rx_control.f.full.r_data.eq(~rx_ready),

                self.tx_control.f.level.r_data.eq(~device.tx_ready),
                self.tx_control.f.empty.r_data.eq(device.tx_ready),
                self.tx_control.f.full.r_data.eq(~device.tx_ready),
            ]

            with m.If(device.rx_stb):
                with m.If(rx_ready):
                    # overrun
                    pass
                with m.Else():
                    m.d.sync += [
                        rx_data.eq(device.rx_data),
                        rx_ready.eq(1),
                    ]

            with m.If(rx_stb):
                m.d.sync += rx_ready.eq(0)

        m.d.comb += [
            self.tx.eq(device.tx),
            device.rx.eq(self.rx),

            device.divisor.eq(self.baud.f.data),
            # divisor_stb set in sync

            self.rx_control.f.ready.r_data.eq(rx_ready),
            self.rx_control.f.max.r_data.eq(max(self.fifo_depth, 1)),
            self.rx_reg.f.r_data.eq(rx_data),
            rx_stb.eq(self.rx_reg.f.r_stb),

            self.tx_control.f.ready.r_data.eq(device.tx_ready),
            self.tx_control.f.max.r_data.eq(max(self.fifo_depth, 1)),
            device.tx_data.eq(self.tx_reg.f.w_data),
            device.tx_stb.eq(self.tx_reg.f.w_stb),
        ]

        # sync over here so it's delayed one cycle so divisor.data is updated
        m.d.sync += device.divisor_stb.eq(self.baud.f.port.w_stb)

        return m
