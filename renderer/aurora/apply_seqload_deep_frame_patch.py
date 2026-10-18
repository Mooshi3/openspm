#!/usr/bin/env python3
"""Upgrade the existing SEQ_LOAD audit to one DrawDone-bounded deep frame capture.

Diagnostic only: no GX behavior, guest memory, frame ordering, or presentation is changed.
"""

from __future__ import annotations
import argparse, json, pathlib, subprocess, sys

HERE = pathlib.Path(__file__).resolve().parent
LOCK = HERE / "aurora.lock.json"
SIGNATURE = "M14 SEQ_LOAD deep-frame audit active"
OLD_SIGNATURE = "M14 SEQ_LOAD GX audit armed"
FRAME_DRAW_CAP = 512

def run(*args: str, cwd: pathlib.Path):
    return subprocess.run(args, cwd=cwd, check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)

def once(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise RuntimeError(f"{label}: expected exactly one source fragment, found {n}")
    return text.replace(old, new, 1)

HELPERS = r'''
bool sM14AuditFrameMode = false;
std::uint64_t sM14AuditVertexBytes = 0;
std::uint32_t sM14AuditDisplayCopies = 0;
std::uint32_t sM14AuditTextureCopies = 0;
std::uint64_t sM14AuditLastVtxStateHash = 0;

std::uint64_t m14_audit_hash(const void* data, std::size_t size) noexcept;

std::uint16_t m14_deep_be16(const std::uint8_t* p) noexcept {
  return static_cast<std::uint16_t>((static_cast<std::uint16_t>(p[0]) << 8u) | p[1]);
}
float m14_deep_bef32(const std::uint8_t* p) noexcept {
  const std::uint32_t bits = (static_cast<std::uint32_t>(p[0]) << 24u) |
                             (static_cast<std::uint32_t>(p[1]) << 16u) |
                             (static_cast<std::uint32_t>(p[2]) << 8u) |
                             static_cast<std::uint32_t>(p[3]);
  float out = 0.0f;
  std::memcpy(&out, &bits, sizeof(out));
  return out;
}
std::size_t m14_deep_fifo_attr_size(GXAttr attr, const VtxAttrFmt& af) noexcept {
  const auto desc = g_gxState.vtxDesc[static_cast<std::size_t>(attr)];
  if (desc == GX_NONE) return 0;
  if (desc == GX_INDEX8) return attr == GX_VA_NRM && af.cnt == GX_NRM_NBT3 ? 3u : 1u;
  if (desc == GX_INDEX16) return attr == GX_VA_NRM && af.cnt == GX_NRM_NBT3 ? 6u : 2u;
  return static_cast<std::size_t>(comp_type_size(attr, af.type)) *
         static_cast<std::size_t>(comp_cnt_count(attr, af.cnt));
}
void m14_deep_vertex_stream(GXPrimitive prim, GXVtxFmt fmt, u16 vtxCount,
                            const std::uint8_t* data, std::size_t size) noexcept {
  if (sM14AuditRemaining == 0 || data == nullptr || vtxCount == 0u) return;
  const auto& state = g_gxState;
  const auto& vf = state.vtxFmts[fmt];
  const std::size_t stride = size / vtxCount;
  if (stride == 0u || stride * vtxCount != size) return;
  sM14AuditVertexBytes += size;

  const std::uint64_t descHash =
      m14_audit_hash(state.vtxDesc.data(), sizeof(state.vtxDesc)) ^
      (m14_audit_hash(&vf, sizeof(vf)) << 1u) ^ static_cast<std::uint64_t>(fmt);
  if (descHash != sM14AuditLastVtxStateHash) {
    const auto& p = vf.attrs[GX_VA_POS];
    const auto& c = vf.attrs[GX_VA_CLR0];
    const auto& t = vf.attrs[GX_VA_TEX0];
    Log.info("M14 GX deep VCDVAT fmt={} stride={} pos={}/{}/{}/{} clr0={}/{}/{}/{} tex0={}/{}/{}/{} nrm={} clr1={} tex1={} tex2={} hash=0x{:016X}",
             static_cast<u32>(fmt), stride,
             static_cast<u32>(state.vtxDesc[GX_VA_POS]), static_cast<u32>(p.cnt), static_cast<u32>(p.type), p.frac,
             static_cast<u32>(state.vtxDesc[GX_VA_CLR0]), static_cast<u32>(c.cnt), static_cast<u32>(c.type), c.frac,
             static_cast<u32>(state.vtxDesc[GX_VA_TEX0]), static_cast<u32>(t.cnt), static_cast<u32>(t.type), t.frac,
             static_cast<u32>(state.vtxDesc[GX_VA_NRM]), static_cast<u32>(state.vtxDesc[GX_VA_CLR1]),
             static_cast<u32>(state.vtxDesc[GX_VA_TEX1]), static_cast<u32>(state.vtxDesc[GX_VA_TEX2]), descHash);
    sM14AuditLastVtxStateHash = descHash;
  }

  std::size_t posOff = static_cast<std::size_t>(-1);
  std::size_t clrOff = static_cast<std::size_t>(-1);
  std::size_t texOff = static_cast<std::size_t>(-1);
  std::size_t off = 0u;
  for (int i = GX_VA_PNMTXIDX; i <= GX_VA_TEX7; ++i) {
    const auto attr = static_cast<GXAttr>(i);
    if (attr == GX_VA_POS) posOff = off;
    if (attr == GX_VA_CLR0) clrOff = off;
    if (attr == GX_VA_TEX0) texOff = off;
    off += m14_deep_fifo_attr_size(attr, vf.attrs[i]);
  }
  if (off != stride) {
    Log.warn("M14 GX deep stride mismatch draw={} computed={} actual={}",
             sM14AuditDrawOrdinal + 1u, off, stride);
    return;
  }

  const std::uint32_t shown = std::min<std::uint32_t>(vtxCount, 4u);
  for (std::uint32_t v = 0; v < shown; ++v) {
    const auto* rec = data + static_cast<std::size_t>(v) * stride;
    bool posDirectF32 = posOff != static_cast<std::size_t>(-1) &&
                        state.vtxDesc[GX_VA_POS] == GX_DIRECT &&
                        vf.attrs[GX_VA_POS].type == GX_F32;
    bool texDirectF32 = texOff != static_cast<std::size_t>(-1) &&
                        state.vtxDesc[GX_VA_TEX0] == GX_DIRECT &&
                        vf.attrs[GX_VA_TEX0].type == GX_F32;
    float px = 0.f, py = 0.f, pz = 0.f, ts = 0.f, tt = 0.f;
    if (posDirectF32) {
      px = m14_deep_bef32(rec + posOff);
      py = m14_deep_bef32(rec + posOff + 4u);
      if (comp_cnt_count(GX_VA_POS, vf.attrs[GX_VA_POS].cnt) > 2u)
        pz = m14_deep_bef32(rec + posOff + 8u);
    }
    if (texDirectF32) {
      ts = m14_deep_bef32(rec + texOff);
      if (comp_cnt_count(GX_VA_TEX0, vf.attrs[GX_VA_TEX0].cnt) > 1u)
        tt = m14_deep_bef32(rec + texOff + 4u);
    }

    std::uint32_t posIndex = UINT32_MAX, texIndex = UINT32_MAX;
    if (posOff != static_cast<std::size_t>(-1) && state.vtxDesc[GX_VA_POS] == GX_INDEX8)
      posIndex = rec[posOff];
    else if (posOff != static_cast<std::size_t>(-1) && state.vtxDesc[GX_VA_POS] == GX_INDEX16)
      posIndex = m14_deep_be16(rec + posOff);
    if (texOff != static_cast<std::size_t>(-1) && state.vtxDesc[GX_VA_TEX0] == GX_INDEX8)
      texIndex = rec[texOff];
    else if (texOff != static_cast<std::size_t>(-1) && state.vtxDesc[GX_VA_TEX0] == GX_INDEX16)
      texIndex = m14_deep_be16(rec + texOff);

    std::uint32_t rgba = 0u;
    bool rgba8 = clrOff != static_cast<std::size_t>(-1) &&
                 state.vtxDesc[GX_VA_CLR0] == GX_DIRECT &&
                 vf.attrs[GX_VA_CLR0].type == GX_RGBA8;
    if (rgba8) {
      rgba = (static_cast<std::uint32_t>(rec[clrOff]) << 24u) |
             (static_cast<std::uint32_t>(rec[clrOff + 1u]) << 16u) |
             (static_cast<std::uint32_t>(rec[clrOff + 2u]) << 8u) |
             static_cast<std::uint32_t>(rec[clrOff + 3u]);
    }
    Log.info("M14 GX deep vertex draw={} v={} prim={} pos_f32={} pos={:.4f},{:.4f},{:.4f} pos_idx={} tex_f32={} tex={:.4f},{:.4f} tex_idx={} clr_rgba8={} rgba=0x{:08X} raw_hash=0x{:016X}",
             sM14AuditDrawOrdinal + 1u, v, static_cast<u32>(prim),
             posDirectF32 ? 1u : 0u, px, py, pz, posIndex,
             texDirectF32 ? 1u : 0u, ts, tt, texIndex,
             rgba8 ? 1u : 0u, rgba, m14_audit_hash(rec, stride));
  }
}
'''

def transform(source: pathlib.Path, check_only: bool) -> str:
    cp_path = source / "lib/gx/command_processor.cpp"
    hdr_path = source / "include/aurora/gx_audit.hpp"
    regs_path = source / "lib/gx/regs.cpp"
    cp = cp_path.read_text(encoding="utf-8")
    hdr = hdr_path.read_text(encoding="utf-8")
    regs = regs_path.read_text(encoding="utf-8")

    if SIGNATURE in cp:
        if "finish_seqload_frame_capture" not in hdr or "note_efb_copy" not in hdr:
            raise RuntimeError("deep audit signature present but hooks are incomplete")
        return "already-applied"
    if OLD_SIGNATURE not in cp:
        raise RuntimeError("base bounded SEQ_LOAD audit is absent")

    hdr = once(hdr,
        "void arm_seqload_capture(std::uint32_t drawLimit) noexcept;\n",
        "void arm_seqload_capture(std::uint32_t drawLimit) noexcept;\n"
        "void finish_seqload_frame_capture() noexcept;\n"
        "void note_efb_copy(std::uint32_t value, bool display, bool clear) noexcept;\n",
        "gx_audit.hpp")

    anchor = ("std::uint32_t sM14AuditRemaining = 0;\n"
              "std::uint32_t sM14AuditGeneration = 0;\n"
              "std::uint64_t sM14AuditDrawOrdinal = 0;\n\n")
    cp = once(cp, anchor, anchor + HELPERS + "\n", "deep helpers")

    vertex = ("  const auto vertexData = reader.take(totalVtxBytes);\n"
              "  gfx::Range vertRange = gfx::push_verts(vertexData.data(), vertexData.size(), canMerge ? 0 : 4);\n")
    cp = once(cp, vertex,
              "  const auto vertexData = reader.take(totalVtxBytes);\n"
              "  m14_deep_vertex_stream(prim, fmt, vtxCount, vertexData.data(), vertexData.size());\n"
              "  gfx::Range vertRange = gfx::push_verts(vertexData.data(), vertexData.size(), canMerge ? 0 : 4);\n",
              "vertex hook")

    cp = once(cp,
'''      "M14 GX audit draw gen={} n={} remaining={} prim={} fmt={} vtx={} vert_off={} vert_bytes={} merged={} sampled_tex=0x{:02X} sampled_tc=0x{:02X} tev_load=0x{:X} tev_write=0x{:X} tev_stages={} texgens={} blend={} src={} dst={} alpha_cmp={}/{}/{}/{}/{} dst_alpha=0x{:08X}",
      sM14AuditGeneration, ordinal, sM14AuditRemaining,
      static_cast<u32>(prim), static_cast<u32>(fmt), vtxCount,
      vertRange.offset, vertRange.size, merged ? 1u : 0u,
      static_cast<u32>(shaderInfo.sampledTextures.to_ulong()),
      static_cast<u32>(shaderInfo.sampledTexCoords.to_ulong()),
      static_cast<u32>(shaderInfo.loadsTevReg.to_ulong()),
      static_cast<u32>(shaderInfo.writesTevReg.to_ulong()),
      state.numTevStages, state.numTexGens,
      static_cast<u32>(state.blendMode), static_cast<u32>(state.blendFacSrc),
      static_cast<u32>(state.blendFacDst),
      static_cast<u32>(state.alphaCompare.comp0), state.alphaCompare.ref0,
      static_cast<u32>(state.alphaCompare.op), static_cast<u32>(state.alphaCompare.comp1),
      state.alphaCompare.ref1, state.dstAlpha);
''',
'''      "M14 GX audit draw gen={} n={} remaining={} prim={} fmt={} vtx={} vert_off={} vert_bytes={} merged={} sampled_tex=0x{:02X} sampled_tc=0x{:02X} tev_load=0x{:X} tev_write=0x{:X} tev_stages={} texgens={} blend={} src={} dst={} logic={} depth={}/{}/{} writes={}/{} cull={} alpha_cmp={}/{}/{}/{}/{} dst_alpha=0x{:08X}",
      sM14AuditGeneration, ordinal, sM14AuditRemaining,
      static_cast<u32>(prim), static_cast<u32>(fmt), vtxCount,
      vertRange.offset, vertRange.size, merged ? 1u : 0u,
      static_cast<u32>(shaderInfo.sampledTextures.to_ulong()),
      static_cast<u32>(shaderInfo.sampledTexCoords.to_ulong()),
      static_cast<u32>(shaderInfo.loadsTevReg.to_ulong()),
      static_cast<u32>(shaderInfo.writesTevReg.to_ulong()),
      state.numTevStages, state.numTexGens,
      static_cast<u32>(state.blendMode), static_cast<u32>(state.blendFacSrc),
      static_cast<u32>(state.blendFacDst), static_cast<u32>(state.blendOp),
      state.depthCompare ? 1u : 0u, static_cast<u32>(state.depthFunc), state.depthUpdate ? 1u : 0u,
      state.colorUpdate ? 1u : 0u, state.alphaUpdate ? 1u : 0u, static_cast<u32>(state.cullMode),
      static_cast<u32>(state.alphaCompare.comp0), state.alphaCompare.ref0,
      static_cast<u32>(state.alphaCompare.op), static_cast<u32>(state.alphaCompare.comp1),
      state.alphaCompare.ref1, state.dstAlpha);
''', "draw state")

    cp = once(cp,
'''        "M14 GX audit tev draw={} stage={} map={} tc={} su={}x{} tcg={}/{}/{}/{} norm={} color_out={} alpha_out={} alpha_abcd={}/{}/{}/{} ind_stage={} ind_mtx={}",
        ordinal, stageIndex, static_cast<u32>(stage.texMapId), tc,
        scaleS, scaleT, tcgType, tcgSrc, tcgMtx, tcgPost, tcgNorm,
        static_cast<u32>(stage.colorOp.outReg), static_cast<u32>(stage.alphaOp.outReg),
        static_cast<u32>(stage.alphaPass.a), static_cast<u32>(stage.alphaPass.b),
        static_cast<u32>(stage.alphaPass.c), static_cast<u32>(stage.alphaPass.d),
        static_cast<u32>(stage.indTexStage), static_cast<u32>(stage.indTexMtxId));
''',
'''        "M14 GX audit tev draw={} stage={} map={} tc={} su={}x{} tcg={}/{}/{}/{} norm={} color_abcd={}/{}/{}/{} color_op={}/{}/{}/{}/{} alpha_abcd={}/{}/{}/{} alpha_op={}/{}/{}/{}/{} ind_stage={} ind_mtx={}",
        ordinal, stageIndex, static_cast<u32>(stage.texMapId), tc,
        scaleS, scaleT, tcgType, tcgSrc, tcgMtx, tcgPost, tcgNorm,
        static_cast<u32>(stage.colorPass.a), static_cast<u32>(stage.colorPass.b),
        static_cast<u32>(stage.colorPass.c), static_cast<u32>(stage.colorPass.d),
        static_cast<u32>(stage.colorOp.op), static_cast<u32>(stage.colorOp.bias),
        static_cast<u32>(stage.colorOp.scale), stage.colorOp.clamp ? 1u : 0u,
        static_cast<u32>(stage.colorOp.outReg),
        static_cast<u32>(stage.alphaPass.a), static_cast<u32>(stage.alphaPass.b),
        static_cast<u32>(stage.alphaPass.c), static_cast<u32>(stage.alphaPass.d),
        static_cast<u32>(stage.alphaOp.op), static_cast<u32>(stage.alphaOp.bias),
        static_cast<u32>(stage.alphaOp.scale), stage.alphaOp.clamp ? 1u : 0u,
        static_cast<u32>(stage.alphaOp.outReg),
        static_cast<u32>(stage.indTexStage), static_cast<u32>(stage.indTexMtxId));
''', "TEV equations")

    cp = once(cp,
'''  --sM14AuditRemaining;
  if (sM14AuditRemaining == 0) {
    Log.info("M14 SEQ_LOAD GX audit complete gen={} draws={}",
             sM14AuditGeneration, sM14AuditDrawOrdinal);
  }
''',
'''  --sM14AuditRemaining;
  if (sM14AuditRemaining == 0) {
    if (sM14AuditFrameMode) {
      Log.warn("M14 SEQ_LOAD deep-frame audit draw cap reached gen={} draws={} cap={}; truncated",
               sM14AuditGeneration, sM14AuditDrawOrdinal, 512u);
      sM14AuditFrameMode = false;
    } else {
      Log.info("M14 SEQ_LOAD GX audit complete gen={} draws={}",
               sM14AuditGeneration, sM14AuditDrawOrdinal);
    }
  }
''', "frame stop")

    cp = once(cp,
'''namespace audit {
void arm_seqload_capture(std::uint32_t drawLimit) noexcept {
  ++sM14AuditGeneration;
  sM14AuditRemaining = drawLimit;
  sM14AuditDrawOrdinal = 0;
  Log.info("M14 SEQ_LOAD GX audit armed gen={} draw_limit={}", sM14AuditGeneration, drawLimit);
}
} // namespace audit
''',
'''namespace audit {
void arm_seqload_capture(std::uint32_t drawLimit) noexcept {
  ++sM14AuditGeneration;
  sM14AuditFrameMode = drawLimit == 0u;
  sM14AuditRemaining = sM14AuditFrameMode ? 512u : drawLimit;
  sM14AuditDrawOrdinal = 0;
  sM14AuditVertexBytes = 0;
  sM14AuditDisplayCopies = 0;
  sM14AuditTextureCopies = 0;
  sM14AuditLastVtxStateHash = 0;
  Log.info("M14 SEQ_LOAD deep-frame audit active gen={} mode={} cap={}",
           sM14AuditGeneration, sM14AuditFrameMode ? "drawdone" : "count", sM14AuditRemaining);
}
void finish_seqload_frame_capture() noexcept {
  if (!sM14AuditFrameMode) return;
  Log.info("M14 SEQ_LOAD deep-frame audit complete gen={} boundary=GXDrawDone draws={} vertex_bytes={} display_copies={} texture_copies={} remaining_cap={}",
           sM14AuditGeneration, sM14AuditDrawOrdinal, sM14AuditVertexBytes,
           sM14AuditDisplayCopies, sM14AuditTextureCopies, sM14AuditRemaining);
  sM14AuditFrameMode = false;
  sM14AuditRemaining = 0;
}
void note_efb_copy(std::uint32_t value, bool display, bool clear) noexcept {
  if (sM14AuditRemaining == 0) return;
  if (display) ++sM14AuditDisplayCopies; else ++sM14AuditTextureCopies;
  const auto& state = g_gxState;
  Log.info("M14 GX deep EFB copy value=0x{:06X} kind={} clear={} src={},{},{}x{} dst=0x{:X} dst_size={}x{} fmt={} pixel_fmt={}",
           value & 0x00FFFFFFu, display ? "display" : "texture", clear ? 1u : 0u,
           state.texCopySrc.x, state.texCopySrc.y, state.texCopySrc.width, state.texCopySrc.height,
           static_cast<std::uint64_t>(reinterpret_cast<std::uintptr_t>(state.texCopyDest)),
           state.texCopyDstWidth, state.texCopyDstHeight, static_cast<u32>(state.texCopyFmt),
           static_cast<u32>(state.pixelFmt));
}
} // namespace audit
''', "arm/hooks")

    regs = once(regs,
        '#include <aurora/gx_guest_memory.hpp>\n#include <aurora/gx_raw.hpp>\n',
        '#include <aurora/gx_guest_memory.hpp>\n#include <aurora/gx_raw.hpp>\n#include <aurora/gx_audit.hpp>\n',
        "regs include")
    copy = ("void bp_efb_copy(u8, u32 value) noexcept {\n"
            "  const bool clear = reg_get(value, 1, 11) != 0;\n")
    regs = once(regs, copy,
                copy + "  fifo::audit::note_efb_copy(value, reg_get(value, 1, 14) != 0, clear);\n",
                "EFB copy hook")

    if not check_only:
        cp_path.write_text(cp, encoding="utf-8", newline="\n")
        hdr_path.write_text(hdr, encoding="utf-8", newline="\n")
        regs_path.write_text(regs, encoding="utf-8", newline="\n")
    return "verified" if check_only else "applied"

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("source", type=pathlib.Path)
    p.add_argument("--check-only", action="store_true")
    a = p.parse_args()
    source = a.source.resolve()
    expected = json.loads(LOCK.read_text(encoding="utf-8"))["commit"]
    try:
        actual = run("git", "rev-parse", "HEAD", cwd=source).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"error: not a usable git checkout: {source}: {exc}", file=sys.stderr); return 2
    if actual != expected:
        print(f"error: Aurora revision mismatch: expected {expected}, got {actual}", file=sys.stderr); return 3
    try:
        result = transform(source, a.check_only)
    except (OSError, RuntimeError) as exc:
        print(f"error: Aurora SEQ_LOAD deep-frame capture failed: {exc}", file=sys.stderr); return 4
    print({"already-applied":"Aurora SEQ_LOAD deep-frame capture already applied",
           "verified":"Aurora SEQ_LOAD deep-frame capture verified",
           "applied":"Aurora SEQ_LOAD deep-frame capture applied"}[result])
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
