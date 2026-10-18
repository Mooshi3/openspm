#!/usr/bin/env python3
"""Run OpenSPMNative under the Windows debug API and expose fatal exception details.

Keep the ordinary Windows heap while capturing native exceptions. Creating a
process with DEBUG_ONLY_THIS_PROCESS otherwise enables Windows heap debugging
and disables the low-fragmentation heap, even for an optimized Release binary.
That is inappropriate for the normal player/performance launcher. The child
receives _NO_DEBUG_HEAP=1 before Windows initializes any of its heaps. Explicit
--debug-heap restores the old allocation-diagnostic behavior.

All non-breakpoint exceptions still go to the debuggee's normal Windows
exception machinery with DBG_EXCEPTION_NOT_HANDLED.
"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import os
import struct
import subprocess
import sys

DEBUG_ONLY_THIS_PROCESS = 0x00000002
CREATE_UNICODE_ENVIRONMENT = 0x00000400
STARTF_USESTDHANDLES = 0x00000100
INFINITE = 0xFFFFFFFF
DBG_CONTINUE = 0x00010002
DBG_EXCEPTION_NOT_HANDLED = 0x80010001

EXCEPTION_DEBUG_EVENT = 1
EXIT_PROCESS_DEBUG_EVENT = 5
EXCEPTION_BREAKPOINT = 0x80000003
STATUS_STACK_BUFFER_OVERRUN = 0xC0000409

STD_INPUT_HANDLE = -10
STD_OUTPUT_HANDLE = -11
STD_ERROR_HANDLE = -12

TH32CS_SNAPMODULE = 0x00000008
TH32CS_SNAPMODULE32 = 0x00000010
THREAD_GET_CONTEXT = 0x0008
THREAD_QUERY_INFORMATION = 0x0040

CONTEXT_AMD64 = 0x00100000
CONTEXT_CONTROL = CONTEXT_AMD64 | 0x00000001
CONTEXT_INTEGER = CONTEXT_AMD64 | 0x00000002
CONTEXT_FLAGS = CONTEXT_CONTROL | CONTEXT_INTEGER
CONTEXT_X64_SIZE = 1232
CONTEXT_FLAGS_OFFSET = 48
CONTEXT_RSP_OFFSET = 152
CONTEXT_RBP_OFFSET = 160
CONTEXT_RIP_OFFSET = 248
STACK_SCAN_BYTES = 32768
STACK_SCAN_CHUNK = 4096
STACK_CANDIDATE_LIMIT = 24

FAST_FAIL_NAMES = {
    0: "LEGACY_GS_VIOLATION",
    1: "VTGUARD_CHECK_FAILURE",
    2: "STACK_COOKIE_CHECK_FAILURE",
    3: "CORRUPT_LIST_ENTRY",
    4: "INCORRECT_STACK",
    5: "INVALID_ARG",
    6: "GS_COOKIE_INIT",
    7: "FATAL_APP_EXIT",
    8: "RANGE_CHECK_FAILURE",
    9: "UNSAFE_REGISTRY_ACCESS",
    10: "GUARD_ICALL_CHECK_FAILURE",
    11: "GUARD_WRITE_CHECK_FAILURE",
    12: "INVALID_FIBER_SWITCH",
    13: "INVALID_SET_OF_CONTEXT",
    14: "INVALID_REFERENCE_COUNT",
    15: "INVALID_JUMP_BUFFER",
    16: "MRDATA_MODIFIED",
    17: "CERTIFICATION_FAILURE",
    18: "INVALID_EXCEPTION_CHAIN",
    19: "CRYPTO_LIBRARY",
    20: "INVALID_CALL_IN_DLL_CALLOUT",
    21: "INVALID_IMAGE_BASE",
    22: "DLOAD_PROTECTION_FAILURE",
    23: "UNSAFE_EXTENSION_CALL",
    24: "DEPRECATED_SERVICE_INVOKED",
    25: "INVALID_BUFFER_ACCESS",
    26: "INVALID_BALANCED_TREE",
    27: "INVALID_NEXT_THREAD",
    28: "GUARD_ICALL_CHECK_SUPPRESSED",
    29: "APPCONTAINER_VIOLATION",
    30: "INVALID_CONTROL_STACK",
    31: "SET_CONTEXT_DENIED",
    32: "INVALID_IAT",
    33: "HEAP_METADATA_CORRUPTION",
    34: "PAYLOAD_RESTRICTION_VIOLATION",
    35: "LOW_LABEL_ACCESS_DENIED",
    36: "ENCLAVE_CALL_FAILURE",
}


class EXCEPTION_RECORD(ctypes.Structure):
    pass


PEXCEPTION_RECORD = ctypes.POINTER(EXCEPTION_RECORD)
EXCEPTION_RECORD._fields_ = [
    ("ExceptionCode", wintypes.DWORD),
    ("ExceptionFlags", wintypes.DWORD),
    ("ExceptionRecord", PEXCEPTION_RECORD),
    ("ExceptionAddress", wintypes.LPVOID),
    ("NumberParameters", wintypes.DWORD),
    ("ExceptionInformation", ctypes.c_size_t * 15),
]


class EXCEPTION_DEBUG_INFO(ctypes.Structure):
    _fields_ = [
        ("ExceptionRecord", EXCEPTION_RECORD),
        ("dwFirstChance", wintypes.DWORD),
    ]


class EXIT_PROCESS_DEBUG_INFO(ctypes.Structure):
    _fields_ = [("dwExitCode", wintypes.DWORD)]


class DEBUG_EVENT_UNION(ctypes.Union):
    _fields_ = [
        ("Exception", EXCEPTION_DEBUG_INFO),
        ("ExitProcess", EXIT_PROCESS_DEBUG_INFO),
        ("_padding", ctypes.c_byte * 160),
    ]


class DEBUG_EVENT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [
        ("dwDebugEventCode", wintypes.DWORD),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
        ("u", DEBUG_EVENT_UNION),
    ]


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


class MODULEENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("th32ModuleID", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("GlblcntUsage", wintypes.DWORD),
        ("ProccntUsage", wintypes.DWORD),
        ("modBaseAddr", ctypes.POINTER(wintypes.BYTE)),
        ("modBaseSize", wintypes.DWORD),
        ("hModule", wintypes.HMODULE),
        ("szModule", wintypes.WCHAR * 256),
        ("szExePath", wintypes.WCHAR * 260),
    ]


def _kernel32():
    if os.name != "nt":
        raise RuntimeError("run_windows_debug.py is Windows-only")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    kernel32.CreateProcessW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.BOOL,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.LPCWSTR,
        ctypes.POINTER(STARTUPINFOW),
        ctypes.POINTER(PROCESS_INFORMATION),
    ]
    kernel32.CreateProcessW.restype = wintypes.BOOL

    # Read the native block, rather than reconstructing it from os.environ:
    # preserve Windows' hidden =C: drive-directory entries as well as Unicode.
    kernel32.GetEnvironmentStringsW.argtypes = []
    kernel32.GetEnvironmentStringsW.restype = wintypes.LPVOID
    kernel32.FreeEnvironmentStringsW.argtypes = [wintypes.LPVOID]
    kernel32.FreeEnvironmentStringsW.restype = wintypes.BOOL

    kernel32.WaitForDebugEvent.argtypes = [
        ctypes.POINTER(DEBUG_EVENT),
        wintypes.DWORD,
    ]
    kernel32.WaitForDebugEvent.restype = wintypes.BOOL

    kernel32.ContinueDebugEvent.argtypes = [
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    kernel32.ContinueDebugEvent.restype = wintypes.BOOL

    kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
    kernel32.GetStdHandle.restype = wintypes.HANDLE

    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    kernel32.ExitProcess.argtypes = [wintypes.UINT]
    kernel32.ExitProcess.restype = None

    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Module32FirstW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(MODULEENTRY32W),
    ]
    kernel32.Module32FirstW.restype = wintypes.BOOL
    kernel32.Module32NextW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(MODULEENTRY32W),
    ]
    kernel32.Module32NextW.restype = wintypes.BOOL

    kernel32.OpenThread.argtypes = [
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    ]
    kernel32.OpenThread.restype = wintypes.HANDLE
    kernel32.GetThreadContext.argtypes = [wintypes.HANDLE, wintypes.LPVOID]
    kernel32.GetThreadContext.restype = wintypes.BOOL
    kernel32.ReadProcessMemory.argtypes = [
        wintypes.HANDLE,
        wintypes.LPCVOID,
        wintypes.LPVOID,
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    kernel32.ReadProcessMemory.restype = wintypes.BOOL
    return kernel32


def _signed32(value: int) -> int:
    return ctypes.c_int32(value & 0xFFFFFFFF).value


def _enumerate_modules(kernel32, pid: int) -> list[tuple[int, int, str, str]]:
    snapshot = kernel32.CreateToolhelp32Snapshot(
        TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid
    )
    invalid = ctypes.c_void_p(-1).value
    snapshot_value = ctypes.cast(snapshot, ctypes.c_void_p).value
    if snapshot_value in (None, invalid):
        return []

    modules: list[tuple[int, int, str, str]] = []
    try:
        entry = MODULEENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        ok = kernel32.Module32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            base = ctypes.cast(entry.modBaseAddr, ctypes.c_void_p).value or 0
            modules.append(
                (
                    int(base),
                    int(entry.modBaseSize),
                    str(entry.szModule),
                    str(entry.szExePath),
                )
            )
            entry = MODULEENTRY32W()
            entry.dwSize = ctypes.sizeof(entry)
            ok = kernel32.Module32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return modules


def _resolve_module(
    modules: list[tuple[int, int, str, str]], address: int
) -> str:
    for base, size, name, path in modules:
        if base <= address < base + size:
            label = name or os.path.basename(path) or "(module)"
            return f"{label}+0x{address - base:X}"
    return "(unknown)"


def _capture_native_frontier(
    kernel32,
    process_handle,
    pid: int,
    thread_id: int,
    exception_address: int,
) -> None:
    modules = _enumerate_modules(kernel32, pid)
    print(
        "[OpenSPMWinDbg] exception module="
        f"{_resolve_module(modules, exception_address)}",
        file=sys.stderr,
        flush=True,
    )

    thread = kernel32.OpenThread(
        THREAD_GET_CONTEXT | THREAD_QUERY_INFORMATION, False, thread_id
    )
    if not thread:
        print(
            f"[OpenSPMWinDbg] OpenThread({thread_id}) failed: "
            f"Win32 {ctypes.get_last_error()}",
            file=sys.stderr,
            flush=True,
        )
        return

    try:
        owner = ctypes.create_string_buffer(CONTEXT_X64_SIZE + 15)
        owner_address = ctypes.addressof(owner)
        context_address = (owner_address + 15) & ~15
        ctypes.memset(context_address, 0, CONTEXT_X64_SIZE)
        ctypes.c_uint32.from_address(
            context_address + CONTEXT_FLAGS_OFFSET
        ).value = CONTEXT_FLAGS

        if not kernel32.GetThreadContext(
            thread, ctypes.c_void_p(context_address)
        ):
            print(
                "[OpenSPMWinDbg] GetThreadContext failed: "
                f"Win32 {ctypes.get_last_error()}",
                file=sys.stderr,
                flush=True,
            )
            return

        rsp = ctypes.c_uint64.from_address(
            context_address + CONTEXT_RSP_OFFSET
        ).value
        rbp = ctypes.c_uint64.from_address(
            context_address + CONTEXT_RBP_OFFSET
        ).value
        rip = ctypes.c_uint64.from_address(
            context_address + CONTEXT_RIP_OFFSET
        ).value
        print(
            "[OpenSPMWinDbg] native frontier "
            f"rip=0x{rip:016X} rsp=0x{rsp:016X} rbp=0x{rbp:016X} "
            f"rip_module={_resolve_module(modules, rip)}",
            file=sys.stderr,
            flush=True,
        )

        stack = bytearray()
        for offset in range(0, STACK_SCAN_BYTES, STACK_SCAN_CHUNK):
            block = (ctypes.c_ubyte * STACK_SCAN_CHUNK)()
            read = ctypes.c_size_t(0)
            ok = kernel32.ReadProcessMemory(
                process_handle,
                ctypes.c_void_p(rsp + offset),
                ctypes.byref(block),
                STACK_SCAN_CHUNK,
                ctypes.byref(read),
            )
            if not ok or read.value == 0:
                break
            stack.extend(bytes(block[: read.value]))
            if read.value < STACK_SCAN_CHUNK:
                break

        candidates: list[str] = []
        seen: set[int] = set()
        for offset in range(0, len(stack) - 7, 8):
            value = struct.unpack_from("<Q", stack, offset)[0]
            if value in seen:
                continue
            label = _resolve_module(modules, value)
            if label == "(unknown)":
                continue
            seen.add(value)
            candidates.append(
                f"sp+0x{offset:X}=0x{value:016X}({label})"
            )
            if len(candidates) >= STACK_CANDIDATE_LIMIT:
                break

        if candidates:
            print(
                "[OpenSPMWinDbg] stack module candidates: "
                + " | ".join(candidates),
                file=sys.stderr,
                flush=True,
            )
        else:
            print(
                "[OpenSPMWinDbg] stack module candidates: none in readable top "
                f"{len(stack)} bytes",
                file=sys.stderr,
                flush=True,
            )
    finally:
        kernel32.CloseHandle(thread)


def self_test() -> int:
    kernel32 = _kernel32()
    if ctypes.sizeof(ctypes.c_void_p) != 8:
        print(
            "[OpenSPMWinDbg] self-test requires the x64 Python used by M14.",
            file=sys.stderr,
        )
        return 2

    expected = {
        "EXCEPTION_RECORD": 152,
        "EXCEPTION_DEBUG_INFO": 160,
        "DEBUG_EVENT": 176,
        "MODULEENTRY32W": 1080,
    }
    actual = {
        "EXCEPTION_RECORD": ctypes.sizeof(EXCEPTION_RECORD),
        "EXCEPTION_DEBUG_INFO": ctypes.sizeof(EXCEPTION_DEBUG_INFO),
        "DEBUG_EVENT": ctypes.sizeof(DEBUG_EVENT),
        "MODULEENTRY32W": ctypes.sizeof(MODULEENTRY32W),
    }
    for name, expected_size in expected.items():
        if actual[name] != expected_size:
            print(
                f"[OpenSPMWinDbg] self-test ABI mismatch: {name} "
                f"size={actual[name]} expected={expected_size}",
                file=sys.stderr,
            )
            return 2

    if (
        not kernel32.CreateProcessW
        or not kernel32.WaitForDebugEvent
        or not kernel32.GetThreadContext
        or not kernel32.ReadProcessMemory
    ):
        print(
            "[OpenSPMWinDbg] self-test could not bind Windows debug APIs.",
            file=sys.stderr,
        )
        return 2

    print(
        "[OpenSPMWinDbg] self-test PASS "
        f"(DEBUG_EVENT={actual['DEBUG_EVENT']} bytes, "
        f"MODULEENTRY32W={actual['MODULEENTRY32W']} bytes, x64 Windows ABI)"
    )
    return 0


def _child_environment_block(entries: list[str], *, debug_heap: bool = False) -> str:
    """Return a sorted, double-NUL-terminated block without mutating our parent."""
    # Windows names are case insensitive. Override inherited policy explicitly;
    # a stale shell setting must not silently contaminate normal player runs.
    entries = [entry for entry in entries
               if not entry.upper().startswith("_NO_DEBUG_HEAP=")]
    entries.append("_NO_DEBUG_HEAP=" + ("0" if debug_heap else "1"))
    return "\0".join(sorted(entries, key=str.upper)) + "\0\0"


def _native_environment_entries(kernel32) -> list[str]:
    block = kernel32.GetEnvironmentStringsW()
    if not block:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        entries = []
        cursor = int(block)
        while True:
            entry = ctypes.wstring_at(cursor)
            if not entry:
                return entries
            entries.append(entry)
            # Windows WCHAR is UTF-16, including surrogate pairs. Python len()
            # counts code points, so it is not a correct pointer increment.
            cursor += len(entry.encode("utf-16-le", errors="surrogatepass")) + 2
    finally:
        kernel32.FreeEnvironmentStringsW(block)


def debug_run(command: list[str], *, debug_heap: bool = False) -> int:
    if not command:
        raise ValueError("missing child command")

    kernel32 = _kernel32()
    command_line = subprocess.list2cmdline(command)
    mutable_command_line = ctypes.create_unicode_buffer(command_line)
    environment = ctypes.create_unicode_buffer(_child_environment_block(
        _native_environment_entries(kernel32), debug_heap=debug_heap))

    startup = STARTUPINFOW()
    startup.cb = ctypes.sizeof(startup)
    startup.dwFlags = STARTF_USESTDHANDLES
    startup.hStdInput = kernel32.GetStdHandle(STD_INPUT_HANDLE)
    startup.hStdOutput = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
    startup.hStdError = kernel32.GetStdHandle(STD_ERROR_HANDLE)

    process = PROCESS_INFORMATION()
    ok = kernel32.CreateProcessW(
        command[0],
        mutable_command_line,
        None,
        None,
        True,
        DEBUG_ONLY_THIS_PROCESS | CREATE_UNICODE_ENVIRONMENT,
        environment,
        None,
        ctypes.byref(startup),
        ctypes.byref(process),
    )
    if not ok:
        error = ctypes.get_last_error()
        print(
            f"[OpenSPMWinDbg] CreateProcessW failed: Win32 {error}",
            file=sys.stderr,
            flush=True,
        )
        return 2

    print(
        f"[OpenSPMWinDbg] debugger attached pid={process.dwProcessId}; "
        "fail-fast subtype/second-chance native frontier capture active; "
        f"heap_policy={'debug' if debug_heap else 'normal'} "
        f"_NO_DEBUG_HEAP={'0' if debug_heap else '1'}",
        file=sys.stderr,
        flush=True,
    )

    exit_code: int | None = None
    captured_frontier = False
    try:
        while True:
            event = DEBUG_EVENT()
            if not kernel32.WaitForDebugEvent(ctypes.byref(event), INFINITE):
                error = ctypes.get_last_error()
                print(
                    f"[OpenSPMWinDbg] WaitForDebugEvent failed: Win32 {error}",
                    file=sys.stderr,
                    flush=True,
                )
                return 2

            continue_status = DBG_CONTINUE
            if event.dwDebugEventCode == EXCEPTION_DEBUG_EVENT:
                info = event.Exception
                record = info.ExceptionRecord
                code = int(record.ExceptionCode)
                first_chance = int(info.dwFirstChance)

                if code == EXCEPTION_BREAKPOINT:
                    continue_status = DBG_CONTINUE
                else:
                    continue_status = DBG_EXCEPTION_NOT_HANDLED
                    if code == STATUS_STACK_BUFFER_OVERRUN or not first_chance:
                        address = ctypes.cast(
                            record.ExceptionAddress, ctypes.c_void_p
                        ).value or 0
                        parameter_count = min(int(record.NumberParameters), 15)
                        parameters = [
                            int(record.ExceptionInformation[i])
                            for i in range(parameter_count)
                        ]
                        params_text = ",".join(
                            f"0x{value:016X}" for value in parameters[:4]
                        )
                        print(
                            "[OpenSPMWinDbg] exception "
                            f"code=0x{code:08X} first_chance={first_chance} "
                            f"thread={event.dwThreadId} address=0x{address:016X} "
                            f"params=[{params_text}]",
                            file=sys.stderr,
                            flush=True,
                        )

                        if code == STATUS_STACK_BUFFER_OVERRUN:
                            subtype = parameters[0] if parameters else None
                            if subtype is None:
                                print(
                                    "[OpenSPMWinDbg] FAIL_FAST subtype=(missing)",
                                    file=sys.stderr,
                                    flush=True,
                                )
                            else:
                                name = FAST_FAIL_NAMES.get(
                                    subtype, f"UNKNOWN_{subtype}"
                                )
                                print(
                                    "[OpenSPMWinDbg] FAIL_FAST "
                                    f"subtype={subtype} ({name})",
                                    file=sys.stderr,
                                    flush=True,
                                )

                        if not captured_frontier:
                            _capture_native_frontier(
                                kernel32,
                                process.hProcess,
                                event.dwProcessId,
                                event.dwThreadId,
                                address,
                            )
                            captured_frontier = True

            elif event.dwDebugEventCode == EXIT_PROCESS_DEBUG_EVENT:
                exit_code = int(event.ExitProcess.dwExitCode)

            if not kernel32.ContinueDebugEvent(
                event.dwProcessId, event.dwThreadId, continue_status
            ):
                error = ctypes.get_last_error()
                print(
                    f"[OpenSPMWinDbg] ContinueDebugEvent failed: Win32 {error}",
                    file=sys.stderr,
                    flush=True,
                )
                return 2

            if exit_code is not None:
                break
    finally:
        if process.hThread:
            kernel32.CloseHandle(process.hThread)
        if process.hProcess:
            kernel32.CloseHandle(process.hProcess)

    signed = _signed32(exit_code)
    print(
        "[OpenSPMWinDbg] process exit "
        f"signed={signed} hex=0x{exit_code:08X}",
        file=sys.stderr,
        flush=True,
    )
    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--debug-heap", action="store_true",
                        help="enable Windows heap diagnostics; not for performance runs")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    if args.self_test:
        return self_test()

    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("child command required after --")

    exit_code = debug_run(command, debug_heap=args.debug_heap)
    sys.stdout.flush()
    sys.stderr.flush()
    _kernel32().ExitProcess(wintypes.UINT(exit_code & 0xFFFFFFFF))
    raise AssertionError("ExitProcess returned")


if __name__ == "__main__":
    raise SystemExit(main())
