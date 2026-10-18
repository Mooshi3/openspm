#!/usr/bin/env python3
import os
import subprocess
import zipfile
from test_m12_display import ROOT, BUILD, write_changed, build_program

def run():
    BUILD.mkdir(parents=True, exist_ok=True)
    payload = ROOT/'ci/payload/spm-m10-portable-source.zip'
    if not payload.exists():
        payload = ROOT.parent/'recovered/spm-m10-portable-source.zip'
    with zipfile.ZipFile(payload) as z:
        source = z.read('module/generated/chunks/chunk_0144_text1_80259940.c').decode()
    body = source.split('label_8025C9C8:', 1)[1].split('label_8025CAE4:', 1)[0]
    body = body.replace('goto return_dispatch_80259940;', 'return;')
    write_changed(BUILD/'original_strcmp.c', '#include "generated.h"\n'
                  f'void original_strcmp(CPUState* ctx) {{\n{body}\n}}\n')
    abi = BUILD/'abi3'
    sources = [abi/'cpu/cpu.c', ROOT/'native/r8pe01_strcmp.c', BUILD/'original_strcmp.c', ROOT/'tests/strcmp_test.c']
    exe = build_program('strcmp-test', sources)
    subprocess.run([str(exe)],check=True)

if __name__ == '__main__':
    run()
