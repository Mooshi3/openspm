/* OpenSPM followers: NPCs that walk behind the player through the whole game
 * (provider side of openspm.content's register_follower,
 * include/openspm/content_service.h; docs/CONTENT-API.md "Followers").
 *
 * A follower is an ordinary game NPC, made the way the game's own map scripts
 * make a scripted NPC (evt_npc_entry with the model, an animation table, the
 * decorative flags, the flip part), so the game draws him: depth, lighting,
 * his shadow, 2D and 3D. OpenSPM then moves him every frame:
 *
 *  - a trail of the player's positions (one point every few units); the
 *    follower walks to the point `distance` along the trail behind the
 *    player, at walking or running pace with the matching animation, and
 *    stands when he is there;
 *  - grounded followers keep to the ground: while the player is in the air
 *    (a jump or fall move) the trail keeps his last height on the ground, and
 *    the follower eases to new heights instead of jumping with him;
 *  - he faces where he walks (left / right in 2D, his heading in 3D);
 *  - a door, a pipe or anything that clears the map's NPCs: he is made again
 *    behind the player on the next map; a cutscene that deletes him: made
 *    again; far behind (a warp): placed at the target;
 *  - where the player is not (a scene that hides Mario, MarioWork flag8
 *    0x1, for a moment or longer): he leaves too, and comes back behind
 *    the player when the player does.
 *
 * Runs once per frame inside the mod tick with the roster's guest access
 * (openspm_roster_io); costs nothing while no follower is registered.
 * Portable C, header-only, no game code. tests/m14_content_followers_test.c
 * drives it against a fake game. */
#ifndef OPENSPM_CONTENT_FOLLOWERS_H
#define OPENSPM_CONTENT_FOLLOWERS_H

#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "openspm/content_roster.h"
#include "openspm/content_service.h"
#include "openspm/spm_evt.h"
#include "openspm/spm_r8pe01.h"

#define OPENSPM_FOLLOWERS_MAX 8u
#define OPENSPM_FOLLOWER_TRAIL 128u           /* trail points */
#define OPENSPM_FOLLOWER_TRAIL_STEP 4.0f      /* units between points */
#define OPENSPM_FOLLOWER_WALK_SPEED 2.2f      /* units per frame */
#define OPENSPM_FOLLOWER_RUN_SPEED 5.5f
#define OPENSPM_FOLLOWER_RUN_GAP 40.0f        /* this far past his place: he runs */
#define OPENSPM_FOLLOWER_WARP_GAP 400.0f      /* this far: he is placed at once */
#define OPENSPM_FOLLOWER_SPAWN_WAIT 30u       /* frames for a spawn script to take effect */
#define OPENSPM_FOLLOWER_NPC_MARGIN 4u        /* NPC slots left free for the map */
#define OPENSPM_FOLLOWER_AWAY_FRAMES 10u      /* the player hidden this long: followers leave */

enum { OPENSPM_FOLLOWER_STAND = 0, OPENSPM_FOLLOWER_WALK = 1, OPENSPM_FOLLOWER_RUN = 2 };

typedef struct openspm_follower {
    char key[48], model[32], anim[3][24];
    float distance;
    uint32_t flags;
    int enabled;
    /* guest (after build) */
    uint32_t str_name, str_model, anim_defs, spawn_script, spawn_pos, spawn_axis, delete_script, anim_script[3];
    /* play */
    uint32_t npc, spawn_wait, delete_wait, spawns;
    int placed, state, logged_anims;
    uint32_t still; /* frames without a step: he stands after a few */
    float x, y, z, heading_x, heading_z;
} openspm_follower;

typedef struct openspm_followers {
    openspm_follower f[OPENSPM_FOLLOWERS_MAX];
    uint32_t count;
    int built, disabled, closed;
    /* the player's trail (newest at head) */
    float trail[OPENSPM_FOLLOWER_TRAIL][3];
    uint32_t trail_head, trail_len;
    float ground_y;
    int have_ground;
    uint32_t hidden_frames; /* frames the player has been hidden */
    uint32_t away_logs;
} openspm_followers;

static inline void openspm_followers_init(openspm_followers *s) { memset(s, 0, sizeof *s); }

/* ---- registration ---------------------------------------------------------- */

/* An animation name: 1..23 characters of [A-Za-z0-9_]. */
static inline int openspm_followers_anim_ok(const char *s) {
    size_t n = 0u;
    for (; s[n]; ++n) {
        const char c = s[n];
        if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '_')) return 0;
    }
    return n >= 1u && n <= 23u;
}

static inline int openspm_followers_register(openspm_followers *s, const openspm_follower_def *def, uint32_t *out_id,
                                             char *why, size_t why_size) {
    openspm_follower *f;
    const char *anim_default[3] = {"S_1", "W_1", "R_1"};
    const char *anims[3];
    uint32_t i;
    if (!def || !out_id || def->header.struct_size < OPENSPM_FOLLOWER_DEF_V1_SIZE) {
        snprintf(why, why_size, "follower: definition too small");
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    }
    if (!def->key || !openspm_roster_text_ok(def->key, 3u, 47u, 0) || !strchr(def->key, ':')) {
        snprintf(why, why_size, "follower key must be \"<mod>:<name>\" ([a-z0-9_.:-], 3..47)");
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    }
    if (!def->model || !openspm_roster_text_ok(def->model, 1u, 31u, 1)) {
        snprintf(why, why_size, "%s: model must be 1..31 characters of [a-z0-9_]", def->key);
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    }
    anims[0] = def->stand_anim;
    anims[1] = def->walk_anim;
    anims[2] = def->run_anim;
    for (i = 0; i < 3u; ++i) {
        if (!anims[i]) anims[i] = anim_default[i];
        if (!openspm_followers_anim_ok(anims[i])) {
            snprintf(why, why_size, "%s: animation names are 1..23 characters of [A-Za-z0-9_]", def->key);
            return OPENSPM_ERROR_INVALID_ARGUMENT;
        }
    }
    if (def->flags & ~OPENSPM_FOLLOWER_FLAGS_KNOWN) {
        snprintf(why, why_size, "%s: unknown follower flags", def->key);
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    }
    if (!(def->distance >= 0.0f && def->distance <= 400.0f)) {
        snprintf(why, why_size, "%s: distance is 0 (default) .. 400", def->key);
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    }
    if (s->closed) {
        snprintf(why, why_size, "%s: followers register at startup, before the game runs", def->key);
        return OPENSPM_ERROR_RUNTIME;
    }
    for (i = 0; i < s->count; ++i)
        if (strcmp(s->f[i].key, def->key) == 0) {
            snprintf(why, why_size, "%s: key already registered", def->key);
            return OPENSPM_ERROR_RUNTIME;
        }
    if (s->count == OPENSPM_FOLLOWERS_MAX) {
        snprintf(why, why_size, "%s: at most %u followers", def->key, (unsigned)OPENSPM_FOLLOWERS_MAX);
        return OPENSPM_ERROR_RUNTIME;
    }
    f = &s->f[s->count];
    memset(f, 0, sizeof *f);
    snprintf(f->key, sizeof f->key, "%s", def->key);
    snprintf(f->model, sizeof f->model, "%s", def->model);
    for (i = 0; i < 3u; ++i) snprintf(f->anim[i], sizeof f->anim[i], "%s", anims[i]);
    f->distance = def->distance > 0.0f ? def->distance : 45.0f;
    f->flags = def->flags;
    f->enabled = !(def->flags & OPENSPM_FOLLOWER_START_HIDDEN);
    *out_id = s->count++;
    return OPENSPM_OK;
}

static inline int openspm_followers_set(openspm_followers *s, uint32_t id, int enabled) {
    if (id >= s->count) return OPENSPM_ERROR_INVALID_ARGUMENT;
    s->f[id].enabled = enabled != 0;
    return OPENSPM_OK;
}

/* ---- build (first in-game frame) ----------------------------------------------- */

static inline uint32_t openspm_followers_put_script(const openspm_roster_io *io, const uint32_t *words, uint32_t count) {
    uint8_t raw[4u * 64u];
    uint32_t i;
    if (count == 0u || count > 64u) return 0u;
    for (i = 0; i < count; ++i) {
        raw[4u * i] = (uint8_t)(words[i] >> 24);
        raw[4u * i + 1u] = (uint8_t)(words[i] >> 16);
        raw[4u * i + 2u] = (uint8_t)(words[i] >> 8);
        raw[4u * i + 3u] = (uint8_t)words[i];
    }
    return openspm_roster_put(io, raw, 4u * count);
}

/* Each follower's scripts, the way the game's map scripts make a scripted NPC:
       evt_npc_entry name, model, 0
       evt_npc_set_property name, 14, <animation table: 0 stand, 1 walk, 2 run>
       evt_npc_set_anim name, 0, 1
       evt_npc_flag8_onoff name, 1, <decorative>
       evt_npc_animflag_onoff name, 1, <decorative>
       evt_npc_add_flip_part name
       evt_npc_set_position name, x, y, z     ; rewritten before each start
       evt_npc_set_axis_movement_unit name, d ; rewritten before each start
   and one evt_npc_set_anim script per animation, one evt_npc_delete. */
static inline int openspm_followers_build(openspm_followers *s, const openspm_roster_io *io) {
    static const uint32_t functions[8] = {SPM_R8PE01_EVT_ENTRY, SPM_R8PE01_EVT_NPC_ENTRY, SPM_R8PE01_EVT_NPC_SET_PROPERTY,
                                          SPM_R8PE01_EVT_NPC_SET_ANIM, SPM_R8PE01_EVT_NPC_FLAG8_ONOFF,
                                          SPM_R8PE01_EVT_NPC_ANIMFLAG_ONOFF, SPM_R8PE01_EVT_NPC_SET_POSITION,
                                          SPM_R8PE01_EVT_NPC_DELETE};
    uint32_t i, k, words[64], count;
    int ok = 1;
    char name[24], message[320];
    spm_evt_builder b;
    for (i = 0; i < 8u; ++i)
        if ((openspm_roster_rd(io, functions[i], &ok) & 0xFFFF0000u) != 0x94210000u) ok = 0;
    if (!ok) {
        s->disabled = 1;
        openspm_roster_log(io, 1, "OpenSPM followers: this game is not R8PE01 rev 2 (NPC functions differ); off.");
        return 0;
    }
    for (i = 0; i < s->count; ++i) {
        openspm_follower *f = &s->f[i];
        uint8_t defs[4u * 8u];
        uint32_t anim_names[3], d;
        snprintf(name, sizeof name, "openspm_f%02u", (unsigned)i);
        f->str_name = openspm_roster_put(io, name, (uint32_t)strlen(name) + 1u);
        f->str_model = openspm_roster_put(io, f->model, (uint32_t)strlen(f->model) + 1u);
        for (k = 0; k < 3u; ++k) anim_names[k] = openspm_roster_put(io, f->anim[k], (uint32_t)strlen(f->anim[k]) + 1u);
        /* { id, name } pairs, -1 last (as the game's tribe tables). */
        {
            const uint32_t table[8] = {0u, anim_names[0], 1u, anim_names[1], 2u, anim_names[2], 0xFFFFFFFFu, anim_names[0]};
            for (d = 0; d < 8u; ++d) {
                defs[4u * d] = (uint8_t)(table[d] >> 24);
                defs[4u * d + 1u] = (uint8_t)(table[d] >> 16);
                defs[4u * d + 2u] = (uint8_t)(table[d] >> 8);
                defs[4u * d + 3u] = (uint8_t)table[d];
            }
        }
        f->anim_defs = openspm_roster_put(io, defs, sizeof defs);
        if (!f->str_name || !f->str_model || !anim_names[0] || !anim_names[1] || !anim_names[2] || !f->anim_defs) goto no_memory;
        {
            const uint32_t entry[3] = {f->str_name, f->str_model, 0u};
            const uint32_t prop[3] = {f->str_name, SPM_R8PE01_NPC_PROPERTY_ANIM_DEFS, f->anim_defs};
            const uint32_t set_anim[3] = {f->str_name, 0u, 1u};
            const uint32_t flags[3] = {f->str_name, 1u, SPM_R8PE01_NPC_FLAG8_DECOR};
            const uint32_t animflags[3] = {f->str_name, 1u, SPM_R8PE01_NPC_ANIMFLAG_DECOR};
            const uint32_t pos[4] = {f->str_name, SPM_EVT_FLOAT(0.0f), SPM_EVT_FLOAT(0.0f), SPM_EVT_FLOAT(0.0f)};
            const uint32_t axis[2] = {f->str_name, 1u};
            uint32_t pos_index, axis_index;
            spm_evt_init(&b, words, 64u);
            spm_evt_user_func(&b, SPM_R8PE01_EVT_NPC_ENTRY, 3u, entry);
            spm_evt_user_func(&b, SPM_R8PE01_EVT_NPC_SET_PROPERTY, 3u, prop);
            spm_evt_user_func(&b, SPM_R8PE01_EVT_NPC_SET_ANIM, 3u, set_anim);
            spm_evt_user_func(&b, SPM_R8PE01_EVT_NPC_FLAG8_ONOFF, 3u, flags);
            spm_evt_user_func(&b, SPM_R8PE01_EVT_NPC_ANIMFLAG_ONOFF, 3u, animflags);
            spm_evt_user_func(&b, SPM_R8PE01_EVT_NPC_ADD_FLIP_PART, 1u, &f->str_name);
            spm_evt_user_func(&b, SPM_R8PE01_EVT_NPC_SET_POSITION, 4u, pos);
            pos_index = b.count - 3u;
            spm_evt_user_func(&b, SPM_R8PE01_EVT_NPC_SET_AXIS, 2u, axis);
            axis_index = b.count - 1u;
            count = spm_evt_finish(&b);
            f->spawn_script = openspm_followers_put_script(io, words, count);
            f->spawn_pos = f->spawn_script + 4u * pos_index;
            f->spawn_axis = f->spawn_script + 4u * axis_index;
        }
        for (k = 0; k < 3u; ++k) {
            const uint32_t set_anim[3] = {f->str_name, k, 1u};
            spm_evt_init(&b, words, 16u);
            spm_evt_user_func(&b, SPM_R8PE01_EVT_NPC_SET_ANIM, 3u, set_anim);
            count = spm_evt_finish(&b);
            f->anim_script[k] = openspm_followers_put_script(io, words, count);
        }
        spm_evt_init(&b, words, 16u);
        spm_evt_user_func(&b, SPM_R8PE01_EVT_NPC_DELETE, 1u, &f->str_name);
        count = spm_evt_finish(&b);
        f->delete_script = openspm_followers_put_script(io, words, count);
        if (!f->spawn_script || !f->anim_script[0] || !f->anim_script[1] || !f->anim_script[2] || !f->delete_script)
            goto no_memory;
        snprintf(message, sizeof message, "OpenSPM followers: %s (model %s; %s / %s / %s) follows %.0f units behind.",
                 f->key, f->model, f->anim[0], f->anim[1], f->anim[2], (double)f->distance);
        openspm_roster_log(io, 0, message);
    }
    s->built = 1;
    return 1;
no_memory:
    s->disabled = 1;
    openspm_roster_log(io, 1, "OpenSPM followers: no scratch memory for the followers; off.");
    return 0;
}

/* ---- the game --------------------------------------------------------------- */

static inline float openspm_followers_rdf(const openspm_roster_io *io, uint32_t a, int *ok) {
    const uint32_t bits = openspm_roster_rd(io, a, ok);
    float v;
    memcpy(&v, &bits, sizeof v);
    if (!isfinite(v)) *ok = 0;
    return v;
}
static inline void openspm_followers_wrf(const openspm_roster_io *io, uint32_t a, float v) {
    uint32_t bits;
    memcpy(&bits, &v, sizeof bits);
    (void)openspm_roster_wr(io, a, bits);
}

/* Is the player in the air (a jump, a fall, a stomp bounce)? Then the trail
   keeps his last height on the ground. */
static inline int openspm_followers_airborne(uint32_t motion) {
    switch (motion) {
    case 3u: case 6u: case 7u: case 8u: case 9u: case 10u: case 11u: case 14u: case 15u: case 74u:
        return 1;
    default:
        return 0;
    }
}

/* Finds each follower's NPC by name; returns the slots in use, or -1. */
static inline int openspm_followers_scan(openspm_followers *s, const openspm_roster_io *io, uint32_t *capacity) {
    int ok = 1, used = 0;
    const uint32_t work = openspm_roster_rd(io, SPM_R8PE01_NPC_WORK_PTR, &ok);
    uint32_t num, entries, i, k;
    for (k = 0; k < s->count; ++k) s->f[k].npc = 0u;
    if (!ok || work == 0u) return -1;
    num = openspm_roster_rd(io, work + SPM_R8PE01_NPC_WORK_NUM, &ok);
    entries = openspm_roster_rd(io, work + SPM_R8PE01_NPC_WORK_ENTRIES, &ok);
    if (!ok || entries == 0u || num == 0u || num > 1024u) return -1;
    *capacity = num;
    for (i = 0; i < num; ++i) {
        const uint32_t npc = entries + i * SPM_R8PE01_NPC_ENTRY_SIZE;
        if ((openspm_roster_rd(io, npc + SPM_R8PE01_NPC_FLAG8, &ok) & 1u) == 0u || !ok) continue;
        ++used;
        if (openspm_roster_rd(io, npc + SPM_R8PE01_NPC_NAME, &ok) != 0x6F70656Eu /* "open" */) continue;
        for (k = 0; k < s->count; ++k) {
            char want[24];
            snprintf(want, sizeof want, "openspm_f%02u", (unsigned)k);
            if (openspm_roster_str_is(io, npc + SPM_R8PE01_NPC_NAME, want)) s->f[k].npc = npc;
        }
    }
    return used;
}

/* The point `distance` along the trail behind the player (the oldest point
   when the trail is shorter). */
static inline void openspm_followers_place_on_trail(const openspm_followers *s, float distance, float out[3]) {
    uint32_t n, a, b;
    float left = distance;
    memcpy(out, s->trail[s->trail_head], sizeof(float) * 3u);
    for (n = 0; n + 1u < s->trail_len; ++n) {
        float d, dx, dy, dz;
        a = (s->trail_head + OPENSPM_FOLLOWER_TRAIL - n) % OPENSPM_FOLLOWER_TRAIL;
        b = (s->trail_head + OPENSPM_FOLLOWER_TRAIL - n - 1u) % OPENSPM_FOLLOWER_TRAIL;
        dx = s->trail[b][0] - s->trail[a][0];
        dy = s->trail[b][1] - s->trail[a][1];
        dz = s->trail[b][2] - s->trail[a][2];
        d = sqrtf(dx * dx + dz * dz);
        if (d >= left && d > 0.0f) {
            const float t = left / d;
            out[0] = s->trail[a][0] + dx * t;
            out[1] = s->trail[a][1] + dy * t;
            out[2] = s->trail[a][2] + dz * t;
            return;
        }
        left -= d;
        memcpy(out, s->trail[b], sizeof(float) * 3u);
    }
}

static inline void openspm_followers_reset_trail(openspm_followers *s, float x, float y, float z) {
    s->trail_head = 0u;
    s->trail_len = 1u;
    s->trail[0][0] = x;
    s->trail[0][1] = y;
    s->trail[0][2] = z;
    s->ground_y = y;
    s->have_ground = 1;
}

/* ---- per frame ----------------------------------------------------------------- */

static inline void openspm_followers_tick(openspm_followers *s, const openspm_roster_io *io) {
    int ok = 1, used;
    uint32_t seq, level, motion, capacity = 0u, k;
    float mx, my, mz, ty;
    char message[200];
    if (s->count == 0u || s->disabled) return;
    seq = openspm_roster_rd(io, SPM_R8PE01_SEQ_WORK, &ok);
    if (!ok || seq != SPM_R8PE01_SEQ_GAME) {
        /* A door or pipe (the map's NPCs go with the old map), the title, a
           file load: start over behind the player on the next map. */
        for (k = 0; k < s->count; ++k) {
            s->f[k].placed = 0;
            s->f[k].spawn_wait = 0u;
        }
        s->trail_len = 0u;
        return;
    }
    s->closed = 1;
    if (!s->built && !openspm_followers_build(s, io)) return;
    level = openspm_roster_rd(io, SPM_R8PE01_SYSTEM_LEVEL, &ok);
    motion = openspm_roster_rd(io, SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_MOTION_ID, &ok) >> 16;
    mx = openspm_followers_rdf(io, SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_POSITION, &ok);
    my = openspm_followers_rdf(io, SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_POSITION + 4u, &ok);
    mz = openspm_followers_rdf(io, SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_POSITION + 8u, &ok);
    if (!ok || level != 0u) return; /* menus: everyone holds still */
    /* A scene without the player (Mario hidden): his followers are not in it
       either. A short hide is ignored. */
    if (openspm_roster_rd(io, SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_FLAG8, &ok) & SPM_R8PE01_MARIO_FLAG8_HIDDEN) {
        if (s->hidden_frames < OPENSPM_FOLLOWER_AWAY_FRAMES && ++s->hidden_frames == OPENSPM_FOLLOWER_AWAY_FRAMES &&
            s->away_logs < 3u) {
            ++s->away_logs;
            openspm_roster_log(io, 0, "OpenSPM followers: the player is hidden (a scene without him); followers leave until he is back.");
        }
    } else {
        if (s->hidden_frames >= OPENSPM_FOLLOWER_AWAY_FRAMES) s->trail_len = 0u; /* back: start behind him */
        s->hidden_frames = 0u;
    }
    /* The trail: the player's ground position, one point per few units. */
    if (!openspm_followers_airborne(motion)) {
        s->ground_y = my;
        s->have_ground = 1;
    }
    ty = s->have_ground ? s->ground_y : my;
    if (s->trail_len == 0u) {
        openspm_followers_reset_trail(s, mx, ty, mz);
    } else {
        const float *last = s->trail[s->trail_head];
        const float dx = mx - last[0], dz = mz - last[2];
        if (dx * dx + dz * dz > 600.0f * 600.0f) {
            openspm_followers_reset_trail(s, mx, ty, mz); /* a warp */
        } else if (dx * dx + dz * dz >= OPENSPM_FOLLOWER_TRAIL_STEP * OPENSPM_FOLLOWER_TRAIL_STEP ||
                   fabsf(ty - last[1]) > 1.0f) {
            s->trail_head = (s->trail_head + 1u) % OPENSPM_FOLLOWER_TRAIL;
            s->trail[s->trail_head][0] = mx;
            s->trail[s->trail_head][1] = ty;
            s->trail[s->trail_head][2] = mz;
            if (s->trail_len < OPENSPM_FOLLOWER_TRAIL) ++s->trail_len;
        }
    }
    used = openspm_followers_scan(s, io, &capacity);
    if (used < 0) return;
    for (k = 0; k < s->count; ++k) {
        openspm_follower *f = &s->f[k];
        float target[3], dx, dz, gap, speed, step;
        int state;
        if (!f->enabled || s->hidden_frames >= OPENSPM_FOLLOWER_AWAY_FRAMES) {
            if (f->npc && f->delete_wait == 0u && openspm_roster_start_script(io, f->delete_script))
                f->delete_wait = OPENSPM_FOLLOWER_SPAWN_WAIT;
            if (f->delete_wait) --f->delete_wait;
            f->spawn_wait = 0u;
            f->placed = 0;
            continue;
        }
        f->delete_wait = 0u;
        /* An extra place for each follower in line behind the first. */
        openspm_followers_place_on_trail(s, f->distance * (float)(k + 1u), target);
        if (!f->npc) {
            if (f->spawn_wait) {
                --f->spawn_wait;
                continue;
            }
            if ((uint32_t)used + OPENSPM_FOLLOWER_NPC_MARGIN >= capacity) continue; /* the map needs its slots */
            openspm_followers_wrf(io, f->spawn_pos, target[0]);
            openspm_followers_wrf(io, f->spawn_pos + 4u, target[1]);
            openspm_followers_wrf(io, f->spawn_pos + 8u, target[2]);
            (void)openspm_roster_wr(io, f->spawn_axis, 1u);
            if (openspm_roster_start_script(io, f->spawn_script)) {
                f->spawn_wait = OPENSPM_FOLLOWER_SPAWN_WAIT;
                f->x = target[0];
                f->y = target[1];
                f->z = target[2];
                f->placed = 1;
                f->state = OPENSPM_FOLLOWER_STAND;
                if (++f->spawns <= 3u || f->spawns % 50u == 0u) {
                    snprintf(message, sizeof message, "OpenSPM followers: %s joins (%u).", f->key, (unsigned)f->spawns);
                    openspm_roster_log(io, 0, message);
                }
            }
            continue;
        }
        f->spawn_wait = 0u;
        if (!f->placed) {
            f->x = target[0];
            f->y = target[1];
            f->z = target[2];
            f->placed = 1;
        }
        /* Walk (or run) toward his place on the trail. */
        dx = target[0] - f->x;
        dz = target[2] - f->z;
        gap = sqrtf(dx * dx + dz * dz);
        if (gap > OPENSPM_FOLLOWER_WARP_GAP) {
            f->x = target[0];
            f->z = target[2];
            f->y = target[1];
            gap = 0.0f;
        }
        /* He runs when far behind (and keeps running until close again). */
        speed = gap > OPENSPM_FOLLOWER_RUN_GAP || (f->state == OPENSPM_FOLLOWER_RUN && gap > 0.5f * OPENSPM_FOLLOWER_RUN_GAP)
                    ? OPENSPM_FOLLOWER_RUN_SPEED
                    : OPENSPM_FOLLOWER_WALK_SPEED;
        step = 0.0f;
        if (gap > 0.25f) {
            step = gap < speed ? gap : speed;
            f->heading_x = dx / gap;
            f->heading_z = dz / gap;
            f->x += f->heading_x * step;
            f->z += f->heading_z * step;
        }
        /* The animation follows what he did: a step walks (or runs), a few
           frames without one stand. */
        if (step > 0.2f) {
            f->still = 0u;
            state = speed == OPENSPM_FOLLOWER_RUN_SPEED && step > OPENSPM_FOLLOWER_WALK_SPEED ? OPENSPM_FOLLOWER_RUN
                                                                                              : OPENSPM_FOLLOWER_WALK;
        } else {
            state = ++f->still > 6u ? OPENSPM_FOLLOWER_STAND : f->state;
        }
        /* Grounded: ease to the trail's height; floating: the player's. */
        if (f->flags & OPENSPM_FOLLOWER_FLOATING)
            f->y = target[1] + (my - ty);
        else
            f->y += (target[1] - f->y) * 0.25f;
        if (state != f->state && openspm_roster_start_script(io, f->anim_script[state])) f->state = state;
        openspm_followers_wrf(io, f->npc + SPM_R8PE01_NPC_POSITION, f->x);
        openspm_followers_wrf(io, f->npc + SPM_R8PE01_NPC_POSITION + 4u, f->y);
        openspm_followers_wrf(io, f->npc + SPM_R8PE01_NPC_POSITION + 8u, f->z);
        if (f->heading_x != 0.0f || f->heading_z != 0.0f) {
            /* He faces his heading: the flip axis for left / right, and the
               turn about the vertical (0 when he walks along x, as in 2D). */
            float degrees = atan2f(-f->heading_z, fabsf(f->heading_x)) * (180.0f / 3.14159265f);
            (void)openspm_roster_wr(io, f->npc + SPM_R8PE01_NPC_AXIS, f->heading_x < 0.0f ? 0xFFFFFFFFu : 1u);
            if (degrees < 0.0f) degrees += 360.0f;
            openspm_followers_wrf(io, f->npc + SPM_R8PE01_NPC_ROTATION + 4u, degrees);
        }
        if (!f->logged_anims) {
            /* Once: the model's animations (what the mod can name). */
            uint32_t pose = openspm_roster_rd(io, f->npc + 0x44u, &ok), work, groups, poses, group, file, data, header,
                     count, anims, i;
            char names[OPENSPM_ROSTER_MODEL_ANIMS][OPENSPM_ROSTER_ANIM_NAME];
            work = openspm_roster_rd(io, OPENSPM_ROSTER_ANIM_WORK_PTR, &ok);
            groups = openspm_roster_rd(io, work, &ok);
            poses = openspm_roster_rd(io, work + 0x10u, &ok);
            group = ok && pose < 1024u ? openspm_roster_rd(io, poses + pose * 0x188u + 0x10u, &ok) : 0u;
            file = ok && group < 1024u ? openspm_roster_rd(io, groups + group * 16u + 8u, &ok) : 0u;
            data = ok && file ? openspm_roster_rd(io, file + 0xA4u, &ok) : 0u;
            header = ok && data ? openspm_roster_rd(io, data, &ok) : 0u;
            count = ok && header >= 0x80000000u ? openspm_roster_rd(io, header + 0x148u, &ok) : 0u;
            anims = ok && header >= 0x80000000u ? openspm_roster_rd(io, header + 0x1ACu, &ok) : 0u;
            if (ok && count && count <= OPENSPM_ROSTER_MODEL_ANIMS && anims >= 0x80000000u) {
                for (i = 0; i < count; ++i) openspm_roster_rd_str(io, anims + 64u * i, names[i], sizeof names[i]);
                snprintf(message, sizeof message, "%s's model %s has %u animations", f->key, f->model, (unsigned)count);
                openspm_roster_log_names(io, message, names, count);
                f->logged_anims = 1;
            }
            ok = 1;
        }
    }
}

#endif
