# Custom levels: new maps, NPCs and enemies

Status: built, first in-game test pending (docs/TEST-LIST.md "00b"). Done:
the map file builder and a level builder on top, a test machine that runs
the game's own loader on their output, mod files (new paths in the game's
file table), the map registry, messages, enemies, a camera for maps without
a camera road, and the sample level (mods/sample_level).

Goal: a mod adds a new playable level (its own map, its own NPCs, its own
enemies) without replacing or changing anything in the game. Everything here
is an addition: new file paths, new map registry rows, new message keys, new
enemy rows.

## How the game does a map (R8PE01 rev 2)

**The registry.** `mapData` (0x803C5098) holds 468 pointers to `MapData`
records: name, file name, a fallback door name, a fallback spawn position
and the init script (linked by relF's prolog, which calls `mapDataPtr(name)`
for each map and stores the script). `mapDataPtr` (0x80029364) and
`mapDataEvtPtr` (0x8002942C) are its only readers: each counts the non-NULL
pointers, asserts the count is exactly 468, then compares names.

**A map change** (`seq_mapChangeMain` 0x80179C38):

1. loads the area's message files (a table at 0x803EA1B0 of message file
   names and the areas that use them; an area in none of them loads none);
2. `mapLoad(file)` (0x800816E8), stage by stage, one per frame;
3. the camera road and backgrounds (from the map archive);
4. `setup/<map>.dat`, the placed enemies (optional: a missing file means
   none);
5. starts the map's init script.

**Map files.** `mapLoad` first looks for `map/<file>.bin`: an LZ-compressed
(Nintendo LZ77, type 0x10) U8 archive with `./dvd/map/<file>/map.dat`,
optional `./dvd/map/<file>/texture.tpl`, `camera_road.bin` and up to 18
backgrounds `./dvd/bg/<file>_NN.tpl`. Without the archive it loads loose
`map/<file>/map.dat` and `map/<file>/texture.tpl` (no camera road or
backgrounds on that path).

**`map.dat`** is the Thousand-Year Door "DMD" format, version "ver1.02".

- A 0x20-byte header: file size (must equal the loaded size), data size,
  pointer-field count, chunk count, a count of 8-byte entries (0).
- The data, then the table of pointer fields (offsets into the data; the
  game adds the data's address to each), the chunk table (offset, name
  offset) and the chunk names.
- Chunks: `information`, `texture_table`, `light_table`, `fog_table`,
  `material_name_table`, `vcd_table`, `animation_table`, `curve_table`.
- `information`: version, root joint, the name of the drawn subtree, the
  name of the collision subtree (plain names, or "|" paths), a date string.
- Joints (0x60 bytes + 8 per mesh): name, type, parent / child / next /
  previous, scale, rotation, translation, bounds, a draw mode (+1 cull
  index: 0 front, 1 back, 2 all, 3 none; +8 collision attributes), then
  (material, mesh) pairs.
- A mesh: +3 = 1 (polygons are display lists), +4 polygon count, +8 element
  mask, +0xC the vcd table, then (offset, size) per polygon.
- A polygon is a GX display list called as is: triangle strips (0x98,
  vertex format 0) of 16-bit indices, in GX attribute order.
- The draw code always sets positions (s16, the shift at vcd +0x44) and
  normals (s8, 6 fraction bits); colours (RGBA8) and texture coordinates
  (s16, shifts at vcd +0x48..) follow the element mask.
- Materials are 0x114 bytes: colour, colour source, blend mode, texture
  count, samplers, texture coordinate transforms, a TEV config.

**Scale.** The game draws and collides every map with a uniform scale of 10
on the root (`mapLoad`: `PSMTXScale` with 10.0 from r2-28876): positions in
the file are world units / 10.

**Collision** comes from the collision subtree's meshes (positions only).
`_hitEnt` turns each triangle into an 84-byte record (three vertices in
world units, three edges, a normal). The ray test (`hitCheckFilter`
0x8006ECB4) walks this frame's list of hit objects (built by 0x8006F598
from the active map entry's HitObj array). Triangles wound
counter-clockwise seen from outside (normal by the right-hand rule) are
what it expects: floors hit from above with an upward normal, sides and
bottoms from outside.

**A level's init script** (e.g. the first level of chapter 1) sets
collision groups for 2D and 3D, places `?` blocks (`evt_mobj_blk`), items
(`evt_item_entry`), a save block, the doors and pipes
(`evt_door_set_map_door_descs`, `evt_door_set_dokan_descs`,
`door_init_evt`), reads the setup file (`evt_read_setup_file`), and starts
the music and the environment.

Doors and pipes work through hit objects in the map: a door descriptor
names the 2D and 3D hit objects that are its loading zone, and the
destination map and door. A map entered with no door name uses its
fallback door, else its fallback spawn position.

**Enemies** are NPCs made from a template (`npcEnemyTemplates` 0x8040B128:
tribe id, AI scripts, flags), whose tribe (`npcTribes` 0x803FD7D0, 535 rows
of 0x68: model, animations, HP, parts with defenses and hitboxes, drops,
score) gives the model and stats.

## What OpenSPM adds

- **Mod files** (built): `mods\<mod dll name>\files\...` beside the
  mod's DLL. The launcher lists each enabled mod's folder in
  `OPENSPM_MOD_FILE_ROOTS`. At boot the backend copies the game's file
  table (FST) to OpenSPM's MEM2 (0x93EB0000) with every file of those
  folders added as a new entry, and points the boot info at it
  (`__DVDFSInit` 0x80279830 takes the pointer at 0x80000038 as is).
  - Every original entry keeps its position and length.
  - New files get positions past any disc (from 0x3C000000), and reads of
    them open the mod's file.
  - A path the game already has (in any case), or that another mod added
    first, is refused and logged.
  - `include/openspm/fst_overlay.h`, `tests/m14_fst_overlay_test.c`; the
    game's own path lookup resolves the added paths in the harness.
- **Map registry** (built): `mapData` moves to MEM2 (0x93E55098) with 64
  spare slots, each starting as a copy of slot 0. That is a real record, so
  the count holds, and a lookup still finds the game's own first. The four
  468 bounds become 532 (`tools/content/map_registry.py`).
  `register_map` writes a record into a spare slot
  (`include/openspm/content_maps.h`, `tests/m14_content_maps_test.c`). In
  the harness, the game's own `mapDataPtr` and `mapDataEvtPtr` on the moved
  registry find the game's maps, return NULL for unknown names, and find a
  registered map and its init script.
- **Messages** (built): mods register keys and texts; OpenSPM serves them
  through a message table of its own in a free slot of the game's message
  work (`include/openspm/content_messages.h`).
- **Enemies** (built): the tribe and template tables move to MEM2
  (templates 0x93E6B128, tribes 0x93E9D7D0), each with 64 spare rows that
  start as copies of row 0; 29 + 5 code references and the template bound
  (435, npcdrv.c) are patched, the one relF reference (a loop over the
  game's 435 templates) at build time (`tools/content/enemy_tables.py`).
  Data words naming particular game rows keep reading the unchanged MEM1
  copy. A new enemy is a new tribe (the look tribe with its own model and
  numbers) with a new template (the base template naming it)
  (`include/openspm/content_enemies.h`, `tests/m14_content_enemies_test.c`).
- **Tattles**: Tippi's tattle first looks for a message
  `<map>_<NPC name>` (`<map>_<NPC name>_ex` late in the story), then the
  catch card's `anna_<card description key>`, filled in with the tribe's HP
  and attack. A level's named NPCs get their own tattles from messages
  named that way.
- **The map builder** (`tools/content/map_build.py`, the formats) and the
  **level builder** (`tools/content/level_build.py`): solid boxes (a top
  material and a side material), scenery, a backdrop, warp pipes, invisible
  zones, and textures drawn in code, to `map/<file>.bin`; nothing from the
  game goes in. A pipe's rim top becomes two named collision objects,
  `A2D_<name>` under the group `A2D` and `A3D_<name>` under `A3D`, which is
  what a pipe descriptor names and what the game's maps do (the init script
  makes the groups 2D-only and 3D-only).
- **The camera** (built): the game's main camera follows a map's camera
  road (`camera_road.bin`) in mode 1; the road is a mesh-based format
  (`cam_road.c`, about 20 KB of code, not decompiled by anyone yet: zones
  as triangle lists, each with its camera settings). A map without one
  leaves the camera in mode 0, where it holds still. A registered map with
  a camera distance gets OpenSPM's camera instead (content_maps.h): side
  on in 2D, from the player's left in 3D (the camera's own 3D flag at
  +10), easing on a flip. The player's 2D movement does not need the road
  (2D is along x; "panes" are z rows).

## The sample level (`mods/sample_level`)

`os1_01`, a meadow (`tools/content/sample_level.py` lays it out; the build
writes it into the mod's files folder). Its init script, written by the mod
in EVT, follows the game's first level of chapter 1:

1. `evt_hitobj_attr_onoff(1, 1, "A2D", 0x40000000)` and the same for
   `"A3D"` with `0x20000000`: the pipe tops' collision groups;
2. blocks (`evt_mobj_blk`: type 0 brick, 1 "?" block; the item inside) and
   coins (`evt_item_entry`). No save block (`evt_mobj_save_blk` would
   work): a save made in a mod's map names a map the game only has while
   the mod is on;
3. the pipes (`evt_door_set_dokan_descs`, two DokanDesc: name, this map,
   the 2D / 3D hit names, Flipside's main square `mac_02` and its pipe
   `dokan_2`), then `door_init_evt` (the player comes out of the pipe the
   map was entered by);
4. three NPCs the way Flipside places its townsfolk (`evt_npc_entry`, the
   animation table property 14 with stand 0 and talk 3, the talk script
   property 9, flag8 0x0C400004, animflag 0x20, a flip part, move mode 1, a
   position); each talk script prints the NPC's own message;
5. the new enemies by template number (`evt_npc_entry_from_template`);
6. the music (`evt_snd_bgmon(0, "BGM_MAP_STG1")`).

Tippi's tattle of a named NPC looks for the message `<map>_<NPC name>`
first, so the NPCs' tattles are three more messages. An enemy spawned by
`evt_npc_entry_from_template` with instance N is named `npc_%08x` of
0x10000000 + N (npcEntryFromSetupEnemy names it so when its template has
no name), so each placed enemy gets a known name and its own tattle the
same way (the new enemies have no catch card for the card tattle).

Entering: F3 asks for the game's map change to `os1_01` by `dokan_1`
(content `go_to_map`); both pipes, or F3 again, go back.

## Testing note: paired singles in the harness

Gekko's ordinary FP instructions either fill both halves of an FPR (`lfs`,
single arithmetic, `frsp`) or set ps0 and keep ps1 (`lfd`, `fmr`, double
arithmetic). The harness first noticed ordinary writes only when ps0
changed, so an `lfs` of the value already there kept a stale ps1: the
game's `PSMTXTrans` (an `lfs f0` of 0.0, then `psq_st f0`) then built a
broken matrix for every second collision object of a level. It now hooks
the ordinary writes a paired-single read can see (found by a scan of the
text sections) and keeps ps1 through an epilogue's `psq_l` + `lfd`.

## Testing without the game: `tools/content/game_harness.py`

A small PowerPC machine (Unicorn) running the player's codegen image at its
real addresses. It covers:

- the Gekko paired-single instructions and GQR moves, emulated;
- the write-gather pipe captured into display lists;
- asserts and OSPanic raising with the game's own message;
- the game's file calls answered from memory.

`tools/content/test_map_build.py` runs the game's LZ decompressor, ARC
library, `mapLoad` and the collision ray test on the builder's output (set
`OPENSPM_CODEGEN_DOL` to the image and `pip install unicorn`).
`tools/content/test_level_build.py` does the same for the sample level and
looks up its pipes' collision objects by name with the game's
`hitNameToPtr`. `tools/content/level_preview.py OUT.png` draws a level from
cameras like the game's (no game needed).

The message tags the sample's texts use are the game's own (the message
parser's tag names include `k`, `p`, `o`, `select`, `col`, `wait`).

The TTYD format notes in PistonMiner's ttyd-tools (GPL-3.0) helped identify
the structures. OpenSPM's writer is its own code, and every field it writes
was checked against the SPM loader.
