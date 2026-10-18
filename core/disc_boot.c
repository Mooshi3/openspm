#include "openspm/disc_boot.h"

#include <string.h>

#define OPENSPM_BOOT_MAGIC UINT32_C(0x0D15EA5E)
#define OPENSPM_BOARD_MODEL_RETAIL UINT32_C(0x00000023)
#define OPENSPM_BUS_CLOCK UINT32_C(0x0E7BE2C0)
#define OPENSPM_CPU_CLOCK UINT32_C(0x2B73A840)
#define OPENSPM_MEM2_PPC_END UINT32_C(0x93400000)
#define OPENSPM_MEM2_ARENA_LO UINT32_C(0x90000800)
#define OPENSPM_MEM2_ARENA_HI UINT32_C(0x933E0000)
#define OPENSPM_IOS_HEAP_START UINT32_C(0x933E0000)
#define OPENSPM_IOS_HEAP_END UINT32_C(0x93400000)

static uint32_t read_be32(const uint8_t *p) {
    return ((uint32_t)p[0] << 24) |
           ((uint32_t)p[1] << 16) |
           ((uint32_t)p[2] << 8) |
           (uint32_t)p[3];
}

static void write_be16(uint8_t *p, uint16_t value) {
    p[0] = (uint8_t)(value >> 8);
    p[1] = (uint8_t)value;
}

static void write_be32(uint8_t *p, uint32_t value) {
    p[0] = (uint8_t)(value >> 24);
    p[1] = (uint8_t)(value >> 16);
    p[2] = (uint8_t)(value >> 8);
    p[3] = (uint8_t)value;
}

static size_t align_down_32(size_t value) {
    return value & ~(size_t)31u;
}

static int is_pal_region(uint8_t region) {
    switch (region) {
    case 'E': /* North America */
    case 'J': /* Japan */
    case 'K': /* Korea */
        return 0;
    default:
        return 1;
    }
}

int openspm_disc_bootstrap(const uint8_t *boot_bin,
                           size_t boot_bin_size,
                           const uint8_t *bi2_bin,
                           size_t bi2_size,
                           const uint8_t *fst_bin,
                           size_t fst_size,
                           uint8_t *mem1,
                           size_t mem1_size,
                           openspm_disc_boot_info *out_info) {
    uint32_t declared_fst_size_words;
    uint32_t declared_fst_max_words;
    uint64_t declared_fst_size_bytes;
    uint64_t declared_fst_max_bytes;
    size_t fst_reserve;
    size_t fst_start;
    size_t bi2_start;
    uint32_t fst_address;
    uint32_t bi2_address;
    uint32_t video_mode;

    if (boot_bin == NULL || bi2_bin == NULL || fst_bin == NULL ||
        mem1 == NULL || out_info == NULL ||
        boot_bin_size < OPENSPM_BOOT_BIN_SIZE ||
        bi2_size == 0u || fst_size == 0u ||
        mem1_size > UINT32_MAX || mem1_size < 0x4000u) {
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    }

    declared_fst_size_words = read_be32(boot_bin + 0x428u);
    declared_fst_max_words = read_be32(boot_bin + 0x42Cu);
    /* Wii partition boot headers store the FST size/max fields in 32-bit-word
       units, just like the corresponding offset. The extracted fst.bin is a
       byte stream, so convert the header values before validating it. Use a
       64-bit intermediate so malformed 32-bit word counts cannot wrap. */
    declared_fst_size_bytes = (uint64_t)declared_fst_size_words * UINT64_C(4);
    declared_fst_max_bytes = (uint64_t)declared_fst_max_words * UINT64_C(4);
    if (fst_size > UINT32_MAX ||
        declared_fst_size_bytes > UINT32_MAX ||
        declared_fst_max_bytes > UINT32_MAX) {
        return OPENSPM_ERROR_LOAD_FAILED;
    }
    if (declared_fst_size_bytes != 0u &&
        declared_fst_size_bytes != (uint64_t)fst_size) {
        return OPENSPM_ERROR_SOURCE_MISMATCH;
    }
    if (declared_fst_max_bytes != 0u &&
        declared_fst_max_bytes < (uint64_t)fst_size) {
        return OPENSPM_ERROR_LOAD_FAILED;
    }

    fst_reserve = declared_fst_max_bytes != 0u
                      ? (size_t)declared_fst_max_bytes
                      : fst_size;
    if (fst_reserve < fst_size || fst_reserve > mem1_size || bi2_size > mem1_size) {
        return OPENSPM_ERROR_LOAD_FAILED;
    }

    /* Keep loader-owned data at the top of MEM1, just as a console apploader
       does conceptually. Reserve the advertised maximum FST span so the arena
       boundary remains valid even for titles that support disc/FST variants. */
    fst_start = align_down_32(mem1_size - fst_reserve);
    if (fst_start < bi2_size) {
        return OPENSPM_ERROR_LOAD_FAILED;
    }
    bi2_start = align_down_32(fst_start - bi2_size);
    if (bi2_start < 0x4000u) {
        return OPENSPM_ERROR_LOAD_FAILED;
    }

    memset(mem1 + fst_start, 0, fst_reserve);
    memcpy(mem1 + fst_start, fst_bin, fst_size);
    memcpy(mem1 + bi2_start, bi2_bin, bi2_size);

    fst_address = OPENSPM_WII_MEM1_BASE + (uint32_t)fst_start;
    bi2_address = OPENSPM_WII_MEM1_BASE + (uint32_t)bi2_start;
    video_mode = is_pal_region(boot_bin[3]) ? 1u : 0u;

    /* Disc identity and layout magic are copied from the player's boot.bin. */
    memcpy(mem1, boot_bin, 0x20u);

    /* SDK-visible low-memory boot globals. Values here are hardware/platform
       properties or pointers to the local copies above, never proprietary game
       payload. All multibyte fields are guest big-endian. */
    write_be32(mem1 + 0x20u, OPENSPM_BOOT_MAGIC);
    write_be32(mem1 + 0x24u, 1u);
    write_be32(mem1 + 0x28u, OPENSPM_WII_MEM1_SIZE);
    write_be32(mem1 + 0x2Cu, OPENSPM_BOARD_MODEL_RETAIL);
    write_be32(mem1 + 0x30u, 0u);
    write_be32(mem1 + 0x34u, bi2_address);
    write_be32(mem1 + 0x38u, fst_address);
    write_be32(mem1 + 0x3Cu, (uint32_t)fst_reserve);
    write_be32(mem1 + 0xCCu, video_mode);
    write_be32(mem1 + 0xECu, OPENSPM_WII_MEM1_BASE + OPENSPM_WII_MEM1_SIZE);
    write_be32(mem1 + 0xF0u, OPENSPM_WII_MEM1_SIZE);
    write_be32(mem1 + 0xF4u, bi2_address);
    write_be32(mem1 + 0xF8u, OPENSPM_BUS_CLOCK);
    write_be32(mem1 + 0xFCu, OPENSPM_CPU_CLOCK);

    /* Wii-era duplicated memory-size/application globals. */
    write_be32(mem1 + 0x3100u, OPENSPM_WII_MEM1_SIZE);
    write_be32(mem1 + 0x3104u, OPENSPM_WII_MEM1_SIZE);
    write_be32(mem1 + 0x3110u, bi2_address);
    write_be32(mem1 + 0x3118u, OPENSPM_WII_MEM2_SIZE);
    write_be32(mem1 + 0x311Cu, OPENSPM_WII_MEM2_SIZE);
    write_be32(mem1 + 0x3120u, OPENSPM_MEM2_PPC_END);
    /* Retail applications reserve low MEM2 setup space and the top IOS heap.
       The SDK copies these two bounds into its MEM2 arena globals during OSInit.
       Leaving them zero made the standalone game derive 0xFFFFFFFF and clear an
       invalid range before IPC initialization. */
    write_be32(mem1 + 0x3124u, OPENSPM_MEM2_ARENA_LO);
    write_be32(mem1 + 0x3128u, OPENSPM_MEM2_ARENA_HI);
    write_be32(mem1 + 0x3130u, OPENSPM_IOS_HEAP_START);
    write_be32(mem1 + 0x3134u, OPENSPM_IOS_HEAP_END);
    mem1[0x315Cu] = 0x80u;
    write_be16(mem1 + 0x315Eu, UINT16_C(0x0113));
    memcpy(mem1 + 0x3180u, boot_bin, 4u);
    mem1[0x3184u] = 0x80u; /* disc application */

    /* Keep the low exception-table scratch area deterministic. */
    memset(mem1 + 0x3000u, 0, 0x3Cu);

    memset(out_info, 0, sizeof(*out_info));
    out_info->header.struct_size = sizeof(*out_info);
    out_info->header.abi_version = OPENSPM_ABI_VERSION;
    out_info->fst_address = fst_address;
    out_info->fst_size = (uint32_t)fst_size;
    out_info->fst_max_size = (uint32_t)fst_reserve;
    out_info->bi2_address = bi2_address;
    out_info->bi2_size = (uint32_t)bi2_size;
    out_info->arena_high = bi2_address;
    out_info->video_mode = video_mode;
    memcpy(out_info->game_id, boot_bin, sizeof(out_info->game_id));
    out_info->disc_number = boot_bin[6];
    out_info->disc_version = boot_bin[7];
    return OPENSPM_OK;
}
