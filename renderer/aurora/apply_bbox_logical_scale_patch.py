#!/usr/bin/env python3
"""Report GX PE bounding boxes in the game's logical coordinates.

Aurora renders the EFB at window resolution (e.g. 1440x1080 for a 4:3 fit on a
2560x1080 panel) while SPM's GX state lives in the 640x480 logical space. The
bbox fragment code records raw EFB pixel positions clamped to 1023, so any
window larger than 640x480 returned scaled - and past 1023 px saturated -
boxes. SPM uses PE bbox to find the screen rectangle to capture with an EFB
texture copy (flip card, door swing, pipe chunk effects), so wrong boxes copy
the wrong region.

Keep GPU coverage in EFB pixels and convert at the game boundary, on the GX
parsing thread (the same thread and scale map_logical_viewport uses):
  write logical->pixel: min fields ceil(v*s), max fields ceil((v+1)*s)-1
  read  pixel->logical: floor(px/s), clamped to 1023
At scale 1 (a 640x480 EFB) every value is identical to before. Lift the pixel
clamp/mask to 16383 so large windows do not saturate. OPENSPM_M14_BBOX_LOGICAL=0
restores raw pixel reporting for A/B. BP (FIFO) bbox writes are converted the
same way as CPU register writes.

Exact/idempotent; fails closed on unexpected source drift.
"""
from __future__ import annotations

import argparse
from pathlib import Path

SIGNATURE = "OpenSPM M14 bbox logical-coordinate reporting"

RAW = Path("lib/gx/raw.cpp")
BBOX = Path("lib/gx/bbox.cpp")
SHADER = Path("lib/gx/shader.cpp")
REGS = Path("lib/gx/regs.cpp")

RAW_INCLUDE_OLD = '#include "bbox.hpp"\n#include "fifo.hpp"\n'
RAW_INCLUDE_NEW = ('#include "bbox.hpp"\n#include "fifo.hpp"\n#include "gx.hpp"\n'
                   '#include "../gfx/recording.hpp"\n\n#include <algorithm>\n#include <cmath>\n#include <cstdlib>\n')

RAW_OLD = '''void write_bbox(std::uint32_t index, std::uint16_t value) noexcept {
  bbox::queue_write(index, value);
}

bool read_bbox(std::array<std::uint16_t, 4>& out) noexcept {
  return bbox::read_sync(out);
}
'''

RAW_NEW_V1 = '''// OpenSPM M14 bbox logical-coordinate reporting: GPU coverage is recorded in
// EFB pixels; the game reads/writes PE bbox registers in its logical GX space.
namespace {
bool bbox_logical_enabled() noexcept {
  static const bool enabled = [] {
    const char* value = std::getenv("OPENSPM_M14_BBOX_LOGICAL");
    return value == nullptr || value[0] != '0';
  }();
  return enabled;
}

// index 0/1 = left/right (x), 2/3 = top/bottom (y). Returns EFB pixels per
// logical unit on that axis (1.0 when unknown).
double bbox_axis_scale(std::uint32_t index) noexcept {
  if (!bbox_logical_enabled()) return 1.0;
  const auto [logicalWidth, logicalHeight] = logical_fb_size();
  const auto [targetWidth, targetHeight] = gfx::get_render_target_size();
  const bool x = index < 2u;
  const double l = static_cast<double>(x ? logicalWidth : logicalHeight);
  const double t = static_cast<double>(x ? targetWidth : targetHeight);
  return l > 0.0 && t > 0.0 ? t / l : 1.0;
}
} // namespace

void write_bbox(std::uint32_t index, std::uint16_t value) noexcept {
  const double s = bbox_axis_scale(index);
  if (s == 1.0) {
    bbox::queue_write(index, value);
    return;
  }
  const bool isMax = (index & 1u) != 0u;
  const double px = isMax ? std::ceil((static_cast<double>(value) + 1.0) * s) - 1.0
                          : std::ceil(static_cast<double>(value) * s);
  bbox::queue_write(index, static_cast<std::uint16_t>(std::clamp(px, 0.0, 16383.0)));
}

bool read_bbox(std::array<std::uint16_t, 4>& out) noexcept {
  if (!bbox::read_sync(out)) return false;
  for (std::uint32_t i = 0; i < out.size(); ++i) {
    const double s = bbox_axis_scale(i);
    if (s == 1.0) {
      out[i] = static_cast<std::uint16_t>(std::min<std::uint32_t>(out[i], 1023u));
      continue;
    }
    const double logical = std::floor(static_cast<double>(out[i]) / s);
    out[i] = static_cast<std::uint16_t>(std::clamp(logical, 0.0, 1023.0));
  }
  return true;
}
'''

RAW_NEW = RAW_NEW_V1.replace('void write_bbox(std::uint32_t index, std::uint16_t value) noexcept {\n  const double s = bbox_axis_scale(index);',
                          'void m14_bbox_memo_write(std::uint32_t index, std::uint16_t value) noexcept; // GXFrameBuffer.cpp\n\nvoid write_bbox(std::uint32_t index, std::uint16_t value) noexcept {\n  m14_bbox_memo_write(index, value);\n  const double s = bbox_axis_scale(index);', 1)

# Revision 3: the Wii tracks the bbox at 2x2-pixel granularity (left/top rounded
# down to even, right/bottom up to odd). SPM pads each box by 2 px and clears the
# padded rect with its capture copy (0x80034D8C / 0x80034BB0); a top edge one
# logical pixel low leaves the star door's top row uncleared (the sliver above the
# doorway) and an even right edge changes the 86 vs 88 capture width.
RAW_NEW_V2 = RAW_NEW
RAW_NEW = RAW_NEW_V2.replace('''    const double logical = std::floor(static_cast<double>(out[i]) / s);
    out[i] = static_cast<std::uint16_t>(std::clamp(logical, 0.0, 1023.0));
  }
  return true;
}
''', '''    const double logical = std::floor(static_cast<double>(out[i]) / s);
    out[i] = static_cast<std::uint16_t>(std::clamp(logical, 0.0, 1023.0));
  }
  // Hardware granularity in the game's space: 2x2 logical pixels (empty stays empty).
  if (bbox_logical_enabled() && out[0] <= out[1] && out[2] <= out[3]) {
    out[0] = static_cast<std::uint16_t>(out[0] & ~1u);
    out[2] = static_cast<std::uint16_t>(out[2] & ~1u);
    out[1] = static_cast<std::uint16_t>(std::min<std::uint32_t>(out[1] | 1u, 1023u));
    out[3] = static_cast<std::uint16_t>(std::min<std::uint32_t>(out[3] | 1u, 1023u));
  }
  return true;
}
''', 1)
assert RAW_NEW != RAW_NEW_V2

BBOX_MASK_OLD = "    out[i] = static_cast<std::uint16_t>(mapped[i] & 0x3ffu);\n"
BBOX_MASK_NEW = ("    // " + SIGNATURE + ": raw EFB pixels (<=16383); raw.cpp converts.\n"
                 "    out[i] = static_cast<std::uint16_t>(mapped[i] & 0x3fffu);\n")
BBOX_RESET_OLD = "  const std::array<std::uint32_t, 4> reset{1023u, 0u, 1023u, 0u};\n"
BBOX_RESET_NEW = "  const std::array<std::uint32_t, 4> reset{16383u, 0u, 16383u, 0u};\n"

SHADER_OLD = (
    '      "\\n    let bbox_x = min(u32(max(floor(in.pos.x), 0.0)), 1023u);"\n'
    '      "\\n    let bbox_y = min(u32(max(floor(in.pos.y), 0.0)), 1023u);"\n'
    '      "\\n    atomicMin(&gx_bbox[0], bbox_x & 0xfffffffeu);"\n'
    '      "\\n    atomicMax(&gx_bbox[1], min(bbox_x | 1u, 1023u));"\n'
    '      "\\n    atomicMin(&gx_bbox[2], bbox_y & 0xfffffffeu);"\n'
    '      "\\n    atomicMax(&gx_bbox[3], min(bbox_y | 1u, 1023u));";\n'
)
SHADER_NEW = SHADER_OLD.replace("1023u", "16383u")

# BP (FIFO) bbox writes carry logical values too (e.g. the game's GX-side reset
# to 1023/0); route them through the same logical->pixel conversion. Without it
# a reset stores 1023 *pixels*, which clamps boxes right of host x=1023 and
# reads back as the empty box 454,0,454,0 at 2.25x.
REGS_OLD = ("  bbox::queue_write(base, static_cast<u16>(reg_get(value, 10, 0)));\n"
            "  bbox::queue_write(base + 1u, static_cast<u16>(reg_get(value, 10, 10)));\n")
REGS_NEW = ("  // " + SIGNATURE + ": BP writes are logical, like CPU writes.\n"
            "  raw::write_bbox(base, static_cast<u16>(reg_get(value, 10, 0)));\n"
            "  raw::write_bbox(base + 1u, static_cast<u16>(reg_get(value, 10, 10)));\n")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if text.count(new) == 1 and (old == new or text.count(old) == 0):
        return text
    if text.count(old) != 1:
        raise RuntimeError(f"{label}: expected one pinned fragment, found {text.count(old)}")
    return text.replace(old, new, 1)


def transform(root: Path, check_only: bool = False) -> str:
    raw = (root / RAW).read_text(encoding="utf-8")
    bbox = (root / BBOX).read_text(encoding="utf-8")
    shader = (root / SHADER).read_text(encoding="utf-8")
    regs = (root / REGS).read_text(encoding="utf-8")
    if RAW_NEW in raw and SIGNATURE in bbox and SHADER_NEW in shader and REGS_NEW in regs:
        return "already active"
    if check_only:
        return "pending"
    if RAW_INCLUDE_NEW not in raw:
        raw = replace_once(raw, RAW_INCLUDE_OLD, RAW_INCLUDE_NEW, "raw.cpp includes")
    if RAW_NEW_V2 in raw:
        raw = raw.replace(RAW_NEW_V2, RAW_NEW, 1)  # migrate: hardware 2x2 bbox granularity
    elif RAW_NEW_V1 in raw:
        raw = raw.replace(RAW_NEW_V1, RAW_NEW, 1)  # migrate: memo hook + 2x2 granularity
    elif SIGNATURE not in raw:
        raw = replace_once(raw, RAW_OLD, RAW_NEW, "raw.cpp bbox boundary")
    regs = replace_once(regs, REGS_OLD, REGS_NEW, "regs.cpp BP bbox writes")
    bbox = replace_once(bbox, BBOX_MASK_OLD, BBOX_MASK_NEW, "bbox.cpp readback mask")
    bbox = replace_once(bbox, BBOX_RESET_OLD, BBOX_RESET_NEW, "bbox.cpp reset sentinel")
    shader = replace_once(shader, SHADER_OLD, SHADER_NEW, "shader.cpp bbox clamp")
    (root / RAW).write_text(raw, encoding="utf-8")
    (root / BBOX).write_text(bbox, encoding="utf-8")
    (root / SHADER).write_text(shader, encoding="utf-8")
    (root / REGS).write_text(regs, encoding="utf-8")
    return "applied"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    print("Aurora M14 bbox logical-coordinate reporting " + transform(args.source, args.check_only))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
