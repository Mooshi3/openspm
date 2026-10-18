/*
 * M14 standalone-native backend v3.
 *
 * v2 owns DOL/disc bootstrap, local-module identity, MEM1/MEM2 and the native
 * execution loop. This layer deliberately composes on top of it while M14 is
 * moving quickly: it adds OpenSPM-owned platform services without copying the
 * v2 implementation or introducing Dolphin/ModernGekko types.
 */

#pragma push_macro("openspm_backend_query")
#undef openspm_backend_query
#define openspm_backend_query openspm_backend_query_v2_internal
#include "openspm_native_backend_v2.c"
#undef openspm_backend_query
#pragma pop_macro("openspm_backend_query")

#include "openspm/fst_overlay.h"
#include "openspm/gamefs_service.h"
#include "openspm/wii_platform.h"

#include <limits.h>

#define OPENSPM_V3_GAMEFS_HANDLES 64u
/* Wii Broadway PVR. Keep this distinct from Gekko/GameCube 0x00083214.
   R8PE01's RVL SDK is allowed to use mfpvr for platform/CPU discrimination;
   reporting the Gekko value here makes a correctly Broadway-compiled module
   look like it booted on GameCube hardware. */
#define OPENSPM_BROADWAY_PVR UINT32_C(0x00087102)

typedef struct openspm_v3_state {
    wchar_t *files_root;
    HANDLE files[OPENSPM_V3_GAMEFS_HANDLES];
    uint32_t spr[1024];
    uint32_t pvr_reported;
    openspm_wii_platform platform;
} openspm_v3_state;

static openspm_v3_state g_v3;

static void v3_reset_handles(void) {
    uint32_t i;
    for (i = 0; i < OPENSPM_V3_GAMEFS_HANDLES; ++i) {
        g_v3.files[i] = INVALID_HANDLE_VALUE;
    }
}

static void v3_close_handles(void) {
    uint32_t i;
    for (i = 0; i < OPENSPM_V3_GAMEFS_HANDLES; ++i) {
        if (g_v3.files[i] != INVALID_HANDLE_VALUE && g_v3.files[i] != NULL) {
            CloseHandle(g_v3.files[i]);
            g_v3.files[i] = INVALID_HANDLE_VALUE;
        }
    }
}

static int v3_path_is_safe(const char *path, const char **out_relative) {
    const char *p;
    const char *component;
    size_t length;

    if (path == NULL || out_relative == NULL) return 0;
    p = path;
    while (*p == '/' || *p == '\\') ++p;
    if (*p == '\0') return 0;
    if (strchr(p, ':') != NULL) return 0;

    component = p;
    for (;;) {
        const char *end = component;
        while (*end != '\0' && *end != '/' && *end != '\\') ++end;
        length = (size_t)(end - component);
        if (length == 0u ||
            (length == 1u && component[0] == '.') ||
            (length == 2u && component[0] == '.' && component[1] == '.')) {
            return 0;
        }
        if (*end == '\0') break;
        component = end + 1;
    }

    *out_relative = p;
    return 1;
}

static wchar_t *v3_gamefs_path(const char *relative_utf8) {
    const char *relative;
    wchar_t *wide = NULL;
    wchar_t *full = NULL;
    int count;
    size_t root_len;
    size_t rel_len;
    size_t i;

    if (g_v3.files_root == NULL ||
        !v3_path_is_safe(relative_utf8, &relative)) {
        return NULL;
    }

    count = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS,
                                relative, -1, NULL, 0);
    if (count <= 0) return NULL;
    wide = (wchar_t *)calloc((size_t)count, sizeof(*wide));
    if (wide == NULL) return NULL;
    if (MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS,
                            relative, -1, wide, count) <= 0) {
        free(wide);
        return NULL;
    }
    for (i = 0; wide[i] != L'\0'; ++i) {
        if (wide[i] == L'/') wide[i] = L'\\';
    }

    root_len = wcslen(g_v3.files_root);
    rel_len = wcslen(wide);
    if (root_len > SIZE_MAX - rel_len - 2u) {
        free(wide);
        return NULL;
    }
    full = (wchar_t *)calloc(root_len + rel_len + 2u, sizeof(*full));
    if (full == NULL) {
        free(wide);
        return NULL;
    }
    memcpy(full, g_v3.files_root, root_len * sizeof(*full));
    if (root_len != 0u && full[root_len - 1u] != L'\\' && full[root_len - 1u] != L'/') {
        full[root_len++] = L'\\';
    }
    memcpy(full + root_len, wide, (rel_len + 1u) * sizeof(*full));
    free(wide);
    return full;
}

/* ---- mod files (include/openspm/fst_overlay.h, docs/CUSTOM-LEVELS.md) ----
   The launcher lists each enabled mod's files folder in OPENSPM_MOD_FILE_ROOTS
   ("|"-separated). At boot every file in them joins the game's file table as
   a new path (paths the game has are refused), and reads of those paths open
   the mod's file. */
typedef struct v3_mod_file {
    char guest[256];   /* "map/os1_01.bin" */
    wchar_t *host;
} v3_mod_file;

static v3_mod_file *g_v3_mod_files;
static uint32_t g_v3_mod_file_count, g_v3_mod_file_capacity;

static int v3_mod_file_add(const char *guest, const wchar_t *host) {
    if (g_v3_mod_file_count == g_v3_mod_file_capacity) {
        const uint32_t cap = g_v3_mod_file_capacity ? g_v3_mod_file_capacity * 2u : 64u;
        v3_mod_file *grown = (v3_mod_file *)realloc(g_v3_mod_files, cap * sizeof *grown);
        if (grown == NULL) return 0;
        g_v3_mod_files = grown;
        g_v3_mod_file_capacity = cap;
    }
    strncpy(g_v3_mod_files[g_v3_mod_file_count].guest, guest, sizeof g_v3_mod_files[0].guest - 1u);
    g_v3_mod_files[g_v3_mod_file_count].guest[sizeof g_v3_mod_files[0].guest - 1u] = 0;
    g_v3_mod_files[g_v3_mod_file_count].host = _wcsdup(host);
    if (g_v3_mod_files[g_v3_mod_file_count].host == NULL) return 0;
    ++g_v3_mod_file_count;
    return 1;
}

static const wchar_t *v3_mod_file_host(const char *guest) {
    uint32_t i;
    while (*guest == '/') ++guest;
    for (i = 0; i < g_v3_mod_file_count; ++i)
        if (_stricmp(g_v3_mod_files[i].guest, guest) == 0) return g_v3_mod_files[i].host;
    return NULL;
}

/* Every file under `dir` (recursively): its guest path relative to `root`. */
static void v3_mod_files_scan(const wchar_t *dir, const char *prefix, openspm_fst_addition **adds, uint32_t *count,
                              uint32_t *capacity, wchar_t ***hosts) {
    WIN32_FIND_DATAW data;
    wchar_t pattern[1024];
    HANDLE find;
    if (_snwprintf_s(pattern, 1024, _TRUNCATE, L"%ls\\*", dir) < 0) return;
    find = FindFirstFileW(pattern, &data);
    if (find == INVALID_HANDLE_VALUE) return;
    do {
        char name8[256], guest[256];
        wchar_t full[1024];
        if (wcscmp(data.cFileName, L".") == 0 || wcscmp(data.cFileName, L"..") == 0) continue;
        if (WideCharToMultiByte(CP_UTF8, 0, data.cFileName, -1, name8, sizeof name8, NULL, NULL) <= 0) continue;
        if (_snprintf_s(guest, sizeof guest, _TRUNCATE, "%s%s%s", prefix, prefix[0] ? "/" : "", name8) < 0) continue;
        if (_snwprintf_s(full, 1024, _TRUNCATE, L"%ls\\%ls", dir, data.cFileName) < 0) continue;
        if (data.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) {
            v3_mod_files_scan(full, guest, adds, count, capacity, hosts);
            continue;
        }
        if (data.nFileSizeHigh != 0u) continue; /* the game's files are 32-bit sized */
        if (*count == *capacity) {
            const uint32_t cap = *capacity ? *capacity * 2u : 64u;
            openspm_fst_addition *a = (openspm_fst_addition *)realloc(*adds, cap * sizeof *a);
            wchar_t **h = (wchar_t **)realloc(*hosts, cap * sizeof *h);
            if (a) *adds = a;
            if (h) *hosts = h;
            if (a == NULL || h == NULL) break;
            *capacity = cap;
        }
        (*adds)[*count].path = _strdup(guest);
        (*adds)[*count].length = data.nFileSizeLow;
        (*adds)[*count].position = 0u;
        (*hosts)[*count] = _wcsdup(full);
        if ((*adds)[*count].path == NULL || (*hosts)[*count] == NULL) break;
        ++*count;
    } while (FindNextFileW(find, &data));
    FindClose(find);
}

static void openspm_mod_files_boot(uint8_t *mem1, uint8_t *mem2) {
    /* The process environment (the launcher sets it after this DLL loaded). */
    wchar_t roots[8192];
    const DWORD roots_len = GetEnvironmentVariableW(L"OPENSPM_MOD_FILE_ROOTS", roots, 8192);
    openspm_fst_addition *adds = NULL;
    wchar_t **hosts = NULL;
    uint32_t count = 0u, capacity = 0u, i, added = 0u, refused = 0u, fst_address, fst_size, out_size = 0u;
    char message[512];
    if (roots_len == 0u || roots_len >= 8192u || mem1 == NULL || mem2 == NULL) return;
    {
        wchar_t *copy = _wcsdup(roots), *next = NULL, *root;
        if (copy == NULL) return;
        for (root = wcstok_s(copy, L"|", &next); root; root = wcstok_s(NULL, L"|", &next))
            v3_mod_files_scan(root, "", &adds, &count, &capacity, &hosts);
        free(copy);
    }
    fst_address = (uint32_t)mem1[0x38] << 24 | (uint32_t)mem1[0x39] << 16 | (uint32_t)mem1[0x3A] << 8 | mem1[0x3B];
    fst_size = (uint32_t)mem1[0x3C] << 24 | (uint32_t)mem1[0x3D] << 16 | (uint32_t)mem1[0x3E] << 8 | mem1[0x3F];
    if (count == 0u) {
        backend_log(OPENSPM_LOG_INFO, "OpenSPM mod files: the mods' files folders are empty.");
    } else if (fst_address < 0x80000000u || fst_address - 0x80000000u + (uint64_t)fst_size > OPENSPM_WII_MEM1_SIZE) {
        backend_log(OPENSPM_LOG_WARN, "OpenSPM mod files: the game's file table is not where expected; no files added.");
    } else {
        uint8_t *out = mem2 + (OPENSPM_FST_OVERLAY_ADDRESS - 0x90000000u);
        const int rc = openspm_fst_extend(mem1 + (fst_address - 0x80000000u), fst_size, adds, count, out,
                                          OPENSPM_FST_OVERLAY_CAPACITY, &out_size);
        if (rc != OPENSPM_OK) {
            _snprintf_s(message, sizeof message, _TRUNCATE,
                        "OpenSPM mod files: could not extend the game's file table (%d); no files added.", rc);
            backend_log(OPENSPM_LOG_WARN, message);
        } else {
            for (i = 0; i < count; ++i) {
                if (adds[i].position == 0u) {
                    ++refused;
                    _snprintf_s(message, sizeof message, _TRUNCATE,
                                "OpenSPM mod files: %s refused (the game already has it, or another mod added it first).",
                                adds[i].path);
                    backend_log(OPENSPM_LOG_WARN, message);
                } else if (v3_mod_file_add(adds[i].path, hosts[i])) {
                    ++added;
                    if (added <= 16u) {
                        _snprintf_s(message, sizeof message, _TRUNCATE, "OpenSPM mod files: + %s (%u bytes)",
                                    adds[i].path, (unsigned)adds[i].length);
                        backend_log(OPENSPM_LOG_INFO, message);
                    }
                }
            }
            /* The game reads its table from here at DVD init. */
            mem1[0x38] = (uint8_t)(OPENSPM_FST_OVERLAY_ADDRESS >> 24);
            mem1[0x39] = (uint8_t)(OPENSPM_FST_OVERLAY_ADDRESS >> 16);
            mem1[0x3A] = (uint8_t)(OPENSPM_FST_OVERLAY_ADDRESS >> 8);
            mem1[0x3B] = (uint8_t)OPENSPM_FST_OVERLAY_ADDRESS;
            mem1[0x3C] = (uint8_t)(out_size >> 24);
            mem1[0x3D] = (uint8_t)(out_size >> 16);
            mem1[0x3E] = (uint8_t)(out_size >> 8);
            mem1[0x3F] = (uint8_t)out_size;
            _snprintf_s(message, sizeof message, _TRUNCATE,
                        "OpenSPM mod files: %u file(s) added to the game's file table, %u refused; table at 0x%08X "
                        "(%u bytes, was %u).",
                        (unsigned)added, (unsigned)refused, (unsigned)OPENSPM_FST_OVERLAY_ADDRESS, (unsigned)out_size,
                        (unsigned)fst_size);
            backend_log(OPENSPM_LOG_INFO, message);
        }
    }
    for (i = 0; i < count; ++i) {
        free((void *)adds[i].path);
        free(hosts[i]);
    }
    free(adds);
    free(hosts);
}

static int OPENSPM_CALL v3_gamefs_open(void *context,
                                       const char *relative_path_utf8,
                                       openspm_gamefs_handle *out_handle,
                                       uint64_t *out_size) {
    wchar_t *path;
    HANDLE file;
    LARGE_INTEGER size;
    uint32_t slot;
    (void)context;

    if (out_handle == NULL || out_size == NULL) return OPENSPM_ERROR_INVALID_ARGUMENT;
    *out_handle = OPENSPM_GAMEFS_INVALID_HANDLE;
    *out_size = 0;
    {
        const wchar_t *mod = relative_path_utf8 ? v3_mod_file_host(relative_path_utf8) : NULL;
        path = mod ? _wcsdup(mod) : v3_gamefs_path(relative_path_utf8);
    }
    if (path == NULL) return OPENSPM_ERROR_INVALID_ARGUMENT;

    file = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING,
                       FILE_ATTRIBUTE_NORMAL | FILE_FLAG_RANDOM_ACCESS, NULL);
    free(path);
    if (file == INVALID_HANDLE_VALUE) return OPENSPM_ERROR_NOT_FOUND;
    if (!GetFileSizeEx(file, &size) || size.QuadPart < 0) {
        CloseHandle(file);
        return OPENSPM_ERROR_LOAD_FAILED;
    }

    for (slot = 0; slot < OPENSPM_V3_GAMEFS_HANDLES; ++slot) {
        if (g_v3.files[slot] == INVALID_HANDLE_VALUE || g_v3.files[slot] == NULL) break;
    }
    if (slot == OPENSPM_V3_GAMEFS_HANDLES) {
        CloseHandle(file);
        return OPENSPM_ERROR_RUNTIME;
    }

    g_v3.files[slot] = file;
    *out_handle = (openspm_gamefs_handle)slot + UINT64_C(1);
    *out_size = (uint64_t)size.QuadPart;
    return OPENSPM_OK;
}

static HANDLE v3_gamefs_resolve(openspm_gamefs_handle handle) {
    uint64_t index;
    if (handle == OPENSPM_GAMEFS_INVALID_HANDLE) return INVALID_HANDLE_VALUE;
    index = handle - UINT64_C(1);
    if (index >= OPENSPM_V3_GAMEFS_HANDLES) return INVALID_HANDLE_VALUE;
    return g_v3.files[(uint32_t)index];
}

static int OPENSPM_CALL v3_gamefs_read(void *context,
                                       openspm_gamefs_handle handle,
                                       uint64_t offset,
                                       void *destination,
                                       uint64_t size,
                                       uint64_t *out_read) {
    HANDLE file = v3_gamefs_resolve(handle);
    LARGE_INTEGER position;
    uint8_t *bytes = (uint8_t *)destination;
    uint64_t total = 0;
    (void)context;

    if (out_read == NULL || (size != 0u && destination == NULL) ||
        file == INVALID_HANDLE_VALUE || file == NULL || offset > INT64_MAX) {
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    }
    *out_read = 0;
    position.QuadPart = (LONGLONG)offset;
    if (!SetFilePointerEx(file, position, NULL, FILE_BEGIN)) return OPENSPM_ERROR_LOAD_FAILED;

    while (total < size) {
        uint64_t remaining = size - total;
        DWORD wanted = remaining > UINT32_MAX ? UINT32_MAX : (DWORD)remaining;
        DWORD got = 0;
        if (!ReadFile(file, bytes + (size_t)total, wanted, &got, NULL)) {
            return OPENSPM_ERROR_LOAD_FAILED;
        }
        total += got;
        if (got < wanted) break;
    }
    *out_read = total;
    return OPENSPM_OK;
}

static int OPENSPM_CALL v3_gamefs_close(void *context,
                                        openspm_gamefs_handle handle) {
    uint64_t index;
    HANDLE file;
    (void)context;
    if (handle == OPENSPM_GAMEFS_INVALID_HANDLE) return OPENSPM_ERROR_INVALID_ARGUMENT;
    index = handle - UINT64_C(1);
    if (index >= OPENSPM_V3_GAMEFS_HANDLES) return OPENSPM_ERROR_INVALID_ARGUMENT;
    file = g_v3.files[(uint32_t)index];
    if (file == INVALID_HANDLE_VALUE || file == NULL) return OPENSPM_ERROR_INVALID_ARGUMENT;
    CloseHandle(file);
    g_v3.files[(uint32_t)index] = INVALID_HANDLE_VALUE;
    return OPENSPM_OK;
}

static int OPENSPM_CALL v3_spr_read(void *context,
                                    uint32_t spr,
                                    uint32_t source_address,
                                    uint32_t *out_value) {
    (void)context;
    (void)source_address;
    if (out_value == NULL || spr >= 1024u) return OPENSPM_ERROR_INVALID_ARGUMENT;
    if (spr == 287u) {
        *out_value = OPENSPM_BROADWAY_PVR;
        if (!g_v3.pvr_reported) {
            backend_log(OPENSPM_LOG_INFO,
                        "M14 Wii CPU identity: Broadway PVR=0x00087102 (Wii mode); Gekko/GameCube PVR is not exposed.");
            g_v3.pvr_reported = 1u;
        }
        return OPENSPM_OK;
    }
    *out_value = g_v3.spr[spr];
    return OPENSPM_OK;
}

static int OPENSPM_CALL v3_spr_write(void *context,
                                     uint32_t spr,
                                     uint32_t source_address,
                                     uint32_t value) {
    (void)context;
    (void)source_address;
    if (spr >= 1024u || spr == 287u) return OPENSPM_ERROR_UNSUPPORTED;
    g_v3.spr[spr] = value;
    return OPENSPM_OK;
}

static int OPENSPM_CALL v3_bus_read(void *context,
                                    uint32_t address,
                                    uint32_t width,
                                    uint64_t *out_value) {
    (void)context;
    return openspm_wii_platform_bus_read(&g_v3.platform, address, width, out_value);
}

static int OPENSPM_CALL v3_bus_write(void *context,
                                     uint32_t address,
                                     uint32_t width,
                                     uint64_t value) {
    (void)context;
    return openspm_wii_platform_bus_write(&g_v3.platform, address, width, value);
}

static int OPENSPM_CALL v3_query_service(void *backend_context,
                                         const char *service_id,
                                         uint32_t requested_version,
                                         void *out_service) {
    if (service_id == NULL || out_service == NULL) return OPENSPM_ERROR_INVALID_ARGUMENT;

    if (strcmp(service_id, OPENSPM_GAMEFS_SERVICE_ID) == 0) {
        openspm_gamefs_service *service = (openspm_gamefs_service *)out_service;
        if (requested_version != OPENSPM_GAMEFS_SERVICE_VERSION ||
            service->header.struct_size < sizeof(*service)) {
            return OPENSPM_ERROR_ABI_MISMATCH;
        }
        memset(service, 0, sizeof(*service));
        service->header.struct_size = sizeof(*service);
        service->header.abi_version = OPENSPM_GAMEFS_SERVICE_VERSION;
        service->capabilities = OPENSPM_GAMEFS_CAP_OPEN_READ |
                                OPENSPM_GAMEFS_CAP_RANDOM_ACCESS |
                                OPENSPM_GAMEFS_CAP_SIZE;
        service->context = &g_v3;
        service->open = v3_gamefs_open;
        service->read = v3_gamefs_read;
        service->close = v3_gamefs_close;
        return OPENSPM_OK;
    }

    if (strcmp(service_id, OPENSPM_WII_CPU_SERVICE_ID) == 0) {
        openspm_wii_cpu_service *service = (openspm_wii_cpu_service *)out_service;
        int result = native_query_service(backend_context, service_id,
                                          requested_version, out_service);
        if (result != OPENSPM_OK) return result;
        service->capabilities |= OPENSPM_WII_CPU_CAP_SPR |
                                 OPENSPM_WII_CPU_CAP_EXTERNAL_BUS;
        service->read_bus = v3_bus_read;
        service->write_bus = v3_bus_write;
        service->read_spr = v3_spr_read;
        service->write_spr = v3_spr_write;
        return OPENSPM_OK;
    }

    return native_query_service(backend_context, service_id,
                                requested_version, out_service);
}

static int OPENSPM_CALL v3_run(void *backend_context,
                               const openspm_launch_desc *launch) {
    wchar_t *game_root = NULL;
    wchar_t *files_root = NULL;
    int result;

    memset(&g_v3, 0, sizeof(g_v3));
    openspm_wii_platform_reset(&g_v3.platform);
    v3_reset_handles();
    if (launch == NULL || launch->game_root_utf8 == NULL) {
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    }
    game_root = wide_from_utf8(launch->game_root_utf8);
    if (game_root == NULL) return OPENSPM_ERROR_INVALID_ARGUMENT;

    {
        const wchar_t suffix[] = L"files";
        size_t root_len = wcslen(game_root);
        size_t suffix_len = (sizeof(suffix) / sizeof(suffix[0])) - 1u;
        files_root = (wchar_t *)calloc(root_len + suffix_len + 2u, sizeof(*files_root));
        if (files_root == NULL) {
            free(game_root);
            return OPENSPM_ERROR_RUNTIME;
        }
        memcpy(files_root, game_root, root_len * sizeof(*files_root));
        if (root_len != 0u && files_root[root_len - 1u] != L'\\' && files_root[root_len - 1u] != L'/') {
            files_root[root_len++] = L'\\';
        }
        memcpy(files_root + root_len, suffix, (suffix_len + 1u) * sizeof(*files_root));
    }
    free(game_root);
    g_v3.files_root = files_root;

    result = native_backend_run(backend_context, launch);

    v3_close_handles();
    free(g_v3.files_root);
    g_v3.files_root = NULL;
    return result;
}

OPENSPM_EXPORT int OPENSPM_CALL openspm_backend_query(
    uint32_t requested_abi,
    const openspm_host_api *host,
    openspm_backend_api *out_api) {
    int result = openspm_backend_query_v2_internal(requested_abi, host, out_api);
    if (result != OPENSPM_OK) return result;
    out_api->backend_version = "14.0-dev4";
    out_api->run = v3_run;
    out_api->query_service = v3_query_service;
    return OPENSPM_OK;
}
