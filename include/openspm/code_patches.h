/* OpenSPM content patches, boot side (docs/CONTENT-API.md).
 *
 * The table (code_patches_table.inc) is generated from
 * state/openspm-code-patches.json. The build applies every patch to the
 * player-local codegen DOL before recompiling; the backend applies the
 * "boot" ones to the boot image in MEM1 right after loading main.dol (patches
 * in relF are written by the game's own REL loader at runtime, so only the
 * recompiled module has them) and copies each relocated table to its new home
 * in MEM2. Each patch is one instruction, checked against the word it
 * replaces; vanilla behaviour must not change while no mod content is
 * registered.
 *
 * Portable C; no game code: only addresses and instruction encodings. */
#ifndef OPENSPM_CODE_PATCHES_H
#define OPENSPM_CODE_PATCHES_H

#include <stddef.h>
#include <stdint.h>
#include <string.h>

typedef struct openspm_code_patch {
    const char *name;
    uint32_t address;
    uint32_t expected;
    uint32_t replacement;
    int boot; /* applied to the boot image too */
} openspm_code_patch;

typedef struct openspm_code_relocation {
    const char *name;
    uint32_t from; /* MEM1 */
    uint32_t size;
    uint32_t to; /* MEM2 */
    uint32_t row_size, rows; /* a per-character table: rows past the copy start as row 0 (0: none) */
} openspm_code_relocation;

#include "openspm/code_patches_table.inc"

#define OPENSPM_CODE_PATCH_COUNT (sizeof k_openspm_code_patches / sizeof k_openspm_code_patches[0])
#define OPENSPM_CODE_RELOCATION_COUNT (sizeof k_openspm_code_relocations / sizeof k_openspm_code_relocations[0])

/* Capacity the character / Pixl lists get from the patches. */
#define OPENSPM_CHARTAB_CAPACITY 64u
#define OPENSPM_PIXLTAB_CAPACITY 64u

/* The relocated item table (itemDataTable): the vanilla 538 entries are
   copied here at boot; OpenSPM writes added items after them (ids 538+). */
#define OPENSPM_ITEM_DATA_RELOCATED UINT32_C(0x93E07E78)
#define OPENSPM_ITEM_DATA_VANILLA_COUNT 538u
#define OPENSPM_ITEM_DATA_MAX_COUNT 4096u /* room up to 0x93F00000 is far larger */

/* The patch with this name, or NULL. */
static inline const openspm_code_patch *openspm_code_patch_named(const char *name) {
    size_t i;
    for (i = 0; i < OPENSPM_CODE_PATCH_COUNT; ++i)
        if (strcmp(k_openspm_code_patches[i].name, name) == 0) return &k_openspm_code_patches[i];
    return NULL;
}

/* The relocation of the table at `from` (MEM1), or NULL. */
static inline const openspm_code_relocation *openspm_code_relocation_of(uint32_t from) {
    size_t i;
    for (i = 0; i < OPENSPM_CODE_RELOCATION_COUNT; ++i)
        if (k_openspm_code_relocations[i].from == from) return &k_openspm_code_relocations[i];
    return NULL;
}

/* Applies every boot patch to a MEM1 image and copies every relocation from
   MEM1 to MEM2. Returns the number of boot patches applied (already-patched
   words count), or -1 when a word is neither the expected nor the
   replacement (another revision): then nothing is changed at all. */
static inline int openspm_code_patches_apply(uint8_t *mem1, size_t mem1_size, uint8_t *mem2, size_t mem2_size) {
    size_t i;
    int applied = 0;
    for (i = 0; i < OPENSPM_CODE_PATCH_COUNT; ++i) {
        const openspm_code_patch *p = &k_openspm_code_patches[i];
        const size_t off = (size_t)(p->address - UINT32_C(0x80000000));
        uint32_t w;
        if (!p->boot) continue;
        if (p->address < UINT32_C(0x80000000) || off + 4u > mem1_size) return -1;
        w = (uint32_t)mem1[off] << 24 | (uint32_t)mem1[off + 1] << 16 | (uint32_t)mem1[off + 2] << 8 | mem1[off + 3];
        if (w != p->expected && w != p->replacement) return -1;
    }
    for (i = 0; i < OPENSPM_CODE_RELOCATION_COUNT; ++i) {
        const openspm_code_relocation *r = &k_openspm_code_relocations[i];
        const uint32_t span = r->rows ? r->row_size * r->rows : r->size;
        if ((size_t)(r->from - UINT32_C(0x80000000)) + r->size > mem1_size ||
            (size_t)(r->to - UINT32_C(0x90000000)) + span > mem2_size || mem2 == NULL)
            return -1;
    }
    for (i = 0; i < OPENSPM_CODE_PATCH_COUNT; ++i) {
        const openspm_code_patch *p = &k_openspm_code_patches[i];
        uint8_t *q;
        if (!p->boot) continue;
        q = mem1 + (p->address - UINT32_C(0x80000000));
        q[0] = (uint8_t)(p->replacement >> 24);
        q[1] = (uint8_t)(p->replacement >> 16);
        q[2] = (uint8_t)(p->replacement >> 8);
        q[3] = (uint8_t)p->replacement;
        ++applied;
    }
    for (i = 0; i < OPENSPM_CODE_RELOCATION_COUNT; ++i) {
        const openspm_code_relocation *r = &k_openspm_code_relocations[i];
        uint8_t *to = mem2 + (r->to - UINT32_C(0x90000000));
        uint32_t row;
        memcpy(to, mem1 + (r->from - UINT32_C(0x80000000)), r->size);
        for (row = r->rows ? r->size / r->row_size : 0u; row < r->rows; ++row)
            memcpy(to + row * r->row_size, to, r->row_size); /* Mario's row until a mod writes its own */
    }
    return applied;
}

#endif
