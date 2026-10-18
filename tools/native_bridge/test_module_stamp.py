#!/usr/bin/env python3
"""The native module's cache stamp (module_stamp.py): written by the build,
current only while nothing it records has changed, and the reason names what
changed."""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

import module_stamp

GAME = "a" * 64


class ModuleStampTest(unittest.TestCase):
    def test_fields(self) -> None:
        want = module_stamp.expected(GAME, "clang-freestanding", 256, 1)
        self.assertEqual(want["translator"], "40637c4683bd2820ac5b23607ee344720beb26df")
        self.assertEqual((want["openspm_abi"], want["native_abi"], want["arch"]), (1, 1, "x86_64"))
        self.assertRegex(want["transform"], r"^[0-9a-f]{64}$")
        names = {p.relative_to(module_stamp.ROOT).as_posix() for p in module_stamp.transform_files()}
        for must in ("tools/native_bridge/legacy_c_adapter_v42.c.in", "tools/native_bridge/m14_freestanding_crt.c",
                     "include/openspm/hooks.h", "state/openspm-hooks.json", "state/openspm-code-patches.json",
                     "backend/audio/openspm_ax_hle.c"):
            self.assertIn(must, names)
        self.assertFalse(any(Path(n).name.startswith("test_") for n in names))
        with self.assertRaises(RuntimeError):
            module_stamp.expected("nothex", "clang-freestanding", 256, 1)

    def test_status(self) -> None:
        want = module_stamp.expected(GAME, "clang-freestanding", 256, 1)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            self.assertEqual(module_stamp.status(out, want), (False, "no native module yet"))
            (out / "openspm_local_native.dll").write_bytes(b"MZ")
            self.assertFalse(module_stamp.status(out, want)[0])
            module_stamp.write(out, want)
            self.assertEqual(module_stamp.status(out, want), (True, "current"))
            for key, value, reason in (("game_sha256", "b" * 64, "the game changed"),
                                       ("transform", "0" * 64, "OpenSPM's module sources changed"),
                                       ("translator", "1" * 40, "the recompiler changed")):
                other = dict(want, **{key: value})
                self.assertEqual(module_stamp.status(out, other), (False, reason))
            other = module_stamp.expected(GAME, "clang-freestanding", 512, 1)
            self.assertEqual(module_stamp.status(out, other), (False, "the build options changed"))
            (out / module_stamp.STAMP_NAME).write_text("{not json")
            self.assertFalse(module_stamp.status(out, want)[0])

    def test_transform_hash_follows_sources(self) -> None:
        # A copy of the tree hashes the same; one changed byte changes it; CRLF does not.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for folder, _ in module_stamp._TRANSFORM_GLOBS:
                src = module_stamp.ROOT / folder
                (root / folder).mkdir(parents=True, exist_ok=True)
                for path in src.iterdir():
                    if path.is_file():
                        shutil.copy2(path, root / folder / path.name)
            base = module_stamp.transform_hash(root)
            self.assertEqual(base, module_stamp.transform_hash())
            bridge = root / "tools/native_bridge/legacy_c_adapter_v42.c.in"
            text = bridge.read_bytes()
            bridge.write_bytes(text.replace(b"\n", b"\r\n"))
            self.assertEqual(module_stamp.transform_hash(root), base)
            bridge.write_bytes(text + b"/* changed */\n")
            self.assertNotEqual(module_stamp.transform_hash(root), base)
            (root / "tools/native_bridge/test_new.py").write_text("x = 1\n")  # tests never count
            bridge.write_bytes(text)
            self.assertEqual(module_stamp.transform_hash(root), base)


if __name__ == "__main__":
    unittest.main()
