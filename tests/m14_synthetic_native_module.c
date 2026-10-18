#include "openspm/native_api.h"

#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#if defined(_MSC_VER)
#define TEST_NOINLINE __declspec(noinline)
#elif defined(__GNUC__) || defined(__clang__)
#define TEST_NOINLINE __attribute__((noinline))
#else
#define TEST_NOINLINE
#endif

typedef struct synthetic_instance {
    const openspm_native_runtime_api *runtime;
    uint32_t stop_requested;
} synthetic_instance;

static TEST_NOINLINE void native_leaf(uint32_t *value) {
    *value += 1u;
}

static TEST_NOINLINE void native_middle(uint32_t *value) {
    native_leaf(value);
}

static TEST_NOINLINE void native_root(uint32_t *value) {
    native_middle(value);
}

static int OPENSPM_CALL synthetic_create(
    const openspm_native_runtime_api *runtime,
    const openspm_launch_desc *launch,
    void **out_instance) {
    synthetic_instance *instance;
    (void)launch;

    if (runtime == NULL || out_instance == NULL) {
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    }
    instance = (synthetic_instance *)calloc(1, sizeof(*instance));
    if (instance == NULL) {
        return OPENSPM_ERROR_RUNTIME;
    }
    instance->runtime = runtime;
    *out_instance = instance;
    return OPENSPM_OK;
}

static int OPENSPM_CALL synthetic_run(
    void *opaque,
    uint64_t soft_cycle_budget,
    openspm_native_exit *out_exit) {
    synthetic_instance *instance = (synthetic_instance *)opaque;
    const openspm_native_runtime_api *runtime;
    uint32_t *word;
    uint64_t control_flags = 0;
    uint32_t i;
    int result;
    const uint64_t work = 1024u;

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
    if (soft_cycle_budget == UINT64_C(0xF00D)) {
        out_exit->reason = OPENSPM_NATIVE_EXIT_FAULT;
        out_exit->detail = 1u;
        out_exit->source_address = UINT64_C(0x8000F00D);
        return OPENSPM_OK;
    }

    word = (uint32_t *)runtime->mem1;
    for (i = 0; i < work; ++i) {
        native_root(word);
    }

    if (runtime->account_cycles != NULL) {
        result = runtime->account_cycles(runtime->context, work);
        if (result != OPENSPM_OK) {
            return result;
        }
    }
    if (runtime->poll_control != NULL) {
        result = runtime->poll_control(runtime->context, &control_flags);
        if (result != OPENSPM_OK) {
            return result;
        }
        if ((control_flags & OPENSPM_NATIVE_CONTROL_STOP) != 0) {
            instance->stop_requested = 1u;
        }
    }

    out_exit->guest_cycles = work;
    if (soft_cycle_budget == 2u) {
        out_exit->reason = OPENSPM_NATIVE_EXIT_FALLBACK_REQUIRED;
        out_exit->detail = 7u;
        out_exit->source_address = UINT64_C(0x80001234);
    } else {
        out_exit->reason = OPENSPM_NATIVE_EXIT_SAFEPOINT;
        out_exit->source_address = UINT64_C(0x80001000);
    }
    return OPENSPM_OK;
}

static int OPENSPM_CALL synthetic_request_stop(void *opaque) {
    synthetic_instance *instance = (synthetic_instance *)opaque;
    if (instance == NULL) {
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    }
    instance->stop_requested = 1u;
    return OPENSPM_OK;
}

static void OPENSPM_CALL synthetic_destroy(void *opaque) {
    free(opaque);
}

OPENSPM_EXPORT int OPENSPM_CALL openspm_native_module_query(
    uint32_t requested_abi,
    openspm_native_module_api *out_api) {
    static const uint8_t source_hash[32] = {
        0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07,
        0x08, 0x09, 0x0a, 0x0b, 0x0c, 0x0d, 0x0e, 0x0f,
        0x10, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17,
        0x18, 0x19, 0x1a, 0x1b, 0x1c, 0x1d, 0x1e, 0x1f
    };

    if (requested_abi != OPENSPM_NATIVE_ABI_VERSION || out_api == NULL ||
        out_api->header.struct_size < sizeof(*out_api)) {
        return OPENSPM_ERROR_ABI_MISMATCH;
    }

    memset(out_api, 0, sizeof(*out_api));
    out_api->header.struct_size = sizeof(*out_api);
    out_api->header.abi_version = OPENSPM_NATIVE_ABI_VERSION;
    out_api->capabilities = OPENSPM_NATIVE_MODULE_CAP_DIRECT_CALLS |
                            OPENSPM_NATIVE_MODULE_CAP_DIRECT_MEMORY |
                            OPENSPM_NATIVE_MODULE_CAP_FALLBACK_EXIT;
    out_api->module_id = "openspm.synthetic.dynamic";
    out_api->module_version = "1";
    out_api->source_game_id = "TEST000";
    memcpy(out_api->source_sha256, source_hash, sizeof(source_hash));
    out_api->create = synthetic_create;
    out_api->run = synthetic_run;
    out_api->request_stop = synthetic_request_stop;
    out_api->destroy = synthetic_destroy;
    return OPENSPM_OK;
}
