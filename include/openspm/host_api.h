#ifndef OPENSPM_HOST_API_H
#define OPENSPM_HOST_API_H

#include "openspm/abi.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef void (OPENSPM_CALL *openspm_log_fn)(void *context, openspm_log_level level, const char *message);
typedef uint64_t (OPENSPM_CALL *openspm_monotonic_ns_fn)(void *context);
typedef void (OPENSPM_CALL *openspm_profile_marker_fn)(void *context, const char *name);
/* Generic service discovery keeps game/runtime APIs out of the bootstrap ABI.
   Callers initialize the service-specific output struct header before querying. */
typedef int (OPENSPM_CALL *openspm_query_service_fn)(
    void *context,
    const char *service_id,
    uint32_t requested_version,
    void *out_service);
/* Backends emit backend-neutral runtime events through this sink. Event IDs and
   payloads are defined by the corresponding service header. */
typedef void (OPENSPM_CALL *openspm_runtime_event_sink_fn)(
    void *context,
    uint32_t event_id,
    const void *event_data);
/* Fast-forward pacing (include/openspm/guest_clock.h). The native module is
   idle until the guest time target_ns (its next VI retrace deadline). When the
   host runs the game unpaced (turbo "unlimited") it moves monotonic_ns forward
   to target_ns and returns nonzero: the module then skips its idle wait.
   Returns 0 otherwise, and the module waits as usual. */
typedef int (OPENSPM_CALL *openspm_clock_skip_to_fn)(void *context, uint64_t target_ns);

enum {
    OPENSPM_HOST_CAP_PROFILE_MARKERS = UINT64_C(1) << 0,
    OPENSPM_HOST_CAP_SERVICE_QUERY = UINT64_C(1) << 1,
    OPENSPM_HOST_CAP_RUNTIME_EVENTS = UINT64_C(1) << 2
};

enum {
    OPENSPM_LAUNCH_FLAG_BOOT_TARGET = UINT64_C(1) << 0,
    OPENSPM_LAUNCH_FLAG_DEBUG_PROFILE = UINT64_C(1) << 1,
    /* The extracted game root is immutable source input. Backends and OpenSPM
       services must place replacements, caches, patches and generated data
       outside this tree and apply them through runtime/overlay mechanisms. */
    OPENSPM_LAUNCH_FLAG_GAME_ROOT_READ_ONLY = UINT64_C(1) << 2
};

typedef struct openspm_host_api {
    openspm_abi_header header;
    uint64_t capabilities;
    void *context;
    openspm_log_fn log;
    openspm_monotonic_ns_fn monotonic_ns;
    openspm_profile_marker_fn profile_begin;
    openspm_profile_marker_fn profile_end;

    /* Appended ABI-v1 service/event fields. Older hosts may omit them. */
    openspm_query_service_fn query_service;
    openspm_runtime_event_sink_fn runtime_event;

    /* Appended fast-forward field. Older hosts omit it; NULL = always wait. */
    openspm_clock_skip_to_fn clock_skip_to;
} openspm_host_api;

typedef struct openspm_launch_desc {
    openspm_abi_header header;
    const char *app_root_utf8;
    const char *runtime_root_utf8;
    /* Read-only source tree. Never patch or replace files here on disk. */
    const char *game_root_utf8;
    /* Writable OpenSPM state: saves, caches, generated overlays, diagnostics. */
    const char *user_root_utf8;
    const char *graphics_backend_utf8;
    uint64_t flags;

    /* Appended ABI-v1 launch-profile fields. Older backends may ignore them. */
    const char *profile_id_utf8;
    const char *boot_target_utf8;
} openspm_launch_desc;

#ifdef __cplusplus
}
#endif

#endif
