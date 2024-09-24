import hashlib
import io
import os.path
import struct
import subprocess
import tempfile

import elftools.elf.elffile
import importlib_resources

class Compiler:
    RUNTIME_FILES = ['crt0.s', 'busy_loop.s']

    def __init__(self, runtime=True, optimize=True, march='rv32i', mabi='ilp32'):
        self.runtime = importlib_resources.files('risky.runtime')
        self.d = tempfile.TemporaryDirectory(prefix='risky-compiler.')

        self.gcc = [
            'riscv-none-elf-gcc',
            '-march=' + march,
            '-mabi=' + mabi,
        ]

        if optimize:
            self.gcc.append('-O3')

        self.linkerscript = self.copy_runtime_file('link.x')
        self.objectpaths = []
        if runtime:
            self.add_runtime()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            self.d.cleanup()
        finally:
            pass

    def path(self, *components):
        return os.path.join(self.d.name, *components)

    def add(self, fname):
        os.makedirs(self.path('objects'), exist_ok=True)
        outpath = self.path('objects', os.path.split(fname)[1] + '.o')
        subprocess.run(self.gcc + [
            '-c', fname,
            '-o', outpath,
        ], check=True)
        self.objectpaths.append(outpath)

    def add_source(self, ext, source):
        hash = hashlib.sha1(source.encode('utf-8')).hexdigest()
        fname = self.path(hash + '.' + ext)
        with open(fname, 'w') as f:
            f.write(source)
        self.add(fname)

    def copy_runtime_file(self, name):
        path = self.path(name)
        with open(path, 'w') as f:
            src = self.runtime.joinpath(name).read_text()
            f.write(src)
        return path

    def add_runtime(self):
        for name in self.RUNTIME_FILES:
            path = self.copy_runtime_file(name)
            self.add(path)

    def link(self):
        elfpath = self.path('program.elf')
        subprocess.run(self.gcc + [
            '-static',
            '-nostartfiles',
            '-static-libgcc',
            *self.objectpaths,
            '-o', elfpath,
            '-T', self.linkerscript,
            '-Wl,--gc-sections',
            '-Wl,-m,elf32lriscv', # FIXME why?
        ], check=True)

        with open(elfpath, 'rb') as f:
            return ElfData(f.read())

class ElfData:
    def __init__(self, data):
        self.data = data
        self._flat = None

    def dump(self, fname):
        with open(fname, 'wb') as f:
            f.write(self.data)

    def dump_disassemble(self, fname):
        with open(fname, 'w') as f:
            f.write(self.disassemble())

    def disassemble(self):
        with tempfile.TemporaryDirectory(prefix='risky-compiler.') as d:
            elfpath = os.path.join(d, 'program.elf')
            self.dump(elfpath)

            p = subprocess.run([
                'riscv-none-elf-objdump',
                '-D',
                elfpath,
            ], check=True, capture_output=True, text=True)

            return p.stdout

    def symbols(self):
        elf = elftools.elf.elffile.ELFFile(io.BytesIO(self.data))
        symbols = {}
        for sec in elf.iter_sections():
            if not isinstance(sec, elftools.elf.sections.SymbolTableSection):
                continue
            for sym in sec.iter_symbols():
                symbols[sym.name] = sym.entry.st_value

        return symbols

    def dump_flat(self, fname):
        with open(fname, 'wb') as f:
            f.write(self.flat)

    @property
    def flat(self):
        if self._flat:
            return self._flat

        with tempfile.TemporaryDirectory(prefix='risky-compiler.') as d:
            elfpath = os.path.join(d, 'program.elf')
            binpath = os.path.join(d, 'program.bin')

            self.dump(elfpath)

            subprocess.run([
                'riscv-none-elf-objcopy',
                '-O', 'binary',
                elfpath,
                binpath,
            ], check=True)

            with open(binpath, 'rb') as f:
                self._flat = f.read()

            return self._flat

    @property
    def flat_words(self):
        return list(x[0] for x in struct.iter_unpack('<I', self.flat))
