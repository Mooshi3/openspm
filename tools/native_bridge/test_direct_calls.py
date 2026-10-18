#!/usr/bin/env python3
"""Game-data-free regression checks for M14 direct-call normalization."""

from __future__ import annotations

import tempfile
from pathlib import Path

import direct_calls
import guard_hosted_direct_entries as hosted_guard


STALE_DIRECT = """void func_80000000(CPUState* ctx) {
label_80000000:
    ctx->pc = 0x80000000u;
    // 80000000: bl      0x802960A0
    {
        void func_80295940(CPUState* ctx);
        ctx->lr = 0x80000004u;
        ctx->pc = 0x802960A0u;
        if (dolrecomp_call_enter()) {
            func_80295940(ctx);
            dolrecomp_call_leave();
            if (ctx->pc == 0x80000004u) goto label_80000004;
        }
        return;
    }

label_80000004:
    ctx->pc = 0x80000004u;
    return;
}
"""

SAME_CHUNK = """void func_80281940(CPUState* ctx) {
label_802838B4:
    // 802838B4: bl      0x8028325C
    {
        ctx->lr = 0x802838B8u;
        if (ctx->downcount <= -(s64)DOLRECOMP_C_LOOP_CYCLE_BUDGET) {
            ctx->pc = 0x8028325Cu;
            return;
        }
        goto label_8028325C;
    }

label_802838B8:
    ctx->pc = 0x802838B8u;
    return;

label_8028325C:
    ctx->gpr[3] = 1u;
    ctx->pc = ctx->lr;
    return;
}
"""

FORMAT_DRIFT_CALLER = """void func_80001000(CPUState* ctx) {
label_80001000:
    ctx->pc = 0x80001000u;
    /* deliberately weird direct-call lowering */
    ctx->lr = 0x80001004u;
    ctx->pc = 0x80285328u;
    if (dolrecomp_call_enter()) {
        func_80285328(ctx);
        dolrecomp_call_leave();
    }
    if (ctx->pc == 0x80001004u) goto label_80001004;
    return;

label_80001004:
    ctx->pc = 0x80001004u;
    return;
}
"""

HOSTED_TARGET = """void func_80285328(CPUState* ctx) {
label_80285328:
    ctx->pc = 0x80285328u;
    ctx->gpr[3] = 0xBAD0BAD0u;
    ctx->pc = ctx->lr;
    return;
}
"""

# Model the exact post-626f cached tree: the previous wrong inline semantic is
# already first and an older backend host guard remains below it. The corrected
# BNE=0 semantic must stay authoritative without requiring WBFS regeneration.
INLINE_WG_EMPTY_TARGET = """void func_8028986C(CPUState* ctx) {
label_8028986C:
    ctx->gpr[3] = 1u; ctx->cr = (ctx->cr & 0x0FFFFFFFu) | 0x40000000u | ((ctx->xer & 0x80000000u) ? 0x10000000u : 0u); ctx->pc = ctx->lr; return;
    if (ctx->host_call != 0 && ctx->host_call(ctx, 0x8028986Cu)) return;
    ctx->pc = 0x8028986Cu;
    ctx->gpr[3] = 1u;
    ctx->cr = 0u;
    ctx->pc = ctx->lr;
    return;
}
"""

# Model the cached tree produced by the previous experiment. 0x802874F8 was
# speculatively silenced before exact DOL inspection proved that execution was
# actually blocked earlier in __AXOutInitDSP. The migration must remove this
# exact injected first statement and restore the original __AXSyncPBs body.
DEPRECATED_AX_SYNC_TARGET = """void func_802874F8(CPUState* ctx) {
label_802874F8:
    ctx->pc = ctx->lr; return;
    ctx->pc = 0x802874F8u;
    ctx->gpr[3] = 0xA5A5A5A5u;
    ctx->pc = ctx->lr;
    return;
}
"""

# Exact revision-2 DOL bytes show __AXOutInitDSP begins at 0x8028711C and then
# waits forever for a Wii DSP task-init callback. Native-audio bring-up returns
# before constructing/booting that Wii DSP task.
INLINE_AX_OUT_INIT_DSP_TARGET = """void func_8028711C(CPUState* ctx) {
label_8028711C:
    ctx->pc = 0x8028711Cu;
    ctx->gpr[3] = 0x5A5A5A5Au;
    ctx->pc = ctx->lr;
    return;
}
"""

# The next runtime reached physical Wii audio DMA programming. Exact revision-2
# DOL bytes identify these two observed functions; first-frame bring-up silences
# only these transport calls while a real PC mixer is still absent.
INLINE_AI_INIT_DMA_TARGET = """void func_80284FE8(CPUState* ctx) {
label_80284FE8:
    ctx->pc = 0x80284FE8u;
    ctx->gpr[3] = 0x11111111u;
    ctx->gpr[4] = 0x22222222u;
    ctx->pc = ctx->lr;
    return;
}
"""

INLINE_AI_START_DMA_TARGET = """void func_80285064(CPUState* ctx) {
label_80285064:
    ctx->pc = 0x80285064u;
    ctx->gpr[3] = 0x33333333u;
    ctx->pc = ctx->lr;
    return;
}
"""

EXPECTED_STALE_SIDE_EXIT = """    // 80000000: bl      0x802960A0
    {
        ctx->lr = 0x80000004u;
        ctx->pc = 0x802960A0u;
        return;
    }
"""

EXPECTED_SAME_CHUNK_SIDE_EXIT = """    // 802838B4: bl      0x8028325C
    {
        ctx->lr = 0x802838B8u;
        ctx->pc = 0x8028325Cu;
        return;
    }
"""

EXPECTED_HOST_LABEL_GUARD = (
    "label_80285328:\n"
    "    if (ctx->host_call != 0 && ctx->host_call(ctx, 0x80285328u)) return;\n"
)

EXPECTED_INLINE_WG_EMPTY = (
    "label_8028986C:\n"
    "    ctx->gpr[3] = 0u; ctx->cr = (ctx->cr & 0x0FFFFFFFu) | 0x20000000u | "
    "((ctx->xer & 0x80000000u) ? 0x10000000u : 0u); ctx->pc = ctx->lr; return;\n"
)

EXPECTED_INLINE_AX_OUT_INIT_DSP = (
    "label_8028711C:\n"
    "    ctx->pc = ctx->lr; return;\n"
)

EXPECTED_INLINE_AI_INIT_DMA = (
    "label_80284FE8:\n"
    "    ctx->pc = ctx->lr; return;\n"
)

EXPECTED_INLINE_AI_START_DMA = (
    "label_80285064:\n"
    "    ctx->pc = ctx->lr; return;\n"
)

DEPRECATED_AX_SYNC_PREFIX = (
    "label_802874F8:\n"
    "    ctx->pc = ctx->lr; return;\n"
)


def make_tree(root: Path) -> Path:
    generated = root / "generated"
    chunks = generated / "chunks"
    chunks.mkdir(parents=True)
    (generated / "generated.h").write_text("/* synthetic */\n", encoding="utf-8")
    (chunks / "chunk_0000_text1_80000000.c").write_text(
        STALE_DIRECT, encoding="utf-8", newline="\n"
    )
    (chunks / "chunk_0001_text1_80281940.c").write_text(
        SAME_CHUNK, encoding="utf-8", newline="\n"
    )
    (chunks / "chunk_0002_text1_80001000.c").write_text(
        FORMAT_DRIFT_CALLER, encoding="utf-8", newline="\n"
    )
    (chunks / "chunk_0003_text1_80285328.c").write_text(
        HOSTED_TARGET, encoding="utf-8", newline="\n"
    )
    (chunks / "chunk_0004_text1_8028986C.c").write_text(
        INLINE_WG_EMPTY_TARGET, encoding="utf-8", newline="\n"
    )
    (chunks / "chunk_0005_text1_802874F8.c").write_text(
        DEPRECATED_AX_SYNC_TARGET, encoding="utf-8", newline="\n"
    )
    (chunks / "chunk_0006_text1_8028711C.c").write_text(
        INLINE_AX_OUT_INIT_DSP_TARGET, encoding="utf-8", newline="\n"
    )
    (chunks / "chunk_0007_text1_80284FE8.c").write_text(
        INLINE_AI_INIT_DMA_TARGET, encoding="utf-8", newline="\n"
    )
    (chunks / "chunk_0008_text1_80285064.c").write_text(
        INLINE_AI_START_DMA_TARGET, encoding="utf-8", newline="\n"
    )
    return generated


def main() -> int:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        source = make_tree(root)
        first = root / "first"
        second = root / "second"
        hosted = {0x802960A0, 0x8028325C, 0x80285328}
        required = {
            0x80284FE8,
            0x80285064,
            0x80285328,
            0x8028711C,
            0x8028986C,
        }

        stats = direct_calls.rewrite_tree(source, first, hosted)
        guard_stats = hosted_guard.guard_tree(first, hosted, require_targets=required)
        stale_text = (first / "chunks" / "chunk_0000_text1_80000000.c").read_text(
            encoding="utf-8"
        )
        same_text = (first / "chunks" / "chunk_0001_text1_80281940.c").read_text(
            encoding="utf-8"
        )
        drift_text = (first / "chunks" / "chunk_0002_text1_80001000.c").read_text(
            encoding="utf-8"
        )
        target_text = (first / "chunks" / "chunk_0003_text1_80285328.c").read_text(
            encoding="utf-8"
        )
        inline_text = (first / "chunks" / "chunk_0004_text1_8028986C.c").read_text(
            encoding="utf-8"
        )
        deprecated_ax_text = (
            first / "chunks" / "chunk_0005_text1_802874F8.c"
        ).read_text(encoding="utf-8")
        ax_init_text = (
            first / "chunks" / "chunk_0006_text1_8028711C.c"
        ).read_text(encoding="utf-8")
        ai_init_dma_text = (
            first / "chunks" / "chunk_0007_text1_80284FE8.c"
        ).read_text(encoding="utf-8")
        ai_start_dma_text = (
            first / "chunks" / "chunk_0008_text1_80285064.c"
        ).read_text(encoding="utf-8")

        if stats["rehosted_direct_calls"] != 1:
            raise AssertionError(f"expected one re-hosted call, got {stats}")
        if stats["hosted_same_chunk_calls"] != 1:
            raise AssertionError(f"expected one hosted same-chunk call, got {stats}")
        if EXPECTED_STALE_SIDE_EXIT not in stale_text:
            raise AssertionError("stale native call was not converted to a host side exit")
        if "func_80295940(ctx);" in stale_text:
            raise AssertionError("stale native target call survived re-hosting")
        if EXPECTED_SAME_CHUNK_SIDE_EXIT not in same_text:
            raise AssertionError("same-chunk hosted call was not converted to a side exit")
        if "goto label_8028325C;" in same_text:
            raise AssertionError("same-chunk hosted target still bypasses host dispatch")

        if "if (dolrecomp_call_enter()) {" not in drift_text:
            raise AssertionError("format-drift caller was unexpectedly rewritten")
        if guard_stats["guarded_entries"] != 6:
            raise AssertionError(f"expected six semantic-label guards, got {guard_stats}")
        if guard_stats["hosted_labels"] != 2:
            raise AssertionError(f"expected two backend-hosted labels, got {guard_stats}")
        if guard_stats["inline_labels"] != 4:
            raise AssertionError(f"expected four inline semantic labels, got {guard_stats}")
        if guard_stats["semantic_labels"] != 6:
            raise AssertionError(f"expected six semantic labels, got {guard_stats}")
        if guard_stats["deprecated_inline_removed"] != 1:
            raise AssertionError(f"expected one deprecated AX-sync inline removal, got {guard_stats}")
        if EXPECTED_HOST_LABEL_GUARD not in target_text:
            raise AssertionError("hosted target label was not guarded")
        if target_text.index("ctx->host_call") > target_text.index("ctx->gpr[3]"):
            raise AssertionError("hosted body can execute before classifier guard")

        # Broadway WPAR bit 0 is BNE (buffer not empty). Native synchronous WG
        # transport therefore corresponds to raw BNE=0. The original andi.
        # leaves r3=0, sets CR0[EQ], mirrors XER[SO], and clears LT/GT.
        if EXPECTED_INLINE_WG_EMPTY not in inline_text:
            raise AssertionError("WG-empty inline semantic was not injected exactly")
        corrected_index = inline_text.index("ctx->gpr[3] = 0u")
        old_inline_index = inline_text.index("ctx->gpr[3] = 1u; ctx->cr =")
        stale_guard_index = inline_text.index("ctx->host_call(ctx, 0x8028986Cu)")
        body_index = inline_text.rindex("ctx->gpr[3] = 1u")
        if not (corrected_index < old_inline_index < stale_guard_index < body_index):
            raise AssertionError("stale WG inline/host/body can execute before corrected semantic")

        # The old AX-sync experiment must be actively removed from a cached
        # staged tree, not merely omitted from the new target set.
        if DEPRECATED_AX_SYNC_PREFIX in deprecated_ax_text:
            raise AssertionError("deprecated AX-sync silent semantic survived migration")
        if "ctx->gpr[3] = 0xA5A5A5A5u" not in deprecated_ax_text:
            raise AssertionError("AX-sync original generated body was not preserved")

        # Silent DSP bootstrap is a void semantic: preserve architectural state
        # except PC, which returns through LR before Wii DSP task construction.
        if EXPECTED_INLINE_AX_OUT_INIT_DSP not in ax_init_text:
            raise AssertionError("AX DSP-bootstrap silent semantic was not injected exactly")
        ax_inline_index = ax_init_text.index("ctx->pc = ctx->lr; return;")
        ax_body_index = ax_init_text.index("ctx->gpr[3] = 0x5A5A5A5Au")
        if ax_inline_index > ax_body_index:
            raise AssertionError("AX DSP-bootstrap Wii body can execute before silent semantic")

        # Runtime-observed AI DMA transport is temporarily silent for graphics
        # bring-up. Both void functions must return before any original body.
        if EXPECTED_INLINE_AI_INIT_DMA not in ai_init_dma_text:
            raise AssertionError("AIInitDMA silent inline semantic was not injected exactly")
        if EXPECTED_INLINE_AI_START_DMA not in ai_start_dma_text:
            raise AssertionError("AIStartDMA silent inline semantic was not injected exactly")
        if ai_init_dma_text.index("ctx->pc = ctx->lr; return;") > ai_init_dma_text.index("0x11111111u"):
            raise AssertionError("AIInitDMA Wii body can execute before silent semantic")
        if ai_start_dma_text.index("ctx->pc = ctx->lr; return;") > ai_start_dma_text.index("0x33333333u"):
            raise AssertionError("AIStartDMA Wii body can execute before silent semantic")

        second_stats = direct_calls.rewrite_tree(first, second, hosted)
        second_guard_stats = hosted_guard.guard_tree(second, hosted, require_targets=required)
        for name in (
            "chunk_0000_text1_80000000.c",
            "chunk_0001_text1_80281940.c",
            "chunk_0002_text1_80001000.c",
            "chunk_0003_text1_80285328.c",
            "chunk_0004_text1_8028986C.c",
            "chunk_0005_text1_802874F8.c",
            "chunk_0006_text1_8028711C.c",
            "chunk_0007_text1_80284FE8.c",
            "chunk_0008_text1_80285064.c",
        ):
            first_text = (first / "chunks" / name).read_text(encoding="utf-8")
            second_text = (second / "chunks" / name).read_text(encoding="utf-8")
            if second_text != first_text:
                raise AssertionError(f"normalization is not idempotent for {name}")
        if second_stats["rehosted_direct_calls"] != 0:
            raise AssertionError(f"second normalization re-hosted a direct call: {second_stats}")
        if second_stats["hosted_same_chunk_calls"] != 0:
            raise AssertionError(f"second normalization changed a same-chunk call: {second_stats}")
        if second_guard_stats["guarded_entries"] != 0:
            raise AssertionError(f"second guard pass changed a semantic label: {second_guard_stats}")
        if second_guard_stats["deprecated_inline_removed"] != 0:
            raise AssertionError(f"second guard pass removed stale semantics: {second_guard_stats}")

    print("direct-call rehosting + hosted-label/inline-semantic guard: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
