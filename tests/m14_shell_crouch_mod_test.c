/* Host test for the Shell Crouch sample mod (mods/shell_crouch): a fake host
   provides guest memory, the guest-call queue and input, and the test plays
   frames: revision check, the script it writes, the crouch trigger, facing,
   cooldown, frozen world, hotkey and a wrong game revision. */
#include "../mods/shell_crouch/openspm_shell_crouch.c"

#include <assert.h>
#include <stdlib.h>

#define MEM1_BASE 0x80000000u
#define MEM1_SIZE 0x01800000u
#define MEM2_BASE 0x90000000u
#define MEM2_SIZE 0x04000000u

static uint8_t *g_mem1, *g_mem2;
static openspm_guest_call_queue g_queue;
static uint32_t g_scratch_used;
static int g_hotkey;
static int g_log_count;
static char g_last_log[256];

static uint8_t *fake_ptr(uint32_t a, uint32_t n) {
    if (a >= MEM1_BASE && a + n <= MEM1_BASE + MEM1_SIZE) return g_mem1 + (a - MEM1_BASE);
    if (a >= MEM2_BASE && a + n <= MEM2_BASE + MEM2_SIZE) return g_mem2 + (a - MEM2_BASE);
    return NULL;
}
static void put32(uint32_t a, uint32_t v) {
    uint8_t *p = fake_ptr(a, 4);
    p[0] = (uint8_t)(v >> 24); p[1] = (uint8_t)(v >> 16); p[2] = (uint8_t)(v >> 8); p[3] = (uint8_t)v;
}
static uint32_t get32(uint32_t a) {
    const uint8_t *p = fake_ptr(a, 4);
    return (uint32_t)p[0] << 24 | (uint32_t)p[1] << 16 | (uint32_t)p[2] << 8 | p[3];
}
static void put16(uint32_t a, uint16_t v) {
    uint8_t *p = fake_ptr(a, 2);
    p[0] = (uint8_t)(v >> 8); p[1] = (uint8_t)v;
}

static int OPENSPM_CALL f_read32(void *c, uint32_t a, uint32_t *v) {
    (void)c;
    if (!openspm_guest_address_ok(a, 4) || !fake_ptr(a, 4)) return OPENSPM_ERROR_INVALID_ARGUMENT;
    *v = get32(a);
    return OPENSPM_OK;
}
static int OPENSPM_CALL f_read16(void *c, uint32_t a, uint16_t *v) {
    const uint8_t *p;
    (void)c;
    if (!openspm_guest_address_ok(a, 2) || !(p = fake_ptr(a, 2))) return OPENSPM_ERROR_INVALID_ARGUMENT;
    *v = (uint16_t)(p[0] << 8 | p[1]);
    return OPENSPM_OK;
}
static int OPENSPM_CALL f_write32(void *c, uint32_t a, uint32_t v) {
    (void)c;
    if (!openspm_guest_address_ok(a, 4) || !fake_ptr(a, 4)) return OPENSPM_ERROR_INVALID_ARGUMENT;
    put32(a, v);
    return OPENSPM_OK;
}
static int OPENSPM_CALL f_call(void *c, uint32_t t, uint32_t n, const uint32_t *args) {
    (void)c;
    return openspm_guest_call_push(&g_queue, t, n, args);
}
static int OPENSPM_CALL f_scratch(void *c, uint32_t size, uint32_t *out) {
    (void)c;
    return openspm_guest_scratch_take(&g_scratch_used, size, out);
}
static int OPENSPM_CALL f_key(void *c, const char *name) {
    (void)c;
    return strcmp(name, "F6") == 0 ? g_hotkey : 0;
}

static int OPENSPM_CALL f_query(void *context, const char *id, uint32_t version, void *out) {
    (void)context;
    if (strcmp(id, OPENSPM_GUEST_MEMORY_SERVICE_ID) == 0) {
        openspm_guest_memory_service *s = out;
        if (openspm_guest_memory_accept(s, version) != OPENSPM_OK) return OPENSPM_ERROR_ABI_MISMATCH;
        s->read32 = f_read32;
        s->read16 = f_read16;
        s->write32 = f_write32;
        return OPENSPM_OK;
    }
    if (strcmp(id, OPENSPM_GUEST_CALL_SERVICE_ID) == 0) {
        openspm_guest_call_service *s = out;
        if (openspm_guest_call_accept(s, version) != OPENSPM_OK) return OPENSPM_ERROR_ABI_MISMATCH;
        s->call = f_call;
        s->scratch = f_scratch;
        return OPENSPM_OK;
    }
    if (strcmp(id, OPENSPM_INPUT_SERVICE_ID) == 0) {
        openspm_input_service *s = out;
        if (openspm_input_accept(s, version) != OPENSPM_OK) return OPENSPM_ERROR_ABI_MISMATCH;
        s->key_down = f_key;
        return OPENSPM_OK;
    }
    return OPENSPM_ERROR_NOT_FOUND; /* no overlay: the mod must cope */
}

static void OPENSPM_CALL f_log(void *c, openspm_log_level level, const char *text) {
    (void)c;
    (void)level;
    ++g_log_count;
    snprintf(g_last_log, sizeof g_last_log, "%s", text);
}

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

static void frame(void) {
    openspm_runtime_tick_event tick;
    memset(&tick, 0, sizeof tick);
    tick.header.struct_size = sizeof tick;
    g_api.on_event(g_api.mod_context, OPENSPM_MOD_EVENT_TICK_BEGIN, &tick);
}

static void game_state(uint32_t seq, uint32_t level, uint16_t motion) {
    put32(SPM_R8PE01_SEQ_WORK, seq);
    put32(SPM_R8PE01_SYSTEM_LEVEL, level);
    put16(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_MOTION_ID, motion);
}

static int take_call(openspm_guest_call_entry *out) { return openspm_guest_call_pop(&g_queue, out); }

static void put_f32(uint32_t a, float f) {
    uint32_t bits;
    memcpy(&bits, &f, sizeof bits);
    put32(a, bits);
}

int main(void) {
    openspm_guest_call_entry call;
    uint32_t script, i, w = 0;
    const uint32_t tmpl = SPM_R8PE01_NPC_TEMPLATES + SPM_R8PE01_TEMPLATE_STRIKER_SHELL * SPM_R8PE01_NPC_TEMPLATE_SIZE;
    const uint32_t xpos = SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_POSITION;
    g_mem1 = calloc(1, MEM1_SIZE);
    g_mem2 = calloc(1, MEM2_SIZE);
    assert(g_mem1 && g_mem2);

    /* The revision's Striker shell template. */
    put32(tmpl + SPM_R8PE01_NPC_TEMPLATE_TRIBE, SPM_R8PE01_TRIBE_STRIKER_SHELL);
    put32(tmpl + SPM_R8PE01_NPC_TEMPLATE_KICK_SCRIPT, SPM_R8PE01_KOOPA_KICK_SCRIPT);
    load();

    /* Title screen: nothing built, nothing called. */
    game_state(0u, 0u, SPM_R8PE01_MOT_STAY);
    for (i = 0; i < 30; ++i) frame();
    assert(g_scratch_used == 0u && !take_call(&call));

    /* In game, standing: the script is built once, no call. */
    game_state(2u, 0u, SPM_R8PE01_MOT_STAY);
    put_f32(xpos, 100.0f);
    frame();
    assert(g_scratch_used != 0u && !take_call(&call));
    script = OPENSPM_GUEST_SCRATCH_BEGIN;
#define W(v) assert(get32(script + 4u * w++) == (uint32_t)(v))
    W((4u << 16) | SPM_EVT_OP_USER_FUNC); W(0x800EFB64u); W(-30000000); W(-29999999); W(-29999998);
    W((2u << 16) | SPM_EVT_OP_ADD); W(-30000000); W(SHELL_AHEAD);
    W((2u << 16) | SPM_EVT_OP_ADD); W(-29999999); W(SHELL_ABOVE);
    W((8u << 16) | SPM_EVT_OP_USER_FUNC); W(0x80106174u); W(0); W(129); W(-30000000); W(-29999999);
    W(-29999998); W(-29999990); W(-270000000);
    W(SPM_EVT_OP_END_EVT); W(SPM_EVT_OP_END_SCRIPT);

    /* A short crouch does nothing; 12 frames call evtEntry(script, 0, 0). */
    game_state(2u, 0u, SPM_R8PE01_MOT_CROUCH);
    for (i = 0; i < SHELL_CROUCH_FRAMES - 1u; ++i) frame();
    assert(!take_call(&call));
    frame();
    assert(take_call(&call) && call.target == SPM_R8PE01_EVT_ENTRY && call.argc == 3u);
    assert(call.args[0] == script && call.args[1] == 0u && call.args[2] == 0u);
    assert(get32(script + 28u) == SHELL_AHEAD); /* never moved yet: facing right */

    /* Holding the crouch never drops another; walking left then crouching
       after the cooldown drops one on the left. */
    for (i = 0; i < 200; ++i) frame();
    assert(!take_call(&call));
    game_state(2u, 0u, SPM_R8PE01_MOT_WALK);
    for (i = 0; i < 5; ++i) {
        put_f32(xpos, 100.0f - 2.0f * (float)(i + 1));
        frame();
    }
    game_state(2u, 0u, SPM_R8PE01_MOT_CROUCH);
    for (i = 0; i < SHELL_CROUCH_FRAMES; ++i) frame();
    assert(take_call(&call) && call.args[0] == script);
    assert(get32(script + 28u) == (uint32_t)-SHELL_AHEAD);
    /* A crouch begun during the cooldown waits for the next crouch. */
    game_state(2u, 0u, SPM_R8PE01_MOT_STAY);
    frame();
    game_state(2u, 0u, SPM_R8PE01_MOT_CROUCH);
    for (i = 0; i < SHELL_COOLDOWN_FRAMES + 30u; ++i) frame();
    assert(!take_call(&call));

    /* Frozen world (menu, cutscene): no shell even with the hotkey. */
    game_state(2u, 1u, SPM_R8PE01_MOT_STAY);
    g_hotkey = 1;
    for (i = 0; i < 5; ++i) frame();
    assert(!take_call(&call));
    g_hotkey = 0;
    frame();
    /* Normal play: the hotkey edge drops one. */
    game_state(2u, 0u, SPM_R8PE01_MOT_STAY);
    g_hotkey = 1;
    frame();
    assert(take_call(&call));
    frame();
    assert(!take_call(&call));
    g_hotkey = 0;
    assert(g_scratch_used == 96u); /* 22 words, built exactly once */

    /* Another revision: template 129 is not the Striker shell -> off, logged. */
    memset(g_mem2, 0, MEM2_SIZE);
    g_scratch_used = 0u;
    put32(tmpl + SPM_R8PE01_NPC_TEMPLATE_TRIBE, 11u);
    load();
    game_state(2u, 0u, SPM_R8PE01_MOT_CROUCH);
    for (i = 0; i < 40; ++i) frame();
    assert(!take_call(&call) && g_scratch_used == 0u && strstr(g_last_log, "off") != NULL);

    free(g_mem1);
    free(g_mem2);
    puts("PASS: shell crouch mod");
    return 0;
}
