#include "openspm/renderer_api.h"

#include <string.h>

typedef struct synthetic_renderer_state {
    uint32_t initialized;
    uint64_t fifo_bytes;
    uint32_t sync_count;
    uint32_t last_sync_reason;
    openspm_gx_raster_bounds bounds;
} synthetic_renderer_state;

static synthetic_renderer_state g_renderer;

static int OPENSPM_CALL synthetic_submit(
    void *context,
    const uint8_t *bytes,
    uint32_t size,
    const openspm_gx_guest_memory *guest_memory) {
    synthetic_renderer_state *state = (synthetic_renderer_state *)context;
    (void)guest_memory;
    if (state == NULL || (size != 0u && bytes == NULL) || !state->initialized)
        return OPENSPM_ERROR_RUNTIME;
    state->fifo_bytes += size;
    return OPENSPM_OK;
}

static int OPENSPM_CALL synthetic_sync(
    void *context,
    uint32_t reason) {
    synthetic_renderer_state *state = (synthetic_renderer_state *)context;
    if (state == NULL || !state->initialized)
        return OPENSPM_ERROR_RUNTIME;
    state->sync_count += 1u;
    state->last_sync_reason = reason;
    return OPENSPM_OK;
}

static int OPENSPM_CALL synthetic_bounds(
    void *context,
    openspm_gx_raster_bounds *out_bounds) {
    synthetic_renderer_state *state = (synthetic_renderer_state *)context;
    if (state == NULL || out_bounds == NULL || !state->initialized)
        return OPENSPM_ERROR_RUNTIME;
    *out_bounds = state->bounds;
    return OPENSPM_OK;
}

static int OPENSPM_CALL synthetic_write_bound(
    void *context,
    uint32_t address,
    uint16_t value) {
    synthetic_renderer_state *state = (synthetic_renderer_state *)context;
    if (state == NULL || !state->initialized)
        return OPENSPM_ERROR_RUNTIME;
    if (address == OPENSPM_GX_PE_BBOX_LEFT) state->bounds.left = value;
    else if (address == OPENSPM_GX_PE_BBOX_RIGHT) state->bounds.right = value;
    else if (address == OPENSPM_GX_PE_BBOX_TOP) state->bounds.top = value;
    else if (address == OPENSPM_GX_PE_BBOX_BOTTOM) state->bounds.bottom = value;
    else return OPENSPM_ERROR_INVALID_ARGUMENT;
    state->bounds.valid = 1u;
    return OPENSPM_OK;
}

static int OPENSPM_CALL synthetic_initialize(
    void *context,
    const openspm_launch_desc *launch) {
    synthetic_renderer_state *state = (synthetic_renderer_state *)context;
    if (state == NULL || launch == NULL || launch->graphics_backend_utf8 == NULL)
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    memset(state, 0, sizeof(*state));
    state->initialized = 1u;
    state->bounds.left = 11u;
    state->bounds.right = 222u;
    state->bounds.top = 33u;
    state->bounds.bottom = 244u;
    state->bounds.valid = 1u;
    return OPENSPM_OK;
}

static void OPENSPM_CALL synthetic_shutdown(void *context) {
    synthetic_renderer_state *state = (synthetic_renderer_state *)context;
    if (state != NULL) state->initialized = 0u;
}

OPENSPM_EXPORT int OPENSPM_CALL openspm_renderer_query(
    uint32_t requested_version,
    const openspm_host_api *host,
    openspm_renderer_api *out_api) {
    (void)host;
    if (out_api == NULL ||
        requested_version != OPENSPM_RENDERER_API_VERSION ||
        out_api->header.struct_size < sizeof(*out_api)) {
        return OPENSPM_ERROR_ABI_MISMATCH;
    }

    memset(&g_renderer, 0, sizeof(g_renderer));
    memset(out_api, 0, sizeof(*out_api));
    out_api->header.struct_size = sizeof(*out_api);
    out_api->header.abi_version = OPENSPM_RENDERER_API_VERSION;
    out_api->renderer_id = "openspm.synthetic-renderer";
    out_api->renderer_version = "1";
    out_api->capabilities = OPENSPM_RENDERER_CAP_GX_STREAM |
                            OPENSPM_RENDERER_CAP_RASTER_BOUNDS;
    out_api->renderer_context = &g_renderer;
    out_api->gx_sink.context = &g_renderer;
    out_api->gx_sink.submit_fifo = synthetic_submit;
    out_api->gx_sink.sync = synthetic_sync;
    out_api->gx_sink.read_bounds = synthetic_bounds;
    out_api->gx_sink.write_bound = synthetic_write_bound;
    out_api->initialize = synthetic_initialize;
    out_api->shutdown = synthetic_shutdown;
    return OPENSPM_OK;
}
