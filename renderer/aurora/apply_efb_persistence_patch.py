#!/usr/bin/env python3
"""Migrate the regressive cross-host-frame EFB persistence experiment back out.

The first EFB-lifetime experiment changed Aurora begin_recording() so every new
host recording loaded the previous color/depth attachments. Player runtime at
cce9d93 showed severe framebuffer feedback/"solitaire" trails, missing foreground
layers and skybox-only scenes. The reason is architectural: OpenSPM's raw display
copy path currently records only a presentation event and does not yet model the
ordered Wii copy -> optional EFB clear operation. Loading old attachments across
unrelated host frames therefore preserves pixels beyond the lifetime the guest
requested.

The actual DrawDone/presentation lifetime fix now lives in the OpenSPM renderer
wrapper: a DrawDone without an original-console display copy keeps the *same*
Aurora recording/EFB open rather than ending one host frame and loading pixels
into another. This migration exists so normal M14-UPDATE-RUN heals already-patched
player caches without deleting .openspm. Fresh Aurora checkouts remain unchanged.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
LOCK = HERE / "aurora.lock.json"
SIGNATURE = "M14 persistent Wii EFB across host recording frames active"


def run(*args: str, cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected source fragment exactly once, found {count}")
    return text.replace(old, new, 1)


def transform(source: pathlib.Path, check_only: bool) -> str:
    path = source / "lib/gfx/recording.cpp"
    text = path.read_text(encoding="utf-8")
    if SIGNATURE not in text:
        return "inactive"

    state_new = '''FrameRecorder g_recorder;
// M14 persistent Wii EFB across host recording frames active. DrawDone
// recycles staging/command packets, but does not clear console EFB state.
bool sM14PersistentEfbValid = false;
uint32_t sM14PersistentEfbWidth = 0;
uint32_t sM14PersistentEfbHeight = 0;
bool sM14PersistentEfbLogged = false;

std::string pass_label(std::string_view kind) {
'''
    state_old = '''FrameRecorder g_recorder;

std::string pass_label(std::string_view kind) {
'''
    text = once(text, state_new, state_old, "recording.cpp persistent-EFB state rollback")

    begin_new = '''  current_render_passes().emplace_back();
  auto& pass = current_render_passes()[0];
  pass.label = pass_label("EFB");
  set_efb_targets(pass);
  const auto m14EfbSize = webgpu::g_frameBuffer.size;
  const bool m14PreserveEfb =
      sM14PersistentEfbValid && sM14PersistentEfbWidth == m14EfbSize.width &&
      sM14PersistentEfbHeight == m14EfbSize.height;
  if (m14PreserveEfb) {
    auto& sceneColor = pass.colorAttachments[SceneColorAttachmentIndex];
    sceneColor.loadOp = wgpu::LoadOp::Load;
    sceneColor.clear = false;
    pass.depthLoadOp = wgpu::LoadOp::Load;
    pass.clearDepth = false;
    if (!sM14PersistentEfbLogged) {
      Log.info("M14 persistent Wii EFB across host recording frames active");
      sM14PersistentEfbLogged = true;
    }
  } else {
    sM14PersistentEfbValid = true;
    sM14PersistentEfbWidth = m14EfbSize.width;
    sM14PersistentEfbHeight = m14EfbSize.height;
  }
  pass.colorAttachments[SceneColorAttachmentIndex].clearValue = gx::g_gxState.clearColor;
  pass.clearDepthValue = gx::clear_depth_value();
'''
    begin_old = '''  current_render_passes().emplace_back();
  auto& pass = current_render_passes()[0];
  pass.label = pass_label("EFB");
  set_efb_targets(pass);
  pass.colorAttachments[SceneColorAttachmentIndex].clearValue = gx::g_gxState.clearColor;
  pass.clearDepthValue = gx::clear_depth_value();
'''
    text = once(text, begin_new, begin_old, "recording.cpp begin-recording EFB rollback")

    shutdown_new = '''void shutdown_recording() {
  sM14PersistentEfbValid = false;
  sM14PersistentEfbWidth = 0;
  sM14PersistentEfbHeight = 0;
  sM14PersistentEfbLogged = false;
  for (auto& pool : g_passSnapshotPools) {
    pool = {};
  }
'''
    shutdown_old = '''void shutdown_recording() {
  for (auto& pool : g_passSnapshotPools) {
    pool = {};
  }
'''
    text = once(text, shutdown_new, shutdown_old, "recording.cpp persistent-EFB reset rollback")

    if not check_only:
        path.write_text(text, encoding="utf-8", newline="\n")
    return "verified-rollback" if check_only else "rolled-back"


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
        print(f"error: Aurora persistent-EFB rollback failed: {exc}", file=sys.stderr)
        return 4
    print({
        "inactive": "Aurora cross-frame EFB persistence experiment inactive",
        "verified-rollback": "Aurora cross-frame EFB persistence rollback verified",
        "rolled-back": "Aurora cross-frame EFB persistence experiment rolled back",
    }[result])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
