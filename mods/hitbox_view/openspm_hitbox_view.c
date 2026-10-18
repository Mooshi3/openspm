/* OpenSPM sample mod: "Hitbox View", a debug toggle that outlines hitboxes.
 *
 * F9 turns it on and off. While on, every frame it outlines:
 *   - Mario's hitbox (green): his position and the width / height the game
 *     uses (MarioWork +0x194 / +0x198, from characterProperties);
 *   - every live NPC's first hittable part (red for an enemy the game's
 *     attack loop would test, yellow for anything else): the NPC's position
 *     and the part's hitbox size (NPCPart +0x3C).
 * Boxes are projected with the game's own camera (Mario's camera: its view
 * matrix +0x54 and projection +0x94, see spm_r8pe01.h), so they follow 2D, 3D
 * and camera moves. Each is the screen rectangle around the 3D box.
 *
 * It only reads game memory and draws through openspm.overlay version 2
 * (draw_boxes); it never changes the game. Off (and silent) until F9. Other
 * mods can draw their own layers (the Francis sample's tongue reach) and
 * follow the same F9 toggle.
 *
 * Portable C: builds as a Windows DLL for OpenSPMNative and as a host object
 * for tests/m14_hitbox_view_mod_test.c. */
#include "openspm/guest_memory.h"
#include "openspm/input_service.h"
#include "openspm/mod_api.h"
#include "openspm/overlay_service.h"
#include "openspm/spm_r8pe01.h"
#include "openspm/spm_screen.h"

#include <math.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>

#define MEMBER_END(type, member) (offsetof(type, member) + sizeof(((type *)0)->member))

#define HITBOX_HOTKEY "F9"
#define HITBOX_LAYER "hitbox_view"
#define HITBOX_MAX_NPCS 512u
#define HITBOX_RGBA_MARIO 0x40FF40FFu
#define HITBOX_RGBA_ENEMY 0xFF4040FFu
#define HITBOX_RGBA_OTHER 0xFFD040C0u

typedef struct hitbox_mod {
    const openspm_host_api *host;
    openspm_guest_memory_service mem;
    openspm_input_service input;
    openspm_overlay_service overlay;
    int disabled, on, hotkey_was_down, drawn;
    uint32_t frames_on;
    openspm_overlay_box boxes[OPENSPM_OVERLAY_MAX_BOXES];
} hitbox_mod;

static hitbox_mod g_mod;

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

static int read32(uint32_t address, uint32_t *value) {
    return g_mod.mem.read32(g_mod.mem.context, address, value) == OPENSPM_OK;
}
static int read_f32(uint32_t address, float *value) {
    uint32_t bits;
    if (!read32(address, &bits)) return 0;
    memcpy(value, &bits, sizeof *value);
    return isfinite(*value);
}

/* ---- the game -------------------------------------------------------------- */

static int cam_read32(void *context, uint32_t address, uint32_t *value) {
    (void)context;
    return read32(address, value);
}

/* The NPC filters of the game's attack loop (0x801AA478), as the Glock
   sample uses them: live, not a decoration, a hittable part. */
static int npc_part(uint32_t npc, float *w, float *h, float *d, int *enemy) {
    uint32_t flag8 = 0u, flagc = 0u, flag46c = 0u, move = 0u, part = 0u, i;
    if (!read32(npc + SPM_R8PE01_NPC_FLAG8, &flag8) || (flag8 & 1u) == 0u || (flag8 & 0x40000000u) != 0u) return 0;
    if (!read32(npc + SPM_R8PE01_NPC_PARTS, &part)) return 0;
    *enemy = read32(npc + SPM_R8PE01_NPC_FLAGC, &flagc) && (flagc & 0x20u) == 0u &&
             read32(npc + SPM_R8PE01_NPC_FLAG46C, &flag46c) && (flag46c & 0x20000u) == 0u &&
             read32(npc + SPM_R8PE01_NPC_MOVE_MODE, &move) && move != 0u && move != 6u && move != 7u;
    for (i = 0u; i < 8u && part != 0u; ++i) {
        uint32_t flags = 1u, min_damage = 0xFFFFFFFFu;
        if (!read32(part + SPM_R8PE01_NPC_PART_FLAGS, &flags) ||
            !read32(part + SPM_R8PE01_NPC_PART_MIN_DAMAGE, &min_damage))
            return 0;
        if ((flags & 1u) == 0u) {
            if (!read_f32(part + SPM_R8PE01_NPC_PART_HITBOX, w) || !read_f32(part + SPM_R8PE01_NPC_PART_HITBOX + 4u, h) ||
                !read_f32(part + SPM_R8PE01_NPC_PART_HITBOX + 8u, d))
                return 0;
            if (!(*w > 0.5f && *w < 1000.0f && *h > 0.5f && *h < 1000.0f)) return 0;
            if (!(*d > 0.5f && *d < 1000.0f)) *d = *w;
            if ((int32_t)min_damage <= -1) *enemy = 0; /* immune: not a target */
            return 1;
        }
        if (!read32(part + SPM_R8PE01_NPC_PART_NEXT, &part)) return 0;
    }
    return 0;
}

static void clear_boxes(void) {
    if (g_mod.drawn && g_mod.overlay.draw_boxes) (void)g_mod.overlay.draw_boxes(g_mod.overlay.context, HITBOX_LAYER, NULL, 0u);
    g_mod.drawn = 0;
}

static void draw(void) {
    spm_screen_camera cam;
    uint32_t n = 0u, work = 0u, num = 0u, entries = 0u, i;
    float mx, my, mz, mw, mh;
    if (!spm_screen_read_camera(&cam, cam_read32, NULL)) {
        clear_boxes();
        return;
    }
    if (read_f32(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_POSITION, &mx) &&
        read_f32(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_POSITION + 4u, &my) &&
        read_f32(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_POSITION + 8u, &mz) &&
        read_f32(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_HIT_WIDTH, &mw) &&
        read_f32(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_HIT_HEIGHT, &mh) && mw > 0.5f && mw < 1000.0f &&
        mh > 0.5f && mh < 1000.0f &&
        spm_screen_standing_box(&cam, mx, my, mz, mw, mh, mw, HITBOX_RGBA_MARIO, &g_mod.boxes[n]))
        ++n;
    if (read32(SPM_R8PE01_NPC_WORK_PTR, &work) && work != 0u && read32(work + SPM_R8PE01_NPC_WORK_NUM, &num) &&
        read32(work + SPM_R8PE01_NPC_WORK_ENTRIES, &entries) && entries != 0u) {
        if (num > HITBOX_MAX_NPCS) num = HITBOX_MAX_NPCS;
        for (i = 0u; i < num && n < OPENSPM_OVERLAY_MAX_BOXES; ++i) {
            const uint32_t npc = entries + i * SPM_R8PE01_NPC_ENTRY_SIZE;
            float x, y, z, w, h, d;
            int enemy = 0;
            if (!npc_part(npc, &w, &h, &d, &enemy)) continue;
            if (!read_f32(npc + SPM_R8PE01_NPC_POSITION, &x) || !read_f32(npc + SPM_R8PE01_NPC_POSITION + 4u, &y) ||
                !read_f32(npc + SPM_R8PE01_NPC_POSITION + 8u, &z) || y < -900.0f) /* parked below the map */
                continue;
            if (spm_screen_standing_box(&cam, x, y, z, w, h, d, enemy ? HITBOX_RGBA_ENEMY : HITBOX_RGBA_OTHER, &g_mod.boxes[n])) ++n;
        }
    }
    if (n == 0u) {
        clear_boxes();
        return;
    }
    if (g_mod.overlay.draw_boxes(g_mod.overlay.context, HITBOX_LAYER, g_mod.boxes, n) == OPENSPM_OK) g_mod.drawn = 1;
}

static void tick(void) {
    uint32_t seq = 0u;
    const int down = g_mod.input.key_down(g_mod.input.context, HITBOX_HOTKEY) == 1;
    if (down && !g_mod.hotkey_was_down) {
        g_mod.on = !g_mod.on;
        if (g_mod.overlay.notify) g_mod.overlay.notify(g_mod.overlay.context, g_mod.on ? "Hitboxes on" : "Hitboxes off", 1200u);
        mod_log(OPENSPM_LOG_INFO, g_mod.on ? "Hitbox View: on (" HITBOX_HOTKEY ")." : "Hitbox View: off.");
        if (!g_mod.on) clear_boxes();
    }
    g_mod.hotkey_was_down = down;
    if (!g_mod.on) return;
    if (!read32(SPM_R8PE01_SEQ_WORK, &seq) || seq != SPM_R8PE01_SEQ_GAME) {
        clear_boxes();
        return;
    }
    ++g_mod.frames_on;
    draw();
}

static void discover_services(void) {
    const int have_mem = query(OPENSPM_GUEST_MEMORY_SERVICE_ID, OPENSPM_GUEST_MEMORY_SERVICE_VERSION, &g_mod.mem,
                               sizeof g_mod.mem) && g_mod.mem.read32;
    const int have_keys = query(OPENSPM_INPUT_SERVICE_ID, OPENSPM_INPUT_SERVICE_VERSION, &g_mod.input,
                                sizeof g_mod.input) && g_mod.input.key_down;
    const int have_boxes = query(OPENSPM_OVERLAY_SERVICE_ID, OPENSPM_OVERLAY_SERVICE_VERSION, &g_mod.overlay,
                                 sizeof g_mod.overlay) &&
                           g_mod.overlay.header.struct_size >= OPENSPM_OVERLAY_SERVICE_V2_SIZE && g_mod.overlay.draw_boxes;
    if (!have_mem || !have_keys || !have_boxes) {
        g_mod.disabled = 1;
        mod_log(OPENSPM_LOG_WARN, "Hitbox View: this OpenSPM lacks guest memory, input or overlay boxes; off.");
        return;
    }
    mod_log(OPENSPM_LOG_INFO, "Hitbox View loaded: press " HITBOX_HOTKEY " in a level to outline hitboxes "
                              "(green Mario, red enemies, yellow others).");
}

static void OPENSPM_CALL hitbox_event(void *context, openspm_mod_event event_id, const void *event_data) {
    (void)context;
    (void)event_data;
    switch (event_id) {
    case OPENSPM_MOD_EVENT_STARTUP:
        discover_services();
        break;
    case OPENSPM_MOD_EVENT_TICK_BEGIN:
        if (!g_mod.disabled) tick();
        break;
    case OPENSPM_MOD_EVENT_SHUTDOWN:
        if (!g_mod.disabled) clear_boxes();
        break;
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
    out_api->mod_id = "openspm.sample.hitbox_view";
    out_api->mod_version = "1.0";
    out_api->on_event = hitbox_event;
    return OPENSPM_OK;
}
