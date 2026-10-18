# M14 dev113: reproducible Yold chunk A/B harness

Date: 2026-09-20  
Branch: `m14-native-execution`

## Purpose

dev113 is a deliberately narrow performance experiment: change the generic
DolRecomp LLVM chunk ceiling from the retained 128-instruction baseline to 256
instructions while leaving semantic partitions, legal cold entries, host routing,
dev97 correctness partitions, dev105 fail-closed dispDraw nesting, renderer
semantics and VI pacing unchanged.

Yold Town / `he2_03` is the canonical benchmark because its seven active
townsfolk remain stable and reproduce the heavy display/main deadline pressure
without the actor-population drift seen in the spikeball room.

The benchmark harness added here makes the A/B reproducible and self-identifying
instead of relying on manually edited generator constants or hand-read logs.

## Build / launch commands

Default dev113 chunk256 candidate:

```bat
M14-LLVM-UPDATE-RUN.cmd
```

Retained chunk128 baseline:

```bat
M14-LLVM-CHUNK128-RUN.cmd
```

The main launcher also accepts the chunk geometry explicitly as its second
argument:

```bat
M14-LLVM-UPDATE-RUN.cmd AUTO 128
M14-LLVM-UPDATE-RUN.cmd AUTO 256
```

Only 128 and 256 are accepted. The value is threaded through the transactional
runner, local module builder and generator. Generated provenance must match the
requested value exactly or the build fails closed.

The selected value is also compiled into the native DLL. Runtime module identity
is therefore truthful:

```text
m14-dev113-chunk128-sim0-yold
m14-dev113-chunk256-sim0-yold
```

The once-per-second dev113 cadence line also includes `chunk=128|256` and
`state_mem=0`, so a scoped capture remains self-identifying even though the
startup banner happened before the capture began. Chunk comparison deliberately
filters to `state_mem=0`; later state-memory experiments cannot contaminate
the dev113 geometry A/B.

## Canonical Yold capture

With OpenSPM running, enter Yold Town / `he2_03`, then run:

```bat
M14-DEBUG-YOLD-BENCH.cmd
```

During the eight-second measurement window, run/jump steadily in one direction.

The script is fail-closed:

1. `require_map he2_03` refuses to benchmark another room.
2. `require_npcs 7` requires the canonical seven-active-townsperson count.
3. `require_npc_names mise,mura_1,mura_2,mura_3,mura_4,mura_5,son` requires
   the exact canonical actor-name set, so another seven-actor story state cannot
   masquerade as the same benchmark.
4. `stable_npcs 2.0 0.20` verifies the NPC count/table is stable before capture.
5. It records a scoped `yold-perf` launch-log capture.
6. It never freezes, hides, suspends or otherwise mutates NPCs.
7. It checks NPC stability again after capture.
8. Every scoped log receives a JSON provenance sidecar containing repo SHA,
   capture timing/source offsets, rotation status, byte count and SHA-256.
9. The launcher immediately runs the performance reporter with its quality gate;
   an incomplete/misattributed capture is saved but is not accepted as an A/B.

The reporter computes median produced-frame cadence, display/main phase cost,
DrawDone producer-sync cost when present, dev105 generated steps per callback,
and a first-retrace deadline model against the exact 60 Hz budget
(`1000 / 60 = 16.667 ms`).

The important interpretation is:

```text
critical ~= disp + main + synchronous DrawDone
margin   = 16.667 ms - critical
```

The measured `done` phase is reported separately and must not be treated as
pure CPU work; it includes the authentic wait for the next VI retrace.

## Reports and automatic A/B comparison

Current launch log:

```bat
M14-PERF-REPORT.cmd
```

Newest scoped Yold capture:

```bat
M14-PERF-REPORT.cmd --latest-capture yold-perf
```

After at least one self-identified 128 and one 256 Yold capture exist:

```bat
M14-PERF-REPORT.cmd --compare-latest-chunks
```

The analyzer scans the capture directory and independently selects the newest
valid chunk128/`state_mem=0` and chunk256/`state_mem=0` run. It will not
silently pair two captures from the same geometry or another compiler-state
mode. Conflicting identities inside one capture are reported rather than
guessed through.

For a specific historical A/B:

```bat
M14-PERF-REPORT.cmd path\to\chunk256.log --baseline path\to\chunk128.log
```

Structured output is available with `--json`.

Every new scoped capture is accompanied by a same-name `.json` manifest. The
analyzer verifies the sidecar's schema, filename and capture SHA-256 before
trusting it. Older captures without sidecars remain readable for historical
analysis; a present-but-invalid sidecar makes the quality gate fail.

For a one-command non-regression check:

```bat
M14-PERF-GATE.cmd
```

With no arguments this compares the newest valid chunk128/state_mem=0 Yold
baseline against chunk256/state_mem=0. Exit codes are designed for scripts/CI:

- `0`: valid benchmark and no configured material regression;
- `3`: benchmark invalid/incomplete;
- `4`: valid A/B but candidate crossed a regression tolerance.

Current default tolerances are explicit and overrideable: 3 fps median cadence,
0.75 ms first-retrace critical-path time, and 0.75 ms DrawDone.

## Why the dev113 A/B is worth testing

The dev108 reduced-partition layout lowered dev105 generated walking from about
22.9 to about 19.7 generated chunks per callback but did not escape the 30 Hz
cliff. The stable Yold run then measured roughly 370 dispDraw callbacks per
produced frame, including about 305/frame at dominant callback target
`0x8008533C`.

That leaves thousands of artificial generated-chunk transitions on the critical
path per frame.

Yold's typical deadline picture is only narrowly late:

```text
disp + main  ~= 15 ms
DrawDone     ~=  2.3 ms
total        ~= 17+ ms
first VI      = 16.667 ms
```

The candidate therefore does not need a 2x CPU improvement. Recovering roughly
1-2 ms can be enough to move completion before the first retrace and produce a
disproportionate 30 -> 60 cadence jump.

The dev113 cadence line reports `nested_steps_per_attempt_x1000` every second.
A useful chunk256 result should lower that ratio and lower critical-path time.
A cadence jump without lower critical time needs separate investigation rather
than being credited to chunk geometry automatically.

## Safety / architecture

This harness does not relax correctness policy. It retains:

- private player-owned Nintendo code/assets only;
- no emulator/general PPC fallback;
- exact host semantic routing;
- dev103 entry-only host filter;
- dev90 three proven structural native-first entries;
- dev107 broad direct-BL native-first execution disabled;
- dev97 HP-zero/Game Over correctness partitions;
- dev105 exact `dispDraw` CPUState nesting, bounded 64-step/32768-cycle walk,
  and fail-closed fallback behavior;
- indexed generated dispatch;
- synchronous renderer memory-visibility semantics;
- fixed-phase VI/retrace behavior;
- transactional restoration of the known-good C module after an LLVM launch.

Switching 128/256 requires no manual cache wipe. DolRecomp's exact resume/job
hash and OpenSPM provenance gate prevent stale geometry from being accepted as
the requested candidate.
