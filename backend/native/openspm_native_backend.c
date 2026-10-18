#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include "openspm/backend_api.h"
#include "openspm/dol_image.h"
#include "openspm/native_loader.h"
#include "openspm/native_runtime.h"
#include "openspm/sha256.h"
#include "openspm/validate.h"
#include "openspm/wii_cpu_service.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define OPENSPM_NATIVE_SLICE_CYCLES UINT64_C(250000)
#define OPENSPM_NATIVE_GAME_ID "R8PE01"

typedef struct native_backend_context {
    const openspm_host_api *host;
    uint8_t *mem1;
    uint8_t *mem2;
    uint64_t accounted_cycles;
    volatile LONG stop_requested;
    volatile LONG running;
} native_backend_context;

static native_backend_context g_backend;

static void backend_log(openspm_log_level level, const char *message) {
    if (g_backend.host != NULL && g_backend.host->log != NULL) {
        g_backend.host->log(g_backend.host->context, level, message);
    }
}

static void backend_log_exit(const char *prefix, const openspm_native_exit *exit_info) {
    char message[256];
    if (exit_info == NULL) return;
    _snprintf_s(message, sizeof(message), _TRUNCATE,
                "%s reason=%u detail=%u source=0x%08llX cycles=%llu",
                prefix,
                (unsigned)exit_info->reason,
                (unsigned)exit_info->detail,
                (unsigned long long)exit_info->source_address,
                (unsigned long long)exit_info->guest_cycles);
    backend_log(OPENSPM_LOG_INFO, message);
}

static wchar_t *wide_from_utf8(const char *value) {
    int count;
    wchar_t *wide;
    if (value == NULL || *value == '\0') return NULL;
    count = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, value, -1, NULL, 0);
    if (count <= 0) return NULL;
    wide = (wchar_t *)calloc((size_t)count, sizeof(*wide));
    if (wide == NULL) return NULL;
    if (MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, value, -1, wide, count) <= 0) {
        free(wide);
        return NULL;
    }
    return wide;
}

static char *join_utf8_path(const char *left, const char *right) {
    size_t a, b;
    int slash;
    char *path;
    if (left == NULL || right == NULL) return NULL;
    a = strlen(left);
    b = strlen(right);
    slash = a != 0u && left[a - 1u] != '/' && left[a - 1u] != '\\';
    if (a > SIZE_MAX - b - (size_t)slash - 1u) return NULL;
    path = (char *)malloc(a + b + (size_t)slash + 1u);
    if (path == NULL) return NULL;
    memcpy(path, left, a);
    if (slash) path[a++] = '/';
    memcpy(path + a, right, b);
    path[a + b] = '\0';
    return path;
}

static int file_exists_utf8(const char *path_utf8) {
    wchar_t *path = wide_from_utf8(path_utf8);
    DWORD attributes;
    if (path == NULL) return 0;
    attributes = GetFileAttributesW(path);
    free(path);
    return attributes != INVALID_FILE_ATTRIBUTES &&
           (attributes & FILE_ATTRIBUTE_DIRECTORY) == 0;
}

static int read_entire_file_utf8(const char *path_utf8, uint8_t **out_bytes, size_t *out_size) {
    wchar_t *path = NULL;
    HANDLE file = INVALID_HANDLE_VALUE;
    LARGE_INTEGER length;
    uint8_t *bytes = NULL;
    size_t size;
    size_t offset = 0;
    int result = OPENSPM_ERROR_LOAD_FAILED;

    if (path_utf8 == NULL || out_bytes == NULL || out_size == NULL) {
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    }
    *out_bytes = NULL;
    *out_size = 0;

    path = wide_from_utf8(path_utf8);
    if (path == NULL) goto cleanup;
    file = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING,
                       FILE_ATTRIBUTE_NORMAL | FILE_FLAG_SEQUENTIAL_SCAN, NULL);
    if (file == INVALID_HANDLE_VALUE) goto cleanup;
    if (!GetFileSizeEx(file, &length) || length.QuadPart <= 0 ||
        (uint64_t)length.QuadPart > (uint64_t)SIZE_MAX) goto cleanup;

    size = (size_t)length.QuadPart;
    bytes = (uint8_t *)malloc(size);
    if (bytes == NULL) {
        result = OPENSPM_ERROR_RUNTIME;
        goto cleanup;
    }
    while (offset < size) {
        DWORD wanted = (DWORD)((size - offset) > (size_t)(16u * 1024u * 1024u)
                                  ? (16u * 1024u * 1024u)
                                  : (size - offset));
        DWORD read = 0;
        if (!ReadFile(file, bytes + offset, wanted, &read, NULL) || read == 0u) goto cleanup;
        offset += (size_t)read;
    }

    *out_bytes = bytes;
    *out_size = size;
    bytes = NULL;
    result = OPENSPM_OK;

cleanup:
    free(bytes);
    if (file != INVALID_HANDLE_VALUE) CloseHandle(file);
    free(path);
    return result;
}

static int OPENSPM_CALL runtime_account_cycles(void *context, uint64_t guest_cycles) {
    native_backend_context *backend = (native_backend_context *)context;
    if (backend == NULL) return OPENSPM_ERROR_INVALID_ARGUMENT;
    backend->accounted_cycles += guest_cycles;
    return OPENSPM_OK;
}

static int OPENSPM_CALL runtime_poll_control(void *context, uint64_t *out_flags) {
    native_backend_context *backend = (native_backend_context *)context;
    if (backend == NULL || out_flags == NULL) return OPENSPM_ERROR_INVALID_ARGUMENT;
    *out_flags = InterlockedCompareExchange(&backend->stop_requested, 0, 0)
                     ? OPENSPM_NATIVE_CONTROL_STOP
                     : OPENSPM_NATIVE_CONTROL_NONE;
    return OPENSPM_OK;
}

/* Cache instructions are synchronization hints for the local generated-code
   bridge. The standalone module is immutable during this M14 bring-up, so data
   cache writeback/invalidate operations need no host action yet. ICBI becomes
   a real code-invalidation boundary once runtime patching is enabled. */
static int OPENSPM_CALL native_cache_control(void *context,
                                             uint32_t operation,
                                             uint32_t address,
                                             uint32_t source_address) {
    (void)context;
    (void)address;
    (void)source_address;
    if (operation > OPENSPM_WII_CACHE_ICBI) return OPENSPM_ERROR_UNSUPPORTED;
    return OPENSPM_OK;
}

static int OPENSPM_CALL native_query_service(void *backend_context,
                                              const char *service_id,
                                              uint32_t requested_version,
                                              void *out_service) {
    openspm_wii_cpu_service *service = (openspm_wii_cpu_service *)out_service;
    (void)backend_context;

    if (service_id == NULL || out_service == NULL ||
        strcmp(service_id, OPENSPM_WII_CPU_SERVICE_ID) != 0 ||
        requested_version != OPENSPM_WII_CPU_SERVICE_VERSION ||
        service->header.struct_size < sizeof(*service)) {
        return OPENSPM_ERROR_NOT_FOUND;
    }

    memset(service, 0, sizeof(*service));
    service->header.struct_size = sizeof(*service);
    service->header.abi_version = OPENSPM_ABI_VERSION;
    service->capabilities = OPENSPM_WII_CPU_CAP_CACHE_CONTROL;
    service->context = &g_backend;
    service->cache_control = native_cache_control;
    return OPENSPM_OK;
}

static int OPENSPM_CALL native_backend_run(void *backend_context,
                                            const openspm_launch_desc *launch) {
    native_backend_context *backend = (native_backend_context *)backend_context;
    char *dol_path = NULL;
    char *user_native_dir = NULL;
    char *module_path = NULL;
    char *runtime_module_path = NULL;
    uint8_t *dol_bytes = NULL;
    size_t dol_size = 0;
    uint8_t source_sha256[32];
    openspm_dol_info dol_info;
    openspm_native_library library;
    openspm_native_runtime_api runtime;
    openspm_native_session session;
    openspm_native_exit exit_info;
    int result;

    memset(&library, 0, sizeof(library));
    memset(&runtime, 0, sizeof(runtime));
    memset(&session, 0, sizeof(session));
    memset(&exit_info, 0, sizeof(exit_info));

    if (backend == NULL || openspm_validate_launch_desc(launch) != OPENSPM_OK) {
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    }

    backend->accounted_cycles = 0;
    InterlockedExchange(&backend->stop_requested, 0);
    InterlockedExchange(&backend->running, 1);

    dol_path = join_utf8_path(launch->game_root_utf8, "sys/main.dol");
    if (dol_path == NULL) {
        result = OPENSPM_ERROR_RUNTIME;
        goto cleanup;
    }
    result = read_entire_file_utf8(dol_path, &dol_bytes, &dol_size);
    if (result != OPENSPM_OK) {
        backend_log(OPENSPM_LOG_ERROR, "Standalone native backend could not read sys/main.dol.");
        goto cleanup;
    }
    result = openspm_dol_parse(dol_bytes, dol_size, &dol_info);
    if (result != OPENSPM_OK) {
        backend_log(OPENSPM_LOG_ERROR, "Standalone native backend rejected the DOL image.");
        goto cleanup;
    }

    backend->mem1 = (uint8_t *)calloc(1, OPENSPM_WII_MEM1_SIZE);
    backend->mem2 = (uint8_t *)calloc(1, OPENSPM_WII_MEM2_SIZE);
    if (backend->mem1 == NULL || backend->mem2 == NULL) {
        result = OPENSPM_ERROR_RUNTIME;
        goto cleanup;
    }
    result = openspm_dol_load_memory(dol_bytes, dol_size, &dol_info,
                                     backend->mem1, OPENSPM_WII_MEM1_SIZE,
                                     backend->mem2, OPENSPM_WII_MEM2_SIZE);
    if (result != OPENSPM_OK) {
        backend_log(OPENSPM_LOG_ERROR, "Standalone native backend could not map the DOL into MEM1/MEM2.");
        goto cleanup;
    }
    openspm_sha256_bytes(dol_bytes, dol_size, source_sha256);

    /* Player-local generated modules live under writable OpenSPM state. A
       runtime/native location remains as a developer fallback during M14. */
    user_native_dir = join_utf8_path(launch->user_root_utf8, "native");
    module_path = user_native_dir ? join_utf8_path(user_native_dir, "openspm_local_native.dll") : NULL;
    runtime_module_path = join_utf8_path(launch->runtime_root_utf8,
                                         "native/openspm_local_native.dll");
    if (module_path == NULL || !file_exists_utf8(module_path)) {
        free(module_path);
        module_path = runtime_module_path;
        runtime_module_path = NULL;
    }
    if (module_path == NULL || !file_exists_utf8(module_path)) {
        backend_log(OPENSPM_LOG_ERROR,
                    "No player-local native module found (expected User-OpenSPM/native/openspm_local_native.dll)." );
        result = OPENSPM_ERROR_NOT_FOUND;
        goto cleanup;
    }

    result = openspm_native_library_open(module_path,
                                         OPENSPM_NATIVE_GAME_ID,
                                         source_sha256,
                                         &library);
    if (result != OPENSPM_OK) {
        if (result == OPENSPM_ERROR_SOURCE_MISMATCH)
            backend_log(OPENSPM_LOG_ERROR, "Native module does not match this player-supplied main.dol.");
        else
            backend_log(OPENSPM_LOG_ERROR, "Could not load the player-local native module.");
        goto cleanup;
    }

    runtime.header.struct_size = sizeof(runtime);
    runtime.header.abi_version = OPENSPM_NATIVE_ABI_VERSION;
    runtime.capabilities = OPENSPM_NATIVE_HOST_CAP_DIRECT_MEMORY |
                           OPENSPM_NATIVE_HOST_CAP_SERVICE_QUERY |
                           OPENSPM_NATIVE_HOST_CAP_CYCLE_ACCOUNTING |
                           OPENSPM_NATIVE_HOST_CAP_CONTROL_POLL;
    runtime.context = backend;
    runtime.host = backend->host;
    runtime.mem1 = backend->mem1;
    runtime.mem1_size = OPENSPM_WII_MEM1_SIZE;
    runtime.mem2 = backend->mem2;
    runtime.mem2_size = OPENSPM_WII_MEM2_SIZE;
    runtime.account_cycles = runtime_account_cycles;
    runtime.poll_control = runtime_poll_control;

    result = openspm_native_session_open(&library.module, &runtime, launch, &session);
    if (result != OPENSPM_OK) {
        backend_log(OPENSPM_LOG_ERROR, "Player-local native module create() failed.");
        goto cleanup;
    }

    {
        char message[192];
        _snprintf_s(message, sizeof(message), _TRUNCATE,
                    "M14 standalone native execution starting at DOL entry 0x%08X.",
                    (unsigned)dol_info.entry_point);
        backend_log(OPENSPM_LOG_INFO, message);
    }

    for (;;) {
        result = openspm_native_session_run(&session, OPENSPM_NATIVE_SLICE_CYCLES, &exit_info);
        if (result != OPENSPM_OK) {
            backend_log(OPENSPM_LOG_ERROR, "Native session run failed.");
            goto cleanup;
        }
        if (exit_info.reason == OPENSPM_NATIVE_EXIT_SAFEPOINT) {
            if (exit_info.detail != 0u) {
                backend_log_exit("Native guest exception boundary", &exit_info);
                result = OPENSPM_ERROR_UNSUPPORTED;
                goto cleanup;
            }
            continue;
        }
        if (exit_info.reason == OPENSPM_NATIVE_EXIT_STOPPED) {
            backend_log_exit("Native session stopped", &exit_info);
            result = OPENSPM_OK;
            goto cleanup;
        }
        if (exit_info.reason == OPENSPM_NATIVE_EXIT_FALLBACK_REQUIRED) {
            backend_log_exit("Native migration boundary reached", &exit_info);
            backend_log(OPENSPM_LOG_WARN,
                        "Standalone service coverage is incomplete at this boundary; no Dolphin fallback was invoked.");
            result = OPENSPM_ERROR_UNSUPPORTED;
            goto cleanup;
        }
        backend_log_exit("Native fault", &exit_info);
        result = OPENSPM_ERROR_RUNTIME;
        goto cleanup;
    }

cleanup:
    openspm_native_session_close(&session);
    openspm_native_library_close(&library);
    free(backend->mem1);
    free(backend->mem2);
    backend->mem1 = NULL;
    backend->mem2 = NULL;
    free(dol_bytes);
    free(dol_path);
    free(user_native_dir);
    free(module_path);
    free(runtime_module_path);
    InterlockedExchange(&backend->running, 0);
    return result;
}

static void OPENSPM_CALL native_backend_shutdown(void *backend_context) {
    native_backend_context *backend = (native_backend_context *)backend_context;
    if (backend != NULL) {
        InterlockedExchange(&backend->stop_requested, 1);
    }
}

OPENSPM_EXPORT int OPENSPM_CALL openspm_backend_query(
    uint32_t requested_abi,
    const openspm_host_api *host,
    openspm_backend_api *out_api) {
    if (requested_abi != OPENSPM_ABI_VERSION || host == NULL || out_api == NULL ||
        openspm_validate_host_api(host) != OPENSPM_OK ||
        out_api->header.struct_size < sizeof(*out_api)) {
        return OPENSPM_ERROR_ABI_MISMATCH;
    }

    memset(&g_backend, 0, sizeof(g_backend));
    g_backend.host = host;

    memset(out_api, 0, sizeof(*out_api));
    out_api->header.struct_size = sizeof(*out_api);
    out_api->header.abi_version = OPENSPM_ABI_VERSION;
    out_api->backend_id = "openspm-standalone-native";
    out_api->backend_version = "14.0-dev1";
    /* Runtime-service/present controls are intentionally not advertised yet.
       The backend currently exposes only the internal Wii CPU boundary service. */
    out_api->capabilities = OPENSPM_BACKEND_CAP_IN_PROCESS;
    out_api->backend_context = &g_backend;
    out_api->run = native_backend_run;
    out_api->shutdown = native_backend_shutdown;
    out_api->query_service = native_query_service;
    return OPENSPM_OK;
}
