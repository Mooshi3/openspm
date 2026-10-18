#ifndef OPENSPM_NATIVE_RUNTIME_H
#define OPENSPM_NATIVE_RUNTIME_H

#include "openspm/native_api.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct openspm_native_session {
    const openspm_native_module_api *module;
    void *instance;
    uint32_t active;
} openspm_native_session;

/* Opens a locally generated native game module. This API deliberately knows
   nothing about Dolphin/ModernGekko or guest CPU implementation details. */
int openspm_native_session_open(
    const openspm_native_module_api *module,
    const openspm_native_runtime_api *runtime,
    const openspm_launch_desc *launch,
    openspm_native_session *out_session);

/* Runs native game code until the module reaches a coarse safepoint or exits.
   soft_cycle_budget is a scheduling hint, not a per-basic-block dispatcher
   budget. Modules may execute many direct native calls before returning. */
int openspm_native_session_run(
    openspm_native_session *session,
    uint64_t soft_cycle_budget,
    openspm_native_exit *out_exit);

int openspm_native_session_request_stop(openspm_native_session *session);
void openspm_native_session_close(openspm_native_session *session);

#ifdef __cplusplus
}
#endif

#endif
