#include "openspm/native_runtime.h"
#include "openspm/validate.h"

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#if defined(_MSC_VER)
#define TEST_NOINLINE __declspec(noinline)
#elif defined(__GNUC__) || defined(__clang__)
#define TEST_NOINLINE __attribute__((noinline))
#else
#define TEST_NOINLINE
#endif

#define TEST_SERVICE_ID "openspm.test.counter"
#define TEST_SERVICE_VERSION 1u

typedef struct test_counter_service {
    openspm_abi_header header;
    void *context;
    void (OPENSPM_CALL *add)(void *context, uint64_t value);
} test_counter_service;

typedef struct test_host_state {
    uint64_t cycle_calls;
    uint64_t cycles;
    uint64_t poll_calls;
    uint64_t service_queries;
    uint64_t service_add_calls;
    uint64_t service_total;
} test_host_state;

typedef struct test_native_instance {
    const openspm_native_runtime_api *runtime;
    uint32_t stop_requested;
} test_native_instance;

static void OPENSPM_CALL test_log(void *context, openspm_log_level level, const char *message) {
    (void)context;
    (void)level;
    (void)message;
}

static uint64_t OPENSPM_CALL test_monotonic_ns(void *context) {
    (void)context;
    return 123456789u;
}

static void OPENSPM_CALL test_counter_add(void *context, uint64_t value) {
    test_host_state *state = (test_host_state *)context;
    state->service_add_calls++;
    state->service_total += value;
}

static int OPENSPM_CALL test_query_service(
    void *context,
    const char *service_id,
    uint32_t requested_version,
    void *out_service) {
    test_host_state *state = (test_host_state *)context;
    test_counter_service *service = (test_counter_service *)out_service;

    state->service_queries++;
    if (service_id == NULL || strcmp(service_id, TEST_SERVICE_ID) != 0 ||
        requested_version != TEST_SERVICE_VERSION || service == NULL ||
        service->header.struct_size < sizeof(*service)) {
        return OPENSPM_ERROR_NOT_FOUND;
    }

    service->header.abi_version = OPENSPM_ABI_VERSION;
    service->context = context;
    service->add = test_counter_add;
    return OPENSPM_OK;
}

static int OPENSPM_CALL test_account_cycles(void *context, uint64_t guest_cycles) {
    test_host_state *state = (test_host_state *)context;
    state->cycle_calls++;
    state->cycles += guest_cycles;
    return OPENSPM_OK;
}

static int OPENSPM_CALL test_poll_control(void *context, uint64_t *out_control_flags) {
    test_host_state *state = (test_host_state *)context;
    state->poll_calls++;
    if (out_control_flags == NULL) {
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    }
    *out_control_flags = OPENSPM_NATIVE_CONTROL_NONE;
    return OPENSPM_OK;
}

/* These are intentionally separate noinline C functions. The smoke test's hot
   path is an ordinary native call chain with no dispatcher or architectural
   CPU-state callback between calls. */
static TEST_NOINLINE void native_level3(uint32_t *word) {
    *word += 1u;
}

static TEST_NOINLINE void native_level2(uint32_t *word) {
    native_level3(word);
}

static TEST_NOINLINE void native_level1(uint32_t *word) {
    native_level2(word);
}

static int OPENSPM_CALL test_native_create(
    const openspm_native_runtime_api *runtime,
    const openspm_launch_desc *launch,
    void **out_instance) {
    test_native_instance *instance;
    (void)launch;

    if (runtime == NULL || out_instance == NULL) {
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    }
    instance = (test_native_instance *)calloc(1, sizeof(*instance));
    if (instance == NULL) {
        return OPENSPM_ERROR_RUNTIME;
    }
    instance->runtime = runtime;
    *out_instance = instance;
    return OPENSPM_OK;
}

static int OPENSPM_CALL test_native_run(
    void *opaque,
    uint64_t soft_cycle_budget,
    openspm_native_exit *out_exit) {
    test_native_instance *instance = (test_native_instance *)opaque;
    const openspm_native_runtime_api *runtime;
    test_counter_service service;
    uint64_t control_flags = 0;
    uint32_t *word;
    uint32_t i;
    int result;
    const uint64_t native_work = 4096u;
    (void)soft_cycle_budget;

    if (instance == NULL || out_exit == NULL) {
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    }
    runtime = instance->runtime;

    out_exit->header.struct_size = sizeof(*out_exit);
    out_exit->header.abi_version = OPENSPM_NATIVE_ABI_VERSION;

    if (instance->stop_requested) {
        out_exit->reason = OPENSPM_NATIVE_EXIT_STOPPED;
        return OPENSPM_OK;
    }

    word = (uint32_t *)runtime->mem1;
    for (i = 0; i < native_work; ++i) {
        native_level1(word);
    }

    /* One boundary service lookup/call after thousands of direct native calls. */
    memset(&service, 0, sizeof(service));
    service.header.struct_size = sizeof(service);
    service.header.abi_version = OPENSPM_ABI_VERSION;
    result = runtime->host->query_service(
        runtime->host->context,
        TEST_SERVICE_ID,
        TEST_SERVICE_VERSION,
        &service);
    if (result != OPENSPM_OK || service.add == NULL) {
        return OPENSPM_ERROR_RUNTIME;
    }
    service.add(service.context, native_work);

    result = runtime->account_cycles(runtime->context, native_work);
    if (result != OPENSPM_OK) {
        return result;
    }
    result = runtime->poll_control(runtime->context, &control_flags);
    if (result != OPENSPM_OK) {
        return result;
    }
    if ((control_flags & OPENSPM_NATIVE_CONTROL_STOP) != 0) {
        instance->stop_requested = 1u;
    }

    out_exit->reason = OPENSPM_NATIVE_EXIT_SAFEPOINT;
    out_exit->source_address = 0x80000000u;
    out_exit->guest_cycles = native_work;
    return OPENSPM_OK;
}

static int OPENSPM_CALL test_native_request_stop(void *opaque) {
    test_native_instance *instance = (test_native_instance *)opaque;
    if (instance == NULL) {
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    }
    instance->stop_requested = 1u;
    return OPENSPM_OK;
}

static void OPENSPM_CALL test_native_destroy(void *opaque) {
    free(opaque);
}

static int expect(int condition, const char *message) {
    if (!condition) {
        fprintf(stderr, "m14_native_runtime_test: %s\n", message);
        return 0;
    }
    return 1;
}

int main(void) {
    test_host_state state;
    openspm_host_api host;
    openspm_native_runtime_api runtime;
    openspm_native_module_api module;
    openspm_launch_desc launch;
    openspm_native_session session;
    openspm_native_exit exit_info;
    uint32_t mem1_words[64];
    uint32_t mem2_words[64];
    int result;

    memset(&state, 0, sizeof(state));
    memset(&host, 0, sizeof(host));
    memset(&runtime, 0, sizeof(runtime));
    memset(&module, 0, sizeof(module));
    memset(&launch, 0, sizeof(launch));
    memset(&session, 0, sizeof(session));
    memset(&exit_info, 0, sizeof(exit_info));
    memset(mem1_words, 0, sizeof(mem1_words));
    memset(mem2_words, 0, sizeof(mem2_words));

    host.header.struct_size = sizeof(host);
    host.header.abi_version = OPENSPM_ABI_VERSION;
    host.capabilities = OPENSPM_HOST_CAP_SERVICE_QUERY;
    host.context = &state;
    host.log = test_log;
    host.monotonic_ns = test_monotonic_ns;
    host.query_service = test_query_service;

    runtime.header.struct_size = sizeof(runtime);
    runtime.header.abi_version = OPENSPM_NATIVE_ABI_VERSION;
    runtime.capabilities = OPENSPM_NATIVE_HOST_CAP_DIRECT_MEMORY |
                           OPENSPM_NATIVE_HOST_CAP_SERVICE_QUERY |
                           OPENSPM_NATIVE_HOST_CAP_CYCLE_ACCOUNTING |
                           OPENSPM_NATIVE_HOST_CAP_CONTROL_POLL;
    runtime.context = &state;
    runtime.host = &host;
    runtime.mem1 = (uint8_t *)mem1_words;
    runtime.mem1_size = sizeof(mem1_words);
    runtime.mem2 = (uint8_t *)mem2_words;
    runtime.mem2_size = sizeof(mem2_words);
    runtime.account_cycles = test_account_cycles;
    runtime.poll_control = test_poll_control;

    module.header.struct_size = sizeof(module);
    module.header.abi_version = OPENSPM_NATIVE_ABI_VERSION;
    module.capabilities = OPENSPM_NATIVE_MODULE_CAP_DIRECT_CALLS |
                          OPENSPM_NATIVE_MODULE_CAP_DIRECT_MEMORY |
                          OPENSPM_NATIVE_MODULE_CAP_FALLBACK_EXIT;
    module.module_id = "openspm.synthetic.m14";
    module.module_version = "1";
    module.source_game_id = "TEST000";
    module.create = test_native_create;
    module.run = test_native_run;
    module.request_stop = test_native_request_stop;
    module.destroy = test_native_destroy;

    launch.header.struct_size = sizeof(launch);
    launch.header.abi_version = OPENSPM_ABI_VERSION;
    launch.app_root_utf8 = ".";
    launch.runtime_root_utf8 = ".";
    launch.game_root_utf8 = ".";
    launch.user_root_utf8 = ".";
    launch.graphics_backend_utf8 = "test";
    launch.flags = OPENSPM_LAUNCH_FLAG_GAME_ROOT_READ_ONLY;

    if (!expect(openspm_validate_native_runtime_api(&runtime) == OPENSPM_OK,
                "native runtime validation failed") ||
        !expect(openspm_validate_native_module_api(&module) == OPENSPM_OK,
                "native module validation failed")) {
        return 1;
    }

    result = openspm_native_session_open(&module, &runtime, &launch, &session);
    if (!expect(result == OPENSPM_OK, "session open failed")) {
        return 2;
    }

    result = openspm_native_session_run(&session, 100000u, &exit_info);
    if (!expect(result == OPENSPM_OK, "native run failed") ||
        !expect(exit_info.reason == OPENSPM_NATIVE_EXIT_SAFEPOINT,
                "first run did not end at a safepoint") ||
        !expect(mem1_words[0] == 4096u,
                "direct native call chain did not mutate MEM1 correctly") ||
        !expect(state.cycle_calls == 1u && state.cycles == 4096u,
                "cycle accounting was not batched to one safepoint callback") ||
        !expect(state.poll_calls == 1u,
                "control polling did not occur exactly once at the safepoint") ||
        !expect(state.service_queries == 1u && state.service_add_calls == 1u &&
                    state.service_total == 4096u,
                "OpenSPM service boundary did not round-trip correctly")) {
        openspm_native_session_close(&session);
        return 3;
    }

    result = openspm_native_session_request_stop(&session);
    if (!expect(result == OPENSPM_OK, "request_stop failed")) {
        openspm_native_session_close(&session);
        return 4;
    }

    result = openspm_native_session_run(&session, 100000u, &exit_info);
    if (!expect(result == OPENSPM_OK, "stop run failed") ||
        !expect(exit_info.reason == OPENSPM_NATIVE_EXIT_STOPPED,
                "stop request did not produce STOPPED exit") ||
        !expect(mem1_words[0] == 4096u,
                "game work continued after stop request")) {
        openspm_native_session_close(&session);
        return 5;
    }

    openspm_native_session_close(&session);
    if (!expect(session.instance == NULL && session.module == NULL && session.active == 0u,
                "session close did not clear state")) {
        return 6;
    }

    /* Capability/argument validation must fail closed. */
    runtime.mem1 = NULL;
    if (!expect(openspm_validate_native_runtime_api(&runtime) == OPENSPM_ERROR_INVALID_ARGUMENT,
                "direct-memory runtime accepted a null MEM1 pointer")) {
        return 7;
    }

    puts("m14_native_runtime_test: PASS - 4096 direct native call chains, one coarse host boundary");
    return 0;
}
