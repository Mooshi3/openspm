#!/usr/bin/env python3
"""The first-run entry point (prepare_game.py): refuses a missing or wrong
game, skips a current module, builds a stale or missing one through the
CMake-free driver, and reports progress lines the exe can read."""
from __future__ import annotations

import contextlib
import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import module_stamp
import prepare_game


def release(root: Path, dol: bytes | None) -> dict:
    paths = prepare_game.layout(root)
    if dol is not None:
        paths["main_dol"].parent.mkdir(parents=True)
        paths["main_dol"].write_bytes(dol)
    paths["dolrecomp"].parent.mkdir(parents=True, exist_ok=True)
    paths["dolrecomp"].write_bytes(b"")
    paths["llvm_bin"].mkdir(parents=True)
    (paths["dolrecomp_source"] / "src" / "cpu").mkdir(parents=True)
    (paths["dolrecomp_source"] / "src" / "cpu" / "cpu.c").write_text("")
    return paths


class PrepareGameTest(unittest.TestCase):
    def run_prepare(self, root: Path, sha: str, runner, force: bool = False) -> tuple[int, str]:
        out = io.StringIO()
        with mock.patch.object(prepare_game, "SUPPORTED_MAIN_DOL_SHA256", sha), contextlib.redirect_stdout(out):
            rc = prepare_game.prepare(root, force=force, run=runner)
        return rc, out.getvalue()

    def test_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dol = b"fake main.dol"
            sha = prepare_game.hashlib.sha256(dol).hexdigest()
            paths = release(root, dol)
            calls = []

            def build(command):
                calls.append(command)
                paths["native"].mkdir(parents=True, exist_ok=True)
                (paths["native"] / "openspm_local_native.dll").write_bytes(b"MZ")
                module_stamp.write(paths["native"], module_stamp.expected(
                    sha, prepare_game.TOOLCHAIN, prepare_game.CHUNK_INSTRUCTIONS, prepare_game.STATE_IN_MEMORY))
                return subprocess.CompletedProcess(command, 0)

            rc, out = self.run_prepare(root, sha, build)
            self.assertEqual(rc, 0, out)
            self.assertIn("OPENSPM-PROGRESS 2/4 Preparing (no native module yet)", out)
            self.assertIn("OPENSPM-RESULT ok prepared", out)
            command = calls[0]
            for arg in ("--no-cmake", "--state-in-memory"):
                self.assertIn(arg, command)
            self.assertEqual(command[command.index("--toolchain") + 1], "clang-freestanding")
            self.assertEqual(command[command.index("--output-dir") + 1], str(paths["native"]))
            # Current: nothing is built.
            rc, out = self.run_prepare(root, sha, build)
            self.assertEqual((rc, len(calls)), (0, 1))
            self.assertIn("OPENSPM-RESULT ok the native module is current", out)
            # --force rebuilds; a stale stamp rebuilds and says why.
            self.run_prepare(root, sha, build, force=True)
            self.assertEqual(len(calls), 2)
            stamp = paths["native"] / module_stamp.STAMP_NAME
            stamp.write_text(stamp.read_text().replace(sha, "b" * 64))
            rc, out = self.run_prepare(root, sha, build)
            self.assertEqual(len(calls), 3)
            self.assertIn("Preparing (the game changed)", out)

    def test_refusals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            never = mock.Mock(side_effect=AssertionError("must not build"))
            rc, out = self.run_prepare(root, "0" * 64, never)
            self.assertEqual(rc, 1)
            self.assertIn("OPENSPM-RESULT error no extracted game", out)
            release(root, b"another game")
            rc, out = self.run_prepare(root, "0" * 64, never)
            self.assertIn("not Super Paper Mario R8PE01 revision 2", out)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dol = b"x"
            paths = release(root, dol)
            paths["dolrecomp"].unlink()
            rc, out = self.run_prepare(root, prepare_game.hashlib.sha256(dol).hexdigest(), never)
            self.assertIn("missing", out)
            # A failing build is reported, not hidden.
            paths["dolrecomp"].write_bytes(b"")
            failing = mock.Mock(return_value=subprocess.CompletedProcess([], 3))
            rc, out = self.run_prepare(root, prepare_game.hashlib.sha256(dol).hexdigest(), failing)
            self.assertEqual(rc, 1)
            self.assertIn("the module build failed (exit 3)", out)

    def test_supported_hash_is_the_known_disc(self) -> None:
        import check_bridge_compile
        self.assertEqual(prepare_game.SUPPORTED_MAIN_DOL_SHA256, check_bridge_compile.MAIN_DOL_SHA256)


if __name__ == "__main__":
    unittest.main()
