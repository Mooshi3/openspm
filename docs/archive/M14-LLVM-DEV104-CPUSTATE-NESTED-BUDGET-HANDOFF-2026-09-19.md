# M14 LLVM dev104 CPUState nested-budget handoff — 2026-09-19

## Executive result

Player log `launch(20260919-162912).log` tested repo `d9ae710d0513` and
changed the diagnosis in two useful ways:

1. **dev103 is a real performance fix.** Removing generic
   `openspm_dev57_addr_taken_*` map rows from the may-host set collapses
   ordinary C-router traffic while retaining exact semantic re-add rules.
2. **Pit Game Over is restored.** HP reaches zero in MOT_24, `next_seq=4`
   appears, SEQ_GAMEOVER progresses through stages 1..6, and the game returns
   to seq 1. The key difference from the broken dev101 run is that dev100's
   generated per-call CFG profiler is retired. Do not re-enable it.
3. **dev102 finally hits the real dispDraw site but cannot nest yet.** It records
   6,338,293 attempts and 6,338,293 budget yields, with zero handled/normal
   returns. This is not a lookup failure; the CPUState budget test was wrong.

## Player evidence

Shutdown totals from the dev103 run:

```text
dev87 bypassed=2139203694
dev90 hits=38294988 fallbacks=0
dev102 attempts=6338293 handled=0 normal_returns=0 abnormal_returns=0
       depth_fallbacks=0 lookup_misses=0 budget_yields=6338293
Game Over next_seq observations=1 final_seq=1 final_next_seq=1
ordinary hostcall sample total=7488744
production-router ordinary=7474560
present pacing avg=17358us p50=17000us p95=20000us p99=34000us
```

The prior dev101 player log ended with 88,347,855 sampled ordinary hostcalls and
p95 present interval around 33 ms. The runs differ in duration, so do not compare
raw totals as a throughput benchmark, but the dev103 cadence logs also fall from
the old heavy ~0.30–0.48M ordinary callbacks/s into mostly low tens of thousands/s.
Keep dev103.

## Why dev102 budget-yielded 100%

OpenSPM's legacy CPUState/RecompCore run loop does this before every top-level
generated dispatch:

```c
instance->cpu.downcount = 0;
dolrecomp_call(...);
charge = -instance->cpu.downcount;
```

The pinned DolRecomp CPUState path settles generated cycles by subtracting them
from `CPUState.downcount`. Therefore a negative downcount means **accumulated
guest-cycle charge**, not "remaining budget exhausted."

dev102 reused the native-ABI helper, whose guard was:

```c
if (cpu->downcount <= 0) return 0;
```

At the CPUState `dispDraw` site, `materialize(target)` settles at least some
cycles before entering the helper, making downcount negative. So every measured
attempt failed that precheck by construction.

## dev104 design

Keep dev99's historical native-ABI helper unchanged. The exact measured
CPUState linked-indirect site `0x8005B29C` now calls:

```text
openspm_m14_dev104_cpustate_nested_dispatch
```

The helper interprets downcount as negative accumulated charge and uses a
bounded 32,768-cycle quantum:

- before a callback, yield only if `downcount <= -32768`;
- otherwise call the existing exact `dolrecomp_call()` under the existing
  `dolrecomp_call_enter/leave` recursion guard;
- resume the parent only for handled + no fallback + no exception + exact PC+4;
- after a normal callback, yield if accumulated charge reached -32768;
- any miss/fallback/exception/unusual return escapes through the historical
  outer path.

When yielding before a callback, CPUState already has PC=target from
`materialize(target)`. When yielding after a normal callback, the nested body
has PC=continuation. The outer run loop therefore sees the exact accumulated
negative charge and resumes at the correct architectural PC.

No callback is skipped, reordered, fabricated, or forced to return normally.

## Identities

Codegen:

```text
llvm-inline-wpar-indirect-budget-v15-dev104-cpustate-charge-quantum
```

Module:

```text
m14-dev104-cpustate-charge-quantum
```

The codegen identity bump intentionally invalidates stale compiler objects that
could otherwise preserve dev103/dev102 behavior.

## Files changed

- `tools/native_bridge/legacy_c_adapter_v42_dev69.c.in`
  - adds CPUState-specific dev104 helper and telemetry;
- `tools/native_bridge/apply_openspm_llvm_wpar_codegen_patch.py`
  - exact site now imports/calls the dev104 helper;
- `tools/native_bridge/prepare_dolrecomp_llvm.py`
- `tools/native_bridge/run_m14_llvm_candidate.py`
  - v15/dev104 codegen identity;
- `tools/native_bridge/legacy_c_adapter_v42.c.in`
  - dev104 module identity;
- `tools/native_bridge/test_llvm_native_pipeline.py`
  - dev104 helper, telemetry and identity contracts;
- `tools/native_bridge/generate_local_llvm.py`
  - dev104 provenance and corrected dev101 partition provenance.

## Correctness canary

Pit death is now a protected canary. A good dev104 run must still show:

```text
HP=0 / motion=24
next_seq=4
SEQ_GAMEOVER stages
return to seq=1
```

If that regresses, narrow/retire dev104 before doing another performance
experiment. Do not force Game Over.

## Next player test

Run:

```text
M14-LLVM-UPDATE-RUN.cmd
```

No manual cache wipe.

Test pit death first, then the same NPC-heavy room for 15–20 seconds and close
normally.

Expected startup marker:

```text
M14 LLVM dev104 CPUState dispDraw nesting active:
```

Expected teardown marker:

```text
M14 LLVM dev104 dispDraw nested stats attempts=...
```

Success criteria for the nesting experiment:

- `attempts > 0`;
- `handled > 0`;
- `normal_returns > 0`;
- `budget_yields` is a much smaller fraction of attempts, not 100%;
- pit Game Over remains correct.

If normal returns become common but the heavy room remains slow, the next
profiling target is inside the remaining `seq_gameMain` / display callback
work rather than another generic router optimization.
