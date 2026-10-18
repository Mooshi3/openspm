#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include "openspm/backend_api.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>

static const openspm_host_api *g_host;

static void log_message(openspm_log_level level, const char *message) {
    if (g_host && g_host->log) g_host->log(g_host->context, level, message);
}

static wchar_t *wide_from_utf8(const char *value) {
    int chars;
    wchar_t *out;
    if (!value) return NULL;
    chars = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, value, -1, NULL, 0);
    if (chars <= 0) return NULL;
    out = (wchar_t *)calloc((size_t)chars, sizeof(wchar_t));
    if (!out) return NULL;
    if (!MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, value, -1, out, chars)) {
        free(out);
        return NULL;
    }
    return out;
}

static wchar_t *join_path(const wchar_t *left, const wchar_t *right) {
    size_t a = wcslen(left), b = wcslen(right);
    int slash = a > 0 && left[a - 1] != L'\\' && left[a - 1] != L'/';
    wchar_t *out = (wchar_t *)calloc(a + b + (size_t)slash + 1, sizeof(wchar_t));
    if (!out) return NULL;
    wcscpy_s(out, a + b + (size_t)slash + 1, left);
    if (slash) wcscat_s(out, a + b + (size_t)slash + 1, L"\\");
    wcscat_s(out, a + b + (size_t)slash + 1, right);
    return out;
}

static int file_exists(const wchar_t *path) {
    DWORD attrs = GetFileAttributesW(path);
    return attrs != INVALID_FILE_ATTRIBUTES && !(attrs & FILE_ATTRIBUTE_DIRECTORY);
}

static int contains_quote(const wchar_t *value) {
    return value && wcschr(value, L'\"') != NULL;
}

static int OPENSPM_CALL legacy_run(void *backend_context, const openspm_launch_desc *launch) {
    wchar_t *runtime = NULL, *legacy = NULL, *exe = NULL, *module = NULL;
    wchar_t *game = NULL, *user = NULL, *graphics = NULL, *command = NULL;
    PROCESS_INFORMATION process;
    STARTUPINFOW startup;
    DWORD exit_code = 1;
    size_t command_chars;
    (void)backend_context;

    if (!launch || launch->header.abi_version != OPENSPM_ABI_VERSION || launch->header.struct_size < sizeof(*launch)) {
        return OPENSPM_ERROR_ABI_MISMATCH;
    }
    runtime = wide_from_utf8(launch->runtime_root_utf8);
    game = wide_from_utf8(launch->game_root_utf8);
    user = wide_from_utf8(launch->user_root_utf8);
    graphics = wide_from_utf8(launch->graphics_backend_utf8);
    if (!runtime || !game || !user || !graphics || contains_quote(runtime) || contains_quote(game) || contains_quote(user) || contains_quote(graphics)) {
        exit_code = (DWORD)OPENSPM_ERROR_INVALID_ARGUMENT;
        goto cleanup;
    }
    legacy = join_path(runtime, L"legacy");
    exe = legacy ? join_path(legacy, L"moderngekko-run.exe") : NULL;
    module = legacy ? join_path(legacy, L"gR8PE01_recomp.dll") : NULL;
    if (!legacy || !exe || !module || !file_exists(exe) || !file_exists(module)) {
        log_message(OPENSPM_LOG_ERROR, "Legacy M12 payload is incomplete.");
        exit_code = (DWORD)OPENSPM_ERROR_NOT_FOUND;
        goto cleanup;
    }

    if (GetEnvironmentVariableW(L"OPENSPM_NATIVE_HELPERS", NULL, 0) == 0) SetEnvironmentVariableW(L"OPENSPM_NATIVE_HELPERS", L"1");
    SetEnvironmentVariableW(L"M10_MMU", L"1");
    SetEnvironmentVariableW(L"MODERNGEKKO_STATICRECOMP", L"1");
    SetEnvironmentVariableW(L"OPENSPM_BUSYWAIT", L"0");
    SetEnvironmentVariableW(L"OPENSPM_IDLE_PC", L"1");
    SetEnvironmentVariableW(L"MODERNGEKKO_CACHE_AFFINITY", L"0");
    SetEnvironmentVariableW(L"OPENSPM_PERF", L"");
    SetEnvironmentVariableW(L"STATICRECOMP_DISPATCH_SAMPLES", L"");

    CreateDirectoryW(user, NULL);
    command_chars = wcslen(exe) + wcslen(game) + wcslen(user) + wcslen(graphics) + 96;
    command = (wchar_t *)calloc(command_chars, sizeof(wchar_t));
    if (!command) { exit_code = (DWORD)OPENSPM_ERROR_RUNTIME; goto cleanup; }
    _snwprintf_s(command, command_chars, _TRUNCATE,
                 L"\"%ls\" --game \"%ls\" --user-dir \"%ls\" --graphics \"%ls\"",
                 exe, game, user, graphics);

    memset(&startup, 0, sizeof(startup));
    memset(&process, 0, sizeof(process));
    startup.cb = sizeof(startup);
    log_message(OPENSPM_LOG_INFO, "Launching proven M12 runtime through the M13 backend boundary.");
    if (!CreateProcessW(exe, command, NULL, NULL, TRUE, 0, NULL, legacy, &startup, &process)) {
        log_message(OPENSPM_LOG_ERROR, "CreateProcessW failed for legacy runtime.");
        exit_code = (DWORD)OPENSPM_ERROR_LOAD_FAILED;
        goto cleanup;
    }
    WaitForSingleObject(process.hProcess, INFINITE);
    if (!GetExitCodeProcess(process.hProcess, &exit_code)) exit_code = 1;
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);

cleanup:
    free(runtime); free(legacy); free(exe); free(module);
    free(game); free(user); free(graphics); free(command);
    return (int)exit_code;
}

static void OPENSPM_CALL legacy_shutdown(void *backend_context) {
    (void)backend_context;
    g_host = NULL;
}

OPENSPM_EXPORT int OPENSPM_CALL openspm_backend_query(
    uint32_t requested_abi,
    const openspm_host_api *host,
    openspm_backend_api *out_api) {
    if (requested_abi != OPENSPM_ABI_VERSION || !host || !out_api ||
        host->header.abi_version != OPENSPM_ABI_VERSION || host->header.struct_size < sizeof(*host) ||
        out_api->header.struct_size < sizeof(*out_api)) {
        return OPENSPM_ERROR_ABI_MISMATCH;
    }
    g_host = host;
    memset(out_api, 0, sizeof(*out_api));
    out_api->header.struct_size = sizeof(*out_api);
    out_api->header.abi_version = OPENSPM_ABI_VERSION;
    out_api->backend_id = "moderngekko-m12-legacy";
    out_api->backend_version = "13.0-transitional";
    out_api->capabilities = OPENSPM_BACKEND_CAP_LEGACY_PROCESS;
    out_api->run = legacy_run;
    out_api->shutdown = legacy_shutdown;
    return OPENSPM_OK;
}
