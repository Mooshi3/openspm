#!/usr/bin/env python3
"""Resolve unscaled EFB texture copies from exactly the pixels the game drew.

At a non-integer EFB scale (2.25x for 1440x1080), a logical copy rectangle such
as x=238 w=88 maps to host 535.5..733.5. map_logical_scissor snaps it outward
(floor/ceil) to 535..734, i.e. 199 px, and the resolve then resamples those
199 px into the 198-px copy texture with linear filtering. The capture starts
half a pixel up-left of the real image and drifts to ~1 px at the far edge, so
effects that draw the copy back in place (star-door swing) sit slightly
down-right of the original and leave a sliver of it visible.

For copies that are not meant to scale (logical source size == destination
size), pick the pixels the rasterizer actually covered (top-left fill rule:
first pixel ceil(v*s - 0.5)) with the destination texture's exact size, so the
resolve is a straight texel copy. Intentional downscales (box-filtered half
size copies) keep Aurora's mapping. OPENSPM_M14_COPY_PIXEL_EXACT=0 restores it.

Revision 2 (the actual star-door offset): GX viewport origin. The game's SDK
(GXSetViewportJitter 0x802923D0, constant 342.0 at r2-220 = 0x80575B84) writes
XF ox/oy = left/top + size/2 + 342, the same 342 the scissor registers carry.
Aurora decodes (and its own GXSetViewport encodes) the viewport with 340, so
every raw-FIFO draw landed 2 logical px (4.5 host px at 2.25x) right of and
below the coordinates the game uses for scissor, bbox and copy rectangles.
Bbox/copy pick up the door where it was drawn (+2), and the snapshot halves,
positioned at that capture rect, get a second +2: they sit 2 px down-right of
the scene's door, leaving its top rows visible above them. Decode and encode
with 342; OPENSPM_M14_VIEWPORT_ORIGIN=0 restores Aurora's 340. A cache
patched by revision 1 migrates (revision 2 only adds the viewport pieces).

Exact/idempotent; fails closed on unexpected source drift.
"""
from __future__ import annotations

import argparse
from pathlib import Path

SIGNATURE = "OpenSPM M14 pixel-exact EFB copy source"
FRAMEBUFFER = Path("lib/dolphin/gx/GXFrameBuffer.cpp")

INCLUDE_ANCHOR = "#include <algorithm>\n#include <cmath>\n"
HELPER = INCLUDE_ANCHOR + r'''#include <cstdlib>

// OpenSPM M14 pixel-exact EFB copy source (OPENSPM_M14_COPY_PIXEL_EXACT=0 disables).
namespace {
aurora::gfx::ClipRect m14_copy_source_rect(const aurora::gfx::ClipRect& logical) noexcept {
  const auto mapped = aurora::gx::map_logical_scissor(logical);
  static const bool enabled = [] {
    const char* value = std::getenv("OPENSPM_M14_COPY_PIXEL_EXACT");
    return value == nullptr || value[0] != '0';
  }();
  if (!enabled || g_gxState.viewportPolicy == AURORA_VIEWPORT_NATIVE || logical.width <= 0 || logical.height <= 0)
    return mapped;
  // Only unscaled copies; box-filtered downscales keep Aurora's resample.
  if (g_gxState.texCopyDstWidth != static_cast<u32>(logical.width) ||
      g_gxState.texCopyDstHeight != static_cast<u32>(logical.height))
    return mapped;
  const auto [logicalWidth, logicalHeight] = aurora::gx::logical_fb_size();
  const auto [targetWidth, targetHeight] = aurora::gfx::get_render_target_size();
  if (logicalWidth == 0 || logicalHeight == 0 || targetWidth == 0 || targetHeight == 0) return mapped;
  const float scaleX = static_cast<float>(targetWidth) / static_cast<float>(logicalWidth);
  const float scaleY = static_cast<float>(targetHeight) / static_cast<float>(logicalHeight);
  // Same size rule as scale_copy_dst, so the resolve needs no resample.
  const auto width = std::max<int32_t>(static_cast<int32_t>(std::lround(static_cast<float>(logical.width) * scaleX)), 1);
  const auto height = std::max<int32_t>(static_cast<int32_t>(std::lround(static_cast<float>(logical.height) * scaleY)), 1);
  // First covered pixel under the top-left fill rule (pixel centres at k + 0.5).
  const auto x = static_cast<int32_t>(std::ceil(static_cast<double>(logical.x) * scaleX - 0.5));
  const auto y = static_cast<int32_t>(std::ceil(static_cast<double>(logical.y) * scaleY - 0.5));
  if (x < 0 || y < 0 || x + width > static_cast<int32_t>(targetWidth) || y + height > static_cast<int32_t>(targetHeight))
    return mapped;
  return {.x = x, .y = y, .width = width, .height = height};
}
} // namespace
'''
RECT_OLD = "  const auto rect = map_logical_scissor(g_gxState.texCopySrc);\n"
RECT_NEW = ("  // " + SIGNATURE + ".\n"
            "  const auto rect = m14_copy_source_rect(g_gxState.texCopySrc);\n")

# Revision 2: GX viewport origin 342 (SDK and scissor), not Aurora's 340.
VIEWPORT_SIGNATURE = "OpenSPM M14 GX viewport origin 342"
REGS = Path("lib/gx/regs.cpp")
TRANSFORM = Path("lib/dolphin/gx/GXTransform.cpp")
ORIGIN_BODY = r'''  static const float origin = [] {
    const char* value = std::getenv("OPENSPM_M14_VIEWPORT_ORIGIN");
    return value != nullptr && value[0] == '0' ? 340.0f : 342.0f;
  }();
  return origin;
'''
REGS_ANCHOR = "// Viewport (0x101A-0x101F)\nvoid xf_load_viewport(const u8* data) noexcept {\n"
# Close the fifo namespaces for a file-scope include, then reopen them (the
# reopening line differs from Aurora's, so other patches' anchors stay unique).
REGS_HELPER = (
    "} // namespace\n} // namespace aurora::gx::fifo\n\n"
    "// " + VIEWPORT_SIGNATURE + ": the game's GXSetViewport writes ox/oy with +342,\n"
    "// like the scissor (bp_scissor); Aurora used 340, shifting every draw 2 px down-right\n"
    "// of scissor/bbox/copy coordinates (OPENSPM_M14_VIEWPORT_ORIGIN=0 restores 340).\n"
    "#include <cstdlib>\n\n"
    "namespace aurora::gx::fifo {\nnamespace { // " + VIEWPORT_SIGNATURE + "\n"
    "float m14_viewport_origin() noexcept {\n" + ORIGIN_BODY + "}\n\n" + REGS_ANCHOR)
REGS_VIEWPORT_OLD = ("      .left = ox - 340.0f - width / 2.0f,\n"
                     "      .top = oy - 340.0f - height / 2.0f,\n")
REGS_VIEWPORT_NEW = ("      .left = ox - m14_viewport_origin() - width / 2.0f,\n"
                     "      .top = oy - m14_viewport_origin() - height / 2.0f,\n")
TRANSFORM_ANCHOR = '#include "gx.hpp"\n#include "__gx.h"\n#include "dolphin/mtx/GeoTypes.h"\n'
TRANSFORM_HELPER = (TRANSFORM_ANCHOR + "\n#include <cstdlib>\n\n"
                    "// " + VIEWPORT_SIGNATURE + " (encode side; matches lib/gx/regs.cpp decode).\n"
                    "static float m14_viewport_origin() noexcept {\n" + ORIGIN_BODY + "}\n")
TRANSFORM_OLD = ("  ox = 340.0f + (left + width / 2.0f);\n"
                 "  oy = 340.0f + (top + height / 2.0f);\n")
TRANSFORM_NEW = ("  ox = m14_viewport_origin() + (left + width / 2.0f);\n"
                 "  oy = m14_viewport_origin() + (top + height / 2.0f);\n")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if text.count(old) != 1:
        raise RuntimeError(f"{label}: expected one pinned fragment, found {text.count(old)}")
    return text.replace(old, new, 1)


def transform(root: Path, check_only: bool = False) -> str:
    fb = (root / FRAMEBUFFER).read_text(encoding="utf-8")
    regs = (root / REGS).read_text(encoding="utf-8")
    xform = (root / TRANSFORM).read_text(encoding="utf-8")
    if (HELPER in fb and RECT_NEW in fb and REGS_HELPER in regs and REGS_VIEWPORT_NEW in regs
            and TRANSFORM_HELPER in xform and TRANSFORM_NEW in xform):
        return "already active"
    if check_only:
        return "pending"
    if HELPER not in fb:
        fb = replace_once(fb, INCLUDE_ANCHOR, HELPER, "GXFrameBuffer.cpp includes")
    if RECT_NEW not in fb:
        fb = replace_once(fb, RECT_OLD, RECT_NEW, "copy_tex source rect")
    # Revision 2 pieces (a revision-1 cache has none of them yet).
    if REGS_HELPER not in regs:
        regs = replace_once(regs, REGS_ANCHOR, REGS_HELPER, "regs.cpp viewport helper")
    if REGS_VIEWPORT_NEW not in regs:
        regs = replace_once(regs, REGS_VIEWPORT_OLD, REGS_VIEWPORT_NEW, "regs.cpp xf_load_viewport origin")
    if TRANSFORM_HELPER not in xform:
        xform = replace_once(xform, TRANSFORM_ANCHOR, TRANSFORM_HELPER, "GXTransform.cpp helper")
    if TRANSFORM_NEW not in xform:
        xform = replace_once(xform, TRANSFORM_OLD, TRANSFORM_NEW, "GXTransform.cpp viewport origin")
    (root / FRAMEBUFFER).write_text(fb, encoding="utf-8")
    (root / REGS).write_text(regs, encoding="utf-8")
    (root / TRANSFORM).write_text(xform, encoding="utf-8")
    return "applied"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    print("Aurora M14 pixel-exact EFB copy source + GX viewport origin " + transform(args.source, args.check_only))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
