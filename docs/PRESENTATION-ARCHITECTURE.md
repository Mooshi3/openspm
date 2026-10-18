# OpenSPM presentation architecture: frame rate, pacing and interpolation

This is the target design for how OpenSPM puts frames on screen. It is written
from the product goal (the definitive way to play Super Paper Mario, in the
spirit of OpenPete and Mario Kart WiiCompiled), not from the dev-series history.
`M14-RESUME.md` remains the chronological log.

## What the player gets

A **Graphics → Frame rate** setting in the OpenSPM menu (the future HOME-button
replacement), stored in `OpenSPM.ini`:

| Mode | Simulation | Presentation | Notes |
| --- | --- | --- | --- |
| **Vanilla 60** (default) | 60 Hz, locked to the display when it fits | 1 image per game frame, vsync | Identical motion to console. |
| **Match display** | 60 Hz | display refresh, interpolated | 100/120/144/165/240 Hz... |
| **Custom cap** | 60 Hz | N fps, interpolated | For laptops / fixed-rate capture. |
| **Unlimited** | 60 Hz | as fast as possible, interpolated | VRR / benchmarking. |

Implemented so far: F1 -> Display -> Frame mode, `[Display] frame_mode =
vanilla | smooth | vanilla_timeline` (`launcher/native_settings.h` expands
`smooth` into the `interp` flag's variables: async presenter + present timeline
in smooth mode + draw tags + Stage C; `vanilla_timeline` is `timelinevanilla`).
Match display = `smooth`. Custom cap and Unlimited need a presenter clock that
is not the display's vblank and are not implemented.

Plus **Display mode**: windowed / borderless / exclusive fullscreen, where
exclusive fullscreen offers a refresh-rate picker and pre-selects a mode that
suits the chosen frame-rate mode (for Vanilla 60: a 59.94/60/120 Hz mode).

Gameplay never runs faster than 60 Hz. Everything above 60 is presentation.

**Aspect** (implemented): `OPENSPM_ASPECT=4:3` (default) keeps the game's
logical aspect: Aurora's EFB is sized to 4:3 inside the window at full height
and presented with black bars (2560x1080 -> 1440x1080 centred). `stretch`
restores the old full-window stretch. True **ultrawide** (Hor+ projection,
clamping, handling backgrounds that were never modelled outside the original
view) is a separate future Graphics toggle, never a framebuffer stretch.

PE bounding boxes (fix implemented, awaiting player test): the bbox shader
records EFB pixels, but SPM reads/writes PE bbox registers in its 640x480
logical space and uses the box to pick the screen rectangle it captures with an
EFB texture copy (2D/3D flip card, door swing, pipe "chunks"). With the EFB at
window resolution the game got 2.25-4x scaled (and >1023 saturated) boxes.
`apply_bbox_logical_scale_patch.py` converts at the renderer/game boundary
(writes logical->pixel, reads pixel->logical, identical at 640x480).
`OPENSPM_M14_BBOX_LOGICAL=0` restores raw pixels for A/B.

EFB copies moved by the CPU (implemented, awaiting player test): SPM snapshots
captured screen rectangles with a CPU memcpy (star-door swing). Aurora keeps
copies only as host textures, so the snapshot address used to read stale RAM.
Each copy now stamps a small tag into its guest RAM. A texture load from memory
carrying a tag uses a frozen GPU copy of the original HD texture. There is no
readback stall, and snapshots stay at full resolution
(`apply_efb_copy_ram_writeback_patch.py`). Real pixel write-back to RAM exists
as the opt-in `OPENSPM_M14_EFB_COPY_RAM=1` for CPU code that inspects pixels.

## Why 60 fps looks juddery on some displays

A frame looks smooth only when every game frame stays on screen for the same
number of display refreshes.

* On a 99.943 Hz panel, 60 game frames must share ~100 refreshes, so they
  alternate 2, 2, 1 refreshes (20/20/10 ms). The world visibly steps during
  camera scrolls. No pacing trick fixes this; only interpolation or VRR can.
* Even on a "60 Hz" panel that is really 59.978 Hz, a free-running 60.000 Hz
  game clock drifts against the panel and periodically doubles/drops a frame.
* The dev132 test ran the game at 49.97 Hz = exactly half of 99.943 Hz: every
  frame was held for exactly 2 refreshes, which is why it felt smoothest.

## Vanilla 60: display-locked simulation clock (implemented)

Emulators solve this by syncing the console clock to the host refresh; OpenSPM
does the same.

1. After creating the window, the renderer reads the display's exact rational
   refresh `R` (SDL3) and logs every refresh the display offers at the current
   resolution.
2. `k = round(R / 60)`. If `R / k` is within 0.5% of 60, the game clock period
   becomes `k / R` (59.978 Hz panel → 16,672,780 ns, 0.037% slow, imperceptible)
   and is published as `OPENSPM_M14_RETRACE_PERIOD_NS` before the first retrace.
3. Otherwise the clock stays 60.000 Hz and the log names the offered modes that
   would make vanilla smooth (e.g. "select 59.978 Hz").

Code: `renderer/aurora/display_pacing.hpp` (pure policy, unit tested),
`renderer/aurora/openspm_renderer_aurora_v6.cpp` (SDL/env glue),
`tools/native_bridge/legacy_c_adapter_v7.c.in` (`bridge_m14_retrace_period_ns`).
Launcher: `M14-VANILLA-RUN.cmd` (`nosync` second argument = fixed-clock control).
Opt out: `OPENSPM_M14_DISPLAY_SYNC=off`.

Audio note: when native audio output lands it must be clocked by the game's
simulation rate (resampling the tiny difference), never the other way round.

Next pacing steps:

* **Phase lock.** Period matching leaves a few-ppm drift between the host clock
  and the panel (a hitch every several minutes at most). Close the loop by
  nudging the period from the renderer's measured present timestamps.
* **Exclusive fullscreen with refresh picker** (implemented, awaiting player
  test): `OPENSPM_WINDOW_MODE=windowed|borderless|exclusive`,
  `OPENSPM_REFRESH_HZ=auto|<Hz>`. Exclusive keeps the desktop resolution and,
  in auto, selects the highest refresh vanilla 60 locks to (59.978 Hz on the
  LG ultrawide; 240 over 120 over 60 elsewhere); otherwise the desktop refresh
  is kept. `M14-VANILLA-RUN.cmd AUTO fullscreen`. Future: interpolated modes
  should instead prefer the highest refresh, and the OpenSPM menu will edit
  these settings and offer a live toggle.

## Above 60: interpolation (design)

Interpolation renders extra images between two completed game frames A and B.
It must be exact about **what** is interpolated and **when** each image is seen.

### When: target the actual scan-out time

For every presented image, compute `alpha` from the predicted display time of
that image on a smoothed simulation timeline (game frame index × period, mapped
to host time), then render the in-between state for that alpha. Pre-rendering
fixed alpha grids and showing whichever is due (dev140–dev145) leaves random
0–10 ms timing error, which is itself visible judder. A fallback frame must keep
the same one-frame latency (show A, not B) or the camera lurches forward/back on
every accepted↔fallback transition.

### Stage A: present timeline (implemented, opt-in, awaiting player test)

The timing half of the design exists without draw interpolation, so it can
be tested on its own.

* **Model** (`renderer/aurora/present_timeline.hpp`: pure, no clocks or SDL).
  It holds two small second-order PLLs, each anchored at its latest sample.
  - *Display:* vblank times `v_ref + k·T`. `T` starts from the display's exact
    rational refresh (99943/1000 Hz → 10,005,703 ns, never an integer-Hz
    truncation) and only trims host-clock ppm drift. It is fed by the time each
    FIFO `Present()` returns, because with the swapchain full, Present blocking
    is the vblank clock.
  - *Game:* each publish is assigned to a grid slot by host time, so a 100 ms
    stall is an index gap on the same line, not a phase error. The line is
    `t_ref + n·P`, with the nominal `P` set to the locked retrace period when
    one exists. Heavy frames get only a limited pull on the line. A persistent
    phase change re-anchors it ("reanchor").
  - *Position* for a predicted scan-out time `s`: `g(s) = n + (s − t_ref)/P − D`.
    - Smooth: `D = 1 + (guard + margin)/P`, so A = ⌊g⌋ and B = A+1 are both
      finished when the present is prepared (one frame of delay), and
      alpha = frac(g).
    - Vanilla: `D = (guard + margin)/P` and alpha = 0, so it shows the newest
      frame on the smoothed timeline.
  - *Margin* is the tracked lateness of frame completions. It starts at 0.3·P,
    grows at once and shrinks very slowly, and `D` only moves on clear
    evidence, so the latency does not wander.
  - *Guarantees:* `g` never decreases. A frame that is not finished yet is
    held ("late"), never guessed. A timeline that falls behind every retained
    frame jumps forward ("snap").
  - *Whole multiples:* on displays that are k × the game rate, the vblank
    phase of `g` is kept mid-slot. Every frame is then held exactly k
    refreshes, and noise cannot flip the choice. A rare re-centre is a single
    latency step of less than one refresh.
* **Presenter** (`present_timeline_presenter.inl`, embedded by
  `apply_present_timeline_patch.py`, last in the CMake chain). The dev139
  presenter thread wakes one guard (0.4·T, 1.5–5 ms) before the predicted
  vblank: a coarse sleep, then a yield loop for the last 1 ms. It selects under
  the presenter mutex and queues exactly one present on Aurora's render
  worker. It never queues a second present behind one that has not returned.
  With the timeline on, the ring keeps 4 images; dev139 keeps its 2. Bbox
  readback priority is respected.
* **Stage A frame choice:** the image shown is the nearer in time of the pair
  (A while alpha < 0.5, then B), or A in Vanilla mode. The alpha is logged,
  not drawn. With `interp` (Stage C below) the worker renders the in-between
  image for that alpha instead.
* **Simulation** (`tests/m14_present_timeline_test.cpp`, run by
  `test_display_pacing.py`). It covers 59.94/60/75/99.943/100/120/144/165/240
  Hz, smooth and vanilla, each with and without a 100 ms stall. The model
  includes jittery frame completion (3 ± 0.7 ms plus 0.5% heavy frames), 0–1
  ms wake jitter, and a display ±25 ppm off the host clock. Asserted: one
  present per vblank, scan-out vblank predicted correctly, timeline error
  under 0.5 ms (measured 0.18–0.39 ms max, about 0.1 ms rms), monotonic
  position, no late frame in steady state, recovery within 0.6 s of the stall
  (measured under 17 ms), and in Vanilla mode on 60/120/240 Hz every frame
  held exactly k refreshes. The test binary takes `-s N` to sweep N extra seeds.
* **Run:** `M14-VANILLA-RUN.cmd AUTO timeline`, or `timelinevanilla`. These
  set `OPENSPM_M14_ASYNC_PRESENTER=1`, `OPENSPM_M14_PRESENT_TIMELINE=1` and
  `OPENSPM_M14_PRESENT_TIMELINE_MODE=smooth|vanilla`. The renderer then keeps
  the display clock lock and publishes `OPENSPM_M14_DISPLAY_REFRESH=n/d`.
  Without these variables nothing changes: the default Vanilla 60 path and the
  dev139 presenter behave exactly as before.
* **Log:** `M14 present timeline active: ...` once, then
  `M14 present timeline: ... hold[1,2,3,4,5+]=... alpha_would_use[0.0..0.9]=...
  late= snaps= missed_vblanks= busy_skips= evicted= delay_ms= margin_ms=` once
  per second. Healthy output has two parts:
  - Hold pattern: at 120 Hz, `hold` is all 2s. At 99.943 Hz the Stage A holds
    are mixed 1s and 2s, which is expected, because the frames themselves
    still judder until Stage C.
  - Counters: `late`, `missed_vblanks` and `busy_skips` stay near 0.

### Stage B: draw tags (implemented, opt-in, awaiting player test)

Every GX draw now carries the identity of the game object that issued it. The
picture does not change.

* **Why this is exact.** `spmarioDisp` (0x801A7D1C) only calls `camDraw`
  (0x8005557C). For each camPtrTbl slot, `camDraw` sets projection, viewport,
  scissor and matrices, then calls `dispDraw(camId)`. Between callbacks,
  `dispDraw` writes only GX state (scissor, blend, alpha compare, z mode) and
  never a primitive. So every frame draw lies inside exactly one callback.
* **Bridge** (`legacy_c_adapter_v42_dev69.c.in` + `tools/native_bridge/m14_draw_tags.h`).
  - The generated LLVM code already sends the exact `bctrl` 0x8005B29C through
    the dev105 helper `openspm_m14_dev105_cpustate_nested_dispatch`. The
    helper is now a thin wrapper, so no codegen and no LLVM objects change.
  - At the call it checks the entry at 0x8056D1D4 (camId, callback, param
    must equal r3, CTR and r4). It then appends "begin owner" BP writes to the
    dev16 write-gather, so they sit in stream order before the callback's first
    byte. When the callback returns to 0x8005B2A0 it appends "end".
  - When the helper yields a callback to the outer dispatcher, that callback
    keeps its owner until the next begin or frame/end marker. That is still
    exact, because no primitive can come in between.
  - Frame markers come from the dev81 native phase hook: a frame marker at
    `spmarioDisp` entry and an end marker at `DEMODoneRender`.
  - Nothing is emitted while a display list is recording. The checks are the
    v19 recorder state and the game's own GXData dlist flag, because a marker
    would otherwise land in guest RAM.
* **Owner id** = hash(callback, param, camId, occurrence).
  - Occurrence counts identical keys in `dispEntry` pool order (pool
    0x8056D1C8, 16-byte entries). The z sort cannot change that order.
  - The entry pointer itself is unstable, because it shifts whenever an
    earlier object spawns or despawns, so it is not part of the id.
  - Known limit: a pool object freed and reused at the same address with the
    same callback keeps its id. Stage C's teleport/discontinuity snap covers
    this case.
* **Camera class:** CamEntry+0x158 selects `C_MTXOrtho` (2D/UI) over
  `C_MTXPerspective` (3D world) in `camDraw`, and is sent with each owner.
* **Wire format:** BP 0x7E carries owner bits [23:0]. BP 0x7F carries owner
  bits [31:24], camera, the ortho flag and kind (end/begin/frame). Hardware
  leaves 0x6A–0x7F unassigned; Aurora mapped both to `bp_unhandled`, and the
  backend v9 framer passes any BP write through.
* **Renderer** (`renderer/aurora/draw_tags.hpp`, embedded by
  `apply_draw_tags_patch.py`, last in the CMake chain).
  - BP 0x7E/0x7F handlers latch the owner. An owner change stops the next draw
    from merging into the previous draw command.
  - Each `DrawCommand` gets `m14DrawOwner` and `m14DrawMeta` in existing
    padding. The size stays 160 bytes.
* **Test:** `tests/m14_draw_tags_test.cpp`, run by `test_display_pacing.py`. It
  sends bridge encoder output through a synthetic FIFO into the renderer
  tracker, with a z order reshuffled every frame, spawn/despawn at the start of
  the pool, duplicate registrations, lost end markers and a part-count change.
  It checks that owners are exact and stable and that
  spawned/despawned/count_changed are exact.
* **Run:** `M14-VANILLA-RUN.cmd AUTO drawtags` (sets
  `OPENSPM_M14_DRAW_TAGS=1`). The LLVM per-player module rebuilds only its
  bridge translation unit; LLVM generation is reused. With the variable unset
  the bridge emits nothing and the renderer stamps nothing.
* **Log, once per second:**
  - `M14 draw tags: frames= draws= tagged= (x%) untagged= cameras=<id>:3D|2D:<share of draws>
    owners= owners_max= spawned= despawned= count_changed= begins= ends=
    repeat_begins= malformed= frame_gaps=` (renderer).
  - `M14 draw tags bridge: frames= callbacks= begins= normal_ends= escaped=
    phase_ends= entry_mismatch= skipped_dl= skipped_nofifo= emit_failures=` (bridge).
  - Healthy output: tagged near 100% during gameplay (the rest is outside-camera
    work such as copies/clears at DEMODoneRender), `entry_mismatch`,
    `malformed`, `frame_gaps` and `repeat_begins` at 0, and `count_changed`
    small while standing still.

### Stage C: interpolated images (implemented, opt-in, awaiting player test)

Each vblank the timeline gives a pair (A, B) and alpha; the presenter shows B's
draw list replayed with A/B-interpolated matrices.

* **Camera views from the game.** Stage B's markers gained a view record: before
  the first owner of each 3D (perspective) camera in a frame, the bridge sends
  that camera's world->view matrix `CamEntry+0x54` (12 floats as 24 BP writes
  to 0x7C/0x7D, `m14_draw_tags.h`). The renderer attaches the record to every
  later draw of the camera (`DrawCommand::m14DrawView`, still in padding;
  `FramePacket::m14DrawViews`). `apply_draw_tags_patch.py` revision 2 adds
  this and migrates revision-1 caches in place.
* **Model** (`renderer/aurora/frame_interp.hpp`: pure, no Aurora).
  - Draws are matched by (owner id, ordinal within the owner in stream order),
    so the z sort never matters.
  - Position matrices: `M = V^-1 PN` per frame, `PN' = V(alpha) interp(M_A, M_B)`.
    `V(alpha)` slerps the camera orientation and lerps the camera position.
    `interp` slerps rotation and lerps per-axis scale and translation. A
    sheared or mirrored basis is lerped element-wise instead (a paper sprite
    turning around passes through zero width, as in the game). Normal matrices
    get the same treatment with the camera rotation removed.
  - Without a valid view pair (camera not reported or not rigid, or
    `OPENSPM_M14_INTERP_VIEW=0`), PN itself is interpolated. For a camera
    that only translates this is exactly the same result; with camera rotation
    it differs only in second order.
  - Owner snaps (the owner keeps its B pose; with views it still follows the
    interpolated camera, so it does not jitter against the world): not in A
    (spawn), different draw count, any draw with a different structure (vertex
    or index count, uniform size, PN slot, skinning, camera), moved more than
    100 units or rotated more than 150 degrees in one frame (teleport, rotation).
  - Whole-pair rejects: camera cut (the camera moves more than 150 units or
    turns more than 45 degrees in one frame; without views, the median draw
    motion), scene change (fewer than 25% of B's 3D draws matched).
  - Untouched: 2D/UI (orthographic) cameras and untagged draws keep B's uniform
    block byte for byte. Projection, texture matrices and every other uniform
    stay B's.
  - `plan_pair()` does everything alpha-independent once per game frame.
    `apply_alpha()` rewrites only the matrices of the patched blocks, once per
    refresh. alpha 0 writes A's matrices and alpha 1 B's block, byte for byte.
* **Aurora glue** (`frame_interp_presenter.inl` + `apply_frame_interp_patch.py`,
  last in the chain).
  - *Retain* (render worker, end of every published frame, in that frame's
    own command encoder):
    - The draw list up to the display copy. The uniforms come from an exact
      CPU mirror written while recording (`FramePacket::m14InterpUniforms`),
      never read back from write-combined staging.
    - GPU copies of the frame's vertex, index, storage and uniform bytes from
      its staging buffer into one of three retained buffer sets. This is the
      "keep A/B alive" part the dev140-145 attempts lacked: their packets died
      with the next frame.
    - Texture bind groups resolved at once, so cache expiry cannot drop them.
    - A planner thread plans (previous, this).
  - *Replay-unsafe frames* (shown natively as B):
    - EFB/texture copies, encoder tasks (bbox writes), bounding-box reads,
      palette conversions, offscreen draws, pass snapshots, custom draws.
    - A first pass that loads the previous EFB (readback continuation),
      texture uploads after draws, a missing or repeated display copy, a layout
      change.
    - Any newer retained frame with an EFB copy (copy textures B samples may
      have changed).
  - *Present* (render worker, per vblank):
    - alpha < 0.001 shows A's own image and alpha > 0.999 B's.
    - Otherwise: write B's patched uniform arena (one queue write),
      re-encode B's recorded EFB passes against the retained buffers into a
      private EFB, repeat B's display copy into a private XFB image, and
      present that. `m14_encode_frame_interp()` in encoding.cpp mirrors
      `render_pass()` exactly, with a private bbox target.
    - Nothing is re-parsed and nothing touches game memory.
    - Any fallback shows B, never A: the presented time never goes backwards.
  - The presenter's guard grows to the measured replay cost + 1.5 ms (0.4..0.85
    of a refresh), so the replay finishes before its vblank.
* **Reused from dev140-145:**
  - the `DrawCommand` replay sidecar (`m14ReplayGX`, uniform prefix, indexed
    matrices);
  - the `m14ReplayEfb` pass flag;
  - `m14_end_frame_packet()`;
  - the private-bbox `ReplayScope`;
  - the display-copy replay through `tex_copy_conv`, now with a uniform-buffer
    override;
  - the fail-closed preflight reasons.

  Not reused: heuristic geometry identity, record-time alpha grids, the
  translation-only camera delta, fallback timing.
* **Cost.**
  - Model, `tests/m14_frame_interp_test.cpp`, 3000 draws (2400 3D in 600
    owners, 10% skinned): plan about 3 ms per game frame (planner thread) and
    apply about 0.7 ms per refresh.
  - Dawn Null backend (`bench_frame_interp_dawn.py`): re-encoding 3000 recorded
    draws takes 0.75-0.95 ms with validation skipped (release) and 1.7 ms with
    it on. The uniform upload (about 4 MB) takes about 0.4 ms.
  - Together that is about 2 ms of CPU per refresh plus the driver's own
    recording, well inside 6.9 ms at 144 Hz. Tight at 240 Hz.
  - Render bundles would cut the encode to about 0.03 ms. They are measured
    but not used yet: bundles cannot set viewport, scissor or blend constant,
    so the draws would have to be split at every such change.
* **Tests** (`test_display_pacing.py`):
  - byte-exact endpoints;
  - mid-alpha poses for walking, turning and static objects under a panning
    and a rotating camera;
  - equivalence with and without views for a translating camera;
  - every snap rule, and whole owners snapping;
  - cuts with and without views, and scene change;
  - UI and untagged draws untouched;
  - z-order reshuffles matching exactly;
  - sprite flip and skinned slots;
  - view record decoding, including exact bits and partial records.

  `check_renderer_compile.py` compiles the chain, a fresh apply and an in-place
  re-configure, and migrates from the revision-1 draw tags and timeline.
* **Run:** `M14-VANILLA-RUN.cmd AUTO interp` (timeline smooth + draw tags +
  `OPENSPM_M14_INTERP=1`), or `interpdirect` (views off). Unset, nothing is
  mirrored, retained or replayed.
* **Log:** `M14 frame interp active: ...` once, then once per second
  `M14 frame interp: presents= interpolated= native_a= native_b= fallbacks=<reason:n,...>
  frames= unsafe_frames= pairs= owners= matched_owners= snapped[spawn,count,structure,teleport,rotation]=
  view_draws= view_static= replay_us_avg= replay_us_max= draws_per_replay= upload_kb_per_replay= plans= plan_us_avg=`.
  - `view_static` is the share of matched draws whose world pose did not
    change. While the camera pans over static scenery it is high only if the
    view records are the matrices the game really uses.
  - `docs/TEST-LIST.md` item 9 lists what healthy looks like.
* **Remaining:**
  - A smarter frame choice at a run of unsafe frames: it holds B.
  - Render bundles for 240 Hz.
  - Interpolating the projection (zoom) and texture matrices.
  - Replaying frames that contain EFB copies (flip and door effects) by
    re-executing the copies.
  - Phase-centring the timeline at alpha 0 on 120/240 Hz, so half the
    refreshes need no replay.

### What: identities come from the game, not from geometry

We have the game's code, so the renderer should be told, not guess:

* **Camera:** the game's exact view/projection per camera (`CamEntry` viewMtx
  at +0x54, `camPtrTbl` 0x804C9F80) marked at the start of each camera pass.
  Interpolate the camera separately from objects: `PN' = V(α) · lerp(M_A, M_B)`
  where `M = V⁻¹ · PN`. This also handles 3D-flip rotation.
* **Objects:** tag every draw with its owner: the `dispDraw` callback
  (0x8005B028–0x8005B2F4) and its object pointer (`callbackParam`), plus the
  draw ordinal inside that callback. Mario's body parts, a door and its house,
  an NPC and its accessories each share one owner group.
* **Where the owner comes from (mapped 2026-09-23, R8PE01 rev 2):**
  `dispDraw(camId)` at 0x8005B028 walks the z-sorted display list (pointer
  array at 0x8056D1CC, count at 0x8056D1D0; sorted by `qsort` from 0x8005B010
  on the f32 at entry+4). For each entry with `entry->camId == camId`
  (u8 +0), it applies the entry's scissor (u8 +2, 16-byte rects at
  0x804CA020) and render mode (u8 +1: blend/alpha-compare/z-mode presets). It
  then stores the entry pointer in 0x8056D1D4 and calls
  `entry->callback(camId, entry->param)` (+8 / +12); the callback pointer is
  also kept in 0x8056D1D8. Every GX draw issued inside that call belongs to
  that entry (r13 = 0x80574900; the globals are r13-30516..-30500).
  The FIFO is parsed later than the CPU runs, so the renderer cannot just
  read those globals at parse time. The bridge must put the owner into the
  command stream: at the `bctrl` (0x8005B29C) and after it returns, emit a
  write to an otherwise unused BP register carrying an owner index (entry
  pointer and param hashed into 24 bits). Aurora's `handle_bp` caches
  unhandled BP registers silently, so a small decode handler can latch
  "current owner" in exact stream order at zero cost to other draws. Camera
  identity comes from the same loop (`camId`, one `dispDraw` call per camera).
* **Group-level fail-closed:** if any draw in an owner group cannot be matched
  between A and B (spawn, despawn, model swap, teleport), the whole group snaps
  to B. Parts can never separate.
* **Screen-space/HUD:** draws under a 2D/UI camera use the authoritative pose.

Interpolating only the camera is not enough: the camera follows Mario, so a
smooth camera with an un-interpolated Mario just moves the judder onto Mario.

### Cost

Re-running SPM's own draw code per extra frame (~6 ms of game CPU per pass)
would starve the 60 Hz simulation at 100–240 Hz, so interpolation stays in the
renderer: record frame B once, replay it with per-group interpolated matrices.
Replay cost should later drop to near zero by re-submitting recorded GPU
command bundles with only uniform buffers changed.

### Invariant

Interpolated values are never written into game memory; gameplay, physics,
timers and scripts only ever see authoritative 60 Hz state.

## Relation to the mod API

The owner/camera tags are the same semantic data mods need ("this draw belongs
to NPC X"). Building them as a proper renderer contract, rather than a private
heuristic, is groundwork for renderer hooks, custom models and per-object
effects later.
