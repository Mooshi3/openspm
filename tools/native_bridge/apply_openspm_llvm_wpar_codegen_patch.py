#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Adapts DolRecomp (GPL-3.0) and carries parts of its source; see LICENSE.
'''Compose OpenSPM's pinned DolRecomp LLVM patches, including restart-safe entries.

The retained base transform owns the existing inline WPAR, MSVC popcount,
Windows progress, linked-call continuation and post-MTMSR entry repairs. This
front-end adds three architectural fixes on top:

* every decoded FPU / paired-single instruction is a legal cold entry because
  an FP-unavailable exception saves the faulting CIA in SRR0 and RFI retries it;
* every native-ABI indirect transfer passes the same soft-budget guard already
  used by direct cross-function transfers. This prevents bctr/bctrl chains from
  running indefinitely without returning control to OpenSPM for VI, scheduler,
  GX-finish and other asynchronous semantic work;
* legacy x86 SJLJ native entries use a 32768-cycle yield quantum instead of the
  generic 256-cycle guard; OpenSPM already owns the outer 200k-cycle safepoint
  loop, so the old quantum caused millions of needless public-dispatch exits;
* legacy LLVM entries check OpenSPM's existing positive-only native-region
  admission cache in generated code before calling ppc_native_region_available.
  Cache misses keep the exact old fail-closed C interception path, while hot
  positive hits avoid billions of C transitions seen in playable dev62 runs.
'''
from __future__ import annotations

import argparse
from pathlib import Path
import sys

import _apply_openspm_llvm_codegen_patch_base as _base
import apply_llvm_scene_profile as _scene_profile

# Keep the historical source-contract probes meaningful while the implementation
# is retained byte-for-byte in the private base module. We actively verify these
# markers before delegating rather than carrying inert compatibility comments.
BASE_SOURCE_CONTRACT_MARKERS = (
    "OPENSPM_M14_LLVM_WPAR_INLINE_GATHER",
    '"openspm_m14_llvm_wg_bytes_ptr"',
    '"openspm_m14_flush_wg_llvm"',
    "Value *openspmValue64 = builder_.CreateZExtOrTrunc(value, i64);",
    "materialize(current_pc_);",
)

FPU_RESUME_SIGNATURE = "OPENSPM_M14_LLVM_FPU_EXCEPTION_RESUME_ENTRIES"
PIPELINE_TARGET = Path("src") / "app" / "pipeline.c"

# dev98: OpenSPM's conservative callback census must make these addresses legal
# native entry points, but they are not semantic function/chunk boundaries.
# DolRecomp deliberately has separate entry-point and partition-point lists.
# Prior to dev98, putting ~8K generic dev57 callbacks (plus the dev97 HP-zero
# interval) in the MAP fed them into both lists, shredding 128-instruction LLVM
# chunks and defeating native ABI / register-allocation locality.
ENTRY_ONLY_SIGNATURE = "OPENSPM_M14_LLVM_ENTRY_ONLY_SYNTHETIC_COLD_ENTRIES"
ENTRY_ONLY_PREFIXES = (
    "openspm_dev57_addr_taken_",
    "openspm_dev97_hp0_motion24_interval_",
)

# dev107: after Game Over was re-proven independently, generic dev57 callback
# census rows can again be legal entries without forcing chunk boundaries. New
# exact direct-BL source rows use the same entry-only treatment. dev97 HP-zero
# rows deliberately remain real partitions.
DEV107_ENTRY_ONLY_SIGNATURE = "OPENSPM_M14_LLVM_DEV107_ENTRY_ONLY_PERF_ENTRIES"
DEV107_ENTRY_ONLY_PREFIXES = (
    "openspm_dev57_addr_taken_",
    "openspm_dev107_direct_bl_source_",
)

FPU_RESUME_OLD = '''            if (inst.op == PPC_OP_MTMSR &&
                llvm_code_address(sections, section_count, address + 4u))
                points[count++] = address + 4u;
'''

FPU_RESUME_NEW = FPU_RESUME_OLD + '''            /* OPENSPM_M14_LLVM_FPU_EXCEPTION_RESUME_ENTRIES
             * FP-unavailable is restartable: SRR0 is the faulting CIA and RFI
             * returns to that same instruction after the Wii handler enables FP.
             * Make every decoder-recognized FPU/paired-single CIA a legal cold
             * entry so architectural exception restart cannot fall into the
             * LLVM entry-miss/fallback loop. */
            if (ppc_op_uses_fpu(inst.op) &&
                llvm_code_address(sections, section_count, address))
                points[count++] = address;
'''

INDIRECT_BUDGET_SIGNATURE = "OPENSPM_M14_LLVM_INDIRECT_BUDGET_GUARD"
EMITTER_HEADER_TARGET = Path("src") / "backend" / "llvm" / "emitter.h"
TIMING_TARGET = Path("src") / "backend" / "llvm" / "timing.cpp"
CONTROL_FLOW_TARGET = Path("src") / "backend" / "llvm" / "control_flow.cpp"

EMITTER_BUDGET_DECL_OLD = "  void emitBudgetGuard(u32 pc);\n"
EMITTER_BUDGET_DECL_NEW = (
    EMITTER_BUDGET_DECL_OLD
    + "  void emitBudgetGuard(llvm::Value *pc);\n"
)

TIMING_DYNAMIC_GUARD = '''
/* OPENSPM_M14_LLVM_INDIRECT_BUDGET_GUARD
 * Direct native cross-function transfers already call emitBudgetGuard(target)
 * before entering the callee.  Indirect bctr/bctrl transfers carry a runtime
 * target Value, so give them the same scheduling contract without forcing the
 * target through a compile-time address.  If the soft budget is exhausted,
 * materialize the already-computed branch target and return to the OpenSPM
 * runtime; otherwise continue the native transfer unchanged. */
void FunctionEmitter::emitBudgetGuard(Value *pc) {
  Value *cycles = builder_.CreateAdd(
      builder_.CreateLoad(Type::getInt64Ty(context_), guard_cycles_local_),
      builder_.CreateLoad(Type::getInt64Ty(context_), cycles_));
  Value *overCycles = builder_.CreateICmpUGE(
      cycles, modern_runtime_ ? builder_.CreateLoad(Type::getInt64Ty(context_),
                                                    builder_.CreateStructGEP(
                                                        chainType(), chain_, 4))
                              : static_cast<Value *>(ConstantInt::get(
                                    Type::getInt64Ty(context_), 256)));
  Value *exhausted = overCycles;
  if (!native_abi_ || !cold_escapes_) {
    Value *steps =
        builder_.CreateLoad(Type::getInt64Ty(context_), guard_steps_);
    Value *nextSteps = builder_.CreateAdd(
        steps, ConstantInt::get(Type::getInt64Ty(context_), 1));
    builder_.CreateStore(nextSteps, guard_steps_);
    Value *overSteps = builder_.CreateICmpUGE(
        nextSteps, ConstantInt::get(Type::getInt64Ty(context_), 2048));
    exhausted = builder_.CreateOr(overCycles, overSteps);
  }
  BasicBlock *run = BasicBlock::Create(context_, "indirect_budget_run", function_);
  BasicBlock *exit = BasicBlock::Create(context_, "indirect_budget_exit", function_);
  builder_.CreateCondBr(exhausted, exit, run);
  builder_.SetInsertPoint(exit);
  if (modern_runtime_)
    builder_.CreateStore(builder_.getInt32(0),
                         builder_.CreateStructGEP(chainType(), chain_, 7));
  materialize(pc);
  returnFromBody();
  builder_.SetInsertPoint(run);
}
'''

CONTROL_FLOW_INDIRECT_OLD = '''    if (native_abi_) {
      if (!cold_escapes_)
        settleCycles();
      returnNative(target);
'''

CONTROL_FLOW_INDIRECT_NEW = '''    if (native_abi_) {
      /* OPENSPM_M14_LLVM_INDIRECT_BUDGET_GUARD
       * Keep computed transfers on the same soft-yield contract as direct
       * native calls.  The architectural target and LR/CTR state have already
       * been computed by the DolIR block, so an exhausted guard can resume at
       * target without re-running the branch instruction. */
      emitBudgetGuard(target);
      if (!cold_escapes_)
        settleCycles();
      returnNative(target);
'''

# dev99: SPM's dispDraw walks a per-frame table and invokes every draw callback
# through linked indirect branches. In legacy x86 cold-escape mode DolRecomp
# currently materializes+SJLJ-escapes every computed target, even though the
# generated dispatch header already owns an exact indexed semantic/native
# dispatcher. Keep this A/B strictly inside the proven US2 dispDraw body.
DISPDRAW_NESTED_SIGNATURE = "OPENSPM_M14_LLVM_DISPDRAW_NESTED_INDIRECT"
DISPDRAW_US2_START = 0x8005B028
DISPDRAW_US2_END = 0x8005B2F4

CONTROL_FLOW_DISPDRAW_OLD = CONTROL_FLOW_INDIRECT_NEW

CONTROL_FLOW_DISPDRAW_NEW = '''    if (native_abi_) {
      /* OPENSPM_M14_LLVM_INDIRECT_BUDGET_GUARD */
      emitBudgetGuard(target);

      /* OPENSPM_M14_LLVM_DISPDRAW_NESTED_INDIRECT
       * US2 cam/disp symbols independently prove dispDraw=[0x8005B028,
       * 0x8005B2F4). Only linked computed calls whose continuation remains in
       * this LLVM body use the nested dispatcher. The bridge helper reuses
       * generated dolrecomp_call() (replacement -> host semantics -> indexed
       * native lookup) and the existing bounded recursion guard.
       *
       * materialize(target) is the same architectural boundary the old
       * returnNative(target) escape uses. If the nested call fails, raises an
       * exception, requests fallback, or returns anywhere except PC+4, return
       * to the proven outer dispatcher with that materialized CPUState. */
      if (cold_escapes_ && term.linked &&
          term.guest_pc >= 0x8005B028u && term.guest_pc < 0x8005B2F4u) {
        const u32 continuation = term.guest_pc + 4u;
        const bool local =
            continuation >= source_.guest_start &&
            continuation < source_.guest_end &&
            ((continuation - source_.guest_start) & 3u) == 0;
        const u32 continuationBlock =
            local ? (continuation - source_.guest_start) / 4u : 0u;
        if (local && continuationBlock < blocks_.size()) {
          materialize(target);
          auto nested = module_.getOrInsertFunction(
              "openspm_m14_dev99_nested_dispatch",
              FunctionType::get(
                  Type::getInt32Ty(context_),
                  {PointerType::getUnqual(context_),
                   Type::getInt32Ty(context_),
                   Type::getInt32Ty(context_)},
                  false));
          Value *handled =
              builder_.CreateCall(nested,
                                  {ctx_, target,
                                   builder_.getInt32(continuation)});
          Value *exception =
              loadOffset(Type::getInt32Ty(context_),
                         offsetof(CPUState, exception));
          Value *returnedPC =
              loadOffset(Type::getInt32Ty(context_), offsetof(CPUState, pc));
          Value *normal = builder_.CreateAnd(
              builder_.CreateICmpNE(handled, builder_.getInt32(0)),
              builder_.CreateAnd(
                  builder_.CreateICmpEQ(exception, builder_.getInt32(0)),
                  builder_.CreateICmpEQ(
                      returnedPC, builder_.getInt32(continuation))));
          BasicBlock *resume =
              BasicBlock::Create(context_, "dispdraw_nested_resume", function_);
          BasicBlock *escape =
              BasicBlock::Create(context_, "dispdraw_nested_escape", function_);
          builder_.CreateCondBr(normal, resume, escape);

          builder_.SetInsertPoint(escape);
          returnFromBody();

          builder_.SetInsertPoint(resume);
          reloadUsedState();
          builder_.CreateBr(blocks_[continuationBlock]);
          return true;
        }
      }

      if (!cold_escapes_)
        settleCycles();
      returnNative(target);
'''


# dev105: dev101's player profiler proved the hot dispDraw bctrl at 0x8005B29C
# is emitted in a legacy CPUState-ABI body. Stock DolRecomp therefore commits
# state and returns to the outer dispatcher for every callback. In crowded
# scenes this one site executes roughly 225-300 times per frame. Keep the
# optimization exact to that measured site and preserve the historical escape
# for every failed/abnormal nested dispatch.
DISPDRAW_CPUSTATE_SIGNATURE = "OPENSPM_M14_LLVM_DISPDRAW_CPUSTATE_NESTED_MULTIBLOCK"
DISPDRAW_CPUSTATE_SITE = 0x8005B29C

CONTROL_FLOW_CPUSTATE_OLD = '''    } else {
      for (u32 slot = 0; slot < DOLIR_STATE_COUNT; slot++) {
        if (dirty_[slot]) {
          auto stateSlot = static_cast<DolIRStateSlot>(slot);
          storeContext(stateSlot,
                       builder_.CreateLoad(type(dolir_state_type(stateSlot)),
                                           state_[slot]));
        }
      }
      storeContext(DOLIR_STATE_PC, target);
      Value *downcount =
          loadOffset(Type::getInt64Ty(context_), offsetof(CPUState, downcount));
      Value *cycles = builder_.CreateLoad(Type::getInt64Ty(context_), cycles_);
      builder_.CreateStore(builder_.CreateSub(downcount, cycles),
                           bytePtr(offsetof(CPUState, downcount)));
      builder_.CreateStore(builder_.getInt64(0), cycles_);
      builder_.CreateStore(builder_.getInt64(0), pending_cycles_);
      returnFromBody();
    }
'''

CONTROL_FLOW_CPUSTATE_NEW = '''    } else {
      /* OPENSPM_M14_LLVM_DISPDRAW_CPUSTATE_NESTED_MULTIBLOCK
       * dev105 keeps dev101's exact measured linked callback terminator at the
       * US2 PC 0x8005B29C. This body is CPUState ABI, explaining why dev99's
       * native-ABI-only A/B recorded zero attempts.
       *
       * materialize(target) is architecturally equivalent to the historical
       * CPUState escape below: it commits dirty guest state, writes PC=target,
       * settles cycle debt to CPUState.downcount, and clears local pending
       * cycles. In the CPUState ABI that makes downcount a negative accumulated
       * charge, not an exhausted positive budget. dev104 proved one generated
       * dispatch often stops at an intermediate chunk; the dev105 helper therefore
       * walks the existing exact replacement/semantic/native dispatcher across a
       * bounded 64 chunks / 32768 accumulated cycles until PC+4. Resume the parent only
       * after handled + no exception/fallback + exact PC+4; every other result
       * returns through the historical outer runtime with materialized state. */
      if (!modern_runtime_ && term.linked &&
          term.guest_pc == 0x8005B29Cu) {
        const u32 continuation = term.guest_pc + 4u;
        const bool local =
            continuation >= source_.guest_start &&
            continuation < source_.guest_end &&
            ((continuation - source_.guest_start) & 3u) == 0;
        const u32 continuationBlock =
            local ? (continuation - source_.guest_start) / 4u : 0u;
        if (local && continuationBlock < blocks_.size()) {
          materialize(target);
          auto nested = module_.getOrInsertFunction(
              "openspm_m14_dev105_cpustate_nested_dispatch",
              FunctionType::get(
                  Type::getInt32Ty(context_),
                  {PointerType::getUnqual(context_),
                   Type::getInt32Ty(context_),
                   Type::getInt32Ty(context_)},
                  false));
          Value *handled =
              builder_.CreateCall(nested,
                                  {ctx_, target,
                                   builder_.getInt32(continuation)});
          Value *exception =
              loadOffset(Type::getInt32Ty(context_),
                         offsetof(CPUState, exception));
          Value *returnedPC =
              loadOffset(Type::getInt32Ty(context_), offsetof(CPUState, pc));
          Value *normal = builder_.CreateAnd(
              builder_.CreateICmpNE(handled, builder_.getInt32(0)),
              builder_.CreateAnd(
                  builder_.CreateICmpEQ(exception, builder_.getInt32(0)),
                  builder_.CreateICmpEQ(
                      returnedPC, builder_.getInt32(continuation))));
          BasicBlock *resume = BasicBlock::Create(
              context_, "cpustate_dispdraw_resume", function_);
          BasicBlock *escape = BasicBlock::Create(
              context_, "cpustate_dispdraw_escape", function_);
          builder_.CreateCondBr(normal, resume, escape);

          builder_.SetInsertPoint(escape);
          returnFromBody();

          builder_.SetInsertPoint(resume);
          reloadUsedState();
          builder_.CreateBr(blocks_[continuationBlock]);
          return true;
        }
      }

      for (u32 slot = 0; slot < DOLIR_STATE_COUNT; slot++) {
        if (dirty_[slot]) {
          auto stateSlot = static_cast<DolIRStateSlot>(slot);
          storeContext(stateSlot,
                       builder_.CreateLoad(type(dolir_state_type(stateSlot)),
                                           state_[slot]));
        }
      }
      storeContext(DOLIR_STATE_PC, target);
      Value *downcount =
          loadOffset(Type::getInt64Ty(context_), offsetof(CPUState, downcount));
      Value *cycles = builder_.CreateLoad(Type::getInt64Ty(context_), cycles_);
      builder_.CreateStore(builder_.CreateSub(downcount, cycles),
                           bytePtr(offsetof(CPUState, downcount)));
      builder_.CreateStore(builder_.getInt64(0), cycles_);
      builder_.CreateStore(builder_.getInt64(0), pending_cycles_);
      returnFromBody();
    }
'''

ADMISSION_CACHE_SIGNATURE = "OPENSPM_M14_LLVM_INLINE_ADMISSION_CACHE"
EXITS_TARGET = Path("src") / "backend" / "llvm" / "exits.cpp"

LEGACY_GUARD_SIGNATURE = "OPENSPM_M14_LLVM_LEGACY_GUARD_QUANTUM_32768"
LEGACY_GUARD_OLD = """                              : static_cast<Value *>(ConstantInt::get(
                                    Type::getInt64Ty(context_), 256)));"""
LEGACY_GUARD_NEW = """                              : static_cast<Value *>(ConstantInt::get(
                                    Type::getInt64Ty(context_),
                                    (native_abi_ && cold_escapes_ &&
                                     intrinsic_escapes_) ? 32768 : 256))); /* OPENSPM_M14_LLVM_LEGACY_GUARD_QUANTUM_32768 */"""

BUDGET_EXIT_COUNTER_SIGNATURE = "OPENSPM_M14_LLVM_BUDGET_EXIT_COUNTER"
LLVM_FUNCTION_INCLUDE = "#include <llvm/IR/Function.h>\n"
LLVM_MODULE_INCLUDE = "#include <llvm/IR/Module.h>\n"
BUDGET_EXIT_SCALAR_OLD = """  builder_.SetInsertPoint(exit);
  sideExit(pc);
"""
BUDGET_EXIT_SCALAR_NEW = """  builder_.SetInsertPoint(exit);
  /* OPENSPM_M14_LLVM_BUDGET_EXIT_COUNTER
   * Observation only: count exact voluntary legacy-x86 budget exits in generated
   * code so the player log can distinguish guard churn from unrelated ordinary
   * public dispatch. Single emulated CPU thread => non-atomic increment. */
  if (!modern_runtime_ && native_abi_ && cold_escapes_ && intrinsic_escapes_) {
    Type *pointer = PointerType::getUnqual(context_);
    Type *i64 = Type::getInt64Ty(context_);
    Value *counterGlobal = module_.getOrInsertGlobal(
        "openspm_m14_llvm_budget_exit_counter_ptr", pointer);
    Value *counter = builder_.CreateLoad(pointer, counterGlobal);
    Value *oldCount = builder_.CreateLoad(i64, counter);
    builder_.CreateStore(builder_.CreateAdd(oldCount, builder_.getInt64(1)),
                         counter);
  }
  sideExit(pc);
"""
BUDGET_EXIT_DYNAMIC_OLD = """  builder_.SetInsertPoint(exit);
  if (modern_runtime_)
"""
BUDGET_EXIT_DYNAMIC_NEW = """  builder_.SetInsertPoint(exit);
  /* OPENSPM_M14_LLVM_BUDGET_EXIT_COUNTER */
  if (!modern_runtime_ && native_abi_ && cold_escapes_ && intrinsic_escapes_) {
    Type *pointer = PointerType::getUnqual(context_);
    Type *i64 = Type::getInt64Ty(context_);
    Value *counterGlobal = module_.getOrInsertGlobal(
        "openspm_m14_llvm_budget_exit_counter_ptr", pointer);
    Value *counter = builder_.CreateLoad(pointer, counterGlobal);
    Value *oldCount = builder_.CreateLoad(i64, counter);
    builder_.CreateStore(builder_.CreateAdd(oldCount, builder_.getInt64(1)),
                         counter);
  }
  if (modern_runtime_)
"""

PHASE_PROFILE_SIGNATURE = "OPENSPM_M14_LLVM_NATIVE_FRAME_PHASE_PROFILE"
INSTRUCTIONS_TARGET = Path("src") / "backend" / "llvm" / "instructions.cpp"

PHASE_PROFILE_OLD = '''    const bool useService =
        modern_runtime_ && native_abi_ && needsInterpreter(block);
    if (loop_headers_[current])
      emitBudgetGuard(block.guest_address);
'''

PHASE_PROFILE_PRE_DEV106 = '''    const bool useService =
        modern_runtime_ && native_abi_ && needsInterpreter(block);

    /* OPENSPM_M14_LLVM_NATIVE_FRAME_PHASE_PROFILE
     * These four addresses are player-local compiler seams already present in
     * the private symbol map. Emit a host-only timing call at the exact guest
     * block entry. The helper takes only a phase id; it does not receive or
     * materialize CPUState, so profiling cannot perturb guest architectural
     * state or native-ABI register residency. */
    u32 openspmPhase = 4u;
    switch (block.guest_address) {
    case 0x801A8A9Cu: openspmPhase = 0u; break; /* DEMOBeforeRender */
    case 0x801A7D1Cu: openspmPhase = 1u; break; /* spmarioDisp */
    case 0x801A7AACu: openspmPhase = 2u; break; /* spmarioMain */
    case 0x801A8B64u: openspmPhase = 3u; break; /* DEMODoneRender */
    default: break;
    }
    if (openspmPhase < 4u) {
      auto phaseType = FunctionType::get(Type::getVoidTy(context_),
                                         {Type::getInt32Ty(context_)}, false);
      FunctionCallee phaseFn = module_.getOrInsertFunction(
          "openspm_m14_note_frame_phase_native", phaseType);
      builder_.CreateCall(phaseFn, {builder_.getInt32(openspmPhase)});
    }
    if (loop_headers_[current])
      emitBudgetGuard(block.guest_address);
'''

PHASE_PROFILE_NEW = '''    const bool useService =
        modern_runtime_ && native_abi_ && needsInterpreter(block);

    /* OPENSPM_M14_LLVM_NATIVE_FRAME_PHASE_PROFILE
     * These four addresses are player-local compiler seams already present in
     * the private symbol map. Emit a host-only timing call at the exact guest
     * block entry. The helper takes only a phase id; it does not receive or
     * materialize CPUState, so profiling cannot perturb guest architectural
     * state or native-ABI register residency. */
    u32 openspmPhase = 4u;
    switch (block.guest_address) {
    case 0x801A8A9Cu: openspmPhase = 0u; break; /* DEMOBeforeRender */
    case 0x801A7D1Cu: openspmPhase = 1u; break; /* spmarioDisp */
    case 0x801A7AACu: openspmPhase = 2u; break; /* spmarioMain */
    case 0x801A8B64u: openspmPhase = 3u; break; /* DEMODoneRender */
    default: break;
    }
    if (openspmPhase < 4u) {
      auto phaseType = FunctionType::get(Type::getVoidTy(context_),
                                         {Type::getInt32Ty(context_)}, false);
      FunctionCallee phaseFn = module_.getOrInsertFunction(
          "openspm_m14_note_frame_phase_native", phaseType);
      builder_.CreateCall(phaseFn, {builder_.getInt32(openspmPhase)});
    }

    /* dev106: rare exact native-entry truth probes for the HP-zero path.
     * These two functions can execute entirely native and therefore bypass
     * host-call telemetry. The helper receives only the compile-time guest PC;
     * it does not materialize CPUState or alter guest state. */
    switch (block.guest_address) {
    case 0x801615E4u: /* onHpEquals0 */
    case 0x8017B834u: { /* seqSetSeq */
      auto deathType = FunctionType::get(Type::getVoidTy(context_),
                                         {Type::getInt32Ty(context_)}, false);
      FunctionCallee deathFn = module_.getOrInsertFunction(
          "openspm_m14_note_death_entry_native", deathType);
      builder_.CreateCall(deathFn, {builder_.getInt32(block.guest_address)});
      break;
    }
    default: break;
    }
    if (loop_headers_[current])
      emitBudgetGuard(block.guest_address);
'''

ADMISSION_ENTRY_OLD = '''  if (modern_runtime_) {
    builder_.CreateBr(query);
  } else {
    Value *hostCall = loadOffset(pointer, offsetof(CPUState, host_call));
    Value *noInterception = builder_.CreateCall(
        expect, {builder_.CreateIsNull(hostCall), builder_.getTrue()});
    builder_.CreateCondBr(noInterception, nativeEntry, query);
  }
  builder_.SetInsertPoint(query);
'''

ADMISSION_ENTRY_NEW = '''  if (modern_runtime_) {
    builder_.CreateBr(query);
  } else {
    Value *hostCall = loadOffset(pointer, offsetof(CPUState, host_call));
    Value *noInterception = builder_.CreateCall(
        expect, {builder_.CreateIsNull(hostCall), builder_.getTrue()});

    /* OPENSPM_M14_LLVM_INLINE_ADMISSION_CACHE
     * OpenSPM's dev58 bridge owns a direct-mapped positive-only cache of native
     * [start,end) admissions. Blocked and dynamic semantic ranges are never
     * inserted. Read that authoritative cache before crossing into C: a hit is
     * therefore exactly equivalent to dev58 returning true from its own cache
     * lookup, while every miss still executes ppc_native_region_available().
     *
     * The region bounds are compile-time constants for this FunctionEmitter, so
     * reproduce bridge_dev58_region_hash() here at code-generation time. The
     * generated guest path performs only three aligned i32 loads and compares.
     */
    BasicBlock *cacheCheck =
        BasicBlock::Create(context_, "openspm_admission_cache_check", function_);
    BasicBlock *cacheLookup =
        BasicBlock::Create(context_, "openspm_admission_cache_lookup", function_);
    builder_.CreateCondBr(noInterception, nativeEntry, cacheCheck);

    builder_.SetInsertPoint(cacheCheck);
    GlobalVariable *cacheGlobal =
        module_.getGlobalVariable("openspm_m14_llvm_region_cache_ptr");
    if (!cacheGlobal) {
      cacheGlobal = new GlobalVariable(
          module_, pointer, false, GlobalValue::ExternalLinkage, nullptr,
          "openspm_m14_llvm_region_cache_ptr");
    }
    Value *cacheBase = builder_.CreateLoad(pointer, cacheGlobal);
    Value *cacheUnavailable = builder_.CreateCall(
        expect, {builder_.CreateIsNull(cacheBase), builder_.getFalse()});
    builder_.CreateCondBr(cacheUnavailable, query, cacheLookup);

    builder_.SetInsertPoint(cacheLookup);
    u32 cacheValue = (source_.guest_start >> 2) ^
                     (source_.guest_end * 0x9E3779B9u);
    cacheValue ^= cacheValue >> 16;
    cacheValue *= 0x7FEB352Du;
    cacheValue ^= cacheValue >> 15;
    u32 cacheOffset = (cacheValue & 0xFFFFu) * 12u;
    Type *i8 = Type::getInt8Ty(context_);
    Type *i32 = Type::getInt32Ty(context_);
    Value *cacheEntry = builder_.CreateInBoundsGEP(
        i8, cacheBase, builder_.getInt32(cacheOffset));
    Value *cacheStart = builder_.CreateLoad(i32, cacheEntry);
    Value *cacheEnd = builder_.CreateLoad(
        i32, builder_.CreateInBoundsGEP(
                 i8, cacheEntry, builder_.getInt32(4)));
    Value *cacheValid = builder_.CreateLoad(
        i32, builder_.CreateInBoundsGEP(
                 i8, cacheEntry, builder_.getInt32(8)));
    Value *cacheHit = builder_.CreateAnd(
        builder_.CreateICmpNE(cacheValid, builder_.getInt32(0)),
        builder_.CreateAnd(
            builder_.CreateICmpEQ(
                cacheStart, builder_.getInt32(source_.guest_start)),
            builder_.CreateICmpEQ(
                cacheEnd, builder_.getInt32(source_.guest_end))));
    cacheHit = builder_.CreateCall(expect, {cacheHit, builder_.getTrue()});
    builder_.CreateCondBr(cacheHit, nativeEntry, query);
  }
  builder_.SetInsertPoint(query);
'''


def _verify_retained_base() -> None:
    text = Path(_base.__file__).read_text(encoding="utf-8")
    missing = [marker for marker in BASE_SOURCE_CONTRACT_MARKERS if marker not in text]
    if missing:
        raise RuntimeError(
            "retained OpenSPM LLVM base patch lost required source contracts: "
            + ", ".join(missing)
        )


def _verify_fpu_shape(text: str) -> None:
    required = [
        FPU_RESUME_SIGNATURE,
        "ppc_op_uses_fpu(inst.op)",
        "points[count++] = address;",
        "inst.op == PPC_OP_MTMSR",
        "if (inst.lk &&",
    ]
    missing = [item for item in required if item not in text]
    if missing:
        raise RuntimeError(
            "incomplete LLVM architectural resume-entry patch: " + ", ".join(missing)
        )


def _patch_fpu_resume_entries(source_root: Path, check_only: bool) -> str:
    path = source_root / PIPELINE_TARGET
    text = path.read_text(encoding="utf-8")
    if FPU_RESUME_SIGNATURE in text:
        _verify_fpu_shape(text)
        return "already-applied"

    if check_only:
        # On an already base-patched checkout, prove the exact upgrade anchor.
        # On a pristine pinned checkout, the retained base transform's own
        # check-only validation proves that it can first create this anchor.
        if _base.COLD_ENTRY_SIGNATURE in text and text.count(FPU_RESUME_OLD) != 1:
            raise RuntimeError(
                f"{PIPELINE_TARGET}: expected post-MTMSR resume anchor exactly once; "
                "refusing to upgrade an unknown DolRecomp source shape"
            )
        return "verified"

    if _base.COLD_ENTRY_SIGNATURE not in text:
        raise RuntimeError(
            f"{PIPELINE_TARGET}: retained base transform did not install its cold-entry patch"
        )
    if text.count(FPU_RESUME_OLD) != 1:
        raise RuntimeError(
            f"{PIPELINE_TARGET}: expected post-MTMSR resume anchor exactly once; "
            "refusing to upgrade an unknown DolRecomp source shape"
        )
    patched = text.replace(FPU_RESUME_OLD, FPU_RESUME_NEW, 1)
    _verify_fpu_shape(patched)
    path.write_text(patched, encoding="utf-8", newline="\n")
    return "applied"


def _verify_entry_only_cold_entry_shape(text: str) -> None:
    required = [
        ENTRY_ONLY_SIGNATURE,
        "static int openspm_m14_symbol_is_entry_only(const char* name)",
        '"openspm_dev57_addr_taken_"',
        '"openspm_dev97_hp0_motion24_interval_"',
        "if (openspm_m14_symbol_is_entry_only(name)) {",
        "openspmEntryOnlySkipped++;",
        "OpenSPM entry-only synthetic symbols:",
        "u32 address = symbols->symbols[i].address;",
    ]
    missing = [item for item in required if item not in text]
    if missing:
        raise RuntimeError(
            "incomplete LLVM entry-only synthetic cold-entry patch: "
            + ", ".join(missing)
        )

    # The legal-entry collector MUST remain broad. dev98 is specifically about
    # decoupling entry legality from chunk partitioning, not removing callbacks.
    entry_start = text.find("static u32* collect_llvm_entry_points(")
    partition_start = text.find("static u32* collect_llvm_partition_points(")
    if entry_start < 0 or partition_start < 0:
        raise RuntimeError("LLVM entry/partition collector is missing")
    entry_end = text.find("\nstatic ", entry_start + 1)
    if entry_end < 0:
        raise RuntimeError("LLVM entry-point collector tail shape changed")
    entry_text = text[entry_start:entry_end]
    for prefix in ENTRY_ONLY_PREFIXES:
        if prefix in entry_text:
            raise RuntimeError(
                "dev98 entry-only filter leaked into collect_llvm_entry_points; "
                f"callback prefix {prefix} would no longer be a legal cold entry"
            )


def _patch_entry_only_synthetic_cold_entries(
    source_root: Path, check_only: bool
) -> str:
    path = source_root / PIPELINE_TARGET
    text = path.read_text(encoding="utf-8")
    if ENTRY_ONLY_SIGNATURE in text:
        _verify_entry_only_cold_entry_shape(text)
        return "already-applied"

    function_anchor = "static u32* collect_llvm_partition_points("
    partition_start = text.find(function_anchor)
    if partition_start < 0:
        raise RuntimeError(
            f"{PIPELINE_TARGET}: LLVM partition-point collector is missing"
        )

    symbol_old = '''    if (symbols) {
        for (u32 i = 0; i < symbols->count; i++) {
            u32 address = symbols->symbols[i].address;
            if (llvm_code_address(sections, section_count, address))
                points[count++] = address;
        }
    }
'''
    symbol_at = text.find(symbol_old, partition_start)
    if symbol_at < 0:
        raise RuntimeError(
            f"{PIPELINE_TARGET}: expected partition symbol loop exactly in "
            "collect_llvm_partition_points; refusing to guess"
        )
    next_function = text.find("\nstatic ", partition_start + len(function_anchor))
    if next_function >= 0 and symbol_at >= next_function:
        raise RuntimeError(
            f"{PIPELINE_TARGET}: partition symbol loop resolved outside collector"
        )

    helper = '''/* OPENSPM_M14_LLVM_ENTRY_ONLY_SYNTHETIC_COLD_ENTRIES
 * OpenSPM's generic address-taken callback census and the dev97 HP-zero
 * callback interval are conservative *entry legality* surfaces, not semantic
 * function boundaries. Keep them in collect_llvm_entry_points(), where LLVM
 * marks their interior PCs as region leaders/cold entries, but do not let
 * thousands of synthetic names fragment the 128-instruction chunk plan.
 *
 * Exact measured restart entries, semantic host seams, scheduler entries and
 * dev79 BL sources use different names and remain normal partition points. */
static int openspm_m14_symbol_is_entry_only(const char* name) {
    if (!name)
        return 0;
    return strncmp(name, "openspm_dev57_addr_taken_",
                   sizeof("openspm_dev57_addr_taken_") - 1u) == 0 ||
           strncmp(name, "openspm_dev97_hp0_motion24_interval_",
                   sizeof("openspm_dev97_hp0_motion24_interval_") - 1u) == 0;
}

'''
    symbol_new = '''    if (symbols) {
        u32 openspmEntryOnlySkipped = 0;
        for (u32 i = 0; i < symbols->count; i++) {
            u32 address = symbols->symbols[i].address;
            const char* name = symbols->symbols[i].name;
            if (openspm_m14_symbol_is_entry_only(name)) {
                openspmEntryOnlySkipped++;
                continue;
            }
            if (llvm_code_address(sections, section_count, address))
                points[count++] = address;
        }
        if (openspmEntryOnlySkipped)
            printf("  OpenSPM entry-only synthetic symbols: %u legal cold entries excluded from chunk partitions\\n",
                   openspmEntryOnlySkipped);
    }
'''

    if check_only:
        return "verified"

    patched = text[:partition_start] + helper + text[partition_start:]
    shifted_partition = partition_start + len(helper)
    symbol_at = patched.find(symbol_old, shifted_partition)
    if symbol_at < 0:
        raise RuntimeError(
            f"{PIPELINE_TARGET}: partition symbol loop disappeared during dev98 patch"
        )
    patched = patched[:symbol_at] + symbol_new + patched[symbol_at + len(symbol_old):]
    _verify_entry_only_cold_entry_shape(patched)
    path.write_text(patched, encoding="utf-8", newline="\n")
    return "applied"


def _verify_indirect_budget_shape(source_root: Path) -> None:
    header = (source_root / EMITTER_HEADER_TARGET).read_text(encoding="utf-8")
    timing = (source_root / TIMING_TARGET).read_text(encoding="utf-8")
    control = (source_root / CONTROL_FLOW_TARGET).read_text(encoding="utf-8")
    required = [
        (header, "void emitBudgetGuard(llvm::Value *pc);", EMITTER_HEADER_TARGET),
        (timing, INDIRECT_BUDGET_SIGNATURE, TIMING_TARGET),
        (timing, "void FunctionEmitter::emitBudgetGuard(Value *pc)", TIMING_TARGET),
        (timing, "materialize(pc);", TIMING_TARGET),
        (control, INDIRECT_BUDGET_SIGNATURE, CONTROL_FLOW_TARGET),
        (control, "emitBudgetGuard(target);", CONTROL_FLOW_TARGET),
    ]
    missing = [f"{path}:{needle}" for text, needle, path in required if needle not in text]
    if missing:
        raise RuntimeError(
            "incomplete LLVM indirect budget-guard patch: " + ", ".join(missing)
        )


def _patch_indirect_budget_guard(source_root: Path, check_only: bool) -> str:
    header_path = source_root / EMITTER_HEADER_TARGET
    timing_path = source_root / TIMING_TARGET
    control_path = source_root / CONTROL_FLOW_TARGET
    header = header_path.read_text(encoding="utf-8")
    timing = timing_path.read_text(encoding="utf-8")
    control = control_path.read_text(encoding="utf-8")

    already = (
        INDIRECT_BUDGET_SIGNATURE in timing
        and INDIRECT_BUDGET_SIGNATURE in control
        and "void emitBudgetGuard(llvm::Value *pc);" in header
    )
    if already:
        _verify_indirect_budget_shape(source_root)
        return "already-applied"

    if header.count(EMITTER_BUDGET_DECL_OLD) != 1:
        raise RuntimeError(
            f"{EMITTER_HEADER_TARGET}: expected scalar budget-guard declaration exactly once"
        )
    if "void FunctionEmitter::emitBudgetGuard(u32 pc)" not in timing:
        raise RuntimeError(f"{TIMING_TARGET}: scalar budget guard is missing")
    if timing.count("\n} // namespace dolllvm\n") != 1:
        raise RuntimeError(f"{TIMING_TARGET}: namespace tail shape changed")
    if control.count(CONTROL_FLOW_INDIRECT_OLD) != 1:
        raise RuntimeError(
            f"{CONTROL_FLOW_TARGET}: native indirect terminator shape changed; refusing to guess"
        )

    if check_only:
        return "verified"

    header = header.replace(EMITTER_BUDGET_DECL_OLD, EMITTER_BUDGET_DECL_NEW, 1)
    timing = timing.replace(
        "\n} // namespace dolllvm\n",
        TIMING_DYNAMIC_GUARD + "\n} // namespace dolllvm\n",
        1,
    )
    control = control.replace(CONTROL_FLOW_INDIRECT_OLD, CONTROL_FLOW_INDIRECT_NEW, 1)
    header_path.write_text(header, encoding="utf-8", newline="\n")
    timing_path.write_text(timing, encoding="utf-8", newline="\n")
    control_path.write_text(control, encoding="utf-8", newline="\n")
    _verify_indirect_budget_shape(source_root)
    return "applied"


def _verify_dispdraw_nested_indirect_shape(text: str) -> None:
    required = [
        DISPDRAW_NESTED_SIGNATURE,
        "term.guest_pc >= 0x8005B028u",
        "term.guest_pc < 0x8005B2F4u",
        "term.linked",
        '"openspm_m14_dev99_nested_dispatch"',
        "materialize(target);",
        "builder_.getInt32(continuation)",
        "dispdraw_nested_resume",
        "dispdraw_nested_escape",
        "reloadUsedState();",
        "builder_.CreateBr(blocks_[continuationBlock]);",
    ]
    missing = [item for item in required if item not in text]
    if missing:
        raise RuntimeError(
            "incomplete dev99 dispDraw nested-indirect patch: "
            + ", ".join(missing)
        )


def _patch_dispdraw_nested_indirect(
    source_root: Path, check_only: bool
) -> str:
    path = source_root / CONTROL_FLOW_TARGET
    text = path.read_text(encoding="utf-8")
    if DISPDRAW_NESTED_SIGNATURE in text:
        _verify_dispdraw_nested_indirect_shape(text)
        return "already-applied"

    # check-only often runs on the pristine pinned checkout before the preceding
    # dev85 transform has physically installed CONTROL_FLOW_INDIRECT_NEW.
    if CONTROL_FLOW_DISPDRAW_OLD not in text:
        if check_only and CONTROL_FLOW_INDIRECT_OLD in text:
            return "verified-after-dev85"
        raise RuntimeError(
            f"{CONTROL_FLOW_TARGET}: dev99 expected the dev85 indirect-budget "
            "shape exactly once"
        )
    if text.count(CONTROL_FLOW_DISPDRAW_OLD) != 1:
        raise RuntimeError(
            f"{CONTROL_FLOW_TARGET}: dev99 indirect anchor count changed: "
            f"{text.count(CONTROL_FLOW_DISPDRAW_OLD)}"
        )
    if check_only:
        return "verified"

    patched = text.replace(
        CONTROL_FLOW_DISPDRAW_OLD, CONTROL_FLOW_DISPDRAW_NEW, 1
    )
    _verify_dispdraw_nested_indirect_shape(patched)
    path.write_text(patched, encoding="utf-8", newline="\n")
    return "applied"



def _verify_dispdraw_cpustate_shape(text: str) -> None:
    required = [
        DISPDRAW_CPUSTATE_SIGNATURE,
        "term.guest_pc == 0x8005B29Cu",
        '"openspm_m14_dev105_cpustate_nested_dispatch"',
        '"cpustate_dispdraw_resume"',
        '"cpustate_dispdraw_escape"',
        "materialize(target);",
        "reloadUsedState();",
        "builder_.CreateBr(blocks_[continuationBlock]);",
    ]
    missing = [item for item in required if item not in text]
    if missing:
        raise RuntimeError(
            "incomplete dev105 CPUState dispDraw nesting patch: "
            + ", ".join(missing)
        )


def _patch_dispdraw_cpustate_nested(
    source_root: Path, check_only: bool
) -> str:
    path = source_root / CONTROL_FLOW_TARGET
    text = path.read_text(encoding="utf-8")
    if DISPDRAW_CPUSTATE_SIGNATURE in text:
        _verify_dispdraw_cpustate_shape(text)
        return "already-applied"
    if text.count(CONTROL_FLOW_CPUSTATE_OLD) != 1:
        raise RuntimeError(
            f"{CONTROL_FLOW_TARGET}: dev105 expected exact CPUState indirect "
            f"escape once, found {text.count(CONTROL_FLOW_CPUSTATE_OLD)}"
        )
    if check_only:
        return "verified"
    patched = text.replace(
        CONTROL_FLOW_CPUSTATE_OLD, CONTROL_FLOW_CPUSTATE_NEW, 1
    )
    _verify_dispdraw_cpustate_shape(patched)
    path.write_text(patched, encoding="utf-8", newline="\n")
    return "applied"


def _verify_legacy_guard_quantum_shape(text: str) -> None:
    if text.count(LEGACY_GUARD_SIGNATURE) != 2:
        raise RuntimeError(
            "LLVM legacy guard quantum patch must cover scalar and dynamic guards exactly twice"
        )
    if text.count("intrinsic_escapes_) ? 32768 : 256") != 2:
        raise RuntimeError(
            "LLVM legacy guard quantum patch lost its x86 SJLJ-only 32768-cycle scope"
        )


def _patch_legacy_guard_quantum(source_root: Path, check_only: bool) -> str:
    path = source_root / TIMING_TARGET
    text = path.read_text(encoding="utf-8")
    if LEGACY_GUARD_SIGNATURE in text:
        _verify_legacy_guard_quantum_shape(text)
        return "already-applied"

    # check-only runs before the indirect Value* overload is physically inserted,
    # so a pristine transformed base has one scalar anchor. A real transform has
    # both scalar and dynamic guards and must patch both or fail closed.
    expected = 1 if check_only else 2
    if text.count(LEGACY_GUARD_OLD) != expected:
        raise RuntimeError(
            f"{TIMING_TARGET}: expected {expected} legacy 256-cycle guard anchor(s), "
            "refusing to patch an unknown DolRecomp source shape"
        )
    if check_only:
        return "verified"

    patched = text.replace(LEGACY_GUARD_OLD, LEGACY_GUARD_NEW)
    _verify_legacy_guard_quantum_shape(patched)
    path.write_text(patched, encoding="utf-8", newline="\n")
    return "applied"


def _patch_budget_exit_counter(source_root: Path, check_only: bool) -> str:
    path = source_root / TIMING_TARGET
    text = path.read_text(encoding="utf-8")

    # getOrInsertGlobal() is a llvm::Module member. Upstream timing.cpp only
    # includes Function.h and therefore sees Module as a forward declaration;
    # MSVC correctly rejects the dev85 counter without the concrete definition.
    has_counter = BUDGET_EXIT_COUNTER_SIGNATURE in text
    if LLVM_MODULE_INCLUDE not in text:
        if text.count(LLVM_FUNCTION_INCLUDE) != 1:
            raise RuntimeError(
                f"{TIMING_TARGET}: expected LLVM Function.h include exactly once "
                "so dev85 can add the required Module.h definition"
            )
        if has_counter and check_only:
            raise RuntimeError(
                "LLVM budget-exit counter is present without llvm/IR/Module.h"
            )
        if not check_only:
            text = text.replace(
                LLVM_FUNCTION_INCLUDE,
                LLVM_FUNCTION_INCLUDE + LLVM_MODULE_INCLUDE,
                1,
            )

    if has_counter:
        expected = 2
        if text.count(BUDGET_EXIT_COUNTER_SIGNATURE) != expected:
            raise RuntimeError(
                f"LLVM budget-exit counter marker count changed: "
                f"{text.count(BUDGET_EXIT_COUNTER_SIGNATURE)} != {expected}"
            )
        if '"openspm_m14_llvm_budget_exit_counter_ptr"' not in text:
            raise RuntimeError("LLVM budget-exit counter lost exported pointer")
        if LLVM_MODULE_INCLUDE not in text:
            raise RuntimeError("LLVM budget-exit counter lost required Module.h include")
        path.write_text(text, encoding="utf-8", newline="\n")
        return "already-applied"

    if text.count(BUDGET_EXIT_SCALAR_OLD) != 1:
        raise RuntimeError(
            f"{TIMING_TARGET}: expected scalar budget-exit anchor exactly once"
        )
    if not check_only and text.count(BUDGET_EXIT_DYNAMIC_OLD) != 1:
        raise RuntimeError(
            f"{TIMING_TARGET}: expected dynamic budget-exit anchor exactly once"
        )
    if check_only:
        return "verified"

    patched = text.replace(BUDGET_EXIT_SCALAR_OLD, BUDGET_EXIT_SCALAR_NEW, 1)
    patched = patched.replace(BUDGET_EXIT_DYNAMIC_OLD, BUDGET_EXIT_DYNAMIC_NEW, 1)
    if patched.count(BUDGET_EXIT_COUNTER_SIGNATURE) != 2:
        raise RuntimeError("LLVM budget-exit counter did not cover both guard overloads")
    if LLVM_MODULE_INCLUDE not in patched:
        raise RuntimeError("LLVM budget-exit counter did not install required Module.h include")
    path.write_text(patched, encoding="utf-8", newline="\n")
    return "applied"


def _verify_admission_cache_shape(text: str) -> None:
    required = [
        ADMISSION_CACHE_SIGNATURE,
        '"openspm_m14_llvm_region_cache_ptr"',
        "source_.guest_start >> 2",
        "source_.guest_end * 0x9E3779B9u",
        "cacheValue *= 0x7FEB352Du",
        "cacheOffset = (cacheValue & 0xFFFFu) * 12u",
        "builder_.CreateCondBr(cacheHit, nativeEntry, query);",
        '"ppc_native_region_available"',
    ]
    missing = [item for item in required if item not in text]
    if missing:
        raise RuntimeError(
            "incomplete LLVM inline admission-cache patch: " + ", ".join(missing)
        )


def _patch_inline_admission_cache(source_root: Path, check_only: bool) -> str:
    path = source_root / EXITS_TARGET
    text = path.read_text(encoding="utf-8")
    if ADMISSION_CACHE_SIGNATURE in text:
        _verify_admission_cache_shape(text)
        return "already-applied"

    if text.count(ADMISSION_ENTRY_OLD) != 1:
        raise RuntimeError(
            f"{EXITS_TARGET}: expected legacy interception-entry anchor exactly once; "
            "refusing to patch an unknown DolRecomp source shape"
        )
    if check_only:
        return "verified"

    patched = text.replace(ADMISSION_ENTRY_OLD, ADMISSION_ENTRY_NEW, 1)
    _verify_admission_cache_shape(patched)
    path.write_text(patched, encoding="utf-8", newline="\n")
    return "applied"


def _verify_native_phase_profile_shape(text: str) -> None:
    required = [
        PHASE_PROFILE_SIGNATURE,
        '"openspm_m14_note_frame_phase_native"',
        "case 0x801A8A9Cu: openspmPhase = 0u;",
        "case 0x801A7D1Cu: openspmPhase = 1u;",
        "case 0x801A7AACu: openspmPhase = 2u;",
        "case 0x801A8B64u: openspmPhase = 3u;",
        "builder_.CreateCall(phaseFn, {builder_.getInt32(openspmPhase)});",
        '"openspm_m14_note_death_entry_native"',
        "case 0x801615E4u:",
        "case 0x8017B834u:",
        "builder_.CreateCall(deathFn, {builder_.getInt32(block.guest_address)});",
    ]
    missing = [item for item in required if item not in text]
    if missing:
        raise RuntimeError(
            "incomplete LLVM native frame-phase profiler patch: "
            + ", ".join(missing)
        )


def _patch_native_phase_profile(source_root: Path, check_only: bool) -> str:
    path = source_root / INSTRUCTIONS_TARGET
    text = path.read_text(encoding="utf-8")
    if PHASE_PROFILE_SIGNATURE in text:
        if '"openspm_m14_note_death_entry_native"' in text:
            _verify_native_phase_profile_shape(text)
            return "already-applied"

        # dev106 migration: player caches already contain the retained dev81
        # phase hook from dev105. Upgrade that exact source shape in place so a
        # codegen-identity bump does not require a manual compiler-cache wipe.
        if text.count(PHASE_PROFILE_PRE_DEV106) != 1:
            raise RuntimeError(
                f"{INSTRUCTIONS_TARGET}: retained phase profiler is present but "
                "does not match the exact pre-dev106 source shape"
            )
        if check_only:
            return "verified-dev106-upgrade"
        patched = text.replace(PHASE_PROFILE_PRE_DEV106, PHASE_PROFILE_NEW, 1)
        _verify_native_phase_profile_shape(patched)
        path.write_text(patched, encoding="utf-8", newline="\n")
        return "upgraded-dev106"

    if text.count(PHASE_PROFILE_OLD) != 1:
        raise RuntimeError(
            f"{INSTRUCTIONS_TARGET}: expected emitRegion phase anchor exactly once; "
            "refusing to patch an unknown DolRecomp source shape"
        )
    if check_only:
        return "verified"

    patched = text.replace(PHASE_PROFILE_OLD, PHASE_PROFILE_NEW, 1)
    _verify_native_phase_profile_shape(patched)
    path.write_text(patched, encoding="utf-8", newline="\n")
    return "applied"


def _restore_synthetic_partition_boundaries(source_root: Path, check_only: bool) -> str:
    """dev101: restore pre-dev98 chunk boundaries after player HP-zero regression.

    A legal interior PC is not proven equivalent to a distinct native partition.
    Larger ranges also change native-ABI eligibility. Retain the old transformer
    only as a migration fixture; production no longer applies it.
    """
    path = source_root / PIPELINE_TARGET
    text = path.read_text(encoding="utf-8")
    if ENTRY_ONLY_SIGNATURE not in text:
        if "openspm_m14_symbol_is_entry_only" in text:
            raise RuntimeError("partial dev98 partition filter cannot be retired safely")
        return "already-restored"
    _verify_entry_only_cold_entry_shape(text)
    helper_start = text.index("/* " + ENTRY_ONLY_SIGNATURE)
    partition_start = text.index("static u32* collect_llvm_partition_points(")
    if helper_start >= partition_start:
        raise RuntimeError("dev98 helper is outside the expected partition collector")
    loop_start = text.index("    if (symbols) {", partition_start)
    loop_end = text.index("\n    }\n", loop_start) + len("\n    }\n")
    loop = text[loop_start:loop_end]
    if loop.count("openspm_m14_symbol_is_entry_only(name)") != 1:
        raise RuntimeError("dev98 partition filter loop changed")
    restored_loop = """    if (symbols) {
        for (u32 i = 0; i < symbols->count; i++) {
            u32 address = symbols->symbols[i].address;
            if (llvm_code_address(sections, section_count, address))
                points[count++] = address;
        }
    }
"""
    restored = (text[:helper_start] + text[partition_start:loop_start]
                + restored_loop + text[loop_end:])
    if "openspm_m14_symbol_is_entry_only" in restored:
        raise RuntimeError("dev98 partition filter remains after retirement")
    if check_only:
        return "verified-restore"
    path.write_text(restored, encoding="utf-8", newline="\n")
    return "restored"



def _patch_dev107_entry_only_perf_entries(
    source_root: Path, check_only: bool
) -> str:
    """Keep dev57 + exact BL-source symbols legal without chunk fragmentation."""
    path = source_root / PIPELINE_TARGET
    text = path.read_text(encoding="utf-8")
    if DEV107_ENTRY_ONLY_SIGNATURE in text:
        required = [
            "openspm_m14_dev107_entry_only_perf",
            '"openspm_dev57_addr_taken_"',
            '"openspm_dev107_direct_bl_source_"',
            "openspmDev107EntryOnlySkipped++",
        ]
        missing = [item for item in required if item not in text]
        if missing:
            raise RuntimeError(
                "incomplete dev107 entry-only performance patch: "
                + ", ".join(missing)
            )
        # Entry legality must remain broad; only the partition collector filters.
        entry_start = text.find("static u32* collect_llvm_entry_points(")
        partition_start = text.find("static u32* collect_llvm_partition_points(")
        entry_end = text.find("\nstatic ", entry_start + 1)
        if entry_start < 0 or partition_start < 0 or entry_end < 0:
            raise RuntimeError("dev107 LLVM entry/partition collector shape changed")
        entry_text = text[entry_start:entry_end]
        if "openspm_m14_dev107_entry_only_perf" in entry_text:
            raise RuntimeError("dev107 filter leaked into legal entry-point collector")
        if '"openspm_dev97_hp0_motion24_interval_"' in text[
            text.find("/* " + DEV107_ENTRY_ONLY_SIGNATURE):partition_start
        ]:
            raise RuntimeError("dev107 must not classify dev97 HP-zero rows entry-only")
        return "already-applied"

    # On a cached pre-dev101 source, check-only may observe the old dev98 filter.
    # The real transform restores it immediately before this patch.
    if ENTRY_ONLY_SIGNATURE in text:
        if check_only:
            return "verified-after-dev101-restore"
        raise RuntimeError("dev107 expected dev101 restoration before applying")

    partition_start = text.find("static u32* collect_llvm_partition_points(")
    if partition_start < 0:
        raise RuntimeError("dev107 partition collector missing")
    symbol_old = """    if (symbols) {
        for (u32 i = 0; i < symbols->count; i++) {
            u32 address = symbols->symbols[i].address;
            if (llvm_code_address(sections, section_count, address))
                points[count++] = address;
        }
    }
"""
    symbol_at = text.find(symbol_old, partition_start)
    next_function = text.find("\nstatic ", partition_start + 1)
    if symbol_at < 0 or (next_function >= 0 and symbol_at >= next_function):
        raise RuntimeError(
            "dev107 expected the restored partition symbol loop exactly once"
        )
    if check_only:
        return "verified"

    helper = """/* OPENSPM_M14_LLVM_DEV107_ENTRY_ONLY_PERF_ENTRIES
 * These symbols are exact legal restart PCs, not desired function boundaries.
 * Keep collect_llvm_entry_points() broad while allowing 128-instruction LLVM
 * ranges/native ABI analysis to span generic callback census rows and direct-BL
 * rescue source PCs. HP-zero/dev97 symbols are intentionally NOT filtered. */
static int openspm_m14_dev107_entry_only_perf(const char* name) {
    if (!name)
        return 0;
    return strncmp(name, "openspm_dev57_addr_taken_",
                   sizeof("openspm_dev57_addr_taken_") - 1u) == 0 ||
           strncmp(name, "openspm_dev107_direct_bl_source_",
                   sizeof("openspm_dev107_direct_bl_source_") - 1u) == 0;
}

"""
    symbol_new = """    if (symbols) {
        u32 openspmDev107EntryOnlySkipped = 0;
        for (u32 i = 0; i < symbols->count; i++) {
            u32 address = symbols->symbols[i].address;
            const char* name = symbols->symbols[i].name;
            if (openspm_m14_dev107_entry_only_perf(name)) {
                openspmDev107EntryOnlySkipped++;
                continue;
            }
            if (llvm_code_address(sections, section_count, address))
                points[count++] = address;
        }
        if (openspmDev107EntryOnlySkipped)
            printf("  OpenSPM dev107 entry-only performance symbols: %u legal entries excluded from chunk partitions\\n",
                   openspmDev107EntryOnlySkipped);
    }
"""
    patched = text[:partition_start] + helper + text[partition_start:]
    shifted = partition_start + len(helper)
    at = patched.find(symbol_old, shifted)
    if at < 0:
        raise RuntimeError("dev107 restored symbol loop disappeared during patch")
    patched = patched[:at] + symbol_new + patched[at + len(symbol_old):]
    path.write_text(patched, encoding="utf-8", newline="\n")
    return "applied"


def transform(source_root: Path, check_only: bool = False) -> str:
    source_root = source_root.resolve()
    _verify_retained_base()
    base_result = _base.transform(source_root, check_only)
    fpu_result = _patch_fpu_resume_entries(source_root, check_only)
    entry_only_result = _restore_synthetic_partition_boundaries(
        source_root, check_only
    )
    dev107_entry_result = _patch_dev107_entry_only_perf_entries(
        source_root, check_only
    )
    indirect_result = _patch_indirect_budget_guard(source_root, check_only)
    dispdraw_nested_result = _patch_dispdraw_nested_indirect(
        source_root, check_only
    )
    dispdraw_cpustate_result = _patch_dispdraw_cpustate_nested(
        source_root, check_only
    )
    guard_result = _patch_legacy_guard_quantum(source_root, check_only)
    budget_counter_result = _patch_budget_exit_counter(source_root, check_only)
    admission_result = _patch_inline_admission_cache(source_root, check_only)
    phase_result = _patch_native_phase_profile(source_root, check_only)
    scene_result = _scene_profile.retire(source_root, check_only)
    return (
        f"{base_result} fpu-resume-entries={fpu_result} "
        f"dev101-synthetic-partitions={entry_only_result} "
        f"dev107-entry-only-perf={dev107_entry_result} "
        f"indirect-budget-guard={indirect_result} "
        f"dispdraw-nested-indirect={dispdraw_nested_result} "
        f"dev105-cpustate-multiblock-dispdraw={dispdraw_cpustate_result} "
        f"legacy-guard-quantum={guard_result} "
        f"budget-exit-counter={budget_counter_result} "
        f"inline-admission-cache={admission_result} "
        f"native-frame-phase-profile={phase_result} "
        f"scene-interval-profile={scene_result}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_root", type=Path)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    try:
        result = transform(args.source_root, args.check_only)
    except (OSError, RuntimeError) as exc:
        print(f"error: OpenSPM LLVM private codegen patch failed: {exc}", file=sys.stderr)
        return 2
    print(f"OpenSPM LLVM private codegen patch {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
