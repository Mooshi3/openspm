/* OpenSPM sample mod: "Flint Follower", Flint Cragley tagging along through
 * the whole game (openspm.content followers, docs/CONTENT-API.md).
 *
 * The whole mod is one follower definition: OpenSPM makes him as a game NPC
 * with his model, walks him along the player's trail (running when he falls
 * behind), keeps him on the ground, and makes him again after every door,
 * pipe or cutscene that clears him away (include/openspm/content_followers.h).
 * Cutscenes he has no business in are the point.
 *
 * His model is the explorer of chapter 5 (n_stg5_hiroshi, next to his
 * expedition members n_stg5_taiin_a / _b). The first time he appears the log
 * lists the model's animations ("... has <n> animations: ..."); if stand / walk
 * / run look wrong, set the names below from that list.
 *
 * Portable C: Windows DLL for OpenSPMNative, host object for tests. */
#include "openspm/content_service.h"
#include "openspm/mod_api.h"

#include <stddef.h>
#include <stdio.h>
#include <string.h>

#define MEMBER_END(type, member) (offsetof(type, member) + sizeof(((type *)0)->member))

#define FLINT_MODEL "n_stg5_hiroshi"

static const openspm_host_api *g_host;
static openspm_content_service g_content;
static uint32_t g_follower;

static void mod_log(openspm_log_level level, const char *message) {
    if (g_host && g_host->log) g_host->log(g_host->context, level, message);
}

static void startup(void) {
    openspm_follower_def def;
    openspm_abi_header *header = &g_content.header;
    if (!g_host || g_host->header.struct_size < MEMBER_END(openspm_host_api, query_service) || !g_host->query_service)
        return;
    memset(&g_content, 0, sizeof g_content);
    header->struct_size = sizeof g_content;
    header->abi_version = OPENSPM_CONTENT_SERVICE_VERSION;
    if (g_host->query_service(g_host->context, OPENSPM_CONTENT_SERVICE_ID, OPENSPM_CONTENT_SERVICE_VERSION, &g_content) !=
            OPENSPM_OK ||
        g_content.header.struct_size < MEMBER_END(openspm_content_service, set_follower) || !g_content.register_follower) {
        mod_log(OPENSPM_LOG_WARN, "Flint Follower: this OpenSPM has no followers; off.");
        return;
    }
    memset(&def, 0, sizeof def);
    def.header.struct_size = sizeof def;
    def.header.abi_version = OPENSPM_CONTENT_SERVICE_VERSION;
    def.key = "openspm.sample.flint_follower:flint";
    def.model = FLINT_MODEL;
    def.stand_anim = "S_1";
    def.walk_anim = "W_1";
    def.run_anim = "R_1";
    def.distance = 50.0f; /* a little behind the Pixl */
    if (g_content.register_follower(g_content.context, &def, &g_follower) == OPENSPM_OK)
        mod_log(OPENSPM_LOG_INFO, "Flint Follower: Flint Cragley follows you everywhere now.");
}

static void OPENSPM_CALL flint_event(void *context, openspm_mod_event event_id, const void *event_data) {
    (void)context;
    (void)event_data;
    if (event_id == OPENSPM_MOD_EVENT_STARTUP) startup();
}

OPENSPM_EXPORT int OPENSPM_CALL openspm_mod_query(uint32_t requested_abi, const openspm_host_api *host,
                                                  openspm_mod_api *out_api) {
    if (requested_abi != OPENSPM_ABI_VERSION || !host || !out_api || host->header.abi_version != OPENSPM_ABI_VERSION ||
        out_api->header.struct_size < sizeof(*out_api))
        return OPENSPM_ERROR_ABI_MISMATCH;
    g_host = host;
    memset(out_api, 0, sizeof(*out_api));
    out_api->header.struct_size = sizeof(*out_api);
    out_api->header.abi_version = OPENSPM_ABI_VERSION;
    out_api->mod_id = "openspm.sample.flint_follower";
    out_api->mod_version = "1.0";
    out_api->on_event = flint_event;
    return OPENSPM_OK;
}
