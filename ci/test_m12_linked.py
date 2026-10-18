#!/usr/bin/env python3
"""Linux integration checks against the full, already-built shared module.
Run the isolated tests first to prepare exact-source reference routines.
No module rebuild, regeneration, or source changes are performed here.
"""
import subprocess
from test_m12_display import ROOT, BUILD, write_changed

def run():
    module = ROOT/'build/module-linux/gR8PE01_recomp.so'
    assert module.is_file(), 'Run ci/build_m12_local.py first'
    wrapper = BUILD/'module-display-wrapper.c'
    write_changed(wrapper, '#include "cpu/cpu.h"\n'
                  'void func_80291940(CPUState*);\n'
                  'void patched_projection(CPUState* c) { func_80291940(c); }\n')
    cases = {
        'module-display': [ROOT/'tests/display_test.c', BUILD/'original_projection.c', wrapper],
        'module-dispatch': [ROOT/'tests/module_dispatch_test.c', BUILD/'original_strcmp.c'],
    }
    for name, sources in cases.items():
        exe = BUILD/(name+'-test')
        command = ['gcc', '-std=c11', '-O2', '-fno-strict-aliasing',
                   '-I'+str(BUILD/'abi3'), '-I'+str(BUILD/'isolated-include'),
                   '-I'+str(ROOT/'native'), *map(str, sources), str(module), '-lm',
                   '-Wl,-rpath,$ORIGIN/../module-linux', '-o', str(exe)]
        subprocess.run(command, check=True)
        with (BUILD/(name+'-test.log')).open('w') as log:
            result = subprocess.run([str(exe)], stdout=subprocess.PIPE, stderr=log, text=True)
        write_changed(BUILD/(name+'-result.log'), result.stdout)
        print(result.stdout, end='')
        result.check_returncode()

if __name__ == '__main__': run()
