# M14 dev133 — high-refresh presentation architecture

Date: 2026-09-21
Branch: `m14-native-execution`

## Trigger

The player reports a monitor refresh of **99.943 Hz**.

At the authoritative 60 Hz Wii simulation rate:

```text
99.943 / 60 ~= 1.6657167
```

so source frames cannot occupy an equal number of physical display refreshes.
The ratio is very close to 5:3: three 60 Hz source frames span roughly five
monitor refreshes. Without interpolation, camera panning therefore produces an
unequal persistence cadence even when every source frame is generated on time.

dev132 is the diagnostic proof: run the same dev130 smooth baseline at
~49.9715 Hz, exactly half of the reported monitor rate. Gameplay is
intentionally slow, but the source/display ratio becomes ~2:1.

If dev132 makes the camera evenly smooth, the production solution must keep
simulation at 60 Hz and decouple presentation.

## Existing OpenSPM/Aurora primitives

This design does **not** require a second guest framebuffer architecture.

### Current-frame Wii XFB snapshot

The active Aurora compatibility chain is:

```text
CMake
 -> apply_fifo_reentrancy_patch.py
 -> apply_bytebuffer_abort_diag_patch.py
 -> apply_display_copy_snapshot_patch.py
```

The display-copy snapshot migration turns raw Wii `GXCopyDisp` into a real
copy-time host XFB equivalent.

At `aurora_end_frame()`, it resolves the display-copy destination from
Aurora's copy-texture cache into:

```cpp
gfx::TextureHandle openspmDisplayCopySnapshot;
```

`TextureHandle` is Aurora's shared owner for a texture. The handle is moved
into the asynchronous end-frame callback, so the texture allocation remains
alive for that frame's asynchronous submission.

**Important correction:** this does not make the pixels immutable across future
Wii frames. Aurora's `copyTextureCache` reuses the same `CopyTextureRef.handle`
for the same destination/size/format key and increments its revision on each
later copy. A retained shared handle can therefore observe newer pixels after a
subsequent `GXCopyDisp` writes into the same allocation.

Historical player build logs explicitly report:

```text
Aurora Wii display-copy XFB snapshot already-applied
```

This is the correct lifetime primitive for the **current** asynchronous present,
but it is not itself a cross-frame history buffer.

### Dedicated presenter history ring

A production high-refresh presenter must copy each completed XFB into its own
presenter-owned history texture before a later Wii display copy can overwrite
the cache allocation.

Use a 3-slot GPU-local ring:

```text
history[N % 3]
  serial
  simulation_timestamp_ns
  dedicated texture
  size/format
```

Two slots are sufficient for interpolation; a third prevents the producer from
reusing a destination that may still be sampled by queued render-worker work.

The current Aurora `gfx::new_render_texture()` allocation used by GX copies has
`TextureBinding | CopyDst | RenderAttachment`, but not `CopySrc`. Therefore the
first safe history implementation should **render-blit** the sampleable current
XFB into a presenter-owned render texture. A later optimization may add an
OpenSPM-specific `CopySrc` usage bit and use `CopyTextureToTexture` after that
change is separately validated.

### Aurora render worker

Aurora already owns a dedicated render worker and exposes:

```cpp
render_worker::enqueue_work(...)
render_worker::synchronize()
```

Present resampling already asserts that its GPU queue writes execute on that
worker.

Therefore a high-refresh presenter should **not** create a side thread that
directly manipulates Dawn/WebGPU. Timing may be driven elsewhere, but GPU work
must be enqueued onto the existing render worker.

## Proposed production split

### Layer 1 — authoritative producer

Unchanged:

- Wii guest simulation remains 60 Hz;
- VI/timebase remain 60 Hz;
- GX command execution remains one authoritative simulation frame;
- each real `GXCopyDisp` produces one current-frame completed-XFB snapshot;
- presenter capture copies that snapshot into the next dedicated history slot;
- game logic/input/save/audio are not resampled.

Each published frame receives:

```text
serial
simulation_timestamp_ns
presenter_history_texture
width/height/format
optional camera/transform metadata
```

Keep at least two completed history frames plus one spare slot:

```text
previous
current
spare/in-flight
```

Publishing rotates presenter-owned history slots, not Aurora's reusable GX copy
cache handle. No guest memory is retained and no CPU readback is required.

### Layer 2 — presentation clock

Independent from Wii VI.

The host learns the actual display cadence from SDL (dev131). A small cadence
scheduler determines target present times.

For the reported 99.943 Hz display:

```text
display period ~= 10.0057 ms
simulation period = 16.6667 ms
```

At each target display tick, enqueue one presenter item on Aurora's render
worker. The item:

1. acquires the current presenter-owned history pair;
2. computes interpolation fraction from their simulation timestamps;
3. acquires the swapchain surface;
4. renders/blits the chosen interpolated presentation;
5. submits;
6. calls Surface.Present();
7. updates Aurora present accounting.

This path must not call the normal GX frame-finalization routines. It is a
presentation of already-completed XFB state, not a second Wii frame.

## Interpolation choices

### Repeating nearest completed XFB

Useful first implementation for decoupling and cadence diagnostics, but **not**
a complete smoothness solution.

At 99.943 Hz it still repeats 60 Hz source positions unevenly. It may improve
host Present regularity but cannot remove intrinsic 60-on-100 motion judder.

### Full-frame alpha blend

Technically easy with two retained XFB textures, but not recommended as the
production solution.

It creates temporal ghosting:

- Mario exists in two positions;
- UI/text double-image;
- particles/edges smear;
- scene cuts blend unrelated frames.

A blend shader may be useful only as an internal proof that the decoupled
presenter can consume two retained XFBs.

### Optical-flow / frame-generation

Can synthesize intermediate pixels without modifying guest transforms, but adds
substantial complexity, latency, artifacts and GPU cost. It is not the preferred
first solution for OpenSPM.

### Semantic render interpolation — preferred long-term direction

Interpolate scene transforms while rendering a genuine intermediate scene.

Potential sources:

1. guest camera and actor transform history captured at the 60 Hz boundary;
2. renderer-visible XF/model matrices tagged by frame serial;
3. targeted game-side interpolation patches for camera + entity transforms.

The authoritative simulation remains untouched. Only draw-time transforms use:

```text
lerp(previous_transform, current_transform, alpha)
```

This produces a real intermediate image instead of blending two finished
images.

For SPM, camera interpolation alone may remove most of the currently visible
symptom, but moving characters/3D flip scenes eventually require object/model
interpolation too.

## Recommended implementation staircase

Do not jump directly to full semantic interpolation.

### P0 — dev132 proof

Compare:

```bat
M14-LLVM-SMOOTH-RUN.cmd
M14-LLVM-100HZ-CADENCE-RUN.cmd
```

If the intentionally slow 49.9715 Hz run becomes evenly smooth, cadence is
proven material.

### P1 — retained-frame presenter skeleton

Add a no-interpolation decoupled presenter using previous/current dedicated
history textures populated by a GPU render-blit from the completed Wii XFB.

Goal:

- prove presenter-owned cross-frame XFB lifetime;
- prove the 3-slot history ring cannot be overwritten by later GXCopyDisp work;
- prove render-worker-only presentation;
- prove stable 99.943 Hz host presents independent of 60 Hz simulation;
- preserve exact image correctness.

This will not yet make 60 Hz motion perfectly smooth.

### P2 — diagnostic blend mode

Optional, hidden A/B only.

Blend previous/current XFBs at presentation alpha to prove two-frame temporal
sampling. Do not ship it as the normal renderer.

### P3 — camera semantic interpolation

Capture previous/current camera transform at the authoritative frame boundary
and apply interpolated camera state at draw/present time without modifying game
simulation state.

This is the highest-value first true interpolation target because camera/world
scrolling is the player's dominant remaining visible defect.

### P4 — entity/model interpolation

Extend frame-history metadata to movable actor/model matrices where required.
Retain exclusions for teleports, scene cuts, 2D/3D transitions, animation
resets, and discontinuous transforms.

## Discontinuity rules

Interpolation must snap instead of lerp when:

- scene/sequence changes;
- room/map changes;
- camera ID/mode changes incompatibly;
- teleport delta exceeds threshold;
- 2D/3D flip state changes discontinuously;
- XFB dimensions/format change;
- frame serial is not consecutive;
- producer missed enough frames that history is stale;
- pause/menu state requests exact frozen output.

The current frame is always the correctness fallback.

## Latency policy

Do not add an unconditional full simulation-frame delay merely to interpolate.

Preferred policy is low-latency interpolation/extrapolation where possible:

- render between known consecutive states when timing permits;
- otherwise present current state;
- never delay input/simulation to wait for a prettier frame.

Any future frame-generation option must be explicitly separable from the
low-latency native mode.

## Player P0 result — 2026-09-21

The player completed the dev130/dev132 A/B.

Observed:

- 60 Hz dev130 baseline remained jittery;
- ~49.9715 Hz dev132 cadence had visibly less jitter;
- dev132 showed horizontal scanline/shearing artifacts.

Runtime logs confirm a ~99.940 Hz desktop mode and Immediate presentation with
vsync disabled. The dev132 log also confirms the 20,011,407 ns fixed VI period
was active. Therefore the half-refresh test is a **positive but not perfectly
clean cadence result**: refresh mismatch is materially contributing to visible
judder, while Immediate-mode tearing is contaminating the visual comparison.

dev135 adds a clean follow-up that changes only presentation policy relative to
dev132: FIFO + vsync ON at the same ~49.9715 Hz simulation cadence. If that
retains the smoother motion while removing tearing, treat P0 as sufficiently
confirmed and begin P1. The production path must still restore 60 Hz simulation.

## Immediate next decision

dev132 must be run before implementation work goes beyond P1.

- **smooth-but-slow at 49.9715 Hz:** proceed with dev133 presenter work;
- **same irregular jitter:** prioritize remaining OpenSPM producer/present bugs;
- **partially improved:** both cadence and sparse host holds are real, so retain
  dev129/dev130 and proceed with interpolation as a second layer.

## Contract added with dev133

The source-contract suite now asserts that the active compatibility chain
actually invokes `apply_display_copy_snapshot_patch.py` and that the migration:

- creates an `openspmDisplayCopySnapshot` TextureHandle;
- resolves it from the Wii display-copy cache;
- moves it into the asynchronous present callback;
- presents the copy-time resolve rather than the later live EFB.

That contract guarantees **current-frame copy-time correctness and callback
lifetime**. It intentionally does not claim cross-frame immutability. dev134's
presenter-history proof must establish that separately with dedicated textures.
