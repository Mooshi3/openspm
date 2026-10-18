#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include "renderer_loader.h"

#include <stdio.h>
#include <string.h>

/* Older Windows SDK headers can omit these declarations even when the target
   system supports the loader flags. Keep the build contract independent of
   SDK header vintage; LoadLibraryExW still validates support at runtime. */
#ifndef LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR
#define LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR 0x00000100
#endif
#ifndef LOAD_LIBRARY_SEARCH_DEFAULT_DIRS
#define LOAD_LIBRARY_SEARCH_DEFAULT_DIRS 0x00001000
#endif

static int renderer_api_valid(const openspm_renderer_api *api) {
    const uint64_t required = OPENSPM_RENDERER_CAP_GX_STREAM |
                              OPENSPM_RENDERER_CAP_RASTER_BOUNDS;
    return api != NULL &&
           api->header.struct_size >= sizeof(*api) &&
           api->header.abi_version == OPENSPM_RENDERER_API_VERSION &&
           api->renderer_id != NULL && api->renderer_id[0] != '\0' &&
           api->renderer_version != NULL && api->renderer_version[0] != '\0' &&
           (api->capabilities & required) == required &&
           api->gx_sink.submit_fifo != NULL &&
           api->gx_sink.sync != NULL &&
           api->gx_sink.read_bounds != NULL &&
           api->gx_sink.write_bound != NULL;
}

static HMODULE load_renderer_module(const wchar_t *path, DWORD *out_error) {
    HMODULE module;
    DWORD error;

    if (out_error != NULL) *out_error = ERROR_SUCCESS;

    /* The candidate deliberately keeps Aurora/Dawn/SDL and DXC dependencies
       next to openspm_renderer.dll under runtime/, while OpenSPMNative.exe lives
       one directory above. Make that layout an explicit loader contract rather
       than depending on the process current directory or legacy DLL search
       rules. LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR searches the renderer's own
       directory for its transitive dependencies; DEFAULT_DIRS retains the safe
       application/system/user-added locations. */
    module = LoadLibraryExW(path, NULL,
                            LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR |
                            LOAD_LIBRARY_SEARCH_DEFAULT_DIRS);
    if (module != NULL) return module;
    error = GetLastError();

    /* The flags above are supported on all Windows versions targeted by the
       native port. Keep a narrow fallback for older developer environments
       whose kernel rejects the search flags, without masking ordinary missing-
       dependency failures on supported systems. */
    if (error == ERROR_INVALID_PARAMETER) {
        module = LoadLibraryW(path);
        if (module != NULL) return module;
        error = GetLastError();
    }

    if (out_error != NULL) *out_error = error;
    SetLastError(error);
    return NULL;
}

int openspm_renderer_load_and_bind(
    const wchar_t *path,
    const openspm_host_api *host,
    const openspm_launch_desc *launch,
    openspm_backend_api *backend,
    openspm_loaded_renderer *out_renderer) {
    openspm_renderer_query_fn query;
    DWORD load_error = ERROR_SUCCESS;
    int result;

    if (path == NULL || path[0] == L'\0' || host == NULL || launch == NULL ||
        backend == NULL || out_renderer == NULL) {
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    }
    memset(out_renderer, 0, sizeof(*out_renderer));

    if (backend->query_service == NULL) return OPENSPM_ERROR_UNSUPPORTED;

    out_renderer->backend_service.header.struct_size =
        sizeof(out_renderer->backend_service);
    out_renderer->backend_service.header.abi_version =
        OPENSPM_GX_RENDERER_SERVICE_VERSION;
    result = backend->query_service(backend->backend_context,
                                    OPENSPM_GX_RENDERER_SERVICE_ID,
                                    OPENSPM_GX_RENDERER_SERVICE_VERSION,
                                    &out_renderer->backend_service);
    if (result != OPENSPM_OK) return result;
    if ((out_renderer->backend_service.capabilities &
         OPENSPM_GX_RENDERER_CAP_BIND_SINK) == 0u ||
        out_renderer->backend_service.bind == NULL) {
        return OPENSPM_ERROR_UNSUPPORTED;
    }

    out_renderer->module = load_renderer_module(path, &load_error);
    if (out_renderer->module == NULL) {
        if (host->log != NULL) {
            char message[160];
            (void)_snprintf_s(message, sizeof(message), _TRUNCATE,
                              "Could not load native renderer or one of its runtime dependencies (Win32 %lu).",
                              (unsigned long)load_error);
            host->log(host->context, OPENSPM_LOG_ERROR, message);
        }
        return OPENSPM_ERROR_LOAD_FAILED;
    }

    query = (openspm_renderer_query_fn)(void *)GetProcAddress(
        out_renderer->module, OPENSPM_RENDERER_QUERY_SYMBOL);
    if (query == NULL) {
        result = OPENSPM_ERROR_ABI_MISMATCH;
        goto fail;
    }

    memset(&out_renderer->api, 0, sizeof(out_renderer->api));
    out_renderer->api.header.struct_size = sizeof(out_renderer->api);
    out_renderer->api.header.abi_version = OPENSPM_RENDERER_API_VERSION;
    result = query(OPENSPM_RENDERER_API_VERSION, host, &out_renderer->api);
    if (result != OPENSPM_OK || !renderer_api_valid(&out_renderer->api)) {
        result = OPENSPM_ERROR_ABI_MISMATCH;
        goto fail;
    }

    if (out_renderer->api.initialize != NULL) {
        result = out_renderer->api.initialize(
            out_renderer->api.renderer_context, launch);
        if (result != OPENSPM_OK) goto fail;
    }
    out_renderer->initialized = 1;

    result = out_renderer->backend_service.bind(
        out_renderer->backend_service.context, &out_renderer->api.gx_sink);
    if (result != OPENSPM_OK) goto fail;
    out_renderer->bound = 1;
    return OPENSPM_OK;

fail:
    openspm_renderer_unload(out_renderer);
    return result;
}

void openspm_renderer_unload(openspm_loaded_renderer *renderer) {
    if (renderer == NULL) return;

    /* Detach before calling into renderer shutdown or unloading its DLL; the
       backend copied function pointers from that module. */
    if (renderer->bound && renderer->backend_service.bind != NULL) {
        (void)renderer->backend_service.bind(
            renderer->backend_service.context, NULL);
    }
    renderer->bound = 0;

    if (renderer->initialized && renderer->api.shutdown != NULL) {
        renderer->api.shutdown(renderer->api.renderer_context);
    }
    renderer->initialized = 0;

    if (renderer->module != NULL) FreeLibrary(renderer->module);
    memset(renderer, 0, sizeof(*renderer));
}
