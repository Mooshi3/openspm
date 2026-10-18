#!/usr/bin/env python3
"""Apply to the exact pristine M10 payload; do not apply the M11 module patch."""
from pathlib import Path
import hashlib
import shutil

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / 'm10/module'
CHUNKS = MODULE / 'generated/chunks'

def replace_once(path, old, new):
    text = path.read_text()
    if text.count(old) != 1:
        raise RuntimeError(f'{path}: expected one exact source anchor: {old[:80]!r}')
    path.write_text(text.replace(old, new, 1))

def apply():
    # These must be from the exact executable, not a regional symbol map.
    camera = (CHUNKS / 'chunk_0014_text1_80051940.c').read_text()
    for anchor in ('// 8005566C: addi    r3, r29, 148',
                   '// 80055670: bl      0x80292164', 'ctx->lr = 0x80055674u;',
                   '// 800546CC: stw     r3, -24704(r27)',
                   '// 8005496C: stw     r3, 44(r27)'):
        assert anchor in camera, anchor
    module_text = (MODULE / 'src/spm_module.c').read_text()
    assert 'openspm_set_aspect_ratio' not in module_text, 'Use pristine M10 source'
    chunk = CHUNKS / 'chunk_0158_text1_80291940.c'
    source_hash = hashlib.sha256(chunk.read_bytes()).hexdigest()
    replace_once(chunk, '#include "../generated.h"',
                 '#include "../generated.h"\nvoid openspm_r8pe01_submit_projection(CPUState* ctx);')
    replace_once(chunk, 'label_802921B8:\n    ctx->pc = 0x802921B8u;',
                 'label_802921B8:\n    ctx->pc = 0x802921B8u;\n'
                 '    openspm_r8pe01_submit_projection(ctx);')
    native = MODULE / 'openspm'
    native.mkdir(exist_ok=True)
    for name in ('display.h', 'display.c', 'r8pe01_display.c'):
        shutil.copy2(ROOT / 'native' / name, native / name)
    replace_once(MODULE / 'CMakeLists.txt', '  src/spm_module.c\n',
                 '  src/spm_module.c\n  openspm/display.c\n  openspm/r8pe01_display.c\n')
    print(f'M12 display: GXSetProjection submission hook; original chunk SHA256={source_hash}')

if __name__ == '__main__':
    apply()
