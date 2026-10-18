# M14 dev118 — retrace pacing diagnosis + precise idle-wait A/B

Date: 2026-09-20  
Branch: `m14-native-execution`

## Why this exists

dev114/state-memory fixed the old NPC-heavy performance collapse. dev117 did not
materially change the remaining whole-frame hold/catch-up judder: heavy Yold
still completes roughly 56 game frames/s while authoritative VI remains near
60/s.

The existing frame profiler cannot identify the cause because its `done`
bucket is the entire interval from `DEMODoneRender` to the next
`DEMOBeforeRender`. That interval includes renderer synchronization, Nintendo
scheduler idle, Windows waiting, VI delivery and thread resumption.

The native VI path also uses `Sleep(1)` while Nintendo's scheduler is truly
idle and the fixed VI deadline is still in the future. Occasional Windows
oversleep is therefore a plausible remaining pacing source, but it had not been
measured directly.

## What dev118 measures

All LLVM runs now emit one `M14 dev118 pacing ...` line about once per host
second. There is no per-frame logging.

Important fields:

- `frames`: completed `spmarioMain` updates in the report window.
- `retraces`: authoritative delivered VI events.
- `from_idle`: deliveries whose exact VI deadline was already being waited on
  from Nintendo's true scheduler idle.
- `busy_due`: deliveries where the deadline arrived before the runtime had
  observed true scheduler idle.
- `ready_margin_avg_us`: how early the game reached idle before VI.
- `delivery_late_avg_us` / `delivery_late_max_us`: actual VI lateness.
- `sleep_cross`: host waits that began before the deadline and returned after
  it.
- `sleep_overshoot_max_us`: worst observed wait overshoot.
- `vi_handler_avg_us` / `vi_handler_max_us`: cost of the recompiled VI
  interrupt + Nintendo scheduler wake path.
- `expired`: fixed-grid deadlines skipped by the existing v20 policy instead
  of burst-delivered.

This separates "not enough frame-production headroom" from "frame was ready but
host waiting woke late."

## Candidate fix

Run:

```bat
M14-LLVM-PACING-RUN.cmd
```

It keeps the proven player baseline:

```text
chunk=256
state_mem=1
heavy_diag=0
OPENSPM_M14_VSYNC=0
```

and adds:

```text
OPENSPM_M14_PRECISE_IDLE_WAIT=1
```

The fixed 60 Hz VI grid, Nintendo scheduler behavior, guest simulation,
renderer/GX ordering and dev117 callback semantics are unchanged.

Only while Nintendo's scheduler is already truly idle:

- more than 2 ms before VI: historical `Sleep(1)`;
- at or inside 2 ms before VI: `Sleep(0)`.

Every poll still returns through the same native safepoint. This does not invent,
advance, burst-deliver or skip a retrace. It avoids asking Windows for another
millisecond sleep immediately before an already-established deadline.

For the exact same source with the wait candidate disabled, run:

```bat
M14-LLVM-DEV117-RUN.cmd
```

That launcher now explicitly sets `OPENSPM_M14_PRECISE_IDLE_WAIT=0`.

Expected module identity for both is the dev118 source family:

```text
m14-dev118-chunk256-sim1-yold-normal
```

and the pacing launcher should additionally log:

```text
M14 dev118 precise idle-wait A/B enabled
```

## How to read the next Yold result

1. **Jitter visibly improves and wait-crossing/lateness falls:** keep the hybrid
   wait. A stable ~56 produced fps is acceptable for now if delivery is even.
2. **`busy_due` tracks the missing ~4 frames/s:** the holds are real late frame
   production. Next target is DrawDone/end-of-frame work, not the host wait.
3. **Mostly `from_idle`, low VI lateness, still visibly jittery:** the native
   VI scheduler is cleared. Trace Aurora produced-frame/present identity next.
4. **VI handler spikes dominate:** investigate Nintendo wake/context-transfer
   work rather than camera or presentation.

## Safety / rollback

The feature defaults off when the environment variable is absent. No camera
logic, guest clock rate, callback ordering, renderer ordering or frame
interpolation was changed. `M14-LLVM-DEV117-RUN.cmd` is the same-build
rollback/baseline.
