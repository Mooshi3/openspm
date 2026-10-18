#!/usr/bin/env python3
"""Retire and roll back the M14 render-worker pacing probe.

The 041850 Windows A/B run proved this probe is not behavior-neutral: the
known-good fe8842 runtime had correct rotating-device Mario visibility and door
transitions, while the first runtime with this probe wrapped around every
EndFrame callback regressed both. The same short run already answered the
profiling question: worker queue wait/end-frame work are tiny, while producer
cadence owns the visible lateness. Preserve that conclusion and remove the
callback wrapper completely.

This migration is intentionally idempotent. Cached player Aurora checkouts that
already contain kM14WorkerPacingBinUs are restored to the pinned upstream
render_worker.cpp. Fresh/already-restored checkouts are only verified. No other
Aurora worker file is touched.

The temporary producer-thread DrawDone profiler that this hook used to launch
was retired by dev88 after it had answered the profiling question. Re-running
an older configure companion must never resurrect that per-draw instrumentation.
This hook now only installs/verifies the narrow raw-Wii resize repair discovered
by the 052222 player A/B: host window resize must not erase EFB-copy results
that represent guest-visible GXCopyTex memory.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
LOCK = HERE / "aurora.lock.json"
TARGET = pathlib.Path("lib/gfx/render_worker.cpp")
SIGNATURE = "kM14WorkerPacingBinUs"


def run(*args: str, cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def pinned_text(source: pathlib.Path) -> str:
    return run("git", "show", f"HEAD:{TARGET.as_posix()}", cwd=source).stdout


def transform(source: pathlib.Path, check_only: bool) -> str:
    path = source / TARGET
    text = path.read_text(encoding="utf-8")
    baseline = pinned_text(source)

    if SIGNATURE not in text:
        if text != baseline:
            raise RuntimeError(
                "render_worker.cpp has non-probe local changes; refusing broad rollback")
        return "already-retired"

    required = (
        "M14 render-worker pacing reason={}",
        "producer_p95_us={}",
        "queue_p95_us={}",
        "work_p95_us={}",
        "m14_reset_worker_pacing();",
    )
    missing = [fragment for fragment in required if fragment not in text]
    if missing:
        raise RuntimeError(
            "render-worker pacing signature is incomplete; refusing rollback: "
            + ", ".join(missing))

    if not check_only:
        path.write_text(baseline, encoding="utf-8", newline="\n")
    return "verified-retire" if check_only else "retired"


def run_companion(source: pathlib.Path, check_only: bool, script_name: str) -> str:
    command = [sys.executable, str(HERE / script_name), str(source)]
    if check_only:
        command.append("--check-only")
    completed = subprocess.run(
        command,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return completed.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=pathlib.Path)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    source = args.source.resolve()
    expected = json.loads(LOCK.read_text(encoding="utf-8"))["commit"]

    try:
        actual = run("git", "rev-parse", "HEAD", cwd=source).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"error: not a usable git checkout: {source}: {exc}", file=sys.stderr)
        return 2
    if actual != expected:
        print(f"error: Aurora revision mismatch: expected {expected}, got {actual}", file=sys.stderr)
        return 3

    try:
        result = transform(source, args.check_only)
        resize_result = run_companion(
            source, args.check_only, "apply_raw_wii_resize_copy_persistence_patch.py")
    except (OSError, subprocess.CalledProcessError, RuntimeError) as exc:
        stderr = getattr(exc, "stderr", "") or ""
        print(
            "error: Aurora worker retirement / resize repair configure failed: "
            + (stderr.strip() or str(exc)),
            file=sys.stderr,
        )
        return 4

    print({
        "already-retired": "Aurora render-worker pacing diagnostics already retired",
        "verified-retire": "Aurora render-worker pacing diagnostic rollback verified",
        "retired": "Aurora render-worker pacing diagnostics retired and upstream worker restored",
    }[result])
    if resize_result:
        print(resize_result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
