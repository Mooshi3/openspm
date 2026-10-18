#include "openspm/validate.h"

#include <stddef.h>

#define OPENSPM_MEMBER_END(type, member) \
    (offsetof(type, member) + sizeof(((type *)0)->member))

static int header_valid(const openspm_abi_header *header, size_t required_size) {
    return header != NULL &&
           header->abi_version >= OPENSPM_ABI_MIN_VERSION &&
           header->abi_version <= OPENSPM_ABI_VERSION &&
           header->struct_size >= required_size;
}

static int native_header_valid(const openspm_abi_header *header, size_t required_size) {
    return header != NULL &&
           header->abi_version >= OPENSPM_NATIVE_ABI_MIN_VERSION &&
           header->abi_version <= OPENSPM_NATIVE_ABI_VERSION &&
           header->struct_size >= required_size;
}

int openspm_validate_host_api(const openspm_host_api *host) {
    /* ABI v1 originally ended at profile_end. Service discovery/events append. */
    if (!header_valid(host ? &host->header : NULL,
                      OPENSPM_MEMBER_END(openspm_host_api, profile_end))) {
        return OPENSPM_ERROR_ABI_MISMATCH;
    }
    if (host->log == NULL || host->monotonic_ns == NULL) {
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    }
    return OPENSPM_OK;
}

int openspm_validate_launch_desc(const openspm_launch_desc *launch) {
    /* ABI v1 originally ended at flags. Appended launch-profile fields are optional. */
    if (!header_valid(launch ? &launch->header : NULL,
                      OPENSPM_MEMBER_END(openspm_launch_desc, flags))) {
        return OPENSPM_ERROR_ABI_MISMATCH;
    }
    if (launch->app_root_utf8 == NULL || launch->runtime_root_utf8 == NULL ||
        launch->game_root_utf8 == NULL || launch->user_root_utf8 == NULL ||
        launch->graphics_backend_utf8 == NULL) {
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    }
    return OPENSPM_OK;
}

int openspm_validate_backend_api(const openspm_backend_api *backend) {
    /* ABI v1 originally ended at shutdown. query_service is optional/appended. */
    if (!header_valid(backend ? &backend->header : NULL,
                      OPENSPM_MEMBER_END(openspm_backend_api, shutdown))) {
        return OPENSPM_ERROR_ABI_MISMATCH;
    }
    if (backend->backend_id == NULL || backend->backend_version == NULL || backend->run == NULL) {
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    }
    return OPENSPM_OK;
}

int openspm_validate_mod_api(const openspm_mod_api *mod) {
    if (!header_valid(mod ? &mod->header : NULL, sizeof(*mod))) {
        return OPENSPM_ERROR_ABI_MISMATCH;
    }
    if (mod->mod_id == NULL || mod->mod_version == NULL) {
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    }
    return OPENSPM_OK;
}

int openspm_validate_native_runtime_api(const openspm_native_runtime_api *runtime) {
    if (!native_header_valid(runtime ? &runtime->header : NULL,
                             sizeof(*runtime))) {
        return OPENSPM_ERROR_ABI_MISMATCH;
    }
    if (runtime->host == NULL || openspm_validate_host_api(runtime->host) != OPENSPM_OK) {
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    }
    if ((runtime->capabilities & OPENSPM_NATIVE_HOST_CAP_DIRECT_MEMORY) != 0) {
        if (runtime->mem1 == NULL || runtime->mem1_size == 0) {
            return OPENSPM_ERROR_INVALID_ARGUMENT;
        }
    }
    if ((runtime->capabilities & OPENSPM_NATIVE_HOST_CAP_SERVICE_QUERY) != 0) {
        if ((runtime->host->capabilities & OPENSPM_HOST_CAP_SERVICE_QUERY) == 0 ||
            runtime->host->query_service == NULL) {
            return OPENSPM_ERROR_INVALID_ARGUMENT;
        }
    }
    if ((runtime->capabilities & OPENSPM_NATIVE_HOST_CAP_CYCLE_ACCOUNTING) != 0 &&
        runtime->account_cycles == NULL) {
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    }
    if ((runtime->capabilities & OPENSPM_NATIVE_HOST_CAP_CONTROL_POLL) != 0 &&
        runtime->poll_control == NULL) {
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    }
    return OPENSPM_OK;
}

int openspm_validate_native_module_api(const openspm_native_module_api *module) {
    /* query_service is appended/optional for modules that do not export services. */
    if (!native_header_valid(module ? &module->header : NULL,
                             OPENSPM_MEMBER_END(openspm_native_module_api, destroy))) {
        return OPENSPM_ERROR_ABI_MISMATCH;
    }
    if (module->module_id == NULL || module->module_version == NULL ||
        module->source_game_id == NULL || module->create == NULL ||
        module->run == NULL || module->destroy == NULL) {
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    }
    if ((module->capabilities & OPENSPM_NATIVE_MODULE_CAP_SERVICE_EXPORTS) != 0 &&
        module->query_service == NULL) {
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    }
    return OPENSPM_OK;
}
