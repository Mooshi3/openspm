#include "openspm/dol_image.h"

#include <string.h>

#define OPENSPM_DOL_HEADER_SIZE 0x100u

static uint32_t read_be32(const uint8_t *p) {
    return ((uint32_t)p[0] << 24) |
           ((uint32_t)p[1] << 16) |
           ((uint32_t)p[2] << 8) |
           (uint32_t)p[3];
}

static int file_range_valid(size_t file_size, uint32_t offset, uint32_t size) {
    size_t start = (size_t)offset;
    size_t length = (size_t)size;
    return start <= file_size && length <= file_size - start;
}

static void *resolve_region(uint32_t address, size_t width,
                            uint32_t cached_base, uint32_t uncached_base,
                            uint8_t *memory, size_t memory_size) {
    uint32_t offset32;
    size_t offset;

    if (memory == NULL) {
        return NULL;
    }
    if (address >= cached_base && (uint64_t)address < (uint64_t)cached_base + memory_size) {
        offset32 = address - cached_base;
    } else if (address >= uncached_base &&
               (uint64_t)address < (uint64_t)uncached_base + memory_size) {
        offset32 = address - uncached_base;
    } else {
        return NULL;
    }

    offset = (size_t)offset32;
    if (offset > memory_size || width > memory_size - offset) {
        return NULL;
    }
    return memory + offset;
}

void *openspm_wii_ram_pointer(uint32_t address, size_t width,
                              uint8_t *mem1, size_t mem1_size,
                              uint8_t *mem2, size_t mem2_size) {
    void *pointer;

    pointer = resolve_region(address, width,
                             OPENSPM_WII_MEM1_BASE, OPENSPM_WII_MEM1_UNCACHED,
                             mem1, mem1_size);
    if (pointer != NULL) {
        return pointer;
    }
    return resolve_region(address, width,
                          OPENSPM_WII_MEM2_BASE, OPENSPM_WII_MEM2_UNCACHED,
                          mem2, mem2_size);
}

static int append_section(openspm_dol_info *info,
                          uint32_t file_offset,
                          uint32_t address,
                          uint32_t size,
                          uint32_t is_text,
                          size_t dol_size) {
    openspm_dol_section *section;

    if (size == 0u) {
        return OPENSPM_OK;
    }
    if (info->section_count >= OPENSPM_DOL_TEXT_SECTION_COUNT + OPENSPM_DOL_DATA_SECTION_COUNT) {
        return OPENSPM_ERROR_RUNTIME;
    }
    if (!file_range_valid(dol_size, file_offset, size)) {
        return OPENSPM_ERROR_LOAD_FAILED;
    }

    section = &info->sections[info->section_count++];
    section->file_offset = file_offset;
    section->address = address;
    section->size = size;
    section->is_text = is_text;
    return OPENSPM_OK;
}

int openspm_dol_parse(const uint8_t *dol_bytes, size_t dol_size, openspm_dol_info *out_info) {
    uint32_t i;
    int result;

    if (dol_bytes == NULL || out_info == NULL || dol_size < OPENSPM_DOL_HEADER_SIZE) {
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    }

    memset(out_info, 0, sizeof(*out_info));
    out_info->header.struct_size = sizeof(*out_info);
    out_info->header.abi_version = OPENSPM_ABI_VERSION;
    out_info->bss_address = read_be32(dol_bytes + 0xD8u);
    out_info->bss_size = read_be32(dol_bytes + 0xDCu);
    out_info->entry_point = read_be32(dol_bytes + 0xE0u);

    for (i = 0; i < OPENSPM_DOL_TEXT_SECTION_COUNT; ++i) {
        result = append_section(out_info,
                                read_be32(dol_bytes + 0x00u + i * 4u),
                                read_be32(dol_bytes + 0x48u + i * 4u),
                                read_be32(dol_bytes + 0x90u + i * 4u),
                                1u,
                                dol_size);
        if (result != OPENSPM_OK) {
            return result;
        }
    }
    for (i = 0; i < OPENSPM_DOL_DATA_SECTION_COUNT; ++i) {
        result = append_section(out_info,
                                read_be32(dol_bytes + 0x1Cu + i * 4u),
                                read_be32(dol_bytes + 0x64u + i * 4u),
                                read_be32(dol_bytes + 0xACu + i * 4u),
                                0u,
                                dol_size);
        if (result != OPENSPM_OK) {
            return result;
        }
    }

    if (out_info->section_count == 0u || out_info->entry_point == 0u) {
        return OPENSPM_ERROR_LOAD_FAILED;
    }
    return OPENSPM_OK;
}

int openspm_dol_load_memory(const uint8_t *dol_bytes,
                            size_t dol_size,
                            const openspm_dol_info *info,
                            uint8_t *mem1,
                            size_t mem1_size,
                            uint8_t *mem2,
                            size_t mem2_size) {
    uint32_t i;
    void *bss = NULL;

    if (dol_bytes == NULL || info == NULL || mem1 == NULL ||
        info->header.abi_version != OPENSPM_ABI_VERSION ||
        info->header.struct_size < sizeof(*info)) {
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    }
    if (info->section_count > OPENSPM_DOL_TEXT_SECTION_COUNT + OPENSPM_DOL_DATA_SECTION_COUNT) {
        return OPENSPM_ERROR_LOAD_FAILED;
    }

    /* Validate all file and guest-memory ranges before mutating caller-owned RAM. */
    for (i = 0; i < info->section_count; ++i) {
        const openspm_dol_section *section = &info->sections[i];
        if (section->size == 0u ||
            !file_range_valid(dol_size, section->file_offset, section->size) ||
            openspm_wii_ram_pointer(section->address,
                                    (size_t)section->size,
                                    mem1, mem1_size,
                                    mem2, mem2_size) == NULL) {
            return OPENSPM_ERROR_LOAD_FAILED;
        }
    }

    if (info->bss_size != 0u) {
        bss = openspm_wii_ram_pointer(info->bss_address,
                                      (size_t)info->bss_size,
                                      mem1, mem1_size,
                                      mem2, mem2_size);
        if (bss == NULL) {
            return OPENSPM_ERROR_LOAD_FAILED;
        }
    }

    /* A DOL's BSS envelope can overlap initialized section address ranges.
       Zero first, then copy file-backed sections so initialized data wins. */
    if (bss != NULL) {
        memset(bss, 0, info->bss_size);
    }

    for (i = 0; i < info->section_count; ++i) {
        const openspm_dol_section *section = &info->sections[i];
        void *destination = openspm_wii_ram_pointer(section->address,
                                                    (size_t)section->size,
                                                    mem1, mem1_size,
                                                    mem2, mem2_size);
        memcpy(destination, dol_bytes + section->file_offset, section->size);
    }
    return OPENSPM_OK;
}
