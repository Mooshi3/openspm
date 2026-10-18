#!/usr/bin/env python3
"""Test launch policy, exception propagation, and (on Windows) real LFH use."""
from __future__ import annotations

import contextlib
import ctypes
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import run_windows_debug as debug


class EnvironmentTests(unittest.TestCase):
    def test_preserves_unicode_hidden_drive_and_equals(self):
        entries = ["Path=C:\\tools", "=D:=D:\\game", "USER=Mooshi🎮",
                   "EXTRA=a=b", "_no_debug_heap=0"]
        before = entries[:]
        block = debug._child_environment_block(entries)
        self.assertTrue(block.endswith("\0\0"))
        self.assertEqual(entries, before)
        self.assertEqual(block[:-2].split("\0"), sorted([
            "Path=C:\\tools", "=D:=D:\\game", "USER=Mooshi🎮",
            "EXTRA=a=b", "_NO_DEBUG_HEAP=1"], key=str.upper))

    def test_empty_environment_and_explicit_diagnostic_mode(self):
        self.assertEqual(debug._child_environment_block([]), "_NO_DEBUG_HEAP=1\0\0")
        self.assertEqual(debug._child_environment_block(
            ["_NO_DEBUG_HEAP=1", "_no_debug_heap=1"], debug_heap=True),
            "_NO_DEBUG_HEAP=0\0\0")

    def test_native_reader_uses_utf16_bytes_and_frees_block(self):
        from unittest.mock import Mock
        kernel = Mock()
        kernel.GetEnvironmentStringsW.return_value = 4096
        entries = ["FIRST=🎮", "=C:=C:\\game"]
        addresses = {}
        cursor = 4096
        for value in [*entries, ""]:
            addresses[cursor] = value
            cursor += len(value.encode("utf-16-le")) + 2
        with patch.object(ctypes, "wstring_at", side_effect=addresses.__getitem__):
            self.assertEqual(debug._native_environment_entries(kernel), entries)
        kernel.FreeEnvironmentStringsW.assert_called_once_with(4096)

    def test_native_reader_frees_on_error(self):
        from unittest.mock import Mock
        kernel = Mock()
        kernel.GetEnvironmentStringsW.return_value = 4096
        with patch.object(ctypes, "wstring_at", side_effect=RuntimeError("read failed")):
            with self.assertRaises(RuntimeError):
                debug._native_environment_entries(kernel)
        kernel.FreeEnvironmentStringsW.assert_called_once_with(4096)

    def test_create_process_receives_policy_and_keeps_crash_capture(self):
        """Exercise debug_run itself, including second-chance AV and exit code."""
        from unittest.mock import Mock
        kernel = Mock()
        kernel.GetStdHandle.return_value = None
        kernel.CreateProcessW.return_value = True
        kernel.ContinueDebugEvent.return_value = True
        def event(ptr, timeout):
            evt = ptr._obj
            if event.count == 0:
                evt.dwDebugEventCode = debug.EXCEPTION_DEBUG_EVENT
                evt.Exception.dwFirstChance = 0
                evt.Exception.ExceptionRecord.ExceptionCode = 0xC0000005
                evt.Exception.ExceptionRecord.NumberParameters = 0
            else:
                evt.dwDebugEventCode = debug.EXIT_PROCESS_DEBUG_EVENT
                evt.ExitProcess.dwExitCode = 0xC0000005
            event.count += 1
            return True
        event.count = 0
        kernel.WaitForDebugEvent.side_effect = event
        with patch.object(debug, "_kernel32", return_value=kernel), \
             patch.object(debug, "_native_environment_entries", return_value=["Path=ok"]), \
             patch.object(debug, "_capture_native_frontier") as capture, \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(debug.debug_run(["game.exe", "argument with spaces"]), 0xC0000005)
        args = kernel.CreateProcessW.call_args.args
        self.assertEqual(args[5], debug.DEBUG_ONLY_THIS_PROCESS | debug.CREATE_UNICODE_ENVIRONMENT)
        self.assertIn("_NO_DEBUG_HEAP=1\0", "".join(args[6]))
        self.assertTrue(args[4])  # inherited stdout/stderr handles
        self.assertEqual(kernel.ContinueDebugEvent.call_args_list[0].args[2],
                         debug.DBG_EXCEPTION_NOT_HANDLED)
        capture.assert_called_once()


# An actual child under DEBUG_ONLY_THIS_PROCESS, not a source-string assertion.
# Explicit LFH activation on a private heap is documented to fail under the
# debugger's default heap policy. No game data or renderer is involved.
WINDOWS_PROBE = r'''
import ctypes, json, os, sys
from ctypes import wintypes as w
k = ctypes.WinDLL("kernel32", use_last_error=True)
k.HeapCreate.argtypes = [w.DWORD, ctypes.c_size_t, ctypes.c_size_t]
k.HeapCreate.restype = w.HANDLE
k.HeapSetInformation.argtypes = [w.HANDLE, ctypes.c_int, w.LPVOID, ctypes.c_size_t]
k.HeapSetInformation.restype = w.BOOL
k.HeapQueryInformation.argtypes = [w.HANDLE, ctypes.c_int, w.LPVOID, ctypes.c_size_t, w.LPVOID]
k.HeapQueryInformation.restype = w.BOOL
k.HeapDestroy.argtypes = [w.HANDLE]
k.HeapDestroy.restype = w.BOOL
k.IsDebuggerPresent.restype = w.BOOL
heap = k.HeapCreate(0, 0, 0)
assert heap
try:
    wanted = w.ULONG(2)
    enabled = bool(k.HeapSetInformation(heap, 0, ctypes.byref(wanted), ctypes.sizeof(wanted)))
    actual = w.ULONG(999)
    assert k.HeapQueryInformation(heap, 0, ctypes.byref(actual), ctypes.sizeof(actual), None)
    print(json.dumps(dict(policy=os.environ.get("_NO_DEBUG_HEAP"),
                          unicode=os.environ.get("OPENSPM_HEAP_TEST"),
                          debugger=bool(k.IsDebuggerPresent()),
                          lfh_enabled=enabled, heap_type=actual.value)), flush=True)
finally:
    k.HeapDestroy(heap)
sys.exit(7)
'''


@unittest.skipUnless(os.name == "nt", "requires native Windows heap/debug APIs")
class WindowsIntegrationTests(unittest.TestCase):
    def probe(self, diagnostic):
        wrapper = str(Path(debug.__file__).resolve())
        command = [sys.executable, wrapper]
        if diagnostic:
            command.append("--debug-heap")
        command += ["--", sys.executable, "-c", WINDOWS_PROBE]
        environment = dict(os.environ, OPENSPM_HEAP_TEST="Mooshi 🎮")
        result = subprocess.run(command, env=environment, capture_output=True,
                                text=True, timeout=30)
        self.assertEqual(result.returncode, 7, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["debugger"])
        self.assertEqual(payload["unicode"], "Mooshi 🎮")
        return payload

    def test_normal_debugger_child_can_use_lfh(self):
        payload = self.probe(False)
        self.assertEqual(payload["policy"], "1")
        self.assertTrue(payload["lfh_enabled"], payload)
        self.assertEqual(payload["heap_type"], 2, payload)
        print("[OpenSPM heap test] normal debugger child: LFH type=2; crash debugger retained")

    def test_explicit_debug_heap_and_exit_status(self):
        payload = self.probe(True)
        self.assertEqual(payload["policy"], "0")
        print("[OpenSPM heap test] explicit diagnostic child: "
              f"LFH enabled={payload['lfh_enabled']} heap_type={payload['heap_type']}")


if __name__ == "__main__":
    unittest.main()
