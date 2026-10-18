#include "openspm/dol_image.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void write_be32(uint8_t *p, uint32_t value) {
    p[0] = (uint8_t)(value >> 24);
    p[1] = (uint8_t)(value >> 16);
    p[2] = (uint8_t)(value >> 8);
    p[3] = (uint8_t)value;
}

static int require(int condition, const char *message) {
    if (!condition) {
        fprintf(stderr, "m14_dol_image_test: %s\n", message);
        return 0;
    }
    return 1;
}

int main(void) {
    uint8_t dol[0x108];
    uint8_t mem1[0x400];
    uint8_t mem2[0x400];
    openspm_dol_info info;
    void *cached;
    void *uncached;

    memset(dol, 0, sizeof(dol));
    memset(mem1, 0xCC, sizeof(mem1));
    memset(mem2, 0xDD, sizeof(mem2));

    /* Synthetic DOL: one text section and one data section. */
    write_be32(dol + 0x00, 0x100u);
    write_be32(dol + 0x48, OPENSPM_WII_MEM1_BASE + 0x100u);
    write_be32(dol + 0x90, 4u);
    write_be32(dol + 0x1C, 0x104u);
    write_be32(dol + 0x64, OPENSPM_WII_MEM2_BASE + 0x20u);
    write_be32(dol + 0xAC, 4u);

    /* Deliberately make the BSS envelope overlap the initialized MEM1 section.
       Real DOLs can have this shape; initialized file-backed bytes must win. */
    write_be32(dol + 0xD8, OPENSPM_WII_MEM1_BASE + 0x0F0u);
    write_be32(dol + 0xDC, 0x30u);
    write_be32(dol + 0xE0, OPENSPM_WII_MEM1_BASE + 0x100u);
    dol[0x100] = 0x11; dol[0x101] = 0x22; dol[0x102] = 0x33; dol[0x103] = 0x44;
    dol[0x104] = 0xAA; dol[0x105] = 0xBB; dol[0x106] = 0xCC; dol[0x107] = 0xDD;

    if (!require(openspm_dol_parse(dol, sizeof(dol), &info) == OPENSPM_OK,
                 "parse failed")) return 1;
    if (!require(info.section_count == 2u, "wrong section count")) return 1;
    if (!require(info.entry_point == OPENSPM_WII_MEM1_BASE + 0x100u,
                 "wrong entry point")) return 1;

    if (!require(openspm_dol_load_memory(dol, sizeof(dol), &info,
                                         mem1, sizeof(mem1), mem2, sizeof(mem2)) == OPENSPM_OK,
                 "load failed")) return 1;
    if (!require(memcmp(mem1 + 0x100, "\x11\x22\x33\x44", 4) == 0,
                 "initialized bytes were erased by overlapping BSS")) return 1;
    if (!require(memcmp(mem2 + 0x20, "\xAA\xBB\xCC\xDD", 4) == 0,
                 "data bytes wrong")) return 1;
    if (!require(mem1[0x0F0] == 0 && mem1[0x0FF] == 0 &&
                 mem1[0x104] == 0 && mem1[0x11F] == 0,
                 "BSS gaps not zeroed")) return 1;

    cached = openspm_wii_ram_pointer(OPENSPM_WII_MEM1_BASE + 0x100u, 4,
                                     mem1, sizeof(mem1), mem2, sizeof(mem2));
    uncached = openspm_wii_ram_pointer(OPENSPM_WII_MEM1_UNCACHED + 0x100u, 4,
                                       mem1, sizeof(mem1), mem2, sizeof(mem2));
    if (!require(cached == mem1 + 0x100 && uncached == cached,
                 "MEM1 cached/uncached alias mismatch")) return 1;

    /* Corrupt the data section so it extends beyond the file. */
    write_be32(dol + 0xAC, 8u);
    if (!require(openspm_dol_parse(dol, sizeof(dol), &info) == OPENSPM_ERROR_LOAD_FAILED,
                 "truncated section was accepted")) return 1;

    puts("PASS m14_dol_image_test");
    return 0;
}
