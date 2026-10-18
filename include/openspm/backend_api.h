#ifndef OPENSPM_BACKEND_API_H
#define OPENSPM_BACKEND_API_H

#include "openspm/host_api.h"

#ifdef __cplusplus
extern "C" {
#endif

#define OPENSPM_BACKEND_QUERY_SYMBOL "openspm_backend_query"

enum {
    OPENSPM_BACKEND_CAP_LEGACY_PROCESS = UINT64_C(1) << 0,
    OPENSPM_BACKEND_CAP_IN_PROCESS = UINT64_C(1) << 1,
    OPENSPM_BACKEND_CAP_MODS = UINT64_C(1) << 2,
    OPENSPM_BACKEND_CAP_PROFILING = UINT64_C(1) << 3,
    OPENSPM_BACKEND_CAP_RENDER_SNAPSHOTS = UINT64_C(1) << 4,
    OPENSPM_BACKEND_CAP_HIGH_REFRESH = UINT64_C(1) << 5,
    OPENSPM_BACKEND_CAP_RUNTIME_SERVICE = UINT64_C(1) << 6
};

typedef int (OPENSPM_CALL *openspm_backend_run_fn)(void *backend_context, const openspm_launch_desc *launch);
typedef void (OPENSPM_CALL *openspm_backend_shutdown_fn)(void *backend_context);
typedef int (OPENSPM_CALL *openspm_backend_query_service_fn)(
    void *backend_context,
    const char *service_id,
    uint32_t requested_version,
    void *out_service);

typedef struct openspm_backend_api {
    openspm_abi_header header;
    const char *backend_id;
    const char *backend_version;
    uint64_t capabilities;
    void *backend_context;
    openspm_backend_run_fn run;
    openspm_backend_shutdown_fn shutdown;

    /* Appended ABI-v1 field. Null means this backend exposes no services. */
    openspm_backend_query_service_fn query_service;
} openspm_backend_api;

typedef int (OPENSPM_CALL *openspm_backend_query_fn)(
    uint32_t requested_abi,
    const openspm_host_api *host,
    openspm_backend_api *out_api);

#ifdef __cplusplus
}
#endif

#endif
