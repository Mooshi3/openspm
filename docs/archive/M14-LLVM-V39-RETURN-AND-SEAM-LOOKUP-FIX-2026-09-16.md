# M14 LLVM dev49 — hosted display-list return + hot seam lookup fix

Date: 2026-09-16
Branch: `m14-native-execution`

## Player evidence

Authoritative player log: `launch(20260916-051927).log`
Player revision: `5207771c063e...` (dev48)

The player reported that the opening `Ahem! Today...` cutscene crashed and the run remained very laggy.

### dev48 did not crash inside the native recorder

The first PC-native display-list begin completed successfully:

```text
M14 GX display-list begin count=1 addr=0x810D8B80 capacity=65536 lr=0x80080624.
M14 LLVM PC-native GX display-list begin #1 target=0x80291DC8 return=0x80080624 ... retail Wii CPU-FIFO body skipped.
```

Immediately afterward the module exited at:

```text
Native migration boundary reached reason=3 detail=0x0000002D source=0x80291DC8
```

`0x2D == 45`, which is the historical v19 failure code in
`openspm_m14_display_list_begin_after_return_v19()` for an invalid/missing
`begin_pending` continuation state.

The failure was therefore caused by an obsolete post-return hook, not by the
OpenSPM display-list recorder or Aurora.

## Root cause: dispatcher callback precedes native-region query

dev48 called the existing bounded OpenSPM GXBeginDisplayList host service and
returned architectural state directly to the guest LR. It then armed v23's
one-shot native-region permit for that LR.

That was insufficient because the DolRecomp dispatcher invokes
`CPUState::host_call(return_pc)` before native lookup / region availability.
The direct callback therefore reached v23's old generated-C-style
`begin_after_return` hook before the one-shot native-region permit could matter.

## dev49 fix

New active bridge:

```text
legacy_c_adapter_v39.c.in
m14-dev49-llvm-pc-display-list-return
```

v39 wraps dev48. After a successfully hosted GXBeginDisplayList or
GXEndDisplayList:

1. dev48 has already set `cpu->pc` to the exact guest LR and armed v23's existing
   region permit;
2. v39 records that exact LR as one pending hosted return;
3. the next ordinary `host_call(address)` is swallowed **only if**
   `address == pending_return_pc`;
4. the pending return is cleared immediately;
5. the following `PPC_HOST_CALL_NATIVE_REGION_QUERY` is still delegated through
   the full underlying chain, where v23 consumes its original one-shot permit.

No unrelated callback is swallowed. A different ordinary callback clears the
stale expectation and is delegated unchanged.

Expected proof marker:

```text
M14 LLVM PC-native GX return-continuation fix active: suppressed obsolete v19 direct callback at exact hosted return PC ...
```

Expected success behavior:

- first Begin no longer exits with detail 45;
- GXEndDisplayList is reached and logged;
- `begins` and `ends` grow together;
- opening cutscene advances beyond the dev48 failure point.

## Performance root cause found in active correctness layer

The dev48 log still ran at roughly 20-21 `spmarioMain` updates/sec while retraces
remained near 60/sec. Region-query telemetry climbed into the tens of millions
before the opening cutscene.

The active hosted direct-call parity layer (`legacy_c_adapter_v26.c.in`) was
performing this lookup for every native-region query:

```c
for (i = 0; i < OPENSPM_LOCAL_LLVM_HOSTCALL_SEAM_COUNT; ++i)
    if (OPENSPM_LOCAL_LLVM_HOSTCALL_SEAMS[i].call_pc == pc)
        return &OPENSPM_LOCAL_LLVM_HOSTCALL_SEAMS[i];
```

Current player seam count: **383**.

DolRecomp LLVM calls `ppc_native_region_available()` on native function/range
entry whenever `CPUState.host_call` is non-null. The current OpenSPM bridge uses
that callback for semantic interception, so this lookup is extremely hot.
Millions of region queries/sec multiplied by a 383-entry linear scan can produce
hundreds of millions of integer comparisons/sec before useful game work.

### Safety proof for binary search

`derive_hostcall_llvm_seams.py` defines `HostCallSeam` as an ordered dataclass,
rejects duplicate call PCs, and explicitly executes:

```python
seams.sort()
```

before emitting `OPENSPM_LOCAL_LLVM_HOSTCALL_SEAMS`.

Therefore `call_pc` ordering is a generator-enforced invariant.

### Performance patch

`legacy_c_adapter_v26.c.in::bridge_v36_find_seam()` now performs binary search.

For 383 seams:

- old worst case: 383 comparisons / lookup;
- new worst case: about 9 comparisons / lookup;
- worst-case comparison reduction: ~42x for this hot lookup.

Semantics are unchanged: exact `call_pc` identity still selects the same seam.

New shutdown telemetry:

```text
M14 LLVM hosted direct-call stats ... lookup_calls=... lookup_steps=... avg_steps=....
```

This lets the next player run quantify the actual average lookup depth.

## Next-run classification

1. Build failure: fix v39/CMake or v26 binary-search compile issue only; do not
   revisit GX semantics.
2. Detail 45 gone + Begin/End logs appear: dev49 return ordering is correct.
3. New display-list failure: use exact boundary/detail and begin/end counts.
4. Compare `spmarioMain` cadence immediately against dev48's ~20-21/s. A large
   jump would confirm the 383-way hot lookup was a primary LLVM bottleneck.
5. If cadence remains poor, use `lookup_calls/lookup_steps` plus region-query
   rate to move to the next query-chain cost. Do not blame Aurora: prior player
   logs already showed steady-state DrawDone/render costs far below frame budget.
