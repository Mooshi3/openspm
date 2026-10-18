# OpenSPM documentation

Start here. These pages describe OpenSPM as it is now; the dated investigation
notes from the M13/M14 bring-up (156 of them) are in [archive/](archive/) and
are kept for history only. When they disagree with a page here, this page wins.

## Playing and testing

- [PLAYING.md](PLAYING.md): what you need, how to launch, keys, settings.
- [TEST-LIST.md](TEST-LIST.md): the in-game tests still waiting for a run.
- [TEXTURE-PACKS.md](TEXTURE-PACKS.md): Dolphin-format HD texture packs.

## How OpenSPM works

- [NATIVE-PORT-ARCHITECTURE.md](NATIVE-PORT-ARCHITECTURE.md): the end state (a
  native PC port runtime, not an emulator) and how the pieces fit.
- [M13-ARCHITECTURE.md](M13-ARCHITECTURE.md): the platform layout (launcher,
  backend, renderer, mods); also shipped as the release README.
- [PRESENTATION-ARCHITECTURE.md](PRESENTATION-ARCHITECTURE.md): frame rate,
  pacing and interpolation.
- [AUDIO-PLAN.md](AUDIO-PLAN.md): the AX audio emulation (HLE) and its status.
- [M14-LIVE-DEBUG-CONSOLE-2026-09-19.md](M14-LIVE-DEBUG-CONSOLE-2026-09-19.md):
  the live debug cockpit.

## Mods and content

- [MODDING.md](MODDING.md): the mod interface today (services, events, samples)
  and what is missing.
- [CONTENT-API.md](CONTENT-API.md): added characters, Pixls and items; how
  OpenSPM puts them into the game (code patch recipes, item table relocation).
- [HOOKS.md](HOOKS.md): the function hook design (`openspm.hooks`), the
  foundation for characters with their own movement and abilities.
- [MOVEMENT.md](MOVEMENT.md): the move table and how a character gets its own
  movement (Kirby, Sonic) through move hooks instead of a new id.
- [CHARACTER-TABLES.md](CHARACTER-TABLES.md): every per-character table and
  why added characters stay on a base slot (swapping rows) instead of ids 4+.
- [CHARACTER-AUDIT.md](CHARACTER-AUDIT.md) (generated) and
  [CUSTOM-CHARACTERS.md](CUSTOM-CHARACTERS.md): groundwork for real character
  ids 4+.
- [MOD-PACKAGES.md](MOD-PACKAGES.md), [MOD-STATE-RECONCILIATION.md](MOD-STATE-RECONCILIATION.md):
  the planned package format and how mod content state is kept consistent.

## Plans

- [OPENSPM-PORT-ROADMAP.md](OPENSPM-PORT-ROADMAP.md): the product target and
  roadmap.
- [RELEASE-PLAN.md](RELEASE-PLAN.md): from "run the .cmd" to "unzip, run the
  exe, pick your game".

## Ground rules (all pages)

- No game code, assets or data in the repository: patches are recipes applied
  to the player's own files, and everything that touches game memory checks
  it is R8PE01 rev 2 first and turns itself off otherwise.
- Risky features are opt-in or have an off switch; the default launch stays
  unchanged.
