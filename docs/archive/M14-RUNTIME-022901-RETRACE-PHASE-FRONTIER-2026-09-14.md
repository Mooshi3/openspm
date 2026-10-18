# M14 runtime 022901 — retrace phase frontier (2026-09-14)

## Tested revision

`69a8c58104c08c7d76fffc2eb16b9fbb18fc0dc0`

Runtime log: `launch(20260914-022901).log`.

## What this run ruled out

The cumulative Aurora frame-acquisition probe disproved hidden GPU/resource wait as the steady slowdown:

- frame-slot at 8,192 acquisitions: `total_us=167`, `max_us=9`, `slow=0`;
- staging-slot: `total_us=7`, `max_us=7`, `slow=0`;
- staging-map: `total_us=3`, `max_us=2`, `slow=0`.

Those costs are far too small to explain a persistent 52–58 retraces/s runtime.

The host-event throttle also remains valid cleanup, but event-pump stalls are sparse and cannot explain the steady deficit.

## Remaining renderer pressure

Synchronous `GXDrawDone` Drain-mode parsing is still visibly expensive in heavy gameplay. By DrawDone call 6,348, the sparse profiler had counted 256 drains at or above 16 ms, with maxima around 39 ms in this run. Heavy `(seq=2, stage=1)` timing windows remain centered near ~52 retraces/s.

This confirms that complex-scene GX CPU work still contributes substantial frame pressure, but DrawDone is also a forcing boundary for deferred synchronous parser work rather than proof that the DrawDone semantic itself is wrong.

Do **not** restore Aurora's retired worker-thread mode: that mode consumed mutable guest-backed indexed arrays after Broadway had rewritten/reused them and caused proven graphics/gameplay corruption.

## Higher-value scheduler finding

The active native VI scheduler in `tools/native_bridge/legacy_c_adapter_v7.c.in` explicitly used delivery-anchored scheduling after every successful retrace:

```c
/* No catch-up burst. Anchor the next simulation tick to delivery time. */
now = bridge_m14_now_ns(instance);
g_m14_next_retrace_ns = now + OPENSPM_M14_RETRACE_PERIOD_NS;
```

That means host-side latency becomes part of the *next* simulation period. Even a small amount of retrace-handler / renderer / scheduler overhead permanently shifts all following deadlines later. This matches the runtime evidence:

- light scenes already hover around 56–58 retraces/s;
- heavy synchronous GX work drives the effective rate toward ~52/s or lower;
- the authoritative 60.75 MHz Wii timebase itself remains correct and must not be scaled.

## v20 phase-lock experiment

A timing-only wrapper was added in `tools/native_bridge/legacy_c_adapter_v12.c.in` above the protected v19 display-list bridge.

The wrapper:

1. remembers the deadline that was scheduled before the proven base run;
2. lets the base bridge deliver at most its existing single retrace;
3. advances the next deadline from the *scheduled* deadline, not handler completion time;
4. if host time has already passed additional slots, advances over those slots without delivering catch-up interrupts;
5. therefore never burst-delivers VI events and never intentionally accelerates guest simulation.

Expected runtime marker:

`M14 native retrace phase-lock active: fixed 60 Hz deadline grid; elapsed deadlines are skipped, never burst-delivered.`

When host stalls actually cross additional grid slots, sparse diagnostics report:

`M14 native retrace phase-lock expired_this=... expired_total=... deliveries=... next_in_us=...`

The local CMake target now builds bridge v20 and fails configure if the phase-lock marker is missing. Proven v19 graphics semantics remain underneath unchanged.

## Validation / rollback criteria

Run `M14-UPDATE-RUN.cmd` normally; do not wipe `.openspm` or saves.

Success evidence:

- light-scene authoritative timing moves from ~56–58 toward ~59–60 retraces/s;
- heavy gameplay improves materially above the previous ~52/s center even if expensive GX drains still cause isolated drops;
- gameplay speed remains real-time rather than visibly accelerated;
- input, animation, door/rotating-device correctness and v19 graphics remain intact;
- no burst of multiple VI handler deliveries appears at one scheduler pass.

Rollback immediately if:

- simulation visibly runs faster than real time;
- retrace callbacks burst/cascade after a stall;
- scheduler/context-transfer correctness regresses;
- known v19 graphics or interaction correctness regresses.

If phase-lock raises the baseline to ~60 but heavy scenes remain below target, the remaining frontier is genuine synchronous renderer CPU cost. The next measurement should reuse the already-paid guest-array stage timers with low-frequency aggregate reporting rather than restoring per-draw profiling.
