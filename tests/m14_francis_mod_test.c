/* Host test for the Francis sample mod (mods/francis): his definition
   (model, card icon, size and speed, voice), the tongue on crouch or F5 only
   while he is in play (he stands still meanwhile), one hit on the nearest
   enemy in front at mouth height (never behind, never above his head), the
   reach box with F9, F4 stepping through his own animations, and the tongue off on another game revision. */
#include "../mods/francis/openspm_francis.c"
#include "openspm/content_roster.h"

#include <assert.h>
#include <stdlib.h>

#define MEM1_SIZE 0x01800000u
static uint8_t *g_mem, *g_mem2;
static openspm_guest_call_queue g_queue;
static uint32_t g_scratch_used;
static int g_active, g_hotkey, g_f9, g_f4, g_registered;
static uint32_t g_boxes, g_box_calls;
static openspm_overlay_box g_box;
static openspm_character_def g_def;

static uint8_t *P(uint32_t a) {
    if (a >= 0x80000000u && a < 0x80000000u + MEM1_SIZE) return g_mem + (a - 0x80000000u);
    if (a >= 0x90000000u && a < 0x94000000u) return g_mem2 + (a - 0x90000000u);
    return NULL;
}
static void w32(uint32_t a, uint32_t v) { uint8_t *p = P(a); p[0] = v >> 24; p[1] = v >> 16; p[2] = v >> 8; p[3] = v; }
static const char *gstr(uint32_t a);
static uint32_t r32(uint32_t a) { const uint8_t *p = P(a); return (uint32_t)p[0] << 24 | p[1] << 16 | p[2] << 8 | p[3]; }
static const char *gstr(uint32_t a) { return (const char *)P(a); }
static void wf(uint32_t a, float f) { uint32_t b; memcpy(&b, &f, 4); w32(a, b); }

static int OPENSPM_CALL f_r32(void *c, uint32_t a, uint32_t *v) { (void)c; if (!P(a)) return -1; *v = r32(a); return 0; }
static int OPENSPM_CALL f_r16(void *c, uint32_t a, uint16_t *v) { (void)c; if (!P(a)) return -1; *v = (uint16_t)(r32(a) >> 16); return 0; }
static int OPENSPM_CALL f_w32(void *c, uint32_t a, uint32_t v) { (void)c; if (!P(a)) return -1; w32(a, v); return 0; }
static int OPENSPM_CALL f_call(void *c, uint32_t t, uint32_t n, const uint32_t *a) { (void)c; return openspm_guest_call_push(&g_queue, t, n, a); }
static int OPENSPM_CALL f_scratch(void *c, uint32_t s, uint32_t *o) { (void)c; return openspm_guest_scratch_take(&g_scratch_used, s, o); }
static uint32_t g_held, g_set_held;
static int g_set_calls;
static int OPENSPM_CALL f_get_wii(void *c, uint32_t *h, uint32_t *p) { (void)c; *h = g_held; *p = 0u; return OPENSPM_OK; }
static int OPENSPM_CALL f_set_wii(void *c, uint32_t h) { (void)c; g_set_held = h; ++g_set_calls; return OPENSPM_OK; }
static int OPENSPM_CALL f_key(void *c, const char *n) { (void)c; return strcmp(n, "F5") == 0 ? g_hotkey : strcmp(n, "F9") == 0 ? g_f9 : strcmp(n, "F4") == 0 ? g_f4 : 0; }
static int OPENSPM_CALL f_register(void *c, const openspm_character_def *d, uint32_t *id) {
    (void)c; g_def = *d; ++g_registered; *id = 4u; return OPENSPM_OK;
}
static int OPENSPM_CALL f_active(void *c, uint32_t id) { (void)c; return id == 4u && g_active; }

static int OPENSPM_CALL f_notify(void *c, const char *t, uint32_t ms) { (void)c; (void)t; (void)ms; return OPENSPM_OK; }
static int OPENSPM_CALL f_boxes(void *c, const char *layer, const openspm_overlay_box *b, uint32_t n) {
    (void)c;
    assert(strcmp(layer, "francis.tongue") == 0);
    if (n) g_box = b[0];
    g_boxes = n;
    ++g_box_calls;
    return OPENSPM_OK;
}
static int OPENSPM_CALL f_query(void *context, const char *id, uint32_t version, void *out) {
    (void)context;
    if (strcmp(id, OPENSPM_CONTENT_SERVICE_ID) == 0) {
        openspm_content_service *s = out;
        if (openspm_content_accept(s, version) != OPENSPM_OK) return OPENSPM_ERROR_ABI_MISMATCH;
        s->register_character = f_register;
        s->character_active = f_active;
        return OPENSPM_OK;
    }
    if (strcmp(id, OPENSPM_GUEST_MEMORY_SERVICE_ID) == 0) {
        openspm_guest_memory_service *s = out;
        s->read32 = f_r32; s->read16 = f_r16; s->write32 = f_w32;
        return OPENSPM_OK;
    }
    if (strcmp(id, OPENSPM_GUEST_CALL_SERVICE_ID) == 0) {
        openspm_guest_call_service *s = out;
        s->call = f_call; s->scratch = f_scratch;
        return OPENSPM_OK;
    }
    if (strcmp(id, OPENSPM_OVERLAY_SERVICE_ID) == 0) {
        openspm_overlay_service *s = out;
        if (openspm_overlay_accept(s, version) != OPENSPM_OK) return OPENSPM_ERROR_ABI_MISMATCH;
        s->notify = f_notify;
        s->draw_boxes = f_boxes;
        return OPENSPM_OK;
    }
    if (strcmp(id, OPENSPM_INPUT_SERVICE_ID) == 0) {
        openspm_input_service *s = out;
        s->key_down = f_key;
        s->get_wii_buttons = f_get_wii;
        s->set_wii_buttons = f_set_wii;
        return OPENSPM_OK;
    }
    return OPENSPM_ERROR_NOT_FOUND;
}
static void OPENSPM_CALL f_log(void *c, openspm_log_level l, const char *t) { (void)c; (void)l; (void)t; }

static openspm_host_api g_host;
static openspm_mod_api g_api;
static void load(void) {
    memset(&g_host, 0, sizeof g_host);
    g_host.header.struct_size = sizeof g_host;
    g_host.header.abi_version = OPENSPM_ABI_VERSION;
    g_host.log = f_log;
    g_host.query_service = f_query;
    memset(&g_api, 0, sizeof g_api);
    g_api.header.struct_size = sizeof g_api;
    assert(openspm_mod_query(OPENSPM_ABI_VERSION, &g_host, &g_api) == OPENSPM_OK);
    g_api.on_event(g_api.mod_context, OPENSPM_MOD_EVENT_STARTUP, NULL);
}
static void frame(void) { g_api.on_event(g_api.mod_context, OPENSPM_MOD_EVENT_TICK_BEGIN, NULL); }
static void motion(uint16_t m) { w32(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_MOTION_ID, (uint32_t)m << 16); }


#define NPC_WORK 0x80600000u
#define NPC_ENTRIES 0x80610000u
#define PARTS 0x80680000u
#define STRINGS 0x80700000u
#define CAM 0x80710000u

static void npc(uint32_t i, float x, float y, float w, float h, uint16_t part_id) {
    const uint32_t e = NPC_ENTRIES + i * SPM_R8PE01_NPC_ENTRY_SIZE, part = PARTS + 0x400u * i;
    w32(e + SPM_R8PE01_NPC_FLAG8, 1u);
    w32(e + SPM_R8PE01_NPC_MOVE_MODE, 1u);
    wf(e + SPM_R8PE01_NPC_POSITION, x);
    wf(e + SPM_R8PE01_NPC_POSITION + 4u, y);
    wf(e + SPM_R8PE01_NPC_POSITION + 8u, 0.0f);
    w32(e + SPM_R8PE01_NPC_PARTS, part);
    w32(part + SPM_R8PE01_NPC_PART_ID, (uint32_t)part_id << 16);
    wf(part + SPM_R8PE01_NPC_PART_HITBOX, w);
    wf(part + SPM_R8PE01_NPC_PART_HITBOX + 4u, h);
}

/* Runs a lash to the end; returns the damage calls (their target names). */
static uint32_t lash_hits(uint32_t *targets, uint32_t max) {
    openspm_guest_call_entry call;
    uint32_t i, n = 0u;
    for (i = 0; i < FRANCIS_COOLDOWN_FRAMES + 2u; ++i) {
        frame();
        while (openspm_guest_call_pop(&g_queue, &call))
            if (call.target == SPM_R8PE01_EVT_ENTRY && call.args[0] != g_mod.script) {
                const uint32_t script = call.args[0];
                assert(r32(script + 4u) == SPM_R8PE01_EVT_NPC_TAKE_DAMAGE);
                assert(strcmp(gstr(r32(script + 8u)), "mario") == 0 && r32(script + 20u) == 0u);
                assert(r32(script + 24u) == SPM_R8PE01_DAMAGE_SQUIRPS_SHOT && r32(script + 28u) == FRANCIS_POWER);
                if (n < max) targets[n] = r32(script + 12u);
                ++n;
            }
    }
    return n;
}

int main(void) {
    openspm_guest_call_entry call;
    uint32_t i, targets[4];
    g_mem = calloc(1, MEM1_SIZE);
    g_mem2 = calloc(1, 0x04000000u);
    assert(g_mem && g_mem2);
    memcpy(P(STRINGS), "e_kamere", 9);
    w32(SPM_R8PE01_NPC_TRIBES + 318u * SPM_R8PE01_NPC_TRIBE_SIZE, STRINGS);
    w32(SPM_R8PE01_SEQ_WORK, 2u);
    wf(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_HIT_WIDTH, 40.0f);
    wf(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_HIT_HEIGHT, 64.0f);
    load();
    assert(g_registered == 1 && strcmp(g_def.model, "e_kamere") == 0 && g_def.base_character == 0u);
    assert(strcmp(g_def.name, "Francis") == 0 && g_def.icon_item == 464u && strchr(g_def.key, ':') != NULL);
    assert(g_def.properties != NULL && strstr(g_def.properties, "walk=") && strstr(g_def.properties, "width="));
    assert(g_def.flags == OPENSPM_CHARACTER_NO_FLIP); /* flipped he would show Mario's 3D model */
    assert(g_def.voice != NULL && strstr(g_def.voice, "SFX_BS_CMK_"));
    {
        /* The definition passes OpenSPM's own checks. */
        static openspm_roster r;
        char why[160];
        uint32_t id;
        openspm_roster_init(&r);
        assert(openspm_roster_register(&r, &g_def, &id, why, sizeof why) == OPENSPM_OK);
        assert(r.chars[0].prop_set == 0xFu);
    }

    /* Not in play: crouching does nothing. */
    motion(SPM_R8PE01_MOT_CROUCH);
    for (i = 0; i < 40u; ++i) frame();
    assert(!openspm_guest_call_pop(&g_queue, &call) && g_scratch_used == 0u);

    /* Enemies: one in reach in front (right), one behind, one far above. */
    w32(SPM_R8PE01_NPC_WORK_PTR, NPC_WORK);
    w32(NPC_WORK + SPM_R8PE01_NPC_WORK_NUM, 8u);
    w32(NPC_WORK + SPM_R8PE01_NPC_WORK_ENTRIES, NPC_ENTRIES);
    npc(0u, 90.0f, 0.0f, 20.0f, 30.0f, 3u);
    npc(1u, -60.0f, 0.0f, 20.0f, 30.0f, 1u);
    npc(2u, 60.0f, 300.0f, 20.0f, 30.0f, 1u);
    npc(3u, 400.0f, 0.0f, 20.0f, 30.0f, 1u); /* out of reach */

    /* In play, facing right: a crouch lashes, one hit on the enemy in front. */
    g_active = 1;
    wf(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_FACING_ANGLE, 180.0f);
    motion(SPM_R8PE01_MOT_STAY);
    frame();
    motion(SPM_R8PE01_MOT_CROUCH);
    for (i = 0; i < FRANCIS_CROUCH_FRAMES - 1u; ++i) frame();
    g_held = OPENSPM_WII_BTN_RIGHT | OPENSPM_WII_BTN_PLUS;
    g_set_calls = 0;
    frame();
    assert(openspm_guest_call_pop(&g_queue, &call) && call.target == SPM_R8PE01_EVT_ENTRY && call.args[0] == g_mod.script);
    {
        /* Mouth open, tongue out (with its sound), back, stand. */
        uint32_t w, seen = 0u;
        for (w = g_mod.script; w < g_mod.script + 4u * 96u; w += 4u) {
            const uint32_t op = r32(w) & 0xFFFFu, argc = r32(w) >> 16;
            if (op == 1u) break;
            if (op == 0x5Cu) {
                const uint32_t f = r32(w + 4u);
                const char *a = f == SPM_R8PE01_EVT_MARIO_SET_POSE || f == SPM_R8PE01_EVT_SND_SFXON_3D_PLAYER ? gstr(r32(w + 8u)) : "";
                if (seen == 0u && strcmp(a, "A_1A") == 0) seen = 1u;
                if (seen == 1u && strcmp(a, "SFX_BS_CMK_TONGUE_GO1") == 0) seen = 2u;
                if (seen == 2u && strcmp(a, "A_1B") == 0) seen = 3u;
                if (seen == 3u && strcmp(a, "A_1C") == 0) seen = 4u;
                if (seen == 4u && strcmp(a, "S_1") == 0) seen = 5u;
            }
            w += 4u * argc;
        }
        assert(seen == 5u);
    }
    motion(SPM_R8PE01_MOT_STAY);
    frame();
    assert(g_set_calls >= 1 && g_set_held == OPENSPM_WII_BTN_PLUS); /* he stands still */
    g_held = 0u;
    assert(lash_hits(targets, 4u) == 1u && targets[0] == NPC_ENTRIES + SPM_R8PE01_NPC_NAME);

    /* Facing left (F5): the enemy behind is now in front. */
    wf(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_FACING_ANGLE, 0.0f);
    g_hotkey = 1;
    frame();
    g_hotkey = 0;
    assert(openspm_guest_call_pop(&g_queue, &call) && call.args[0] == g_mod.script);
    assert(lash_hits(targets, 4u) == 1u && targets[0] == NPC_ENTRIES + SPM_R8PE01_NPC_ENTRY_SIZE + SPM_R8PE01_NPC_NAME);

    /* F9 with a camera: the reach box is drawn only while the tongue is out. */
    w32(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_CAMERA_ID, 0u);
    w32(SPM_R8PE01_CAMERA_TABLE, CAM);
    wf(CAM + SPM_R8PE01_CAMERA_VIEW_MTX + 0u, 1.0f);
    wf(CAM + SPM_R8PE01_CAMERA_VIEW_MTX + 20u, 1.0f);
    wf(CAM + SPM_R8PE01_CAMERA_VIEW_MTX + 40u, 1.0f);
    wf(CAM + SPM_R8PE01_CAMERA_VIEW_MTX + 44u, -500.0f);
    wf(CAM + SPM_R8PE01_CAMERA_PROJ_MTX + 0u, 2.0f);
    wf(CAM + SPM_R8PE01_CAMERA_PROJ_MTX + 20u, 2.0f);
    wf(CAM + SPM_R8PE01_CAMERA_PROJ_MTX + 40u, -0.001f);
    wf(CAM + SPM_R8PE01_CAMERA_PROJ_MTX + 44u, -1.0f);
    wf(CAM + SPM_R8PE01_CAMERA_PROJ_MTX + 56u, -1.0f);
    g_f9 = 1;
    frame();
    g_f9 = 0;
    wf(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_FACING_ANGLE, 180.0f);
    g_hotkey = 1;
    frame();
    g_hotkey = 0;
    {
        uint32_t drawn = 0u;
        for (i = 0; i < FRANCIS_COOLDOWN_FRAMES + 2u; ++i) {
            frame();
            while (openspm_guest_call_pop(&g_queue, &call)) {}
            if (g_boxes == 1u) {
                ++drawn;
                assert(g_box.rgba == FRANCIS_TONGUE_RGBA && g_box.x0 > 0.5f); /* to his right */
            }
        }
        assert(drawn >= FRANCIS_OUT_FRAMES - 2u && drawn <= FRANCIS_OUT_FRAMES && g_boxes == 0u);
    }

    /* F4: his model's own animations one per press (the names OpenSPM adds,
       "mario_...", are skipped), wrapping around. */
    {
        const uint32_t work = 0x80720000u, header = 0x80721000u, anims = 0x80722000u;
        static const char *const names[4] = {"S_1", "A_1B", "mario_S_1", "Big_mario_S_1"};
        static const char *const expect[3] = {"S_1", "A_1B", "S_1"};
        w32(SPM_R8PE01_ANIM_WORK_PTR, work);
        w32(work, work + 0x100u);                  /* groups */
        w32(work + 0x10u, work + 0x200u);          /* poses; pose 0 -> group 0 */
        w32(work + 0x100u + 8u, work + 0x400u);    /* group 0 file */
        w32(work + 0x400u + 0xA4u, work + 0x500u); /* file data */
        w32(work + 0x500u, header);
        w32(header + 0x148u, 4u);
        w32(header + 0x1ACu, anims);
        for (i = 0; i < 4u; ++i) memcpy(P(anims + 64u * i), names[i], strlen(names[i]) + 1u);
        for (i = 0; i < 3u; ++i) {
            g_f4 = 1;
            frame();
            g_f4 = 0;
            frame();
            assert(openspm_guest_call_pop(&g_queue, &call) && call.args[0] == g_mod.browse_script);
            assert(r32(g_mod.browse_script + 4u) == SPM_R8PE01_EVT_MARIO_SET_POSE);
            assert(strcmp(gstr(r32(g_mod.browse_script + 8u)), expect[i]) == 0);
            assert(!openspm_guest_call_pop(&g_queue, &call));
        }
    }

    /* Back to Mario: nothing. */
    g_active = 0;
    motion(SPM_R8PE01_MOT_CROUCH);
    for (i = 0; i < 40u; ++i) frame();
    assert(!openspm_guest_call_pop(&g_queue, &call));

    /* Another revision: the tongue stays off. */
    memset(g_mem2, 0, 0x04000000u);
    g_scratch_used = 0u;
    memcpy(P(STRINGS), "e_other", 8);
    load();
    g_active = 1;
    for (i = 0; i < 40u; ++i) frame();
    assert(!openspm_guest_call_pop(&g_queue, &call) && g_mod.ability_off);

    free(g_mem);
    free(g_mem2);
    puts("PASS: francis mod");
    return 0;
}
