#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include "renderer_loader.h"

#include <stdio.h>
#include <string.h>

typedef struct fake_backend_state {
    openspm_gx_renderer_sink sink;
    uint32_t bind_count;
    uint32_t detach_count;
} fake_backend_state;

static fake_backend_state g_backend_state;

static int OPENSPM_CALL fake_bind(void *context,
                                  const openspm_gx_renderer_sink *renderer) {
    fake_backend_state *state = (fake_backend_state *)context;
    if (state == NULL) return OPENSPM_ERROR_INVALID_ARGUMENT;
    memset(&state->sink, 0, sizeof(state->sink));
    if (renderer != NULL) {
        state->sink = *renderer;
        ++state->bind_count;
    } else {
        ++state->detach_count;
    }
    return OPENSPM_OK;
}

static int OPENSPM_CALL fake_query_service(void *context,
                                           const char *service_id,
                                           uint32_t requested_version,
                                           void *out_service) {
    openspm_gx_renderer_service *service;
    if (context == NULL || service_id == NULL || out_service == NULL)
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    if (strcmp(service_id, OPENSPM_GX_RENDERER_SERVICE_ID) != 0)
        return OPENSPM_ERROR_NOT_FOUND;
    if (requested_version != OPENSPM_GX_RENDERER_SERVICE_VERSION)
        return OPENSPM_ERROR_ABI_MISMATCH;

    service = (openspm_gx_renderer_service *)out_service;
    if (service->header.struct_size < sizeof(*service))
        return OPENSPM_ERROR_ABI_MISMATCH;
    memset(service, 0, sizeof(*service));
    service->header.struct_size = sizeof(*service);
    service->header.abi_version = OPENSPM_GX_RENDERER_SERVICE_VERSION;
    service->capabilities = OPENSPM_GX_RENDERER_CAP_BIND_SINK;
    service->context = context;
    service->bind = fake_bind;
    return OPENSPM_OK;
}

static void OPENSPM_CALL test_log(void *context,
                                  openspm_log_level level,
                                  const char *message) {
    (void)context;
    (void)level;
    (void)message;
}

static uint64_t OPENSPM_CALL test_clock(void *context) {
    (void)context;
    return 1u;
}

#define CHECK(expr) \
    do { \
        if (!(expr)) { \
            fprintf(stderr, "CHECK failed: %s at %s:%d\n", \
                    #expr, __FILE__, __LINE__); \
            rc = 20; \
            goto done; \
        } \
    } while (0)

int wmain(int argc, wchar_t **argv) {
    openspm_host_api host;
    openspm_backend_api backend;
    openspm_launch_desc launch;
    openspm_loaded_renderer renderer;
    openspm_gx_raster_bounds bounds;
    const uint8_t fifo[] = {0x61u, 0x47u, 0x12u, 0x34u, 0x56u};
    int rc = 0;

    if (argc != 2) {
        fprintf(stderr, "Expected exactly one synthetic renderer DLL path.\n");
        return 10;
    }

    memset(&g_backend_state, 0, sizeof(g_backend_state));
    memset(&host, 0, sizeof(host));
    memset(&backend, 0, sizeof(backend));
    memset(&launch, 0, sizeof(launch));
    memset(&renderer, 0, sizeof(renderer));

    host.header.struct_size = sizeof(host);
    host.header.abi_version = OPENSPM_ABI_VERSION;
    host.log = test_log;
    host.monotonic_ns = test_clock;

    backend.header.struct_size = sizeof(backend);
    backend.header.abi_version = OPENSPM_ABI_VERSION;
    backend.backend_context = &g_backend_state;
    backend.query_service = fake_query_service;

    launch.header.struct_size = sizeof(launch);
    launch.header.abi_version = OPENSPM_ABI_VERSION;
    launch.app_root_utf8 = ".";
    launch.runtime_root_utf8 = ".";
    launch.game_root_utf8 = ".";
    launch.user_root_utf8 = ".";
    launch.graphics_backend_utf8 = "AUTO";
    launch.profile_id_utf8 = "test";
    launch.boot_target_utf8 = "normal";

    CHECK(openspm_renderer_load_and_bind(argv[1], &host, &launch,
                                         &backend, &renderer) == OPENSPM_OK);
    CHECK(renderer.bound == 1 && renderer.initialized == 1);
    CHECK(g_backend_state.bind_count == 1u);
    CHECK(g_backend_state.sink.submit_fifo != NULL);
    CHECK(g_backend_state.sink.read_bounds != NULL);
    CHECK(strcmp(renderer.api.renderer_id, "openspm.synthetic-renderer") == 0);

    CHECK(g_backend_state.sink.submit_fifo(
              g_backend_state.sink.context, fifo, (uint32_t)sizeof(fifo), NULL) ==
          OPENSPM_OK);
    memset(&bounds, 0, sizeof(bounds));
    CHECK(g_backend_state.sink.read_bounds(
              g_backend_state.sink.context, &bounds) == OPENSPM_OK);
    CHECK(bounds.valid == 1u && bounds.left == 11u && bounds.right == 222u &&
          bounds.top == 33u && bounds.bottom == 244u);

done:
    openspm_renderer_unload(&renderer);
    if (rc == 0) {
        CHECK(g_backend_state.detach_count == 1u);
        puts("m14 renderer loader tests passed");
    }
    return rc;
}
