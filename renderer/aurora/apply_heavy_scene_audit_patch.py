#!/usr/bin/env python3
"""Auto-arm one DrawDone-bounded GX audit on the first heavy raw-array frame.

The first full native gameplay run reaches Flipside but shows scene-dependent
renderer failures (white 3D objects, invisible actors, wrong material colors)
and a matching performance cliff. The authoritative run also proves that the
heavy path stages more than 7 MiB of raw guest vertex-array prefixes per Aurora
frame with no per-frame reuse hits.

Do not change GX semantics to chase those symptoms yet. Reuse the existing deep
metadata/hash audit and arm it exactly once when raw guest-array staging first
crosses the already-existing 7 MiB high-water threshold. drawLimit=0 selects the
existing DrawDone-bounded mode (internally capped at 512 draws), so the capture
ends at the Wii completion boundary and remains diagnostic-only.

This patch is layered after the complete M14 Aurora repair/audit chain and is
idempotent for the player-local cached checkout. Unknown source drift fails
closed. It is also intentionally listed in CMAKE_CONFIGURE_DEPENDS: touching
this migration sentinel forces M14-UPDATE-RUN to reconfigure the cached Aurora
checkout so newer migration-chain changes cannot remain silently stale.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
LOCK = HERE / "aurora.lock.json"
SIGNATURE = "M14 heavy-scene GX audit auto-armed"


def run(*args: str, cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected source fragment exactly once, found {count}")
    return text.replace(old, new, 1)


def transform(source: pathlib.Path, check_only: bool) -> str:
    path = source / "lib/gx/command_processor.cpp"
    text = path.read_text(encoding="utf-8")

    if SIGNATURE in text:
        if text.count(SIGNATURE) != 1:
            raise RuntimeError("heavy-scene audit signature is inconsistent")
        return "already-applied"

    if "M14 SEQ_LOAD deep-frame audit active" not in text or \
       "M14 raw guest-array staging high-water" not in text:
        raise RuntimeError("required deep-audit/guest-array adaptations are absent")

    globals_old = (
        "bool sM14GuestArrayReuseReported = false;\n"
        "bool sM14GuestArrayHighWaterReported = false;\n"
        "u64 sM14LineDrawCount = 0;\n"
    )
    globals_new = (
        "bool sM14GuestArrayReuseReported = false;\n"
        "bool sM14GuestArrayHighWaterReported = false;\n"
        "bool sM14HeavySceneAuditArmed = false;\n"
        "u64 sM14LineDrawCount = 0;\n"
    )
    text = replace_once(text, globals_old, globals_new,
                        "command_processor.cpp heavy-audit global")

    highwater_old = (
        "  if (!sM14GuestArrayHighWaterReported && sM14GuestArrayStagedBytes + uploadSize >= 7u * 1024u * 1024u) {\n"
        "    Log.info(\"M14 raw guest-array staging high-water frame={} staged={} next={} entries={} hits={} misses={}\",\n"
        "             sM14GuestArrayStageFrame, sM14GuestArrayStagedBytes, uploadSize,\n"
        "             sM14GuestArrayStages.size(), sM14GuestArrayReuseHits, sM14GuestArrayReuseMisses);\n"
        "    sM14GuestArrayHighWaterReported = true;\n"
        "  }\n"
    )
    highwater_new = (
        "  if (!sM14GuestArrayHighWaterReported && sM14GuestArrayStagedBytes + uploadSize >= 7u * 1024u * 1024u) {\n"
        "    Log.info(\"M14 raw guest-array staging high-water frame={} staged={} next={} entries={} hits={} misses={}\",\n"
        "             sM14GuestArrayStageFrame, sM14GuestArrayStagedBytes, uploadSize,\n"
        "             sM14GuestArrayStages.size(), sM14GuestArrayReuseHits, sM14GuestArrayReuseMisses);\n"
        "    if (!sM14HeavySceneAuditArmed && sM14AuditRemaining == 0u) {\n"
        "      audit::arm_seqload_capture(0u);\n"
        "      sM14HeavySceneAuditArmed = true;\n"
        "      Log.info(\"M14 heavy-scene GX audit auto-armed frame={} staged={} next={} entries={} source=0x{:X} stride={}\",\n"
        "               sM14GuestArrayStageFrame, sM14GuestArrayStagedBytes, uploadSize,\n"
        "               sM14GuestArrayStages.size(),\n"
        "               static_cast<u64>(reinterpret_cast<std::uintptr_t>(array.data)),\n"
        "               static_cast<u32>(array.stride));\n"
        "    }\n"
        "    sM14GuestArrayHighWaterReported = true;\n"
        "  }\n"
    )
    text = replace_once(text, highwater_old, highwater_new,
                        "command_processor.cpp high-water trigger")

    if not check_only:
        path.write_text(text, encoding="utf-8", newline="\n")
    return "verified" if check_only else "applied"


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
    except (OSError, RuntimeError) as exc:
        print(f"error: Aurora heavy-scene audit adaptation failed: {exc}", file=sys.stderr)
        return 4

    messages = {
        "already-applied": "Aurora heavy-scene GX audit already applied",
        "verified": "Aurora heavy-scene GX audit verified",
        "applied": "Aurora heavy-scene GX audit applied",
    }
    print(messages[result])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
