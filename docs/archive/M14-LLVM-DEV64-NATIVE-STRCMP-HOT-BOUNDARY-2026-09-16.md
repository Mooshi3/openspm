# M14 LLVM dev64 — native `strcmp` hot-boundary repair

Date: 2026-09-16  
Branch: `m14-native-execution`  
Implementation commit: `47e2873c36c014389ae6b37bd524701d4d462713`

## Why dev64 exists

Dev63 was a successful architectural A/B but not the final performance fix. In `launch(20260916-201959).log`, generated LLVM stopped crossing into C for the old billion-scale positive region-admission hits:

```text
M14 LLVM dev58 stats queries=234131 cache_hits=0 misses=173597 ...
```

The previous playable run had ~1.418 billion dev58 queries / positive hits. So dev63 removed the intended admission storm.

Simulation cadence nevertheless remained roughly 30 `spmarioMain` updates/s in the opening path, and teardown still showed an enormous independent ordinary-dispatch path:

```text
ordinary hostcall sample total=425024175
production-router ordinary=425020719
seam_lookup=425013690/3705331347
host_lookup=424767850/2319480460
```

This proves the positive-admission callback was not the only major tax.

## The new smoking gun

The dev58 ordinary-call sampler repeatedly retained two heavy addresses:

```text
0x8025CABC
0x8025CAD4
```

The verified US2 symbol map places both inside retail `strcmp`:

```text
strcmp  = 0x8025C9C8
strncmp = 0x8025CAE4
```

OpenSPM currently forces:

```text
DOLRECOMP_LLVM_CHUNK_INSTRUCTIONS=64
```

A 64-instruction range starting at `strcmp` ends at exactly:

```text
0x8025C9C8 + 64 * 4 = 0x8025CAC8
```

The two sampled PCs sit only 12 bytes on either side of that artificial chunk edge:

```text
0x8025CABC  = edge - 0x0C
0x8025CAD4  = edge + 0x0C
```

That is compelling evidence that a tiny retail libc loop is repeatedly crossing a native partition boundary and falling through generic OpenSPM dispatch/interception machinery.

## dev64 implementation

`tools/native_bridge/legacy_c_adapter_v42.c.in` now wraps dev63 instead of merely including v41 directly.

At native-region admission:

- a region containing exact verified US2 `strcmp` entry `0x8025C9C8` is blocked before native execution;
- the ordinary host call at that exact entry executes an OpenSPM native specialization;
- all other addresses delegate unchanged to the dev63/dev58 chain.

The specialization is not a libc `strcmp()` approximation. It is the same architectural strategy already used by the earlier generated-C path:

- direct bounded MEM1 only;
- retail byte/word comparison flow;
- retail scratch `r0/r3/r4/r5/r6/r7/r8` effects;
- `CTR`, `XER`, `CR0` behavior;
- exact return magnitude;
- accumulated retail cycle charges into `downcount`;
- return PC from guest LR.

The only scheduling difference is intentional: a proven <=512-byte compare completes in the host call instead of yielding back across the pathological 64-instruction boundary.

## Fail-open behavior

If either string is not provably backed by a direct 512-byte MEM1 span, dev64 does not guess.

It arms one original-native permit and returns false. The next region query for `strcmp` is allowed directly without delegating to dev58, so the original generated function executes once. That permit is deliberately **not inserted** into dev58's positive cache; future ordinary calls still get the native specialization when eligible.

This preserves long/MEM2/edge-of-RAM behavior without letting one unusual call silently disable the optimization for the process lifetime.

## Input fix already stacked above the tested log

Current branch also contains commit `d71c16fb04b3276d85f3a83c912f9f9447e4f73a`, which retains newly pressed PC input edges until the real guest `wpadGetButtonsPressed()` path observes them. `launch(20260916-201959).log` predates that commit, so the next local run tests both:

1. dev27 input edge consumption;
2. dev64 hot `strcmp` elimination.

## Next local test

Run exactly:

```text
M14-LLVM-UPDATE-RUN.cmd
```

No save/cache wipe.

Expected startup marker:

```text
M14 LLVM dev64 native strcmp repair active
```

Expected teardown marker:

```text
M14 LLVM dev64 strcmp stats calls=... completed=... declined=... region_blocks=... permits=...
```

### Strong success signal

The important A/B is not merely a small frame-time improvement. If the hot-boundary diagnosis is right:

- ordinary hostcall total should fall dramatically from ~425M for a similar-duration path;
- the `0x8025CABC` / `0x8025CAD4` heavy hitters should disappear or become negligible;
- production-router seam/host lookup counts should collapse with them;
- `spmarioMain` cadence should rise materially toward the 60 Hz retrace rate;
- input should feel better both because dev27 retains edges and because simulation consumes them more often.

### If calls do not collapse

Do not tune renderer/VI first. The remaining ordinary dispatcher must be classified by exact exit source. Promote the next heavy PCs and instrument whether they came from native chunk transitions, indirect returns, soft-budget exits, or true host semantic seams.

### If `strcmp` calls are high but helper declines are high

Inspect the string-address domains first. A high decline ratio means the workload is not predominantly bounded MEM1 and the optimization should be generalized by proven address space rather than by weakening the span check.

## Preserved constraints

- no interpreter/emulator fallback;
- no Nintendo bytes/assets in source control;
- dev63 positive admission cache retained;
- v19 GX CPU-shadow/display-list semantics retained;
- fixed 60 Hz VI/retrace semantics retained;
- chunk 64 remains unchanged for this A/B;
- only the measured retail `strcmp` function is specialized.
