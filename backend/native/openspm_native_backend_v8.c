/*
 * M14 standalone-native backend v8.
 *
 * This file remains the current standalone-native behavior layer even as the
 * runtime provenance advances past the original v8 milestone. dev21 keeps the
 * runtime-proven dev17 ANSI OSFont compatibility seam, dev18 AI SRC lift, and
 * dev19 Broadway write-gather setup lift, plus the current dev20 hostcall
 * behavior. dev21 fixes hosted DVD reads so they work for ordinary SDK
 * DVDFileInfo objects instead of assuming every handle belongs to SPM's
 * DVDMgr wrapper.
 *
 * The live dev17 trace proved AIInit reaches __AI_SRC_INIT at 0x80285328 and
 * then spins waiting for the physical AI sample counter at 0xCD006C08. A PC
 * audio backend owns its own sample clock, so the private calibration routine
 * is a semantic no-op. Keep the surrounding AI control/volume/timing register
 * state that Nintendo code reads and writes, but do not invent a general AI
 * sample counter or audio-device emulator.
 */

#pragma push_macro("openspm_backend_query")
#undef openspm_backend_query
#define openspm_backend_query openspm_backend_query_v6_internal
#include "openspm_native_backend_v6.c"
#undef openspm_backend_query
#pragma pop_macro("openspm_backend_query")

#include "openspm/m14_hostcall_service.h"

#define R8PE01_MEM1_CACHED UINT32_C(0x80000000)
#define R8PE01_MEM1_UNCACHED UINT32_C(0xC0000000)
#define R8PE01_MEM2_CACHED UINT32_C(0x90000000)
#define R8PE01_MEM2_UNCACHED UINT32_C(0xD0000000)
#define R8PE01_WPAD_READY_WORD UINT32_C(0x804F245C)
#define R8PE01_VI_WAIT_MICRO_TIME UINT32_C(0x80282E10)
#define R8PE01_AI_SRC_INIT UINT32_C(0x80285328)
#define R8PE01_GX_WRITE_GATHER_EMPTY UINT32_C(0x8028986C)
#define R8PE01_GX_ENABLE_WRITE_GATHER UINT32_C(0x8028987C)

/* SDK-visible low-memory BootInfo fields written by openspm_disc_bootstrap. */
#define R8PE01_BOOT_FST_ADDRESS UINT32_C(0x80000038)
#define R8PE01_BOOT_FST_MAX_SIZE UINT32_C(0x8000003C)
#define R8PE01_DVD_FILE_START_OFFSET UINT32_C(0x30)
#define R8PE01_DVD_FILE_LENGTH_OFFSET UINT32_C(0x34)
#define OPENSPM_V8_FST_ENTRY_SIZE UINT32_C(12)
#define OPENSPM_V8_DISC_PATH_MAX 512u
#define OPENSPM_V8_FST_MAX_DEPTH 64u

/* Broadway GPIOB_OUT. R8PE01's WPAD startup only observes bit 8 here to
   power the physical Wii sensor bar. Preserve exactly that guest-visible bit;
   all other Hollywood GPIO state remains outside the native runtime. */
#define R8PE01_GPIOB_OUT UINT32_C(0xCD0000C0)
#define R8PE01_GPIO_SENSOR_BAR UINT32_C(0x00000100)

#define R8PE01_AI_CONTROL UINT32_C(0xCD006C00)
#define R8PE01_AI_VOLUME UINT32_C(0xCD006C04)
#define R8PE01_AI_SAMPLE_COUNTER UINT32_C(0xCD006C08)
#define R8PE01_AI_INTERRUPT_TIMING UINT32_C(0xCD006C0C)
#define R8PE01_AI_CONTROL_ORDINARY_BITS UINT32_C(0x00000077)
#define R8PE01_AI_CONTROL_STATUS_BIT UINT32_C(0x00000008)
#define R8PE01_AI_CONTROL_KNOWN_BITS \
    (R8PE01_AI_CONTROL_ORDINARY_BITS | R8PE01_AI_CONTROL_STATUS_BIT)

#define R8PE01_EXI0_CSR UINT32_C(0xCD006800)
#define R8PE01_EXI0_MAR UINT32_C(0xCD006804)
#define R8PE01_EXI0_LENGTH UINT32_C(0xCD006808)
#define R8PE01_EXI0_CR UINT32_C(0xCD00680C)
#define R8PE01_EXI0_DATA UINT32_C(0xCD006810)

/* Matching RVL OSFont reads the western compressed font as 0x3000 bytes
   beginning at IPL ROM offset 0x1FCF00, in <=256-byte __OSReadROM chunks.
   The live first command is therefore 0x1FCF00 << 6 == 0x07F3C000. */
#define R8PE01_ANSI_FONT_ROM_BASE UINT32_C(0x001FCF00)
#define R8PE01_ANSI_FONT_ROM_SIZE UINT32_C(0x00003000)
#define R8PE01_ANSI_FONT_SELECT UINT32_C(0x00000130)
#define R8PE01_EXI_SELECT_FREQ_MASK UINT32_C(0x000003F0)
#define R8PE01_EXI_IMM_WRITE4 UINT32_C(0x00000035)
#define R8PE01_EXI_DMA_READ UINT32_C(0x00000003)

/* OpenSPM-owned decoded font: 224 ANSI slots, one 16x14 sheet of 8x8 cells.
   The image is intentionally blank during native bring-up; it is structurally
   valid OSFont data and contains no Nintendo artwork. */
#define OPENSPM_ANSI_FONT_DECODED_SIZE UINT32_C(0x00000F10)
#define OPENSPM_ANSI_FONT_WIDTH_COUNT UINT32_C(224)
#define OPENSPM_ANSI_FONT_WIDTH_OFFSET UINT32_C(0x30)
#define OPENSPM_ANSI_FONT_IMAGE_OFFSET UINT32_C(0x110)
#define OPENSPM_ANSI_FONT_IMAGE_BYTES UINT32_C(3584)
#define OPENSPM_ANSI_FONT_SHEET_SIZE UINT32_C(7168)
#define OPENSPM_ANSI_FONT_YAY0_MASK_BYTES UINT32_C(484)
#define OPENSPM_ANSI_FONT_YAY0_DATA_OFFSET UINT32_C(500)

#define R8PE01_BUTTON_PLUS UINT32_C(0x00000100)
#define R8PE01_BUTTON_A    UINT32_C(0x00000800)

typedef struct openspm_v8_hostcall_state {
    uint32_t keyboard_down;
    uint32_t ios_handle_counter;
    uint32_t gamefs_fst_reported;
    uint32_t gamefs_wrapper_reported;
    uint64_t handled_calls;
} openspm_v8_hostcall_state;

typedef struct openspm_v8_ansi_font_state {
    uint8_t rom[R8PE01_ANSI_FONT_ROM_SIZE];
    uint32_t stream_offset;
    uint32_t stream_active;
    uint32_t reported;
} openspm_v8_ansi_font_state;

typedef struct openspm_v8_ai_state {
    uint32_t control;
    uint32_t volume;
    uint32_t interrupt_timing;
    uint32_t calibration_reported;
} openspm_v8_ai_state;

static openspm_v8_hostcall_state g_v8_hostcall;
static openspm_v8_ansi_font_state g_v8_ansi_font;
static openspm_v8_ai_state g_v8_ai;
static uint32_t g_v8_sensor_bar_power;

static void v8_store_be16(uint8_t *p, uint16_t value) {
    p[0] = (uint8_t)(value >> 8);
    p[1] = (uint8_t)value;
}

static void v8_store_be32(uint8_t *p, uint32_t value) {
    p[0] = (uint8_t)(value >> 24);
    p[1] = (uint8_t)(value >> 16);
    p[2] = (uint8_t)(value >> 8);
    p[3] = (uint8_t)value;
}

static uint32_t v8_load_be32(const uint8_t *p) {
    return ((uint32_t)p[0] << 24) |
           ((uint32_t)p[1] << 16) |
           ((uint32_t)p[2] << 8) |
           (uint32_t)p[3];
}

static void v8_build_ansi_font_rom(void) {
    uint8_t decoded[OPENSPM_ANSI_FONT_DECODED_SIZE];
    uint32_t i;

    memset(&g_v8_ansi_font, 0, sizeof(g_v8_ansi_font));
    memset(decoded, 0, sizeof(decoded));

    /* OSFontHeader, matching the documented SDK/Dolphin-compatible layout. */
    v8_store_be16(decoded + 0x00, UINT16_C(0x0000)); /* Windows-1252 */
    v8_store_be16(decoded + 0x02, UINT16_C(0x0020));
    v8_store_be16(decoded + 0x04, UINT16_C(0x00FF));
    v8_store_be16(decoded + 0x06, UINT16_C(0x0020));
    v8_store_be16(decoded + 0x08, UINT16_C(8));
    v8_store_be16(decoded + 0x0A, UINT16_C(0));
    v8_store_be16(decoded + 0x0C, UINT16_C(8));
    v8_store_be16(decoded + 0x0E, UINT16_C(9));
    v8_store_be16(decoded + 0x10, UINT16_C(8));
    v8_store_be16(decoded + 0x12, UINT16_C(8));
    v8_store_be32(decoded + 0x14, OPENSPM_ANSI_FONT_SHEET_SIZE);
    v8_store_be16(decoded + 0x18, UINT16_C(0));
    v8_store_be16(decoded + 0x1A, UINT16_C(16));
    v8_store_be16(decoded + 0x1C, UINT16_C(14));
    v8_store_be16(decoded + 0x1E, UINT16_C(128));
    v8_store_be16(decoded + 0x20, UINT16_C(112));
    v8_store_be16(decoded + 0x22, (uint16_t)OPENSPM_ANSI_FONT_WIDTH_OFFSET);
    v8_store_be32(decoded + 0x24, OPENSPM_ANSI_FONT_IMAGE_OFFSET);
    v8_store_be32(decoded + 0x28, OPENSPM_ANSI_FONT_SHEET_SIZE);
    decoded[0x2A] = UINT8_C(0x00);
    decoded[0x2B] = UINT8_C(0x55);
    decoded[0x2C] = UINT8_C(0xAA);
    decoded[0x2D] = UINT8_C(0xFF);

    for (i = 0; i < OPENSPM_ANSI_FONT_WIDTH_COUNT; ++i)
        decoded[OPENSPM_ANSI_FONT_WIDTH_OFFSET + i] = UINT8_C(8);
    decoded[OPENSPM_ANSI_FONT_WIDTH_OFFSET] = UINT8_C(4); /* space/invalid */

    /* A link-free Yay0 stream is simple and deterministic: every mask bit is
       literal and the chunks table is the decoded OSFont bytes. */
    g_v8_ansi_font.rom[0] = 'Y';
    g_v8_ansi_font.rom[1] = 'a';
    g_v8_ansi_font.rom[2] = 'y';
    g_v8_ansi_font.rom[3] = '0';
    v8_store_be32(g_v8_ansi_font.rom + 4, OPENSPM_ANSI_FONT_DECODED_SIZE);
    v8_store_be32(g_v8_ansi_font.rom + 8, OPENSPM_ANSI_FONT_YAY0_DATA_OFFSET);
    v8_store_be32(g_v8_ansi_font.rom + 12, OPENSPM_ANSI_FONT_YAY0_DATA_OFFSET);
    memset(g_v8_ansi_font.rom + 16, 0xFF, OPENSPM_ANSI_FONT_YAY0_MASK_BYTES);
    memcpy(g_v8_ansi_font.rom + OPENSPM_ANSI_FONT_YAY0_DATA_OFFSET,
           decoded, OPENSPM_ANSI_FONT_DECODED_SIZE);
}

static int v8_ansi_font_selected(void) {
    return (g_v3.platform.exi_csr[0] & R8PE01_EXI_SELECT_FREQ_MASK) ==
           R8PE01_ANSI_FONT_SELECT;
}

static uint32_t v8_exi_command_address(uint32_t command) {
    return (command >> 6) & UINT32_C(0x01FFFFFF);
}

static int v8_try_begin_ansi_font_read(uint32_t control) {
    uint32_t command;
    uint32_t address;

    if (control != R8PE01_EXI_IMM_WRITE4 || !v8_ansi_font_selected()) return 0;
    command = g_v3.platform.exi_data[0];
    if ((command & UINT32_C(0x80000000)) != 0u) return 0;
    address = v8_exi_command_address(command);
    if (address < R8PE01_ANSI_FONT_ROM_BASE ||
        address >= R8PE01_ANSI_FONT_ROM_BASE + R8PE01_ANSI_FONT_ROM_SIZE) {
        return 0;
    }

    g_v8_ansi_font.stream_offset = address - R8PE01_ANSI_FONT_ROM_BASE;
    g_v8_ansi_font.stream_active = 1u;
    g_v3.platform.exi_cr[0] = control & ~UINT32_C(1);
    if (!g_v8_ansi_font.reported) {
        backend_log(OPENSPM_LOG_INFO,
                    "M14 native ANSI OSFont ROM compatibility stream active at IPL 0x001FCF00.");
        g_v8_ansi_font.reported = 1u;
    }
    return 1;
}

static int v8_try_complete_ansi_font_dma(uint32_t control) {
    uint32_t length;
    uint32_t mar;

    if (control != R8PE01_EXI_DMA_READ ||
        !g_v8_ansi_font.stream_active || !v8_ansi_font_selected()) {
        return 0;
    }

    length = g_v3.platform.exi_length[0];
    mar = g_v3.platform.exi_mar[0];
    if (length == 0u ||
        g_v8_ansi_font.stream_offset > R8PE01_ANSI_FONT_ROM_SIZE ||
        length > R8PE01_ANSI_FONT_ROM_SIZE - g_v8_ansi_font.stream_offset ||
        g_backend.mem1 == NULL || mar > OPENSPM_WII_MEM1_SIZE ||
        length > OPENSPM_WII_MEM1_SIZE - mar) {
        return -1;
    }

    memcpy(g_backend.mem1 + mar,
           g_v8_ansi_font.rom + g_v8_ansi_font.stream_offset,
           length);
    g_v8_ansi_font.stream_offset += length;
    g_v3.platform.exi_length[0] = 0u;
    g_v3.platform.exi_cr[0] = control & ~UINT32_C(1);
    return 1;
}

static void *v8_guest_ptr(uint32_t address, uint32_t size) {
    uint32_t offset;
    if (size > OPENSPM_WII_MEM2_SIZE) return NULL;

    if (address >= R8PE01_MEM1_CACHED) {
        offset = address - R8PE01_MEM1_CACHED;
        if (offset <= OPENSPM_WII_MEM1_SIZE && size <= OPENSPM_WII_MEM1_SIZE - offset)
            return g_backend.mem1 + offset;
    }
    if (address >= R8PE01_MEM1_UNCACHED) {
        offset = address - R8PE01_MEM1_UNCACHED;
        if (offset <= OPENSPM_WII_MEM1_SIZE && size <= OPENSPM_WII_MEM1_SIZE - offset)
            return g_backend.mem1 + offset;
    }
    if (address >= R8PE01_MEM2_CACHED) {
        offset = address - R8PE01_MEM2_CACHED;
        if (offset <= OPENSPM_WII_MEM2_SIZE && size <= OPENSPM_WII_MEM2_SIZE - offset)
            return g_backend.mem2 + offset;
    }
    if (address >= R8PE01_MEM2_UNCACHED) {
        offset = address - R8PE01_MEM2_UNCACHED;
        if (offset <= OPENSPM_WII_MEM2_SIZE && size <= OPENSPM_WII_MEM2_SIZE - offset)
            return g_backend.mem2 + offset;
    }
    return NULL;
}

static int v8_read16(uint32_t address, uint16_t *out_value) {
    const uint8_t *p = (const uint8_t *)v8_guest_ptr(address, 2u);
    if (p == NULL || out_value == NULL) return 0;
    *out_value = (uint16_t)(((uint16_t)p[0] << 8) | p[1]);
    return 1;
}

static int v8_write16(uint32_t address, uint16_t value) {
    uint8_t *p = (uint8_t *)v8_guest_ptr(address, 2u);
    if (p == NULL) return 0;
    p[0] = (uint8_t)(value >> 8);
    p[1] = (uint8_t)value;
    return 1;
}

static int v8_read32(uint32_t address, uint32_t *out_value) {
    const uint8_t *p = (const uint8_t *)v8_guest_ptr(address, 4u);
    if (p == NULL || out_value == NULL) return 0;
    *out_value = ((uint32_t)p[0] << 24) |
                 ((uint32_t)p[1] << 16) |
                 ((uint32_t)p[2] << 8) |
                 (uint32_t)p[3];
    return 1;
}

static int v8_write32(uint32_t address, uint32_t value) {
    uint8_t *p = (uint8_t *)v8_guest_ptr(address, 4u);
    if (p == NULL) return 0;
    p[0] = (uint8_t)(value >> 24);
    p[1] = (uint8_t)(value >> 16);
    p[2] = (uint8_t)(value >> 8);
    p[3] = (uint8_t)value;
    return 1;
}

static int v8_wrapper_path(uint32_t file_info, char *out_path, size_t capacity) {
    const uint8_t *guest;
    size_t i;
    size_t start = 0u;

    if (out_path == NULL || capacity < 2u || file_info < UINT32_C(0x40)) return 0;
    guest = (const uint8_t *)v8_guest_ptr(file_info - UINT32_C(0x40), 64u);
    if (guest == NULL) return 0;

    /* SPM's DVDMgr embeds DVDFileInfo at +0x40 after a 64-byte path. Accept
       the harmless SDK-style ./ prefix here; v3's generic path validator still
       rejects interior '.' and all '..' traversal components. */
    while (start + 1u < 64u && guest[start] == '.' &&
           (guest[start + 1u] == '/' || guest[start + 1u] == '\\')) {
        start += 2u;
    }
    if (start >= 64u || guest[start] == 0u) return 0;

    for (i = 0u; start + i < 64u && i + 1u < capacity; ++i) {
        uint8_t ch = guest[start + i];
        if (ch == 0u) {
            if (i == 0u) return 0;
            out_path[i] = '\0';
            return 1;
        }
        if (ch < 0x20u || ch >= 0x7Fu) return 0;
        out_path[i] = (char)ch;
    }
    return 0;
}

static int v8_fst_is_dir(const uint8_t *fst, uint32_t entry) {
    return (v8_load_be32(fst + (size_t)entry * OPENSPM_V8_FST_ENTRY_SIZE) &
            UINT32_C(0xFF000000)) != 0u;
}

static uint32_t v8_fst_parent(const uint8_t *fst, uint32_t entry) {
    uint32_t cursor;
    if (entry == 0u) return UINT32_MAX;
    cursor = entry;
    while (cursor != 0u) {
        uint32_t candidate = cursor - 1u;
        const uint8_t *node = fst + (size_t)candidate * OPENSPM_V8_FST_ENTRY_SIZE;
        if (v8_fst_is_dir(fst, candidate) && v8_load_be32(node + 8u) > entry)
            return candidate;
        cursor = candidate;
    }
    return UINT32_MAX;
}

static int v8_fst_append_name(const uint8_t *fst,
                              uint32_t fst_size,
                              uint32_t entry_count,
                              uint32_t entry,
                              char *out_path,
                              size_t capacity,
                              size_t *inout_length) {
    const uint8_t *node;
    uint32_t name_offset;
    size_t string_base;
    size_t source;
    size_t length;

    if (fst == NULL || out_path == NULL || inout_length == NULL || entry == 0u)
        return 0;
    node = fst + (size_t)entry * OPENSPM_V8_FST_ENTRY_SIZE;
    name_offset = v8_load_be32(node) & UINT32_C(0x00FFFFFF);
    string_base = (size_t)entry_count * OPENSPM_V8_FST_ENTRY_SIZE;
    if (string_base > fst_size || name_offset >= fst_size - string_base) return 0;
    source = string_base + (size_t)name_offset;
    length = *inout_length;

    if (length != 0u) {
        if (length + 1u >= capacity) return 0;
        out_path[length++] = '/';
    }
    while (source < fst_size) {
        uint8_t ch = fst[source++];
        if (ch == 0u) {
            if (length == 0u || length >= capacity) return 0;
            out_path[length] = '\0';
            *inout_length = length;
            return 1;
        }
        if (ch < 0x20u || ch == 0x7Fu || length + 1u >= capacity) return 0;
        out_path[length++] = (char)ch;
    }
    return 0;
}

static int v8_fst_path_for_file_info(uint32_t file_info,
                                     char *out_path,
                                     size_t capacity) {
    uint32_t start_address;
    uint32_t file_length;
    uint32_t fst_address;
    uint32_t fst_size;
    const uint8_t *fst;
    uint32_t entry_count;
    uint32_t match = UINT32_MAX;
    uint32_t i;
    uint32_t chain[OPENSPM_V8_FST_MAX_DEPTH];
    uint32_t depth = 0u;
    uint32_t cursor;
    size_t path_length = 0u;

    if (out_path == NULL || capacity < 2u ||
        !v8_read32(file_info + R8PE01_DVD_FILE_START_OFFSET, &start_address) ||
        !v8_read32(file_info + R8PE01_DVD_FILE_LENGTH_OFFSET, &file_length) ||
        !v8_read32(R8PE01_BOOT_FST_ADDRESS, &fst_address) ||
        !v8_read32(R8PE01_BOOT_FST_MAX_SIZE, &fst_size) || fst_size < 12u) {
        return 0;
    }
    fst = (const uint8_t *)v8_guest_ptr(fst_address, fst_size);
    if (fst == NULL) return 0;

    entry_count = v8_load_be32(fst + 8u);
    if (entry_count == 0u ||
        (uint64_t)entry_count * OPENSPM_V8_FST_ENTRY_SIZE > (uint64_t)fst_size) {
        return 0;
    }

    for (i = 1u; i < entry_count; ++i) {
        const uint8_t *node = fst + (size_t)i * OPENSPM_V8_FST_ENTRY_SIZE;
        if (!v8_fst_is_dir(fst, i) &&
            v8_load_be32(node + 4u) == start_address &&
            v8_load_be32(node + 8u) == file_length) {
            if (match != UINT32_MAX) return 0; /* ambiguous source metadata */
            match = i;
        }
    }
    if (match == UINT32_MAX) return 0;

    cursor = match;
    while (cursor != 0u) {
        uint32_t parent;
        if (depth >= OPENSPM_V8_FST_MAX_DEPTH) return 0;
        chain[depth++] = cursor;
        parent = v8_fst_parent(fst, cursor);
        if (parent == UINT32_MAX) return 0;
        cursor = parent;
    }

    while (depth != 0u) {
        if (!v8_fst_append_name(fst, fst_size, entry_count, chain[--depth],
                                out_path, capacity, &path_length)) {
            return 0;
        }
    }
    return path_length != 0u;
}

static int v8_resolve_gamefs_path(uint32_t file_info,
                                  char *out_path,
                                  size_t capacity) {
    if (v8_fst_path_for_file_info(file_info, out_path, capacity)) {
        if (!g_v8_hostcall.gamefs_fst_reported) {
            backend_log(OPENSPM_LOG_INFO,
                        "M14 native GameFS DVDFileInfo->FST resolution active; ordinary SDK DVD handles supported.");
            g_v8_hostcall.gamefs_fst_reported = 1u;
        }
        return 1;
    }
    if (v8_wrapper_path(file_info, out_path, capacity)) {
        if (!g_v8_hostcall.gamefs_wrapper_reported) {
            backend_log(OPENSPM_LOG_INFO,
                        "M14 native GameFS using DVDMgr embedded-path compatibility fallback.");
            g_v8_hostcall.gamefs_wrapper_reported = 1u;
        }
        return 1;
    }
    return 0;
}

static int v8_gamefs_read_common(openspm_m14_hostcall_state *state,
                                 int asynchronous) {
    uint32_t file_info = state->gpr[3];
    uint32_t dest_address = state->gpr[4];
    uint32_t length = state->gpr[5];
    uint32_t offset = state->gpr[6];
    uint32_t callback = asynchronous ? state->gpr[7] : 0u;
    openspm_gamefs_handle handle = OPENSPM_GAMEFS_INVALID_HANDLE;
    uint64_t file_size = 0u;
    uint64_t read = 0u;
    char path[OPENSPM_V8_DISC_PATH_MAX];
    void *destination;
    int io_result;
    int32_t game_result;

    if (length > UINT32_C(0x08000000) ||
        (asynchronous && callback == 0u) ||
        !v8_resolve_gamefs_path(file_info, path, sizeof(path))) {
        return OPENSPM_ERROR_NOT_FOUND;
    }
    destination = v8_guest_ptr(dest_address, length);
    if (destination == NULL && length != 0u) return OPENSPM_ERROR_NOT_FOUND;
    if (length != 0u) memset(destination, 0, length);

    io_result = v3_gamefs_open(&g_v3, path, &handle, &file_size);
    if (io_result == OPENSPM_OK) {
        (void)file_size;
        io_result = v3_gamefs_read(&g_v3, handle, offset,
                                   destination, length, &read);
        (void)v3_gamefs_close(&g_v3, handle);
    }

    game_result = (io_result == OPENSPM_OK && read <= (uint64_t)length)
                      ? (int32_t)read
                      : -1;

    if (asynchronous) {
        state->gpr[3] = 1u;
        state->event_target = callback;
        state->event_argc = 2u;
        state->event_args[0] = (uint32_t)game_result;
        state->event_args[1] = file_info;
    } else {
        state->gpr[3] = (uint32_t)game_result;
    }
    state->pc = state->lr;
    return OPENSPM_OK;
}

static uint32_t v8_poll_pressed_buttons(void) {
    uint32_t down = 0u;
    uint32_t pressed;

    if ((GetAsyncKeyState(VK_SPACE) & 0x8000) != 0 ||
        (GetAsyncKeyState('Z') & 0x8000) != 0) {
        down |= R8PE01_BUTTON_A;
    }
    if ((GetAsyncKeyState(VK_RETURN) & 0x8000) != 0) {
        down |= R8PE01_BUTTON_PLUS;
    }

    pressed = down & ~g_v8_hostcall.keyboard_down;
    g_v8_hostcall.keyboard_down = down;
    return pressed;
}

static int OPENSPM_CALL v8_handle_hostcall(void *context,
                                           uint32_t target,
                                           openspm_m14_hostcall_state *state) {
    uint16_t ready;
    uint32_t work_ptr_address;
    uint32_t work_ptr;
    uint32_t display_list_size;
    int result;
    (void)context;

    if (state == NULL || state->header.struct_size < OPENSPM_M14_HOSTCALL_STATE_V1_SIZE)
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    state->event_target = 0u;
    state->event_argc = 0u;
    memset(state->event_args, 0, sizeof(state->event_args));
    ++g_v8_hostcall.handled_calls;

    switch (target) {
    case UINT32_C(0x8026DFA0):
    case UINT32_C(0x8026E028):
        state->pc = state->lr;
        return OPENSPM_OK;

    case UINT32_C(0x80279CDC):
        return v8_gamefs_read_common(state, 0);
    case UINT32_C(0x80279BF4):
        return v8_gamefs_read_common(state, 1);

    case UINT32_C(0x80291DC8):
        result = openspm_gx_platform_begin_display_list(
            &g_v6_gx, state->gpr[3], state->gpr[4]);
        if (result != OPENSPM_OK) return result;
        state->pc = state->lr;
        return OPENSPM_OK;

    case UINT32_C(0x80291E78):
        display_list_size = 0u;
        result = openspm_gx_platform_end_display_list(
            &g_v6_gx, &display_list_size);
        if (result != OPENSPM_OK) return result;
        state->gpr[3] = display_list_size;
        state->pc = state->lr;
        return OPENSPM_OK;

    case UINT32_C(0x8023CEF0):
        work_ptr_address = state->gpr[13] + (uint32_t)(int32_t)-32080;
        if (v8_read32(work_ptr_address, &work_ptr) && work_ptr != 0u) {
            (void)v8_write32(work_ptr + UINT32_C(0x000), 0u);
            (void)v8_write32(work_ptr + UINT32_C(0x1AC), 0u);
            (void)v8_write32(work_ptr + UINT32_C(0x1B0), 0u);
            (void)v8_write32(work_ptr + UINT32_C(0x1B4), 0u);
            (void)v8_write32(work_ptr + UINT32_C(0x1B8), 0u);
        }
        state->pc = state->lr;
        return OPENSPM_OK;

    case UINT32_C(0x8029A500):
        if (state->gpr[5] != 0u) (void)v8_write32(state->gpr[5], 0u);
        state->gpr[3] = 0u;
        state->pc = state->lr;
        if (state->gpr[6] != 0u) {
            state->event_target = state->gpr[6];
            state->event_argc = 2u;
            state->event_args[0] = 0u;
            state->event_args[1] = state->gpr[7];
        }
        return OPENSPM_OK;

    case UINT32_C(0x80236D00):
        ready = 0u;
        (void)v8_read16(R8PE01_WPAD_READY_WORD, &ready);
        ready = (uint16_t)(ready | UINT16_C(0x0008));
        state->gpr[3] = ready;
        state->pc = state->lr;
        return OPENSPM_OK;

    case UINT32_C(0x80235AB8):
        state->gpr[3] = v8_poll_pressed_buttons();
        state->pc = state->lr;
        return OPENSPM_OK;

    case UINT32_C(0x802CD5DC):
        state->gpr[3] = 3u;
        state->pc = state->lr;
        return OPENSPM_OK;

    case UINT32_C(0x802A0150):
        ready = 0u;
        (void)v8_read16(R8PE01_WPAD_READY_WORD, &ready);
        (void)v8_write16(R8PE01_WPAD_READY_WORD,
                         (uint16_t)(ready | UINT16_C(0x0008)));
        state->gpr[3] = 0u;
        state->pc = state->lr;
        return OPENSPM_OK;

    case UINT32_C(0x8029A7E0):
        state->pc = state->lr;
        return OPENSPM_OK;
    case UINT32_C(0x8029A880):
        state->gpr[3] = 0u;
        state->pc = state->lr;
        return OPENSPM_OK;

    case UINT32_C(0x80282A68):
        state->gpr[3] = 1u;
        state->pc = state->lr;
        return OPENSPM_OK;

    case R8PE01_VI_WAIT_MICRO_TIME:
        state->pc = state->lr;
        return OPENSPM_OK;

    case UINT32_C(0x8028325C):
        state->gpr[3] = 1u;
        state->pc = state->lr;
        return OPENSPM_OK;

    case R8PE01_GX_WRITE_GATHER_EMPTY:
        backend_log(OPENSPM_LOG_INFO,
                    "M14 native GX write-gather empty query hosted; WG FIFO writes complete synchronously.");
        state->gpr[3] = 1u;
        state->pc = state->lr;
        return OPENSPM_OK;

    case R8PE01_GX_ENABLE_WRITE_GATHER:
        backend_log(OPENSPM_LOG_INFO,
                    "M14 native GX write-gather pipe setup bypass active; OpenSPM owns WG FIFO transport.");
        state->pc = state->lr;
        return OPENSPM_OK;

    case R8PE01_AI_SRC_INIT:
        if (!g_v8_ai.calibration_reported) {
            backend_log(OPENSPM_LOG_INFO,
                        "M14 native AI SRC calibration bypass active; PC audio clock owns sample timing.");
            g_v8_ai.calibration_reported = 1u;
        }
        state->pc = state->lr;
        return OPENSPM_OK;

    case UINT32_C(0x802961B8):
        state->gpr[3] = 10u + (g_v8_hostcall.ios_handle_counter++ & 31u);
        state->pc = state->lr;
        return OPENSPM_OK;
    case UINT32_C(0x802960A0):
        state->gpr[3] = 0u;
        state->pc = state->lr;
        return OPENSPM_OK;
    case UINT32_C(0x80296748):
        state->gpr[3] = state->gpr[5];
        state->pc = state->lr;
        return OPENSPM_OK;
    case UINT32_C(0x802962D8):
    case UINT32_C(0x80296398):
    case UINT32_C(0x80296440):
    case UINT32_C(0x80296540):
    case UINT32_C(0x80296648):
    case UINT32_C(0x80296850):
    case UINT32_C(0x80296930):
    case UINT32_C(0x80296A68):
    case UINT32_C(0x80296CD4):
    case UINT32_C(0x80296DB8):
        state->gpr[3] = 0u;
        state->pc = state->lr;
        return OPENSPM_OK;

    default:
        return OPENSPM_ERROR_NOT_FOUND;
    }
}

static void v8_log_rejected_exi_control(uint32_t control, int result) {
    char message[256];
    _snprintf_s(message, sizeof(message), _TRUNCATE,
                "M14 EXI0 rejected control=0x%08X csr=0x%08X data=0x%08X result=%d",
                (unsigned)control,
                (unsigned)g_v3.platform.exi_csr[0],
                (unsigned)g_v3.platform.exi_data[0],
                result);
    backend_log(OPENSPM_LOG_WARN, message);
}

static int OPENSPM_CALL v8_bus_read(void *context,
                                    uint32_t address,
                                    uint32_t width,
                                    uint64_t *out_value) {
    if (out_value == NULL) return OPENSPM_ERROR_INVALID_ARGUMENT;

    if (address == R8PE01_GPIOB_OUT) {
        if (width != 4u) return OPENSPM_ERROR_UNSUPPORTED;
        *out_value = g_v8_sensor_bar_power ? R8PE01_GPIO_SENSOR_BAR : 0u;
        return OPENSPM_OK;
    }

    if (address == R8PE01_AI_CONTROL || address == R8PE01_AI_VOLUME ||
        address == R8PE01_AI_INTERRUPT_TIMING) {
        if (width != 4u) return OPENSPM_ERROR_UNSUPPORTED;
        if (address == R8PE01_AI_CONTROL) *out_value = g_v8_ai.control;
        else if (address == R8PE01_AI_VOLUME) *out_value = g_v8_ai.volume;
        else *out_value = g_v8_ai.interrupt_timing;
        return OPENSPM_OK;
    }

    /* Intentionally do not synthesize R8PE01_AI_SAMPLE_COUNTER. The only
       proven consumer is the hosted physical SRC calibration loop above. */
    return v6_bus_read(context, address, width, out_value);
}

static int OPENSPM_CALL v8_bus_write(void *context,
                                     uint32_t address,
                                     uint32_t width,
                                     uint64_t value) {
    int font_dma;
    int result;

    if (address == R8PE01_GPIOB_OUT) {
        if (width != 4u) return OPENSPM_ERROR_UNSUPPORTED;
        if ((value & ~UINT64_C(0x00000100)) != 0u)
            return OPENSPM_ERROR_UNSUPPORTED;
        g_v8_sensor_bar_power =
            (value & R8PE01_GPIO_SENSOR_BAR) != 0u ? 1u : 0u;
        return OPENSPM_OK;
    }

    if (address == R8PE01_AI_CONTROL) {
        uint32_t guest_value;
        uint32_t status;
        if (width != 4u) return OPENSPM_ERROR_UNSUPPORTED;
        if ((value & ~UINT64_C(0x0000007F)) != 0u)
            return OPENSPM_ERROR_UNSUPPORTED;
        guest_value = (uint32_t)value;
        status = g_v8_ai.control & R8PE01_AI_CONTROL_STATUS_BIT;
        if ((guest_value & R8PE01_AI_CONTROL_STATUS_BIT) != 0u)
            status = 0u;
        g_v8_ai.control = status |
                          (guest_value & R8PE01_AI_CONTROL_ORDINARY_BITS);
        return OPENSPM_OK;
    }

    if (address == R8PE01_AI_VOLUME || address == R8PE01_AI_INTERRUPT_TIMING) {
        if (width != 4u) return OPENSPM_ERROR_UNSUPPORTED;
        /* dev17 proves AIInit writes zero to these two latches. Keep the
           bring-up model exact and fail closed when later audio code exposes
           the first non-zero volume/timing behavior. */
        if (value != 0u) return OPENSPM_ERROR_UNSUPPORTED;
        if (address == R8PE01_AI_VOLUME) g_v8_ai.volume = 0u;
        else g_v8_ai.interrupt_timing = 0u;
        return OPENSPM_OK;
    }

    if (address == R8PE01_EXI0_CR && width == 4u) {
        if (v8_try_begin_ansi_font_read((uint32_t)value)) return OPENSPM_OK;
        font_dma = v8_try_complete_ansi_font_dma((uint32_t)value);
        if (font_dma > 0) return OPENSPM_OK;
        if (font_dma < 0) return OPENSPM_ERROR_NOT_FOUND;
    }

    result = v6_bus_write(context, address, width, value);
    if (result == OPENSPM_OK && address == R8PE01_EXI0_CSR && width == 4u &&
        !v8_ansi_font_selected()) {
        g_v8_ansi_font.stream_active = 0u;
    }
    if (result != OPENSPM_OK && address == R8PE01_EXI0_CR && width == 4u) {
        v8_log_rejected_exi_control((uint32_t)value, result);
    }
    return result;
}

static int OPENSPM_CALL v8_query_service(void *backend_context,
                                         const char *service_id,
                                         uint32_t requested_version,
                                         void *out_service) {
    int result;

    if (service_id != NULL &&
        strcmp(service_id, OPENSPM_M14_HOSTCALL_SERVICE_ID) == 0) {
        openspm_m14_hostcall_service *service =
            (openspm_m14_hostcall_service *)out_service;
        if (service == NULL ||
            requested_version != OPENSPM_M14_HOSTCALL_SERVICE_VERSION ||
            service->header.struct_size < sizeof(*service)) {
            return OPENSPM_ERROR_ABI_MISMATCH;
        }
        memset(service, 0, sizeof(*service));
        service->header.struct_size = sizeof(*service);
        service->header.abi_version = OPENSPM_ABI_VERSION;
        service->context = &g_v8_hostcall;
        service->handle = v8_handle_hostcall;
        return OPENSPM_OK;
    }

    result = v6_query_service(backend_context, service_id,
                              requested_version, out_service);
    if (result != OPENSPM_OK) return result;

    if (service_id != NULL &&
        strcmp(service_id, OPENSPM_WII_CPU_SERVICE_ID) == 0) {
        openspm_wii_cpu_service *service = (openspm_wii_cpu_service *)out_service;
        service->read_bus = v8_bus_read;
        service->write_bus = v8_bus_write;
    }
    return OPENSPM_OK;
}

static int OPENSPM_CALL v8_run(void *backend_context,
                               const openspm_launch_desc *launch) {
    memset(&g_v8_hostcall, 0, sizeof(g_v8_hostcall));
    memset(&g_v8_ai, 0, sizeof(g_v8_ai));
    g_v8_sensor_bar_power = 0u;
    v8_build_ansi_font_rom();
    return v6_run(backend_context, launch);
}

OPENSPM_EXPORT int OPENSPM_CALL openspm_backend_query(
    uint32_t requested_abi,
    const openspm_host_api *host,
    openspm_backend_api *out_api) {
    int result = openspm_backend_query_v6_internal(requested_abi, host, out_api);
    if (result != OPENSPM_OK) return result;
    out_api->backend_id = "openspm.native";
    out_api->backend_version = "14.0-dev21";
    out_api->run = v8_run;
    out_api->query_service = v8_query_service;
    return OPENSPM_OK;
}