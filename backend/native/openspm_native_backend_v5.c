/*
 * M14 standalone-native backend v5.
 *
 * v4 binds the base Wii platform to standalone MEM1/MEM2. This layer routes
 * the next observed OSInit boundary through the bounded boot-platform model:
 * Hollywood audio PLL setup plus DSP/ARAM bootstrap. Keeping it layered means
 * the proven v3/v4 fallback remains intact while bring-up advances.
 */

#pragma push_macro("openspm_backend_query")
#undef openspm_backend_query
#define openspm_backend_query openspm_backend_query_v4_internal
#include "openspm_native_backend_v4.c"
#undef openspm_backend_query
#pragma pop_macro("openspm_backend_query")

#include "openspm/wii_boot_platform.h"

static openspm_wii_boot_platform g_v5_boot;

static void v5_attach_platform_memory(void *context) {
    native_backend_context *backend = (native_backend_context *)context;
    if (backend == NULL) return;

    openspm_wii_platform_set_memory(&g_v3.platform,
                                    backend->mem1, OPENSPM_WII_MEM1_SIZE,
                                    backend->mem2, OPENSPM_WII_MEM2_SIZE);
    openspm_wii_boot_platform_set_memory(&g_v5_boot,
                                         backend->mem1, OPENSPM_WII_MEM1_SIZE,
                                         backend->mem2, OPENSPM_WII_MEM2_SIZE);
}

static int OPENSPM_CALL v5_bus_read(void *context,
                                    uint32_t address,
                                    uint32_t width,
                                    uint64_t *out_value) {
    v5_attach_platform_memory(context);
    return openspm_wii_boot_platform_bus_read(&g_v5_boot, address, width, out_value);
}

static int OPENSPM_CALL v5_bus_write(void *context,
                                     uint32_t address,
                                     uint32_t width,
                                     uint64_t value) {
    v5_attach_platform_memory(context);
    return openspm_wii_boot_platform_bus_write(&g_v5_boot, address, width, value);
}

static int OPENSPM_CALL v5_query_service(void *backend_context,
                                         const char *service_id,
                                         uint32_t requested_version,
                                         void *out_service) {
    int result = v4_query_service(backend_context, service_id,
                                  requested_version, out_service);
    if (result != OPENSPM_OK) return result;

    if (service_id != NULL &&
        strcmp(service_id, OPENSPM_WII_CPU_SERVICE_ID) == 0) {
        openspm_wii_cpu_service *service = (openspm_wii_cpu_service *)out_service;
        service->read_bus = v5_bus_read;
        service->write_bus = v5_bus_write;
    }
    return OPENSPM_OK;
}

static int OPENSPM_CALL v5_run(void *backend_context,
                               const openspm_launch_desc *launch) {
    openspm_wii_boot_platform_reset(&g_v5_boot, &g_v3.platform);
    return v3_run(backend_context, launch);
}

OPENSPM_EXPORT int OPENSPM_CALL openspm_backend_query(
    uint32_t requested_abi,
    const openspm_host_api *host,
    openspm_backend_api *out_api) {
    int result = openspm_backend_query_v4_internal(requested_abi, host, out_api);
    if (result != OPENSPM_OK) return result;
    out_api->backend_version = "14.0-dev6";
    out_api->run = v5_run;
    out_api->query_service = v5_query_service;
    return OPENSPM_OK;
}