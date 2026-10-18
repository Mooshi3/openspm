# Per-character tables: what a real character id 4+ would touch

> Superseded: the plan for real ids, with a complete list of the game's reads
> of the id (402, not the 232 below), is docs/CHARACTER-IDS.md. This page's
> table survey still holds.

Survey of every place the game indexes something by the current character
(MarioWork +0x38), from the 51 "index" rows of CHARACTER-AUDIT.md (R8PE01
rev 2 main.dol; the relF rows are the same code paths through the map
library). The question: can a new id (4+) just get one more row everywhere,
or does it need hooks? The answer changes the plan for characters v2.

## What is indexed

**A. Static tables with a string per character** (a new id reads the next
table's data: a wrong or NULL pointer, a crash or a wrong model):

| table | row | read by | holds |
| --- | --- | --- | --- |
| 0x803E8148 | 8 | 0x80121CE0, 0x80122C5C | the model: `p_wii_*` / `p_big_*` (Mega Star) |
| 0x803E8168 | 8 | 0x80122C5C | Mario's second model (`p_wii_mario_r`), NULL for the others |
| 0x803EB238 | 8 | pause menu 0x8018511C | the models the menu preloads before a switch |
| 0x803CC5D8 | 8 | 0x800CED1C | the 8-bit models (`p_dot_*`) |
| 0x803D4880 | 4 | 0x800E31A4, 0x800E33F4, 0x800E3490 | the pipe animation models (`P_dokan_*`) |
| 0x803E82E0 | 4 x 3 | 0x8012A014, 0x8012A1B4 | hurt voice |
| 0x803E8588 | 4 x 4 | 0x8012D4E8 | stylish-move voice |
| 0x803EA038 | 4 x 4 | 0x80174A04 | attack voice |
| 0x803E8708 | 4 | 0x80142580 | landing sound |
| 0x803EBB88 | 4 | 0x80193358 | a pointer per character to a 0x68-byte block (not identified) |

**B. Static float tables, one value per character** (a new id reads the next
table: a nonsense number, so physics or placement goes wrong; no crash).
In most of them Bowser's value is the largest and Mario's and Luigi's are
close, so they scale with body size:

| table | row | read by | notes |
| --- | --- | --- | --- |
| 0x803E8228 | 20 | 0x801220C4, 0x80122300, 0x80122324 | characterProperties: hitbox width, height, walk, dash, one more |
| 0x803E80B0, 0x803E80C0 | 4 | 0x8011C888 (next to the Pixl follower setup) | probably where the Pixl floats relative to the character |
| 0x803E8108, 0x803E8118 | 4 | 0x80121050 | size-scaled (not identified) |
| 0x803E8128, 0x803E8138 | 4 | 0x801215A0 | size-scaled (not identified) |
| 0x80330C60, 0x80330C70 | 4 | 0x8011785C | large, size-scaled (camera-like distances?) |
| 0x803E86F8 | 4 | 0x8013BA04 (mario_hit.c) | a hit / contact size |
| 0x803E91E0 | 8 | 0x801538B0 | size-scaled |
| 0x803E9A60, 0x803E9A70 | 4 | 0x8016C1DC | movement tuning; one character's value is 0 |
| 0x803E8988 | 4 | 0x80143B94 | used with a wind object (`MOBJ_ta3_kaze_aka`) |

**C. Arrays inside the game's work structures** (per-character slots in
MarioWork, the pouch and scratch arrays; a new id writes past them into the
next field, which corrupts state and can reach the save):

0x800D00EC, 0x80121CE0 (+0x24C anim poses), 0x8012CBE4 (two reads),
0x80142F40, 0x801437F0 (four reads, strides 32, 60, 60 and 48), 0x801581EC,
0x80167538, and the pouch's `characters[4]` (+0xB4, followed by the Pixls;
CUSTOM-CHARACTERS.md).

## What that means

- **Group A** (10 tables) and **group B** (about 15) can each get a fifth
  row: relocate the table and patch its `lis`/`addi` pair, the way the item
  table is moved (code patch recipes, tools/content/table_refs.py). That is
  about 25 recipes, each checked at build and boot.
- **Group C** can not be solved by rows. Each one needs a hook on its
  function or a relocated structure, and the pouch one touches the save.
  These are the risky ones.
- A real id also needs the `< 4` assert in marioSetCharacter hooked, and the
  155 compares do nothing for it (it plays like plain Mario).

## Recommendation: keep the base slot, grow what it can swap

An added character already plays on a base character's slot (id 0-3), and
OpenSPM swaps that slot's rows while he plays: model, preload, voice
(3 tables) and now size and speed (characterProperties). That covers most of
group A and part of group B, and group C never comes up, because the id
stays 0-3. A real id 4+ would buy nothing a player sees: only one character
plays at a time, a new id plays like Mario anyway, and it costs about 25
table moves plus hooks on eight functions and the save.

So characters v2 should:

1. **Swap more rows of the base slot**, table by table, as mods need them:
   the 8-bit model and pipe models first (group A: without them an added
   character most likely shows as the base character in 8-bit sections and
   pipe animations; untested), then the landing sound, then the size-scaled group B tables
   once each is identified (the Pixl position matters for big characters
   like Francis).
2. **Use hooks for behaviour**, not ids: the ability dispatch and the hurt,
   jump and attack paths, per function, with the mod supplying the
   behaviour (docs/HOOKS.md). A hook can check "is an added character
   playing" through OpenSPM without the game ever seeing a new id.
3. Leave the id-4+ route documented (this page) in case something needs two
   added characters to exist at once.
