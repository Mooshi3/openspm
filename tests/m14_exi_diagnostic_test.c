#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include "openspm/backend_api.h"
#include "openspm/m14_hostcall_service.h"
#include "openspm/wii_cpu_service.h"

#include <stdint.h>
#include <stdio.h>
#include <string.h>

static char g_last_warning[512];

static void OPENSPM_CALL test_log(void *context, openspm_log_level level,
                                  const char *message) {
    (void)context;
    if (level != OPENSPM_LOG_WARN || message == NULL) return;
    strncpy_s(g_last_warning, sizeof(g_last_warning), message, _TRUNCATE);
}

static uint64_t OPENSPM_CALL test_clock(void *context) {
    (void)context;
    return 1u;
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
    openspm_wii_cpu_service cpu;
    openspm_m14_hostcall_service hostcall;
    openspm_m14_hostcall_state state;
    uint64_t value = 0u;
    int rc = 0;

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
    memset(g_last_warning, 0, sizeof(g_last_warning));
    host.header.struct_size = sizeof(host);
    host.header.abi_version = OPENSPM_ABI_VERSION;
    host.log = test_log;
    host.monotonic_ns = test_clock;
    backend.header.struct_size = sizeof(backend);
    backend.header.abi_version = OPENSPM_ABI_VERSION;
    CHECK(query(OPENSPM_ABI_VERSION, &host, &backend) == OPENSPM_OK);
    CHECK(backend.backend_version != NULL);
    CHECK(strcmp(backend.backend_version, "14.0-dev29") == 0);

    memset(&cpu, 0, sizeof(cpu));
    cpu.header.struct_size = sizeof(cpu);
    cpu.header.abi_version = OPENSPM_WII_CPU_SERVICE_VERSION;
    CHECK(backend.query_service != NULL);
    CHECK(backend.query_service(backend.backend_context,
                                OPENSPM_WII_CPU_SERVICE_ID,
                                OPENSPM_WII_CPU_SERVICE_VERSION,
                                &cpu) == OPENSPM_OK);
    CHECK(cpu.write_bus != NULL && cpu.read_bus != NULL);

    /* dev18 owns only the AI register state observed around AIInit. Control
       uses documented SDK bits 0..6 with bit 3 as W1C status; the two startup
       latches accept the proven zero writes. The physical sample counter is
       deliberately NOT synthesized because __AI_SRC_INIT is hosted instead. */
    CHECK(cpu.write_bus(cpu.context, UINT32_C(0xCD006C04), 4u, 0u) == OPENSPM_OK);
    CHECK(cpu.write_bus(cpu.context, UINT32_C(0xCD006C0C), 4u, 0u) == OPENSPM_OK);
    CHECK(cpu.write_bus(cpu.context, UINT32_C(0xCD006C00), 4u,
                        UINT64_C(0x00000020)) == OPENSPM_OK);
    CHECK(cpu.read_bus(cpu.context, UINT32_C(0xCD006C00), 4u, &value) == OPENSPM_OK);
    CHECK(value == UINT64_C(0x00000020));
    CHECK(cpu.write_bus(cpu.context, UINT32_C(0xCD006C00), 4u,
                        UINT64_C(0x00000060)) == OPENSPM_OK);
    CHECK(cpu.read_bus(cpu.context, UINT32_C(0xCD006C00), 4u, &value) == OPENSPM_OK);
    CHECK(value == UINT64_C(0x00000060));
    CHECK(cpu.write_bus(cpu.context, UINT32_C(0xCD006C00), 4u,
                        UINT64_C(0x00000080)) == OPENSPM_ERROR_UNSUPPORTED);
    CHECK(cpu.read_bus(cpu.context, UINT32_C(0xCD006C08), 4u, &value) ==
          OPENSPM_ERROR_NOT_FOUND);
    CHECK(cpu.write_bus(cpu.context, UINT32_C(0xCD006C04), 4u, 1u) ==
          OPENSPM_ERROR_UNSUPPORTED);
    CHECK(cpu.write_bus(cpu.context, UINT32_C(0xCD006C0C), 4u, 1u) ==
          OPENSPM_ERROR_UNSUPPORTED);

    memset(&hostcall, 0, sizeof(hostcall));
    hostcall.header.struct_size = sizeof(hostcall);
    hostcall.header.abi_version = OPENSPM_M14_HOSTCALL_SERVICE_VERSION;
    CHECK(backend.query_service(backend.backend_context,
                                OPENSPM_M14_HOSTCALL_SERVICE_ID,
                                OPENSPM_M14_HOSTCALL_SERVICE_VERSION,
                                &hostcall) == OPENSPM_OK);
    CHECK(hostcall.handle != NULL);

    /* dev29 preserves dev28/dev27/dev26/dev24's reserved non-guest presentation event
       around the proven dev21 hostcall service. Before backend.run() there is
       no renderer bound, so the event must remain a side-effect-free no-op. */
    memset(&state, 0, sizeof(state));
    state.header.struct_size = sizeof(state);
    state.header.abi_version = OPENSPM_ABI_VERSION;
    state.pc = UINT32_C(0x81230000);
    state.lr = UINT32_C(0x81234500);
    state.gpr[3] = UINT32_C(0xCAFEBABE);
    CHECK(hostcall.handle(hostcall.context,
                          OPENSPM_M14_HOST_EVENT_PRESENT_SAFEPOINT,
                          &state) == OPENSPM_OK);
    CHECK(state.pc == UINT32_C(0x81230000));
    CHECK(state.lr == UINT32_C(0x81234500));
    CHECK(state.gpr[3] == UINT32_C(0xCAFEBABE));
    CHECK(state.event_target == 0u && state.event_argc == 0u);

    /* The exact R8PE01 private SRC-calibration function is the semantic
       boundary. It is void: preserve registers, return through LR, and raise
       no completion event. */
    memset(&state, 0, sizeof(state));
    state.header.struct_size = sizeof(state);
    state.header.abi_version = OPENSPM_ABI_VERSION;
    state.pc = UINT32_C(0x80285328);
    state.lr = UINT32_C(0x81234560);
    state.gpr[3] = UINT32_C(0xDEADBEEF);
    CHECK(hostcall.handle(hostcall.context, UINT32_C(0x80285328), &state) ==
          OPENSPM_OK);
    CHECK(state.pc == UINT32_C(0x81234560));
    CHECK(state.lr == UINT32_C(0x81234560));
    CHECK(state.gpr[3] == UINT32_C(0xDEADBEEF));
    CHECK(state.event_target == 0u && state.event_argc == 0u);

    /* The old backend WPAR query remains as a defensive semantic even though
       the player-local generated module now handles the exact PPC helper
       inline before this service is reached. */
    memset(&state, 0, sizeof(state));
    state.header.struct_size = sizeof(state);
    state.header.abi_version = OPENSPM_ABI_VERSION;
    state.pc = UINT32_C(0x8028986C);
    state.lr = UINT32_C(0x812345E0);
    state.gpr[3] = UINT32_C(0xDEADBEEF);
    CHECK(hostcall.handle(hostcall.context, UINT32_C(0x8028986C), &state) ==
          OPENSPM_OK);
    CHECK(state.pc == UINT32_C(0x812345E0));
    CHECK(state.lr == UINT32_C(0x812345E0));
    CHECK(state.gpr[3] == 1u);
    CHECK(state.event_target == 0u && state.event_argc == 0u);

    /* dev19 lifts only Broadway's physical WPAR/HID2 setup for the GX
       write-gather pipe. OpenSPM already owns the WG FIFO transport; this
       semantic is void and must not perturb guest registers or callbacks. */
    memset(&state, 0, sizeof(state));
    state.header.struct_size = sizeof(state);
    state.header.abi_version = OPENSPM_ABI_VERSION;
    state.pc = UINT32_C(0x8028987C);
    state.lr = UINT32_C(0x81234600);
    state.gpr[3] = UINT32_C(0xA5A5A5A5);
    CHECK(hostcall.handle(hostcall.context, UINT32_C(0x8028987C), &state) ==
          OPENSPM_OK);
    CHECK(state.pc == UINT32_C(0x81234600));
    CHECK(state.lr == UINT32_C(0x81234600));
    CHECK(state.gpr[3] == UINT32_C(0xA5A5A5A5));
    CHECK(state.event_target == 0u && state.event_argc == 0u);

    /* This DLL-level contract runs before backend.run(). The layered Wii boot
       platform is intentionally reset/attached by v5_run, so ordinary EXI CSR
       and DATA staging is not a valid pre-run operation. The dev21 GameFS FST
       resolver likewise requires the player disc bootstrap in guest memory and
       is integration-tested by the strict-native launcher rather than faked
       in this pre-run DLL lifecycle test. */
    CHECK(cpu.write_bus(cpu.context, UINT32_C(0xCD006800), 4u,
                        UINT64_C(0x00000080)) == OPENSPM_ERROR_NOT_FOUND);
    CHECK(cpu.write_bus(cpu.context, UINT32_C(0xCD006810), 4u,
                        UINT64_C(0x12345678)) == OPENSPM_ERROR_NOT_FOUND);

    /* The rejected-EXI diagnostic remains available around the narrowly
       hosted font window. With no live selection/data pre-run, CR=0x35 still
       delegates and fails closed exactly as before. */
    memset(g_last_warning, 0, sizeof(g_last_warning));
    CHECK(cpu.write_bus(cpu.context, UINT32_C(0xCD00680C), 4u,
                        UINT64_C(0x00000035)) == OPENSPM_ERROR_NOT_FOUND);
    CHECK(strstr(g_last_warning, "M14 EXI0 rejected control=0x00000035") != NULL);
    CHECK(strstr(g_last_warning, "csr=0x00000000") != NULL);
    CHECK(strstr(g_last_warning, "data=0x00000000") != NULL);
    CHECK(strstr(g_last_warning, "result=-3") != NULL);

    puts("m14 dev29 wrapper / dev24 inherited GameFS-GX-AI-EXI lifecycle contract passed");

done:
    if (backend.shutdown != NULL) backend.shutdown(backend.backend_context);
    if (module != NULL) FreeLibrary(module);
    return rc;
}
