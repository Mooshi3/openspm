/* Host test for include/openspm/cheats.h: Gecko 04/05 parsing, guest address
   validation and the once-per-frame apply step against a fake guest memory. */
#include "openspm/cheats.h"

#include <assert.h>
#include <stdio.h>
#include <string.h>

typedef struct fake_memory {
    uint32_t address[16];
    uint32_t value[16];
    unsigned used;
    unsigned writes;
} fake_memory;

static uint32_t *fake_slot(fake_memory *m, uint32_t address, int create) {
    unsigned i;
    for (i = 0; i < m->used; ++i)
        if (m->address[i] == address) return &m->value[i];
    if (!create || m->used == 16u) return NULL;
    m->address[m->used] = address;
    m->value[m->used] = 0u;
    return &m->value[m->used++];
}
static void fake_set(fake_memory *m, uint32_t address, uint32_t value) { *fake_slot(m, address, 1) = value; }
static uint32_t fake_get(fake_memory *m, uint32_t address) {
    uint32_t *slot = fake_slot(m, address, 0);
    return slot != NULL ? *slot : 0xDEADBEEFu;
}
static int fake_read(void *context, uint32_t address, uint32_t *value) {
    uint32_t *slot = fake_slot((fake_memory *)context, address, 0);
    if (slot == NULL) return 0;
    *value = *slot;
    return 1;
}
static int fake_write(void *context, uint32_t address, uint32_t value) {
    fake_memory *m = (fake_memory *)context;
    ++m->writes;
    fake_set(m, address, value);
    return 1;
}

#define HP (OPENSPM_CHEAT_POUCH + OPENSPM_CHEAT_POUCH_HP)
#define MAX_HP (OPENSPM_CHEAT_POUCH + OPENSPM_CHEAT_POUCH_MAX_HP)
#define COINS (OPENSPM_CHEAT_POUCH + OPENSPM_CHEAT_POUCH_COINS)

static void game(fake_memory *m, uint32_t seq, uint32_t hp, uint32_t max_hp, uint32_t coins) {
    memset(m, 0, sizeof *m);
    fake_set(m, OPENSPM_CHEAT_SEQ_WORK, seq);
    fake_set(m, HP, hp);
    fake_set(m, MAX_HP, max_hp);
    fake_set(m, COINS, coins);
}

int main(void) {
    openspm_cheat_write w[4];
    uint32_t count = 99u;
    char error[96];
    openspm_cheat_table table;
    fake_memory m;

    /* Address validation: MEM1/MEM2 cached, aligned, whole word inside. */
    assert(openspm_cheat_address_ok(0x80000000u) && openspm_cheat_address_ok(0x817FFFFCu));
    assert(!openspm_cheat_address_ok(0x817FFFFEu) && !openspm_cheat_address_ok(0x81800000u));
    assert(openspm_cheat_address_ok(0x90000000u) && openspm_cheat_address_ok(0x93FFFFFCu));
    assert(!openspm_cheat_address_ok(0x94000000u) && !openspm_cheat_address_ok(0x7FFFFFFCu));
    assert(!openspm_cheat_address_ok(0xC0000000u) && !openspm_cheat_address_ok(0x804D0429u));
    assert(!openspm_cheat_address_ok(0x00000000u) && !openspm_cheat_address_ok(0xFFFFFFFCu));

    /* Gecko parsing. */
    assert(openspm_cheat_parse_gecko("", w, 4u, &count, error, sizeof error) && count == 0u);
    assert(openspm_cheat_parse_gecko(NULL, w, 4u, &count, error, sizeof error) && count == 0u);
    assert(openspm_cheat_parse_gecko("044D0444 000003E7", w, 4u, &count, error, sizeof error) && count == 1u);
    assert(w[0].address == 0x804D0444u && w[0].value == 0x3E7u);
    assert(openspm_cheat_parse_gecko(" 044d0434 00000063\r\n\t0500AB00,DEADBEEF\n", w, 4u, &count, error,
                                     sizeof error));
    assert(count == 2u && w[0].address == 0x804D0434u && w[0].value == 0x63u);
    assert(w[1].address == 0x8100AB00u && w[1].value == 0xDEADBEEFu);
    assert(openspm_cheat_parse_gecko("04000000\n12345678", w, 4u, &count, error, sizeof error) && count == 1u);
    /* Rejections leave count 0 and say why (with the line). */
    assert(!openspm_cheat_parse_gecko("044D0444", w, 4u, &count, error, sizeof error) && count == 0u);
    assert(strstr(error, "missing its value") != NULL);
    assert(!openspm_cheat_parse_gecko("044D0444 00000063\n024D0444 00000063", w, 4u, &count, error, sizeof error));
    assert(count == 0u && strstr(error, "only 04/05") != NULL && strncmp(error, "line 2:", 7) == 0);
    assert(!openspm_cheat_parse_gecko("C2000000 00000001", w, 4u, &count, error, sizeof error));
    assert(!openspm_cheat_parse_gecko("044D0446 00000063", w, 4u, &count, error, sizeof error));   /* unaligned */
    assert(strstr(error, "MEM1/MEM2") != NULL);
    assert(!openspm_cheat_parse_gecko("05800000 00000001", w, 4u, &count, error, sizeof error));   /* past MEM1 */
    assert(!openspm_cheat_parse_gecko("044D044 00000063", w, 4u, &count, error, sizeof error));    /* 7 digits */
    assert(!openspm_cheat_parse_gecko("044D04440 0000063", w, 4u, &count, error, sizeof error));   /* 9 digits */
    assert(!openspm_cheat_parse_gecko("044D0444 0000006G", w, 4u, &count, error, sizeof error));   /* bad hex */
    assert(!openspm_cheat_parse_gecko("$Coins 044D0444 00000063", w, 4u, &count, error, sizeof error));
    assert(!openspm_cheat_parse_gecko("04000000 0 04000004 1 04000008 2", w, 4u, &count, error, sizeof error));
    assert(!openspm_cheat_parse_gecko(
        "04000000 00000000 04000004 00000000 04000008 00000000 0400000C 00000000 04000010 00000000", w, 4u, &count,
        error, sizeof error));
    assert(strstr(error, "too many") != NULL && count == 0u);
    assert(openspm_cheat_parse_gecko("044D0444 00000063", w, 4u, &count, NULL, 0u) && count == 1u);
    assert(!openspm_cheat_parse_gecko("044D0444", w, 4u, &count, NULL, 0u)); /* no error buffer: fine */
    (void)count;

    /* Apply: nothing enabled -> no reads that matter, no writes. */
    memset(&table, 0, sizeof table);
    table.struct_size = sizeof table;
    game(&m, 2u, 3u, 10u, 5u);
    assert(openspm_cheats_apply(&table, NULL, fake_read, fake_write, &m) == 0u && m.writes == 0u);
    /* Wrong struct size: ignored. */
    table.flags = OPENSPM_CHEAT_INFINITE_HP | 0x2u; /* 0x2: the retired Max coins bit, ignored */
    table.struct_size = 8u;
    assert(openspm_cheats_apply(&table, NULL, fake_read, fake_write, &m) == 0u && m.writes == 0u);
    table.struct_size = sizeof table;
    /* In game: HP refilled to max; coins are never touched. Second frame: nothing. */
    assert(openspm_cheats_apply(&table, NULL, fake_read, fake_write, &m) == 1u);
    assert(fake_get(&m, HP) == 10u && fake_get(&m, COINS) == 5u && fake_get(&m, MAX_HP) == 10u);
    assert(openspm_cheats_apply(&table, NULL, fake_read, fake_write, &m) == 0u && m.writes == 1u);
    /* Not in game (title, map change): built-ins do nothing. */
    game(&m, 3u, 3u, 10u, 5u);
    assert(openspm_cheats_apply(&table, NULL, fake_read, fake_write, &m) == 0u && m.writes == 0u);
    /* HP 0 (dying) is never revived; implausible max HP is left alone. */
    game(&m, 2u, 0u, 10u, 999u);
    assert(openspm_cheats_apply(&table, NULL, fake_read, fake_write, &m) == 0u && fake_get(&m, HP) == 0u);
    game(&m, 2u, 3u, 0u, 999u);
    assert(openspm_cheats_apply(&table, NULL, fake_read, fake_write, &m) == 0u);
    game(&m, 2u, 3u, 5000u, 999u);
    assert(openspm_cheats_apply(&table, NULL, fake_read, fake_write, &m) == 0u);
    /* The retired Max coins bit alone does nothing. */
    table.flags = 0x2u;
    game(&m, 2u, 3u, 10u, 5u);
    assert(openspm_cheats_apply(&table, NULL, fake_read, fake_write, &m) == 0u && fake_get(&m, COINS) == 5u);
    /* Custom writes: applied every frame they differ, any sequence; invalid
       addresses in a hand-built table are skipped. */
    table.flags = 0u;
    table.write_count = 2u;
    table.writes[0].address = 0x80001000u;
    table.writes[0].value = 0x12345678u;
    table.writes[1].address = 0x80001002u; /* unaligned: skipped */
    table.writes[1].value = 1u;
    game(&m, 0u, 3u, 10u, 5u);
    assert(openspm_cheats_apply(&table, NULL, fake_read, fake_write, &m) == 1u && fake_get(&m, 0x80001000u) == 0x12345678u);
    assert(fake_slot(&m, 0x80001002u, 0) == NULL);
    assert(openspm_cheats_apply(&table, NULL, fake_read, fake_write, &m) == 0u);
    fake_set(&m, 0x80001000u, 7u); /* the game changed it back */
    assert(openspm_cheats_apply(&table, NULL, fake_read, fake_write, &m) == 1u && fake_get(&m, 0x80001000u) == 0x12345678u);
    table.write_count = 1000u; /* corrupt count: bounded by the array */
    assert(openspm_cheats_apply(&table, NULL, fake_read, fake_write, &m) == 0u);

    /* Gift: once per press, only in game; a press outside the game waits. */
    {
        openspm_cheat_state state;
        memset(&state, 0, sizeof state);
        memset(&table, 0, sizeof table);
        table.struct_size = sizeof table;
        assert(!openspm_cheats_gift_due(&table, &state, 2u)); /* never pressed */
        table.gift_serial = 1u;
        assert(openspm_cheats_gift_due(&table, &state, 2u));
        assert(!openspm_cheats_gift_due(&table, &state, 2u)); /* once */
        table.gift_serial = 2u;
        assert(!openspm_cheats_gift_due(&table, &state, 3u)); /* map change: waits */
        assert(openspm_cheats_gift_due(&table, &state, 2u));
        table.gift_serial = 4u; /* two presses in one frame: one gift per frame seen */
        assert(openspm_cheats_gift_due(&table, &state, 2u) && !openspm_cheats_gift_due(&table, &state, 2u));
        assert(!openspm_cheats_gift_due(NULL, &state, 2u) && !openspm_cheats_gift_due(&table, NULL, 2u));
    }
    assert(openspm_cheats_apply(NULL, NULL, fake_read, fake_write, &m) == 0u);
    puts("PASS: cheats");
    return 0;
}
