#!/usr/bin/env python3
"""OpenSPM hook registry: loads, the generated files match it, enabled entries
reach the host targets, and first instructions are checked against a DOL."""
from __future__ import annotations

import json
import struct
import tempfile
import unittest
from pathlib import Path

import openspm_hooks
import direct_calls
import guard_hosted_direct_entries as hosted_guard


def registry(hooks: list[dict]) -> dict:
    return {"schema_version": 1, "game_id": "R8PE01", "revision": 2, "hooks": hooks}


def write(path: Path, data: dict) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


class HookRegistryTest(unittest.TestCase):
    def test_checked_in_registry_and_generated_files(self):
        hooks = openspm_hooks.load()
        self.assertTrue(hooks)
        self.assertEqual(openspm_hooks.BRIDGE_INC.read_text(encoding="utf-8"), openspm_hooks.bridge_inc_text(),
                         "run tools/native_bridge/openspm_hooks.py --write")
        self.assertEqual(openspm_hooks.BACKEND_INC.read_text(encoding="utf-8"), openspm_hooks.backend_inc_text(),
                         "run tools/native_bridge/openspm_hooks.py --write")

    def test_enabled_hooks_join_the_host_targets(self):
        # The default manifest path adds the registry's enabled entries; an
        # explicit other path (tests, tools) gets the manifest only.
        with_hooks = direct_calls.load_host_targets()
        self.assertTrue(openspm_hooks.enabled_addresses() <= with_hooks)
        manifest = json.loads(Path(direct_calls.DEFAULT_HOST_TARGETS).read_text(encoding="utf-8"))
        platform = {int(entry["address"], 0) for entry in manifest["targets"]}
        self.assertEqual(with_hooks, platform | openspm_hooks.enabled_addresses())

    def test_enabled_only_and_generated_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write(Path(tmp) / "hooks.json", registry([
                {"name": "a", "address": "0x80004000", "expect": "0x9421FFF0", "enabled": True},
                {"name": "b", "address": "0x80004004", "expect": "0x7C0802A6", "enabled": False},
            ]))
            self.assertEqual(openspm_hooks.enabled_addresses(path), {0x80004000})
            self.assertIn("OPENSPM_M14_HOST_TARGET(0x80004000u) /* a */", openspm_hooks.bridge_inc_text(path))
            self.assertNotIn("0x80004004", openspm_hooks.bridge_inc_text(path))
            self.assertIn("OPENSPM_M14_HOST_TARGET(0x80002FF0u) /* OPENSPM_HOOK_RETURN_ADDRESS */",
                          openspm_hooks.bridge_inc_text(path))
            none = write(Path(tmp) / "none.json", registry([
                {"name": "b", "address": "0x80004004", "expect": "0x7C0802A6", "enabled": False}]))
            self.assertNotIn("0x80002FF0", openspm_hooks.bridge_inc_text(none))  # no hook: build unchanged
            text = openspm_hooks.backend_inc_text(path)
            self.assertIn("#define OPENSPM_HOOK_TABLE_COUNT 1u", text)
            self.assertIn('{UINT32_C(0x80004000), "a", UINT32_C(0x9421FFF0)},', text)

    def test_hooked_call_meets_two_host_checks(self):
        # The bridge's continue-original bypass (legacy_c_adapter_v7.c.in)
        # lets exactly two host checks pass: the dispatcher's, reached because
        # the call site became a side exit, and the first-statement guard at
        # the hooked label. Both transforms must cover a hooked address.
        hook = 0x80004010
        chunk = (
            "void func_80004000(CPUState* ctx) {\n"
            "label_80004000:\n"
            "    // 80004000: bl      0x80004010\n"
            "    {\n"
            "        ctx->lr = 0x80004004u;\n"
            "        ctx->pc = 0x80004010u;\n"
            "        return;\n"
            "    }\n"
            "label_80004004:\n"
            "    ctx->pc = ctx->lr; return;\n"
            "}\n"
            "void func_80004010(CPUState* ctx) {\n"
            "label_80004010:\n"
            "    ctx->gpr[4] = (u32)(s32)(-1);\n"
            "    ctx->pc = ctx->lr; return;\n"
            "}\n")
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "generated"
            (source / "chunks").mkdir(parents=True)
            (source / "generated.h").write_text("", encoding="utf-8")
            (source / "chunks" / "chunk_0001_text1_80004000.c").write_text(chunk, encoding="utf-8")
            out = Path(tmp) / "out"
            direct_calls.rewrite_tree(source, out, host_targets={hook})
            hosted_guard.guard_tree(out, host_targets={hook})
            text = (out / "chunks" / "chunk_0001_text1_80004000.c").read_text(encoding="utf-8")
            self.assertIn("ctx->pc = 0x80004010u;\n        return;", text)  # side exit, not a native call
            self.assertIn("label_80004010:\n    if (ctx->host_call != 0 && ctx->host_call(ctx, 0x80004010u)) return;",
                          text)

    def test_return_address_matches_header(self):
        header = (openspm_hooks.ROOT / "include" / "openspm" / "hooks.h").read_text(encoding="utf-8")
        self.assertIn(f"#define OPENSPM_HOOK_RETURN_ADDRESS UINT32_C(0x{openspm_hooks.RETURN_ADDRESS:08X})", header)
        self.assertLess(openspm_hooks.RETURN_ADDRESS, 0x80003100)

    def test_bad_entries_fail_closed(self):
        bad = [
            [{"name": "a", "address": "0x80004002", "expect": "0x0"}],                    # unaligned
            [{"name": "a", "address": "0x80004000", "expect": "0x0"},
             {"name": "a", "address": "0x80004004", "expect": "0x0"}],                    # duplicate name
            [{"name": "9a", "address": "0x80004000", "expect": "0x0"}],                   # bad name
            [{"name": "a", "address": "0x70001000", "expect": "0x0"}],                    # not MEM1 code
        ]
        with tempfile.TemporaryDirectory() as tmp:
            for i, hooks in enumerate(bad):
                path = write(Path(tmp) / f"bad{i}.json", registry(hooks))
                with self.assertRaises(RuntimeError):
                    openspm_hooks.load(path)

    def test_check_dol(self):
        # One text section at 0x80004000 holding two words.
        header = bytearray(0x100)
        struct.pack_into(">I", header, 0, 0x100)        # offset
        struct.pack_into(">I", header, 72, 0x80004000)  # address
        struct.pack_into(">I", header, 144, 8)          # size
        dol = bytes(header) + struct.pack(">II", 0x9421FFF0, 0x7C0802A6)
        with tempfile.TemporaryDirectory() as tmp:
            good = write(Path(tmp) / "good.json", registry([
                {"name": "a", "address": "0x80004000", "expect": "0x9421FFF0"},
                {"name": "b", "address": "0x80004004", "expect": "0x7C0802A6"}]))
            self.assertEqual(openspm_hooks.check_dol(dol, good), [])
            bad = write(Path(tmp) / "bad.json", registry([
                {"name": "a", "address": "0x80004000", "expect": "0x60000000"},
                {"name": "c", "address": "0x80005000", "expect": "0x0"}]))
            problems = openspm_hooks.check_dol(dol, bad)
            self.assertEqual(len(problems), 2)


if __name__ == "__main__":
    unittest.main()
