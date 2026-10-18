#ifndef OPENSPM_VALIDATE_H
#define OPENSPM_VALIDATE_H

#include "openspm/backend_api.h"
#include "openspm/mod_api.h"
#include "openspm/native_api.h"

#ifdef __cplusplus
extern "C" {
#endif

int openspm_validate_host_api(const openspm_host_api *host);
int openspm_validate_launch_desc(const openspm_launch_desc *launch);
int openspm_validate_backend_api(const openspm_backend_api *backend);
int openspm_validate_mod_api(const openspm_mod_api *mod);
int openspm_validate_native_runtime_api(const openspm_native_runtime_api *runtime);
int openspm_validate_native_module_api(const openspm_native_module_api *module);

#ifdef __cplusplus
}
#endif

#endif
