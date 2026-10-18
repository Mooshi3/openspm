# dev68: resume runnable threads before the next VI deadline

Branch: `m14-native-execution`. Base: `451c2bf77c4824ac587b8067023b84c57be2fe6d`.

Status: concrete host scheduling defect repaired, production C behavior tested
with synthetic events, full bridge syntax checked. **Player gameplay/FPS has
not yet been measured on dev68.** Run `M14-LLVM-UPDATE-RUN.cmd`; no cache/save wipe.

## Evidence from the new player log

Input: `launch(20260916-232035).log`, revision `451c2bf77c48`, dev67.

- 152 cadence windows: weighted 27.52 `spmarioMain` updates/s versus 59.90 retraces/s.
- 68 windows round to 30 updates/s; 17 round to 20. Other windows vary.
- 4,260 GX finish completions; **every logged completion leaves RunQueueBits
  equal to `0x00008000`**. The handler has made a guest thread runnable.
- 4,261 presents, mean interval 36,448 us, median bin 34,000 us; the presentation
  call itself averages 217 us. That does not measure all renderer work, but it
  rules out a 16 ms wait inside the presentation call as the explanation.
- dev67 records 155,644,000 us across 110,468 native slices. Its large
  `idle -> idle` bucket must not be interpreted as native CPU work.
- `hang_80065128_max=0` in this run. This alone does not establish that the
  previous wedding AFK case was revisited or fixed.

dev66 already reduced underlying ordinary-router traffic by about 99.4% without
lifting the ~30 Hz ceiling. Keep its speculative callback cache retired.

## Root cause in the host scheduler

The production run chain reaches `bridge_run_dev50` in v39, which calls the
underlying execution stack and then the real, signature-verified GX finish
handler when a FinishQueue waiter is present at scheduler idle.

The finish handler returns normally after setting the real guest RunQueueBits.
The persistent CPU can still be at `0x8027551C`, the native SelectThread idle
continuation. This is correct: native execution must now re-read the queue and
select the newly runnable thread.

But `bridge_m14_service_retrace` in v7 previously used **only that PC** to decide
to `Sleep(1)` and return `idle_wait=1` on every slice until the next VI deadline.
The awake thread could not run even though guest OS state said it was runnable.
When it finally resumed, it could reach its real VI wait and consume another
retrace. This host-imposed wait can create a two-retrace/30 Hz frame floor even
when frame work fits in 16.7 ms. Lower heavy-scene cadence may still include real
compute/render cost; the new player run must measure that separately.

## Repair and scope

In the LLVM build only, v7 checks the mapped, aligned US2 RunQueueBits word at
live `r13 - 29552` before the pre-deadline idle sleep. These coordinates were
already derived and used by dev39/dev47, not imported from another revision.
If it is nonzero, leave `idle_wait=0` and run the existing native scheduler.

- No PC, LR, TCB, run queue, callback, or saved guest context is fabricated.
- The check reads live RAM each time; there is no learned callback bypass/cache.
- Empty queues retain the existing short host yield.
- Unmapped/unaligned queue addresses retain the old wait behavior without an
  unsafe read or invented runnable state.
- Due VI events still use the existing interrupt-enable gate and fixed-phase
  deadline grid. No extra retraces, catch-up, or timebase changes.
- Generated-C behavior is unchanged behind `DOLRECOMP_BACKEND_LLVM`.
- LLVM codegen/objects, dev63 admission cache, v19 GX semantics and input are
  unchanged. This is a small bridge rebuild, not another compiler regeneration.

The active v42 provenance becomes `m14-dev68-llvm-runnable-idle-wake`.
Success logs for GX finish are bounded to the first eight/powers of two instead
of formatting/flushing one per produced frame. Failures and final totals remain.

## Measurement corrections

MSVC `clock()` returns wall-clock elapsed time, with a 1 ms unit, unlike the ISO
CPU-time definition. v42 now uses the existing monotonic host clock and reports
the actual time spent in v7's idle sleep separately. Neither is a CPU profiler.

The old weighted Space-Saving sketch also assigned an evicted bucket's total to
its replacement PC pair. A newly retained pair with `calls=1 us=10011000` did
**not** prove a ten-second call (the same log's maximum slice was 276 ms).
dev68 reports `estimate_us`, `error_us`, and `retained_calls`; the pair's true
total is bounded by `[estimate_us - error_us, estimate_us]`. Entries/counters
from this approximate sketch must not be read as exact attributed CPU timings.

New startup marker:

```text
M14 LLVM dev68 runnable-idle wake active:
```

First eight relevant resumes:

```text
M14 LLVM dev68 runnable idle resume #... runq=0x00008000 next_retrace_us=...
```

At teardown:

```text
M14 LLVM dev68 slice stats ...
M14 LLVM dev68 idle stats runnable_resumes=... idle_polls=... idle_sleep_us=... idle_entry_calls=... idle_entry_wall_us=...
```

`next_retrace_us` is the time to the next deadline when runnable work resumes;
it is not a measured end-to-end performance gain. `idle_entry_wall_us` includes
any work executed after a wake; only `idle_sleep_us` measures the actual sleep.
The existing `dev67 stationary diagnostic` marker is retained for hang snapshots.

## Validation performed

`python tools/native_bridge/test_llvm_idle_wake.py` compiles the actual v7
retrace service and v12 fixed-phase wrapper, then executes them with synthetic
RAM, clock, PE wake and VI events. It uses no Nintendo code/assets. It proves:

- runnable work resumes before VI with no guest state or deadline mutation;
- empty queues still sleep; invalid/unaligned addresses are not read;
- non-idle execution continues; first clock arm still works;
- due VI delivery and MSR[EE] deferral remain intact;
- a thread doing 5 ms work, waiting on PE finish, then waiting on VI produces
  **120 frames/4 seconds using the preserved PC-only path versus 239/4 seconds
  using the LLVM repair**. This is a deterministic fixture, not a game benchmark.

`python tools/native_bridge/test_llvm_native_pipeline.py` passes.

Full LLVM v42 and generated-C v13 include chains pass GCC C11 syntax checks,
including implicit-function and format errors, using pinned public DolRecomp
`40637c...` CPU headers and synthetic generated metadata. This is not a Windows
DLL link or gameplay test. The executable regression also compiles the Windows
sleep branch against a fake Sleep function.

Existing `test_event_bridge_contract.py` fails its historical "root Windows
backend advances through dev26 compatibility entry" assertion because it expects
`14.0-dev26` while the untouched backend already advertises `14.0-dev27`. That
stale assertion predates these edits; do not interpret it as an idle-wake failure.

## Next player run and interpretation

Run `M14-LLVM-UPDATE-RUN.cmd`, visit the same light and heavy scenes, and close
normally after a minute or two. Check the new startup marker and send the log.

1. Rising `runnable_resumes` confirms that the old PC-only gate would have
   parked work in this run. Compare `spmarioMain` cadence with the matching scene.
2. If light scenes reach ~60 but heavy ones remain ~30, the artificial wait is
   gone; profile the remaining active frame work against 16.7 ms. Do not undo
   the correct run-queue check or resurrect the speculative dev66 cache.
3. If cadence stays ~30 despite resumes, capture phase timings/host CPU samples
   and the native scheduler continuation. Check for another wait/double cadence
   constraint rather than treating dispatch counts as time measurements.
4. If any new liveness failure occurs, retain the exact log and inspect native
   context resumption. Do not skip guest PCs or fabricate sequence state.

Hosted CI minutes remain exhausted. No local game input/Windows runtime was
available in this session; the player's run remains the gameplay acceptance gate.

## Primary-source research

- [Microsoft clock documentation](https://learn.microsoft.com/en-us/cpp/c-runtime-library/reference/clock?view=msvc-170):
  explains Windows wall-clock behavior, 1,000 units/s, and the distinction from
  CPU accounting. This invalidates the earlier handoff's CPU-time interpretation.
- [Dolphin PixelEngine](https://github.com/dolphin-emu/dolphin/blob/master/Source/Core/VideoCommon/PixelEngine.cpp):
  `SetFinish` raises a PE finish event and the main-thread callback updates the
  PE interrupt. Completion is an independent event, not intrinsically a VI wake.
  No Dolphin runtime or source was added to the port.
- [Pinned DolRecomp CPU interface](https://github.com/ExpansionPak/DolRecomp/blob/40637c4683bd2820ac5b23607ee344720beb26df/src/cpu/cpu.h):
  used for the full bridge syntax check. Compiler/runtime ABI was not changed.
