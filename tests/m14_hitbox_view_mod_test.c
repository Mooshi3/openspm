/* Host test for the Hitbox View sample mod (mods/hitbox_view): a fake game
   with a perspective camera (the layout camDraw builds), Mario, an enemy, a
   townsperson, an NPC behind the camera and one parked below the map. F9
   turns the view on and off; boxes land where the projection puts them,
   coloured by kind; leaving the game or turning it off clears the layer. */
#include "../mods/hitbox_view/openspm_hitbox_view.c"

#include <assert.h>
#include <stdlib.h>

#define MEM1_SIZE 0x01800000u
#define CAM 0x80600000u
#define NPC_WORK 0x80610000u
#define NPC_ENTRIES 0x80620000u
#define PARTS 0x80700000u

static uint8_t *g_mem;
static int g_f9;
static openspm_overlay_box g_boxes[OPENSPM_OVERLAY_MAX_BOXES];
static uint32_t g_box_count, g_box_calls;
static char g_note[64];

static uint8_t *P(uint32_t a) { return a >= 0x80000000u && a + 4u <= 0x80000000u + MEM1_SIZE ? g_mem + (a - 0x80000000u) : NULL; }
static void w32(uint32_t a, uint32_t v) { uint8_t *p = P(a); p[0] = v >> 24; p[1] = v >> 16; p[2] = v >> 8; p[3] = v; }
static void wf(uint32_t a, float f) { uint32_t b; memcpy(&b, &f, 4); w32(a, b); }

static int OPENSPM_CALL f_read32(void *c, uint32_t a, uint32_t *v) {
    const uint8_t *p = P(a);
    (void)c;
    if (!p) return OPENSPM_ERROR_INVALID_ARGUMENT;
    *v = (uint32_t)p[0] << 24 | p[1] << 16 | p[2] << 8 | p[3];
    return OPENSPM_OK;
}
static int OPENSPM_CALL f_key(void *c, const char *k) { (void)c; return strcmp(k, "F9") == 0 ? g_f9 : 0; }
static int OPENSPM_CALL f_boxes(void *c, const char *layer, const openspm_overlay_box *b, uint32_t n) {
    (void)c;
    assert(strcmp(layer, "hitbox_view") == 0 && n <= OPENSPM_OVERLAY_MAX_BOXES);
    if (n) memcpy(g_boxes, b, n * sizeof *b);
    g_box_count = n;
    ++g_box_calls;
    return OPENSPM_OK;
}
static int OPENSPM_CALL f_notify(void *c, const char *t, uint32_t ms) { (void)c; (void)ms; snprintf(g_note, sizeof g_note, "%s", t); return OPENSPM_OK; }
static int OPENSPM_CALL f_query(void *context, const char *id, uint32_t version, void *out) {
    (void)context;
    if (strcmp(id, OPENSPM_GUEST_MEMORY_SERVICE_ID) == 0) {
        ((openspm_guest_memory_service *)out)->read32 = f_read32;
        return OPENSPM_OK;
    }
    if (strcmp(id, OPENSPM_INPUT_SERVICE_ID) == 0) {
        ((openspm_input_service *)out)->key_down = f_key;
        return OPENSPM_OK;
    }
    if (strcmp(id, OPENSPM_OVERLAY_SERVICE_ID) == 0) {
        openspm_overlay_service *s = out;
        if (openspm_overlay_accept(s, version) != OPENSPM_OK) return OPENSPM_ERROR_ABI_MISMATCH;
        s->notify = f_notify;
        s->draw_boxes = f_boxes;
        return OPENSPM_OK;
    }
    return OPENSPM_ERROR_NOT_FOUND;
}
static void OPENSPM_CALL f_log(void *c, openspm_log_level l, const char *t) { (void)c; (void)l; (void)t; }

static openspm_host_api g_host;
static openspm_mod_api g_api;
static void frame(void) { g_api.on_event(g_api.mod_context, OPENSPM_MOD_EVENT_TICK_BEGIN, NULL); }

/* An NPC with one part: live, an enemy (move mode 1) or not (0). */
static void npc(uint32_t i, float x, float y, float z, float w, float h, int enemy) {
    const uint32_t e = NPC_ENTRIES + i * SPM_R8PE01_NPC_ENTRY_SIZE, part = PARTS + 0x400u * i;
    w32(e + SPM_R8PE01_NPC_FLAG8, 1u);
    w32(e + SPM_R8PE01_NPC_MOVE_MODE, enemy ? 1u : 0u);
    wf(e + SPM_R8PE01_NPC_POSITION, x);
    wf(e + SPM_R8PE01_NPC_POSITION + 4u, y);
    wf(e + SPM_R8PE01_NPC_POSITION + 8u, z);
    w32(e + SPM_R8PE01_NPC_PARTS, part);
    wf(part + SPM_R8PE01_NPC_PART_HITBOX, w);
    wf(part + SPM_R8PE01_NPC_PART_HITBOX + 4u, h);
    wf(part + SPM_R8PE01_NPC_PART_HITBOX + 8u, w);
}

int main(void) {
    const float n = 1.0f, f = 2000.0f, cot = 1.0f / tanf(15.0f * 3.14159265f / 180.0f), aspect = 4.0f / 3.0f;
    uint32_t i;
    g_mem = calloc(1, MEM1_SIZE);
    assert(g_mem);
    /* Camera 0 at z = +500 looking down -z (view: translate by -500), and
       C_MTXPerspective's matrix for fovy 30, 4:3. */
    w32(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_CAMERA_ID, 0u);
    w32(SPM_R8PE01_CAMERA_TABLE, CAM);
    w32(CAM + SPM_R8PE01_CAMERA_PROJ_TYPE, 0u);
    wf(CAM + SPM_R8PE01_CAMERA_VIEW_MTX + 0u, 1.0f);
    wf(CAM + SPM_R8PE01_CAMERA_VIEW_MTX + 20u, 1.0f);
    wf(CAM + SPM_R8PE01_CAMERA_VIEW_MTX + 40u, 1.0f);
    wf(CAM + SPM_R8PE01_CAMERA_VIEW_MTX + 44u, -500.0f);
    wf(CAM + SPM_R8PE01_CAMERA_PROJ_MTX + 0u, cot / aspect);
    wf(CAM + SPM_R8PE01_CAMERA_PROJ_MTX + 20u, cot);
    wf(CAM + SPM_R8PE01_CAMERA_PROJ_MTX + 40u, -n / (f - n));
    wf(CAM + SPM_R8PE01_CAMERA_PROJ_MTX + 44u, -(f * n) / (f - n));
    wf(CAM + SPM_R8PE01_CAMERA_PROJ_MTX + 56u, -1.0f);
    /* Mario at the origin, 20 x 40. */
    wf(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_HIT_WIDTH, 20.0f);
    wf(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_HIT_HEIGHT, 40.0f);
    w32(SPM_R8PE01_SEQ_WORK, SPM_R8PE01_SEQ_GAME);
    /* NPCs: an enemy to the right, a townsperson to the left, one behind the
       camera, one parked below the map. */
    w32(SPM_R8PE01_NPC_WORK_PTR, NPC_WORK);
    w32(NPC_WORK + SPM_R8PE01_NPC_WORK_NUM, 8u);
    w32(NPC_WORK + SPM_R8PE01_NPC_WORK_ENTRIES, NPC_ENTRIES);
    npc(0u, 100.0f, 0.0f, 0.0f, 30.0f, 30.0f, 1);
    npc(2u, -100.0f, 0.0f, 0.0f, 20.0f, 50.0f, 0);
    npc(3u, 0.0f, 0.0f, 900.0f, 20.0f, 20.0f, 1);
    npc(4u, 0.0f, -1000.0f, 0.0f, 20.0f, 20.0f, 1);

    memset(&g_host, 0, sizeof g_host);
    g_host.header.struct_size = sizeof g_host;
    g_host.header.abi_version = OPENSPM_ABI_VERSION;
    g_host.log = f_log;
    g_host.query_service = f_query;
    memset(&g_api, 0, sizeof g_api);
    g_api.header.struct_size = sizeof g_api;
    assert(openspm_mod_query(OPENSPM_ABI_VERSION, &g_host, &g_api) == OPENSPM_OK);
    g_api.on_event(g_api.mod_context, OPENSPM_MOD_EVENT_STARTUP, NULL);
    assert(!g_mod.disabled);

    /* Off until F9: nothing drawn. */
    for (i = 0; i < 5u; ++i) frame();
    assert(g_box_calls == 0u);

    /* F9: Mario, the enemy and the townsperson. */
    g_f9 = 1;
    frame();
    assert(g_mod.on && strcmp(g_note, "Hitboxes on") == 0);
    assert(g_box_count == 3u);
    {
        const openspm_overlay_box *m = &g_boxes[0], *e = &g_boxes[1], *t = &g_boxes[2];
        assert(m->rgba == HITBOX_RGBA_MARIO && e->rgba == HITBOX_RGBA_ENEMY && t->rgba == HITBOX_RGBA_OTHER);
        /* Mario: centred horizontally, standing at the middle of the picture
           (y = 0 at the camera's height) and reaching up. */
        assert(m->x0 < 0.5f && m->x1 > 0.5f && fabsf((m->x0 + m->x1) - 1.0f) < 1e-3f);
        assert(m->y1 > 0.5f - 1e-2f && m->y0 < 0.5f && m->y1 - m->y0 > 0.05f);
        /* One unit at depth 500 is cot / (2 * aspect * 500) of the width. */
        assert(fabsf((m->x1 - m->x0) - 20.0f * cot / (2.0f * aspect * 500.0f)) < 2e-3f);
        assert(e->x0 > m->x1 && t->x1 < m->x0);
    }
    /* Held: stays on. */
    frame();
    assert(g_mod.on && g_box_count == 3u);
    /* A door (not SEQ_GAME): cleared. */
    w32(SPM_R8PE01_SEQ_WORK, 3u);
    frame();
    assert(g_box_count == 0u);
    w32(SPM_R8PE01_SEQ_WORK, SPM_R8PE01_SEQ_GAME);
    frame();
    assert(g_box_count == 3u);
    /* F9 again: off, cleared, nothing more. */
    g_f9 = 0;
    frame();
    g_f9 = 1;
    frame();
    assert(!g_mod.on && g_box_count == 0u && strcmp(g_note, "Hitboxes off") == 0);
    i = g_box_calls;
    g_f9 = 0;
    frame();
    frame();
    assert(g_box_calls == i);
    free(g_mem);
    puts("PASS: Hitbox View mod");
    return 0;
}
