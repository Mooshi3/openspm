#ifndef OPENSPM_MOD_LOADER_H
#define OPENSPM_MOD_LOADER_H

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stddef.h>

#include "openspm/mod_api.h"
#include "openspm/mod_manifest.h"

typedef struct openspm_loaded_mod {
    HMODULE module;
    openspm_mod_api api;
    wchar_t *path;
    int has_manifest; /* mods\<dll name>.toml was there and checked */
    openspm_mod_manifest manifest;
} openspm_loaded_mod;

typedef struct openspm_mod_set {
    openspm_loaded_mod *items;
    size_t count;
} openspm_mod_set;

typedef int (*openspm_mod_enabled_fn)(void *context, const wchar_t *filename);

int openspm_mods_load(const wchar_t *mods_dir, const openspm_host_api *host, openspm_mod_set *out_set);
int openspm_mods_load_filtered(const wchar_t *mods_dir, const openspm_host_api *host,
                              openspm_mod_enabled_fn is_enabled, void *filter_context,
                              openspm_mod_set *out_set);
void openspm_mods_event(openspm_mod_set *set, openspm_mod_event event_id, const void *event_data);
/* Each loaded mod's new files (mods\<dll name>\files\..., docs/CUSTOM-LEVELS.md):
   lists the folders in OPENSPM_MOD_FILE_ROOTS for the backend, which adds
   the files to the game's file table at boot. Call before backend.run.
   Returns the number of folders. */
size_t openspm_mods_publish_file_roots(const openspm_mod_set *set, const openspm_host_api *host);
void openspm_mods_unload(openspm_mod_set *set);

#endif
