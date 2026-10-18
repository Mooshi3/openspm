/* OpenSPM sample mod: "Shell Crouch".
 *
 * Crouch (hold down while standing still) and a Koopa shell drops in front of
 * you, ready to kick: the plain shell object the Koopa Striker (the soccer
 * Koopa) plays with, which never turns back into a Koopa. F6 drops one too
 * (for characters that cannot crouch).
 *
 * What it shows about the mod API (docs/MODDING.md):
 *   - openspm.guest_memory: read game state (sequence, world freeze level,
 *     Mario's motion and position) and write the mod's own EVT script into
 *     guest memory;
 *   - openspm.guest_call: reserve that memory (scratch) and make the game
 *     start the script (evtEntry). The script asks the game for Mario's
 *     position and spawns NPC template 129 with the game's own
 *     evt_npc_entry_from_template, so the shell is a real game object: it
 *     collides, can be kicked and knocks enemies over like any shell;
 *   - openspm.input: a hotkey; openspm.overlay: a short on-screen note.
 * It never writes game data: only its own scratch memory.
 *
 * R8PE01 rev 2 only: at the first in-game frame it checks that template 129
 * is still the Striker shell (tribe 70 with the Koopa kick script) and turns
 * itself off (logged) otherwise.
 *
 * Portable C: builds as a Windows DLL for OpenSPMNative and as a host object
 * for tests/m14_shell_crouch_mod_test.c. */
#include "openspm/guest_call.h"
#include "openspm/guest_memory.h"
#include "openspm/input_service.h"
#include "openspm/mod_api.h"
#include "openspm/overlay_service.h"
#include "openspm/runtime_service.h"
#include "openspm/spm_evt.h"
#include "openspm/spm_r8pe01.h"

#include <stddef.h>
#include <stdio.h>
#include <string.h>

#define MEMBER_END(type, member) (offsetof(type, member) + sizeof(((type *)0)->member))

/* Crouch this many consecutive frames (0.2 s) to drop a shell; one per crouch,
   at most one per SHELL_COOLDOWN_FRAMES. */
#define SHELL_CROUCH_FRAMES 12u
#define SHELL_COOLDOWN_FRAMES 45u
#define SHELL_HOTKEY "F6"
/* Game units in front of Mario (he is ~20 wide) and above his feet, so the
   shell appears beside him and settles on the ground. */
#define SHELL_AHEAD 36
#define SHELL_ABOVE 8

typedef struct shell_mod {
    const openspm_host_api *host;
    openspm_guest_memory_service mem;
    openspm_guest_call_service calls;
    openspm_input_service input;
    openspm_overlay_service overlay;
    int have_mem, have_calls, have_input, have_overlay;
    int disabled;       /* wrong revision / services missing */
    uint32_t script;    /* guest address of our script, 0 = not built */
    uint32_t ahead_word; /* guest address of the ADD's immediate (x offset) */
    uint32_t crouch_frames;
    uint32_t cooldown;
    int armed;          /* crouch released since the last shell */
    int hotkey_was_down;
    int facing;         /* +1 right, -1 left, from Mario's last x movement */
    float last_x;
    int have_x;
    uint32_t shells;
} shell_mod;

static shell_mod g_mod;

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

static void discover_services(void) {
    g_mod.have_mem = query(OPENSPM_GUEST_MEMORY_SERVICE_ID, OPENSPM_GUEST_MEMORY_SERVICE_VERSION, &g_mod.mem,
                           sizeof g_mod.mem) && g_mod.mem.read32 && g_mod.mem.write32;
    g_mod.have_calls = query(OPENSPM_GUEST_CALL_SERVICE_ID, OPENSPM_GUEST_CALL_SERVICE_VERSION, &g_mod.calls,
                             sizeof g_mod.calls) && g_mod.calls.call && g_mod.calls.scratch;
    g_mod.have_input = query(OPENSPM_INPUT_SERVICE_ID, OPENSPM_INPUT_SERVICE_VERSION, &g_mod.input,
                             sizeof g_mod.input) && g_mod.input.key_down;
    g_mod.have_overlay = query(OPENSPM_OVERLAY_SERVICE_ID, OPENSPM_OVERLAY_SERVICE_VERSION, &g_mod.overlay,
                               sizeof g_mod.overlay) && g_mod.overlay.notify;
    if (!g_mod.have_mem || !g_mod.have_calls) {
        g_mod.disabled = 1;
        mod_log(OPENSPM_LOG_WARN, "Shell Crouch: this OpenSPM has no openspm.guest_memory / openspm.guest_call; off.");
    } else {
        mod_log(OPENSPM_LOG_INFO, "Shell Crouch loaded: crouch (or press " SHELL_HOTKEY ") to drop a Koopa shell.");
    }
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
    memcpy(value, &bits, sizeof bits);
    return 1;
}

/* Revision check + script, once, at the first in-game frame:
       USER_FUNC evt_mario_get_pos, LW(0), LW(1), LW(2)
       ADD LW(0), <ahead>            ; rewritten before each shell (facing)
       ADD LW(1), 8
       USER_FUNC evt_npc_entry_from_template, 0, 129, LW(0), LW(1), LW(2), LW(10), null
       END_EVT / END_SCRIPT */
static void build_script(void) {
    char message[200];
    uint32_t tribe = 0u, kick = 0u, words[40], count, address, i;
    spm_evt_builder b;
    const uint32_t tmpl = SPM_R8PE01_NPC_TEMPLATES + SPM_R8PE01_TEMPLATE_STRIKER_SHELL * SPM_R8PE01_NPC_TEMPLATE_SIZE;
    const uint32_t pos[3] = {SPM_EVT_LW(0), SPM_EVT_LW(1), SPM_EVT_LW(2)};
    const uint32_t spawn[7] = {0u, SPM_R8PE01_TEMPLATE_STRIKER_SHELL, SPM_EVT_LW(0), SPM_EVT_LW(1), SPM_EVT_LW(2),
                               SPM_EVT_LW(10), SPM_EVT_NULLPTR};
    uint32_t ahead_index;
    if (!read32(tmpl + SPM_R8PE01_NPC_TEMPLATE_TRIBE, &tribe) ||
        !read32(tmpl + SPM_R8PE01_NPC_TEMPLATE_KICK_SCRIPT, &kick) || tribe != SPM_R8PE01_TRIBE_STRIKER_SHELL ||
        kick != SPM_R8PE01_KOOPA_KICK_SCRIPT) {
        g_mod.disabled = 1;
        snprintf(message, sizeof message,
                 "Shell Crouch: NPC template %u is tribe %u / kick 0x%08X, not the Koopa Striker shell of R8PE01 "
                 "rev 2; off.",
                 (unsigned)SPM_R8PE01_TEMPLATE_STRIKER_SHELL, (unsigned)tribe, (unsigned)kick);
        mod_log(OPENSPM_LOG_WARN, message);
        return;
    }
    spm_evt_init(&b, words, 40u);
    spm_evt_user_func(&b, SPM_R8PE01_EVT_MARIO_GET_POS, 3u, pos);
    spm_evt_add(&b, SPM_EVT_LW(0), (uint32_t)SHELL_AHEAD);
    ahead_index = b.count - 1u;
    spm_evt_add(&b, SPM_EVT_LW(1), (uint32_t)SHELL_ABOVE);
    spm_evt_user_func(&b, SPM_R8PE01_EVT_NPC_ENTRY_FROM_TEMPLATE, 7u, spawn);
    count = spm_evt_finish(&b);
    if (count == 0u || g_mod.calls.scratch(g_mod.calls.context, count * 4u, &address) != OPENSPM_OK) {
        g_mod.disabled = 1;
        mod_log(OPENSPM_LOG_WARN, "Shell Crouch: no scratch memory for the script; off.");
        return;
    }
    for (i = 0u; i < count; ++i) {
        if (g_mod.mem.write32(g_mod.mem.context, address + 4u * i, words[i]) != OPENSPM_OK) {
            g_mod.disabled = 1;
            mod_log(OPENSPM_LOG_WARN, "Shell Crouch: could not write the script; off.");
            return;
        }
    }
    g_mod.script = address;
    g_mod.ahead_word = address + 4u * ahead_index;
    snprintf(message, sizeof message, "Shell Crouch: script ready at 0x%08X (%u words).", (unsigned)address,
             (unsigned)count);
    mod_log(OPENSPM_LOG_INFO, message);
}

static void drop_shell(const char *why) {
    char message[160];
    const uint32_t args[3] = {g_mod.script, 0u, 0u}; /* evtEntry(script, priority 0, flags 0) */
    const int32_t ahead = g_mod.facing < 0 ? -SHELL_AHEAD : SHELL_AHEAD;
    if (g_mod.mem.write32(g_mod.mem.context, g_mod.ahead_word, (uint32_t)ahead) != OPENSPM_OK) return;
    if (g_mod.calls.call(g_mod.calls.context, SPM_R8PE01_EVT_ENTRY, 3u, args) != OPENSPM_OK) return;
    ++g_mod.shells;
    g_mod.cooldown = SHELL_COOLDOWN_FRAMES;
    g_mod.armed = 0;
    snprintf(message, sizeof message, "Shell Crouch: shell #%u (%s, %s).", (unsigned)g_mod.shells, why,
             ahead < 0 ? "left" : "right");
    mod_log(OPENSPM_LOG_INFO, message);
    if (g_mod.have_overlay) g_mod.overlay.notify(g_mod.overlay.context, "Shell!", 1000u);
}

static void track_facing(void) {
    float x;
    if (!read_f32(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_POSITION, &x) || x != x) return;
    if (g_mod.have_x) {
        const float dx = x - g_mod.last_x;
        if (dx > 0.2f) g_mod.facing = 1;
        else if (dx < -0.2f) g_mod.facing = -1;
    }
    g_mod.last_x = x;
    g_mod.have_x = 1;
}

static void tick(void) {
    uint32_t seq = 0u, level = 1u;
    uint16_t motion = 0xFFFFu;
    int hotkey = 0;
    if (g_mod.disabled) return;
    if (g_mod.cooldown != 0u) --g_mod.cooldown;
    if (!read32(SPM_R8PE01_SEQ_WORK, &seq) || seq != SPM_R8PE01_SEQ_GAME) {
        g_mod.crouch_frames = 0u;
        g_mod.have_x = 0;
        return;
    }
    if (g_mod.script == 0u) {
        build_script();
        if (g_mod.disabled) return;
    }
    track_facing();
    if (g_mod.have_input) hotkey = g_mod.input.key_down(g_mod.input.context, SHELL_HOTKEY) == 1;
    (void)read32(SPM_R8PE01_SYSTEM_LEVEL, &level);
    (void)read16(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_MOTION_ID, &motion);
    if (motion == SPM_R8PE01_MOT_CROUCH) {
        if (g_mod.crouch_frames != UINT32_MAX) ++g_mod.crouch_frames;
        if (g_mod.cooldown != 0u) g_mod.armed = 0; /* one shell per crouch: a crouch begun in the cooldown waits */
    } else {
        g_mod.crouch_frames = 0u;
        g_mod.armed = 1;
    }
    /* Only in normal play: not while the world is frozen (menus, cutscenes). */
    if (level == 0u && g_mod.cooldown == 0u) {
        if (hotkey && !g_mod.hotkey_was_down) drop_shell(SHELL_HOTKEY);
        else if (g_mod.armed && g_mod.crouch_frames >= SHELL_CROUCH_FRAMES) drop_shell("crouch");
    }
    g_mod.hotkey_was_down = hotkey;
}

static void OPENSPM_CALL shell_event(void *context, openspm_mod_event event_id, const void *event_data) {
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
        snprintf(message, sizeof message, "Shell Crouch: %u shells this session.", (unsigned)g_mod.shells);
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
    g_mod.armed = 1;
    g_mod.facing = 1;
    memset(out_api, 0, sizeof(*out_api));
    out_api->header.struct_size = sizeof(*out_api);
    out_api->header.abi_version = OPENSPM_ABI_VERSION;
    out_api->mod_id = "openspm.sample.shell_crouch";
    out_api->mod_version = "1.1";
    out_api->on_event = shell_event;
    return OPENSPM_OK;
}
