/* OpenSPM sample mod: "Glock", a Pixl-style partner that fires Squirps
 * blasts.
 *
 * F7 calls Glock (a Frackle, the little cannon Fracktail spawns) or sends him
 * away. He floats behind Mario's head like a Pixl. Press 1 and he moves in
 * front of Mario (armed); while armed every press of 1 fires a blast. Crouch
 * to put him back behind Mario. While Glock is out the 1 button belongs to
 * him (the current Pixl's ability waits until he is sent away).
 *
 * The blast borrows from Squirps' shot: the same damage type, attacker and
 * flags as the game's own Squirps hit (npcTakeDamage(Mario, part, 32, ...)),
 * the same shot sound (SFX_MAP_TAMARA_SHOT1) and the same direction rule
 * (Mario's facing angle). Squirps' own shot can not be reused outside space:
 * the game only allocates, moves and draws it while Mario is in space mode,
 * a flag that about 170 places in Mario's code read. So the mod flies its own
 * projectile: a game NPC with the Wrackle's cannonball model, which the mod
 * moves every frame and tests against the enemies the game's own attack loop
 * would test (same filters), then asks the game to apply the damage.
 *
 * Everything the game sees is made with its own EVT functions, in the order
 * its map scripts use them (evt_npc_entry, the animation table, decorative
 * flags); after that the mod only writes the position and facing of its own
 * NPCs. It never writes game data it did not create, and it does nothing
 * while the world is frozen (menus, cutscenes, doors).
 *
 * R8PE01 rev 2 only: the first in-game frame checks the Frackle and cannonball
 * tribes, the shot sound name and every function entry it calls, and turns
 * the mod off (logged) on anything else.
 *
 * Portable C: builds as a Windows DLL for OpenSPMNative and as a host object
 * for tests/m14_glock_mod_test.c. */
#include "openspm/content_service.h"
#include "openspm/guest_call.h"
#include "openspm/guest_memory.h"
#include "openspm/input_service.h"
#include "openspm/mod_api.h"
#include "openspm/overlay_service.h"
#include "openspm/runtime_service.h"
#include "openspm/spm_evt.h"
#include "openspm/spm_r8pe01.h"

#include <math.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>

#define MEMBER_END(type, member) (offsetof(type, member) + sizeof(((type *)0)->member))

#define GLOCK_HOTKEY "F7"
#define GLOCK_BLASTS 4u            /* blasts in flight at once (NPCs made once per map) */
#define GLOCK_BLAST_SPEED 7.0f     /* game units per frame (Squirps' shot is about as fast) */
#define GLOCK_BLAST_LIFE 50u       /* frames: ~350 units, about a screen */
#define GLOCK_FIRE_COOLDOWN 8u     /* frames between blasts */
#define GLOCK_POWER 1u             /* damage per blast, like a Squirps shot */
#define GLOCK_SETTLE_FRAMES 30u    /* normal play this long (after a map load) before Glock appears */
#define GLOCK_SPAWN_TIMEOUT 90u    /* frames to wait for the game to create the NPCs */
#define GLOCK_FREE_NPC_MARGIN 8u   /* leave the map this many free NPC slots */
#define GLOCK_PARK_Y (-1000.0f)    /* unused blasts wait below the map (the game's own trick) */
#define GLOCK_MAX_NPCS 512u
#define GLOCK_NAME_COUNT (1u + GLOCK_BLASTS)
#define GLOCK_DAMAGE_SLOTS 8u
#define GLOCK_ANIM_STAND 0u        /* Frackle S_1 */
#define GLOCK_ANIM_SHOT 0u         /* cannonball P_1 */

typedef struct glock_npc {
    uint32_t address; /* NPCEntry in guest memory, 0 = not in this map */
    uint32_t spawn_script;
    uint32_t spawn_pos_word; /* first of the three set_position float words */
    uint32_t spawn_axis_word;
} glock_npc;

typedef struct glock_blast {
    int active;
    float x, y, z, vx; /* vx: speed along the screen's right axis (rx, rz) */
    float rx, rz;
    int32_t axis; /* sprite side on screen */
    uint32_t life;
} glock_blast;

typedef struct glock_mod {
    const openspm_host_api *host;
    openspm_guest_memory_service mem;
    openspm_guest_call_service calls;
    openspm_input_service input;
    openspm_overlay_service overlay;
    int have_input_buttons, have_input_set, have_keys, have_overlay;
    int disabled;
    int built;
    /* Guest strings and scripts (scratch). */
    uint32_t str_names[GLOCK_NAME_COUNT], str_pose, str_mario, frackle_anims, shot_anims;
    uint32_t sound_script, dismiss_script[GLOCK_NAME_COUNT + 1u];
    uint32_t damage_script[GLOCK_DAMAGE_SLOTS], damage_next;
    glock_npc npcs[GLOCK_NAME_COUNT];
    glock_blast blasts[GLOCK_BLASTS];
    /* Behaviour. */
    int wanted;            /* the player wants Glock out (F7, or picked in the Pixl menu) */
    openspm_content_service content;
    int pixl_mode;         /* a real Pixl in the Pixl menu (openspm.content) */
    uint32_t pixl_item;
    int gave_up;
    int armed;
    int hotkey_was_down;
    uint32_t spawn_wait;   /* frames left for a queued spawn */
    uint32_t spawn_failures;
    uint32_t spawn_attempts; /* spawns started since Glock was last seen */
    uint32_t settle;       /* consecutive normal-play frames */
    uint32_t cooldown;
    int have_pos;
    float gx, gy, gz;      /* Glock's current position */
    /* Mario's forward in the world x/z plane: in 2D the screen's right axis
       signed by his facing; in 3D the direction he last moved in (his facing
       value only says left / right on screen). */
    int have_fwd, have_last, last_left;
    float fx, fz, last_mx, last_mz;
    uint32_t diag_frames, diag_logs;
    uint32_t frame;
    uint32_t shots, hits;
} glock_mod;

static glock_mod g_mod;

static const char *const k_names[GLOCK_NAME_COUNT] = {"glock", "glock_b0", "glock_b1", "glock_b2", "glock_b3"};

static void mod_log(openspm_log_level level, const char *message) {
    if (g_mod.host && g_mod.host->log) g_mod.host->log(g_mod.host->context, level, message);
}

static void notify(const char *text) {
    if (g_mod.have_overlay) g_mod.overlay.notify(g_mod.overlay.context, text, 1500u);
}

static int query(const char *id, uint32_t version, void *service, uint32_t size) {
    openspm_abi_header *header = (openspm_abi_header *)service;
    if (!g_mod.host || g_mod.host->header.struct_size < MEMBER_END(openspm_host_api, query_service) ||
        !g_mod.host->query_service)
        return 0;
    memset(service, 0, size);
    header->struct_size = size;
    header->abi_version = version;
    return g_mod.host->query_service(g_mod.host->context, id, version, service) == OPENSPM_OK;
}

static void discover_services(void) {
    const int have_mem = query(OPENSPM_GUEST_MEMORY_SERVICE_ID, OPENSPM_GUEST_MEMORY_SERVICE_VERSION, &g_mod.mem,
                               sizeof g_mod.mem) && g_mod.mem.read32 && g_mod.mem.write32;
    const int have_calls = query(OPENSPM_GUEST_CALL_SERVICE_ID, OPENSPM_GUEST_CALL_SERVICE_VERSION, &g_mod.calls,
                                 sizeof g_mod.calls) && g_mod.calls.call && g_mod.calls.scratch;
    if (query(OPENSPM_INPUT_SERVICE_ID, OPENSPM_INPUT_SERVICE_VERSION, &g_mod.input, sizeof g_mod.input)) {
        g_mod.have_input_buttons = g_mod.input.get_wii_buttons != NULL;
        g_mod.have_input_set = g_mod.input.set_wii_buttons != NULL;
        g_mod.have_keys = g_mod.input.key_down != NULL;
    }
    g_mod.have_overlay = query(OPENSPM_OVERLAY_SERVICE_ID, OPENSPM_OVERLAY_SERVICE_VERSION, &g_mod.overlay,
                               sizeof g_mod.overlay) && g_mod.overlay.notify;
    if (!have_mem || !have_calls || !g_mod.have_keys) {
        g_mod.disabled = 1;
        mod_log(OPENSPM_LOG_WARN, "Glock: this OpenSPM lacks openspm.guest_memory / guest_call / input; off.");
        return;
    }
    /* A real Pixl when OpenSPM can add one; otherwise the F7 partner. */
    if (query(OPENSPM_CONTENT_SERVICE_ID, OPENSPM_CONTENT_SERVICE_VERSION, &g_mod.content, sizeof g_mod.content) &&
        g_mod.content.header.struct_size >= sizeof g_mod.content && g_mod.content.register_pixl &&
        g_mod.content.pixl_active) {
        openspm_pixl_def def;
        memset(&def, 0, sizeof def);
        def.header.struct_size = sizeof def;
        def.header.abi_version = OPENSPM_CONTENT_SERVICE_VERSION;
        def.key = "openspm.sample.glock:glock";
        def.name = "Glock";
        def.description = "A Frackle with a Squirps-style blaster. Press 1 to arm him, 1 again to fire. "
                          "Crouch to stand him down.";
        def.model = "e_kozun";
        def.menu_anim = "S_1";
        def.icon_item = SPM_R8PE01_ITEM_FIRE_BURST; /* a fireball for a Pixl that shoots (Pixls have no own icons yet) */
        if (g_mod.content.register_pixl(g_mod.content.context, &def, &g_mod.pixl_item) == OPENSPM_OK) {
            g_mod.pixl_mode = 1;
            mod_log(OPENSPM_LOG_INFO, "Glock loaded as a Pixl: pick him in the pause menu's Pixl tab; 1 = arm / fire, "
                                      "crouch = stand down.");
            return;
        }
    }
    mod_log(OPENSPM_LOG_INFO, "Glock loaded: press " GLOCK_HOTKEY " in a level to call him; 1 = arm / fire, "
                              "crouch = stand down.");
}

/* ---- guest memory helpers ------------------------------------------------ */
static int read32(uint32_t address, uint32_t *value) {
    return g_mod.mem.read32(g_mod.mem.context, address, value) == OPENSPM_OK;
}
static int read16(uint32_t address, uint16_t *value) {
    return g_mod.mem.read16 && g_mod.mem.read16(g_mod.mem.context, address, value) == OPENSPM_OK;
}
static int write32(uint32_t address, uint32_t value) {
    return g_mod.mem.write32(g_mod.mem.context, address, value) == OPENSPM_OK;
}
static int read_f32(uint32_t address, float *value) {
    uint32_t bits;
    if (!read32(address, &bits)) return 0;
    memcpy(value, &bits, sizeof bits);
    return *value == *value; /* not NaN */
}
static int write_f32(uint32_t address, float value) {
    uint32_t bits;
    memcpy(&bits, &value, sizeof bits);
    return write32(address, bits);
}
/* Up to 31 characters of a guest C string (word reads). */
static int read_cstr(uint32_t address, char out[32]) {
    uint32_t i, w;
    for (i = 0u; i < 32u; i += 4u) {
        if (!read32(address + i, &w)) return 0;
        out[i] = (char)(w >> 24);
        out[i + 1u] = (char)(w >> 16);
        out[i + 2u] = (char)(w >> 8);
        out[i + 3u] = (char)w;
    }
    out[31] = '\0';
    return 1;
}
static uint32_t scratch(uint32_t size) {
    uint32_t address = 0u;
    return g_mod.calls.scratch(g_mod.calls.context, size, &address) == OPENSPM_OK ? address : 0u;
}
static uint32_t put_string(const char *text) {
    const uint32_t n = (uint32_t)strlen(text) + 1u, size = (n + 3u) & ~3u;
    const uint32_t address = scratch(size);
    uint32_t i;
    if (address == 0u) return 0u;
    for (i = 0u; i < size; i += 4u) {
        uint32_t w = 0u, k;
        for (k = 0u; k < 4u; ++k) w = w << 8 | (i + k < n ? (uint8_t)text[i + k] : 0u);
        if (!write32(address + i, w)) return 0u;
    }
    return address;
}
static uint32_t put_script(const uint32_t *words, uint32_t count) {
    const uint32_t address = count ? scratch(count * 4u) : 0u;
    uint32_t i;
    if (address == 0u) return 0u;
    for (i = 0u; i < count; ++i)
        if (!write32(address + 4u * i, words[i])) return 0u;
    return address;
}

static int start_script(uint32_t script) {
    const uint32_t args[3] = {script, 0u, 0u}; /* evtEntry(script, priority 0, flags 0) */
    return script != 0u && g_mod.calls.call(g_mod.calls.context, SPM_R8PE01_EVT_ENTRY, 3u, args) == OPENSPM_OK;
}

/* ---- revision check and scripts, once ------------------------------------ */
static int tribe_is(uint32_t tribe, const char *pose, uint32_t *anim_defs) {
    char name[32];
    uint32_t pose_ptr = 0u;
    const uint32_t entry = SPM_R8PE01_NPC_TRIBES + tribe * SPM_R8PE01_NPC_TRIBE_SIZE;
    return read32(entry + SPM_R8PE01_NPC_TRIBE_POSE, &pose_ptr) && read_cstr(pose_ptr, name) &&
           strcmp(name, pose) == 0 && read32(entry + SPM_R8PE01_NPC_TRIBE_ANIM_DEFS, anim_defs) &&
           *anim_defs >= UINT32_C(0x80000000) && *anim_defs < UINT32_C(0x81800000);
}

static int verify_revision(char *why, size_t size) {
    static const uint32_t functions[] = {
        SPM_R8PE01_EVT_ENTRY, SPM_R8PE01_EVT_NPC_ENTRY, SPM_R8PE01_EVT_NPC_SET_PROPERTY,
        SPM_R8PE01_EVT_NPC_SET_ANIM, SPM_R8PE01_EVT_NPC_FLAG8_ONOFF, SPM_R8PE01_EVT_NPC_ANIMFLAG_ONOFF,
        SPM_R8PE01_EVT_NPC_ADD_FLIP_PART, SPM_R8PE01_EVT_NPC_SET_POSITION, SPM_R8PE01_EVT_NPC_SET_AXIS,
        SPM_R8PE01_EVT_NPC_DELETE, SPM_R8PE01_EVT_NPC_TAKE_DAMAGE, SPM_R8PE01_EVT_SND_SFXON};
    char sfx[32];
    uint32_t i, word;
    for (i = 0u; i < sizeof functions / sizeof functions[0]; ++i) {
        /* Every one of them starts with stwu r1, -n(r1). */
        if (!read32(functions[i], &word) || (word & 0xFFFF0000u) != 0x94210000u) {
            snprintf(why, size, "no function entry at 0x%08X", (unsigned)functions[i]);
            return 0;
        }
    }
    if (!tribe_is(SPM_R8PE01_TRIBE_FRACKLE, "e_kozun", &g_mod.frackle_anims) ||
        !tribe_is(SPM_R8PE01_TRIBE_WRACKLE_SHOT, "e_kozun", &g_mod.shot_anims)) {
        snprintf(why, size, "tribes %u/%u are not the Frackle and its cannonball",
                 (unsigned)SPM_R8PE01_TRIBE_FRACKLE, (unsigned)SPM_R8PE01_TRIBE_WRACKLE_SHOT);
        return 0;
    }
    if (!read_cstr(SPM_R8PE01_SFX_SQUIRPS_SHOT_STRING, sfx) || strcmp(sfx, "SFX_MAP_TAMARA_SHOT1") != 0) {
        snprintf(why, size, "no Squirps shot sound name at 0x%08X", (unsigned)SPM_R8PE01_SFX_SQUIRPS_SHOT_STRING);
        return 0;
    }
    return 1;
}

/* One NPC, the way the game's own map scripts make a scripted flying NPC:
       evt_npc_entry name, "e_kozun", 0
       evt_npc_set_property name, 14, <tribe animation table>
       evt_npc_set_anim name, <anim>, 1
       evt_npc_flag8_onoff name, 1, 0x0C420004
       evt_npc_animflag_onoff name, 1, 0x20
       evt_npc_add_flip_part name
       evt_npc_set_position name, x, y, z      ; rewritten before each start
       evt_npc_set_axis_movement_unit name, d  ; rewritten before each start */
static int build_spawn(glock_npc *npc, uint32_t name, uint32_t anim_defs, uint32_t anim) {
    uint32_t words[64], count, pos_index, axis_index;
    spm_evt_builder b;
    const uint32_t entry[3] = {name, g_mod.str_pose, 0u};
    const uint32_t prop[3] = {name, SPM_R8PE01_NPC_PROPERTY_ANIM_DEFS, anim_defs};
    const uint32_t set_anim[3] = {name, anim, 1u};
    const uint32_t flags[3] = {name, 1u, SPM_R8PE01_NPC_FLAG8_DECOR};
    const uint32_t animflags[3] = {name, 1u, SPM_R8PE01_NPC_ANIMFLAG_DECOR};
    const uint32_t pos[4] = {name, SPM_EVT_FLOAT(0.0f), SPM_EVT_FLOAT(GLOCK_PARK_Y), SPM_EVT_FLOAT(0.0f)};
    const uint32_t axis[2] = {name, 1u};
    spm_evt_init(&b, words, 64u);
    spm_evt_user_func(&b, SPM_R8PE01_EVT_NPC_ENTRY, 3u, entry);
    spm_evt_user_func(&b, SPM_R8PE01_EVT_NPC_SET_PROPERTY, 3u, prop);
    spm_evt_user_func(&b, SPM_R8PE01_EVT_NPC_SET_ANIM, 3u, set_anim);
    spm_evt_user_func(&b, SPM_R8PE01_EVT_NPC_FLAG8_ONOFF, 3u, flags);
    spm_evt_user_func(&b, SPM_R8PE01_EVT_NPC_ANIMFLAG_ONOFF, 3u, animflags);
    spm_evt_user_func(&b, SPM_R8PE01_EVT_NPC_ADD_FLIP_PART, 1u, &name);
    spm_evt_user_func(&b, SPM_R8PE01_EVT_NPC_SET_POSITION, 4u, pos);
    pos_index = b.count - 3u;
    spm_evt_user_func(&b, SPM_R8PE01_EVT_NPC_SET_AXIS, 2u, axis);
    axis_index = b.count - 1u;
    count = spm_evt_finish(&b);
    npc->spawn_script = put_script(words, count);
    npc->spawn_pos_word = npc->spawn_script + 4u * pos_index;
    npc->spawn_axis_word = npc->spawn_script + 4u * axis_index;
    return npc->spawn_script != 0u;
}

static int build(void) {
    char why[128], message[200];
    uint32_t words[16], count, i;
    spm_evt_builder b;
    if (!verify_revision(why, sizeof why)) {
        g_mod.disabled = 1;
        snprintf(message, sizeof message, "Glock: %s (not R8PE01 rev 2?); off.", why);
        mod_log(OPENSPM_LOG_WARN, message);
        return 0;
    }
    g_mod.str_pose = put_string("e_kozun");
    g_mod.str_mario = put_string("mario");
    for (i = 0u; i < GLOCK_NAME_COUNT; ++i) g_mod.str_names[i] = put_string(k_names[i]);
    for (i = 0u; i < GLOCK_NAME_COUNT; ++i) {
        const int glock = i == 0u;
        if (!build_spawn(&g_mod.npcs[i], g_mod.str_names[i], glock ? g_mod.frackle_anims : g_mod.shot_anims,
                         glock ? GLOCK_ANIM_STAND : GLOCK_ANIM_SHOT))
            goto no_memory;
    }
    /* evt_snd_sfxon "SFX_MAP_TAMARA_SHOT1" (the game's own string). */
    {
        const uint32_t sfx = SPM_R8PE01_SFX_SQUIRPS_SHOT_STRING;
        spm_evt_init(&b, words, 16u);
        spm_evt_user_func(&b, SPM_R8PE01_EVT_SND_SFXON, 1u, &sfx);
        count = spm_evt_finish(&b);
        if ((g_mod.sound_script = put_script(words, count)) == 0u) goto no_memory;
    }
    /* evt_npc_delete name, one script per NPC (only started for NPCs that
       exist this frame: evt_npc_delete asserts on a missing name). */
    for (i = 0u; i < GLOCK_NAME_COUNT; ++i) {
        spm_evt_init(&b, words, 16u);
        spm_evt_user_func(&b, SPM_R8PE01_EVT_NPC_DELETE, 1u, &g_mod.str_names[i]);
        count = spm_evt_finish(&b);
        if ((g_mod.dismiss_script[i] = put_script(words, count)) == 0u) goto no_memory;
    }
    /* Damage: evt 0x80108034 "mario", <enemy name>, <part id>, 0, 32, power,
       0, 0 - a ring of scripts so one in flight is never rewritten. */
    for (i = 0u; i < GLOCK_DAMAGE_SLOTS; ++i) {
        const uint32_t args[8] = {g_mod.str_mario, 0u, 0u, 0u, SPM_R8PE01_DAMAGE_SQUIRPS_SHOT, GLOCK_POWER, 0u, 0u};
        spm_evt_init(&b, words, 16u);
        spm_evt_user_func(&b, SPM_R8PE01_EVT_NPC_TAKE_DAMAGE, 8u, args);
        count = spm_evt_finish(&b);
        if ((g_mod.damage_script[i] = put_script(words, count)) == 0u) goto no_memory;
    }
    if (!g_mod.str_pose || !g_mod.str_mario) goto no_memory;
    for (i = 0u; i < GLOCK_NAME_COUNT; ++i)
        if (!g_mod.str_names[i]) goto no_memory;
    g_mod.built = 1;
    mod_log(OPENSPM_LOG_INFO, "Glock: R8PE01 rev 2 checks passed; scripts ready.");
    return 1;
no_memory:
    g_mod.disabled = 1;
    mod_log(OPENSPM_LOG_WARN, "Glock: no scratch memory for the scripts; off.");
    return 0;
}

/* ---- NPC table ------------------------------------------------------------ */
typedef struct npc_table {
    uint32_t entries, capacity, used;
} npc_table;

static int read_npc_table(npc_table *t) {
    uint32_t work = 0u, num = 0u;
    memset(t, 0, sizeof *t);
    if (!read32(SPM_R8PE01_NPC_WORK_PTR, &work) || work == 0u || !read32(work + SPM_R8PE01_NPC_WORK_NUM, &num) ||
        !read32(work + SPM_R8PE01_NPC_WORK_ENTRIES, &t->entries) || t->entries == 0u || num == 0u)
        return 0;
    t->capacity = num < GLOCK_MAX_NPCS ? num : GLOCK_MAX_NPCS;
    return 1;
}

/* Finds Glock's NPCs by name and counts the slots in use. */
static void scan_npcs(npc_table *t) {
    uint32_t i, k, flag8, w0, w1, w2;
    char name[12];
    for (k = 0u; k < GLOCK_NAME_COUNT; ++k) g_mod.npcs[k].address = 0u;
    for (i = 0u; i < t->capacity; ++i) {
        const uint32_t npc = t->entries + i * SPM_R8PE01_NPC_ENTRY_SIZE;
        if (!read32(npc + SPM_R8PE01_NPC_FLAG8, &flag8) || (flag8 & 1u) == 0u) continue;
        ++t->used;
        if (!read32(npc + SPM_R8PE01_NPC_NAME, &w0) || w0 != 0x676C6F63u /* "gloc" */) continue;
        if (!read32(npc + SPM_R8PE01_NPC_NAME + 4u, &w1) || !read32(npc + SPM_R8PE01_NPC_NAME + 8u, &w2)) continue;
        name[0] = 'g'; name[1] = 'l'; name[2] = 'o'; name[3] = 'c';
        name[4] = (char)(w1 >> 24); name[5] = (char)(w1 >> 16); name[6] = (char)(w1 >> 8); name[7] = (char)w1;
        name[8] = (char)(w2 >> 24); name[9] = (char)(w2 >> 16); name[10] = (char)(w2 >> 8); name[11] = '\0';
        for (k = 0u; k < GLOCK_NAME_COUNT; ++k)
            if (strcmp(name, k_names[k]) == 0) g_mod.npcs[k].address = npc;
    }
}

/* An NPC the game's own attack loop (0x801AA478) would test, and only one
   made from a tribe (its model is the tribe's): enemies, never townsfolk. */
static int enemy_part(uint32_t npc, uint32_t *part_out, float *half_w, float *height) {
    uint32_t flag8, flagc, flag46c, move, tribe, tribe_pose, part, i;
    char pose[32], want[32];
    if (!read32(npc + SPM_R8PE01_NPC_FLAG8, &flag8) || (flag8 & 1u) == 0u || (flag8 & 0x40000000u) != 0u) return 0;
    if (!read32(npc + SPM_R8PE01_NPC_FLAGC, &flagc) || (flagc & 0x20u) != 0u) return 0;
    if (!read32(npc + SPM_R8PE01_NPC_FLAG46C, &flag46c) || (flag46c & 0x20000u) != 0u) return 0;
    if (!read32(npc + SPM_R8PE01_NPC_MOVE_MODE, &move) || move == 0u || move == 6u || move == 7u) return 0;
    if (!read32(npc + SPM_R8PE01_NPC_TRIBE_ID, &tribe) || tribe >= SPM_R8PE01_NPC_TRIBE_COUNT ||
        tribe == SPM_R8PE01_TRIBE_WRACKLE_SHOT)
        return 0;
    if (!read32(SPM_R8PE01_NPC_TRIBES + tribe * SPM_R8PE01_NPC_TRIBE_SIZE, &tribe_pose) ||
        !read_cstr(tribe_pose, want) || !read_cstr(npc + SPM_R8PE01_NPC_POSE_NAME, pose) || strcmp(pose, want) != 0)
        return 0;
    if (!read32(npc + SPM_R8PE01_NPC_PARTS, &part)) return 0;
    for (i = 0u; i < 8u && part != 0u; ++i) {
        uint32_t flags = 1u, min_damage = 0xFFFFFFFFu;
        float w = 0.0f, h = 0.0f;
        if (!read32(part + SPM_R8PE01_NPC_PART_FLAGS, &flags) ||
            !read32(part + SPM_R8PE01_NPC_PART_MIN_DAMAGE, &min_damage))
            return 0;
        if ((flags & 1u) == 0u && (int32_t)min_damage > -1) {
            (void)read_f32(part + SPM_R8PE01_NPC_PART_HITBOX, &w);
            (void)read_f32(part + SPM_R8PE01_NPC_PART_HITBOX + 4u, &h);
            *part_out = part;
            *half_w = (w > 1.0f && w < 400.0f ? w : 20.0f) * 0.5f;
            *height = h > 1.0f && h < 400.0f ? h : 20.0f;
            return 1;
        }
        if (!read32(part + SPM_R8PE01_NPC_PART_NEXT, &part)) return 0;
    }
    return 0;
}

static void damage(uint32_t npc, uint32_t part) {
    uint16_t part_id = 0u;
    const uint32_t script = g_mod.damage_script[g_mod.damage_next];
    if (!read16(part + SPM_R8PE01_NPC_PART_ID, &part_id)) return;
    /* args: word 0 header, 1 function, 2 "mario", 3 target name, 4 part id */
    if (!write32(script + 12u, npc + SPM_R8PE01_NPC_NAME) || !write32(script + 16u, part_id)) return;
    if (!start_script(script)) return;
    g_mod.damage_next = (g_mod.damage_next + 1u) % GLOCK_DAMAGE_SLOTS;
    ++g_mod.hits;
}

/* ---- behaviour ------------------------------------------------------------- */
static int facing_left(void) {
    float angle;
    if (!read_f32(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_FACING_ANGLE, &angle)) return 0;
    return spm_r8pe01_facing_left(angle);
}

/* The game's own 2D/3D test (see spm_r8pe01.h); 0 if it can not be read. */
static int in_3d(void) {
    uint32_t id = 0u, camera = 0u;
    uint16_t mode = 0u;
    if (!read32(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_CAMERA_ID, &id) || id >= 16u ||
        !read32(SPM_R8PE01_CAMERA_TABLE + 4u * id, &camera) || camera == 0u ||
        !read16(camera + SPM_R8PE01_CAMERA_3D, &mode))
        return 0;
    return mode != 0u;
}

/* The screen's right axis in the world's x/z plane: row 0 of the current
   camera's view matrix (CamEntry +0x54). Most maps are (1, 0); buildings
   whose 2D plane is turned run along z. (1, 0) if it can not be read. */
static void screen_right(float *rx, float *rz) {
    uint32_t id = 0u, camera = 0u;
    float x = 1.0f, z = 0.0f, len;
    *rx = 1.0f;
    *rz = 0.0f;
    if (!read32(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_CAMERA_ID, &id) || id >= 16u ||
        !read32(SPM_R8PE01_CAMERA_TABLE + 4u * id, &camera) || camera == 0u ||
        !read_f32(camera + SPM_R8PE01_CAMERA_VIEW_MTX, &x) || !read_f32(camera + SPM_R8PE01_CAMERA_VIEW_MTX + 8u, &z))
        return;
    len = sqrtf(x * x + z * z);
    if (!(len > 0.5f && len < 2.0f)) return; /* not a view matrix: keep x */
    *rx = x / len;
    *rz = z / len;
}

static void update_forward(float mx, float mz, int left, int flat, float rx, float rz) {
    const float dx = mx - g_mod.last_mx, dz = mz - g_mod.last_mz;
    const float moved = sqrtf(dx * dx + dz * dz);
    const int step = g_mod.have_last && moved > 0.4f && moved < 50.0f; /* not a teleport */
    if (!g_mod.have_fwd) {
        g_mod.fx = left ? -rx : rx;
        g_mod.fz = left ? -rz : rz;
        g_mod.have_fwd = 1;
    }
    if (!flat) {
        if (step) {
            g_mod.fx = dx / moved;
            g_mod.fz = dz / moved;
        }
    } else {
        /* 2D: the facing value is the verified one (spm_r8pe01_facing_left). */
        g_mod.fx = left ? -rx : rx;
        g_mod.fz = left ? -rz : rz;
    }
    g_mod.last_mx = mx;
    g_mod.last_mz = mz;
    g_mod.last_left = left;
    g_mod.have_last = 1;
}

static void place(uint32_t npc, float x, float y, float z, int32_t axis) {
    (void)write_f32(npc + SPM_R8PE01_NPC_POSITION, x);
    (void)write_f32(npc + SPM_R8PE01_NPC_POSITION + 4u, y);
    (void)write_f32(npc + SPM_R8PE01_NPC_POSITION + 8u, z);
    (void)write32(npc + SPM_R8PE01_NPC_AXIS, (uint32_t)axis);
}

/* His turn about the vertical axis: 0 in 2D (the flip does left / right);
   in 3D the flip stays +1 (his sprite faces world +x) and he turns to
   Mario's forward: about y, (1, 0) turns to (cos a, -sin a). */
static void turn(uint32_t npc, int flat) {
    float degrees = 0.0f;
    if (!flat) {
        degrees = atan2f(-g_mod.fz, g_mod.fx) * (180.0f / 3.14159265f);
        if (degrees < 0.0f) degrees += 360.0f;
    }
    (void)write_f32(npc + SPM_R8PE01_NPC_ROTATION + 4u, degrees);
}

static void spawn_missing(const npc_table *t, float mx, float my, float mz, int left) {
    uint32_t k, missing = 0u;
    char message[128];
    for (k = 0u; k < GLOCK_NAME_COUNT; ++k) missing += g_mod.npcs[k].address == 0u;
    if (missing == 0u) return;
    if (t->used + missing + GLOCK_FREE_NPC_MARGIN > t->capacity) {
        if (g_mod.spawn_failures++ == 0u) {
            snprintf(message, sizeof message, "Glock: this map uses %u of %u NPC slots; he waits for a roomier one.",
                     (unsigned)t->used, (unsigned)t->capacity);
            mod_log(OPENSPM_LOG_INFO, message);
        }
        return;
    }
    for (k = 0u; k < GLOCK_NAME_COUNT; ++k) {
        const glock_npc *n = &g_mod.npcs[k];
        const float y = k == 0u ? my + 30.0f : GLOCK_PARK_Y;
        if (n->address != 0u) continue;
        (void)write32(n->spawn_pos_word, SPM_EVT_FLOAT(mx));
        (void)write32(n->spawn_pos_word + 4u, SPM_EVT_FLOAT(y));
        (void)write32(n->spawn_pos_word + 8u, SPM_EVT_FLOAT(mz));
        (void)write32(n->spawn_axis_word, left ? (uint32_t)-1 : 1u);
        if (!start_script(n->spawn_script)) return; /* queue full: next frame */
    }
    g_mod.spawn_wait = GLOCK_SPAWN_TIMEOUT;
    ++g_mod.spawn_attempts;
}

static void dismiss(void) {
    uint32_t k;
    for (k = 0u; k < GLOCK_NAME_COUNT; ++k)
        if (g_mod.npcs[k].address != 0u) (void)start_script(g_mod.dismiss_script[k]);
    memset(g_mod.blasts, 0, sizeof g_mod.blasts);
    g_mod.armed = 0;
    g_mod.have_pos = 0;
    mod_log(OPENSPM_LOG_INFO, "Glock: sent away.");
    notify("Glock: see you!");
}

static void fire(float rx, float rz, int left) {
    uint32_t k;
    char message[96];
    for (k = 0u; k < GLOCK_BLASTS; ++k) {
        glock_blast *s = &g_mod.blasts[k];
        if (s->active || g_mod.npcs[1u + k].address == 0u) continue;
        s->active = 1;
        s->vx = GLOCK_BLAST_SPEED; /* along (rx, rz): Mario's forward */
        s->rx = rx;
        s->rz = rz;
        s->x = g_mod.gx + 14.0f * rx;
        s->y = g_mod.gy + 4.0f;
        s->z = g_mod.gz + 14.0f * rz;
        s->life = GLOCK_BLAST_LIFE;
        s->axis = left ? -1 : 1;
        g_mod.cooldown = GLOCK_FIRE_COOLDOWN;
        (void)start_script(g_mod.sound_script);
        ++g_mod.shots;
        if (g_mod.shots <= 3u || g_mod.shots % 50u == 0u) {
            snprintf(message, sizeof message, "Glock: blast #%u (%s).", (unsigned)g_mod.shots, left ? "left" : "right");
            mod_log(OPENSPM_LOG_INFO, message);
        }
        return;
    }
}

static void update_blasts(const npc_table *t) {
    uint32_t k, i;
    /* In 2D depth does not count (the player sees everything in one plane);
       in 3D a blast only hits what is in its lane. */
    const float depth = in_3d() ? 40.0f : 1.0e9f;
    for (k = 0u; k < GLOCK_BLASTS; ++k) {
        glock_blast *s = &g_mod.blasts[k];
        const uint32_t npc = g_mod.npcs[1u + k].address;
        int hit = 0;
        if (!s->active) continue;
        if (npc == 0u) {
            s->active = 0;
            continue;
        }
        s->x += s->vx * s->rx;
        s->z += s->vx * s->rz;
        for (i = 0u; i < t->capacity && !hit; ++i) {
            const uint32_t other = t->entries + i * SPM_R8PE01_NPC_ENTRY_SIZE;
            uint32_t part = 0u, flag8;
            float ex, ey, ez, half_w, height;
            if (other == npc || !read32(other + SPM_R8PE01_NPC_FLAG8, &flag8) || (flag8 & 1u) == 0u) continue;
            float along, across;
            if (!read_f32(other + SPM_R8PE01_NPC_POSITION, &ex) || !read_f32(other + SPM_R8PE01_NPC_POSITION + 4u, &ey) ||
                !read_f32(other + SPM_R8PE01_NPC_POSITION + 8u, &ez))
                continue;
            /* Along the screen's right axis (the blast's line) and across it
               (depth: only counts in 3D). */
            along = (ex - s->x) * s->rx + (ez - s->z) * s->rz;
            across = (ez - s->z) * s->rx - (ex - s->x) * s->rz;
            if (fabsf(along) > 120.0f || fabsf(across) > depth) continue;
            if (!enemy_part(other, &part, &half_w, &height)) continue;
            if (fabsf(along) <= half_w + 8.0f && s->y >= ey - 8.0f && s->y <= ey + height + 8.0f) {
                damage(other, part);
                hit = 1;
            }
        }
        if (hit || --s->life == 0u) {
            s->active = 0;
            place(npc, s->x, GLOCK_PARK_Y, s->z, s->axis);
        } else {
            place(npc, s->x, s->y, s->z, s->axis);
        }
    }
}

static void update_glock(float mx, float my, float mz, int left, float rx, float rz) {
    float tx, ty, tz, off;
    if (g_mod.armed) {
        off = 26.0f; /* in front of Mario, chest height */
        ty = my + 14.0f;
    } else {
        off = -16.0f; /* the Pixl spot: behind and above his head */
        ty = my + 34.0f + 2.0f * sinf((float)g_mod.frame * 0.08f);
    }
    /* Along Mario's forward, one unit toward the viewer (across the screen's
       right axis). */
    tx = mx + off * g_mod.fx - 1.0f * rz;
    tz = mz + off * g_mod.fz + 1.0f * rx;
    if (!g_mod.have_pos || fabsf(tx - g_mod.gx) > 400.0f || fabsf(ty - g_mod.gy) > 400.0f ||
        fabsf(tz - g_mod.gz) > 400.0f) {
        g_mod.gx = tx; g_mod.gy = ty; g_mod.gz = tz;
        g_mod.have_pos = 1;
    } else {
        g_mod.gx += (tx - g_mod.gx) * 0.3f;
        g_mod.gy += (ty - g_mod.gy) * 0.3f;
        g_mod.gz += (tz - g_mod.gz) * 0.3f;
    }
    place(g_mod.npcs[0].address, g_mod.gx, g_mod.gy, g_mod.gz, left ? -1 : 1);
    if (g_mod.diag_logs < 40u && ++g_mod.diag_frames >= 180u) {
        /* Where he is against Mario and the camera (buildings, 3D). */
        char message[240];
        uint32_t cam = 0u;
        (void)read32(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_CAMERA_ID, &cam);
        g_mod.diag_frames = 0u;
        ++g_mod.diag_logs;
        snprintf(message, sizeof message,
                 "Glock: at (%.0f, %.0f, %.0f), Mario (%.0f, %.0f, %.0f), forward (%.2f, %.2f), screen right (%.2f, "
                 "%.2f), camera %u, %s.",
                 (double)g_mod.gx, (double)g_mod.gy, (double)g_mod.gz, (double)mx, (double)my, (double)mz,
                 (double)g_mod.fx, (double)g_mod.fz, (double)rx, (double)rz, (unsigned)cam, in_3d() ? "3D" : "2D");
        mod_log(OPENSPM_LOG_INFO, message);
    }
}

static void tick(void) {
    uint32_t seq = 0u, level = 1u, held = 0u, pressed = 0u;
    uint16_t motion = 0xFFFFu;
    float mx, my, mz;
    int hotkey = 0, edge, left, flat;
    float rx = 1.0f, rz = 0.0f;
    npc_table table;
    if (g_mod.disabled) return;
    ++g_mod.frame;
    if (g_mod.cooldown) --g_mod.cooldown;
    if (g_mod.pixl_mode) {
        /* Picked in (or put away from) the Pixl menu. */
        const int active = g_mod.content.pixl_active(g_mod.content.context, g_mod.pixl_item) == 1;
        if (!active) g_mod.gave_up = 0;
        edge = active ? (!g_mod.wanted && !g_mod.gave_up) : g_mod.wanted;
    } else {
        hotkey = g_mod.input.key_down(g_mod.input.context, GLOCK_HOTKEY) == 1;
        edge = hotkey && !g_mod.hotkey_was_down;
        g_mod.hotkey_was_down = hotkey;
    }
    if (!read32(SPM_R8PE01_SEQ_WORK, &seq) || seq != SPM_R8PE01_SEQ_GAME) {
        g_mod.settle = 0u;
        g_mod.spawn_wait = 0u;
        g_mod.spawn_attempts = 0u;
        g_mod.have_pos = 0;
        g_mod.have_last = g_mod.have_fwd = 0; /* a new map: forward from his facing again */
        memset(g_mod.blasts, 0, sizeof g_mod.blasts);
        return;
    }
    if (!g_mod.built && !build()) return;
    /* The world is frozen (menu, cutscene, door): leave everything alone. */
    (void)read32(SPM_R8PE01_SYSTEM_LEVEL, &level);
    if (level != 0u) {
        g_mod.settle = 0u;
        return;
    }
    if (g_mod.settle < GLOCK_SETTLE_FRAMES) ++g_mod.settle;
    if (!g_mod.wanted && !edge) return; /* nothing of ours in the game: no reads */
    if (!read_npc_table(&table)) return;
    scan_npcs(&table);
    if (g_mod.npcs[0].address == 0u) {
        memset(g_mod.blasts, 0, sizeof g_mod.blasts);
        g_mod.have_pos = 0;
        g_mod.armed = 0;
    } else {
        g_mod.spawn_attempts = 0u;
    }
    if (edge) {
        g_mod.wanted = !g_mod.wanted;
        g_mod.spawn_failures = 0u;
        g_mod.spawn_attempts = 0u;
        g_mod.spawn_wait = 0u;
        if (!g_mod.wanted) {
            dismiss();
            return;
        }
        mod_log(OPENSPM_LOG_INFO, "Glock: called.");
        notify("Glock! (1 = arm / fire, crouch = stand down)");
    }
    if (!read_f32(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_POSITION, &mx) ||
        !read_f32(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_POSITION + 4u, &my) ||
        !read_f32(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_POSITION + 8u, &mz))
        return;
    screen_right(&rx, &rz);
    flat = !in_3d();
    update_forward(mx, mz, facing_left(), flat, rx, rz);
    /* His sprite and "left" follow Mario's forward on screen (in 3D the
       turn does it, see turn()). */
    left = flat ? g_mod.fx * rx + g_mod.fz * rz < 0.0f : 0;
    if (g_mod.spawn_wait) --g_mod.spawn_wait;
    if (g_mod.spawn_wait == 0u && g_mod.settle >= GLOCK_SETTLE_FRAMES) {
        if (g_mod.spawn_attempts >= 2u) {
            /* Twice asked, never appeared: never ask a third time (a missed
               scan must not fill the map with copies). */
            g_mod.wanted = 0;
            g_mod.gave_up = 1;
            mod_log(OPENSPM_LOG_WARN, "Glock: the game did not create his NPCs; staying away.");
            notify("Glock could not come");
            return;
        }
        spawn_missing(&table, mx, my, mz, left);
    }
    if (g_mod.npcs[0].address == 0u) return;

    /* The 1 button is Glock's while he is out. */
    if (g_mod.have_input_buttons &&
        g_mod.input.get_wii_buttons(g_mod.input.context, &held, &pressed) == OPENSPM_OK) {
        if (g_mod.have_input_set && (held & OPENSPM_WII_BTN_1) != 0u)
            (void)g_mod.input.set_wii_buttons(g_mod.input.context, held & ~OPENSPM_WII_BTN_1);
    }
    (void)read16(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_MOTION_ID, &motion);
    if (motion == SPM_R8PE01_MOT_CROUCH) {
        if (g_mod.armed) notify("Glock: standing down");
        g_mod.armed = 0;
    } else if ((pressed & OPENSPM_WII_BTN_1) != 0u) {
        if (!g_mod.armed) {
            g_mod.armed = 1;
            notify("Glock: armed");
        } else if (g_mod.cooldown == 0u) {
            fire(g_mod.fx, g_mod.fz, left);
        }
    }
    update_glock(mx, my, mz, left, rx, rz);
    turn(g_mod.npcs[0].address, flat);
    update_blasts(&table);
}

static void OPENSPM_CALL glock_event(void *context, openspm_mod_event event_id, const void *event_data) {
    (void)context;
    (void)event_data;
    switch (event_id) {
    case OPENSPM_MOD_EVENT_STARTUP:
        discover_services();
        break;
    case OPENSPM_MOD_EVENT_TICK_BEGIN:
        tick();
        break;
    case OPENSPM_MOD_EVENT_SHUTDOWN: {
        char message[96];
        snprintf(message, sizeof message, "Glock: %u blasts, %u hits this session.", (unsigned)g_mod.shots,
                 (unsigned)g_mod.hits);
        mod_log(OPENSPM_LOG_INFO, message);
        break;
    }
    default:
        break;
    }
}

OPENSPM_EXPORT int OPENSPM_CALL openspm_mod_query(uint32_t requested_abi, const openspm_host_api *host,
                                                  openspm_mod_api *out_api) {
    if (requested_abi != OPENSPM_ABI_VERSION || !host || !out_api || host->header.abi_version != OPENSPM_ABI_VERSION ||
        host->header.struct_size < MEMBER_END(openspm_host_api, profile_end) ||
        out_api->header.struct_size < sizeof(*out_api))
        return OPENSPM_ERROR_ABI_MISMATCH;
    memset(&g_mod, 0, sizeof g_mod);
    g_mod.host = host;
    memset(out_api, 0, sizeof(*out_api));
    out_api->header.struct_size = sizeof(*out_api);
    out_api->header.abi_version = OPENSPM_ABI_VERSION;
    out_api->mod_id = "openspm.sample.glock";
    out_api->mod_version = "1.0";
    out_api->on_event = glock_event;
    return OPENSPM_OK;
}
