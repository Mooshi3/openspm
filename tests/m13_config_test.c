#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include "../launcher/config.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>

int wmain(void) {
    wchar_t temp[MAX_PATH];
    wchar_t dir[MAX_PATH];
    wchar_t ini[MAX_PATH];
    openspm_config default_config;
    openspm_config write_config;
    openspm_config read_config;
    DWORD used = GetTempPathW(MAX_PATH, temp);
    if (used == 0 || used >= MAX_PATH) return 10;
    _snwprintf_s(dir, MAX_PATH, _TRUNCATE, L"%lsOpenSPM-M13-config-%lu", temp, GetCurrentProcessId());
    CreateDirectoryW(dir, NULL);

    /* M14's clean-install path is the standalone native backend. Explicit
       configs may still select the old compatibility backend for regression. */
    openspm_config_init(&default_config);
    if (!openspm_config_load(dir, &default_config)) return 24;
    if (!default_config.backend ||
        wcscmp(default_config.backend, L"runtime\\openspm_native_backend.dll") != 0)
        return 25;
    if (!default_config.interpolation || wcscmp(default_config.interpolation, L"auto") != 0)
        return 28;
    if (default_config.interpolation_target_fps != 0)
        return 29;
    openspm_config_free(&default_config);

    openspm_config_init(&write_config);
    openspm_config_init(&read_config);
    write_config.game_root = _wcsdup(L"C:\\Games\\R8PE01");
    write_config.user_root = _wcsdup(L"C:\\Games\\OpenSPMUser");
    write_config.backend = _wcsdup(L"runtime\\moderngekko_backend.dll");
    write_config.graphics = _wcsdup(L"OGL");
    write_config.profile_id = _wcsdup(L"debug");
    write_config.boot_target = _wcsdup(L"bowser_minions");
    write_config.interpolation = _wcsdup(L"on");
    write_config.interpolation_target_fps = 144;
    write_config.mods_enabled = 0;
    if (!write_config.game_root || !write_config.user_root || !write_config.backend || !write_config.graphics ||
        !write_config.profile_id || !write_config.boot_target || !write_config.interpolation) return 11;
    if (!openspm_config_save(dir, &write_config)) return 12;
    if (!openspm_config_set_mod_enabled(dir, L"openspm_debug.dll", 0)) return 13;
    if (!openspm_config_load(dir, &read_config)) return 14;
    if (!read_config.game_root || wcscmp(read_config.game_root, write_config.game_root) != 0) return 15;
    if (!read_config.user_root || wcscmp(read_config.user_root, write_config.user_root) != 0) return 16;
    if (!read_config.backend || wcscmp(read_config.backend, write_config.backend) != 0) return 17;
    if (!read_config.graphics || wcscmp(read_config.graphics, write_config.graphics) != 0) return 18;
    if (!read_config.profile_id || wcscmp(read_config.profile_id, write_config.profile_id) != 0) return 19;
    if (!read_config.boot_target || wcscmp(read_config.boot_target, write_config.boot_target) != 0) return 20;
    if (!read_config.interpolation || wcscmp(read_config.interpolation, write_config.interpolation) != 0) return 26;
    if (read_config.interpolation_target_fps != write_config.interpolation_target_fps) return 27;
    if (read_config.mods_enabled != 0) return 21;
    if (openspm_config_mod_enabled(dir, L"openspm_debug.dll", 1) != 0) return 22;
    if (openspm_config_mod_enabled(dir, L"unknown.dll", 1) != 1) return 23;

    openspm_config_free(&write_config);
    openspm_config_free(&read_config);
    _snwprintf_s(ini, MAX_PATH, _TRUNCATE, L"%ls\\OpenSPM.ini", dir);
    DeleteFileW(ini);
    RemoveDirectoryW(dir);
    return 0;
}
