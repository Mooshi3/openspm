# M14 dev132 — 99.943 Hz monitor-half cadence diagnostic

Date: 2026-09-21
Branch: `m14-native-execution`

## Why this exists

The player reports a desktop monitor refresh of **99.943 Hz**.

That matters because Super Paper Mario's authoritative simulation currently runs
at 60 Hz. A 60 Hz source cannot map evenly onto a ~100 Hz fixed-refresh display:

```text
99.943 / 60 ~= 1.6657 display refreshes per game frame
```

Without interpolation or a refresh rate that is an integer multiple of 60,
successive source frames necessarily persist for unequal numbers of physical
refreshes. Horizontal camera/world scrolling makes this cadence much more
visible than local sprite/character animation.

This does **not** replace the dev129 scheduler fix. dev129 addresses a real
OpenSPM artifact where an empty Nintendo scheduler idle loop could burn inside an
LLVM slice before the host recognized true idle. dev132 tests a distinct
question:

> after host/scheduler tails are minimized, how much of the remaining
> camera-only judder is simply 60 Hz content displayed at ~100 Hz?

## Diagnostic design

A full high-refresh interpolation implementation is too large to build before
proving this display-cadence hypothesis.

Instead dev132 creates a deliberately slow diagnostic source rate that should
map almost exactly 2:1 onto the player's monitor.

Player monitor:

```text
99.943 Hz
```

Half-rate diagnostic simulation:

```text
49.9715 Hz
period = 20,011,407 ns
```

Thus:

```text
99.943 / 49.9715 = 2.0000
```

The game will run roughly **16.7% slower than normal**. That is intentional and
makes this unsuitable as a production mode.

## Launcher

Compare these two runs on the same Yold walking path:

```bat
M14-LLVM-SMOOTH-RUN.cmd
M14-LLVM-100HZ-CADENCE-RUN.cmd
```

Both retain:

- dev129 state-sensitive scheduler-idle side exit;
- dev125 cached QPC frequency;
- dev126 final true-idle spin;
- dev127 fastest-EfficiencyClass CPU Set preference;
- dev119 Immediate presentation;
- FIFO Drain;
- sticky pressed-edge retention ON;
- normal thread priority;
- HighQoS OFF;
- chunk256;
- state_mem=1;
- heavy_diag=0.

The only simulation/pacing difference is:

```text
OPENSPM_M14_DIAG_HALF_99_943HZ=1
```

which replaces the normal 16,666,667 ns VI period with 20,011,407 ns.

Expected marker:

```text
M14 dev132 diagnostic cadence active:
VI period=20011407ns (~49.9715 Hz),
exactly half of player-reported 99.943 Hz monitor rate;
gameplay is intentionally ~16.7% slow for cadence diagnosis only.
```

dev131 also logs the SDL display mode OpenSPM actually sees:

```text
M14 display cadence current=...
```

## Interpretation

### Camera becomes clearly even/smooth, but gameplay is slower

This is strong evidence that most of the final camera-only symptom is ordinary
60-on-100 cadence rather than another guest-camera or producer bug.

Production direction:

- restore authoritative 60 Hz simulation;
- implement high-refresh presentation decoupling/interpolation;
- never solve it by changing simulation speed.

### Camera has essentially the same irregular hitch pattern

The monitor cadence is not sufficient to explain the defect. Keep dev129/dev130
and continue with dev128's remaining producer ownership / host Present tails.

### Camera improves somewhat but still has occasional distinct hitches

Both effects exist:

1. periodic 60-on-100 cadence judder;
2. sparse OpenSPM missed/held frames.

That is plausible given the six-way logs: fastcore/final-spin reduced but did
not eliminate >25 ms Present tails.

## Safety

This mode is diagnostic only.

- no save format changes;
- no guest scheduler state is fabricated;
- no renderer ordering changes;
- no monitor mode is changed;
- no Windows display settings are changed;
- no interpolation is claimed;
- simply exit and run any normal launcher to return to 60 Hz.

The ordinary default remains exactly 60 Hz when the environment variable is
unset.
