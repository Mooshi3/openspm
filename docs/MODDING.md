# OpenSPM modding: what exists and where it goes

Moddability is a core goal. This page describes the mod interface that exists
today, what it can do, and the gaps before mods can do "nearly anything".

## The mod ABI today (`include/openspm/mod_api.h`)

A mod is a Windows DLL in the `mods` folder that exports:

```c
int OPENSPM_CALL openspm_mod_query(uint32_t requested_abi,
                                   const openspm_host_api *host,
                                   openspm_mod_api *out_api);
```

It fills `openspm_mod_api` with an id, version, capability bits, its own
context pointer and an `on_event` callback. The host then delivers:

| Event | When |
| --- | --- |
| `STARTUP` / `SHUTDOWN` | mod load / unload |
| `RUNTIME_RUNNING` / `RUNTIME_STOPPED` | the game core starts / stops |
| `TICK_BEGIN` / `TICK_END` | around each simulation tick |
| `PRESENT_BEGIN` / `PRESENT_END` | around each presented frame |

Through `host->query_service` a mod can get host services. The only one so
far is `openspm.runtime` (`include/openspm/runtime_service.h`): performance
snapshots, pause/resume, request stop, lifecycle, present and simulation-tick
events. `mods/debug/openspm_debug.c` is the reference mod: it acquires the
runtime service and logs the first tick and present with performance data.

Mods can be switched on and off per file in `OpenSPM.ini`:

```ini
[Mods]
openspm_debug.dll = 0
```

## M14 native runner: opt-in loading (new)

`OpenSPMNative.exe` (what the M14 `.cmd` launchers run) now loads mods when
`OPENSPM_MODS=1` or `OpenSPM.ini [Mods] enabled = 1`. It loads from the `mods`
folder next to the exe (`openspm_debug.dll` builds into the build folder; copy
it there to try it). It delivers `STARTUP`, `RUNTIME_RUNNING` (right before the
game session), `RUNTIME_STOPPED` and `SHUTDOWN`, plus the per-frame
`TICK_BEGIN` and `PRESENT_END` events below.

All of it lives in one settings file: `[Mods] enabled` and the per-mod
`[Mods] <file>.dll = 0/1` toggles are read from the file the launcher applied
(`OPENSPM_SETTINGS`, which `M14-VANILLA-RUN.cmd` points at `OpenSPM.ini` next to
the .cmd files; otherwise `OpenSPM.ini` next to `OpenSPMNative.exe`). The F1
menu's Mods tab edits that file.

## Per-frame tick and guest memory (OpenSPMNative)

Once per game frame the native backend reaches its `PRESENT_SAFEPOINT` (the
once-per-retrace boundary: Broadway paused between two guest frames, input for
the next frame latched, F1 cheats just applied). There it delivers:

| Event | Payload | Notes |
| --- | --- | --- |
| `TICK_BEGIN` | `openspm_runtime_tick_event` (`tick_count` = frame index) | guest memory is open |
| `PRESENT_END` | `openspm_runtime_present_event` (`frame_count`, `present_count`) | after the renderer took the frame at the safepoint (not the display flip); guest memory is closed |

Both arrive on the guest (CPU) thread, before the next guest frame runs, so
keep callbacks short: the game waits for them. `TICK_END` is not sent by the
native backend (the safepoint is both the end of one frame and the start of
the next). Check `header.struct_size` before reading a payload field.

How it is wired (no ABI break): the backend already stores the
`openspm_host_api` pointer it gets in `openspm_backend_query`, and that struct
already has the ABI-v1 `runtime_event` sink. OpenSPMNative sets
`host.runtime_event` (and `OPENSPM_HOST_CAP_RUNTIME_EVENTS`) only when at least
one mod loaded, before `backend.run`, and clears it after. At the first frame
the backend caches the sink (or notes there is none); from then on a run
without mods pays one flag compare per frame. The launcher maps runtime events
to mod events with `openspm_mod_event_from_runtime()` (`mod_api.h`).

### `openspm.guest_memory` service (`include/openspm/guest_memory.h`)

Query it once, e.g. at `STARTUP`:

```c
openspm_guest_memory_service mem = {0};
mem.header.struct_size = sizeof(mem);
mem.header.abi_version = OPENSPM_GUEST_MEMORY_SERVICE_VERSION;
host->query_service(host->context, OPENSPM_GUEST_MEMORY_SERVICE_ID,
                    OPENSPM_GUEST_MEMORY_SERVICE_VERSION, &mem);
/* in TICK_BEGIN: */
uint32_t seq;
if (mem.read32(mem.context, 0x804D0D60u, &seq) == OPENSPM_OK) { /* ... */ }
```

- `read8/16/32`, `write8/16/32`: big-endian guest values (the value you pass
  or get is the number the game sees).
- Addresses: cached MEM1 `0x80000000..0x817FFFFF` or MEM2
  `0x90000000..0x93FFFFFF`, naturally aligned to the access size, with the
  backing store attached: the cheats' validation
  (`openspm_cheat_address_ok`), generalized to 1/2/4 bytes. Otherwise
  `OPENSPM_ERROR_INVALID_ARGUMENT` (or `OPENSPM_ERROR_RUNTIME` when the
  backing is missing).
- Only valid inside your `TICK_BEGIN` callback, on the thread that delivered
  it. Any other time every call returns `OPENSPM_ERROR_UNSUPPORTED` and
  touches nothing. Don't keep host pointers into guest memory; there are none
  to keep.
- Versioning: the service is version 1; later versions only append fields,
  and a caller must pass `struct_size >= sizeof(v1)`. The mod ABI
  (`openspm_mod_api`, event IDs, `openspm_host_api`) is unchanged, so existing
  mods keep working; a host or backend without the service answers the query
  with an error, which a mod treats as "not available".
- Addresses are game-revision facts (R8PE01 rev 2 here). Mod writes run after
  the F1 cheats in the same safepoint, so a mod write to the same word wins.

`mods/debug/openspm_debug.c` demonstrates it: every 600 ticks it logs
`OpenSPM Debug tick=<n> seq=<SEQ_WORK sequence>` (2 = in game). It only reads.

## Making the game do things: `openspm.guest_call` (new)

`include/openspm/guest_call.h`. A mod can ask the game to run one of its own
functions (integer/pointer arguments, up to 3), and reserve guest memory for
its own data:

- `call(context, target, argc, args)`, TICK_BEGIN only: queued; the backend
  hands one call per frame to the bridge right after the frame safepoint,
  which runs it as recompiled game code on a scratch CPU context (like an
  interrupt handler: every game thread is idle then). A call the module cannot
  enter is logged (`OpenSPM mod guest call ... failed`) and skipped; the game
  keeps running.
- `scratch(context, size, &address)`: zeroed guest memory above the game's
  arena and the IOS heap (`0x93F00000`, 1 MiB, no free).

The practical way in is SPM's own script system: build an EVT script with
`include/openspm/spm_evt.h`, write it into scratch memory, and call `evtEntry`
(`SPM_R8PE01_EVT_ENTRY`). The game then runs it like any of its scripts, with
every EVT user function available (spawn NPCs and items, move the camera,
play sounds, change maps). Addresses for R8PE01 rev 2 are in
`include/openspm/spm_r8pe01.h`.

## Input and on-screen text: `openspm.input`, `openspm.overlay` (new)

- `include/openspm/input_service.h`: this frame's Wii buttons, replacing them
  in TICK_BEGIN (macros, replays), and PC key state for mod hotkeys (only while
  an OpenSPM window has focus; F1/F8/F11/F12/Tab are taken).
- `include/openspm/overlay_service.h`: text panels (top right) and short
  notifications (bottom) drawn over the game from any thread. Version 2 adds
  `draw_boxes(layer, boxes, count)`: outlined rectangles in fractions of the
  game picture (they follow window size, aspect and bars); a layer keeps its
  boxes until set again, count 0 clears it. `include/openspm/spm_screen.h`
  turns game world positions into those fractions with the game's own camera
  matrices.

## Mod info files (new)

Next to `mods\<name>.dll`, an optional `mods\<name>.toml` says what the mod
is (`include/openspm/mod_manifest.h` has the format):

```toml
id = "mooshi.francis"           # must be the DLL's mod_id
name = "Francis"
version = "1.0.0"
author = "Mooshi"
description = "Play as Francis: big, slow, with a tongue attack."
requires = ["openspm.content>=1", "openspm.overlay>=2"]
```

OpenSPMNative refuses the mod, with the reason in the log (`Refused mod
<file>: ...`), when the file does not parse, its id is not the DLL's, or a
service in `requires` is missing at that version. F1 -> Mods shows the name,
version, author and description (or the file's problem). A DLL without the
file still loads (`Loaded mod (no info file)`). The samples ship theirs
(`mods/<sample>/openspm_<sample>.toml`, copied next to the DLLs). This is the
first step of the package format in MOD-PACKAGES.md.

## Save file state: `openspm.game_state` (new, read only)

`include/openspm/game_state.h`: questions about the save file being played,
so a mod can gate things on the player's progress ("unlocked once you beat
the game", "after chapter 3"):

- `story(&value)`: GSW(0), the story progress value the game's scripts
  advance; it only grows through a playthrough, so "progress >= N" is the
  usual test.
- `flag(n, &on)`: the game's saved flags GSWF(0..8191).
- `byte(n, &value)`: the saved byte variables GSW(1..2047).
- `pouch(&pouch)`: level, attack, HP, max HP, coins.
- `file_loaded()`: 1 while a save file is being played.

Every call answers OPENSPM_ERROR_RUNTIME on the title screen and file select
(no file loaded yet), and all of it is for TICK_BEGIN, like guest memory.
There are no writes: a mod keeps what it unlocks in its own state, never in
the game's save. Which story value means what (the end of a chapter, the
credits) comes from the game's scripts; OpenSPM does not name them yet, so
look them up (or log `story()` at the moment you care about) before relying
on one.

## Sample mod: Francis (`mods/francis`)

A second added character: Francis (tribe 318, model `e_kamere`) on Mario's
base, with his catch card's icon, `properties` for a big hitbox and slow
feet, his boss sounds as a voice, and a tongue on crouch / F5: a
set-pose script (A_1A / A_1B / A_1C) while the mod holds the buttons back,
and during the tongue-out frames one hit (the game's damage call) on the
nearest enemy in front within reach at mouth height. With F9 the reach is
drawn as an overlay box. F4 steps through his model's own animations (read
from the model's animation table at run time, skipping the names OpenSPM
adds) and shows each name: copy that function to find a new character's
animations.

## Sample mod: Flint Follower (`mods/flint_follower`)

Flint Cragley (model `n_stg5_hiroshi`, the chapter 5 explorer) follows the
player through the whole game. The whole mod is one `register_follower()`
call: OpenSPM does the spawning, walking, grounding and re-spawning
(docs/CONTENT-API.md "Followers").

## Sample mod: Sample Level (`mods/sample_level`)

A whole new level, `os1_01`: a meadow map built by
`tools/content/sample_level.py` (boxes and textures drawn in code, through
`tools/content/level_build.py`) into the mod's files folder
(`mods\openspm_sample_level\files\map\os1_01.bin`, made by the build), two
new enemies (`register_enemy`: the Brick Goomba on a Goomba's behaviour and
the Frost Koopa on a Koopa Troopa's, each with its own tint, size and
numbers), three NPCs with their own lines and tattles (`register_message`),
and the map itself (`register_map`) with an init script the mod writes in
EVT: collision groups, blocks, coins, two pipes back to
Flipside, the arrival, the NPCs, the enemies, the music. OpenSPM moves the
camera there (the map has no camera road). F3 travels there and back.
`tests/m14_sample_level_mod_test.c` decodes the script it builds;
docs/CUSTOM-LEVELS.md has the how and why.

## Sample mod: Hitbox View (`mods/hitbox_view`)

A debug toggle: F9 outlines Mario's hitbox (green), enemies' (red) and other
NPCs' (yellow) every frame, projected with Mario's camera. Read only; off
until F9. Other mods can follow the same key (the Francis sample shows his
tongue's reach).

## Sample mod: Shell Crouch (`mods/shell_crouch`)

Crouch (or press F6) and the Koopa Striker's shell (NPC template 129, a plain
kickable shell) drops in front of the player, spawned by the game's own
`evt_npc_entry_from_template` through an EVT script the mod writes. It checks
the template first and turns itself off on another game revision. The build
puts `openspm_shell_crouch.dll` into the `mods` folder next to
`OpenSPMNative.exe`; set `OpenSPM.ini [Mods] enabled = 1` to load it.
`tests/m14_shell_crouch_mod_test.c` runs it against fake services.

## Content service: added characters (`openspm.content`)

`include/openspm/content_service.h`. At STARTUP a mod calls
`register_character()` with a key (`"<mod>:<name>"`), name, description,
model (a game animation pose file such as `e_k_shooter`) and base character
(0 Mario, 1 Peach, 2 Bowser, 3 Luigi). OpenSPM assigns the roster id, lists the
character in the pause menu's character tab, switches to it (the game plays
the base character with the new model) and undoes everything when another
character is picked or the mod is not loaded. The mod asks
`character_active(id)` each frame to run the character's own ability. See
docs/CONTENT-API.md for how it works and its limits.

Sample: `mods/striker` adds the **Koopa Striker** (Mario underneath, the soccer
Koopa's model) who drops a kickable shell when he crouches.

## Sample mod: SFX Log (`mods/sfx_log`)

The first user of function hooks (`openspm.hooks`, docs/HOOKS.md). It hooks
`spsndSFXOn` with a pre callback that only reads: each sound effect name is
logged the first time the game plays it (`SFX Log: SFX_P_MARIO_JUMP1 ...`),
and once a minute how many sounds started. Needs the `spsndSFXOn` entry of
`state/openspm-hooks.json` enabled (off for now); without it the mod logs
that once and does nothing. The pattern: `find("spsndSFXOn")` at STARTUP,
`add_pre(id, fn, user)`, read the arguments with `openspm.guest_memory`
inside the callback.

## Sample mod: Glock (`mods/glock`)

A new partner, made entirely from the game's own parts. F7 calls Glock (a
Frackle, the little cannon Fracktail spawns): he floats behind Mario's head
like a Pixl. Press 1 and he moves in front of Mario (armed); every further
press of 1 fires a blast; crouch to put him back. While he is out the 1 button
is his (the mod removes it from the frame with `openspm.input`), except while
the world is frozen, when the game gets it back for its menus.

How it is built, as a pattern for other mods:

- **NPCs made the game's way.** One EVT script per NPC, in the order the
  game's own map scripts make a scripted flying NPC: `evt_npc_entry`, the
  animation table (`evt_npc_set_property 14`, pointing at the Frackle's and
  the Wrackle cannonball's own tribe tables), the decorative flags, the flip
  part, position and facing. Glock plus four blast NPCs are made once per map
  (only when the map has room left in its NPC table) and remade after a map
  change. Nothing is ever deleted mid-map: a finished blast is parked below
  the map, as game scripts do.
- **The mod moves its own NPCs.** Every frame it writes the position and
  facing of the NPCs it made (and nothing else of the game's).
- **Squirps' blast, borrowed.** Squirps' own shot exists only in space (the
  game allocates, moves and draws it only in space mode). The mod flies its
  own projectile, tests it against the enemies the game's own attack loop
  would test (the same NPC filters, plus "the NPC wears its tribe's model", so
  townsfolk are never hit), and asks the game to apply the hit exactly as the
  Squirps shot does: evt `0x80108034("mario", enemy, part, 0, 32, 1, 0, 0)`,
  which calls `npcTakeDamage(Mario, part, 32, ...)`. Defence, death, coins
  and score are the game's. Sound: the game's `SFX_MAP_TAMARA_SHOT1`.
- **Fail closed.** The first in-game frame checks every function entry it
  calls, both tribes' model names and the sound name; anything else and the
  mod turns itself off with a log line.

`tests/m14_glock_mod_test.c` plays it against a fake game (including an EVT
interpreter that fails on any call the real game would assert on).

This is a "partner" mod. A real Pixl in the Pixl menu (its own item id, menu
entry, save data) needs the content API (docs/CONTENT-API.md).

## Guest-memory writes: how the Cheats tab does it

The F1 Cheats tab predates the mod service and uses a small contract of its
own (`include/openspm/cheats.h`):

1. The renderer (F1 menu) owns the UI and `OpenSPM.ini [Cheats]`. It keeps one
   `openspm_cheat_table` (built-in cheat flags plus validated 32-bit writes)
   behind a mutex and exports `openspm_renderer_cheats(openspm_cheat_table *)`,
   which copies it out and returns 0 when every cheat is off. The renderer
   never writes guest memory.
2. The native backend finds that export with `GetModuleHandleA` /
   `GetProcAddress` (like the renderer gamepad export) and calls it at the
   `PRESENT_SAFEPOINT` host event, the once-per-retrace boundary where input is
   latched and Broadway is paused before the next guest frame. There
   `openspm_cheats_apply()` performs the writes, re-validating every address
   against MEM1/MEM2.

So writes never happen mid-frame or from the render thread. Mod writes
through `openspm.guest_memory` run at the same safepoint, right after these.

## Roadmap

1. **Tick/present events in OpenSPMNative**: done (`TICK_BEGIN` and
   `PRESENT_END` at the frame safepoint, above). A present event from the
   renderer's actual display flip (render thread) is not delivered.
2. **Game-state service** (`openspm.game`): raw guest memory at the tick is
   available (`openspm.guest_memory`, above). Next: versioned named accessors
   (sequence/stage, Mario position, like the F1 Debug tab's game state) so mods
   don't poke raw addresses that differ between game revisions.
3. **Function hooks**: the recompiler already routes selected guest addresses
   to host code (hosted direct entries). Expose a registration API so a mod
   can run before/after or replace a named game function, with the address
   table owned by OpenSPM per game revision. Version 2 is built
   (`openspm.hooks`: pre and post callbacks, integer and floating-point
   arguments and results, docs/HOOKS.md); no registry entry is enabled
   yet.
4. **Asset overrides**: redirect game file reads (DVD layer) to a mod's
   folder: textures, models, text, maps.
5. **Renderer hooks**: draw-owner identities from the display list (see
   PRESENTATION-ARCHITECTURE.md) give mods "this draw belongs to NPC X" for
   per-object effects and custom models.
6. **OpenSPM menu -> Mods tab**: listing and enable/disable (per-mod
   `[Mods] <file>.dll` toggles) work; per-mod settings are next.

Mods ship their own code and assets only, never Nintendo's.
