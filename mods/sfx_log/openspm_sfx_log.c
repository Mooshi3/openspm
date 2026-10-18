/* OpenSPM sample mod: "SFX Log", the first user of function hooks
 * (openspm.hooks, docs/HOOKS.md). It hooks spsndSFXOn, the function the game
 * starts most sound effects with, and logs each sound name the first time
 * the game plays it, plus how many sounds started per minute. Nothing in the
 * game changes (a pre callback that only reads).
 *
 * Needs the spsndSFXOn entry of state/openspm-hooks.json enabled (and the
 * rebuilt native module); otherwise it logs that once and does nothing.
 *
 * Portable C: Windows DLL for OpenSPMNative, host object for
 * tests/m14_sfx_log_mod_test.c. */
#include "openspm/guest_memory.h"
#include "openspm/hooks.h"
#include "openspm/mod_api.h"

#include <stddef.h>
#include <stdio.h>
#include <string.h>

#define MEMBER_END(type, member) (offsetof(type, member) + sizeof(((type *)0)->member))
#define SFX_LOG_NAMES 512u
#define SFX_LOG_NAME 48u
#define SFX_LOG_MINUTE_TICKS 3600u

typedef struct sfx_log_mod {
    const openspm_host_api *host;
    openspm_hook_service hooks;
    openspm_guest_memory_service mem;
    int on;
    uint32_t hashes[SFX_LOG_NAMES], known, started, minute_started, ticks;
} sfx_log_mod;

static sfx_log_mod g_mod;

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

static uint32_t fnv1a(const char *s) {
    uint32_t h = 2166136261u;
    while (*s) h = (h ^ (uint8_t)*s++) * 16777619u;
    return h ? h : 1u;
}

static void read_name(uint32_t address, char *out) {
    uint32_t i;
    out[0] = 0;
    if (address < 0x80000000u || !g_mod.mem.read8) return;
    for (i = 0; i + 1u < SFX_LOG_NAME; ++i) {
        uint8_t c = 0u;
        if (g_mod.mem.read8(g_mod.mem.context, address + i, &c) != OPENSPM_OK) break;
        if (c == 0u) break;
        out[i] = (char)(c >= 32u && c < 127u ? c : '?');
    }
    out[i] = 0;
}

/* spsndSFXOn(const char *name): r3 = the sound's name. */
static void OPENSPM_CALL on_sfx(void *user, openspm_hook_call *call) {
    char name[SFX_LOG_NAME], message[96];
    uint32_t h, i;
    (void)user;
    ++g_mod.started;
    ++g_mod.minute_started;
    read_name(call->args[0], name);
    if (!name[0]) return;
    h = fnv1a(name);
    for (i = 0; i < g_mod.known; ++i)
        if (g_mod.hashes[i] == h) return;
    if (g_mod.known < SFX_LOG_NAMES) g_mod.hashes[g_mod.known++] = h;
    snprintf(message, sizeof message, "SFX Log: %s (sound #%u, %u different so far)", name, (unsigned)g_mod.started,
             (unsigned)g_mod.known);
    mod_log(OPENSPM_LOG_INFO, message);
}

static void startup(void) {
    uint32_t id = 0u;
    if (!query(OPENSPM_HOOK_SERVICE_ID, OPENSPM_HOOK_SERVICE_VERSION, &g_mod.hooks, sizeof g_mod.hooks) ||
        !g_mod.hooks.find || !g_mod.hooks.add_pre) {
        mod_log(OPENSPM_LOG_WARN, "SFX Log: this OpenSPM has no openspm.hooks; off.");
        return;
    }
    if (!query(OPENSPM_GUEST_MEMORY_SERVICE_ID, OPENSPM_GUEST_MEMORY_SERVICE_VERSION, &g_mod.mem, sizeof g_mod.mem))
        memset(&g_mod.mem, 0, sizeof g_mod.mem);
    if (g_mod.hooks.find(g_mod.hooks.context, "spsndSFXOn", &id) != OPENSPM_OK) {
        mod_log(OPENSPM_LOG_INFO, "SFX Log: the spsndSFXOn hook is not enabled in this build "
                                  "(state/openspm-hooks.json); nothing to log.");
        return;
    }
    if (g_mod.hooks.add_pre(g_mod.hooks.context, id, on_sfx, NULL) != OPENSPM_OK) {
        mod_log(OPENSPM_LOG_WARN, "SFX Log: could not hook spsndSFXOn; off.");
        return;
    }
    g_mod.on = 1;
    mod_log(OPENSPM_LOG_INFO, "SFX Log: logging each sound effect name the first time the game plays it.");
}

static void tick(void) {
    char message[96];
    if (!g_mod.on || ++g_mod.ticks < SFX_LOG_MINUTE_TICKS) return;
    snprintf(message, sizeof message, "SFX Log: %u sounds started in the last minute (%u in all).",
             (unsigned)g_mod.minute_started, (unsigned)g_mod.started);
    mod_log(OPENSPM_LOG_INFO, message);
    g_mod.ticks = 0u;
    g_mod.minute_started = 0u;
}

static void OPENSPM_CALL sfx_log_event(void *context, openspm_mod_event event_id, const void *event_data) {
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
    memset(out_api, 0, sizeof(*out_api));
    out_api->header.struct_size = sizeof(*out_api);
    out_api->header.abi_version = OPENSPM_ABI_VERSION;
    out_api->mod_id = "openspm.sample.sfx_log";
    out_api->mod_version = "1.0";
    out_api->on_event = sfx_log_event;
    return OPENSPM_OK;
}
