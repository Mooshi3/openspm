/* OpenSPM sample mod: "Koopa Striker", a playable character added through
 * OpenSPM's content service (openspm.content, docs/CONTENT-API.md).
 *
 * The mod only describes the character; OpenSPM gives him a roster id, lists
 * him in the pause menu's character tab after the game's own characters, and
 * does the switching, model swap and clean-up. Underneath he plays as Mario
 * (base character 0: jump, saves; no flip) with the Koopa Striker's model (the
 * soccer Koopa), plus his own ability: press down and he stops, sets one of
 * his shells down in front of him and kicks it, with the enemy's own
 * animations and sound (the plain shell object the Koopa Striker plays with,
 * NPC template 129). F6 does it too while he is being played.
 *
 * R8PE01 rev 2 only: the first in-game frame checks template 129 and turns
 * the ability off (logged) otherwise; OpenSPM checks everything it touches.
 *
 * Portable C: Windows DLL for OpenSPMNative, host object for
 * tests/m14_striker_mod_test.c. */
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

#define STRIKER_CROUCH_FRAMES 2u
#define STRIKER_SETTLE_FRAMES 10u
#define STRIKER_SET_FRAMES 24u
#define STRIKER_PAUSE_FRAMES 12u
#define STRIKER_WINDUP_FRAMES 8u
#define STRIKER_SLIDE_FRAMES 10u
#define STRIKER_SLIDE_STEP 4 /* units per frame toward the shell (it sits STRIKER_AHEAD away) */
#define STRIKER_RECOVER_FRAMES 16u
#define STRIKER_SEQUENCE_FRAMES \
    (STRIKER_SETTLE_FRAMES + STRIKER_SET_FRAMES + STRIKER_PAUSE_FRAMES + STRIKER_WINDUP_FRAMES + STRIKER_SLIDE_FRAMES + \
     STRIKER_RECOVER_FRAMES)
#define STRIKER_COOLDOWN_FRAMES (STRIKER_SEQUENCE_FRAMES + 15u)
#define STRIKER_HOTKEY "F6"
#define STRIKER_MAX_SHELLS 4u /* live shells (each one is a game NPC with its own scripts) */
#define STRIKER_MAX_NPCS 256u
#define STRIKER_FROZEN_BUTTONS (OPENSPM_WII_BTN_LEFT | OPENSPM_WII_BTN_RIGHT | OPENSPM_WII_BTN_UP | \
                                OPENSPM_WII_BTN_DOWN | OPENSPM_WII_BTN_1 | OPENSPM_WII_BTN_2 | OPENSPM_WII_BTN_A)
#define STRIKER_AHEAD 36
#define STRIKER_ABOVE 8

typedef struct striker_mod {
    const openspm_host_api *host;
    openspm_content_service content;
    openspm_guest_memory_service mem;
    openspm_guest_call_service calls;
    openspm_input_service input;
    openspm_overlay_service overlay;
    int have_mem, have_calls, have_keys, have_overlay;
    int registered, ability_off;
    uint32_t id;
    uint32_t script, ahead_word, step_word;
    uint32_t crouch_frames, cooldown, freeze;
    int have_buttons;
    int armed, hotkey_was_down, was_active;
    uint32_t shells;
} striker_mod;

static striker_mod g_mod;

static void mod_log(openspm_log_level level, const char *message) {
    if (g_mod.host && g_mod.host->log) g_mod.host->log(g_mod.host->context, level, message);
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

static void startup(void) {
    openspm_character_def def;
    char message[160];
    if (!query(OPENSPM_CONTENT_SERVICE_ID, OPENSPM_CONTENT_SERVICE_VERSION, &g_mod.content, sizeof g_mod.content) ||
        !g_mod.content.register_character || !g_mod.content.character_active) {
        mod_log(OPENSPM_LOG_WARN, "Striker: this OpenSPM has no openspm.content (added characters); off.");
        return;
    }
    memset(&def, 0, sizeof def);
    def.header.struct_size = sizeof def;
    def.header.abi_version = OPENSPM_CONTENT_SERVICE_VERSION;
    def.key = "openspm.sample.striker:koopa_striker";
    def.name = "Koopa Striker";
    def.description = "The soccer Koopa. Plays like Mario; crouch to drop a shell, then kick it.";
    def.model = "e_k_shooter";
    def.base_character = 0u;
    def.flags = OPENSPM_CHARACTER_NO_FLIP; /* a Koopa does not flip */
    def.icon_item = SPM_R8PE01_ITEM_CARD_KOOPA_STRIKER; /* his catch card's icon in the character tab */
    /* Mario's poses his model names differently (tribe 69's anim defs):
       jump up / fall, and the hurt poses. The rest share names or stand. */
    def.anim_map = "J_1B=J_1_a,J_1C=J_1_b,J_2A=J_1_a,J_2B=J_1_a,J_2C=J_1_b,J_2D=J_1_b,KJ_1A=J_1_a,KJ_1B=J_1_b,"
                   "D_2=D_1,D_3=D_1,D_4=D_1,D_5=D_1,D_1A=D_1,D_1B=D_1";
    /* Koopa Strikers never speak in the game: his shell's sounds instead of
       Mario's voice (hurt, stylish moves, attacks). */
    def.voice = "damage=SFX_E_HIT_KOURA1,acro=SFX_E_JUMP_KOURA1,attack=SFX_E_KICK_KOURA1";
    /* A Koopa is a bit slower than Mario on his feet; his hitbox stays
       Mario's. */
    def.properties = "walk=1.2,dash=2.5";
    if (g_mod.content.register_character(g_mod.content.context, &def, &g_mod.id) != OPENSPM_OK) {
        mod_log(OPENSPM_LOG_WARN, "Striker: OpenSPM refused the character (see its log line); off.");
        return;
    }
    g_mod.registered = 1;
    g_mod.have_mem = query(OPENSPM_GUEST_MEMORY_SERVICE_ID, OPENSPM_GUEST_MEMORY_SERVICE_VERSION, &g_mod.mem,
                           sizeof g_mod.mem) && g_mod.mem.read32 && g_mod.mem.write32;
    g_mod.have_calls = query(OPENSPM_GUEST_CALL_SERVICE_ID, OPENSPM_GUEST_CALL_SERVICE_VERSION, &g_mod.calls,
                             sizeof g_mod.calls) && g_mod.calls.call && g_mod.calls.scratch;
    g_mod.have_keys = query(OPENSPM_INPUT_SERVICE_ID, OPENSPM_INPUT_SERVICE_VERSION, &g_mod.input, sizeof g_mod.input) &&
                      g_mod.input.key_down;
    g_mod.have_buttons = g_mod.have_keys && g_mod.input.get_wii_buttons && g_mod.input.set_wii_buttons;
    g_mod.have_overlay = query(OPENSPM_OVERLAY_SERVICE_ID, OPENSPM_OVERLAY_SERVICE_VERSION, &g_mod.overlay,
                               sizeof g_mod.overlay) && g_mod.overlay.notify;
    if (!g_mod.have_mem || !g_mod.have_calls) g_mod.ability_off = 1;
    snprintf(message, sizeof message,
             "Striker loaded: the Koopa Striker (roster id %u) is in the pause menu's character tab%s.",
             (unsigned)g_mod.id, g_mod.ability_off ? " (no shell ability: guest memory/calls missing)" : "");
    mod_log(OPENSPM_LOG_INFO, message);
}

static int read32(uint32_t address, uint32_t *value) {
    return g_mod.mem.read32(g_mod.mem.context, address, value) == OPENSPM_OK;
}
static int read16(uint32_t address, uint16_t *value) {
    return g_mod.mem.read16 && g_mod.mem.read16(g_mod.mem.context, address, value) == OPENSPM_OK;
}

static uint32_t put_string(const char *text) {
    const uint32_t size = (uint32_t)strlen(text) + 1u;
    uint32_t address, i, word;
    if (g_mod.calls.scratch(g_mod.calls.context, (size + 3u) & ~3u, &address) != OPENSPM_OK) return 0u;
    for (i = 0u; i < size; i += 4u) {
        uint32_t k;
        word = 0u;
        for (k = 0u; k < 4u; ++k) word = (word << 8) | (i + k < size ? (uint8_t)text[i + k] : 0u);
        if (g_mod.mem.write32(g_mod.mem.context, address + i, word) != OPENSPM_OK) return 0u;
    }
    return address;
}

/* The Koopa Striker's own set-and-kick, as the enemy does it (move script
   0x804518A8), on the game's thread:
       (he stops: the mod holds back the d-pad, 1, 2 and A for the whole
       sequence; evt_mario_key_off is the cutscene freeze, black bars too)
       WAIT_FRM STRIKER_SETTLE_FRAMES           ; his stand-up from the crouch
       USER_FUNC evt_snd_sfxon_3d_player, "SFX_E_KOURASHOOTER_SET1"
       USER_FUNC evt_mario_set_pose, "A_1_a", 0  ; puts the shell down
       WAIT_FRM STRIKER_SET_FRAMES
       USER_FUNC evt_mario_get_pos, LW(0), LW(1), LW(2)
       ADD LW(0), <ahead>                      ; rewritten before each run (facing)
       ADD LW(1), 8
       USER_FUNC evt_npc_entry_from_template, 0, 129, LW(0), LW(1), LW(2), LW(10), LW(11)
                                                ; LW(10) = the new NPC's name (npc +0x24)
       USER_FUNC evt_mario_set_pose, "S_1", 0
       WAIT_FRM STRIKER_PAUSE_FRAMES
       SET LW(3), <step>                        ; rewritten before each run (facing)
       USER_FUNC evt_mario_set_pose, "A_1_b", 0  ; the kick
       WAIT_FRM STRIKER_WINDUP_FRAMES
       STRIKER_SLIDE_FRAMES x {                 ; he slides into the shell: the
           USER_FUNC evt_mario_get_pos, ...     ; game's own contact kick sends it
           ADD LW(0), LW(3)                     ; (a damage call only popped it)
           USER_FUNC evt_mario_set_pos, LW(0), LW(1), LW(2)
           WAIT_FRM 1 }
       WAIT_FRM STRIKER_RECOVER_FRAMES
       USER_FUNC evt_mario_set_pose, "S_1", 0
   Fixed waits (never "wait for the animation": a looping one would keep him
   frozen). */
static void build_script(void) {
    char message[200];
    uint32_t tribe = 0u, kick = 0u, words[256], count, address, i, ahead_index, step_index;
    uint32_t s_sfx, s_set, s_kick, s_stand;
    spm_evt_builder b;
    const uint32_t tmpl = SPM_R8PE01_NPC_TEMPLATES + SPM_R8PE01_TEMPLATE_STRIKER_SHELL * SPM_R8PE01_NPC_TEMPLATE_SIZE;
    const uint32_t pos[3] = {SPM_EVT_LW(0), SPM_EVT_LW(1), SPM_EVT_LW(2)};
    const uint32_t spawn[7] = {0u, SPM_R8PE01_TEMPLATE_STRIKER_SHELL, SPM_EVT_LW(0), SPM_EVT_LW(1), SPM_EVT_LW(2),
                               SPM_EVT_LW(10), SPM_EVT_LW(11)};
    if (!read32(tmpl + SPM_R8PE01_NPC_TEMPLATE_TRIBE, &tribe) ||
        !read32(tmpl + SPM_R8PE01_NPC_TEMPLATE_KICK_SCRIPT, &kick) || tribe != SPM_R8PE01_TRIBE_STRIKER_SHELL ||
        kick != SPM_R8PE01_KOOPA_KICK_SCRIPT) {
        g_mod.ability_off = 1;
        snprintf(message, sizeof message, "Striker: NPC template %u is not the Koopa Striker shell; shell ability off.",
                 (unsigned)SPM_R8PE01_TEMPLATE_STRIKER_SHELL);
        mod_log(OPENSPM_LOG_WARN, message);
        return;
    }
    s_sfx = put_string("SFX_E_KOURASHOOTER_SET1");
    s_set = put_string(SPM_R8PE01_STRIKER_ANIM_SET);
    s_kick = put_string(SPM_R8PE01_STRIKER_ANIM_KICK);
    s_stand = put_string(SPM_R8PE01_STRIKER_ANIM_STAND);
    if (!s_sfx || !s_set || !s_kick || !s_stand) {
        g_mod.ability_off = 1;
        return;
    }
    {
        const uint32_t pose_set[2] = {s_set, 0u}, pose_kick[2] = {s_kick, 0u}, pose_stand[2] = {s_stand, 0u};
        spm_evt_init(&b, words, 256u);
        /* He was crouching: the game's own stand-up (down is held back from
           now on) changes his pose a frame or two later and would overwrite
           A_1_a, so let it settle first and set A_1_a once more after. */
        spm_evt_wait_frames(&b, STRIKER_SETTLE_FRAMES);
        spm_evt_user_func(&b, SPM_R8PE01_EVT_SND_SFXON_3D_PLAYER, 1u, &s_sfx);
        spm_evt_user_func(&b, SPM_R8PE01_EVT_MARIO_SET_POSE, 2u, pose_set);
        spm_evt_wait_frames(&b, 3u);
        spm_evt_user_func(&b, SPM_R8PE01_EVT_MARIO_SET_POSE, 2u, pose_set);
        spm_evt_wait_frames(&b, STRIKER_SET_FRAMES - 3u);
        spm_evt_user_func(&b, SPM_R8PE01_EVT_MARIO_GET_POS, 3u, pos);
        spm_evt_add(&b, SPM_EVT_LW(0), (uint32_t)STRIKER_AHEAD);
        ahead_index = b.count - 1u;
        spm_evt_add(&b, SPM_EVT_LW(1), (uint32_t)STRIKER_ABOVE);
        spm_evt_user_func(&b, SPM_R8PE01_EVT_NPC_ENTRY_FROM_TEMPLATE, 7u, spawn);
        spm_evt_user_func(&b, SPM_R8PE01_EVT_MARIO_SET_POSE, 2u, pose_stand);
        spm_evt_wait_frames(&b, STRIKER_PAUSE_FRAMES);
        spm_evt_set(&b, SPM_EVT_LW(3), (uint32_t)STRIKER_SLIDE_STEP);
        step_index = b.count - 1u;
        spm_evt_user_func(&b, SPM_R8PE01_EVT_MARIO_SET_POSE, 2u, pose_kick);
        spm_evt_wait_frames(&b, STRIKER_WINDUP_FRAMES);
        for (i = 0u; i < STRIKER_SLIDE_FRAMES; ++i) {
            spm_evt_user_func(&b, SPM_R8PE01_EVT_MARIO_GET_POS, 3u, pos);
            spm_evt_add(&b, SPM_EVT_LW(0), SPM_EVT_LW(3));
            spm_evt_user_func(&b, SPM_R8PE01_EVT_MARIO_SET_POS, 3u, pos);
            spm_evt_wait_frames(&b, 1u);
        }
        spm_evt_wait_frames(&b, STRIKER_RECOVER_FRAMES);
        spm_evt_user_func(&b, SPM_R8PE01_EVT_MARIO_SET_POSE, 2u, pose_stand);
        count = spm_evt_finish(&b);
    }
    if (count == 0u || g_mod.calls.scratch(g_mod.calls.context, count * 4u, &address) != OPENSPM_OK) {
        g_mod.ability_off = 1;
        return;
    }
    for (i = 0u; i < count; ++i)
        if (g_mod.mem.write32(g_mod.mem.context, address + 4u * i, words[i]) != OPENSPM_OK) {
            g_mod.ability_off = 1;
            return;
        }
    g_mod.script = address;
    g_mod.ahead_word = address + 4u * ahead_index;
    g_mod.step_word = address + 4u * step_index;
}

static int facing_left(void) {
    uint32_t bits;
    float angle;
    if (!read32(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_FACING_ANGLE, &bits)) return 0;
    memcpy(&angle, &bits, sizeof angle);
    return spm_r8pe01_facing_left(angle);
}

/* Shells of the Koopa Striker's kind in the map (his and the enemies'). */
static uint32_t count_shells(void) {
    uint32_t work = 0u, num = 0u, entries = 0u, i, flag8, tribe, n = 0u;
    if (!read32(SPM_R8PE01_NPC_WORK_PTR, &work) || work == 0u || !read32(work + SPM_R8PE01_NPC_WORK_NUM, &num) ||
        !read32(work + SPM_R8PE01_NPC_WORK_ENTRIES, &entries) || entries == 0u)
        return 0u;
    if (num > STRIKER_MAX_NPCS) num = STRIKER_MAX_NPCS;
    for (i = 0u; i < num; ++i) {
        const uint32_t npc = entries + i * SPM_R8PE01_NPC_ENTRY_SIZE;
        if (read32(npc + SPM_R8PE01_NPC_FLAG8, &flag8) && (flag8 & 1u) && read32(npc + SPM_R8PE01_NPC_TRIBE_ID, &tribe) &&
            tribe == SPM_R8PE01_TRIBE_STRIKER_SHELL)
            ++n;
    }
    return n;
}

static void drop_shell(const char *why) {
    char message[160];
    const uint32_t args[3] = {g_mod.script, 0u, 0u};
    if (count_shells() >= STRIKER_MAX_SHELLS) {
        if (g_mod.have_overlay) g_mod.overlay.notify(g_mod.overlay.context, "Koopa Striker: shells are all out", 1500u);
        g_mod.cooldown = 30u;
        g_mod.armed = 0;
        return;
    }
    const int left = facing_left();
    const int32_t ahead = left ? -STRIKER_AHEAD : STRIKER_AHEAD, step = left ? -STRIKER_SLIDE_STEP : STRIKER_SLIDE_STEP;
    if (g_mod.mem.write32(g_mod.mem.context, g_mod.ahead_word, (uint32_t)ahead) != OPENSPM_OK ||
        g_mod.mem.write32(g_mod.mem.context, g_mod.step_word, (uint32_t)step) != OPENSPM_OK)
        return;
    if (g_mod.calls.call(g_mod.calls.context, SPM_R8PE01_EVT_ENTRY, 3u, args) != OPENSPM_OK) return;
    ++g_mod.shells;
    g_mod.cooldown = STRIKER_COOLDOWN_FRAMES;
    g_mod.freeze = STRIKER_SEQUENCE_FRAMES;
    g_mod.armed = 0;
    if (g_mod.shells <= 3u || g_mod.shells % 25u == 0u) {
        snprintf(message, sizeof message, "Striker: shell #%u (%s).", (unsigned)g_mod.shells, why);
        mod_log(OPENSPM_LOG_INFO, message);
    }
}

static void tick(void) {
    uint32_t seq = 0u, level = 1u;
    uint16_t motion = 0xFFFFu;
    int active, hotkey = 0;
    if (!g_mod.registered) return;
    active = g_mod.content.character_active(g_mod.content.context, g_mod.id) == 1;
    if (active != g_mod.was_active) {
        mod_log(OPENSPM_LOG_INFO, active ? "Striker: the Koopa Striker is in play." : "Striker: back to the game's characters.");
        if (active && g_mod.have_overlay) g_mod.overlay.notify(g_mod.overlay.context, "Koopa Striker: crouch for a shell", 2500u);
        g_mod.was_active = active;
    }
    if (g_mod.cooldown) --g_mod.cooldown;
    if (!active || g_mod.ability_off) {
        g_mod.crouch_frames = 0u;
        g_mod.freeze = 0u;
        return;
    }
    if (g_mod.freeze) {
        /* The set-and-kick is playing: he stands still (no walking, jumping,
           Pixl or flip) until it is over. */
        uint32_t held = 0u, pressed = 0u;
        --g_mod.freeze;
        if (g_mod.have_buttons && g_mod.input.get_wii_buttons(g_mod.input.context, &held, &pressed) == OPENSPM_OK)
            (void)g_mod.input.set_wii_buttons(g_mod.input.context, held & ~STRIKER_FROZEN_BUTTONS);
    }
    if (!read32(SPM_R8PE01_SEQ_WORK, &seq) || seq != SPM_R8PE01_SEQ_GAME) return;
    if (g_mod.script == 0u) {
        build_script();
        if (g_mod.ability_off) return;
    }
    if (g_mod.have_keys) hotkey = g_mod.input.key_down(g_mod.input.context, STRIKER_HOTKEY) == 1;
    (void)read32(SPM_R8PE01_SYSTEM_LEVEL, &level);
    (void)read16(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_MOTION_ID, &motion);
    if (motion == SPM_R8PE01_MOT_CROUCH) {
        if (g_mod.crouch_frames != UINT32_MAX) ++g_mod.crouch_frames;
        if (g_mod.cooldown) g_mod.armed = 0;
    } else {
        g_mod.crouch_frames = 0u;
        g_mod.armed = 1;
    }
    if (level == 0u && g_mod.cooldown == 0u) {
        if (hotkey && !g_mod.hotkey_was_down) drop_shell(STRIKER_HOTKEY);
        else if (g_mod.armed && g_mod.crouch_frames >= STRIKER_CROUCH_FRAMES) drop_shell("crouch");
    }
    g_mod.hotkey_was_down = hotkey;
}

static void OPENSPM_CALL striker_event(void *context, openspm_mod_event event_id, const void *event_data) {
    (void)context;
    (void)event_data;
    if (event_id == OPENSPM_MOD_EVENT_STARTUP) startup();
    else if (event_id == OPENSPM_MOD_EVENT_TICK_BEGIN) tick();
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
    out_api->mod_id = "openspm.sample.striker";
    out_api->mod_version = "1.0";
    out_api->on_event = striker_event;
    return OPENSPM_OK;
}
