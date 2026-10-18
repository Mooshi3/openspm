/*
 * M14 standalone-native backend v6.
 *
 * v5 owns the current persistent Wii boot/platform services. This layer adds
 * OpenSPM's GX/PE bridge so graphics traffic stops disappearing into a generic
 * external-bus fallback. It deliberately does NOT provide a synthetic raster
 * bounding box: production progress now requires renderer-owned feedback.
 *
 * The renderer is also given a bounded guest-memory resolver rather than raw
 * backend globals. That is required for native decoding of CP array bases,
 * display lists, textures and copy destinations while keeping the renderer
 * independent of how the standalone backend allocates MEM1/MEM2.
 */

#pragma push_macro("openspm_backend_query")
#undef openspm_backend_query
#define openspm_backend_query openspm_backend_query_v5_internal
#include "openspm_native_backend_v5.c"
#undef openspm_backend_query
#pragma pop_macro("openspm_backend_query")

#include "openspm/gx_platform.h"
#include "openspm/gx_renderer_service.h"

#define OPENSPM_WII_MEM1_PHYSICAL UINT32_C(0x00000000)
#define OPENSPM_WII_MEM2_PHYSICAL UINT32_C(0x10000000)
#define OPENSPM_WII_VI_CONFIG_FIRST UINT32_C(0xCC002000)
#define OPENSPM_WII_VI_CONFIG_LAST UINT32_C(0xCC002074)
#define OPENSPM_WII_VI_CONFIG_WORDS 59u
#define OPENSPM_WII_VI_DISPLAY_CONFIG UINT32_C(0xCC002002)
#define OPENSPM_WII_VI_DISPLAY_ENABLED UINT16_C(0x0001)
#define OPENSPM_WII_VI_INTERRUPT_FIRST UINT32_C(0xCC002030)
#define OPENSPM_WII_VI_INTERRUPT_LAST UINT32_C(0xCC00203E)
#define OPENSPM_WII_VI_INTERRUPT_WORDS 8u
#define OPENSPM_WII_VI_FILTER_FIRST UINT32_C(0xCC00204C)
#define OPENSPM_WII_VI_FILTER_LAST UINT32_C(0xCC002066)
#define OPENSPM_WII_VI_FILTER_WORDS 14u
#define OPENSPM_WII_VI_DTV_STATUS UINT32_C(0xCC00206E)
#define OPENSPM_WII_VI_DTV_COMPONENT_PRESENT UINT16_C(0x0001)
#define OPENSPM_WII_VI_FB_WIDTH UINT32_C(0xCC002070)

static openspm_gx_platform g_v6_gx;
static openspm_gx_renderer_sink g_v6_bound_renderer;
static uint16_t g_v6_vi_config[OPENSPM_WII_VI_CONFIG_WORDS];
static uint16_t g_v6_vi_interrupt[OPENSPM_WII_VI_INTERRUPT_WORDS];
static uint16_t g_v6_vi_filter[OPENSPM_WII_VI_FILTER_WORDS];
static uint16_t g_v6_vi_fb_width;

static int v6_is_gx_address(uint32_t address) {
    return address == OPENSPM_GX_FIFO_ADDRESS ||
           address == OPENSPM_GX_CP_STATUS ||
           address == OPENSPM_GX_CP_CONTROL ||
           address == OPENSPM_GX_CP_CLEAR ||
           address == OPENSPM_GX_CP_PERF_SELECT ||
           (address >= OPENSPM_GX_CP_FIFO_FIRST &&
            address <= OPENSPM_GX_CP_FIFO_LAST) ||
           address == OPENSPM_GX_PI_FIFO_BASE ||
           address == OPENSPM_GX_PI_FIFO_END ||
           address == OPENSPM_GX_PI_FIFO_WPTR ||
           (address >= OPENSPM_GX_PE_BASE && address < OPENSPM_GX_PE_END);
}

/* Nintendo's VI library owns a shadow register file and flushes dirty
   halfwords from __VIRetraceHandler. In the native port these are bounded
   guest-visible configuration latches only: Aurora owns scanout and the host
   retrace seam owns the simulation clock. Preserve the documented writable
   timing/XFB/latch/scaling/border groups, while beam position, DTV detect,
   unknown AA state and unknown ranges remain fail-closed or semantically
   hosted elsewhere. */
static int v6_vi_config_index(uint32_t address, uint32_t *out_index) {
    uint32_t relative;
    int writable_group;

    if (address < OPENSPM_WII_VI_CONFIG_FIRST ||
        address > OPENSPM_WII_VI_CONFIG_LAST ||
        (address & UINT32_C(1)) != 0u) {
        return 0;
    }

    writable_group =
        (address >= UINT32_C(0xCC002000) && address <= UINT32_C(0xCC00202A)) ||
        (address >= UINT32_C(0xCC002040) && address <= UINT32_C(0xCC00204A)) ||
        address == UINT32_C(0xCC002072) ||
        address == UINT32_C(0xCC002074);
    if (!writable_group) return 0;

    relative = address - OPENSPM_WII_VI_CONFIG_FIRST;
    if (relative / UINT32_C(2) >= OPENSPM_WII_VI_CONFIG_WORDS) return 0;
    if (out_index != NULL) *out_index = relative / UINT32_C(2);
    return 1;
}

static int v6_vi_interrupt_index(uint32_t address, uint32_t *out_index) {
    uint32_t relative;
    if (address < OPENSPM_WII_VI_INTERRUPT_FIRST ||
        address > OPENSPM_WII_VI_INTERRUPT_LAST ||
        (address & UINT32_C(1)) != 0u) {
        return 0;
    }
    relative = address - OPENSPM_WII_VI_INTERRUPT_FIRST;
    if (relative / UINT32_C(2) >= OPENSPM_WII_VI_INTERRUPT_WORDS) return 0;
    if (out_index != NULL) *out_index = relative / UINT32_C(2);
    return 1;
}

static int v6_vi_filter_index(uint32_t address, uint32_t *out_index) {
    uint32_t relative;
    if (address < OPENSPM_WII_VI_FILTER_FIRST ||
        address > OPENSPM_WII_VI_FILTER_LAST ||
        (address & UINT32_C(1)) != 0u) {
        return 0;
    }
    relative = address - OPENSPM_WII_VI_FILTER_FIRST;
    if (relative / UINT32_C(2) >= OPENSPM_WII_VI_FILTER_WORDS) return 0;
    if (out_index != NULL) *out_index = relative / UINT32_C(2);
    return 1;
}

static int v6_try_map_region(uint32_t address,
                             uint32_t min_size,
                             uint32_t base,
                             uint8_t *memory,
                             uint32_t memory_size,
                             openspm_gx_guest_span *out_span) {
    uint64_t offset;
    uint64_t remaining;
    if (memory == NULL || address < base) return 0;
    offset = (uint64_t)address - (uint64_t)base;
    if (offset > memory_size) return 0;
    remaining = (uint64_t)memory_size - offset;
    if ((uint64_t)min_size > remaining) return 0;
    out_span->bytes = memory + (size_t)offset;
    out_span->size = (uint32_t)remaining;
    return 1;
}

static int OPENSPM_CALL v6_map_guest_memory(void *context,
                                            uint32_t guest_address,
                                            uint32_t min_size,
                                            openspm_gx_guest_span *out_span) {
    native_backend_context *backend = (native_backend_context *)context;
    if (backend == NULL || out_span == NULL)
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    out_span->bytes = NULL;
    out_span->size = 0u;

    /* GX register fields generally carry physical addresses, while game code
       uses cached/uncached effective aliases. Accept all three views of each
       RAM bank and resolve them to the same backend-owned host allocation. */
    if (v6_try_map_region(guest_address, min_size,
                          OPENSPM_WII_MEM1_PHYSICAL,
                          backend->mem1, OPENSPM_WII_MEM1_SIZE, out_span) ||
        v6_try_map_region(guest_address, min_size,
                          OPENSPM_WII_MEM1_BASE,
                          backend->mem1, OPENSPM_WII_MEM1_SIZE, out_span) ||
        v6_try_map_region(guest_address, min_size,
                          OPENSPM_WII_MEM1_UNCACHED,
                          backend->mem1, OPENSPM_WII_MEM1_SIZE, out_span) ||
        v6_try_map_region(guest_address, min_size,
                          OPENSPM_WII_MEM2_PHYSICAL,
                          backend->mem2, OPENSPM_WII_MEM2_SIZE, out_span) ||
        v6_try_map_region(guest_address, min_size,
                          OPENSPM_WII_MEM2_BASE,
                          backend->mem2, OPENSPM_WII_MEM2_SIZE, out_span) ||
        v6_try_map_region(guest_address, min_size,
                          OPENSPM_WII_MEM2_UNCACHED,
                          backend->mem2, OPENSPM_WII_MEM2_SIZE, out_span)) {
        return OPENSPM_OK;
    }

    return OPENSPM_ERROR_NOT_FOUND;
}

static int OPENSPM_CALL v6_bind_renderer(void *context,
                                         const openspm_gx_renderer_sink *renderer) {
    (void)context;
    memset(&g_v6_bound_renderer, 0, sizeof(g_v6_bound_renderer));
    if (renderer != NULL) g_v6_bound_renderer = *renderer;
    openspm_gx_platform_set_renderer(&g_v6_gx, renderer);
    return OPENSPM_OK;
}

static int OPENSPM_CALL v6_bus_read(void *context,
                                    uint32_t address,
                                    uint32_t width,
                                    uint64_t *out_value) {
    uint32_t vi_index;
    int result;

    /* Aurora owns PC scanout before the guest CPU starts. Nintendo's VIInit
       samples DCR.ENB first and only runs the low-level Wii timing-register
       bootstrap when that bit is clear. Preserve the guest's remaining DCR
       mode bits but force ENB visible so obsolete console scanout is never the
       authority for host presentation. */
    if (address == OPENSPM_WII_VI_DISPLAY_CONFIG) {
        if (out_value == NULL) return OPENSPM_ERROR_INVALID_ARGUMENT;
        if (width != 2u) return OPENSPM_ERROR_UNSUPPORTED;
        *out_value = (uint16_t)(g_v6_vi_config[1] |
                                OPENSPM_WII_VI_DISPLAY_ENABLED);
        return OPENSPM_OK;
    }

    if (v6_vi_config_index(address, &vi_index)) {
        if (out_value == NULL) return OPENSPM_ERROR_INVALID_ARGUMENT;
        if (width != 2u) return OPENSPM_ERROR_UNSUPPORTED;
        *out_value = g_v6_vi_config[vi_index];
        return OPENSPM_OK;
    }

    /* VIInit clears IR_INT by reading the high half of the pre/post-retrace
       interrupt registers, masking bit 15, and writing the halfword back.
       Preserve all four VI interrupt register pairs as guest-visible latches.
       OpenSPM intentionally does not synthesize raw beam/retrace IRQ status
       here: assertion is driven by the host timing/retrace seam, so the native
       port has one clock rather than a fake second VI clock. */
    if (v6_vi_interrupt_index(address, &vi_index)) {
        if (out_value == NULL) return OPENSPM_ERROR_INVALID_ARGUMENT;
        if (width != 2u) return OPENSPM_ERROR_UNSUPPORTED;
        *out_value = g_v6_vi_interrupt[vi_index];
        return OPENSPM_OK;
    }

    /* R8PE01's VIInit also loads the complete filter-coefficient block and
       framebuffer-width register even when DCR.ENB is already set. They are
       ordinary read/write VI latches; OpenSPM preserves their guest-visible
       state while Aurora remains the actual host scanout owner. */
    if (v6_vi_filter_index(address, &vi_index)) {
        if (out_value == NULL) return OPENSPM_ERROR_INVALID_ARGUMENT;
        if (width != 2u) return OPENSPM_ERROR_UNSUPPORTED;
        *out_value = g_v6_vi_filter[vi_index];
        return OPENSPM_OK;
    }

    /* The retrace handler polls the DTV status register after flushing dirty
       VI shadow state. On Wii, bit 0 reports a component-capable output and
       bit 1 distinguishes NTSC-J. Aurora/Windows is inherently progressive-
       capable and R8PE01 is NTSC-U, so expose the same stable native output
       state as the higher-level hosted VIGetDTVStatus path: 0x0001. This is a
       read-only host capability, not an emulated cable-detect device. */
    if (address == OPENSPM_WII_VI_DTV_STATUS) {
        if (out_value == NULL) return OPENSPM_ERROR_INVALID_ARGUMENT;
        if (width != 2u) return OPENSPM_ERROR_UNSUPPORTED;
        *out_value = OPENSPM_WII_VI_DTV_COMPONENT_PRESENT;
        return OPENSPM_OK;
    }

    if (address == OPENSPM_WII_VI_FB_WIDTH) {
        if (out_value == NULL) return OPENSPM_ERROR_INVALID_ARGUMENT;
        if (width != 2u) return OPENSPM_ERROR_UNSUPPORTED;
        *out_value = g_v6_vi_fb_width;
        return OPENSPM_OK;
    }

    if (v6_is_gx_address(address)) {
        result = openspm_gx_platform_bus_read(&g_v6_gx, address, width, out_value);
        if (result != OPENSPM_ERROR_NOT_FOUND) return result;
    }
    return v5_bus_read(context, address, width, out_value);
}

static int OPENSPM_CALL v6_bus_write(void *context,
                                     uint32_t address,
                                     uint32_t width,
                                     uint64_t value) {
    uint32_t vi_index;
    int result;

    if (v6_vi_config_index(address, &vi_index)) {
        if (width != 2u) return OPENSPM_ERROR_UNSUPPORTED;
        g_v6_vi_config[vi_index] = (uint16_t)value;
        return OPENSPM_OK;
    }
    if (v6_vi_interrupt_index(address, &vi_index)) {
        if (width != 2u) return OPENSPM_ERROR_UNSUPPORTED;
        g_v6_vi_interrupt[vi_index] = (uint16_t)value;
        return OPENSPM_OK;
    }
    if (v6_vi_filter_index(address, &vi_index)) {
        if (width != 2u) return OPENSPM_ERROR_UNSUPPORTED;
        g_v6_vi_filter[vi_index] = (uint16_t)value;
        return OPENSPM_OK;
    }
    if (address == OPENSPM_WII_VI_DTV_STATUS)
        return OPENSPM_ERROR_UNSUPPORTED;
    if (address == OPENSPM_WII_VI_FB_WIDTH) {
        if (width != 2u) return OPENSPM_ERROR_UNSUPPORTED;
        g_v6_vi_fb_width = (uint16_t)value;
        return OPENSPM_OK;
    }

    if (v6_is_gx_address(address)) {
        result = openspm_gx_platform_bus_write(&g_v6_gx, address, width, value);
        if (result != OPENSPM_ERROR_NOT_FOUND) return result;
    }
    return v5_bus_write(context, address, width, value);
}

static int OPENSPM_CALL v6_query_service(void *backend_context,
                                         const char *service_id,
                                         uint32_t requested_version,
                                         void *out_service) {
    if (service_id != NULL &&
        strcmp(service_id, OPENSPM_GX_RENDERER_SERVICE_ID) == 0) {
        openspm_gx_renderer_service *service =
            (openspm_gx_renderer_service *)out_service;
        if (service == NULL ||
            requested_version != OPENSPM_GX_RENDERER_SERVICE_VERSION ||
            service->header.struct_size < sizeof(*service)) {
            return OPENSPM_ERROR_ABI_MISMATCH;
        }
        memset(service, 0, sizeof(*service));
        service->header.struct_size = sizeof(*service);
        service->header.abi_version = OPENSPM_GX_RENDERER_SERVICE_VERSION;
        service->capabilities = OPENSPM_GX_RENDERER_CAP_BIND_SINK;
        service->context = backend_context;
        service->bind = v6_bind_renderer;
        return OPENSPM_OK;
    }

    {
        int result = v5_query_service(backend_context, service_id,
                                      requested_version, out_service);
        if (result != OPENSPM_OK) return result;

        if (service_id != NULL &&
            strcmp(service_id, OPENSPM_WII_CPU_SERVICE_ID) == 0) {
            openspm_wii_cpu_service *service = (openspm_wii_cpu_service *)out_service;
            service->capabilities |= OPENSPM_WII_CPU_CAP_EXTERNAL_BUS;
            service->read_bus = v6_bus_read;
            service->write_bus = v6_bus_write;
        }
        return OPENSPM_OK;
    }
}

static int OPENSPM_CALL v6_run(void *backend_context,
                               const openspm_launch_desc *launch) {
    openspm_gx_guest_memory guest_memory;
    const openspm_gx_renderer_sink *renderer = NULL;

    if (g_v6_bound_renderer.submit_fifo != NULL ||
        g_v6_bound_renderer.read_bounds != NULL) {
        renderer = &g_v6_bound_renderer;
    }

    /* Preserve a renderer that the launcher attached before run. The backend
       still owns the GX platform lifecycle and guest-memory mapping. */
    openspm_gx_platform_reset(&g_v6_gx, renderer);
    memset(g_v6_vi_config, 0, sizeof(g_v6_vi_config));
    memset(g_v6_vi_interrupt, 0, sizeof(g_v6_vi_interrupt));
    memset(g_v6_vi_filter, 0, sizeof(g_v6_vi_filter));
    g_v6_vi_fb_width = 0u;
    memset(&guest_memory, 0, sizeof(guest_memory));
    guest_memory.context = backend_context;
    guest_memory.map = v6_map_guest_memory;
    openspm_gx_platform_set_guest_memory(&g_v6_gx, &guest_memory);
    return v5_run(backend_context, launch);
}

OPENSPM_EXPORT int OPENSPM_CALL openspm_backend_query(
    uint32_t requested_abi,
    const openspm_host_api *host,
    openspm_backend_api *out_api) {
    int result = openspm_backend_query_v5_internal(requested_abi, host, out_api);
    if (result != OPENSPM_OK) return result;
    out_api->backend_version = "14.0-dev7";
    out_api->run = v6_run;
    out_api->query_service = v6_query_service;
    return OPENSPM_OK;
}
