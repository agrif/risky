import amaranth as am
import amaranth.lib.enum
import amaranth.lib.fifo

import amaranth_soc.csr

import risky.csr
import risky.test

class Unbuffered(am.lib.wiring.Component):
    cs: am.lib.wiring.Out(1)
    sclk: am.lib.wiring.Out(1)
    copi: am.lib.wiring.Out(1)
    cipo: am.lib.wiring.In(1)

    # load up with (number of clk cycles per bit) - 1
    divisor: am.lib.wiring.In(32, init=-1)
    # 0 idle low, 1 idle high
    cpol: am.lib.wiring.In(1)
    # 0 sample active edge, 1 sample idle edge
    cpha: am.lib.wiring.In(1)
    # override chip select
    force_cs: am.lib.wiring.In(1)

    # is a busy ongoing?
    busy: am.lib.wiring.Out(1)

    rx_data: am.lib.wiring.Out(8)
    rx_stb: am.lib.wiring.Out(1)

    tx_ready: am.lib.wiring.Out(1)
    tx_data: am.lib.wiring.In(8)
    tx_stb: am.lib.wiring.In(1)

    class State(amaranth.lib.enum.Enum):
        IDLE = 0
        BUSY = 1
        WAIT = 2
        FINISH = 3

    class ClockState(amaranth.lib.enum.Enum):
        IDLE = 0
        ACTIVE = 1

    def elaborate(self, platform):
        m = am.Module()

        # overall state
        state = am.Signal(self.State)
        m.d.comb += self.cs.eq(state.matches(self.State.BUSY, self.State.WAIT) | self.force_cs)

        # clock divider
        count = am.Signal(33)
        half_baud = count[-1]
        reset_count = am.Signal(1)

        with m.If(half_baud | state.matches(self.State.IDLE)):
            m.d.comb += reset_count.eq(1)

        # divisor + 2 is (clk cycles per bit) + 1
        # so (divisor + 2) >> 1 is (clk cycles per bit + 1) / 2
        # (recall: (a + 1) / 2 is a/2, rounded)
        # and ((divisor + 2) >> 1) - 2 == (divisor >> 1) - 1
        # so (divisor >> 1) - 1 is ((clk cycles per bit + 1) / 2) - 2
        # -2 because we pass through 0 *and* -1 before resetting
        # this resets twice per (clk cycles per bit) cycles
        m.d.sync += count.eq(am.Mux(reset_count, self.divisor >> 1, count) - 1)

        # internal clock
        i_sclk = am.Signal(self.ClockState)
        m.d.comb += self.sclk.eq(self.cpol ^ i_sclk)

        with m.If(half_baud & state.matches(self.State.BUSY)):
            m.d.sync += i_sclk.eq(~i_sclk)

        # tx load
        tx_shift = am.Signal(8)
        tx_bits = am.Signal(range(9))
        m.d.comb += self.tx_ready.eq(~tx_bits.any())
        with m.If(self.tx_ready & self.tx_stb):
            m.d.sync += [
                tx_shift.eq(self.tx_data),
                tx_bits.eq(8),
            ]

        m.d.comb += self.busy.eq(state.matches(self.State.BUSY) | tx_bits.any())

        # rx register
        rx_shift = am.Signal(8)
        rx_bits = am.Signal(range(8), init=1)
        m.d.comb += self.rx_data.eq(rx_shift)
        m.d.sync += self.rx_stb.eq(0)

        # state machine
        with m.If(state.matches(self.State.IDLE)):
            with m.If(tx_bits.any()):
                m.d.sync += state.eq(self.State.BUSY)
                with m.If(~self.cpha):
                    m.d.sync += self.copi.eq(tx_shift[-1])

        with m.Elif(state.matches(self.State.BUSY)):
            with m.If(half_baud):
                # this seems backwards, because we are testing the clock state
                # *now* -- it will change to the opposite next tick
                sample_edge = am.Mux(self.cpha, i_sclk.matches(self.ClockState.ACTIVE), i_sclk.matches(self.ClockState.IDLE))

                with m.If(sample_edge):
                    # sample data
                    m.d.sync += [
                        rx_shift.eq((rx_shift << 1) | self.cipo),
                        rx_bits.eq(rx_bits + 1),
                    ]

                    with m.If(~rx_bits.any()):
                        m.d.sync += self.rx_stb.eq(1)

                with m.Else():
                    # output data
                    m.d.sync += self.copi.eq(tx_shift[-1])

                # now for un-polarity affected things
                with m.If(i_sclk.matches(self.ClockState.IDLE)):
                    # active edge
                    m.d.sync += [
                        tx_shift.eq(tx_shift << 1),
                        tx_bits.eq(tx_bits - 1),
                    ]

                with m.Else():
                    # idle edge
                    with m.If(~tx_bits.any()):
                        # late catch tx_stb, if we're running as
                        # fast as possible
                        with m.If(self.tx_stb):
                            with m.If(~self.cpha):
                                m.d.sync += self.copi.eq(self.tx_data[-1])
                        with m.Else():
                            # if not tx_stb, then end busy
                            m.d.sync += state.eq(self.State.WAIT)

        with m.Elif(state.matches(self.State.WAIT)):
            with m.If(half_baud):
                m.d.sync += state.eq(self.State.FINISH)

        with m.Elif(state.matches(self.State.FINISH)):
            with m.If(half_baud):
                m.d.sync += state.eq(self.State.IDLE)

        return m

class Buffered(am.lib.wiring.Component):
    def __init__(self, depth):
        super().__init__({
            'cs': am.lib.wiring.Out(1),
            'sclk': am.lib.wiring.Out(1),
            'copi': am.lib.wiring.Out(1),
            'cipo': am.lib.wiring.Out(1),

            'divisor': am.lib.wiring.In(32, init=-1),
            'cpol': am.lib.wiring.In(1),
            'cpha': am.lib.wiring.In(1),
            'force_cs': am.lib.wiring.In(1),

            'busy': am.lib.wiring.Out(1),

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

        m.submodules.rx_fifo = rx_fifo = am.lib.fifo.SyncFIFO(width=8, depth=self.depth)
        m.submodules.tx_fifo = tx_fifo = am.lib.fifo.SyncFIFO(width=8, depth=self.depth)

        m.d.comb += [
            self.cs.eq(unbuffered.cs),
            self.sclk.eq(unbuffered.sclk),
            self.copi.eq(unbuffered.copi),
            unbuffered.cipo.eq(self.cipo),

            unbuffered.divisor.eq(self.divisor),
            unbuffered.cpol.eq(self.cpol),
            unbuffered.cpha.eq(self.cpha),
            unbuffered.force_cs.eq(self.force_cs),

            self.busy.eq(unbuffered.busy),

            # rx fifo input
            rx_fifo.w_data.eq(unbuffered.rx_data),
            rx_fifo.w_en.eq(unbuffered.rx_stb & rx_fifo.w_rdy),

            # rx fifo output
            self.rx_ready.eq(rx_fifo.r_rdy),
            self.rx_data.eq(rx_fifo.r_data),
            rx_fifo.r_en.eq(self.rx_stb),

            # tx fifo input
            self.tx_ready.eq(tx_fifo.w_rdy),
            tx_fifo.w_data.eq(self.tx_data),
            tx_fifo.w_en.eq(self.tx_stb),

            # tx fifo output
            unbuffered.tx_stb.eq(tx_fifo.r_rdy & unbuffered.tx_ready),
            tx_fifo.r_en.eq(unbuffered.tx_stb),
            unbuffered.tx_data.eq(tx_fifo.r_data),

            # levels
            self.rx_level.eq(rx_fifo.level),
            self.tx_level.eq(tx_fifo.level),
        ]

        return m

class Peripheral(risky.csr.Peripheral):
    class SpiInfo(amaranth_soc.csr.Register, access='rw'):
        busy: amaranth_soc.csr.Field(amaranth_soc.csr.action.R, 1)
        cpol: amaranth_soc.csr.Field(amaranth_soc.csr.action.RW, 1)
        cpha: amaranth_soc.csr.Field(amaranth_soc.csr.action.RW, 1)
        cs: amaranth_soc.csr.Field(amaranth_soc.csr.action.RW, 1)

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

        super().__init__(depth=14, signature={
            'cs': am.lib.wiring.Out(1),
            'sclk': am.lib.wiring.Out(1),
            'copi': am.lib.wiring.Out(1),
            'cipo': am.lib.wiring.Out(1),
        })

        with self.register_builder() as b:
            self.control = b.add('control', self.SpiInfo())
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
            self.cs.eq(device.cs),
            self.sclk.eq(device.sclk),
            self.copi.eq(device.copi),
            device.cipo.eq(self.cipo),

            device.divisor.eq(self.baud.f.data),
            device.cpol.eq(self.control.f.cpol.data),
            device.cpha.eq(self.control.f.cpha.data),
            device.force_cs.eq(self.control.f.cs.data),

            self.control.f.busy.r_data.eq(device.busy),

            self.rx_control.f.ready.r_data.eq(rx_ready),
            self.rx_control.f.max.r_data.eq(max(self.fifo_depth, 1)),
            self.rx_reg.f.r_data.eq(rx_data),
            rx_stb.eq(self.rx_reg.f.r_stb),

            self.tx_control.f.ready.r_data.eq(device.tx_ready),
            self.tx_control.f.max.r_data.eq(max(self.fifo_depth, 1)),
            device.tx_data.eq(self.tx_reg.f.w_data),
            device.tx_stb.eq(self.tx_reg.f.w_stb),
        ]

        return m
