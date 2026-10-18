# OpenSPM mod package direction

> Status: the manifest's first form is built as a loose info file next to
> each DLL (`mods\<name>.toml`, id / name / version / author / description /
> requires; docs/MODDING.md "Mod info files"). Packages, payload classes and
> dependencies between mods are still to come.

This document fixes the distribution and trust model for additive OpenSPM content without freezing the future gameplay-service ABI too early.

## Package, not loose DLL

A native DLL is an execution payload, not the user-facing mod distribution format. The intended installable unit is a single `.openspm` package (ZIP-compatible or equivalently inspectable container) with a manifest and optional payload classes.

Example:

```text
mooshi.goomba-capsule.openspm
  mod.toml
  bin/
    win-x64/
      mod.dll
  assets/
  overlays/
  data/
  icon.png
```

A package may eventually contain one or more of:

- native OpenSPM code;
- legacy SPM REL payloads;
- virtual filesystem overlays;
- asset/texture replacements;
- custom maps, rooms, actors, events, dialogue, music, or other data;
- a future sandboxed payload format.

This gives the launcher/mod manager one stable object to install, enable, disable, inspect, version, and remove.

## Minimal manifest contract

The exact serialization is not frozen, but the manifest needs stable concepts roughly equivalent to:

```toml
id = "mooshi.goomba_capsule"
name = "Goomba Capsule"
version = "0.1.0"
openspm = ">=0.13"

[code]
windows_x64 = "bin/win-x64/mod.dll"

[capabilities]
items = true
actors = true
persistent_state = true
```

Required design properties:

- globally stable, namespaced mod ID;
- explicit package version;
- explicit OpenSPM compatibility range;
- declared payloads and capabilities;
- dependencies expressed by stable IDs/version ranges rather than filenames/load order hacks;
- deterministic conflict reporting;
- package contents never need to be unpacked into the immutable R8PE01 game root.

## Trust tiers

### Native payload

A native Windows DLL is trusted process code. It has maximum performance and access, which is appropriate for the first serious C/C++ SDK and for ambitious total-conversion work.

OpenSPM's supported APIs remain non-destructive, but the OS cannot stop a deliberately malicious in-process DLL from calling filesystem APIs itself. The UI should make this trust fact clear rather than pretending native code is sandboxed.

Native code is the v1 priority. Sandboxing must not delay the useful SDK.

### Sandboxed payload (future)

A later execution tier, potentially WASM or another capability-safe format, can constrain filesystem/process access and expose only explicit OpenSPM services. The `.openspm` container is deliberately payload-neutral so adding this later does not require a second mod distribution ecosystem.

## Legacy content

Legacy SPM mods remain first-class import targets rather than being forced into native C rewrites. A package can wrap them, for example:

```text
classic-mod.openspm
  mod.toml
  legacy/
    mod.rel
  overlays/
```

Planned compatibility order remains:

1. REL mods;
2. Riivolution-style filesystem overlays where semantics map cleanly;
3. Dolphin-compatible texture packs;
4. Gecko/Ocarina compatibility where safe and meaningful.

Import/autodetection may accept existing layouts, but installed OpenSPM-managed content should converge on one package-management model.

## Install and disable semantics

Installing a package only makes its definitions and payloads available. It does not grant gameplay content automatically.

Disabling a package:

- removes its active definitions and runtime effects from the next coherent campaign revision;
- leaves unrelated vanilla progress untouched;
- leaves namespaced mod-owned persistent state dormant by default;
- reconciles owned additive content losslessly according to `MOD-STATE-RECONCILIATION.md`;
- never repairs or rewrites the extracted R8PE01 source because the package never modifies it in the first place.

Native DLL hot-unload is not required for v1. A native enable/disable change may require a clean relaunch. That is an implementation constraint, not a semantic loophole.

## Creator ergonomics requirement

The normal creator loop must eventually be closer to:

```c
openspm_register_item(&goomba_capsule);
openspm_register_character(&new_character);
openspm_register_pixl(&new_pixl);
```

than to Wii modding.

A normal additive mod should not require:

- selecting an unused vanilla slot;
- hard-coded guest addresses in public mod code;
- knowing MEM1/MEM2 layouts;
- rebuilding OpenSPM, ModernGekko, Dolphin, or the translated R8PE01 module;
- patching the user's game files on disk.

Common changes should mean replacing or dropping one package. OpenSPM internals may still use translated guest structures behind services, but those details are implementation-private.

## Goomba Capsule package acceptance slice

The first useful native-content proof should be a real package, not only an SDK unit test:

- ID: `mooshi.goomba_capsule`;
- new item definition, not a replaced vanilla slot;
- price 0 from Flipside shop inventory;
- normal 10-slot carried-inventory behavior;
- overflow/recovery through 32-slot shop storage then Flipside mail;
- use action calls a native actor-spawn service to create a Goomba;
- ownership/state lives in the mod namespace;
- disable/re-enable is lossless and does not mutate vanilla save semantics;
- can be tested rapidly using the Flipside Shop debug boot target once live game services exist.

That slice is intentionally small but crosses the whole platform: package discovery -> native C -> additive registry -> stock-facing shop/inventory integration -> actor service -> persistent ownership -> reconciliation.