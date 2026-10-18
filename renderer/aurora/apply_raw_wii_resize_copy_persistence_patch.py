#!/usr/bin/env python3
"""Preserve raw-Wii guest-visible graphics state across host window resize.

Aurora's normal source-port resize path treats every live OS window resize as a
full graphics reconfiguration: window.cpp immediately calls webgpu::resize_swapchain,
which synchronizes the GPU; gpu.cpp then clears caches and recreates the host EFB,
depth buffer, copy bind group and surface configuration. That is a reasonable
source-port policy, but it is the wrong ownership model for OpenSPM's original-Wii
runtime. A user dragging the PC window must not mutate Wii graphics state or pause
Wii simulation for hundreds of milliseconds.

OpenSPM therefore splits shell resize from graphics apply in raw-Wii mode:
* ordinary SDL pixel-size/display-scale events update host window metadata but do
  not call webgpu::resize_swapchain;
* Aurora's existing internal FutureResize event is the explicit graphics-apply
  path and still performs the normal reconfiguration. This is the seam an eventual
  OpenSPM graphics menu can use for preset sizes/aspect ratios;
* explicit apply is allowed even when a prior shell resize already updated the
  cached AuroraWindowSize, so it cannot be swallowed by the old equality early-out.

The gpu.cpp protection remains as a second line of defense for explicit graphics
apply: in raw-Wii mode, guest-visible GXCopyTex results are not erased merely
because the host render target changes. Future GXCopyTex operations remain correct
because Aurora's CopyTextureKey includes destination, scaled dimensions and format.

Normal Aurora/source-port behavior is unchanged when the raw-Wii guest-memory
mapper is absent. Unknown or partial source drift fails closed. The script also
migrates the short-lived compile-broken diagnostic that referenced g_gxState from
gpu.cpp.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
LOCK = HERE / "aurora.lock.json"
GPU_TARGET = Path("lib/webgpu/gpu.cpp")
WINDOW_TARGET = Path("lib/window.cpp")
GPU_SIGNATURE = "M14 raw-Wii resize preserves guest-visible EFB copy textures"
WINDOW_SIGNATURE = "M14 raw-Wii live window resize deferred"

UPSTREAM_BLOCK = (
    "  if (sizeChanged) {\n"
    "    gx::clear_copy_texture_cache();\n"
    "    gfx::clear_caches();\n"
    "  }\n"
)

BROKEN_BLOCK = (
    "  if (sizeChanged) {\n"
    "    if (gx::guest_memory::has_mapper()) {\n"
    "      // M14 raw-Wii resize preserves guest-visible EFB copy textures.\n"
    "      // These host textures represent GXCopyTex contents that the Wii\n"
    "      // game believes live in RAM; a PC window resize is not a guest\n"
    "      // memory invalidation. Future copies re-key by scaled size.\n"
    "      static uint64_t sM14RawWiiResizeCount = 0;\n"
    "      const uint64_t ordinal = ++sM14RawWiiResizeCount;\n"
    "      if (ordinal <= 8u || (ordinal & (ordinal - 1u)) == 0u) {\n"
    "        Log.info(\"M14 raw-Wii resize preserves guest-visible EFB copy textures n={} render={}x{} surface={}x{} copies={} cache_entries={}\",\n"
    "                 ordinal, width, height, nativeWidth, nativeHeight,\n"
    "                 g_gxState.copyTextures.size(), g_gxState.copyTextureCache.size());\n"
    "      }\n"
    "    } else {\n"
    "      gx::clear_copy_texture_cache();\n"
    "    }\n"
    "    gfx::clear_caches();\n"
    "  }\n"
)

GOOD_BLOCK = (
    "  if (sizeChanged) {\n"
    "    if (gx::guest_memory::has_mapper()) {\n"
    "      // M14 raw-Wii resize preserves guest-visible EFB copy textures.\n"
    "      // These host textures represent GXCopyTex contents that the Wii\n"
    "      // game believes live in RAM; a PC window resize is not a guest\n"
    "      // memory invalidation. Future copies re-key by scaled size.\n"
    "      static uint64_t sM14RawWiiResizeCount = 0;\n"
    "      const uint64_t ordinal = ++sM14RawWiiResizeCount;\n"
    "      if (ordinal <= 8u || (ordinal & (ordinal - 1u)) == 0u) {\n"
    "        Log.info(\"M14 raw-Wii resize preserves guest-visible EFB copy textures n={} render={}x{} surface={}x{}\",\n"
    "                 ordinal, width, height, nativeWidth, nativeHeight);\n"
    "      }\n"
    "    } else {\n"
    "      gx::clear_copy_texture_cache();\n"
    "    }\n"
    "    gfx::clear_caches();\n"
    "  }\n"
)

WINDOW_INCLUDE_OLD = (
    "#ifdef AURORA_ENABLE_GX\n"
    "#include \"imgui.hpp\"\n"
    "#include \"webgpu/gpu.hpp\"\n"
    "#endif\n"
)
WINDOW_INCLUDE_NEW = (
    "#ifdef AURORA_ENABLE_GX\n"
    "#include <aurora/gx_guest_memory.hpp>\n"
    "#include \"imgui.hpp\"\n"
    "#include \"webgpu/gpu.hpp\"\n"
    "#endif\n"
)
WINDOW_CALL_OLD = (
    "#ifdef AURORA_ENABLE_GX\n"
    "  webgpu::resize_swapchain(size.fb_width, size.fb_height, size.native_fb_width, size.native_fb_height);\n"
    "#endif\n"
)
WINDOW_CALL_NEW = (
    "#ifdef AURORA_ENABLE_GX\n"
    "  if (aurora::gx::guest_memory::has_mapper() && !explicitGraphicsApply) {\n"
    "    // M14 raw-Wii live window resize deferred. The OS shell may resize,\n"
    "    // but Wii EFB/surface resources remain untouched until an explicit\n"
    "    // Aurora FutureResize graphics apply (future OpenSPM menu seam).\n"
    "    static unsigned long long sM14RawWiiLiveResizeCount = 0;\n"
    "    const auto ordinal = ++sM14RawWiiLiveResizeCount;\n"
    "    if (ordinal <= 8u || (ordinal & (ordinal - 1u)) == 0u) {\n"
    "      Log.info(\"M14 raw-Wii live window resize deferred n={} shell={}x{} native={}x{}; graphics apply unchanged\",\n"
    "               ordinal, size.width, size.height, size.native_fb_width, size.native_fb_height);\n"
    "    }\n"
    "    return;\n"
    "  }\n"
    "  webgpu::resize_swapchain(size.fb_width, size.fb_height, size.native_fb_width, size.native_fb_height);\n"
    "#endif\n"
)
FUTURE_RESIZE_OLD = (
    "    if (event.type == g_sdlCustomEventsStart + CustomEvent::FutureResize) {\n"
    "      // Future resize event\n"
    "      resize_swapchain();\n"
)
FUTURE_RESIZE_NEW = (
    "    if (event.type == g_sdlCustomEventsStart + CustomEvent::FutureResize) {\n"
    "      // Future resize event: explicit graphics apply, including raw-Wii mode.\n"
    "      resize_swapchain(true);\n"
)


def run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected source fragment exactly once, found {count}")
    return text.replace(old, new, 1)


def transform_gpu(source: Path, check_only: bool) -> str:
    path = source / GPU_TARGET
    text = path.read_text(encoding="utf-8")

    # Heal the compile-broken first diagnostic revision in existing player
    # caches. The actual resize-preservation policy is unchanged.
    if "g_gxState.copyTextures.size()" in text or "g_gxState.copyTextureCache.size()" in text:
        if text.count(BROKEN_BLOCK) != 1:
            raise RuntimeError("broken resize diagnostic detected with unexpected source shape")
        text = text.replace(BROKEN_BLOCK, GOOD_BLOCK, 1)
        if not check_only:
            path.write_text(text, encoding="utf-8", newline="\n")
        return "migrated"

    if GPU_SIGNATURE in text:
        required = (
            "#include <aurora/gx_guest_memory.hpp>",
            "gx::guest_memory::has_mapper()",
            "sM14RawWiiResizeCount",
            "gx::clear_copy_texture_cache();",
            "gfx::clear_caches();",
        )
        missing = [fragment for fragment in required if fragment not in text]
        if missing:
            raise RuntimeError(
                "resize-persistence marker present with incomplete source shape: "
                + ", ".join(missing)
            )
        if text.count(GOOD_BLOCK) != 1:
            raise RuntimeError("resize-persistence marker present with unexpected policy block")
        return "already-applied"

    if not (source / "include/aurora/gx_guest_memory.hpp").exists():
        raise RuntimeError("raw-Wii guest-memory adaptation must be applied first")

    text = once(
        text,
        "#include <aurora/aurora.h>\n"
        "#include <aurora/webgpu.hpp>\n"
        "#include <aurora/gfx.h>\n",
        "#include <aurora/aurora.h>\n"
        "#include <aurora/webgpu.hpp>\n"
        "#include <aurora/gfx.h>\n"
        "#include <aurora/gx_guest_memory.hpp>\n",
        "gpu guest-memory include",
    )
    text = once(text, UPSTREAM_BLOCK, GOOD_BLOCK, "resize copy-cache policy")

    if not check_only:
        path.write_text(text, encoding="utf-8", newline="\n")
    return "verified" if check_only else "applied"


def transform_window(source: Path, check_only: bool) -> str:
    path = source / WINDOW_TARGET
    text = path.read_text(encoding="utf-8")

    if WINDOW_SIGNATURE in text:
        required = (
            "#include <aurora/gx_guest_memory.hpp>",
            "void resize_swapchain(bool explicitGraphicsApply = false) noexcept",
            "if (!explicitGraphicsApply && size == g_windowSize)",
            "aurora::gx::guest_memory::has_mapper() && !explicitGraphicsApply",
            "resize_swapchain(true);",
        )
        missing = [fragment for fragment in required if fragment not in text]
        if missing:
            raise RuntimeError(
                "live-resize deferral marker present with incomplete source shape: "
                + ", ".join(missing)
            )
        return "already-applied"

    text = once(text, WINDOW_INCLUDE_OLD, WINDOW_INCLUDE_NEW, "window guest-memory include")
    text = once(
        text,
        "void resize_swapchain() noexcept {\n"
        "  const auto size = get_window_size();\n"
        "  if (size == g_windowSize) {\n",
        "void resize_swapchain(bool explicitGraphicsApply = false) noexcept {\n"
        "  const auto size = get_window_size();\n"
        "  if (!explicitGraphicsApply && size == g_windowSize) {\n",
        "window explicit resize apply gate",
    )
    text = once(text, WINDOW_CALL_OLD, WINDOW_CALL_NEW, "window raw-Wii live resize deferral")
    text = once(text, FUTURE_RESIZE_OLD, FUTURE_RESIZE_NEW, "window FutureResize explicit apply")

    if not check_only:
        path.write_text(text, encoding="utf-8", newline="\n")
    return "verified" if check_only else "applied"


def transform(source: Path, check_only: bool) -> tuple[str, str]:
    return transform_gpu(source, check_only), transform_window(source, check_only)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
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
        gpu_result, window_result = transform(source, args.check_only)
    except (OSError, RuntimeError) as exc:
        print(f"error: Aurora raw-Wii resize adaptation failed: {exc}", file=sys.stderr)
        return 4

    if args.check_only:
        print(f"Aurora raw-Wii resize policy verified gpu={gpu_result} window={window_result}")
    else:
        print(f"Aurora raw-Wii resize policy ready gpu={gpu_result} window={window_result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
