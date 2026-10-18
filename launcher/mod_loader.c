#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include "mod_loader.h"
#include "openspm/validate.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>

static void log_line(const openspm_host_api *host, openspm_log_level level, const char *text) {
    if (host && host->log) host->log(host->context, level, text);
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

static char *utf8_from_wide(const wchar_t *value) {
    int bytes;
    char *out;
    if (!value) return NULL;
    bytes = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, value, -1, NULL, 0, NULL, NULL);
    if (bytes <= 0) return NULL;
    out = (char *)malloc((size_t)bytes);
    if (!out) return NULL;
    if (!WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, value, -1, out, bytes, NULL, NULL)) {
        free(out);
        return NULL;
    }
    return out;
}

static int compare_names(const void *a, const void *b) {
    const wchar_t *const *left = (const wchar_t *const *)a;
    const wchar_t *const *right = (const wchar_t *const *)b;
    return _wcsicmp(*left, *right);
}

static void free_names(wchar_t **names, size_t count) {
    size_t i;
    for (i = 0; i < count; ++i) free(names[i]);
    free(names);
}

static int collect_mod_names(const wchar_t *mods_dir, wchar_t ***out_names, size_t *out_count) {
    wchar_t *pattern = NULL;
    WIN32_FIND_DATAW data;
    HANDLE find = INVALID_HANDLE_VALUE;
    wchar_t **names = NULL;
    size_t count = 0;
    int rc = OPENSPM_OK;

    *out_names = NULL;
    *out_count = 0;
    CreateDirectoryW(mods_dir, NULL);
    pattern = join_path(mods_dir, L"*.dll");
    if (!pattern) return OPENSPM_ERROR_RUNTIME;
    find = FindFirstFileW(pattern, &data);
    free(pattern);
    if (find == INVALID_HANDLE_VALUE) {
        DWORD error = GetLastError();
        return error == ERROR_FILE_NOT_FOUND ? OPENSPM_OK : OPENSPM_ERROR_RUNTIME;
    }
    do {
        wchar_t **grown;
        wchar_t *copy;
        if (data.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) continue;
        copy = _wcsdup(data.cFileName);
        if (!copy) { rc = OPENSPM_ERROR_RUNTIME; break; }
        grown = (wchar_t **)realloc(names, (count + 1) * sizeof(*names));
        if (!grown) { free(copy); rc = OPENSPM_ERROR_RUNTIME; break; }
        names = grown;
        names[count++] = copy;
    } while (FindNextFileW(find, &data));
    FindClose(find);
    if (rc != OPENSPM_OK) {
        free_names(names, count);
        return rc;
    }
    if (count > 1) qsort(names, count, sizeof(*names), compare_names);
    *out_names = names;
    *out_count = count;
    return OPENSPM_OK;
}

static void log_mod_result(const openspm_host_api *host, openspm_log_level level,
                           const char *prefix, const wchar_t *filename,
                           const openspm_mod_api *api) {
    char *name8 = utf8_from_wide(filename);
    char buffer[768];
    if (api && api->mod_id && api->mod_version) {
        _snprintf_s(buffer, sizeof(buffer), _TRUNCATE, "%s %s %s (%s)",
                    prefix, api->mod_id, api->mod_version, name8 ? name8 : "?");
    } else {
        _snprintf_s(buffer, sizeof(buffer), _TRUNCATE, "%s %s", prefix, name8 ? name8 : "?");
    }
    log_line(host, level, buffer);
    free(name8);
}

/* mods\<dll name>.toml (include/openspm/mod_manifest.h). Returns 1 = no
   file (load as before), 2 = checked (manifest filled), 0 = refuse (reason
   in why). */
static int check_manifest(const wchar_t *dll_path, const openspm_host_api *host, const openspm_mod_api *api,
                          openspm_mod_manifest *manifest, char *why, size_t why_size) {
    size_t n = wcslen(dll_path), i;
    wchar_t *path;
    FILE *file = NULL;
    char *text;
    size_t got;
    if (n < 4u || _wcsicmp(dll_path + n - 4u, L".dll") != 0) return 1;
    path = (wchar_t *)calloc(n + 2u, sizeof(wchar_t));
    if (!path) return 1;
    wcscpy_s(path, n + 2u, dll_path);
    wcscpy_s(path + n - 4u, 6u, L".toml");
    if (_wfopen_s(&file, path, L"rb") != 0 || file == NULL) {
        free(path);
        return 1;
    }
    free(path);
    text = (char *)calloc(1, 16385u);
    if (!text) {
        fclose(file);
        _snprintf_s(why, why_size, _TRUNCATE, "out of memory reading its info file");
        return 0;
    }
    got = fread(text, 1, 16384u, file);
    if (!feof(file)) {
        fclose(file);
        free(text);
        _snprintf_s(why, why_size, _TRUNCATE, "its info file is over 16 KB");
        return 0;
    }
    fclose(file);
    text[got] = 0;
    if (!openspm_manifest_parse(text, manifest, why, why_size)) {
        free(text);
        return 0;
    }
    free(text);
    if (strcmp(manifest->id, api->mod_id) != 0) {
        _snprintf_s(why, why_size, _TRUNCATE, "its info file says id \"%s\" but the DLL is \"%s\"", manifest->id,
                    api->mod_id);
        return 0;
    }
    for (i = 0; i < manifest->require_count; ++i) {
        /* Ask for the service at the version the mod needs, into a large
           zeroed buffer (every service struct is far smaller). */
        union {
            openspm_abi_header header;
            unsigned char bytes[2048];
            void *align;
        } probe;
        int rc = OPENSPM_ERROR_UNSUPPORTED;
        memset(&probe, 0, sizeof probe);
        probe.header.struct_size = (uint32_t)sizeof probe;
        probe.header.abi_version = manifest->needs[i].min_version;
        if (host->header.struct_size >= offsetof(openspm_host_api, query_service) + sizeof(host->query_service) &&
            host->query_service)
            rc = host->query_service(host->context, manifest->needs[i].service, manifest->needs[i].min_version,
                                     &probe);
        if (rc != OPENSPM_OK) {
            _snprintf_s(why, why_size, _TRUNCATE, "it needs %s version %u, which this OpenSPM does not have",
                        manifest->needs[i].service, (unsigned)manifest->needs[i].min_version);
            return 0;
        }
    }
    return 2;
}

int openspm_mods_load_filtered(const wchar_t *mods_dir, const openspm_host_api *host,
                              openspm_mod_enabled_fn is_enabled, void *filter_context,
                              openspm_mod_set *out_set) {
    openspm_mod_manifest manifest;
    wchar_t **names = NULL;
    size_t name_count = 0, i;
    int rc;
    if (!mods_dir || !host || !out_set) return OPENSPM_ERROR_INVALID_ARGUMENT;
    memset(out_set, 0, sizeof(*out_set));
    rc = collect_mod_names(mods_dir, &names, &name_count);
    if (rc != OPENSPM_OK) return rc;

    for (i = 0; i < name_count; ++i) {
        wchar_t *path;
        HMODULE module;
        openspm_mod_query_fn query;
        openspm_mod_api api;
        openspm_loaded_mod *grown;
        int query_rc, has_manifest = 0;
        if (is_enabled && !is_enabled(filter_context, names[i])) {
            log_mod_result(host, OPENSPM_LOG_INFO, "Skipped disabled mod", names[i], NULL);
            continue;
        }
        path = join_path(mods_dir, names[i]);
        if (!path) continue;
        module = LoadLibraryW(path);
        if (!module) {
            log_mod_result(host, OPENSPM_LOG_WARN, "Could not load mod DLL:", names[i], NULL);
            free(path);
            continue;
        }
        query = (openspm_mod_query_fn)(void *)GetProcAddress(module, OPENSPM_MOD_QUERY_SYMBOL);
        if (!query) {
            log_mod_result(host, OPENSPM_LOG_WARN, "Ignoring DLL without openspm_mod_query:", names[i], NULL);
            FreeLibrary(module);
            free(path);
            continue;
        }
        memset(&api, 0, sizeof(api));
        api.header.struct_size = sizeof(api);
        api.header.abi_version = OPENSPM_ABI_VERSION;
        query_rc = query(OPENSPM_ABI_VERSION, host, &api);
        if (query_rc != OPENSPM_OK || openspm_validate_mod_api(&api) != OPENSPM_OK) {
            log_mod_result(host, OPENSPM_LOG_WARN, "Rejected incompatible mod:", names[i], NULL);
            FreeLibrary(module);
            free(path);
            continue;
        }
        {
            char why[256];
            const int checked = check_manifest(path, host, &api, &manifest, why, sizeof why);
            if (checked == 0) {
                char *name8 = utf8_from_wide(names[i]);
                char line[512];
                _snprintf_s(line, sizeof line, _TRUNCATE, "Refused mod %s: %s.", name8 ? name8 : "?", why);
                log_line(host, OPENSPM_LOG_WARN, line);
                free(name8);
                FreeLibrary(module);
                free(path);
                continue;
            }
            has_manifest = checked == 2;
        }
        grown = (openspm_loaded_mod *)realloc(out_set->items, (out_set->count + 1) * sizeof(*out_set->items));
        if (!grown) {
            FreeLibrary(module);
            free(path);
            free_names(names, name_count);
            openspm_mods_unload(out_set);
            return OPENSPM_ERROR_RUNTIME;
        }
        out_set->items = grown;
        out_set->items[out_set->count].module = module;
        out_set->items[out_set->count].api = api;
        out_set->items[out_set->count].path = path;
        out_set->items[out_set->count].has_manifest = has_manifest;
        if (has_manifest) out_set->items[out_set->count].manifest = manifest;
        else memset(&out_set->items[out_set->count].manifest, 0, sizeof manifest);
        ++out_set->count;
        log_mod_result(host, OPENSPM_LOG_INFO, has_manifest ? "Loaded mod" : "Loaded mod (no info file)", names[i], &api);
    }
    free_names(names, name_count);
    return OPENSPM_OK;
}

int openspm_mods_load(const wchar_t *mods_dir, const openspm_host_api *host, openspm_mod_set *out_set) {
    return openspm_mods_load_filtered(mods_dir, host, NULL, NULL, out_set);
}

void openspm_mods_event(openspm_mod_set *set, openspm_mod_event event_id, const void *event_data) {
    size_t i;
    if (!set) return;
    for (i = 0; i < set->count; ++i) {
        if (set->items[i].api.on_event) {
            set->items[i].api.on_event(set->items[i].api.mod_context, event_id, event_data);
        }
    }
}

size_t openspm_mods_publish_file_roots(const openspm_mod_set *set, const openspm_host_api *host) {
    wchar_t *list = NULL;
    size_t used = 0, found = 0, i;
    if (!set) return 0;
    for (i = 0; i < set->count; ++i) {
        const wchar_t *dll = set->items[i].path;
        size_t n = dll ? wcslen(dll) : 0;
        wchar_t *dir;
        DWORD attributes;
        if (n < 4u || _wcsicmp(dll + n - 4u, L".dll") != 0) continue;
        dir = (wchar_t *)calloc(n + 8u, sizeof *dir); /* "<path without .dll>\files" */
        if (!dir) continue;
        wcsncpy_s(dir, n + 8u, dll, n - 4u);
        wcscat_s(dir, n + 8u, L"\\files");
        attributes = GetFileAttributesW(dir);
        if (attributes != INVALID_FILE_ATTRIBUTES && (attributes & FILE_ATTRIBUTE_DIRECTORY)) {
            const size_t len = wcslen(dir);
            wchar_t *grown = (wchar_t *)realloc(list, (used + len + 2u) * sizeof *grown);
            if (grown) {
                list = grown;
                if (used) list[used++] = L'|'; /* not a path character on Windows */
                wcscpy_s(list + used, len + 1u, dir);
                used += len;
                ++found;
                {
                    char *dir8 = utf8_from_wide(dir);
                    char line[600];
                    _snprintf_s(line, sizeof line, _TRUNCATE, "Mod files folder: %s", dir8 ? dir8 : "?");
                    if (host) log_line(host, OPENSPM_LOG_INFO, line);
                    free(dir8);
                }
            }
        }
        free(dir);
    }
    SetEnvironmentVariableW(L"OPENSPM_MOD_FILE_ROOTS", found ? list : NULL); /* the backend reads the process environment */
    free(list);
    return found;
}

void openspm_mods_unload(openspm_mod_set *set) {
    size_t i;
    if (!set) return;
    for (i = set->count; i > 0; --i) {
        openspm_loaded_mod *mod = &set->items[i - 1];
        if (mod->module) FreeLibrary(mod->module);
        free(mod->path);
    }
    free(set->items);
    memset(set, 0, sizeof(*set));
}
