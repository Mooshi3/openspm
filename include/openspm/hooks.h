/* OpenSPM function hooks (docs/HOOKS.md): mods run code when the game calls
 * one of the functions in OpenSPM's hook registry (state/openspm-hooks.json).
 *
 * Service "openspm.hooks" version 2 (version 1 = without add_post), for mods
 * (register at STARTUP): find(name) -> hook id (OPENSPM_ERROR_NOT_FOUND when
 * this build does not hook it: the registry entry is off, or its first
 * instruction did not match at boot), add_pre(id, fn, user),
 * add_post(id, fn, user). A pre callback sees the call's integer arguments
 * (r3..r10), floating-point arguments (f1..f8) and the caller; it may change
 * the arguments, or replace the call: set `replace`, `ret` (r3) and / or
 * `fret` (f1) and the game's own function does not run. A post callback runs
 * when the call has returned, sees the arguments as they were at the call and
 * the result, and may change ret / fret. Callbacks run on the game's thread;
 * openspm.guest_memory is open during them, guest calls are not; keep them
 * short. Floating-point values need a bridge that carries FPRs (has_fpr).
 *
 * Host side (the backend): a table of registry entries (generated,
 * include/openspm/hooks_table.inc), a boot check of each entry's first
 * instruction, the dispatcher for a host call to a hooked address, and the
 * return path for post callbacks (OPENSPM_HOOK_RETURN_ADDRESS).
 *
 * Portable C; tests/m14_hooks_test.c. */
#ifndef OPENSPM_HOOKS_H
#define OPENSPM_HOOKS_H

#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "openspm/abi.h"
#include "openspm/m14_hostcall_service.h"

#ifdef __cplusplus
extern "C" {
#endif

#define OPENSPM_HOOK_SERVICE_ID "openspm.hooks"
#define OPENSPM_HOOK_SERVICE_VERSION 2u /* 1 still accepted (no add_post) */
#define OPENSPM_HOOK_ARGS 8u

/* Where a hooked function with post callbacks returns to: a reserved address
   below the game's code (never executed; its host exit runs the post
   callbacks and returns to the real caller). Only in builds with a hook
   enabled (tools/native_bridge/openspm_hooks.py). */
#define OPENSPM_HOOK_RETURN_ADDRESS UINT32_C(0x80002FF0)

typedef struct openspm_hook_call {
    openspm_abi_header header;
    uint32_t hook_id;
    uint32_t args[OPENSPM_HOOK_ARGS]; /* r3..r10; writable in pre; in post, as they were at the call */
    uint32_t ret;                     /* r3: set in pre to replace; readable / writable in post */
    uint32_t return_address;          /* the caller (read only) */
    int replace;                      /* pre: 1 = skip the game's function, return ret (and fret) */
    /* Version 2 (header.struct_size covers them). */
    double fargs[OPENSPM_HOOK_ARGS]; /* f1..f8; writable in pre */
    double fret;                     /* f1 on return: like ret */
    int has_fpr;                     /* 0: this build's bridge carries no FPRs; fargs / fret are 0 and not written */
    int post;                        /* 1 in a post callback */
} openspm_hook_call;

typedef void(OPENSPM_CALL *openspm_hook_fn)(void *user, openspm_hook_call *call);

typedef struct openspm_hook_service {
    openspm_abi_header header;
    void *context;
    int(OPENSPM_CALL *find)(void *context, const char *name, uint32_t *out_hook_id);
    int(OPENSPM_CALL *add_pre)(void *context, uint32_t hook_id, openspm_hook_fn fn, void *user);
    /* Version 2: runs after the call returns (the game's body, or a pre
       callback's replacement), in registration order; sees and may change
       ret / fret. */
    int(OPENSPM_CALL *add_post)(void *context, uint32_t hook_id, openspm_hook_fn fn, void *user);
} openspm_hook_service;

#define OPENSPM_HOOK_SERVICE_V1_SIZE (offsetof(openspm_hook_service, add_pre) + sizeof(void *))
#define OPENSPM_HOOK_SERVICE_V2_SIZE (offsetof(openspm_hook_service, add_post) + sizeof(void *))
#define OPENSPM_HOOK_CALL_V1_SIZE offsetof(openspm_hook_call, fargs)

static inline int openspm_hook_accept(const openspm_hook_service *out, uint32_t requested_version) {
    if (out == NULL) return OPENSPM_ERROR_INVALID_ARGUMENT;
    if (requested_version == 1u && out->header.struct_size >= OPENSPM_HOOK_SERVICE_V1_SIZE) return OPENSPM_OK;
    if (requested_version == 2u && out->header.struct_size >= OPENSPM_HOOK_SERVICE_V2_SIZE) return OPENSPM_OK;
    return OPENSPM_ERROR_ABI_MISMATCH;
}

/* ---- host side --------------------------------------------------------------------- */

typedef struct openspm_hook_entry {
    uint32_t address;
    const char *name;
    uint32_t expect; /* first instruction word */
} openspm_hook_entry;

#define OPENSPM_HOOK_MAX 64u
#define OPENSPM_HOOK_MAX_CALLBACKS 8u
/* Calls with post callbacks under way at once (all guest threads, nesting
   included). A call that finds it full runs without its post callbacks. */
#define OPENSPM_HOOK_MAX_FRAMES 128u

typedef struct openspm_hook_slot {
    openspm_hook_fn fn;
    void *user;
} openspm_hook_slot;

typedef struct openspm_hook_frame {
    uint32_t sp, lr, id;
    uint32_t args[OPENSPM_HOOK_ARGS];
    double fargs[OPENSPM_HOOK_ARGS];
} openspm_hook_frame;

typedef struct openspm_hook_runtime {
    const openspm_hook_entry *entries;
    uint32_t count;
    uint8_t ok[OPENSPM_HOOK_MAX]; /* first instruction matched at boot */
    openspm_hook_slot pre[OPENSPM_HOOK_MAX][OPENSPM_HOOK_MAX_CALLBACKS];
    openspm_hook_slot post[OPENSPM_HOOK_MAX][OPENSPM_HOOK_MAX_CALLBACKS];
    uint32_t npre[OPENSPM_HOOK_MAX], npost[OPENSPM_HOOK_MAX];
    uint64_t calls[OPENSPM_HOOK_MAX], replaced[OPENSPM_HOOK_MAX];
    openspm_hook_frame frames[OPENSPM_HOOK_MAX_FRAMES];
    uint32_t nframes;
    uint64_t posts_skipped;  /* calls that ran without post callbacks: no free frame */
    uint64_t lost_returns;   /* returns to OPENSPM_HOOK_RETURN_ADDRESS with no frame */
    int verified;
} openspm_hook_runtime;

static inline void openspm_hooks_init(openspm_hook_runtime *rt, const openspm_hook_entry *entries, uint32_t count) {
    memset(rt, 0, sizeof *rt);
    rt->entries = entries;
    rt->count = count < OPENSPM_HOOK_MAX ? count : OPENSPM_HOOK_MAX;
}

/* At boot (guest RAM holds the image): an entry whose first instruction does
   not read as expected is off (its calls continue into the game untouched). */
typedef int (*openspm_hooks_read32_fn)(void *context, uint32_t address, uint32_t *value);
static inline uint32_t openspm_hooks_verify(openspm_hook_runtime *rt, openspm_hooks_read32_fn read32, void *context) {
    uint32_t i, good = 0u;
    for (i = 0; i < rt->count; ++i) {
        uint32_t word = 0u;
        rt->ok[i] = read32 != NULL && read32(context, rt->entries[i].address, &word) &&
                    word == rt->entries[i].expect;
        good += rt->ok[i];
    }
    rt->verified = 1;
    return good;
}

/* Hook id of a hooked address, -1 if not in the table. */
static inline int32_t openspm_hooks_index(const openspm_hook_runtime *rt, uint32_t address) {
    uint32_t i;
    for (i = 0; i < rt->count; ++i)
        if (rt->entries[i].address == address) return (int32_t)i;
    return -1;
}

static inline int openspm_hooks_find(const openspm_hook_runtime *rt, const char *name, uint32_t *out_id) {
    uint32_t i;
    if (name == NULL || out_id == NULL) return OPENSPM_ERROR_INVALID_ARGUMENT;
    for (i = 0; i < rt->count; ++i)
        if (strcmp(rt->entries[i].name, name) == 0) {
            if (rt->verified && !rt->ok[i]) return OPENSPM_ERROR_NOT_FOUND;
            *out_id = i;
            return OPENSPM_OK;
        }
    return OPENSPM_ERROR_NOT_FOUND;
}

static inline int openspm_hooks_add_slot(openspm_hook_slot *slots, uint32_t *n, openspm_hook_fn fn, void *user) {
    if (*n >= OPENSPM_HOOK_MAX_CALLBACKS) return OPENSPM_ERROR_RUNTIME;
    slots[*n].fn = fn;
    slots[*n].user = user;
    ++*n;
    return OPENSPM_OK;
}

static inline int openspm_hooks_add_pre(openspm_hook_runtime *rt, uint32_t id, openspm_hook_fn fn, void *user) {
    if (fn == NULL || id >= rt->count) return OPENSPM_ERROR_INVALID_ARGUMENT;
    return openspm_hooks_add_slot(rt->pre[id], &rt->npre[id], fn, user);
}

static inline int openspm_hooks_add_post(openspm_hook_runtime *rt, uint32_t id, openspm_hook_fn fn, void *user) {
    if (fn == NULL || id >= rt->count) return OPENSPM_ERROR_INVALID_ARGUMENT;
    return openspm_hooks_add_slot(rt->post[id], &rt->npost[id], fn, user);
}

static inline double openspm_hooks_f64(uint64_t bits) {
    double v;
    memcpy(&v, &bits, sizeof v);
    return v;
}

static inline uint64_t openspm_hooks_bits(double v) {
    uint64_t bits;
    memcpy(&bits, &v, sizeof bits);
    return bits;
}

/* f1 from a callback's fret (only when it changed it: an unchanged double
   keeps the register's exact bits). */
static inline void openspm_hooks_set_f1(openspm_m14_hostcall_state *state, const openspm_hook_call *call,
                                        double before) {
    if (call->has_fpr && openspm_hooks_bits(call->fret) != openspm_hooks_bits(before))
        state->fpr_bits[1] = openspm_hooks_bits(call->fret);
}

static inline void openspm_hooks_run_post(openspm_hook_runtime *rt, uint32_t id, openspm_hook_call *call) {
    uint32_t i;
    call->post = 1;
    call->replace = 0;
    for (i = 0; rt->ok[id] && i < rt->npost[id]; ++i) rt->post[id][i].fn(rt->post[id][i].user, call);
}

static inline void openspm_hooks_call_init(openspm_hook_call *call, uint32_t id,
                                           const openspm_m14_hostcall_state *state) {
    uint32_t i;
    memset(call, 0, sizeof *call);
    call->header.struct_size = (uint32_t)sizeof *call;
    call->header.abi_version = OPENSPM_HOOK_SERVICE_VERSION;
    call->hook_id = id;
    call->has_fpr = openspm_m14_hostcall_has_fpr(state);
    for (i = 0; i < OPENSPM_HOOK_ARGS; ++i) {
        call->args[i] = state->gpr[3u + i];
        if (call->has_fpr) call->fargs[i] = openspm_hooks_f64(state->fpr_bits[1u + i]);
    }
    call->ret = state->gpr[3];
    call->fret = call->has_fpr ? call->fargs[0] : 0.0;
    call->return_address = state->lr;
}

/* A host call to hooked function `id`: run its pre callbacks in registration
   order; either return to the caller with the replacement r3 / f1 (post
   callbacks run at once), or continue into the game's own body
   (OPENSPM_M14_HOOK_CONTINUE_ORIGINAL, pc at the entry) with the arguments as
   the callbacks left them. With post callbacks the body returns to
   OPENSPM_HOOK_RETURN_ADDRESS (see openspm_hooks_return). Returns 1 when
   replaced, 0 when continued. */
static inline int openspm_hooks_dispatch(openspm_hook_runtime *rt, uint32_t id, openspm_m14_hostcall_state *state) {
    openspm_hook_call call;
    double fret0;
    uint32_t i;
    state->event_argc = 0u;
    memset(state->event_args, 0, sizeof state->event_args);
    ++rt->calls[id];
    openspm_hooks_call_init(&call, id, state);
    fret0 = call.fret;
    for (i = 0; rt->ok[id] && i < rt->npre[id] && !call.replace; ++i) rt->pre[id][i].fn(rt->pre[id][i].user, &call);
    for (i = 0; i < OPENSPM_HOOK_ARGS; ++i) {
        state->gpr[3u + i] = call.args[i];
        if (call.has_fpr) state->fpr_bits[1u + i] = openspm_hooks_bits(call.fargs[i]); /* unchanged = same bits */
    }
    if (call.replace) {
        ++rt->replaced[id];
        if (rt->ok[id] && rt->npost[id] != 0u) openspm_hooks_run_post(rt, id, &call);
        state->gpr[3] = call.ret;
        openspm_hooks_set_f1(state, &call, fret0);
        state->pc = state->lr;
        state->event_target = 0u;
        return 1;
    }
    if (rt->ok[id] && rt->npost[id] != 0u) {
        if (rt->nframes < OPENSPM_HOOK_MAX_FRAMES) {
            openspm_hook_frame *f = &rt->frames[rt->nframes++];
            f->sp = state->gpr[1];
            f->lr = state->lr;
            f->id = id;
            memcpy(f->args, call.args, sizeof f->args);
            memcpy(f->fargs, call.fargs, sizeof f->fargs);
            state->lr = OPENSPM_HOOK_RETURN_ADDRESS;
        } else {
            ++rt->posts_skipped;
        }
    }
    state->pc = rt->entries[id].address;
    state->event_target = OPENSPM_M14_HOOK_CONTINUE_ORIGINAL;
    return 0;
}

/* A host call to OPENSPM_HOOK_RETURN_ADDRESS: a hooked body returned. Its
   frame is the newest one with this stack pointer (the stack is back where it
   was at the call; nested and other threads' calls have other ones). Runs
   the post callbacks and returns to the real caller. Returns 1, or 0 when no
   frame matches (lost_returns; the caller cannot be recovered). */
static inline int openspm_hooks_return(openspm_hook_runtime *rt, openspm_m14_hostcall_state *state) {
    openspm_hook_call call;
    openspm_hook_frame f;
    uint32_t i = rt->nframes;
    while (i != 0u && rt->frames[i - 1u].sp != state->gpr[1]) --i;
    if (i == 0u) {
        ++rt->lost_returns;
        return 0;
    }
    f = rt->frames[i - 1u];
    memmove(&rt->frames[i - 1u], &rt->frames[i], (rt->nframes - i) * sizeof rt->frames[0]);
    --rt->nframes;
    openspm_hooks_call_init(&call, f.id, state);
    memcpy(call.args, f.args, sizeof call.args);
    memcpy(call.fargs, f.fargs, sizeof call.fargs);
    call.ret = state->gpr[3];
    call.fret = call.has_fpr ? openspm_hooks_f64(state->fpr_bits[1]) : 0.0;
    call.return_address = f.lr;
    {
        const double before = call.fret;
        openspm_hooks_run_post(rt, f.id, &call);
        state->gpr[3] = call.ret;
        openspm_hooks_set_f1(state, &call, before);
    }
    state->lr = f.lr;
    state->pc = f.lr;
    state->event_argc = 0u;
    memset(state->event_args, 0, sizeof state->event_args);
    state->event_target = 0u;
    return 1;
}

#ifdef __cplusplus
}
#endif

#endif
