# OpenSPM content API: design

Goal: mods add characters, Pixls, items, maps, cutscenes and abilities, and
override hardcoded game behaviour, without knowing or fighting over the
game's internal ids. A mod says "I add a Pixl called Glock with this model and
this ability"; OpenSPM assigns the id, validates the definition, installs it
while the mod is enabled and removes it when the mod is disabled. That is the
difference from REL modding, where every mod patches the game directly and
two mods that pick the same id break each other.

Everything below is R8PE01 rev 2 (US) and was checked in the DOL. No game
code or data is stored in the repository: patches are recipes applied to the
player's own files at build/boot time.

## Three OpenSPM-owned mechanisms

Mods never touch these directly; OpenSPM's content systems use them.

1. **Table expansion (build time + boot).** Fixed-size game tables are moved
   to a larger OpenSPM-owned copy in MEM2 above the IOS heap. At build time
   OpenSPM rewrites the address constants of every instruction that references
   the table (in the player-local codegen DOL, before DolRecomp, and in the
   boot image in guest RAM so interpreter fallbacks agree); at boot it copies
   the original entries into the new table. Extra entries belong to OpenSPM,
   which hands them to mods. One rebuild when a table is first expanded; none
   per mod.

   `itemDataTable` (538 entries x 0x2C at 0x803B7E78) is referenced from 86
   places, all the same two-instruction form `lis rX, 0x803B` +
   `addi rY, rX, 0x7E78`: mechanically patchable.

2. **Function hooks (build time, runtime dispatch).** Game functions whose
   logic has to learn about new content get an OpenSPM host target (the
   recompiler turns each call into a host exit, as it already does for disc,
   save and GX services). OpenSPM's handler either handles the call itself
   (for new content: a custom Pixl id) or continues into the game's own
   original function body (for everything else), so the game's behaviour for
   its own content is unchanged. With no mod content installed every hook just
   continues.

   Mechanism: the backend's hostcall handler returns with
   `pc = function entry` and the "continue original" marker; the bridge
   bypasses the host routing for that one dispatch so the recompiled body runs,
   returning to the original caller. Post-hooks return through an OpenSPM
   return address.

3. **Guest calls and scripts (runtime, exists).** `openspm.guest_call` runs a
   game function / EVT script between frames (docs/MODDING.md). Abilities and
   spawns are built on it.

## Content registry service: `openspm.content`

Mods call it at STARTUP; OpenSPM validates and assigns ids, installs content
when a save/game is running, and uninstalls it (restores the tables, removes
the item from the pouch, falls back to a vanilla Pixl) when the mod is
disabled. Saves store OpenSPM's stable content keys ("glockmod:glock") next to
the save, so a missing mod never leaves a dangling id in the game's own data.

Planned kinds, in this order:

| Kind | Needs | Status |
| --- | --- | --- |
| Pixl | item table expansion; hooks on the 6 functions with Pixl range checks; ability dispatch | first target (Glock) |
| Item | item table expansion; use-script table | after Pixl |
| Character | build patch (character list 64 entries), roster in the backend | **version 1 done** (below) |
| NPC template / enemy | npcEnemyTemplates expansion | later |
| Map / cutscene | file overrides (asset layer) + EVT | later |

## First target: the Pixl "Glock"

A new Pixl that shoots Squirps' blasts.

Facts found so far:
- Pixl item ids are 220..231 (12 Pixls). The pouch has 16 Pixl slots
  (MarioPouchWork +0xC4) but none is free: from REL-mod testing, the four
  that look empty are used (two crash the game when filled, two belong to
  Tippi-like entries). Glock gets an id at 538+ in the expanded item table.
- No fixed-size table is extended in place. Tables become dynamic:
  global tables (items, NPC templates, fairyUseFuncs) are relocated to an
  OpenSPM-owned copy sized for the installed content; arrays inside a game
  struct (the pouch's Pixl slots, saved with the file) stay exactly as the
  game has them, and custom entries live in an OpenSPM side table (saved in
  OpenSPM's own save sidecar) that the hooked pouch/menu functions merge in.
- Functions with hardcoded Pixl range checks (220 <= id < 232), to hook:
  pouchAddItem (0x8014CF5C), pouchCheckHaveItem (0x8014D558) and four more
  (0x8007A5E0, 0x80130B70, 0x80153FF8, 0x801F5784; being identified).
- Pixl abilities are dispatched by `marioFairyUse` through `fairyUseFuncs`
  (13 entries of { itemId, useFunc }).
- A Pixl's model comes from its item entry (`animPoseName`, `animName`); its
  name and description are message keys (`nameMsg`, `descMsg`).

Status: Glock exists now as a runtime mod (`mods/glock`, docs/MODDING.md):
the partner, the armed/unarmed behaviour, the blasts and their Squirps-type
damage all run without any table change. What still needs the content API is
the Pixl-menu side: an item id, a menu entry and the save data.

Steps for that: (1) hook engine, (2) item table expansion, (3) Pixl hooks + registry,
(4) Glock mod: registered Pixl, Squirps-shot ability through an EVT script,
added to the pouch by the mod. Each step lands behind an off switch and gets
its own in-game test.

## Characters (version 1, implemented)

`openspm.content` `register_character()` (include/openspm/content_service.h),
provider `include/openspm/content_roster.h`, run by the backend inside the mod
tick. A mod gives a key, a name, a description, a game model (animation pose
file) and a base character (0-3); OpenSPM assigns roster ids 4, 5, ...

How it works without the game ever seeing an id above 3:

- **Build patch** (state/openspm-code-patches.json, applied to the codegen DOL
  and the boot image): the character tab's list is allocated for 64 entries
  instead of 5. The game's shared list code scrolls any count 8 rows at a
  time. Without the rebuilt module the list still holds 5: one added character.
- **Menu entries**: after the game fills its (at most 4) entries, OpenSPM adds
  one per registered character whose base character is unlocked, and grows the
  tab's window to fit them (the game sizes it once, at open, for its own rows:
  25 per row + 50, at most 8 rows). With the rebuilt module each character has
  his own item (after the added Pixls' items, a copy of the base character's
  item): his name, description and icon (`icon_item`, a game item's icon; 0 =
  the base character's). Without it they share placeholder item 232 ("ICON",
  unused), whose name/description follow the cursor.
- **Animations**: the game asks the player model for
  `<prefix><pose><R when seen from behind>` (0x80127400): `mario_` /
  `Big_mario_` for Mario, `luigi_` / `Big_luigi_` for Luigi, nothing for Peach
  and Bowser; an NPC model has none of those names, so it showed its first
  animation only. Once per model load OpenSPM gives the character's model a
  copy of its animation table with those names added (the base character's
  poses from marioChgPose's call sites, and the model's own), each playing the
  model's animation of the same name, the one `anim_map` names
  ("J_1B=J_1_a,..."), or S_1.
- **Voice**: `voice` ("damage=<sound>,acro=<sound>,attack=<sound>", a sound
  or "none" per kind) replaces the base character's row in the player voice
  tables (hurt 0x803E82E0, stylish 0x803E8588, attack 0x803EA038) while he is
  played, restored with the model tables; checked at build (every row must
  read SFX_P_V_<who>_<kind><n>), otherwise the base's voice stays.
- **Size and speed**: `properties` ("width=<n>,height=<n>,walk=<n>,dash=<n>",
  any subset; width 4..80, height 8..100, walk 0.2..6, dash 0.2..10)
  replaces those fields of the base character's row of `characterProperties`
  (0x803E8228, 20 bytes per character: hitbox width, height, walk, dash, one
  unidentified) while he is played, restored with the model tables. The game
  copies the row into MarioWork when it switches to the base character
  (0x801220C4, called from `_marioChangeCharacter`), which happens after
  OpenSPM swaps it. Checked at build (every row must read as plausible
  numbers), otherwise the base's size and speed stay.
- **Flags**: `OPENSPM_CHARACTER_NO_FLIP`: the character can not flip to 3D
  (OpenSPM keeps the flip button, A, from the game while he is played and the
  world runs; menus keep it).
- **Open menu**: a tab is open while its pausewin window exists with the tab's
  own init callback (entry +100). chartab +164 is not an open flag: the close
  routine sets it to 3 and each tab's delete counts it down.
- **Map changes** (seq 3) keep the picked character and Pixl.
- **Messages**: OpenSPM builds a sorted message table (the game's own format)
  and installs it into a free message-table slot of msgdrv; msgSearch finds
  OpenSPM's keys like the game's. Map loads zero those slots; it is re-installed.
- **Picking**: button 2 on an added entry is kept from the game; the base
  character's model table entries (0x803E8148 / preload 0x803EB238) point at
  the character's model, the cursor moves to the base character, and the game
  gets button 2 on the next frame: it performs its own switch (and preload).
  If the base character is already playing, the game just closes the menu
  (its switch request, 0x80146DB0, refuses the current character). OpenSPM
  then asks for what that request does for another character: MarioWork
  +0x1480 = the base, `marioChgMot(71, 1)` from a guest call, and on the
  frame move 71 runs, +0 |= 0x10000000 (the game sets it after the move;
  `marioChgMot` refuses every change while it is set). Move 71 is the
  game's flip transition; at the flip it calls `_marioChangeCharacter`,
  which reloads the model on the game's thread. Outside plain play (another
  transition, Pixl moves, special states), or if move 71 has not started
  after 10 frames, OpenSPM reloads the model without the flip from an EVT
  script (`evt_mario_set_character` away and back: model loads are
  synchronous and never run from a guest call). Picking the game's own
  entry back while the added character plays works the same way.
- **Leaving**: picking a game character, any character change by the game
  (story scripts), leaving the game, or not loading the mod: all tables and
  the placeholder item go back; nothing is written to the save.

Not yet: per-character properties (hitbox, speeds), save of the chosen character.
Those need the item table and character-property expansion (same patch
mechanism: the 86 item-table references mostly share their `lis` with other
tables on the same page, so relocation has to be done per function).

## Item table relocation (implemented, groundwork for added items and Pixls)

`itemDataTable` (538 entries at 0x803B7E78) is followed directly by other
data, so it can not grow in place. Every one of its 86 code references is
`lis rX, 0x803B` + `addi rY, rX, 0x7E78` where the `lis` register serves
nothing else, and no data word points into the table. The build therefore
rewrites just those 86 `lis` immediates to `0x93E0` (recipes item-data-NN in
state/openspm-code-patches.json; the 10 in relF only in the recompiled module,
since the game's REL loader writes relF at runtime) and the backend copies the
538 entries to 0x93E07E78 (MEM2, above the game's arena) at boot. The game
keeps writing shop prices into the table through the same references, so the
copy stays the live one; the original is left untouched. Added items get ids
538+ right after the copy. The eleven `id < 538` checks in the game are all
"is a catch card (282..537)" tests, which added items correctly fail; the
item-by-name search stops at 537 (added items are not found by name).

The same patch set grows the Pixl tab list from 17 to 64 entries.

## Pixls (implemented, needs the rebuilt module)

`register_pixl()` (content_service.h): key, name, description, model (the
pose shown in the Pixl menu), menu animation, icon (a game item's). OpenSPM
gives the Pixl its own item id (538+) and writes its entry into the relocated
item table (copied from a real Pixl entry, then its own name/description
through OpenSPM's message table, model, icon), so the Pixl menu shows it the
game's own way.

The game's Pixl code is closed to new ids: `marioFairyUse` (0x80130B70)
asserts `220 <= id < 232` and dispatches through a 13-entry table, and the
follower setup (0x8011CF60) and pouch functions only know the pouch's 16
slots, which are saved. So an added Pixl never enters the pouch:

- Menu: OpenSPM appends one entry per added Pixl to the Pixl tab list (64
  entries after the build patch) and grows the tab's window to fit; the entry
  points at an OpenSPM-owned slot.
- Picking it: button 2 is kept from the game (its own confirm would write the
  pouch); OpenSPM marks its slot, plays the game's pick sound and sets the
  chartab result "a Pixl was picked" (+0 |= 0x2000), so the menu closes as for
  a game Pixl (on close the game sees its pouch selection unchanged: no Pixl
  change motion). Once play resumes, OpenSPM unselects the game's Pixl in the
  pouch and asks for the game's Pixl flip, 0x8014777C(old item, new item)
  (move 72, as the menu does for a game Pixl), which rebuilds the followers
  with the call `evt_fairy_reset` makes: no game Pixl follows, `marioFairyUse`
  finds none and returns without its assert. Both items go to a size lookup
  that panics outside the game's Pixls (0x80153FF8, items 220..231), so the
  new item is a game Pixl standing in (221, the one added Pixl items are
  copied from) and the old one is the game Pixl whose follower is out, or 0
  (0x8011E2E0's result is used unchecked). Outside plain play OpenSPM runs
  `evt_fairy_reset` (0x800E8638) instead, without the flip; re-picking the
  Pixl that is already out does nothing. The mod sees `pixl_active()` and runs
  the Pixl's follower and ability (mods/glock).
- Picking a game Pixl: the game's own confirm clears every listed slot (ours
  too) and switches as usual from "none". Leaving the game clears ours.
- Saves only ever contain game Pixls (saved while an added Pixl is out: no Pixl
  selected; pick one in the menu).

Not yet: the Pixl's own menu icon (it borrows a game item's), a shared
follower in OpenSPM (the mod draws its own), remembering the pick across a
reload (save sidecar).

## Followers (implemented, first in-game test pending)

A follower is an NPC who walks behind the player through the whole game:
doors, pipes, story scenes (the way a party member trails along, or Flavio
in TTYD's famous sequence break). `register_follower()` at STARTUP with an
`openspm_follower_def` (include/openspm/content_service.h):

- `model`: any game model (animation pose file), loaded by name the way map
  scripts load their NPCs; `stand_anim` / `walk_anim` / `run_anim` name its
  animations (default `S_1` / `W_1` / `R_1`). The first time he appears the
  log lists the model's animations.
- `distance`: how far behind the player he walks, along the player's path
  (0 = 45 units; a second follower walks twice as far back, and so on).
- `OPENSPM_FOLLOWER_FLOATING`: rise and fall with the player (a fairy);
  without it he keeps to the ground and eases up and down to new heights.
- `OPENSPM_FOLLOWER_START_HIDDEN` + `set_follower(id, 1)`: he joins later.

What OpenSPM does (include/openspm/content_followers.h): makes him as a
decorative game NPC (`evt_npc_entry` with the model, an animation table
`{0 stand, 1 walk, 2 run}`, the decorative flags, the flip part), so the game
draws him with depth, light and shadow in 2D and 3D; keeps a trail of the
player's ground positions and walks him to his place on it (walking pace,
running when he falls 40 units behind, standing after a few still frames),
facing where he walks; makes him again when a door, pipe or cutscene clears
him away (after a short wait, and only while the map leaves NPC slots free);
places him at once after a warp. A scene the player is not in (a script hides
Mario: MarioWork flag8 0x1, for more than 10 frames) sends him away too; he
comes back behind the player when the player does. Scenes the player is in
keep him, like the TTYD Flavio glitch. Up to 8 followers.

Not yet: talking to him, his own animations in scripted scenes (he keeps
standing or walking), models the game does not have (the mod model files
step, then a TTYD model converter).


## Maps (implemented, first in-game test pending)

A mod adds a new map: its files in its files folder, its record in the
game's map registry. Nothing of the game's is replaced (docs/CUSTOM-LEVELS.md).

```c
openspm_map_def map = {0};
map.header.struct_size = sizeof map;
map.key = "openspm.sample.level:start";
map.name = "os1_01";          /* the area is the first three letters: os1 */
map.file = NULL;              /* map/os1_01.bin in the mod's files folder */
map.fallback_door = NULL;     /* entered with no door name: the spawn below */
map.spawn[0] = -400.0f; map.spawn[1] = 0.0f;
map.init_script = script;     /* an EVT script the mod built in guest memory */
content->register_map(content->context, &map, &slot);   /* from a mod tick */
...
content->go_to_map(content->context, "os1_01", NULL);   /* the game's map change */
```

- **Files**: `mods\<mod dll name>\files\map\<file>.bin` (see "Mod files"
  in docs/CUSTOM-LEVELS.md), built with `tools/content/map_build.py`;
  optionally `setup\<name>.dat` for placed enemies.
- **The registry**: the map registry patches
  (`tools/content/map_registry.py`) move the game's 468 map pointers to
  MEM2, followed by 64 spare slots. A registration writes a MapData record
  into one; the game's own lookups then find it. A name the game already
  has is refused.
- **The init script** runs when the map loads, like a game map's: doors
  and pipes, blocks, items, NPCs, enemies, music.
- **Its file must be there**: `register_map` reads the game's file table
  and refuses (`OPENSPM_ERROR_NOT_FOUND`, logged) a map whose
  `map/<file>.bin` (or `map/<file>/map.dat`) is missing, since loading it
  would stop the game.
- **The camera**: a map without a camera road (`camera_road.bin`, the
  game's per-map camera paths, a format nobody has worked out yet) leaves
  the game's camera where it was. Set `camera_distance` (with
  `camera_height`, `camera_look_height` and an x range for the point it
  looks at) and OpenSPM moves the camera after the player on that map:
  side on in 2D, from the player's left in 3D, swinging between the two
  on a flip. The sample level uses 700 / 70 / 60.
- `register_map` and `go_to_map` work from a mod tick (guest memory open).
  A module built before the registry patches answers
  `OPENSPM_ERROR_UNSUPPORTED`; the next launch rebuilds it.

## Messages (implemented, first in-game test pending)

`register_message(context, key, text)`: a mod's own message, found by the
game's message search like its own (an NPC's `evt_msg_print`, an item name,
a door sign). OpenSPM keeps every registered message in one sorted table in
a free slot of the game's message work, put back after a map load reuses
the slot. The game's tables are searched first: a key the game has keeps the
game's text, so mods add messages, never replace them. Prefix keys with
something of the mod's own (`os1_mayor_01`). Texts use the game's message
tags (`<p>` next page, `<k>` wait for a button).

## Enemies (implemented, first in-game test pending)

`register_enemy(context, def, &template)` (from a mod tick): a new enemy
that behaves like a game template and looks like a game tribe, with its own
numbers. It gets the next spare template number (435 and up), which a map's
init script spawns it with (`evt_npc_entry_from_template`) or a setup file
places. Nothing of the game's is replaced: the enemy table patches
(`tools/content/enemy_tables.py`) move the 535 tribes and 435 templates to
MEM2, each followed by 64 spare rows, and a registration fills one of each.

```c
openspm_enemy_def e = {0};
e.header.struct_size = sizeof e;
e.key = "openspm.sample.level:brick_goomba";
e.base_template = 2;          /* walks, turns and attacks like a Goomba */
e.look_tribe = -1;            /* the base template's tribe: the Goomba model */
e.score = -1;                 /* the look tribe's points */
e.max_hp = 3;                 /* 0 keeps the look tribe's */
e.attack = 2;                 /* what the tattle shows */
e.contact_damage = 2;         /* what touching it costs */
e.color[0] = 255; e.color[1] = 120; e.color[2] = 80; e.color[3] = 255;
e.scale = 1.4f;
content->register_enemy(content->context, &e, &template_no);
```

- **Look**: `look_tribe` gives the model, animations, parts (hitboxes,
  defenses) and drops; `model` swaps in another game model that has the
  same animations (`e_kuribo_y` for a Goomba). A tint, a size or a contact
  damage gets a spawn script of OpenSPM's: the base's (or `spawn_script`,
  the mod's own) runs first, then `evt_npc_set_color`,
  `evt_npc_set_scale`, `evt_npc_set_part_attack_power` on "me".
- **Catch card**: 0 (none) by default: the damage bonus from cards skips it
  (the game checks for 0) and the tattle falls back to the game's
  "unknown" text. `OPENSPM_ENEMY_CARD_LOOK` shares the look tribe's card:
  the game's tattle text for that enemy, with this one's HP and attack.
- **Limits**: a base template whose scripts check their own template or
  tribe number (bosses, story enemies) may act differently under the new
  number; plain enemies (Goombas, Koopas, Squigs) do not. 64 enemies in
  all mods together.
