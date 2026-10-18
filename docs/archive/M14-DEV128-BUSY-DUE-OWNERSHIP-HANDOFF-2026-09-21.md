# M14 dev128 — tail-only busy-due phase ownership

Date: 2026-09-21
Branch: `m14-native-execution`

## Purpose

If the independent dev123/dev124/dev126/dev127 scheduling A/Bs are neutral, the
next question is no longer "is Windows scheduling involved?" but exactly which
guest top-level phase still owns the producer when a fixed VI deadline becomes
due.

dev128 adds that attribution without restoring the retired broad profiler.

## Data source

The existing v13 frame-phase wrapper already takes four monotonic timestamps per
produced frame at these durable guest entries:

- phase 0: DEMOBeforeRender;
- phase 1: spmarioDisp;
- phase 2: spmarioMain;
- phase 3: DEMODoneRender.

dev128 simply publishes the current phase ID and its existing timestamp into the
shared bridge state. No additional steady-state clock read is added.

At VI delivery, dev118 already decides whether the deadline was reached from
true Nintendo scheduler idle or while the guest was still busy. Only in the
second case does dev128 emit a bounded diagnostic line.

## Marker

```text
M14 dev128 busy-due owner n=...
  phase=2(main)
  phase_age_us=...
  late_us=...
  pc=0x...
  lr=0x...
  msr=0x...
  downcount=...
```

Logging is bounded to the first 64 events and later powers of two.

## Interpretation

The phase ID is the top-level segment that was active when VI became due:

- `before`: pre-display work between DEMOBeforeRender and spmarioDisp;
- `disp`: display/render submission work;
- `main`: gameplay simulation/update work;
- `done`: post-main / DEMODoneRender-to-next-frame region.

The exact PC/LR gives the next drill-down target inside that phase.

This avoids the previous mistake of treating the whole dev122 `done_us` bucket
as producer work: dev128 observes the producer **at the instant the VI deadline
is due**, before the retrace handler is delivered.

## Use

No special launcher is required. dev128 is observational and is included in all
current LLVM launchers.

Run the normal A/B sequence and keep the launch logs:

```bat
M14-LLVM-IMMEDIATE-RUN.cmd
M14-LLVM-SPINWAIT-RUN.cmd
M14-LLVM-PRIORITY-RUN.cmd
M14-LLVM-HIGHQOS-RUN.cmd
M14-LLVM-FASTCORE-RUN.cmd
M14-LLVM-NOSTICKY-INPUT-RUN.cmd
```

If the same PC/phase dominates across all variants, optimize or restructure that
specific deterministic tail. If ownership moves/disappears under one host
scheduling variant, the scheduling hypothesis gains direct support.
