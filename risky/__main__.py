#!/usr/bin/env python3

import functools
import json
import os.path
import subprocess
import tempfile

import risky.cpu
import risky.demo
import risky.soc
import risky.test
import risky.test.rv32i
import risky.test.plain

import amaranth as am
import amaranth.sim
import amaranth.back.verilog

import amaranth_boards.de10_nano
import amaranth_boards.icestick
import amaranth_boards.tang_nano_9k

import click

# a dirty trick to unbuffer stdout
# https://stackoverflow.com/a/181654
def unbuffer_stdout():
    import io, os, sys
    sys.stdout = io.TextIOWrapper(
        open(sys.stdout.fileno(), 'wb', 0),
        write_through=True,
    )

@click.group()
def cli():
    pass

@cli.command()
def test():
    for t in risky.test.UnitTest.iter_tests():
        print(t.name)
        t.run()

@cli.command()
@click.option('-o', '--output', type=click.File('w'))
@click.option('--cycles', type=int)
@click.argument('sources', nargs=-1, required=True)
def simulate(output, cycles, sources):
    plain = risky.test.plain.Plain(sources, cycles=cycles)
    unbuffer_stdout()
    plain.run(output=output)

IVERILOG_TB = """
`include "top.v"

module bench;
    reg clk;
    top top(.clk(clk));

    initial begin
        {output_c}$dumpfile({output});
        {output_c}$dumpvars(0, top);

        clk = 0;
        forever begin
            #1 clk = ~clk;
        end
    end

    initial begin
        {cycles_c}#{cycles};
        {cycles_c}$finish();
    end
endmodule
"""

@cli.command()
@click.option('-o', '--output')
@click.option('--cycles', type=int)
@click.argument('sources', nargs=-1, required=True)
def iverilog(output, cycles, sources):
    top = risky.soc.Soc.with_source_files(1_000_000, *sources)
    with tempfile.TemporaryDirectory(prefix='risky-iverilog.') as d:
        toppath = os.path.join(d, 'top.v')
        tbpath = os.path.join(d, 'bench.v')
        vvppath = os.path.join(d, 'bench.vvp')

        with open(toppath, 'w') as f:
            f.write(am.back.verilog.convert(top))

        with open(tbpath, 'w') as f:
            output_c = ''
            if output is None:
                output_c = '//'

            cycles_c = ''
            if cycles is None:
                cycles_c = '//'

            tb = IVERILOG_TB.format(
                output_c=output_c,
                output=json.dumps(output),
                cycles_c=cycles_c,
                cycles=cycles * 2 if cycles else cycles,
            )

            f.write(tb)

        subprocess.run([
            'iverilog',
            '-I', d,
            '-o', vvppath,
            tbpath,
        ], check=True)

        unbuffer_stdout()
        subprocess.run(['vvp', '-i', vvppath], check=True, input=b'')

@cli.command()
@click.argument('output', type=click.File('w'), default='-')
def verilog(output):
    top = risky.cpu.Cpu()
    output.write(am.back.verilog.convert(top))

BOARDS = {
    'de10-nano': amaranth_boards.de10_nano.DE10NanoPlatform,
    'icestick': amaranth_boards.icestick.ICEStickPlatform,
    'tang-nano-9k': amaranth_boards.tang_nano_9k.TangNano9kPlatform,
}

@cli.command()
@click.option('--board', '-b', type=click.Choice(sorted(BOARDS.keys()), case_sensitive=False), default='icestick')
@click.option('--generate', '-g', is_flag=True)
@click.option('--archive', '-a', type=click.File('wb'))
@click.option('--build-dir', default='build')
@click.option('--program', '-p', is_flag=True)
@click.option('--toolchain', type=str)
@click.option('--topname', type=str, default='top')
@click.option('--ssh', type=str)
@click.option('--ssh-path', type=str)
@click.argument('sources', nargs=-1, required=True)
def demo(board, generate, archive, build_dir, program, toolchain, topname, ssh, ssh_path, sources):
    if archive:
        generate = True

    if ssh and not ssh_path:
        raise RuntimeError('ssh requires an ssh path')

    connect_to = dict()
    if ssh:
        connect_to = dict(
            hostname=ssh,
        )

    BoardPlatform = BOARDS[board]
    demo = risky.demo.Demo(sources)

    platform_kwargs = dict()
    if toolchain:
        platform_kwargs['toolchain'] = toolchain

    plat = BoardPlatform(**platform_kwargs)

    build_kwargs = dict(
        name=topname,
        do_build=not generate,
        build_dir=build_dir,
        do_program=program,
        debug_verilog=True,
    )

    def build_ssh():
        plan = plat.prepare(demo, **build_kwargs)
        if generate:
            return plan
        products = plan.execute_remote_ssh(connect_to=connect_to, root=ssh_path)
        if not program:
            return products
        plat.toolchain_program(products, name, build_kwargs.get('program_opts', {}))

    if ssh:
        # remote build
        result = build_ssh()
    else:
        # local build
        result = plat.build(demo, **build_kwargs)

    if generate:
        plan = result
        if archive:
            plan.archive(archive)
        else:
            if ssh:
                # remote
                plan.execute_remote_ssh(connect_to=connect_to, root=ssh_path, run_script=False)
            else:
                # local
                plan.extract(build_dir)
        return

    if not program:
        products = result
        return

if __name__ == '__main__':
    cli()
