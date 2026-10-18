#ifndef OPENSPM_CONFIG_H
#define OPENSPM_CONFIG_H

#include <wchar.h>

typedef struct openspm_config {
    wchar_t *game_root;
    wchar_t *user_root;
    wchar_t *backend;
    wchar_t *graphics;
    wchar_t *profile_id;
    wchar_t *boot_target;
    wchar_t *interpolation;
    int interpolation_target_fps;
    int mods_enabled;
} openspm_config;

void openspm_config_init(openspm_config *config);
int openspm_config_load(const wchar_t *app_root, openspm_config *config);
int openspm_config_save(const wchar_t *app_root, const openspm_config *config);
int openspm_config_mod_enabled(const wchar_t *app_root, const wchar_t *filename, int default_enabled);
int openspm_config_set_mod_enabled(const wchar_t *app_root, const wchar_t *filename, int enabled);
void openspm_config_free(openspm_config *config);

#endif
