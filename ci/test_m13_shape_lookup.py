#!/usr/bin/env python3
import subprocess
from test_m12_display import ROOT, BUILD, build_program


def run():
    BUILD.mkdir(parents=True, exist_ok=True)
    abi = BUILD / 'abi3'
    sources = [
        abi / 'cpu/cpu.c',
        ROOT / 'native/r8pe01_strcmp.c',
        ROOT / 'native/r8pe01_shape_lookup.c',
        ROOT / 'tests/shape_lookup_test.c',
    ]
    exe = build_program('shape-lookup-test', sources)
    subprocess.run([str(exe)], check=True)


if __name__ == '__main__':
    run()
