#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <commdlg.h>
#include <shellapi.h>
#include <shlobj.h>

#include "renderer_loader.h"
#include "native_settings.h"
#include "native_game.h"
#include "mod_loader.h"
#include "sample_profiler.h"
#include "config.h"
#include "openspm/backend_api.h"
#include "openspm/overlay_service.h"
#include "openspm/validate.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>

typedef struct native_host_context {
    openspm_backend_api *backend;
    openspm_mod_set *mods; /* set only when at least one mod is loaded */
} native_host_context;

static int host_should_quiet_legacy_gx_info(const char *message) {
    unsigned long long count;
    if (message == NULL) return 0;

    /* These were invaluable while file-select/raw-GX bring-up was unstable,
       but the automatic SEQ_LOAD capture is now retired diagnostic debt. The
       backend can still compute its bounded audit internally; do not serialize
       thousands of per-vertex/TEV records to stderr during ordinary gameplay.
       Manual/targeted diagnostics and all warnings/errors remain visible. */
    if (strncmp(message, "M14 GX deep ", 12u) == 0 ||
        strncmp(message, "M14 GX audit draw ", 18u) == 0 ||
        strncmp(message, "M14 GX audit texture ", 21u) == 0 ||
        strncmp(message, "M14 GX audit tev ", 17u) == 0 ||
        strncmp(message, "M14 GX audit tevreg", 18u) == 0) {
        return 1;
    }

    /* Legacy g_v9_seqload_audit_armed stays set after its one-shot capture and
       makes the backend emit every GXEndDisplayList forever. Keep the useful
       first-eight/power-of-two checkpoints without paying continuous console /
       debugger I/O. This is log filtering only; display-list execution, hashing,
       v19 continuation hooks and renderer behavior are untouched. */
    if (sscanf(message, "M14 GX display-list end count=%llu", &count) == 1) {
        if (count > 8u && (count & (count - 1u)) != 0u) return 1;
    }
    return 0;
}

static void OPENSPM_CALL host_log(void *context, openspm_log_level level,
                                  const char *message) {
    const char *name = "info";
    static int quiet_marker_reported;
    (void)context;
    if (level == OPENSPM_LOG_DEBUG) name = "debug";
    else if (level == OPENSPM_LOG_WARN) name = "warn";
    else if (level == OPENSPM_LOG_ERROR) name = "error";

    if (level == OPENSPM_LOG_INFO && host_should_quiet_legacy_gx_info(message)) {
        if (!quiet_marker_reported) {
            fprintf(stderr,
                    "[OpenSPMNative:info] M14 launcher quiet mode active for retired high-volume GX audit logs; targeted diagnostics remain available.\n");
            quiet_marker_reported = 1;
        }
        return;
    }
    fprintf(stderr, "[OpenSPMNative:%s] %s\n", name, message ? message : "");
}

static int host_apply_main_thread_priority(void) {
    const char *value = getenv("OPENSPM_M14_MAIN_PRIORITY");
    HANDLE thread;
    int previous;
    int desired;

    if (value == NULL || *value == '\0' ||
        strcmp(value, "0") == 0 ||
        strcmp(value, "off") == 0 || strcmp(value, "OFF") == 0 ||
        strcmp(value, "normal") == 0 || strcmp(value, "NORMAL") == 0) {
        return THREAD_PRIORITY_ERROR_RETURN;
    }

    desired = THREAD_PRIORITY_ABOVE_NORMAL;
    if (strcmp(value, "highest") == 0 || strcmp(value, "HIGHEST") == 0)
        desired = THREAD_PRIORITY_HIGHEST;

    thread = GetCurrentThread();
    previous = GetThreadPriority(thread);
    if (previous == THREAD_PRIORITY_ERROR_RETURN) {
        fprintf(stderr,
            "[OpenSPMNative:warn] M14 main-thread priority A/B could not read current priority (Win32 %lu).\n",
            GetLastError());
        return THREAD_PRIORITY_ERROR_RETURN;
    }
    if (!SetThreadPriority(thread, desired)) {
        fprintf(stderr,
            "[OpenSPMNative:warn] M14 main-thread priority A/B request failed old=%d requested=%d (Win32 %lu).\n",
            previous, desired, GetLastError());
        return THREAD_PRIORITY_ERROR_RETURN;
    }

    fprintf(stderr,
        "[OpenSPMNative:info] M14 dev123 producer scheduling A/B active: main thread priority old=%d new=%d; Aurora workers were created before this change.\n",
        previous, desired);
    return previous;
}

static void host_restore_main_thread_priority(int previous) {
    if (previous == THREAD_PRIORITY_ERROR_RETURN) return;
    if (!SetThreadPriority(GetCurrentThread(), previous)) {
        fprintf(stderr,
            "[OpenSPMNative:warn] M14 main-thread priority restore failed target=%d (Win32 %lu).\n",
            previous, GetLastError());
    }
}

typedef struct openspm_thread_power_throttling_state {
    ULONG Version;
    ULONG ControlMask;
    ULONG StateMask;
} openspm_thread_power_throttling_state;

typedef BOOL (WINAPI *openspm_set_thread_information_fn)(
    HANDLE thread, int information_class, LPVOID information,
    DWORD information_size);

#define OPENSPM_THREAD_POWER_THROTTLING_CLASS 3
#define OPENSPM_THREAD_POWER_THROTTLING_CURRENT_VERSION 1u
#define OPENSPM_THREAD_POWER_THROTTLING_EXECUTION_SPEED 0x1u

static openspm_set_thread_information_fn host_set_thread_information_proc(void) {
    HMODULE kernel32 = GetModuleHandleW(L"kernel32.dll");
    FARPROC proc;
    if (kernel32 == NULL) return NULL;
    proc = GetProcAddress(kernel32, "SetThreadInformation");
    return proc != NULL
        ? (openspm_set_thread_information_fn)(void *)proc
        : NULL;
}

static int host_apply_main_thread_high_qos(void) {
    const char *value = getenv("OPENSPM_M14_MAIN_QOS");
    openspm_thread_power_throttling_state throttling;
    openspm_set_thread_information_fn set_thread_information;

    if (value == NULL || *value == '\0' ||
        strcmp(value, "0") == 0 ||
        strcmp(value, "off") == 0 || strcmp(value, "OFF") == 0 ||
        strcmp(value, "system") == 0 || strcmp(value, "SYSTEM") == 0) {
        return 0;
    }
    if (strcmp(value, "high") != 0 && strcmp(value, "HIGH") != 0) {
        fprintf(stderr,
            "[OpenSPMNative:warn] Unknown OPENSPM_M14_MAIN_QOS='%s'; expected high or off.\n",
            value);
        return 0;
    }

    set_thread_information = host_set_thread_information_proc();
    if (set_thread_information == NULL) {
        fprintf(stderr,
            "[OpenSPMNative:warn] M14 HighQoS A/B unavailable: SetThreadInformation is missing.\n");
        return 0;
    }

    ZeroMemory(&throttling, sizeof(throttling));
    throttling.Version = OPENSPM_THREAD_POWER_THROTTLING_CURRENT_VERSION;
    throttling.ControlMask = OPENSPM_THREAD_POWER_THROTTLING_EXECUTION_SPEED;
    throttling.StateMask = 0u; /* Explicit HighQoS: execution-speed throttling off. */
    if (!set_thread_information(
            GetCurrentThread(), OPENSPM_THREAD_POWER_THROTTLING_CLASS,
            &throttling, (DWORD)sizeof(throttling))) {
        fprintf(stderr,
            "[OpenSPMNative:warn] M14 HighQoS A/B request failed (Win32 %lu).\n",
            GetLastError());
        return 0;
    }

    fprintf(stderr,
        "[OpenSPMNative:info] M14 dev124 producer HighQoS A/B active: execution-speed throttling disabled on main thread after Aurora worker creation.\n");
    return 1;
}

static void host_restore_main_thread_qos(int applied) {
    openspm_thread_power_throttling_state throttling;
    openspm_set_thread_information_fn set_thread_information;
    if (!applied) return;

    set_thread_information = host_set_thread_information_proc();
    if (set_thread_information == NULL) return;

    ZeroMemory(&throttling, sizeof(throttling));
    throttling.Version = OPENSPM_THREAD_POWER_THROTTLING_CURRENT_VERSION;
    throttling.ControlMask = 0u;
    throttling.StateMask = 0u;
    if (!set_thread_information(
            GetCurrentThread(), OPENSPM_THREAD_POWER_THROTTLING_CLASS,
            &throttling, (DWORD)sizeof(throttling))) {
        fprintf(stderr,
            "[OpenSPMNative:warn] M14 main-thread QoS restore-to-system failed (Win32 %lu).\n",
            GetLastError());
    }
}

typedef struct openspm_cpu_set_record_prefix {
    DWORD Size;
    DWORD Type;
    DWORD Id;
    WORD Group;
    BYTE LogicalProcessorIndex;
    BYTE CoreIndex;
    BYTE LastLevelCacheIndex;
    BYTE NumaNodeIndex;
    BYTE EfficiencyClass;
    BYTE AllFlags;
    DWORD Reserved;
    ULONGLONG AllocationTag;
} openspm_cpu_set_record_prefix;

typedef BOOL (WINAPI *openspm_get_system_cpu_set_information_fn)(
    void *information, ULONG buffer_length, PULONG returned_length,
    HANDLE process, ULONG flags);
typedef BOOL (WINAPI *openspm_set_thread_selected_cpu_sets_fn)(
    HANDLE thread, const ULONG *cpu_set_ids, ULONG cpu_set_id_count);
typedef BOOL (WINAPI *openspm_get_thread_selected_cpu_sets_fn)(
    HANDLE thread, PULONG cpu_set_ids, ULONG cpu_set_id_count,
    PULONG required_id_count);

typedef struct openspm_cpu_set_restore {
    ULONG *previous_ids;
    ULONG previous_count;
    int applied;
} openspm_cpu_set_restore;

#define OPENSPM_CPU_SET_TYPE_CPU_SET 0u
#define OPENSPM_CPU_SET_FLAG_PARKED 0x01u
#define OPENSPM_CPU_SET_FLAG_ALLOCATED 0x02u
#define OPENSPM_CPU_SET_FLAG_ALLOCATED_TO_TARGET 0x04u

static FARPROC host_kernel32_proc(const char *name) {
    HMODULE kernel32 = GetModuleHandleW(L"kernel32.dll");
    if (kernel32 == NULL || name == NULL) return NULL;
    return GetProcAddress(kernel32, name);
}

static int host_cpu_set_fast_requested(void) {
    const char *value = getenv("OPENSPM_M14_MAIN_CPUSET");
    return value != NULL &&
           (strcmp(value, "fast") == 0 || strcmp(value, "FAST") == 0);
}

static int host_capture_thread_cpu_sets(
    openspm_get_thread_selected_cpu_sets_fn get_thread_sets,
    openspm_cpu_set_restore *restore) {
    ULONG required = 0u;
    DWORD error = ERROR_SUCCESS;

    if (get_thread_sets == NULL || restore == NULL) return 0;
    ZeroMemory(restore, sizeof(*restore));

    if (!get_thread_sets(GetCurrentThread(), NULL, 0u, &required)) {
        error = GetLastError();
        if (error != ERROR_INSUFFICIENT_BUFFER) {
            fprintf(stderr,
                "[OpenSPMNative:warn] M14 fast-core CPU Set A/B could not query prior thread CPU Sets (Win32 %lu).\n",
                error);
            return 0;
        }
    }
    if (required == 0u) return 1;

    restore->previous_ids = (ULONG *)calloc(required, sizeof(ULONG));
    if (restore->previous_ids == NULL) return 0;
    if (!get_thread_sets(
            GetCurrentThread(), restore->previous_ids, required, &required)) {
        fprintf(stderr,
            "[OpenSPMNative:warn] M14 fast-core CPU Set A/B could not read prior thread CPU Sets (Win32 %lu).\n",
            GetLastError());
        free(restore->previous_ids);
        ZeroMemory(restore, sizeof(*restore));
        return 0;
    }
    restore->previous_count = required;
    return 1;
}

static int host_apply_main_thread_fast_cpuset(openspm_cpu_set_restore *restore) {
    openspm_get_system_cpu_set_information_fn get_system_sets;
    openspm_set_thread_selected_cpu_sets_fn set_thread_sets;
    openspm_get_thread_selected_cpu_sets_fn get_thread_sets;
    BYTE *buffer = NULL;
    ULONG required = 0u;
    ULONG returned = 0u;
    ULONG offset;
    ULONG eligible_count = 0u;
    ULONG selected_count = 0u;
    ULONG *selected_ids = NULL;
    BYTE min_efficiency = 0xffu;
    BYTE max_efficiency = 0u;
    int have_efficiency = 0;

    if (restore == NULL) return 0;
    ZeroMemory(restore, sizeof(*restore));
    if (!host_cpu_set_fast_requested()) return 0;

    get_system_sets = (openspm_get_system_cpu_set_information_fn)(void *)
        host_kernel32_proc("GetSystemCpuSetInformation");
    set_thread_sets = (openspm_set_thread_selected_cpu_sets_fn)(void *)
        host_kernel32_proc("SetThreadSelectedCpuSets");
    get_thread_sets = (openspm_get_thread_selected_cpu_sets_fn)(void *)
        host_kernel32_proc("GetThreadSelectedCpuSets");
    if (get_system_sets == NULL || set_thread_sets == NULL ||
        get_thread_sets == NULL) {
        fprintf(stderr,
            "[OpenSPMNative:warn] M14 dev127 fast-core CPU Set A/B unavailable: required Windows CPU Set APIs are missing.\n");
        return 0;
    }

    if (!host_capture_thread_cpu_sets(get_thread_sets, restore))
        return 0;

    if (!get_system_sets(NULL, 0u, &required, GetCurrentProcess(), 0u) &&
        GetLastError() != ERROR_INSUFFICIENT_BUFFER) {
        fprintf(stderr,
            "[OpenSPMNative:warn] M14 fast-core CPU Set enumeration size query failed (Win32 %lu).\n",
            GetLastError());
        goto fail;
    }
    if (required < sizeof(openspm_cpu_set_record_prefix)) {
        fprintf(stderr,
            "[OpenSPMNative:warn] M14 fast-core CPU Set A/B found no usable CPU Set records.\n");
        goto fail;
    }

    buffer = (BYTE *)malloc(required);
    if (buffer == NULL) goto fail;
    if (!get_system_sets(buffer, required, &returned, GetCurrentProcess(), 0u)) {
        fprintf(stderr,
            "[OpenSPMNative:warn] M14 fast-core CPU Set enumeration failed (Win32 %lu).\n",
            GetLastError());
        goto fail;
    }

    offset = 0u;
    while (offset + sizeof(DWORD) * 2u <= returned) {
        openspm_cpu_set_record_prefix *record =
            (openspm_cpu_set_record_prefix *)(void *)(buffer + offset);
        BYTE flags;
        if (record->Size < sizeof(openspm_cpu_set_record_prefix) ||
            record->Size > returned - offset) {
            fprintf(stderr,
                "[OpenSPMNative:warn] M14 fast-core CPU Set enumeration returned malformed record size=%lu offset=%lu total=%lu.\n",
                (unsigned long)record->Size, (unsigned long)offset,
                (unsigned long)returned);
            goto fail;
        }
        if (record->Type == OPENSPM_CPU_SET_TYPE_CPU_SET) {
            flags = record->AllFlags;
            if ((flags & OPENSPM_CPU_SET_FLAG_PARKED) == 0u &&
                ((flags & OPENSPM_CPU_SET_FLAG_ALLOCATED) == 0u ||
                 (flags & OPENSPM_CPU_SET_FLAG_ALLOCATED_TO_TARGET) != 0u)) {
                if (!have_efficiency || record->EfficiencyClass < min_efficiency)
                    min_efficiency = record->EfficiencyClass;
                if (!have_efficiency || record->EfficiencyClass > max_efficiency)
                    max_efficiency = record->EfficiencyClass;
                have_efficiency = 1;
                ++eligible_count;
            }
        }
        offset += record->Size;
    }

    if (!have_efficiency || eligible_count == 0u) goto fail;
    if (max_efficiency == min_efficiency) {
        fprintf(stderr,
            "[OpenSPMNative:info] M14 dev127 fast-core CPU Set A/B skipped: all %lu eligible CPU Sets report the same EfficiencyClass=%u.\n",
            (unsigned long)eligible_count, (unsigned)max_efficiency);
        goto fail;
    }

    selected_ids = (ULONG *)calloc(eligible_count, sizeof(ULONG));
    if (selected_ids == NULL) goto fail;
    offset = 0u;
    while (offset + sizeof(DWORD) * 2u <= returned) {
        openspm_cpu_set_record_prefix *record =
            (openspm_cpu_set_record_prefix *)(void *)(buffer + offset);
        const BYTE flags = record->AllFlags;
        if (record->Type == OPENSPM_CPU_SET_TYPE_CPU_SET &&
            record->EfficiencyClass == max_efficiency &&
            (flags & OPENSPM_CPU_SET_FLAG_PARKED) == 0u &&
            ((flags & OPENSPM_CPU_SET_FLAG_ALLOCATED) == 0u ||
             (flags & OPENSPM_CPU_SET_FLAG_ALLOCATED_TO_TARGET) != 0u)) {
            selected_ids[selected_count++] = record->Id;
        }
        offset += record->Size;
    }
    if (selected_count == 0u) goto fail;

    if (!set_thread_sets(GetCurrentThread(), selected_ids, selected_count)) {
        fprintf(stderr,
            "[OpenSPMNative:warn] M14 fast-core CPU Set assignment failed (Win32 %lu).\n",
            GetLastError());
        goto fail;
    }

    restore->applied = 1;
    fprintf(stderr,
        "[OpenSPMNative:info] M14 dev127 fast-core CPU Set A/B active: selected=%lu eligible=%lu fastest_efficiency_class=%u slowest_efficiency_class=%u; soft thread CPU Set preference only.\n",
        (unsigned long)selected_count, (unsigned long)eligible_count,
        (unsigned)max_efficiency, (unsigned)min_efficiency);
    free(selected_ids);
    free(buffer);
    return 1;

fail:
    free(selected_ids);
    free(buffer);
    free(restore->previous_ids);
    ZeroMemory(restore, sizeof(*restore));
    return 0;
}

static void host_restore_main_thread_cpuset(openspm_cpu_set_restore *restore) {
    openspm_set_thread_selected_cpu_sets_fn set_thread_sets;
    if (restore == NULL) return;
    if (!restore->applied) {
        free(restore->previous_ids);
        ZeroMemory(restore, sizeof(*restore));
        return;
    }

    set_thread_sets = (openspm_set_thread_selected_cpu_sets_fn)(void *)
        host_kernel32_proc("SetThreadSelectedCpuSets");
    if (set_thread_sets != NULL &&
        !set_thread_sets(GetCurrentThread(),
                         restore->previous_count != 0u
                             ? restore->previous_ids : NULL,
                         restore->previous_count)) {
        fprintf(stderr,
            "[OpenSPMNative:warn] M14 main-thread CPU Set restore failed (Win32 %lu).\n",
            GetLastError());
    }
    free(restore->previous_ids);
    ZeroMemory(restore, sizeof(*restore));
}

static LARGE_INTEGER g_host_qpc_frequency;

static int host_init_monotonic_clock(void) {
    if (!QueryPerformanceFrequency(&g_host_qpc_frequency) ||
        g_host_qpc_frequency.QuadPart <= 0) {
        g_host_qpc_frequency.QuadPart = 0;
        return 0;
    }
    fprintf(stderr,
        "[OpenSPMNative:info] M14 dev125 cached QPC frequency active: frequency=%lld Hz; host timestamp calls query only the counter.\n",
        (long long)g_host_qpc_frequency.QuadPart);
    return 1;
}

static uint64_t OPENSPM_CALL host_monotonic_ns(void *context) {
    LARGE_INTEGER counter;
    uint64_t whole_seconds, remainder;
    const uint64_t frequency = (uint64_t)g_host_qpc_frequency.QuadPart;
    (void)context;
    if (frequency == 0u || !QueryPerformanceCounter(&counter)) {
        return 0;
    }
    whole_seconds = (uint64_t)counter.QuadPart / frequency;
    remainder = (uint64_t)counter.QuadPart % frequency;
    return whole_seconds * UINT64_C(1000000000) +
           (remainder * UINT64_C(1000000000)) / frequency;
}

/* openspm.overlay (include/openspm/overlay_service.h): mod text panels and
   notifications, forwarded to the renderer DLL's openspm_renderer_overlay
   export (same process; resolved on first use). */
static openspm_renderer_overlay_fn host_overlay_export(void) {
    static openspm_renderer_overlay_fn fn;
    static int resolved;
    if (!resolved) {
        HMODULE renderer = GetModuleHandleA("openspm_renderer.dll");
        resolved = renderer != NULL;
        if (renderer != NULL)
            fn = (openspm_renderer_overlay_fn)(void *)GetProcAddress(renderer, OPENSPM_RENDERER_OVERLAY_EXPORT);
    }
    return fn;
}

static int OPENSPM_CALL host_overlay_set_panel(void *context, const char *panel_id, const char *text) {
    openspm_renderer_overlay_fn fn = host_overlay_export();
    (void)context;
    if (panel_id == NULL || panel_id[0] == '\0') return OPENSPM_ERROR_INVALID_ARGUMENT;
    return fn != NULL ? fn(OPENSPM_RENDERER_OVERLAY_SET_PANEL, panel_id, text, 0u) : OPENSPM_ERROR_UNSUPPORTED;
}

static int OPENSPM_CALL host_overlay_notify(void *context, const char *text, uint32_t milliseconds) {
    openspm_renderer_overlay_fn fn = host_overlay_export();
    (void)context;
    if (text == NULL) return OPENSPM_ERROR_INVALID_ARGUMENT;
    return fn != NULL ? fn(OPENSPM_RENDERER_OVERLAY_NOTIFY, NULL, text, milliseconds) : OPENSPM_ERROR_UNSUPPORTED;
}

static int OPENSPM_CALL host_overlay_draw_boxes(void *context, const char *layer_id,
                                                const openspm_overlay_box *boxes, uint32_t count) {
    static openspm_renderer_overlay_boxes_fn fn;
    static int resolved;
    (void)context;
    if (layer_id == NULL || layer_id[0] == '\0' || (count != 0u && boxes == NULL))
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    if (!resolved) {
        HMODULE renderer = GetModuleHandleA("openspm_renderer.dll");
        resolved = renderer != NULL;
        if (renderer != NULL)
            fn = (openspm_renderer_overlay_boxes_fn)(void *)GetProcAddress(renderer,
                                                                           OPENSPM_RENDERER_OVERLAY_BOXES_EXPORT);
    }
    return fn != NULL ? fn(layer_id, boxes, count) : OPENSPM_ERROR_UNSUPPORTED;
}

static int OPENSPM_CALL host_query_service(void *context,
                                           const char *service_id,
                                           uint32_t requested_version,
                                           void *out_service) {
    native_host_context *host_context = (native_host_context *)context;
    if (service_id != NULL && strcmp(service_id, OPENSPM_OVERLAY_SERVICE_ID) == 0) {
        openspm_overlay_service *service = (openspm_overlay_service *)out_service;
        int rc = openspm_overlay_accept(service, requested_version);
        if (rc != OPENSPM_OK) return rc;
        {
            /* Fill no more than the mod's struct holds (version 1: no draw_boxes). */
            openspm_overlay_service full;
            const uint32_t size = service->header.struct_size < sizeof full ? service->header.struct_size
                                                                            : (uint32_t)sizeof full;
            memset(&full, 0, sizeof full);
            full.header.struct_size = size;
            full.header.abi_version = requested_version;
            full.set_panel = host_overlay_set_panel;
            full.notify = host_overlay_notify;
            full.draw_boxes = host_overlay_draw_boxes;
            memcpy(service, &full, size);
        }
        return OPENSPM_OK;
    }
    if (!host_context || !host_context->backend ||
        !host_context->backend->query_service) {
        return OPENSPM_ERROR_UNSUPPORTED;
    }
    return host_context->backend->query_service(
        host_context->backend->backend_context,
        service_id, requested_version, out_service);
}

/* Runtime-event sink, installed as host.runtime_event only when mods are
   loaded (no mods: the backend sees NULL and skips the per-frame work). The
   backend calls it on the guest thread at the frame safepoint. Core running /
   stopped are delivered by this launcher around backend.run, so only the
   per-frame events are forwarded here. */
static void OPENSPM_CALL host_runtime_event(void *context, uint32_t event_id, const void *event_data) {
    native_host_context *host_context = (native_host_context *)context;
    openspm_mod_event mod_event;
    if (!host_context || !host_context->mods || !openspm_mod_event_from_runtime(event_id, &mod_event)) return;
    if (mod_event != OPENSPM_MOD_EVENT_TICK_BEGIN && mod_event != OPENSPM_MOD_EVENT_TICK_END &&
        mod_event != OPENSPM_MOD_EVENT_PRESENT_END)
        return;
    openspm_mods_event(host_context->mods, mod_event, event_data);
}

static wchar_t *application_root(void) {
    DWORD size = 512;
    for (;;) {
        wchar_t *buffer = (wchar_t *)calloc(size, sizeof(*buffer));
        DWORD used;
        wchar_t *slash;
        if (!buffer) return NULL;
        used = GetModuleFileNameW(NULL, buffer, size);
        if (used == 0) { free(buffer); return NULL; }
        if (used < size - 1) {
            slash = wcsrchr(buffer, L'\\');
            if (!slash) { free(buffer); return NULL; }
            *slash = L'\0';
            return buffer;
        }
        free(buffer);
        size *= 2;
        if (size > 32768) return NULL;
    }
}

static wchar_t *join_path(const wchar_t *left, const wchar_t *right) {
    size_t a, b, total;
    int slash;
    wchar_t *out;
    if (!left || !right) return NULL;
    a = wcslen(left);
    b = wcslen(right);
    slash = a > 0 && left[a - 1] != L'\\' && left[a - 1] != L'/';
    if (a > SIZE_MAX - b - (size_t)slash - 1u) return NULL;
    total = a + b + (size_t)slash + 1u;
    out = (wchar_t *)calloc(total, sizeof(*out));
    if (!out) return NULL;
    wcscpy_s(out, total, left);
    if (slash) wcscat_s(out, total, L"\\");
    wcscat_s(out, total, right);
    return out;
}

static char *utf8_from_wide(const wchar_t *value) {
    int bytes;
    char *out;
    if (!value) return NULL;
    bytes = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS,
                                value, -1, NULL, 0, NULL, NULL);
    if (bytes <= 0) return NULL;
    out = (char *)malloc((size_t)bytes);
    if (!out) return NULL;
    if (!WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS,
                             value, -1, out, bytes, NULL, NULL)) {
        free(out);
        return NULL;
    }
    return out;
}

static int file_exists(const wchar_t *path) {
    DWORD attrs = path ? GetFileAttributesW(path) : INVALID_FILE_ATTRIBUTES;
    return attrs != INVALID_FILE_ATTRIBUTES && !(attrs & FILE_ATTRIBUTE_DIRECTORY);
}

static void usage(void) {
    fwprintf(stderr,
        L"Usage: OpenSPMNative.exe [--game <extracted R8PE01 folder | .iso | .wbfs>] "
        L"[--user-dir <folder>] [--graphics AUTO]\n"
        L"Without --game the game comes from OpenSPM.ini [Game] path, or a file dialog on first run.\n");
}

static int native_settings_env_is_set(void *user, const char *name) {
    (void)user;
    return GetEnvironmentVariableA(name, NULL, 0) > 0;
}

static int native_settings_set_env(void *user, const char *name, const char *value) {
    (void)user;
    /* Process environment (GetEnvironmentVariableA in the bridge) and the CRT
       copy (getenv in the renderer/Aurora) both see the value; the DLLs load
       after this runs. */
    return SetEnvironmentVariableA(name, value) && _putenv_s(name, value) == 0;
}

static void native_settings_log(void *user, const char *line) {
    (void)user;
    fprintf(stderr, "[OpenSPMNative:info] %s\n", line);
}

/* Mods (opt-in: OPENSPM_MODS=1 / OpenSPM.ini [Mods] enabled = 1): load the
   DLLs in <exe dir>\mods through the existing mod ABI (launcher/mod_loader.c)
   and deliver startup/shutdown and runtime running/stopped events around the
   game session. Per-mod toggles: [Mods] <file>.dll = 0. */
static int native_mods_enabled(void) {
    const char *value = getenv("OPENSPM_MODS");
    return value != NULL && value[0] == '1';
}

/* One settings file for everything: OPENSPM_SETTINGS (M14-VANILLA-RUN.cmd points
   it next to the .cmd files), otherwise OpenSPM.ini next to the exe. The per-mod
   toggles live there too, like [Mods] enabled. Caller frees. */
static wchar_t *native_settings_path(const wchar_t *app) {
    const wchar_t *explicit_path = _wgetenv(L"OPENSPM_SETTINGS");
    if (explicit_path && *explicit_path) return _wcsdup(explicit_path);
    return join_path(app, L"OpenSPM.ini");
}

static int native_mod_enabled_from_config(void *context, const wchar_t *filename) {
    if (_wcsicmp(filename, L"enabled") == 0) return 0;
    if (!context) return 1;
    return GetPrivateProfileIntW(L"Mods", filename, 1, (const wchar_t *)context) != 0;
}

/* OpenSPM.ini [Display]/[Effects] -> OPENSPM_* defaults (see native_settings.h).
   OPENSPM_SETTINGS names the file; otherwise OpenSPM.ini next to the exe. */
static void native_apply_settings(const wchar_t *app) {
    wchar_t *owned = native_settings_path(app);
    const wchar_t *path = owned;
    FILE *file = NULL;
    char *text = NULL;
    size_t used = 0;
    openspm_settings_io io = {native_settings_env_is_set, native_settings_set_env, native_settings_log, NULL};
    if (!path) return;
    if (_wfopen_s(&file, path, L"rb") != 0 || !file) {
        fwprintf(stderr, L"[OpenSPMNative:info] No settings file at %ls; using defaults.\n", path);
        free(owned);
        return;
    }
    text = (char *)calloc(65536, 1);
    if (text) used = fread(text, 1, 65535, file);
    fclose(file);
    if (text) {
        text[used] = '\0';
        fwprintf(stderr, L"[OpenSPMNative:info] Settings: %ls\n", path);
        (void)openspm_settings_apply(text, &io);
        free(text);
    }
    free(owned);
}

/* ---- Game selection (first run) ------------------------------------------
   Without --game, OpenSPMNative reads OpenSPM.ini [Game] path; when that is
   missing or unusable it shows a file-open dialog and saves the choice. A
   disc image (.iso/.wbfs/.wia) is identified from its header (R8PE01, disc
   revision 2) and extracted once with the player's wit.exe into
   <user dir>\game, the same `wit extract --psel DATA --pmode NONE` the .cmd
   flow uses. With --game <folder> (the .cmd flow) nothing here runs. */

#define NATIVE_GAME_MARKER L"openspm-game-source.txt"

static wchar_t *wide_from_utf8(const char *value) {
    int count;
    wchar_t *out;
    if (!value) return NULL;
    count = MultiByteToWideChar(CP_UTF8, 0, value, -1, NULL, 0);
    if (count <= 0) return NULL;
    out = (wchar_t *)calloc((size_t)count, sizeof(*out));
    if (!out) return NULL;
    if (!MultiByteToWideChar(CP_UTF8, 0, value, -1, out, count)) {
        free(out);
        return NULL;
    }
    return out;
}

static int directory_exists(const wchar_t *path) {
    DWORD attrs = path ? GetFileAttributesW(path) : INVALID_FILE_ATTRIBUTES;
    return attrs != INVALID_FILE_ATTRIBUTES && (attrs & FILE_ATTRIBUTE_DIRECTORY);
}

static void native_game_message(UINT icon, const wchar_t *text) {
    fwprintf(stderr, L"[OpenSPMNative:%ls] %ls\n", icon == MB_ICONERROR ? L"error" : L"info", text);
    MessageBoxW(NULL, text, L"OpenSPM", MB_OK | MB_SETFOREGROUND | icon);
}

/* NUL-terminated contents of a small file (settings, markers); NULL if absent. */
static char *native_read_text(const wchar_t *path) {
    FILE *file = NULL;
    char *text;
    size_t used;
    if (!path || _wfopen_s(&file, path, L"rb") != 0 || !file) return NULL;
    text = (char *)calloc(262144, 1);
    if (!text) {
        fclose(file);
        return NULL;
    }
    used = fread(text, 1, 262143, file);
    text[used] = '\0';
    fclose(file);
    return text;
}

static int native_write_text(const wchar_t *path, const char *text) {
    wchar_t *temp = NULL;
    FILE *file = NULL;
    size_t len = strlen(text), n = wcslen(path) + 5u;
    int ok;
    temp = (wchar_t *)calloc(n, sizeof(*temp));
    if (!temp) return 0;
    swprintf(temp, n, L"%ls.tmp", path);
    if (_wfopen_s(&file, temp, L"wb") != 0 || !file) {
        free(temp);
        return 0;
    }
    ok = fwrite(text, 1, len, file) == len;
    ok = (fclose(file) == 0) && ok;
    ok = ok && MoveFileExW(temp, path, MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH);
    if (!ok) DeleteFileW(temp);
    free(temp);
    return ok;
}

static int native_handle_read(void *user, uint64_t offset, void *buffer, size_t size) {
    LARGE_INTEGER at;
    DWORD got = 0;
    if (size > 0x100000u) return 0;
    at.QuadPart = (LONGLONG)offset;
    if (!SetFilePointerEx((HANDLE)user, at, NULL, FILE_BEGIN)) return 0;
    return ReadFile((HANDLE)user, buffer, (DWORD)size, &got, NULL) && got == size;
}

/* 1 when `path` holds a disc header (an image, or an extracted sys\boot.bin). */
static int native_probe_file(const wchar_t *path, openspm_disc_info *info) {
    HANDLE file = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_EXISTING,
                              FILE_ATTRIBUTE_NORMAL, NULL);
    int found;
    memset(info, 0, sizeof(*info));
    if (file == INVALID_HANDLE_VALUE) return 0;
    found = openspm_image_probe(native_handle_read, file, info);
    CloseHandle(file);
    return found;
}

static void native_check_message(openspm_game_check check, const openspm_disc_info *info,
                                 const wchar_t *path, wchar_t *out, size_t cap) {
    char reason[768];
    wchar_t *wide;
    openspm_game_check_message(check, info, reason, sizeof reason);
    wide = wide_from_utf8(reason);
    swprintf(out, cap, L"%ls\n\n%ls", path, wide ? wide : L"");
    free(wide);
}

/* An extracted folder whose sys\boot.bin (when present) is R8PE01 rev 2. */
static int native_game_folder_ok(const wchar_t *root, wchar_t *why, size_t cap) {
    wchar_t *dol = join_path(root, L"sys\\main.dol");
    wchar_t *boot = join_path(root, L"sys\\boot.bin");
    openspm_disc_info info;
    int ok = 0;
    if (!dol || !boot) goto done;
    if (!file_exists(dol)) {
        swprintf(why, cap, L"%ls\n\nThis folder does not contain an extracted game (sys\\main.dol).", root);
        goto done;
    }
    ok = 1;
    if (file_exists(boot)) {
        openspm_game_check check = native_probe_file(boot, &info) ? openspm_game_check_info(&info)
                                                                   : OPENSPM_GAME_UNREADABLE;
        if (check != OPENSPM_GAME_OK && check != OPENSPM_GAME_UNSUPPORTED_FORMAT) {
            native_check_message(check, &info, root, why, cap);
            ok = 0;
        }
    }
done:
    free(dol);
    free(boot);
    return ok;
}

static wchar_t *native_parent_dir(const wchar_t *path) {
    wchar_t *out = _wcsdup(path);
    wchar_t *slash = out ? wcsrchr(out, L'\\') : NULL;
    wchar_t *fwd = out ? wcsrchr(out, L'/') : NULL;
    if (fwd && (!slash || fwd > slash)) slash = fwd;
    if (!slash) {
        free(out);
        return NULL;
    }
    *slash = L'\0';
    return out;
}

static int native_delete_tree(const wchar_t *path) {
    size_t n = wcslen(path);
    wchar_t *from = (wchar_t *)calloc(n + 2u, sizeof(*from)); /* double-NUL list */
    SHFILEOPSTRUCTW op;
    int rc;
    if (!from) return 0;
    memcpy(from, path, n * sizeof(*from));
    memset(&op, 0, sizeof(op));
    op.wFunc = FO_DELETE;
    op.pFrom = from;
    op.fFlags = FOF_NO_UI;
    rc = SHFileOperationW(&op);
    free(from);
    return rc == 0 && !op.fAnyOperationsAborted && !directory_exists(path);
}

/* wit.exe: [Game] wit, OPENSPM_WIT (as in the .cmd flow), next to the exe
   (wit.exe, wit\wit.exe, wit\bin\wit.exe), then PATH. */
static wchar_t *native_find_wit(const wchar_t *app, const char *settings_text) {
    static const wchar_t *const local[] = {L"wit.exe", L"wit\\wit.exe", L"wit\\bin\\wit.exe"};
    char configured[4096];
    wchar_t found[MAX_PATH * 4];
    const wchar_t *env = _wgetenv(L"OPENSPM_WIT");
    size_t i;
    if (openspm_ini_get_raw(settings_text, "Game", "wit", configured, sizeof configured)) {
        wchar_t *wide = wide_from_utf8(configured);
        if (wide && file_exists(wide)) return wide;
        free(wide);
    }
    if (env && *env && file_exists(env)) return _wcsdup(env);
    for (i = 0; i < sizeof(local) / sizeof(local[0]); ++i) {
        wchar_t *path = join_path(app, local[i]);
        if (path && file_exists(path)) return path;
        free(path);
    }
    if (SearchPathW(NULL, L"wit.exe", NULL, (DWORD)(sizeof(found) / sizeof(found[0])), found, NULL))
        return _wcsdup(found);
    return NULL;
}

static int native_run_wit(const wchar_t *wit, const wchar_t *image, const wchar_t *dest) {
    size_t n = wcslen(wit) + wcslen(image) + wcslen(dest) + 96u;
    wchar_t *command = (wchar_t *)calloc(n, sizeof(*command));
    STARTUPINFOW si;
    PROCESS_INFORMATION pi;
    DWORD code = 1;
    if (!command) return 0;
    swprintf(command, n, L"\"%ls\" extract --psel DATA --pmode NONE \"%ls\" -d \"%ls\"", wit, image, dest);
    memset(&si, 0, sizeof(si));
    si.cb = sizeof(si);
    memset(&pi, 0, sizeof(pi));
    fwprintf(stderr, L"[OpenSPMNative:info] Extracting: %ls\n", command);
    if (!CreateProcessW(wit, command, NULL, NULL, FALSE, 0, NULL, NULL, &si, &pi)) {
        fwprintf(stderr, L"[OpenSPMNative:error] Could not start wit (Win32 %lu)\n", GetLastError());
        free(command);
        return 0;
    }
    WaitForSingleObject(pi.hProcess, INFINITE);
    GetExitCodeProcess(pi.hProcess, &code);
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    free(command);
    return code == 0;
}

/* Extracts `image` into <user>\game once; later launches reuse it while the
   image path, size and time are unchanged. *retry: the player may pick again. */
static wchar_t *native_extract_image(const wchar_t *app, const wchar_t *user, const char *settings_text,
                                     const wchar_t *image, int *retry) {
    WIN32_FILE_ATTRIBUTE_DATA attrs;
    wchar_t *dest = join_path(user, L"game");
    wchar_t *partial = join_path(user, L"game.extracting");
    wchar_t *marker = dest ? join_path(dest, NATIVE_GAME_MARKER) : NULL;
    wchar_t *partial_marker = partial ? join_path(partial, NATIVE_GAME_MARKER) : NULL;
    wchar_t *dol = dest ? join_path(dest, L"sys\\main.dol") : NULL;
    wchar_t *partial_dol = partial ? join_path(partial, L"sys\\main.dol") : NULL;
    wchar_t *wit = NULL;
    char *image8 = utf8_from_wide(image), *stamp = NULL, *existing = NULL;
    wchar_t msg[2048];
    wchar_t *result = NULL;

    *retry = 0;
    if (!dest || !partial || !marker || !partial_marker || !dol || !partial_dol || !image8) goto done;
    if (!GetFileAttributesExW(image, GetFileExInfoStandard, &attrs)) goto done;
    stamp = (char *)calloc(strlen(image8) + 128u, 1);
    if (!stamp) goto done;
    snprintf(stamp, strlen(image8) + 128u, "image=%s\r\nsize=%llu\r\nmodified=%llu\r\n", image8,
            ((unsigned long long)attrs.nFileSizeHigh << 32) | attrs.nFileSizeLow,
            ((unsigned long long)attrs.ftLastWriteTime.dwHighDateTime << 32) | attrs.ftLastWriteTime.dwLowDateTime);
    existing = native_read_text(marker);
    if (existing && strcmp(existing, stamp) == 0 && file_exists(dol)) {
        fwprintf(stderr, L"[OpenSPMNative:info] Using the extracted game in %ls\n", dest);
        result = _wcsdup(dest);
        goto done;
    }
    if (directory_exists(dest) && !existing) {
        swprintf(msg, sizeof(msg) / sizeof(msg[0]),
                 L"OpenSPM extracts the game to\n%ls\n\nbut that folder already exists and was not created by "
                 L"OpenSPM. Move or delete it, then start OpenSPM again.", dest);
        native_game_message(MB_ICONERROR, msg);
        goto done;
    }
    wit = native_find_wit(app, settings_text);
    if (!wit) {
        swprintf(msg, sizeof(msg) / sizeof(msg[0]),
                 L"The game image is Super Paper Mario (USA, revision 2), but it has to be extracted once and "
                 L"OpenSPM needs Wiimms ISO Tools (wit) for that.\n\n"
                 L"Download Wiimms ISO Tools for Windows (wit.wiimm.de), unpack it into a folder named \"wit\" "
                 L"next to OpenSPMNative.exe:\n%ls\\wit\\bin\\wit.exe\n\n"
                 L"or set [Game] wit = <path to wit.exe> in OpenSPM.ini, then start OpenSPM again.", app);
        native_game_message(MB_ICONERROR, msg);
        goto done;
    }
    swprintf(msg, sizeof(msg) / sizeof(msg[0]),
             L"OpenSPM will now extract your game once (about 4 GB, a few minutes) to\n%ls\n\n"
             L"Progress is shown in the console window.", dest);
    if (MessageBoxW(NULL, msg, L"OpenSPM", MB_OKCANCEL | MB_ICONINFORMATION | MB_SETFOREGROUND) != IDOK) goto done;

    SHCreateDirectoryExW(NULL, user, NULL); /* nested --user-dir too; exists = fine */
    if (directory_exists(partial) && !native_delete_tree(partial)) {
        swprintf(msg, sizeof(msg) / sizeof(msg[0]), L"Could not remove the unfinished extraction in\n%ls", partial);
        native_game_message(MB_ICONERROR, msg);
        goto done;
    }
    if (!native_run_wit(wit, image, partial) || !file_exists(partial_dol)) {
        swprintf(msg, sizeof(msg) / sizeof(msg[0]),
                 L"Extracting the game with\n%ls\nfailed; the console window shows wit's message. Check that the "
                 L"disk has about 5 GB free and that the image is not damaged.", wit);
        native_game_message(MB_ICONERROR, msg);
        goto done;
    }
    if (!native_write_text(partial_marker, stamp) ||
        (directory_exists(dest) && !native_delete_tree(dest)) ||
        !MoveFileExW(partial, dest, 0)) {
        swprintf(msg, sizeof(msg) / sizeof(msg[0]),
                 L"The game was extracted to\n%ls\nbut could not be moved to\n%ls (Win32 %lu).", partial, dest,
                 GetLastError());
        native_game_message(MB_ICONERROR, msg);
        goto done;
    }
    fwprintf(stderr, L"[OpenSPMNative:info] Game extracted to %ls\n", dest);
    result = _wcsdup(dest);
done:
    free(dest); free(partial); free(marker); free(partial_marker); free(dol); free(partial_dol);
    free(wit); free(image8); free(stamp); free(existing);
    return result;
}

/* Game root for a chosen path: an extracted folder, its sys\main.dol, or a
   disc image (extracted on first use). NULL with *retry=1 when the player
   should pick something else. */
static wchar_t *native_game_root_from_choice(const wchar_t *app, const wchar_t *user, const char *settings_text,
                                             const wchar_t *choice, int *retry) {
    wchar_t why[2048];
    const wchar_t *name = wcsrchr(choice, L'\\');
    wchar_t *root = NULL;
    *retry = 1;
    why[0] = L'\0';
    name = name ? name + 1 : choice;
    if (directory_exists(choice)) {
        wchar_t *data = join_path(choice, L"DATA");
        wchar_t *data_dol = data ? join_path(data, L"sys\\main.dol") : NULL;
        if (data_dol && file_exists(data_dol)) root = _wcsdup(data); /* extracted with a DATA\ level */
        else if (_wcsicmp(name, L"sys") == 0) root = native_parent_dir(choice);
        else root = _wcsdup(choice);
        free(data);
        free(data_dol);
    } else if (_wcsicmp(name, L"main.dol") == 0) {
        wchar_t *sys = native_parent_dir(choice);
        root = sys ? native_parent_dir(sys) : NULL;
        free(sys);
    } else if (file_exists(choice)) {
        openspm_disc_info info;
        openspm_game_check check = native_probe_file(choice, &info) ? openspm_game_check_info(&info)
                                                                     : OPENSPM_GAME_UNREADABLE;
        if (check != OPENSPM_GAME_OK) {
            native_check_message(check, &info, choice, why, sizeof(why) / sizeof(why[0]));
            wcscat_s(why, sizeof(why) / sizeof(why[0]), L"\n\nChoose the game again.");
            native_game_message(MB_ICONWARNING, why);
            return NULL;
        }
        fwprintf(stderr, L"[OpenSPMNative:info] Game image: %ls (R8PE01 revision %u)\n", choice, info.revision);
        return native_extract_image(app, user, settings_text, choice, retry);
    } else {
        swprintf(why, sizeof(why) / sizeof(why[0]), L"The game was not found:\n%ls\n\nChoose the game again.",
                 choice);
        native_game_message(MB_ICONWARNING, why);
        return NULL;
    }
    if (root && native_game_folder_ok(root, why, sizeof(why) / sizeof(why[0]))) return root;
    if (!why[0]) swprintf(why, sizeof(why) / sizeof(why[0]), L"%ls\n\nThis is not an extracted game.", choice);
    wcscat_s(why, sizeof(why) / sizeof(why[0]), L"\n\nChoose the game again.");
    native_game_message(MB_ICONWARNING, why);
    free(root);
    return NULL;
}

static wchar_t *native_pick_game(void) {
    wchar_t *path = (wchar_t *)calloc(32768, sizeof(*path));
    OPENFILENAMEW ofn;
    if (!path) return NULL;
    memset(&ofn, 0, sizeof(ofn));
    ofn.lStructSize = sizeof(ofn);
    ofn.lpstrFilter = L"Super Paper Mario (*.iso, *.wbfs, *.wia, sys\\main.dol)\0*.iso;*.wbfs;*.wia;*.rvz;main.dol\0"
                      L"All files (*.*)\0*.*\0";
    ofn.lpstrFile = path;
    ofn.nMaxFile = 32768;
    ofn.lpstrTitle = L"OpenSPM - choose your Super Paper Mario (USA) disc image";
    ofn.Flags = OFN_EXPLORER | OFN_FILEMUSTEXIST | OFN_PATHMUSTEXIST | OFN_NOCHANGEDIR | OFN_HIDEREADONLY |
                OFN_DONTADDTORECENT;
    if (!GetOpenFileNameW(&ofn)) {
        DWORD error = CommDlgExtendedError();
        if (error) fwprintf(stderr, L"[OpenSPMNative:error] File dialog failed (0x%lx)\n", error);
        free(path);
        return NULL;
    }
    return path;
}

static void native_store_game_path(const wchar_t *app, const wchar_t *settings, const char *settings_text,
                                   const wchar_t *choice) {
    char *choice8 = utf8_from_wide(choice);
    char *base = NULL, *updated = NULL;
    if (!settings || !choice8) goto done;
    if (!settings_text) { /* first run: start from the documented example */
        wchar_t *example = join_path(app, L"OpenSPM.ini.example");
        base = native_read_text(example);
        free(example);
    }
    updated = openspm_ini_set_raw(settings_text ? settings_text : (base ? base : ""), "Game", "path", choice8);
    if (updated && native_write_text(settings, updated))
        fwprintf(stderr, L"[OpenSPMNative:info] Saved [Game] path in %ls\n", settings);
    else
        fwprintf(stderr, L"[OpenSPMNative:warn] Could not save [Game] path in %ls\n", settings);
done:
    free(choice8); free(base); free(updated);
}

/* --game <folder> is used as given (the .cmd flow). --game <image> is
   identified and extracted. No --game: OpenSPM.ini [Game] path, else the
   file dialog. */
static wchar_t *native_resolve_game(const wchar_t *app, const wchar_t *user, const wchar_t *arg) {
    wchar_t *settings = native_settings_path(app);
    char *settings_text = native_read_text(settings);
    char configured[8192];
    wchar_t *choice = NULL, *root = NULL;
    int from_config = 1, retry = 0;
    if (arg && *arg) {
        if (!file_exists(arg)) root = _wcsdup(arg);
        else root = native_game_root_from_choice(app, user, settings_text, arg, &retry);
        goto done;
    }
    if (openspm_ini_get_raw(settings_text, "Game", "path", configured, sizeof configured))
        choice = wide_from_utf8(configured);
    for (;;) {
        if (!choice) {
            choice = native_pick_game();
            from_config = 0;
            if (!choice) {
                fwprintf(stderr, L"[OpenSPMNative:info] No game selected.\n");
                break;
            }
        }
        root = native_game_root_from_choice(app, user, settings_text, choice, &retry);
        if (root) {
            if (!from_config) native_store_game_path(app, settings, settings_text, choice);
            break;
        }
        free(choice);
        choice = NULL;
        if (!retry) break;
    }
done:
    free(choice);
    free(settings);
    free(settings_text);
    return root;
}

int wmain(int argc, wchar_t **argv) {
    const wchar_t *game = NULL;
    const wchar_t *user_arg = NULL;
    const wchar_t *graphics = L"AUTO";
    wchar_t *app = NULL, *runtime = NULL, *default_user = NULL;
    wchar_t *backend_path = NULL, *renderer_path = NULL, *main_dol = NULL;
    wchar_t *local_module_user = NULL, *local_module_runtime = NULL;
    char *app8 = NULL, *runtime8 = NULL, *game8 = NULL, *user8 = NULL, *graphics8 = NULL;
    HMODULE backend_module = NULL;
    openspm_backend_query_fn backend_query = NULL;
    openspm_backend_api backend;
    openspm_loaded_renderer renderer;
    openspm_host_api host;
    openspm_launch_desc launch;
    native_host_context host_context;
    int rc = OPENSPM_ERROR_RUNTIME;
    openspm_sample_profiler *profiler = NULL;
    int previous_main_priority = THREAD_PRIORITY_ERROR_RETURN;
    int main_qos_applied = 0;
    openspm_cpu_set_restore main_cpu_set_restore;
    openspm_mod_set mods;
    wchar_t *mods_dir = NULL;
    wchar_t *mods_settings = NULL;
    wchar_t *resolved_game = NULL;
    int interactive = 0;
    int i;

    memset(&backend, 0, sizeof(backend));
    memset(&renderer, 0, sizeof(renderer));
    memset(&host, 0, sizeof(host));
    memset(&launch, 0, sizeof(launch));
    memset(&host_context, 0, sizeof(host_context));
    memset(&main_cpu_set_restore, 0, sizeof(main_cpu_set_restore));
    memset(&mods, 0, sizeof(mods));

    for (i = 1; i < argc; ++i) {
        if (wcscmp(argv[i], L"--game") == 0 && i + 1 < argc) game = argv[++i];
        else if (wcscmp(argv[i], L"--user-dir") == 0 && i + 1 < argc) user_arg = argv[++i];
        else if (wcscmp(argv[i], L"--graphics") == 0 && i + 1 < argc) graphics = argv[++i];
        else if (wcscmp(argv[i], L"--version") == 0) {
            fwprintf(stdout, L"OpenSPMNative M14 ABI %u\n", OPENSPM_ABI_VERSION);
            return 0;
        } else if (wcscmp(argv[i], L"--help") == 0 || wcscmp(argv[i], L"-h") == 0) {
            usage();
            return 0;
        } else {
            fwprintf(stderr, L"Unknown or incomplete option: %ls\n", argv[i]);
            usage();
            return 2;
        }
    }
    /* No --game: first-run game picker / OpenSPM.ini [Game] path (below). */
    interactive = !game || !*game;

    /* QueryPerformanceFrequency is invariant for the running system. Cache it
       once before the host API is exposed; dev118's fine VI wait can call the
       monotonic clock hundreds of thousands of times per second. */
    if (!host_init_monotonic_clock()) {
        fprintf(stderr,
            "[OpenSPMNative:error] QueryPerformanceFrequency is unavailable; native monotonic timing cannot start.\n");
        return 1;
    }

    app = application_root();
    if (!app) goto cleanup;
    native_apply_settings(app);
    runtime = join_path(app, L"runtime");
    default_user = join_path(app, L"User-OpenSPM");
    if (!default_user) goto cleanup;
    resolved_game = native_resolve_game(app, (user_arg && *user_arg) ? user_arg : default_user, game);
    if (!resolved_game) {
        rc = OPENSPM_ERROR_NOT_FOUND;
        goto cleanup;
    }
    game = resolved_game;
    backend_path = runtime ? join_path(runtime, L"openspm_native_backend.dll") : NULL;
    renderer_path = runtime ? join_path(runtime, L"openspm_renderer.dll") : NULL;
    main_dol = join_path(game, L"sys\\main.dol");
    if (!runtime || !default_user || !backend_path || !renderer_path || !main_dol) goto cleanup;

    if (!file_exists(main_dol)) {
        fwprintf(stderr, L"Missing extracted R8PE01 sys\\main.dol: %ls\n", main_dol);
        rc = OPENSPM_ERROR_NOT_FOUND;
        goto cleanup;
    }
    if (!file_exists(backend_path)) {
        fwprintf(stderr, L"Strict M14 launch requires: %ls\n", backend_path);
        rc = OPENSPM_ERROR_NOT_FOUND;
        goto cleanup;
    }
    if (!file_exists(renderer_path)) {
        fwprintf(stderr, L"Strict M14 launch requires: %ls\n", renderer_path);
        rc = OPENSPM_ERROR_NOT_FOUND;
        goto cleanup;
    }

    {
        const wchar_t *user = (user_arg && *user_arg) ? user_arg : default_user;
        wchar_t *native_dir = join_path(user, L"native");
        local_module_user = native_dir ? join_path(native_dir, L"openspm_local_native.dll") : NULL;
        free(native_dir);
        local_module_runtime = runtime ? join_path(runtime, L"native\\openspm_local_native.dll") : NULL;
        if ((!local_module_user || !file_exists(local_module_user)) &&
            (!local_module_runtime || !file_exists(local_module_runtime))) {
            fwprintf(stderr,
                L"No player-local generated native module found. Expected either:\n  %ls\n  %ls\n",
                local_module_user ? local_module_user : L"(invalid user path)",
                local_module_runtime ? local_module_runtime : L"(invalid runtime path)");
            if (interactive) {
                wchar_t msg[2048];
                swprintf(msg, sizeof(msg) / sizeof(msg[0]),
                         L"The game is ready, but this OpenSPM folder has no native game module yet:\n%ls\n\n"
                         L"The module is generated from your own main.dol on this PC. Until the release "
                         L"packages do that step, build it once with M14-VANILLA-RUN.cmd (see docs\\PLAYING.md).",
                         local_module_user ? local_module_user : L"User-OpenSPM\\native\\openspm_local_native.dll");
                native_game_message(MB_ICONERROR, msg);
            }
            rc = OPENSPM_ERROR_NOT_FOUND;
            goto cleanup;
        }

        app8 = utf8_from_wide(app);
        runtime8 = utf8_from_wide(runtime);
        game8 = utf8_from_wide(game);
        user8 = utf8_from_wide(user);
        graphics8 = utf8_from_wide(graphics);
    }
    if (!app8 || !runtime8 || !game8 || !user8 || !graphics8) goto cleanup;

    backend_module = LoadLibraryW(backend_path);
    if (!backend_module) {
        fwprintf(stderr, L"Could not load M14 native backend: %ls (Win32 %lu)\n",
                 backend_path, GetLastError());
        rc = OPENSPM_ERROR_LOAD_FAILED;
        goto cleanup;
    }
    backend_query = (openspm_backend_query_fn)(void *)GetProcAddress(
        backend_module, OPENSPM_BACKEND_QUERY_SYMBOL);
    if (!backend_query) {
        fprintf(stderr, "M14 native backend is missing %s.\n", OPENSPM_BACKEND_QUERY_SYMBOL);
        rc = OPENSPM_ERROR_ABI_MISMATCH;
        goto cleanup;
    }

    host_context.backend = &backend;
    host_context.mods = NULL;
    host.header.struct_size = sizeof(host);
    host.header.abi_version = OPENSPM_ABI_VERSION;
    host.capabilities = OPENSPM_HOST_CAP_SERVICE_QUERY;
    host.context = &host_context;
    host.log = host_log;
    host.monotonic_ns = host_monotonic_ns;
    host.query_service = host_query_service;

    backend.header.struct_size = sizeof(backend);
    backend.header.abi_version = OPENSPM_ABI_VERSION;
    rc = backend_query(OPENSPM_ABI_VERSION, &host, &backend);
    if (rc != OPENSPM_OK || openspm_validate_backend_api(&backend) != OPENSPM_OK) {
        fprintf(stderr, "M14 native backend ABI negotiation failed (%d).\n", rc);
        rc = OPENSPM_ERROR_ABI_MISMATCH;
        goto cleanup;
    }
    if (strcmp(backend.backend_id, "openspm.native") != 0) {
        fprintf(stderr, "Refusing non-native backend '%s'.\n", backend.backend_id);
        rc = OPENSPM_ERROR_UNSUPPORTED;
        goto cleanup;
    }

    launch.header.struct_size = sizeof(launch);
    launch.header.abi_version = OPENSPM_ABI_VERSION;
    launch.app_root_utf8 = app8;
    launch.runtime_root_utf8 = runtime8;
    launch.game_root_utf8 = game8;
    launch.user_root_utf8 = user8;
    launch.graphics_backend_utf8 = graphics8;
    launch.profile_id_utf8 = "m14-native";
    launch.boot_target_utf8 = "normal";
    launch.flags = OPENSPM_LAUNCH_FLAG_GAME_ROOT_READ_ONLY;
    if (openspm_validate_launch_desc(&launch) != OPENSPM_OK) {
        rc = OPENSPM_ERROR_INVALID_ARGUMENT;
        goto cleanup;
    }

    rc = openspm_renderer_load_and_bind(renderer_path, &host, &launch,
                                        &backend, &renderer);
    if (rc != OPENSPM_OK) {
        fwprintf(stderr,
            L"Native renderer exists but could not bind to the M14 backend: %ls (OpenSPM %d)\n",
            renderer_path, rc);
        goto cleanup;
    }
    if ((renderer.api.capabilities & OPENSPM_RENDERER_CAP_PRESENT) == 0u) {
        fprintf(stderr, "Refusing renderer without native presentation capability.\n");
        rc = OPENSPM_ERROR_UNSUPPORTED;
        goto cleanup;
    }

    fprintf(stderr,
        "OpenSPMNative -> backend %s %s; renderer %s %s; no emulator fallback\n",
        backend.backend_id, backend.backend_version,
        renderer.api.renderer_id, renderer.api.renderer_version);

    /* dev123: set priority only after renderer initialization. This avoids
       changing the priority inherited/selected by Aurora worker threads and
       isolates the A/B to the producer thread that executes the recompiled Wii
       runtime. No guest timing, VI deadline, renderer ordering or CPU affinity
       is changed. */
    if (native_mods_enabled()) {
        mods_dir = join_path(app, L"mods");
        mods_settings = native_settings_path(app); /* [Mods] <file>.dll toggles */
        if (mods_dir && openspm_mods_load_filtered(mods_dir, &host, native_mod_enabled_from_config, mods_settings, &mods) ==
                            OPENSPM_OK) {
            fwprintf(stderr, L"[OpenSPMNative:info] Mods: %zu loaded from %ls\n", mods.count, mods_dir);
            openspm_mods_event(&mods, OPENSPM_MOD_EVENT_STARTUP, NULL);
            (void)openspm_mods_publish_file_roots(&mods, &host);
            if (mods.count != 0u) {
                /* Per-frame TICK_BEGIN / PRESENT_END from the backend safepoint.
                   Set before backend.run, which reads it at the first frame. */
                host_context.mods = &mods;
                host.runtime_event = host_runtime_event;
                host.capabilities |= OPENSPM_HOST_CAP_RUNTIME_EVENTS;
            }
        } else {
            fwprintf(stderr, L"[OpenSPMNative:warn] Mods: discovery failed in %ls; continuing without mods\n",
                     mods_dir ? mods_dir : L"(null)");
            memset(&mods, 0, sizeof(mods));
        }
    }

    previous_main_priority = host_apply_main_thread_priority();
    main_qos_applied = host_apply_main_thread_high_qos();
    (void)host_apply_main_thread_fast_cpuset(&main_cpu_set_restore);
    openspm_mods_event(&mods, OPENSPM_MOD_EVENT_RUNTIME_RUNNING, NULL);
    profiler = openspm_sample_profiler_start(); /* OPENSPM_PROFILE=1 only */
    rc = backend.run(backend.backend_context, &launch);
    openspm_sample_profiler_stop(profiler);
    profiler = NULL;
    host.runtime_event = NULL;
    host_context.mods = NULL;
    openspm_mods_event(&mods, OPENSPM_MOD_EVENT_RUNTIME_STOPPED, NULL);
    host_restore_main_thread_cpuset(&main_cpu_set_restore);
    host_restore_main_thread_qos(main_qos_applied);
    main_qos_applied = 0;
    host_restore_main_thread_priority(previous_main_priority);
    previous_main_priority = THREAD_PRIORITY_ERROR_RETURN;

cleanup:
    if (mods.count != 0u) {
        openspm_mods_event(&mods, OPENSPM_MOD_EVENT_SHUTDOWN, NULL);
        openspm_mods_unload(&mods);
    }
    free(mods_dir);
    free(mods_settings);
    host_restore_main_thread_cpuset(&main_cpu_set_restore);
    host_restore_main_thread_qos(main_qos_applied);
    host_restore_main_thread_priority(previous_main_priority);
    openspm_renderer_unload(&renderer);
    if (backend_module) {
        if (backend.shutdown) backend.shutdown(backend.backend_context);
        FreeLibrary(backend_module);
    }
    free(app8); free(runtime8); free(game8); free(user8); free(graphics8);
    free(app); free(runtime); free(default_user);
    free(backend_path); free(renderer_path); free(main_dol);
    free(local_module_user); free(local_module_runtime);
    free(resolved_game);
    return rc < 0 ? 1 : rc;
}
