# M14 dev88 — retire temporary DrawDone per-draw profiling

Date: 2026-09-18

## dev87 player result

Player log: `launch(20260918-170925).log`

Tested repo revision:

`498351e0e53b25f2bbeaa4412bda6ca808d317b2`

dev87 is retained. It was a large mechanical dispatch improvement:

```text
dev87 prefilter bypassed = 468,889,861
production ordinary callbacks = 48,393,282
hang_80065128_max = 0
```

Late heavy gameplay callback cadence fell from dev86's roughly 2-3 million/s
to roughly 0.34-0.46 million/s. Exact LLVM voluntary budget exits remained zero.

That did **not** remove the visible heavy-scene frame cliff. Representative late
phase windows remain around 30-35 completed frames/s:

```text
frames=35/1016ms disp=5.886ms main=6.517ms done=16.621ms
frames=32/1016ms disp=6.194ms main=6.519ms done=19.048ms
frames=30/1000ms disp=8.187ms main=5.501ms done=19.649ms
frames=31/1033ms disp=10.055ms main=6.486ms done=16.797ms
```

This falsifies the idea that the remaining LLVM->C ordinary callback crossing is
the primary missing 2x.

## Deadline interpretation

The dev81 phase profiler charges each elapsed interval to the phase that began at
the previous entry. Therefore `done_us` is DEMODoneRender -> next
DEMOBeforeRender.

In light startup gameplay one measured window is:

```text
frames=58/1015ms
disp=0.343ms
main=2.162ms
done=14.577ms
```

That is the expected shape of a frame which finishes its work early and then
waits for the next fixed 60 Hz VI deadline.

In heavy gameplay, `disp+main` grows to roughly 12-16 ms. Synchronous DrawDone
producer work is then roughly another 3-4 ms. This puts the producer/main thread
at or just beyond the 16.667 ms VI deadline. Once that deadline is missed, the
game's own VI wait naturally resumes on the following retrace, creating the
observed ~30 Hz cliff.

The fixed-grid retrace code is therefore not being treated as an artificial
extra full-frame sleep. The current target is to recover the small amount of
producer CPU necessary to stop crossing the first deadline.

## DrawDone measurement result

The final 256-frame DrawDone profile window reports:

```text
draws=943,268
avg_frame_draw_us=3,490
scan_us=213,274
arrays_us=376,552
producer DrawDone avg_us=3,830
```

That is about 3,684 draws per frame, ~0.83 ms/frame in the mandatory guest-index
scan and ~1.47 ms/frame in the array-handling section.

Those numbers are useful, but the profiler obtaining them is itself now a
material contaminant. `apply_drawdone_substage_profile_patch.py` wraps thousands
of draws per frame with multiple `std::chrono::steady_clock::now()` calls:
outer draw, guest scan, vertex push, array staging and conditional
pipeline/bind/uniform rebuilds.

The performance frontier is now narrow enough that this temporary measurement
tax can plausibly decide whether a heavy frame lands just before or just after a
VI deadline.

## dev88 change

New migration:

`renderer/aurora/apply_drawdone_profile_retirement_patch.py`

It removes only the temporary per-draw DrawDone measurement instrumentation and
restores the already-proven quiet `handle_draw` path.

It does **not** alter:

- FIFO ordering or DrawDone semantics;
- guest-array requirement scanning;
- compact interval staging or exact snapshot/cache semantics;
- draw merging;
- pipeline, binding or uniform validity logic;
- renderer threading or presentation policy.

The migration is exact/idempotent and leaves a zero-cost source marker:

```text
M14 DrawDone per-draw profiler retired for normal play
```

Because the player's Aurora checkout is cached, root `CMakeLists.txt` now runs
this migration during Aurora configure. Normal incremental
`M14-LLVM-UPDATE-RUN.cmd` is sufficient; no home-test reset or cache deletion
is required.

## Next player test

Run:

`M14-LLVM-UPDATE-RUN.cmd`

Expected configure message:

```text
Aurora DrawDone per-draw profiler retired for normal play
```

Runtime must no longer print:

```text
M14 GX DrawDone substage profiler active
M14 GX DrawDone substage profile ...
```

The low-frequency producer-sync line remains valid and should still appear:

```text
M14 producer sync window kind=draw_done ...
```

Pass one wedding, then use the same heavy area.

Interpretation:

1. If heavy completed-frame cadence rises sharply toward 60 and `done_us`
   falls, the temporary profiler itself was enough to push borderline frames
   across the VI deadline.
2. If DrawDone producer time drops but heavy cadence remains ~30-35, optimize
   real DrawDone work next. The measured largest safe targets are exact
   guest-array staging/cache work (~1.47 ms/frame) and mandatory guest-index
   scanning (~0.83 ms/frame).
3. If DrawDone does not move materially, profile the native `disp/main`
   interior instead. Do not return to budget-guard or broad dispatcher work.

dev87's conservative host-call prefilter remains active for this A/B.
