# M14 dev139 — decoupled host presenter proof

Date: 2026-09-22  
Branch: `m14-native-execution`

## Why dev139 exists

dev138 is accepted.

The successful `launch(20260922-004449).log` restored the pre-dev137 producer
performance envelope and demonstrated stable final geometry/material identity in
normal gameplay. Sustained ~3000-draw samples are 99.9-100% exact with no
ambiguous matches or hash failures. The remaining visible issue is the known
presentation-cadence mismatch: authoritative 60 Hz output on a measured
~99.94 Hz display.

dev139 isolates presentation from simulation before any geometry interpolation
is attempted.

## What dev139 does

With `OPENSPM_M14_ASYNC_PRESENTER=1`:

1. Wii simulation and GX production remain 60 Hz and authoritative.
2. The normal GXCopyDisp snapshot remains the source of presentation truth.
3. At the end of each producer frame, that completed XFB snapshot is copied into
   a two-slot host-owned retained-image ring.
4. The producer submits its frame and publishes the completed retained slot.
5. A dedicated host presenter thread repeatedly presents the newest retained
   image at `OPENSPM_INTERPOLATION_TARGET_FPS`.
6. The proof launcher defaults to 100 Hz to match the player's measured
   ~99.94 Hz display.

No guest state is advanced by the presenter. No extra GX draw is generated. No
transform interpolation is active.

## Important consequence

This build is **not expected to make motion genuinely smooth**.

At 100 host presents/sec with only 60 unique source poses/sec, some source poses
must repeat. dev139 proves that host presentation can be decoupled from the Wii
producer and that retained frame resources survive safely across those extra
presentation slots. It does not yet synthesize intermediate camera/actor poses.

## Resource lifetime design

The presenter ring has two immutable host textures.

The producer always copies the newest completed Wii XFB into the non-current
slot, submits that copy, and only then publishes the slot. Producer and presenter
queue submissions share a mutex so Dawn queue ordering is deterministic.

The presenter copies the selected WebGPU handles while holding the ring mutex;
the handles are ref-counted, so replacing a ring entry cannot destroy resources
still referenced by a presenter command.

The Wii XFB resolve texture originally lacked `CopySrc` usage. dev139's
migration adds `CopySrc` to Aurora render textures so copying the completed XFB
into the retained ring is WebGPU-valid.

## Surface caveat for the proof

The pinned Aurora surface path was designed around one render-worker presenter.
dev139 moves ordinary Present ownership to a second thread only when explicitly
enabled. Normal no-dev139 behavior is unchanged.

For the first proof run, avoid deliberately resizing/toggling fullscreen during
the measurement window. If ordinary gameplay proves the presenter architecture,
the production presenter should centralize surface mutation/presentation
ownership before HOME-menu graphics changes are exposed live.

## Telemetry

Startup:

```
M14 dev139 decoupled host presenter active:
  target_hz=...
  source=retained-wii-xfb
  simulation_hz=60
  interpolation=off
```

Sparse cadence:

```
M14 dev139 presenter cadence
  presents=...
  source_publishes=...
  repeats=...
  target_hz=...
```

Shutdown:

```
M14 dev139 presenter summary
  source_publishes=...
  host_presents=...
  target_hz=...
```

The existing host Present pacing diagnostic remains active.

## First launcher

```bat
M14-LLVM-ASYNC-PRESENTER-RUN.cmd
```

Optional target override:

```bat
M14-LLVM-ASYNC-PRESENTER-RUN.cmd AUTO 120
```

## Pass criteria

The proof passes if:

- heavy gameplay producer cadence remains roughly ~56-60+ completed frames/s;
- source publication remains around the 60 Hz Wii cadence;
- host presentation approaches the requested/display cadence (~100 Hz for the
  default run);
- Present interval statistics move toward ~10 ms on the current display;
- no black flicker, stale retained frame, geometry breakup, detached doors,
  crash, or major producer slowdown appears.

Motion can still have 60 Hz pose judder and dev139 can still pass.

## Failure interpretation

If producer fps collapses, the retained copy/presenter synchronization is too
expensive or is back-pressuring the render worker.

If host presents stay near 60 Hz despite ~100 Hz target, FIFO/surface ownership
or Dawn scheduling is still coupled to the producer.

If host presents reach ~100 Hz but frames flicker/corrupt, retained-image
lifetime/queue ordering is wrong.

If only resizing/fullscreen breaks, surface ownership must be centralized before
turning the proof into production infrastructure.

## After dev139

dev140 should generate actual interpolated presentation slots from retained scene
data.

Use dev138's final geometry/material identity for correspondence, but also apply
strict snap/fallback gates for:

- implausible transform distance;
- projection/type changes;
- room/scene replacement;
- 2D/3D flip discontinuities;
- unmatched or ambiguous draws;
- indexed/deformed cases not yet proven safe.

The producer must never run a second full `build_uniform` per draw on the live
GX path. Interpolated transform uniforms belong in the retained/off-critical-path
presentation layer.
