# M14 display-list v19 frontier — 2026-09-13

## Status

**ACTIVE CANDIDATE / NOT YET WINDOWS RUNTIME-PROVEN.**

The isolated raw GX texture-copy half-scale test succeeded semantically (`608x480 -> 304x240`) but produced no meaningful visual improvement. That clean negative result cleared the way to promote v19 as the next isolated graphics-state experiment.

Active local module is now `legacy_c_adapter_v11.c.in` / `m14-dev19`; backend remains `openspm.native 14.0-dev26`. Begin/End are removed from the whole-function host-target manifest. Public OpenSPM still contains no Nintendo instruction bodies/assets.

# Corrected v18 forensics

The first v18 Windows run ended at PC `0x8026BAD8`, LR `0x8019DA5C`. This was previously described as native `GXBeginDisplayList` entering an unmodeled Wii hardware wait. Exact player-local US2 code disproved that.

`0x8026BAD8` is the terminal OS halt loop and the observed LR is inside `OSPanic`. A map display-list builder calls `GXEndDisplayList`, compares returned `r3` against zero, and asserts if the size is zero. V18 therefore reached a **zero display-list size assertion**, then deliberately panicked/halted.

V18's real design flaw was continuation lifetime. Its after-native recorder callback lived inside a temporary host C direct-call wrapper. DolRecomp native calls may yield, unwind that host frame, and later resume by authoritative guest PC/LR. When the SDK finally returns, the transient wrapper callback can be gone.

That explains all of the observed evidence:

1. the v18 post-Begin marker never appeared;
2. the neutral recorder did not reliably start;
3. native End could observe no meaningful PI FIFO count;
4. End returned zero;
5. SPM asserted and called `OSPanic`.

The architecture was not disproven; the hook placement was.

# Exact CPU-side GXData facts

Player-local R8PE01 source matches public RVL SDK behavior:

- `GXBeginDisplayList` = `0x80291DC8`
- `GXEndDisplayList` = `0x80291E78`
- `__GXData` pointer from `[r2 - 0x1E0]`
- context span = `0x600`
- `cpEnable +0x08`
- `inDispList +0x5F8`
- `dlSaveContext +0x5F9`
- `dirtyState +0x5FC`

Begin flushes dirty GX shadow state, optionally saves GXData, marks list state, switches CPU FIFO and resets write gather. End flushes the list, restores CPU FIFO, restores GXData while preserving `cpEnable`, clears list state and returns count/wrap result.

This CPU-side save/restore is the important fidelity missing from the former whole-function hosted recorder and is a plausible systemic mechanism for inherited TEV/texture/material state.

# Existing PI/CP support

OpenSPM's GX platform already latches the CPU-visible PI FIFO base/end/write-pointer and CP FIFO/status/control state. Native `GXSetCPUFifo` is therefore not an unknown-MMIO dead end.

The intentional mismatch is narrower: the neutral recorder owns the real list payload and aligned/overflow count, but recorded bytes do not advance the emulated PI write pointer. Native End can see zero even when the recorder owns a valid list. V19 substitutes the recorder's authoritative size at the caller continuation.

# V19 continuation-safe design

Exact R8PE01 has three Begin and three End callsites. None of their six caller return labels is another guest branch target.

## Begin

1. pre-call hook persists `buffer` and `capacity`;
2. native Begin executes and may yield/resume arbitrarily;
3. at the **guest return label**, v19 flushes any pending write-gather bytes and calls OpenSPM's existing Begin handler only to activate the neutral bounded recorder;
4. list payload stores then go to the caller-owned guest buffer through the recorder.

## End

1. pre-call hook records pending End while recorder is active;
2. native End executes, including Nintendo's CPU-side GXData/FIFO restore, and may yield/resume;
3. at the **guest return label**, v19 invokes OpenSPM's existing End handler to close the recorder/get authoritative aligned size;
4. v19 writes that size to guest `r3` before any caller instruction executes.

Unexpected nesting, partial migration, invalid buffer/capacity, missing recorder state or ambiguous return-label control flow fails closed.

# Active files

- `tools/native_bridge/display_list_return_hooks_v19.py`
- `tools/native_bridge/test_display_list_return_hooks_v19.py`
- `tools/native_bridge/legacy_c_adapter_v11.c.in`
- `tools/native_bridge/renormalize_staged.py`
- `tools/native_bridge/CMakeLists.txt`
- `state/m14-host-targets.json`
- `tools/native_bridge/m14_host_targets.inc`

Semantic routing is now **40 backend host exits + 5 inline semantics = 45**. Begin/End are absent from the host manifest but their backend compatibility handlers intentionally remain callable by v19's physical-recorder hooks.

# Cache-safe promotion path

Normal `M14-UPDATE-RUN.cmd` is sufficient; do not wipe `.openspm`.

`renormalize_staged.py` performs:

1. retire exact historical v18 transient hooks;
2. direct-call normalize using the current 40-target manifest, promoting cached v17 Begin/End callsites native;
3. retire stale NAND guard;
4. reversible hosted-label guard removes obsolete Begin/End entry guards;
5. install v19 pre-call/return-label hooks;
6. second pass requires byte-identical source and exact v19 stats `(3,3,0)`.

Expected migration line:

```text
staged-renormalize ... v19=begin:3,end:3,changed:... verify=stable
```

Expected runtime first-use markers:

```text
M14 v19 continuation-safe GXBeginDisplayList return hook active...
M14 v19 continuation-safe GXEndDisplayList return hook active...
```

# Acceptance / rollback

Windows proof must show:

- no zero-size assertion / `0x8026BAD8` OSPanic halt;
- both v19 markers appear;
- title/save/gameplay remain reachable;
- command-aware FIFO + nested CALL_DL loading-animation repair remains intact;
- compare black/inherited materials, missing outlines, blue/striped flip corruption, unexpected flip-star effect, and door/map-object rendering.

If the candidate fails before either marker, inspect staged normalization/callsite continuation first. If Begin appears but End does not, inspect native End return path. If End appears and the game still hits the old assertion, log recorder size/r3 immediately at the End hook.

Do not mix the separate guest-array performance rewrite into this runtime proof. The latest player log exposed a ~15.48 MB/frame, 335-miss staging cost, but compact interval staging is intentionally the next isolated performance candidate after v19 attribution is known.
