# M14 LLVM MTMSR cold-entry fix — 2026-09-14

## What the second stationary hang proved

The first LLVM black-window hang was stationary at `0x80271E48` inside the Revolution OS interrupt helpers. The temporary v19 bridge recovery for that address worked and let startup advance.

The next run then became stationary at a completely different PC:

- `pc=0x802F09B4`
- `lr=0x8017B664`
- `region=[0x802F09A8,0x802F0A1C)`
- `region_queries=137089`, permanently unchanged
- `exc=0`, `program=0`, `wg=0`

Adding every direct linked-call return (`BL/BCL -> PC+4`) to DolRecomp's LLVM entry-point collector did not move this frontier. That falsified the narrower theory that the second hang was a linked-call cold-return problem.

## Exact compiler-side producer

Pinned DolRecomp `src/backend/llvm/special_instructions.cpp` has explicit behavior for writes to `DOLIR_STATE_MSR`. After storing the new MSR value, it detects an EE transition from disabled to enabled:

```cpp
Value *enabled = builder_.CreateAnd(
    builder_.CreateAnd(builder_.CreateNot(old), value),
    builder_.getInt32(0x00008000u));
...
sideExit(inst.guest_pc + 4u);
```

Therefore every `mtmsr` which enables MSR[EE] can deliberately materialize and return to OpenSPM at the instruction immediately following `mtmsr`.

Under generated-C M14, resuming at an arbitrary instruction was normal. Under the LLVM backend, a top-level PC inside a native range must be one of that range's legal entry leaders. If `MTMSR + 4` is not an entry leader, the RecompCore fallback path is entered. OpenSPM intentionally supplies only a narrow migration fallback rather than a full PPC interpreter, so an ordinary instruction at that cold PC can leave PC unchanged and loop forever inside the native function.

This explains both stationary hangs with one compiler contract violation.

## Fix

`tools/native_bridge/apply_openspm_llvm_wpar_codegen_patch.py` now extends pinned DolRecomp's app-level `collect_llvm_entry_points()` policy. In addition to normal direct branch targets and linked-call continuations, it adds:

```c
if (inst.op == PPC_OP_MTMSR &&
    llvm_code_address(sections, section_count, address + 4u))
    points[count++] = address + 4u;
```

`PPC_OP_MTMSR` is a first-class opcode in the pinned decoder, so no raw-word pattern matching is used.

The global 128-instruction range layout is unchanged. `pipeline.c` is intentionally outside DolRecomp's codegen-source digest, while the per-object cache key includes only entry points which fall inside that object. Therefore the expected cache impact is local: chunks containing affected `mtmsr` continuations re-emit; unrelated LLVM objects remain reusable.

The older linked-call continuation addition is retained because it is independently correct for native callees that cold-escape back through the dispatcher.

## Validation

Run the normal opt-in candidate:

```bat
M14-LLVM-UPDATE-RUN.cmd
```

Acceptance criteria:

1. the private DolRecomp compiler rebuilds with the updated pipeline patch;
2. most of the 14,414 guest objects remain cache hits;
3. the old `0x802F09B4 / region_queries=137089` stationary frontier disappears;
4. if another stationary frontier appears, classify its producer before adding any address-specific shim.

Keep the temporary OSInterrupt v19 recovery until compiler-side cold-entry parity has booted far enough to prove it can be removed safely.
