/* Host test for OpenSPM function hooks (include/openspm/hooks.h): the boot
   check of first instructions, find, pre callbacks in order, changing
   arguments and continuing into the game's body, replacing the call, an entry
   that did not match (untouched), the callback limit; floating-point
   arguments and results, post callbacks through the return address (nested
   calls, two threads, a full frame table), a bridge without FPRs, and the
   service's version check. */
#include "openspm/hooks.h"

#include <assert.h>
#include <stdio.h>

static const openspm_hook_entry k_table[3] = {
    {UINT32_C(0x8023950C), "spsndSFXOn", UINT32_C(0x3880FFFF)},
    {UINT32_C(0x80146ED4), "marioSetCharacter", UINT32_C(0x9421FFF0)},
    {0u, "", 0u},
};

static int fake_read(void *context, uint32_t address, uint32_t *value) {
    (void)context;
    if (address == 0x8023950Cu) { *value = 0x3880FFFFu; return 1; }
    if (address == 0x80146ED4u) { *value = 0x60000000u; return 1; } /* another revision */
    return 0;
}

static int g_order[4], g_count;
static void first(void *user, openspm_hook_call *call) {
    (void)user;
    g_order[g_count++] = 1;
    call->args[0] = 0x80400000u; /* another sound name */
}
static void second(void *user, openspm_hook_call *call) {
    g_order[g_count++] = 2;
    if (*(int *)user) {
        call->replace = 1;
        call->ret = 0xFFFFFFFFu;
    }
}
static void never(void *user, openspm_hook_call *call) {
    (void)user; (void)call;
    assert(0);
}

static openspm_m14_hostcall_state state_for(uint32_t pc) {
    openspm_m14_hostcall_state s;
    memset(&s, 0, sizeof s);
    s.header.struct_size = sizeof s;
    s.pc = pc;
    s.lr = 0x80123456u;
    s.gpr[3] = 0x80331000u;
    s.gpr[4] = 7u;
    s.event_argc = 3u;
    return s;
}


/* Version 2: floating-point arguments and post callbacks. */
static double g_seen_f2;
static void fpre(void *user, openspm_hook_call *call) {
    (void)user;
    assert(call->has_fpr && !call->post);
    g_seen_f2 = call->fargs[1];
    call->fargs[0] = 2.5; /* f1 */
}
static int g_posts;
static uint32_t g_post_ret, g_post_arg;
static void fpost(void *user, openspm_hook_call *call) {
    (void)user;
    assert(call->post);
    ++g_posts;
    g_post_ret = call->ret;
    g_post_arg = call->args[0];
    call->ret += 1u;
    if (call->has_fpr) call->fret *= 2.0;
}
static void freplace(void *user, openspm_hook_call *call) {
    (void)user;
    call->replace = 1;
    call->ret = 5u;
    call->fret = 0.75;
}

static uint64_t bits(double v) {
    uint64_t b;
    memcpy(&b, &v, sizeof b);
    return b;
}
static double f64(uint64_t b) {
    double v;
    memcpy(&v, &b, sizeof v);
    return v;
}

/* The body "returns": r3/f1 hold its result, pc is where lr pointed. */
static void body_returns(openspm_m14_hostcall_state *s, uint32_t r3, double f1) {
    s->gpr[3] = r3;
    s->fpr_bits[1] = bits(f1);
    s->pc = s->lr;
    s->event_target = 0u;
}

static void v2_tests(void) {
    static const openspm_hook_entry table[2] = {
        {UINT32_C(0x80200000), "fn", UINT32_C(0x9421FFF0)},
        {UINT32_C(0x80200100), "other", UINT32_C(0x9421FFE0)},
    };
    static openspm_hook_runtime rt;
    openspm_m14_hostcall_state a, b, c;
    uint32_t i;

    openspm_hooks_init(&rt, table, 2u);
    rt.ok[0] = rt.ok[1] = 1u;
    rt.verified = 1;
    assert(openspm_hooks_add_pre(&rt, 0u, fpre, NULL) == OPENSPM_OK);
    assert(openspm_hooks_add_post(&rt, 0u, fpost, NULL) == OPENSPM_OK);
    assert(openspm_hooks_add_post(&rt, 9u, fpost, NULL) == OPENSPM_ERROR_INVALID_ARGUMENT);

    /* Continue with a post callback: f1 changed, f2 seen, the body returns to
       the return address, the post sees the result and the entry argument. */
    a = state_for(0x80200000u);
    a.gpr[1] = 0x80F00000u;
    a.fpr_bits[1] = bits(1.0);
    a.fpr_bits[2] = bits(-3.0);
    a.fpr_bits[9] = bits(9.0); /* f9: not an argument, untouched */
    assert(openspm_hooks_dispatch(&rt, 0u, &a) == 0);
    assert(g_seen_f2 == -3.0 && f64(a.fpr_bits[1]) == 2.5 && f64(a.fpr_bits[9]) == 9.0);
    assert(a.event_target == OPENSPM_M14_HOOK_CONTINUE_ORIGINAL && a.pc == 0x80200000u);
    assert(a.lr == OPENSPM_HOOK_RETURN_ADDRESS && rt.nframes == 1u);

    /* Nested: the body calls the hooked function again, deeper on the stack. */
    b = a;
    b.lr = 0x80200040u;
    b.gpr[1] = 0x80EFFF00u;
    b.gpr[3] = 0x11u;
    assert(openspm_hooks_dispatch(&rt, 0u, &b) == 0 && rt.nframes == 2u);
    /* Another thread's call, on its own stack. */
    c = state_for(0x80200000u);
    c.gpr[1] = 0x80A00000u;
    c.lr = 0x80300000u;
    c.gpr[3] = 0x22u;
    assert(openspm_hooks_dispatch(&rt, 0u, &c) == 0 && rt.nframes == 3u);

    /* The inner call returns first, then the other thread's, then the outer:
       each finds its own frame by stack pointer. */
    body_returns(&b, 100u, 1.5);
    assert(b.pc == OPENSPM_HOOK_RETURN_ADDRESS);
    assert(openspm_hooks_return(&rt, &b) == 1);
    assert(b.pc == 0x80200040u && b.lr == 0x80200040u && b.gpr[3] == 101u && f64(b.fpr_bits[1]) == 3.0);
    assert(g_post_ret == 100u && g_post_arg == 0x11u && rt.nframes == 2u);
    body_returns(&c, 200u, 0.5);
    assert(openspm_hooks_return(&rt, &c) == 1 && c.pc == 0x80300000u && c.gpr[3] == 201u && g_post_arg == 0x22u);
    body_returns(&a, 300u, 4.0);
    assert(openspm_hooks_return(&rt, &a) == 1);
    assert(a.pc == 0x80123456u && a.gpr[3] == 301u && f64(a.fpr_bits[1]) == 8.0 && g_post_arg == 0x80331000u);
    assert(rt.nframes == 0u && g_posts == 3);

    /* A return nobody recorded. */
    body_returns(&a, 1u, 0.0);
    a.pc = OPENSPM_HOOK_RETURN_ADDRESS;
    assert(openspm_hooks_return(&rt, &a) == 0 && rt.lost_returns == 1u);

    /* Replace with a float result: the post runs at once, no frame. */
    assert(openspm_hooks_add_pre(&rt, 1u, freplace, NULL) == OPENSPM_OK);
    assert(openspm_hooks_add_post(&rt, 1u, fpost, NULL) == OPENSPM_OK);
    a = state_for(0x80200100u);
    a.fpr_bits[1] = bits(1.0);
    assert(openspm_hooks_dispatch(&rt, 1u, &a) == 1);
    assert(a.pc == 0x80123456u && a.gpr[3] == 6u && f64(a.fpr_bits[1]) == 1.5 && rt.nframes == 0u && g_posts == 4);

    /* A full frame table: the call still runs, without its post callbacks. */
    for (i = 0; i < OPENSPM_HOOK_MAX_FRAMES; ++i) {
        a = state_for(0x80200000u);
        a.gpr[1] = 0x80800000u - 64u * i;
        assert(openspm_hooks_dispatch(&rt, 0u, &a) == 0 && a.lr == OPENSPM_HOOK_RETURN_ADDRESS);
    }
    a = state_for(0x80200000u);
    assert(openspm_hooks_dispatch(&rt, 0u, &a) == 0 && a.lr == 0x80123456u && rt.posts_skipped == 1u);

    /* A bridge built before the FPRs: the shorter state; integer hooks work,
       no float is read or written. */
    {
        static openspm_hook_runtime rt1;
        openspm_hooks_init(&rt1, table, 2u);
        rt1.ok[1] = 1u;
        rt1.verified = 1;
        assert(openspm_hooks_add_pre(&rt1, 1u, freplace, NULL) == OPENSPM_OK);
        a = state_for(0x80200100u);
        a.header.struct_size = OPENSPM_M14_HOSTCALL_STATE_V1_SIZE;
        a.fpr_bits[1] = bits(1.0);
        assert(!openspm_m14_hostcall_has_fpr(&a));
        assert(openspm_hooks_dispatch(&rt1, 1u, &a) == 1 && a.gpr[3] == 5u && f64(a.fpr_bits[1]) == 1.0);
    }

    /* The service: version 1 needs the v1 struct, version 2 the v2 one. */
    {
        openspm_hook_service svc;
        memset(&svc, 0, sizeof svc);
        svc.header.struct_size = (uint32_t)OPENSPM_HOOK_SERVICE_V1_SIZE;
        assert(openspm_hook_accept(&svc, 1u) == OPENSPM_OK);
        assert(openspm_hook_accept(&svc, 2u) == OPENSPM_ERROR_ABI_MISMATCH);
        svc.header.struct_size = (uint32_t)sizeof svc;
        assert(openspm_hook_accept(&svc, 2u) == OPENSPM_OK && openspm_hook_accept(&svc, 3u) == OPENSPM_ERROR_ABI_MISMATCH);
    }
    assert(OPENSPM_HOOK_RETURN_ADDRESS < 0x80003100u);
}

int main(void) {
    static openspm_hook_runtime rt;
    openspm_m14_hostcall_state s;
    uint32_t id = 99u, i;
    int replace = 0;

    openspm_hooks_init(&rt, k_table, 2u);
    assert(openspm_hooks_index(&rt, 0x8023950Cu) == 0 && openspm_hooks_index(&rt, 0x80000000u) == -1);
    /* Before the boot check a mod can already find and register. */
    assert(openspm_hooks_find(&rt, "spsndSFXOn", &id) == OPENSPM_OK && id == 0u);
    assert(openspm_hooks_find(&rt, "nope", &id) == OPENSPM_ERROR_NOT_FOUND);
    assert(openspm_hooks_add_pre(&rt, 0u, first, NULL) == OPENSPM_OK);
    assert(openspm_hooks_add_pre(&rt, 0u, second, &replace) == OPENSPM_OK);
    assert(openspm_hooks_add_pre(&rt, 1u, never, NULL) == OPENSPM_OK);
    assert(openspm_hooks_add_pre(&rt, 5u, first, NULL) == OPENSPM_ERROR_INVALID_ARGUMENT);

    /* Boot check: the second entry does not match this game. */
    assert(openspm_hooks_verify(&rt, fake_read, NULL) == 1u);
    assert(openspm_hooks_find(&rt, "marioSetCharacter", &id) == OPENSPM_ERROR_NOT_FOUND);

    /* Continue: callbacks in order, changed argument, the game's body runs. */
    s = state_for(0x8023950Cu);
    assert(openspm_hooks_dispatch(&rt, 0u, &s) == 0);
    assert(g_count == 2 && g_order[0] == 1 && g_order[1] == 2);
    assert(s.gpr[3] == 0x80400000u && s.gpr[4] == 7u);
    assert(s.pc == 0x8023950Cu && s.event_target == OPENSPM_M14_HOOK_CONTINUE_ORIGINAL && s.event_argc == 0u);

    /* Replace: back to the caller with r3 = the replacement. */
    replace = 1;
    g_count = 0;
    s = state_for(0x8023950Cu);
    assert(openspm_hooks_dispatch(&rt, 0u, &s) == 1);
    assert(s.gpr[3] == 0xFFFFFFFFu && s.pc == 0x80123456u && s.event_target == 0u);
    assert(rt.calls[0] == 2u && rt.replaced[0] == 1u);

    /* The entry that did not match: its callbacks never run, the call
       continues untouched. */
    s = state_for(0x80146ED4u);
    assert(openspm_hooks_dispatch(&rt, 1u, &s) == 0);
    assert(s.gpr[3] == 0x80331000u && s.pc == 0x80146ED4u && s.event_target == OPENSPM_M14_HOOK_CONTINUE_ORIGINAL);

    /* At most OPENSPM_HOOK_MAX_CALLBACKS per hook. */
    for (i = rt.npre[0]; i < OPENSPM_HOOK_MAX_CALLBACKS; ++i) assert(openspm_hooks_add_pre(&rt, 0u, first, NULL) == OPENSPM_OK);
    assert(openspm_hooks_add_pre(&rt, 0u, first, NULL) == OPENSPM_ERROR_RUNTIME);

    /* The generated table of this checkout (no hook enabled) compiles. */
    {
#include "openspm/hooks_table.inc"
        assert(k_openspm_hook_table[OPENSPM_HOOK_TABLE_COUNT].address == 0u);
    }
    v2_tests();
    puts("PASS: OpenSPM hooks");
    return 0;
}
