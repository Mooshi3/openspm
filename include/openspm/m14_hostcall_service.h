#ifndef OPENSPM_M14_HOSTCALL_SERVICE_H
#define OPENSPM_M14_HOSTCALL_SERVICE_H

#include "openspm/abi.h"

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Temporary M14 migration service.

   This is intentionally not a public mod API. It lets the player-local
   generated-C bridge hand a semantic function side exit to the OpenSPM native
   backend without exposing the bridge's private CPUState type. The service
   disappears once codegen emits direct OpenSPM service calls for these
   boundaries. */
#define OPENSPM_M14_HOSTCALL_SERVICE_ID "openspm.m14.hostcall.r8pe01"
#define OPENSPM_M14_HOSTCALL_SERVICE_VERSION 1u
#define OPENSPM_M14_HOSTCALL_MAX_EVENT_ARGS 3u

/* Reserved OpenSPM migration events are deliberately outside Wii effective
   address space. They are direct bridge/backend semantics, never guest labels
   and never members of state/m14-host-targets.json. */
#define OPENSPM_M14_HOST_EVENT_PRESENT_SAFEPOINT UINT32_C(0xFFFFFFFE)

/* OpenSPM content hooks (docs/CONTENT-API.md): a hostcall handler that sets
   event_target to this marker (event_argc 0) and leaves pc at the hooked
   function's entry asks the bridge to run the game's original recompiled body
   for exactly this call (same return address and stack pointer), instead of
   routing it to the host again. Everything else about the call is unchanged:
   the handler may have edited argument registers first (pre-hook). */
#define OPENSPM_M14_HOOK_CONTINUE_ORIGINAL UINT32_C(0xFFFFFFFD)

/* Only state required by the currently proven host-call shims is carried here
   (plus the FPRs, for hooks); exception state remains private to the
   generated module.

   event_* describes one guest completion callback that must run after the
   hosted function has logically returned but before ordinary guest execution
   resumes. This reproduces the ordering used by the successful M14 diagnostic
   for async GameFS/save completions without putting CPUState in OpenSPM's ABI. */
typedef struct openspm_m14_hostcall_state {
    openspm_abi_header header;
    uint32_t gpr[32];
    uint32_t pc;
    uint32_t lr;
    uint32_t ctr;
    uint32_t cr;
    uint32_t xer;
    uint32_t fpscr;
    uint32_t event_target;
    uint32_t event_argc;
    uint32_t event_args[OPENSPM_M14_HOSTCALL_MAX_EVENT_ARGS];
    /* Appended for function hooks (include/openspm/hooks.h): f0..f31 (ps0) as
       IEEE double bit patterns. Present when header.struct_size covers them
       (openspm_m14_hostcall_has_fpr); a bridge built before them sends the
       shorter state, which every handler still accepts. A handler that edits
       one writes the new bits; the bridge copies a changed register back into
       both ps0 and ps1, as lfs/frsp results are. */
    uint64_t fpr_bits[32];
} openspm_m14_hostcall_state;

/* The state before fpr_bits: the minimum every handler requires. */
#define OPENSPM_M14_HOSTCALL_STATE_V1_SIZE ((uint32_t)offsetof(openspm_m14_hostcall_state, fpr_bits))

static inline int openspm_m14_hostcall_has_fpr(const openspm_m14_hostcall_state *state) {
    return state != 0 && state->header.struct_size >= sizeof(openspm_m14_hostcall_state);
}

typedef int (OPENSPM_CALL *openspm_m14_hostcall_handle_fn)(
    void *context,
    uint32_t target_address,
    openspm_m14_hostcall_state *state);

typedef struct openspm_m14_hostcall_service {
    openspm_abi_header header;
    void *context;
    openspm_m14_hostcall_handle_fn handle;
} openspm_m14_hostcall_service;

#ifdef __cplusplus
}
#endif

#endif
