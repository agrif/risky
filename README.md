Risky
=====

Risky is a RISC-V core in Amaranth, but mostly, it's a project to
learn RISC-V and Amaranth. It's probably not very good.

Get Started
-----------

 1. Get a RISC-V toolchain, with *riscv-none-elf-gcc*, and put it in
    your PATH.
     * I got mine [from platformio][platformio].

       [platformio]: https://registry.platformio.org/tools/platformio/toolchain-riscv

 2. `python3 -m venv my-virtual-env`
 3. `. my-virtual-env/bin/activate`
 4. `pip install -r requirements.txt`
 5. `python -m risky simulate hello.c`

Python may complain about an *amaranth* version conflict. If so,
comment out the line for *amaranth* in *requirements.txt* and try `pip
install -r requirements.txt` again. Afterwards, install *amaranth*
manually, with the same version in the line you commented out.

If you have an [FPGA toolchain][], by all means experiment with `python -m
risky demo hello.c`, which will build an image you can write to your
FPGA.

  [FPGA toolchain]: https://github.com/YosysHQ/oss-cad-suite-build/releases/
