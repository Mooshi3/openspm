#!/usr/bin/env python3
"""Present a stretched framebuffer at a fixed display aspect (16:9 widescreen).

With [Display] widescreen = 1 the game renders an anamorphic 16:9 image into
its 4:3 (640x456..) EFB, like a Wii set to a 16:9 TV. The renderer then uses
AURORA_VIEWPORT_STRETCH (independent X/Y logical-to-host scales, the same path
as aspect = stretch) and this patch adds a stretch target aspect: the content
framebuffer is fitted to 16:9 inside the window instead of filling it, so the
existing present path centres it with pillarbox / letterbox bars. The host EFB
itself is 16:9 (e.g. 1920x1080), so the anamorphic image is rendered at full
horizontal resolution and never upscaled.

  AuroraSetStretchAspect(float aspect)  0 = fill the window (plain stretch)

Exact/idempotent; fails closed on unexpected source drift.
"""
from __future__ import annotations

import argparse
from pathlib import Path

SIGNATURE = "OpenSPM widescreen stretch aspect"

WINDOW_CPP = Path("lib/window.cpp")
WINDOW_HPP = Path("lib/window.hpp")
GX_AURORA_H = Path("include/dolphin/gx/GXAurora.h")
GX_AURORA_CPP = Path("lib/dolphin/gx/GXAurora.cpp")

GLOBAL_OLD = "bool g_frameBufferAspectFit = false;\n"
GLOBAL_NEW = ("bool g_frameBufferAspectFit = false;\n"
              "// " + SIGNATURE + ": >0 fits a non-FIT framebuffer to this display aspect.\n"
              "float g_frameBufferStretchAspect = 0.f;\n")

SIZE_OLD = """  if (g_frameBufferAspectFit) {
    const auto [baseW, baseH] = vi::configured_fb_size();
    if (baseW > 0 && baseH > 0) {
      const auto [fitW, fitH] =
          fit_frame_buffer_to_aspect(fb_w, fb_h, static_cast<float>(baseW) / static_cast<float>(baseH));
      fb_w = fitW;
      fb_h = fitH;
    }
  }
"""
SIZE_NEW = SIZE_OLD[:-1] + """ else if (g_frameBufferStretchAspect > 0.f) {
    const auto [fitW, fitH] = fit_frame_buffer_to_aspect(fb_w, fb_h, g_frameBufferStretchAspect);
    fb_w = fitW;
    fb_h = fitH;
  }
"""

SETTER_OLD = """void set_frame_buffer_aspect_fit(bool fit) {
  if (g_frameBufferAspectFit == fit) {
    return;
  }

  g_frameBufferAspectFit = fit;
  request_frame_buffer_resize();
}
"""
SETTER_NEW = SETTER_OLD + """
void set_frame_buffer_stretch_aspect(float aspect) {
  if (!(aspect > 0.f)) {
    aspect = 0.f;
  }
  if (g_frameBufferStretchAspect == aspect) {
    return;
  }

  g_frameBufferStretchAspect = aspect;
  request_frame_buffer_resize();
}
"""

HPP_OLD = "void set_frame_buffer_aspect_fit(bool fit);\n"
HPP_NEW = HPP_OLD + "void set_frame_buffer_stretch_aspect(float aspect);\n"

H_OLD = "void AuroraSetViewportPolicy(AuroraViewportPolicy policy);\n"
H_NEW = H_OLD + """
/**
 * """ + SIGNATURE + """: when the viewport policy is not AURORA_VIEWPORT_FIT and
 * aspect > 0, the content framebuffer is fitted to this display aspect inside the
 * window (e.g. 16.f / 9.f for a widescreen anamorphic image). 0 fills the window.
 */
void AuroraSetStretchAspect(float aspect);
"""

CPP_OLD = """void AuroraSetViewportPolicy(AuroraViewportPolicy policy) {
  aurora::gx::set_viewport_policy(policy);
}
"""
CPP_NEW = CPP_OLD + """
void AuroraSetStretchAspect(float aspect) { aurora::window::set_frame_buffer_stretch_aspect(aspect); }
"""


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    if text.count(old) != 1:
        raise RuntimeError(f"{label}: expected one pinned fragment, found {text.count(old)}")
    return text.replace(old, new, 1)


def transform(root: Path, check_only: bool = False) -> str:
    paths = (WINDOW_CPP, WINDOW_HPP, GX_AURORA_H, GX_AURORA_CPP)
    texts = {p: (root / p).read_text(encoding="utf-8") for p in paths}
    edits = {
        WINDOW_CPP: [(GLOBAL_OLD, GLOBAL_NEW, "window.cpp global"), (SIZE_OLD, SIZE_NEW, "window.cpp fb size"),
                     (SETTER_OLD, SETTER_NEW, "window.cpp setter")],
        WINDOW_HPP: [(HPP_OLD, HPP_NEW, "window.hpp declaration")],
        GX_AURORA_H: [(H_OLD, H_NEW, "GXAurora.h declaration")],
        GX_AURORA_CPP: [(CPP_OLD, CPP_NEW, "GXAurora.cpp definition")],
    }
    if all(new in texts[p] for p, items in edits.items() for _, new, _ in items):
        return "already active"
    if check_only:
        return "pending"
    for path, items in edits.items():
        for old, new, label in items:
            texts[path] = replace_once(texts[path], old, new, label)
    for path in paths:
        (root / path).write_text(texts[path], encoding="utf-8")
    return "applied"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    print("Aurora widescreen stretch aspect " + transform(args.source, args.check_only))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
