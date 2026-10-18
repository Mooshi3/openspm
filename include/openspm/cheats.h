/* OpenSPM cheats: guest-memory writes applied once per frame at a safe point.
 *
 * Design (docs/PLAYING.md "Cheats", docs/MODDING.md):
 *   - The F1 menu (renderer) owns the UI and OpenSPM.ini [Cheats]. It keeps one
 *     openspm_cheat_table (built-in cheat flags + parsed custom writes) behind a
 *     mutex and exports openspm_renderer_cheats() to copy it out. It never
 *     writes guest memory itself.
 *   - The backend looks that export up with GetModuleHandleA/GetProcAddress
 *     (like the renderer gamepad export) and, at the PRESENT_SAFEPOINT host
 *     event (Broadway paused between two guest frames, where input is latched),
 *     copies the table and runs openspm_cheats_apply(). So every write lands
 *     before the next simulation frame and never races the game or the render
 *     thread.
 *   - Everything is off by default. The export returns 0 when nothing is
 *     enabled and the backend then does nothing at all.
 *
 * Portable C (no Windows headers): the Gecko parser, address validation and the
 * apply step are host unit-tested by tests/m14_cheats_test.c.
 *
 * Addresses are R8PE01 rev 2 (US) facts, verified against the game's own code:
 *   MarioPouchWork 0x804D0428: pouchGetPtr (0x8014B9EC) returns it; pouchInit
 *   (0x8014B9F8) clears 0x6A0 bytes and clamps +0x0C hp to 0..maxHp, +0x10 maxHp
 *   to 0..999, +0x1C coins to 0..999; pouchSetHp (0x8014CD78) clamps +0x0C to
 *   +0x10; pouchSetCoin (0x8014CEAC), pouchGetCoin (0x8014CEE0) and
 *   pouchAddCoin (0x8014CEF0) use +0x1C with a 999 cap.
 *   SEQ_WORK 0x804D0D60: +0 current sequence, 2 = in game (backend and F1 menu
 *   game-state readout). */
#ifndef OPENSPM_CHEATS_H
#define OPENSPM_CHEATS_H

#include <stddef.h>
#include <stdint.h>

#include "openspm/abi.h"

#ifdef __cplusplus
extern "C" {
#endif

#define OPENSPM_CHEAT_INFINITE_HP UINT32_C(0x00000001)
/* 0x00000002 was "Max coins": setting the count softlocked shops (their
   scripts wait for the count to settle at the new value). Replaced by the
   one-shot gift below, which the game's own item code handles. */
#define OPENSPM_CHEAT_KNOWN_FLAGS OPENSPM_CHEAT_INFINITE_HP

/* One-shot gift: each press of the F1 menu button bumps gift_serial; the
   backend then asks the game to add the item with its own pouchAddItem
   (0x8014CF5C, one argument: the item id) at the frame safepoint, only in
   game. Gold Bar x3 (item 72, "KINKAI_300") sells for coins. */
#define OPENSPM_CHEAT_POUCH_ADD_ITEM UINT32_C(0x8014CF5C)
#define OPENSPM_CHEAT_GIFT_ITEM UINT32_C(72)

#define OPENSPM_CHEAT_MAX_WRITES 128u

#define OPENSPM_CHEAT_SEQ_WORK UINT32_C(0x804D0D60)
#define OPENSPM_CHEAT_SEQ_GAME UINT32_C(2)
#define OPENSPM_CHEAT_POUCH UINT32_C(0x804D0428)
#define OPENSPM_CHEAT_POUCH_HP UINT32_C(0x0C)
#define OPENSPM_CHEAT_POUCH_MAX_HP UINT32_C(0x10)
#define OPENSPM_CHEAT_POUCH_COINS UINT32_C(0x1C)
#define OPENSPM_CHEAT_HP_CAP UINT32_C(999)
#define OPENSPM_CHEAT_COINS_CAP UINT32_C(999)

/* Guest ranges a custom write may target (cached views of the Wii's RAM). */
#define OPENSPM_CHEAT_MEM1_BEGIN UINT32_C(0x80000000)
#define OPENSPM_CHEAT_MEM1_END UINT32_C(0x81800000) /* 24 MiB */
#define OPENSPM_CHEAT_MEM2_BEGIN UINT32_C(0x90000000)
#define OPENSPM_CHEAT_MEM2_END UINT32_C(0x94000000) /* 64 MiB */

typedef struct openspm_cheat_write {
    uint32_t address; /* validated guest address (4-byte aligned) */
    uint32_t value;   /* big-endian 32-bit value written there */
} openspm_cheat_write;

typedef struct openspm_cheat_table {
    uint32_t struct_size; /* sizeof(openspm_cheat_table) */
    uint32_t flags;       /* OPENSPM_CHEAT_* built-in cheats */
    uint32_t write_count; /* custom writes used in writes[] */
    uint32_t gift_serial; /* bumped by each "give" press (0: never pressed) */
    openspm_cheat_write writes[OPENSPM_CHEAT_MAX_WRITES];
} openspm_cheat_table;

/* Renderer export. Fills *out and returns 1 when any cheat is enabled or a
   gift was ever requested; returns 0 (and leaves nothing to apply) otherwise. */
#define OPENSPM_RENDERER_CHEATS_EXPORT "openspm_renderer_cheats"
typedef int(OPENSPM_CALL *openspm_renderer_cheats_fn)(openspm_cheat_table *out);

/* 1 when [address, address + 4) is a 4-byte-aligned MEM1 or MEM2 address. */
static inline int openspm_cheat_address_ok(uint32_t address) {
    if ((address & 3u) != 0u) return 0;
    if (address >= OPENSPM_CHEAT_MEM1_BEGIN && address <= OPENSPM_CHEAT_MEM1_END - 4u) return 1;
    if (address >= OPENSPM_CHEAT_MEM2_BEGIN && address <= OPENSPM_CHEAT_MEM2_END - 4u) return 1;
    return 0;
}

static inline int openspm_cheat_hex_digit(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

static inline void openspm_cheat_error(char *error, size_t capacity, const char *what, unsigned line) {
    size_t i = 0u;
    char digits[12];
    unsigned n = 0u, value = line;
    if (error == NULL || capacity == 0u) return;
    /* Tiny formatter: "line N: what" without pulling in stdio. */
    do { digits[n++] = (char)('0' + value % 10u); value /= 10u; } while (value != 0u && n < sizeof digits);
    {
        const char *prefix = "line ";
        while (*prefix && i + 1u < capacity) error[i++] = *prefix++;
    }
    while (n > 0u && i + 1u < capacity) error[i++] = digits[--n];
    if (i + 1u < capacity) error[i++] = ':';
    if (i + 1u < capacity) error[i++] = ' ';
    while (*what && i + 1u < capacity) error[i++] = *what++;
    error[i] = '\0';
}

/* Parses Gecko "04XXXXXX YYYYYYYY" (32-bit write) codes. 05 is the same code
   type for addresses 0x81000000..0x817FFFFF (Gecko's base-address bit 24).
   Words are 8 hex digits separated by spaces, tabs, commas or newlines; the two
   words of a code may sit on one line or span lines. Anything else (other
   Gecko/Action Replay code types, bad hex, an address outside MEM1/MEM2 or not
   4-byte aligned, more than `capacity` codes) rejects the whole text so a
   half-understood code list never runs. Returns 1 on success with *count set;
   0 with *count = 0 and a message in `error`. Empty text is a valid, empty
   list. */
static inline int openspm_cheat_parse_gecko(const char *text, openspm_cheat_write *out, uint32_t capacity,
                                            uint32_t *count, char *error, size_t error_capacity) {
    uint32_t used = 0u, words[2] = {0u, 0u};
    unsigned line = 1u, have = 0u;
    if (count != NULL) *count = 0u;
    if (error != NULL && error_capacity > 0u) error[0] = '\0';
    if (count == NULL || (out == NULL && capacity != 0u)) return 0;
    if (text == NULL) return 1;
    for (;;) {
        char c = *text;
        uint32_t word = 0u;
        int digits = 0, d;
        if (c == '\n') { ++line; ++text; continue; }
        if (c == ' ' || c == '\t' || c == '\r' || c == ',') { ++text; continue; }
        if (c == '\0') break;
        while ((d = openspm_cheat_hex_digit(*text)) >= 0) {
            if (digits == 8) break;
            word = (word << 4) | (uint32_t)d;
            ++digits;
            ++text;
        }
        c = *text;
        if (digits != 8 || !(c == '\0' || c == ' ' || c == '\t' || c == '\r' || c == '\n' || c == ',')) {
            openspm_cheat_error(error, error_capacity, "expected 8 hex digits per word", line);
            return 0;
        }
        words[have++] = word;
        if (have < 2u) continue;
        have = 0u;
        {
            const uint32_t type = words[0] >> 24;
            uint32_t address;
            if (type != 0x04u && type != 0x05u) {
                openspm_cheat_error(error, error_capacity,
                                    "only 04/05 (32-bit write) codes are supported", line);
                return 0;
            }
            address = OPENSPM_CHEAT_MEM1_BEGIN | (words[0] & UINT32_C(0x01FFFFFF));
            if (!openspm_cheat_address_ok(address)) {
                openspm_cheat_error(error, error_capacity,
                                    "address outside MEM1/MEM2 or not 4-byte aligned", line);
                return 0;
            }
            if (used >= capacity) {
                openspm_cheat_error(error, error_capacity, "too many codes", line);
                return 0;
            }
            out[used].address = address;
            out[used].value = words[1];
            ++used;
        }
    }
    if (have != 0u) {
        openspm_cheat_error(error, error_capacity, "code is missing its value word", line);
        return 0;
    }
    *count = used;
    return 1;
}

/* Carries the one-shot gift across frames. */
typedef struct openspm_cheat_state {
    uint32_t last_gift_serial; /* last press handled */
} openspm_cheat_state;

/* 1 once for each new press, when the game is in play (seq 2); a press made
   outside the game waits for it. The caller queues the pouchAddItem call. */
static inline int openspm_cheats_gift_due(const openspm_cheat_table *table, openspm_cheat_state *state, uint32_t seq) {
    if (table == 0 || state == 0 || table->gift_serial == state->last_gift_serial) return 0;
    if (seq != OPENSPM_CHEAT_SEQ_GAME) return 0;
    state->last_gift_serial = table->gift_serial;
    return 1;
}

typedef int (*openspm_cheat_read32_fn)(void *context, uint32_t address, uint32_t *value);
typedef int (*openspm_cheat_write32_fn)(void *context, uint32_t address, uint32_t value);

/* Runs one frame of cheats. Call only at the backend's once-per-frame safe
   point. Built-in cheats act only in game (SEQ_WORK sequence 2) and never
   revive Mario (HP 0 is left to the game's own death/Game Over flow). Custom
   writes are re-validated and applied every frame, like Gecko. state is
   unused here (kept for the gift, openspm_cheats_gift_due). Returns the
   number of guest words changed. */
static inline unsigned openspm_cheats_apply(const openspm_cheat_table *table, openspm_cheat_state *state,
                                            openspm_cheat_read32_fn read32, openspm_cheat_write32_fn write32,
                                            void *context) {
    unsigned changed = 0u;
    uint32_t i, seq = UINT32_MAX;
    if (table == NULL || read32 == NULL || write32 == NULL || table->struct_size != sizeof(*table)) return 0u;
    if ((table->flags & OPENSPM_CHEAT_KNOWN_FLAGS) != 0u && !read32(context, OPENSPM_CHEAT_SEQ_WORK, &seq))
        seq = UINT32_MAX;
    (void)state;
    if ((table->flags & OPENSPM_CHEAT_KNOWN_FLAGS) != 0u && seq == OPENSPM_CHEAT_SEQ_GAME) {
        if ((table->flags & OPENSPM_CHEAT_INFINITE_HP) != 0u) {
            uint32_t hp = 0u, max_hp = 0u;
            const uint32_t hp_address = OPENSPM_CHEAT_POUCH + OPENSPM_CHEAT_POUCH_HP;
            if (read32(context, OPENSPM_CHEAT_POUCH + OPENSPM_CHEAT_POUCH_MAX_HP, &max_hp) &&
                read32(context, hp_address, &hp) && max_hp >= 1u && max_hp <= OPENSPM_CHEAT_HP_CAP &&
                (int32_t)hp > 0 && hp < max_hp && write32(context, hp_address, max_hp))
                ++changed;
        }
    }
    for (i = 0u; i < table->write_count && i < OPENSPM_CHEAT_MAX_WRITES; ++i) {
        const openspm_cheat_write *w = &table->writes[i];
        uint32_t current = 0u;
        if (!openspm_cheat_address_ok(w->address)) continue;
        if (read32(context, w->address, &current) && current == w->value) continue;
        if (write32(context, w->address, w->value)) ++changed;
    }
    return changed;
}

#ifdef __cplusplus
}
#endif

#endif
