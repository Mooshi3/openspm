#!/usr/bin/env python3
"""Roll back the M14 copy-texture ownership metadata experiment.

Runtime at 5820200 proved the shape guard is not a valid ownership test for
SPM's flip path: immediately after Wii A/flip, a host-side 608x480 copy is
legitimately sampled through a 304x240 GXTexObj.  Rejecting that 2:1 relation
introduced new blue/striped flip corruption and exposed effects that were not
visible before.  The guard therefore cannot distinguish stale guest-address
reuse from valid scaled EFB feedback.

This configure-time migration removes only the ownership experiment from an
already-patched player Aurora checkout and is a no-op on a clean/past cache.
No .openspm wipe is required.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
LOCK = HERE / "aurora.lock.json"

COPY_REF_BASE = '''  struct CopyTextureRef {\n    gfx::TextureHandle handle;\n    u32 revision = 0;\n\n    operator bool() const noexcept { return handle.operator bool(); }\n  };\n'''
COPY_REF_EXPERIMENT = '''  struct CopyTextureRef {\n    gfx::TextureHandle handle;\n    u32 revision = 0;\n\n    // Logical Wii image identity for the virtual EFB copy. OpenSPM maps both\n    // BP copy destinations and Image3 addresses to the same guest-RAM host\n    // pointer, so pointer identity alone is not sufficient ownership proof when\n    // the game allocator later reuses an address for another texture.\n    u32 logicalWidth = 0;\n    u32 logicalHeight = 0;\n    u32 logicalFormat = 0;\n\n    operator bool() const noexcept { return handle.operator bool(); }\n  };\n'''

COPY_PUBLISH_BASE = '''  ++handle.revision;\n  g_gxState.copyTextures[dest] = handle;\n  texture::invalidate_bindings();\n'''
COPY_PUBLISH_EXPERIMENT = '''  ++handle.revision;\n  // Preserve the original Wii texture identity, not the host-scaled render\n  // target dimensions. A later GXTexObj at the same guest address may only use\n  // this virtual copy while it still describes the same logical image.\n  handle.logicalWidth = g_gxState.texCopyDstWidth;\n  handle.logicalHeight = g_gxState.texCopyDstHeight;\n  handle.logicalFormat = static_cast<u32>(texCopyFmt);\n  g_gxState.copyTextures[dest] = handle;\n  texture::invalidate_bindings();\n'''

FORMAT_HELPER_BASE = '''u32 resolved_format_for_handle(const gfx::TextureHandle& handle) {\n  if (!handle) {\n    return GX_TF_RGBA8;\n  }\n  if (handle->gxFormat != gfx::InvalidTextureFormat) {\n    return handle->gxFormat;\n  }\n  return GX_TF_RGBA8_PC;\n}\n\n'''
FORMAT_HELPER_EXPERIMENT = FORMAT_HELPER_BASE + '''bool copy_texture_matches(const GXTexObj_& obj, const GXState::CopyTextureRef& source) noexcept {\n  return source.handle && source.logicalWidth == obj.width() && source.logicalHeight == obj.height() &&\n         source.logicalFormat == obj.format();\n}\n\n'''

RESOLVE_COPY_BASE = '''    gfx::TextureHandle handle;\n    const auto copyIt = g_gxState.copyTextures.find(obj.data);\n    const GXState::CopyTextureRef* copyRef = copyIt != g_gxState.copyTextures.end() ? &copyIt->second : nullptr;\n    if (is_palette_format(obj.format())) {\n'''
RESOLVE_COPY_EXPERIMENT = '''    gfx::TextureHandle handle;\n    const auto copyIt = g_gxState.copyTextures.find(obj.data);\n    const GXState::CopyTextureRef* copyRef =\n        copyIt != g_gxState.copyTextures.end() && copy_texture_matches(obj, copyIt->second) ? &copyIt->second : nullptr;\n    if (copyIt != g_gxState.copyTextures.end() && copyRef == nullptr) {\n      // OpenSPM M14 stale copy-texture ownership rejected. The Wii address has\n      // been rebound as a differently shaped/formatted texture, so the old\n      // host-only EFB copy must not shadow the new guest RAM contents. Keep the\n      // diagnostic sparse because animated allocators can reuse addresses often.\n      static uint64_t staleCopyRejects = 0;\n      const uint64_t n = ++staleCopyRejects;\n      if (n <= 16u || (n & (n - 1u)) == 0u) {\n        Log.warn(\n            "OpenSPM M14 stale copy-texture ownership rejected n={} copy={}x{}/fmt={} sample={}x{}/fmt={}",\n            n, copyIt->second.logicalWidth, copyIt->second.logicalHeight, copyIt->second.logicalFormat, obj.width(),\n            obj.height(), obj.format());\n      }\n    }\n    if (is_palette_format(obj.format())) {\n'''


def run(*args: str, cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def rollback_once(text: str, experimental: str, base: str, label: str) -> tuple[str, bool]:
    if experimental in text:
        return text.replace(experimental, base, 1), True
    if base in text:
        return text, False
    raise RuntimeError(f"{label}: neither experiment nor baseline source shape found")


def transform(source: pathlib.Path, check_only: bool) -> str:
    paths = {
        "state": source / "lib" / "gx" / "gx.hpp",
        "framebuffer": source / "lib" / "dolphin" / "gx" / "GXFrameBuffer.cpp",
        "texture": source / "lib" / "gx" / "texture.cpp",
    }
    texts = {name: path.read_text(encoding="utf-8") for name, path in paths.items()}
    changed = False

    texts["state"], did = rollback_once(texts["state"], COPY_REF_EXPERIMENT, COPY_REF_BASE, "CopyTextureRef")
    changed |= did
    # The later opt-in copy trace (apply_copy_trace_diag_patch.py v1/v2)
    # inserted its call inside this block in player caches. Move it after the
    # block so the pinned publication shape is contiguous again.
    trace_call_start = "  m14_copytrace_note_copy(dest, "
    publish_head = "  ++handle.revision;\n  g_gxState.copyTextures[dest] = handle;\n"
    invalidate = "  texture::invalidate_bindings();\n"
    fb = texts["framebuffer"]
    marker = publish_head + trace_call_start
    if fb.count(marker) == 1:
        call_start = fb.index(marker) + len(publish_head)
        call_end = fb.index(");\n", call_start) + 3
        if fb.startswith(invalidate, call_end):
            call = fb[call_start:call_end]
            fb = fb[:call_start] + invalidate + call + fb[call_end + len(invalidate):]
            texts["framebuffer"] = fb
            changed = True
    texts["framebuffer"], did = rollback_once(
        texts["framebuffer"], COPY_PUBLISH_EXPERIMENT, COPY_PUBLISH_BASE, "copy publication")
    changed |= did
    texts["texture"], did = rollback_once(
        texts["texture"], FORMAT_HELPER_EXPERIMENT, FORMAT_HELPER_BASE, "copy compatibility helper")
    changed |= did
    # Copy trace v1/v2 put its bind hook inside the pinned resolver block; drop
    # it here (the trace patch re-inserts it before the block later in the chain).
    old_trace_bind = "    m14_copytrace_bind(obj, copyRef != nullptr);\n"
    if texts["texture"].count(old_trace_bind) == 1:
        texts["texture"] = texts["texture"].replace(old_trace_bind, "", 1)
        changed = True
    texts["texture"], did = rollback_once(
        texts["texture"], RESOLVE_COPY_EXPERIMENT, RESOLVE_COPY_BASE, "copy resolver")
    changed |= did

    if COPY_REF_EXPERIMENT in texts["state"] or COPY_PUBLISH_EXPERIMENT in texts["framebuffer"]:
        raise RuntimeError("copy ownership rollback left experimental metadata active")
    if "copy_texture_matches(" in texts["texture"] or "stale copy-texture ownership rejected" in texts["texture"]:
        raise RuntimeError("copy ownership rollback left experimental resolver active")

    if not check_only and changed:
        for name, path in paths.items():
            path.write_text(texts[name], encoding="utf-8", newline="\n")

    if check_only:
        return "verified"
    return "rolled-back" if changed else "already-baseline"


def main() -> int:
    if len(sys.argv) < 2 or len(sys.argv) > 3 or (len(sys.argv) == 3 and sys.argv[2] != "--check-only"):
        print("usage: apply_copy_texture_ownership_rollback_patch.py <aurora-source-dir> [--check-only]", file=sys.stderr)
        return 2
    source = pathlib.Path(sys.argv[1]).resolve()
    check_only = len(sys.argv) == 3
    expected = json.loads(LOCK.read_text(encoding="utf-8"))["commit"]
    try:
        actual = run("git", "rev-parse", "HEAD", cwd=source).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"error: not a usable git checkout: {source}: {exc}", file=sys.stderr)
        return 3
    if actual != expected:
        print(f"error: Aurora revision mismatch: expected {expected}, got {actual}", file=sys.stderr)
        return 4
    try:
        result = transform(source, check_only)
    except (OSError, RuntimeError) as exc:
        print(f"error: Aurora copy-ownership rollback failed: {exc}", file=sys.stderr)
        return 5
    print(f"Aurora copy-texture ownership experiment {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
