/* OpenSPM enemies: new enemies in spare rows of the game's enemy tables
 * (provider side of openspm.content's register_enemy,
 * include/openspm/content_service.h; docs/CUSTOM-LEVELS.md "Enemies").
 *
 * With the enemy table patches (state/openspm-code-patches.json,
 * tools/content/enemy_tables.py) the game's 535 tribes and 435 templates live
 * in MEM2, each followed by 64 spare rows that start as copies of row 0. A new
 * enemy takes one spare row of each:
 *   - its tribe: a copy of the look tribe (model, animations, parts, drops)
 *     with the definition's model, HP, attack, score and catch card;
 *   - its template: a copy of the base template (AI scripts, flags) naming
 *     the new tribe, and, when the enemy has a tint, a size, a contact damage
 *     or its own spawn script, a spawn script OpenSPM builds: the base's (or
 *     the mod's) as a child script, then evt_npc_set_color /
 *     evt_npc_set_scale / evt_npc_set_part_attack_power on "me".
 * The game's rows are not touched: every game enemy stays as it is.
 *
 * Runs inside a mod tick with the roster's guest access (openspm_roster_io).
 * Portable C, header-only. tests/m14_content_enemies_test.c. */
#ifndef OPENSPM_CONTENT_ENEMIES_H
#define OPENSPM_CONTENT_ENEMIES_H

#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "openspm/content_roster.h"
#include "openspm/content_service.h"
#include "openspm/spm_evt.h"

#define OPENSPM_ENEMIES_GAME_TRIBES 535u
#define OPENSPM_ENEMIES_GAME_TEMPLATES 435u
#define OPENSPM_ENEMIES_SPARE 64u
#define OPENSPM_ENEMIES_ROW 0x68u
#define OPENSPM_ENEMIES_TRIBES UINT32_C(0x93E9D7D0)    /* where the patches moved npcTribes */
#define OPENSPM_ENEMIES_TEMPLATES UINT32_C(0x93E6B128) /* and npcEnemyTemplates */
/* Three words the patched build has: npcGetTribe's lis of the tribes,
   npcEntryFromSetupEnemy's lis of the templates and its bound. */
#define OPENSPM_ENEMIES_TRIBE_LIS UINT32_C(0x801C8C4C)
#define OPENSPM_ENEMIES_TRIBE_LIS_MOVED UINT32_C(0x3C6093EA)
#define OPENSPM_ENEMIES_TEMPLATE_LIS UINT32_C(0x801BEB24)
#define OPENSPM_ENEMIES_TEMPLATE_LIS_MOVED UINT32_C(0x3C6093E7)
#define OPENSPM_ENEMIES_TEMPLATE_BOUND UINT32_C(0x801BEAF0)
#define OPENSPM_ENEMIES_TEMPLATE_BOUND_MOVED UINT32_C(0x2C0E01F3)

/* NPCTribe fields. */
#define OPENSPM_TRIBE_MODEL 0x00u /* const char * animPoseName */
#define OPENSPM_TRIBE_CARD 0x08u  /* s32 catch card item */
#define OPENSPM_TRIBE_HP 0x18u    /* u8 max HP */
#define OPENSPM_TRIBE_SCORE 0x38u /* s16 points */
#define OPENSPM_TRIBE_ATTACK 0x64u /* u8 attack (tattle) */
/* NPCEnemyTemplate fields. */
#define OPENSPM_TEMPLATE_TRIBE 0x14u
#define OPENSPM_TEMPLATE_SPAWN_SCRIPT 0x30u

typedef struct openspm_enemy_entry {
    char key[48];
    uint32_t tribe, template_id;
} openspm_enemy_entry;

typedef struct openspm_enemies {
    openspm_enemy_entry e[OPENSPM_ENEMIES_SPARE];
    uint32_t count;
    uint32_t me; /* the guest string "me" */
} openspm_enemies;

static inline void openspm_enemies_init(openspm_enemies *s) { memset(s, 0, sizeof *s); }

static inline uint32_t openspm_enemies_tribe(uint32_t id) { return OPENSPM_ENEMIES_TRIBES + id * OPENSPM_ENEMIES_ROW; }
static inline uint32_t openspm_enemies_template(uint32_t id) {
    return OPENSPM_ENEMIES_TEMPLATES + id * OPENSPM_ENEMIES_ROW;
}

static inline int openspm_enemies_model_ok(const char *m) {
    size_t i, len = strlen(m);
    if (len == 0u || len >= 32u) return 0;
    for (i = 0; i < len; ++i)
        if (!((m[i] >= 'a' && m[i] <= 'z') || (m[i] >= 'A' && m[i] <= 'Z') || (m[i] >= '0' && m[i] <= '9') ||
              m[i] == '_'))
            return 0;
    return 1;
}

/* Rewrites one byte / halfword of a big-endian word in guest memory. */
static inline int openspm_enemies_put(const openspm_roster_io *io, uint32_t a, uint32_t v, uint32_t bytes) {
    int ok = 1;
    const uint32_t word = a & ~3u, shift = 8u * (4u - bytes - (a & 3u));
    const uint32_t mask = (bytes == 1u ? 0xFFu : 0xFFFFu) << shift;
    const uint32_t w = openspm_roster_rd(io, word, &ok);
    return ok && openspm_roster_wr(io, word, (w & ~mask) | ((v << shift) & mask));
}

static inline int openspm_enemies_copy_row(const openspm_roster_io *io, uint32_t to, uint32_t from) {
    int ok = 1;
    uint32_t k;
    for (k = 0; k < OPENSPM_ENEMIES_ROW; k += 4u)
        if (!openspm_roster_wr(io, to + k, openspm_roster_rd(io, from + k, &ok)) || !ok) return 0;
    return 1;
}

/* The template's spawn script: the base's (or the mod's) as a child, then the
   tint, the size and the contact damage. 0 when the enemy needs none of them
   (the base's script stays), UINT32_MAX when out of guest memory. */
static inline uint32_t openspm_enemies_spawn_script(openspm_enemies *s, const openspm_roster_io *io,
                                                    const openspm_enemy_def *def, uint32_t base_script) {
    uint32_t words[64], at = 0u, n, i;
    const int tint = def->color[0] | def->color[1] | def->color[2] | def->color[3];
    spm_evt_builder b;
    if (!tint && def->scale == 0.0f && def->contact_damage == 0u && def->spawn_script == 0u) return 0u;
    if (s->me == 0u) s->me = openspm_roster_put(io, "me", 3u);
    if (s->me == 0u) return UINT32_MAX;
    spm_evt_init(&b, words, 64u);
    if (def->spawn_script) spm_evt_run_child(&b, def->spawn_script);
    else if (base_script) spm_evt_run_child(&b, base_script);
    if (tint) {
        const uint32_t a[5] = {s->me, def->color[0], def->color[1], def->color[2], def->color[3]};
        spm_evt_user_func(&b, SPM_R8PE01_EVT_NPC_SET_COLOR, 5u, a);
    }
    if (def->scale != 0.0f) {
        const uint32_t f = SPM_EVT_FLOAT(def->scale);
        const uint32_t a[4] = {s->me, f, f, f};
        spm_evt_user_func(&b, SPM_R8PE01_EVT_NPC_SET_SCALE, 4u, a);
    }
    if (def->contact_damage) {
        const uint32_t a[3] = {s->me, UINT32_MAX /* every part */, def->contact_damage};
        spm_evt_user_func(&b, SPM_R8PE01_EVT_NPC_SET_PART_ATTACK_POWER, 3u, a);
    }
    n = spm_evt_finish(&b);
    if (n == 0u || io->scratch(io->context, 4u * n, &at) != OPENSPM_OK) return UINT32_MAX;
    for (i = 0; i < n; ++i)
        if (!openspm_roster_wr(io, at + 4u * i, words[i])) return UINT32_MAX;
    return at;
}

static inline int openspm_enemies_register(openspm_enemies *s, const openspm_roster_io *io, const openspm_enemy_def *def,
                                           uint32_t *out_template, char *why, size_t why_size) {
    int ok = 1;
    uint32_t i, look, base, tribe_id, template_id, tribe, tmpl, model = 0u, spawn, base_script;
    if (def == NULL || def->header.struct_size < OPENSPM_ENEMY_DEF_V1_SIZE || def->key == NULL) {
        snprintf(why, why_size, "needs a key");
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    }
    if (def->base_template == 0u || def->base_template >= OPENSPM_ENEMIES_GAME_TEMPLATES) {
        snprintf(why, why_size, "base template %u: a game template is 1 to 434", (unsigned)def->base_template);
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    }
    if (def->look_tribe < -1 || def->look_tribe >= (int32_t)OPENSPM_ENEMIES_GAME_TRIBES) {
        snprintf(why, why_size, "look tribe %d: a game tribe is 0 to 534 (-1: the base template's)", (int)def->look_tribe);
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    }
    if (def->model && !openspm_enemies_model_ok(def->model)) {
        snprintf(why, why_size, "model name: 1 to 31 characters of a-z, A-Z, 0-9, _");
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    }
    if (def->max_hp > 255u || def->attack > 255u || def->contact_damage > 99u || def->score < -1 || def->score > 32767) {
        snprintf(why, why_size, "HP and attack are 0 to 255, contact damage 0 to 99, score -1 to 32767");
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    }
    if (!(def->scale == 0.0f || (def->scale >= 0.1f && def->scale <= 10.0f))) {
        snprintf(why, why_size, "scale: 0.1 to 10 (0: as is)");
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    }
    if (def->catch_card != 0u && def->catch_card != OPENSPM_ENEMY_CARD_LOOK &&
        def->catch_card >= OPENSPM_CONTENT_FIRST_ITEM_ID) {
        snprintf(why, why_size, "catch card %u: 0 (none), the look tribe's, or a game card item", (unsigned)def->catch_card);
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    }
    if (def->spawn_script && (def->spawn_script < 0x80000000u || (def->spawn_script & 3u))) {
        snprintf(why, why_size, "spawn script 0x%08X is not a guest address", (unsigned)def->spawn_script);
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    }
    if (def->flags != 0u) {
        snprintf(why, why_size, "unknown flags 0x%X", (unsigned)def->flags);
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    }
    if (openspm_roster_rd(io, OPENSPM_ENEMIES_TRIBE_LIS, &ok) != OPENSPM_ENEMIES_TRIBE_LIS_MOVED ||
        openspm_roster_rd(io, OPENSPM_ENEMIES_TEMPLATE_LIS, &ok) != OPENSPM_ENEMIES_TEMPLATE_LIS_MOVED ||
        openspm_roster_rd(io, OPENSPM_ENEMIES_TEMPLATE_BOUND, &ok) != OPENSPM_ENEMIES_TEMPLATE_BOUND_MOVED || !ok) {
        snprintf(why, why_size, "this build has no enemy tables (the native module predates them: rebuild it)");
        return OPENSPM_ERROR_UNSUPPORTED;
    }
    for (i = 0; i < s->count; ++i)
        if (strcmp(s->e[i].key, def->key) == 0) {
            snprintf(why, why_size, "key already registered");
            return OPENSPM_ERROR_RUNTIME;
        }
    if (s->count == OPENSPM_ENEMIES_SPARE || strlen(def->key) >= sizeof s->e[0].key) {
        snprintf(why, why_size, s->count == OPENSPM_ENEMIES_SPARE ? "no spare enemy rows left" : "key too long");
        return OPENSPM_ERROR_RUNTIME;
    }
    base = openspm_enemies_template(def->base_template);
    look = def->look_tribe >= 0 ? (uint32_t)def->look_tribe : openspm_roster_rd(io, base + OPENSPM_TEMPLATE_TRIBE, &ok);
    base_script = openspm_roster_rd(io, base + OPENSPM_TEMPLATE_SPAWN_SCRIPT, &ok);
    if (!ok) {
        snprintf(why, why_size, "guest memory is closed (register from a mod tick)");
        return OPENSPM_ERROR_RUNTIME;
    }
    if (look >= OPENSPM_ENEMIES_GAME_TRIBES) {
        snprintf(why, why_size, "base template %u names tribe %u, not a game tribe", (unsigned)def->base_template,
                 (unsigned)look);
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    }
    tribe_id = OPENSPM_ENEMIES_GAME_TRIBES + s->count;
    template_id = OPENSPM_ENEMIES_GAME_TEMPLATES + s->count;
    tribe = openspm_enemies_tribe(tribe_id);
    tmpl = openspm_enemies_template(template_id);
    if (def->model) model = openspm_roster_put(io, def->model, (uint32_t)strlen(def->model) + 1u);
    spawn = openspm_enemies_spawn_script(s, io, def, base_script);
    if ((def->model && model == 0u) || spawn == UINT32_MAX) {
        snprintf(why, why_size, "no guest memory left");
        return OPENSPM_ERROR_RUNTIME;
    }
    /* The tribe: the look tribe, then the definition's numbers. */
    if (!openspm_enemies_copy_row(io, tribe, openspm_enemies_tribe(look)) ||
        (model && !openspm_roster_wr(io, tribe + OPENSPM_TRIBE_MODEL, model)) ||
        (def->catch_card != OPENSPM_ENEMY_CARD_LOOK && !openspm_roster_wr(io, tribe + OPENSPM_TRIBE_CARD, def->catch_card)) ||
        (def->max_hp && !openspm_enemies_put(io, tribe + OPENSPM_TRIBE_HP, def->max_hp, 1u)) ||
        (def->attack && !openspm_enemies_put(io, tribe + OPENSPM_TRIBE_ATTACK, def->attack, 1u)) ||
        (def->score >= 0 && !openspm_enemies_put(io, tribe + OPENSPM_TRIBE_SCORE, (uint32_t)def->score, 2u)) ||
        /* The template: the base, naming the new tribe. */
        !openspm_enemies_copy_row(io, tmpl, base) || !openspm_roster_wr(io, tmpl + OPENSPM_TEMPLATE_TRIBE, tribe_id) ||
        (spawn && !openspm_roster_wr(io, tmpl + OPENSPM_TEMPLATE_SPAWN_SCRIPT, spawn))) {
        snprintf(why, why_size, "guest memory is closed (register from a mod tick)");
        return OPENSPM_ERROR_RUNTIME;
    }
    snprintf(s->e[s->count].key, sizeof s->e[0].key, "%s", def->key);
    s->e[s->count].tribe = tribe_id;
    s->e[s->count].template_id = template_id;
    ++s->count;
    if (out_template) *out_template = template_id;
    return OPENSPM_OK;
}

#endif
