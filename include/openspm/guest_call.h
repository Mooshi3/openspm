/* OpenSPM guest-call service for mods ("openspm.guest_call", docs/MODDING.md).
 *
 * Lets a mod ask the game to run one of its own functions, which is how a mod
 * makes the game *do* things: start an EVT script (evtEntry), spawn an item or
 * NPC, play a sound, change a map. Everything runs as recompiled game code,
 * with the game's own rules, so the result is exactly what the game would do.
 *
 * call(target, argc, args): TICK_BEGIN only. Queues a call to the guest
 * function at `target` (a function entry in MEM1 code) with up to
 * OPENSPM_GUEST_CALL_MAX_ARGS integer/pointer arguments in r3.. (no float
 * arguments: pass floats through an EVT script instead, see
 * include/openspm/spm_evt.h). The backend runs one queued call per game frame,
 * right after the frame safepoint and before the next guest frame, on a scratch
 * CPU context like an interrupt handler: the game's threads are all idle then.
 * The return value is not reported; a call the recompiled module cannot enter
 * is logged and skipped (the game keeps running). OPENSPM_ERROR_RUNTIME when
 * the queue (OPENSPM_GUEST_CALL_QUEUE entries) is full.
 *
 * scratch(size, &address): reserves `size` zeroed bytes of guest memory for the
 * mod's own data (scripts, strings, tables) in a MEM2 window above the game's
 * arena and the IOS heap (never used by the game). Guest address, 32-byte
 * aligned, valid for the whole run; there is no free. Write it with
 * openspm.guest_memory during TICK_BEGIN. OPENSPM_ERROR_RUNTIME when the
 * OPENSPM_GUEST_SCRATCH_SIZE window is exhausted.
 *
 * Addresses are game-revision facts (R8PE01 rev 2 here, spm_r8pe01.h).
 * Portable C; version 1; later versions only append fields. */
#ifndef OPENSPM_GUEST_CALL_H
#define OPENSPM_GUEST_CALL_H

#include <stdint.h>
#include <string.h>

#include "openspm/abi.h"

#ifdef __cplusplus
extern "C" {
#endif

#define OPENSPM_GUEST_CALL_SERVICE_ID "openspm.guest_call"
#define OPENSPM_GUEST_CALL_SERVICE_VERSION 1u
#define OPENSPM_GUEST_CALL_MAX_ARGS 3u /* = OPENSPM_M14_HOSTCALL_MAX_EVENT_ARGS */
#define OPENSPM_GUEST_CALL_QUEUE 8u
#define OPENSPM_GUEST_SCRATCH_BEGIN UINT32_C(0x93F00000)
#define OPENSPM_GUEST_SCRATCH_SIZE UINT32_C(0x00100000) /* 1 MiB, up to the end of MEM2 */

enum {
    OPENSPM_GUEST_CALL_CAP_CALL = UINT64_C(1) << 0,
    OPENSPM_GUEST_CALL_CAP_SCRATCH = UINT64_C(1) << 1
};

typedef int(OPENSPM_CALL *openspm_guest_call_fn)(void *context, uint32_t target, uint32_t argc,
                                                 const uint32_t *args);
typedef int(OPENSPM_CALL *openspm_guest_scratch_fn)(void *context, uint32_t size, uint32_t *out_address);

typedef struct openspm_guest_call_service {
    openspm_abi_header header;
    uint64_t capabilities;
    void *context;
    openspm_guest_call_fn call;
    openspm_guest_scratch_fn scratch;
} openspm_guest_call_service;

static inline int openspm_guest_call_accept(const openspm_guest_call_service *out, uint32_t requested_version) {
    if (out == 0) return OPENSPM_ERROR_INVALID_ARGUMENT;
    if (requested_version != OPENSPM_GUEST_CALL_SERVICE_VERSION || out->header.struct_size < sizeof(*out))
        return OPENSPM_ERROR_ABI_MISMATCH;
    return OPENSPM_OK;
}

/* Provider side: a small FIFO the backend drains once per frame. */
typedef struct openspm_guest_call_entry {
    uint32_t target;
    uint32_t argc;
    uint32_t args[OPENSPM_GUEST_CALL_MAX_ARGS];
} openspm_guest_call_entry;

typedef struct openspm_guest_call_queue {
    openspm_guest_call_entry entries[OPENSPM_GUEST_CALL_QUEUE];
    uint32_t head, count;
} openspm_guest_call_queue;

/* A plausible function entry: 4-byte aligned, in cached MEM1 above the
   exception vectors. (Whether the recompiled module can enter it is only
   known when it runs.) */
static inline int openspm_guest_call_target_ok(uint32_t target) {
    return (target & 3u) == 0u && target >= UINT32_C(0x80004000) && target < UINT32_C(0x81800000);
}

static inline int openspm_guest_call_push(openspm_guest_call_queue *q, uint32_t target, uint32_t argc,
                                          const uint32_t *args) {
    openspm_guest_call_entry *e;
    if (q == 0 || !openspm_guest_call_target_ok(target) || argc > OPENSPM_GUEST_CALL_MAX_ARGS ||
        (argc != 0u && args == 0))
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    if (q->count >= OPENSPM_GUEST_CALL_QUEUE) return OPENSPM_ERROR_RUNTIME;
    e = &q->entries[(q->head + q->count) % OPENSPM_GUEST_CALL_QUEUE];
    memset(e, 0, sizeof(*e));
    e->target = target;
    e->argc = argc;
    if (argc != 0u) memcpy(e->args, args, argc * sizeof(args[0]));
    ++q->count;
    return OPENSPM_OK;
}

static inline int openspm_guest_call_pop(openspm_guest_call_queue *q, openspm_guest_call_entry *out) {
    if (q == 0 || out == 0 || q->count == 0u) return 0;
    *out = q->entries[q->head];
    q->head = (q->head + 1u) % OPENSPM_GUEST_CALL_QUEUE;
    --q->count;
    return 1;
}

/* Bump allocation in the scratch window; *used is the provider's counter. */
static inline int openspm_guest_scratch_take(uint32_t *used, uint32_t size, uint32_t *out_address) {
    uint32_t rounded;
    if (used == 0 || out_address == 0 || size == 0u || size > OPENSPM_GUEST_SCRATCH_SIZE)
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    rounded = (size + 31u) & ~31u;
    if (*used > OPENSPM_GUEST_SCRATCH_SIZE - rounded) return OPENSPM_ERROR_RUNTIME;
    *out_address = OPENSPM_GUEST_SCRATCH_BEGIN + *used;
    *used += rounded;
    return OPENSPM_OK;
}

#ifdef __cplusplus
}
#endif

#endif
