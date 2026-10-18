from pathlib import Path


def replace_once(path: str, old: str, new: str, label: str) -> None:
    p = Path(path)
    s = p.read_text().replace("\r\n", "\n")
    count = s.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one anchor, found {count}")
    p.write_text(s.replace(old, new, 1))


module = "m10/module/src/spm_module.c"
replace_once(
    module,
    '#include "module_tables.inc"\n',
    '#include "module_tables.inc"\n\n#include <stdio.h>\n',
    "module stdio include",
)
replace_once(
    module,
    '''static u32 g_openspm_aspect_mode = OPENSPM_ASPECT_AUTO;\n''',
    '''static u32 g_openspm_aspect_mode = OPENSPM_ASPECT_AUTO;\nstatic u32 g_openspm_persp_report_mode = 0xffffffffu;\nstatic u32 g_openspm_ortho_report_mode = 0xffffffffu;\n''',
    "projection diagnostic state",
)
replace_once(
    module,
    '''void openspm_adjust_perspective_projection(CPUState* ctx, u32 cam_entry, f64* aspect)\n{\n    const float scale = openspm_projection_scale(ctx, cam_entry);\n    if (scale != 1.0f)\n        *aspect = (f64)((float)*aspect * scale);\n}\n''',
    '''void openspm_adjust_perspective_projection(CPUState* ctx, u32 cam_entry, f64* aspect)\n{\n    const float scale = openspm_projection_scale(ctx, cam_entry);\n    if (scale != 1.0f)\n    {\n        const float before = (float)*aspect;\n        *aspect = (f64)(before * scale);\n        if (g_openspm_persp_report_mode != g_openspm_aspect_mode)\n        {\n            const u32 flags = mem_read32(ctx, cam_entry + 0x000u);\n            const s32 cam_no = (s32)mem_read32(ctx, cam_entry + 0x004u);\n            fprintf(stderr,\n                    "[OPENSPM-PROJ] perspective mode=%u cam=%d flags=%08x before=%.6f "\n                    "scale=%.6f after=%.6f\\n",\n                    g_openspm_aspect_mode, cam_no, flags, (double)before, (double)scale,\n                    (double)(float)*aspect);\n            fflush(stderr);\n            g_openspm_persp_report_mode = g_openspm_aspect_mode;\n        }\n    }\n}\n''',
    "perspective projection diagnostic",
)
replace_once(
    module,
    '''void openspm_adjust_ortho_projection(CPUState* ctx, u32 cam_entry, f64* left, f64* right)\n{\n    const float scale = openspm_projection_scale(ctx, cam_entry);\n    if (scale != 1.0f)\n    {\n        *left = (f64)((float)*left * scale);\n        *right = (f64)((float)*right * scale);\n    }\n}\n''',
    '''void openspm_adjust_ortho_projection(CPUState* ctx, u32 cam_entry, f64* left, f64* right)\n{\n    const float scale = openspm_projection_scale(ctx, cam_entry);\n    if (scale != 1.0f)\n    {\n        const float before_left = (float)*left;\n        const float before_right = (float)*right;\n        *left = (f64)(before_left * scale);\n        *right = (f64)(before_right * scale);\n        if (g_openspm_ortho_report_mode != g_openspm_aspect_mode)\n        {\n            const u32 flags = mem_read32(ctx, cam_entry + 0x000u);\n            const s32 cam_no = (s32)mem_read32(ctx, cam_entry + 0x004u);\n            fprintf(stderr,\n                    "[OPENSPM-PROJ] ortho mode=%u cam=%d flags=%08x left=%.3f->%.3f "\n                    "right=%.3f->%.3f scale=%.6f\\n",\n                    g_openspm_aspect_mode, cam_no, flags, (double)before_left,\n                    (double)(float)*left, (double)before_right, (double)(float)*right,\n                    (double)scale);\n            fflush(stderr);\n            g_openspm_ortho_report_mode = g_openspm_aspect_mode;\n        }\n    }\n}\n''',
    "ortho projection diagnostic",
)

print("M11C projection diagnostics applied")
