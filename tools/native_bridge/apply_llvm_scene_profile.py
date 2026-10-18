#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Adapts DolRecomp (GPL-3.0) and carries parts of its source; see LICENSE.
"""dev100: sample main-call and dispDraw callback intervals without CPU spills."""
from pathlib import Path

TARGET = Path("src/backend/llvm/control_flow.cpp")
SIGNATURE = "OPENSPM_M14_LLVM_SCENE_INTERVAL_PROFILE"
ANCHOR = """  switch (term.kind) {
  case DOLIR_TERM_FALLTHROUGH:"""
REPLACEMENT = """  /* OPENSPM_M14_LLVM_SCENE_INTERVAL_PROFILE
   * Main-loop direct BL intervals and dispDraw linked computed callbacks only.
   * These host-only markers observe SSA values; they do not materialize CPUState.
   * The bridge samples one whole frame in 64. Unselected frames take only the
   * flag load/branch, with no host call, clock read, allocation or logging.
   * An interval ends at the next marker or frame-phase boundary, so it includes
   * intervening caller traversal as well as the callee, including cold escapes. */
  const bool openspmMainCall = !modern_runtime_ && term.linked &&
      term.kind == DOLIR_TERM_BRANCH &&
      term.guest_pc >= 0x801A7AACu && term.guest_pc < 0x801A7D1Cu;
  const bool openspmDrawCall = !modern_runtime_ && term.linked &&
      term.kind == DOLIR_TERM_INDIRECT &&
      term.guest_pc >= 0x8005B028u && term.guest_pc < 0x8005B2F4u;
  if (openspmMainCall || openspmDrawCall) {
    Type *i32 = Type::getInt32Ty(context_);
    Value *flag = module_.getOrInsertGlobal("openspm_m14_scene_profile_active", i32);
    Value *enabled = builder_.CreateICmpNE(
        builder_.CreateLoad(i32, flag), builder_.getInt32(0));
    if (openspmDrawCall)
      enabled = builder_.CreateAnd(enabled, values_[term.condition]);
    BasicBlock *observe = BasicBlock::Create(context_, "scene_observe", function_);
    BasicBlock *resume = BasicBlock::Create(context_, "scene_resume", function_);
    builder_.CreateCondBr(enabled, observe, resume);
    builder_.SetInsertPoint(observe);
    auto marker = module_.getOrInsertFunction(
        "openspm_m14_scene_interval",
        FunctionType::get(Type::getVoidTy(context_), {i32, i32, i32}, false));
    Value *target = openspmDrawCall ? values_[term.target_value]
        : static_cast<Value *>(builder_.getInt32(term.target_addresses[0]));
    builder_.CreateCall(marker, {builder_.getInt32(openspmDrawCall ? 1u : 2u),
                                 builder_.getInt32(term.guest_pc), target});
    builder_.CreateBr(resume);
    builder_.SetInsertPoint(resume);
  }
  switch (term.kind) {
  case DOLIR_TERM_FALLTHROUGH:"""


# dev100 incorrectly restricted host-only observation to native-register-ABI
# bodies. CPUState-ABI LLVM bodies are equally observable without state writes.
OLD_REPLACEMENT = REPLACEMENT.replace(
    "openspmMainCall = !modern_runtime_", "openspmMainCall = native_abi_ && !modern_runtime_"
).replace("openspmDrawCall = !modern_runtime_", "openspmDrawCall = native_abi_ && !modern_runtime_")


def retire(source_root: Path, check_only: bool = False) -> str:
    """dev102: remove dev100's CFG instrumentation after it identified the hot sites.

    The profiler inserted observe/resume basic blocks immediately before guest
    terminators.  Its measurements are now durable, so return the compiler to
    the pristine terminator CFG for correctness-sensitive motion/sequence code.
    Runtime reporting may remain linked; with no generated markers it is inert.
    """
    path = source_root / TARGET
    source = path.read_text(encoding="utf-8")
    if source.count(REPLACEMENT) == 1:
        if check_only:
            return "verified-retire"
        path.write_text(source.replace(REPLACEMENT, ANCHOR, 1),
                        encoding="utf-8", newline="\n")
        return "retired"
    if source.count(OLD_REPLACEMENT) == 1:
        if check_only:
            return "verified-retire-old"
        path.write_text(source.replace(OLD_REPLACEMENT, ANCHOR, 1),
                        encoding="utf-8", newline="\n")
        return "retired-old"
    if SIGNATURE in source:
        raise RuntimeError("dev100 scene interval transform is partial; refusing retirement")
    return "already-retired"


def transform(source_root: Path, check_only: bool = False) -> str:
    path = source_root / TARGET
    source = path.read_text(encoding="utf-8")
    if source.count(OLD_REPLACEMENT) == 1:
        if check_only:
            return "verified-upgrade"
        path.write_text(source.replace(OLD_REPLACEMENT, REPLACEMENT, 1), encoding="utf-8", newline="\n")
        return "upgraded"
    if SIGNATURE in source:
        if source.count(REPLACEMENT) != 1:
            raise RuntimeError("dev100 scene interval transform is incomplete")
        return "already-applied"
    if source.count(ANCHOR) != 1:
        raise RuntimeError("dev100 emitTerminator anchor changed")
    if check_only:
        return "verified"
    path.write_text(source.replace(ANCHOR, REPLACEMENT, 1), encoding="utf-8", newline="\n")
    return "applied"
