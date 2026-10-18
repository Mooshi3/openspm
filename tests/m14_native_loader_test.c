#include "openspm/native_loader.h"

#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef struct loader_test_state {
    uint64_t account_calls;
    uint64_t accounted_cycles;
    uint64_t poll_calls;
} loader_test_state;

static void OPENSPM_CALL test_log(void *context, openspm_log_level level, const char *message) {
    (void)context;
    (void)level;
    (void)message;
}

static uint64_t OPENSPM_CALL test_monotonic_ns(void *context) {
    (void)context;
    return 1u;
}

static int OPENSPM_CALL test_account_cycles(void *context, uint64_t guest_cycles) {
    loader_test_state *state = (loader_test_state *)context;
    state->account_calls++;
    state->accounted_cycles += guest_cycles;
    return OPENSPM_OK;
}

static int OPENSPM_CALL test_poll_control(void *context, uint64_t *out_control_flags) {
    loader_test_state *state = (loader_test_state *)context;
    state->poll_calls++;
    if (out_control_flags == NULL) {
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    }
    *out_control_flags = OPENSPM_NATIVE_CONTROL_NONE;
    return OPENSPM_OK;
}

static int expect(int condition, const char *message) {
    if (!condition) {
        fprintf(stderr, "m14_native_loader_test: %s\n", message);
        return 0;
    }
    return 1;
}

static void init_launch(openspm_launch_desc *launch) {
    memset(launch, 0, sizeof(*launch));
    launch->header.struct_size = sizeof(*launch);
    launch->header.abi_version = OPENSPM_ABI_VERSION;
    launch->app_root_utf8 = ".";
    launch->runtime_root_utf8 = ".";
    launch->game_root_utf8 = ".";
    launch->user_root_utf8 = ".";
    launch->graphics_backend_utf8 = "test";
    launch->flags = OPENSPM_LAUNCH_FLAG_GAME_ROOT_READ_ONLY;
}

static void init_runtime(
    openspm_host_api *host,
    openspm_native_runtime_api *runtime,
    loader_test_state *state,
    uint32_t *mem1,
    size_t mem1_size,
    uint32_t *mem2,
    size_t mem2_size) {
    memset(host, 0, sizeof(*host));
    host->header.struct_size = sizeof(*host);
    host->header.abi_version = OPENSPM_ABI_VERSION;
    host->context = state;
    host->log = test_log;
    host->monotonic_ns = test_monotonic_ns;

    memset(runtime, 0, sizeof(*runtime));
    runtime->header.struct_size = sizeof(*runtime);
    runtime->header.abi_version = OPENSPM_NATIVE_ABI_VERSION;
    runtime->capabilities = OPENSPM_NATIVE_HOST_CAP_DIRECT_MEMORY |
                            OPENSPM_NATIVE_HOST_CAP_CYCLE_ACCOUNTING |
                            OPENSPM_NATIVE_HOST_CAP_CONTROL_POLL;
    runtime->context = state;
    runtime->host = host;
    runtime->mem1 = (uint8_t *)mem1;
    runtime->mem1_size = mem1_size;
    runtime->mem2 = (uint8_t *)mem2;
    runtime->mem2_size = mem2_size;
    runtime->account_cycles = test_account_cycles;
    runtime->poll_control = test_poll_control;
}

int main(int argc, char **argv) {
    static const uint8_t expected_hash[32] = {
        0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07,
        0x08, 0x09, 0x0a, 0x0b, 0x0c, 0x0d, 0x0e, 0x0f,
        0x10, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17,
        0x18, 0x19, 0x1a, 0x1b, 0x1c, 0x1d, 0x1e, 0x1f
    };
    uint8_t wrong_hash[32];
    loader_test_state state;
    openspm_host_api host;
    openspm_native_runtime_api runtime;
    openspm_launch_desc launch;
    openspm_native_library library;
    openspm_native_session session;
    openspm_native_exit exit_info;
    uint32_t mem1[64];
    uint32_t mem2[64];
    int result;

    if (argc != 2) {
        fprintf(stderr, "usage: m14_native_loader_test <synthetic-module>\n");
        return 64;
    }

    memset(&state, 0, sizeof(state));
    memset(&library, 0, sizeof(library));
    memset(&session, 0, sizeof(session));
    memset(&exit_info, 0, sizeof(exit_info));
    memset(mem1, 0, sizeof(mem1));
    memset(mem2, 0, sizeof(mem2));
    init_launch(&launch);
    init_runtime(&host, &runtime, &state, mem1, sizeof(mem1), mem2, sizeof(mem2));

    result = openspm_native_library_open(argv[1], "TEST000", expected_hash, &library);
    if (!expect(result == OPENSPM_OK, "could not load valid synthetic native module") ||
        !expect(strcmp(library.module.module_id, "openspm.synthetic.dynamic") == 0,
                "loaded module metadata is wrong")) {
        return 1;
    }

    result = openspm_native_session_open(&library.module, &runtime, &launch, &session);
    if (!expect(result == OPENSPM_OK, "could not create native session")) {
        openspm_native_library_close(&library);
        return 2;
    }

    result = openspm_native_session_run(&session, 1u, &exit_info);
    if (!expect(result == OPENSPM_OK && exit_info.reason == OPENSPM_NATIVE_EXIT_SAFEPOINT,
                "SAFEPOINT exit failed") ||
        !expect(mem1[0] == 1024u, "direct native DLL call chain did not mutate MEM1") ||
        !expect(state.account_calls == 1u && state.accounted_cycles == 1024u,
                "native DLL did not batch cycle accounting") ||
        !expect(state.poll_calls == 1u, "native DLL did not poll once at safepoint")) {
        openspm_native_session_close(&session);
        openspm_native_library_close(&library);
        return 3;
    }

    result = openspm_native_session_run(&session, 2u, &exit_info);
    if (!expect(result == OPENSPM_OK &&
                    exit_info.reason == OPENSPM_NATIVE_EXIT_FALLBACK_REQUIRED &&
                    exit_info.source_address == UINT64_C(0x80001234),
                "FALLBACK_REQUIRED exit failed")) {
        openspm_native_session_close(&session);
        openspm_native_library_close(&library);
        return 4;
    }

    result = openspm_native_session_request_stop(&session);
    if (!expect(result == OPENSPM_OK, "request_stop failed")) {
        openspm_native_session_close(&session);
        openspm_native_library_close(&library);
        return 5;
    }
    result = openspm_native_session_run(&session, 1u, &exit_info);
    if (!expect(result == OPENSPM_OK && exit_info.reason == OPENSPM_NATIVE_EXIT_STOPPED,
                "STOPPED exit failed")) {
        openspm_native_session_close(&session);
        openspm_native_library_close(&library);
        return 6;
    }
    openspm_native_session_close(&session);

    /* New session for FAULT, because a fault intentionally makes the session inactive. */
    result = openspm_native_session_open(&library.module, &runtime, &launch, &session);
    if (!expect(result == OPENSPM_OK, "second native session open failed")) {
        openspm_native_library_close(&library);
        return 7;
    }
    result = openspm_native_session_run(&session, UINT64_C(0xF00D), &exit_info);
    if (!expect(result == OPENSPM_OK && exit_info.reason == OPENSPM_NATIVE_EXIT_FAULT &&
                    session.active == 0u,
                "FAULT exit failed")) {
        openspm_native_session_close(&session);
        openspm_native_library_close(&library);
        return 8;
    }
    openspm_native_session_close(&session);
    openspm_native_library_close(&library);

    /* Source identity is mandatory for production. A mismatched locally generated
       module must be rejected before a session is created. */
    memcpy(wrong_hash, expected_hash, sizeof(wrong_hash));
    wrong_hash[31] ^= 0xffu;
    result = openspm_native_library_open(argv[1], "TEST000", wrong_hash, &library);
    if (!expect(result == OPENSPM_ERROR_SOURCE_MISMATCH,
                "source hash mismatch was not rejected")) {
        openspm_native_library_close(&library);
        return 9;
    }
    result = openspm_native_library_open(argv[1], "WRONG", expected_hash, &library);
    if (!expect(result == OPENSPM_ERROR_SOURCE_MISMATCH,
                "source game ID mismatch was not rejected")) {
        openspm_native_library_close(&library);
        return 10;
    }

    puts("m14_native_loader_test: PASS - dynamic local module, all exit classes, source identity checked");
    return 0;
}
