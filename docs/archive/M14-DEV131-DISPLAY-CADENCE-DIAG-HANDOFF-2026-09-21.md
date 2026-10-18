# M14 dev131 — display-refresh cadence diagnostic

Date: 2026-09-21
Branch: `m14-native-execution`

## Purpose

The remaining visible defect is disproportionately obvious during horizontal
camera/world scrolling. dev122/dev128 show that this is not simply a frozen
camera transform, and dev129 now repairs a separate scheduler-idle ownership
artifact.

There is one additional presentation-level possibility that should be measured
before implementing high-refresh interpolation: the desktop display refresh may
not be an integer multiple of the authoritative 60 Hz Wii simulation rate.

Even when every 60 Hz game frame is produced on time, a non-integer display
ratio (for example 144/60 or 165/60) can make successive source frames persist
for unequal numbers of physical refreshes. Full-screen camera panning exposes
that cadence much more strongly than local character animation.

dev131 is therefore **observation-only**. It does not change present mode,
vsync, VI timing, renderer ordering or simulation cadence.

## Six-run result that motivates the next baseline

The player's six independent A/B runs were parsed only for steady
`seq=2 stage=1` gameplay windows.

| Variant | sampled seconds | busy_due/s | >25 ms Present tails/s |
| --- | ---: | ---: | ---: |
| baseline Immediate | 67.6 | 1.405 | 1.094 |
| dev126 final spin | 46.4 | 0.926 | 0.603 |
| dev123 ABOVE_NORMAL | 51.5 | 1.165 | 0.952 |
| dev124 HighQoS | 64.1 | 1.684 | 1.263 |
| dev127 fastcore | 493.6 | 0.798 | 0.677 |
| no-sticky | 48.9 | 2.045 | 1.698 |

This supports the player's subjective report:

- fastcore felt best and was quantitatively better than baseline;
- final-spin also moved the tail rate in the helpful direction;
- no-sticky was clearly worse both subjectively and in the logs;
- HighQoS should not be stacked into the candidate.

Accordingly dev130 combines only fastcore + final-spin and keeps sticky pressed
edges ON.

## Diagnostic

The pinned Aurora `lib/window.cpp` now logs the SDL display mode associated
with the actual OpenSPM window immediately after window creation.

Expected marker:

```text
M14 display cadence current=WIDTHxHEIGHT
  refresh_hz=...
  precise=NUM/DEN
  ratio60=...
  nearest_multiple=...
  error_hz=...
  integer_multiple=true/false
```

The diagnostic uses:

- `SDL_GetDisplayForWindow(g_window)`;
- `SDL_GetCurrentDisplayMode(display)`;
- `SDL_DisplayMode.refresh_rate`;
- the precise numerator/denominator fields exposed by SDL3.

`integer_multiple=true` uses a narrow ±0.25 Hz tolerance around
`60 * nearest_multiple`. This is diagnostic labeling only; no behavior depends
on it.

## Interpretation

1. **dev129/dev130 remove the visible hitch and the display is an integer
   multiple of 60 Hz**

   The problem was host/scheduler tail behavior. Keep the interpolation work as
   a later high-refresh feature rather than a bug fix.

2. **dev129/dev130 remove most missed-frame tails but camera scrolling still
   shows a small periodic judder, and the display is not an integer multiple of
   60 Hz**

   The remaining symptom is a strong candidate for ordinary 60-on-N-Hz cadence.
   The correct long-term solution is the already-planned decoupled
   high-refresh/interpolated presentation path, not more Wii scheduler changes.

3. **The display is a clean 120/180/240 Hz multiple and camera jitter remains**

   Continue tracing host frame production/presentation. Display-ratio judder is
   then unlikely to explain the residual.

4. **The log reports an unavailable display mode**

   Do not infer a ratio. The diagnostic fails closed and changes nothing.

## Next player test

After pulling the current branch:

```bat
M14-LLVM-IMMEDIATE-RUN.cmd
M14-LLVM-SMOOTH-RUN.cmd
```

The first isolates dev129. The second adds only the two independently helpful
scheduling settings: final-spin + fastcore.

For either log, retain:

- dev129 empty/runnable/unknown idle stats;
- dev128 busy-due owner lines;
- dev118 pacing;
- host Present tails;
- the new `M14 display cadence current=` line.
