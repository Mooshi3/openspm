#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include "openspm/backend_api.h"
#include "openspm/gx_platform.h"
#include "openspm/gx_renderer_service.h"
#include "openspm/m14_hostcall_service.h"
#include "openspm/wii_cpu_service.h"

#include <stdio.h>
#include <string.h>

typedef struct fake_renderer {
    uint8_t bytes[16];
    uint32_t size;
} fake_renderer;

static void OPENSPM_CALL test_log(void *context, openspm_log_level level,
                                  const char *message) {
    (void)context;
    (void)level;
    (void)message;
}

static uint64_t OPENSPM_CALL test_clock(void *context) {
    (void)context;
    return 1u;
}

static int OPENSPM_CALL fake_submit(
    void *context,
    const uint8_t *bytes,
    uint32_t size,
    const openspm_gx_guest_memory *guest_memory) {
    fake_renderer *renderer = (fake_renderer *)context;
    (void)guest_memory;
    if (renderer == NULL || bytes == NULL ||
        renderer->size + size > sizeof(renderer->bytes)) {
        return OPENSPM_ERROR_RUNTIME;
    }
    memcpy(renderer->bytes + renderer->size, bytes, size);
    renderer->size += size;
    return OPENSPM_OK;
}

static int OPENSPM_CALL fake_bounds(void *context,
                                    openspm_gx_raster_bounds *out_bounds) {
    (void)context;
    if (out_bounds == NULL) return OPENSPM_ERROR_INVALID_ARGUMENT;
    out_bounds->left = 7u;
    out_bounds->right = 321u;
    out_bounds->top = 9u;
    out_bounds->bottom = 239u;
    out_bounds->valid = 1u;
    return OPENSPM_OK;
}

#define CHECK(expr) \
    do { \
        if (!(expr)) { \
            fprintf(stderr, "CHECK failed: %s at %s:%d\n", \
                    #expr, __FILE__, __LINE__); \
            rc = 20; \
            goto done; \
        } \
    } while (0)

int wmain(int argc, wchar_t **argv) {
    HMODULE module = NULL;
    openspm_backend_query_fn query;
    openspm_host_api host;
    openspm_backend_api backend;
    openspm_gx_renderer_service renderer_service;
    openspm_m14_hostcall_service hostcall_service;
    openspm_m14_hostcall_state hostcall_state;
    openspm_wii_cpu_service cpu;
    openspm_gx_renderer_sink sink;
    fake_renderer renderer;
    uint64_t value = 0u;
    uint32_t i;
    int rc = 0;
    static const uint32_t vi_config_addresses[] = {
        UINT32_C(0xCC002000), UINT32_C(0xCC002002),
        UINT32_C(0xCC002004), UINT32_C(0xCC002006),
        UINT32_C(0xCC002008), UINT32_C(0xCC00200A),
        UINT32_C(0xCC00200C), UINT32_C(0xCC00200E),
        UINT32_C(0xCC002010), UINT32_C(0xCC002012),
        UINT32_C(0xCC002014), UINT32_C(0xCC002016),
        UINT32_C(0xCC002018), UINT32_C(0xCC00201A),
        UINT32_C(0xCC00201C), UINT32_C(0xCC00201E),
        UINT32_C(0xCC002020), UINT32_C(0xCC002022),
        UINT32_C(0xCC002024), UINT32_C(0xCC002026),
        UINT32_C(0xCC002028), UINT32_C(0xCC00202A),
        UINT32_C(0xCC002040), UINT32_C(0xCC002042),
        UINT32_C(0xCC002044), UINT32_C(0xCC002046),
        UINT32_C(0xCC002048), UINT32_C(0xCC00204A),
        UINT32_C(0xCC002072), UINT32_C(0xCC002074)
    };

    if (argc != 2) {
        fprintf(stderr, "Expected exactly one native backend DLL path.\n");
        return 10;
    }

    module = LoadLibraryW(argv[1]);
    if (module == NULL) return 11;
    query = (openspm_backend_query_fn)(void *)GetProcAddress(
        module, OPENSPM_BACKEND_QUERY_SYMBOL);
    if (query == NULL) {
        rc = 12;
        goto done;
    }

    memset(&host, 0, sizeof(host));
    memset(&backend, 0, sizeof(backend));
    host.header.struct_size = sizeof(host);
    host.header.abi_version = OPENSPM_ABI_VERSION;
    host.log = test_log;
    host.monotonic_ns = test_clock;
    backend.header.struct_size = sizeof(backend);
    backend.header.abi_version = OPENSPM_ABI_VERSION;
    CHECK(query(OPENSPM_ABI_VERSION, &host, &backend) == OPENSPM_OK);
    CHECK(backend.query_service != NULL);

    /* v8's current retrace frontier is a physical VI encoder delay. The
       player-local normalizer must route WaitMicroTime to the native hostcall,
       and the backend must make exactly that delay a no-op without fabricating
       Wii time or mutating guest-visible call results. This test is part of the
       ordinary M14-UPDATE-RUN GX/service test set, so local one-click builds
       catch a stale/broken v8 before launching the game. */
    memset(&hostcall_service, 0, sizeof(hostcall_service));
    hostcall_service.header.struct_size = sizeof(hostcall_service);
    hostcall_service.header.abi_version = OPENSPM_M14_HOSTCALL_SERVICE_VERSION;
    CHECK(backend.query_service(backend.backend_context,
                                OPENSPM_M14_HOSTCALL_SERVICE_ID,
                                OPENSPM_M14_HOSTCALL_SERVICE_VERSION,
                                &hostcall_service) == OPENSPM_OK);
    CHECK(hostcall_service.handle != NULL);

    memset(&hostcall_state, 0, sizeof(hostcall_state));
    hostcall_state.header.struct_size = sizeof(hostcall_state);
    hostcall_state.header.abi_version = OPENSPM_ABI_VERSION;
    hostcall_state.gpr[3] = UINT32_C(0x12345678);
    hostcall_state.pc = UINT32_C(0x80282E10);
    hostcall_state.lr = UINT32_C(0x80280ABC);
    hostcall_state.event_target = UINT32_C(0xDEADBEEF);
    hostcall_state.event_argc = 3u;
    hostcall_state.event_args[0] = 1u;
    hostcall_state.event_args[1] = 2u;
    hostcall_state.event_args[2] = 3u;
    CHECK(hostcall_service.handle(hostcall_service.context,
                                  UINT32_C(0x80282E10),
                                  &hostcall_state) == OPENSPM_OK);
    CHECK(hostcall_state.pc == hostcall_state.lr);
    CHECK(hostcall_state.gpr[3] == UINT32_C(0x12345678));
    CHECK(hostcall_state.event_target == 0u);
    CHECK(hostcall_state.event_argc == 0u);
    CHECK(hostcall_state.event_args[0] == 0u &&
          hostcall_state.event_args[1] == 0u &&
          hostcall_state.event_args[2] == 0u);

    memset(&renderer_service, 0, sizeof(renderer_service));
    renderer_service.header.struct_size = sizeof(renderer_service);
    renderer_service.header.abi_version = OPENSPM_GX_RENDERER_SERVICE_VERSION;
    CHECK(backend.query_service(backend.backend_context,
                                OPENSPM_GX_RENDERER_SERVICE_ID,
                                OPENSPM_GX_RENDERER_SERVICE_VERSION,
                                &renderer_service) == OPENSPM_OK);
    CHECK((renderer_service.capabilities & OPENSPM_GX_RENDERER_CAP_BIND_SINK) != 0u);
    CHECK(renderer_service.bind != NULL);

    memset(&renderer, 0, sizeof(renderer));
    memset(&sink, 0, sizeof(sink));
    sink.context = &renderer;
    sink.submit_fifo = fake_submit;
    sink.read_bounds = fake_bounds;
    CHECK(renderer_service.bind(renderer_service.context, &sink) == OPENSPM_OK);

    memset(&cpu, 0, sizeof(cpu));
    cpu.header.struct_size = sizeof(cpu);
    cpu.header.abi_version = OPENSPM_WII_CPU_SERVICE_VERSION;
    CHECK(backend.query_service(backend.backend_context,
                                OPENSPM_WII_CPU_SERVICE_ID,
                                OPENSPM_WII_CPU_SERVICE_VERSION,
                                &cpu) == OPENSPM_OK);
    CHECK(cpu.write_bus != NULL && cpu.read_bus != NULL);

    /* R8PE01 WPAD startup reads Broadway GPIOB_OUT and toggles only bit 8 to
       power the physical Wii sensor bar. The PC input layer has no such device,
       but the recompiled SDK still observes the logical bit. Preserve exactly
       that one bit and keep every unproven GPIO bit/width fail-closed. */
    CHECK(cpu.read_bus(cpu.context, UINT32_C(0xCD0000C0), 4u, &value) == OPENSPM_OK);
    CHECK(value == UINT64_C(0));
    CHECK(cpu.read_bus(cpu.context, UINT32_C(0xCD0000C0), 2u, &value) ==
          OPENSPM_ERROR_UNSUPPORTED);
    CHECK(cpu.write_bus(cpu.context, UINT32_C(0xCD0000C0), 4u,
                        UINT64_C(0x00000100)) == OPENSPM_OK);
    CHECK(cpu.read_bus(cpu.context, UINT32_C(0xCD0000C0), 4u, &value) == OPENSPM_OK);
    CHECK(value == UINT64_C(0x00000100));
    CHECK(cpu.write_bus(cpu.context, UINT32_C(0xCD0000C0), 4u,
                        UINT64_C(0x00000020)) == OPENSPM_ERROR_UNSUPPORTED);
    CHECK(cpu.read_bus(cpu.context, UINT32_C(0xCD0000C0), 4u, &value) == OPENSPM_OK);
    CHECK(value == UINT64_C(0x00000100));
    CHECK(cpu.write_bus(cpu.context, UINT32_C(0xCD0000C0), 4u,
                        UINT64_C(0x00000000)) == OPENSPM_OK);
    CHECK(cpu.read_bus(cpu.context, UINT32_C(0xCD0000C0), 4u, &value) == OPENSPM_OK);
    CHECK(value == UINT64_C(0));

    /* Aurora owns host scanout before the guest starts, so VI DCR.ENB is
       presented as set. Other guest mode bits still round-trip. */
    CHECK(cpu.read_bus(cpu.context, UINT32_C(0xCC002002), 2u, &value) == OPENSPM_OK);
    CHECK(value == UINT64_C(1));
    CHECK(cpu.write_bus(cpu.context, UINT32_C(0xCC002002), 2u,
                        UINT64_C(0x0100)) == OPENSPM_OK);
    CHECK(cpu.read_bus(cpu.context, UINT32_C(0xCC002002), 2u, &value) == OPENSPM_OK);
    CHECK(value == UINT64_C(0x0101));

    /* First native retrace proof reached R8PE01's __VIRetraceHandler dirty VI
       flush at 0x80280874. Preserve the exact observed timing writes rather
       than making the handler bounce out through the generic MMIO fallback. */
    CHECK(cpu.write_bus(cpu.context, UINT32_C(0xCC002000), 2u,
                        UINT64_C(0x0006)) == OPENSPM_OK);
    CHECK(cpu.write_bus(cpu.context, UINT32_C(0xCC002002), 2u,
                        UINT64_C(0x0001)) == OPENSPM_OK);
    CHECK(cpu.write_bus(cpu.context, UINT32_C(0xCC002004), 2u,
                        UINT64_C(0x4769)) == OPENSPM_OK);
    CHECK(cpu.write_bus(cpu.context, UINT32_C(0xCC002006), 2u,
                        UINT64_C(0x01AD)) == OPENSPM_OK);
    CHECK(cpu.write_bus(cpu.context, UINT32_C(0xCC002008), 2u,
                        UINT64_C(0x02FE)) == OPENSPM_OK);
    CHECK(cpu.read_bus(cpu.context, UINT32_C(0xCC002000), 2u, &value) == OPENSPM_OK);
    CHECK(value == UINT64_C(0x0006));
    CHECK(cpu.read_bus(cpu.context, UINT32_C(0xCC002004), 2u, &value) == OPENSPM_OK);
    CHECK(value == UINT64_C(0x4769));
    CHECK(cpu.read_bus(cpu.context, UINT32_C(0xCC002006), 2u, &value) == OPENSPM_OK);
    CHECK(value == UINT64_C(0x01AD));
    CHECK(cpu.read_bus(cpu.context, UINT32_C(0xCC002008), 2u, &value) == OPENSPM_OK);
    CHECK(value == UINT64_C(0x02FE));

    /* Batch the whole documented writable configuration family used by the VI
       shadow-register flush: timing, XFB descriptors, display latches/scaling,
       and border blanking. These remain guest-visible latches only; Aurora and
       the host retrace seam own presentation/timing semantics. */
    for (i = 0; i < (uint32_t)(sizeof(vi_config_addresses) /
                               sizeof(vi_config_addresses[0])); ++i) {
        uint16_t expected = (uint16_t)(UINT16_C(0x0200) + i * UINT16_C(0x11));
        uint64_t read_expected = expected;
        CHECK(cpu.write_bus(cpu.context, vi_config_addresses[i], 2u, expected) == OPENSPM_OK);
        CHECK(cpu.read_bus(cpu.context, vi_config_addresses[i], 2u, &value) == OPENSPM_OK);
        if (vi_config_addresses[i] == UINT32_C(0xCC002002))
            read_expected |= UINT64_C(1);
        CHECK(value == read_expected);
    }
    CHECK(cpu.write_bus(cpu.context, UINT32_C(0xCC002000), 4u,
                        UINT64_C(0x12345678)) == OPENSPM_ERROR_UNSUPPORTED);
    CHECK(cpu.read_bus(cpu.context, UINT32_C(0xCC00202C), 2u, &value) ==
          OPENSPM_ERROR_NOT_FOUND);
    CHECK(cpu.read_bus(cpu.context, UINT32_C(0xCC00202E), 2u, &value) ==
          OPENSPM_ERROR_NOT_FOUND);

    /* VIInit clears IR_INT on the high halves of the pre/post-retrace
       registers by read-mask-write. Preserve the complete four interrupt
       register pairs as 16-bit state; raw IRQ assertion belongs to the host
       retrace seam rather than a second emulated beam clock. */
    for (i = 0; i < 8u; ++i) {
        uint32_t address = UINT32_C(0xCC002030) + i * UINT32_C(2);
        uint16_t expected = (uint16_t)(UINT16_C(0x1000) + i * UINT16_C(0x101));
        CHECK(cpu.read_bus(cpu.context, address, 2u, &value) == OPENSPM_OK);
        CHECK(value == 0u);
        CHECK(cpu.write_bus(cpu.context, address, 2u, expected) == OPENSPM_OK);
        CHECK(cpu.read_bus(cpu.context, address, 2u, &value) == OPENSPM_OK);
        CHECK(value == expected);
    }
    CHECK(cpu.write_bus(cpu.context, UINT32_C(0xCC002030), 2u,
                        UINT64_C(0x9107)) == OPENSPM_OK);
    CHECK(cpu.read_bus(cpu.context, UINT32_C(0xCC002030), 2u, &value) == OPENSPM_OK);
    CHECK(cpu.write_bus(cpu.context, UINT32_C(0xCC002030), 2u,
                        value & UINT64_C(0x7FFF)) == OPENSPM_OK);
    CHECK(cpu.read_bus(cpu.context, UINT32_C(0xCC002030), 2u, &value) == OPENSPM_OK);
    CHECK(value == UINT64_C(0x1107));
    CHECK(cpu.write_bus(cpu.context, UINT32_C(0xCC002030), 4u,
                        UINT64_C(0x12345678)) == OPENSPM_ERROR_UNSUPPORTED);

    for (i = 0; i < 14u; ++i) {
        uint32_t address = UINT32_C(0xCC00204C) + i * UINT32_C(2);
        uint16_t expected = (uint16_t)(UINT16_C(0x1100) + i * UINT16_C(0x31));
        CHECK(cpu.write_bus(cpu.context, address, 2u, expected) == OPENSPM_OK);
        CHECK(cpu.read_bus(cpu.context, address, 2u, &value) == OPENSPM_OK);
        CHECK(value == expected);
    }

    CHECK(cpu.read_bus(cpu.context, UINT32_C(0xCC00206E), 2u, &value) == OPENSPM_OK);
    CHECK(value == UINT64_C(0x0001));
    CHECK(cpu.read_bus(cpu.context, UINT32_C(0xCC00206E), 4u, &value) ==
          OPENSPM_ERROR_UNSUPPORTED);
    CHECK(cpu.write_bus(cpu.context, UINT32_C(0xCC00206E), 2u,
                        UINT64_C(0x0000)) == OPENSPM_ERROR_UNSUPPORTED);

    CHECK(cpu.write_bus(cpu.context, UINT32_C(0xCC002070), 2u,
                        UINT64_C(0x0280)) == OPENSPM_OK);
    CHECK(cpu.read_bus(cpu.context, UINT32_C(0xCC002070), 2u, &value) == OPENSPM_OK);
    CHECK(value == UINT64_C(0x0280));
    CHECK(cpu.write_bus(cpu.context, UINT32_C(0xCC00204C), 4u,
                        UINT64_C(0x12345678)) == OPENSPM_ERROR_UNSUPPORTED);
    CHECK(cpu.read_bus(cpu.context, UINT32_C(0xCC002068), 2u, &value) ==
          OPENSPM_ERROR_NOT_FOUND);

    CHECK(cpu.write_bus(cpu.context, OPENSPM_GX_FIFO_ADDRESS, 1u, 0x61u) == OPENSPM_OK);
    CHECK(cpu.write_bus(cpu.context, OPENSPM_GX_FIFO_ADDRESS, 4u, 0x47001234u) == OPENSPM_OK);
    CHECK(renderer.size == 5u);
    CHECK(renderer.bytes[0] == 0x61u && renderer.bytes[1] == 0x47u &&
          renderer.bytes[3] == 0x12u && renderer.bytes[4] == 0x34u);

    CHECK(cpu.read_bus(cpu.context, OPENSPM_GX_PE_BBOX_RIGHT, 2u, &value) == OPENSPM_OK);
    CHECK(value == 321u);
    CHECK(cpu.read_bus(cpu.context, OPENSPM_GX_PE_TOKEN, 2u, &value) == OPENSPM_OK);
    CHECK(value == 0x1234u);

    CHECK(renderer_service.bind(renderer_service.context, NULL) == OPENSPM_OK);
    CHECK(cpu.read_bus(cpu.context, OPENSPM_GX_PE_BBOX_RIGHT, 2u, &value) ==
          OPENSPM_ERROR_NOT_FOUND);

    puts("m14 gx renderer + v8 hostcall service test passed");

done:
    if (backend.shutdown != NULL) backend.shutdown(backend.backend_context);
    if (module != NULL) FreeLibrary(module);
    return rc;
}
