# M14 runtime 162213 — simulation cadence / native-slice frontier

## Player observation that changed the diagnosis

On `e3d6dfa51262fd5eb1014f4ef8e9fbf93d2078c9`, completed Wii display copies were presented immediately at DrawDone. That improved early host delivery, but the player still reported real gameplay slowdown:

- Koopas / shells move too slowly; Mario can outrun a shell.
- Mario movement jitters slightly forward/back even in lighter areas.
- large spiky boulder enemies roll too slowly;
- those boulders appear to spawn at roughly normal real-time cadence, so slow movers clump together.

This is stronger evidence than presentation telemetry alone. It means at least some gameplay systems are advancing at different effective rates.

## What 162213 proved about presentation

The completed-copy experiment is working as designed. The renderer consumes essentially one parsed Wii display copy at each game-produced DrawDone:

- at 2048: `drawdone_presents=2048`, `total_display_copies=2049`;
- at 4096: `drawdone_presents=4096`, `total_display_copies=4097`;
- at 8192: `drawdone_presents=8192`, `total_display_copies=8193`.

Therefore the old scheduler-idle presentation gate was a real latency contributor, but it is not the remaining root cause.

Host pacing after that fix:

- 2048 presents: avg 17.406 ms, p95 20 ms;
- 4096 presents: avg 18.151 ms, p95 30.5 ms;
- 8192 presents: avg 20.240 ms, p95 34.5 ms.

At 8192, average game-produced frame cadence is only about 49.4 Hz even though authoritative VI timing still reports 60/61 retraces/sec. DrawDone is ~5.13 ms average there, so renderer work is real but does not alone explain the ~20.24 ms mean frame interval.

## Existing `fps=60` diagnostic was not a true update-rate measurement

`backend/native/openspm_native_backend_v9.c` increments `g_v9_input_frame` at the host presentation/retrace epoch and reads the logged `fps` value from a guest field at `gp_ptr + 4`. Neither proves that SPM's top-level gameplay update completed 60 times per second.

This is why prior logs could claim `fps=60` while enemies visibly moved too slowly.

## True SPM update heartbeat

Seeky's SPM decomp shows the top-level game loop:

```c
while (true) {
    DEMOBeforeRender();
    spmarioDisp();
    spmarioMain();
    DEMODoneRender();
}
```

The repo's own normalizer states the player-local source is exact **R8PE01 revision 2**. The corresponding NTSC-U revision-2 symbol map gives:

- `spmarioMain = 0x801A7AAC`.

`tools/native_bridge/instrument_spmario_main_v20.py` now injects one call only at the durable guest entry label `label_801A7AAC`. A yielded/resumed invocation that resumes at an internal label does not count twice.

The bridge logs once per host second:

```text
M14 simulation cadence host_ms=... spmarioMain=... retraces=... total_updates=... pc=...
```

This is the first direct measurement of actual top-level SPM updates vs VI.

## Artificial native slice boundary

`backend/native/openspm_native_backend_v2.c` historically used:

```c
#define OPENSPM_NATIVE_SLICE_CYCLES 250000
```

and called `openspm_native_session_run()` once per slice in a tight backend loop.

A nominal 729 MHz Broadway has about 12.15 million CPU cycles per 60 Hz frame. DolRecomp's guest-cycle accounting is not assumed to be cycle-perfect, but 250k is still an extremely fine bring-up slice and can force many module/backend round trips per real game update. Every module run re-enters the v7 retrace/service wrapper.

### Current controlled A/B

The slice budget is now **2,000,000 guest cycles (8x)**. This is still bounded and should preserve multiple host-service opportunities within one nominal 60 Hz frame, while removing most artificial slice churn.

The backend samples every 128 native session calls and reports approximately once per host second:

```text
M14 native slice cadence host_ms=... calls=... guest_cycles=... avg_cycles=... safepoints=... budget=2000000.
```

No Nintendo simulation timing was intentionally changed. VI remains host-time fixed-phase; this experiment only allows more native work before returning through the backend migration loop.

## Interpretation of next Windows run

Primary observation remains player behavior: shell/enemy speed, boulder spacing, Mario jitter.

Then compare:

### Case A: `spmarioMain` was below 60 and rises materially toward 60 with 2M slices

This is the desired breakthrough. Native slice / service / dispatch scaffolding was starving the real game loop. Keep coarser slices and continue reducing migration round trips until the game reliably sustains 60 simulation updates/sec.

### Case B: `spmarioMain` remains ~40-55 while native slice calls drop sharply

The game is genuinely CPU-bound elsewhere in the current native architecture. Use the now-grounded update cadence to profile the remaining per-update CPU path; do not return to presentation/vsync guesses.

### Case C: `spmarioMain` is already ~60 while movement remains slow

Then update frequency is not the cause. Inspect SPM's own gameplay speed scaling (`marioGetGameSpeedScale`, gp timing fields, event/NPC update deltas) for a wrong time-scale value or duplicate/retrograde transform state.

### Case D: 2M slices delay VI / break correctness

Rollback only the budget change to 250k; keep the real `spmarioMain` cadence instrumentation. The counter is semantics-neutral and remains useful.

## High-refresh target

Do not run SPM gameplay at 200 Hz. First restore a stable authoritative 60 Hz `spmarioMain` simulation. Then build decoupled/interpolated presentation at ~200 Hz. That gives smooth high-refresh output without changing game speed and creates a clean base for mods such as flip-as-anyone, moonjump, superspeed, custom character/Pixl/item slots, etc.
