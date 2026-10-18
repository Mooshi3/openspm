#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include "config.h"

#include <stdlib.h>
#include <string.h>
#include <wchar.h>

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

static wchar_t *read_value(const wchar_t *path, const wchar_t *key) {
    DWORD capacity = 32768;
    wchar_t *buffer = (wchar_t *)calloc(capacity, sizeof(wchar_t));
    DWORD used;
    if (!buffer) return NULL;
    used = GetPrivateProfileStringW(L"OpenSPM", key, L"", buffer, capacity, path);
    if (used == 0) {
        free(buffer);
        return NULL;
    }
    return buffer;
}

void openspm_config_init(openspm_config *config) {
    if (!config) return;
    memset(config, 0, sizeof(*config));
    config->mods_enabled = 1;
}

int openspm_config_load(const wchar_t *app_root, openspm_config *config) {
    wchar_t *path;
    if (!app_root || !config) return 0;
    path = join_path(app_root, L"OpenSPM.ini");
    if (!path) return 0;
    config->game_root = read_value(path, L"game_root");
    config->user_root = read_value(path, L"user_root");
    config->backend = read_value(path, L"backend");
    if (!config->backend)
        config->backend = _wcsdup(L"runtime\\openspm_native_backend.dll");
    config->graphics = read_value(path, L"graphics");
    config->profile_id = read_value(path, L"profile_id");
    config->boot_target = read_value(path, L"boot_target");
    config->interpolation = read_value(path, L"interpolation");
    if (!config->interpolation)
        config->interpolation = _wcsdup(L"auto");
    config->interpolation_target_fps =
        GetPrivateProfileIntW(L"OpenSPM", L"interpolation_target_fps", 0, path);
    if (config->interpolation_target_fps < 0)
        config->interpolation_target_fps = 0;
    config->mods_enabled = GetPrivateProfileIntW(L"OpenSPM", L"mods_enabled", 1, path) != 0;
    free(path);
    return config->backend != NULL;
}

int openspm_config_save(const wchar_t *app_root, const openspm_config *config) {
    wchar_t *path;
    const wchar_t *mods;
    wchar_t interpolation_fps[32];
    int ok = 1;
    if (!app_root || !config || !config->game_root) return 0;
    path = join_path(app_root, L"OpenSPM.ini");
    if (!path) return 0;
    mods = config->mods_enabled ? L"1" : L"0";
    _snwprintf_s(interpolation_fps, _countof(interpolation_fps), _TRUNCATE, L"%d",
                 config->interpolation_target_fps > 0 ? config->interpolation_target_fps : 0);
    ok = ok && WritePrivateProfileStringW(L"OpenSPM", L"game_root", config->game_root, path);
    ok = ok && WritePrivateProfileStringW(L"OpenSPM", L"user_root", config->user_root ? config->user_root : L"", path);
    ok = ok && WritePrivateProfileStringW(L"OpenSPM", L"backend", config->backend ? config->backend : L"", path);
    ok = ok && WritePrivateProfileStringW(L"OpenSPM", L"graphics", config->graphics ? config->graphics : L"OGL", path);
    ok = ok && WritePrivateProfileStringW(L"OpenSPM", L"profile_id", config->profile_id ? config->profile_id : L"default", path);
    ok = ok && WritePrivateProfileStringW(L"OpenSPM", L"boot_target", config->boot_target ? config->boot_target : L"normal", path);
    ok = ok && WritePrivateProfileStringW(L"OpenSPM", L"interpolation",
                                           config->interpolation ? config->interpolation : L"auto", path);
    ok = ok && WritePrivateProfileStringW(L"OpenSPM", L"interpolation_target_fps",
                                           interpolation_fps, path);
    ok = ok && WritePrivateProfileStringW(L"OpenSPM", L"mods_enabled", mods, path);
    free(path);
    return ok;
}

int openspm_config_mod_enabled(const wchar_t *app_root, const wchar_t *filename, int default_enabled) {
    wchar_t *path;
    int result;
    if (!app_root || !filename || !*filename) return default_enabled != 0;
    path = join_path(app_root, L"OpenSPM.ini");
    if (!path) return default_enabled != 0;
    result = GetPrivateProfileIntW(L"Mods", filename, default_enabled ? 1 : 0, path) != 0;
    free(path);
    return result;
}

int openspm_config_set_mod_enabled(const wchar_t *app_root, const wchar_t *filename, int enabled) {
    wchar_t *path;
    int ok;
    if (!app_root || !filename || !*filename) return 0;
    path = join_path(app_root, L"OpenSPM.ini");
    if (!path) return 0;
    ok = WritePrivateProfileStringW(L"Mods", filename, enabled ? L"1" : L"0", path) != 0;
    free(path);
    return ok;
}

void openspm_config_free(openspm_config *config) {
    if (!config) return;
    free(config->game_root);
    free(config->user_root);
    free(config->backend);
    free(config->graphics);
    free(config->profile_id);
    free(config->boot_target);
    free(config->interpolation);
    memset(config, 0, sizeof(*config));
}
