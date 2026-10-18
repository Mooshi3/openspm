# M14 dev136 — OpenPete-style transform interpolation backport

Date: 2026-09-21
Branch: `m14-native-execution`

## Decision

The pacing campaign has reached the point where presentation interpolation is
now the correct frontier.

Player-visible cadence results:

- dev130: authoritative 60 Hz simulation on ~99.94 Hz display remains jittery;
- dev132: ~49.9715 Hz (exact half-display diagnostic) has materially less jitter
  but Immediate/vsync-off tears;
- dev135: same ~49.9715 Hz diagnostic with FIFO/vsync ON removes tearing and
  leaves only a small residual judder.

Therefore do not slow the shipped simulation. Restore 60 Hz Wii timing and
decouple presentation.

## Target architecture

Follow the same architectural contract used by modern native recompilation
ports:

1. game logic/input/audio/VI remain authoritative at the original 60 Hz;
2. rendering records presentation-relevant state for each completed simulation
   tick;
3. corresponding render draws are paired across consecutive ticks;
4. projection/model/normal transforms are evaluated at sub-tick alpha;
5. the completed host scene is replayed for additional display slots;
6. presentation may run at ~99.94, 120, 144 Hz, etc. without running game logic
   additional times;
7. present-time interpolation never writes guest RAM.

Do **not** use finished-XFB alpha blending as the production interpolation
mechanism. It ghosts actors, text/UI and particles.

## External reference audit

OpenPete's source is not public, so there is no implementation to copy. Its
public architecture describes fixed-tick simulation with render-time sub-tick
interpolation.

WiiCompiled is public and contains an Aurora/Wii interpolation implementation.
It is useful as an architectural reference because it records GX draw
projection/position/normal transforms, matches corresponding draws across
frames, builds interpolated uniform ranges, seals a scene, and replays that
scene for inserted presentation slots.

WiiCompiled's runtime is GPL-licensed. OpenSPM therefore does **not** import or
copy that implementation. dev136 is a clean-room implementation against the
pinned MIT `encounter/aurora` API and OpenSPM's existing patch layer.

## Why retained scene replay is required

Pinned Aurora currently owns each host scene through a `FramePacket`.
Render passes are encoded on Aurora's render worker and the packet/staging slot
is recycled after submission. A completed XFB texture can be retained, but that
only preserves final pixels; it cannot render a real intermediate world state.

True interpolation therefore needs two independent pieces:

- **transform history:** previous/current renderer-visible GX state per draw;
- **retained scene replay:** immutable/replayable host draw data whose uniform
  ranges can select current or interpolated transforms.

dev136 implements the first piece and deliberately leaves pixels unchanged.

## dev136 implementation

Patch:
`renderer/aurora/apply_transform_interpolation_probe_patch.py`

Launcher:
`M14-LLVM-INTERP-PROBE-RUN.cmd`

The launcher restores the authoritative 60 Hz Wii cadence, uses FIFO/vsync for
a tear-free baseline, and enables:

```
OPENSPM_M14_INTERP_PROBE=1
```

For each GX draw, dev136 records:

- a stable material/shape identity;
- projection matrix;
- full `MaxPnMtx` position/normal palette;
- current PN matrix index;
- whether per-vertex indexed matrices are active.

At the real `GXCopyDisp` boundary it pairs current draws against the previous
completed Wii frame, reports candidate/match/motion counts, and validates
finite/bounded interpolation pairs. No guest state or draw output is modified.

Expected log marker:

```
M14 dev136 GX transform interpolation probe active
```

Expected periodic stats:

```
M14 dev136 interpolation pairing frame=... candidates=... previous=...
matches=... match_pct=... moving=... safe_midpoints=... safe_pct=...
rigid_matches=... indexed_matches=...
```

## Interpretation

A high stable match percentage in normal Yold camera motion means the renderer
has enough correspondence to proceed directly to replay. Low match rate means
identity needs refinement before any visual interpolation is enabled.

`indexed_matches` is important. A high count means the retained-scene phase
must support the entire PN matrix palette from the start rather than only the
single current matrix.

## dev137 target after dev136 evidence

Build the retained scene/uniform replay layer:

- seal completed GX render passes before Aurora recycles the FramePacket;
- retain immutable vertices/indices/storage/uniform inputs required for replay;
- preserve texture/bind-group ownership safely across the replay window;
- generate at least one interpolated uniform slot from previous/current
  transforms;
- first visual proof: one midpoint scene between two 60 Hz frames;
- render GPU work only on Aurora's existing render worker;
- if replay is unsafe because of CPU-visible EFB/readback or discontinuity,
  present the authoritative current frame instead;
- never fabricate a Wii retrace or run SPM logic for the inserted slot.

After midpoint replay is correct, generalize the scheduler from fixed 120 Hz
semantics to the actual SDL display period (~99.94 Hz on the player's current
monitor), choosing alpha from host presentation time.

## Keep

Keep all dev129/dev130 scheduler/performance fixes. Interpolation is now a
presentation layer on top of the recovered ~60 Hz producer; it is not a
replacement for those fixes.
