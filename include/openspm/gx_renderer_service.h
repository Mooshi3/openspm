#ifndef OPENSPM_GX_RENDERER_SERVICE_H
#define OPENSPM_GX_RENDERER_SERVICE_H

#include "openspm/abi.h"
#include "openspm/gx_platform.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Backend-side attachment point for a native PC renderer. The renderer itself
   is a replaceable host component; the recompiled game/backend only knows this
   neutral sink contract and never depends on Aurora, Dolphin, or a particular
   graphics API. */
#define OPENSPM_GX_RENDERER_SERVICE_ID "openspm.gx-renderer"
#define OPENSPM_GX_RENDERER_SERVICE_VERSION 1u

enum {
    OPENSPM_GX_RENDERER_CAP_BIND_SINK = UINT64_C(1) << 0
};

typedef int (OPENSPM_CALL *openspm_gx_renderer_bind_fn)(
    void *context,
    const openspm_gx_renderer_sink *renderer);

typedef struct openspm_gx_renderer_service {
    openspm_abi_header header;
    uint64_t capabilities;
    void *context;
    /* Passing NULL detaches the current renderer. The backend copies the sink
       table; the renderer remains responsible for the lifetime of sink.context. */
    openspm_gx_renderer_bind_fn bind;
} openspm_gx_renderer_service;

#ifdef __cplusplus
}
#endif

#endif
