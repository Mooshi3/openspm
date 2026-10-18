#!/usr/bin/env python3
"""Game-free contract tests for dormant M14 display-list return hooks v19."""
from __future__ import annotations

import importlib.util
import pathlib
import shutil
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
MODULE_PATH = HERE / "display_list_return_hooks_v19.py"

spec = importlib.util.spec_from_file_location("display_list_return_hooks_v19", MODULE_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError("could not load display_list_return_hooks_v19.py")
v19 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v19)


def native_call(pc: int, target: int, function: int) -> str:
    ret = pc + 4
    return f'''// {pc:08X}: bl      0x{target:08X}
{{
    void func_{function:08X}(CPUState* ctx);
    ctx->lr = 0x{ret:08X}u;
    ctx->pc = 0x{target:08X}u;
    if (dolrecomp_call_enter()) {{
        func_{function:08X}(ctx);
        dolrecomp_call_leave();
        if (ctx->pc == 0x{ret:08X}u) goto label_{ret:08X};
    }}
    return;
}}
label_{ret:08X}:
    ctx->gpr[7] ^= 1u;
'''


def same_chunk_call(pc: int, target: int) -> str:
    ret = pc + 4
    return f'''// {pc:08X}: bl      0x{target:08X}
{{
    ctx->lr = 0x{ret:08X}u;
    if (ctx->downcount <= -(s64)DOLRECOMP_C_LOOP_CYCLE_BUDGET) {{
        ctx->pc = 0x{target:08X}u;
        return;
    }}
    goto label_{target:08X};
}}
label_{ret:08X}:
    ctx->gpr[8] ^= 1u;
'''


def write_fixture(root: pathlib.Path, branch_collision: bool = False) -> None:
    chunks = root / "chunks"
    chunks.mkdir(parents=True)
    begin = int(v19.BEGIN_TARGET, 16)
    end = int(v19.END_TARGET, 16)

    text_a = "#include \"generated.h\"\nvoid synthetic(CPUState* ctx) {\n"
    text_a += native_call(0x80010000, begin, 0x80291000)
    text_a += native_call(0x80010020, end, 0x80291000)
    text_a += native_call(0x80010040, begin, 0x80291000)
    text_a += native_call(0x80010060, end, 0x80291000)
    if branch_collision:
        text_a += "// 80010080: b       0x80010004\n"
    text_a += "return;\n}\n"
    (chunks / "chunk_0001_text_80010000.c").write_text(text_a, encoding="utf-8")

    text_b = "#include \"generated.h\"\nvoid synthetic2(CPUState* ctx) {\n"
    text_b += same_chunk_call(0x80020000, begin)
    text_b += same_chunk_call(0x80020020, end)
    text_b += f"label_{begin:08X}:\n    return;\n"
    text_b += f"label_{end:08X}:\n    return;\n"
    text_b += "}\n"
    (chunks / "chunk_0002_text_80020000.c").write_text(text_b, encoding="utf-8")
    (root / "generated.h").write_text("/* synthetic */\n", encoding="utf-8")


def must_fail(root: pathlib.Path, contains: str) -> None:
    try:
        v19.transform_tree(root)
    except RuntimeError as exc:
        if contains not in str(exc):
            raise AssertionError(f"unexpected failure: {exc}") from exc
        return
    raise AssertionError(f"expected failure containing {contains!r}")


def main() -> int:
    temp = pathlib.Path(tempfile.mkdtemp(prefix="openspm-v19-test-"))
    try:
        clean = temp / "clean"
        write_fixture(clean)
        begin, end, changed = v19.transform_tree(clean)
        assert (begin, end, changed) == (3, 3, 2)
        begin2, end2, changed2 = v19.transform_tree(clean)
        assert (begin2, end2, changed2) == (3, 3, 0)

        joined = "\n".join(
            p.read_text(encoding="utf-8") for p in (clean / "chunks").glob("*.c")
        )
        assert joined.count(v19.BEGIN_PRE) == 6
        assert joined.count(v19.BEGIN_AFTER) == 6
        assert joined.count(v19.END_PRE) == 6
        assert joined.count(v19.END_AFTER) == 6
        end_ret = "label_80010024:\n    {\n        int " + v19.END_AFTER
        assert end_ret in joined

        partial = temp / "partial"
        shutil.copytree(clean, partial)
        one = next((partial / "chunks").glob("*.c"))
        text = one.read_text(encoding="utf-8")
        text = text.replace(
            f"if (!{v19.BEGIN_AFTER}(ctx)) return;",
            "/* missing after hook */",
            1,
        )
        one.write_text(text, encoding="utf-8")
        must_fail(partial, "partial v19 return-hook state")

        collision = temp / "collision"
        write_fixture(collision, branch_collision=True)
        must_fail(collision, "also a guest branch target")

        check_only = temp / "check-only"
        write_fixture(check_only)
        before = {p.name: p.read_bytes() for p in (check_only / "chunks").glob("*.c")}
        assert v19.transform_tree(check_only, check_only=True) == (3, 3, 2)
        after = {p.name: p.read_bytes() for p in (check_only / "chunks").glob("*.c")}
        assert before == after
    finally:
        shutil.rmtree(temp, ignore_errors=True)

    print("continuation-safe GX display-list v19 transform contract: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
