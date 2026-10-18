#include "openspm/backend_api.h"

#include <stdio.h>
#include <string.h>

int OPENSPM_CALL openspm_backend_query(uint32_t requested_abi, const openspm_host_api *host, openspm_backend_api *out_api);

static void OPENSPM_CALL test_log(void *context, openspm_log_level level, const char *message) {
    (void)context; (void)level; (void)message;
}
static uint64_t OPENSPM_CALL test_clock(void *context) { (void)context; return 1; }

int main(void) {
    openspm_host_api host;
    openspm_backend_api backend;
    int rc;
    memset(&host, 0, sizeof(host));
    memset(&backend, 0, sizeof(backend));
    host.header.struct_size = sizeof(host);
    host.header.abi_version = OPENSPM_ABI_VERSION;
    host.log = test_log;
    host.monotonic_ns = test_clock;
    backend.header.struct_size = sizeof(backend);
    backend.header.abi_version = OPENSPM_ABI_VERSION;
    rc = openspm_backend_query(OPENSPM_ABI_VERSION, &host, &backend);
    if (rc != OPENSPM_OK) return 1;
    if (!backend.backend_id || !backend.run) return 2;
    if (!(backend.capabilities & OPENSPM_BACKEND_CAP_LEGACY_PROCESS)) return 3;

    memset(&backend, 0, sizeof(backend));
    backend.header.struct_size = sizeof(backend);
    rc = openspm_backend_query(OPENSPM_ABI_VERSION + 1, &host, &backend);
    if (rc != OPENSPM_ERROR_ABI_MISMATCH) return 4;
    puts("M13 backend negotiation passed");
    return 0;
}
