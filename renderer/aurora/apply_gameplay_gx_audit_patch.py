#!/usr/bin/env python3
"""Apply the first runtime-facing fixes from the second M14 gameplay GX audit.

This layer runs last, after OpenSPM's existing raw-GX, diagnostic, TEV and timing
adaptations. It intentionally fixes only source-proven defects with narrow
semantics:

* legal zero/incomplete primitives consume their FIFO payload but submit no draw;
* raw GX vertex-cache invalidation becomes an ordered visibility epoch, clears
  per-slot staged ranges and breaks draw merging;
* NBT3 requirement scanning accounts for the shader's per-slice B/T offset;
* raw CP array-base writes no longer dirty the shader pipeline merely because the
  address register was written; cp_arraybase still dirties on endianness/layout
  transition itself;
* the per-frame raw-array reuse cache keeps one immutable snapshot per active
  base/stride and trusts it within a visibility epoch, comparing bytes exactly
  after GX invalidation;
* ordinary and indirect-simple texture sampling applies GX SU scale in texel
  space before normalizing by the logical texture dimensions.

The transform is exact/idempotent against the pinned Aurora revision after the
current OpenSPM M14 patch chain. Unknown or partial source shapes fail closed.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
LOCK = HERE / "aurora.lock.json"
CP_SIGNATURE = "M14 gameplay GX audit v1: vertex visibility epoch active"
REGS_SIGNATURE = "M14 gameplay GX audit v1: base-only CP writes do not dirty pipeline"
SHADER_SIGNATURE = "M14 gameplay GX audit v1: ordinary SU scaling active"


def run(*args: str, cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected source fragment exactly once, found {count}")
    return text.replace(old, new, 1)


def transform_command_processor(text: str) -> str:
    text = once(
        text,
        "DrawCache sDrawCache;\nthread_local u32 sDisplayListDepth = 0;\n",
        "DrawCache sDrawCache;\n"
        "// M14 gameplay GX audit v1: vertex visibility epoch active. The Wii's\n"
        "// GX_CMD_INVL_VC is the visibility boundary for CPU-mutated raw arrays.\n"
        "u64 sM14GuestArrayVisibilityEpoch = 1;\n"
        "thread_local u32 sDisplayListDepth = 0;\n",
        "command_processor visibility epoch declaration",
    )

    text = once(
        text,
        "    case CP_CMD_INVAL_VTX: {\n"
        "      // Invalidate vertex cache\n"
        "      break;\n"
        "    }\n",
        "    case CP_CMD_INVAL_VTX: {\n"
        "      // Raw-console GX vertex-cache invalidation is an ordered visibility\n"
        "      // boundary. Force the next indexed draw through staging/content\n"
        "      // validation and forbid merging with a draw that used pre-invalidate\n"
        "      // bytes. The frame snapshot cache may still reuse an immutable range\n"
        "      // after exact comparison of newly-visible bytes.\n"
        "      ++sM14GuestArrayVisibilityEpoch;\n"
        "      for (int i = GX_VA_POS; i <= GX_VA_TEX7; ++i) {\n"
        "        auto& array = g_gxState.arrays[i];\n"
        "        if (array.guestMapped) array.cachedRange = {};\n"
        "      }\n"
        "      sDrawCache.lastDrawFmt = GX_MAX_VTXFMT;\n"
        "      g_gxState.dirty |= DirtyImmediates;\n"
        "      static bool m14InvalidateReported = false;\n"
        "      if (!m14InvalidateReported) {\n"
        "        Log.info(\"M14 raw GX vertex-cache invalidation/merge barrier active\");\n"
        "        m14InvalidateReported = true;\n"
        "      }\n"
        "      break;\n"
        "    }\n",
        "command_processor raw vertex invalidation",
    )

    text = once(
        text,
        "  const auto vertexData = reader.take(totalVtxBytes);\n"
        "  const auto guestArrays = guest_array_requirements(fmt, vtxCount, vertexData);\n",
        "  const auto vertexData = reader.take(totalVtxBytes);\n"
        "  const bool hasCompletePrimitive = [&]() noexcept {\n"
        "    switch (prim) {\n"
        "    case GX_QUADS: return vtxCount >= 4;\n"
        "    case GX_TRIANGLES: return vtxCount >= 3;\n"
        "    case GX_TRIANGLESTRIP: return vtxCount >= 3;\n"
        "    case GX_TRIANGLEFAN: return vtxCount >= 3;\n"
        "    case GX_LINES: return vtxCount >= 2;\n"
        "    case GX_LINESTRIP: return vtxCount >= 2;\n"
        "    case GX_POINTS: return vtxCount >= 1;\n"
        "    default: return false;\n"
        "    }\n"
        "  }();\n"
        "  if (!hasCompletePrimitive) {\n"
        "    static u64 m14EmptyDraws = 0;\n"
        "    const u64 n = ++m14EmptyDraws;\n"
        "    if (n <= 8u || (n & (n - 1u)) == 0u) {\n"
        "      Log.info(\"M14 consumed non-raster GX primitive n={} prim={} fmt={} vtx={} bytes={}\",\n"
        "               n, static_cast<u32>(prim), static_cast<u32>(fmt), vtxCount, totalVtxBytes);\n"
        "    }\n"
        "    return;\n"
        "  }\n"
        "  const auto guestArrays = guest_array_requirements(fmt, vtxCount, vertexData);\n",
        "command_processor zero/small primitive handling",
    )

    text = once(
        text,
        "    for (u16 v = 0; v < vtxCount; v += 4) {\n",
        "    for (u16 v = 0; v + 3u < vtxCount; v += 4) {\n",
        "command_processor incomplete quad handling",
    )

    text = once(
        text,
        "  const auto record_index = [&](int attrIndex, u32 index) {\n"
        "    if (attrIndex < GX_VA_POS || attrIndex > GX_VA_TEX7) return;\n"
        "    auto& array = g_gxState.arrays[attrIndex];\n"
        "    if (!array.guestMapped) return;\n"
        "    const u32 elementSize = guest_array_element_size(fmt, attrIndex);\n"
        "    const uint64_t end = static_cast<uint64_t>(index) * array.stride + elementSize;\n",
        "  const auto record_index = [&](int attrIndex, u32 index, u32 within) {\n"
        "    if (attrIndex < GX_VA_POS || attrIndex > GX_VA_TEX7) return;\n"
        "    auto& array = g_gxState.arrays[attrIndex];\n"
        "    if (!array.guestMapped) return;\n"
        "    const u32 elementSize = guest_array_element_size(fmt, attrIndex);\n"
        "    const uint64_t end = static_cast<uint64_t>(index) * array.stride + within + elementSize;\n",
        "command_processor NBT3 record-index extent",
    )
    text = once(
        text,
        "        for (u32 n = 0; n < indexCount; ++n) record_index(i, vertexData[cursor++]);\n",
        "        const u32 sliceBytes = guest_array_element_size(fmt, i);\n"
        "        for (u32 n = 0; n < indexCount; ++n) {\n"
        "          const u32 within = i == GX_VA_NRM && attrFmt.cnt == GX_NRM_NBT3 ? n * sliceBytes : 0u;\n"
        "          record_index(i, vertexData[cursor++], within);\n"
        "        }\n",
        "command_processor NBT3 INDEX8 extent",
    )
    text = once(
        text,
        "          record_index(i, read_bits<u16>(vertexData.data() + cursor));\n"
        "          cursor += sizeof(u16);\n",
        "          const u32 within = i == GX_VA_NRM && attrFmt.cnt == GX_NRM_NBT3\n"
        "                                 ? n * guest_array_element_size(fmt, i)\n"
        "                                 : 0u;\n"
        "          record_index(i, read_bits<u16>(vertexData.data() + cursor), within);\n"
        "          cursor += sizeof(u16);\n",
        "command_processor NBT3 INDEX16 extent",
    )

    text = once(
        text,
        "struct M14GuestArrayStageEntry {\n"
        "  const void* data = nullptr;\n"
        "  u8 stride = 0;\n"
        "  std::vector<u8> snapshot;\n"
        "  gfx::Range range{};\n"
        "};\n",
        "struct M14GuestArrayStageEntry {\n"
        "  const void* data = nullptr;\n"
        "  u8 stride = 0;\n"
        "  u64 visibilityEpoch = 0;\n"
        "  std::vector<u8> snapshot;\n"
        "  gfx::Range range{};\n"
        "};\n",
        "command_processor stage-entry visibility epoch",
    )

    old_lookup = '''  const auto* source = static_cast<const u8*>(array.data);
  for (auto it = sM14GuestArrayStages.rbegin(); it != sM14GuestArrayStages.rend(); ++it) {
    if (it->data != array.data || it->stride != array.stride || it->snapshot.size() < uploadSize) continue;
    if (std::memcmp(it->snapshot.data(), source, uploadSize) != 0) continue;
    ++sM14GuestArrayReuseHits;
    if (!sM14GuestArrayReuseReported) {
      Log.info("M14 raw guest-array per-frame content reuse active");
      sM14GuestArrayReuseReported = true;
    }
    profileFinish();
    return it->range;
  }

  ++sM14GuestArrayReuseMisses;
'''
    new_lookup = '''  const auto* source = static_cast<const u8*>(array.data);
  M14GuestArrayStageEntry* currentEntry = nullptr;
  for (auto& entry : sM14GuestArrayStages) {
    if (entry.data == array.data && entry.stride == array.stride) {
      currentEntry = &entry;
      break;
    }
  }
  if (currentEntry != nullptr && currentEntry->snapshot.size() >= uploadSize) {
    const bool sameVisibility = currentEntry->visibilityEpoch == sM14GuestArrayVisibilityEpoch;
    const bool sameBytes = sameVisibility || std::memcmp(currentEntry->snapshot.data(), source, uploadSize) == 0;
    if (sameBytes) {
      currentEntry->visibilityEpoch = sM14GuestArrayVisibilityEpoch;
      ++sM14GuestArrayReuseHits;
      if (!sM14GuestArrayReuseReported) {
        Log.info("M14 raw guest-array per-frame content/epoch reuse active");
        sM14GuestArrayReuseReported = true;
      }
      profileFinish();
      return currentEntry->range;
    }
  }

  ++sM14GuestArrayReuseMisses;
'''
    text = once(text, old_lookup, new_lookup, "command_processor per-base epoch cache lookup")

    old_tail = '''  const gfx::Range range = gfx::push_storage(source, uploadSize);
  M14GuestArrayStageEntry entry;
  entry.data = array.data;
  entry.stride = array.stride;
  entry.snapshot.assign(source, source + uploadSize);
  entry.range = range;
  sM14GuestArrayStages.emplace_back(std::move(entry));
  sM14GuestArrayStagedBytes += uploadSize;
  profileFinish();
  return range;
}
'''
    new_tail = '''  const gfx::Range range = gfx::push_storage(source, uploadSize);
  if (currentEntry != nullptr) {
    currentEntry->visibilityEpoch = sM14GuestArrayVisibilityEpoch;
    currentEntry->snapshot.assign(source, source + uploadSize);
    currentEntry->range = range;
  } else {
    M14GuestArrayStageEntry entry;
    entry.data = array.data;
    entry.stride = array.stride;
    entry.visibilityEpoch = sM14GuestArrayVisibilityEpoch;
    entry.snapshot.assign(source, source + uploadSize);
    entry.range = range;
    sM14GuestArrayStages.emplace_back(std::move(entry));
  }
  sM14GuestArrayStagedBytes += uploadSize;
  profileFinish();
  return range;
}
'''
    text = once(text, old_tail, new_tail, "command_processor per-base epoch cache update")

    text = once(
        text,
        "    const u32 uploadSize = array.guestMapped ? guestArrays[i] : array.size;\n"
        "    AURORA_ASSERT(!array.guestMapped || uploadSize != 0,\n"
        "                  \"raw GX indexed array {} had no referenced prefix\", i);\n",
        "    const u32 uploadSize = array.guestMapped ? guestArrays[i] : array.size;\n"
        "    if (array.guestMapped && uploadSize == 0) {\n"
        "      Log.error(\"M14 nonempty raw GX draw missing prefix prim={} fmt={} vtx={} attr={} desc={} base=0x{:X} span={} stride={} cached={} epoch={}\",\n"
        "                static_cast<u32>(prim), static_cast<u32>(fmt), vtxCount, i,\n"
        "                static_cast<u32>(state.vtxDesc[i]),\n"
        "                static_cast<u64>(reinterpret_cast<std::uintptr_t>(array.data)),\n"
        "                array.size, static_cast<u32>(array.stride), array.cachedRange.size,\n"
        "                sM14GuestArrayVisibilityEpoch);\n"
        "    }\n"
        "    AURORA_ASSERT(!array.guestMapped || uploadSize != 0,\n"
        "                  \"raw GX indexed array {} had no referenced prefix\", i);\n",
        "command_processor nonempty missing-prefix metadata",
    )

    return text


def transform_regs(text: str) -> str:
    return once(
        text,
        "    regs[0xA0 + i] = {cp_arraybase, DirtyPipeline, /* alwaysHandle */ true};\n",
        "    // M14 gameplay GX audit v1: base-only CP writes do not dirty pipeline.\n"
        "    // cp_arraybase itself still marks DirtyPipeline for an endian/layout transition.\n"
        "    regs[0xA0 + i] = {cp_arraybase, 0, /* alwaysHandle */ true};\n",
        "regs.cpp raw array-base dirty policy",
    )


def transform_shader(text: str) -> str:
    text = once(
        text,
        "      const bool useSimpleCoords = stage.indTexMtxId == GX_ITM_OFF && !stage.indTexAddPrev;\n",
        "      // M14 gameplay GX audit v1: ordinary SU scaling active. Keep the\n"
        "      // indirect path in texel space so SU scaling and wrap semantics are\n"
        "      // identical even when no indirect matrix/add-prev is selected.\n"
        "      const bool useSimpleCoords = false;\n",
        "shader indirect simple-coordinate SU scaling",
    )
    text = once(
        text,
        "    if (uvIn.empty()) {\n"
        "      // No indirect texturing\n"
        "      uvIn = fmt::format(\"tex{0}_uv\", underlying(stage.texCoordId));\n"
        "    }\n",
        "    if (uvIn.empty()) {\n"
        "      // No indirect texturing: GX SU registers convert the generated\n"
        "      // coordinate to texel space. Aurora samples normalized textures, so\n"
        "      // normalize that result by the logical image dimensions.\n"
        "      const u32 texCoordId = underlying(stage.texCoordId);\n"
        "      const u32 texMapId = underlying(stage.texMapId);\n"
        "      uvIn = fmt::format(\n"
        "          \"tex{0}_uv * ubuf.texcoord_scale[{0}].xy / ubuf.tex{1}_size_bias.xy\",\n"
        "          texCoordId, texMapId);\n"
        "    }\n",
        "shader ordinary SU scaling",
    )
    return text


def transform(source: pathlib.Path, check_only: bool) -> str:
    cp_path = source / "lib/gx/command_processor.cpp"
    regs_path = source / "lib/gx/regs.cpp"
    shader_path = source / "lib/gx/shader.cpp"
    shader_info_path = source / "lib/gx/shader_info.cpp"
    cp = cp_path.read_text(encoding="utf-8")
    regs = regs_path.read_text(encoding="utf-8")
    shader = shader_path.read_text(encoding="utf-8")
    shader_info = shader_info_path.read_text(encoding="utf-8")

    states = (CP_SIGNATURE in cp, REGS_SIGNATURE in regs, SHADER_SIGNATURE in shader)
    if all(states):
        return "already-applied"
    if any(states):
        raise RuntimeError(f"partial gameplay GX audit adaptation detected: cp={states[0]} regs={states[1]} shader={states[2]}")

    required = {
        "command_processor timing": "M14 raw guest-array stage profile" in cp,
        "command_processor heavy audit": "M14 heavy-scene GX audit auto-armed" in cp,
        "raw guest arrays": "guest_array_requirements" in cp,
        "raw cp base": "M14 raw CP array-base duplicate write preserved staged cache" in regs,
        "TEV repair": "M14 component-aware TEV register liveness" in shader_info,
    }
    missing = [name for name, ok in required.items() if not ok]
    if missing:
        raise RuntimeError("required prior M14 layer missing: " + ", ".join(missing))

    cp_new = transform_command_processor(cp)
    regs_new = transform_regs(regs)
    shader_new = transform_shader(shader)

    if not check_only:
        cp_path.write_text(cp_new, encoding="utf-8", newline="\n")
        regs_path.write_text(regs_new, encoding="utf-8", newline="\n")
        shader_path.write_text(shader_new, encoding="utf-8", newline="\n")
    return "verified" if check_only else "applied"


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
        print(f"error: Aurora gameplay GX audit adaptation failed: {exc}", file=sys.stderr)
        return 4

    print({
        "already-applied": "Aurora gameplay GX audit v1 already applied",
        "verified": "Aurora gameplay GX audit v1 transforms verified",
        "applied": "Aurora gameplay GX audit v1 applied",
    }[result])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
