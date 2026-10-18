#!/usr/bin/env python3
"""Apply OpenSPM's raw-FIFO reentrancy fix to the adapted Aurora checkout.

The Stage-A raster layer queues PE bounding-box clear/write operations as Aurora
encoder tasks so they stay ordered between GX draws. Aurora's public
push_encoder_task() first drains the GX FIFO, which is correct for source-port
callers but recursive when the task originates from Aurora's raw FIFO parser.
At the first SPM GXDrawDone this recurses into fifo::drain() while process_to()
already owns the FIFO mutex.

This adaptation layer adds an internal no-drain task path used only by the raw
bbox parser. The public Aurora API retains its normal validation and
drain-before-task contract. The transform is exact and idempotent against the
pinned revision after apply_patches.py + apply_raster_patch.py.

This configure hook also owns small idempotent migrations that must reach cached
player Aurora checkouts during an ordinary M14 update-run. The ByteBuffer abort
probe remains available for fail-fast diagnostics, while the high-frequency
2048-draw CPU profiler is retired from normal runs so host clock reads/logging
do not contaminate current performance measurements. Low-volume frame-acquire
wait diagnostics distinguish render-worker slot pressure from staging-map stalls,
phase diagnostics split the remaining Aurora update/begin-frame time,
present-pacing diagnostics measure successful swapchain Present() cadence on the
render worker rather than inferring smoothness from Wii retrace delivery, and
render-worker pacing diagnostics split scene-dependent frame lateness into
producer cadence, worker queue backlog, and end-frame work.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
LOCK = HERE / "aurora.lock.json"
SIGNATURE = "push_encoder_task_from_fifo"

TRANSFORMS: dict[str, list[tuple[str, str]]] = {
    "lib/gfx/recording.hpp": [
        (
            "void clear_caches() noexcept;\n",
            "void clear_caches() noexcept;\n"
            "\n"
            "// Internal raw-GX path: caller is already executing inside fifo::drain().\n"
            "// Record an encoder task at the current command-stream position without\n"
            "// recursively draining the FIFO. Public push_encoder_task() keeps its\n"
            "// normal drain-before-task contract.\n"
            "bool push_encoder_task_from_fifo(EncoderTaskId type, const void* payload, size_t payloadSize);\n",
        ),
    ],
    "lib/gfx/recording.cpp": [
        (
            "bool push_encoder_task(EncoderTaskId type, const void* payload, size_t payloadSize) {\n",
            "bool push_encoder_task_from_fifo(EncoderTaskId type, const void* payload, size_t payloadSize) {\n",
        ),
        (
            "  if (!find_runtime_encoder_task_type(type)) {\n"
            "    Log.warn(\"push_encoder_task: unregistered encoder task type {:#x}\", type);\n"
            "    return false;\n"
            "  }\n"
            "\n"
            "  gx::fifo::drain();\n"
            "\n"
            "  if (!g_recorder.active() || g_recorder.currentRenderPass == UINT32_MAX) {\n",
            "  if (!find_runtime_encoder_task_type(type)) {\n"
            "    Log.warn(\"push_encoder_task: unregistered encoder task type {:#x}\", type);\n"
            "    return false;\n"
            "  }\n"
            "\n"
            "  if (!g_recorder.active() || g_recorder.currentRenderPass == UINT32_MAX) {\n",
        ),
        (
            "  resume_efb_pass_loading(prevPass);\n"
            "  return true;\n"
            "}\n"
            "\n"
            "template <>\n"
            "void push_draw_command(gx::DrawData data) {\n",
            "  resume_efb_pass_loading(prevPass);\n"
            "  return true;\n"
            "}\n"
            "\n"
            "bool push_encoder_task(EncoderTaskId type, const void* payload, size_t payloadSize) {\n"
            "  // Preserve upstream public behavior: reject invalid calls before\n"
            "  // draining, then establish the command-stream boundary and delegate\n"
            "  // to the same validated recorder used by the raw-FIFO path.\n"
            "  if (type == InvalidEncoderTask) {\n"
            "    Log.warn(\"push_encoder_task: invalid encoder task type\");\n"
            "    return false;\n"
            "  }\n"
            "  if (payloadSize > InlineDrawPayloadSize) {\n"
            "    Log.warn(\"push_encoder_task: payload size {} exceeds inline payload size {}\", payloadSize, InlineDrawPayloadSize);\n"
            "    return false;\n"
            "  }\n"
            "  if (payloadSize > 0 && payload == nullptr) {\n"
            "    Log.warn(\"push_encoder_task: non-zero payload size with null payload\");\n"
            "    return false;\n"
            "  }\n"
            "  if (!find_runtime_encoder_task_type(type)) {\n"
            "    Log.warn(\"push_encoder_task: unregistered encoder task type {:#x}\", type);\n"
            "    return false;\n"
            "  }\n"
            "  gx::fifo::drain();\n"
            "  return push_encoder_task_from_fifo(type, payload, payloadSize);\n"
            "}\n"
            "\n"
            "template <>\n"
            "void push_draw_command(gx::DrawData data) {\n",
        ),
    ],
    "lib/gx/bbox.cpp": [
        (
            '#include <aurora/gfx.hpp>\n'
            "\n"
            '#include "../internal.hpp"\n',
            '#include <aurora/gfx.hpp>\n'
            "\n"
            '#include "../gfx/recording.hpp"\n'
            '#include "../internal.hpp"\n',
        ),
        (
            "    if (gfx::push_encoder_task(sWriteTask, &payload, sizeof(payload))) return;\n",
            "    if (gfx::push_encoder_task_from_fifo(sWriteTask, &payload, sizeof(payload))) return;\n",
        ),
    ],
}


def run(*args: str, cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def transform(source: pathlib.Path, check_only: bool) -> str:
    paths = {relative: source / relative for relative in TRANSFORMS}
    texts = {relative: path.read_text(encoding="utf-8") for relative, path in paths.items()}

    signature_state = {relative: SIGNATURE in text for relative, text in texts.items()}
    if all(signature_state.values()):
        return "already-applied"
    if any(signature_state.values()):
        partial = ", ".join(
            f"{relative}={'yes' if present else 'no'}"
            for relative, present in signature_state.items()
        )
        raise RuntimeError(f"partial FIFO reentrancy adaptation detected: {partial}")

    pending: dict[pathlib.Path, str] = {}
    for relative, replacements in TRANSFORMS.items():
        text = texts[relative]
        for old, new in replacements:
            count = text.count(old)
            if count != 1:
                raise RuntimeError(
                    f"{relative}: expected source fragment exactly once, found {count}"
                )
            text = text.replace(old, new, 1)
        pending[paths[relative]] = text

    if not check_only:
        for path, contents in pending.items():
            path.write_text(contents, encoding="utf-8", newline="\n")
    return "verified" if check_only else "applied"


def run_companion_patch(source: pathlib.Path, check_only: bool,
                        script_name: str) -> str:
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
    parser.add_argument(
        "source",
        type=pathlib.Path,
        help="Aurora checkout after OpenSPM raw+raster adaptations",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="verify the exact transform without modifying source",
    )
    args = parser.parse_args()

    source = args.source.resolve()
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    expected = lock["commit"]

    try:
        actual = run("git", "rev-parse", "HEAD", cwd=source).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"error: not a usable git checkout: {source}: {exc}", file=sys.stderr)
        return 2
    if actual != expected:
        print(f"error: Aurora revision mismatch: expected {expected}, got {actual}", file=sys.stderr)
        return 3

    if not (source / "include/aurora/gx_guest_memory.hpp").exists():
        print("error: raw-GX layer is not applied; run apply_patches.py first", file=sys.stderr)
        return 4
    if not (source / "lib/gx/bbox.cpp").exists():
        print("error: raster layer is not applied; run apply_raster_patch.py first", file=sys.stderr)
        return 5

    try:
        result = transform(source, args.check_only)
    except (OSError, RuntimeError) as exc:
        print(f"error: Aurora FIFO reentrancy adaptation failed: {exc}", file=sys.stderr)
        return 6

    try:
        diag_result = run_companion_patch(
            source, args.check_only, "apply_bytebuffer_abort_diag_patch.py")
        quiet_result = run_companion_patch(
            source, args.check_only, "apply_normal_run_diagnostic_quiet_patch.py")
        frame_wait_result = run_companion_patch(
            source, args.check_only, "apply_frame_acquire_wait_diag_patch.py")
        phase_result = run_companion_patch(
            source, args.check_only, "apply_aurora_begin_phase_diag_patch.py")
        present_pacing_result = run_companion_patch(
            source, args.check_only, "apply_present_pacing_diag_patch.py")
        worker_pacing_result = run_companion_patch(
            source, args.check_only, "apply_render_worker_pacing_diag_patch.py")
        duplicate_pipeline_result = run_companion_patch(
            source, args.check_only, "apply_cp_duplicate_pipeline_patch.py")
    except (OSError, subprocess.CalledProcessError) as exc:
        stderr = getattr(exc, "stderr", "") or ""
        print(f"error: Aurora configure companion patch failed: {stderr.strip() or exc}", file=sys.stderr)
        return 7

    if result == "already-applied":
        print("Aurora FIFO reentrancy adaptation already applied")
    elif result == "verified":
        print("Aurora FIFO reentrancy transforms verified")
    else:
        print("Aurora FIFO reentrancy transforms applied")
    if diag_result:
        print(diag_result)
    if quiet_result:
        print(quiet_result)
    if frame_wait_result:
        print(frame_wait_result)
    if phase_result:
        print(phase_result)
    if present_pacing_result:
        print(present_pacing_result)
    if worker_pacing_result:
        print(worker_pacing_result)
    if duplicate_pipeline_result:
        print(duplicate_pipeline_result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
