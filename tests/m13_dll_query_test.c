#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include "openspm/backend_api.h"
#include "openspm/runtime_service.h"
#include "openspm/validate.h"

#include <stdio.h>
#include <string.h>

static void OPENSPM_CALL test_log(void *context, openspm_log_level level, const char *message) {
    (void)context;
    fprintf(stderr, "[backend:%d] %s\n", (int)level, message ? message : "(null)");
}
static uint64_t OPENSPM_CALL test_clock(void *context) { (void)context; return 7; }

static void print_win32_error(const char *operation) {
    DWORD code = GetLastError();
    char *message = NULL;
    DWORD flags = FORMAT_MESSAGE_ALLOCATE_BUFFER | FORMAT_MESSAGE_FROM_SYSTEM |
                  FORMAT_MESSAGE_IGNORE_INSERTS;
    DWORD length = FormatMessageA(flags, NULL, code, 0, (LPSTR)&message, 0, NULL);
    if (length && message) {
        fprintf(stderr, "%s failed: Win32 error %lu: %s\n", operation,
                (unsigned long)code, message);
        LocalFree(message);
    } else {
        fprintf(stderr, "%s failed: Win32 error %lu\n", operation,
                (unsigned long)code);
    }
}

static int validate_runtime_service(openspm_backend_api *backend) {
    openspm_runtime_service runtime;
    openspm_runtime_perf_snapshot snapshot;
    int rc;
    if (!(backend->capabilities & OPENSPM_BACKEND_CAP_RUNTIME_SERVICE))
        return 0;
    if (!backend->query_service) {
        fprintf(stderr, "Backend advertises runtime service but query_service is null.\n");
        return 20;
    }

    memset(&runtime, 0, sizeof(runtime));
    runtime.header.struct_size = sizeof(runtime);
    runtime.header.abi_version = OPENSPM_RUNTIME_SERVICE_VERSION;
    rc = backend->query_service(backend->backend_context, OPENSPM_RUNTIME_SERVICE_ID,
                                OPENSPM_RUNTIME_SERVICE_VERSION, &runtime);
    if (rc != OPENSPM_OK) {
        fprintf(stderr, "Runtime service query returned %d.\n", rc);
        return 21;
    }
    if (!runtime.get_performance || !runtime.pause || !runtime.resume || !runtime.request_stop) {
        fprintf(stderr, "Runtime service is missing required phase5 controls.\n");
        return 22;
    }
    if (!(runtime.capabilities & OPENSPM_RUNTIME_CAP_PERF_SNAPSHOT) ||
        !(runtime.capabilities & OPENSPM_RUNTIME_CAP_PRESENT_EVENTS)) {
        fprintf(stderr, "Runtime service is missing advertised phase5 capabilities.\n");
        return 23;
    }

    memset(&snapshot, 0, sizeof(snapshot));
    snapshot.header.struct_size = sizeof(snapshot);
    snapshot.header.abi_version = OPENSPM_RUNTIME_SERVICE_VERSION;
    rc = runtime.get_performance(runtime.runtime_context, &snapshot);
    if (rc != OPENSPM_ERROR_RUNTIME) {
        fprintf(stderr, "Inactive runtime performance query should return runtime error, got %d.\n", rc);
        return 24;
    }
    puts("Runtime service table validated before boot.");
    return 0;
}

int wmain(int argc, wchar_t **argv) {
    HMODULE module;
    openspm_backend_query_fn query;
    openspm_host_api host;
    openspm_backend_api backend;
    char expected_id[128];
    int expected_bytes;
    int rc;
    if (argc != 2 && argc != 3) {
        fprintf(stderr, "Expected backend DLL path and optional expected backend ID.\n");
        return 10;
    }
    memset(expected_id, 0, sizeof(expected_id));
    if (argc == 3) {
        expected_bytes = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS,
                                             argv[2], -1, expected_id,
                                             (int)sizeof(expected_id), NULL, NULL);
        if (expected_bytes <= 0) {
            fprintf(stderr, "Expected backend ID could not be converted to UTF-8.\n");
            return 15;
        }
    }

    module = LoadLibraryW(argv[1]);
    if (!module) {
        print_win32_error("LoadLibraryW");
        return 11;
    }
    query = (openspm_backend_query_fn)(void *)GetProcAddress(module, OPENSPM_BACKEND_QUERY_SYMBOL);
    if (!query) {
        print_win32_error("GetProcAddress(openspm_backend_query)");
        FreeLibrary(module);
        return 12;
    }
    memset(&host, 0, sizeof(host));
    memset(&backend, 0, sizeof(backend));
    host.header.struct_size = sizeof(host);
    host.header.abi_version = OPENSPM_ABI_VERSION;
    host.log = test_log;
    host.monotonic_ns = test_clock;
    backend.header.struct_size = sizeof(backend);
    backend.header.abi_version = OPENSPM_ABI_VERSION;
    rc = query(OPENSPM_ABI_VERSION, &host, &backend);
    if (rc != OPENSPM_OK) {
        fprintf(stderr, "openspm_backend_query returned %d.\n", rc);
        FreeLibrary(module);
        return 13;
    }
    rc = openspm_validate_backend_api(&backend);
    if (rc != OPENSPM_OK) {
        fprintf(stderr, "openspm_validate_backend_api returned %d.\n", rc);
        FreeLibrary(module);
        return 14;
    }
    if (argc == 3 && strcmp(backend.backend_id, expected_id) != 0) {
        fprintf(stderr, "Backend ID mismatch: expected '%s', got '%s'.\n",
                expected_id, backend.backend_id);
        FreeLibrary(module);
        return 16;
    }
    printf("Loaded backend DLL: %s %s\n", backend.backend_id, backend.backend_version);
    rc = validate_runtime_service(&backend);
    if (rc != 0) {
        FreeLibrary(module);
        return rc;
    }
    if (backend.shutdown) backend.shutdown(backend.backend_context);
    FreeLibrary(module);
    return 0;
}
