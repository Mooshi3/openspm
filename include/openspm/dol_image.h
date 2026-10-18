#ifndef OPENSPM_DOL_IMAGE_H
#define OPENSPM_DOL_IMAGE_H

#include "openspm/abi.h"

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define OPENSPM_DOL_TEXT_SECTION_COUNT 7u
#define OPENSPM_DOL_DATA_SECTION_COUNT 11u
#define OPENSPM_WII_MEM1_BASE UINT32_C(0x80000000)
#define OPENSPM_WII_MEM1_UNCACHED UINT32_C(0xC0000000)
#define OPENSPM_WII_MEM1_SIZE UINT32_C(0x01800000)
#define OPENSPM_WII_MEM2_BASE UINT32_C(0x90000000)
#define OPENSPM_WII_MEM2_UNCACHED UINT32_C(0xD0000000)
#define OPENSPM_WII_MEM2_SIZE UINT32_C(0x04000000)

typedef struct openspm_dol_section {
    uint32_t file_offset;
    uint32_t address;
    uint32_t size;
    uint32_t is_text;
} openspm_dol_section;

typedef struct openspm_dol_info {
    openspm_abi_header header;
    uint32_t entry_point;
    uint32_t bss_address;
    uint32_t bss_size;
    uint32_t section_count;
    openspm_dol_section sections[OPENSPM_DOL_TEXT_SECTION_COUNT + OPENSPM_DOL_DATA_SECTION_COUNT];
} openspm_dol_info;

/* Parse a raw DOL header and validate all non-empty section file ranges.
   No game bytes are retained by this function. */
int openspm_dol_parse(const uint8_t *dol_bytes, size_t dol_size, openspm_dol_info *out_info);

/* Load parsed DOL sections into caller-owned MEM1/MEM2 and zero the BSS.
   Both cached and uncached Wii aliases resolve to the same host memory. */
int openspm_dol_load_memory(
    const uint8_t *dol_bytes,
    size_t dol_size,
    const openspm_dol_info *info,
    uint8_t *mem1,
    size_t mem1_size,
    uint8_t *mem2,
    size_t mem2_size);

/* Resolve a Wii effective RAM address into the caller-owned host buffers.
   Returns NULL for MMIO/unmapped addresses or if the requested width crosses
   the end of the corresponding memory region. */
void *openspm_wii_ram_pointer(
    uint32_t address,
    size_t width,
    uint8_t *mem1,
    size_t mem1_size,
    uint8_t *mem2,
    size_t mem2_size);

#ifdef __cplusplus
}
#endif

#endif
