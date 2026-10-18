# spm-recomp (OpenSPM)

A native PC port of Super Paper Mario (USA, R8PE01 revision 2) built by static
recompilation with DolRecomp and Aurora. Bring your own game; no Nintendo code
or assets are included.

## Status: wrapped up

This project is no longer developed. For playing Super Paper Mario on PC, see
[New Super Paper Bros.](https://github.com/josiahg2502-glitch/NewSuperPaperBros)
(WiiCompiled-based; it runs the revision 0 disc). OpenSPM's code is MIT
licensed: take whatever is useful, keeping the notice.

What is worth taking, besides the code:

* **Mods that add content** (characters, Pixls, followers, new maps, enemies,
  NPC messages): [docs/CONTENT-API.md](docs/CONTENT-API.md),
  [docs/MODDING.md](docs/MODDING.md), [docs/CHARACTER-IDS.md](docs/CHARACTER-IDS.md),
  [docs/CHARACTER-TABLES.md](docs/CHARACTER-TABLES.md).
* **New levels**: the game's map, pipe, enemy, tattle and camera formats and
  how to make new ones: [docs/CUSTOM-LEVELS.md](docs/CUSTOM-LEVELS.md), with
  the level builder in `tools/content/` (`level_build.py`, `map_build.py`,
  `sample_level.py`) and a test machine that runs the game's own code on the
  output (`tools/content/game_harness.py`).
* **Frame pacing and interpolation design**:
  [docs/PRESENTATION-ARCHITECTURE.md](docs/PRESENTATION-ARCHITECTURE.md).
* **What was never tested in the game** (the sample level, Stage C
  interpolation, the Wii Remote shake, ...): [docs/TEST-LIST.md](docs/TEST-LIST.md).
  Everything listed there is built but unproven.

All game addresses in this repository are for R8PE01 revision 2.

## Docs

* How to play, controls, hotkeys and settings: [docs/PLAYING.md](docs/PLAYING.md)
* Audio plan: [docs/AUDIO-PLAN.md](docs/AUDIO-PLAN.md)
* Dolphin HD texture packs: [docs/TEXTURE-PACKS.md](docs/TEXTURE-PACKS.md)
* Development log: [M14-RESUME.md](M14-RESUME.md)
