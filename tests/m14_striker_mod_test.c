/* Host test for the Koopa Striker sample mod (mods/striker): it registers its
   character through openspm.content (checked definition), drops shells only
   while that character is in play (crouch or F6), faces by Mario's facing
   angle, and turns the ability off on another game revision. */
#include "../mods/striker/openspm_striker.c"

#include <assert.h>
#include <stdlib.h>

#define MEM1_SIZE 0x01800000u
static uint8_t *g_mem, *g_mem2;
static openspm_guest_call_queue g_queue;
static uint32_t g_scratch_used;
static int g_active, g_hotkey, g_registered;
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
static int OPENSPM_CALL f_key(void *c, const char *n) { (void)c; return strcmp(n, "F6") == 0 ? g_hotkey : 0; }
static int OPENSPM_CALL f_register(void *c, const openspm_character_def *d, uint32_t *id) {
    (void)c; g_def = *d; ++g_registered; *id = 4u; return OPENSPM_OK;
}
static int OPENSPM_CALL f_active(void *c, uint32_t id) { (void)c; return id == 4u && g_active; }

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

int main(void) {
    openspm_guest_call_entry call;
    uint32_t i;
    const uint32_t tmpl = SPM_R8PE01_NPC_TEMPLATES + 129u * SPM_R8PE01_NPC_TEMPLATE_SIZE;
    g_mem = calloc(1, MEM1_SIZE);
    g_mem2 = calloc(1, 0x04000000u);
    assert(g_mem && g_mem2);
    w32(tmpl + SPM_R8PE01_NPC_TEMPLATE_TRIBE, 70u);
    w32(tmpl + SPM_R8PE01_NPC_TEMPLATE_KICK_SCRIPT, SPM_R8PE01_KOOPA_KICK_SCRIPT);
    w32(SPM_R8PE01_SEQ_WORK, 2u);
    load();
    assert(g_registered == 1 && strcmp(g_def.model, "e_k_shooter") == 0 && g_def.base_character == 0u);
    assert(strcmp(g_def.name, "Koopa Striker") == 0 && strchr(g_def.key, ':') != NULL);

    /* Not in play: crouching does nothing. */
    motion(SPM_R8PE01_MOT_CROUCH);
    for (i = 0; i < 40u; ++i) frame();
    assert(!openspm_guest_call_pop(&g_queue, &call) && g_scratch_used == 0u);

    /* In play: a crouch drops one shell to the right. */
    g_active = 1;
    wf(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_FACING_ANGLE, 180.0f); /* facing right */
    motion(SPM_R8PE01_MOT_STAY);
    frame();
    motion(SPM_R8PE01_MOT_CROUCH);
    for (i = 0; i < STRIKER_CROUCH_FRAMES; ++i) frame();
    assert(openspm_guest_call_pop(&g_queue, &call) && call.target == SPM_R8PE01_EVT_ENTRY && call.args[0] == g_mod.script);
    assert((int32_t)r32(g_mod.ahead_word) == STRIKER_AHEAD);
    assert((int32_t)r32(g_mod.step_word) == STRIKER_SLIDE_STEP);
    {
        /* The enemy's set-and-kick: control held, A_1_a, the shell, A_1_b,
           the kick (damage from "mario" to the new shell), control back. */
        uint32_t w, seen = 0u, last_func = 0u;
        for (w = g_mod.script; w < g_mod.script + 4u * 256u; w += 4u) {
            const uint32_t op = r32(w) & 0xFFFFu, argc = r32(w) >> 16;
            if (op == 1u) break; /* END_SCRIPT */
            if (op == 0x5Cu) {  /* USER_FUNC */
                const uint32_t f = r32(w + 4u);
                last_func = f;
                assert(f != SPM_R8PE01_EVT_MARIO_KEY_OFF && f != SPM_R8PE01_EVT_MARIO_KEY_ON); /* no cutscene mode */
                if (f == SPM_R8PE01_EVT_SND_SFXON_3D_PLAYER && seen == 0u) seen = 1u;
                if (f == SPM_R8PE01_EVT_MARIO_SET_POSE && seen == 1u && strcmp(gstr(r32(w + 8u)), "A_1_a") == 0) seen = 2u;
                if (f == SPM_R8PE01_EVT_NPC_ENTRY_FROM_TEMPLATE && seen == 2u) seen = 3u;
                if (f == SPM_R8PE01_EVT_MARIO_SET_POSE && seen == 3u && strcmp(gstr(r32(w + 8u)), "A_1_b") == 0) seen = 4u;
                assert(f != SPM_R8PE01_EVT_NPC_TAKE_DAMAGE); /* a damage call pops the shell */
                if (f == SPM_R8PE01_EVT_MARIO_SET_POS && seen == 4u) seen = 5u; /* the slide into it */
            }
            w += 4u * argc;
        }
        assert(seen == 5u && last_func == SPM_R8PE01_EVT_MARIO_SET_POSE);
    }
    /* He stands still while it plays: walking, jumping, 1 and A are held
       back; afterwards the buttons are his again. */
    g_held = OPENSPM_WII_BTN_RIGHT | OPENSPM_WII_BTN_2 | OPENSPM_WII_BTN_PLUS;
    g_set_calls = 0;
    frame();
    assert(g_set_calls == 1 && g_set_held == OPENSPM_WII_BTN_PLUS);
    for (i = 0; i < STRIKER_SEQUENCE_FRAMES; ++i) frame();
    g_set_calls = 0;
    frame();
    assert(g_set_calls == 0);
    g_held = 0u;
    for (i = 0; i < 100u; ++i) frame();
    assert(!openspm_guest_call_pop(&g_queue, &call)); /* one per crouch */

    /* Facing left: F6 drops one on the left. */
    motion(SPM_R8PE01_MOT_STAY);
    wf(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_FACING_ANGLE, 0.0f);
    g_hotkey = 1;
    frame();
    assert(openspm_guest_call_pop(&g_queue, &call) && (int32_t)r32(g_mod.ahead_word) == -STRIKER_AHEAD);
    assert((int32_t)r32(g_mod.step_word) == -STRIKER_SLIDE_STEP);
    g_hotkey = 0;

    /* Four shells already out: no fifth (each is a game NPC). */
    w32(SPM_R8PE01_NPC_WORK_PTR, 0x80600000u);
    w32(0x80600000u + SPM_R8PE01_NPC_WORK_NUM, 8u);
    w32(0x80600000u + SPM_R8PE01_NPC_WORK_ENTRIES, 0x80610000u);
    for (i = 0; i < 4u; ++i) {
        w32(0x80610000u + i * SPM_R8PE01_NPC_ENTRY_SIZE + SPM_R8PE01_NPC_FLAG8, 1u);
        w32(0x80610000u + i * SPM_R8PE01_NPC_ENTRY_SIZE + SPM_R8PE01_NPC_TRIBE_ID, SPM_R8PE01_TRIBE_STRIKER_SHELL);
    }
    for (i = 0; i < STRIKER_COOLDOWN_FRAMES; ++i) frame();
    g_hotkey = 1;
    frame();
    assert(!openspm_guest_call_pop(&g_queue, &call));
    g_hotkey = 0;
    w32(0x80610000u + SPM_R8PE01_NPC_FLAG8, 0u); /* one exploded */
    for (i = 0; i < 40u; ++i) frame();
    g_hotkey = 1;
    frame();
    assert(openspm_guest_call_pop(&g_queue, &call));
    g_hotkey = 0;

    /* Another revision: the ability stays off. */
    memset(g_mem2, 0, 0x04000000u);
    g_scratch_used = 0u;
    w32(tmpl + SPM_R8PE01_NPC_TEMPLATE_TRIBE, 11u);
    load();
    motion(SPM_R8PE01_MOT_CROUCH);
    for (i = 0; i < 40u; ++i) frame();
    assert(!openspm_guest_call_pop(&g_queue, &call) && g_mod.ability_off);

    free(g_mem);
    free(g_mem2);
    puts("PASS: striker mod");
    return 0;
}
