#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Adapts DolRecomp (GPL-3.0) and carries parts of its source; see LICENSE.
'''Patch private DolRecomp LLVM codegen for OpenSPM and MSVC portability.

The primary transform adds OpenSPM's inline WPAR gather to LLVM codegen. The
pinned upstream revision also uses GCC/Clang's __builtin_popcountll in its LLVM
ABI policy; OpenSPM builds that compiler with the player's active MSVC toolchain
on Windows, so the same private transform replaces that one operation with an
exact compiler-neutral C++17 helper.

The pinned Windows pipeline also needs app-level repairs:
- report real completed cache reuse/emission rather than pre-enumerating jobs;
- treat linked-call continuations (BL/BCL -> PC+4) as legal LLVM cold entries;
- treat the PC after MTMSR as a legal cold entry, because the LLVM emitter
  explicitly side-exits there when MSR[EE] transitions from disabled to enabled.
pipeline.c is intentionally not part of DolRecomp's codegen-source digest, so
these app-level repairs do not invalidate every proven guest-object cache entry.
The cold-entry set is hashed per object, so only objects that actually gain
continuation entry points are re-emitted.
'''
from __future__ import annotations

import argparse
from pathlib import Path
import sys

TARGET = Path("src") / "backend" / "llvm" / "memory_service.cpp"
SIGNATURE = "OPENSPM_M14_LLVM_WPAR_INLINE_GATHER"
POPCOUNT_TARGET = Path("src") / "backend" / "llvm" / "abi_policy.cpp"
POPCOUNT_SIGNATURE = "OPENSPM_M14_PORTABLE_POPCOUNT64"
PROGRESS_TARGET = Path("src") / "app" / "pipeline.c"
PROGRESS_SIGNATURE = "OPENSPM_M14_WINDOWS_LLVM_REAL_PROGRESS"
COLD_ENTRY_SIGNATURE = "OPENSPM_M14_LLVM_LINK_CONTINUATION_ENTRIES"

POPCOUNT_OLD_NAMESPACE = '''namespace {

u32 stateCount(const u64 *first, const u64 *second = nullptr) {
'''
POPCOUNT_NEW_NAMESPACE = '''namespace {

// OPENSPM_M14_PORTABLE_POPCOUNT64
// MSVC/C++17 lacks the GCC/Clang 64-bit population-count builtin.
// Kernighan's loop is exact for u64 and runs only while computing native ABI
// policy during code generation.
u32 portablePopcount64(u64 value) {
  u32 count = 0;
  while (value) {
    value &= value - 1u;
    count++;
  }
  return count;
}

u32 stateCount(const u64 *first, const u64 *second = nullptr) {
'''
POPCOUNT_OLD_CALL = '''    count += static_cast<u32>(
        __builtin_popcountll(first[word] | (second ? second[word] : 0u)));
'''
POPCOUNT_NEW_CALL = '''    count += portablePopcount64(first[word] | (second ? second[word] : 0u));
'''

PROGRESS_OLD_REUSE = '''static int emit_llvm_chunk_job(const void* data, void* user) {
    const LLVMChunkJob* job = (const LLVMChunkJob*)data;
    (void)user;
    if (reuse_llvm_object(job))
        return 1;
'''
PROGRESS_NEW_REUSE = '''static int emit_llvm_chunk_job(const void* data, void* user) {
    const LLVMChunkJob* job = (const LLVMChunkJob*)data;
    (void)user;
    if (reuse_llvm_object(job)) {
#ifdef _WIN32
        /* OPENSPM_M14_WINDOWS_LLVM_REAL_PROGRESS
         * The stock Windows path printed all job names before workers started,
         * which made console position look like completion progress. Report a
         * cache hit only after this worker has actually validated/reused it. */
        printf("[%u/%u] Reusing cached LLVM object %s\\n", job->index,
               job->total, job->name);
        fflush(stdout);
#endif
        return 1;
    }
'''

PROGRESS_OLD_RETURN = '''    if (!ok)
        remove(temp_path);
    return ok;
}

static void report_llvm_progress'''
PROGRESS_NEW_RETURN = '''    if (!ok)
        remove(temp_path);
#ifdef _WIN32
    if (ok) {
        printf("[%u/%u] Emitted LLVM object %s\\n", job->index, job->total,
               job->name);
        fflush(stdout);
    }
#endif
    return ok;
}

static void report_llvm_progress'''

PROGRESS_OLD_WINDOWS_LOOP = '''#ifdef _WIN32
    for (u32 i = 0; i < count; i++) {
        printf("[%u/%u] Emitting LLVM object %s\\n", jobs[i].index,
               jobs[i].total, jobs[i].name);
        fflush(stdout);
    }
    return run_parallel_jobs(jobs, sizeof(*jobs), count, workers,
                             emit_llvm_chunk_job, NULL);
#else
'''
PROGRESS_NEW_WINDOWS_LOOP = '''#ifdef _WIN32
    printf("[OpenSPM] Starting %u LLVM object jobs with %u Windows workers; progress below reflects completed cache reuse/emission.\\n",
           count, workers);
    fflush(stdout);
    return run_parallel_jobs(jobs, sizeof(*jobs), count, workers,
                             emit_llvm_chunk_job, NULL);
#else
'''

COLD_ENTRY_OLD_HEAD = '''static u32* collect_llvm_entry_points(const LoadedCodeSection* sections,
                                      u32 section_count, u32 program_entry,
                                      const DolRecompSymbolMap* symbols,
                                      u32* result_count) {
    size_t capacity = 1u + (symbols ? symbols->count : 0u);
    for (u32 i = 0; i < section_count; i++)
        capacity += sections[i].size / 4u;
'''
COLD_ENTRY_NEW_HEAD = '''static u32* collect_llvm_entry_points(const LoadedCodeSection* sections,
                                      u32 section_count, u32 program_entry,
                                      const DolRecompSymbolMap* symbols,
                                      u32* result_count) {
    /* OPENSPM_M14_LLVM_LINK_CONTINUATION_ENTRIES
     * A linked native callee can cold-escape through the OpenSPM dispatcher,
     * then resume at BL/BCL + 4. The LLVM emitter also side-exits at MTMSR + 4
     * when MSR[EE] transitions 0 -> 1. Keep both continuation classes legal as
     * fresh native entries without changing the global chunk/range layout. */
    size_t capacity = 1u + (symbols ? symbols->count : 0u);
    for (u32 i = 0; i < section_count; i++)
        capacity += 2u * (sections[i].size / 4u);
'''

COLD_ENTRY_OLD_BRANCH = '''            if ((inst.op == PPC_OP_B || inst.op == PPC_OP_BC) &&
                llvm_code_address(sections, section_count, inst.branch_target))
                points[count++] = inst.branch_target;
'''
COLD_ENTRY_NEW_BRANCH = '''            if ((inst.op == PPC_OP_B || inst.op == PPC_OP_BC) &&
                llvm_code_address(sections, section_count, inst.branch_target)) {
                points[count++] = inst.branch_target;
                if (inst.lk &&
                    llvm_code_address(sections, section_count, address + 4u))
                    points[count++] = address + 4u;
            }
            if (inst.op == PPC_OP_MTMSR &&
                llvm_code_address(sections, section_count, address + 4u))
                points[count++] = address + 4u;
'''

OLD = '''  Type *pointer = PointerType::getUnqual(context_);
  Value *function = loadOffset(pointer, offsetof(CPUState, external_write));
  BasicBlock *call = BasicBlock::Create(context_, "write_external", function_);
  BasicBlock *done =
      BasicBlock::Create(context_, "write_slow_done", function_);
  builder_.CreateCondBr(builder_.CreateIsNotNull(function), call, done);
'''

NEW = '''  Type *pointer = PointerType::getUnqual(context_);
  Type *i8 = Type::getInt8Ty(context_);
  Type *i32 = Type::getInt32Ty(context_);
  Type *i64 = Type::getInt64Ty(context_);

  // OPENSPM_M14_LLVM_WPAR_INLINE_GATHER
  // The exported pointer globals below alias dev16's exact private gather
  // storage. Ordinary WPAR stores can therefore remain entirely in generated
  // native code; only a completed line crosses back to the OpenSPM bridge.
  Value *openspmHavePtrGlobal = module_.getOrInsertGlobal(
      "openspm_m14_llvm_wg_have_fifo_ptr", pointer);
  Value *openspmHavePtr = builder_.CreateLoad(pointer, openspmHavePtrGlobal);
  Value *openspmHave = builder_.CreateLoad(i32, openspmHavePtr);
  Value *openspmInWindow = builder_.CreateAnd(
      builder_.CreateICmpUGE(address, builder_.getInt32(0xCC008000u)),
      builder_.CreateICmpULT(address, builder_.getInt32(0xCC008020u)));
  Value *openspmFast = builder_.CreateAnd(
      openspmInWindow,
      builder_.CreateICmpNE(openspmHave, builder_.getInt32(0)));

  BasicBlock *openspmWpar =
      BasicBlock::Create(context_, "openspm_wpar_inline", function_);
  BasicBlock *openspmAppend =
      BasicBlock::Create(context_, "openspm_wpar_append", function_);
  BasicBlock *openspmOverflow =
      BasicBlock::Create(context_, "openspm_wpar_overflow", function_);
  BasicBlock *openspmFlush =
      BasicBlock::Create(context_, "openspm_wpar_flush", function_);
  BasicBlock *openspmFailed =
      BasicBlock::Create(context_, "openspm_wpar_failed", function_);
  BasicBlock *openspmGeneric =
      BasicBlock::Create(context_, "openspm_wpar_generic", function_);
  BasicBlock *done =
      BasicBlock::Create(context_, "write_slow_done", function_);
  builder_.CreateCondBr(openspmFast, openspmWpar, openspmGeneric,
                        MDBuilder(context_).createBranchWeights(2000, 1));

  builder_.SetInsertPoint(openspmWpar);
  Value *openspmSizePtrGlobal = module_.getOrInsertGlobal(
      "openspm_m14_llvm_wg_size_ptr", pointer);
  Value *openspmSizePtr = builder_.CreateLoad(pointer, openspmSizePtrGlobal);
  Value *openspmSize = builder_.CreateLoad(i32, openspmSizePtr);
  /* Define the widened store value before the append/overflow split so it
     dominates both successors. */
  Value *openspmValue64 = builder_.CreateZExtOrTrunc(value, i64);
  Value *openspmFits = builder_.CreateICmpULE(
      openspmSize, builder_.getInt32(32u - width));
  builder_.CreateCondBr(openspmFits, openspmAppend, openspmOverflow,
                        MDBuilder(context_).createBranchWeights(2000, 1));

  builder_.SetInsertPoint(openspmAppend);
  Value *openspmBytesPtrGlobal = module_.getOrInsertGlobal(
      "openspm_m14_llvm_wg_bytes_ptr", pointer);
  Value *openspmBytes = builder_.CreateLoad(pointer, openspmBytesPtrGlobal);
  for (u32 openspmByte = 0; openspmByte < width; openspmByte++) {
    const u32 openspmShift = (width - 1u - openspmByte) * 8u;
    Value *openspmPart = openspmValue64;
    if (openspmShift != 0u)
      openspmPart = builder_.CreateLShr(openspmPart,
                                        builder_.getInt64(openspmShift));
    openspmPart = builder_.CreateTrunc(openspmPart, i8);
    Value *openspmIndex = builder_.CreateAdd(
        openspmSize, builder_.getInt32(openspmByte));
    builder_.CreateStore(
        openspmPart,
        builder_.CreateInBoundsGEP(i8, openspmBytes, openspmIndex));
  }
  Value *openspmPendingPtrGlobal = module_.getOrInsertGlobal(
      "openspm_m14_llvm_wg_pending_ptr", pointer);
  Value *openspmPendingPtr =
      builder_.CreateLoad(pointer, openspmPendingPtrGlobal);
  Value *openspmPending = builder_.CreateLoad(i32, openspmPendingPtr);
  builder_.CreateStore(builder_.CreateAdd(openspmPending, builder_.getInt32(1)),
                       openspmPendingPtr);
  Value *openspmNewSize =
      builder_.CreateAdd(openspmSize, builder_.getInt32(width));
  builder_.CreateStore(openspmNewSize, openspmSizePtr);
  builder_.CreateCondBr(
      builder_.CreateICmpEQ(openspmNewSize, builder_.getInt32(32)),
      openspmFlush, done, MDBuilder(context_).createBranchWeights(1, 7));

  builder_.SetInsertPoint(openspmOverflow);
  auto openspmSlow = module_.getOrInsertFunction(
      "openspm_m14_try_wg_write_llvm",
      FunctionType::get(i32,
                        {pointer, i32, i32, i64, i8}, false));
  CallInst *openspmSlowResult = builder_.CreateCall(
      openspmSlow,
      {ctx_, builder_.getInt32(current_pc_), address, openspmValue64,
       builder_.getInt8(width)});
  openspmSlowResult->addFnAttr(Attribute::NoUnwind);
  builder_.CreateCondBr(
      builder_.CreateICmpEQ(openspmSlowResult, builder_.getInt32(1)),
      done, openspmFailed);

  builder_.SetInsertPoint(openspmFlush);
  auto openspmFlushFn = module_.getOrInsertFunction(
      "openspm_m14_flush_wg_llvm",
      FunctionType::get(i32, {pointer, i32}, false));
  CallInst *openspmFlushResult = builder_.CreateCall(
      openspmFlushFn, {ctx_, builder_.getInt32(current_pc_)});
  openspmFlushResult->addFnAttr(Attribute::NoUnwind);
  builder_.CreateCondBr(
      builder_.CreateICmpEQ(openspmFlushResult, builder_.getInt32(1)),
      done, openspmFailed);

  builder_.SetInsertPoint(openspmFailed);
  materialize(current_pc_);
  returnFromBody();

  builder_.SetInsertPoint(openspmGeneric);
  Value *function = loadOffset(pointer, offsetof(CPUState, external_write));
  BasicBlock *call = BasicBlock::Create(context_, "write_external", function_);
  builder_.CreateCondBr(builder_.CreateIsNotNull(function), call, done);
'''


def _has_stock_popcount_call(text: str) -> bool:
    '''Reject the executable builtin call without being fooled by comments/docs.'''
    return "__builtin_popcountll(" in text


def _patch_popcount(source_root: Path, check_only: bool) -> str:
    path = source_root / POPCOUNT_TARGET
    text = path.read_text(encoding="utf-8")
    if POPCOUNT_SIGNATURE in text:
        required = [
            "u32 portablePopcount64(u64 value)",
            "value &= value - 1u;",
            "count += portablePopcount64(first[word] | (second ? second[word] : 0u));",
        ]
        missing = [item for item in required if item not in text]
        if missing or _has_stock_popcount_call(text):
            raise RuntimeError("incomplete MSVC popcount portability patch")
        return "already-applied"
    if text.count(POPCOUNT_OLD_NAMESPACE) != 1 or text.count(POPCOUNT_OLD_CALL) != 1:
        raise RuntimeError(
            f"{POPCOUNT_TARGET}: expected pinned __builtin_popcountll shape exactly once; "
            "refusing to patch an unknown DolRecomp revision"
        )
    patched = text.replace(POPCOUNT_OLD_NAMESPACE, POPCOUNT_NEW_NAMESPACE, 1)
    patched = patched.replace(POPCOUNT_OLD_CALL, POPCOUNT_NEW_CALL, 1)
    if _has_stock_popcount_call(patched):
        raise RuntimeError("MSVC portability transform left executable builtin popcount call behind")
    if not check_only:
        path.write_text(patched, encoding="utf-8", newline="\n")
    return "verified" if check_only else "applied"


def _patch_windows_progress(source_root: Path, check_only: bool) -> str:
    path = source_root / PROGRESS_TARGET
    text = path.read_text(encoding="utf-8")
    if PROGRESS_SIGNATURE in text:
        required = [
            "Reusing cached LLVM object %s",
            "Emitted LLVM object %s",
            "progress below reflects completed cache reuse/emission",
        ]
        missing = [item for item in required if item not in text]
        if missing or PROGRESS_OLD_WINDOWS_LOOP in text:
            raise RuntimeError("incomplete Windows LLVM progress patch")
        return "already-applied"
    anchors = (
        (PROGRESS_OLD_REUSE, PROGRESS_NEW_REUSE, "reuse"),
        (PROGRESS_OLD_RETURN, PROGRESS_NEW_RETURN, "completion"),
        (PROGRESS_OLD_WINDOWS_LOOP, PROGRESS_NEW_WINDOWS_LOOP, "worker-start"),
    )
    patched = text
    for old, new, label in anchors:
        if patched.count(old) != 1:
            raise RuntimeError(
                f"{PROGRESS_TARGET}: expected pinned Windows LLVM {label} progress anchor exactly once; "
                "refusing to patch an unknown DolRecomp revision"
            )
        patched = patched.replace(old, new, 1)
    if not check_only:
        path.write_text(patched, encoding="utf-8", newline="\n")
    return "verified" if check_only else "applied"


def _patch_link_continuation_entries(source_root: Path, check_only: bool) -> str:
    path = source_root / PROGRESS_TARGET
    text = path.read_text(encoding="utf-8")
    if COLD_ENTRY_SIGNATURE in text:
        required = [
            "capacity += 2u * (sections[i].size / 4u);",
            "if (inst.lk &&",
            "inst.op == PPC_OP_MTMSR",
            "points[count++] = address + 4u;",
        ]
        missing = [item for item in required if item not in text]
        if missing:
            raise RuntimeError(
                "incomplete LLVM resumable continuation entry patch: "
                + ", ".join(missing)
            )
        return "already-applied"
    if text.count(COLD_ENTRY_OLD_HEAD) != 1:
        raise RuntimeError(
            f"{PROGRESS_TARGET}: expected pinned collect_llvm_entry_points head exactly once; "
            "refusing to patch an unknown DolRecomp revision"
        )
    if text.count(COLD_ENTRY_OLD_BRANCH) != 1:
        raise RuntimeError(
            f"{PROGRESS_TARGET}: expected pinned linked-branch entry collector exactly once; "
            "refusing to patch an unknown DolRecomp revision"
        )
    patched = text.replace(COLD_ENTRY_OLD_HEAD, COLD_ENTRY_NEW_HEAD, 1)
    patched = patched.replace(COLD_ENTRY_OLD_BRANCH, COLD_ENTRY_NEW_BRANCH, 1)
    if not check_only:
        path.write_text(patched, encoding="utf-8", newline="\n")
    return "verified" if check_only else "applied"


def transform(source_root: Path, check_only: bool = False) -> str:
    popcount_result = _patch_popcount(source_root, check_only)
    progress_result = _patch_windows_progress(source_root, check_only)
    cold_entry_result = _patch_link_continuation_entries(source_root, check_only)
    path = source_root / TARGET
    text = path.read_text(encoding="utf-8")
    if SIGNATURE in text:
        required = [
            '"openspm_m14_llvm_wg_bytes_ptr"',
            '"openspm_m14_llvm_wg_size_ptr"',
            '"openspm_m14_llvm_wg_pending_ptr"',
            '"openspm_m14_flush_wg_llvm"',
            '"openspm_m14_try_wg_write_llvm"',
            "Value *openspmValue64 = builder_.CreateZExtOrTrunc(value, i64);",
            "for (u32 openspmByte = 0; openspmByte < width; openspmByte++)",
            "materialize(current_pc_);",
        ]
        missing = [item for item in required if item not in text]
        if missing:
            raise RuntimeError("incomplete LLVM inline WPAR patch: " + ", ".join(missing))
        value_pos = text.find("Value *openspmValue64")
        split_pos = text.find("builder_.CreateCondBr(openspmFits")
        if value_pos < 0 or split_pos < 0 or value_pos > split_pos:
            raise RuntimeError("LLVM inline WPAR patch has invalid SSA value placement")
        return (
            f"wpar=already-applied msvc-popcount={popcount_result} "
            f"windows-progress={progress_result} cold-entries={cold_entry_result}"
        )
    if "OPENSPM_M14_LLVM_WPAR_FASTPATH" in text:
        raise RuntimeError(
            "older OpenSPM per-store LLVM WPAR patch already present; reset the pinned "
            "DolRecomp source before applying the inline-gather revision"
        )
    count = text.count(OLD)
    if count != 1:
        raise RuntimeError(
            f"{TARGET}: expected stock externalWrite anchor exactly once, found {count}; "
            "refusing to patch an unknown DolRecomp revision"
        )
    patched = text.replace(OLD, NEW, 1)
    if not check_only:
        path.write_text(patched, encoding="utf-8", newline="\n")
    wpar_result = "verified" if check_only else "applied"
    return (
        f"wpar={wpar_result} msvc-popcount={popcount_result} "
        f"windows-progress={progress_result} cold-entries={cold_entry_result}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_root", type=Path)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    try:
        result = transform(args.source_root.resolve(), args.check_only)
    except (OSError, RuntimeError) as exc:
        print(f"error: OpenSPM LLVM private codegen patch failed: {exc}", file=sys.stderr)
        return 2
    print(f"OpenSPM LLVM private codegen patch {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
