#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include "openspm/backend_api.h"
#include "openspm/code_patches.h"
#include "openspm/disc_boot.h"
#include "openspm/dol_image.h"
#include "openspm/native_loader.h"
#include "openspm/native_runtime.h"
#include "openspm/sha256.h"
#include "openspm/validate.h"
#include "openspm/wii_cpu_service.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* Keep the runtime-proven bring-up slice budget. The 165002 A/B raised this to
 * 2,000,000 guest cycles and produced no subjective improvement; real
 * spmarioMain still collapsed to ~30 Hz in the heavy gameplay window. Retain
 * the slice telemetry below, but do not carry a behavior-changing budget
 * experiment into the next phase-localization run. */
#define OPENSPM_NATIVE_SLICE_CYCLES UINT64_C(250000)
#define OPENSPM_NATIVE_GAME_ID "R8PE01"

typedef struct native_backend_context {
    const openspm_host_api *host;
    uint8_t *mem1;
    uint8_t *mem2;
    uint64_t accounted_cycles;
    uint64_t slice_probe_calls;
    uint64_t slice_sample_calls;
    uint64_t slice_sample_cycles;
    uint64_t slice_sample_safepoints;
    uint64_t slice_sample_ns;
    volatile LONG stop_requested;
    volatile LONG running;
} native_backend_context;

typedef struct native_file {
    uint8_t *bytes;
    size_t size;
} native_file;

/* Defined in openspm_native_backend_v3.c (one translation unit). */
static void openspm_mod_files_boot(uint8_t *mem1, uint8_t *mem2);

static native_backend_context g_backend;
/* Later layers may hand the native module a different host API than the
   launcher's (v9: a guest clock that stands still while the OpenSPM menu
   pauses the game). NULL = the launcher's host, unchanged. */
static const openspm_host_api *(*g_native_runtime_host_filter)(const openspm_host_api *host);

static void backend_log(openspm_log_level level, const char *message) {
    if (g_backend.host != NULL && g_backend.host->log != NULL) {
        g_backend.host->log(g_backend.host->context, level, message);
    }
}

static void backend_log_exit(const char *prefix, const openspm_native_exit *exit_info) {
    char message[256];
    if (exit_info == NULL) return;
    _snprintf_s(message, sizeof(message), _TRUNCATE,
                "%s reason=%u detail=0x%08X source=0x%08llX cycles=%llu",
                prefix,
                (unsigned)exit_info->reason,
                (unsigned)exit_info->detail,
                (unsigned long long)exit_info->source_address,
                (unsigned long long)exit_info->guest_cycles);
    backend_log(OPENSPM_LOG_INFO, message);
}

static void backend_sample_native_slices(native_backend_context *backend,
                                         const openspm_native_exit *exit_info) {
    uint64_t now;
    uint64_t elapsed;
    char message[320];

    if (backend == NULL || exit_info == NULL) return;
    ++backend->slice_probe_calls;
    ++backend->slice_sample_calls;
    backend->slice_sample_cycles += exit_info->guest_cycles;
    if (exit_info->reason == OPENSPM_NATIVE_EXIT_SAFEPOINT)
        ++backend->slice_sample_safepoints;

    /* Avoid adding a host clock query to every native slice. */
    if ((backend->slice_probe_calls & UINT64_C(127)) != 0u ||
        backend->host == NULL || backend->host->monotonic_ns == NULL ||
        backend->host->log == NULL) {
        return;
    }

    now = backend->host->monotonic_ns(backend->host->context);
    if (now == 0u) return;
    if (backend->slice_sample_ns == 0u) {
        backend->slice_sample_ns = now;
        backend->slice_sample_calls = 0u;
        backend->slice_sample_cycles = 0u;
        backend->slice_sample_safepoints = 0u;
        return;
    }
    elapsed = now - backend->slice_sample_ns;
    if (elapsed < UINT64_C(1000000000)) return;

    _snprintf_s(message, sizeof(message), _TRUNCATE,
                "M14 native slice cadence host_ms=%llu calls=%llu guest_cycles=%llu avg_cycles=%llu safepoints=%llu budget=%llu.",
                (unsigned long long)(elapsed / UINT64_C(1000000)),
                (unsigned long long)backend->slice_sample_calls,
                (unsigned long long)backend->slice_sample_cycles,
                (unsigned long long)(backend->slice_sample_calls != 0u
                    ? backend->slice_sample_cycles / backend->slice_sample_calls : 0u),
                (unsigned long long)backend->slice_sample_safepoints,
                (unsigned long long)OPENSPM_NATIVE_SLICE_CYCLES);
    backend->host->log(backend->host->context, OPENSPM_LOG_INFO, message);
    backend->slice_sample_ns = now;
    backend->slice_sample_calls = 0u;
    backend->slice_sample_cycles = 0u;
    backend->slice_sample_safepoints = 0u;
}

static wchar_t *wide_from_utf8(const char *value) {
    int count;
    wchar_t *wide;
    if (value == NULL || *value == '\0') return NULL;
    count = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, value, -1, NULL, 0);
    if (count <= 0) return NULL;
    wide = (wchar_t *)calloc((size_t)count, sizeof(*wide));
    if (wide == NULL) return NULL;
    if (MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS,
                            value, -1, wide, count) <= 0) {
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

static void native_file_free(native_file *file) {
    if (file == NULL) return;
    free(file->bytes);
    file->bytes = NULL;
    file->size = 0;
}

static int read_entire_file_utf8(const char *path_utf8, native_file *out_file) {
    wchar_t *path = NULL;
    HANDLE file = INVALID_HANDLE_VALUE;
    LARGE_INTEGER length;
    uint8_t *bytes = NULL;
    size_t size;
    size_t offset = 0;
    int result = OPENSPM_ERROR_LOAD_FAILED;

    if (path_utf8 == NULL || out_file == NULL)
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    memset(out_file, 0, sizeof(*out_file));
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
        if (!ReadFile(file, bytes + offset, wanted, &read, NULL) || read == 0u)
            goto cleanup;
        offset += (size_t)read;
    }

    out_file->bytes = bytes;
    out_file->size = size;
    bytes = NULL;
    result = OPENSPM_OK;

cleanup:
    free(bytes);
    if (file != INVALID_HANDLE_VALUE) CloseHandle(file);
    free(path);
    return result;
}

static int load_sys_file(const openspm_launch_desc *launch,
                         const char *name,
                         native_file *out_file) {
    char relative[128];
    char *path;
    int result;
    _snprintf_s(relative, sizeof(relative), _TRUNCATE, "sys/%s", name);
    path = join_utf8_path(launch->game_root_utf8, relative);
    if (path == NULL) return OPENSPM_ERROR_RUNTIME;
    result = read_entire_file_utf8(path, out_file);
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
    if (backend == NULL || out_flags == NULL)
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    *out_flags = InterlockedCompareExchange(&backend->stop_requested, 0, 0)
                     ? OPENSPM_NATIVE_CONTROL_STOP
                     : OPENSPM_NATIVE_CONTROL_NONE;
    return OPENSPM_OK;
}

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
        strcmp(service_id, OPENSPM_WII_CPU_SERVICE_ID) != 0) {
        return OPENSPM_ERROR_NOT_FOUND;
    }
    if (requested_version != OPENSPM_WII_CPU_SERVICE_VERSION ||
        service->header.struct_size < sizeof(*service)) {
        return OPENSPM_ERROR_ABI_MISMATCH;
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
    native_file dol = {0};
    native_file boot = {0};
    native_file bi2 = {0};
    native_file fst = {0};
    char *user_native_dir = NULL;
    char *module_path = NULL;
    char *runtime_module_path = NULL;
    uint8_t source_sha256[32];
    openspm_dol_info dol_info;
    openspm_disc_boot_info disc_info;
    openspm_native_library library;
    openspm_native_runtime_api runtime;
    openspm_native_session session;
    openspm_native_exit exit_info;
    int result = OPENSPM_ERROR_RUNTIME;

    memset(&dol_info, 0, sizeof(dol_info));
    memset(&disc_info, 0, sizeof(disc_info));
    memset(&library, 0, sizeof(library));
    memset(&runtime, 0, sizeof(runtime));
    memset(&session, 0, sizeof(session));
    memset(&exit_info, 0, sizeof(exit_info));

    if (backend == NULL || openspm_validate_launch_desc(launch) != OPENSPM_OK)
        return OPENSPM_ERROR_INVALID_ARGUMENT;

    backend->accounted_cycles = 0u;
    backend->slice_probe_calls = 0u;
    backend->slice_sample_calls = 0u;
    backend->slice_sample_cycles = 0u;
    backend->slice_sample_safepoints = 0u;
    backend->slice_sample_ns = 0u;
    InterlockedExchange(&backend->stop_requested, 0);
    InterlockedExchange(&backend->running, 1);

    result = load_sys_file(launch, "main.dol", &dol);
    if (result != OPENSPM_OK) {
        backend_log(OPENSPM_LOG_ERROR,
                    "Standalone native backend could not read sys/main.dol.");
        goto cleanup;
    }
    result = load_sys_file(launch, "boot.bin", &boot);
    if (result != OPENSPM_OK) {
        backend_log(OPENSPM_LOG_ERROR,
                    "Standalone native backend needs the extracted sys/boot.bin.");
        goto cleanup;
    }
    result = load_sys_file(launch, "bi2.bin", &bi2);
    if (result != OPENSPM_OK) {
        backend_log(OPENSPM_LOG_ERROR,
                    "Standalone native backend needs the extracted sys/bi2.bin.");
        goto cleanup;
    }
    result = load_sys_file(launch, "fst.bin", &fst);
    if (result != OPENSPM_OK) {
        backend_log(OPENSPM_LOG_ERROR,
                    "Standalone native backend needs the extracted sys/fst.bin.");
        goto cleanup;
    }
    if (boot.size < 6u || memcmp(boot.bytes, OPENSPM_NATIVE_GAME_ID, 6u) != 0) {
        backend_log(OPENSPM_LOG_ERROR,
                    "The extracted disc is not the expected R8PE01 source game.");
        result = OPENSPM_ERROR_SOURCE_MISMATCH;
        goto cleanup;
    }

    result = openspm_dol_parse(dol.bytes, dol.size, &dol_info);
    if (result != OPENSPM_OK) {
        backend_log(OPENSPM_LOG_ERROR,
                    "Standalone native backend rejected the DOL image.");
        goto cleanup;
    }

    backend->mem1 = (uint8_t *)calloc(1, OPENSPM_WII_MEM1_SIZE);
    backend->mem2 = (uint8_t *)calloc(1, OPENSPM_WII_MEM2_SIZE);
    if (backend->mem1 == NULL || backend->mem2 == NULL) {
        result = OPENSPM_ERROR_RUNTIME;
        goto cleanup;
    }
    result = openspm_dol_load_memory(dol.bytes, dol.size, &dol_info,
                                     backend->mem1, OPENSPM_WII_MEM1_SIZE,
                                     backend->mem2, OPENSPM_WII_MEM2_SIZE);
    if (result != OPENSPM_OK) {
        backend_log(OPENSPM_LOG_ERROR,
                    "Standalone native backend could not map the DOL into MEM1/MEM2.");
        goto cleanup;
    }
    {
        /* OpenSPM content patches (include/openspm/code_patches.h): the same
           checked recipes the build applied to the recompiled module. */
        char message[160];
        const int patched = openspm_code_patches_apply(backend->mem1, OPENSPM_WII_MEM1_SIZE, backend->mem2,
                                                       OPENSPM_WII_MEM2_SIZE);
        _snprintf_s(message, sizeof(message), _TRUNCATE,
                    patched < 0 ? "OpenSPM content patches: this main.dol is not R8PE01 rev 2; none applied."
                                : "OpenSPM content patches: %d applied to the boot image; item table relocated to MEM2.",
                    patched);
        backend_log(patched < 0 ? OPENSPM_LOG_WARN : OPENSPM_LOG_INFO, message);
    }
    result = openspm_disc_bootstrap(boot.bytes, boot.size,
                                    bi2.bytes, bi2.size,
                                    fst.bytes, fst.size,
                                    backend->mem1, OPENSPM_WII_MEM1_SIZE,
                                    &disc_info);
    if (result != OPENSPM_OK) {
        backend_log(OPENSPM_LOG_ERROR,
                    "Standalone native backend rejected the extracted disc bootstrap files.");
        goto cleanup;
    }
    openspm_sha256_bytes(dol.bytes, dol.size, source_sha256);

    {
        char message[256];
        _snprintf_s(message, sizeof(message), _TRUNCATE,
                    "M14 standalone memory ready: entry=0x%08X FST=0x%08X/%u BI2=0x%08X ArenaHi=0x%08X.",
                    (unsigned)dol_info.entry_point,
                    (unsigned)disc_info.fst_address,
                    (unsigned)disc_info.fst_size,
                    (unsigned)disc_info.bi2_address,
                    (unsigned)disc_info.arena_high);
        backend_log(OPENSPM_LOG_INFO, message);
    }
    /* Mods' new files join the game's file table (openspm_native_backend_v3.c). */
    openspm_mod_files_boot(backend->mem1, backend->mem2);

    user_native_dir = join_utf8_path(launch->user_root_utf8, "native");
    module_path = user_native_dir
                      ? join_utf8_path(user_native_dir,
                                       "openspm_local_native.dll")
                      : NULL;
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
            backend_log(OPENSPM_LOG_ERROR,
                        "Native module does not match this player-supplied main.dol.");
        else
            backend_log(OPENSPM_LOG_ERROR,
                        "Could not load the player-local native module.");
        goto cleanup;
    }

    runtime.header.struct_size = sizeof(runtime);
    runtime.header.abi_version = OPENSPM_NATIVE_ABI_VERSION;
    runtime.capabilities = OPENSPM_NATIVE_HOST_CAP_DIRECT_MEMORY |
                           OPENSPM_NATIVE_HOST_CAP_SERVICE_QUERY |
                           OPENSPM_NATIVE_HOST_CAP_CYCLE_ACCOUNTING |
                           OPENSPM_NATIVE_HOST_CAP_CONTROL_POLL;
    runtime.context = backend;
    runtime.host = g_native_runtime_host_filter != NULL
                       ? g_native_runtime_host_filter(backend->host)
                       : backend->host;
    runtime.mem1 = backend->mem1;
    runtime.mem1_size = OPENSPM_WII_MEM1_SIZE;
    runtime.mem2 = backend->mem2;
    runtime.mem2_size = OPENSPM_WII_MEM2_SIZE;
    runtime.account_cycles = runtime_account_cycles;
    runtime.poll_control = runtime_poll_control;

    result = openspm_native_session_open(&library.module, &runtime, launch, &session);
    if (result != OPENSPM_OK) {
        backend_log(OPENSPM_LOG_ERROR,
                    "Player-local native module create() failed.");
        goto cleanup;
    }

    {
        char message[224];
        _snprintf_s(message, sizeof(message), _TRUNCATE,
                    "M14 standalone native CPU starting at DOL entry 0x%08X; slice_budget=%llu (runtime-proven baseline).",
                    (unsigned)dol_info.entry_point,
                    (unsigned long long)OPENSPM_NATIVE_SLICE_CYCLES);
        backend_log(OPENSPM_LOG_INFO, message);
    }

    for (;;) {
        result = openspm_native_session_run(&session,
                                            OPENSPM_NATIVE_SLICE_CYCLES,
                                            &exit_info);
        if (result != OPENSPM_OK) {
            backend_log(OPENSPM_LOG_ERROR, "Native session run failed.");
            goto cleanup;
        }
        backend_sample_native_slices(backend, &exit_info);
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
                        "Standalone Wii-service coverage is incomplete at this boundary; no Dolphin CPU fallback was invoked.");
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
    native_file_free(&dol);
    native_file_free(&boot);
    native_file_free(&bi2);
    native_file_free(&fst);
    free(user_native_dir);
    free(module_path);
    free(runtime_module_path);
    InterlockedExchange(&backend->running, 0);
    return result;
}

static void OPENSPM_CALL native_backend_shutdown(void *backend_context) {
    native_backend_context *backend = (native_backend_context *)backend_context;
    if (backend != NULL) InterlockedExchange(&backend->stop_requested, 1);
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
    out_api->backend_version = "14.0-dev2";
    out_api->capabilities = OPENSPM_BACKEND_CAP_IN_PROCESS;
    out_api->backend_context = &g_backend;
    out_api->run = native_backend_run;
    out_api->shutdown = native_backend_shutdown;
    out_api->query_service = native_query_service;
    return OPENSPM_OK;
}
