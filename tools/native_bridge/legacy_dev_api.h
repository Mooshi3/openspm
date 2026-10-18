#ifndef OPENSPM_LEGACY_DEV_API_H
#define OPENSPM_LEGACY_DEV_API_H

#include "openspm/abi.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Development-only service exported by the temporary generated-C bridge.
   It exists solely for differential testing while the real OpenSPM codegen
   backend is brought up. It is not part of the public mod SDK. */
#define OPENSPM_LEGACY_DEV_SERVICE_ID "openspm.dev.legacy-state"
#define OPENSPM_LEGACY_DEV_SERVICE_VERSION 1u

typedef struct openspm_legacy_dev_state {
    openspm_abi_header header;
    uint32_t gpr[32];
    uint64_t fpr_bits[32];
    uint64_t ps1_bits[32];
    uint32_t pc;
    uint32_t lr;
    uint32_t ctr;
    uint32_t cr;
    uint32_t xer;
    uint32_t fpscr;
    uint32_t msr;
    int64_t downcount;
    uint64_t timebase;
    uint32_t exception;
    uint32_t program_exception;
} openspm_legacy_dev_state;

typedef int (OPENSPM_CALL *openspm_legacy_dev_get_state_fn)(
    void *context,
    openspm_legacy_dev_state *out_state);

typedef int (OPENSPM_CALL *openspm_legacy_dev_set_state_fn)(
    void *context,
    const openspm_legacy_dev_state *state);

typedef struct openspm_legacy_dev_service {
    openspm_abi_header header;
    void *context;
    openspm_legacy_dev_get_state_fn get_state;
    openspm_legacy_dev_set_state_fn set_state;
} openspm_legacy_dev_service;

#ifdef __cplusplus
}
#endif

#endif
