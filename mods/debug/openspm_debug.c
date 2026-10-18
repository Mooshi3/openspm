#include "openspm/mod_api.h"
#include "openspm/runtime_service.h"
#include "openspm/guest_memory.h"

#include <stddef.h>
#include <stdio.h>
#include <string.h>

#define MEMBER_END(type, member) (offsetof(type, member) + sizeof(((type *)0)->member))

static const openspm_host_api *g_host;
static openspm_runtime_service g_runtime;
static int g_runtime_available;
static int g_logged_first_present;
static int g_logged_first_tick;
static openspm_guest_memory_service g_memory;
static int g_memory_available;

/* Logged every this many ticks (about 10 s at 60 fps). */
#define DEBUG_TICK_LOG_INTERVAL 600u

static void debug_log(openspm_log_level level, const char *message) {
    if (g_host && g_host->log) g_host->log(g_host->context, level, message);
}

static int launch_has_boot_target(const openspm_launch_desc *launch) {
    size_t required = offsetof(openspm_launch_desc, boot_target_utf8) + sizeof(launch->boot_target_utf8);
    return launch && launch->header.struct_size >= required && launch->boot_target_utf8;
}

static void discover_runtime_service(void) {
    int rc;
    g_runtime_available = 0;
    memset(&g_runtime, 0, sizeof(g_runtime));
    if (!g_host || g_host->header.struct_size < MEMBER_END(openspm_host_api, query_service) ||
        !g_host->query_service)
        return;
    g_runtime.header.struct_size = sizeof(g_runtime);
    g_runtime.header.abi_version = OPENSPM_RUNTIME_SERVICE_VERSION;
    rc = g_host->query_service(g_host->context, OPENSPM_RUNTIME_SERVICE_ID,
                               OPENSPM_RUNTIME_SERVICE_VERSION, &g_runtime);
    if (rc == OPENSPM_OK && g_runtime.get_performance) {
        g_runtime_available = 1;
        debug_log(OPENSPM_LOG_INFO,
                  "OpenSPM Debug acquired live openspm.runtime service (perf + lifecycle + true simulation ticks).\n");
    }
}

static void discover_guest_memory_service(void) {
    g_memory_available = 0;
    memset(&g_memory, 0, sizeof(g_memory));
    if (!g_host || g_host->header.struct_size < MEMBER_END(openspm_host_api, query_service) ||
        !g_host->query_service)
        return;
    g_memory.header.struct_size = sizeof(g_memory);
    g_memory.header.abi_version = OPENSPM_GUEST_MEMORY_SERVICE_VERSION;
    if (g_host->query_service(g_host->context, OPENSPM_GUEST_MEMORY_SERVICE_ID,
                              OPENSPM_GUEST_MEMORY_SERVICE_VERSION, &g_memory) == OPENSPM_OK &&
        g_memory.read32) {
        g_memory_available = 1;
        debug_log(OPENSPM_LOG_INFO, "OpenSPM Debug acquired openspm.guest_memory (valid during TICK_BEGIN).");
    }
}

/* Every DEBUG_TICK_LOG_INTERVAL ticks: the tick count and SEQ_WORK's current
   sequence (R8PE01 rev 2, 0x804D0D60 +0; 2 = in game), read through the
   guest-memory service. Read-only; this mod never writes guest memory. */
static void log_periodic_tick(const openspm_runtime_tick_event *event) {
    char message[256];
    uint32_t seq = 0u;
    int rc = OPENSPM_ERROR_UNSUPPORTED;
    if (!event || event->header.struct_size < MEMBER_END(openspm_runtime_tick_event, tick_count) ||
        event->tick_count == 0u || event->tick_count % DEBUG_TICK_LOG_INTERVAL != 0u)
        return;
    if (g_memory_available) rc = g_memory.read32(g_memory.context, OPENSPM_CHEAT_SEQ_WORK, &seq);
    if (rc == OPENSPM_OK) {
        _snprintf_s(message, sizeof(message), _TRUNCATE, "OpenSPM Debug tick=%llu seq=%u",
                    (unsigned long long)event->tick_count, (unsigned)seq);
    } else {
        _snprintf_s(message, sizeof(message), _TRUNCATE, "OpenSPM Debug tick=%llu seq=unavailable (%d)",
                    (unsigned long long)event->tick_count, rc);
    }
    debug_log(OPENSPM_LOG_INFO, message);
}

static void log_first_present(const openspm_runtime_present_event *event) {
    openspm_runtime_perf_snapshot snapshot;
    char message[384];
    int rc;
    if (g_logged_first_present || !event)
        return;
    g_logged_first_present = 1;

    if (!g_runtime_available) {
        debug_log(OPENSPM_LOG_INFO, "OpenSPM Debug received its first live present event.");
        return;
    }

    memset(&snapshot, 0, sizeof(snapshot));
    snapshot.header.struct_size = sizeof(snapshot);
    snapshot.header.abi_version = OPENSPM_RUNTIME_SERVICE_VERSION;
    rc = g_runtime.get_performance(g_runtime.runtime_context, &snapshot);
    if (rc != OPENSPM_OK) {
        debug_log(OPENSPM_LOG_WARN, "Live present arrived but runtime performance snapshot was unavailable.");
        return;
    }

    _snprintf_s(message, sizeof(message), _TRUNCATE,
                "Live present confirmed: frame=%llu present=%llu fps=%.1f vps=%.1f speed=%.1f%%.",
                (unsigned long long)event->frame_count,
                (unsigned long long)event->present_count,
                snapshot.fps, snapshot.vps, snapshot.speed * 100.0);
    debug_log(OPENSPM_LOG_INFO, message);
}

static void log_first_tick(const openspm_runtime_tick_event *event) {
    char message[256];
    if (g_logged_first_tick || !event)
        return;
    g_logged_first_tick = 1;
    _snprintf_s(message, sizeof(message), _TRUNCATE,
                "True SPM simulation tick confirmed on CPU thread: tick=%llu.",
                (unsigned long long)event->tick_count);
    debug_log(OPENSPM_LOG_INFO, message);
}

static void OPENSPM_CALL debug_event(void *mod_context, openspm_mod_event event_id, const void *event_data) {
    (void)mod_context;
    switch (event_id) {
        case OPENSPM_MOD_EVENT_STARTUP: {
            const openspm_launch_desc *launch = (const openspm_launch_desc *)event_data;
            g_logged_first_present = 0;
            g_logged_first_tick = 0;
            discover_runtime_service();
            discover_guest_memory_service();
            debug_log(OPENSPM_LOG_INFO,
                      "OpenSPM Debug loaded. Live runtime, CPU-thread simulation ticks, and internal startup-profile routing are enabled.");
            if (launch_has_boot_target(launch) && strcmp(launch->boot_target_utf8, "normal") != 0) {
                char message[384];
                _snprintf_s(message, sizeof(message), _TRUNCATE,
                            "Debug startup target requested: %s. The R8PE01 backend will route it after the first safe gameplay window.",
                            launch->boot_target_utf8);
                debug_log(OPENSPM_LOG_INFO, message);
            }
            break;
        }
        case OPENSPM_MOD_EVENT_RUNTIME_RUNNING:
            debug_log(OPENSPM_LOG_INFO, "Live runtime event: emulated core is running.");
            break;
        case OPENSPM_MOD_EVENT_TICK_BEGIN:
            log_first_tick((const openspm_runtime_tick_event *)event_data);
            log_periodic_tick((const openspm_runtime_tick_event *)event_data);
            break;
        case OPENSPM_MOD_EVENT_TICK_END:
            break;
        case OPENSPM_MOD_EVENT_PRESENT_END:
            log_first_present((const openspm_runtime_present_event *)event_data);
            break;
        case OPENSPM_MOD_EVENT_RUNTIME_STOPPED:
            debug_log(OPENSPM_LOG_INFO, "Live runtime event: emulated core stopped cleanly.");
            break;
        case OPENSPM_MOD_EVENT_SHUTDOWN:
            debug_log(OPENSPM_LOG_DEBUG, "OpenSPM Debug shutting down.");
            break;
        default:
            break;
    }
}

OPENSPM_EXPORT int OPENSPM_CALL openspm_mod_query(
    uint32_t requested_abi,
    const openspm_host_api *host,
    openspm_mod_api *out_api) {
    const size_t host_v1_size = MEMBER_END(openspm_host_api, profile_end);
    if (requested_abi != OPENSPM_ABI_VERSION || !host || !out_api ||
        host->header.abi_version != OPENSPM_ABI_VERSION || host->header.struct_size < host_v1_size ||
        out_api->header.struct_size < sizeof(*out_api)) {
        return OPENSPM_ERROR_ABI_MISMATCH;
    }
    g_host = host;
    memset(out_api, 0, sizeof(*out_api));
    out_api->header.struct_size = sizeof(*out_api);
    out_api->header.abi_version = OPENSPM_ABI_VERSION;
    out_api->mod_id = "openspm.debug";
    out_api->mod_version = "13.5-directboot-us2";
    out_api->capabilities = 0;
    out_api->mod_context = NULL;
    out_api->on_event = debug_event;
    return OPENSPM_OK;
}
