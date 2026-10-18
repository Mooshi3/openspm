# M14 LLVM dev83 — 4096-cycle legacy x86 yield quantum

Date: 2026-09-18

## Why this is now the highest-value performance A/B

The last known-good dev79 gameplay log (`launch(20260918-020807).log`) ended with:

- `ordinary=691331701` production-router callbacks;
- `input_frames=14303`;
- the ordinary callback sampler dominated by `0x8025CABC` and `0x8025CAD4`;
- renderer DrawDone/present work already far below the missing ~16 ms in heavy simulation.

At 60 input frames/s, 14,303 frames are about 238.38 seconds. That makes the
ordinary public-dispatch rate about **2.900 million callbacks/s**.

Pinned DolRecomp's legacy LLVM `emitBudgetGuard` yields after exactly **256
guest cycles**. Multiplying the measured callback rate by 256 gives about
**742.4 million guest cycles/s** — essentially Broadway's ~729 MHz execution
scale. This is much too close to be accidental.

There is a second independent clue. Seeky's R8PE01 US2 symbol list places
`strcmp` at `0x8025C9C8` and `strncmp` at `0x8025CAE4`; therefore both
dominant ordinary callback PCs (`0x8025CABC` and `0x8025CAD4`) are inside the
same 0x11c-byte MSL `strcmp`. Budget guards are emitted at loop headers. The
hot public-dispatch PCs are therefore exactly where a tiny 256-cycle loop yield
would be expected to repeatedly materialize a hot string-comparison loop.

This explains why dev66/dev69 made router lookup dramatically cheaper without
recovering the missing simulation throughput: they optimized what happens
*after* a budget exit, but did not stop LLVM from manufacturing millions of
budget exits per second.

## dev83 change

The active private DolRecomp patch now uses a 4096-cycle guard only when all of
these are true:

```text
native_abi_ && cold_escapes_ && intrinsic_escapes_
```

That is the exact legacy x86 SJLJ configuration used by OpenSPM's Windows
RecompCore-style LLVM objects.

All other DolRecomp configurations keep the upstream 256-cycle threshold.

Both guard overloads are changed:

- scalar/direct `emitBudgetGuard(u32 pc)`;
- dynamic/indirect `emitBudgetGuard(Value *pc)`.

The transform fails unless it finds and changes both exact anchors.

Expected steady-state budget-exit/public-dispatch pressure from this one change
is roughly 16x lower: ~2.9M/s -> ~181k/s if the measured ordinary callbacks are
predominantly guard exits.

## Why this is safer than dev80

dev80 tried to skip the host-first semantic router by predicting which PCs were
safe native entries. That regressed boot at `0x8026CA60`, so dev82 fully
restored the proven dev79 host-first dispatcher.

dev83 does **not** reorder dispatch and does **not** bypass any semantic host
callback. PE finish, DSP, post-MTMSR, GX display-list, HOME, relF, scheduler and
all other production routing remain exactly dev79/dev82.

It only lets a native chain do more guest work before voluntarily returning to
the same dispatcher.

OpenSPM's outer `bridge_run` still owns a 200,000-cycle soft budget and
`bridge_run_v7` still performs the same scheduler/VI safepoint after that outer
run. At 4096 cycles, there are still roughly 49 possible internal yields per
200k-cycle outer run. Exception/fallback/host-call exits are unaffected.

## Active provenance

`llvm-inline-wpar-indirect-budget-v6-dev83-guard4096`

This intentionally invalidates/rebuilds affected LLVM objects.

## Next player test

Run:

```text
M14-LLVM-UPDATE-RUN.cmd
```

No manual cache wipe.

Gates:

1. boot past the dev80 regression frontier `0x8026CA60`;
2. pass the wedding;
3. play a known heavy/jittery NPC area for at least ~30 seconds;
4. capture the dev81 `M14 main-loop phases ...` lines;
5. compare `ordinary hostcall` / `production-router ordinary=` growth against
   the old ~2.9M callbacks/s rate.

If heavy simulation moves materially toward 60 Hz while retraces remain ~60,
dev83 has identified a primary lag source. If correctness regresses, revert only
the 4096 quantum to 256; dev82's host-first dispatcher restoration remains
independent.
