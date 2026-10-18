# OpenSPM portability boundary

M11 and later should treat ModernGekko/RecompCore/Dolphin as a temporary compatibility backend, not as OpenSPM's permanent architecture.

## Stable OpenSPM-facing contracts

Game patches, OpenSPM menus, and future tooling should talk in OpenSPM semantics, for example:

- aspect ratio: Auto / 4:3 / 16:9 / 21:9 / 32:9
- fast-forward: enabled / disabled (and later hold/toggle bindings)
- free camera: enabled, transform, speed, HUD visibility, freeze/frame-step controls
- presentation: simulation rate vs presentation rate
- input: logical OpenSPM actions, not Dolphin hotkey identifiers

Those contracts must not expose Dolphin `Config::*`, Qt objects, Dolphin hotkey IDs, presenter enums, or RecompCore internals.

## Temporary backend adapter

The current Windows runtime may implement those contracts using RecompCore/Dolphin facilities. That dependency belongs only in the adapter implementation. The game/module side should receive small backend-neutral values or exports such as `openspm_set_aspect_ratio(u32)`.

When OpenSPM migrates to a standalone static-recomp runtime, replace the adapter implementation while preserving the OpenSPM-facing API and game-side behavior.

## M11 ultrawide

The M11 module patch follows this rule: it owns an OpenSPM aspect enum and projection behavior. The temporary host adapter maps that aspect choice to the current presenter and forwards the same OpenSPM aspect value to the module.

Do not implement future OpenSPM features by directly wiring menu actions to Dolphin features unless that wiring is hidden behind an OpenSPM API that can be reimplemented later.
