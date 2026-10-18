/* Host test for the SFX Log sample mod (mods/sfx_log): through the real hook
   runtime (include/openspm/hooks.h) behind a fake openspm.hooks service, a
   call to spsndSFXOn logs the sound's name once per name, the call continues
   into the game untouched, and a build without the hook turns the mod off. */
#include "../mods/sfx_log/openspm_sfx_log.c"

#include <assert.h>

static uint8_t g_ram[0x1000];
static openspm_hook_runtime g_rt;
static char g_last[160];
static int g_logs;

static int OPENSPM_CALL f_read8(void *c, uint32_t a, uint8_t *v) {
    (void)c;
    if (a < 0x80000000u || a >= 0x80000000u + sizeof g_ram) return OPENSPM_ERROR_INVALID_ARGUMENT;
    *v = g_ram[a - 0x80000000u];
    return OPENSPM_OK;
}
static int OPENSPM_CALL f_find(void *c, const char *n, uint32_t *id) { (void)c; return openspm_hooks_find(&g_rt, n, id); }
static int OPENSPM_CALL f_add(void *c, uint32_t id, openspm_hook_fn fn, void *u) {
    (void)c;
    return openspm_hooks_add_pre(&g_rt, id, fn, u);
}
static int OPENSPM_CALL f_query(void *context, const char *id, uint32_t version, void *out) {
    (void)context;
    if (strcmp(id, OPENSPM_HOOK_SERVICE_ID) == 0) {
        openspm_hook_service *s = out;
        if (openspm_hook_accept(s, version) != OPENSPM_OK) return OPENSPM_ERROR_ABI_MISMATCH;
        s->find = f_find;
        s->add_pre = f_add;
        return OPENSPM_OK;
    }
    if (strcmp(id, OPENSPM_GUEST_MEMORY_SERVICE_ID) == 0) {
        openspm_guest_memory_service *s = out;
        s->read8 = f_read8;
        return OPENSPM_OK;
    }
    return OPENSPM_ERROR_NOT_FOUND;
}
static int fake_image(void *c, uint32_t a, uint32_t *v) {
    (void)c;
    if (a != 0x8023950Cu) return 0;
    *v = 0x3880FFFFu; /* li r4, -1: spsndSFXOn's first instruction */
    return 1;
}
static void OPENSPM_CALL f_log(void *c, openspm_log_level l, const char *t) {
    (void)c; (void)l;
    snprintf(g_last, sizeof g_last, "%s", t);
    ++g_logs;
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

static void play(uint32_t name) {
    openspm_m14_hostcall_state s;
    memset(&s, 0, sizeof s);
    s.gpr[3] = name;
    s.lr = 0x80111111u;
    assert(openspm_hooks_dispatch(&g_rt, 0u, &s) == 0);          /* continues into the game */
    assert(s.gpr[3] == name && s.pc == 0x8023950Cu);             /* untouched */
}

int main(void) {
    static const openspm_hook_entry on[2] = {{UINT32_C(0x8023950C), "spsndSFXOn", UINT32_C(0x3880FFFF)}, {0, "", 0}};
    int logs;
    memcpy(g_ram + 0x100, "SFX_P_MARIO_JUMP1", 18);
    memcpy(g_ram + 0x200, "SFX_P_MARIO_LAND1", 18);

    /* A build with the hook: each name once. */
    openspm_hooks_init(&g_rt, on, 1u);
    load();
    assert(strstr(g_last, "logging each sound") != NULL);
    assert(openspm_hooks_verify(&g_rt, fake_image, NULL) == 1u); /* the backend's first hooked call */
    play(0x80000100u);
    assert(strstr(g_last, "SFX_P_MARIO_JUMP1") && strstr(g_last, "sound #1"));
    logs = g_logs;
    play(0x80000100u);
    assert(g_logs == logs); /* already known */
    play(0x80000200u);
    assert(strstr(g_last, "SFX_P_MARIO_LAND1") && strstr(g_last, "2 different"));
    {
        uint32_t i;
        for (i = 0; i < SFX_LOG_MINUTE_TICKS; ++i) g_api.on_event(g_api.mod_context, OPENSPM_MOD_EVENT_TICK_BEGIN, NULL);
        assert(strstr(g_last, "3 sounds started in the last minute") != NULL);
    }

    /* A build without it: off, says so. */
    openspm_hooks_init(&g_rt, on, 0u);
    load();
    assert(strstr(g_last, "not enabled in this build") != NULL && !g_mod.on);
    puts("PASS: SFX Log mod");
    return 0;
}
