# M14 session handoff — dev132 test gate / dev133 high-refresh path

Date: 2026-09-21
Branch: `m14-native-execution`
Handoff base head: `5a55c103d10a20ad08c6a125d044e841bc1d3aef`

## Where the project is now

The old broad performance problem is solved. The remaining visible defect is a
small camera/world-scroll judder while ordinary animation feels much smoother.

Do **not** restart the old "get the average frame below 16.667 ms" campaign:

- dev114 already measured a ~13.514 ms median first-retrace path in Yold;
- dev118 showed most VI deliveries reach Nintendo scheduler idle before the
  deadline;
- dev119 Immediate presentation removed roughly three quarters of the visible
  judder;
- dev122 showed camera-5 guest state/view translation continuing to advance
  through most visible held-frame events.

The residual has two plausible components:

1. sparse OpenSPM host/scheduler/present tails;
2. fundamental 60 Hz source cadence displayed on the player's **99.943 Hz**
   monitor.

## Player-tested six-way scheduling/input A/B

Steady `seq=2 stage=1` gameplay windows were quantified:

| variant | busy_due/s | >25 ms Present tails/s |
| --- | ---: | ---: |
| baseline Immediate | 1.405 | 1.094 |
| dev126 final-spin | 0.926 | 0.603 |
| dev123 ABOVE_NORMAL | 1.165 | 0.952 |
| dev124 HighQoS | 1.684 | 1.263 |
| dev127 fastcore | 0.798 | 0.677 |
| no-sticky | 2.045 | 1.698 |

Player report matched the measurements:

- fastcore felt best of the independent variants, though still imperfect;
- no-sticky was clearly worst and could feel like Mario stopped for a frame;
- most other variants felt similar.

Keep sticky pressed-edge retention ON. The sticky mechanism carries only
unconsumed `buttonsPressed` edges; held directions are freshly sampled each
input epoch. HighQoS is not part of the preferred candidate.

## dev129 / dev130

dev128 revealed that most nominal `busy_due` events were actually observed at
Nintendo's scheduler-idle PC `0x8027551C`. That exposed an ownership artifact:
the LLVM path could continue executing the idle loop inside a native slice
before the host recognized the empty run queue.

dev129 adds the state-sensitive idle seam:

- idle PC + empty RunQueueBits -> private host side exit / park immediately;
- idle PC + runnable RunQueueBits -> preserve dev68/dev90 native SelectThread
  re-entry;
- invalid/unknown runqueue state -> fail open to Nintendo/native execution.

An executable synthetic regression now covers all three states.

dev130 is the combined "smooth" candidate and intentionally stacks only the
independently helpful settings:

- dev129 dynamic scheduler-idle handling;
- dev125 cached QPC frequency;
- dev126 final <=2 ms true-idle spin;
- dev127 fastest-EfficiencyClass soft CPU Set preference;
- dev119 Immediate presentation;
- FIFO Drain;
- sticky pressed-edge delivery ON;
- normal Win32 priority;
- HighQoS OFF.

Launcher:

```bat
M14-LLVM-SMOOTH-RUN.cmd
```

## dev131 display diagnostic

Aurora now logs the actual SDL display mode:

```text
M14 display cadence current=...
refresh_hz=...
precise=NUM/DEN
ratio60=...
nearest_multiple=...
error_hz=...
integer_multiple=...
```

This is observation-only.

## dev132 decisive cadence A/B

The player reports a **99.943 Hz** monitor.

A 60 Hz source cannot map evenly to ~100 Hz without interpolation:

```text
99.943 / 60 ~= 1.6657
```

That naturally creates unequal source-frame persistence during camera pans even
when the game produces every 60 Hz frame correctly.

dev132 is a deliberately slow diagnostic that makes the source cadence exactly
half the reported display rate:

```text
simulation ~= 49.9715 Hz
period = 20,011,407 ns
99.943 / 49.9715 ~= 2.0
```

Run back-to-back:

```bat
M14-LLVM-SMOOTH-RUN.cmd
M14-LLVM-100HZ-CADENCE-RUN.cmd
```

The second run is **diagnostic only** and intentionally runs gameplay ~16.7%
slow. It must never become the production gameplay rate.

Interpretation:

- camera becomes evenly smooth but slower -> 60-on-100 display cadence is a
  major remaining cause; proceed to decoupled high-refresh presentation;
- same irregular jitter -> continue producer/present-tail debugging;
- partially better -> both display cadence and sparse OpenSPM holds matter.

Expected dev132 marker:

```text
M14 dev132 diagnostic cadence active:
```

## dev133 production design if dev132 is positive

Read:
`docs/archive/M14-DEV133-HIGH-REFRESH-PRESENTER-DESIGN-2026-09-21.md`.

Production must keep Wii simulation/VI/input/audio at 60 Hz and decouple host
presentation.

Important renderer lifetime finding:

- active `GXCopyDisp` handling creates an `openspmDisplayCopySnapshot`
  `gfx::TextureHandle` for the current asynchronous present;
- that shared handle keeps the texture allocation alive for the current present;
- **it is not immutable cross-frame history**;
- Aurora's `copyTextureCache` may reuse the same allocation on later
  `GXCopyDisp` calls, so simply retaining the shared pointer can show newer
  pixels.

Therefore the next implementation proof, tentatively dev134, should create a
presenter-owned **3-slot GPU texture history ring**:

```text
previous / current / spare-in-flight
```

At each completed Wii display copy, GPU render-blit the current sampleable XFB
into the next dedicated history texture before a later `GXCopyDisp` can reuse
the cache allocation.

Why render-blit first:

- the current XFB snapshot is sampleable;
- Aurora has render-texture allocation and copy-bind-group primitives;
- the current GX copy texture usage/lifetime should not be changed merely to get
  cross-frame history;
- a later optimization can add validated CopySrc/CopyTextureToTexture support.

GPU work must remain on Aurora's existing render worker. Do not create a side
thread that calls Dawn/WebGPU directly.

## Interpolation staircase

Do not jump straight to full semantic interpolation before dev132.

If dev132 is positive:

1. **P1 / dev134:** presenter-owned three-frame texture ring + decoupled display
   clock; nearest completed frame only. This proves lifetime and 99.943 Hz host
   presentation, but will not remove intrinsic 60-on-100 motion cadence.
2. **P2 optional diagnostic:** full-frame blend between previous/current XFB.
   Hidden proof only; ghosting makes this unsuitable as the normal renderer.
3. **P3 preferred first real smoothing:** semantic camera interpolation while
   simulation remains 60 Hz.
4. **P4:** actor/model transform interpolation with snap rules for teleports,
   scene changes, 2D/3D flips, camera-mode discontinuities, stale history, etc.

Do not add an unconditional one-simulation-frame latency just to interpolate.

## Exact resume order

1. Pull branch and verify head includes this handoff.
2. Player runs:
   `M14-LLVM-SMOOTH-RUN.cmd`
   then
   `M14-LLVM-100HZ-CADENCE-RUN.cmd`.
3. Inspect subjective camera cadence plus:
   - `M14 display cadence current=`
   - dev129 dynamic scheduler-idle stats
   - dev128 busy-due owner lines
   - dev118 pacing
   - >25 ms Present tails.
4. If dev132 is smooth-but-slow, start dev134 3-slot presenter-history proof.
5. If dev132 is neutral, do not build interpolation yet; return to residual
   producer/present ownership.

## Relevant durable commits

Recent chain at handoff time:

```text
5a55c103d10a20ad08c6a125d044e841bc1d3aef Correct dev133 XFB history lifetime model
14b39d10f96bc98ee6da002a74f122aff8ebeefb Document dev133 high-refresh presenter architecture
50f22d743624e2aa5fef0e9040cfac9b75bcbe24 Add dev132 100Hz cadence diagnostic
24a024adfc2a4faead9888a29abaa1ac3aafd44d Add executable dev129 idle-dispatch regression
c6b4c50b5840013bafd776db59a1654d4334d6cb Add dev131 display cadence diagnostic
```

No GitHub Actions are required or authoritative for this workflow.
