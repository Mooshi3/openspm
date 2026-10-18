#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "m14_lab"))

import probe_generated  # noqa: E402


class M14LabTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state_path = ROOT / "state" / "m14.json"
        self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
        # The probe tool's own settings: the live state keeps only the proven
        # input identity (the probe was retired once the input service took
        # over), so the probe tests get a fixture with the poll count added.
        self._fixture = tempfile.TemporaryDirectory()
        fixture = dict(self.state)
        fixture["input_probe"] = dict(self.state["input_probe"], inject_on_poll=8)
        self.probe_state_path = Path(self._fixture.name) / "m14.json"
        self.probe_state_path.write_text(json.dumps(fixture), encoding="utf-8")

    def tearDown(self) -> None:
        self._fixture.cleanup()

    def test_state_identity_and_input_contract(self) -> None:
        game = self.state["game"]
        probe = self.state["input_probe"]
        self.assertEqual(game["id"], "R8PE01")
        self.assertEqual(len(game["main_dol_sha256"]), 64)
        self.assertEqual(int(game["entry_point"], 0), 0x80006124)
        self.assertEqual(int(probe["return_address"], 0), int(probe["safety_callsite"], 0) + 4)
        button = int(probe["a_button"], 0)
        accepted = int(probe["accepted_mask"], 0)
        self.assertEqual(button & accepted, button)
        self.assertEqual(button, 0x800)

    def test_safety_a_probe_is_exact_and_idempotent(self) -> None:
        probe_args = probe_generated.load_probe_from_state(self.probe_state_path)
        with tempfile.TemporaryDirectory() as directory:
            generated = Path(directory)
            (generated / "chunks").mkdir()
            (generated / "generated.h").write_text("/* test */\n", encoding="utf-8")
            chunk = generated / "chunks" / "chunk_0138_text1_80241940.c"
            chunk.write_text(
                '#include "generated.h"\n\n'
                'void func_80241940(CPUState* ctx) {\n'
                'label_80242CF0:\n'
                '    ctx->pc = 0x80242CF0u;\n'
                '    // 80242CF0: bl      0x80235AB8\n'
                '    {\n'
                '        ctx->lr = 0x80242CF4u;\n'
                '        ctx->pc = 0x80235AB8u;\n'
                '        return;\n'
                '    }\n'
                'label_80242CF4:\n'
                '    ctx->pc = 0x80242CF4u;\n'
                '}\n',
                encoding="utf-8",
            )

            first = probe_generated.apply_safety_a_probe(generated, **probe_args)
            self.assertTrue(first["changed"])
            patched = chunk.read_text(encoding="utf-8")
            self.assertEqual(patched.count(probe_generated.PROBE_BEGIN), 1)
            self.assertEqual(patched.count(probe_generated.PROBE_END), 1)
            self.assertIn("ctx->gpr[3] = 0x00000800u;", patched)
            self.assertIn("++openspm_diag_safety_input_polls == 8u", patched)
            self.assertIn("goto label_80242CF4;", patched)

            second = probe_generated.apply_safety_a_probe(generated, **probe_args)
            self.assertFalse(second["changed"])
            self.assertEqual(patched, chunk.read_text(encoding="utf-8"))

    def test_probe_refuses_ambiguous_callsite(self) -> None:
        probe_args = probe_generated.load_probe_from_state(self.probe_state_path)
        with tempfile.TemporaryDirectory() as directory:
            generated = Path(directory)
            (generated / "chunks").mkdir()
            (generated / "generated.h").write_text("/* test */\n", encoding="utf-8")
            body = (
                '#include "generated.h"\n'
                'label_80242CF0:\n'
                '    ctx->pc = 0x80242CF0u;\n'
                '    // 80242CF0: bl      0x80235AB8\n'
            )
            for index in range(2):
                (generated / "chunks" / f"chunk_{index:04d}_text1_80241940.c").write_text(
                    body, encoding="utf-8"
                )
            with self.assertRaises(RuntimeError):
                probe_generated.apply_safety_a_probe(generated, **probe_args)

    def test_runner_preserves_bss_before_sections_rule(self) -> None:
        source = (ROOT / "tools" / "m14_lab" / "diagnostic_runner.c").read_text(encoding="utf-8")
        zero = source.index("memset(pointer, 0, bss_size);")
        sections = source.index("for (i = 0; i < 18; ++i)")
        self.assertLess(zero, sections)
        self.assertIn("openspm_wii_boot_platform_bus_read", source)
        self.assertIn("openspm_wii_boot_platform_bus_write", source)


if __name__ == "__main__":
    unittest.main()
