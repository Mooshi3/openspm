#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include "../launcher/mod_loader.h"

#include <stdio.h>
#include <string.h>

static void OPENSPM_CALL test_log(void *context, openspm_log_level level, const char *message) {
    (void)context;
    (void)level;
    if (message) printf("%s\n", message);
}

static uint64_t OPENSPM_CALL test_clock(void *context) {
    (void)context;
    return 123;
}

static int reject_all(void *context, const wchar_t *filename) {
    (void)context;
    (void)filename;
    return 0;
}

int wmain(int argc, wchar_t **argv) {
    openspm_host_api host;
    openspm_mod_set mods;
    int rc;
    if (argc != 2) return 10;
    memset(&host, 0, sizeof(host));
    memset(&mods, 0, sizeof(mods));
    host.header.struct_size = sizeof(host);
    host.header.abi_version = OPENSPM_ABI_VERSION;
    host.log = test_log;
    host.monotonic_ns = test_clock;
    rc = openspm_mods_load(argv[1], &host, &mods);
    if (rc != OPENSPM_OK) return 11;
    if (mods.count != 1) {
        fprintf(stderr, "Expected exactly one OpenSPM mod, got %zu\n", mods.count);
        openspm_mods_unload(&mods);
        return 12;
    }
    if (strcmp(mods.items[0].api.mod_id, "openspm.debug") != 0) {
        openspm_mods_unload(&mods);
        return 13;
    }
    openspm_mods_event(&mods, OPENSPM_MOD_EVENT_STARTUP, NULL);
    openspm_mods_event(&mods, OPENSPM_MOD_EVENT_SHUTDOWN, NULL);
    openspm_mods_unload(&mods);

    rc = openspm_mods_load_filtered(argv[1], &host, reject_all, NULL, &mods);
    if (rc != OPENSPM_OK) return 14;
    if (mods.count != 0) {
        openspm_mods_unload(&mods);
        return 15;
    }
    openspm_mods_unload(&mods);
    return 0;
}
