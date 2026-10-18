/* The sampling profiler (opt-in: OPENSPM_PROFILE=1, or OpenSPM.ini [Debug]
 * profile=1). A thread wakes about once a millisecond, suspends the game
 * thread, records its instruction pointer and resumes it; nothing else
 * happens while the game thread is suspended (no allocation, no logging), so
 * a lock it holds cannot deadlock the sampler. Every 30 s, and when the game
 * ends, the sampler logs the window's top functions
 * (include/openspm/sample_profile.h), named from each module's linker map
 * (<module>.map next to the DLL or exe). Costs the game a few percent while
 * on; off, nothing runs. */
#include "sample_profiler.h"

#include "openspm/sample_profile.h"

#include <windows.h>
#include <stdio.h>
#include <wchar.h>

#define PROFILE_WINDOW_MS 30000u
#define PROFILE_TOP 40u
#define PROFILE_MAX_MODULES 32u

typedef struct profile_module {
    HMODULE handle;
    uint64_t base;
    char name[64];
    openspm_profile_map map; /* empty without a .map */
} profile_module;

struct openspm_sample_profiler {
    HANDLE target, thread;
    volatile LONG stop;
    openspm_profile_counts window, total;
    profile_module modules[PROFILE_MAX_MODULES];
    uint32_t module_count;
    uint32_t windows;
};

static void profile_line(void *user, const char *line) {
    (void)user;
    fprintf(stderr, "[OpenSPMNative:info] %s\n", line);
}

static char *read_file(const wchar_t *path) {
    FILE *f = _wfopen(path, L"rb");
    char *text = NULL;
    long size;
    if (f == NULL) return NULL;
    if (fseek(f, 0, SEEK_END) == 0 && (size = ftell(f)) > 0 && fseek(f, 0, SEEK_SET) == 0 &&
        (text = (char *)malloc((size_t)size + 1u)) != NULL) {
        if (fread(text, 1u, (size_t)size, f) != (size_t)size) {
            free(text);
            text = NULL;
        } else {
            text[size] = '\0';
        }
    }
    fclose(f);
    return text;
}

/* The module an address is in, loading its map the first time. */
static profile_module *module_of(openspm_sample_profiler *p, uint64_t address) {
    HMODULE handle = NULL;
    wchar_t path[MAX_PATH], *dot, *slash;
    profile_module *m;
    uint32_t i;
    if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                            (LPCWSTR)(uintptr_t)address, &handle))
        return NULL;
    for (i = 0; i < p->module_count; ++i)
        if (p->modules[i].handle == handle) return &p->modules[i];
    if (p->module_count == PROFILE_MAX_MODULES) return NULL;
    m = &p->modules[p->module_count++];
    m->handle = handle;
    m->base = (uint64_t)(uintptr_t)handle;
    snprintf(m->name, sizeof m->name, "module");
    if (GetModuleFileNameW(handle, path, MAX_PATH) == 0u || path[0] == L'\0') return m;
    slash = wcsrchr(path, L'\\');
    WideCharToMultiByte(CP_UTF8, 0, slash ? slash + 1 : path, -1, m->name, (int)sizeof m->name, NULL, NULL);
    if ((dot = wcsrchr(path, L'.')) != NULL && (slash == NULL || dot > slash) && dot + 4 < path + MAX_PATH) {
        wcscpy(dot, L".map");
        (void)openspm_profile_map_parse(&m->map, read_file(path));
        if (m->map.count) {
            char line[160];
            snprintf(line, sizeof line, "OpenSPM profile: %s has %u named functions (its .map).", m->name,
                     (unsigned)m->map.count);
            profile_line(NULL, line);
        }
    }
    return m;
}

static int resolve(void *user, uint64_t address, openspm_profile_place *out) {
    profile_module *m = module_of((openspm_sample_profiler *)user, address);
    const openspm_profile_symbol *s;
    if (m == NULL) return 0;
    out->module = m->name;
    s = m->map.count ? openspm_profile_map_find(&m->map, address - m->base) : NULL;
    out->symbol = s ? s->name : NULL;
    return 1;
}

static void merge(openspm_profile_counts *into, const openspm_profile_counts *from) {
    uint32_t i, k;
    for (i = 0; i < OPENSPM_PROFILE_SLOTS; ++i)
        for (k = 0; k < from->count[i] && from->address[i]; ++k) openspm_profile_add(into, from->address[i]);
}

static DWORD WINAPI profiler_main(void *arg) {
    openspm_sample_profiler *p = (openspm_sample_profiler *)arg;
    CONTEXT context;
    ULONGLONG window_start = GetTickCount64();
    char title[48];
    SetThreadPriority(GetCurrentThread(), THREAD_PRIORITY_HIGHEST);
    while (!p->stop) {
        Sleep(1);
        if (SuspendThread(p->target) != (DWORD)-1) {
            memset(&context, 0, sizeof context);
            context.ContextFlags = CONTEXT_CONTROL;
            if (GetThreadContext(p->target, &context)) openspm_profile_add(&p->window, context.Rip);
            ResumeThread(p->target);
        }
        if (GetTickCount64() - window_start >= PROFILE_WINDOW_MS) {
            snprintf(title, sizeof title, "window %u (30 s)", (unsigned)++p->windows);
            (void)openspm_profile_report(&p->window, title, PROFILE_TOP, resolve, p, profile_line, NULL);
            merge(&p->total, &p->window);
            openspm_profile_clear(&p->window);
            window_start = GetTickCount64();
        }
    }
    merge(&p->total, &p->window);
    (void)openspm_profile_report(&p->total, "whole run", PROFILE_TOP, resolve, p, profile_line, NULL);
    return 0;
}

openspm_sample_profiler *openspm_sample_profiler_start(void) {
    openspm_sample_profiler *p;
    if (GetEnvironmentVariableW(L"OPENSPM_PROFILE", NULL, 0) == 0u) return NULL;
    {
        wchar_t value[8] = L"";
        GetEnvironmentVariableW(L"OPENSPM_PROFILE", value, 8);
        if (wcscmp(value, L"1") != 0) return NULL;
    }
    if ((p = (openspm_sample_profiler *)calloc(1, sizeof *p)) == NULL) return NULL;
    if (!DuplicateHandle(GetCurrentProcess(), GetCurrentThread(), GetCurrentProcess(), &p->target,
                         THREAD_SUSPEND_RESUME | THREAD_GET_CONTEXT | THREAD_QUERY_INFORMATION, FALSE, 0) ||
        (p->thread = CreateThread(NULL, 0, profiler_main, p, 0, NULL)) == NULL) {
        if (p->target) CloseHandle(p->target);
        free(p);
        profile_line(NULL, "OpenSPM profile: could not start the sampler; off.");
        return NULL;
    }
    profile_line(NULL, "OpenSPM profile: sampling the game thread (about 1000 per second); a report every 30 s "
                       "and at exit.");
    return p;
}

void openspm_sample_profiler_stop(openspm_sample_profiler *p) {
    uint32_t i;
    if (p == NULL) return;
    InterlockedExchange(&p->stop, 1);
    WaitForSingleObject(p->thread, INFINITE);
    CloseHandle(p->thread);
    CloseHandle(p->target);
    for (i = 0; i < p->module_count; ++i) openspm_profile_map_free(&p->modules[i].map);
    free(p);
}
