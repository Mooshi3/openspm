# M14 LLVM dev67 — retire speculative ordinary cache, profile safe native slices

Date: 2026-09-16
Branch: `m14-native-execution`

## Evidence from the two dev66 player runs

Both logs are from repo revision `a7211f167194` and show the dev66 startup marker.

### Wedding-cutscene AFK run

`launch(20260916-225937).log`

The run did not produce a native exception/fail-fast crash. It progressed normally at about 30 `spmarioMain` updates per ~60 VI retraces until total update 3404, then stopped guest progress at:

```text
pc  = 0x80065128
lr  = 0x80064E34
ctr = 0x80064E00
seq = 2
stage = 1
```

The exact PC then remained stationary for minutes while authoritative Wii time and VI continued near 60 Hz. Write-gather bytes also stopped changing at `98191937`. Native slices continued to return, eventually settling at exactly 65536 guest cycles each. No guest exception/program exception was reported.

So this was a liveness failure / guest spin, not an Aurora or Windows native crash.

### Long gameplay run with Isaac closed

`launch(20260916-230527).log`

Dev66 mechanically worked extremely well at reducing semantic-router work:

```text
dev66 callbacks      = 528,935,935
bypass hits          = 526,080,775  (~99.46%)
delegate hits        =   2,599,704
misses               =     255,456
production ordinary  =   2,841,040
```

For comparison dev65 had about 503 million production ordinary callbacks. The underlying router therefore fell to roughly 0.56% of the old count, and its billions of seam/host comparisons collapsed to tens of millions.

But simulation cadence did not materially improve. The long isolated run remained predominantly around:

```text
spmarioMain = 30-31 / second
retraces    = 60-62 / second
```

with later heavy gameplay occasionally lower. Therefore the ordinary C semantic router was real overhead but **not** the missing ~2x simulation throughput.

## Why dev66 is retired

Dev66 learned an exact PC as `BYPASS` when one complete semantic-router call:

- returned `false`; and
- did not immediately mutate the probed CPU/fallback state.

That is not sufficient to prove permanent non-semantic behavior. A state-dependent callback can legitimately do nothing on its first visit and become necessary later after external/device/guest state changes. The wedding liveness failure is consistent with that class of risk, even though the current log alone does not prove which exact callback was misclassified.

Because dev66 delivered no meaningful simulation-rate gain, adding more exclusion rules is not justified. The speculative ordinary-PC cache is removed entirely in dev67.

## dev67 implementation

`tools/native_bridge/legacy_c_adapter_v42.c.in`

Module version:

```text
m14-dev67-llvm-safe-slice-profiler
```

Dev67 restores the exact v41/dev58/dev50 ordinary host-call behavior. It retains:

- dev63 generated positive native-region admission cache;
- dev58/v19 GX semantics;
- dev62/dev61 continuation policy;
- dev55 indirect soft-budget guard;
- chunk-128/indexed LLVM objects from dev65 for now.

No ordinary host callback is cached, wrapped or reclassified.

## New observation-only profiler

Dev67 wraps only the native module `run()` boundary, which is called hundreds of times per second rather than hundreds of millions of times per session.

It records:

- total run-slice calls;
- aggregate and maximum host time from `clock()`;
- duration histogram (`<=250`, `<=500`, `<=1000`, `<=2000`, `<=4000`, `>4000` us);
- an 8-slot weighted heavy-hitter sketch keyed by exact `(start_pc,end_pc)` run-slice pairs.

Teardown marker:

```text
M14 LLVM dev67 slice stats calls=... wall_us=... avg_us=... max_us=... hist_us=[...] hang_80065128_max=... heavy_pairs=...
```

This does not yet constitute a full host CPU profile, but it should distinguish a small set of pathological run-boundary paths from generally slow translated execution without modifying LLVM objects.

## Targeted wedding-hang diagnostic

If the exact returned PC remains `0x80065128` for 64 consecutive native slices, dev67 logs once:

- PC/LR/CTR/CR/XER/MSR/downcount;
- nine player-owned PPC instruction words centered on `0x80065128`;
- GPR3-GPR12 and GPR29-GPR31.

It repeats the same snapshot at 1024 consecutive slices. The data is emitted only to the player's local launch log; no Nintendo instruction bytes are committed to the repository.

Marker:

```text
M14 LLVM dev67 stationary diagnostic count=64 pc=0x80065128 ...
```

## Next player test

Run:

```text
M14-LLVM-UPDATE-RUN.cmd
```

No save/cache wipe.

Expected startup marker:

```text
M14 LLVM dev67 safe slice profiler active: dev66 speculative ordinary-PC bypass cache is RETIRED
```

Test priorities:

1. Revisit/AFK the wedding cutscene if convenient. If it no longer stalls, dev66 was the regression and dev67 is the safe baseline.
2. Play the same laggy gameplay path for a minute or two.
3. Close normally if possible so `dev67 slice stats` is emitted.
4. If wedding still hangs, leave it for at least ~2 seconds so both stationary snapshots have a chance to appear, then send the log.

## Interpretation after next run

- **Wedding fixed, cadence still ~30:** keep dev66 retired. Use dev67 slice pairs to choose the next native-code profiling target.
- **Wedding still hangs:** decode the local instruction/register snapshot around `0x80065128`; do not fabricate sequence state or add an arbitrary skip.
- **Slice timing dominated by a small exact start/end pair:** instrument or fix that native region/system boundary directly.
- **Slice timing broadly distributed while cadence stays ~30:** move to true LLVM host CPU profiling / PGO and apples-to-apples generated-C comparison rather than more bridge routing work.
