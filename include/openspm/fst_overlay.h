/* Mod files: new paths in the game's file table (docs/CUSTOM-LEVELS.md).
 *
 * The game finds files through the disc's FST (file system table), which
 * OpenSPM's boot places in guest memory (0x80000038 = its address,
 * 0x8000003C = its size). openspm_fst_extend() makes a copy with more
 * files: every original entry keeps its position and length, new files join
 * their directories (made when missing) with positions no disc file uses, so
 * a read of one can be told apart and served from the mod's folder. A path
 * the game already has is refused: mods add files, they do not replace them.
 *
 * FST format: 12-byte entries, then the name strings. An entry is
 *   word 0: type (1 = directory) << 24 | name offset;
 *   file:   word 1 position (in 4-byte units on Wii), word 2 length;
 *   directory: word 1 parent's index, word 2 the index after its last
 *   descendant.
 * Entry 0 is the root (its word 2 = the entry count); a directory's
 * children follow it.
 *
 * Portable C (malloc); tests/m14_fst_overlay_test.c. */
#ifndef OPENSPM_FST_OVERLAY_H
#define OPENSPM_FST_OVERLAY_H

#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "openspm/abi.h"

/* Where the extended FST goes (OpenSPM's MEM2, between the moved tables and
   the guest scratch area). */
#define OPENSPM_FST_OVERLAY_ADDRESS UINT32_C(0x93EB0000)
#define OPENSPM_FST_OVERLAY_CAPACITY UINT32_C(0x00050000)
/* New files' positions start here (4-byte units: byte 0xF0000000, past any
   disc), 16 units apart. */
#define OPENSPM_FST_OVERLAY_FIRST_POSITION UINT32_C(0x3C000000)
#define OPENSPM_FST_OVERLAY_STRIDE 16u

typedef struct openspm_fst_addition {
    const char *path;   /* "map/os1_01.bin" (no leading slash) */
    uint32_t length;
    uint32_t position;  /* out: the position given, 0 = refused */
} openspm_fst_addition;

static inline uint32_t openspm_fst_be32(const uint8_t *p) {
    return (uint32_t)p[0] << 24 | (uint32_t)p[1] << 16 | (uint32_t)p[2] << 8 | p[3];
}

static inline void openspm_fst_put32(uint8_t *p, uint32_t v) {
    p[0] = (uint8_t)(v >> 24);
    p[1] = (uint8_t)(v >> 16);
    p[2] = (uint8_t)(v >> 8);
    p[3] = (uint8_t)v;
}

static inline int openspm_fst_name_eq(const char *a, size_t alen, const char *b) {
    size_t i;
    for (i = 0; i < alen; ++i) {
        char x = a[i], y = b[i];
        if (y == 0) return 0;
        if (x >= 'A' && x <= 'Z') x = (char)(x - 'A' + 'a');
        if (y >= 'A' && y <= 'Z') y = (char)(y - 'A' + 'a');
        if (x != y) return 0;
    }
    return b[alen] == 0;
}

/* Checks the table's shape: entry count, names in range, directories'
   ranges nested. */
static inline int openspm_fst_valid(const uint8_t *fst, uint32_t size, uint32_t *out_count) {
    uint32_t count, i, names;
    if (fst == NULL || size < 12u) return 0;
    count = openspm_fst_be32(fst + 8);
    if (count == 0u || (uint64_t)count * 12u > size || !(fst[0] & 1u)) return 0;
    names = count * 12u;
    for (i = 0; i < count; ++i) {
        const uint8_t *e = fst + 12u * i;
        const uint32_t name = openspm_fst_be32(e) & 0xFFFFFFu;
        if (i && names + name >= size) return 0;
        if (e[0] & 1u) {
            const uint32_t parent = openspm_fst_be32(e + 4), next = openspm_fst_be32(e + 8);
            if ((i && parent >= i) || next <= i || next > count) return 0;
        }
    }
    if (out_count) *out_count = count;
    return 1;
}

/* The entry for `path` (case-insensitive, like the SDK), -1 if none. */
static inline int32_t openspm_fst_find(const uint8_t *fst, uint32_t size, const char *path) {
    uint32_t count, dir = 0u;
    const char *p = path;
    if (!openspm_fst_valid(fst, size, &count) || path == NULL) return -1;
    while (*p == '/') ++p;
    for (;;) {
        const char *end = p;
        uint32_t i, last, found = UINT32_MAX;
        size_t len;
        while (*end && *end != '/') ++end;
        len = (size_t)(end - p);
        if (len == 0u) return (int32_t)dir;
        last = openspm_fst_be32(fst + 12u * dir + 8);
        for (i = dir + 1u; i < last;) {
            const uint8_t *e = fst + 12u * i;
            const char *name = (const char *)fst + count * 12u + (openspm_fst_be32(e) & 0xFFFFFFu);
            if (openspm_fst_name_eq(p, len, name)) {
                found = i;
                break;
            }
            i = (e[0] & 1u) ? openspm_fst_be32(e + 8) : i + 1u; /* skip a directory's contents */
        }
        if (found == UINT32_MAX) return -1;
        if (*end == 0) return (int32_t)found;
        if (!(fst[12u * found] & 1u)) return -1;
        dir = found;
        p = end + 1;
    }
}

/* ---- extending -------------------------------------------------------------- */

typedef struct openspm_fst_node {
    const char *name;      /* into the original table, or an addition's path */
    uint32_t name_len;
    int dir;
    uint32_t position, length;
    int32_t first_child, next_sibling, last_child;
} openspm_fst_node;

typedef struct openspm_fst_build {
    openspm_fst_node *nodes;
    uint32_t count, capacity;
} openspm_fst_build;

static inline int32_t openspm_fst_new_node(openspm_fst_build *b) {
    if (b->count == b->capacity) {
        const uint32_t cap = b->capacity ? b->capacity * 2u : 256u;
        openspm_fst_node *grown = (openspm_fst_node *)realloc(b->nodes, cap * sizeof *grown);
        if (grown == NULL) return -1;
        b->nodes = grown;
        b->capacity = cap;
    }
    memset(&b->nodes[b->count], 0, sizeof b->nodes[0]);
    b->nodes[b->count].first_child = b->nodes[b->count].next_sibling = b->nodes[b->count].last_child = -1;
    return (int32_t)b->count++;
}

static inline void openspm_fst_link(openspm_fst_build *b, int32_t parent, int32_t child) {
    openspm_fst_node *p = &b->nodes[parent];
    if (p->last_child < 0) p->first_child = child;
    else b->nodes[p->last_child].next_sibling = child;
    p->last_child = child;
}

/* Pre-order write; returns the index after the subtree, or 0 on overflow. */
static inline uint32_t openspm_fst_write_node(const openspm_fst_build *b, int32_t n, uint32_t parent_index,
                                              uint32_t index, uint8_t *entries, uint32_t total, uint8_t *names,
                                              uint32_t *names_used, uint32_t names_capacity) {
    const openspm_fst_node *node = &b->nodes[n];
    uint8_t *e = entries + 12u * index;
    uint32_t name_off = 0u, next;
    int32_t c;
    if (index >= total) return 0u;
    if (index) {
        if (*names_used + node->name_len + 1u > names_capacity) return 0u;
        name_off = *names_used;
        memcpy(names + name_off, node->name, node->name_len);
        names[name_off + node->name_len] = 0;
        *names_used += node->name_len + 1u;
    } else if (names_capacity) {
        names[0] = 0; /* the root's name */
        *names_used = 1u;
    }
    openspm_fst_put32(e, (node->dir ? 0x01000000u : 0u) | name_off);
    if (!node->dir) {
        openspm_fst_put32(e + 4, node->position);
        openspm_fst_put32(e + 8, node->length);
        return index + 1u;
    }
    openspm_fst_put32(e + 4, parent_index);
    next = index + 1u;
    for (c = node->first_child; c >= 0; c = b->nodes[c].next_sibling) {
        next = openspm_fst_write_node(b, c, index, next, entries, total, names, names_used, names_capacity);
        if (next == 0u) return 0u;
    }
    openspm_fst_put32(e + 8, next);
    return next;
}

/* Writes the extended table to `out`. Each addition's `position` is set
   (0 when refused: the path, or a directory on its way, is a file the game
   has, or it names a directory). Returns OPENSPM_OK (some may be refused),
   OPENSPM_ERROR_INVALID_ARGUMENT for a bad table, OPENSPM_ERROR_RUNTIME when
   `out` is too small. */
static inline int openspm_fst_extend(const uint8_t *fst, uint32_t fst_size, openspm_fst_addition *adds,
                                     uint32_t add_count, uint8_t *out, uint32_t capacity, uint32_t *out_size) {
    openspm_fst_build b;
    uint32_t count, i, position = OPENSPM_FST_OVERLAY_FIRST_POSITION, total, names_used = 0u;
    int32_t *map = NULL, stack[64];
    int result = OPENSPM_OK;
    if (!openspm_fst_valid(fst, fst_size, &count) || out == NULL || out_size == NULL)
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    memset(&b, 0, sizeof b);
    map = (int32_t *)malloc(count * sizeof *map);
    if (map == NULL) return OPENSPM_ERROR_RUNTIME;
    /* The original tree. */
    {
        uint32_t depth = 0u, ends[64];
        for (i = 0; i < count; ++i) {
            const uint8_t *e = fst + 12u * i;
            const int32_t n = openspm_fst_new_node(&b);
            if (n < 0) {
                result = OPENSPM_ERROR_RUNTIME;
                goto done;
            }
            while (depth && i >= ends[depth - 1u]) --depth;
            map[i] = n;
            b.nodes[n].dir = e[0] & 1u;
            if (i) {
                b.nodes[n].name = (const char *)fst + count * 12u + (openspm_fst_be32(e) & 0xFFFFFFu);
                {
                    const uint32_t room = fst_size - (uint32_t)(b.nodes[n].name - (const char *)fst);
                    uint32_t len = 0u;
                    while (len < room && b.nodes[n].name[len]) ++len;
                    b.nodes[n].name_len = len;
                }
                openspm_fst_link(&b, stack[depth - 1u], n);
            }
            if (b.nodes[n].dir) {
                if (depth == 64u) {
                    result = OPENSPM_ERROR_INVALID_ARGUMENT;
                    goto done;
                }
                stack[depth] = n;
                ends[depth++] = openspm_fst_be32(e + 8);
            } else {
                b.nodes[n].position = openspm_fst_be32(e + 4);
                b.nodes[n].length = openspm_fst_be32(e + 8);
                if (b.nodes[n].position >= position) /* stay clear of every disc file */
                    position = (b.nodes[n].position + OPENSPM_FST_OVERLAY_STRIDE) & ~(OPENSPM_FST_OVERLAY_STRIDE - 1u);
            }
        }
    }
    /* The additions. */
    for (i = 0; i < add_count; ++i) {
        const char *p = adds[i].path;
        int32_t dir = map[0];
        adds[i].position = 0u;
        if (p == NULL) continue;
        while (*p == '/') ++p;
        for (;;) {
            const char *end = p;
            int32_t c, found = -1;
            size_t len;
            while (*end && *end != '/') ++end;
            len = (size_t)(end - p);
            if (len == 0u) break;
            for (c = b.nodes[dir].first_child; c >= 0; c = b.nodes[c].next_sibling) {
                char tmp[256];
                if (b.nodes[c].name_len >= sizeof tmp) continue;
                memcpy(tmp, b.nodes[c].name, b.nodes[c].name_len);
                tmp[b.nodes[c].name_len] = 0;
                if (openspm_fst_name_eq(p, len, tmp)) {
                    found = c;
                    break;
                }
            }
            if (*end == 0) { /* the file itself */
                if (found < 0) {
                    const int32_t n = openspm_fst_new_node(&b);
                    if (n < 0) {
                        result = OPENSPM_ERROR_RUNTIME;
                        goto done;
                    }
                    b.nodes[n].name = p;
                    b.nodes[n].name_len = (uint32_t)len;
                    b.nodes[n].position = position;
                    b.nodes[n].length = adds[i].length;
                    openspm_fst_link(&b, dir, n);
                    adds[i].position = position;
                    position += OPENSPM_FST_OVERLAY_STRIDE;
                }
                break;
            }
            if (found >= 0 && !b.nodes[found].dir) break; /* a file where a directory should be */
            if (found < 0) {
                found = openspm_fst_new_node(&b);
                if (found < 0) {
                    result = OPENSPM_ERROR_RUNTIME;
                    goto done;
                }
                b.nodes[found].dir = 1;
                b.nodes[found].name = p;
                b.nodes[found].name_len = (uint32_t)len;
                openspm_fst_link(&b, dir, found);
            }
            dir = found;
            p = end + 1;
        }
    }
    total = b.count;
    if ((uint64_t)total * 12u > capacity) {
        result = OPENSPM_ERROR_RUNTIME;
        goto done;
    }
    if (openspm_fst_write_node(&b, 0, 0u, 0u, out, total, out + total * 12u, &names_used, capacity - total * 12u) !=
        total) {
        result = OPENSPM_ERROR_RUNTIME;
        goto done;
    }
    *out_size = (total * 12u + names_used + 3u) & ~3u;
    if (*out_size > capacity) {
        result = OPENSPM_ERROR_RUNTIME;
        goto done;
    }
    memset(out + total * 12u + names_used, 0, *out_size - total * 12u - names_used);
done:
    free(map);
    free(b.nodes);
    return result;
}

#endif
