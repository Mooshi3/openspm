#include "openspm/native_loader.h"
#include "openspm/validate.h"

#include <stdlib.h>
#include <string.h>

#if defined(_WIN32)
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#else
#include <dlfcn.h>
#endif

#if defined(_WIN32)
static HMODULE openspm_platform_library_open(const char *path_utf8) {
    int count;
    wchar_t *wide;
    HMODULE module;

    if (path_utf8 == NULL || *path_utf8 == '\0') {
        return NULL;
    }
    count = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, path_utf8, -1, NULL, 0);
    if (count <= 0) {
        return NULL;
    }
    wide = (wchar_t *)malloc((size_t)count * sizeof(*wide));
    if (wide == NULL) {
        return NULL;
    }
    if (MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, path_utf8, -1, wide, count) <= 0) {
        free(wide);
        return NULL;
    }
    module = LoadLibraryW(wide);
    free(wide);
    return module;
}
#endif

int openspm_native_library_open(
    const char *path_utf8,
    const char *expected_game_id,
    const uint8_t expected_source_sha256[32],
    openspm_native_library *out_library) {
    openspm_native_module_query_fn query = NULL;
    openspm_native_module_api api;
    int result;

    if (path_utf8 == NULL || *path_utf8 == '\0' || out_library == NULL) {
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    }
    memset(out_library, 0, sizeof(*out_library));
    memset(&api, 0, sizeof(api));
    api.header.struct_size = sizeof(api);
    api.header.abi_version = OPENSPM_NATIVE_ABI_VERSION;

#if defined(_WIN32)
    {
        HMODULE handle = openspm_platform_library_open(path_utf8);
        union {
            FARPROC proc;
            openspm_native_module_query_fn function;
        } symbol;
        if (handle == NULL) {
            return OPENSPM_ERROR_LOAD_FAILED;
        }
        symbol.proc = GetProcAddress(handle, OPENSPM_NATIVE_MODULE_QUERY_SYMBOL);
        if (symbol.proc == NULL) {
            FreeLibrary(handle);
            return OPENSPM_ERROR_LOAD_FAILED;
        }
        query = symbol.function;
        out_library->platform_handle = (void *)handle;
    }
#else
    {
        void *handle = dlopen(path_utf8, RTLD_NOW | RTLD_LOCAL);
        union {
            void *object;
            openspm_native_module_query_fn function;
        } symbol;
        if (handle == NULL) {
            return OPENSPM_ERROR_LOAD_FAILED;
        }
        symbol.object = dlsym(handle, OPENSPM_NATIVE_MODULE_QUERY_SYMBOL);
        if (symbol.object == NULL) {
            dlclose(handle);
            return OPENSPM_ERROR_LOAD_FAILED;
        }
        query = symbol.function;
        out_library->platform_handle = handle;
    }
#endif

    result = query(OPENSPM_NATIVE_ABI_VERSION, &api);
    if (result != OPENSPM_OK) {
        openspm_native_library_close(out_library);
        return result;
    }
    result = openspm_validate_native_module_api(&api);
    if (result != OPENSPM_OK) {
        openspm_native_library_close(out_library);
        return result;
    }

    if (expected_game_id != NULL && strcmp(api.source_game_id, expected_game_id) != 0) {
        openspm_native_library_close(out_library);
        return OPENSPM_ERROR_SOURCE_MISMATCH;
    }
    if (expected_source_sha256 != NULL &&
        memcmp(api.source_sha256, expected_source_sha256, sizeof(api.source_sha256)) != 0) {
        openspm_native_library_close(out_library);
        return OPENSPM_ERROR_SOURCE_MISMATCH;
    }

    out_library->module = api;
    return OPENSPM_OK;
}

void openspm_native_library_close(openspm_native_library *library) {
    if (library == NULL) {
        return;
    }
#if defined(_WIN32)
    if (library->platform_handle != NULL) {
        FreeLibrary((HMODULE)library->platform_handle);
    }
#else
    if (library->platform_handle != NULL) {
        dlclose(library->platform_handle);
    }
#endif
    memset(library, 0, sizeof(*library));
}
