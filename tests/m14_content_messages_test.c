/* Host test for include/openspm/content_messages.h: registered messages
   become one table sorted by key in guest memory (entries of name / text
   offsets from the base), installed into a free message slot of the
   game's message work, put back after a map load clears it, rebuilt when a
   text changes; bad keys refused. A binary search like msgSearch finds every
   key. */
#include "openspm/content_messages.h"
#include "openspm/guest_call.h"

#include <assert.h>
#include <stdio.h>

#define MEM1 0x01800000u
#define WORK 0x80500000u
static uint8_t *g_mem;
static uint32_t g_scratch_used;

static uint8_t *P(uint32_t a) {
    if (a >= 0x80000000u && a < 0x80000000u + MEM1) return g_mem + (a - 0x80000000u);
    if (a >= OPENSPM_GUEST_SCRATCH_BEGIN && a < OPENSPM_GUEST_SCRATCH_BEGIN + OPENSPM_GUEST_SCRATCH_SIZE)
        return g_mem + 0x01000000u + (a - OPENSPM_GUEST_SCRATCH_BEGIN);
    return NULL;
}
static void w32(uint32_t a, uint32_t v) { uint8_t *p = P(a); p[0] = v >> 24; p[1] = v >> 16; p[2] = v >> 8; p[3] = v; }
static uint32_t r32(uint32_t a) { const uint8_t *p = P(a); return (uint32_t)p[0] << 24 | p[1] << 16 | p[2] << 8 | p[3]; }
static int f_read32(void *c, uint32_t a, uint32_t *v) { (void)c; if (!P(a)) return OPENSPM_ERROR_INVALID_ARGUMENT; *v = r32(a); return OPENSPM_OK; }
static int f_write32(void *c, uint32_t a, uint32_t v) { (void)c; if (!P(a)) return OPENSPM_ERROR_INVALID_ARGUMENT; w32(a, v); return OPENSPM_OK; }
static int f_scratch(void *c, uint32_t s, uint32_t *o) { (void)c; return openspm_guest_scratch_take(&g_scratch_used, s, o); }
static const openspm_roster_io k_io = {NULL, f_read32, f_write32, NULL, f_scratch, NULL, NULL, NULL};

/* msgSearch over one table slot. */
static const char *search(uint32_t slot, const char *key) {
    const uint32_t base = r32(slot + 4u), count = r32(slot + 8u), entries = r32(r32(slot + 12u));
    uint32_t lo = 0, hi = count;
    while (lo < hi) {
        const uint32_t mid = (lo + hi) / 2u;
        const int c = strcmp(key, (const char *)P(base + r32(entries + 8u * mid)));
        if (c == 0) return (const char *)P(base + r32(entries + 8u * mid + 4u));
        if (c < 0) hi = mid;
        else lo = mid + 1u;
    }
    return NULL;
}

int main(void) {
    static openspm_messages s;
    const uint32_t slot8 = WORK + 16u * 8u, slot7 = WORK + 16u * 7u;
    g_mem = calloc(1, MEM1);
    assert(g_mem);
    w32(OPENSPM_ROSTER_MSG_WORK_PTR, WORK);
    openspm_messages_init(&s);
    /* Nothing registered: nothing installed. */
    openspm_messages_tick(&s, &k_io);
    assert(r32(slot8 + 12u) == 0u);
    assert(openspm_messages_register(&s, "os1_mayor_01", "Welcome!<k>") == OPENSPM_OK);
    assert(openspm_messages_register(&s, "os1_a", "First.<k>") == OPENSPM_OK);
    assert(openspm_messages_register(&s, "os1_zz", "Last.<k>") == OPENSPM_OK);
    assert(openspm_messages_register(&s, "os1 bad", "x") == OPENSPM_ERROR_INVALID_ARGUMENT);
    assert(openspm_messages_register(&s, "", "x") == OPENSPM_ERROR_INVALID_ARGUMENT);
    openspm_messages_tick(&s, &k_io);
    assert(r32(slot8 + 8u) == 3u && r32(slot8 + 12u) == s.handle);
    assert(strcmp(search(slot8, "os1_mayor_01"), "Welcome!<k>") == 0 && strcmp(search(slot8, "os1_a"), "First.<k>") == 0 &&
           strcmp(search(slot8, "os1_zz"), "Last.<k>") == 0 && search(slot8, "os1_b") == NULL);
    /* A map load takes slot 8 for its own table: ours goes to the highest
       free one. */
    w32(slot8 + 12u, 0x80600000u);
    openspm_messages_tick(&s, &k_io);
    assert(r32(slot7 + 12u) == s.handle && r32(slot8 + 12u) == 0x80600000u);
    /* Still there: not installed twice. */
    openspm_messages_tick(&s, &k_io);
    assert(r32(WORK + 16u * 6u + 12u) == 0u);
    /* A changed text: rebuilt (a new handle), and installed again. */
    {
        const uint32_t old = s.handle;
        assert(openspm_messages_register(&s, "os1_a", "Changed.<k>") == OPENSPM_OK && s.count == 3u);
        openspm_messages_tick(&s, &k_io);
        assert(s.handle != old && r32(WORK + 16u * 6u + 12u) == s.handle);
        assert(strcmp(search(WORK + 16u * 6u, "os1_a"), "Changed.<k>") == 0);
    }
    openspm_messages_free(&s);
    free(g_mem);
    puts("PASS: content messages");
    return 0;
}
