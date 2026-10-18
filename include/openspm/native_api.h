#ifndef OPENSPM_NATIVE_API_H
#define OPENSPM_NATIVE_API_H

#include "openspm/host_api.h"

#ifdef __cplusplus
extern "C" {
#endif

/*
 * OpenSPM standalone native-module ABI.
 *
 * This ABI is intentionally owned by OpenSPM and contains no Dolphin,
 * ModernGekko, RecompCore, PowerPCState, CPUState, or proprietary game-symbol
 * types. A native module is generated locally from the player's verified game
 * input and is not a redistributable game payload.
 *
 * Ordinary game execution is expected to remain inside the module across many
 * direct native calls. The module returns to the host only at coarse runtime
 * boundaries (safepoints, stop/fault, or temporary migration fallback).
 */
#define OPENSPM_NATIVE_ABI_VERSION 1u
#define OPENSPM_NATIVE_ABI_MIN_VERSION 1u
#define OPENSPM_NATIVE_MODULE_QUERY_SYMBOL "openspm_native_module_query"

typedef enum openspm_native_exit_reason {
    OPENSPM_NATIVE_EXIT_SAFEPOINT = 1,
    OPENSPM_NATIVE_EXIT_STOPPED = 2,
    OPENSPM_NATIVE_EXIT_FALLBACK_REQUIRED = 3,
    OPENSPM_NATIVE_EXIT_FAULT = 4
} openspm_native_exit_reason;

enum {
    /* Module executes ordinary RAM accesses directly through mem1/mem2. */
    OPENSPM_NATIVE_HOST_CAP_DIRECT_MEMORY = UINT64_C(1) << 0,
    /* Module may resolve OpenSPM-owned service vtables and cache them. */
    OPENSPM_NATIVE_HOST_CAP_SERVICE_QUERY = UINT64_C(1) << 1,
    /* Host accepts batched guest-cycle accounting at native safepoints. */
    OPENSPM_NATIVE_HOST_CAP_CYCLE_ACCOUNTING = UINT64_C(1) << 2,
    /* Module may cheaply poll for pause/stop work at compiler-selected safepoints. */
    OPENSPM_NATIVE_HOST_CAP_CONTROL_POLL = UINT64_C(1) << 3
};

enum {
    OPENSPM_NATIVE_CONTROL_NONE = 0,
    OPENSPM_NATIVE_CONTROL_PAUSE = UINT64_C(1) << 0,
    OPENSPM_NATIVE_CONTROL_STOP = UINT64_C(1) << 1
};

enum {
    /* Module can stay native across ordinary direct game-function calls. */
    OPENSPM_NATIVE_MODULE_CAP_DIRECT_CALLS = UINT64_C(1) << 0,
    /* Module can use direct MEM1/MEM2 pointers supplied by the host. */
    OPENSPM_NATIVE_MODULE_CAP_DIRECT_MEMORY = UINT64_C(1) << 1,
    /* Module supports temporary migration fallback exits while M14 is incomplete. */
    OPENSPM_NATIVE_MODULE_CAP_FALLBACK_EXIT = UINT64_C(1) << 2,
    /* Module can expose game-native OpenSPM services without raw guest symbols. */
    OPENSPM_NATIVE_MODULE_CAP_SERVICE_EXPORTS = UINT64_C(1) << 3
};

typedef int (OPENSPM_CALL *openspm_native_account_cycles_fn)(
    void *context,
    uint64_t guest_cycles);

typedef int (OPENSPM_CALL *openspm_native_poll_control_fn)(
    void *context,
    uint64_t *out_control_flags);

typedef struct openspm_native_runtime_api {
    openspm_abi_header header;
    uint64_t capabilities;
    void *context;

    /* Existing OpenSPM host surface: logging, monotonic clock, service query. */
    const openspm_host_api *host;

    /* Native fast-path memory. The locally generated module owns guest address
       translation rules; the host owns allocation/lifetime. */
    uint8_t *mem1;
    uint64_t mem1_size;
    uint8_t *mem2;
    uint64_t mem2_size;

    /* Boundary-only callbacks. Neither belongs in the ordinary call path. */
    openspm_native_account_cycles_fn account_cycles;
    openspm_native_poll_control_fn poll_control;
} openspm_native_runtime_api;

typedef struct openspm_native_exit {
    openspm_abi_header header;
    uint32_t reason;
    uint32_t detail;

    /* Diagnostic/migration coordinates only. They are not public mod symbols
       and are not required to be touched on the common native path. */
    uint64_t source_address;
    uint64_t guest_cycles;
} openspm_native_exit;

typedef int (OPENSPM_CALL *openspm_native_create_fn)(
    const openspm_native_runtime_api *runtime,
    const openspm_launch_desc *launch,
    void **out_instance);

typedef int (OPENSPM_CALL *openspm_native_run_fn)(
    void *instance,
    uint64_t soft_cycle_budget,
    openspm_native_exit *out_exit);

typedef int (OPENSPM_CALL *openspm_native_request_stop_fn)(void *instance);
typedef void (OPENSPM_CALL *openspm_native_destroy_fn)(void *instance);
typedef int (OPENSPM_CALL *openspm_native_query_service_fn)(
    void *instance,
    const char *service_id,
    uint32_t requested_version,
    void *out_service);

typedef struct openspm_native_module_api {
    openspm_abi_header header;
    uint64_t capabilities;

    /* OpenSPM-generated metadata only. Do not embed proprietary symbol names. */
    const char *module_id;
    const char *module_version;
    const char *source_game_id;
    uint8_t source_sha256[32];

    openspm_native_create_fn create;
    openspm_native_run_fn run;
    openspm_native_request_stop_fn request_stop;
    openspm_native_destroy_fn destroy;
    openspm_native_query_service_fn query_service;
} openspm_native_module_api;

typedef int (OPENSPM_CALL *openspm_native_module_query_fn)(
    uint32_t requested_abi,
    openspm_native_module_api *out_api);

#ifdef __cplusplus
}
#endif

#endif
