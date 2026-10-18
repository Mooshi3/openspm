#!/usr/bin/env python3
"""Persistent Linux module build using the available compiler, without CMake.
The production Windows build uses the original CMake target. No regeneration.
"""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT/'build/module-linux'
MODULE = ROOT/'m10/module'

def run():
    BUILD.mkdir(parents=True, exist_ok=True)
    abi = ROOT/'build/native-tests/abi3'
    sources = sorted((MODULE/'generated/chunks').glob('*.c'))
    assert len(sources) == 218
    sources += [MODULE/'src/spm_module.c', abi/'cpu/cpu.c']
    sources += sorted((MODULE/'openspm').glob('*.c'))
    cc = os.environ.get('CC','gcc')
    flags = ['-std=c11','-O2','-fPIC','-fno-strict-aliasing',
             '-I'+str(abi),'-I'+str(MODULE/'include'),'-I'+str(MODULE/'generated')]
    headers = sorted(abi.rglob('*.h')) + sorted(MODULE.rglob('*.h'))
    fingerprint = hashlib.sha256()
    fingerprint.update(json.dumps([cc,*flags]).encode())
    for h in headers: fingerprint.update(h.read_bytes())
    def compile_one(source):
        obj = BUILD/(source.stem+'.o')
        stamp = obj.with_suffix('.sha256')
        digest = fingerprint.copy(); digest.update(source.read_bytes()); value = digest.hexdigest()
        if obj.exists() and stamp.exists() and stamp.read_text() == value: return obj,False
        log = BUILD/(source.stem+'.log')
        with log.open('w') as stream:
            subprocess.run([cc,*flags,'-c',str(source),'-o',str(obj)],stdout=stream,stderr=stream,check=True)
        stamp.write_text(value)
        return obj,True
    objects = []; rebuilt = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for obj, changed in pool.map(compile_one,sources):
            objects.append(str(obj)); rebuilt += changed
            if len(objects)%16 == 0: print(f'{len(objects)}/{len(sources)} objects ready',flush=True)
    library = BUILD/'gR8PE01_recomp.so'
    subprocess.run([cc,'-shared',*objects,'-lm','-o',str(library)],check=True)
    probe = BUILD/'spm_module_probe'
    subprocess.run([cc,'-O2',str(ROOT/'m10/tests/module_descriptor_probe.c'),'-ldl','-o',str(probe)],check=True)
    subprocess.run([str(probe),str(library)],check=True)
    print(f'Rebuilt {rebuilt}/{len(sources)} objects; SHA256={hashlib.sha256(library.read_bytes()).hexdigest()}')

if __name__ == '__main__': run()
