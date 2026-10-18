# M14 dev119 — Immediate-vs-Mailbox presentation A/B

Date: 2026-09-20
Branch: `m14-native-execution`

## Result that motivated this

Player-tested dev118 precise VI waiting in the heavy scrolling case. Subjectively
the jitter remained, but appeared slightly reduced.

The dev118 log clears the native VI boundary as the main remaining cause:

- sustained windows deliver ~60 VI retraces/s;
- typically 57-60/60 deliveries are `from_idle`;
- `busy_due` is usually 0-3/60;
- idle-ready margin is commonly ~2-3 ms in the heavy scene;
- ordinary VI delivery lateness is generally single-digit microseconds;
- VI handler cost is tiny (roughly tens of microseconds).

Therefore the game is usually ready before VI. The precise final-2-ms wait is
worth retaining as a small pacing improvement, but it is not the root fix.

The same run points downstream at desktop presentation:

```text
M14 host present pacing reason=shutdown
presents=5245
avg_us=17189
p50_us=17000
p95_us=19000
p99_us=31000
over25=90
over33=6
present_call_avg_us=199
```

The `Present()` call itself is cheap, but roughly 1% tail intervals are close
to a two-refresh hold. That matches the visible whole-frame hold/catch-up shape.

## dev119 change

Pinned Aurora normally does this when vsync is off on D3D12:

1. prefer Mailbox;
2. fall back to Immediate only if Mailbox is unavailable.

dev119 adds a private environment override in the pinned Aurora adaptation:

```text
OPENSPM_M14_PRESENT_MODE=immediate
OPENSPM_M14_PRESENT_MODE=mailbox
OPENSPM_M14_PRESENT_MODE=fifo
```

Unset preserves historical automatic selection.

No guest timing, fixed VI deadline, scheduler semantics, GX ordering, frame
production logic or callback behavior changes.

## Player A/B

Candidate:

```bat
M14-LLVM-IMMEDIATE-RUN.cmd
```

This keeps:

- chunk=256
- state_mem=1
- heavy_diag=0
- OPENSPM_M14_PRECISE_IDLE_WAIT=1
- vsync off

and forces Immediate.

The startup log must say:

```text
Using surface format ... present mode Immediate
```

Same-source Mailbox comparison:

```bat
M14-LLVM-PACING-RUN.cmd
```

which now explicitly sets:

```text
OPENSPM_M14_PRESENT_MODE=mailbox
```

After the first renderer rebuild, switching modes should not require another
source change.

## Decision

- If Immediate materially smooths scrolling and the ~30-31 ms present tail
  collapses, presentation-mode/compositor pacing is the remaining defect.
  Keep Immediate as the development default while a polished VRR/frame-pacing
  policy is designed.
- If Immediate looks the same and still has ~30 ms tails, instrument produced
  display-copy serial -> Aurora surface-present serial to find where a frame is
  being held.
- If Immediate looks smoother but tears visibly, the diagnosis is still useful:
  presentation interpolation or a better VRR-aware pacing strategy becomes the
  polish path rather than more guest CPU optimization.

dev118's precise VI wait remains enabled for this A/B because the player saw a
small subjective improvement and its measurements show it is semantically
well-contained.
