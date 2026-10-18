#!/usr/bin/env python3
"""Opt-in EFB texture-copy trace for missing capture effects (flip card, doors).

SPM's 2D/3D flip and door-swing effects capture part of the EFB with GXCopyTex
and draw it back as a texture. Aurora keeps each copy as a host texture keyed
by the exact guest destination pointer; texture loads find it with an exact
address lookup, otherwise they read (never-written) guest RAM. The pipe effect
was fixed by logical PE bboxes; flip/doors were not, so the remaining failure is
in copy <-> texture correspondence or copy content. This trace records:

  copy : dest, logical source rect, logical/scaled dst size, copy format, EFB
         pixel format, alpha update / dst alpha, clear
  bind : a sampled texture that exactly hits a copy (with its load format and
         size vs the copy), or that lies *inside* a copy at a byte offset
         (exact-address miss that reads guest RAM instead)

Off unless OPENSPM_M14_COPY_TRACE=1. Each unique event is logged once, capped
at 4096 lines. No rendering behavior changes. Exact/idempotent; fails closed.
"""
from __future__ import annotations

import argparse
from pathlib import Path

SIGNATURE = "OpenSPM M14 copy trace"

FRAMEBUFFER = Path("lib/dolphin/gx/GXFrameBuffer.cpp")
TEXTURE = Path("lib/gx/texture.cpp")

FB_DECL_OLD = "namespace aurora::gx {\nvoid copy_tex(const void* dest, GXBool clear) noexcept {\n"
FB_DECL_NEW = ("namespace aurora::gx {\n"
               "// " + SIGNATURE + " (defined in lib/gx/texture.cpp).\n"
               "void m14_copytrace_note_copy(const void* dest, u32 srcX, u32 srcY, u32 srcW, u32 srcH,\n"
               "                             u32 dstW, u32 dstH, u32 scaledW, u32 scaledH, u32 fmt,\n"
               "                             u32 pixelFmt, bool alphaUpdate, u32 dstAlpha, bool clear) noexcept;\n"
               "void copy_tex(const void* dest, GXBool clear) noexcept {\n")

# Keep the pinned three-line publication block contiguous: the configure-time
# copy-ownership rollback (run earlier in the chain on every configure) matches
# it exactly. The trace call goes after the complete block.
FB_PUBLISH = ("  ++handle.revision;\n  g_gxState.copyTextures[dest] = handle;\n"
              "  texture::invalidate_bindings();\n")
FB_CALL = ("  m14_copytrace_note_copy(dest, static_cast<u32>(g_gxState.texCopySrc.x),\n"
               "                          static_cast<u32>(g_gxState.texCopySrc.y),\n"
               "                          static_cast<u32>(g_gxState.texCopySrc.width),\n"
               "                          static_cast<u32>(g_gxState.texCopySrc.height),\n"
               "                          g_gxState.texCopyDstWidth, g_gxState.texCopyDstHeight, dstWidth, dstHeight,\n"
               "                          static_cast<u32>(texCopyFmt), static_cast<u32>(g_gxState.pixelFmt),\n"
               "                          g_gxState.alphaUpdate, g_gxState.dstAlpha, clear != GX_FALSE);\n")
FB_CALL_OLD = FB_PUBLISH
FB_CALL_NEW = FB_PUBLISH + FB_CALL
# v1/v2 placed the call between the publication and invalidate_bindings().
FB_CALL_V1 = ("  ++handle.revision;\n  g_gxState.copyTextures[dest] = handle;\n" + FB_CALL +
              "  texture::invalidate_bindings();\n")

TEX_ANCHOR = "void resolve_sampled_textures(const ShaderInfo& info) noexcept {\n"
TEX_HELPERS = r'''// OpenSPM M14 copy trace: opt-in correspondence log for EFB capture effects.
namespace {
struct M14CopyTraceEntry {
  u32 width = 0, height = 0, format = 0;
};
bool m14_copytrace_enabled() noexcept {
  static const bool enabled = [] {
    const char* value = std::getenv("OPENSPM_M14_COPY_TRACE");
    return value != nullptr && value[0] == '1';
  }();
  return enabled;
}
absl::flat_hash_map<const void*, M14CopyTraceEntry>& m14_copytrace_copies() noexcept {
  static absl::flat_hash_map<const void*, M14CopyTraceEntry> copies;
  return copies;
}
bool m14_copytrace_once(uint64_t key) noexcept {
  static absl::flat_hash_set<uint64_t> seen;
  if (seen.size() >= 4096u) return false;
  return seen.insert(key).second;
}
uint64_t m14_copytrace_key(uint64_t kind, const void* p, uint64_t a, uint64_t b) noexcept {
  uint64_t h = kind * 0x9E3779B97F4A7C15ull;
  h ^= reinterpret_cast<uintptr_t>(p) + 0x9E3779B97F4A7C15ull + (h << 6) + (h >> 2);
  h ^= a + 0x9E3779B97F4A7C15ull + (h << 6) + (h >> 2);
  h ^= b + 0x9E3779B97F4A7C15ull + (h << 6) + (h >> 2);
  return h;
}
void m14_copytrace_bind(const GXTexObj_& obj, bool exact) noexcept {
  if (!m14_copytrace_enabled() || obj.data == nullptr) return;
  auto& copies = m14_copytrace_copies();
  if (exact) {
    const auto it = copies.find(obj.data);
    const M14CopyTraceEntry copy = it != copies.end() ? it->second : M14CopyTraceEntry{};
    const uint64_t blend = (uint64_t(g_gxState.blendMode) << 24) | (uint64_t(g_gxState.blendFacSrc) << 16) |
                           (uint64_t(g_gxState.blendFacDst) << 8) | uint64_t(g_gxState.blendOp);
    if (!m14_copytrace_once(m14_copytrace_key(2, obj.data, (uint64_t(obj.format()) << 32) | blend,
                                              (uint64_t(obj.width()) << 16) | obj.height())))
      return;
    Log.info("M14 copy trace bind=hit data={} load_fmt={} load={}x{} copy_fmt={} copy={}x{} tlut={} "
             "blend_mode={} blend_src={} blend_dst={} logic_op={}",
             obj.data, obj.format(), obj.width(), obj.height(), copy.format, copy.width, copy.height,
             static_cast<u32>(obj.tlut), static_cast<u32>(g_gxState.blendMode),
             static_cast<u32>(g_gxState.blendFacSrc), static_cast<u32>(g_gxState.blendFacDst),
             static_cast<u32>(g_gxState.blendOp));
    return;
  }
  const auto* data = static_cast<const uint8_t*>(obj.data);
  for (const auto& [dest, copy] : copies) {
    const auto* base = static_cast<const uint8_t*>(dest);
    const uint64_t bytes = uint64_t(copy.width) * copy.height * 4u; // generous upper bound
    if (data > base && data < base + bytes) {
      if (!m14_copytrace_once(m14_copytrace_key(3, obj.data, obj.format(), uintptr_t(dest)))) return;
      Log.warn("M14 copy trace bind=offset-miss data={} copy_dest={} offset={} load_fmt={} load={}x{} "
               "copy_fmt={} copy={}x{} blend_mode={} logic_op={}: exact-address lookup misses, texture reads guest RAM",
               obj.data, dest, static_cast<uint64_t>(data - base), obj.format(), obj.width(), obj.height(),
               copy.format, copy.width, copy.height, static_cast<u32>(g_gxState.blendMode),
               static_cast<u32>(g_gxState.blendOp));
      return;
    }
  }
}
} // namespace

void m14_copytrace_note_copy(const void* dest, u32 srcX, u32 srcY, u32 srcW, u32 srcH, u32 dstW, u32 dstH,
                             u32 scaledW, u32 scaledH, u32 fmt, u32 pixelFmt, bool alphaUpdate, u32 dstAlpha,
                             bool clear) noexcept {
  if (!m14_copytrace_enabled()) return;
  m14_copytrace_copies()[dest] = M14CopyTraceEntry{dstW, dstH, fmt};
  if (!m14_copytrace_once(m14_copytrace_key(1, dest, (uint64_t(dstW) << 32) | dstH,
                                            (uint64_t(fmt) << 32) | (uint64_t(srcX) << 16) | srcY)))
    return;
  static bool reported = false;
  if (!reported) {
    reported = true;
    Log.info("M14 copy trace active (OPENSPM_M14_COPY_TRACE=1): logging unique EFB texture copies and copy binds");
  }
  Log.info("M14 copy trace copy dest={} src={},{} {}x{} dst={}x{} scaled={}x{} fmt={} pixel_fmt={} "
           "alpha_update={} dst_alpha={} clear={}",
           dest, srcX, srcY, srcW, srcH, dstW, dstH, scaledW, scaledH, fmt, pixelFmt, alphaUpdate,
           dstAlpha == UINT32_MAX ? -1 : static_cast<int64_t>(dstAlpha), clear);
}

'''

# The copy-ownership rollback (earlier in the chain, every configure) pins the
# block starting at "gfx::TextureHandle handle;", so the bind hook goes before
# it and performs its own lookup.
TEX_BIND_OLD = ("    gfx::TextureHandle handle;\n"
                "    const auto copyIt = g_gxState.copyTextures.find(obj.data);\n")
TEX_BIND_CALL = ("    m14_copytrace_bind(obj, g_gxState.copyTextures.find(obj.data) != "
                 "g_gxState.copyTextures.end());\n")
TEX_BIND_NEW = TEX_BIND_CALL + TEX_BIND_OLD
TEX_BIND_V1 = "    m14_copytrace_bind(obj, copyRef != nullptr);\n"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if text.count(old) != 1:
        raise RuntimeError(f"{label}: expected one pinned fragment, found {text.count(old)}")
    return text.replace(old, new, 1)


def transform(root: Path, check_only: bool = False) -> str:
    fb = (root / FRAMEBUFFER).read_text(encoding="utf-8")
    tex = (root / TEXTURE).read_text(encoding="utf-8")
    helper_start = "// OpenSPM M14 copy trace: opt-in correspondence log for EFB capture effects.\n"
    if SIGNATURE in fb and SIGNATURE in tex:
        moved = False
        if FB_CALL_V1 in fb:
            if check_only:
                return "pending migration"
            fb = fb.replace(FB_CALL_V1, FB_CALL_NEW, 1)
            (root / FRAMEBUFFER).write_text(fb, encoding="utf-8")
            moved = True
        if FB_CALL_NEW not in fb:
            raise RuntimeError("copy trace: publication call not found in expected position")
        if TEX_BIND_NEW not in tex:
            if check_only:
                return "pending migration"
            tex = tex.replace(TEX_BIND_V1, "", 1)
            tex = replace_once(tex, TEX_BIND_OLD, TEX_BIND_NEW, "texture.cpp copy lookup (migration)")
            (root / TEXTURE).write_text(tex, encoding="utf-8")
            moved = True
        if TEX_HELPERS in tex:
            return "migrated" if moved else "already active"
        # The Aurora cache is patched in place, never reset: migrate the first
        # trace revision (no blend/logic-op fields) to the current helpers.
        if check_only:
            return "pending migration"
        start = tex.find(helper_start)
        end = tex.find(TEX_ANCHOR, start)
        if start < 0 or end < 0 or tex.count(helper_start) != 1:
            raise RuntimeError("copy trace migration: helper block not found exactly once")
        tex = tex[:start] + TEX_HELPERS + tex[end:]
        (root / TEXTURE).write_text(tex, encoding="utf-8")
        return "migrated"
    if check_only:
        return "pending"
    fb = replace_once(fb, FB_DECL_OLD, FB_DECL_NEW, "GXFrameBuffer.cpp copy_tex declaration")
    fb = replace_once(fb, FB_CALL_OLD, FB_CALL_NEW, "GXFrameBuffer.cpp copy_tex publication")
    tex = replace_once(tex, TEX_ANCHOR, TEX_HELPERS + TEX_ANCHOR, "texture.cpp resolve_sampled_textures")
    tex = replace_once(tex, TEX_BIND_OLD, TEX_BIND_NEW, "texture.cpp copy lookup")
    if "#include <cstdlib>" not in tex:
        tex = replace_once(tex, "#include <cstring>\n", "#include <cstdlib>\n#include <cstring>\n",
                           "texture.cpp includes")
    (root / FRAMEBUFFER).write_text(fb, encoding="utf-8")
    (root / TEXTURE).write_text(tex, encoding="utf-8")
    return "applied"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    print("Aurora M14 EFB copy trace " + transform(args.source, args.check_only))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
