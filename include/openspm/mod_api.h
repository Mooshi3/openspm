#ifndef OPENSPM_MOD_API_H
#define OPENSPM_MOD_API_H

#include "openspm/host_api.h"
#include "openspm/runtime_service.h"

#ifdef __cplusplus
extern "C" {
#endif

#define OPENSPM_MOD_QUERY_SYMBOL "openspm_mod_query"

typedef enum openspm_mod_event {
    OPENSPM_MOD_EVENT_STARTUP = 1,
    OPENSPM_MOD_EVENT_SHUTDOWN = 2,
    OPENSPM_MOD_EVENT_TICK_BEGIN = 3,
    OPENSPM_MOD_EVENT_TICK_END = 4,
    OPENSPM_MOD_EVENT_PRESENT_BEGIN = 5,
    OPENSPM_MOD_EVENT_PRESENT_END = 6,
    OPENSPM_MOD_EVENT_RUNTIME_RUNNING = 7,
    OPENSPM_MOD_EVENT_RUNTIME_STOPPED = 8
} openspm_mod_event;

typedef void (OPENSPM_CALL *openspm_mod_event_fn)(void *mod_context, openspm_mod_event event_id, const void *event_data);

typedef struct openspm_mod_api {
    openspm_abi_header header;
    const char *mod_id;
    const char *mod_version;
    uint64_t capabilities;
    void *mod_context;
    openspm_mod_event_fn on_event;
} openspm_mod_api;

/* Maps a backend runtime event (host->runtime_event, runtime_service.h) to the
   mod event a launcher delivers for it; the payload passes through unchanged
   (TICK_* carry openspm_runtime_tick_event, PRESENT_END carries
   openspm_runtime_present_event; check header.struct_size before reading).
   Returns 1 and sets *out for a known event, 0 otherwise. */
static inline int openspm_mod_event_from_runtime(uint32_t runtime_event, openspm_mod_event *out) {
    openspm_mod_event mapped;
    switch (runtime_event) {
        case OPENSPM_RUNTIME_EVENT_CORE_RUNNING: mapped = OPENSPM_MOD_EVENT_RUNTIME_RUNNING; break;
        case OPENSPM_RUNTIME_EVENT_CORE_STOPPED: mapped = OPENSPM_MOD_EVENT_RUNTIME_STOPPED; break;
        case OPENSPM_RUNTIME_EVENT_TICK_BEGIN: mapped = OPENSPM_MOD_EVENT_TICK_BEGIN; break;
        case OPENSPM_RUNTIME_EVENT_TICK_END: mapped = OPENSPM_MOD_EVENT_TICK_END; break;
        case OPENSPM_RUNTIME_EVENT_PRESENT_END: mapped = OPENSPM_MOD_EVENT_PRESENT_END; break;
        default: return 0;
    }
    if (out) *out = mapped;
    return 1;
}

typedef int (OPENSPM_CALL *openspm_mod_query_fn)(
    uint32_t requested_abi,
    const openspm_host_api *host,
    openspm_mod_api *out_api);

#ifdef __cplusplus
}
#endif

#endif
