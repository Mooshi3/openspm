# M14 LLVM dev86 — measured hot-ordinary dispatcher fast path

Date: 2026-09-18

## Runtime evidence from dev85

Player log: `launch(20260918-152140).log` at repo revision
`45d4595b60e6`.

The run spent a long AFK interval repeating the wedding and then included live
gameplay at the end. The wedding remained live and the final slice summary
reported:

```text
hang_80065128_max=0
```

The dev85 exact counter resolved the current causal question. End-gameplay
cadence repeatedly showed millions of ordinary public callbacks per second while
the exact LLVM voluntary budget-exit rate stayed zero:

```text
ordinary_rate_per_s ~= 2.0M-4.1M
budget_exit_rate_per_s = 0
```

Therefore dev83/dev84's correlation was not causal: the voluntary inner budget
guard is not manufacturing the callback storm.

The final 60 cadence windows average roughly:

- ordinary callbacks: ~2.99M/s;
- direct-BL rescues: ~12.1k/s;
- completed main-loop frames: ~33.0/s.

The dev81 phase profiler typically attributes late gameplay frames around:

- `disp`: ~7.4 ms;
- `main`: ~8.0 ms;
- `done`: ~15.8 ms.

The legacy `simulation cadence spmarioMain` counter is not a completed-frame
counter in the active host-first build: phase 2 is observed once by the public
host callback and again by dev81's native phase hook. A ~60-62/s spmarioMain
counter can therefore coexist with ~30-33 completed phase windows. Use
`M14 main-loop phases frames/host_ms` as the frame-cadence metric.

## Measured ordinary hot PCs

The final 1/4096 ordinary callback heavy-hitter sample retained:

```text
0x8025CABC
0x8025CAD4
```

Project symbol context places both inside the R8PE01 US2 MSL `strcmp` body.
These were already hot in the dev79 run. dev85 proves they are not budget-exit
telemetry artifacts; they are materialized public-dispatch PCs paying the
host-first callback before native lookup.

## dev86 change

New tool:

`tools/native_bridge/apply_llvm_hot_ordinary_fastpath.py`

The generated public dispatcher changes ordering only for the two exact measured
PCs:

```text
if address == 0x8025CABC or 0x8025CAD4:
    try dolrecomp_call_original first
    if native lookup refuses:
        fall through immediately to the unchanged host-first path
all other addresses:
    unchanged host-first path
```

This is intentionally not dev80.

dev80 attempted a broad static reconstruction of legal native entries and
regressed early boot at `0x8026CA60`. dev86 has no global legal-entry policy,
no learned negative cache and no range-wide native-first behavior.

Before patching `generated.h`, generation proves from the player's private
combined DOL + map that each hot PC:

1. is executable;
2. is not an LR-writing linked transfer;
3. is not a platform host target;
4. is not a semantic hosted-call/relF map row;
5. is not one of the retained GX/HOME/DSP/post-MTMSR/frame-phase exact semantic
   coordinates.

If any proof fails, generation stops. If native lookup fails at runtime, the
historical host-first path remains the immediate fallback.

No Nintendo instruction bytes are persisted in source control. The local build
log prints the verified raw words only as player-local diagnostic evidence.

## Why this target first

The remaining direct-BL rescue rate at the end is roughly 10-16k/s, two orders
of magnitude below the 2-4M/s ordinary callback rate. The long-run rescue sample
is also contaminated by the AFK wedding interval. Do not prioritize another BL
source partition before measuring dev86.

The renderer's host `Present()` call remains only about 0.2 ms average. The
~30-33 Hz result tracks the completed game-loop phase budget, not a 30 ms host
present call.

## Next test

Run:

`M14-LLVM-UPDATE-RUN.cmd`

No manual cache wipe.

Expected generation marker:

```text
[OpenSPM] LLVM dev86 measured hot-ordinary fast path:
0x8025CABC=raw:0x... 0x8025CAD4=raw:0x...
native-first only for these exact PCs, host-first fallback retained.
```

Gates:

1. build/source contracts pass;
2. boot past the old dev80 `0x8026CA60` frontier;
3. pass at least one wedding;
4. play >=30 seconds in a known laggy NPC area;
5. compare `ordinary_rate_per_s` and `M14 main-loop phases frames/host_ms`
   against dev85.

Strong success means a large drop from the old ~2-4M ordinary callbacks/s and a
material rise above the old ~30-33 completed frames/s. If callback pressure
collapses but frame cadence does not, stop dispatch work and use the dev81 phase
ownership directly.
