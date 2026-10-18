# M14 dev117 — progress-aware dispDraw callback walker

Date: 2026-09-20  
Branch: `m14-native-execution`

## Why dev117 exists

The dev114 state-in-memory change closed the original heavy-scene performance
blocker. With chunk256 + state_mem=1, Yold recovered from ~30 fps to ~58 fps
median and historically bad rooms/cutscenes became subjectively smooth.

The remaining visible camera-pan judder was then proven by desktop video to be
whole-frame repetition/catch-up, not an intermittently updating guest camera.
Mailbox presentation removed most of the old ~33 ms swapchain tail but did not
remove the visible judder. The next frontier is therefore produced-frame
headroom, not camera logic or Present() cost.

dev113's experimental chunk512 A/B was the clean test for "are generated chunks
simply too small?". The player reported it felt maybe worse, and the runtime
numbers agree:

- comparable late Yold `seq=2/stage=1` windows produced roughly 53 fps under
  chunk512 versus roughly 56 fps under chunk256;
- typical heavy `disp + main` rose slightly rather than falling;
- dev105 still averaged about 23 generated chunks per callback;
- one ~1.84 s host update-events stall was a window/host event outlier, not a
  normal guest CPU frame cost.

Conclusion: generic 256->512 chunk geometry does not collapse the callback walk.
Explicit partitions/function exits dominate. Keep chunk256 as the baseline.

## What dev117 changes

Only the existing exact CPUState `dispDraw` nested walker at linked indirect
site `0x8005B29C` changes.

dev105 previously failed closed whenever one generated dispatch returned with
the same guest PC, even if `CPUState.downcount` became more negative. In
RecompCore mode negative downcount is accumulated guest-cycle charge, so:

- same PC + lower downcount means guest instructions actually ran;
- same PC + unchanged/non-decreasing downcount is true no-progress.

dev117 now keeps walking for the first case and still fails closed for the
second.

dev117 also raises only the generated-chunk step cap:

- dev105: 64 generated chunks;
- dev117: 128 generated chunks.

The accumulated guest-cycle quantum remains exactly **32768**. Semantic
fallbacks, exceptions, lookup misses, recursion failures, true no-progress and
cycle-quantum exhaustion all retain the historical fail-closed outer path.

No callback is skipped or reordered. This is deliberately narrower than the
rejected dev107 broad direct-BL native-first experiment.

## Why this is worth testing

The rejected chunk512 shutdown reported approximately:

- 1,499,211 nested attempts;
- 34,131,209 generated nested steps;
- 61,441 budget yields;
- 62,394 64-step-limit yields;
- 17,740 same-PC/no-progress yields.

The step-limit and same-PC classes are artificial outer-dispatch exits that
dev117 can reduce without widening the 32768-cycle monopolization bound.

## Active test

Run:

```bat
M14-LLVM-DEV117-RUN.cmd
```

This pins the known good comparison knobs:

- chunk = 256;
- state_mem = 1;
- heavy_diag = 0;
- `OPENSPM_M14_VSYNC=0` (Mailbox when Aurora/D3D12 supports it).

Test normal play first, including Yold scrolling. The build log should identify
`m14-dev117-chunk256-sim1-yold-normal` and print:

`M14 LLVM dev117 CPUState dispDraw progress-aware nesting active`

At shutdown inspect the existing dev105 stats line. dev117 additionally appends
`same_pc_progress=`.

Success criteria are deliberately practical: no correctness regression and a
clear reduction in frame holds / improvement in heavy produced-frame cadence.
A tiny numerical win that still feels identical is optional polish, not a new
blocker.

## Rollback

The known playable baseline is still:

```bat
M14-LLVM-UPDATE-RUN.cmd
```

with chunk256/state_mem=1/heavy_diag=0. If dev117 regresses correctness, revert
the dev117 walker changes and retain dev114 state-memory.

`M14-LLVM-CHUNK512-RUN.cmd` is retained only to reproduce the rejected 512 A/B;
it is not the active frontier.
