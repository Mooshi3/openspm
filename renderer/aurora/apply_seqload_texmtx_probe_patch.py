#!/usr/bin/env python3
"""Extend the DrawDone-bounded SEQ_LOAD capture around the dynamic UI atlas path.

The first complete gray-bar frame showed a distinct UI/glyph-looking path using
GX_TEXMTX0 (matrix id 30) with a 512x256 I4 texture and direct S16/U16 vertex
attributes. The next runtime proved GX_TEXMTX0 itself is the expected 1/512,
1/256 texel-to-UV scale, while the earlier BP 0x66 coherence repair did not
change the image.

Keep the matrix probe, and additionally decode the direct S16 position / U16
TEX0 values exactly as Aurora's generated vertex shader should see them. Also
record TEVREG0 whenever the effective shader loads it; the suspect atlas path
uses TEVREG0 for RGB and the I4 sample primarily as an alpha mask. These are
metadata/value diagnostics only and do not change GX state, texture bytes,
ordering, or presentation.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
LOCK = HERE / "aurora.lock.json"
SIGNATURE = "M14 GX deep texmtx0"
INTEGER_SIGNATURE = "M14 GX deep integer vertex"
TEVREG_SIGNATURE = "M14 GX deep tevreg0"

MATRIX_OLD = '''    sM14AuditLastVtxStateHash = descHash;\n  }\n\n  std::size_t posOff = static_cast<std::size_t>(-1);\n'''
MATRIX_NEW = '''    const u32 rawTexMtx = static_cast<u32>(state.tcgs[0].mtx);\n    if (rawTexMtx >= static_cast<u32>(GX_TEXMTX0) && rawTexMtx <= static_cast<u32>(GX_TEXMTX9) &&\n        ((rawTexMtx - static_cast<u32>(GX_TEXMTX0)) % 3u) == 0u) {\n      const u32 texMtxIdx = (rawTexMtx - static_cast<u32>(GX_TEXMTX0)) / 3u;\n      const auto& tm = state.texMtxs[texMtxIdx];\n      Log.info("M14 GX deep texmtx0 id={} idx={} m=[{:.7f},{:.7f},{:.7f},{:.7f};{:.7f},{:.7f},{:.7f},{:.7f};{:.7f},{:.7f},{:.7f},{:.7f}]",\n               rawTexMtx, texMtxIdx,\n               tm.m0[0], tm.m0[1], tm.m0[2], tm.m0[3],\n               tm.m1[0], tm.m1[1], tm.m1[2], tm.m1[3],\n               tm.m2[0], tm.m2[1], tm.m2[2], tm.m2[3]);\n    }\n    sM14AuditLastVtxStateHash = descHash;\n  }\n\n  std::size_t posOff = static_cast<std::size_t>(-1);\n'''

INTEGER_OLD = '''    Log.info("M14 GX deep vertex draw={} v={} prim={} pos_f32={} pos={:.4f},{:.4f},{:.4f} pos_idx={} tex_f32={} tex={:.4f},{:.4f} tex_idx={} clr_rgba8={} rgba=0x{:08X} raw_hash=0x{:016X}",\n             sM14AuditDrawOrdinal + 1u, v, static_cast<u32>(prim),\n             posDirectF32 ? 1u : 0u, px, py, pz, posIndex,\n             texDirectF32 ? 1u : 0u, ts, tt, texIndex,\n             rgba8 ? 1u : 0u, rgba, m14_audit_hash(rec, stride));\n'''
INTEGER_NEW = '''    const bool posDirectS16 = posOff != static_cast<std::size_t>(-1) &&\n                              state.vtxDesc[GX_VA_POS] == GX_DIRECT &&\n                              vf.attrs[GX_VA_POS].type == GX_S16;\n    const bool texDirectU16 = texOff != static_cast<std::size_t>(-1) &&\n                              state.vtxDesc[GX_VA_TEX0] == GX_DIRECT &&\n                              vf.attrs[GX_VA_TEX0].type == GX_U16;\n    std::int32_t pix = 0, piy = 0, piz = 0;\n    std::uint32_t tiu = 0, tiv = 0;\n    float pfx = 0.f, pfy = 0.f, pfz = 0.f, tfu = 0.f, tfv = 0.f;\n    if (posDirectS16) {\n      const auto comps = comp_cnt_count(GX_VA_POS, vf.attrs[GX_VA_POS].cnt);\n      pix = static_cast<std::int16_t>(m14_deep_be16(rec + posOff));\n      piy = static_cast<std::int16_t>(m14_deep_be16(rec + posOff + 2u));\n      if (comps > 2u) piz = static_cast<std::int16_t>(m14_deep_be16(rec + posOff + 4u));\n      const float scale = 1.0f / static_cast<float>(1u << vf.attrs[GX_VA_POS].frac);\n      pfx = static_cast<float>(pix) * scale;\n      pfy = static_cast<float>(piy) * scale;\n      pfz = static_cast<float>(piz) * scale;\n    }\n    if (texDirectU16) {\n      const auto comps = comp_cnt_count(GX_VA_TEX0, vf.attrs[GX_VA_TEX0].cnt);\n      tiu = m14_deep_be16(rec + texOff);\n      if (comps > 1u) tiv = m14_deep_be16(rec + texOff + 2u);\n      const float scale = 1.0f / static_cast<float>(1u << vf.attrs[GX_VA_TEX0].frac);\n      tfu = static_cast<float>(tiu) * scale;\n      tfv = static_cast<float>(tiv) * scale;\n    }\n    Log.info("M14 GX deep integer vertex draw={} v={} prim={} pos_f32={} pos={:.4f},{:.4f},{:.4f} pos_idx={} pos_s16={} pos_i={},{},{} pos_fx={:.4f},{:.4f},{:.4f} tex_f32={} tex={:.4f},{:.4f} tex_idx={} tex_u16={} tex_i={},{} tex_fx={:.4f},{:.4f} clr_rgba8={} rgba=0x{:08X} raw_hash=0x{:016X}",\n             sM14AuditDrawOrdinal + 1u, v, static_cast<u32>(prim),\n             posDirectF32 ? 1u : 0u, px, py, pz, posIndex,\n             posDirectS16 ? 1u : 0u, pix, piy, piz, pfx, pfy, pfz,\n             texDirectF32 ? 1u : 0u, ts, tt, texIndex,\n             texDirectU16 ? 1u : 0u, tiu, tiv, tfu, tfv,\n             rgba8 ? 1u : 0u, rgba, m14_audit_hash(rec, stride));\n'''

TEVREG_ANCHOR = '''  for (u32 tex = 0; tex < MaxTextures; ++tex) {\n'''
TEVREG_NEW = '''  if (shaderInfo.loadsTevReg.test(GX_TEVREG0)) {\n    const auto& r = state.colorRegs[GX_TEVREG0];\n    Log.info("M14 GX deep tevreg0 draw={} rgba={:.7f},{:.7f},{:.7f},{:.7f}",\n             ordinal, r.x(), r.y(), r.z(), r.w());\n  }\n\n  for (u32 tex = 0; tex < MaxTextures; ++tex) {\n'''


def run(*args: str, cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def apply_optional(text: str, signature: str, old: str, new: str, label: str) -> tuple[str, bool]:
    if signature in text:
        if text.count(signature) != 1:
            raise RuntimeError(f"lib/gx/command_processor.cpp: duplicate {label}")
        return text, False
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label} anchor expected once, found {count}")
    return text.replace(old, new, 1), True


def transform(source: pathlib.Path, check_only: bool) -> str:
    path = source / "lib/gx/command_processor.cpp"
    text = path.read_text(encoding="utf-8")
    if "M14 SEQ_LOAD deep-frame audit active" not in text:
        raise RuntimeError("deep-frame audit must be installed before atlas probe")

    changed = False
    text, did = apply_optional(text, SIGNATURE, MATRIX_OLD, MATRIX_NEW, "texture-matrix probe")
    changed |= did
    text, did = apply_optional(text, INTEGER_SIGNATURE, INTEGER_OLD, INTEGER_NEW, "integer-vertex probe")
    changed |= did
    text, did = apply_optional(text, TEVREG_SIGNATURE, TEVREG_ANCHOR, TEVREG_NEW, "TEVREG0 probe")
    changed |= did

    if not check_only and changed:
        path.write_text(text, encoding="utf-8", newline="\n")
    if changed:
        return "verified" if check_only else "applied"
    return "already-applied"


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
        print(f"error: Aurora SEQ_LOAD atlas probe failed: {exc}", file=sys.stderr)
        return 4

    if result == "already-applied":
        print("Aurora SEQ_LOAD atlas probe already applied")
    elif result == "verified":
        print("Aurora SEQ_LOAD atlas probe verified")
    else:
        print("Aurora SEQ_LOAD atlas probe applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
