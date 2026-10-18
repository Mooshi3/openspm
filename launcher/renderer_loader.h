#ifndef OPENSPM_RENDERER_LOADER_H
#define OPENSPM_RENDERER_LOADER_H

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include "openspm/backend_api.h"
#include "openspm/gx_renderer_service.h"
#include "openspm/renderer_api.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct openspm_loaded_renderer {
    HMODULE module;
    openspm_renderer_api api;
    openspm_gx_renderer_service backend_service;
    int initialized;
    int bound;
} openspm_loaded_renderer;

int openspm_renderer_load_and_bind(
    const wchar_t *path,
    const openspm_host_api *host,
    const openspm_launch_desc *launch,
    openspm_backend_api *backend,
    openspm_loaded_renderer *out_renderer);

void openspm_renderer_unload(openspm_loaded_renderer *renderer);

#ifdef __cplusplus
}
#endif

#endif
