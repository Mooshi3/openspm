#ifndef OPENSPM_RENDERER_API_H
#define OPENSPM_RENDERER_API_H

#include "openspm/gx_platform.h"
#include "openspm/host_api.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Renderer plugins are ordinary PC-native graphics components. They are not
   game-code modules and they do not own the recompiled CPU runtime. Keeping
   this ABI separate lets OpenSPM swap Aurora/WebGPU for a later game-aware
   renderer without changing the native SPM backend. */
#define OPENSPM_RENDERER_QUERY_SYMBOL "openspm_renderer_query"
#define OPENSPM_RENDERER_API_VERSION 1u

enum {
    OPENSPM_RENDERER_CAP_GX_STREAM = UINT64_C(1) << 0,
    OPENSPM_RENDERER_CAP_RASTER_BOUNDS = UINT64_C(1) << 1,
    OPENSPM_RENDERER_CAP_PRESENT = UINT64_C(1) << 2
};

typedef int (OPENSPM_CALL *openspm_renderer_initialize_fn)(
    void *renderer_context,
    const openspm_launch_desc *launch);

typedef void (OPENSPM_CALL *openspm_renderer_shutdown_fn)(
    void *renderer_context);

typedef struct openspm_renderer_api {
    openspm_abi_header header;
    const char *renderer_id;
    const char *renderer_version;
    uint64_t capabilities;
    void *renderer_context;

    /* The backend copies this sink when the launcher binds the renderer. The
       renderer DLL must therefore remain loaded until it has been detached. */
    openspm_gx_renderer_sink gx_sink;

    /* initialize is called after launch configuration is known and before the
       sink is bound to the backend. shutdown is called after detaching it. */
    openspm_renderer_initialize_fn initialize;
    openspm_renderer_shutdown_fn shutdown;
} openspm_renderer_api;

typedef int (OPENSPM_CALL *openspm_renderer_query_fn)(
    uint32_t requested_version,
    const openspm_host_api *host,
    openspm_renderer_api *out_api);

#ifdef __cplusplus
}
#endif

#endif
