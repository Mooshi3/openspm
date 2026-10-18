#!/usr/bin/env python3
"""Recover and correctly snapshot sampled GX texgen state in OpenSPM's raw Aurora path.

The strict-native prologue reached Mario's house and Aurora aborted while
building a shader with ``tcg.src == GX_MAX_TEXGENSRC`` (numeric 21). That value
is Aurora's C++ sentinel for an uninitialized texgen source, not a legal Wii GX
source.

The first recovery attempt patched ``populate_pipeline_config`` only for slots
below ``numTexGens``. A runtime rerun then failed at boot with the same shader
fatal and no recovery diagnostic. Inspection showed why: shader generation is
keyed by ``ShaderInfo::sampledTexCoords`` (TEV-referenced coordinates), not by
``numTexGens``. A sampled slot can therefore still contain the sentinel even
when it lies outside the copied active-prefix.

Later gameplay logs made the lifetime problem more explicit: the shader-boundary
fallback sometimes saw a stale sentinel in the draw's PipelineConfig while the
current raw XF shadow was already valid and decoded to TEX0. That means shader
construction can consume a PipelineConfig snapshot made before the old
numTexGens-limited copy captured the referenced slot. The safe fix is to snapshot
all eight decoded TCG slots when the draw's PipelineConfig is populated. This
does not activate unused coordinates; ShaderInfo still decides which coordinates
the shader consumes. It only makes the draw snapshot complete and immutable.

This patch migrates cached Aurora trees away from the original active-prefix
copy (and from the older v1 recovery), installs the complete draw-time TCG
snapshot, and retains the narrow shader-boundary GXInit fallback for a slot that
truly remains Aurora's impossible sentinel. Every other invalid source still
fails closed. Shader compilation uses immutable snapshots only. Older pipeline cache rows
are retired through an OpenSPM config version; their background recompilation
previously produced misleading live-XF diagnostics and a C++ data race.
"""

from __future__ import annotations

from pathlib import Path
import sys


PIPELINE_ORIGINAL = '''  for (u8 i = 0; i < g_gxState.numTexGens; ++i) {
    config.shaderConfig.tcgs[i] = g_gxState.tcgs[i];
  }
'''

PIPELINE_CAPTURE_ALL = '''  // OpenSPM M14 draw-time TCG snapshot: TEV/shader liveness, not only
  // numTexGens, determines which texcoord slots can be consumed by the eventual
  // shader. Capture all decoded slots now so deferred pipeline construction
  // cannot observe an incomplete active-prefix snapshot. Unused slots remain
  // inert because ShaderInfo still owns sampledTexCoords.
  for (u32 i = 0; i < MaxTexCoord; ++i) {
    config.shaderConfig.tcgs[i] = g_gxState.tcgs[i];
  }
'''

PIPELINE_BAD_V1 = '''  // OpenSPM raw-Wii correctness: GXInit gives all eight texgen slots valid
  // pass-through TEX0..TEX7 sources. Aurora's raw GXState{} instead uses
  // GX_MAX_TEXGENSRC as an internal uninitialized sentinel. If an original-Wii
  // draw activates a slot before this parser has reconstructed its setup write,
  // recover only that impossible sentinel to the documented GXInit value. Keep
  // every other invalid source fail-closed in shader generation.
  static u32 openspmTcgInitRecoveryMask = 0;
  for (u8 i = 0; i < g_gxState.numTexGens; ++i) {
    auto tcg = g_gxState.tcgs[i];
    if (tcg.src == GX_MAX_TEXGENSRC) {
      tcg.src = static_cast<GXTexGenSrc>(GX_TG_TEX0 + i);
      const u32 bit = 1u << i;
      if ((openspmTcgInitRecoveryMask & bit) == 0) {
        const u32 xfReg = 0x40u + i;
        const bool xfValid = g_gxState.xfRegValid.test(xfReg);
        const u32 xfRaw = g_gxState.xfRegCache[xfReg];
        Log.warn("OpenSPM M14 recovered active TCG {} from uninitialized sentinel to GXInit src {}; xf_valid={} xf_raw=0x{:08X}",
                 i, underlying(tcg.src), xfValid ? 1 : 0, xfRaw);
        openspmTcgInitRecoveryMask |= bit;
      }
    }
    config.shaderConfig.tcgs[i] = tcg;
  }
'''

SHADER_ORIGINAL = '''    const auto& tcg = config.tcgs[i];
    if (tcg.type == GX_TG_MTX3x4) {
'''

SHADER_RECOVERY_V2 = '''    auto tcg = config.tcgs[i];
    // OpenSPM raw-Wii correctness: GX_MAX_TEXGENSRC is Aurora's internal
    // uninitialized sentinel, never a legal Wii GX source. Real GXInit gives
    // every texgen slot a pass-through TEXi source. Recover only a slot that
    // TEV has actually made shader-visible; all other invalid values remain
    // fatal below.
    if (tcg.src == GX_MAX_TEXGENSRC) {
      const u32 xfReg = 0x40u + static_cast<u32>(i);
      const bool xfValid = g_gxState.xfRegValid.test(xfReg);
      const u32 xfRaw = g_gxState.xfRegCache[xfReg];
      tcg.src = static_cast<GXTexGenSrc>(GX_TG_TEX0 + i);
      Log.warn("OpenSPM M14 recovered sampled TCG {} sentinel to GXInit src {}; num_texgens={} xf_valid={} xf_raw=0x{:08X}",
               i, underlying(tcg.src), static_cast<u32>(g_gxState.numTexGens), xfValid ? 1 : 0, xfRaw);
    }
    if (tcg.type == GX_TG_MTX3x4) {
'''


SHADER_RECOVERY = SHADER_RECOVERY_V2.replace(
    "      const u32 xfReg = 0x40u + static_cast<u32>(i);\n"
    "      const bool xfValid = g_gxState.xfRegValid.test(xfReg);\n"
    "      const u32 xfRaw = g_gxState.xfRegCache[xfReg];\n", ""
).replace(
    '      Log.warn("OpenSPM M14 recovered sampled TCG {} sentinel to GXInit src {}; num_texgens={} xf_valid={} xf_raw=0x{:08X}",\n'
    '               i, underlying(tcg.src), static_cast<u32>(g_gxState.numTexGens), xfValid ? 1 : 0, xfRaw);\n',
    '      // M14 immutable shader recovery: background cache compilation must\n'
    '      // never read the game thread\'s mutable g_gxState (a data race).\n'
    '      Log.warn("M14 sampled TCG snapshot fallback slot={} src={}; no live GX state consulted",\n'
    '               i, underlying(tcg.src));\n'
)

PIPELINE_VERSION_OLD = "constexpr uint32_t GXPipelineConfigVersion = 13;"
PIPELINE_VERSION_NEW = "constexpr uint32_t GXPipelineConfigVersion = 0x4D140001; // OpenSPM immutable TCG snapshot v1"


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"cannot read {path}: {exc}") from exc


def write(path: Path, text: str) -> None:
    try:
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"cannot write {path}: {exc}") from exc


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: apply_tcg_init_recovery_patch.py <aurora-source-dir>", file=sys.stderr)
        return 2

    root = Path(sys.argv[1])
    gx_path = root / "lib" / "gx" / "gx.cpp"
    shader_path = root / "lib" / "gx" / "shader.cpp"
    version_path = root / "lib" / "gx" / "pipeline.hpp"

    try:
        gx_text = read(gx_path)
        shader_text = read(shader_path)
        version_text = read(version_path)
    except RuntimeError as exc:
        print(f"OpenSPM M14 sampled-TCG recovery: {exc}", file=sys.stderr)
        return 1

    migrated = False
    if PIPELINE_BAD_V1 in gx_text:
        gx_text = gx_text.replace(PIPELINE_BAD_V1, PIPELINE_ORIGINAL, 1)
        migrated = True
    elif PIPELINE_ORIGINAL not in gx_text and PIPELINE_CAPTURE_ALL not in gx_text:
        print(
            "OpenSPM M14 sampled-TCG recovery source drift: gx.cpp pipeline-copy anchor not found",
            file=sys.stderr,
        )
        return 1

    captured = False
    if PIPELINE_CAPTURE_ALL not in gx_text:
        if PIPELINE_ORIGINAL not in gx_text:
            print(
                "OpenSPM M14 sampled-TCG recovery source drift: draw-time TCG snapshot anchor not found",
                file=sys.stderr,
            )
            return 1
        gx_text = gx_text.replace(PIPELINE_ORIGINAL, PIPELINE_CAPTURE_ALL, 1)
        captured = True

    # Cached pipeline descriptions are immutable historical snapshots. The old
    # config version can contain the pre-repair active-prefix sentinel; warming
    # those entries does not demonstrate a current draw-state capture failure.
    if PIPELINE_VERSION_NEW not in version_text:
        if version_text.count(PIPELINE_VERSION_OLD) != 1:
            print("OpenSPM TCG pipeline version source drift", file=sys.stderr)
            return 1
        version_text = version_text.replace(PIPELINE_VERSION_OLD, PIPELINE_VERSION_NEW, 1)
    patched = False
    if SHADER_RECOVERY_V2 in shader_text:
        shader_text = shader_text.replace(SHADER_RECOVERY_V2, SHADER_RECOVERY, 1)
        patched = True
    if SHADER_RECOVERY not in shader_text:
        if SHADER_ORIGINAL not in shader_text:
            print(
                "OpenSPM M14 sampled-TCG recovery source drift: shader texgen-consumption anchor not found",
                file=sys.stderr,
            )
            return 1
        shader_text = shader_text.replace(SHADER_ORIGINAL, SHADER_RECOVERY, 1)
        patched = True

    try:
        if migrated or captured:
            write(gx_path, gx_text)
        if patched:
            write(shader_path, shader_text)
        if read(version_path) != version_text:
            write(version_path, version_text)
    except RuntimeError as exc:
        print(f"OpenSPM M14 sampled-TCG recovery: {exc}", file=sys.stderr)
        return 1

    actions = []
    if migrated:
        actions.append("migrated v1 recovery")
    if captured:
        actions.append("snapshotted all draw-time TCG slots")
    if patched:
        actions.append("patched immutable sampled shader fallback")
    if actions:
        print("; ".join(actions))
    else:
        print("sampled raw TCG draw snapshot/recovery and OpenSPM pipeline version active")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

