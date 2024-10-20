import amaranth as am
import amaranth.lib.data
import amaranth.lib.enum

import amaranth_soc.csr

import risky.csr
import risky.test

class Device(am.lib.wiring.Component):
    data_in: am.lib.wiring.In(512)
    data_stb: am.lib.wiring.In(1)

    ready: am.lib.wiring.Out(1)

    initialize: am.lib.wiring.In(1)
    output: am.lib.wiring.Out(160)

    class State(am.lib.enum.Enum):
        IDLE = 0
        BUSY = 1
        FINISH = 2

    def __init__(self):
        super().__init__()

        self.data = am.Signal(am.lib.data.ArrayLayout(32, 16))
        self.state = am.Signal(self.State)
        self.round = am.Signal(range(80))
        self.word = am.Signal(32)
        self.hash = am.Signal(am.lib.data.ArrayLayout(32, 5), init=[
            0x67452301,
            0xEFCDAB89,
            0x98BADCFE,
            0x10325476,
            0xC3D2E1F0,
        ])
        self.chunkhash = am.Signal(self.hash.shape())

    @property
    def debug_traces(self):
        return [
            self.data_stb,
            self.ready,
            self.initialize,
            self.output,
            self.state,
            self.round,
            self.word,
            self.chunkhash,
            self.data,
        ]

    def elaborate(self, platform):
        m = am.Module()

        idle = self.state.matches(self.State.IDLE)
        busy = self.state.matches(self.State.BUSY)
        finish = self.state.matches(self.State.FINISH)

        m.d.comb += self.ready.eq(idle)

        # load self.data
        with m.If(idle & self.data_stb):
            # reverse it so self.data[0] is the MSBs, etc.
            # this way self.data[0] is the first word we process
            for i, w in enumerate(self.data):
                start = 32 * (len(self.data) - i - 1)
                end = start + 32
                m.d.sync += w.eq(self.data_in[start:end])

            # exit idle and initialize chunkhash
            m.d.sync += [
                self.round.eq(0),
                self.state.eq(self.State.BUSY),
                self.chunkhash.eq(self.hash),
            ]

            with m.If(self.initialize):
                # use initial value instead, and reset hash
                m.d.sync += [
                    self.hash.eq(self.hash.as_value().init),
                    self.chunkhash.eq(self.hash.as_value().init),
                ]

        # output hash
        for i, w in enumerate(self.hash):
            # reverse it for same reasons as data
            start = 32 * (len(self.hash) - i - 1)
            end = start + 32
            m.d.comb += self.output[start:end].eq(w)

        # busy round counter
        with m.If(busy):
            m.d.sync += self.round.eq(self.round + 1)
            with m.If(self.round + 1 == 80):
                # move to finish state
                m.d.sync += self.state.eq(self.State.FINISH)

        # finish round add
        with m.If(finish):
            # return to idle and add chunkhash to hash
            m.d.sync += self.state.eq(self.State.IDLE)
            for h, ch in zip(self.hash, self.chunkhash):
                m.d.sync += h.eq(h + ch)

        # message schedule
        with m.If(self.round < len(self.data)):
            m.d.comb += self.word.eq(self.data[self.round])
        with m.Else():
            a = (self.round - 3) % len(self.data)
            b = (self.round - 8) % len(self.data)
            c = (self.round - 14) % len(self.data)
            d = (self.round - 16) % len(self.data)

            v = self.data[a] ^ self.data[b] ^ self.data[c] ^ self.data[d]
            m.d.comb += self.word.eq(v.rotate_left(1))

        # update data with message schedule as we go
        with m.If(busy):
            m.d.sync += self.data[self.round % len(self.data)].eq(self.word)

        # perform a busy step
        with m.If(busy):
            a, b, c, d, e = self.chunkhash
            f = am.Signal(32)
            k = am.Signal(32)

            with m.If(self.round < 20):
                m.d.comb += [
                    f.eq((b & c) | (~b & d)),
                    k.eq(0x5A827999),
                ]
            with m.Elif(self.round < 40):
                m.d.comb += [
                    f.eq(b ^ c ^ d),
                    k.eq(0x6ED9EBA1),
                ]
            with m.Elif(self.round < 60):
                m.d.comb += [
                    f.eq((b & c) | (b & d) | (c & d)),
                    k.eq(0x8F1BBCDC),
                ]
            with m.Else():
                m.d.comb += [
                    f.eq(b ^ c ^ d),
                    k.eq(0xCA62C1D6),
                ]

            m.d.sync += [
                self.chunkhash[0].eq(a.rotate_left(5) + f + e + k + self.word),
                self.chunkhash[1].eq(a),
                self.chunkhash[2].eq(b.rotate_left(30)),
                self.chunkhash[3].eq(c),
                self.chunkhash[4].eq(d),
            ]

        return m

class Peripheral(risky.csr.Peripheral):
    class Control(amaranth_soc.csr.Register, access='rw'):
        ready: amaranth_soc.csr.Field(amaranth_soc.csr.action.R, 1)
        start: amaranth_soc.csr.Field(amaranth_soc.csr.action.W, 1)
        initialize: amaranth_soc.csr.Field(amaranth_soc.csr.action.W, 1)

    class Input(amaranth_soc.csr.Register, access='rw'):
        def __init__(self):
            super().__init__(
                amaranth_soc.csr.Field(amaranth_soc.csr.action.RW, 32)
            )

    class Output(amaranth_soc.csr.Register, access='r'):
        def __init__(self):
            super().__init__(
                amaranth_soc.csr.Field(amaranth_soc.csr.action.R, 32)
            )

    def __init__(self):
        self.device = Device()

        input_words = len(self.device.data_in) // 32
        output_words = len(self.device.output) // 32

        super().__init__(depth=4 + 4 * (input_words + output_words))

        with self.register_builder() as b:
            self.control = b.add('control', self.Control())

            self.input = []
            for i in range(input_words):
                self.input.append(b.add('input' + str(i), self.Input()))

            self.output = []
            for i in range(output_words):
                self.output.append(b.add('output' + str(i), self.Output()))

    @property
    def debug_traces(self):
        return self.device.debug_traces

    def elaborate(self, platform):
        m = am.Module()

        self.elaborate_registers(platform, m)
        m.submodules += self.device

        m.d.comb += [
            self.control.f.ready.r_data.eq(self.device.ready),

            self.device.data_stb.eq(self.control.f.start.w_data & self.control.f.start.w_stb),
            self.device.initialize.eq(self.control.f.initialize.w_data & self.control.f.start.w_stb),
        ]

        for i, w in enumerate(self.input):
            # reverse these so lower address maps to first bits of message
            # and endian-swap them because we want first bytes of the words
            # to be first bytes of the message
            start = len(self.device.data_in) - 32 - 32 * i
            end = start + 32

            word = w.f.data

            word = am.Cat(word[24:32], word[16:24], word[8:16], word[0:8])
            m.d.comb += self.device.data_in[start:end].eq(word)

        for i, w in enumerate(self.output):
            # reverse these so lower address maps to first bits of hash
            # and endian-swap them so first bytes are first bytes of hash
            start = len(self.device.output) - 32 - 32 * i
            end = start + 32

            word = self.device.output[start:end]
            word = am.Cat(word[24:32], word[16:24], word[8:16], word[0:8])
            m.d.comb += w.f.r_data.eq(word)

        return m

class Test(risky.test.Simulated):
    def construct(self):
        return Device()

    @classmethod
    def chunks(cls, msg):
        chunksize = 64
        i = 0

        endmarker = False
        end = False

        while not end:
            chunk = msg[i:i + chunksize]

            if not endmarker and len(chunk) < chunksize:
                chunk += b'\x80'
                endmarker = True

            while len(chunk) != chunksize - 8 and len(chunk) < chunksize:
                chunk += b'\x00'

            if len(chunk) == chunksize - 8:
                import struct
                chunk += struct.pack('>Q', 8 * len(msg))
                end = True

            i += chunksize
            assert len(chunk) == chunksize
            yield chunk

    async def testbench(self, ctx):
        import hashlib
        messages = [
            b'',
            b'The quick brown fox jumps over the lazy dog',
            b'a' * (64 - 1 - 8),
            b'b' * (64 - 8),
            b'c' * 64,
        ]

        for msg in messages:
            ctx.set(self.dut.initialize, 1)

            for chunk in self.chunks(msg):
                assert ctx.get(self.dut.ready)

                for i, b in enumerate(chunk):
                    start = len(self.dut.data_in) - 8 - 8 * i
                    end = start + 8
                    ctx.set(self.dut.data_in[start:end], b)

                ctx.set(self.dut.data_stb, 1)
                await ctx.tick()

                ctx.set(self.dut.initialize, 0)
                ctx.set(self.dut.data_in, 0)
                ctx.set(self.dut.data_stb, 0)

                assert not ctx.get(self.dut.ready)

                while not ctx.get(self.dut.ready):
                    await ctx.tick()

            hash = ctx.get(self.dut.output)
            hexdigest = "{:040x}".format(hash)
            truth = hashlib.sha1(msg).hexdigest()

            print("{} {!r}".format(hexdigest, msg))
            assert hexdigest == truth

if __name__ == '__main__':
    test = Test()
    test.run(output='output.vcd', gtkw_file='output.gtkw')
