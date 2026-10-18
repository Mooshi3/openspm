/* Super Paper Mario, R8PE01 revision 2 (US): game facts for mods.
 *
 * Addresses and layouts a mod needs to read game state and to make the game do
 * things through openspm.guest_call (include/openspm/guest_call.h). Every value
 * here was checked against this revision's code (the function or table it
 * names reads/writes it); symbol names follow the public spm-decomp project.
 * Other revisions differ: a mod should check spm_r8pe01_verify() (or read a
 * known value itself) before it writes anything.
 *
 * Facts only: no game code or data is included. */
#ifndef OPENSPM_SPM_R8PE01_H
#define OPENSPM_SPM_R8PE01_H

#include <stdint.h>

/* ---- Game state ---------------------------------------------------------- */
/* SEQ_WORK: +0 current sequence (2 = in game), +4 stage. */
#define SPM_R8PE01_SEQ_WORK UINT32_C(0x804D0D60)
#define SPM_R8PE01_SEQ_GAME UINT32_C(2)
#define SPM_R8PE01_SEQ_MAPCHANGE UINT32_C(3) /* between two maps: still in the game */
/* _spmarioSystemLevel (spmarioSystemLevel 0x801A7D44 stores it at r13-30008):
   0 = normal play; 1+ = the world is frozen (item use, pause menu, events). */
#define SPM_R8PE01_SYSTEM_LEVEL UINT32_C(0x8056D3C8)

/* MarioWork (marioGetPtr 0x8012191C returns it). */
#define SPM_R8PE01_MARIO_WORK UINT32_C(0x804CEE58)
/* u32 flags (evt_mario_flag8_onoff 0x800EF5D8 sets and clears them). Bit
   0x1: Mario is not drawn (his draw registration, 0x801276A8, skips him);
   scripts set it for scenes he is not in. */
#define SPM_R8PE01_MARIO_FLAG8 0x8u
#define SPM_R8PE01_MARIO_FLAG8_HIDDEN 0x1u
#define SPM_R8PE01_MARIO_MOTION_ID 0x2Cu /* u16, MOT_* below */
#define SPM_R8PE01_MARIO_CHARACTER 0x38u /* s8: 0 Mario, 1 Peach, 2 Bowser, 3 Luigi */
#define SPM_R8PE01_MARIO_POSITION 0x5Cu  /* 3 x f32 (x, y, z) */
/* f32 facing angle in degrees. The Squirps shot (0x80113AC0) sets its flag
   0x20 for a value in [90, 270] (wrapped to [0, 360)), and that flag makes
   the shot travel at angle 90 (+x): [90, 270] is facing RIGHT, the rest
   facing left. spm_r8pe01_facing_left() below. */
#define SPM_R8PE01_MARIO_FACING_ANGLE 0x184u
/* In 3D (flipped) or not: the game's own test (0x8012AA54, 309 callers) reads
   the camera id at MarioWork +0x120, the camera pointer table and that
   camera's u16 at +10 (nonzero = 3D). */
#define SPM_R8PE01_MARIO_CAMERA_ID 0x120u
#define SPM_R8PE01_CAMERA_TABLE UINT32_C(0x804C9F80)
#define SPM_R8PE01_CAMERA_3D 10u
#define SPM_R8PE01_CAMERA_VIEW_MTX 0x54u /* Mtx34; row 0 = the screen's right axis */
/* camDraw (0x800555A0): projection Mtx44 at +0x94 (C_MTXPerspective /
   C_MTXOrtho output, GXSetProjection input), projection type at +0x158
   (0 perspective, 1 orthographic), viewport x, y, w, h, near, far at +0x20
   (6 x f32). */
#define SPM_R8PE01_CAMERA_PROJ_MTX 0x94u
#define SPM_R8PE01_CAMERA_PROJ_TYPE 0x158u
#define SPM_R8PE01_CAMERA_VIEWPORT 0x20u
/* Mario's hitbox as the game uses it (0x801220C4 writes it from
   characterProperties, scaled for some states): width +0x194, height
   +0x198 (f32; the unscaled copies are +0x19C / +0x1A0). Walk / dash speed
   +0x14C / +0x150. */
#define SPM_R8PE01_MARIO_HIT_WIDTH 0x194u
#define SPM_R8PE01_MARIO_HIT_HEIGHT 0x198u
static inline int spm_r8pe01_facing_left(float angle) {
    if (angle != angle) return 0;
    while (angle < 0.0f) angle += 360.0f;
    while (angle >= 360.0f) angle -= 360.0f;
    return !(angle >= 90.0f && angle <= 270.0f);
}
#define SPM_R8PE01_MOT_STAY 0x00u
#define SPM_R8PE01_MOT_WALK 0x01u
#define SPM_R8PE01_MOT_DASH 0x02u
#define SPM_R8PE01_MOT_JUMP 0x03u
#define SPM_R8PE01_MOT_CROUCH 0x04u
#define SPM_R8PE01_MOT_TALK 0x13u

/* MarioPouchWork (pouchGetPtr 0x8014B9EC): +0x0C hp, +0x10 max hp, +0x1C coins. */
#define SPM_R8PE01_POUCH UINT32_C(0x804D0428)

/* ---- Functions a mod can call (openspm.guest_call, integer args) --------- */
/* EvtEntry *evtEntry(const EvtScriptCode *script, u32 priority, u8 flags):
   starts an EVT script; the game's evtmgr runs it from the next frame on. */
#define SPM_R8PE01_EVT_ENTRY UINT32_C(0x800D8CDC)

/* EVT user functions (call them from a mod's EVT script, spm_evt.h):
   evt_mario_get_pos(&x, &y, &z) - Mario's position as integers.
   evt_npc_entry_from_template(idOverride (0 = new), templateId, x, y, z,
                               &instanceId, &name (EVT null ok)) - spawn an NPC. */
#define SPM_R8PE01_EVT_MARIO_GET_POS UINT32_C(0x800EFB64)
/* evt_mario_key_off(0) / evt_mario_key_on(): the player's control held and
   given back (cutscenes). evt_mario_set_pose(name, time): MarioWork +0x1C
   animation name, applied by Mario's own update (any name the current model
   has). */
#define SPM_R8PE01_EVT_MARIO_KEY_OFF UINT32_C(0x800EF768)
#define SPM_R8PE01_EVT_MARIO_KEY_ON UINT32_C(0x800EF744)
#define SPM_R8PE01_EVT_MARIO_SET_POSE UINT32_C(0x800F1A6C)
/* The player's animation: the animation work pointer (its +0x00 group table,
   16 bytes per group, file at +8; +0x10 pose table, 0x188 bytes per pose,
   group at +0x10) and Mario's pose ids (MarioWork +0x24C). A file's data
   (+0xA4) points at the model header: animation count +0x148, the table
   +0x1AC (64 bytes per animation: name, data pointer at +60). */
#define SPM_R8PE01_ANIM_WORK_PTR UINT32_C(0x8056C958)
#define SPM_R8PE01_MARIO_POSE_IDS 0x24Cu
#define SPM_R8PE01_EVT_MARIO_SET_POS UINT32_C(0x800EFAA0) /* (x, y, z) */
/* Player voices, sound name pointers per character (Mario, Peach, Bowser,
   Luigi rows): hurt 3 each ("SFX_P_V_MARIO_DAMEGE1".."3"), acrobatics
   (stylish) 4 each ("SFX_P_V_MARIO_ACRO1".."4"), attack 4 each
   ("SFX_P_V_MARIO_ATTACK1".."4"). */
#define SPM_R8PE01_VOICE_DAMAGE_TABLE UINT32_C(0x803E82E0)
#define SPM_R8PE01_VOICE_DAMAGE_COUNT 3u
#define SPM_R8PE01_VOICE_ACRO_TABLE UINT32_C(0x803E8588)
#define SPM_R8PE01_VOICE_ACRO_COUNT 4u
#define SPM_R8PE01_VOICE_ATTACK_TABLE UINT32_C(0x803EA038)
#define SPM_R8PE01_VOICE_ATTACK_COUNT 4u
#define SPM_R8PE01_EVT_SND_SFXON_3D_PLAYER UINT32_C(0x800D2D9C) /* (name): at the player */
/* Koopa Striker (tribe 69, e_k_shooter) animations from its anim defs
   (0x80450388): the enemy's own set-and-kick (move script 0x804518A8) plays
   SFX_E_KOURASHOOTER_SET1 with A_1_a, spawns the shell, then A_1_b and kicks
   366 ms in. */
#define SPM_R8PE01_STRIKER_ANIM_SET "A_1_a"
#define SPM_R8PE01_STRIKER_ANIM_KICK "A_1_b"
#define SPM_R8PE01_STRIKER_ANIM_STAND "S_1"
#define SPM_R8PE01_EVT_NPC_ENTRY_FROM_TEMPLATE UINT32_C(0x80106174)

/* npcEnemyTemplates: 435 entries of 0x68 bytes; +0x14 tribe id, +0x54 the
   shell-kick script (shared by every Koopa-type template). Template 129 is the
   Koopa Striker's shell (tribe 70): a plain shell object that waits to be
   kicked and never turns back into a Koopa. */
#define SPM_R8PE01_NPC_TEMPLATES UINT32_C(0x8040B128)
#define SPM_R8PE01_NPC_TEMPLATE_SIZE 0x68u
#define SPM_R8PE01_NPC_TEMPLATE_TRIBE 0x14u
#define SPM_R8PE01_NPC_TEMPLATE_KICK_SCRIPT 0x54u
#define SPM_R8PE01_TEMPLATE_STRIKER_SHELL 129u
#define SPM_R8PE01_TRIBE_STRIKER_SHELL 70u
#define SPM_R8PE01_KOOPA_KICK_SCRIPT UINT32_C(0x803FCE20)

/* ---- NPCs ---------------------------------------------------------------- */
/* npcdrv_wp (npcGetWorkPtr 0x801C8C5C reads it at r13-32120): pointer to
   NPCWork { +0 flag; +4 s32 num (capacity); +8 NPCEntry *entries }. */
#define SPM_R8PE01_NPC_WORK_PTR UINT32_C(0x8056CB88)
#define SPM_R8PE01_NPC_WORK_NUM 0x4u
#define SPM_R8PE01_NPC_WORK_ENTRIES 0x8u
/* NPCEntry (0x748 bytes). The fields the game's own attack loop
   (0x801AA478, Mario's attacks and the Squirps shot) filters on: flag8 bit 0
   = in use, flag8 0x40000000 / flagC 0x20 / flag46C 0x20000 = skip, moveMode
   0, 6, 7 = cannot be hit. */
#define SPM_R8PE01_NPC_ENTRY_SIZE 0x748u
#define SPM_R8PE01_NPC_FLAG8 0x008u
#define SPM_R8PE01_NPC_FLAGC 0x00Cu
#define SPM_R8PE01_NPC_NAME 0x024u       /* char[32] */
#define SPM_R8PE01_NPC_POSE_NAME 0x048u  /* char[32], NPCAnim.animPoseName */
#define SPM_R8PE01_NPC_POSITION 0x2A0u   /* 3 x f32 */
#define SPM_R8PE01_NPC_MOVE_MODE 0x2F8u
#define SPM_R8PE01_NPC_FLAG46C 0x46Cu
#define SPM_R8PE01_NPC_TRIBE_ID 0x49Cu
#define SPM_R8PE01_NPC_HP 0x4F0u
#define SPM_R8PE01_NPC_AXIS 0x508u       /* s32: -1 faces left, 1 faces right */
/* f32 x 3 rotation in degrees (evt_npc_rotate 0x800FE0EC writes it, wrapped
   to [0, 360)); +4 is the turn about the vertical axis. */
#define SPM_R8PE01_NPC_ROTATION 0xB4u
#define SPM_R8PE01_NPC_PARTS 0x714u      /* NPCPart * (linked list) */
/* NPCPart: +0 u16 id, +4 s32 minimum damage (<= -1: immune), +0x2C flags
   (bit 0: skip), +0x3C hitbox size (3 x f32), +0x390 next part. */
#define SPM_R8PE01_NPC_PART_ID 0x000u
#define SPM_R8PE01_NPC_PART_MIN_DAMAGE 0x004u
#define SPM_R8PE01_NPC_PART_FLAGS 0x02Cu
#define SPM_R8PE01_NPC_PART_HITBOX 0x03Cu
#define SPM_R8PE01_NPC_PART_NEXT 0x390u
/* npcTribes: 535 entries of 0x68 bytes; +0 animPoseName, +4 animDefs
   ({ s32 id; const char *animName; } terminated by id -1). */
#define SPM_R8PE01_NPC_TRIBES UINT32_C(0x803FD7D0)
#define SPM_R8PE01_NPC_TRIBE_COUNT 535u
#define SPM_R8PE01_NPC_TRIBE_SIZE 0x68u
#define SPM_R8PE01_NPC_TRIBE_POSE 0x0u
#define SPM_R8PE01_NPC_TRIBE_ANIM_DEFS 0x4u
/* Frackle (the small cannons Fracktail spawns, pose "e_kozun": S_1 stand,
   W_1 walk, A_1 attack) and the Wrackle's cannonball (same pose, P_1). */
#define SPM_R8PE01_TRIBE_FRACKLE 450u
#define SPM_R8PE01_TRIBE_WRACKLE_SHOT 452u

/* EVT user functions for NPCs (name = guest pointer to a C string; the
   game's own map scripts use them in this order to make a scripted NPC):
   evt_npc_entry(name, animPoseName, instanceId 0)
   evt_npc_set_property(name, 14, animDefs)   - the NPC's animation table
   evt_npc_set_anim(name, animId, 1)
   evt_npc_flag8_onoff(name, on, mask)
   evt_npc_animflag_onoff(name, on, mask)
   evt_npc_add_flip_part(name)
   evt_npc_set_position(name, x, y, z)        - EVT floats
   evt_npc_set_axis_movement_unit(name, -1/1)
   evt_npc_delete(name)
   Every one of them asserts (crashes) when no NPC has that name. */
#define SPM_R8PE01_EVT_NPC_ENTRY UINT32_C(0x801029AC)
#define SPM_R8PE01_EVT_NPC_SET_PROPERTY UINT32_C(0x801046E4)
#define SPM_R8PE01_NPC_PROPERTY_ANIM_DEFS 14u
#define SPM_R8PE01_EVT_NPC_SET_ANIM UINT32_C(0x80102B58)
#define SPM_R8PE01_EVT_NPC_FLAG8_ONOFF UINT32_C(0x801030DC)
#define SPM_R8PE01_EVT_NPC_ANIMFLAG_ONOFF UINT32_C(0x80103408)
#define SPM_R8PE01_EVT_NPC_ADD_FLIP_PART UINT32_C(0x80102AA4)
#define SPM_R8PE01_EVT_NPC_SET_POSITION UINT32_C(0x800FDCA0)
#define SPM_R8PE01_EVT_NPC_SET_AXIS UINT32_C(0x80105420)
#define SPM_R8PE01_EVT_NPC_DELETE UINT32_C(0x801028B8)
/* evt_npc_set_color(name, r, g, b, a) (0-255 each, into the NPC's tint),
   evt_npc_set_scale(name, x, y, z) (EVT floats) and
   evt_npc_set_part_attack_power(name, part, power) (part -1: every part).
   Inside an NPC's own scripts the name "me" is that NPC. */
/* A map's init script (docs/CUSTOM-LEVELS.md; the game's maps use these):
   evt_hitobj_attr_onoff(1, 1, group, attr)   - collision group attributes:
       "A2D" 0x40000000, "A3D" 0x20000000 (2D-only / 3D-only collision)
   evt_mobj_blk(type, name, x, y, z, item, 0, 0) - a block: type 0 brick,
       1 "?" block, 2 "?" brick, 3 ten-coin block; item an item id (1 coin,
       6 mushroom)
   evt_item_entry(name, item, 0, x, y, z, 0, 0, 0, 0) - an item lying there
   evt_mobj_save_blk(name, x, y, z, 0)          - a save block
   evt_door_set_dokan_descs(descs, count)        - the map's pipes (DokanDesc,
       0x20 bytes: s32 script, u16 flags, pad, name, this map's name, 2D hit,
       3D hit, destination map, destination door)
   door_init_evt (a script, RUN_CHILD_EVT)        - the arrival by door or pipe
   evt_npc_set_move_mode(name, 1)                - a townsperson standing
   evt_npc_set_property(name, 9, script)         - what talking to it runs
   evt_msg_print(0, key, 0, speaker)             - a message box ("me": the NPC)
   evt_snd_bgmon(0, name)                        - the music */
#define SPM_R8PE01_EVT_HITOBJ_ATTR_ONOFF UINT32_C(0x800EB880)
#define SPM_R8PE01_HIT_ATTR_2D_ONLY UINT32_C(0x40000000)
#define SPM_R8PE01_HIT_ATTR_3D_ONLY UINT32_C(0x20000000)
#define SPM_R8PE01_EVT_MOBJ_BLK UINT32_C(0x800F8C2C)
#define SPM_R8PE01_BLK_BRICK 0u
#define SPM_R8PE01_BLK_QUESTION 1u
#define SPM_R8PE01_EVT_ITEM_ENTRY UINT32_C(0x800ECDD8)
#define SPM_R8PE01_ITEM_COIN 1u
#define SPM_R8PE01_ITEM_MUSHROOM 6u
#define SPM_R8PE01_EVT_MOBJ_SAVE_BLK UINT32_C(0x800FB15C)
#define SPM_R8PE01_EVT_DOOR_SET_DOKAN_DESCS UINT32_C(0x800E36DC)
#define SPM_R8PE01_DOKAN_DESC_SIZE 0x20u
#define SPM_R8PE01_DOOR_INIT_EVT UINT32_C(0x803DBE48)
#define SPM_R8PE01_EVT_NPC_SET_MOVE_MODE UINT32_C(0x801040E4)
#define SPM_R8PE01_NPC_PROPERTY_TALK 9u
#define SPM_R8PE01_NPC_FLAG8_TOWNSPERSON UINT32_C(0x0C400004) /* Flipside's townsfolk */
#define SPM_R8PE01_NPC_ANIMFLAG_TOWNSPERSON UINT32_C(0x20)
#define SPM_R8PE01_EVT_MSG_PRINT UINT32_C(0x800FBA98)
#define SPM_R8PE01_EVT_SND_BGMON UINT32_C(0x800D22E4)
#define SPM_R8PE01_EVT_NPC_SET_COLOR UINT32_C(0x800FEEF4)
#define SPM_R8PE01_EVT_NPC_SET_SCALE UINT32_C(0x800FE9B4)
#define SPM_R8PE01_EVT_NPC_SET_PART_ATTACK_POWER UINT32_C(0x801087E8)
/* flag8 / animflag values the game's Dashell scene uses for a decorative
   flying NPC (dan chest scene). */
#define SPM_R8PE01_NPC_FLAG8_DECOR UINT32_C(0x0C420004)
#define SPM_R8PE01_NPC_ANIMFLAG_DECOR UINT32_C(0x20)
/* evt 0x80108034(attacker, target, partId, unused, damageType, power, flags,
   param): resolves both names without asserting (returns quietly when one is
   missing; "mario" = Mario) and calls npcTakeDamage(attacker, part,
   damageType, power, flags, param). Damage type 32 with attacker Mario and
   flags 0 is exactly what the Squirps shot does (0x801AACF0). */
#define SPM_R8PE01_EVT_NPC_TAKE_DAMAGE UINT32_C(0x80108034)
#define SPM_R8PE01_DAMAGE_SQUIRPS_SHOT 32u
/* evt_snd_sfxon(name) and the Squirps shot sound's name string. */
#define SPM_R8PE01_EVT_SND_SFXON UINT32_C(0x800D299C)
#define SPM_R8PE01_SFX_SQUIRPS_SHOT_STRING UINT32_C(0x80330BAC) /* "SFX_MAP_TAMARA_SHOT1" */

/* ---- Characters and the pause menu's character tab --------------------- */
/* evt_mario_set_character(id): marioSetCharacter (asserts 0 <= id < 4; does
   nothing when id is the current character). A switch loads the character's
   model with animPoseEntry, which reads the file synchronously: run it from
   an EVT script (the game's own threads), never from a guest call. */
#define SPM_R8PE01_EVT_MARIO_SET_CHARACTER UINT32_C(0x800EFA70)
/* Character models, read at runtime by the model loader (0x80121CE0):
   MODEL_TABLE[character * 2 + 0] normal, [+1] Mega Star ("p_big_*");
   MODEL_TABLE_EXTRA[character * 2] Mario's second model ("p_wii_mario_r"),
   NULL for the others. The pause menu preloads PRELOAD_TABLE[character * 2]
   and [+1] before a switch (0x8018511C). */
#define SPM_R8PE01_CHAR_MODEL_TABLE UINT32_C(0x803E8148)
#define SPM_R8PE01_CHAR_MODEL_TABLE_EXTRA UINT32_C(0x803E8168)
#define SPM_R8PE01_CHAR_PRELOAD_TABLE UINT32_C(0x803EB238)
/* characterProperties[4]: 20 bytes per character, f32 hitbox width (+0),
   height (+4), walk speed (+8), dash speed (+12), +16 (not identified).
   Read by 0x801220C4 on every character change (into MarioWork +0x194 /
   +0x19C width, +0x198 / +0x1A0 height, +0x14C walk, +0x150 dash, +0x158) and
   by the accessors 0x80122300 (width) and 0x80122324 (height). */
#define SPM_R8PE01_CHAR_PROPERTIES UINT32_C(0x803E8228)
#define SPM_R8PE01_CHAR_PROPERTIES_SIZE 20u
/* Character item ids (itemDataTable): 216 + character id. */
#define SPM_R8PE01_ITEM_CHARACTER_FIRST 216u
/* itemDataTable: 538 entries of 0x2C: +0 name, +4 s16 iconId, +0x10 nameMsg,
   +0x14 descMsg. Item 232 ("ICON", msg_unknown_item) is a placeholder. */
#define SPM_R8PE01_ITEM_DATA UINT32_C(0x803B7E78)
#define SPM_R8PE01_ITEM_DATA_SIZE 0x2Cu
#define SPM_R8PE01_ITEM_NAME 0x00u
#define SPM_R8PE01_ITEM_ICON 0x04u
#define SPM_R8PE01_ITEM_NAME_MSG 0x10u
#define SPM_R8PE01_ITEM_DESC_MSG 0x14u
#define SPM_R8PE01_ITEM_PLACEHOLDER 232u
#define SPM_R8PE01_ITEM_CARD_KOOPA_STRIKER 319u /* ename_064 / ehelp_064, model e_k_shooter */
#define SPM_R8PE01_TRIBE_KOOPA_STRIKER 69u
/* Pause-menu windows (pausewin_wp at r13-30160): +4 entries of 120 bytes,
   +0 u16 flags (bit 0 in use), +60 state; visible = 10 <= state < 20
   (0x801849A0). */
#define SPM_R8PE01_PAUSEWIN_WP UINT32_C(0x8056D330)
#define SPM_R8PE01_PAUSEWIN_ENTRIES 0x4u
#define SPM_R8PE01_PAUSEWIN_ENTRY_SIZE 120u
#define SPM_R8PE01_PAUSEWIN_STATE 60u
/* The pause menu's pick, in the same work (its pointer at r13-30160): a
   confirmed entry's index and item are copied to +40 / +44 (0x80195640);
   the character switch then reads +44 (item - 216 = the id, 0x8018511C) a
   frame or more later. */
#define SPM_R8PE01_PAUSE_PICK_ITEM 44u
/* The character / Pixl / item tabs' work (r13-32264): +16 the character
   tab's window id, +68 its list { entries (12 bytes each: u8 selectable,
   u32 itemId, u32 0), count, cursor, ... } allocated for 5 entries (60 bytes)
   and filled with at most 4 (0x80195698), +164 the number of tab lists the
   close still has to free (the close, 0x801954A0, sets 3; each tab's delete
   callback frees its list and counts down; it is NOT 3 while the menu is
   open: whether a tab is open is its pausewin window's, see content_roster.h). Confirm in the character tab is button 2 (0x80195F30); an entry
   whose selectable byte is 0 only buzzes. */
#define SPM_R8PE01_CHARTAB_WP UINT32_C(0x8056CAF8)
#define SPM_R8PE01_CHARTAB_WINDOW_ID 16u
#define SPM_R8PE01_CHARTAB_LIST 68u
#define SPM_R8PE01_CHARTAB_COUNT 72u
#define SPM_R8PE01_CHARTAB_CURSOR 76u
#define SPM_R8PE01_CHARTAB_LIVE_LISTS 164u
#define SPM_R8PE01_CHARTAB_LIST_CAPACITY 5u
#define SPM_R8PE01_CHARTAB_ENTRY_SIZE 12u

/* ---- Item use scripts ---------------------------------------------------- */
/* itemEventDataTable: 33 entries of { s32 itemId; EvtScriptCode *useScript;
   const char *useMsgName; } (0xC bytes). The pause menu's item use
   (0x80185260) starts useScript with evtEntryType(script, 0, 0, 0) and stores
   the item id in the script's LW(5). */
#define SPM_R8PE01_ITEM_EVENT_TABLE UINT32_C(0x803BDAF0)
#define SPM_R8PE01_ITEM_EVENT_COUNT 33u
#define SPM_R8PE01_ITEM_EVENT_SIZE 0xCu
/* Entry index, item id and use script of the attack items (msg_AC_*). */
#define SPM_R8PE01_ITEM_FIRE_BURST 65u        /* honoo_sakuretsu, index 0 */
#define SPM_R8PE01_ITEM_ICE_STORM 66u         /* koori_no_ibuki, index 1 */
#define SPM_R8PE01_ITEM_THUNDER_RAGE 67u      /* kaminari_dokkan, index 2 */
#define SPM_R8PE01_ITEM_SHOOTING_STAR 68u     /* kirakira_otoshi, index 3 */
#define SPM_R8PE01_ITEM_POW_BLOCK 69u         /* pow_block, index 4 */
#define SPM_R8PE01_ITEM_SHELL_SHOCK 70u       /* koura_de_pon, index 5 */
#define SPM_R8PE01_SHELL_SHOCK_INDEX 5u
#define SPM_R8PE01_SHELL_SHOCK_USE_SCRIPT UINT32_C(0x803BFA88)

#endif
