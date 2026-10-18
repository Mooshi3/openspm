#include "openspm/disc_boot.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static uint32_t read_be32(const uint8_t *p) {
    return ((uint32_t)p[0] << 24) |
           ((uint32_t)p[1] << 16) |
           ((uint32_t)p[2] << 8) |
           (uint32_t)p[3];
}

static void write_be32(uint8_t *p, uint32_t value) {
    p[0] = (uint8_t)(value >> 24);
    p[1] = (uint8_t)(value >> 16);
    p[2] = (uint8_t)(value >> 8);
    p[3] = (uint8_t)value;
}

static int require(int condition, const char *message) {
    if (!condition) {
        fprintf(stderr, "m14_disc_boot_test: %s\n", message);
        return 0;
    }
    return 1;
}

int main(void) {
    uint8_t boot[OPENSPM_BOOT_BIN_SIZE];
    uint8_t bi2[OPENSPM_BI2_EXPECTED_SIZE];
    uint8_t fst[0x140];
    uint8_t *mem1;
    openspm_disc_boot_info info;
    size_t fst_offset;
    size_t bi2_offset;
    int result;

    memset(boot, 0, sizeof(boot));
    memset(bi2, 0xB2, sizeof(bi2));
    memset(fst, 0xF5, sizeof(fst));
    memcpy(boot, "R8PE01", 6);
    boot[6] = 0;
    boot[7] = 2;
    write_be32(boot + 0x18, UINT32_C(0x5D1C9EA3));
    /* Wii boot.bin stores FST size/max in 32-bit-word units. */
    write_be32(boot + 0x428, (uint32_t)(sizeof(fst) / 4u));
    write_be32(boot + 0x42C, UINT32_C(0x180) / 4u);

    mem1 = (uint8_t *)calloc(1, OPENSPM_WII_MEM1_SIZE);
    if (!require(mem1 != NULL, "MEM1 allocation failed")) return 1;

    result = openspm_disc_bootstrap(boot, sizeof(boot), bi2, sizeof(bi2),
                                    fst, sizeof(fst), mem1, OPENSPM_WII_MEM1_SIZE,
                                    &info);
    if (!require(result == OPENSPM_OK, "bootstrap failed")) return 1;
    if (!require(memcmp(mem1, "R8PE01", 6) == 0, "disc identity was not copied")) return 1;
    if (!require(read_be32(mem1 + 0x20) == UINT32_C(0x0D15EA5E), "boot magic wrong")) return 1;
    if (!require(read_be32(mem1 + 0x28) == OPENSPM_WII_MEM1_SIZE, "MEM1 size wrong")) return 1;
    if (!require(read_be32(mem1 + 0xF8) == UINT32_C(0x0E7BE2C0), "bus clock wrong")) return 1;
    if (!require(read_be32(mem1 + 0xFC) == UINT32_C(0x2B73A840), "CPU clock wrong")) return 1;
    if (!require(read_be32(mem1 + 0x38) == info.fst_address, "FST pointer mismatch")) return 1;
    if (!require(read_be32(mem1 + 0xF4) == info.bi2_address, "BI2 pointer mismatch")) return 1;
    if (!require(read_be32(mem1 + 0x34) == info.arena_high, "ArenaHi mismatch")) return 1;
    if (!require(read_be32(mem1 + 0x3118) == OPENSPM_WII_MEM2_SIZE, "MEM2 size wrong")) return 1;
    if (!require(read_be32(mem1 + 0x3124) == UINT32_C(0x90000800),
                 "MEM2 arena low wrong")) return 1;
    if (!require(read_be32(mem1 + 0x3128) == UINT32_C(0x933E0000),
                 "MEM2 arena high wrong")) return 1;
    if (!require(memcmp(mem1 + 0x3180, "R8PE", 4) == 0 && mem1[0x3184] == 0x80,
                 "Wii disc application globals wrong")) return 1;
    if (!require(info.video_mode == 0u && read_be32(mem1 + 0xCC) == 0u,
                 "NTSC video bootstrap wrong")) return 1;

    fst_offset = (size_t)(info.fst_address - OPENSPM_WII_MEM1_BASE);
    bi2_offset = (size_t)(info.bi2_address - OPENSPM_WII_MEM1_BASE);
    if (!require(fst_offset + sizeof(fst) <= OPENSPM_WII_MEM1_SIZE,
                 "FST out of MEM1")) return 1;
    if (!require(bi2_offset + sizeof(bi2) <= fst_offset,
                 "BI2/FST overlap")) return 1;
    if (!require(memcmp(mem1 + fst_offset, fst, sizeof(fst)) == 0,
                 "FST bytes wrong")) return 1;
    if (!require(memcmp(mem1 + bi2_offset, bi2, sizeof(bi2)) == 0,
                 "BI2 bytes wrong")) return 1;

    /* A mismatched extracted FST must not be paired with this boot.bin. */
    write_be32(boot + 0x428, (uint32_t)(sizeof(fst) / 4u) + 1u);
    result = openspm_disc_bootstrap(boot, sizeof(boot), bi2, sizeof(bi2),
                                    fst, sizeof(fst), mem1, OPENSPM_WII_MEM1_SIZE,
                                    &info);
    if (!require(result == OPENSPM_ERROR_SOURCE_MISMATCH,
                 "mismatched FST size was accepted")) return 1;

    free(mem1);
    puts("PASS m14_disc_boot_test");
    return 0;
}
