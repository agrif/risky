import re
import sys

import serial

BANNER_RE = re.compile(b'risky-b([0-9]+)(?:\r\n|\r|\n)')
ERR_RE = re.compile(b'e:\s+(.+)(?:\r\n|\r|\n)')
STATUS_RE = re.compile(b'([a-z])\s+([0-9a-fA-F]+)(?:\r\n|\r|\n)')
INFO_RE = re.compile(b'([a-z]+)\s+([0-9a-fA-F]+)(?:\r\n|\r|\n)')
READ_RE = re.compile(b'([0-9a-fA-F]+):((?:\s+[0-9a-fA-F]+)+)(?:\r\n|\r|\n)')

class Bootloader:
    def __init__(self, port, baud=115200):
        self.ser = serial.Serial(port, baud)

        self.version = None
        self.info = None

    @property
    def banner(self):
        return self.info['banner']

    @property
    def boot_address(self):
        return self.info['b']

    @property
    def buffer_size(self):
        return self.info['k']

    def wait_for_reset(self):
        while True:
            line = self.ser.readline()
            m = BANNER_RE.search(line)
            if m:
                self.version = int(m.group(1))
                self.info = self.read_info()

                if self.info['version'] != self.version:
                    raise RuntimeError('bootloader info responded with unexpected version')

                if not line.strip().endswith(self.info['banner']):
                    raise RuntimeError('bootloader info responded with unexpected banner')

                break

    def command(self, char, *args, response=True):
        if not self.version:
            raise RuntimeError('bootloader not connected')

        line = char + ' ' + ' '.join('{:x}'.format(a) for a in args)
        self.ser.write(line.encode('utf-8') + b'\r\n')

        if not response:
            return

        char = char.encode('utf-8')

        response = []

        while True:
            line = self.ser.readline()

            m = ERR_RE.match(line)
            if m:
                raise RuntimeError('bootloader reported error: {}'.format(m.group(1).decode('utf-8')))

            m = STATUS_RE.match(line)
            if m and m.group(1) == char:
                return (int(m.group(2), 16), response)
                
            response.append(line)

    def read_info(self):
        version, lines = self.command('i')

        info = {
            'version': version,
            'banner': lines[0].strip(),
        }

        names = b'bk'

        for line in lines[1:]:
            m = STATUS_RE.match(line)
            if not m or not m.group(1) in names:
                raise RuntimeError('unexpected line in bootloader info: {}'.format(line))

            info[m.group(1).decode('utf-8')] = int(m.group(2), 16)

        return info

    def boot(self, addr=None):
        if addr is not None:
            self.command('b', addr, response=False)
        else:
            self.command('b', response=False)

        self.version = None
        self.info = None

    def read_memory(self, start, end):
        amount, lines = self.command('m', start, end)
        if amount != end - start:
            raise RuntimeError('bootloader response is incorrect number of bytes')

        data = b''
        current = start
        for line in lines:
            m = READ_RE.match(line)
            if not m:
                raise RuntimeError('unexpected line from bootloader read: {}'.format(line))

            if int(m.group(1), 16) != current:
                raise RuntimeError('bad address from bootloader read: {}'.format(int(m.group(1), 16)))

            new = [int(d, 16) for d in m.group(2).split()]
            data += bytes(new)
            current += len(new)

        if not len(data) == amount:
            raise RuntimeError('bootloader returned incorrect number of bytes')

        return data

    def read_memory_stream(self, start, end, chunk_size=256):
        cur = start
        while cur < end:
            chunk = self.read_memory(cur, min(cur + chunk_size, end))
            cur += len(chunk)

            yield chunk

    def copy_memory(self, start, end, dest):
        amount, lines = self.command('c', start, end, dest)
        if amount != end - start:
            raise RuntimeError('bootloader copied incorrect number of bytes')

        if lines:
            raise RuntimeError('unxpected lines from bootloader copy')

    def write_memory_stream(self, start, data):
        i = 0
        chunk_size = (self.buffer_size - len(b'p 00000000\r\n')) // len(b' 00')

        while i < len(data):
            chunk = data[i:i + chunk_size]
            amount, lines = self.command('p', start + i, *chunk)
            i += len(chunk)

            if amount != len(chunk):
                raise RuntimeError('bootloader wrote incorrect number of bytes')

            if lines:
                raise RuntimeError('unexpected lines from bootloader write')

            yield chunk

    def write_memory(self, start, data):
        for _ in self.write_memory_stream(start, data):
            pass

    def attach(self):
        # FIXME forward stdin too
        while True:
            b = self.ser.read()
            sys.stdout.buffer.write(b)
            sys.stdout.flush()
