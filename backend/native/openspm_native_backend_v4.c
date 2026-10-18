/*
 * M14 standalone-native backend v4.
 *
 * v3 owns GameFS, SPRs and the first Wii MMIO model. This layer keeps that
 * implementation intact and adds the one piece EXI DMA needs: binding the
 * platform model to the MEM1/MEM2 buffers already owned by the standalone
 * backend. No guest RAM pointers are added to the public service ABI.
 */

#pragma push_macro("openspm_backend_query")
#undef openspm_backend_query
#define openspm_backend_query openspm_backend_query_v3_internal
#include "openspm_native_backend_v3.c"
#undef openspm_backend_query
#pragma pop_macro("openspm_backend_query")

static void v4_attach_platform_memory(void *context) {
    native_backend_context *backend = (native_backend_context *)context;
    if (backend == NULL) return;
    openspm_wii_platform_set_memory(&g_v3.platform,
                                    backend->mem1, OPENSPM_WII_MEM1_SIZE,
                                    backend->mem2, OPENSPM_WII_MEM2_SIZE);
}

static int OPENSPM_CALL v4_bus_read(void *context,
                                    uint32_t address,
                                    uint32_t width,
                                    uint64_t *out_value) {
    v4_attach_platform_memory(context);
    return openspm_wii_platform_bus_read(&g_v3.platform, address, width, out_value);
}

static int OPENSPM_CALL v4_bus_write(void *context,
                                     uint32_t address,
                                     uint32_t width,
                                     uint64_t value) {
    v4_attach_platform_memory(context);
    return openspm_wii_platform_bus_write(&g_v3.platform, address, width, value);
}

static int OPENSPM_CALL v4_query_service(void *backend_context,
                                         const char *service_id,
                                         uint32_t requested_version,
                                         void *out_service) {
    int result = v3_query_service(backend_context, service_id,
                                  requested_version, out_service);
    if (result != OPENSPM_OK) return result;

    if (service_id != NULL &&
        strcmp(service_id, OPENSPM_WII_CPU_SERVICE_ID) == 0) {
        openspm_wii_cpu_service *service = (openspm_wii_cpu_service *)out_service;
        service->read_bus = v4_bus_read;
        service->write_bus = v4_bus_write;
    }
    return OPENSPM_OK;
}

OPENSPM_EXPORT int OPENSPM_CALL openspm_backend_query(
    uint32_t requested_abi,
    const openspm_host_api *host,
    openspm_backend_api *out_api) {
    int result = openspm_backend_query_v3_internal(requested_abi, host, out_api);
    if (result != OPENSPM_OK) return result;
    out_api->backend_version = "14.0-dev5";
    out_api->query_service = v4_query_service;
    return OPENSPM_OK;
}