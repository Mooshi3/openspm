#ifndef OPENSPM_DISC_BOOT_H
#define OPENSPM_DISC_BOOT_H

#include "openspm/abi.h"
#include "openspm/dol_image.h"

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define OPENSPM_BOOT_BIN_SIZE 0x440u
#define OPENSPM_BI2_EXPECTED_SIZE 0x2000u

typedef struct openspm_disc_boot_info {
    openspm_abi_header header;
    uint32_t fst_address;
    uint32_t fst_size;
    uint32_t fst_max_size;
    uint32_t bi2_address;
    uint32_t bi2_size;
    uint32_t arena_high;
    uint32_t video_mode;
    uint8_t game_id[6];
    uint8_t disc_number;
    uint8_t disc_version;
} openspm_disc_boot_info;

/*
 * Build the SDK-visible low-memory disc bootstrap state from files extracted
 * from the player's own disc image. boot.bin/bi2.bin/fst.bin remain source
 * inputs; this function copies only the bytes the running title expects into
 * caller-owned MEM1.
 *
 * The high-memory placement is chosen locally and need not reproduce the
 * original physical disc loader address. The published pointers and arena
 * boundary are internally consistent and reserve max-FST growth plus BI2.
 */
int openspm_disc_bootstrap(
    const uint8_t *boot_bin,
    size_t boot_bin_size,
    const uint8_t *bi2_bin,
    size_t bi2_size,
    const uint8_t *fst_bin,
    size_t fst_size,
    uint8_t *mem1,
    size_t mem1_size,
    openspm_disc_boot_info *out_info);

#ifdef __cplusplus
}
#endif

#endif
