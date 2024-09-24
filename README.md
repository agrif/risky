Risky
=====

Risky is a RISC-V core in Amaranth, but mostly, it's a project to
learn RISC-V and Amaranth. It's probably not very good.

Get Started
-----------

 1. Get a RISC-V toolchain, with `riscv-none-elf-gcc', and put it in
    your PATH.
     * Good candidates are [from platformio][platformio] and [from
    sifive][sifive].

    [platformio]: https://registry.platformio.org/tools/platformio/toolchain-riscv
    [sifive]: https://github.com/sifive/freedom-tools/releases

 2. `python -m venv my-virtual-env`
 3. `. my-virtual-env/bin/activate`
 4. `pip install -r requirements.txt`
 5. `python -m risky simulate hello.c`

If you have an FPGA toolchain, by all means experiment with `python -m
risky demo hello.c`, which will build an image you can write to your
FPGA.
