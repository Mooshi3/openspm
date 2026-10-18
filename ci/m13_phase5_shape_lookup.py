#!/usr/bin/env python3
from pathlib import Path
import shutil

from m12_native_display import replace_once, ROOT, MODULE, CHUNKS


def apply():
    # Exact R8PE01/US2 animPoseGetShapeIdx inner loop. By this point the
    # original function has already validated the pose, resolved the shape
    # table, and saved all callee-saved registers on its guest stack.
    chunk = CHUNKS / "chunk_0013_text1_8004D940.c"
    replace_once(
        chunk,
        '#include "../generated.h"',
        '#include "../generated.h"\nint openspm_r8pe01_shape_lookup_loop(CPUState* ctx);',
    )
    replace_once(
        chunk,
        'label_8004DD8C:\n    ctx->downcount -= 3;',
        '''label_8004DD8C:\n    {\n        const int native_shape = openspm_r8pe01_shape_lookup_loop(ctx);\n        if (native_shape == 2) return;\n        if (native_shape == 1) goto label_8004DDBC;\n    }\n    ctx->downcount -= 3;''',
    )

    shutil.copy2(ROOT / 'native/r8pe01_shape_lookup.c',
                 MODULE / 'openspm/r8pe01_shape_lookup.c')
    replace_once(
        MODULE / 'CMakeLists.txt',
        '  openspm/r8pe01_strcmp.c\n',
        '  openspm/r8pe01_strcmp.c\n  openspm/r8pe01_shape_lookup.c\n',
    )
    replace_once(
        MODULE / 'src/spm_module.c',
        'void openspm_configure_native_helpers(void);\n',
        'void openspm_configure_native_helpers(void);\nvoid openspm_configure_native_shape_lookup(void);\n',
    )
    replace_once(
        MODULE / 'src/spm_module.c',
        '    openspm_configure_native_helpers();\n',
        '    openspm_configure_native_helpers();\n    openspm_configure_native_shape_lookup();\n',
    )
    print('M13 Phase5 animPoseGetShapeIdx loop specialization staged')


if __name__ == '__main__':
    apply()
