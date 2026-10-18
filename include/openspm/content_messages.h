/* OpenSPM messages: mods' own message keys and texts (provider side of
 * openspm.content's register_message, include/openspm/content_service.h;
 * docs/CUSTOM-LEVELS.md "Messages").
 *
 * The game's message work (msgdrv, pointer at 0x8056C918) has 9 tables of
 * 16 bytes { size, data base, count, handle -> entries }, entries
 * { name offset, text offset } sorted by name; msgSearch binary-searches the
 * tables in order. OpenSPM builds one more table from every registered
 * message (sorted, in guest scratch memory) and keeps it in a free table
 * slot (map loads clear the slots they reuse, so it is checked each frame).
 * The game's tables come first: a key the game has keeps the game's text,
 * mods only add.
 *
 * Texts are the game's message format: plain text with the game's tags
 * ("<p>" next page, "<k>" wait for a button, ...); docs/CUSTOM-LEVELS.md.
 *
 * Runs inside the mod tick with the roster's guest access
 * (openspm_roster_io). Portable C, header-only.
 * tests/m14_content_messages_test.c. */
#ifndef OPENSPM_CONTENT_MESSAGES_H
#define OPENSPM_CONTENT_MESSAGES_H

#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "openspm/content_roster.h"

#define OPENSPM_MESSAGES_MAX 512u
#define OPENSPM_MESSAGES_KEY 64u
#define OPENSPM_MESSAGES_TEXT 2048u

typedef struct openspm_message {
    char key[OPENSPM_MESSAGES_KEY];
    char *text; /* malloc'd */
} openspm_message;

typedef struct openspm_messages {
    openspm_message m[OPENSPM_MESSAGES_MAX];
    uint32_t count;
    int dirty;
    uint32_t base, size, entries, handle; /* the guest table */
} openspm_messages;

static inline void openspm_messages_init(openspm_messages *s) { memset(s, 0, sizeof *s); }

static inline void openspm_messages_free(openspm_messages *s) {
    uint32_t i;
    for (i = 0; i < s->count; ++i) free(s->m[i].text);
    memset(s, 0, sizeof *s);
}

/* Keys: 1-63 printable ASCII characters, no spaces; texts: up to 2047
   bytes. Registering a key again replaces its text. */
static inline int openspm_messages_register(openspm_messages *s, const char *key, const char *text) {
    size_t klen, tlen, i;
    uint32_t k;
    char *copy;
    if (key == NULL || text == NULL) return OPENSPM_ERROR_INVALID_ARGUMENT;
    klen = strlen(key);
    tlen = strlen(text);
    if (klen == 0u || klen >= OPENSPM_MESSAGES_KEY || tlen >= OPENSPM_MESSAGES_TEXT) return OPENSPM_ERROR_INVALID_ARGUMENT;
    for (i = 0; i < klen; ++i)
        if ((unsigned char)key[i] <= 0x20u || (unsigned char)key[i] >= 0x7Fu) return OPENSPM_ERROR_INVALID_ARGUMENT;
    copy = (char *)malloc(tlen + 1u);
    if (copy == NULL) return OPENSPM_ERROR_RUNTIME;
    memcpy(copy, text, tlen + 1u);
    for (k = 0; k < s->count; ++k)
        if (strcmp(s->m[k].key, key) == 0) {
            free(s->m[k].text);
            s->m[k].text = copy;
            s->dirty = 1;
            return OPENSPM_OK;
        }
    if (s->count == OPENSPM_MESSAGES_MAX) {
        free(copy);
        return OPENSPM_ERROR_RUNTIME;
    }
    memcpy(s->m[s->count].key, key, klen + 1u);
    s->m[s->count].text = copy;
    ++s->count;
    s->dirty = 1;
    return OPENSPM_OK;
}

static inline int openspm_messages_cmp(const void *a, const void *b) {
    return strcmp(((const openspm_message *)a)->key, ((const openspm_message *)b)->key);
}

static inline int openspm_messages_write(const openspm_roster_io *io, uint32_t at, const uint8_t *bytes, uint32_t n) {
    uint32_t i;
    for (i = 0; i < n; i += 4u) {
        uint32_t w = 0u, k;
        for (k = 0; k < 4u; ++k) w |= (uint32_t)(i + k < n ? bytes[i + k] : 0u) << (24u - 8u * k);
        if (io->write32(io->context, at + i, w) != OPENSPM_OK) return 0;
    }
    return 1;
}

/* Builds the guest table (sorted) when messages changed. */
static inline int openspm_messages_build(openspm_messages *s, const openspm_roster_io *io) {
    uint32_t i, used = 0u, base = 0u, entries = 0u, handle = 0u;
    uint8_t *blob, *table;
    if (!s->dirty) return 1;
    if (s->count == 0u) {
        s->dirty = 0;
        s->handle = 0u;
        return 1;
    }
    qsort(s->m, s->count, sizeof s->m[0], openspm_messages_cmp);
    for (i = 0; i < s->count; ++i) used += (uint32_t)(strlen(s->m[i].key) + strlen(s->m[i].text) + 2u);
    blob = (uint8_t *)malloc(used);
    table = (uint8_t *)malloc(8u * s->count);
    if (blob == NULL || table == NULL) {
        free(blob);
        free(table);
        return 0;
    }
    used = 0u;
    for (i = 0; i < s->count; ++i) {
        const uint32_t name = used, klen = (uint32_t)strlen(s->m[i].key) + 1u;
        const uint32_t tlen = (uint32_t)strlen(s->m[i].text) + 1u;
        memcpy(blob + used, s->m[i].key, klen);
        used += klen;
        memcpy(blob + used, s->m[i].text, tlen);
        table[8u * i + 0u] = (uint8_t)(name >> 24);
        table[8u * i + 1u] = (uint8_t)(name >> 16);
        table[8u * i + 2u] = (uint8_t)(name >> 8);
        table[8u * i + 3u] = (uint8_t)name;
        table[8u * i + 4u] = (uint8_t)(used >> 24);
        table[8u * i + 5u] = (uint8_t)(used >> 16);
        table[8u * i + 6u] = (uint8_t)(used >> 8);
        table[8u * i + 7u] = (uint8_t)used;
        used += tlen;
    }
    if (io->scratch(io->context, (used + 3u) & ~3u, &base) != OPENSPM_OK ||
        io->scratch(io->context, 8u * s->count, &entries) != OPENSPM_OK ||
        io->scratch(io->context, 4u, &handle) != OPENSPM_OK || !openspm_messages_write(io, base, blob, used) ||
        !openspm_messages_write(io, entries, table, 8u * s->count) ||
        io->write32(io->context, handle, entries) != OPENSPM_OK) {
        free(blob);
        free(table);
        return 0;
    }
    free(blob);
    free(table);
    s->base = base;
    s->size = used;
    s->entries = entries;
    s->handle = handle;
    s->dirty = 0;
    return 1;
}

/* Each frame: the table is in a message slot (the highest free one), built
   first when needed. */
static inline void openspm_messages_tick(openspm_messages *s, const openspm_roster_io *io) {
    int ok = 1;
    uint32_t work, i, free_slot = 0u;
    if (s->count == 0u || !openspm_messages_build(s, io) || s->handle == 0u) return;
    work = openspm_roster_rd(io, OPENSPM_ROSTER_MSG_WORK_PTR, &ok);
    if (!ok || work == 0u) return;
    for (i = 1; i < OPENSPM_ROSTER_MSG_TABLES; ++i) {
        const uint32_t slot = work + 16u * i;
        const uint32_t handle = openspm_roster_rd(io, slot + 12u, &ok);
        if (!ok) return;
        if (handle == s->handle) return; /* still there */
        if (handle == 0u) free_slot = slot;
    }
    if (free_slot == 0u) return;
    (void)openspm_roster_wr(io, free_slot + 0u, s->size);
    (void)openspm_roster_wr(io, free_slot + 4u, s->base);
    (void)openspm_roster_wr(io, free_slot + 8u, s->count);
    (void)openspm_roster_wr(io, free_slot + 12u, s->handle);
}

#endif
