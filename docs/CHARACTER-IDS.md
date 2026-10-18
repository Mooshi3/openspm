# Real character ids for added characters

Status: phases 1 and 2 built (patches, table moves, the roster on real ids);
waiting for the first in-game test. Phase 3 (removing the slot code) and 4
(the save) are open.

Today an added character plays on a game character's slot: the game thinks
Mario is playing, and OpenSPM swaps Mario's table rows (model, voice, size)
while the added character is out. This page is the plan that replaces that:
an added character gets its own id in MarioWork +0x38, its own rows in the
tables a mod fills, and the game's own transitions. Everything else in the
game treats the id as Mario (a "dummy second Mario"), by construction.

## What the game does with the id

`tools/content/character_reads.py` finds every read of MarioWork +0x38 by
following the MarioWork pointer through the code (from marioGetPtr
0x8012191C, the lis/addi of 0x804CEE58, function arguments, the move
functions in the move table, across calls and jump tables). R8PE01 rev 2
main.dol + relF: **402 reads**, and every `lbz rX, 0x38(rY)` + `extsb rX` in
the image (the shape of a signed character id) is one of them. The older
`character_audit.py` (CHARACTER-AUDIT.md) found 232: it did not follow
MarioWork into functions that receive it as an argument.

Following each read's value along every path (`value_uses`):

| kind | reads | what |
| --- | --- | --- |
| tests only for 1, 2 or 3, no sign extension | 141 | "is this Peach / Bowser / Luigi" |
| sign-extended, then switched / indexed / passed | 237 | 65 `switch` statements (compares with 0 and 4: an id above 3 takes the `default`), 100+ table lookups, ids passed to functions |
| index only tables a mod fills ("own" tables) | 16 | model, second model, 8-bit model, pipe models, size and speed, three voice tables, landing sound |
| range checks (`id - 1 <= n`) and a copy | 5 | safe with any id |
| own table plus a comparison | 3 | marioApplyCharacterProperties (0x801220C4) and its two getters |

Writes to the id: initialisation (0), a re-initialisation that keeps it
(0x80123084: reads it, clears MarioWork, writes it back; that read keeps the
real id), and `_marioChangeCharacter` (0x80146F68). Nothing else.

Other places a character id comes from: the change target MarioWork +0x1480
(set by the change request, read by move 71), the "previous character"
+0x1508 (kept around a temporary change and restored with 0x80146ED4), and
the pause menu (item id - 216).

## The design

1. **Ids**: added characters get ids 4, 8, 12, ... 124 (31 of them). The low
   two bits are 0: Mario.
2. **The dummy**: each of the 236 sign extensions (`extsb rA, rS`) becomes
   `rlwinm rA, rS, 0, 30, 31` (`rA = rS & 3`). One instruction each; for ids
   0-3 it is the same value, so the game is unchanged. For an added
   character every switch takes Mario's case, every per-character table
   reads Mario's row, every function given the id gets 0. The 141
   comparisons without a sign extension already answer "not Peach / Bowser /
   Luigi", which is Mario too.
   Exception: a read compared with another character id (`cmpw`, "is he
   already this one") keeps the real id. Four of them: the two change
   requests' "already this character" checks (0x80146E18, 0x80146F3C), the
   pause menu's current-entry mark (0x80195714) and a relF list that skips
   the current character. Masked, Francis read as Mario there: picking
   Mario did nothing, picking Francis again replayed the flip (seen in play).
3. **Own rows**: the 16 own-table reads keep the real id, and those tables
   move to OpenSPM memory with 128 rows (tools/content/table_refs.py
   recipes, like the item table): rows 0-3 the game's, the others a copy of
   the base character's row until a mod fills them (so a stray id never
   reads garbage). The second model keeps the base's row: with an added id
   the model loader takes the base's branch, and for Mario that branch looks
   up parts in his second model (`p_wii_mario_r`); an empty row there is a
   pose id of -1 and the game's `animPoseNum` assert (seen in play).
4. **The rest of the id's paths**:
   - marioApplyCharacterProperties switches on the id for the hitbox size
     with a `cmpwi r5, 4` bound (0x80122118); the bound becomes 127, so an
     added character takes Mario's branch.
   - Move 71 indexes the transition's file table by the change target
     (0x801473F0): the shift's mask is narrowed to the low two bits.
   - 0x801532DC (the character's screen box, for the flip) takes the id as
     an argument; its copy `mr r24, r3` becomes `rlwinm r24, r3, 0, 30, 31`,
     which covers every caller.
   - The change requests 0x80146DB0 / 0x80146ED4 assert `0 <= id < 4`; the
     bound becomes 128.
5. **Pause menu**: the character tab's pick computes the id from the item
   (item - 216, read from the menu's copy at pause work +44); OpenSPM's
   entries carry their own items, and once the menu has copied one, OpenSPM
   makes the copy 216 + the real id (phase 2). The entry itself keeps its
   item: writing 216 + id there made the menu draw item 220 / 224 (Tippi,
   Thudley) for him until it closed.
6. **Save**: the save keeps a game id (the base) and OpenSPM's sidecar the
   real one, so a save made as an added character loads as Mario without the
   mod (phase 4).

## Table relocation: what moves cleanly

`table_refs.py` follows each `lis` of a table through every path
(fallthrough, both sides of a condition, branch targets, jump tables) and
every pointer derived from it (`addi`, `add` with an index, `mr`, update
loads). A `lis` is patched only when an address formed directly from it
lands in the table and nothing derived from it lands outside. Of the 25
per-character tables in CHARACTER-TABLES.md, 21 move cleanly. Four share a
base register with neighbouring data (the model table, the landing sound,
0x80330C60, 0x803E8988); the last two are not own tables (they read
Mario's row through step 2), the model table and the landing sound need
their neighbours moved with them (in progress).

## What a player sees

- Picking an added character from any character plays the game's own flip
  (the id differs from the current one, so the game's change request runs).
- In play he is himself to the game (MarioWork +0x38 holds his id) with his
  model, voice, size and speed; everything else (moves, abilities, Pixls,
  camera, 8-bit form, pipe animation) is Mario's.
- Scripts asking "who is playing" (`evt_mario_get_character`) hear Mario. A
  cutscene that saves the character and restores it later would bring him
  back as Mario (not seen yet; to be checked in play).
- `OPENSPM_CHARACTER_SLOTS=1` switches back to the old slot mode (and a
  module built before these patches uses it on its own).

## Phases

1. Patches of steps 2 and 4, relocation of the own tables, rows filled from
   the character definitions. With only game characters registered nothing
   changes (ids 0-3 read the same values). **Testable: vanilla play must be
   identical.**
2. Pause menu: picking an added character asks for a real switch to its id
   (the game's flip runs because the id differs from the current one).
3. Remove the row swapping from the roster.
4. Save sidecar.
