/* OpenSPM sample mod: "Francis", a playable character added through
 * OpenSPM's content service (openspm.content, docs/CONTENT-API.md).
 *
 * Francis (the chameleon of chapter 3, the game's tribe 318 "KAMEREKING",
 * model e_kamere) plays as Mario underneath (base character 0), with his own
 * model, a big hitbox, slow feet, his boss sounds for a voice, and his tongue:
 * press down (or F5) and he stops, opens his mouth, shoots his tongue forward
 * (SFX_BS_CMK_TONGUE_GO1) and pulls it back; the first enemy within reach in
 * front of him at mouth height takes a hit (the game's own damage call, the
 * kind Squirps' shot does). With the Hitbox View's F9 on, the tongue's reach
 * is outlined in blue while it is out.
 *
 * His animations are the tribe's own (0x803FD7D0 + 318 * 0x68): S_1, W_1 and
 * R_1 share Mario's names; hurt poses map to D_1; the tongue is the attack
 * series A_1A (mouth open) / A_1B (tongue out) / A_1C (back), a best guess
 * until an in-game run shows it (the roster logs the model's animation names
 * once, "OpenSPM content anim:").
 *
 * F4 (a debug aid while he plays) steps through his model's own animations,
 * one per press, and shows each name: for finding the ones a mod wants.
 *
 * R8PE01 rev 2 only: the first in-game frame checks tribe 318's model name
 * and turns the tongue off (logged) otherwise.
 *
 * Portable C: Windows DLL for OpenSPMNative, host object for
 * tests/m14_francis_mod_test.c. */
#include "openspm/content_service.h"
#include "openspm/guest_call.h"
#include "openspm/guest_memory.h"
#include "openspm/input_service.h"
#include "openspm/mod_api.h"
#include "openspm/overlay_service.h"
#include "openspm/runtime_service.h"
#include "openspm/spm_evt.h"
#include "openspm/spm_r8pe01.h"
#include "openspm/spm_screen.h"

#include <math.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>

#define MEMBER_END(type, member) (offsetof(type, member) + sizeof(((type *)0)->member))

#define FRANCIS_TRIBE 318u
#define FRANCIS_MODEL "e_kamere"
#define FRANCIS_CARD_ITEM 464u /* KAMEREKING's catch card (ename_262) */
#define FRANCIS_ANIM_OPEN "A_1A"
#define FRANCIS_ANIM_TONGUE "A_1B"
#define FRANCIS_ANIM_BACK "A_1C"
#define FRANCIS_ANIM_STAND "S_1"
#define FRANCIS_HOTKEY "F5"
#define FRANCIS_BOX_HOTKEY "F9" /* the Hitbox View's toggle: his tongue box follows it */
#define FRANCIS_BROWSE_HOTKEY "F4"
#define FRANCIS_MAX_ANIMS 512u
#define FRANCIS_CROUCH_FRAMES 2u
#define FRANCIS_SETTLE_FRAMES 8u  /* the stand-up from the crouch */
#define FRANCIS_OPEN_FRAMES 8u
#define FRANCIS_OUT_FRAMES 16u    /* the tongue is out: the hit window */
#define FRANCIS_BACK_FRAMES 12u
#define FRANCIS_SEQUENCE_FRAMES (FRANCIS_SETTLE_FRAMES + FRANCIS_OPEN_FRAMES + FRANCIS_OUT_FRAMES + FRANCIS_BACK_FRAMES)
#define FRANCIS_COOLDOWN_FRAMES (FRANCIS_SEQUENCE_FRAMES + 10u)
#define FRANCIS_REACH 110.0f      /* game units past his front */
#define FRANCIS_LANE 40.0f        /* 3D: half-width of the tongue's lane in depth */
#define FRANCIS_POWER 2u
#define FRANCIS_DAMAGE_SLOTS 4u
#define FRANCIS_MAX_NPCS 512u
#define FRANCIS_TONGUE_RGBA 0x40A0FFFFu
#define FRANCIS_FROZEN_BUTTONS (OPENSPM_WII_BTN_LEFT | OPENSPM_WII_BTN_RIGHT | OPENSPM_WII_BTN_UP | \
                                OPENSPM_WII_BTN_DOWN | OPENSPM_WII_BTN_1 | OPENSPM_WII_BTN_2 | OPENSPM_WII_BTN_A)

typedef struct francis_mod {
    const openspm_host_api *host;
    openspm_content_service content;
    openspm_guest_memory_service mem;
    openspm_guest_call_service calls;
    openspm_input_service input;
    openspm_overlay_service overlay;
    int have_keys, have_buttons, have_overlay, have_boxes;
    int registered, ability_off, checked;
    uint32_t id;
    uint32_t script, str_mario, damage_script[FRANCIS_DAMAGE_SLOTS], damage_next;
    uint32_t crouch_frames, cooldown, freeze, since; /* since: frames into the current tongue */
    int armed, hotkey_was_down, box_key_was_down, boxes_on, box_drawn, hit_done, was_active;
    float fx, fz; /* the tongue's direction (world x/z), fixed at the start */
    uint32_t tongues, hits;
    uint32_t browse_script, browse_name, browse_next; /* F4: his animations, one per press */
    int browse_key_was_down;
} francis_mod;

static francis_mod g_mod;

static void mod_log(openspm_log_level level, const char *message) {
    if (g_mod.host && g_mod.host->log) g_mod.host->log(g_mod.host->context, level, message);
}

static void notify(const char *text, uint32_t ms) {
    if (g_mod.have_overlay) g_mod.overlay.notify(g_mod.overlay.context, text, ms);
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

static int read32(uint32_t address, uint32_t *value) {
    return g_mod.mem.read32(g_mod.mem.context, address, value) == OPENSPM_OK;
}
static int read16(uint32_t address, uint16_t *value) {
    return g_mod.mem.read16 && g_mod.mem.read16(g_mod.mem.context, address, value) == OPENSPM_OK;
}
static int read_f32(uint32_t address, float *value) {
    uint32_t bits;
    if (!read32(address, &bits)) return 0;
    memcpy(value, &bits, sizeof *value);
    return isfinite(*value);
}
static int read_cstr(uint32_t address, char out[32]) {
    uint32_t i, word = 0u;
    for (i = 0u; i < 32u; ++i) {
        if ((i & 3u) == 0u && !read32(address + i, &word)) return 0;
        out[i] = (char)(word >> (24u - 8u * (i & 3u)));
        if (out[i] == '\0') return 1;
    }
    out[31] = '\0';
    return 1;
}
static int cam_read32(void *context, uint32_t address, uint32_t *value) {
    (void)context;
    return read32(address, value);
}

static void startup(void) {
    openspm_character_def def;
    char message[200];
    if (!query(OPENSPM_CONTENT_SERVICE_ID, OPENSPM_CONTENT_SERVICE_VERSION, &g_mod.content, sizeof g_mod.content) ||
        !g_mod.content.register_character || !g_mod.content.character_active) {
        mod_log(OPENSPM_LOG_WARN, "Francis: this OpenSPM has no openspm.content (added characters); off.");
        return;
    }
    memset(&def, 0, sizeof def);
    def.header.struct_size = sizeof def;
    def.header.abi_version = OPENSPM_CONTENT_SERVICE_VERSION;
    def.key = "openspm.sample.francis:francis";
    def.name = "Francis";
    def.description = "The chameleon nerd of Francis's castle. Big, slow, and his tongue reaches far: "
                      "crouch to lash it at the enemy in front.";
    def.model = FRANCIS_MODEL;
    def.base_character = 0u;
    def.icon_item = FRANCIS_CARD_ITEM;
    /* Hurt poses to his one damage animation; the rest share Mario's names
       (S_1, W_1, R_1) or stand. */
    def.anim_map = "D_2=D_1,D_3=D_1,D_4=D_1,D_5=D_1,D_1A=D_1,D_1B=D_1";
    def.voice = "damage=SFX_BS_CMK_DOWN1,acro=none,attack=SFX_BS_CMK_PAKU1";
    /* Much wider and taller than Mario, a little slower. */
    def.properties = "width=72,height=72,walk=1.1,dash=2.2";
    def.flags = OPENSPM_CHARACTER_NO_FLIP; /* flipped, the 3D view would show Mario's second model */
    if (g_mod.content.register_character(g_mod.content.context, &def, &g_mod.id) != OPENSPM_OK) {
        mod_log(OPENSPM_LOG_WARN, "Francis: OpenSPM refused the character (see its log line); off.");
        return;
    }
    g_mod.registered = 1;
    if (!query(OPENSPM_GUEST_MEMORY_SERVICE_ID, OPENSPM_GUEST_MEMORY_SERVICE_VERSION, &g_mod.mem, sizeof g_mod.mem) ||
        !g_mod.mem.read32 || !g_mod.mem.write32 ||
        !query(OPENSPM_GUEST_CALL_SERVICE_ID, OPENSPM_GUEST_CALL_SERVICE_VERSION, &g_mod.calls, sizeof g_mod.calls) ||
        !g_mod.calls.call || !g_mod.calls.scratch)
        g_mod.ability_off = 1;
    g_mod.have_keys = query(OPENSPM_INPUT_SERVICE_ID, OPENSPM_INPUT_SERVICE_VERSION, &g_mod.input, sizeof g_mod.input) &&
                      g_mod.input.key_down;
    g_mod.have_buttons = g_mod.have_keys && g_mod.input.get_wii_buttons && g_mod.input.set_wii_buttons;
    g_mod.have_overlay = query(OPENSPM_OVERLAY_SERVICE_ID, OPENSPM_OVERLAY_SERVICE_VERSION, &g_mod.overlay,
                               sizeof g_mod.overlay) && g_mod.overlay.notify;
    g_mod.have_boxes = g_mod.have_overlay && g_mod.overlay.header.struct_size >= OPENSPM_OVERLAY_SERVICE_V2_SIZE &&
                       g_mod.overlay.draw_boxes;
    snprintf(message, sizeof message, "Francis loaded: Francis (roster id %u) is in the pause menu's character tab%s.",
             (unsigned)g_mod.id, g_mod.ability_off ? " (no tongue: guest memory/calls missing)" : "");
    mod_log(OPENSPM_LOG_INFO, message);
}

static uint32_t put_words(const uint32_t *words, uint32_t count) {
    uint32_t address, i;
    if (count == 0u || g_mod.calls.scratch(g_mod.calls.context, count * 4u, &address) != OPENSPM_OK) return 0u;
    for (i = 0u; i < count; ++i)
        if (g_mod.mem.write32(g_mod.mem.context, address + 4u * i, words[i]) != OPENSPM_OK) return 0u;
    return address;
}

static uint32_t put_string(const char *text) {
    uint32_t words[16] = {0u}, i;
    const uint32_t size = (uint32_t)strlen(text) + 1u;
    if (size > sizeof words) return 0u;
    for (i = 0u; i < size; ++i) words[i / 4u] |= (uint32_t)(uint8_t)text[i] << (24u - 8u * (i % 4u));
    return put_words(words, (size + 3u) / 4u);
}

/* The tongue, on the game's thread (he is held still by the mod meanwhile):
       WAIT_FRM FRANCIS_SETTLE_FRAMES                 ; the stand-up from the crouch
       USER_FUNC evt_mario_set_pose, "A_1A", 0         ; mouth open
       WAIT_FRM 3 ; USER_FUNC evt_mario_set_pose, "A_1A", 0 (the stand-up may overwrite it)
       WAIT_FRM FRANCIS_OPEN_FRAMES - 3
       USER_FUNC evt_snd_sfxon_3d_player, "SFX_BS_CMK_TONGUE_GO1"
       USER_FUNC evt_mario_set_pose, "A_1B", 0         ; tongue out (the mod tests hits)
       WAIT_FRM FRANCIS_OUT_FRAMES
       USER_FUNC evt_snd_sfxon_3d_player, "SFX_BS_CMK_TONGUE_BACK1"
       USER_FUNC evt_mario_set_pose, "A_1C", 0         ; back
       WAIT_FRM FRANCIS_BACK_FRAMES
       USER_FUNC evt_mario_set_pose, "S_1", 0
   and the damage ring (as the Glock sample): evt_npc_take_damage "mario",
   <enemy name>, <part id>, 0, 32, FRANCIS_POWER, 0, 0. */
static void build(void) {
    char pose[32], message[160];
    uint32_t words[96], count, i, tribe_pose = 0u;
    uint32_t s_open, s_tongue, s_back, s_stand, s_go, s_ret;
    spm_evt_builder b;
    g_mod.checked = 1;
    if (!read32(SPM_R8PE01_NPC_TRIBES + FRANCIS_TRIBE * SPM_R8PE01_NPC_TRIBE_SIZE + SPM_R8PE01_NPC_TRIBE_POSE, &tribe_pose) ||
        !read_cstr(tribe_pose, pose) || strcmp(pose, FRANCIS_MODEL) != 0) {
        g_mod.ability_off = 1;
        snprintf(message, sizeof message, "Francis: tribe %u is not %s in this game; tongue off.", (unsigned)FRANCIS_TRIBE,
                 FRANCIS_MODEL);
        mod_log(OPENSPM_LOG_WARN, message);
        return;
    }
    s_open = put_string(FRANCIS_ANIM_OPEN);
    s_tongue = put_string(FRANCIS_ANIM_TONGUE);
    s_back = put_string(FRANCIS_ANIM_BACK);
    s_stand = put_string(FRANCIS_ANIM_STAND);
    s_go = put_string("SFX_BS_CMK_TONGUE_GO1");
    s_ret = put_string("SFX_BS_CMK_TONGUE_BACK1");
    g_mod.str_mario = put_string("mario");
    g_mod.browse_name = put_string("S_1                    "); /* room for any animation name */
    if (!s_open || !s_tongue || !s_back || !s_stand || !s_go || !s_ret || !g_mod.str_mario || !g_mod.browse_name)
        goto no_memory;
    {
        const uint32_t p_open[2] = {s_open, 0u}, p_tongue[2] = {s_tongue, 0u}, p_back[2] = {s_back, 0u},
                       p_stand[2] = {s_stand, 0u};
        spm_evt_init(&b, words, 96u);
        spm_evt_wait_frames(&b, FRANCIS_SETTLE_FRAMES);
        spm_evt_user_func(&b, SPM_R8PE01_EVT_MARIO_SET_POSE, 2u, p_open);
        spm_evt_wait_frames(&b, 3u);
        spm_evt_user_func(&b, SPM_R8PE01_EVT_MARIO_SET_POSE, 2u, p_open);
        spm_evt_wait_frames(&b, FRANCIS_OPEN_FRAMES - 3u);
        spm_evt_user_func(&b, SPM_R8PE01_EVT_SND_SFXON_3D_PLAYER, 1u, &s_go);
        spm_evt_user_func(&b, SPM_R8PE01_EVT_MARIO_SET_POSE, 2u, p_tongue);
        spm_evt_wait_frames(&b, FRANCIS_OUT_FRAMES);
        spm_evt_user_func(&b, SPM_R8PE01_EVT_SND_SFXON_3D_PLAYER, 1u, &s_ret);
        spm_evt_user_func(&b, SPM_R8PE01_EVT_MARIO_SET_POSE, 2u, p_back);
        spm_evt_wait_frames(&b, FRANCIS_BACK_FRAMES);
        spm_evt_user_func(&b, SPM_R8PE01_EVT_MARIO_SET_POSE, 2u, p_stand);
        count = spm_evt_finish(&b);
        if ((g_mod.script = put_words(words, count)) == 0u) goto no_memory;
        {
            const uint32_t p_browse[2] = {g_mod.browse_name, 0u};
            spm_evt_init(&b, words, 16u);
            spm_evt_user_func(&b, SPM_R8PE01_EVT_MARIO_SET_POSE, 2u, p_browse);
            count = spm_evt_finish(&b);
            if ((g_mod.browse_script = put_words(words, count)) == 0u) goto no_memory;
        }
    }
    for (i = 0u; i < FRANCIS_DAMAGE_SLOTS; ++i) {
        const uint32_t args[8] = {g_mod.str_mario, 0u, 0u, 0u, SPM_R8PE01_DAMAGE_SQUIRPS_SHOT, FRANCIS_POWER, 0u, 0u};
        spm_evt_init(&b, words, 16u);
        spm_evt_user_func(&b, SPM_R8PE01_EVT_NPC_TAKE_DAMAGE, 8u, args);
        count = spm_evt_finish(&b);
        if ((g_mod.damage_script[i] = put_words(words, count)) == 0u) goto no_memory;
    }
    return;
no_memory:
    g_mod.ability_off = 1;
    mod_log(OPENSPM_LOG_WARN, "Francis: no scratch memory for his scripts; tongue off.");
}

static int start_script(uint32_t script) {
    const uint32_t args[3] = {script, 0u, 0u};
    return g_mod.calls.call(g_mod.calls.context, SPM_R8PE01_EVT_ENTRY, 3u, args) == OPENSPM_OK;
}

static int facing_left(void) {
    float angle;
    if (!read_f32(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_FACING_ANGLE, &angle)) return 0;
    return spm_r8pe01_facing_left(angle);
}

/* The camera's right axis in the world x/z plane (view matrix row 0), as the
   Glock sample uses it: (1, 0) in most maps, turned in some buildings. */
static void screen_right(float *rx, float *rz) {
    spm_screen_camera cam;
    float len;
    *rx = 1.0f;
    *rz = 0.0f;
    if (!spm_screen_read_camera(&cam, cam_read32, NULL)) return;
    len = sqrtf(cam.view[0][0] * cam.view[0][0] + cam.view[0][2] * cam.view[0][2]);
    if (!(len > 0.5f && len < 2.0f)) return;
    *rx = cam.view[0][0] / len;
    *rz = cam.view[0][2] / len;
}

static int in_3d(void) {
    uint32_t id = 0u, camera = 0u;
    uint16_t mode = 0u;
    if (!read32(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_CAMERA_ID, &id) || id >= 16u ||
        !read32(SPM_R8PE01_CAMERA_TABLE + 4u * id, &camera) || camera == 0u ||
        !read16(camera + SPM_R8PE01_CAMERA_3D, &mode))
        return 0;
    return mode != 0u;
}

/* An enemy the game's attack loop (0x801AA478) would test: its first
   hittable part and its size. */
static int enemy_part(uint32_t npc, uint32_t *part_out, float *w, float *h) {
    uint32_t flag8, flagc, flag46c, move, part, i;
    if (!read32(npc + SPM_R8PE01_NPC_FLAG8, &flag8) || (flag8 & 1u) == 0u || (flag8 & 0x40000000u) != 0u) return 0;
    if (!read32(npc + SPM_R8PE01_NPC_FLAGC, &flagc) || (flagc & 0x20u) != 0u) return 0;
    if (!read32(npc + SPM_R8PE01_NPC_FLAG46C, &flag46c) || (flag46c & 0x20000u) != 0u) return 0;
    if (!read32(npc + SPM_R8PE01_NPC_MOVE_MODE, &move) || move == 0u || move == 6u || move == 7u) return 0;
    if (!read32(npc + SPM_R8PE01_NPC_PARTS, &part)) return 0;
    for (i = 0u; i < 8u && part != 0u; ++i) {
        uint32_t flags = 1u, min_damage = 0xFFFFFFFFu;
        if (!read32(part + SPM_R8PE01_NPC_PART_FLAGS, &flags) ||
            !read32(part + SPM_R8PE01_NPC_PART_MIN_DAMAGE, &min_damage))
            return 0;
        if ((flags & 1u) == 0u && (int32_t)min_damage > -1) {
            if (!read_f32(part + SPM_R8PE01_NPC_PART_HITBOX, w) || !read_f32(part + SPM_R8PE01_NPC_PART_HITBOX + 4u, h))
                return 0;
            if (!(*w > 1.0f && *w < 400.0f)) *w = 20.0f;
            if (!(*h > 1.0f && *h < 400.0f)) *h = 20.0f;
            *part_out = part;
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
    if (g_mod.mem.write32(g_mod.mem.context, script + 12u, npc + SPM_R8PE01_NPC_NAME) != OPENSPM_OK ||
        g_mod.mem.write32(g_mod.mem.context, script + 16u, part_id) != OPENSPM_OK || !start_script(script))
        return;
    g_mod.damage_next = (g_mod.damage_next + 1u) % FRANCIS_DAMAGE_SLOTS;
    ++g_mod.hits;
}

/* His mouth-height band and front edge (from his hitbox as the game uses it). */
static int mouth(float *mx, float *my, float *mz, float *front, float *low, float *high) {
    float w = 40.0f, h = 64.0f;
    if (!read_f32(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_POSITION, mx) ||
        !read_f32(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_POSITION + 4u, my) ||
        !read_f32(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_POSITION + 8u, mz))
        return 0;
    (void)read_f32(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_HIT_WIDTH, &w);
    (void)read_f32(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_HIT_HEIGHT, &h);
    if (!(w > 1.0f && w < 400.0f)) w = 40.0f;
    if (!(h > 1.0f && h < 400.0f)) h = 64.0f;
    *front = 0.5f * w;
    *low = *my + 0.35f * h;
    *high = *my + 0.9f * h;
    return 1;
}

/* The tongue is out: the nearest enemy in front within reach at mouth
   height takes one hit. In 2D depth does not count; in 3D only the lane. */
static void tongue_hit(void) {
    uint32_t work = 0u, num = 0u, entries = 0u, i, best = 0u, best_part = 0u;
    float mx, my, mz, front, low, high, best_along = 1.0e9f;
    const float lane = in_3d() ? FRANCIS_LANE : 1.0e9f;
    if (g_mod.hit_done || !mouth(&mx, &my, &mz, &front, &low, &high)) return;
    if (!read32(SPM_R8PE01_NPC_WORK_PTR, &work) || work == 0u || !read32(work + SPM_R8PE01_NPC_WORK_NUM, &num) ||
        !read32(work + SPM_R8PE01_NPC_WORK_ENTRIES, &entries) || entries == 0u)
        return;
    if (num > FRANCIS_MAX_NPCS) num = FRANCIS_MAX_NPCS;
    for (i = 0u; i < num; ++i) {
        const uint32_t npc = entries + i * SPM_R8PE01_NPC_ENTRY_SIZE;
        uint32_t part = 0u;
        float ex, ey, ez, w, h, along, across;
        if (!enemy_part(npc, &part, &w, &h)) continue;
        if (!read_f32(npc + SPM_R8PE01_NPC_POSITION, &ex) || !read_f32(npc + SPM_R8PE01_NPC_POSITION + 4u, &ey) ||
            !read_f32(npc + SPM_R8PE01_NPC_POSITION + 8u, &ez))
            continue;
        along = (ex - mx) * g_mod.fx + (ez - mz) * g_mod.fz;
        across = (ez - mz) * g_mod.fx - (ex - mx) * g_mod.fz;
        if (along + 0.5f * w < front || along - 0.5f * w > front + FRANCIS_REACH) continue;
        if (fabsf(across) > lane || ey + h < low || ey > high) continue;
        if (along < best_along) {
            best_along = along;
            best = npc;
            best_part = part;
        }
    }
    if (best != 0u) {
        damage(best, best_part);
        g_mod.hit_done = 1;
    }
}

/* F9 (the Hitbox View's key): the tongue's reach in blue while it is out. */
static void tongue_box(int out) {
    spm_screen_camera cam;
    openspm_overlay_box box;
    float mx, my, mz, front, low, high, x0, z0, x1, z1;
    if (!g_mod.have_boxes) return;
    if (!out || !g_mod.boxes_on || !spm_screen_read_camera(&cam, cam_read32, NULL) ||
        !mouth(&mx, &my, &mz, &front, &low, &high)) {
        if (g_mod.box_drawn) (void)g_mod.overlay.draw_boxes(g_mod.overlay.context, "francis.tongue", NULL, 0u);
        g_mod.box_drawn = 0;
        return;
    }
    x0 = mx + g_mod.fx * front;
    z0 = mz + g_mod.fz * front;
    x1 = mx + g_mod.fx * (front + FRANCIS_REACH);
    z1 = mz + g_mod.fz * (front + FRANCIS_REACH);
    if (spm_screen_box(&cam, fminf(x0, x1), low, fminf(z0, z1) - 2.0f, fmaxf(x0, x1), high, fmaxf(z0, z1) + 2.0f,
                       FRANCIS_TONGUE_RGBA, &box) &&
        g_mod.overlay.draw_boxes(g_mod.overlay.context, "francis.tongue", &box, 1u) == OPENSPM_OK)
        g_mod.box_drawn = 1;
}

/* The model header of the player's current animation (spm_r8pe01.h), 0 if
   none. */
static uint32_t player_model(void) {
    uint32_t work = 0u, groups = 0u, poses = 0u, id = 0u, group = 0u, file = 0u, data = 0u, header = 0u;
    if (!read32(SPM_R8PE01_ANIM_WORK_PTR, &work) || work == 0u || !read32(work, &groups) || groups == 0u ||
        !read32(work + 0x10u, &poses) || poses == 0u ||
        !read32(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_POSE_IDS, &id) || id > 1024u ||
        !read32(poses + id * 0x188u + 0x10u, &group) || group > 1024u ||
        !read32(groups + group * 16u + 8u, &file) || file == 0u || !read32(file + 0xA4u, &data) || data == 0u ||
        !read32(data, &header))
        return 0u;
    return header >= 0x80000000u ? header : 0u;
}

/* F4: the next of the model's own animations (the ones before the names
   OpenSPM adds for Mario's poses, which start "mario_" / "Big_"). */
static void browse(void) {
    char name[32], message[96];
    uint32_t header, count = 0u, table = 0u, own = 0u, i;
    if ((header = player_model()) == 0u || !read32(header + 0x148u, &count) || count > FRANCIS_MAX_ANIMS ||
        !read32(header + 0x1ACu, &table) || table == 0u)
        return;
    for (own = 0u; own < count; ++own)
        if (!read_cstr(table + 64u * own, name) || strncmp(name, "mario_", 6) == 0 || strncmp(name, "Big_", 4) == 0)
            break;
    if (own == 0u) return;
    i = g_mod.browse_next % own;
    if (!read_cstr(table + 64u * i, name) || strlen(name) > 23u) return;
    {
        uint32_t words[6] = {0u}, k;
        for (k = 0u; name[k]; ++k) words[k / 4u] |= (uint32_t)(uint8_t)name[k] << (24u - 8u * (k % 4u));
        for (k = 0u; k < 6u; ++k)
            if (g_mod.mem.write32(g_mod.mem.context, g_mod.browse_name + 4u * k, words[k]) != OPENSPM_OK) return;
    }
    if (!start_script(g_mod.browse_script)) return;
    g_mod.browse_next = i + 1u;
    snprintf(message, sizeof message, "Francis animation %u/%u: %s", (unsigned)(i + 1u), (unsigned)own, name);
    notify(message, 4000u);
    mod_log(OPENSPM_LOG_INFO, message);
}

static void lash(const char *why) {
    char message[160];
    float rx, rz;
    if (!start_script(g_mod.script)) return;
    screen_right(&rx, &rz);
    if (facing_left()) {
        rx = -rx;
        rz = -rz;
    }
    g_mod.fx = rx;
    g_mod.fz = rz;
    g_mod.freeze = FRANCIS_SEQUENCE_FRAMES;
    g_mod.cooldown = FRANCIS_COOLDOWN_FRAMES;
    g_mod.since = 0u;
    g_mod.hit_done = 0;
    g_mod.armed = 0;
    ++g_mod.tongues;
    if (g_mod.tongues <= 3u || g_mod.tongues % 25u == 0u) {
        snprintf(message, sizeof message, "Francis: tongue #%u (%s), %u hit(s) so far.", (unsigned)g_mod.tongues, why,
                 (unsigned)g_mod.hits);
        mod_log(OPENSPM_LOG_INFO, message);
    }
}

static void tick(void) {
    uint32_t seq = 0u, level = 1u;
    uint16_t motion = 0xFFFFu;
    int active, hotkey = 0, box_key = 0, out = 0;
    if (!g_mod.registered) return;
    if (g_mod.have_keys) {
        box_key = g_mod.input.key_down(g_mod.input.context, FRANCIS_BOX_HOTKEY) == 1;
        if (box_key && !g_mod.box_key_was_down) g_mod.boxes_on = !g_mod.boxes_on;
        g_mod.box_key_was_down = box_key;
    }
    active = g_mod.content.character_active(g_mod.content.context, g_mod.id) == 1;
    if (active != g_mod.was_active) {
        mod_log(OPENSPM_LOG_INFO, active ? "Francis: Francis is in play." : "Francis: back to the game's characters.");
        if (active) notify("Francis: crouch to lash his tongue", 2500u);
        g_mod.was_active = active;
    }
    if (g_mod.cooldown) --g_mod.cooldown;
    if (!active || g_mod.ability_off) {
        g_mod.crouch_frames = 0u;
        g_mod.freeze = 0u;
        tongue_box(0);
        return;
    }
    if (g_mod.freeze) {
        /* The tongue is playing: he stands still until it is over. */
        uint32_t held = 0u, pressed = 0u;
        --g_mod.freeze;
        ++g_mod.since;
        if (g_mod.have_buttons && g_mod.input.get_wii_buttons(g_mod.input.context, &held, &pressed) == OPENSPM_OK)
            (void)g_mod.input.set_wii_buttons(g_mod.input.context, held & ~FRANCIS_FROZEN_BUTTONS);
        out = g_mod.since > FRANCIS_SETTLE_FRAMES + FRANCIS_OPEN_FRAMES &&
              g_mod.since <= FRANCIS_SETTLE_FRAMES + FRANCIS_OPEN_FRAMES + FRANCIS_OUT_FRAMES;
    }
    if (!read32(SPM_R8PE01_SEQ_WORK, &seq) || seq != SPM_R8PE01_SEQ_GAME) {
        tongue_box(0);
        return;
    }
    if (!g_mod.checked) build();
    if (g_mod.ability_off) return;
    if (out) tongue_hit();
    tongue_box(out);
    if (g_mod.have_keys) hotkey = g_mod.input.key_down(g_mod.input.context, FRANCIS_HOTKEY) == 1;
    (void)read32(SPM_R8PE01_SYSTEM_LEVEL, &level);
    (void)read16(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_MOTION_ID, &motion);
    if (motion == SPM_R8PE01_MOT_CROUCH) {
        if (g_mod.crouch_frames != UINT32_MAX) ++g_mod.crouch_frames;
        if (g_mod.cooldown) g_mod.armed = 0;
    } else {
        g_mod.crouch_frames = 0u;
        g_mod.armed = 1;
    }
    if (level == 0u && g_mod.cooldown == 0u && g_mod.freeze == 0u) {
        if (hotkey && !g_mod.hotkey_was_down) lash(FRANCIS_HOTKEY);
        else if (g_mod.armed && g_mod.crouch_frames >= FRANCIS_CROUCH_FRAMES) lash("crouch");
    }
    g_mod.hotkey_was_down = hotkey;
    if (g_mod.have_keys) {
        const int browse_key = g_mod.input.key_down(g_mod.input.context, FRANCIS_BROWSE_HOTKEY) == 1;
        if (browse_key && !g_mod.browse_key_was_down && level == 0u && g_mod.freeze == 0u) browse();
        g_mod.browse_key_was_down = browse_key;
    }
}

static void OPENSPM_CALL francis_event(void *context, openspm_mod_event event_id, const void *event_data) {
    (void)context;
    (void)event_data;
    if (event_id == OPENSPM_MOD_EVENT_STARTUP) {
        startup();
    } else if (event_id == OPENSPM_MOD_EVENT_TICK_BEGIN) {
        tick();
    } else if (event_id == OPENSPM_MOD_EVENT_SHUTDOWN && g_mod.registered) {
        char message[96];
        snprintf(message, sizeof message, "Francis: %u tongues, %u hits this session.", (unsigned)g_mod.tongues,
                 (unsigned)g_mod.hits);
        mod_log(OPENSPM_LOG_INFO, message);
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
    g_mod.armed = 1;
    memset(out_api, 0, sizeof(*out_api));
    out_api->header.struct_size = sizeof(*out_api);
    out_api->header.abi_version = OPENSPM_ABI_VERSION;
    out_api->mod_id = "openspm.sample.francis";
    out_api->mod_version = "1.0";
    out_api->on_event = francis_event;
    return OPENSPM_OK;
}
