from pathlib import Path


def replace_once(path: str, old: str, new: str, label: str) -> None:
    p = Path(path)
    s = p.read_text().replace("\r\n", "\n")
    count = s.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one anchor, found {count}")
    p.write_text(s.replace(old, new, 1))


# M11E ultrawide follow-up, staged only. Apply after the M11 module patch and
# M11C projection diagnostics. The old hook was inserted in camDraw immediately
# after argument loads; the runtime proved the aspect setter worked but those
# helper calls never produced a projection log. Move the hook to the actual
# SDK matrix-function entries instead.
#
# Exact R8PE01 generated-source facts verified against the portable source:
#   C_MTXPerspective = 0x80278E10, chunk_0151_text1_80275940.c
#   C_MTXOrtho       = 0x80278EFC, same chunk
#   camDraw perspective call returns to 0x80055614
#   camDraw ortho call returns to 0x80055638
#   r29 is a nonvolatile GPR and still contains camDraw's live CamEntry* at
#   both matrix-function entries.
#
# Gating on LR means unrelated users of C_MTXPerspective/C_MTXOrtho remain
# byte-for-byte semantically unchanged. The existing helper keeps SPM's stock
# perspective callback and orthographic camera-number exclusions.

old_chunk = "m10/module/generated/chunks/chunk_0014_text1_80051940.c"
replace_once(
    old_chunk,
    '''\n    /* OpenSPM: convert SPM's stock 4:3/16:9 perspective to the selected output ratio. */\n    openspm_adjust_perspective_projection(ctx, ctx->gpr[29], &ctx->fpr[2]);\n''',
    '''\n    /* OpenSPM M11E: projection override moved to C_MTXPerspective entry. */\n''',
    "remove flaky camDraw perspective injection",
)
replace_once(
    old_chunk,
    '''\n    /* OpenSPM: widen eligible orthographic world/UI cameras using SPM's own exclusions. */\n    openspm_adjust_ortho_projection(ctx, ctx->gpr[29], &ctx->fpr[3], &ctx->fpr[4]);\n''',
    '''\n    /* OpenSPM M11E: projection override moved to C_MTXOrtho entry. */\n''',
    "remove flaky camDraw ortho injection",
)

matrix_chunk = "m10/module/generated/chunks/chunk_0151_text1_80275940.c"
replace_once(
    matrix_chunk,
    '''label_80278E10:\n    ctx->pc = 0x80278E10u;\n    ctx->downcount -= 19;\n''',
    '''label_80278E10:\n    ctx->pc = 0x80278E10u;\n    /* OpenSPM: camDraw -> C_MTXPerspective. PPC r29 is nonvolatile and still\n       holds CamEntry*, while f2 is the incoming aspect argument. */\n    if (ctx->lr == 0x80055614u)\n        openspm_adjust_perspective_projection(ctx, ctx->gpr[29], &ctx->fpr[2]);\n    ctx->downcount -= 19;\n''',
    "C_MTXPerspective entry hook",
)
replace_once(
    matrix_chunk,
    '''label_80278EFC:\n    ctx->pc = 0x80278EFCu;\n    ctx->downcount -= 86;\n''',
    '''label_80278EFC:\n    ctx->pc = 0x80278EFCu;\n    /* OpenSPM: camDraw -> C_MTXOrtho. f3/f4 are left/right and r29 remains\n       the live CamEntry* from the caller. */\n    if (ctx->lr == 0x80055638u)\n        openspm_adjust_ortho_projection(ctx, ctx->gpr[29], &ctx->fpr[3], &ctx->fpr[4]);\n    ctx->downcount -= 86;\n''',
    "C_MTXOrtho entry hook",
)

print("M11E robust camera projection entry hooks staged")
