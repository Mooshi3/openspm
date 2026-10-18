#!/usr/bin/env python3
"""Add a bounded metadata-only SEQ_LOAD GX capture to pinned Aurora.

The player has proven that filesel.tpl loads correctly while file select renders
as gray segmented quads.  Rather than mutate more GX semantics speculatively,
this diagnostic records the *effective* Aurora state for only the first bounded
batch of draws after the native backend explicitly arms it in stable SEQ_LOAD.

No command-stream bytes or texture/TLUT contents are printed.  Guest resources
are represented by dimensions/formats, canonical host identities and FNV-1a
hashes.  The capture is inert until armed and stops automatically.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
LOCK = HERE / "aurora.lock.json"
SIGNATURE = "M14 SEQ_LOAD GX audit armed"
DOWNSTREAM_SIGNATURE = "M14 SEQ_LOAD deep-frame audit active"

AUDIT_HEADER = r'''#pragma once

#include <cstdint>

namespace aurora::gx::fifo::audit {
// Arms a bounded metadata/hash capture.  This function is diagnostic-only and
// must not drain, submit, present, clear, or otherwise alter GX state.
void arm_seqload_capture(std::uint32_t drawLimit) noexcept;
} // namespace aurora::gx::fifo::audit
'''


def run(*args: str, cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected source fragment exactly once, found {count}")
    return text.replace(old, new, 1)


def transform(source: pathlib.Path, check_only: bool) -> str:
    cp_path = source / "lib/gx/command_processor.cpp"
    header_path = source / "include/aurora/gx_audit.hpp"
    cp = cp_path.read_text(encoding="utf-8")

    # The DrawDone-bounded deep-frame diagnostic is a strict downstream upgrade
    # of this bounded capture and intentionally replaces the original arm-log
    # signature.  Treat either state as already installed so configure-time
    # replay remains idempotent on cached player Aurora checkouts.
    if SIGNATURE in cp or DOWNSTREAM_SIGNATURE in cp:
        if not header_path.exists() or "arm_seqload_capture" not in header_path.read_text(encoding="utf-8"):
            raise RuntimeError("SEQ_LOAD audit signature present but public diagnostic header is missing")
        return "already-applied"

    # This diagnostic is intentionally layered after OpenSPM's raw display-list
    # and guest-array adaptations.  Refuse an unexpected patch order.
    if "guest_array_requirements" not in cp or "sDisplayListDepth" not in cp:
        raise RuntimeError("command_processor.cpp: required raw display-list/guest-array adaptations are absent")

    cp = replace_once(
        cp,
        '#include <aurora/gx_guest_memory.hpp>\n',
        '#include <aurora/gx_guest_memory.hpp>\n#include <aurora/gx_audit.hpp>\n',
        "command_processor.cpp audit include",
    )

    globals_old = (
        "DrawCache sDrawCache;\n"
        "thread_local u32 sDisplayListDepth = 0;\n"
        "constexpr u32 MaxDisplayListDepth = 32;\n"
    )
    globals_new = globals_old + r'''
std::uint32_t sM14AuditRemaining = 0;
std::uint32_t sM14AuditGeneration = 0;
std::uint64_t sM14AuditDrawOrdinal = 0;

std::uint64_t m14_audit_hash(const void* data, std::size_t size) noexcept {
  if (data == nullptr || size == 0) return 0;
  constexpr std::uint64_t Offset = UINT64_C(1469598103934665603);
  constexpr std::uint64_t Prime = UINT64_C(1099511628211);
  auto* bytes = static_cast<const std::uint8_t*>(data);
  std::uint64_t hash = Offset;
  for (std::size_t i = 0; i < size; ++i) {
    hash ^= bytes[i];
    hash *= Prime;
  }
  return hash;
}

void m14_audit_display_list(u32 guestAddress, u32 size, const void* data, u32 depth) noexcept {
  if (sM14AuditRemaining == 0) return;
  const std::uint64_t hash = size <= (8u * 1024u * 1024u) ? m14_audit_hash(data, size) : 0;
  Log.info("M14 GX audit CALL_DL gen={} depth={} addr=0x{:08X} size={} hash=0x{:016X}",
           sM14AuditGeneration, depth, guestAddress, size, hash);
}

void m14_audit_draw(GXPrimitive prim, GXVtxFmt fmt, u16 vtxCount, gfx::Range vertRange,
                    bool merged, const ShaderInfo& shaderInfo) noexcept {
  if (sM14AuditRemaining == 0) return;

  auto& state = g_gxState;
  const std::uint64_t ordinal = ++sM14AuditDrawOrdinal;
  Log.info(
      "M14 GX audit draw gen={} n={} remaining={} prim={} fmt={} vtx={} vert_off={} vert_bytes={} merged={} sampled_tex=0x{:02X} sampled_tc=0x{:02X} tev_load=0x{:X} tev_write=0x{:X} tev_stages={} texgens={} blend={} src={} dst={} alpha_cmp={}/{}/{}/{}/{} dst_alpha=0x{:08X}",
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

  for (u32 tex = 0; tex < MaxTextures; ++tex) {
    if (!shaderInfo.sampledTextures.test(tex)) continue;
    const auto& obj = state.loadedTextures[tex];
    const auto& bind = state.textures[tex];
    const std::size_t sourceBytes = texture::texture_source_size(
        obj.format(), obj.width(), obj.height(), obj.mip_count());
    const std::uint64_t sourceHash = obj.has_data() && sourceBytes <= (8u * 1024u * 1024u)
                                         ? m14_audit_hash(obj.data, sourceBytes)
                                         : 0;
    const auto copyIt = state.copyTextures.find(obj.data);
    const bool isCopy = copyIt != state.copyTextures.end();
    const u32 hostWidth = bind.ref ? bind.ref->size.width : 0u;
    const u32 hostHeight = bind.ref ? bind.ref->size.height : 0u;
    const u32 hostGxFmt = bind.ref ? bind.ref->gxFormat : aurora::gfx::InvalidTextureFormat;

    Log.info(
        "M14 GX audit texture draw={} map={} image0=0x{:08X} image3=0x{:08X} data=0x{:X} w={} h={} fmt={} mips={} wrap={}/{} filter={}/{} src_bytes={} src_hash=0x{:016X} bind={} host={}x{} host_gxfmt={} copy={} copy_rev={} tlut={}",
        ordinal, tex, obj.image0, obj.image3,
        static_cast<std::uint64_t>(reinterpret_cast<std::uintptr_t>(obj.data)),
        obj.width(), obj.height(), obj.format(), obj.mip_count(),
        static_cast<u32>(obj.wrap_s()), static_cast<u32>(obj.wrap_t()),
        static_cast<u32>(obj.min_filter()), static_cast<u32>(obj.mag_filter()),
        sourceBytes, sourceHash, bind.ref ? 1u : 0u,
        hostWidth, hostHeight, hostGxFmt,
        isCopy ? 1u : 0u, isCopy ? copyIt->second.revision : 0u,
        static_cast<u32>(obj.tlut));

    const u32 tlutIndex = static_cast<u32>(obj.tlut);
    if (is_palette_format(obj.format()) && tlutIndex < MaxTluts) {
      const auto& tlut = state.loadedTluts[tlutIndex];
      const std::size_t tlutBytes = texture::tlut_source_size(tlut.numEntries);
      const std::uint64_t tlutHash = tlut.data != nullptr && tlutBytes <= (64u * 1024u)
                                         ? m14_audit_hash(tlut.data, tlutBytes)
                                         : 0;
      Log.info(
          "M14 GX audit tlut draw={} map={} slot={} entries={} fmt={} data=0x{:X} bytes={} hash=0x{:016X}",
          ordinal, tex, tlutIndex, tlut.numEntries, static_cast<u32>(tlut.format),
          static_cast<std::uint64_t>(reinterpret_cast<std::uintptr_t>(tlut.data)),
          tlutBytes, tlutHash);
    }
  }

  const u32 tevCount = std::min<u32>(state.numTevStages, MaxTevStages);
  for (u32 stageIndex = 0; stageIndex < tevCount; ++stageIndex) {
    const auto& stage = state.tevStages[stageIndex];
    u32 scaleS = 0;
    u32 scaleT = 0;
    u32 tcgType = 0xFFFFFFFFu;
    u32 tcgSrc = 0xFFFFFFFFu;
    u32 tcgMtx = 0xFFFFFFFFu;
    u32 tcgPost = 0xFFFFFFFFu;
    u32 tcgNorm = 0;
    const u32 tc = static_cast<u32>(stage.texCoordId);
    if (tc < MaxTexCoord) {
      const auto& scale = state.texCoordScales[tc];
      const auto& tcg = state.tcgs[tc];
      scaleS = static_cast<u32>(scale.scaleS) + 1u;
      scaleT = static_cast<u32>(scale.scaleT) + 1u;
      tcgType = static_cast<u32>(tcg.type);
      tcgSrc = static_cast<u32>(tcg.src);
      tcgMtx = static_cast<u32>(tcg.mtx);
      tcgPost = static_cast<u32>(tcg.postMtx);
      tcgNorm = tcg.normalize ? 1u : 0u;
    }
    Log.info(
        "M14 GX audit tev draw={} stage={} map={} tc={} su={}x{} tcg={}/{}/{}/{} norm={} color_out={} alpha_out={} alpha_abcd={}/{}/{}/{} ind_stage={} ind_mtx={}",
        ordinal, stageIndex, static_cast<u32>(stage.texMapId), tc,
        scaleS, scaleT, tcgType, tcgSrc, tcgMtx, tcgPost, tcgNorm,
        static_cast<u32>(stage.colorOp.outReg), static_cast<u32>(stage.alphaOp.outReg),
        static_cast<u32>(stage.alphaPass.a), static_cast<u32>(stage.alphaPass.b),
        static_cast<u32>(stage.alphaPass.c), static_cast<u32>(stage.alphaPass.d),
        static_cast<u32>(stage.indTexStage), static_cast<u32>(stage.indTexMtxId));
  }

  --sM14AuditRemaining;
  if (sM14AuditRemaining == 0) {
    Log.info("M14 SEQ_LOAD GX audit complete gen={} draws={}",
             sM14AuditGeneration, sM14AuditDrawOrdinal);
  }
}
'''
    cp = replace_once(cp, globals_old, globals_new, "command_processor.cpp audit globals")

    # Log the exact display-list bytes at the consumer, represented only by a hash.
    dl_old = (
        "      ++sDisplayListDepth;\n"
        "      const ProcessResult nested = process(span.data, displayListSize);\n"
    )
    dl_new = (
        "      m14_audit_display_list(guestAddress, displayListSize, span.data, sDisplayListDepth + 1u);\n"
        "      ++sDisplayListDepth;\n"
        "      const ProcessResult nested = process(span.data, displayListSize);\n"
    )
    cp = replace_once(cp, dl_old, dl_new, "command_processor.cpp CALL_DL audit")

    # Unmerged draws reach this point only after pipeline/texture/bind resolution,
    # which is the effective state we need to inspect.
    draw_old = "  state.dirty &= ~DirtyImmediates;\n\n  uint32_t instanceCount = 1;\n"
    draw_new = (
        "  state.dirty &= ~DirtyImmediates;\n\n"
        "  m14_audit_draw(prim, fmt, vtxCount, vertRange, false, cache.shaderInfo);\n\n"
        "  uint32_t instanceCount = 1;\n"
    )
    cp = replace_once(cp, draw_old, draw_new, "command_processor.cpp effective draw audit")

    # Clean-state merges bypass push_gx_draw; the cached shader/bind state is the
    # same effective state, so include the new draw before folding it into the prior command.
    merge_old = "    gfx::detail::increment_merged_draw_count();\n    return;\n"
    merge_new = (
        "    m14_audit_draw(prim, fmt, vtxCount, vertRange, true, sDrawCache.shaderInfo);\n"
        "    gfx::detail::increment_merged_draw_count();\n"
        "    return;\n"
    )
    cp = replace_once(cp, merge_old, merge_new, "command_processor.cpp merged draw audit")

    # Export only the arm operation; all capture state remains translation-unit local.
    arm_anchor = "} // namespace\n\nstatic void handle_draw(u8 cmd, ByteReader& reader) noexcept;\n"
    arm_text = r'''} // namespace

namespace audit {
void arm_seqload_capture(std::uint32_t drawLimit) noexcept {
  ++sM14AuditGeneration;
  sM14AuditRemaining = drawLimit;
  sM14AuditDrawOrdinal = 0;
  Log.info("M14 SEQ_LOAD GX audit armed gen={} draw_limit={}", sM14AuditGeneration, drawLimit);
}
} // namespace audit

static void handle_draw(u8 cmd, ByteReader& reader) noexcept;
'''
    cp = replace_once(cp, arm_anchor, arm_text, "command_processor.cpp audit arm")

    if not check_only:
        cp_path.write_text(cp, encoding="utf-8", newline="\n")
        header_path.parent.mkdir(parents=True, exist_ok=True)
        header_path.write_text(AUDIT_HEADER, encoding="utf-8", newline="\n")
    return "verified" if check_only else "applied"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=pathlib.Path, help="pinned Aurora checkout")
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
        print(f"error: Aurora SEQ_LOAD capture adaptation failed: {exc}", file=sys.stderr)
        return 4

    if result == "already-applied":
        print("Aurora bounded SEQ_LOAD GX audit already applied")
    elif result == "verified":
        print("Aurora bounded SEQ_LOAD GX audit verified")
    else:
        print("Aurora bounded SEQ_LOAD GX audit applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
