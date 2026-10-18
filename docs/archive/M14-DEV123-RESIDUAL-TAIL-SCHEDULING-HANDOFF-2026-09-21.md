# M14 dev123 — residual-tail scheduling + sticky-input isolation A/B

Date: 2026-09-21
Branch: `m14-native-execution`

## Historical correction: do not reopen the old average-deadline diagnosis

The player correctly recalled that OpenSPM already reached a first-retrace path
below 16.667 ms while camera/world-scroll judder remained.

dev114's canonical Yold A/B measured:

- display + main: 11.227 ms median;
- DrawDone: 2.287 ms median;
- first-retrace critical path: 13.514 ms;
- nominal VI margin: +3.153 ms.

Camera judder remained.

dev118 then measured the VI boundary directly. In the representative heavy case,
most VI deliveries were already reached from true Nintendo scheduler idle, often
with roughly 2–3 ms of ready margin. That explicitly demoted
"the game is generally too slow to meet VI" as the remaining root cause.

dev119 subsequently showed that Mailbox presentation was a much larger visible
amplifier: forcing Immediate cut the player's perceived jitter to roughly one
quarter of its previous level.

Therefore:

- do **not** restart a broad "lower average work below 16.667 ms" campaign;
- do **not** read dev81/dev122 `done_us` as pure producer work.

`done_us` intentionally spans DEMODoneRender through the next
DEMOBeforeRender and includes synchronous renderer work, Nintendo scheduler
idle, host waiting, VI delivery and thread resumption.

## What dev122 adds to that history

The 2026-09-21 dev122 run still shows camera-5 position/target/view translation
advancing smoothly during most walking-related ~33 ms visible holds.

It also shows the remaining problem is sparse rather than the old global
30-fps collapse:

- ordinary Yold windows complete roughly 56–60 visual frames/s;
- dev118 often reports only about 2–5 `busy_due` deliveries per second;
- current DrawDone is around 2.5 ms median with ~3.25 ms p95;
- walking-related dev122 tails have roughly 14.2 ms median display+main.

That means the *tail* can still sit on the 16.667 ms cliff even though ordinary
and median frames are safely below it. This does not contradict dev114/dev118.

The correct open question is now whether those few late producer frames are
caused by genuine deterministic game/GX work or by host scheduling/preemption.

### Strong dev122 correlation

A direct line-window analysis of the player log
`launch(20260921-044640).log` makes the residual much less ambiguous.

Across the steady gameplay portion used for walking:

- per-second dev118 `busy_due` count vs >25 ms host-Present tail count:
  Pearson correlation approximately **0.98**;
- `busy_due` count vs dev122 >20 ms complete-frame tail count:
  Pearson correlation approximately **0.99**;
- observed totals in that slice: 119 `busy_due` deliveries vs 104
  Present/frame-tail events.

This is not proof that every `busy_due` event is visible, because the report
windows and tail thresholds differ. It is strong evidence that **the residual
post-Immediate hitch is now the rare producer/VI tail**, rather than a second
independent downstream-present problem.

That conclusion is deliberately narrower than the rejected old diagnosis:
ordinary/median frames are already safely fast enough; the target is only the
few frames per second that fail to reach idle before VI.

The same steady walking tail samples also argue against input cancellation:
55 representative tails while Mario was continuously moving had median absolute
Mario delta about **3.0 units/frame** (roughly 2.09–3.05 over that sample).
The camera view translation also continued moving. The visible hold therefore
does not coincide with Mario receiving a zero-direction frame.

## Sticky-input audit

The player's "sticky inputs" concern is worth preserving, but the implementation
does **not** currently replay held directions.

The active path has three distinct concepts:

1. `g_v9_input_down`
   - sampled from physical keyboard state once per native retrace/input epoch;
   - written directly into KPAD `buttonsHeld`;
   - Left/Right remain ordinary held bits.

2. `g_v9_input_pressed`
   - edge mask: `down & ~previous_down`;
   - dev27/dev29 may carry an *unconsumed pressed edge* across retraces until the
     real guest `wpadGetButtonsPressed()` observes it.

3. `g_v12_input_repeat`
   - SDK menu-repeat marker `0x80000000`;
   - ORed into `buttonsHeld` after the configured initial/repeat cadence;
   - does not replace or toggle the actual D-pad bit.

Therefore the sticky system cannot directly alternate/cancel a continuously held
direction: the held D-pad bit comes from `g_v9_input_down`, while only the
pressed-edge stream is sticky.

Still, dev123 exposes a clean A/B because the feature was added during the
low-framerate era and should be proven unnecessary for this defect.

### No-sticky launcher

```bat
M14-LLVM-NOSTICKY-INPUT-RUN.cmd
```

This sets:

```text
OPENSPM_M14_STICKY_PRESSED=0
OPENSPM_M14_MAIN_PRIORITY=off
```

Only old pressed edges stop being republished after a retrace if the guest did
not consume them. `buttonsHeld`, physical direction sampling, released bits,
menu repeat, VI timing, renderer and game state are unchanged.

Expected marker:

```text
M14 dev123 sticky-pressed A/B disabled:
```

A/B caution: very short taps may again be easier for the guest to miss if a
frame stalls. Continuous walking directions should behave identically.

## Producer scheduling experiment

OpenSPM previously had no Windows producer-thread priority A/B.

Run:

```bat
M14-LLVM-PRIORITY-RUN.cmd
```

It retains the known best configuration:

- chunk256;
- state_mem=1;
- heavy_diag=0;
- precise VI wait enabled;
- Immediate presentation;
- FIFO Drain;
- sticky pressed-edge delivery explicitly ON.

It additionally sets:

```text
OPENSPM_M14_MAIN_PRIORITY=above_normal
```

The native launcher applies `THREAD_PRIORITY_ABOVE_NORMAL` only **after**
`openspm_renderer_load_and_bind()` has created/bound Aurora. This deliberately
avoids changing Aurora worker-thread scheduling and isolates the A/B to the
producer/main thread that enters `backend.run()`.

The previous priority is restored after the run.

No CPU affinity, process priority, guest timebase, VI deadline, GX ordering,
renderer ordering or game state changes.

Expected marker:

```text
M14 dev123 producer scheduling A/B active: main thread priority old=... new=...
```

## Test order

The two launchers are intentionally independent.

1. Test `M14-LLVM-PRIORITY-RUN.cmd` in the same Yold walking path.
2. On a separate launch test `M14-LLVM-NOSTICKY-INPUT-RUN.cmd`.

Do not combine them until each one-axis A/B has a player result.

## Interpretation

Priority A/B:

- If the remaining camera/world-scroll hitch materially drops and `busy_due`
  falls, retain a modest producer priority policy and investigate a polished
  Windows scheduling/QoS implementation.
- If subjective jitter and `busy_due` are unchanged, host preemption is
  demoted. Return to exact p95/p99 producer ownership rather than average-frame
  optimization.

No-sticky A/B:

- If camera scrolling unexpectedly improves while continuously holding a
  direction, instrument the exact KPAD held/pressed/repeat masks per guest frame;
  the source audit says this should not happen, so such a result would expose a
  higher-level input consumer interaction.
- If unchanged, retire sticky pressed-edge delivery as a jitter hypothesis. It
  may still be kept for reliable taps or removed later as obsolete compatibility
  behavior.

## Additional next candidates if both are negative

1. **P-core / EcoQoS audit.** The player's Intel 12th-gen hybrid CPU makes rare
   producer migration or efficiency scheduling a plausible tail source. Prefer
   Windows CPU-set / power-throttling telemetry before hard-pinning cores.
2. **Tail-only producer attribution.** Timestamp only frames whose dev118 path
   becomes `busy_due`, then split display, main and DrawDone without restoring
   broad per-draw/per-slice profiling.
3. **Host presentation identity.** Attach produced display-copy serial to the
   successful Immediate Present serial. This distinguishes late production from
   a downstream duplicated/held present without inferring from timing alone.
4. **Interpolation/pacing polish.** If rare 60-Hz simulation misses remain after
   deterministic work is healthy, host-side camera/world transform interpolation
   can hide an isolated repeated source frame without changing guest simulation.
   Treat this as polish, not a substitute for identifying avoidable stalls.

## Permanent reasoning note

A 33 ms dev122 frame-tail line alone is **not proof** that 33 ms of producer work
occurred. Always use dev118's `from_idle/busy_due` evidence plus measured
DrawDone/display/main work before deciding that a VI deadline was missed.
