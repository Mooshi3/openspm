# M14 dev137 — configurable visual transform interpolation proof

> **REJECTED RUNTIME EXPERIMENT.** dev137's live midpoint substitution caused
> severe producer stalls (~55–105+ ms DEMODoneRender tails in heavy scenes),
> black flicker and cross-instance geometry corruption. It must not be restored.
> See `M14-DEV138-GEOMETRY-IDENTITY-PROBE-2026-09-21.md`.

Date: 2026-09-21
Branch: `m14-native-execution`

## dev136 player result

The player exercised normal movement, multiple rooms, 2D/3D flips and scene
transitions for roughly 10,000 completed display-copy frames.

The dev136 periodic pairing telemetry is strong enough to advance:

- 91 total sampled reports (startup + every 120 completed display-copy frames);
- excluding startup samples, 217,123 candidate draws were observed;
- 212,388 matched the prior frame (97.82% weighted);
- 209,560 matched draws passed the dev136 interpolation safety gate
  (98.67% weighted among matches);
- median sampled match rate was 100%;
- median sampled safety rate was 100%;
- every sampled matched draw was rigid/non-indexed
  (`indexed_matches=0` in this run).

The low samples cluster at real discontinuities, which is desirable:

- frame 720: a new scene has 2,387 candidates but the prior frame has one;
  matching falls effectively to zero and must snap;
- frame 6840: 3,285 candidates, 92.8% match, only 65.2% of those pass the old
  broad safety gate during a heavy transition/flip window;
- later steady samples return to 100% match / 100% safe.

Therefore interpolation must be fail-closed per draw and per discontinuity. Do
not force continuity across a room load, flip cut, teleport or unmatched mesh.

## dev136 matcher correction

The probe identity is intentionally coarse: pipeline + texture + primitive +
vertex format/count + projection type + indexed/non-indexed state. Multiple
instances can share one identity. dev136 paired repeated identities by occurrence
order, which was sufficient for feasibility telemetry but is too optimistic for
visible output.

dev137 changes visible pairing to choose the nearest unused previous transform
among matching identities. A previous draw can be consumed once per frame.
Invalid, discontinuous, indexed or unmatched draws retain the authoritative
current uniform range.

## Config model

Interpolation is now a persistent OpenSPM setting rather than a machine-specific
hardcode.

`OpenSPM.ini` keys:

```ini
[OpenSPM]
interpolation=auto
interpolation_target_fps=0
```

Semantics:

- `auto`: use interpolation when the active renderer supports it;
- `on`: explicitly enable it;
- `off`: vanilla 60 Hz presentation;
- `interpolation_target_fps=0`: follow the active display refresh;
- a positive target requests that presentation rate once the decoupled presenter
  is available.

Launcher CLI now also accepts:

```
--interpolation auto|on|off
--interpolation-fps <hz>
```

The launcher exports stable process settings for the renderer. The current M14
developer launchers still set the environment directly because they launch the
candidate without going through the eventual consumer UI.

## HOME / OpenSPM menu architecture

Do not build the full in-game menu during dev137.

The native runtime already owns the exact SPM HOME seam:
`spmarioMain -> homebuttonMain`. Nintendo's HOME overlay currently reports
inactive and OpenSPM owns PC system UI policy there.

The future HOME-button overlay should be a front end over the same persistent
settings model, not a separate configuration system. Planned top-level sections:

- Resume
- Graphics (resolution, aspect/ultrawide, vsync, interpolation, target FPS,
  filtering and later renderer options)
- Controls / keybindings
- Mods
- Cheats
- Debug / diagnostics
- System (reset/quit and related port actions)

Settings that are safe to change live should apply through renderer/runtime
services; settings requiring renderer restart can be clearly marked and persisted
for next launch.

## dev137 visual proof

Patch:
`renderer/aurora/apply_transform_interpolation_visual_patch.py`

Launcher:
`M14-LLVM-INTERP-MIDPOINT-RUN.cmd`

This is deliberately still a 60 Hz presentation test. It does **not** claim the
high-refresh goal yet.

For matched rigid draws it creates a second uniform block containing:

- linearly interpolated projection;
- position transform decomposed to translation / scale / quaternion rotation;
- quaternion slerp for rotation;
- interpolated normal transform;
- default alpha 0.5, configurable with `OPENSPM_INTERPOLATION_ALPHA`.

Aurora's GX `DrawData` already consumes the full 128-byte inline draw payload
on 64-bit Windows, so dev137 deliberately does **not** add a second field to that
ABI. The authoritative range remains in the draw cache; a temporary local
midpoint range is staged beside it while recording the draw. When interpolation
is enabled and `OPENSPM_M14_INTERP_VISUAL=midpoint`, the existing
`DrawData.uniformRange` field receives the midpoint range for that recorded
command. Otherwise it receives the authoritative range. No `pipeline.hpp` or
`pipeline.cpp` migration is required for this proof.

Safety rules:

- indexed PN-matrix draws: current frame;
- unmatched draw: current frame;
- matrix-slot mismatch: current frame;
- non-finite/decomposition failure: current frame;
- translation discontinuity above 256 units/tick: current frame.

The authoritative GX state is restored immediately after staging each
interpolated uniform block. Guest RAM and simulation state are never modified.

## What the player should judge

Run:

```bat
M14-LLVM-INTERP-MIDPOINT-RUN.cmd
```

Do not judge framerate smoothness yet: output is still one present per 60 Hz
simulation frame. Judge correctness:

- camera/world should be spatially plausible, just half a tick delayed;
- Mario/NPC/object transforms should not warp or swap between repeated meshes;
- enter/leave rooms;
- flip 2D <-> 3D repeatedly;
- trigger effects, doors, UI/text and cut-like transitions;
- look for geometry explosions, wrong transforms, flicker, one-frame smears or
  persistent half-position artifacts.

If dev137 is visually correct, dev138 is the retained-scene/replay presenter:
the same interpolated uniform mechanism is evaluated at display-time alpha and
the sealed scene is rendered into extra host presentation slots while SPM
continues to simulate at 60 Hz.
