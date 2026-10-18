# Mario's movement: the move table, for characters with their own moves

How the game moves the player (R8PE01 rev 2), for mods that give an added
character its own movement (a Kirby with a float and multi-jump, a Sonic with
momentum and a spin dash). The short version: movement is keyed by the
current *move*, not by the character, so it is changed through the move
functions, not through a new character id (CHARACTER-TABLES.md).

## The machinery

- **Current move**: MarioWork +0x2C (u16), the previous one +0x2E.
- **Move table**: 0x803E8718, 78 rows of 8 bytes: `main` (runs every frame
  while the move is current) and `exit` (runs when the game leaves the move;
  0 = none). Rows 46-67 and 77 point at an empty function (0x80148330,
  `blr`): unused ids.
- **Every frame**: `marioMotionMain` (0x80142CD4) calls
  `table[move].main(MarioWork)`, and calls again whenever the move changed
  meanwhile (MarioWork +0x0C bit 0).
- **A change**: `marioChgMot(move, arg)` (0x80142D48, 81 callers) stores the
  old move in +0x2E and the new one in +0x2C, runs the old move's `exit`,
  and flags the new move to run at once. 0x80142E84 is a smaller variant (19
  callers) without the extra checks.

Both are in the hook registry (off): `marioChgMot` (redirect a change: when
the game starts a jump, a character's own jump instead) and
`marioMotionMain` (the character's own per-frame movement before the
game's). The `main` functions are called indirectly through the table, and
a hook on one of them still fires (the dispatcher's entry check and the
entry guard catch indirect calls, docs/HOOKS.md).

## The moves

Named from the animations each move sets (and MOT_* in spm_r8pe01.h); "?"
marks a guess.

| id | main | animations | move |
| --- | --- | --- | --- |
| 0 | 0x801119D4 | S_1, S_3, E_1, E_2, N_4 | stand (checks for Bowser) |
| 1 | 0x80118A14 | W_1, R_1, S_1 | walk |
| 2 | 0x80118F58 | W_1, R_1, R_3, Z_2 | dash |
| 3, 6, 9 | 0x80174A44 | J_1B, N_7 | jump |
| 4 | 0x80119E04 | S_2 | crouch |
| 5 | 0x8011A300 | C_5 | crouch walk? |
| 7 | 0x801735C8 | J_1B, J_1C, N_7, N_8 | jump (up / fall) |
| 8 | 0x8017416C | J_1B, J_1C | jump variant |
| 10, 11 | 0x80174EB8 | J_1C, N_8 | fall / land |
| 12 | 0x80174D50 | - | ? |
| 13 | 0x8011B050 | C_1 .. C_4 | climb? |
| 14, 15 | 0x80170070 | J_1B, KJ_1A, KJ_1B | stomp bounce (checks for Bowser) |
| 16 | 0x8016DDB8 | H_1C | ? |
| 17 | 0x801717CC | J_1B, J_1C, S_2, T_8 | ? |
| 18 | 0x80176BC8 | W_1, R_1, M_2A | ? |
| 19 | 0x8014A66C | T_2, O_2B | talk (MOT_TALK) |
| 20 .. 33 | several | D_1 .. D_5, U_1 | hurt, knock-back and fall-out variants |
| 36 .. 45 | 0x80167C10 .. 0x8016CBD0 | N_*, M_1*, M_2A, KJ_1A, SD_1, A_4A | Pixl moves (bomb set: SFX_F_BOMB_SET1, 37 and 38) |
| 68 | 0x801179C0 | I_2 | ? (uses `ultramarioFrm`) |
| 69, 70 | 0x80112598, 0x801164D0 | O_2A, O_2B, O_3B | ? (69 checks for Luigi and Bowser) |
| 71, 72 | 0x801471A4, 0x80147AA8 | I_1, I_2 | item use? |
| 73 | 0x8015E9F0 | M_2A, N_4 | ? |
| 74 | 0x801758A0 | J_2A .. J_2D | a special jump (stylish?) |
| 75 | 0x80149378 | G_1, G_2, J_1B, J_1C | ? |

Only 11 moves look at the character id at all (stand, stomp bounce, two
hurt moves, Pixl moves 39, 40 and 44, 69 and 71); the rest are the same for
everyone. The characters' own abilities (Peach's parasol, Bowser's fire,
Luigi's super jump) are chosen elsewhere, in the ability and input code.

## What a Kirby or Sonic mod does with it

1. **Per-frame nudges** (works today, no hooks): read and write MarioWork's
   speed and state every frame from the mod tick. Momentum (slow deceleration,
   speed building while running) and "jump again in the air" (switch back to
   a jump move) are this.
2. **A borrowed base**: base the character on Peach and the parasol float
   comes with it (it is keyed on her id).
3. **Hooks** (built, first in-game test pending): `marioChgMot` pre to send a
   change to another move (spin dash: crouch + jump), `marioMotionMain` pre
   for the character's own per-frame movement, or a hook on one move's `main`
   to change it (continue, adjust after, or skip for that frame), each only
   while the added character plays.
4. **New moves**: the table can be moved and given rows (like the item table)
   so a character has moves the game never had; their `main` would be a
   hooked empty function the mod implements. Needs the per-frame physics and
   collision the game's own moves call, so it is the last step.
