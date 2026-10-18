#include "openspm/validate.h"
#include "openspm/runtime_service.h"

#include <stddef.h>
#include <stdio.h>
#include <string.h>

#define MEMBER_END(type, member) (offsetof(type, member) + sizeof(((type *)0)->member))

static void OPENSPM_CALL test_log(void *context, openspm_log_level level, const char *message) {
    (void)context; (void)level; (void)message;
}
static uint64_t OPENSPM_CALL test_clock(void *context) { (void)context; return 42; }
static int OPENSPM_CALL test_run(void *context, const openspm_launch_desc *launch) { (void)context; (void)launch; return 0; }

int main(void) {
    openspm_host_api host;
    openspm_launch_desc launch;
    openspm_backend_api backend;
    openspm_runtime_service runtime_service;
    memset(&host, 0, sizeof(host));
    memset(&launch, 0, sizeof(launch));
    memset(&backend, 0, sizeof(backend));
    memset(&runtime_service, 0, sizeof(runtime_service));

    host.header.struct_size = sizeof(host);
    host.header.abi_version = OPENSPM_ABI_VERSION;
    host.log = test_log;
    host.monotonic_ns = test_clock;
    if (openspm_validate_host_api(&host) != OPENSPM_OK) return 1;

    /* ABI-v1 hosts that predate service discovery remain valid. */
    host.header.struct_size = (uint32_t)MEMBER_END(openspm_host_api, profile_end);
    if (openspm_validate_host_api(&host) != OPENSPM_OK) return 2;
    host.header.struct_size = sizeof(host);

    launch.header.struct_size = sizeof(launch);
    launch.header.abi_version = OPENSPM_ABI_VERSION;
    launch.app_root_utf8 = "app";
    launch.runtime_root_utf8 = "runtime";
    launch.game_root_utf8 = "game";
    launch.user_root_utf8 = "user";
    launch.graphics_backend_utf8 = "OGL";
    if (openspm_validate_launch_desc(&launch) != OPENSPM_OK) return 3;

    backend.header.struct_size = sizeof(backend);
    backend.header.abi_version = OPENSPM_ABI_VERSION;
    backend.backend_id = "test";
    backend.backend_version = "1";
    backend.run = test_run;
    if (openspm_validate_backend_api(&backend) != OPENSPM_OK) return 4;

    /* ABI-v1 backends that predate query_service remain valid. */
    backend.header.struct_size = (uint32_t)MEMBER_END(openspm_backend_api, shutdown);
    if (openspm_validate_backend_api(&backend) != OPENSPM_OK) return 5;
    backend.header.struct_size = sizeof(backend);

    runtime_service.header.struct_size = sizeof(runtime_service);
    runtime_service.header.abi_version = OPENSPM_RUNTIME_SERVICE_VERSION;
    if (runtime_service.header.struct_size < sizeof(openspm_abi_header)) return 6;

    backend.header.abi_version = OPENSPM_ABI_VERSION + 1;
    if (openspm_validate_backend_api(&backend) != OPENSPM_ERROR_ABI_MISMATCH) return 7;
    puts("M13 ABI validation passed");
    return 0;
}
