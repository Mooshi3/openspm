#include "openspm/native_runtime.h"
#include "openspm/validate.h"

#include <string.h>

int openspm_native_session_open(
    const openspm_native_module_api *module,
    const openspm_native_runtime_api *runtime,
    const openspm_launch_desc *launch,
    openspm_native_session *out_session) {
    void *instance = NULL;
    int result;

    if (out_session == NULL) {
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    }
    memset(out_session, 0, sizeof(*out_session));

    result = openspm_validate_native_module_api(module);
    if (result != OPENSPM_OK) {
        return result;
    }
    result = openspm_validate_native_runtime_api(runtime);
    if (result != OPENSPM_OK) {
        return result;
    }
    result = openspm_validate_launch_desc(launch);
    if (result != OPENSPM_OK) {
        return result;
    }

    result = module->create(runtime, launch, &instance);
    if (result != OPENSPM_OK) {
        return result;
    }
    if (instance == NULL) {
        return OPENSPM_ERROR_RUNTIME;
    }

    out_session->module = module;
    out_session->instance = instance;
    out_session->active = 1u;
    return OPENSPM_OK;
}

int openspm_native_session_run(
    openspm_native_session *session,
    uint64_t soft_cycle_budget,
    openspm_native_exit *out_exit) {
    int result;

    if (session == NULL || out_exit == NULL || session->active == 0u ||
        session->module == NULL || session->instance == NULL) {
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    }

    memset(out_exit, 0, sizeof(*out_exit));
    out_exit->header.struct_size = sizeof(*out_exit);
    out_exit->header.abi_version = OPENSPM_NATIVE_ABI_VERSION;

    result = session->module->run(session->instance, soft_cycle_budget, out_exit);
    if (result != OPENSPM_OK) {
        return result;
    }

    if (out_exit->header.struct_size < sizeof(*out_exit) ||
        out_exit->header.abi_version < OPENSPM_NATIVE_ABI_MIN_VERSION ||
        out_exit->header.abi_version > OPENSPM_NATIVE_ABI_VERSION) {
        return OPENSPM_ERROR_ABI_MISMATCH;
    }
    if (out_exit->reason < OPENSPM_NATIVE_EXIT_SAFEPOINT ||
        out_exit->reason > OPENSPM_NATIVE_EXIT_FAULT) {
        return OPENSPM_ERROR_RUNTIME;
    }
    if (out_exit->reason == OPENSPM_NATIVE_EXIT_STOPPED ||
        out_exit->reason == OPENSPM_NATIVE_EXIT_FAULT) {
        session->active = 0u;
    }
    return OPENSPM_OK;
}

int openspm_native_session_request_stop(openspm_native_session *session) {
    if (session == NULL || session->module == NULL || session->instance == NULL) {
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    }
    if (session->module->request_stop == NULL) {
        return OPENSPM_ERROR_UNSUPPORTED;
    }
    return session->module->request_stop(session->instance);
}

void openspm_native_session_close(openspm_native_session *session) {
    if (session == NULL) {
        return;
    }
    if (session->module != NULL && session->module->destroy != NULL &&
        session->instance != NULL) {
        session->module->destroy(session->instance);
    }
    memset(session, 0, sizeof(*session));
}
