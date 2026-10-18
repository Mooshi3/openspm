/* OpenSPM content roster: registered characters in the game (provider side of
 * "openspm.content", include/openspm/content_service.h; docs/CONTENT-API.md).
 *
 * Runs once per frame inside the mod tick (guest memory open, input
 * replaceable, guest calls queueable) and costs nothing while no character is
 * registered. The game itself never sees a character id above 3:
 *
 *  - Menu: the pause menu's character tab list (allocated for 64 entries by
 *    OpenSPM's build patch, 5 without it) gets one entry per registered
 *    character after the game's own. The entries use placeholder item 232,
 *    whose name/description OpenSPM points at its own messages while the
 *    cursor is on one of them (message table installed in a free msgdrv slot,
 *    so msgSearch finds OpenSPM's keys like the game's own).
 *  - Picking: button 2 on an added entry is taken from the game; OpenSPM puts
 *    the character's model into the base character's model tables, moves the
 *    cursor to the base character and presses 2 for it on the next frame, so
 *    the game performs its own, normal switch to the base character (with
 *    that model). When the base character already is the current one, the
 *    game only closes the menu; OpenSPM then asks for the same flip
 *    transition the game plays for a switch (move 71, which reloads the
 *    model), or, outside plain play, reloads it from an EVT script.
 *  - Pixls: the game's Pixl flip (move 72) after the menu closes, with a game
 *    Pixl's item standing in where the game sizes the flip.
 *  - Leaving: picking any game character, a character change by the game, or
 *    leaving the game restores every table and the placeholder item.
 *
 * Portable C, header-only, no game code: addresses and layouts only
 * (include/openspm/spm_r8pe01.h). tests/m14_content_roster_test.c drives it
 * against a fake game. */
#ifndef OPENSPM_CONTENT_ROSTER_H
#define OPENSPM_CONTENT_ROSTER_H

#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "openspm/abi.h"
#include "openspm/code_patches.h"
#include "openspm/content_service.h"
#include "openspm/input_service.h"
#include "openspm/spm_evt.h"
#include "openspm/spm_r8pe01.h"

/* msgdrv work (r13-32744): 9 message tables of 16 bytes { size, data base,
   count, handle -> { entries } }, entries { nameOffset, textOffset } sorted
   by name; msgSearch binary-searches every table in order. Map loads fill
   tables 1.. and msgUnload (0x8002F1E0) only zeroes them. */
#define OPENSPM_ROSTER_MSG_WORK_PTR UINT32_C(0x8056C918)
#define OPENSPM_ROSTER_MSG_TABLES 9u
#define OPENSPM_ROSTER_PAUSEWIN_SET_MESSAGE UINT32_C(0x80187C88) /* (window, itemId, 0) */
#define OPENSPM_ROSTER_CHARTAB_MSG_WINDOW 28u /* chartab work: message window id */
#define OPENSPM_ROSTER_MAX_LIST 64u
/* Pixls: evt_fairy_reset (rebuild the Pixl followers from the pouch, as the
   Pixl-change motion does), the pouch's 16 Pixl slots { u8 enabled, u8
   selected, u16 itemId } at MarioPouchWork+0xC4, the Pixl tab's window id
   and list (+20, +100 entries, +104 count, +108 cursor) and the build patch
   that grows that list. */
#define OPENSPM_ROSTER_EVT_FAIRY_RESET UINT32_C(0x800E8638)
#define OPENSPM_ROSTER_POUCH_PIXLS (SPM_R8PE01_POUCH + 0xC4u)
#define OPENSPM_ROSTER_POUCH_PIXL_SLOTS 16u
/* animdrv (0x8056C958 -> AnimWork): +0 groups (16 bytes: +8 FileEntry, whose
   +0xA4 -> data -> AnimPoseData header), +0x10 poses (0x188 bytes: +0 flag,
   +4 type flags (paper pose), +0x10 group, +0x14 anim index, +0x20 frame
   counter). Header +0x148 anim count, +0x1AC anims (64 bytes, name first). */
#define OPENSPM_ROSTER_ANIM_WORK_PTR SPM_R8PE01_ANIM_WORK_PTR
#define OPENSPM_ROSTER_MARIO_POSE_NAME 0x1Cu
#define OPENSPM_ROSTER_MARIO_POSE_IDS SPM_R8PE01_MARIO_POSE_IDS
#define OPENSPM_ROSTER_PAUSEWIN_INIT_FN 100u /* pausewin entry: init callback (run by create) */
/* pausewin entry +16: f32 height. The pause menu sizes a tab's window once, at
   open, for the game's own rows (25 per row + 50, at most 250 = 8 rows;
   0x80194B28) and the list draw (0x80197EB0) clips rows below it. */
#define OPENSPM_ROSTER_PAUSEWIN_HEIGHT 16u
/* chartab work +0 (u16) results the pause menu reads (0x801955E8): 0x2000 =
   "a Pixl was picked", which closes the menu (state 30: no Pixl change when
   the pouch's selected Pixl did not change, as with ours). Set by the game's
   own Pixl confirm (0x80196A48) together with SFX_SYS_MENU_DESIDE1. */
#define OPENSPM_ROSTER_CHARTAB_PIXL_PICKED 0x20000000u /* as the high half of the +0 word */
#define OPENSPM_ROSTER_SFX_PLAY UINT32_C(0x8023950C)
#define OPENSPM_ROSTER_SFX_DECIDE UINT32_C(0x8033515B) /* "SFX_SYS_MENU_DESIDE1" */
#define OPENSPM_ROSTER_CHARTAB_INIT UINT32_C(0x80195698) /* character tab list builder */
#define OPENSPM_ROSTER_PIXLTAB_INIT UINT32_C(0x80196070) /* Pixl tab list builder */
#define OPENSPM_ROSTER_PIXLTAB_WINDOW_ID 20u
#define OPENSPM_ROSTER_PIXLTAB_LIST 100u
#define OPENSPM_ROSTER_PIXLTAB_COUNT 104u
#define OPENSPM_ROSTER_PIXLTAB_CURSOR 108u
#define OPENSPM_ROSTER_PIXLTAB_PATCH UINT32_C(0x801960A8)
#define OPENSPM_ROSTER_PIXLTAB_PATCHED UINT32_C(0x38800300)
#define OPENSPM_ROSTER_PIXLTAB_VANILLA_CAPACITY 17u
#define OPENSPM_ROSTER_ITEM_PIXL_DEFAULT_ICON 220u /* Tippi's menu icon */
/* The game's own switch transitions (the card flip after a pick in the pause
   menu). The menu's close (0x80185178 / 0x801851E0) asks for them:
   - a character, 0x80146DB0(id): returns at once when id is the current
     character; else MarioWork +0x1480 = id, marioChgMot(71, 1), then +0
     |= 0x10000000. Move 71 waits for the character's files, flips, and calls
     _marioChangeCharacter(+0x1480), which reloads the model whatever the id.
   - a Pixl, 0x8014777C(old item, new item): +0x1484 / +0x1488, then the same
     with move 72, which rebuilds the followers from the pouch at the flip.
     Both items go to a size lookup that panics outside the game's Pixls
     (0x80153FF8: items 220..231), and a non-zero old item must have its
     follower out (0x8011E2E0's result is used without a check).
   marioChgMot refuses every change while +0 has 0x10000000 (the transition
   holds it), so the flag goes on only once move 71 runs. Both requests take
   other paths in special states (+0 0x00100000, +4 0x00800000 / 0x00004000,
   moves 0x26 / 0x29); OpenSPM uses them only from plain play. The models
   load on the game's thread at the flip (never from a guest call), as with
   the scripts these replace. */
#define OPENSPM_ROSTER_MARIO_CHG_MOT UINT32_C(0x80142D48)
#define OPENSPM_ROSTER_PIXL_CHANGE UINT32_C(0x8014777C)
#define OPENSPM_ROSTER_MARIO_CHANGE_TARGET 0x1480u
#define OPENSPM_ROSTER_MOT_CHARACTER_CHANGE 71u
#define OPENSPM_ROSTER_MARIO_LOCKED 0x10000000u    /* MarioWork +0 */
#define OPENSPM_ROSTER_MARIO_SPECIAL0 0x00100000u  /* MarioWork +0 */
#define OPENSPM_ROSTER_MARIO_SPECIAL4 0x00804000u  /* MarioWork +4 */
#define OPENSPM_ROSTER_FOLLOWERS UINT32_C(0x804CEA30) /* +0 bit 0: none; +4 entries (208 bytes, item +4); +840 count */
#define OPENSPM_ROSTER_PIXL_FIRST 220u
#define OPENSPM_ROSTER_PIXL_STANDIN 221u /* a game Pixl: added Pixl items copy it; sizes the flip */
/* Per-character tables with real ids (rows of 8, 4 and 4 bytes): the 8-bit
   model, the pipe animation model, the landing sound. */
#define OPENSPM_ROSTER_DOT_MODEL_TABLE UINT32_C(0x803CC5D8)
#define OPENSPM_ROSTER_PIPE_MODEL_TABLE UINT32_C(0x803D4880)
#define OPENSPM_ROSTER_LANDING_TABLE UINT32_C(0x803E8708)
#define OPENSPM_ROSTER_ITEM_CHARACTER_PICK 216u /* the pause menu's id = entry item - 216 */
#define OPENSPM_ROSTER_MAX_REAL_IDS 31u /* ids 4, 8, ... 124 */

static const uint32_t k_openspm_roster_voice_count[3] = {SPM_R8PE01_VOICE_DAMAGE_COUNT, SPM_R8PE01_VOICE_ACRO_COUNT,
                                                          SPM_R8PE01_VOICE_ATTACK_COUNT};
static const char *const k_openspm_roster_voice_kind[3] = {"damage", "acro", "attack"};
/* characters' own properties (openspm_character_def.properties) */
static const char *const k_openspm_roster_prop_name[4] = {"width", "height", "walk", "dash"};
static const float k_openspm_roster_prop_min[4] = {4.0f, 8.0f, 0.2f, 0.2f};
static const float k_openspm_roster_prop_max[4] = {80.0f, 100.0f, 6.0f, 10.0f};

/* "width=24,walk=1.2" -> values and a mask; 0 on anything else. */
static inline int openspm_roster_parse_props(const char *m, float *out, uint32_t *set) {
    *set = 0u;
    if (m == NULL) return 1;
    if (strlen(m) > 127u) return 0;
    while (*m) {
        const char *end = strchr(m, ',');
        const char *eq = strchr(m, '=');
        uint32_t i, found = 4u;
        char num[24];
        size_t n;
        double v = 0.0, scale = 0.0;
        if (end == NULL) end = m + strlen(m);
        if (eq == NULL || eq >= end) return 0;
        for (i = 0; i < 4u; ++i)
            if ((size_t)(eq - m) == strlen(k_openspm_roster_prop_name[i]) &&
                strncmp(m, k_openspm_roster_prop_name[i], (size_t)(eq - m)) == 0)
                found = i;
        n = (size_t)(end - eq - 1);
        if (found == 4u || (*set & (1u << found)) || n == 0u || n >= sizeof num) return 0;
        memcpy(num, eq + 1, n);
        num[n] = 0;
        /* Digits and one '.', no sign or exponent (locale-free). */
        for (i = 0; num[i]; ++i) {
            if (num[i] == '.') {
                if (scale != 0.0) return 0;
                scale = 1.0;
            } else if (num[i] >= '0' && num[i] <= '9') {
                if (scale != 0.0) {
                    scale /= 10.0;
                    v += (num[i] - '0') * scale;
                } else {
                    v = v * 10.0 + (num[i] - '0');
                }
            } else {
                return 0;
            }
        }
        if (!(v >= k_openspm_roster_prop_min[found] && v <= k_openspm_roster_prop_max[found])) return 0;
        out[found] = (float)v;
        *set |= 1u << found;
        m = *end ? end + 1 : end;
    }
    return 1;
}

typedef struct openspm_roster_io {
    void *context;
    int (*read32)(void *context, uint32_t address, uint32_t *value);
    int (*write32)(void *context, uint32_t address, uint32_t value);
    int (*call)(void *context, uint32_t target, uint32_t argc, const uint32_t *args);
    int (*scratch)(void *context, uint32_t size, uint32_t *address);
    int (*get_buttons)(void *context, uint32_t *held, uint32_t *pressed);
    int (*set_buttons)(void *context, uint32_t held);
    void (*log)(void *context, int warn, const char *text);
} openspm_roster_io;

typedef struct openspm_roster_character {
    char key[48], name[32], description[256], model[32];
    uint32_t base, flags, icon_item;
    char anim_map[256];
    char voice[256];
    uint32_t str_voice[3]; /* damage, acro, attack: guest sound name, 0 = the base's */
    float prop[4];        /* width, height, walk, dash (characterProperties +0/+4/+8/+12) */
    uint32_t prop_set;    /* bit i: prop[i] replaces the base's */
    uint32_t item_id;                       /* own item (char_items), else 0 */
    uint32_t alias_buf, alias_cap, alias_header; /* model animation names for the base character */
    int own_names_logged; /* the model's own animation names went to the log */
    uint32_t str_model, key_name, key_desc; /* guest addresses (after build) */
    uint32_t game_id; /* the game's character id with real ids (4, 8, ... 124), else 0 */
} openspm_roster_character;

typedef struct openspm_roster_pixl {
    char key[48], name[32], description[256], model[32], menu_anim[16];
    uint32_t icon_item;
    uint32_t item_id;
    uint32_t key_name, key_desc, str_key, str_model, str_anim, slot; /* guest (after build) */
} openspm_roster_pixl;

typedef struct openspm_roster {
    openspm_roster_character chars[OPENSPM_CONTENT_MAX_CHARACTERS];
    openspm_roster_pixl pixls[OPENSPM_CONTENT_MAX_PIXLS];
    uint32_t pixl_count;
    int pixls_ready; /* item table relocated and our items written */
    int char_items;  /* characters have their own items (relocated table) */
    uint32_t pixl_capacity, pixl_list_entries, fairy_reset_script;
    int32_t pixl_active, pixl_pending;
    int pixl_mask2;
    uint32_t pixl_first, pixl_listed;
    uint32_t count;
    int closed, disabled, built;
    uint32_t capacity;
    /* guest data */
    uint32_t msg_handle, msg_base, msg_count, msg_size;
    uint32_t reload_script[4];
    uint32_t orig_model[4], orig_preload[4];
    /* Where the per-character tables are in this run: in MEM2 when the
       character-id relocations are live (docs/CHARACTER-IDS.md), else where
       the game has them. */
    uint32_t t_model, t_preload, t_props, t_voice[3];
    /* Real character ids (docs/CHARACTER-IDS.md): each added character is its
       own id in the game, with its own rows in the moved tables. Off when the
       moves are not live (an older module) or with OPENSPM_CHARACTER_SLOTS=1
       (slots_only): then characters play on a base character's slot. */
    int real_ids, slots_only;
    uint32_t t_model2, t_dot, t_pipe, t_landing;
    uint32_t orig_voice[3][4][4]; /* kind, character, slot */
    int voice_ok;
    uint32_t orig_prop[4][4]; /* character, property (f32 bits) */
    int prop_ok;
    uint32_t orig_item_name, orig_item_desc;
    int item_patched;
    int32_t item_char;
    /* menu */
    uint32_t list_entries;
    uint8_t entry_char[OPENSPM_ROSTER_MAX_LIST]; /* list index -> roster index + 1 */
    uint32_t last_cursor;
    int mask2, inject2;
    /* play */
    int32_t active, pending, tables_char, reload_base;
    int pending_was_current;
    uint32_t pending_frames;
    uint32_t pick_item, pick_frames; /* real ids: the picked entry's item, until the menu's copy is his id */
    uint32_t activations;
    uint32_t diag_frames, diag_logs; /* animation diagnostic while an added character plays */
    uint32_t hitbox_frames;          /* frames since the switch, until the applied hitbox is logged */
    uint32_t change_wait, change_base; /* our character transition: frames left for move 71 to start */
    uint32_t pixl_old_item;            /* the game Pixl out when one of ours was picked (0: none) */
} openspm_roster;

static inline void openspm_roster_init(openspm_roster *r) {
    memset(r, 0, sizeof *r);
    r->t_model = SPM_R8PE01_CHAR_MODEL_TABLE;
    r->t_preload = SPM_R8PE01_CHAR_PRELOAD_TABLE;
    r->t_props = SPM_R8PE01_CHAR_PROPERTIES;
    r->t_voice[0] = SPM_R8PE01_VOICE_DAMAGE_TABLE;
    r->t_voice[1] = SPM_R8PE01_VOICE_ACRO_TABLE;
    r->t_voice[2] = SPM_R8PE01_VOICE_ATTACK_TABLE;
    r->t_model2 = SPM_R8PE01_CHAR_MODEL_TABLE_EXTRA;
    r->t_dot = OPENSPM_ROSTER_DOT_MODEL_TABLE;
    r->t_pipe = OPENSPM_ROSTER_PIPE_MODEL_TABLE;
    r->t_landing = OPENSPM_ROSTER_LANDING_TABLE;
    r->active = r->pending = r->tables_char = r->reload_base = r->item_char = -1;
    r->pixl_active = r->pixl_pending = -1;
    r->last_cursor = UINT32_MAX;
}

/* ---- registration ------------------------------------------------------- */
static inline int openspm_roster_text_ok(const char *s, size_t min, size_t max, int kind) {
    size_t n;
    if (s == NULL) return min == 0u;
    n = strlen(s);
    if (n < min || n > max) return 0;
    for (; *s; ++s) {
        const char c = *s;
        const int lower = (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_';
        if (kind == 0 && !(lower || c == '.' || c == ':' || c == '-')) return 0; /* key */
        if (kind == 1 && !lower) return 0;                                          /* model */
        if (kind == 2 && (c < 0x20 || c > 0x7E)) return 0;                          /* text */
    }
    return 1;
}

static inline int openspm_roster_register(openspm_roster *r, const openspm_character_def *def, uint32_t *out_id,
                                          char *why, size_t why_size) {
    uint32_t i;
    openspm_roster_character *c;
    uint32_t icon_item = 0u, prop_set = 0u;
    float prop[4] = {0.0f, 0.0f, 0.0f, 0.0f};
    if (def == NULL || def->header.struct_size < OPENSPM_CHARACTER_DEF_V1_SIZE) {
        snprintf(why, why_size, "definition too small (older header?)");
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    }
    if (def->header.struct_size >= offsetof(openspm_character_def, icon_item) + sizeof(uint32_t))
        icon_item = def->icon_item;
    if (def->header.struct_size >= offsetof(openspm_character_def, anim_map) + sizeof(def->anim_map) &&
        def->anim_map != NULL) {
        const char *m = def->anim_map;
        size_t n = 0;
        for (; m[n]; ++n)
            if (n >= 255u || !((m[n] >= 'a' && m[n] <= 'z') || (m[n] >= 'A' && m[n] <= 'Z') ||
                               (m[n] >= '0' && m[n] <= '9') || m[n] == '_' || m[n] == '=' || m[n] == ',')) {
                snprintf(why, why_size, "%s: anim_map: 0-255 of [A-Za-z0-9_=,]", def->key ? def->key : "?");
                return OPENSPM_ERROR_INVALID_ARGUMENT;
            }
    }
    if (def->header.struct_size >= offsetof(openspm_character_def, voice) + sizeof(def->voice) && def->voice != NULL) {
        const char *m = def->voice;
        size_t n = 0;
        for (; m[n]; ++n)
            if (n >= 255u || !((m[n] >= 'a' && m[n] <= 'z') || (m[n] >= 'A' && m[n] <= 'Z') ||
                               (m[n] >= '0' && m[n] <= '9') || m[n] == '_' || m[n] == '=' || m[n] == ',')) {
                snprintf(why, why_size, "%s: voice: 0-255 of [A-Za-z0-9_=,]", def->key ? def->key : "?");
                return OPENSPM_ERROR_INVALID_ARGUMENT;
            }
    }
    if (def->header.struct_size >= offsetof(openspm_character_def, properties) + sizeof(def->properties)) {
        float props[4] = {0.0f, 0.0f, 0.0f, 0.0f};
        if (!openspm_roster_parse_props(def->properties, props, &prop_set)) {
            snprintf(why, why_size,
                     "%s: properties: width=4..80, height=8..100, walk=0.2..6, dash=0.2..10, split by ','",
                     def->key ? def->key : "?");
            return OPENSPM_ERROR_INVALID_ARGUMENT;
        }
        memcpy(prop, props, sizeof prop);
    }
    if (icon_item >= OPENSPM_CONTENT_FIRST_ITEM_ID) {
        snprintf(why, why_size, "%s: icon_item must be one of the game's items (0-537)", def->key ? def->key : "?");
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    }
    if (!openspm_roster_text_ok(def->key, 3u, 47u, 0) || strchr(def->key, ':') == NULL) {
        snprintf(why, why_size, "key must be \"<mod>:<name>\", 3-47 of [a-z0-9_.:-]");
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    }
    if (!openspm_roster_text_ok(def->name, 1u, 31u, 2) || !openspm_roster_text_ok(def->description, 0u, 255u, 2)) {
        snprintf(why, why_size, "%s: name 1-31 / description 0-255 printable ASCII", def->key);
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    }
    if (!openspm_roster_text_ok(def->model, 1u, 31u, 1) || def->base_character > 3u ||
        (def->flags & ~OPENSPM_CHARACTER_FLAGS_KNOWN) != 0u) {
        snprintf(why, why_size, "%s: model 1-31 of [a-z0-9_], base_character 0-3, flags OPENSPM_CHARACTER_*",
                 def->key);
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    }
    if (r->closed) {
        snprintf(why, why_size, "%s: characters register at startup, before the game runs", def->key);
        return OPENSPM_ERROR_RUNTIME;
    }
    for (i = 0; i < r->count; ++i)
        if (strcmp(r->chars[i].key, def->key) == 0) {
            snprintf(why, why_size, "%s: key already registered", def->key);
            return OPENSPM_ERROR_RUNTIME;
        }
    if (r->count >= OPENSPM_CONTENT_MAX_CHARACTERS) {
        snprintf(why, why_size, "%s: the roster is full", def->key);
        return OPENSPM_ERROR_RUNTIME;
    }
    c = &r->chars[r->count];
    memset(c, 0, sizeof *c);
    snprintf(c->key, sizeof c->key, "%s", def->key);
    snprintf(c->name, sizeof c->name, "%s", def->name);
    snprintf(c->description, sizeof c->description, "%s", def->description ? def->description : "");
    snprintf(c->model, sizeof c->model, "%s", def->model);
    /* '<' starts a message tag in the game's text: never pass one through. */
    for (i = 0; c->name[i]; ++i) if (c->name[i] == '<' || c->name[i] == '>') c->name[i] = ' ';
    for (i = 0; c->description[i]; ++i) if (c->description[i] == '<' || c->description[i] == '>') c->description[i] = ' ';
    c->base = def->base_character;
    c->flags = def->flags;
    c->icon_item = icon_item;
    if (def->header.struct_size >= offsetof(openspm_character_def, anim_map) + sizeof(def->anim_map) &&
        def->anim_map != NULL)
        snprintf(c->anim_map, sizeof c->anim_map, "%s", def->anim_map);
    if (def->header.struct_size >= offsetof(openspm_character_def, voice) + sizeof(def->voice) && def->voice != NULL)
        snprintf(c->voice, sizeof c->voice, "%s", def->voice);
    memcpy(c->prop, prop, sizeof c->prop);
    c->prop_set = prop_set;
    if (out_id) *out_id = OPENSPM_CONTENT_FIRST_CHARACTER_ID + r->count;
    ++r->count;
    return OPENSPM_OK;
}

static inline int openspm_roster_character_active(const openspm_roster *r, uint32_t id) {
    return id >= OPENSPM_CONTENT_FIRST_CHARACTER_ID && r->active >= 0 &&
           (uint32_t)r->active == id - OPENSPM_CONTENT_FIRST_CHARACTER_ID;
}

static inline int openspm_roster_register_pixl(openspm_roster *r, const openspm_pixl_def *def, uint32_t *out_id,
                                               char *why, size_t why_size) {
    uint32_t i;
    openspm_roster_pixl *p;
    if (def == NULL || def->header.struct_size < sizeof *def) {
        snprintf(why, why_size, "Pixl definition too small (older header?)");
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    }
    if (!openspm_roster_text_ok(def->key, 3u, 47u, 0) || strchr(def->key, ':') == NULL ||
        !openspm_roster_text_ok(def->name, 1u, 31u, 2) || !openspm_roster_text_ok(def->description, 0u, 255u, 2) ||
        !openspm_roster_text_ok(def->model, 1u, 31u, 1) ||
        (def->menu_anim && !openspm_roster_text_ok(def->menu_anim, 1u, 15u, 2)) || def->flags != 0u ||
        def->icon_item >= OPENSPM_CONTENT_FIRST_ITEM_ID) {
        snprintf(why, why_size, "%s: bad Pixl definition (key/name/description/model/menu_anim/icon_item)",
                 def->key ? def->key : "?");
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    }
    if (r->closed) {
        snprintf(why, why_size, "%s: Pixls register at startup, before the game runs", def->key);
        return OPENSPM_ERROR_RUNTIME;
    }
    for (i = 0; i < r->pixl_count; ++i)
        if (strcmp(r->pixls[i].key, def->key) == 0) {
            snprintf(why, why_size, "%s: key already registered", def->key);
            return OPENSPM_ERROR_RUNTIME;
        }
    if (r->pixl_count >= OPENSPM_CONTENT_MAX_PIXLS) {
        snprintf(why, why_size, "%s: no room for more Pixls", def->key);
        return OPENSPM_ERROR_RUNTIME;
    }
    p = &r->pixls[r->pixl_count];
    memset(p, 0, sizeof *p);
    snprintf(p->key, sizeof p->key, "%s", def->key);
    snprintf(p->name, sizeof p->name, "%s", def->name);
    snprintf(p->description, sizeof p->description, "%s", def->description ? def->description : "");
    snprintf(p->model, sizeof p->model, "%s", def->model);
    snprintf(p->menu_anim, sizeof p->menu_anim, "%s", def->menu_anim ? def->menu_anim : "S_1");
    for (i = 0; p->name[i]; ++i) if (p->name[i] == '<' || p->name[i] == '>') p->name[i] = ' ';
    for (i = 0; p->description[i]; ++i) if (p->description[i] == '<' || p->description[i] == '>') p->description[i] = ' ';
    p->icon_item = def->icon_item ? def->icon_item : OPENSPM_ROSTER_ITEM_PIXL_DEFAULT_ICON;
    p->item_id = OPENSPM_CONTENT_FIRST_ITEM_ID + r->pixl_count;
    if (out_id) *out_id = p->item_id;
    ++r->pixl_count;
    return OPENSPM_OK;
}

static inline int openspm_roster_pixl_active(const openspm_roster *r, uint32_t item_id) {
    return r->pixl_active >= 0 && r->pixls[r->pixl_active].item_id == item_id;
}

/* ---- guest helpers ---------------------------------------------------------- */
static inline void openspm_roster_log(const openspm_roster_io *io, int warn, const char *text) {
    if (io->log) io->log(io->context, warn, text);
}
static inline uint32_t openspm_roster_rd(const openspm_roster_io *io, uint32_t a, int *ok) {
    uint32_t v = 0u;
    if (io->read32(io->context, a, &v) != OPENSPM_OK) *ok = 0;
    return v;
}
static inline int openspm_roster_wr(const openspm_roster_io *io, uint32_t a, uint32_t v) {
    return io->write32(io->context, a, v) == OPENSPM_OK;
}
static inline int openspm_roster_str_is(const openspm_roster_io *io, uint32_t a, const char *want) {
    size_t n = strlen(want) + 1u, i;
    int ok = 1;
    for (i = 0; i < n; i += 4u) {
        const uint32_t w = openspm_roster_rd(io, a + (uint32_t)i, &ok);
        size_t k;
        if (!ok) return 0;
        for (k = 0; k < 4u && i + k < n; ++k)
            if ((char)(w >> (24u - 8u * k)) != want[i + k]) return 0;
    }
    return 1;
}
static inline uint32_t openspm_roster_put(const openspm_roster_io *io, const void *bytes, uint32_t n) {
    const uint8_t *b = (const uint8_t *)bytes;
    const uint32_t size = (n + 3u) & ~3u;
    uint32_t a = 0u, i;
    if (size == 0u || io->scratch(io->context, size, &a) != OPENSPM_OK) return 0u;
    for (i = 0; i < size; i += 4u) {
        uint32_t w = 0u, k;
        for (k = 0; k < 4u; ++k) w = w << 8 | (i + k < n ? b[i + k] : 0u);
        if (!openspm_roster_wr(io, a + i, w)) return 0u;
    }
    return a;
}
static inline void openspm_roster_set_slot(const openspm_roster_io *io, uint32_t slot, int selected) {
    int ok = 1;
    const uint32_t w = openspm_roster_rd(io, slot, &ok);
    if (ok) (void)openspm_roster_wr(io, slot, (w & 0xFF00FFFFu) | (selected ? 0x00010000u : 0u));
}

static inline int openspm_roster_start_script(const openspm_roster_io *io, uint32_t script) {
    const uint32_t args[3] = {script, 0u, 0u};
    return io->call(io->context, SPM_R8PE01_EVT_ENTRY, 3u, args) == OPENSPM_OK;
}

/* ---- build (first in-game frame) -------------------------------------------- */
static inline uint32_t openspm_roster_item(uint32_t id) {
    return OPENSPM_ITEM_DATA_RELOCATED + id * SPM_R8PE01_ITEM_DATA_SIZE;
}

/* The boot copy of the item table is in place (the rebuilt native module). */
static inline int openspm_roster_items_relocated(const openspm_roster_io *io) {
    int ok = 1;
    const uint32_t name216 = openspm_roster_rd(io, SPM_R8PE01_ITEM_DATA + 216u * SPM_R8PE01_ITEM_DATA_SIZE, &ok);
    const uint32_t moved216 = openspm_roster_rd(io, openspm_roster_item(216u), &ok);
    return ok && name216 != 0u && moved216 == name216;
}

/* Characters' own items, after the added Pixls': the base character's item
   with the character's name, description and icon. */
static inline void openspm_roster_build_char_items(openspm_roster *r, const openspm_roster_io *io) {
    int ok = 1;
    uint32_t i, k;
    if (!openspm_roster_items_relocated(io)) return;
    for (i = 0; i < r->count; ++i) {
        openspm_roster_character *c = &r->chars[i];
        const uint32_t id = OPENSPM_CONTENT_FIRST_ITEM_ID + r->pixl_count + i;
        const uint32_t dst = openspm_roster_item(id), base = openspm_roster_item(SPM_R8PE01_ITEM_CHARACTER_FIRST + c->base);
        for (k = 0; k < SPM_R8PE01_ITEM_DATA_SIZE; k += 4u)
            (void)openspm_roster_wr(io, dst + k, openspm_roster_rd(io, base + k, &ok));
        if (c->icon_item) {
            const uint32_t icon = openspm_roster_rd(io, openspm_roster_item(c->icon_item) + SPM_R8PE01_ITEM_ICON, &ok);
            (void)openspm_roster_wr(io, dst + SPM_R8PE01_ITEM_ICON,
                                    (icon & 0xFFFF0000u) | (openspm_roster_rd(io, dst + SPM_R8PE01_ITEM_ICON, &ok) & 0xFFFFu));
        }
        (void)openspm_roster_wr(io, dst + SPM_R8PE01_ITEM_NAME_MSG, c->key_name);
        (void)openspm_roster_wr(io, dst + SPM_R8PE01_ITEM_DESC_MSG, c->key_desc);
        c->item_id = id;
    }
    if (!ok) {
        for (i = 0; i < r->count; ++i) r->chars[i].item_id = 0u;
        return;
    }
    r->char_items = 1;
}

/* Pixls need the relocated item table (the build patches + the boot copy):
   their items go right after the game's 538. */
static inline void openspm_roster_build_pixls(openspm_roster *r, const openspm_roster_io *io) {
    char message[200];
    int ok = 1;
    uint32_t i, k, words[16], count;
    const uint32_t name216 = openspm_roster_rd(io, SPM_R8PE01_ITEM_DATA + 216u * SPM_R8PE01_ITEM_DATA_SIZE, &ok);
    const uint32_t moved216 = openspm_roster_rd(io, openspm_roster_item(216u), &ok);
    const uint32_t patch = openspm_roster_rd(io, OPENSPM_ROSTER_PIXLTAB_PATCH, &ok);
    const uint32_t reset = openspm_roster_rd(io, OPENSPM_ROSTER_EVT_FAIRY_RESET, &ok);
    spm_evt_builder b;
    if (!ok || name216 == 0u || moved216 != name216 || (reset & 0xFFFF0000u) != 0x94210000u) {
        openspm_roster_log(io, 1, "OpenSPM content: added Pixls need the rebuilt native module (item table "
                                  "relocation); they are off this run.");
        return;
    }
    r->pixl_capacity = patch == OPENSPM_ROSTER_PIXLTAB_PATCHED ? OPENSPM_PIXLTAB_CAPACITY
                                                                : OPENSPM_ROSTER_PIXLTAB_VANILLA_CAPACITY;
    for (i = 0; i < r->pixl_count; ++i) {
        const openspm_roster_pixl *px = &r->pixls[i];
        const uint32_t dst = openspm_roster_item(px->item_id), tmpl = openspm_roster_item(OPENSPM_ROSTER_PIXL_STANDIN);
        const uint32_t icon = openspm_roster_rd(io, openspm_roster_item(px->icon_item) + SPM_R8PE01_ITEM_ICON, &ok);
        for (k = 0; k < SPM_R8PE01_ITEM_DATA_SIZE; k += 4u)
            (void)openspm_roster_wr(io, dst + k, openspm_roster_rd(io, tmpl + k, &ok));
        (void)openspm_roster_wr(io, dst + SPM_R8PE01_ITEM_NAME, px->str_key);
        (void)openspm_roster_wr(io, dst + SPM_R8PE01_ITEM_ICON,
                                (icon & 0xFFFF0000u) | (openspm_roster_rd(io, dst + SPM_R8PE01_ITEM_ICON, &ok) & 0xFFFFu));
        (void)openspm_roster_wr(io, dst + 0x08u, px->str_model);
        (void)openspm_roster_wr(io, dst + 0x0Cu, px->str_anim);
        (void)openspm_roster_wr(io, dst + SPM_R8PE01_ITEM_NAME_MSG, px->key_name);
        (void)openspm_roster_wr(io, dst + SPM_R8PE01_ITEM_DESC_MSG, px->key_desc);
        (void)openspm_roster_wr(io, dst + 0x18u, 0u); /* no buy / sell price */
    }
    spm_evt_init(&b, words, 16u);
    spm_evt_user_func(&b, OPENSPM_ROSTER_EVT_FAIRY_RESET, 0u, NULL);
    count = spm_evt_finish(&b);
    {
        uint8_t raw[64];
        for (k = 0; k < count; ++k) {
            raw[4u * k] = (uint8_t)(words[k] >> 24); raw[4u * k + 1u] = (uint8_t)(words[k] >> 16);
            raw[4u * k + 2u] = (uint8_t)(words[k] >> 8); raw[4u * k + 3u] = (uint8_t)words[k];
        }
        r->fairy_reset_script = count ? openspm_roster_put(io, raw, count * 4u) : 0u;
    }
    if (!ok || !r->fairy_reset_script) {
        openspm_roster_log(io, 1, "OpenSPM content: could not write the added Pixls' items; they are off.");
        return;
    }
    r->pixls_ready = 1;
    snprintf(message, sizeof message, "OpenSPM content: %u added Pixl(s) as items %u-%u; Pixl list holds %u entries.",
             (unsigned)r->pixl_count, (unsigned)r->pixls[0].item_id,
             (unsigned)r->pixls[r->pixl_count - 1u].item_id, (unsigned)r->pixl_capacity);
    openspm_roster_log(io, 0, message);
}

/* A table's address in this run: its MEM2 home when the patch named `probe`
   (one of its lis recipes) is in the code, else the game's own address. */
static inline uint32_t openspm_roster_table_home(const openspm_roster_io *io, uint32_t table, const char *probe) {
    const openspm_code_patch *p = openspm_code_patch_named(probe);
    const openspm_code_relocation *reloc = openspm_code_relocation_of(table);
    int ok = 1;
    if (p == NULL || reloc == NULL || openspm_roster_rd(io, p->address, &ok) != p->replacement || !ok) return table;
    return reloc->to;
}

static inline void openspm_roster_find_tables(openspm_roster *r, const openspm_roster_io *io) {
    r->t_model = openspm_roster_table_home(io, SPM_R8PE01_CHAR_MODEL_TABLE, "char-model-00");
    r->t_preload = openspm_roster_table_home(io, SPM_R8PE01_CHAR_PRELOAD_TABLE, "char-preload-00");
    r->t_props = openspm_roster_table_home(io, SPM_R8PE01_CHAR_PROPERTIES, "char-properties-00");
    r->t_voice[0] = openspm_roster_table_home(io, SPM_R8PE01_VOICE_DAMAGE_TABLE, "char-voice-damage-00");
    r->t_voice[1] = openspm_roster_table_home(io, SPM_R8PE01_VOICE_ACRO_TABLE, "char-voice-acro-00");
    r->t_voice[2] = openspm_roster_table_home(io, SPM_R8PE01_VOICE_ATTACK_TABLE, "char-voice-attack-00");
    r->t_model2 = openspm_roster_table_home(io, SPM_R8PE01_CHAR_MODEL_TABLE_EXTRA, "char-model2-00");
    r->t_dot = openspm_roster_table_home(io, OPENSPM_ROSTER_DOT_MODEL_TABLE, "char-dot-model-00");
    r->t_pipe = openspm_roster_table_home(io, OPENSPM_ROSTER_PIPE_MODEL_TABLE, "char-pipe-model-00");
    r->t_landing = openspm_roster_table_home(io, OPENSPM_ROSTER_LANDING_TABLE, "char-landing-00");
}

/* All ten tables moved, in this run. */
static inline int openspm_roster_tables_moved(const openspm_roster *r) {
    return r->t_model != SPM_R8PE01_CHAR_MODEL_TABLE && r->t_preload != SPM_R8PE01_CHAR_PRELOAD_TABLE &&
           r->t_props != SPM_R8PE01_CHAR_PROPERTIES && r->t_voice[0] != SPM_R8PE01_VOICE_DAMAGE_TABLE &&
           r->t_voice[1] != SPM_R8PE01_VOICE_ACRO_TABLE && r->t_voice[2] != SPM_R8PE01_VOICE_ATTACK_TABLE &&
           r->t_model2 != SPM_R8PE01_CHAR_MODEL_TABLE_EXTRA && r->t_dot != OPENSPM_ROSTER_DOT_MODEL_TABLE &&
           r->t_pipe != OPENSPM_ROSTER_PIPE_MODEL_TABLE && r->t_landing != OPENSPM_ROSTER_LANDING_TABLE;
}

static inline void openspm_roster_copy_row(const openspm_roster_io *io, uint32_t table, uint32_t row_size,
                                           uint32_t from, uint32_t to) {
    int ok = 1;
    uint32_t k;
    for (k = 0; k < row_size; k += 4u)
        (void)openspm_roster_wr(io, table + row_size * to + k, openspm_roster_rd(io, table + row_size * from + k, &ok));
}

/* Real ids: each character's rows, from his base character's and his own
   definition (model, voice, size and speed). */
static inline void openspm_roster_write_rows(openspm_roster *r, const openspm_roster_io *io) {
    uint32_t i, v, s, f;
    char message[320];
    size_t used;
    for (i = 0; i < r->count; ++i) {
        openspm_roster_character *c = &r->chars[i];
        const uint32_t id = c->game_id, b = c->base;
        (void)openspm_roster_wr(io, r->t_model + 8u * id, c->str_model);      /* his model, also with a Mega Star */
        (void)openspm_roster_wr(io, r->t_model + 8u * id + 4u, c->str_model);
        /* The base's second model (Mario's p_wii_mario_r, NULL for the
           others): with his id the model loader takes the base's branch,
           which looks up parts in that model (0x80121AB4) and asserts on a
           missing one. */
        openspm_roster_copy_row(io, r->t_model2, 8u, b, id);
        (void)openspm_roster_wr(io, r->t_preload + 8u * id, c->str_model);    /* what the menu preloads */
        (void)openspm_roster_wr(io, r->t_preload + 8u * id + 4u, 0u);
        openspm_roster_copy_row(io, r->t_dot, 8u, b, id);
        openspm_roster_copy_row(io, r->t_pipe, 4u, b, id);
        openspm_roster_copy_row(io, r->t_landing, 4u, b, id);
        for (v = 0; v < 3u; ++v) {
            const uint32_t n = k_openspm_roster_voice_count[v];
            openspm_roster_copy_row(io, r->t_voice[v], 4u * n, b, id);
            if (c->str_voice[v])
                for (s = 0; s < n; ++s) (void)openspm_roster_wr(io, r->t_voice[v] + 4u * (n * id + s), c->str_voice[v]);
        }
        openspm_roster_copy_row(io, r->t_props, SPM_R8PE01_CHAR_PROPERTIES_SIZE, b, id);
        for (f = 0; f < 4u; ++f)
            if (c->prop_set & (1u << f)) {
                uint32_t bits;
                memcpy(&bits, &c->prop[f], sizeof bits);
                (void)openspm_roster_wr(io, r->t_props + SPM_R8PE01_CHAR_PROPERTIES_SIZE * id + 4u * f, bits);
            }
    }
    used = (size_t)snprintf(message, sizeof message, "OpenSPM content: real character ids:");
    for (i = 0; i < r->count && used < sizeof message; ++i)
        used += (size_t)snprintf(message + used, sizeof message - used, "%s %s = %u", i ? "," : "", r->chars[i].name,
                                 (unsigned)r->chars[i].game_id);
    openspm_roster_log(io, 0, message);
}

static inline int openspm_roster_build(openspm_roster *r, const openspm_roster_io *io) {
    static const char *const base_models[4] = {"p_wii_mario", "p_wii_peach", "p_wii_kuppa", "p_wii_luigi"};
    static const uint32_t functions[3] = {SPM_R8PE01_EVT_ENTRY, SPM_R8PE01_EVT_MARIO_SET_CHARACTER,
                                          OPENSPM_ROSTER_PAUSEWIN_SET_MESSAGE};
    char message[320];
    int ok = 1;
    uint32_t i, j, word;
    const uint32_t item = SPM_R8PE01_ITEM_DATA + SPM_R8PE01_ITEM_PLACEHOLDER * SPM_R8PE01_ITEM_DATA_SIZE;
    openspm_roster_find_tables(r, io);
    for (i = 0; i < 3u; ++i)
        if ((openspm_roster_rd(io, functions[i], &ok) & 0xFFFF0000u) != 0x94210000u) ok = 0;
    for (i = 0; i < 4u && ok; ++i) {
        r->orig_model[i] = openspm_roster_rd(io, r->t_model + 8u * i, &ok);
        r->orig_preload[i] = openspm_roster_rd(io, r->t_preload + 8u * i, &ok);
        if (ok && (!openspm_roster_str_is(io, r->orig_model[i], base_models[i]) ||
                   !openspm_roster_str_is(io, r->orig_preload[i], base_models[i])))
            ok = 0;
    }
    if (ok && !openspm_roster_str_is(io, openspm_roster_rd(io, item + SPM_R8PE01_ITEM_NAME, &ok), "ICON")) ok = 0;
    {
        /* Voice tables: only swapped when every row reads as that
           character's voice (a quiet fallback to the base's voice). */
        static const char *const who[4] = {"MARIO", "PEACH", "KOOPA", "LUIGI"};
        static const char *const kind[3] = {"DAMEGE", "ACRO", "ATTACK"};
        uint32_t v, ch, s;
        int vok = ok;
        char want[40];
        for (v = 0; v < 3u && vok; ++v)
            for (ch = 0; ch < 4u && vok; ++ch)
                for (s = 0; s < k_openspm_roster_voice_count[v] && vok; ++s) {
                    r->orig_voice[v][ch][s] = openspm_roster_rd(
                        io, r->t_voice[v] + 4u * (ch * k_openspm_roster_voice_count[v] + s), &vok);
                    snprintf(want, sizeof want, "SFX_P_V_%s_%s%u", who[ch], kind[v], (unsigned)s + 1u);
                    if (vok && !openspm_roster_str_is(io, r->orig_voice[v][ch][s], want)) vok = 0;
                }
        r->voice_ok = vok;
    }
    {
        /* characterProperties: only swapped when all four rows read as
           plausible sizes and speeds (else characters keep the base's). */
        uint32_t ch, f;
        int pok = ok;
        for (ch = 0; ch < 4u && pok; ++ch)
            for (f = 0; f < 4u && pok; ++f) {
                float v;
                r->orig_prop[ch][f] = openspm_roster_rd(
                    io, r->t_props + SPM_R8PE01_CHAR_PROPERTIES_SIZE * ch + 4u * f, &pok);
                memcpy(&v, &r->orig_prop[ch][f], sizeof v);
                if (pok && !(v >= 0.1f && v <= 200.0f)) pok = 0;
            }
        r->prop_ok = pok;
    }
    r->orig_item_name = openspm_roster_rd(io, item + SPM_R8PE01_ITEM_NAME_MSG, &ok);
    r->orig_item_desc = openspm_roster_rd(io, item + SPM_R8PE01_ITEM_DESC_MSG, &ok);
    if (!ok) {
        r->disabled = 1;
        openspm_roster_log(io, 1, "OpenSPM content: this game is not R8PE01 rev 2 (character tables differ); "
                                  "added characters are off.");
        return 0;
    }
    word = openspm_roster_rd(io, k_openspm_code_patches[0].address, &ok);
    r->capacity = word == k_openspm_code_patches[0].replacement ? OPENSPM_CHARTAB_CAPACITY
                                                                 : SPM_R8PE01_CHARTAB_LIST_CAPACITY;
    /* Strings and the message table: keys openspm_chr_NN_d / _n, then
       openspm_pxl_NN_d / _n ("chr" < "pxl": the table stays sorted). */
    {
        enum { TOTAL = OPENSPM_CONTENT_MAX_CHARACTERS + OPENSPM_CONTENT_MAX_PIXLS };
        static char blob[TOTAL * 2u * 300u];
        static uint32_t offsets[TOTAL * 4u];
        static uint8_t raw[TOTAL * 4u * 4u];
        uint32_t used = 0u, n = 0u;
        for (i = 0; i < r->count + r->pixl_count; ++i) {
            const int pixl = i >= r->count;
            const uint32_t k = pixl ? i - r->count : i;
            const char *desc = pixl ? r->pixls[k].description : r->chars[k].description;
            const char *name = pixl ? r->pixls[k].name : r->chars[k].name;
            int w;
            for (j = 0; j < 2u; ++j) { /* d before n: sorted */
                offsets[n * 2u] = used;
                w = snprintf(blob + used, sizeof blob - used, "openspm_%s_%02u_%c", pixl ? "pxl" : "chr", (unsigned)k,
                             j == 0u ? 'd' : 'n');
                used += (uint32_t)w + 1u;
                offsets[n * 2u + 1u] = used;
                w = snprintf(blob + used, sizeof blob - used, "%s", j == 0u ? desc : name);
                used += (uint32_t)w + 1u;
                ++n;
            }
        }
        r->msg_base = openspm_roster_put(io, blob, used);
        r->msg_size = used;
        r->msg_count = n;
        for (i = 0; i < n * 2u; ++i) {
            raw[4u * i] = (uint8_t)(offsets[i] >> 24); raw[4u * i + 1u] = (uint8_t)(offsets[i] >> 16);
            raw[4u * i + 2u] = (uint8_t)(offsets[i] >> 8); raw[4u * i + 3u] = (uint8_t)offsets[i];
        }
        word = openspm_roster_put(io, raw, n * 8u);
        {
            uint8_t handle[4] = {(uint8_t)(word >> 24), (uint8_t)(word >> 16), (uint8_t)(word >> 8), (uint8_t)word};
            r->msg_handle = word ? openspm_roster_put(io, handle, 4u) : 0u;
        }
        for (i = 0; i < r->count; ++i) {
            r->chars[i].key_desc = r->msg_base + offsets[4u * i];
            r->chars[i].key_name = r->msg_base + offsets[4u * i + 2u];
            r->chars[i].str_model = openspm_roster_put(io, r->chars[i].model, (uint32_t)strlen(r->chars[i].model) + 1u);
            {
                /* Voice kinds: "damage=SFX_...,acro=none" ("none": a name no
                   sound archive has, so the game plays nothing). */
                openspm_roster_character *c = &r->chars[i];
                uint32_t v;
                for (v = 0; v < 3u; ++v) {
                    const size_t kn = strlen(k_openspm_roster_voice_kind[v]);
                    const char *m = c->voice;
                    c->str_voice[v] = 0u;
                    while (*m) {
                        const char *end = strchr(m, ',');
                        if (end == NULL) end = m + strlen(m);
                        if ((size_t)(end - m) > kn + 1u && strncmp(m, k_openspm_roster_voice_kind[v], kn) == 0 &&
                            m[kn] == '=') {
                            char sound[64];
                            const size_t len = (size_t)(end - m) - kn - 1u < sizeof sound - 1u
                                                   ? (size_t)(end - m) - kn - 1u
                                                   : sizeof sound - 1u;
                            memcpy(sound, m + kn + 1u, len);
                            sound[len] = 0;
                            if (strcmp(sound, "none") == 0) snprintf(sound, sizeof sound, "SFX_OPENSPM_SILENT");
                            c->str_voice[v] = openspm_roster_put(io, sound, (uint32_t)strlen(sound) + 1u);
                        }
                        m = *end ? end + 1 : end;
                    }
                }
            }
            if (!r->chars[i].str_model) r->msg_handle = 0u;
        }
        for (i = 0; i < r->pixl_count; ++i) {
            openspm_roster_pixl *px = &r->pixls[i];
            const uint32_t m = r->count + i;
            px->key_desc = r->msg_base + offsets[4u * m];
            px->key_name = r->msg_base + offsets[4u * m + 2u];
            px->str_key = openspm_roster_put(io, px->key, (uint32_t)strlen(px->key) + 1u);
            px->str_model = openspm_roster_put(io, px->model, (uint32_t)strlen(px->model) + 1u);
            px->str_anim = openspm_roster_put(io, px->menu_anim, (uint32_t)strlen(px->menu_anim) + 1u);
            {
                /* the slot the Pixl tab entry points at: enabled, not selected */
                const uint8_t slot[4] = {1u, 0u, (uint8_t)(px->item_id >> 8), (uint8_t)px->item_id};
                px->slot = openspm_roster_put(io, slot, 4u);
            }
            if (!px->str_key || !px->str_model || !px->str_anim || !px->slot) r->msg_handle = 0u;
        }
    }
    /* Reload a base character's model on the game's own thread:
           USER_FUNC evt_mario_set_character, <other>
           USER_FUNC evt_mario_set_character, <base>   */
    for (i = 0; i < 4u; ++i) {
        uint32_t words[12], count;
        spm_evt_builder b;
        const uint32_t other = i == 0u ? 1u : 0u;
        spm_evt_init(&b, words, 12u);
        spm_evt_user_func(&b, SPM_R8PE01_EVT_MARIO_SET_CHARACTER, 1u, &other);
        spm_evt_user_func(&b, SPM_R8PE01_EVT_MARIO_SET_CHARACTER, 1u, &i);
        count = spm_evt_finish(&b);
        r->reload_script[i] = 0u;
        if (count) {
            uint8_t raw[48];
            for (j = 0; j < count; ++j) {
                raw[4u * j] = (uint8_t)(words[j] >> 24); raw[4u * j + 1u] = (uint8_t)(words[j] >> 16);
                raw[4u * j + 2u] = (uint8_t)(words[j] >> 8); raw[4u * j + 3u] = (uint8_t)words[j];
            }
            r->reload_script[i] = openspm_roster_put(io, raw, count * 4u);
        }
        if (!r->reload_script[i]) r->msg_handle = 0u;
    }
    if (!r->msg_base || !r->msg_handle) {
        r->disabled = 1;
        openspm_roster_log(io, 1, "OpenSPM content: no scratch memory for the roster; added characters are off.");
        return 0;
    }
    r->built = 1;
    if (r->count && !r->slots_only && openspm_roster_tables_moved(r) && r->count <= OPENSPM_ROSTER_MAX_REAL_IDS) {
        for (i = 0; i < r->count; ++i) r->chars[i].game_id = 4u * (i + 1u);
        r->real_ids = 1;
        openspm_roster_write_rows(r, io);
    } else if (r->count) {
        openspm_roster_log(io, 0, r->slots_only ? "OpenSPM content: added characters play on a game character's slot "
                                                  "(OPENSPM_CHARACTER_SLOTS=1)."
                                                : "OpenSPM content: this module has no real character ids (rebuild it); "
                                                  "added characters play on a game character's slot.");
    }
    if (r->count) {
        snprintf(message, sizeof message,
                 "OpenSPM content: %u added character(s); character list holds %u entries%s.", (unsigned)r->count,
                 (unsigned)r->capacity,
                 r->capacity > SPM_R8PE01_CHARTAB_LIST_CAPACITY ? "" : " (rebuild the native module for more than one)");
        openspm_roster_log(io, 0, message);
        for (i = 0; i < r->count; ++i)
            if (r->chars[i].prop_set && r->prop_ok) {
                /* His values next to the base character's (the game's). */
                const openspm_roster_character *c = &r->chars[i];
                float base[4], own[4];
                uint32_t k;
                for (k = 0; k < 4u; ++k) {
                    memcpy(&base[k], &r->orig_prop[c->base][k], sizeof base[k]);
                    own[k] = (c->prop_set >> k & 1u) ? c->prop[k] : base[k];
                }
                snprintf(message, sizeof message,
                         "OpenSPM content: %s has its own size / speed: width %.1f (base %.1f), height %.1f (base "
                         "%.1f), walk %.2f (base %.2f), dash %.2f (base %.2f).",
                         c->key, (double)own[0], (double)base[0], (double)own[1], (double)base[1], (double)own[2],
                         (double)base[2], (double)own[3], (double)base[3]);
                openspm_roster_log(io, 0, message);
            } else if (r->chars[i].prop_set) {
                snprintf(message, sizeof message,
                         "OpenSPM content: %s: the game's character properties do not read as expected; he keeps "
                         "the base character's size and speed.",
                         r->chars[i].key);
                openspm_roster_log(io, 1, message);
            }
    }
    if (r->pixl_count) openspm_roster_build_pixls(r, io);
    if (r->count) openspm_roster_build_char_items(r, io);
    return 1;
}

/* ---- tables -------------------------------------------------------------------- */

static inline void openspm_roster_restore_tables(openspm_roster *r, const openspm_roster_io *io) {
    uint32_t i, k, s;
    if (r->tables_char < 0) return;
    for (i = 0; i < 4u; ++i) {
        (void)openspm_roster_wr(io, r->t_model + 8u * i, r->orig_model[i]);
        (void)openspm_roster_wr(io, r->t_preload + 8u * i, r->orig_preload[i]);
    }
    if (r->voice_ok)
        for (k = 0; k < 3u; ++k)
            for (i = 0; i < 4u; ++i)
                for (s = 0; s < k_openspm_roster_voice_count[k]; ++s)
                    (void)openspm_roster_wr(io, r->t_voice[k] + 4u * (i * k_openspm_roster_voice_count[k] + s),
                                            r->orig_voice[k][i][s]);
    if (r->prop_ok)
        for (i = 0; i < 4u; ++i)
            for (k = 0; k < 4u; ++k)
                (void)openspm_roster_wr(io, r->t_props + SPM_R8PE01_CHAR_PROPERTIES_SIZE * i + 4u * k,
                                        r->orig_prop[i][k]);
    r->tables_char = -1;
}
static inline void openspm_roster_swap_tables(openspm_roster *r, const openspm_roster_io *io, int32_t k) {
    const openspm_roster_character *c = &r->chars[k];
    openspm_roster_restore_tables(r, io);
    (void)openspm_roster_wr(io, r->t_model + 8u * c->base, c->str_model);
    (void)openspm_roster_wr(io, r->t_preload + 8u * c->base, c->str_model);
    if (r->voice_ok) {
        uint32_t v, s;
        for (v = 0; v < 3u; ++v)
            if (c->str_voice[v])
                for (s = 0; s < k_openspm_roster_voice_count[v]; ++s)
                    (void)openspm_roster_wr(io,
                                            r->t_voice[v] +
                                                4u * (c->base * k_openspm_roster_voice_count[v] + s),
                                            c->str_voice[v]);
    }
    if (r->prop_ok) {
        /* The game copies the row into MarioWork when it switches to the base
           character (0x801220C4), which comes after this swap. */
        uint32_t f, bits;
        for (f = 0; f < 4u; ++f)
            if (c->prop_set & (1u << f)) {
                memcpy(&bits, &c->prop[f], sizeof bits);
                (void)openspm_roster_wr(
                    io, r->t_props + SPM_R8PE01_CHAR_PROPERTIES_SIZE * c->base + 4u * f, bits);
            }
    }
    r->tables_char = k;
}
static inline void openspm_roster_restore_item(openspm_roster *r, const openspm_roster_io *io) {
    const uint32_t item = SPM_R8PE01_ITEM_DATA + SPM_R8PE01_ITEM_PLACEHOLDER * SPM_R8PE01_ITEM_DATA_SIZE;
    if (!r->item_patched) return;
    (void)openspm_roster_wr(io, item + SPM_R8PE01_ITEM_NAME_MSG, r->orig_item_name);
    (void)openspm_roster_wr(io, item + SPM_R8PE01_ITEM_DESC_MSG, r->orig_item_desc);
    r->item_patched = 0;
    r->item_char = -1;
}

static inline void openspm_roster_install_messages(openspm_roster *r, const openspm_roster_io *io) {
    int ok = 1;
    uint32_t i, free_slot = 0u;
    const uint32_t work = openspm_roster_rd(io, OPENSPM_ROSTER_MSG_WORK_PTR, &ok);
    if (!ok || work == 0u) return;
    for (i = 1; i < OPENSPM_ROSTER_MSG_TABLES; ++i) {
        const uint32_t slot = work + 16u * i;
        const uint32_t handle = openspm_roster_rd(io, slot + 12u, &ok);
        if (!ok) return;
        if (handle == r->msg_handle) return; /* still there */
        if (handle == 0u) free_slot = slot;   /* keep the highest free one */
    }
    if (free_slot == 0u) return; /* every table in use by this map: names fall back */
    (void)openspm_roster_wr(io, free_slot + 0u, r->msg_size);
    (void)openspm_roster_wr(io, free_slot + 4u, r->msg_base);
    (void)openspm_roster_wr(io, free_slot + 8u, r->msg_count);
    (void)openspm_roster_wr(io, free_slot + 12u, r->msg_handle);
}

/* Leaves every game table as the game had it. */
static inline void openspm_roster_reset(openspm_roster *r, const openspm_roster_io *io) {
    uint32_t i;
    for (i = 0; i < r->pixl_count && r->built; ++i)
        if (r->pixls[i].slot) openspm_roster_set_slot(io, r->pixls[i].slot, 0);
    r->pixl_active = r->pixl_pending = -1;
    r->pixl_list_entries = 0u;
    r->pixl_mask2 = 0;
    r->pick_item = 0u;
    openspm_roster_restore_tables(r, io);
    openspm_roster_restore_item(r, io);
    r->active = r->pending = r->reload_base = -1;
    r->change_wait = 0u;
    r->list_entries = 0u;
    r->mask2 = r->inject2 = 0;
    r->last_cursor = UINT32_MAX;
}

/* ---- the game's switch transitions ----------------------------------------------------- */

/* Plain play: no transition or special state the game's requests treat
   differently. */
static inline int openspm_roster_mario_plain(const openspm_roster_io *io) {
    int ok = 1;
    const uint32_t f0 = openspm_roster_rd(io, SPM_R8PE01_MARIO_WORK, &ok);
    const uint32_t f4 = openspm_roster_rd(io, SPM_R8PE01_MARIO_WORK + 4u, &ok);
    const uint32_t motion = openspm_roster_rd(io, SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_MOTION_ID, &ok) >> 16;
    return ok && !(f0 & (OPENSPM_ROSTER_MARIO_LOCKED | OPENSPM_ROSTER_MARIO_SPECIAL0)) &&
           !(f4 & OPENSPM_ROSTER_MARIO_SPECIAL4) && motion != 0x26u && motion != 0x29u &&
           motion != OPENSPM_ROSTER_MOT_CHARACTER_CHANGE;
}

/* The flip to `base` (already the current character): what 0x80146DB0 does
   for another character, which it refuses for the current one. The model
   comes from the tables as they are (ours swapped in, or the game's back).
   0 when not in plain play (the caller reloads without the flip). */
static inline int openspm_roster_start_character_change(openspm_roster *r, const openspm_roster_io *io, uint32_t base) {
    const uint32_t motion[2] = {OPENSPM_ROSTER_MOT_CHARACTER_CHANGE, 1u};
    if (!openspm_roster_mario_plain(io) ||
        !openspm_roster_wr(io, SPM_R8PE01_MARIO_WORK + OPENSPM_ROSTER_MARIO_CHANGE_TARGET, base))
        return 0;
    if (io->call(io->context, OPENSPM_ROSTER_MARIO_CHG_MOT, 2u, motion) != OPENSPM_OK) return 0;
    r->change_wait = 10u;
    r->change_base = base;
    return 1;
}

/* Each frame after a start: once move 71 runs, the flag the game sets with
   it; if it never starts, the reload without the flip. */
static inline void openspm_roster_follow_character_change(openspm_roster *r, const openspm_roster_io *io) {
    int ok = 1;
    const uint32_t motion = openspm_roster_rd(io, SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_MOTION_ID, &ok) >> 16;
    const uint32_t f0 = openspm_roster_rd(io, SPM_R8PE01_MARIO_WORK, &ok);
    if (!ok) return;
    if (motion == OPENSPM_ROSTER_MOT_CHARACTER_CHANGE) {
        (void)openspm_roster_wr(io, SPM_R8PE01_MARIO_WORK, f0 | OPENSPM_ROSTER_MARIO_LOCKED);
        r->change_wait = 0u;
    } else if (--r->change_wait == 0u) {
        openspm_roster_log(io, 1, "OpenSPM content: the switch transition did not start; switched without it.");
        (void)openspm_roster_start_script(io, r->reload_script[r->change_base]);
    }
}

/* The game Pixl whose follower is out, if `item` is one (else 0). */
static inline uint32_t openspm_roster_pixl_out(const openspm_roster_io *io, uint32_t item) {
    int ok = 1;
    uint32_t flags, count, i;
    if (item < OPENSPM_ROSTER_PIXL_FIRST || item >= OPENSPM_ROSTER_PIXL_FIRST + 12u) return 0u;
    flags = openspm_roster_rd(io, OPENSPM_ROSTER_FOLLOWERS, &ok);
    count = openspm_roster_rd(io, OPENSPM_ROSTER_FOLLOWERS + 840u, &ok);
    if (!ok || (flags & 1u) || count > 16u) return 0u;
    for (i = 0; i < count; ++i)
        if (openspm_roster_rd(io, OPENSPM_ROSTER_FOLLOWERS + 4u + 208u * i + 4u, &ok) == item && ok) return item;
    return 0u;
}

/* ---- menu ------------------------------------------------------------------------ */
static inline int openspm_roster_window_visible(const openspm_roster_io *io, uint32_t cw, uint32_t id_offset) {
    int ok = 1;
    const uint32_t id = openspm_roster_rd(io, cw + id_offset, &ok);
    const uint32_t pw = openspm_roster_rd(io, SPM_R8PE01_PAUSEWIN_WP, &ok);
    uint32_t entries, entry, flags, state;
    if (!ok || id == UINT32_MAX || pw == 0u || id > 64u) return 0;
    entries = openspm_roster_rd(io, pw + SPM_R8PE01_PAUSEWIN_ENTRIES, &ok);
    entry = entries + id * SPM_R8PE01_PAUSEWIN_ENTRY_SIZE;
    flags = openspm_roster_rd(io, entry, &ok);
    state = openspm_roster_rd(io, entry + SPM_R8PE01_PAUSEWIN_STATE, &ok);
    return ok && (flags & 0x00010000u) != 0u && state >= 10u && state < 20u;
}

/* The tab's window exists and is the one the game built for that tab: the
   pause menu creates the character, Pixl and key-item windows together on
   open (pausewin create runs the tab's init callback, entry +100, which
   fills the list right away) and deletes them on close (the delete callback,
   entry +116, frees the list; the flags go to 0). The list pointer in the
   chartab work stays behind after a close, so only the window says whether
   the list is live. (chartab +164 counts the lists the close still has to
   free: it reads 3 only while the menu closes.) */
static inline int openspm_roster_window_live(const openspm_roster_io *io, uint32_t cw, uint32_t id_offset,
                                             uint32_t init_fn) {
    int ok = 1;
    const uint32_t id = openspm_roster_rd(io, cw + id_offset, &ok);
    const uint32_t pw = openspm_roster_rd(io, SPM_R8PE01_PAUSEWIN_WP, &ok);
    uint32_t entries, entry, flags, init;
    if (!ok || id == UINT32_MAX || pw == 0u || id > 64u) return 0;
    entries = openspm_roster_rd(io, pw + SPM_R8PE01_PAUSEWIN_ENTRIES, &ok);
    if (!ok || entries == 0u) return 0;
    entry = entries + id * SPM_R8PE01_PAUSEWIN_ENTRY_SIZE;
    flags = openspm_roster_rd(io, entry, &ok);
    init = openspm_roster_rd(io, entry + OPENSPM_ROSTER_PAUSEWIN_INIT_FN, &ok);
    return ok && (flags & 0x00010000u) != 0u && init == init_fn;
}

/* Room for `rows` rows in the tab's window (never smaller than the game made it). */
static inline void openspm_roster_fit_window(const openspm_roster_io *io, uint32_t cw, uint32_t id_offset,
                                             uint32_t rows) {
    int ok = 1;
    const uint32_t id = openspm_roster_rd(io, cw + id_offset, &ok);
    const uint32_t pw = openspm_roster_rd(io, SPM_R8PE01_PAUSEWIN_WP, &ok);
    uint32_t entries, bits;
    float want = 25.0f * (float)rows + 50.0f, have;
    if (!ok || id == UINT32_MAX || pw == 0u || id > 64u) return;
    entries = openspm_roster_rd(io, pw + SPM_R8PE01_PAUSEWIN_ENTRIES, &ok);
    bits = openspm_roster_rd(io, entries + id * SPM_R8PE01_PAUSEWIN_ENTRY_SIZE + OPENSPM_ROSTER_PAUSEWIN_HEIGHT, &ok);
    if (!ok || entries == 0u) return;
    memcpy(&have, &bits, sizeof have);
    if (want > 250.0f) want = 250.0f;
    if (!(have < want)) return;
    memcpy(&bits, &want, sizeof bits);
    (void)openspm_roster_wr(io, entries + id * SPM_R8PE01_PAUSEWIN_ENTRY_SIZE + OPENSPM_ROSTER_PAUSEWIN_HEIGHT, bits);
}

static inline uint32_t openspm_roster_find_item(const openspm_roster_io *io, uint32_t entries, uint32_t count,
                                                uint32_t item) {
    uint32_t i;
    int ok = 1;
    for (i = 0; i < count; ++i)
        if (openspm_roster_rd(io, entries + i * SPM_R8PE01_CHARTAB_ENTRY_SIZE + 4u, &ok) == item && ok) return i;
    return UINT32_MAX;
}

/* A new character list (the menu just opened): add ours after the game's. */
static inline void openspm_roster_fill_list(openspm_roster *r, const openspm_roster_io *io, uint32_t cw,
                                            uint32_t entries) {
    int ok = 1;
    uint32_t count = openspm_roster_rd(io, cw + SPM_R8PE01_CHARTAB_COUNT, &ok), i;
    const uint32_t capacity = r->capacity < OPENSPM_ROSTER_MAX_LIST ? r->capacity : OPENSPM_ROSTER_MAX_LIST;
    memset(r->entry_char, 0, sizeof r->entry_char);
    r->list_entries = entries;
    r->last_cursor = UINT32_MAX;
    if (!ok || count == 0u || count > 4u) return; /* not the game's own list: leave it */
    for (i = 0; i < count; ++i) {
        const uint32_t item = openspm_roster_rd(io, entries + i * SPM_R8PE01_CHARTAB_ENTRY_SIZE + 4u, &ok);
        if (!ok || item < SPM_R8PE01_ITEM_CHARACTER_FIRST || item > SPM_R8PE01_ITEM_CHARACTER_FIRST + 3u) return;
    }
    for (i = 0; i < r->count && count < capacity; ++i) {
        const uint32_t base_index = openspm_roster_find_item(io, entries, count, SPM_R8PE01_ITEM_CHARACTER_FIRST +
                                                                                   r->chars[i].base);
        uint32_t head, e;
        if (base_index == UINT32_MAX) continue; /* base character not unlocked yet */
        head = openspm_roster_rd(io, entries + base_index * SPM_R8PE01_CHARTAB_ENTRY_SIZE, &ok) & 0xFF000000u;
        e = entries + count * SPM_R8PE01_CHARTAB_ENTRY_SIZE;
        if (!ok || !openspm_roster_wr(io, e, head) || !openspm_roster_wr(io, e + 4u, r->char_items ? r->chars[i].item_id : SPM_R8PE01_ITEM_PLACEHOLDER) ||
            !openspm_roster_wr(io, e + 8u, 0u))
            return;
        r->entry_char[count] = (uint8_t)(i + 1u);
        ++count;
        (void)openspm_roster_wr(io, cw + SPM_R8PE01_CHARTAB_COUNT, count);
        if (r->active == (int32_t)i) (void)openspm_roster_wr(io, cw + SPM_R8PE01_CHARTAB_CURSOR, count - 1u);
    }
    openspm_roster_fit_window(io, cw, SPM_R8PE01_CHARTAB_WINDOW_ID, count);
}

static inline void openspm_roster_menu(openspm_roster *r, const openspm_roster_io *io, uint32_t cw) {
    int ok = 1;
    const uint32_t entries = openspm_roster_rd(io, cw + SPM_R8PE01_CHARTAB_LIST, &ok);
    const uint32_t item = SPM_R8PE01_ITEM_DATA + SPM_R8PE01_ITEM_PLACEHOLDER * SPM_R8PE01_ITEM_DATA_SIZE;
    uint32_t cursor, held = 0u, pressed = 0u;
    int k;
    if (!ok || entries == 0u) return;
    if (entries != r->list_entries) openspm_roster_fill_list(r, io, cw, entries);
    cursor = openspm_roster_rd(io, cw + SPM_R8PE01_CHARTAB_CURSOR, &ok);
    if (!ok) return;
    k = cursor < OPENSPM_ROSTER_MAX_LIST ? (int)r->entry_char[cursor] - 1 : -1;
    /* Name and description of the entry under the cursor. */
    if (k >= 0 && (r->item_char != k || cursor != r->last_cursor)) {
        uint32_t win_id, pw, win;
        const uint32_t shown = r->char_items ? r->chars[k].item_id : SPM_R8PE01_ITEM_PLACEHOLDER;
        if (!r->char_items) { /* no own items: the shared placeholder item speaks for him */
            (void)openspm_roster_wr(io, item + SPM_R8PE01_ITEM_NAME_MSG, r->chars[k].key_name);
            (void)openspm_roster_wr(io, item + SPM_R8PE01_ITEM_DESC_MSG, r->chars[k].key_desc);
            r->item_patched = 1;
        }
        r->item_char = k;
        win_id = openspm_roster_rd(io, cw + OPENSPM_ROSTER_CHARTAB_MSG_WINDOW, &ok);
        pw = openspm_roster_rd(io, SPM_R8PE01_PAUSEWIN_WP, &ok);
        win = openspm_roster_rd(io, pw + SPM_R8PE01_PAUSEWIN_ENTRIES, &ok) + win_id * SPM_R8PE01_PAUSEWIN_ENTRY_SIZE;
        if (ok && win_id < 64u) {
            const uint32_t args[3] = {win, shown, 0u};
            (void)io->call(io->context, OPENSPM_ROSTER_PAUSEWIN_SET_MESSAGE, 3u, args);
        }
    }
    r->last_cursor = cursor;
    if (io->get_buttons == NULL || io->set_buttons == NULL ||
        io->get_buttons(io->context, &held, &pressed) != OPENSPM_OK)
        return;
    if (r->inject2) { /* the game presses 2 on the base character now */
        r->inject2 = 0;
        (void)io->set_buttons(io->context, held | OPENSPM_WII_BTN_2);
        return;
    }
    if (r->mask2) {
        if (held & OPENSPM_WII_BTN_2) {
            (void)io->set_buttons(io->context, held & ~OPENSPM_WII_BTN_2);
            return;
        }
        r->mask2 = 0;
    }
    if (!(pressed & OPENSPM_WII_BTN_2) || !openspm_roster_window_visible(io, cw, SPM_R8PE01_CHARTAB_WINDOW_ID)) return;
    {
        const uint32_t head = openspm_roster_rd(io, entries + cursor * SPM_R8PE01_CHARTAB_ENTRY_SIZE, &ok);
        const uint32_t picked = openspm_roster_rd(io, entries + cursor * SPM_R8PE01_CHARTAB_ENTRY_SIZE + 4u, &ok);
        uint32_t current = 0u, base_index;
        if (!ok || (head & 0xFF000000u) == 0u) return; /* not selectable now: the game buzzes */
        current = openspm_roster_rd(io, SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_CHARACTER, &ok) >> 24;
        if (k >= 0 && r->real_ids) {
            /* His own id: the game's own pick does the rest (preload, the
               change request, the flip, the model from his rows). The menu
               takes the id from the picked item (item - 216): once it has
               copied his entry's item, the tick makes the copy 216 + his id
               (the entry keeps his item, so the menu keeps drawing him). */
            r->pick_item = picked;
            r->pick_frames = 0u;
            r->pending = k;
            r->pending_frames = 0u;
        } else if (k >= 0) {
            const openspm_roster_character *c = &r->chars[k];
            base_index = openspm_roster_find_item(io, entries, cursor, SPM_R8PE01_ITEM_CHARACTER_FIRST + c->base);
            if (base_index == UINT32_MAX) return;
            (void)io->set_buttons(io->context, held & ~OPENSPM_WII_BTN_2);
            r->mask2 = 1;
            openspm_roster_swap_tables(r, io, k);
            r->pending = k;
            r->pending_was_current = ok && current == c->base;
            r->pending_frames = 0u;
            r->reload_base = -1;
            (void)openspm_roster_wr(io, cw + SPM_R8PE01_CHARTAB_CURSOR, base_index);
            r->inject2 = 1;
        } else if (r->real_ids) {
            r->pending = -1; /* a game character: the game switches */
        } else if (r->tables_char >= 0 || r->active >= 0) {
            /* A game character: everything goes back now, before the game
               switches (or, same character, OpenSPM reloads it after). */
            const int32_t was = r->tables_char >= 0 ? r->tables_char : r->active;
            const uint32_t base = r->chars[was].base;
            openspm_roster_restore_tables(r, io);
            r->active = r->pending = -1;
            if (picked == SPM_R8PE01_ITEM_CHARACTER_FIRST + base && ok && current == base) r->reload_base = (int32_t)base;
        }
    }
}

/* ---- Pixl tab ---------------------------------------------------------------------------- */

static inline void openspm_roster_pixl_menu(openspm_roster *r, const openspm_roster_io *io, uint32_t cw) {
    int ok = 1;
    const uint32_t entries = openspm_roster_rd(io, cw + OPENSPM_ROSTER_PIXLTAB_LIST, &ok);
    uint32_t count, cursor, held = 0u, pressed = 0u, i;
    if (!r->pixls_ready || !ok || entries == 0u) return;
    if (entries != r->pixl_list_entries) {
        /* A new Pixl list (menu opened): ours after the game's (at most 16). */
        r->pixl_list_entries = entries;
        r->pixl_first = UINT32_MAX;
        count = openspm_roster_rd(io, cw + OPENSPM_ROSTER_PIXLTAB_COUNT, &ok);
        if (!ok || count > OPENSPM_ROSTER_POUCH_PIXL_SLOTS) return;
        r->pixl_first = count;
        r->pixl_listed = 0u;
        for (i = 0; i < r->pixl_count && count < r->pixl_capacity; ++i) {
            const uint32_t e = entries + count * SPM_R8PE01_CHARTAB_ENTRY_SIZE;
            if (!openspm_roster_wr(io, e, 0x01000000u) || !openspm_roster_wr(io, e + 4u, r->pixls[i].item_id) ||
                !openspm_roster_wr(io, e + 8u, r->pixls[i].slot))
                break;
            if (r->pixl_active == (int32_t)i || r->pixl_pending == (int32_t)i)
                (void)openspm_roster_wr(io, cw + OPENSPM_ROSTER_PIXLTAB_CURSOR, count);
            ++count;
            ++r->pixl_listed;
        }
        (void)openspm_roster_wr(io, cw + OPENSPM_ROSTER_PIXLTAB_COUNT, count);
        openspm_roster_fit_window(io, cw, OPENSPM_ROSTER_PIXLTAB_WINDOW_ID, count);
    }
    if (r->pixl_first == UINT32_MAX || io->get_buttons == NULL || io->set_buttons == NULL ||
        io->get_buttons(io->context, &held, &pressed) != OPENSPM_OK)
        return;
    if (r->pixl_mask2) {
        if (held & OPENSPM_WII_BTN_2) {
            (void)io->set_buttons(io->context, held & ~OPENSPM_WII_BTN_2);
            return;
        }
        r->pixl_mask2 = 0;
    }
    if (!(pressed & OPENSPM_WII_BTN_2) ||
        !openspm_roster_window_visible(io, cw, OPENSPM_ROSTER_PIXLTAB_WINDOW_ID))
        return;
    cursor = openspm_roster_rd(io, cw + OPENSPM_ROSTER_PIXLTAB_CURSOR, &ok);
    if (!ok) return;
    if (cursor >= r->pixl_first && cursor < r->pixl_first + r->pixl_listed) {
        /* Ours: the game never sees this press (its own switch would load a
           model for "no Pixl"); the switch happens after the menu closes. */
        const int32_t k = (int32_t)(cursor - r->pixl_first);
        const uint32_t flags = openspm_roster_rd(io, cw, &ok);
        const uint32_t sfx[3] = {OPENSPM_ROSTER_SFX_DECIDE, 0u, 0u};
        (void)io->set_buttons(io->context, held & ~OPENSPM_WII_BTN_2);
        r->pixl_mask2 = 1;
        /* The game Pixl out now (the pouch's selected one, as 0x8014EC80
           reads it), for the flip. */
        r->pixl_old_item = 0u;
        for (i = 0; i < OPENSPM_ROSTER_POUCH_PIXL_SLOTS && r->pixl_old_item == 0u; ++i) {
            const uint32_t w = openspm_roster_rd(io, OPENSPM_ROSTER_POUCH_PIXLS + 4u * i, &ok);
            if (ok && (w & 0xFFFFu) != 0u && (w & 0x00FF0000u) != 0u) r->pixl_old_item = w & 0xFFFFu;
        }
        for (i = 0; i < r->pixl_count; ++i) openspm_roster_set_slot(io, r->pixls[i].slot, (int32_t)i == k);
        r->pixl_pending = k;
        /* What the game's own confirm does next: the pick sound and "a Pixl
           was picked", so the menu closes as it does for a game Pixl. */
        if (ok) (void)openspm_roster_wr(io, cw, flags | OPENSPM_ROSTER_CHARTAB_PIXL_PICKED);
        (void)io->call(io->context, OPENSPM_ROSTER_SFX_PLAY, 1u, sfx);
    } else {
        /* A game Pixl: the game clears every listed slot (ours too) and
           switches itself. */
        r->pixl_pending = -1;
    }
}

/* After the menu: switch to a picked Pixl (no game Pixl selected, followers
   rebuilt by the game's own evt_fairy_reset), or notice that the game
   switched back to one of its own. */
static inline void openspm_roster_pixl_play(openspm_roster *r, const openspm_roster_io *io, uint32_t level) {
    int ok = 1, pouch_selected = 0;
    uint32_t i;
    char message[160];
    for (i = 0; i < OPENSPM_ROSTER_POUCH_PIXL_SLOTS; ++i) {
        const uint32_t w = openspm_roster_rd(io, OPENSPM_ROSTER_POUCH_PIXLS + 4u * i, &ok);
        if (ok && (w & 0xFFFFu) != 0u && (w & 0x00FF0000u) != 0u) pouch_selected = 1;
    }
    if (r->pixl_pending >= 0) {
        if (level != 0u) return; /* wait for normal play */
        for (i = 0; i < OPENSPM_ROSTER_POUCH_PIXL_SLOTS; ++i) {
            const uint32_t slot = OPENSPM_ROSTER_POUCH_PIXLS + 4u * i;
            const uint32_t w = openspm_roster_rd(io, slot, &ok);
            if (ok && (w & 0x00FF0000u)) (void)openspm_roster_wr(io, slot, w & 0xFF00FFFFu);
        }
        if (r->pixl_active != r->pixl_pending) {
            /* The game's Pixl flip (move 72), which rebuilds the followers
               from the pouch as evt_fairy_reset does; game items only for
               its size lookups (see OPENSPM_ROSTER_PIXL_CHANGE). */
            const uint32_t args[2] = {openspm_roster_pixl_out(io, r->pixl_old_item), OPENSPM_ROSTER_PIXL_STANDIN};
            if (!openspm_roster_mario_plain(io) ||
                io->call(io->context, OPENSPM_ROSTER_PIXL_CHANGE, 2u, args) != OPENSPM_OK)
                (void)openspm_roster_start_script(io, r->fairy_reset_script);
        }
        r->pixl_active = r->pixl_pending;
        r->pixl_pending = -1;
        snprintf(message, sizeof message, "OpenSPM content: Pixl %s (%s, item %u) in play.",
                 r->pixls[r->pixl_active].name, r->pixls[r->pixl_active].key, (unsigned)r->pixls[r->pixl_active].item_id);
        openspm_roster_log(io, 0, message);
        return;
    }
    if (r->pixl_active >= 0) {
        const uint32_t w = openspm_roster_rd(io, r->pixls[r->pixl_active].slot, &ok);
        if (pouch_selected || (ok && !(w & 0x00FF0000u))) {
            /* The game picked one of its own (menu, story): ours steps back. */
            openspm_roster_set_slot(io, r->pixls[r->pixl_active].slot, 0);
            r->pixl_active = -1;
        }
    }
}

static inline void openspm_roster_rd_str(const openspm_roster_io *io, uint32_t address, char *out, uint32_t size) {
    uint32_t i;
    int ok = 1;
    out[0] = 0;
    if (address < 0x80000000u) return;
    for (i = 0; i + 1u < size; ++i) {
        const uint32_t a = address + i;
        const char c = (char)(openspm_roster_rd(io, a & ~3u, &ok) >> (24u - 8u * (a & 3u)));
        if (!ok || c == 0) break;
        out[i] = c < 32 || c > 126 ? '?' : c;
    }
    out[i] = 0;
}

/* ---- animation names ------------------------------------------------------------------- */
/* Mario builds the animation name from a per-character prefix, the pose name
   and "R" when seen from behind (0x80127400: sprintf "%s%s%s"): "mario_" and
   "Big_mario_" for Mario, "luigi_" / "Big_luigi_" for Luigi, none for Peach
   and Bowser. The pose names the game passes to marioChgPose (0x801260B8),
   collected from its call sites. */
static const char *const k_openspm_roster_mario_poses[] = {
    "S_1", "S_2", "S_2B", "S_3", "S_3a", "S_3c", "W_1", "R_1", "R_3", "J_1B", "J_1C", "J_2A", "J_2B", "J_2C",
    "J_2D", "KJ_1A", "KJ_1B", "D_1", "D_1A", "D_1B", "D_2", "D_3", "D_4", "D_5", "AC_1", "AC_2", "AC_3", "AC_4",
    "AC_5", "AC_6", "AC_7", "N_1", "N_3", "N_3B", "N_4", "N_5", "N_7", "N_8", "N_10A", "N_10B", "N_12", "M_1a",
    "M_1A", "M_1b", "M_1B", "M_1c", "M_1C", "M_2a", "M_2A", "O_1", "O_2A", "O_2B", "O_2C", "O_3B", "O_7", "E_1",
    "E_2", "E_3", "G_1", "G_2", "I_2", "T_2", "T_8", "T_11", "U_1", "P_1", "Z_2", "C_4", "C_5", "SD_1", "H_1C",
    "A_1_a", "A_1_b", "A_1_c", "A_1_d", "A_4_a", "A_4_b", "A_4_c", "A_4_d"};
#define OPENSPM_ROSTER_MODEL_ANIMS 160u
#define OPENSPM_ROSTER_ANIM_NAME 24u

/* The AnimPoseData header of the pose the player is drawn with (0 if none). */
static inline uint32_t openspm_roster_player_model(const openspm_roster_io *io) {
    int ok = 1;
    const uint32_t work = openspm_roster_rd(io, OPENSPM_ROSTER_ANIM_WORK_PTR, &ok);
    uint32_t groups, poses, id, group, file, data, header;
    if (!ok || work == 0u) return 0u;
    groups = openspm_roster_rd(io, work, &ok);
    poses = openspm_roster_rd(io, work + 0x10u, &ok);
    id = openspm_roster_rd(io, SPM_R8PE01_MARIO_WORK + OPENSPM_ROSTER_MARIO_POSE_IDS, &ok);
    if (!ok || groups == 0u || poses == 0u || id > 1024u) return 0u;
    group = openspm_roster_rd(io, poses + id * 0x188u + 0x10u, &ok);
    if (!ok || group > 1024u) return 0u;
    file = openspm_roster_rd(io, groups + group * 16u + 8u, &ok);
    data = ok && file ? openspm_roster_rd(io, file + 0xA4u, &ok) : 0u;
    header = ok && data ? openspm_roster_rd(io, data, &ok) : 0u;
    return ok && header >= 0x80000000u ? header : 0u;
}

static inline int openspm_roster_wr_name(const openspm_roster_io *io, uint32_t entry, const char *name) {
    uint32_t i;
    for (i = 0; i < 60u; i += 4u) {
        uint32_t w = 0u, k;
        for (k = 0; k < 4u; ++k) {
            const size_t at = i + k;
            w = w << 8 | (at < strlen(name) ? (uint8_t)name[at] : 0u);
        }
        if (!openspm_roster_wr(io, entry + i, w)) return 0;
    }
    return 1;
}

/* The model's index for a pose name through the character's anim_map
   (-1 if none). */
static inline int openspm_roster_anim_target(const openspm_roster_character *c, char (*names)[OPENSPM_ROSTER_ANIM_NAME],
                                             uint32_t count, const char *pose) {
    const char *m = c->anim_map;
    const size_t pn = strlen(pose);
    uint32_t i;
    while (*m) {
        const char *eq = strchr(m, '='), *end = strchr(m, ',');
        if (end == NULL) end = m + strlen(m);
        if (eq != NULL && eq < end && (size_t)(eq - m) == pn && strncmp(m, pose, pn) == 0) {
            for (i = 0; i < count; ++i)
                if ((size_t)(end - eq - 1) == strlen(names[i]) && strncmp(eq + 1, names[i], strlen(names[i])) == 0)
                    return (int)i;
        }
        m = *end ? end + 1 : end;
    }
    for (i = 0; i < count; ++i)
        if (strcmp(names[i], pose) == 0) return (int)i;
    for (i = 0; i < count; ++i)
        if (strcmp(names[i], "S_1") == 0) return (int)i;
    return -1;
}

/* A list of animation names, as many log lines as it takes (a boss model has
   well over a hundred). */
static inline void openspm_roster_log_names(const openspm_roster_io *io, const char *what,
                                            char (*names)[OPENSPM_ROSTER_ANIM_NAME], uint32_t count) {
    char line[400], message[480];
    uint32_t i, used = 0u;
    line[0] = 0;
    for (i = 0; i < count; ++i) {
        const size_t n = strlen(names[i]);
        if (used != 0u && used + 1u + n >= sizeof line) {
            snprintf(message, sizeof message, "OpenSPM content anim: %s: %s", what, line);
            openspm_roster_log(io, 0, message);
            used = 0u;
            line[0] = 0;
        }
        used += (uint32_t)snprintf(line + used, sizeof line - used, "%s%s", used ? " " : "", names[i]);
    }
    snprintf(message, sizeof message, "OpenSPM content anim: %s: %s", what, count ? line : "(none)");
    openspm_roster_log(io, 0, message);
}

/* Once per model load while an added character plays: the model's animation
   table gets the names the game asks the base character's model for, each
   entry pointing at the model's own animation data (a copy of the table with
   the extra entries, in OpenSPM memory). Other users of the same model (the
   enemy) only gain names. */
static inline void openspm_roster_anim_alias(openspm_roster *r, const openspm_roster_io *io) {
    static const char *const prefixes[4][2] = {{"mario_", "Big_mario_"}, {"", ""}, {"", ""}, {"luigi_", "Big_luigi_"}};
    static const char *const suffixes[2] = {"", "R"};
    openspm_roster_character *c = &r->chars[r->active];
    char names[OPENSPM_ROSTER_MODEL_ANIMS][OPENSPM_ROSTER_ANIM_NAME], alias[48], message[200];
    uint32_t data_ptr[OPENSPM_ROSTER_MODEL_ANIMS], count, anims, i, p, s, k, total, extra = 0u, at;
    const uint32_t header = openspm_roster_player_model(io);
    const uint32_t pbase = r->real_ids ? 0u : c->base; /* with real ids the game asks for Mario's names */
    const uint32_t npref = pbase == 0u || pbase == 3u ? 2u : 1u;
    const uint32_t nposes = (uint32_t)(sizeof k_openspm_roster_mario_poses / sizeof k_openspm_roster_mario_poses[0]);
    int ok = 1;
    if (header == 0u) return;
    anims = openspm_roster_rd(io, header + 0x1ACu, &ok);
    if (!ok || (header == c->alias_header && anims == c->alias_buf)) return;
    count = openspm_roster_rd(io, header + 0x148u, &ok);
    if (!ok || count == 0u || count > OPENSPM_ROSTER_MODEL_ANIMS || anims < 0x80000000u) return;
    for (i = 0; i < count; ++i) {
        openspm_roster_rd_str(io, anims + 64u * i, names[i], sizeof names[i]);
        data_ptr[i] = openspm_roster_rd(io, anims + 64u * i + 60u, &ok);
        /* Still a player model (the swap is not in yet): leave it alone. */
        if (strncmp(names[i], "mario_", 6) == 0 || strncmp(names[i], "luigi_", 6) == 0 ||
            strncmp(names[i], "Big_", 4) == 0 || strncmp(names[i], "peach_", 6) == 0)
            return;
    }
    if (!ok) return;
    /* Once per character: the model's own names, before any alias is added
       (what an anim_map can point at). */
    if (!c->own_names_logged) {
        snprintf(message, sizeof message, "%s's model %s has %u animations of its own", c->name, c->model,
                 (unsigned)count);
        openspm_roster_log_names(io, message, names, count);
        c->own_names_logged = 1;
    }
    /* Count, then write: model entries first (lookups find them first), then
       every prefix x pose x suffix the model lacks. */
    for (k = 0; k < 2u; ++k) {
        at = count;
        for (p = 0; p < npref; ++p)
            for (s = 0; s < 2u; ++s)
                for (i = 0; i < nposes + count; ++i) {
                    const char *pose = i < nposes ? k_openspm_roster_mario_poses[i] : names[i - nposes];
                    uint32_t j;
                    int dup = 0, t;
                    if (i >= nposes) /* a model name that is also a game pose: done above */
                        for (j = 0; j < nposes && !dup; ++j) dup = strcmp(pose, k_openspm_roster_mario_poses[j]) == 0;
                    snprintf(alias, sizeof alias, "%s%s%s", prefixes[pbase][p], pose, suffixes[s]);
                    for (j = 0; j < count && !dup; ++j) dup = strcmp(alias, names[j]) == 0;
                    if (dup || (t = openspm_roster_anim_target(c, names, count, pose)) < 0) continue;
                    if (k == 1u) {
                        const uint32_t e = c->alias_buf + 64u * at;
                        if (!openspm_roster_wr_name(io, e, alias) || !openspm_roster_wr(io, e + 60u, data_ptr[t])) return;
                    }
                    ++at;
                }
        if (k == 0u) {
            extra = at - count;
            total = at;
            if (c->alias_buf == 0u || c->alias_cap < total) {
                const uint32_t cap = total + 16u;
                if (io->scratch(io->context, cap * 64u, &c->alias_buf) != OPENSPM_OK) {
                    c->alias_buf = 0u;
                    return;
                }
                c->alias_cap = cap;
            }
            for (i = 0; i < count; ++i)
                for (s = 0; s < 64u; s += 4u)
                    if (!openspm_roster_wr(io, c->alias_buf + 64u * i + s, openspm_roster_rd(io, anims + 64u * i + s, &ok)))
                        return;
        }
    }
    if (!ok || !openspm_roster_wr(io, header + 0x148u, total) || !openspm_roster_wr(io, header + 0x1ACu, c->alias_buf)) return;
    c->alias_header = header;
    snprintf(message, sizeof message, "OpenSPM content: %s: %u animation names of the base character mapped onto %s's %u.",
             c->name, (unsigned)extra, c->model, (unsigned)count);
    openspm_roster_log(io, 0, message);
}

/* ---- animation diagnostic ---------------------------------------------------------------- */

/* While an added character plays: every 2 s (first 30 times) what Mario asks
   for and what his pose does; once, the model's animation names. Data for
   the character's animation mapping. */
static inline void openspm_roster_anim_diag(openspm_roster *r, const openspm_roster_io *io) {
    int ok = 1;
    char want[24], names[400], one[24], message[480];
    uint32_t work, poses, groups, id, pose, group, file, data, header, count, anims, i, used = 0u;
    if (r->diag_logs >= 30u || ++r->diag_frames < 120u) return;
    r->diag_frames = 0u;
    work = openspm_roster_rd(io, OPENSPM_ROSTER_ANIM_WORK_PTR, &ok);
    if (!ok || work == 0u) return;
    groups = openspm_roster_rd(io, work, &ok);
    poses = openspm_roster_rd(io, work + 0x10u, &ok);
    id = openspm_roster_rd(io, SPM_R8PE01_MARIO_WORK + OPENSPM_ROSTER_MARIO_POSE_IDS, &ok);
    openspm_roster_rd_str(io, openspm_roster_rd(io, SPM_R8PE01_MARIO_WORK + OPENSPM_ROSTER_MARIO_POSE_NAME, &ok), want,
                          sizeof want);
    if (!ok || poses == 0u || id > 1024u) return;
    pose = poses + id * 0x188u;
    {
        const uint32_t flag = openspm_roster_rd(io, pose, &ok), type = openspm_roster_rd(io, pose + 4u, &ok);
        const uint32_t index = openspm_roster_rd(io, pose + 0x14u, &ok), fbits = openspm_roster_rd(io, pose + 0x20u, &ok);
        float frame;
        memcpy(&frame, &fbits, sizeof frame);
        group = openspm_roster_rd(io, pose + 0x10u, &ok);
        snprintf(message, sizeof message,
                 "OpenSPM content anim: pose \"%s\" motion %u; pose ids %u/%u/%u; pose %u flag 0x%08X type 0x%08X "
                 "group %u anim %u frame %.2f.",
                 want, (unsigned)(openspm_roster_rd(io, SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_MOTION_ID, &ok) >> 16),
                 (unsigned)id, (unsigned)openspm_roster_rd(io, SPM_R8PE01_MARIO_WORK + OPENSPM_ROSTER_MARIO_POSE_IDS + 4u, &ok),
                 (unsigned)openspm_roster_rd(io, SPM_R8PE01_MARIO_WORK + OPENSPM_ROSTER_MARIO_POSE_IDS + 8u, &ok),
                 (unsigned)id, (unsigned)flag, (unsigned)type, (unsigned)group, (unsigned)index, (double)frame);
        openspm_roster_log(io, 0, message);
    }
    if (r->diag_logs++ != 0u || groups == 0u || group > 1024u) return;
    file = openspm_roster_rd(io, groups + group * 16u + 8u, &ok);
    data = ok && file ? openspm_roster_rd(io, file + 0xA4u, &ok) : 0u;
    header = ok && data ? openspm_roster_rd(io, data, &ok) : 0u;
    if (!ok || header < 0x80000000u) return;
    count = openspm_roster_rd(io, header + 0x148u, &ok);
    anims = openspm_roster_rd(io, header + 0x1ACu, &ok);
    /* As many lines as it takes (a boss model has well over a hundred). */
    names[0] = 0;
    for (i = 0; ok && i < count && i < 256u; ++i) {
        int w;
        openspm_roster_rd_str(io, anims + 64u * i, one, sizeof one);
        w = snprintf(names + used, sizeof names - used, "%s%s", used ? " " : "", one);
        if (w < 0 || used + (uint32_t)w >= sizeof names) {
            snprintf(message, sizeof message, "OpenSPM content anim: model has %u animations: %s", (unsigned)count, names);
            openspm_roster_log(io, 0, message);
            used = 0u;
            names[0] = 0;
            w = snprintf(names, sizeof names, "%s", one);
            if (w < 0) break;
        }
        used += (uint32_t)w;
    }
    if (used != 0u || count == 0u) {
        snprintf(message, sizeof message, "OpenSPM content anim: model has %u animations: %s", (unsigned)count, names);
        openspm_roster_log(io, 0, message);
    }
}

/* ---- per frame ----------------------------------------------------------------------- */
static inline void openspm_roster_tick(openspm_roster *r, const openspm_roster_io *io) {
    int ok = 1;
    uint32_t seq, level, cw, current;
    int chars_live = 0, pixls_live = 0;
    char message[160];
    if (r->count + r->pixl_count == 0u || r->disabled) return;
    seq = openspm_roster_rd(io, SPM_R8PE01_SEQ_WORK, &ok);
    /* A door or pipe: the picked character and Pixl stay (the game loads the
       next map's models through the tables we keep swapped); nothing is
       touched until the new map runs. */
    if (ok && seq == SPM_R8PE01_SEQ_MAPCHANGE && r->built) return;
    if (!ok || seq != SPM_R8PE01_SEQ_GAME) {
        openspm_roster_reset(r, io);
        return;
    }
    r->closed = 1;
    if (!r->built && !openspm_roster_build(r, io)) return;
    openspm_roster_install_messages(r, io);
    if (r->pick_item != 0u) {
        /* The menu copied his entry's item: make it his id's item. */
        const uint32_t work = openspm_roster_rd(io, SPM_R8PE01_PAUSEWIN_WP, &ok);
        const uint32_t at = work + SPM_R8PE01_PAUSE_PICK_ITEM;
        int pick_ok = ok && work >= 0x80000000u;
        if (pick_ok && openspm_roster_rd(io, at, &pick_ok) == r->pick_item && pick_ok && r->pending >= 0) {
            (void)openspm_roster_wr(io, at, OPENSPM_ROSTER_ITEM_CHARACTER_PICK + r->chars[r->pending].game_id);
            r->pick_item = 0u;
        } else if (++r->pick_frames > 120u) {
            r->pick_item = 0u;
        }
        ok = 1;
    }
    level = openspm_roster_rd(io, SPM_R8PE01_SYSTEM_LEVEL, &ok);
    current = openspm_roster_rd(io, SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_CHARACTER, &ok) >> 24;
    if (!ok) return;
    cw = openspm_roster_rd(io, SPM_R8PE01_CHARTAB_WP, &ok);
    if (ok && cw != 0u) {
        chars_live = openspm_roster_window_live(io, cw, SPM_R8PE01_CHARTAB_WINDOW_ID, OPENSPM_ROSTER_CHARTAB_INIT);
        pixls_live = openspm_roster_window_live(io, cw, OPENSPM_ROSTER_PIXLTAB_WINDOW_ID, OPENSPM_ROSTER_PIXLTAB_INIT);
    }
    if (chars_live || pixls_live) {
        if (pixls_live) openspm_roster_pixl_menu(r, io, cw);
        if (chars_live && r->count) openspm_roster_menu(r, io, cw);
        return;
    }
    /* Menu closed. */
    openspm_roster_restore_item(r, io);
    r->list_entries = 0u;
    r->pixl_list_entries = 0u;
    if (r->pixls_ready) openspm_roster_pixl_play(r, io, level);
    r->inject2 = 0;
    if (r->mask2) {
        uint32_t held = 0u, pressed = 0u;
        if (io->get_buttons && io->get_buttons(io->context, &held, &pressed) == OPENSPM_OK &&
            (held & OPENSPM_WII_BTN_2) && io->set_buttons)
            (void)io->set_buttons(io->context, held & ~OPENSPM_WII_BTN_2); /* no jump from the held 2 */
        else
            r->mask2 = 0;
    }
    if (level != 0u) return;
    if (r->real_ids) {
        /* Playing an added character = his id in MarioWork. */
        int32_t now = -1, i;
        for (i = 0; i < (int32_t)r->count; ++i)
            if (r->chars[i].game_id == current) now = i;
        if (now != r->active) {
            if (now >= 0) {
                const openspm_roster_character *c = &r->chars[now];
                ++r->activations;
                r->hitbox_frames = 0u;
                snprintf(message, sizeof message, "OpenSPM content: now playing %s (%s, id %u, model %s).", c->name,
                         c->key, (unsigned)c->game_id, c->model);
                openspm_roster_log(io, 0, message);
            }
            r->active = now;
        }
        if (r->pending >= 0 && (now == r->pending || ++r->pending_frames > 600u)) r->pending = -1;
    } else {
    if (r->change_wait) openspm_roster_follow_character_change(r, io);
    if (r->reload_base >= 0) {
        /* Back to the game's own model of the current character: with the flip. */
        const uint32_t base = (uint32_t)r->reload_base;
        if (base == current && !openspm_roster_start_character_change(r, io, base))
            (void)openspm_roster_start_script(io, r->reload_script[base]);
        r->reload_base = -1;
    }
    if (r->pending >= 0) {
        const openspm_roster_character *c = &r->chars[r->pending];
        if (current == c->base) {
            /* His base was the current character, so the game did not switch:
               the flip the game would play, then his model. */
            if (r->pending_was_current && !openspm_roster_start_character_change(r, io, c->base))
                (void)openspm_roster_start_script(io, r->reload_script[c->base]);
            r->active = r->pending;
            r->pending = -1;
            ++r->activations;
            r->hitbox_frames = 0u;
            snprintf(message, sizeof message, "OpenSPM content: now playing %s (%s, model %s).", c->name, c->key,
                     c->model);
            openspm_roster_log(io, 0, message);
        } else if (++r->pending_frames > 600u) { /* the game never switched */
            openspm_roster_restore_tables(r, io);
            r->pending = -1;
        }
        return;
    }
    if (r->active >= 0 && current != r->chars[r->active].base) {
        /* The game changed character on its own (story event, script). */
        openspm_roster_restore_tables(r, io);
        r->active = -1;
    }
    }
    if (r->active >= 0) {
        openspm_roster_anim_alias(r, io);
        openspm_roster_anim_diag(r, io);
        if (++r->hitbox_frames == 30u) {
            /* Half a second in: the size the game actually uses for him. */
            uint32_t wb = openspm_roster_rd(io, SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_HIT_WIDTH, &ok);
            uint32_t hb = openspm_roster_rd(io, SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_HIT_HEIGHT, &ok);
            float w, h;
            memcpy(&w, &wb, sizeof w);
            memcpy(&h, &hb, sizeof h);
            if (ok) {
                snprintf(message, sizeof message, "OpenSPM content: %s's hitbox in play: width %.1f, height %.1f.",
                         r->chars[r->active].name, (double)w, (double)h);
                openspm_roster_log(io, 0, message);
            }
        }
    }
    if (r->active >= 0 && (r->chars[r->active].flags & OPENSPM_CHARACTER_NO_FLIP) && io->get_buttons &&
        io->set_buttons) {
        /* No flip for him: the flip button (A) never reaches the game in play
           (menus and cutscenes, level != 0, keep it). */
        uint32_t held = 0u, pressed = 0u;
        if (io->get_buttons(io->context, &held, &pressed) == OPENSPM_OK && (held & OPENSPM_WII_BTN_A))
            (void)io->set_buttons(io->context, held & ~OPENSPM_WII_BTN_A);
    }
}

#endif
