#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include "config.h"
#include "gui.h"
#include "mod_loader.h"
#include "renderer_loader.h"
#include "openspm/backend_api.h"
#include "openspm/runtime_service.h"
#include "openspm/validate.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>

typedef struct openspm_host_context {
    openspm_backend_api *backend;
    openspm_mod_set *mods;
    int *mods_started;
} openspm_host_context;

static void OPENSPM_CALL host_log(void *context, openspm_log_level level, const char *message) {
    const char *name = "info";
    (void)context;
    if (level == OPENSPM_LOG_DEBUG) name = "debug";
    else if (level == OPENSPM_LOG_WARN) name = "warn";
    else if (level == OPENSPM_LOG_ERROR) name = "error";
    fprintf(stderr, "[OpenSPM:%s] %s\n", name, message ? message : "");
}

static uint64_t OPENSPM_CALL host_monotonic_ns(void *context) {
    LARGE_INTEGER counter, frequency;
    uint64_t whole_seconds, remainder;
    (void)context;
    if (!QueryPerformanceCounter(&counter) || !QueryPerformanceFrequency(&frequency) || frequency.QuadPart <= 0) return 0;
    whole_seconds = (uint64_t)(counter.QuadPart / frequency.QuadPart);
    remainder = (uint64_t)(counter.QuadPart % frequency.QuadPart);
    return whole_seconds * UINT64_C(1000000000) +
           (remainder * UINT64_C(1000000000)) / (uint64_t)frequency.QuadPart;
}

static void OPENSPM_CALL host_profile_begin(void *context, const char *name) {
    (void)context;
    (void)name;
}

static void OPENSPM_CALL host_profile_end(void *context, const char *name) {
    (void)context;
    (void)name;
}

static int OPENSPM_CALL host_query_service(void *context, const char *service_id,
                                           uint32_t requested_version, void *out_service) {
    openspm_host_context *host_context = (openspm_host_context *)context;
    if (!host_context || !host_context->backend || !host_context->backend->query_service)
        return OPENSPM_ERROR_UNSUPPORTED;
    return host_context->backend->query_service(host_context->backend->backend_context,
                                                service_id, requested_version, out_service);
}

static void OPENSPM_CALL host_runtime_event(void *context, uint32_t event_id, const void *event_data) {
    openspm_host_context *host_context = (openspm_host_context *)context;
    if (!host_context || !host_context->mods || !host_context->mods_started ||
        !*host_context->mods_started)
        return;

    switch ((openspm_runtime_event)event_id) {
        case OPENSPM_RUNTIME_EVENT_CORE_RUNNING:
            openspm_mods_event(host_context->mods, OPENSPM_MOD_EVENT_RUNTIME_RUNNING, event_data);
            break;
        case OPENSPM_RUNTIME_EVENT_PRESENT_END:
            openspm_mods_event(host_context->mods, OPENSPM_MOD_EVENT_PRESENT_END, event_data);
            break;
        case OPENSPM_RUNTIME_EVENT_CORE_STOPPED:
            openspm_mods_event(host_context->mods, OPENSPM_MOD_EVENT_RUNTIME_STOPPED, event_data);
            break;
        default:
            break;
    }
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

static int replace_wide(wchar_t **target, const wchar_t *value) {
    wchar_t *copy = value && *value ? _wcsdup(value) : NULL;
    if (value && *value && !copy) return 0;
    free(*target);
    *target = copy;
    return 1;
}

static wchar_t *application_root(void) {
    DWORD size = 512;
    for (;;) {
        wchar_t *buffer = (wchar_t *)calloc(size, sizeof(wchar_t));
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

static int file_exists(const wchar_t *path) {
    DWORD attrs = GetFileAttributesW(path);
    return attrs != INVALID_FILE_ATTRIBUTES && !(attrs & FILE_ATTRIBUTE_DIRECTORY);
}

static int interpolation_mode_valid(const wchar_t *value) {
    return value != NULL &&
           (_wcsicmp(value, L"auto") == 0 ||
            _wcsicmp(value, L"on") == 0 ||
            _wcsicmp(value, L"off") == 0);
}

static int mod_enabled_from_config(void *context, const wchar_t *filename) {
    return openspm_config_mod_enabled((const wchar_t *)context, filename, 1);
}

static void hide_owned_console_for_gui(void) {
    DWORD processes[2];
    DWORD count = GetConsoleProcessList(processes, (DWORD)_countof(processes));
    HWND console = GetConsoleWindow();
    if (console && count == 1) ShowWindow(console, SW_HIDE);
}

static void usage(void) {
    fwprintf(stderr,
             L"Usage: OpenSPM.exe [--gui] [--launch] [--game <extracted R8PE01 folder>] "
             L"[--user-dir <folder>] [--backend <dll>] [--renderer <dll>] [--graphics OGL] [--profile <id>] "
             L"[--boot-target <id>] [--interpolation auto|on|off] [--interpolation-fps <hz>] "
             L"[--mods|--no-mods]\n");
}

int wmain(int argc, wchar_t **argv) {
    const wchar_t *game_arg = NULL;
    const wchar_t *user_arg = NULL;
    const wchar_t *backend_cli = NULL;
    const wchar_t *renderer_arg = NULL;
    const wchar_t *graphics_arg = NULL;
    const wchar_t *profile_arg = NULL;
    const wchar_t *boot_target_arg = NULL;
    const wchar_t *interpolation_arg = NULL;
    int interpolation_fps_arg = -1;
    const wchar_t *game = NULL;
    const wchar_t *user = NULL;
    const wchar_t *backend_arg = NULL;
    const wchar_t *graphics = NULL;
    const wchar_t *profile = NULL;
    const wchar_t *boot_target = NULL;
    const wchar_t *interpolation = NULL;
    int interpolation_target_fps = 0;
    int force_gui = argc == 1;
    int mods_override = -1;
    int mods_enabled = 1;
    wchar_t *app = NULL, *runtime = NULL, *mods_dir = NULL, *backend_path = NULL, *renderer_path = NULL, *default_user = NULL, *main_dol = NULL;
    char *app8 = NULL, *runtime8 = NULL, *game8 = NULL, *user8 = NULL, *graphics8 = NULL, *profile8 = NULL, *boot_target8 = NULL;
    HMODULE module = NULL;
    openspm_backend_query_fn query;
    openspm_host_api host;
    openspm_backend_api backend;
    openspm_loaded_renderer renderer;
    openspm_launch_desc launch;
    openspm_mod_set mods;
    openspm_config config;
    openspm_host_context host_context;
    int mods_started = 0;
    int rc = OPENSPM_ERROR_RUNTIME;
    int i;

    memset(&backend, 0, sizeof(backend));
    memset(&renderer, 0, sizeof(renderer));
    memset(&mods, 0, sizeof(mods));
    memset(&host_context, 0, sizeof(host_context));
    openspm_config_init(&config);
    for (i = 1; i < argc; ++i) {
        if (wcscmp(argv[i], L"--gui") == 0) force_gui = 1;
        else if (wcscmp(argv[i], L"--launch") == 0) { /* explicit direct-launch mode */ }
        else if (wcscmp(argv[i], L"--game") == 0 && i + 1 < argc) game_arg = argv[++i];
        else if (wcscmp(argv[i], L"--user-dir") == 0 && i + 1 < argc) user_arg = argv[++i];
        else if (wcscmp(argv[i], L"--backend") == 0 && i + 1 < argc) backend_cli = argv[++i];
        else if (wcscmp(argv[i], L"--renderer") == 0 && i + 1 < argc) renderer_arg = argv[++i];
        else if (wcscmp(argv[i], L"--graphics") == 0 && i + 1 < argc) graphics_arg = argv[++i];
        else if (wcscmp(argv[i], L"--profile") == 0 && i + 1 < argc) profile_arg = argv[++i];
        else if (wcscmp(argv[i], L"--boot-target") == 0 && i + 1 < argc) boot_target_arg = argv[++i];
        else if (wcscmp(argv[i], L"--interpolation") == 0 && i + 1 < argc) interpolation_arg = argv[++i];
        else if (wcscmp(argv[i], L"--interpolation-fps") == 0 && i + 1 < argc) {
            wchar_t *end = NULL;
            long parsed = wcstol(argv[++i], &end, 10);
            if (end == argv[i] || *end != L'\0' || parsed < 0 || parsed > 1000) {
                fwprintf(stderr, L"Invalid --interpolation-fps value: %ls\n", argv[i]);
                return 2;
            }
            interpolation_fps_arg = (int)parsed;
        }
        else if (wcscmp(argv[i], L"--mods") == 0) mods_override = 1;
        else if (wcscmp(argv[i], L"--no-mods") == 0) mods_override = 0;
        else if (wcscmp(argv[i], L"--version") == 0) { fwprintf(stdout, L"OpenSPM M13 ABI %u\n", OPENSPM_ABI_VERSION); return 0; }
        else if (wcscmp(argv[i], L"--help") == 0 || wcscmp(argv[i], L"-h") == 0) { usage(); return 0; }
        else { fwprintf(stderr, L"Unknown or incomplete option: %ls\n", argv[i]); usage(); return 2; }
    }

    app = application_root();
    if (!app) { fwprintf(stderr, L"Could not determine OpenSPM application directory.\n"); goto cleanup; }
    if (force_gui) {
        int gui_rc;
        hide_owned_console_for_gui();
        gui_rc = openspm_gui_run(app);
        free(app);
        openspm_config_free(&config);
        return gui_rc;
    }

    openspm_config_load(app, &config);
    game = game_arg ? game_arg : config.game_root;
    user = user_arg ? user_arg : config.user_root;
    backend_arg = backend_cli ? backend_cli : config.backend;
    graphics = graphics_arg ? graphics_arg : (config.graphics ? config.graphics : L"OGL");
    profile = profile_arg ? profile_arg : (config.profile_id ? config.profile_id : L"default");
    boot_target = boot_target_arg ? boot_target_arg : (config.boot_target ? config.boot_target : L"normal");
    interpolation = interpolation_arg ? interpolation_arg :
                    (config.interpolation ? config.interpolation : L"auto");
    if (!interpolation_mode_valid(interpolation)) {
        if (interpolation_arg) {
            fwprintf(stderr, L"Invalid --interpolation value: %ls (expected auto, on, or off)\n",
                     interpolation_arg);
            rc = 2;
            goto cleanup;
        }
        host_log(NULL, OPENSPM_LOG_WARN,
                 "Invalid interpolation setting in OpenSPM.ini; falling back to auto.");
        interpolation = L"auto";
    }
    interpolation_target_fps =
        interpolation_fps_arg >= 0 ? interpolation_fps_arg : config.interpolation_target_fps;
    mods_enabled = mods_override >= 0 ? mods_override : config.mods_enabled;
    if (!game || !*game) {
        fwprintf(stderr, L"No game path is configured yet. Open the GUI or pass --game once.\n");
        usage();
        rc = 2;
        goto cleanup;
    }

    runtime = join_path(app, L"runtime");
    mods_dir = join_path(app, L"mods");
    default_user = join_path(app, L"User-OpenSPM");
    if (!runtime || !mods_dir || !default_user) goto cleanup;
    if (!user || !*user) user = default_user;
    main_dol = join_path(game, L"sys\\main.dol");
    if (!main_dol || !file_exists(main_dol)) {
        fwprintf(stderr, L"No sys\\main.dol found under: %ls\n", game);
        rc = OPENSPM_ERROR_NOT_FOUND;
        goto cleanup;
    }

    if (!replace_wide(&config.game_root, game) ||
        !replace_wide(&config.user_root, user) ||
        !replace_wide(&config.backend, backend_arg && *backend_arg ? backend_arg : NULL) ||
        !replace_wide(&config.graphics, graphics) ||
        !replace_wide(&config.profile_id, profile) ||
        !replace_wide(&config.boot_target, boot_target) ||
        !replace_wide(&config.interpolation, interpolation)) {
        rc = OPENSPM_ERROR_RUNTIME;
        goto cleanup;
    }
    config.interpolation_target_fps = interpolation_target_fps;
    config.mods_enabled = mods_enabled;
    game = config.game_root;
    user = config.user_root;
    backend_arg = config.backend;
    graphics = config.graphics;
    profile = config.profile_id;
    boot_target = config.boot_target;
    interpolation = config.interpolation;
    interpolation_target_fps = config.interpolation_target_fps;
    if (!openspm_config_save(app, &config)) {
        host_log(NULL, OPENSPM_LOG_WARN, "Could not persist OpenSPM.ini; launch will continue.");
    }

    /* Renderer enhancements are persisted in OpenSPM.ini now and exported
       through stable process settings. The future in-game OpenSPM HOME overlay
       will edit the same settings model; live application can be added without
       changing the persisted keys. A target of 0 means follow the display. */
    if (interpolation && *interpolation)
        _wputenv_s(L"OPENSPM_INTERPOLATION", interpolation);
    {
        wchar_t interpolation_fps[32];
        _snwprintf_s(interpolation_fps, _countof(interpolation_fps), _TRUNCATE,
                     L"%d", interpolation_target_fps > 0 ? interpolation_target_fps : 0);
        _wputenv_s(L"OPENSPM_INTERPOLATION_TARGET_FPS", interpolation_fps);
    }

    if (backend_arg && *backend_arg && wcschr(backend_arg, L':') == NULL && backend_arg[0] != L'\\' && backend_arg[0] != L'/')
        backend_path = join_path(app, backend_arg);
    else
        backend_path = (backend_arg && *backend_arg) ? _wcsdup(backend_arg) : join_path(runtime, L"moderngekko_backend.dll");
    if (!backend_path) goto cleanup;
    module = LoadLibraryW(backend_path);
    if (!module) {
        fwprintf(stderr, L"Could not load backend: %ls (Win32 error %lu)\n", backend_path, GetLastError());
        rc = OPENSPM_ERROR_LOAD_FAILED;
        goto cleanup;
    }
    query = (openspm_backend_query_fn)(void *)GetProcAddress(module, OPENSPM_BACKEND_QUERY_SYMBOL);
    if (!query) {
        fprintf(stderr, "Backend does not export %s.\n", OPENSPM_BACKEND_QUERY_SYMBOL);
        rc = OPENSPM_ERROR_ABI_MISMATCH;
        goto cleanup;
    }

    app8 = utf8_from_wide(app);
    runtime8 = utf8_from_wide(runtime);
    game8 = utf8_from_wide(game);
    user8 = utf8_from_wide(user);
    graphics8 = utf8_from_wide(graphics);
    profile8 = utf8_from_wide(profile ? profile : L"default");
    boot_target8 = utf8_from_wide(boot_target ? boot_target : L"normal");
    if (!app8 || !runtime8 || !game8 || !user8 || !graphics8 || !profile8 || !boot_target8) goto cleanup;

    host_context.backend = &backend;
    host_context.mods = &mods;
    host_context.mods_started = &mods_started;

    memset(&host, 0, sizeof(host));
    host.header.struct_size = sizeof(host);
    host.header.abi_version = OPENSPM_ABI_VERSION;
    host.capabilities = OPENSPM_HOST_CAP_PROFILE_MARKERS |
                        OPENSPM_HOST_CAP_SERVICE_QUERY |
                        OPENSPM_HOST_CAP_RUNTIME_EVENTS;
    host.context = &host_context;
    host.log = host_log;
    host.monotonic_ns = host_monotonic_ns;
    host.profile_begin = host_profile_begin;
    host.profile_end = host_profile_end;
    host.query_service = host_query_service;
    host.runtime_event = host_runtime_event;

    backend.header.struct_size = sizeof(backend);
    backend.header.abi_version = OPENSPM_ABI_VERSION;
    rc = query(OPENSPM_ABI_VERSION, &host, &backend);
    if (rc != OPENSPM_OK || openspm_validate_backend_api(&backend) != OPENSPM_OK) {
        fprintf(stderr, "Backend ABI negotiation failed (%d).\n", rc);
        rc = OPENSPM_ERROR_ABI_MISMATCH;
        goto cleanup;
    }

    memset(&launch, 0, sizeof(launch));
    launch.header.struct_size = sizeof(launch);
    launch.header.abi_version = OPENSPM_ABI_VERSION;
    launch.app_root_utf8 = app8;
    launch.runtime_root_utf8 = runtime8;
    launch.game_root_utf8 = game8;
    launch.user_root_utf8 = user8;
    launch.graphics_backend_utf8 = graphics8;
    launch.profile_id_utf8 = profile8;
    launch.boot_target_utf8 = boot_target8;
    launch.flags |= OPENSPM_LAUNCH_FLAG_GAME_ROOT_READ_ONLY;
    if (strcmp(boot_target8, "normal") != 0) launch.flags |= OPENSPM_LAUNCH_FLAG_BOOT_TARGET;
    if (strcmp(profile8, "debug") == 0) launch.flags |= OPENSPM_LAUNCH_FLAG_DEBUG_PROFILE;
    if (openspm_validate_launch_desc(&launch) != OPENSPM_OK) {
        rc = OPENSPM_ERROR_INVALID_ARGUMENT;
        goto cleanup;
    }

    if (renderer_arg && *renderer_arg) {
        if (wcschr(renderer_arg, L':') == NULL && renderer_arg[0] != L'\\' && renderer_arg[0] != L'/')
            renderer_path = join_path(app, renderer_arg);
        else
            renderer_path = _wcsdup(renderer_arg);
        if (!renderer_path) goto cleanup;
        rc = openspm_renderer_load_and_bind(renderer_path, &host, &launch, &backend, &renderer);
        if (rc != OPENSPM_OK) {
            fwprintf(stderr, L"Could not load/bind renderer: %ls (OpenSPM error %d)\n", renderer_path, rc);
            goto cleanup;
        }
    } else {
        renderer_path = join_path(runtime, L"openspm_renderer.dll");
        if (!renderer_path) goto cleanup;
        if (file_exists(renderer_path)) {
            rc = openspm_renderer_load_and_bind(renderer_path, &host, &launch, &backend, &renderer);
            if (rc != OPENSPM_OK) {
                fwprintf(stderr, L"Found native renderer but could not bind it: %ls (OpenSPM error %d)\n", renderer_path, rc);
                goto cleanup;
            }
        }
    }

    if (mods_enabled) {
        rc = openspm_mods_load_filtered(mods_dir, &host, mod_enabled_from_config, app, &mods);
        if (rc != OPENSPM_OK) {
            fprintf(stderr, "Mod discovery failed (%d).\n", rc);
            goto cleanup;
        }
        openspm_mods_event(&mods, OPENSPM_MOD_EVENT_STARTUP, &launch);
        (void)openspm_mods_publish_file_roots(&mods, &host);
        mods_started = 1;
    }

    fprintf(stderr, "OpenSPM M13 shell -> backend %s %s; renderer=%s%s%s; mods=%zu%s; profile=%s; boot=%s\n",
            backend.backend_id, backend.backend_version,
            renderer.module ? renderer.api.renderer_id : "none",
            renderer.module ? " " : "",
            renderer.module ? renderer.api.renderer_version : "",
            mods.count,
            mods_enabled ? "" : " (disabled)", profile8, boot_target8);
    rc = backend.run(backend.backend_context, &launch);

cleanup:
    if (mods_started) openspm_mods_event(&mods, OPENSPM_MOD_EVENT_SHUTDOWN, NULL);
    openspm_mods_unload(&mods);
    openspm_renderer_unload(&renderer);
    if (module) {
        if (backend.shutdown) backend.shutdown(backend.backend_context);
        FreeLibrary(module);
    }
    free(app8); free(runtime8); free(game8); free(user8); free(graphics8); free(profile8); free(boot_target8);
    free(app); free(runtime); free(mods_dir); free(backend_path); free(renderer_path); free(default_user); free(main_dol);
    openspm_config_free(&config);
    return rc < 0 ? 1 : rc;
}