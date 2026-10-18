#!/usr/bin/env python3
"""Game-data-free migration contract for GX display-list bridge v19.

Keep proving that exact cached v18 transient hooks can be retired safely, but the
active architecture is now the continuation-safe v19 path: Begin/End are absent
from the host-target manifest and local CMake builds legacy_c_adapter_v11.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import retire_native_display_list_hooks as retired_dl

HOOKED_BEGIN = """void func_80001000(CPUState* ctx) {
label_80001000:
    // 80001000: bl      0x80291DC8
    {
        void func_80290000(CPUState* ctx);
        void openspm_m14_display_list_begin_after_native(CPUState* ctx, u32 buffer, u32 capacity);
        const u32 openspm_dl_buffer = ctx->gpr[3];
        const u32 openspm_dl_capacity = ctx->gpr[4];
        ctx->lr = 0x80001004u;
        ctx->pc = 0x80291DC8u;
        if (dolrecomp_call_enter()) {
            func_80290000(ctx);
            dolrecomp_call_leave();
            if (ctx->pc == 0x80001004u) {
                openspm_m14_display_list_begin_after_native(ctx, openspm_dl_buffer, openspm_dl_capacity);
                if (ctx->pc == 0x80001004u) goto label_80001004;
            }
        }
        return;
    }
label_80001004:
    return;
}
"""

HOOKED_END = """void func_80002000(CPUState* ctx) {
label_80002000:
    // 80002000: bl      0x80291E78
    {
        void func_80290000(CPUState* ctx);
        void openspm_m14_display_list_end_after_native(CPUState* ctx);
        ctx->lr = 0x80002004u;
        ctx->pc = 0x80291E78u;
        if (dolrecomp_call_enter()) {
            func_80290000(ctx);
            dolrecomp_call_leave();
            if (ctx->pc == 0x80002004u) {
                openspm_m14_display_list_end_after_native(ctx);
                if (ctx->pc == 0x80002004u) goto label_80002004;
            }
        }
        return;
    }
label_80002004:
    return;
}
"""


def main() -> int:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        chunks = root / "chunks"
        chunks.mkdir(parents=True)
        (root / "generated.h").write_text("/* synthetic */\n", encoding="utf-8")
        begin_path = chunks / "chunk_0000_text1_80001000.c"
        end_path = chunks / "chunk_0001_text1_80002000.c"
        begin_path.write_text(HOOKED_BEGIN, encoding="utf-8", newline="\n")
        end_path.write_text(HOOKED_END, encoding="utf-8", newline="\n")

        stats = retired_dl.retire_tree(root)
        assert stats == {
            "begin_hooks_removed": 1,
            "end_hooks_removed": 1,
            "changed_files": 2,
        }, stats

        begin = begin_path.read_text(encoding="utf-8")
        end = end_path.read_text(encoding="utf-8")
        assert "openspm_m14_display_list_begin_after_native" not in begin
        assert "openspm_m14_display_list_end_after_native" not in end
        assert "void func_80290000(CPUState* ctx);" in begin
        assert "ctx->pc = 0x80291DC8u;" in begin
        assert "ctx->pc = 0x80291E78u;" in end

        # Historical retirement remains safe on every ordinary updater.
        verify = retired_dl.retire_tree(root)
        assert verify == {
            "begin_hooks_removed": 0,
            "end_hooks_removed": 0,
            "changed_files": 0,
        }, verify

    repo = Path(__file__).resolve().parents[2]
    host_targets = (repo / "state" / "m14-host-targets.json").read_text(
        encoding="utf-8"
    )
    cmake = (repo / "tools" / "native_bridge" / "CMakeLists.txt").read_text(
        encoding="utf-8"
    )
    renormalize = (repo / "tools" / "native_bridge" / "renormalize_staged.py").read_text(
        encoding="utf-8"
    )

    assert '"0x80291DC8"' not in host_targets
    assert '"0x80291E78"' not in host_targets
    assert "legacy_c_adapter_v11.c.in" in cmake
    assert 'add_library(openspm_local_native SHARED\n    "${CMAKE_CURRENT_LIST_DIR}/legacy_c_adapter_v11.c.in"' in cmake
    assert "display_list_return_hooks_v19 as native_dl_v19" in renormalize
    assert "native_dl_v19.transform_tree(output)" in renormalize
    assert "verify_v19_stats != (3, 3, 0)" in renormalize

    print("native GX display-list v18 retirement + v19 promotion contract: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
