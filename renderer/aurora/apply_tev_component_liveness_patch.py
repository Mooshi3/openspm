#!/usr/bin/env python3
"""Repair Aurora TEV register liveness for original-Wii component semantics.

Aurora's pinned shader analysis tracks TEV writes as one whole-register bit.
A GX TEV color pass writes only RGB, while the alpha pass writes only alpha.
Marking the whole register written immediately after the color pass therefore
makes a later alpha read believe its component is initialized even when it must
come from the guest-visible TEV register value entering the shader.

Aurora's generated WGSL also preserves the untouched component explicitly:
color writes are emitted as ``reg = vec4f(new_rgb, reg.a)`` and alpha writes as
``reg.a = new_alpha``. Component-aware liveness must therefore keep the entering
register value available whenever the opposite component has not yet been
initialized. Track RGB and alpha writes independently while retaining Aurora's
public whole-register load/write bitsets for shader declaration/uniform layout.

The M14 heavy-scene capture proved this path is relevant: affected 3D frames
bind real textures successfully while TEV register loads are aggressively
elided. This is a semantic correctness repair, not a visual special case.
Unknown pinned source drift fails closed.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
LOCK = HERE / "aurora.lock.json"
SIGNATURE = "M14 component-aware TEV register liveness"


def run(*args: str, cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected source fragment exactly once, found {count}")
    return text.replace(old, new, 1)


def transform(source: pathlib.Path, check_only: bool) -> str:
    path = source / "lib/gx/shader_info.cpp"
    text = path.read_text(encoding="utf-8")
    if SIGNATURE in text:
        return "already-applied"

    text = once(
        text,
        '#include <cmath>\n',
        '#include <bitset>\n#include <cmath>\n',
        "shader_info includes",
    )

    old_color = '''void color_arg_reg_info(GXTevColorArg arg, const TevStage& stage, ShaderInfo& info) {
  switch (arg) {
  case GX_CC_CPREV:
  case GX_CC_APREV:
    if (!info.writesTevReg.test(GX_TEVPREV)) {
      info.loadsTevReg.set(GX_TEVPREV);
    }
    break;
  case GX_CC_C0:
  case GX_CC_A0:
    if (!info.writesTevReg.test(GX_TEVREG0)) {
      info.loadsTevReg.set(GX_TEVREG0);
    }
    break;
  case GX_CC_C1:
  case GX_CC_A1:
    if (!info.writesTevReg.test(GX_TEVREG1)) {
      info.loadsTevReg.set(GX_TEVREG1);
    }
    break;
  case GX_CC_C2:
  case GX_CC_A2:
    if (!info.writesTevReg.test(GX_TEVREG2)) {
      info.loadsTevReg.set(GX_TEVREG2);
    }
    break;
'''
    new_color = '''// M14 component-aware TEV register liveness: GX color and alpha writes
// initialize different components of the same TEV register.
void color_arg_reg_info(GXTevColorArg arg, const TevStage& stage, ShaderInfo& info,
                        const std::bitset<MaxTevRegs>& colorWritten,
                        const std::bitset<MaxTevRegs>& alphaWritten) {
  switch (arg) {
  case GX_CC_CPREV:
    if (!colorWritten.test(GX_TEVPREV)) info.loadsTevReg.set(GX_TEVPREV);
    break;
  case GX_CC_APREV:
    if (!alphaWritten.test(GX_TEVPREV)) info.loadsTevReg.set(GX_TEVPREV);
    break;
  case GX_CC_C0:
    if (!colorWritten.test(GX_TEVREG0)) info.loadsTevReg.set(GX_TEVREG0);
    break;
  case GX_CC_A0:
    if (!alphaWritten.test(GX_TEVREG0)) info.loadsTevReg.set(GX_TEVREG0);
    break;
  case GX_CC_C1:
    if (!colorWritten.test(GX_TEVREG1)) info.loadsTevReg.set(GX_TEVREG1);
    break;
  case GX_CC_A1:
    if (!alphaWritten.test(GX_TEVREG1)) info.loadsTevReg.set(GX_TEVREG1);
    break;
  case GX_CC_C2:
    if (!colorWritten.test(GX_TEVREG2)) info.loadsTevReg.set(GX_TEVREG2);
    break;
  case GX_CC_A2:
    if (!alphaWritten.test(GX_TEVREG2)) info.loadsTevReg.set(GX_TEVREG2);
    break;
'''
    text = once(text, old_color, new_color, "color component liveness")

    old_alpha = '''void alpha_arg_reg_info(GXTevAlphaArg arg, const TevStage& stage, ShaderInfo& info) {
  switch (arg) {
  case GX_CA_APREV:
    if (!info.writesTevReg.test(GX_TEVPREV)) {
      info.loadsTevReg.set(GX_TEVPREV);
    }
    break;
  case GX_CA_A0:
    if (!info.writesTevReg.test(GX_TEVREG0)) {
      info.loadsTevReg.set(GX_TEVREG0);
    }
    break;
  case GX_CA_A1:
    if (!info.writesTevReg.test(GX_TEVREG1)) {
      info.loadsTevReg.set(GX_TEVREG1);
    }
    break;
  case GX_CA_A2:
    if (!info.writesTevReg.test(GX_TEVREG2)) {
      info.loadsTevReg.set(GX_TEVREG2);
    }
    break;
'''
    new_alpha = '''void alpha_arg_reg_info(GXTevAlphaArg arg, const TevStage& stage, ShaderInfo& info,
                        const std::bitset<MaxTevRegs>& alphaWritten) {
  switch (arg) {
  case GX_CA_APREV:
    if (!alphaWritten.test(GX_TEVPREV)) info.loadsTevReg.set(GX_TEVPREV);
    break;
  case GX_CA_A0:
    if (!alphaWritten.test(GX_TEVREG0)) info.loadsTevReg.set(GX_TEVREG0);
    break;
  case GX_CA_A1:
    if (!alphaWritten.test(GX_TEVREG1)) info.loadsTevReg.set(GX_TEVREG1);
    break;
  case GX_CA_A2:
    if (!alphaWritten.test(GX_TEVREG2)) info.loadsTevReg.set(GX_TEVREG2);
    break;
'''
    text = once(text, old_alpha, new_alpha, "alpha component liveness")

    old_loop = '''  for (int i = 0; i < config.tevStageCount; ++i) {
    const auto& stage = config.tevStages[i];
    // Color pass
    color_arg_reg_info(stage.colorPass.a, stage, info);
    color_arg_reg_info(stage.colorPass.b, stage, info);
    color_arg_reg_info(stage.colorPass.c, stage, info);
    color_arg_reg_info(stage.colorPass.d, stage, info);
    info.writesTevReg.set(stage.colorOp.outReg);

    // Alpha pass
    alpha_arg_reg_info(stage.alphaPass.a, stage, info);
    alpha_arg_reg_info(stage.alphaPass.b, stage, info);
    alpha_arg_reg_info(stage.alphaPass.c, stage, info);
    alpha_arg_reg_info(stage.alphaPass.d, stage, info);
    if (!info.writesTevReg.test(stage.alphaOp.outReg)) {
      // If we're writing alpha to a register that's not been
      // written to in the shader, load from uniform buffer
      info.loadsTevReg.set(stage.alphaOp.outReg);
      info.writesTevReg.set(stage.alphaOp.outReg);
    }
  }
'''
    new_loop = '''  std::bitset<MaxTevRegs> colorWritten;
  std::bitset<MaxTevRegs> alphaWritten;
  for (int i = 0; i < config.tevStageCount; ++i) {
    const auto& stage = config.tevStages[i];
    // Color pass: explicit arguments observe component state entering this
    // stage. Aurora emits the RGB write as vec4f(new_rgb, outReg.a), so the
    // untouched alpha component is also an implicit read that must be live.
    color_arg_reg_info(stage.colorPass.a, stage, info, colorWritten, alphaWritten);
    color_arg_reg_info(stage.colorPass.b, stage, info, colorWritten, alphaWritten);
    color_arg_reg_info(stage.colorPass.c, stage, info, colorWritten, alphaWritten);
    color_arg_reg_info(stage.colorPass.d, stage, info, colorWritten, alphaWritten);
    if (!alphaWritten.test(stage.colorOp.outReg)) {
      info.loadsTevReg.set(stage.colorOp.outReg);
    }
    colorWritten.set(stage.colorOp.outReg);
    info.writesTevReg.set(stage.colorOp.outReg);

    // Alpha pass writes only .a. If RGB has never been initialized, load the
    // entering vec4 before the partial assignment exactly as the old whole-reg
    // logic intended, but without confusing a prior RGB write for an alpha one.
    alpha_arg_reg_info(stage.alphaPass.a, stage, info, alphaWritten);
    alpha_arg_reg_info(stage.alphaPass.b, stage, info, alphaWritten);
    alpha_arg_reg_info(stage.alphaPass.c, stage, info, alphaWritten);
    alpha_arg_reg_info(stage.alphaPass.d, stage, info, alphaWritten);
    if (!colorWritten.test(stage.alphaOp.outReg)) {
      info.loadsTevReg.set(stage.alphaOp.outReg);
    }
    alphaWritten.set(stage.alphaOp.outReg);
    info.writesTevReg.set(stage.alphaOp.outReg);
  }
'''
    text = once(text, old_loop, new_loop, "TEV stage component tracking")

    if not check_only:
        path.write_text(text, encoding="utf-8", newline="\n")
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
        print(f"error: Aurora TEV component-liveness adaptation failed: {exc}", file=sys.stderr)
        return 4
    print({
        "already-applied": "Aurora TEV component liveness already applied",
        "verified": "Aurora TEV component liveness verified",
        "applied": "Aurora TEV component liveness applied",
    }[result])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
