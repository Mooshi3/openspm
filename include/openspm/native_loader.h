#ifndef OPENSPM_NATIVE_LOADER_H
#define OPENSPM_NATIVE_LOADER_H

#include "openspm/native_runtime.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct openspm_native_library {
    void *platform_handle;
    openspm_native_module_api module;
} openspm_native_library;

/* Load a locally generated module and validate its OpenSPM-owned ABI.
   expected_game_id/expected_source_sha256 may be NULL for synthetic tooling,
   but production callers should provide both from the verified player input. */
int openspm_native_library_open(
    const char *path_utf8,
    const char *expected_game_id,
    const uint8_t expected_source_sha256[32],
    openspm_native_library *out_library);

void openspm_native_library_close(openspm_native_library *library);

#ifdef __cplusplus
}
#endif

#endif
