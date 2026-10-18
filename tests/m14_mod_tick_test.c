/* Host test for the per-frame mod tick (docs/MODDING.md): runtime-event to
   mod-event mapping, the openspm.guest_memory service version check, address
   validation (the same ranges the cheats use) and the access window that is
   open only during the TICK_BEGIN callback, against a fake MEM1/MEM2. */
#include "openspm/guest_memory.h"
#include "openspm/mod_api.h"

#include <assert.h>
#include <stdio.h>
#include <string.h>

/* 64 KiB at the start of each arena is enough for the test addresses. */
#define FAKE_SPAN 0x10000u
static uint8_t g_mem1[FAKE_SPAN];
static uint8_t g_mem2[FAKE_SPAN];
static uint8_t g_seq_page[FAKE_SPAN]; /* 0x804D0000.. (SEQ_WORK) */
#define SEQ_PAGE UINT32_C(0x804D0000)
static int g_mem2_attached = 1;

static uint8_t *fake_resolve(void *context, uint32_t address, uint32_t size) {
    (void)context;
    if (address >= SEQ_PAGE && address - SEQ_PAGE <= FAKE_SPAN - size) return g_seq_page + (address - SEQ_PAGE);
    if (address >= OPENSPM_CHEAT_MEM1_BEGIN && address - OPENSPM_CHEAT_MEM1_BEGIN <= FAKE_SPAN - size)
        return g_mem1 + (address - OPENSPM_CHEAT_MEM1_BEGIN);
    if (g_mem2_attached && address >= OPENSPM_CHEAT_MEM2_BEGIN && address - OPENSPM_CHEAT_MEM2_BEGIN <= FAKE_SPAN - size)
        return g_mem2 + (address - OPENSPM_CHEAT_MEM2_BEGIN);
    return NULL;
}

static openspm_guest_window g_window = {0, fake_resolve, NULL};

static void test_event_mapping(void) {
    openspm_mod_event e = (openspm_mod_event)0;
    assert(openspm_mod_event_from_runtime(OPENSPM_RUNTIME_EVENT_TICK_BEGIN, &e) && e == OPENSPM_MOD_EVENT_TICK_BEGIN);
    assert(openspm_mod_event_from_runtime(OPENSPM_RUNTIME_EVENT_TICK_END, &e) && e == OPENSPM_MOD_EVENT_TICK_END);
    assert(openspm_mod_event_from_runtime(OPENSPM_RUNTIME_EVENT_PRESENT_END, &e) && e == OPENSPM_MOD_EVENT_PRESENT_END);
    assert(openspm_mod_event_from_runtime(OPENSPM_RUNTIME_EVENT_CORE_RUNNING, &e) && e == OPENSPM_MOD_EVENT_RUNTIME_RUNNING);
    assert(openspm_mod_event_from_runtime(OPENSPM_RUNTIME_EVENT_CORE_STOPPED, &e) && e == OPENSPM_MOD_EVENT_RUNTIME_STOPPED);
    e = OPENSPM_MOD_EVENT_STARTUP;
    assert(!openspm_mod_event_from_runtime(0u, &e) && e == OPENSPM_MOD_EVENT_STARTUP);
    assert(!openspm_mod_event_from_runtime(99u, &e));
    assert(openspm_mod_event_from_runtime(OPENSPM_RUNTIME_EVENT_TICK_BEGIN, NULL));
    /* Existing mod event IDs are frozen ABI. */
    assert(OPENSPM_MOD_EVENT_TICK_BEGIN == 3 && OPENSPM_MOD_EVENT_PRESENT_END == 6);
}

static void test_service_versioning(void) {
    openspm_guest_memory_service s;
    memset(&s, 0, sizeof s);
    assert(openspm_guest_memory_accept(NULL, OPENSPM_GUEST_MEMORY_SERVICE_VERSION) == OPENSPM_ERROR_INVALID_ARGUMENT);
    s.header.struct_size = (uint32_t)sizeof s;
    assert(openspm_guest_memory_accept(&s, OPENSPM_GUEST_MEMORY_SERVICE_VERSION) == OPENSPM_OK);
    assert(openspm_guest_memory_accept(&s, 0u) == OPENSPM_ERROR_ABI_MISMATCH);
    assert(openspm_guest_memory_accept(&s, OPENSPM_GUEST_MEMORY_SERVICE_VERSION + 1u) == OPENSPM_ERROR_ABI_MISMATCH);
    s.header.struct_size = (uint32_t)sizeof s - 1u;
    assert(openspm_guest_memory_accept(&s, OPENSPM_GUEST_MEMORY_SERVICE_VERSION) == OPENSPM_ERROR_ABI_MISMATCH);
    s.header.struct_size = (uint32_t)sizeof s + 64u; /* a newer caller with a larger struct */
    assert(openspm_guest_memory_accept(&s, OPENSPM_GUEST_MEMORY_SERVICE_VERSION) == OPENSPM_OK);
    /* The host API sink this relies on already exists in ABI v1 (appended). */
    assert(offsetof(openspm_host_api, runtime_event) > offsetof(openspm_host_api, query_service));
}

static void test_address_validation(void) {
    static const uint32_t samples[] = {
        0x7FFFFFFCu, 0x80000000u, 0x80000001u, 0x80000002u, 0x804D0D60u, 0x817FFFFCu, 0x817FFFFEu,
        0x817FFFFFu, 0x81800000u, 0x8FFFFFFCu, 0x90000000u, 0x93FFFFFCu, 0x93FFFFFFu, 0x94000000u,
        0xC0000000u, 0xCC000000u, 0x00000000u, 0xFFFFFFFCu};
    size_t i;
    for (i = 0; i < sizeof samples / sizeof samples[0]; ++i)
        assert(openspm_guest_address_ok(samples[i], 4u) == openspm_cheat_address_ok(samples[i]));
    assert(openspm_guest_address_ok(0x817FFFFFu, 1u));
    assert(!openspm_guest_address_ok(0x81800000u, 1u));
    assert(openspm_guest_address_ok(0x817FFFFEu, 2u));
    assert(!openspm_guest_address_ok(0x817FFFFFu, 2u)); /* misaligned */
    assert(!openspm_guest_address_ok(0x80000001u, 2u));
    assert(openspm_guest_address_ok(0x93FFFFFFu, 1u));
    assert(!openspm_guest_address_ok(0x94000000u, 1u));
    assert(!openspm_guest_address_ok(0xCC008000u, 4u)); /* hardware registers */
    assert(!openspm_guest_address_ok(0x80000000u, 0u));
    assert(!openspm_guest_address_ok(0x80000000u, 3u));
    assert(!openspm_guest_address_ok(0x80000000u, 8u));
}

static void test_window(void) {
    uint32_t v = 0xDEADBEEFu;
    memset(g_mem1, 0, sizeof g_mem1);
    memset(g_mem2, 0, sizeof g_mem2);
    /* Closed window: nothing is read or written. */
    assert(openspm_guest_window_write(&g_window, 0x80000100u, 4u, 0x11223344u) == OPENSPM_ERROR_UNSUPPORTED);
    assert(openspm_guest_window_read(&g_window, 0x80000100u, 4u, &v) == OPENSPM_ERROR_UNSUPPORTED);
    assert(v == 0xDEADBEEFu && g_mem1[0x100] == 0u);
    assert(openspm_guest_window_read(NULL, 0x80000100u, 4u, &v) == OPENSPM_ERROR_UNSUPPORTED);

    g_window.active = 1;
    assert(openspm_guest_window_write(&g_window, 0x80000100u, 4u, 0x11223344u) == OPENSPM_OK);
    assert(g_mem1[0x100] == 0x11 && g_mem1[0x101] == 0x22 && g_mem1[0x102] == 0x33 && g_mem1[0x103] == 0x44);
    assert(openspm_guest_window_read(&g_window, 0x80000100u, 4u, &v) == OPENSPM_OK && v == 0x11223344u);
    assert(openspm_guest_window_read(&g_window, 0x80000102u, 2u, &v) == OPENSPM_OK && v == 0x3344u);
    assert(openspm_guest_window_read(&g_window, 0x80000101u, 1u, &v) == OPENSPM_OK && v == 0x22u);
    assert(openspm_guest_window_write(&g_window, 0x80000104u, 2u, 0xABCDu) == OPENSPM_OK);
    assert(g_mem1[0x104] == 0xAB && g_mem1[0x105] == 0xCD);
    assert(openspm_guest_window_write(&g_window, 0x80000107u, 1u, 0x5Au) == OPENSPM_OK && g_mem1[0x107] == 0x5A);
    assert(openspm_guest_window_write(&g_window, 0x90000010u, 4u, 0xCAFEF00Du) == OPENSPM_OK);
    assert(openspm_guest_window_read(&g_window, 0x90000010u, 4u, &v) == OPENSPM_OK && v == 0xCAFEF00Du);

    /* Validation: misaligned, out of range, bad size, NULL out. */
    assert(openspm_guest_window_write(&g_window, 0x80000102u, 4u, 1u) == OPENSPM_ERROR_INVALID_ARGUMENT);
    assert(openspm_guest_window_write(&g_window, 0x80000101u, 2u, 1u) == OPENSPM_ERROR_INVALID_ARGUMENT);
    assert(openspm_guest_window_write(&g_window, 0xCC000000u, 4u, 1u) == OPENSPM_ERROR_INVALID_ARGUMENT);
    assert(openspm_guest_window_write(&g_window, 0x81800000u, 1u, 1u) == OPENSPM_ERROR_INVALID_ARGUMENT);
    assert(openspm_guest_window_write(&g_window, 0x80000100u, 3u, 1u) == OPENSPM_ERROR_INVALID_ARGUMENT);
    assert(openspm_guest_window_read(&g_window, 0x80000100u, 4u, NULL) == OPENSPM_ERROR_INVALID_ARGUMENT);
    /* Valid address whose backing is not attached. */
    g_mem2_attached = 0;
    assert(openspm_guest_window_read(&g_window, 0x90000010u, 4u, &v) == OPENSPM_ERROR_RUNTIME);
    g_mem2_attached = 1;
    g_window.active = 0;
}

/* The backend's tick protocol, modelled: no sink -> nothing runs; with a sink
   the window is open exactly for the duration of TICK_BEGIN. */
typedef struct fake_mod {
    unsigned ticks, presents, other;
    uint32_t seq_seen;
    int write_rc_in_tick;
} fake_mod;

static void OPENSPM_CALL fake_mod_event(void *context, openspm_mod_event id, const void *data) {
    fake_mod *mod = (fake_mod *)context;
    if (id == OPENSPM_MOD_EVENT_TICK_BEGIN) {
        const openspm_runtime_tick_event *tick = (const openspm_runtime_tick_event *)data;
        assert(tick && tick->header.struct_size >= sizeof(*tick));
        ++mod->ticks;
        assert(openspm_guest_window_read(&g_window, OPENSPM_CHEAT_SEQ_WORK, 4u, &mod->seq_seen) == OPENSPM_OK);
        mod->write_rc_in_tick = openspm_guest_window_write(&g_window, 0x80000200u, 4u, (uint32_t)tick->tick_count);
    } else if (id == OPENSPM_MOD_EVENT_PRESENT_END) {
        uint32_t v;
        ++mod->presents;
        /* Guest memory is closed outside TICK_BEGIN. */
        assert(openspm_guest_window_read(&g_window, OPENSPM_CHEAT_SEQ_WORK, 4u, &v) == OPENSPM_ERROR_UNSUPPORTED);
    } else {
        ++mod->other;
    }
}

typedef struct fake_host {
    openspm_mod_api *mods;
    size_t count;
} fake_host;

static void OPENSPM_CALL fake_host_sink(void *context, uint32_t event_id, const void *event_data) {
    fake_host *host = (fake_host *)context;
    openspm_mod_event e;
    size_t i;
    if (!openspm_mod_event_from_runtime(event_id, &e)) return;
    for (i = 0; i < host->count; ++i) host->mods[i].on_event(host->mods[i].mod_context, e, event_data);
}

static unsigned g_sink_calls;
static void fake_frame(openspm_runtime_event_sink_fn sink, void *sink_context, uint64_t frame) {
    openspm_runtime_tick_event tick;
    openspm_runtime_present_event present;
    if (sink == NULL) return; /* zero-cost path: one pointer check */
    ++g_sink_calls;
    memset(&tick, 0, sizeof tick);
    tick.header.struct_size = (uint32_t)sizeof tick;
    tick.header.abi_version = OPENSPM_RUNTIME_SERVICE_VERSION;
    tick.tick_count = frame;
    g_window.active = 1;
    sink(sink_context, OPENSPM_RUNTIME_EVENT_TICK_BEGIN, &tick);
    g_window.active = 0;
    memset(&present, 0, sizeof present);
    present.header.struct_size = (uint32_t)sizeof present;
    present.header.abi_version = OPENSPM_RUNTIME_SERVICE_VERSION;
    present.frame_count = frame;
    present.present_count = frame;
    sink(sink_context, OPENSPM_RUNTIME_EVENT_PRESENT_END, &present);
}

static void test_tick_protocol(void) {
    fake_mod state;
    openspm_mod_api api;
    fake_host host;
    uint64_t f;
    uint32_t v = 0u;
    memset(&state, 0, sizeof state);
    memset(&api, 0, sizeof api);
    api.header.struct_size = (uint32_t)sizeof api;
    api.header.abi_version = OPENSPM_ABI_VERSION;
    api.mod_id = "test.tick";
    api.mod_version = "1";
    api.mod_context = &state;
    api.on_event = fake_mod_event;
    host.mods = &api;
    host.count = 1u;
    memset(g_mem1, 0, sizeof g_mem1);
    memset(g_seq_page, 0, sizeof g_seq_page);
    g_seq_page[(OPENSPM_CHEAT_SEQ_WORK - SEQ_PAGE) + 3u] = (uint8_t)OPENSPM_CHEAT_SEQ_GAME; /* seq = 2 */

    for (f = 1u; f <= 3u; ++f) fake_frame(NULL, &host, f);
    assert(g_sink_calls == 0u && state.ticks == 0u);

    for (f = 1u; f <= 600u; ++f) fake_frame(fake_host_sink, &host, f);
    assert(state.ticks == 600u && state.presents == 600u && state.other == 0u);
    assert(state.write_rc_in_tick == OPENSPM_OK && state.seq_seen == OPENSPM_CHEAT_SEQ_GAME);
    g_window.active = 1;
    assert(openspm_guest_window_read(&g_window, 0x80000200u, 4u, &v) == OPENSPM_OK && v == 600u);
    g_window.active = 0;
    assert(openspm_guest_window_write(&g_window, 0x80000200u, 4u, 1u) == OPENSPM_ERROR_UNSUPPORTED);
}

int main(void) {
    test_event_mapping();
    test_service_versioning();
    test_address_validation();
    test_window();
    test_tick_protocol();
    puts("m14_mod_tick_test: ok");
    return 0;
}
