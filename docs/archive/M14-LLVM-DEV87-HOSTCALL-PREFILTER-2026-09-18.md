# M14 LLVM dev87 — exact negative host-call prefilter

Date: 2026-09-18

## Player result that selects this frontier

Player log: `launch(20260918-163035).log` at repo revision
`8896170eee29`.

The player still reports visible lag. dev86 nevertheless worked mechanically:
its two measured `strcmp` PCs no longer survive the final ordinary-callback
heavy-hitter sample. The remaining traffic is diffuse.

Late heavy gameplay still spends long windows at roughly 30-32 completed
main-loop frames/s while VI remains about 60 Hz. Representative adjacent windows
show approximately:

- ordinary public callbacks: ~2.1M-2.9M/s;
- `disp`: ~8-10 ms in the heaviest steady windows;
- `main`: ~7-9 ms;
- `done`: ~16 ms;
- exact voluntary budget exits: zero.

This means dev85 remains conclusive: the 32768-cycle budget guard is not creating
the callback storm. dev86 also demonstrates that optimizing one sampled PC at a
time is not sufficient.

The shutdown totals are stronger:

```text
ordinary callbacks = 2,816,258,463
seam negative-filter rejects = 2,761,324,023
host-target negative-filter rejects = 2,810,490,694
direct hosted redirects = 1,633,238
direct-BL rescues = 27,142,100
hang_80065128_max = 0
```

The dev69 negative filters therefore prove, but only *after entering C*, that the
overwhelming majority of public callbacks cannot match the immutable hosted-call
or platform-target tables.

## dev87 design

dev87 moves only that conservative negative classification ahead of the
LLVM -> C callback crossing.

It does **not** revive dev80 native-first execution.

Generation builds an exact one-bit-per-instruction table over the player-owned
combined DOL + relF image. A set bit means "this PC may require the existing
host-first router." The candidate set is intentionally a conservative superset:

1. every player-local compiler/map entry, including structural entries;
2. every checked-in platform host target;
3. every direct BL source in executable player code, preserving the generic
   direct-BL rescue safety net;
4. retained exact GX/HOME/DSP/post-MTMSR semantic coordinates.

Unknown, aliased or out-of-image PCs always retain host-first routing.

For a proven zero-bit ordinary PC, generated dispatch changes only this:

```text
old:
    ppc_host_call(ctx, pc)
    dolrecomp_call_original(ctx, pc)

dev87 definite ordinary PC:
    dolrecomp_call_original(ctx, pc)
```

The native lookup itself, chunk entry behavior, guest state and control-flow
order are unchanged. Addresses that may be semantic still execute the historical
host callback first.

This avoids dev80's fatal assumption that finding a containing native chunk
proves an exact PC is restartable. dev87 never reconstructs or guesses legal
native entries.

## Runtime proof

Expected startup marker:

```text
M14 LLVM dev87 exact host-call prefilter active:
```

Expected teardown marker:

```text
M14 LLVM dev87 host-call prefilter stats bypassed=...
```

The existing dev85 cadence remains useful. If dev87 is attacking the measured
cost, `ordinary_rate_per_s` should collapse because bypassed definite ordinary
PCs never enter `bridge_host_call_dev50`.

## Next player test

Run:

`M14-LLVM-UPDATE-RUN.cmd`

No manual cache wipe.

Gates:

1. generation prints `LLVM dev87 exact host-call prefilter` and a nonzero
   `may_host_in_image` count;
2. startup prints the dev87 active marker;
3. boot passes the historical dev80 `0x8026CA60` frontier;
4. pass one wedding;
5. play >=30 seconds in a known heavy NPC area;
6. compare completed `M14 main-loop phases frames/host_ms`, `disp/main`
   timings and dev85 `ordinary_rate_per_s`;
7. on exit, confirm a large dev87 `bypassed=` count.

Strong success is callback pressure dropping by orders of magnitude with
`disp+main` moving far enough below the 16.7 ms VI deadline to stop the
alternate-retrace ~30 FPS collapse.

If callback pressure collapses but `disp+main` and completed frame cadence do
not improve, dispatch is no longer the primary target and the next audit should
profile work inside the remaining native `disp/main` phases rather than add
more dispatcher heuristics.
