/* Boot side of the OpenSPM content patches (include/openspm/code_patches.h):
   every boot patch lands, relF patches are left to the game's REL loader, the
   item table is copied to MEM2, each per-character table is copied with its
   other rows starting as row 0 (Mario), the map registry and the enemy
   tables with their spare rows starting as row 0, a second run is harmless, and a
   foreign word leaves the image untouched. */
#include "openspm/code_patches.h"

#include <assert.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>

#define MEM1 0x01800000u
#define MEM2 0x04000000u

static uint32_t rd(const uint8_t *m, uint32_t off) {
    return (uint32_t)m[off] << 24 | (uint32_t)m[off + 1] << 16 | (uint32_t)m[off + 2] << 8 | m[off + 3];
}
static void wr(uint8_t *m, uint32_t off, uint32_t v) {
    m[off] = (uint8_t)(v >> 24); m[off + 1] = (uint8_t)(v >> 16); m[off + 2] = (uint8_t)(v >> 8); m[off + 3] = (uint8_t)v;
}

int main(void) {
    uint8_t *mem1 = calloc(1, MEM1), *mem2 = calloc(1, MEM2);
    size_t i;
    int boot = 0;
    assert(mem1 && mem2);
    for (i = 0; i < OPENSPM_CODE_PATCH_COUNT; ++i)
        if (k_openspm_code_patches[i].boot) {
            wr(mem1, k_openspm_code_patches[i].address - 0x80000000u, k_openspm_code_patches[i].expected);
            ++boot;
        }
    for (i = 0; i < 538u * 0x2Cu; ++i) mem1[0x003B7E78u + i] = (uint8_t)(i * 7u);
    for (i = 0; i < OPENSPM_CODE_RELOCATION_COUNT; ++i) {
        const openspm_code_relocation *r = &k_openspm_code_relocations[i];
        uint32_t k;
        if (!r->rows) continue;
        for (k = 0; k < r->size; ++k) mem1[r->from - 0x80000000u + k] = (uint8_t)(k + 1u);
    }
    /* A foreign word: nothing changes. */
    wr(mem1, k_openspm_code_patches[0].address - 0x80000000u, 0x60000000u);
    assert(openspm_code_patches_apply(mem1, MEM1, mem2, MEM2) == -1);
    assert(rd(mem1, k_openspm_code_patches[1].address - 0x80000000u) == k_openspm_code_patches[1].expected);
    assert(mem2[OPENSPM_ITEM_DATA_RELOCATED - 0x90000000u + 1u] == 0u);
    wr(mem1, k_openspm_code_patches[0].address - 0x80000000u, k_openspm_code_patches[0].expected);
    /* The real run. */
    assert(openspm_code_patches_apply(mem1, MEM1, mem2, MEM2) == boot);
    for (i = 0; i < OPENSPM_CODE_PATCH_COUNT; ++i) {
        const openspm_code_patch *p = &k_openspm_code_patches[i];
        if (p->boot) assert(rd(mem1, p->address - 0x80000000u) == p->replacement);
        else assert(p->address >= 0x80C00000u && rd(mem1, p->address - 0x80000000u) == 0u);
    }
    assert(memcmp(mem2 + (OPENSPM_ITEM_DATA_RELOCATED - 0x90000000u), mem1 + 0x003B7E78u, 538u * 0x2Cu) == 0);
    {
        unsigned tables = 0u;
        for (i = 0; i < OPENSPM_CODE_RELOCATION_COUNT; ++i) {
            const openspm_code_relocation *r = &k_openspm_code_relocations[i];
            const uint8_t *to = mem2 + (r->to - 0x90000000u);
            uint32_t row;
            if (!r->rows) continue;
            ++tables;
            if (strcmp(r->name, "map-registry") == 0) {
                /* 468 map pointers, then spare slots holding slot 0's pointer. */
                assert(r->size == 468u * 4u && r->row_size == 4u && r->rows == 532u);
            } else if (strcmp(r->name, "enemy-tribes") == 0) {
                assert(r->size == 535u * 0x68u && r->row_size == 0x68u && r->rows == 599u);
            } else if (strcmp(r->name, "enemy-templates") == 0) {
                assert(r->size == 435u * 0x68u && r->row_size == 0x68u && r->rows == 499u);
            } else {
                assert(r->rows == 128u && r->size == 4u * r->row_size); /* a per-character table */
            }
            assert(memcmp(to, mem1 + (r->from - 0x80000000u), r->size) == 0); /* the game's rows */
            for (row = r->size / r->row_size; row < r->rows; ++row)
                assert(memcmp(to + row * r->row_size, to, r->row_size) == 0);
        }
        assert(tables == 13u);
    }
    assert(openspm_code_patches_apply(mem1, MEM1, mem2, MEM2) == boot); /* idempotent */
    free(mem1);
    free(mem2);
    printf("PASS: code patches (%d boot, %u total, item table relocated)\n", boot, (unsigned)OPENSPM_CODE_PATCH_COUNT);
    return 0;
}
