# M14 dev122 — camera vs presentation tail attribution

Date: 2026-09-21
Branch: `m14-native-execution`

## Why the camera case is reopened

Player observation after dev121:

- everything else feels very smooth;
- the remaining visible defect is the world/camera scroll while Mario walks;
- dev120 Inline and dev121 diagnostic retirement did not change it.

Earlier camera CSV traces are still valid: camera 5 position/target/viewMtx advanced
smoothly in guest memory and did not show exact stalls while Mario moved. That
only proves guest camera state is continuous. It does not prove every camera
transform reaches the desktop at an evenly paced presentation interval.

The dev121 run still reports a real host-present tail:

- p50 ~17 ms;
- p99 ~31 ms;
- 115 / 6441 intervals >25 ms;
- Present() itself ~0.196 ms average.

A duplicated/late whole frame is easiest to see during camera panning because
Mario remains near screen center while the entire world is translating.

## dev122 instrumentation

This is observation-only.

### Produced-frame tail

The existing four main-loop timestamps are reused; no new frame clock is added.
When one complete 0->1->2->3->0 cycle exceeds 20 ms:

```text
M14 dev122 frame tail n=...
  total_us=...
  before_us=...
  disp_us=...
  main_us=...
  done_us=...
```

### Camera companion

At normal phase-0 cadence, dev122 reads only:

- Mario X;
- camera-5 position X;
- camera-5 target X;
- camera-5 viewMtx translation X.

Only when the frame-tail marker fires does it emit:

```text
M14 dev122 camera tail n=...
  mario_dx=...
  pos_dx=...
  target_dx=...
  view_dx=...
  view_repeat=...
```

The camera layout matches the debug-console camera trace:

- pointer table `0x804C9F80`;
- camera 5;
- viewMtx `+0x54`;
- position `+0x15C`;
- target `+0x168`.

### Host Present tail

The existing Aurora present probe already pays for both clock reads. dev122 adds
no new timestamp. If the measured successful-present interval exceeds 25 ms it
emits:

```text
M14 host present tail n=... present=... interval_us=... call_us=...
```

## Player run

```bat
M14-LLVM-CAMERA-TAIL-RUN.cmd
```

Configuration is otherwise the current best baseline:

- chunk256;
- state_mem=1;
- heavy_diag=0;
- precise VI wait enabled;
- Immediate presentation;
- FIFO Drain;
- dev121 quiet normal-play path retained.

## Interpretation

1. Frame tail + Present tail + non-zero smooth camera delta:
   guest camera is innocent; a produced/host frame is late and camera motion is
   merely the most visible detector.

2. Camera tail reports `view_repeat=1` while Mario moved:
   reopen guest camera update/order and trace the exact camera update callback.

3. Present tail with no corresponding produced-frame tail:
   downstream render-worker/swapchain/compositor cadence is responsible.

4. Produced-frame tail without a Present tail:
   producer was late but downstream presentation absorbed/recovered it.

The next fix should follow the observed class rather than another broad A/B.
