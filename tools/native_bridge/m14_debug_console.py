#!/usr/bin/env python3
"""OpenSPM M14 development-only live debug console.

Connects to the native backend's Local\\OpenSPM.Debug.<pid> shared-memory
mailbox. Guest reads/writes are executed by the backend at its once-per-frame
safe guest-thread boundary; this client never maps MEM1/MEM2 directly.
"""

from __future__ import annotations

import argparse
import csv
import ctypes
import hashlib
import json
import platform
from ctypes import wintypes
from pathlib import Path
import re
import subprocess
import struct
import sys
import threading
import time
from datetime import datetime

IPC_VERSION = 1
COMMAND_BYTES = 256
RESPONSE_BYTES = 768
PROCESS_NAME = "OpenSPMNative.exe"
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DEFAULT_LAUNCH_LOG = ROOT / ".openspm" / "m14" / "home-test" / "launch.log"
DEFAULT_CAPTURE_DIR = ROOT / ".openspm" / "m14" / "debug-captures"
# Public R8PE01/US2 camera layout from SeekyCt/spm-decomp camdrv.h/linker map.
# Camera 5 is the 3D camera; camera 11 is the normal 2D camera.
R8PE01_CAM_PTR_TABLE = 0x804C9F80
CAM_ID_3D = 5
CAM_ID_2D = 11
CAM_ENTRY_VIEW_MTX = 0x054
CAM_ENTRY_POS = 0x15C
CAM_ENTRY_TARGET = 0x168

FILE_MAP_WRITE = 0x0002
FILE_MAP_READ = 0x0004
WAIT_OBJECT_0 = 0x00000000
WAIT_ABANDONED = 0x00000080
WAIT_TIMEOUT = 0x00000102


class Mailbox(ctypes.Structure):
    _fields_ = [
        ("version", ctypes.c_uint32),
        ("command_generation", ctypes.c_int32),
        ("response_generation", ctypes.c_int32),
        ("command", ctypes.c_char * COMMAND_BYTES),
        ("response", ctypes.c_char * RESPONSE_BYTES),
    ]


class DebugConnection:
    def __init__(self, pid: int, timeout: float = 3.0) -> None:
        if sys.platform != "win32":
            raise RuntimeError("M14 live debug IPC is currently Windows-only")
        self.pid = pid
        self.timeout = timeout
        self._send_lock = threading.RLock()
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel32.OpenFileMappingW.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        ]
        self.kernel32.OpenFileMappingW.restype = wintypes.HANDLE
        self.kernel32.MapViewOfFile.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_size_t,
        ]
        self.kernel32.MapViewOfFile.restype = ctypes.c_void_p
        self.kernel32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
        self.kernel32.UnmapViewOfFile.restype = wintypes.BOOL
        self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel32.CloseHandle.restype = wintypes.BOOL
        self.kernel32.CreateMutexW.argtypes = [
            ctypes.c_void_p,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        ]
        self.kernel32.CreateMutexW.restype = wintypes.HANDLE
        self.kernel32.WaitForSingleObject.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
        ]
        self.kernel32.WaitForSingleObject.restype = wintypes.DWORD
        self.kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
        self.kernel32.ReleaseMutex.restype = wintypes.BOOL
        # On 64-bit Windows these interlocked operations may not be exported
        # directly from kernel32.dll even though the Win32 API documents them
        # there. Resolve the real export from KernelBase/the API-set instead
        # of making the entire debug client depend on kernel32's export table.
        self._interlocked_dll = None
        self._interlocked_exchange = None
        self._interlocked_compare_exchange = None
        interlocked_candidates = (
            self.kernel32,
            ctypes.WinDLL("KernelBase", use_last_error=True),
            ctypes.WinDLL(
                "api-ms-win-core-interlocked-l1-1-0.dll",
                use_last_error=True,
            ),
        )
        for candidate in interlocked_candidates:
            try:
                exchange = candidate.InterlockedExchange
                compare_exchange = candidate.InterlockedCompareExchange
            except AttributeError:
                continue
            exchange.argtypes = [
                ctypes.POINTER(ctypes.c_long),
                ctypes.c_long,
            ]
            exchange.restype = ctypes.c_long
            compare_exchange.argtypes = [
                ctypes.POINTER(ctypes.c_long),
                ctypes.c_long,
                ctypes.c_long,
            ]
            compare_exchange.restype = ctypes.c_long
            self._interlocked_dll = candidate
            self._interlocked_exchange = exchange
            self._interlocked_compare_exchange = compare_exchange
            break
        self._atomic_fallback = False
        if self._interlocked_exchange is None or self._interlocked_compare_exchange is None:
            machine = platform.machine().lower()
            if machine in {"amd64", "x86_64", "x86", "i386", "i686"}:
                # command_generation and response_generation are naturally
                # aligned 32-bit fields. On x86/x64 Windows, aligned 32-bit
                # loads/stores are atomic and the architecture preserves the
                # ordering needed by this single-producer mailbox protocol.
                self._atomic_fallback = True
            else:
                raise RuntimeError(
                    "Windows interlocked APIs are unavailable and this CPU "
                    f"architecture ({machine or 'unknown'}) has no debug IPC fallback"
                )

        mutex_name = f"Local\\OpenSPM.Debug.Client.{pid}"
        self.client_mutex = self.kernel32.CreateMutexW(None, False, mutex_name)
        if not self.client_mutex:
            error = ctypes.get_last_error()
            raise RuntimeError(
                f"could not create debug client mutex for PID {pid} (Win32 {error})"
            )

        name = f"Local\\OpenSPM.Debug.{pid}"
        self.mapping = self.kernel32.OpenFileMappingW(
            FILE_MAP_READ | FILE_MAP_WRITE, False, name
        )
        if not self.mapping:
            error = ctypes.get_last_error()
            self.kernel32.CloseHandle(self.client_mutex)
            self.client_mutex = None
            raise RuntimeError(
                f"debug mailbox for PID {pid} is not ready (Win32 {error})"
            )
        self.view = self.kernel32.MapViewOfFile(
            self.mapping,
            FILE_MAP_READ | FILE_MAP_WRITE,
            0,
            0,
            ctypes.sizeof(Mailbox),
        )
        if not self.view:
            error = ctypes.get_last_error()
            self.kernel32.CloseHandle(self.mapping)
            self.mapping = None
            self.kernel32.CloseHandle(self.client_mutex)
            self.client_mutex = None
            raise RuntimeError(
                f"could not map debug mailbox for PID {pid} (Win32 {error})"
            )
        self.box = ctypes.cast(self.view, ctypes.POINTER(Mailbox)).contents
        if self.box.version != IPC_VERSION:
            version = self.box.version
            self.close()
            raise RuntimeError(
                f"debug IPC version mismatch: runtime={version}, client={IPC_VERSION}"
            )

    def close(self) -> None:
        with self._send_lock:
            if getattr(self, "view", None):
                self.kernel32.UnmapViewOfFile(self.view)
                self.view = None
            if getattr(self, "mapping", None):
                self.kernel32.CloseHandle(self.mapping)
                self.mapping = None
            if getattr(self, "client_mutex", None):
                self.kernel32.CloseHandle(self.client_mutex)
                self.client_mutex = None

    def __enter__(self) -> "DebugConnection":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _atomic_read(self, field_name: str) -> int:
        field = getattr(Mailbox, field_name)
        address = ctypes.addressof(self.box) + field.offset
        ptr = ctypes.cast(address, ctypes.POINTER(ctypes.c_long))
        if self._atomic_fallback:
            return int(ptr.contents.value)
        return int(self._interlocked_compare_exchange(ptr, 0, 0))

    def _atomic_write(self, field_name: str, value: int) -> None:
        field = getattr(Mailbox, field_name)
        address = ctypes.addressof(self.box) + field.offset
        ptr = ctypes.cast(address, ctypes.POINTER(ctypes.c_long))
        if self._atomic_fallback:
            ptr.contents.value = value
            return
        self._interlocked_exchange(ptr, value)

    @staticmethod
    def _decode(buffer: ctypes.Array[ctypes.c_char]) -> str:
        return bytes(buffer).split(b"\0", 1)[0].decode("utf-8", errors="replace")

    def send(self, command: str) -> str:
        with self._send_lock:
            mutex = getattr(self, "client_mutex", None)
            if not mutex:
                raise RuntimeError("debug connection is closed")
            wait_ms = max(1, min(int((self.timeout + 1.0) * 1000.0), 60000))
            wait = int(self.kernel32.WaitForSingleObject(mutex, wait_ms))
            if wait == WAIT_TIMEOUT:
                raise TimeoutError(
                    "another OpenSPM debug client held the mailbox too long"
                )
            if wait not in (WAIT_OBJECT_0, WAIT_ABANDONED):
                raise RuntimeError(
                    f"could not acquire debug client mailbox mutex (Win32 wait {wait})"
                )
            try:
                return self._send_locked(command)
            finally:
                self.kernel32.ReleaseMutex(mutex)

    def _send_locked(self, command: str) -> str:
        if not getattr(self, "view", None):
            raise RuntimeError("debug connection is closed")
        encoded = command.encode("utf-8")
        if not encoded:
            raise ValueError("empty command")
        if len(encoded) >= COMMAND_BYTES:
            raise ValueError(
                f"command is too long ({len(encoded)} bytes; max {COMMAND_BYTES - 1})"
            )

        current = self._atomic_read("command_generation")
        generation = current + 1
        if generation <= 0 or generation > 0x7FFFFFFF:
            generation = 1

        # Write bytes before publishing the generation. InterlockedExchange is
        # the release barrier observed by the backend's Interlocked read.
        ctypes.memset(
            ctypes.addressof(self.box) + Mailbox.command.offset,
            0,
            COMMAND_BYTES,
        )
        ctypes.memmove(
            ctypes.addressof(self.box) + Mailbox.command.offset,
            encoded,
            len(encoded),
        )
        self._atomic_write("command_generation", generation)

        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            if self._atomic_read("response_generation") == generation:
                return self._decode(self.box.response)
            time.sleep(0.01)
        raise TimeoutError(
            "runtime did not answer before timeout; the game may be paused, "
            "exiting, or not reaching the safe frame boundary"
        )


def running_pids() -> list[int]:
    try:
        output = subprocess.check_output(
            [
                "tasklist",
                "/FI",
                f"IMAGENAME eq {PROCESS_NAME}",
                "/FO",
                "CSV",
                "/NH",
            ],
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.CalledProcessError):
        return []

    result: list[int] = []
    for row in csv.reader(output.splitlines()):
        if len(row) < 2 or row[0].lower() != PROCESS_NAME.lower():
            continue
        try:
            result.append(int(row[1]))
        except ValueError:
            pass
    return sorted(set(result), reverse=True)


def connect(pid: int | None, timeout: float) -> DebugConnection:
    if pid is not None:
        return DebugConnection(pid, timeout)

    pids = running_pids()
    if not pids:
        raise RuntimeError(
            f"no running {PROCESS_NAME} process found; launch M14 first"
        )

    failures: list[str] = []
    for candidate in pids:
        try:
            return DebugConnection(candidate, timeout)
        except RuntimeError as exc:
            failures.append(f"PID {candidate}: {exc}")
    raise RuntimeError(
        "found OpenSPMNative but none exposed the current debug mailbox:\n  "
        + "\n  ".join(failures)
    )



def _repo_revision() -> str | None:
    try:
        value = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(ROOT),
            text=True,
            encoding="utf-8",
            errors="replace",
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return value if re.fullmatch(r"[0-9a-fA-F]{40}", value) else None


def _safe_name(value: str) -> str:
    value = value.strip() or "capture"
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", value)
    value = value.strip("-._") or "capture"
    return value[:80]


def _parse_read32_value(response: str) -> int:
    match = re.search(r"=\s+(0x[0-9A-Fa-f]+)", response)
    if not match:
        raise RuntimeError(f"could not parse read32 response: {response}")
    return int(match.group(1), 16)


def _parse_dump_bytes(response: str, expected: int) -> bytes:
    parts = response.strip().split()
    if len(parts) != expected or any(
        not re.fullmatch(r"[0-9A-Fa-f]{2}", part) for part in parts
    ):
        raise RuntimeError(
            f"could not parse {expected}-byte dump: {response}"
        )
    return bytes(int(part, 16) for part in parts)


def _guest_f32(data: bytes, offset: int) -> float:
    if offset < 0 or offset + 4 > len(data):
        raise RuntimeError("camera trace float offset out of range")
    return struct.unpack(">f", data[offset:offset + 4])[0]


def _parse_pos_response(response: str) -> tuple[float, float, float]:
    match = re.search(
        r"pos=\(\s*([-+0-9.eE]+),\s*([-+0-9.eE]+),\s*([-+0-9.eE]+)\)",
        response,
    )
    if not match:
        raise RuntimeError(f"could not parse Mario position: {response}")
    return tuple(float(match.group(i)) for i in range(1, 4))


def _camera_trace_summary(rows: list[dict[str, float]]) -> list[str]:
    result: list[str] = []
    if len(rows) < 3:
        return ["camera trace too short for movement analysis"]
    for camera_id in (CAM_ID_2D, CAM_ID_3D):
        prefix = f"cam{camera_id}_"
        moving = 0
        stalls = 0
        max_target_step = 0.0
        max_pos_step = 0.0
        max_lag_change = 0.0
        prev = rows[0]
        for row in rows[1:]:
            mario_dx = row["mario_x"] - prev["mario_x"]
            target_dx = row[prefix + "target_x"] - prev[prefix + "target_x"]
            pos_dx = row[prefix + "pos_x"] - prev[prefix + "pos_x"]
            max_target_step = max(max_target_step, abs(target_dx))
            max_pos_step = max(max_pos_step, abs(pos_dx))
            lag_prev = prev["mario_x"] - prev[prefix + "target_x"]
            lag_now = row["mario_x"] - row[prefix + "target_x"]
            max_lag_change = max(max_lag_change, abs(lag_now - lag_prev))
            if abs(mario_dx) >= 0.10:
                moving += 1
                if abs(target_dx) < 0.01 and abs(pos_dx) < 0.01:
                    stalls += 1
            prev = row
        stall_pct = (100.0 * stalls / moving) if moving else 0.0
        result.append(
            f"camera {camera_id}: moving_intervals={moving} "
            f"stalled_while_mario_moves={stalls} ({stall_pct:.1f}%) "
            f"max_target_step={max_target_step:.3f} "
            f"max_pos_step={max_pos_step:.3f} "
            f"max_lag_change={max_lag_change:.3f}"
        )
    if all(f"cam5_view_{r}{c}" in rows[0] for r in range(3) for c in range(4)):
        moving = 0
        view_stalls = 0
        view_steps: list[float] = []
        prev = rows[0]
        for row in rows[1:]:
            mario_dx = row["mario_x"] - prev["mario_x"]
            diffs = [
                row[f"cam5_view_{r}{c}"] - prev[f"cam5_view_{r}{c}"]
                for r in range(3) for c in range(4)
            ]
            step = sum(value * value for value in diffs) ** 0.5
            if abs(mario_dx) >= 0.10:
                moving += 1
                view_steps.append(step)
                if step < 1.0e-6:
                    view_stalls += 1
            prev = row
        ordered = sorted(view_steps)
        median_step = (
            ordered[len(ordered) // 2] if ordered else 0.0
        )
        result.append(
            "camera 5 viewMtx: "
            f"moving_intervals={moving} exact_stalls={view_stalls} "
            f"median_matrix_step={median_step:.6f} "
            f"max_matrix_step={max(view_steps) if view_steps else 0.0:.6f}"
        )
    return result


class LaunchLogCapture:
    def __init__(
        self,
        launch_log: Path = DEFAULT_LAUNCH_LOG,
        capture_dir: Path = DEFAULT_CAPTURE_DIR,
    ) -> None:
        self.launch_log = launch_log
        self.capture_dir = capture_dir
        self.active = False
        self.name = ""
        self.offset = 0
        self.started = 0.0

    def start(self, connection: DebugConnection, name: str = "capture") -> str:
        if self.active:
            raise RuntimeError(f"capture already active: {self.name}")
        self.name = _safe_name(name)
        self.offset = self.launch_log.stat().st_size if self.launch_log.exists() else 0
        self.started = time.time()
        try:
            connection.send(f"mark CAPTURE-BEGIN {self.name}")
        except Exception:
            self.name = ""
            self.offset = 0
            self.started = 0.0
            raise
        self.active = True
        return f"capture started: {self.name} @ byte {self.offset}"

    def stop(self, connection: DebugConnection) -> Path:
        if not self.active:
            raise RuntimeError("no capture is active")
        connection.send(f"mark CAPTURE-END {self.name}")
        time.sleep(0.15)
        self.capture_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        output = self.capture_dir / f"{stamp}-{self.name}.log"

        rotated = False
        if self.launch_log.exists():
            current_size = self.launch_log.stat().st_size
            # A new launch can truncate launch.log while a cockpit is open.
            # In that case capture from the new file's beginning rather than
            # silently seeking to its EOF and producing an empty snippet.
            rotated = current_size < self.offset
            start_offset = self.offset if not rotated else 0
            with self.launch_log.open("rb") as source:
                source.seek(start_offset)
                data = source.read()
        else:
            current_size = 0
            start_offset = 0
            data = b"(launch.log was not found when capture stopped)\r\n"

        stopped_at = time.time()
        elapsed = stopped_at - self.started
        header = (
            f"# OpenSPM M14 live debug capture\n"
            f"# name={self.name}\n"
            f"# pid={connection.pid}\n"
            f"# source={self.launch_log}\n"
            f"# elapsed_s={elapsed:.3f}\n\n"
        ).encode("utf-8")
        capture_bytes = header + data
        output.write_bytes(capture_bytes)

        # Keep a machine-readable provenance sidecar beside every scoped log.
        # It intentionally contains no Nintendo code/assets: only host paths,
        # process/capture metadata, repo identity and a hash of the local
        # diagnostic snippet.
        manifest = {
            "schema_version": 1,
            "capture_file": output.name,
            "capture_sha256": hashlib.sha256(capture_bytes).hexdigest(),
            "name": self.name,
            "pid": connection.pid,
            "source_log": str(self.launch_log),
            "source_offset": start_offset,
            "source_size_at_stop": current_size,
            "source_rotated_during_capture": rotated,
            "captured_log_bytes": len(data),
            "started_unix": self.started,
            "stopped_unix": stopped_at,
            "elapsed_s": elapsed,
            "repo_revision": _repo_revision(),
        }
        output.with_suffix(".json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )

        self.active = False
        self.name = ""
        self.offset = 0
        return output


def execute_script_text(
    connection: DebugConnection,
    text: str,
    *,
    emit=print,
    stop_requested=None,
    capture: LaunchLogCapture | None = None,
) -> bool:
    capture = capture or LaunchLogCapture()
    cleanup_commands: list[str] = []

    def stopped() -> bool:
        return bool(stop_requested and stop_requested())

    def run_lines() -> bool:
        for line_number, raw in enumerate(text.splitlines(), 1):
            if stopped():
                emit(f"! script stopped before line {line_number}")
                return False
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            lower = line.lower()

            if lower.startswith("defer ") or lower.startswith("onexit "):
                command = line.split(None, 1)[1].strip() if " " in line else ""
                if not command:
                    emit(f"! line {line_number}: defer requires a command")
                    return False
                cleanup_commands.append(command)
                emit(f"> defer {command}")
                continue

            if lower.startswith("sleep ") or lower.startswith("wait "):
                try:
                    seconds = float(line.split(None, 1)[1])
                    if seconds < 0:
                        raise ValueError
                except ValueError:
                    emit(f"! line {line_number}: invalid wait: {line}")
                    return False
                emit(f"> {line}")
                deadline = time.monotonic() + seconds
                while time.monotonic() < deadline:
                    if stopped():
                        emit("! script stopped during wait")
                        return False
                    time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
                continue

            if lower.startswith("capture start"):
                name = line[len("capture start"):].strip() or "capture"
                try:
                    emit(f"> {line}")
                    emit(capture.start(connection, name))
                except (ValueError, RuntimeError, TimeoutError) as exc:
                    emit(f"! line {line_number}: {exc}")
                    return False
                continue

            if lower == "capture stop":
                try:
                    emit(f"> {line}")
                    output = capture.stop(connection)
                    emit(f"saved capture: {output}")
                except (ValueError, RuntimeError, TimeoutError) as exc:
                    emit(f"! line {line_number}: {exc}")
                    return False
                continue

            if lower.startswith("require_map "):
                parts = line.split()
                if len(parts) != 2 or not re.fullmatch(r"[A-Za-z0-9_]+", parts[1]):
                    emit(
                        f"! line {line_number}: "
                        "usage: require_map <exact-map-id>"
                    )
                    return False
                expected_map = parts[1]
                emit(f"> {line}")
                try:
                    response = connection.send("map")
                except (ValueError, RuntimeError, TimeoutError) as exc:
                    emit(f"! line {line_number}: {exc}")
                    return False
                match = re.search(r"\bmap=([^\s]+)", response)
                if not match:
                    emit(
                        f"! line {line_number}: could not parse current map from: "
                        f"{response}"
                    )
                    return False
                current_map = match.group(1)
                if current_map.lower() != expected_map.lower():
                    emit(
                        "! benchmark map mismatch: "
                        f"required={expected_map} current={current_map}"
                    )
                    return False
                emit(f"map requirement OK: {current_map}")
                continue

            if lower.startswith("require_npc_names "):
                raw_names = line.split(None, 1)[1].strip() if " " in line else ""
                expected_names = {
                    value.strip()
                    for value in raw_names.split(",")
                    if value.strip()
                }
                if not expected_names or any(
                    not re.fullmatch(r"[A-Za-z0-9_.-]+", value)
                    for value in expected_names
                ):
                    emit(
                        f"! line {line_number}: "
                        "usage: require_npc_names name1,name2,..."
                    )
                    return False
                emit(f"> {line}")
                try:
                    response = connection.send("npcs list")
                except (ValueError, RuntimeError, TimeoutError) as exc:
                    emit(f"! line {line_number}: {exc}")
                    return False
                actual_names = set(
                    re.findall(r"\bname=([^\s]+)", response)
                )
                if actual_names != expected_names:
                    emit(
                        "! benchmark NPC identity mismatch: "
                        f"required={','.join(sorted(expected_names))} "
                        f"current={','.join(sorted(actual_names))}"
                    )
                    return False
                emit(
                    "NPC identity requirement OK: "
                    + ",".join(sorted(actual_names))
                )
                continue

            if lower.startswith("require_npcs "):
                parts = line.split()
                if len(parts) != 2:
                    emit(
                        f"! line {line_number}: "
                        "usage: require_npcs <exact-active-count>"
                    )
                    return False
                try:
                    expected_active = int(parts[1], 10)
                    if expected_active < 0 or expected_active > 4096:
                        raise ValueError
                except ValueError:
                    emit(
                        f"! line {line_number}: require_npcs expects an "
                        "integer active count in 0..4096"
                    )
                    return False
                emit(f"> {line}")
                try:
                    response = connection.send("npcs info")
                except (ValueError, RuntimeError, TimeoutError) as exc:
                    emit(f"! line {line_number}: {exc}")
                    return False
                match = re.search(r"\bnum=(\d+)\s+active=(\d+)", response)
                if not match:
                    emit(
                        f"! line {line_number}: could not parse NPC count from: "
                        f"{response}"
                    )
                    return False
                total = int(match.group(1))
                active = int(match.group(2))
                if active != expected_active:
                    emit(
                        "! benchmark NPC population mismatch: "
                        f"required_active={expected_active} current_active={active} "
                        f"num={total}"
                    )
                    return False
                emit(
                    "NPC population requirement OK: "
                    f"active={active} num={total}"
                )
                continue

            if lower.startswith("stable_npcs "):
                parts = line.split()
                if len(parts) not in (2, 3):
                    emit(
                        f"! line {line_number}: "
                        "usage: stable_npcs <seconds> [interval-seconds]"
                    )
                    return False
                try:
                    seconds = float(parts[1])
                    interval = float(parts[2]) if len(parts) == 3 else 0.25
                    if seconds <= 0 or interval <= 0:
                        raise ValueError
                except ValueError:
                    emit(
                        f"! line {line_number}: stable_npcs requires positive "
                        "seconds and interval"
                    )
                    return False

                baseline = None
                deadline = time.monotonic() + seconds
                samples = 0
                emit(f"> {line}")
                while True:
                    if stopped():
                        emit("! script stopped during stable_npcs")
                        return False
                    try:
                        response = connection.send("npcs info")
                    except (ValueError, RuntimeError, TimeoutError) as exc:
                        emit(f"! line {line_number}: {exc}")
                        return False
                    match = re.search(
                        r"\bnum=(\d+)\s+active=(\d+).*?\bentries=(0x[0-9A-Fa-f]+)",
                        response,
                    )
                    if not match:
                        emit(
                            f"! line {line_number}: could not parse NPC stability "
                            f"from: {response}"
                        )
                        return False
                    state = (
                        int(match.group(1)),
                        int(match.group(2)),
                        int(match.group(3), 16),
                    )
                    samples += 1
                    if baseline is None:
                        baseline = state
                    elif state != baseline:
                        emit(
                            "! NPC stability check failed: "
                            f"started num={baseline[0]} active={baseline[1]} "
                            f"entries=0x{baseline[2]:08X}, then saw "
                            f"num={state[0]} active={state[1]} "
                            f"entries=0x{state[2]:08X}"
                        )
                        return False
                    now = time.monotonic()
                    if now >= deadline:
                        break
                    time.sleep(min(interval, max(0.0, deadline - now)))
                emit(
                    "NPC stability OK: "
                    f"samples={samples} num={baseline[0]} active={baseline[1]} "
                    f"entries=0x{baseline[2]:08X}"
                )
                continue

            if lower.startswith("npc_group_pulse "):
                parts = line.split(None, 2)
                if len(parts) != 3:
                    emit(
                        f"! line {line_number}: "
                        "usage: npc_group_pulse <seconds> name1,name2,..."
                    )
                    return False
                try:
                    seconds = float(parts[1])
                    if seconds <= 0 or seconds > 5.0:
                        raise ValueError
                except ValueError:
                    emit(
                        f"! line {line_number}: npc_group_pulse seconds "
                        "must be >0 and <=5"
                    )
                    return False
                wanted_names = [
                    value.strip()
                    for value in parts[2].split(",")
                    if value.strip()
                ]
                if (
                    not wanted_names
                    or len(wanted_names) != len(set(wanted_names))
                    or any(
                        not re.fullmatch(r"[A-Za-z0-9_.-]+", value)
                        for value in wanted_names
                    )
                ):
                    emit(
                        f"! line {line_number}: npc_group_pulse requires "
                        "unique comma-separated NPC names"
                    )
                    return False

                emit(f"> {line}")
                snapshots = []
                restore_error = None
                active_marker_started = False
                active_names = ",".join(wanted_names)
                try:
                    map_response = connection.send("map")
                    map_match = re.search(r"\bmap=([^\s]+)", map_response)
                    if not map_match:
                        raise RuntimeError(
                            f"could not parse current map from: {map_response}"
                        )
                    start_map = map_match.group(1)

                    info_response = connection.send("npcs info")
                    info_match = re.search(
                        r"\bnum=(\d+)\s+active=(\d+).*?"
                        r"\bentries=(0x[0-9A-Fa-f]+)",
                        info_response,
                    )
                    if not info_match:
                        raise RuntimeError(
                            f"could not parse NPC table from: {info_response}"
                        )
                    entries = int(info_match.group(3), 16)

                    list_response = connection.send("npcs list")
                    active_rows = {}
                    for match in re.finditer(
                        r"#(\d+):id=(\d+),tribe=(\d+),name=([^\s]+)",
                        list_response,
                    ):
                        active_rows[match.group(4)] = (
                            int(match.group(1)),
                            int(match.group(2)),
                            int(match.group(3)),
                        )
                    missing = [
                        name for name in wanted_names if name not in active_rows
                    ]
                    if missing:
                        raise RuntimeError(
                            "requested NPC(s) are not currently active: "
                            + ",".join(missing)
                        )

                    for name in wanted_names:
                        table_index, npc_id, tribe_id = active_rows[name]
                        flag_address = entries + table_index * 0x748 + 0x8
                        read_response = connection.send(
                            f"read32 0x{flag_address:08X}"
                        )
                        read_match = re.search(
                            r"=\s+(0x[0-9A-Fa-f]+)", read_response
                        )
                        if not read_match:
                            raise RuntimeError(
                                f"could not read flag8 for {name}: "
                                f"{read_response}"
                            )
                        flag8 = int(read_match.group(1), 16)
                        if (flag8 & 1) == 0:
                            raise RuntimeError(
                                f"{name} became inactive before group pulse"
                            )
                        snapshots.append(
                            (name, table_index, npc_id, tribe_id,
                             flag_address, flag8)
                        )

                    for (
                        name, table_index, npc_id, tribe_id,
                        flag_address, flag8
                    ) in snapshots:
                        write_response = connection.send(
                            f"write32 0x{flag_address:08X} "
                            f"0x{(flag8 & ~1):08X}"
                        )
                        if "<-" not in write_response:
                            raise RuntimeError(
                                f"could not suppress {name}: {write_response}"
                            )

                    active_names = ",".join(name for name, *_ in snapshots)
                    emit(
                        "NPC group pulse active: "
                        + ", ".join(
                            f"{name}(#{table_index},id={npc_id},tribe={tribe_id})"
                            for name, table_index, npc_id, tribe_id, _, _ in snapshots
                        )
                    )
                    connection.send(
                        "mark YOLD-GROUP-ACTIVE-BEGIN "
                        f"count={len(snapshots)} names={active_names}"
                    )
                    active_marker_started = True

                    deadline = time.monotonic() + seconds
                    while time.monotonic() < deadline:
                        if stopped():
                            break
                        time.sleep(
                            min(
                                0.05,
                                max(0.0, deadline - time.monotonic()),
                            )
                        )
                except (ValueError, RuntimeError, TimeoutError) as exc:
                    restore_error = str(exc)
                finally:
                    if snapshots:
                        try:
                            map_response = connection.send("map")
                            map_match = re.search(
                                r"\bmap=([^\s]+)", map_response
                            )
                            info_response = connection.send("npcs info")
                            info_match = re.search(
                                r"\bnum=(\d+)\s+active=(\d+).*?"
                                r"\bentries=(0x[0-9A-Fa-f]+)",
                                info_response,
                            )
                            safe_restore = (
                                map_match is not None
                                and info_match is not None
                                and map_match.group(1) == start_map
                                and int(info_match.group(3), 16) == entries
                            )
                            if not safe_restore:
                                raise RuntimeError(
                                    "map/NPC table changed during group pulse; "
                                    "refusing stale-address restoration"
                                )
                            restore_rows = []
                            drifted = []
                            for (
                                name, table_index, npc_id, tribe_id,
                                flag_address, original_flag8
                            ) in snapshots:
                                read_response = connection.send(
                                    f"read32 0x{flag_address:08X}"
                                )
                                read_match = re.search(
                                    r"=\s+(0x[0-9A-Fa-f]+)", read_response
                                )
                                if not read_match:
                                    raise RuntimeError(
                                        f"could not re-read {name} for restore"
                                    )
                                current_flag8 = int(read_match.group(1), 16)
                                if current_flag8 & 1:
                                    drifted.append(name)
                                restore_rows.append(
                                    (name, flag_address, original_flag8,
                                     current_flag8)
                                )
                            if active_marker_started:
                                connection.send(
                                    "mark YOLD-GROUP-ACTIVE-END "
                                    f"count={len(snapshots)} names={active_names}"
                                )
                            restore_failures = []
                            for (
                                name, flag_address, original_flag8,
                                current_flag8
                            ) in restore_rows:
                                restored = (
                                    (current_flag8 & ~1)
                                    | (original_flag8 & 1)
                                )
                                write_response = connection.send(
                                    f"write32 0x{flag_address:08X} "
                                    f"0x{restored:08X}"
                                )
                                if "<-" not in write_response:
                                    restore_failures.append(
                                        f"{name}: {write_response}"
                                    )
                            if restore_failures:
                                raise RuntimeError(
                                    "NPC group pulse restoration failed: "
                                    + "; ".join(restore_failures)
                                )
                            if active_marker_started and drifted:
                                raise RuntimeError(
                                    "selected NPC active bit re-enabled during "
                                    "measurement: " + ",".join(drifted)
                                )
                            emit(
                                "NPC group pulse restored active bit: "
                                + ",".join(
                                    name for name, *_ in snapshots
                                )
                            )
                        except (ValueError, RuntimeError, TimeoutError) as exc:
                            if restore_error is None:
                                restore_error = str(exc)
                            else:
                                restore_error += f"; restore failed: {exc}"

                if restore_error is not None:
                    emit(
                        f"! line {line_number}: NPC group pulse failed: "
                        f"{restore_error}"
                    )
                    return False
                if stopped():
                    emit("! script stopped during NPC group pulse")
                    return False
                continue

            if lower.startswith("camera_trace"):
                parts = line.split()
                if len(parts) not in (2, 3):
                    emit(
                        f"! line {line_number}: "
                        "usage: camera_trace <seconds> [interval-seconds]"
                    )
                    return False
                try:
                    seconds = float(parts[1])
                    interval = float(parts[2]) if len(parts) == 3 else 0.05
                    if seconds <= 0 or seconds > 30.0 or interval < 0.0:
                        raise ValueError
                except ValueError:
                    emit(
                        f"! line {line_number}: camera_trace requires "
                        "0<seconds<=30 and interval>=0"
                    )
                    return False

                emit(f"> {line}")
                try:
                    map_response = connection.send("map")
                    map_match = re.search(r"\bmap=([^\s]+)", map_response)
                    current_map = map_match.group(1) if map_match else "unknown"

                    camera_ptrs: dict[int, int] = {}
                    for camera_id in (CAM_ID_2D, CAM_ID_3D):
                        ptr_addr = R8PE01_CAM_PTR_TABLE + camera_id * 4
                        ptr = _parse_read32_value(
                            connection.send(f"read32 0x{ptr_addr:08X}")
                        )
                        if ptr == 0:
                            raise RuntimeError(
                                f"camera {camera_id} pointer is null"
                            )
                        camera_ptrs[camera_id] = ptr

                    rows: list[dict[str, float]] = []
                    started = time.monotonic()
                    deadline = started + seconds
                    next_sample = started
                    sample_index = 0
                    while True:
                        if stopped():
                            emit("! script stopped during camera_trace")
                            return False
                        now = time.monotonic()
                        if now >= deadline and rows:
                            break

                        mario = _parse_pos_response(connection.send("pos"))
                        row: dict[str, float] = {
                            "sample": float(sample_index),
                            "host_s": time.monotonic() - started,
                            "mario_x": mario[0],
                            "mario_y": mario[1],
                            "mario_z": mario[2],
                        }
                        for camera_id in (CAM_ID_2D, CAM_ID_3D):
                            ptr = camera_ptrs[camera_id]
                            data = _parse_dump_bytes(
                                connection.send(
                                    f"dump 0x{ptr + CAM_ENTRY_POS:08X} 24"
                                ),
                                24,
                            )
                            prefix = f"cam{camera_id}_"
                            row[prefix + "pos_x"] = _guest_f32(data, 0)
                            row[prefix + "pos_y"] = _guest_f32(data, 4)
                            row[prefix + "pos_z"] = _guest_f32(data, 8)
                            row[prefix + "target_x"] = _guest_f32(data, 12)
                            row[prefix + "target_y"] = _guest_f32(data, 16)
                            row[prefix + "target_z"] = _guest_f32(data, 20)

                        # camera 5 is the live world-camera path in both Yold
                        # traces and in dev115's map callback census. Sample the
                        # actual render view matrix as the final guest-side
                        # discriminator before investigating presentation.
                        view = _parse_dump_bytes(
                            connection.send(
                                f"dump 0x{camera_ptrs[CAM_ID_3D] + CAM_ENTRY_VIEW_MTX:08X} 48"
                            ),
                            48,
                        )
                        for matrix_index in range(12):
                            matrix_row = matrix_index // 4
                            matrix_col = matrix_index % 4
                            row[
                                f"cam5_view_{matrix_row}{matrix_col}"
                            ] = _guest_f32(view, matrix_index * 4)
                        rows.append(row)
                        sample_index += 1

                        if interval > 0.0:
                            next_sample += interval
                            remaining = next_sample - time.monotonic()
                            if remaining > 0.0:
                                time.sleep(remaining)

                    DEFAULT_CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
                    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                    output = DEFAULT_CAPTURE_DIR / (
                        f"{stamp}-camera-follow-trace.csv"
                    )
                    fieldnames = [
                        "sample", "host_s",
                        "mario_x", "mario_y", "mario_z",
                        "cam11_pos_x", "cam11_pos_y", "cam11_pos_z",
                        "cam11_target_x", "cam11_target_y", "cam11_target_z",
                        "cam5_pos_x", "cam5_pos_y", "cam5_pos_z",
                        "cam5_target_x", "cam5_target_y", "cam5_target_z",
                        "cam5_view_00", "cam5_view_01", "cam5_view_02", "cam5_view_03",
                        "cam5_view_10", "cam5_view_11", "cam5_view_12", "cam5_view_13",
                        "cam5_view_20", "cam5_view_21", "cam5_view_22", "cam5_view_23",
                    ]
                    with output.open(
                        "w", encoding="utf-8", newline=""
                    ) as stream:
                        writer = csv.DictWriter(stream, fieldnames=fieldnames)
                        writer.writeheader()
                        writer.writerows(rows)

                    metadata = {
                        "schema_version": 1,
                        "kind": "camera-follow-trace",
                        "pid": connection.pid,
                        "map": current_map,
                        "duration_requested_s": seconds,
                        "interval_requested_s": interval,
                        "sample_count": len(rows),
                        "repo_revision": _repo_revision(),
                        "camera_ptr_table": f"0x{R8PE01_CAM_PTR_TABLE:08X}",
                        "camera_ptrs": {
                            str(key): f"0x{value:08X}"
                            for key, value in camera_ptrs.items()
                        },
                        "cam_entry_view_matrix_offset": f"0x{CAM_ENTRY_VIEW_MTX:X}",
                        "cam_entry_pos_offset": f"0x{CAM_ENTRY_POS:X}",
                        "cam_entry_target_offset": f"0x{CAM_ENTRY_TARGET:X}",
                    }
                    output.with_suffix(".json").write_text(
                        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8",
                        newline="\n",
                    )
                    emit(
                        f"saved camera trace: {output} "
                        f"(samples={len(rows)} map={current_map})"
                    )
                    for summary in _camera_trace_summary(rows):
                        emit(summary)
                except (ValueError, RuntimeError, TimeoutError, OSError) as exc:
                    emit(f"! line {line_number}: camera_trace failed: {exc}")
                    return False
                continue

            if lower.startswith("sample "):
                parts = line.split(None, 3)
                if len(parts) != 4:
                    emit(
                        f"! line {line_number}: "
                        "usage: sample <seconds> <count> <command>"
                    )
                    return False
                try:
                    interval = float(parts[1])
                    count = int(parts[2])
                    if interval < 0 or count < 1 or count > 10000:
                        raise ValueError
                except ValueError:
                    emit(
                        f"! line {line_number}: sample interval must be >=0 "
                        "and count 1..10000"
                    )
                    return False
                for index in range(count):
                    if stopped():
                        emit("! script stopped during sample")
                        return False
                    emit(f"> sample [{index + 1}/{count}] {parts[3]}")
                    try:
                        emit(connection.send(parts[3]))
                    except (ValueError, RuntimeError, TimeoutError) as exc:
                        emit(f"! line {line_number}: {exc}")
                        return False
                    if index + 1 < count:
                        deadline = time.monotonic() + interval
                        while time.monotonic() < deadline:
                            if stopped():
                                emit("! script stopped during sample wait")
                                return False
                            time.sleep(
                                min(
                                    0.05,
                                    max(0.0, deadline - time.monotonic()),
                                )
                            )
                continue

            if lower.startswith("repeat "):
                parts = line.split(None, 2)
                if len(parts) != 3:
                    emit(f"! line {line_number}: usage: repeat <count> <command>")
                    return False
                try:
                    count = int(parts[1])
                    if count < 1 or count > 10000:
                        raise ValueError
                except ValueError:
                    emit(f"! line {line_number}: repeat count must be 1..10000")
                    return False
                for index in range(count):
                    if stopped():
                        emit("! script stopped during repeat")
                        return False
                    emit(f"> [{index + 1}/{count}] {parts[2]}")
                    try:
                        emit(connection.send(parts[2]))
                    except (ValueError, RuntimeError, TimeoutError) as exc:
                        emit(f"! line {line_number}: {exc}")
                        return False
                continue

            emit(f"> {line}")
            try:
                emit(connection.send(line))
            except (ValueError, RuntimeError, TimeoutError) as exc:
                emit(f"! line {line_number}: {exc}")
                return False
        return True

    ok = run_lines()

    # Deferred commands are deliberately best-effort and run in reverse order,
    # even after Stop or an experiment failure. This keeps temporary NPC/speed/
    # memory mutations from leaking into the rest of a long live session.
    cleanup_ok = True
    for command in reversed(cleanup_commands):
        emit(f"> cleanup: {command}")
        try:
            emit(connection.send(command))
        except (ValueError, RuntimeError, TimeoutError) as exc:
            emit(f"! cleanup failed: {command}: {exc}")
            cleanup_ok = False

    # A capture is never left armed merely because a script failed or the user
    # pressed Stop. Include cleanup effects in the same snippet.
    if capture.active:
        try:
            output = capture.stop(connection)
            emit(f"capture auto-stopped: {output}")
        except (ValueError, RuntimeError, TimeoutError) as exc:
            emit(f"! capture auto-stop failed: {exc}")
            cleanup_ok = False

    return ok and cleanup_ok

def run_one(connection: DebugConnection, command: str, echo: bool = True) -> bool:
    if echo:
        print(f"> {command}")
    try:
        response = connection.send(command)
    except (ValueError, RuntimeError, TimeoutError) as exc:
        print(f"! {exc}", file=sys.stderr)
        return False
    print(response)
    return True


def run_script(connection: DebugConnection, path: Path) -> bool:
    if not path.is_file():
        print(f"script not found: {path}", file=sys.stderr)
        return False
    return execute_script_text(
        connection,
        path.read_text(encoding="utf-8"),
        capture=LaunchLogCapture(),
    )


def interactive(connection: DebugConnection) -> int:
    print(
        f"Connected to {PROCESS_NAME} PID {connection.pid}. "
        "Type 'help' for runtime commands; 'quit' exits this client."
    )
    while True:
        try:
            line = input("openspm> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            continue
        if line.lower() in {"quit", "exit"}:
            return 0
        run_one(connection, line, echo=False)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Connect to the running OpenSPM M14 live debug mailbox."
    )
    parser.add_argument(
        "--cli",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--pid", type=int, help="specific OpenSPMNative PID")
    parser.add_argument(
        "--timeout",
        type=float,
        default=3.0,
        help="response timeout in seconds (default: 3)",
    )
    parser.add_argument("--script", type=Path, help="run commands from a text file")
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="one command to send, e.g. status or read32 0x804D0D60",
    )
    args = parser.parse_args()

    if args.timeout <= 0:
        parser.error("--timeout must be positive")

    try:
        connection = connect(args.pid, args.timeout)
    except RuntimeError as exc:
        print(f"OpenSPM debug: {exc}", file=sys.stderr)
        return 2

    with connection:
        if args.script is not None:
            return 0 if run_script(connection, args.script) else 1
        if args.command:
            command = " ".join(args.command).strip()
            return 0 if run_one(connection, command, echo=False) else 1
        return interactive(connection)


if __name__ == "__main__":
    raise SystemExit(main())
