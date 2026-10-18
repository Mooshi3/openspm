#!/usr/bin/env python3
"""Compile and exercise the actual generated GX routine, original vs patched."""
import os
from pathlib import Path
import subprocess
import zipfile
import json

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / 'build/native-tests'
CHUNK = 'module/generated/chunks/chunk_0158_text1_80291940.c'

def write_changed(path, text):
    if not path.exists() or path.read_text() != text:
        path.write_text(text)

def build_program(name, sources):
    cc = os.environ.get('CC', 'cl' if os.name == 'nt' else 'gcc')
    msvc = Path(cc).name.lower() in ('cl', 'cl.exe')
    abi = BUILD / 'abi3'
    # These isolated routines use generated math helpers, but never the
    # whole-game dispatcher. MSVC retains its static tables even when its
    # inline functions are unused, pulling in 217 unrelated chunks.
    # Preserve the exact helper definitions; omit only the unused dispatcher.
    # The actual DLL build still uses the complete generated header.
    test_include = BUILD / 'isolated-include'
    test_include.mkdir(parents=True, exist_ok=True)
    header = (ROOT/'m10/module/generated/generated.h').read_text()
    assert header.count('// Function entry points') == 1
    write_changed(test_include/'generated.h',
                  header.split('// Function entry points', 1)[0] + '\n#endif\n')
    includes = [abi, test_include, ROOT/'native']
    flags = (['/nologo', '/std:c11', '/O2', '/D_CRT_SECURE_NO_WARNINGS'] +
             ['/I'+str(p) for p in includes]) if msvc else (
             ['-std=c11', '-O2', '-fno-strict-aliasing'] + ['-I'+str(p) for p in includes])
    objects = []
    for source in sources:
        obj = BUILD / (source.stem + ('.obj' if msvc else '.o'))
        deps = [source, abi/'cpu/cpu.h', abi/'common/types.h',
                ROOT/'native/display.h', test_include/'generated.h']
        command = [cc,*flags] + (['/c',str(source),'/Fo'+str(obj)] if msvc else
                                 ['-c',str(source),'-o',str(obj)])
        stamp = obj.with_suffix(obj.suffix+'.command')
        signature = json.dumps(command)
        if (not obj.exists() or not stamp.exists() or stamp.read_text() != signature or
                any(p.stat().st_mtime > obj.stat().st_mtime for p in deps)):
            subprocess.run(command, check=True)
            stamp.write_text(signature)
        objects.append(str(obj))
    exe = BUILD / (name + ('.exe' if msvc else ''))
    command = ([cc,'/nologo',*objects,'/Fe'+str(exe)] if msvc else
               [cc,*objects,'-lm','-o',str(exe)])
    subprocess.run(command, check=True)
    return exe

def run():
    BUILD.mkdir(parents=True, exist_ok=True)
    payload = ROOT / 'ci/payload/spm-m10-portable-source.zip'
    if not payload.exists():
        payload = ROOT.parent / 'recovered/spm-m10-portable-source.zip'
    with zipfile.ZipFile(payload) as z:
        original = z.read(CHUNK).decode()
    patched = (ROOT / 'm10' / CHUNK).read_text()
    for name, source in [('original', original), ('patched', patched)]:
        body = source.split('label_80292164:', 1)[1].split('label_802921C8:', 1)[0]
        body = body.replace('goto return_dispatch_80291940;', 'return;')
        write_changed(BUILD / f'{name}_projection.c', '#include "generated.h"\n'
                      'void openspm_r8pe01_submit_projection(CPUState*);\n'
                      f'void {name}_projection(CPUState* ctx) {{\n{body}\n}}\n')
    abi = BUILD / 'abi3'
    sources = [abi/'cpu/cpu.c', ROOT/'native/display.c', ROOT/'native/r8pe01_display.c',
               BUILD/'original_projection.c', BUILD/'patched_projection.c', ROOT/'tests/display_test.c']
    exe = build_program('display-test', sources)
    with (BUILD/'display-test.log').open('w') as log:
        result = subprocess.run([str(exe)], stdout=subprocess.PIPE, stderr=log, text=True)
    print(result.stdout, end='')
    result.check_returncode()

if __name__ == '__main__':
    run()
