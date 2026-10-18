# Custom playable characters: findings and plan

> Status: implemented as the OpenSPM content service (docs/CONTENT-API.md,
> "Characters (version 1)"); the sample is mods/striker. This page keeps the
> original findings.

Goal (from testing): a fifth playable character with its own look (an NPC's
model) and a unique ability (drop a kickable Koopa shell), selectable in the
character menu, without crashing. This page records what the game code does
with characters (R8PE01 rev 2, verified in the DOL) and what that means.

## What limits the roster to four

- `marioSetCharacter(id)` (0x80146ED4, reached by `evt_mario_set_character`
  0x800EFA70) asserts `0 <= id < 4` first (assert at 0x80146F34, line 3391).
  A failed assert halts the game, so id 4 crashes at once.
- `_marioChangeCharacter` (0x80146F68) swaps the model: MarioWork keeps the
  current character's anim poses in `animPoseIds` (+0x24C) and loads the new
  ones from per-character data.
- `characterProperties[4]` (hitbox width/height, walk/dash speed, 0x14 each)
  is indexed by the character id.
- MarioPouchWork has exactly 4 character slots (`characters[4]` at +0xB4,
  item type per slot) immediately followed by the Pixl slots (+0xC4), and the
  whole struct is copied to and from the save file. A fifth slot would
  overlap the first Pixl and change the save format.
- Abilities (Peach's parasol, Bowser's fire, Luigi's super jump) are
  `switch`/compare on the character id inside Mario's C code.

## Why a mod cannot just "expand the table"

Code addresses of those tables and the `< 4` checks are compiled into the
recompiled native module as constants. Writing a bigger table somewhere in
guest memory changes nothing: the native code keeps reading the old one.
Expanding the roster means replacing the functions that use the character id,
which needs **function hooks**: extra entries in `state/m14-host-targets.json`
so the recompiler routes those guest functions to host code, plus a mod API to
implement them. That is roadmap item 3 in docs/MODDING.md and a rebuild of the
native module.

## Two ways forward

1. **Custom character in an existing slot** (much smaller): the game keeps
   using ids 0-3; a mod swaps one slot's model to an NPC's (the anim pose
   names `_marioChangeCharacter` loads), its properties and its ability, and
   renames it in the menu. No save-format change. Needs hooks on
   `_marioChangeCharacter` and the ability dispatch only.
2. **A real fifth slot**: hooks on `marioSetCharacter`, `_marioChangeCharacter`,
   every `characterProperties` accessor, the ability switches, the character
   select window and the pouch character functions, a relocated pouch
   character list (save compatibility shim), and menu layout for 5 icons.
   Many functions to find and replace, each one a crash risk until tested.

Both start with the hook system. What exists today (docs/MODDING.md):
guest memory, guest calls (the game runs its own functions / EVT scripts),
input and overlay. The Shell Crouch sample mod already spawns the kickable
Koopa Striker shell (NPC template 129) on crouch; it becomes the custom
character's ability once the character exists.
