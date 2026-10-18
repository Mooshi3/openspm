#!/usr/bin/env python3

import importlib.util
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "native_bridge" / "direct_calls.py"
spec = importlib.util.spec_from_file_location("m14_direct_calls", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)


def make_tree(base: pathlib.Path) -> pathlib.Path:
    source = base / "generated"
    (source / "chunks").mkdir(parents=True)
    (source / "generated.h").write_text("/* test */\n", encoding="utf-8")
    return source


class DirectCallRewriteTest(unittest.TestCase):
    def test_direct_call_stays_inside_native_chunks(self):
        with tempfile.TemporaryDirectory() as temp:
            base = pathlib.Path(temp)
            source = make_tree(base)
            chunks = source / "chunks"
            caller = chunks / "chunk_0000_text0_80001000.c"
            caller.write_text(
                "void func_80001000(CPUState* ctx) {\n"
                "label_80001000:\n"
                "    ctx->pc = 0x80001000u;\n"
                "    // 80001000: bl      0x80002000\n"
                "    {\n"
                "            ctx->lr = 0x80001004u;\n"
                "            ctx->pc = 0x80002000u;\n"
                "            return;\n"
                "    }\n"
                "label_80001004:\n"
                "    ctx->pc = 0x80001004u;\n"
                "}\n",
                encoding="utf-8",
            )
            (chunks / "chunk_0001_text0_80002000.c").write_text(
                "void func_80002000(CPUState* ctx) {\n"
                "label_80002000:\n"
                "    ctx->pc = 0x80002000u;\n"
                "}\n",
                encoding="utf-8",
            )

            output = base / "direct"
            stats = module.rewrite_tree(source, output)
            text = (output / "chunks" / caller.name).read_text(encoding="utf-8")
            self.assertEqual(stats["direct_calls"], 1)
            self.assertIn("void func_80002000(CPUState* ctx);", text)
            self.assertIn("func_80002000(ctx);", text)
            self.assertIn("goto label_80001004;", text)
            self.assertIn("dolrecomp_call_enter()", text)

    def test_cross_chunk_host_target_stays_a_side_exit(self):
        with tempfile.TemporaryDirectory() as temp:
            base = pathlib.Path(temp)
            source = make_tree(base)
            chunks = source / "chunks"
            caller = chunks / "chunk_0000_text0_80001000.c"
            original_call = (
                "    // 80001000: bl      0x80002000\n"
                "    {\n"
                "            ctx->lr = 0x80001004u;\n"
                "            ctx->pc = 0x80002000u;\n"
                "            return;\n"
                "    }\n"
            )
            caller.write_text(
                "void func_80001000(CPUState* ctx) {\n"
                "label_80001000:\n" + original_call +
                "label_80001004:\n}\n",
                encoding="utf-8",
            )
            (chunks / "chunk_0001_text0_80002000.c").write_text(
                "void func_80002000(CPUState* ctx) {\nlabel_80002000:\n}\n",
                encoding="utf-8",
            )
            output = base / "direct"
            stats = module.rewrite_tree(source, output, [0x80002000])
            text = (output / "chunks" / caller.name).read_text(encoding="utf-8")
            self.assertEqual(stats["hosted_cross_calls"], 1)
            self.assertEqual(stats["direct_calls"], 0)
            self.assertIn(original_call, text)
            self.assertNotIn("func_80002000(ctx);", text)

    def test_same_chunk_host_target_is_forced_back_to_side_exit(self):
        with tempfile.TemporaryDirectory() as temp:
            base = pathlib.Path(temp)
            source = make_tree(base)
            caller = source / "chunks" / "chunk_0000_text0_80001000.c"
            caller.write_text(
                "void func_80001000(CPUState* ctx) {\n"
                "label_80001000:\n"
                "    // 80001000: bl      0x80001020\n"
                "    {\n"
                "            ctx->lr = 0x80001004u;\n"
                "            if (ctx->downcount <= -(s64)DOLRECOMP_C_LOOP_CYCLE_BUDGET) {\n"
                "                    ctx->pc = 0x80001020u;\n"
                "                    return;\n"
                "            }\n"
                "            goto label_80001020;\n"
                "    }\n"
                "label_80001004:\n"
                "    return;\n"
                "label_80001020:\n"
                "    return;\n"
                "}\n",
                encoding="utf-8",
            )
            output = base / "direct"
            stats = module.rewrite_tree(source, output, [0x80001020])
            text = (output / "chunks" / caller.name).read_text(encoding="utf-8")
            self.assertEqual(stats["hosted_same_chunk_calls"], 1)
            self.assertIn("ctx->pc = 0x80001020u;", text)
            self.assertNotIn("goto label_80001020;", text)
            self.assertNotIn("DOLRECOMP_C_LOOP_CYCLE_BUDGET", text)

    def test_unmapped_target_is_left_untouched(self):
        with tempfile.TemporaryDirectory() as temp:
            base = pathlib.Path(temp)
            source = make_tree(base)
            caller = source / "chunks" / "chunk_0000_text0_80001000.c"
            original = (
                "void func_80001000(CPUState* ctx) {\n"
                "label_80001000:\n"
                "    // 80001000: bl      0x81234560\n"
                "    {\n"
                "            ctx->lr = 0x80001004u;\n"
                "            ctx->pc = 0x81234560u;\n"
                "            return;\n"
                "    }\n"
                "label_80001004:\n"
                "}\n"
            )
            caller.write_text(original, encoding="utf-8")
            output = base / "direct"
            stats = module.rewrite_tree(source, output)
            text = (output / "chunks" / caller.name).read_text(encoding="utf-8")
            self.assertEqual(stats["direct_calls"], 0)
            self.assertEqual(stats["unmapped_calls"], 1)
            self.assertEqual(text, original)

    def test_guest_address_parser_rejects_out_of_range(self):
        self.assertEqual(module.parse_guest_address("0x80235ab8"), 0x80235AB8)
        with self.assertRaises(Exception):
            module.parse_guest_address("0x100000000")

    def test_gx_display_list_boundaries_stay_host_side(self):
        # GXBeginDisplayList / GXEndDisplayList left the static-call manifest
        # (state/m14-host-targets.json) for the LLVM path's own host-first
        # sets: the dispatch priority and the host-call prefilter must both
        # keep them, or display lists run as ordinary guest code.
        import re
        for script, name in (("apply_llvm_dispatch_priority.py", "EXTRA_HOST_PRIORITY"),
                             ("apply_llvm_hostcall_prefilter.py", "EXTRA_MAY_HOST")):
            text = (ROOT / "tools" / "native_bridge" / script).read_text(encoding="utf-8")
            block = re.search(name + r" = \{(.*?)\n\}", text, re.S)
            self.assertIsNotNone(block, f"{script}: {name}")
            values = {int(v, 16) for v in re.findall(r"0x[0-9A-Fa-f]{8}", block.group(1))}
            self.assertIn(0x80291DC8, values, script)
            self.assertIn(0x80291E78, values, script)


if __name__ == "__main__":
    unittest.main()
