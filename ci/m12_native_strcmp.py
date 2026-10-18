#!/usr/bin/env python3
from pathlib import Path
import hashlib
import shutil
from m12_native_display import replace_once, ROOT, MODULE, CHUNKS

def apply():
    chunk = CHUNKS / 'chunk_0144_text1_80259940.c'
    print('M12 strcmp: original chunk SHA256=' + hashlib.sha256(chunk.read_bytes()).hexdigest())
    replace_once(chunk, '#include "../generated.h"',
                 '#include "../generated.h"\nint openspm_r8pe01_strcmp(CPUState* ctx);')
    replace_once(chunk, 'label_8025C9C8:\n    ctx->pc = 0x8025C9C8u;',
                 'label_8025C9C8:\n'
                 '    {\n'
                 '        const int native_result = openspm_r8pe01_strcmp(ctx);\n'
                 '        if (native_result == 2) return;\n'
                 '        if (native_result == 1) goto return_dispatch_80259940;\n'
                 '    }\n'
                 '    ctx->pc = 0x8025C9C8u;')
    shutil.copy2(ROOT/'native/r8pe01_strcmp.c', MODULE/'openspm/r8pe01_strcmp.c')
    replace_once(MODULE/'CMakeLists.txt', '  openspm/display.c\n',
                 '  openspm/display.c\n  openspm/r8pe01_strcmp.c\n')
    replace_once(MODULE/'src/spm_module.c', 'SPM_EXPORT const StaticRecompModuleDesc* staticrecomp_get_module(void)\n{',
                 'void openspm_configure_native_helpers(void);\n'
                 'SPM_EXPORT const StaticRecompModuleDesc* staticrecomp_get_module(void)\n{\n'
                 '    openspm_configure_native_helpers();')

if __name__ == '__main__':
    apply()
