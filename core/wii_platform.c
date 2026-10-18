#include "openspm/wii_platform.h"

#include <string.h>

#define WII_PI_INTERRUPT_CAUSE UINT32_C(0xCC003000)
#define WII_PI_INTERRUPT_MASK  UINT32_C(0xCC003004)
#define WII_MI_INTERRUPT_MASK  UINT32_C(0xCC00401C)
#define WII_MI_INTERRUPT_CAUSE UINT32_C(0xCC00401E)
#define WII_VI_CLOCK_SELECT     UINT32_C(0xCC00206C)
#define WII_SI_CHANNEL0_OUT     UINT32_C(0xCD006400)
#define WII_SI_POLL             UINT32_C(0xCD006430)
#define WII_SI_COMCSR           UINT32_C(0xCD006434)
#define WII_SI_STATUS           UINT32_C(0xCD006438)
#define WII_SI_EXI_CLOCK_COUNT  UINT32_C(0xCD00643C)
#define WII_SI_IO_BUFFER        UINT32_C(0xCD006480)
#define WII_DSP_CONTROL         UINT32_C(0xCC00500A)
#define WII_AI_CONTROL          UINT32_C(0xCD006C00)
#define WII_EXI0_CSR            UINT32_C(0xCD006800)
#define WII_EXI0_MAR            UINT32_C(0xCD006804)
#define WII_EXI0_LENGTH         UINT32_C(0xCD006808)
#define WII_EXI0_CR             UINT32_C(0xCD00680C)
#define WII_EXI0_DATA           UINT32_C(0xCD006810)
#define WII_HW_PPCIRQFLAG       UINT32_C(0xCD000030)
#define WII_HW_PPCIRQMASK       UINT32_C(0xCD000034)

#define WII_SI_CHANNEL_STRIDE UINT32_C(0x0C)
#define WII_SI_BUFFER_WORDS UINT32_C(32)
#define WII_SI_TSTART UINT32_C(0x00000001)
#define WII_SI_CHANNEL_MASK UINT32_C(0x00000006)
#define WII_SI_CALLBEN UINT32_C(0x00000040)
#define WII_SI_CMDEN UINT32_C(0x00000080)
#define WII_SI_IN_LENGTH_MASK UINT32_C(0x00007F00)
#define WII_SI_OUT_LENGTH_MASK UINT32_C(0x007F0000)
#define WII_SI_CHANEN UINT32_C(0x01000000)
#define WII_SI_CHANNUM_MASK UINT32_C(0x06000000)
#define WII_SI_RDSTINTMSK UINT32_C(0x08000000)
#define WII_SI_RDSTINT UINT32_C(0x10000000)
#define WII_SI_COMERR UINT32_C(0x20000000)
#define WII_SI_TCINTMSK UINT32_C(0x40000000)
#define WII_SI_TCINT UINT32_C(0x80000000)
#define WII_SI_COMCSR_CONTROL_BITS \
    (WII_SI_CHANNEL_MASK | WII_SI_CALLBEN | WII_SI_CMDEN | \
     WII_SI_IN_LENGTH_MASK | WII_SI_OUT_LENGTH_MASK | WII_SI_CHANEN | \
     WII_SI_CHANNUM_MASK | WII_SI_RDSTINTMSK | WII_SI_TCINTMSK)
#define WII_SI_COMCSR_KNOWN_WRITE_BITS \
    (WII_SI_TSTART | WII_SI_COMCSR_CONTROL_BITS | WII_SI_RDSTINT | WII_SI_TCINT)
#define WII_SI_STATUS_ERROR_BITS UINT32_C(0x0F0F0F0F)
#define WII_SI_STATUS_WRST_BITS  UINT32_C(0x10101010)
#define WII_SI_STATUS_RDST_BITS  UINT32_C(0x20202020)
#define WII_SI_STATUS_WR         UINT32_C(0x80000000)
#define WII_SI_STATUS_KNOWN_BITS \
    (WII_SI_STATUS_ERROR_BITS | WII_SI_STATUS_WRST_BITS | \
     WII_SI_STATUS_RDST_BITS | WII_SI_STATUS_WR)

#define WII_EXI_STRIDE UINT32_C(0x14)
#define WII_EXI_IRQ_MASK_BITS UINT32_C(0x00000405)
#define WII_EXI_IRQ_STATUS_BITS UINT32_C(0x0000080A)
#define WII_EXI_SELECT_FREQ_BITS UINT32_C(0x000003F0)
#define WII_EXI_ROMDIS UINT32_C(0x00002000)
#define WII_EXI_CSR_WRITABLE_BITS \
    (WII_EXI_IRQ_MASK_BITS | WII_EXI_IRQ_STATUS_BITS | \
     WII_EXI_SELECT_FREQ_BITS | WII_EXI_ROMDIS)
#define WII_EXI_CR_START UINT32_C(0x00000001)
#define WII_EXI_CR_DMA UINT32_C(0x00000002)
#define WII_EXI_CR_RW_SHIFT 2u
#define WII_EXI_CR_RW_MASK UINT32_C(0x0000000C)
#define WII_EXI_CR_LENGTH_SHIFT 4u
#define WII_EXI_CR_LENGTH_MASK UINT32_C(0x00000030)
#define WII_EXI_CR_KNOWN_BITS \
    (WII_EXI_CR_START | WII_EXI_CR_DMA | WII_EXI_CR_RW_MASK | WII_EXI_CR_LENGTH_MASK)
#define WII_EXI_DMA_FIELD_MASK UINT32_C(0x03FFFFE0)
#define WII_EXI_STREAM_NONE UINT8_C(0)
#define WII_EXI_STREAM_SRAM_READ UINT8_C(1)
#define WII_EXI_STREAM_SRAM_WRITE UINT8_C(2)
#define WII_EXI_SRAM_BASE UINT32_C(0x00800000)
#define WII_EXI_SRAM_SIZE UINT32_C(68)

#define WII_DSP_IRQ_MASK_BITS UINT16_C(0x0150)
#define WII_DSP_IRQ_STATUS_BITS UINT16_C(0x02A8)
#define WII_AI_IRQ_MASK_BITS UINT32_C(0x00000004)
#define WII_AI_IRQ_STATUS_BITS UINT32_C(0x00000008)

static int require_width(uint32_t actual, uint32_t expected) {
    return actual == expected ? OPENSPM_OK : OPENSPM_ERROR_UNSUPPORTED;
}

static int si_channel_register_index(uint32_t address,
                                     uint32_t offset,
                                     uint32_t *out_index) {
    uint32_t relative;
    uint32_t index;
    if (address < WII_SI_CHANNEL0_OUT + offset) return 0;
    relative = address - (WII_SI_CHANNEL0_OUT + offset);
    if (relative % WII_SI_CHANNEL_STRIDE != 0u) return 0;
    index = relative / WII_SI_CHANNEL_STRIDE;
    if (index >= 4u) return 0;
    if (out_index != NULL) *out_index = index;
    return 1;
}

static int si_buffer_word_index(uint32_t address, uint32_t *out_index) {
    uint32_t relative;
    uint32_t index;
    if (address < WII_SI_IO_BUFFER) return 0;
    relative = address - WII_SI_IO_BUFFER;
    if ((relative & UINT32_C(3)) != 0u) return 0;
    index = relative / UINT32_C(4);
    if (index >= WII_SI_BUFFER_WORDS) return 0;
    if (out_index != NULL) *out_index = index;
    return 1;
}

static uint32_t si_norep_bit(uint32_t channel) {
    return UINT32_C(0x08000000) >> (channel * 8u);
}

static uint32_t si_rdst_bit(uint32_t channel) {
    return UINT32_C(0x20000000) >> (channel * 8u);
}

static void si_update_rdst_interrupt(openspm_wii_platform *platform) {
    if ((platform->si_status & WII_SI_STATUS_RDST_BITS) != 0u)
        platform->si_comcsr |= WII_SI_RDSTINT;
    else
        platform->si_comcsr &= ~WII_SI_RDSTINT;
}

static void si_complete_absent_device_transfer(openspm_wii_platform *platform,
                                               uint32_t channel) {
    /* OpenSPM does not expose a legacy GameCube controller through SI during
       M14 bring-up; gameplay input is supplied through the native WPAD seam.
       Complete the SDK's SI probe synchronously with the hardware-visible
       no-response result instead of leaving TSTART spinning forever. */
    platform->si_comcsr &= ~WII_SI_TSTART;
    platform->si_comcsr |= WII_SI_COMERR | WII_SI_TCINT;
    platform->si_status |= si_norep_bit(channel);
}

static int exi_register_index(uint32_t address, uint32_t offset, uint32_t *out_index) {
    uint32_t relative;
    uint32_t index;
    if (address < WII_EXI0_CSR + offset) return 0;
    relative = address - (WII_EXI0_CSR + offset);
    if (relative % WII_EXI_STRIDE != 0u) return 0;
    index = relative / WII_EXI_STRIDE;
    if (index >= 3u) return 0;
    if (out_index != NULL) *out_index = index;
    return 1;
}

static int exi_selected_device(const openspm_wii_platform *platform,
                               uint32_t channel,
                               uint32_t *out_device) {
    uint32_t selected;
    if (platform == NULL || channel >= 3u || out_device == NULL) return 0;
    selected = (platform->exi_csr[channel] >> 7) & UINT32_C(0x7);
    if (selected == 1u) *out_device = 0u;
    else if (selected == 2u) *out_device = 1u;
    else if (selected == 4u) *out_device = 2u;
    else return 0;
    return 1;
}

static uint32_t exi_command_from_data(uint32_t data, uint32_t size) {
    if (size >= 4u) return data;
    return data >> ((4u - size) * 8u);
}

static uint32_t exi_internal_command_address(uint32_t command) {
    return (command >> 6) & UINT32_C(0x01FFFFFF);
}

static uint8_t *guest_dma_pointer(openspm_wii_platform *platform,
                                  uint32_t physical_address,
                                  uint32_t size) {
    uint64_t end;
    if (platform == NULL) return NULL;
    end = (uint64_t)physical_address + (uint64_t)size;
    if (end >= (uint64_t)physical_address &&
        end <= platform->mem1_size && platform->mem1 != NULL) {
        return platform->mem1 + physical_address;
    }
    /* Broadway MEM2 is physically based at 0x10000000. EXI MAR cannot reach
       it with the observed 26-bit field, but keeping the resolver explicit
       makes the ownership rule clear for future platform DMA engines. */
    if (physical_address >= UINT32_C(0x10000000)) {
        uint32_t offset = physical_address - UINT32_C(0x10000000);
        end = (uint64_t)offset + (uint64_t)size;
        if (end >= (uint64_t)offset &&
            end <= platform->mem2_size && platform->mem2 != NULL) {
            return platform->mem2 + offset;
        }
    }
    return NULL;
}

static void exi_clear_stream(openspm_wii_platform *platform, uint32_t channel) {
    if (platform == NULL || channel >= 3u) return;
    platform->exi_last_command[channel] = 0u;
    platform->exi_last_command_size[channel] = 0u;
    platform->exi_stream_mode[channel] = WII_EXI_STREAM_NONE;
    platform->exi_stream_offset[channel] = 0u;
}

static int exi_complete_immediate(openspm_wii_platform *platform,
                                  uint32_t channel,
                                  uint32_t control) {
    uint32_t device;
    uint32_t rw;
    uint32_t size;
    if (platform == NULL || channel >= 3u) return OPENSPM_ERROR_INVALID_ARGUMENT;
    if ((control & WII_EXI_CR_DMA) != 0u) return OPENSPM_ERROR_UNSUPPORTED;
    if ((control & ~WII_EXI_CR_KNOWN_BITS) != 0u) return OPENSPM_ERROR_UNSUPPORTED;
    if (!exi_selected_device(platform, channel, &device)) return OPENSPM_ERROR_NOT_FOUND;

    rw = (control & WII_EXI_CR_RW_MASK) >> WII_EXI_CR_RW_SHIFT;
    size = ((control & WII_EXI_CR_LENGTH_MASK) >> WII_EXI_CR_LENGTH_SHIFT) + 1u;
    if (rw == 1u) {
        uint32_t command;

        if (channel == 0u && device == 1u &&
            platform->exi_stream_mode[channel] == WII_EXI_STREAM_SRAM_WRITE) {
            uint32_t cursor = platform->exi_stream_offset[channel];
            uint32_t data = platform->exi_data[channel];
            uint32_t i;
            if (cursor > WII_EXI_SRAM_SIZE || size > WII_EXI_SRAM_SIZE - cursor)
                return OPENSPM_ERROR_NOT_FOUND;
            for (i = 0; i < size; ++i) {
                platform->exi_sram[cursor + i] =
                    (uint8_t)(data >> ((3u - i) * 8u));
            }
            platform->exi_stream_offset[channel] += size;
            return OPENSPM_OK;
        }

        command = exi_command_from_data(platform->exi_data[channel], size);

        /* EXIInit probes legacy SP1/dev2 with a two-byte ID command. */
        if (channel == 0u && device == 2u && size == 2u && command == 0u) {
            platform->exi_last_command[channel] = command;
            platform->exi_last_command_size[channel] = (uint8_t)size;
            return OPENSPM_OK;
        }

        /* OS SRAM setup selects the internal MX23L4005 at ch0/dev1. Its
           command encodes a byte address in bits 6..30 and write direction in
           bit 31. Accept only the documented 68-byte SRAM window. */
        if (channel == 0u && device == 1u && size == 4u) {
            uint32_t address = exi_internal_command_address(command);
            uint32_t offset;
            if (address < WII_EXI_SRAM_BASE ||
                address >= WII_EXI_SRAM_BASE + WII_EXI_SRAM_SIZE)
                return OPENSPM_ERROR_NOT_FOUND;
            offset = address - WII_EXI_SRAM_BASE;
            platform->exi_last_command[channel] = command;
            platform->exi_last_command_size[channel] = (uint8_t)size;
            platform->exi_stream_offset[channel] = offset;
            platform->exi_stream_mode[channel] =
                (command & UINT32_C(0x80000000)) != 0u
                    ? WII_EXI_STREAM_SRAM_WRITE
                    : WII_EXI_STREAM_SRAM_READ;
            return OPENSPM_OK;
        }

        /* Fail at the initiating write rather than carrying an unknown
           transaction forward and failing on some later read. */
        return OPENSPM_ERROR_NOT_FOUND;
    }
    if (rw != 0u) return OPENSPM_ERROR_UNSUPPORTED;

    if (channel == 0u && device == 2u && size == 4u &&
        platform->exi_last_command_size[channel] == 2u &&
        platform->exi_last_command[channel] == 0u) {
        platform->exi_data[channel] = 0u;
        return OPENSPM_OK;
    }
    return OPENSPM_ERROR_NOT_FOUND;
}

static int exi_complete_dma(openspm_wii_platform *platform,
                            uint32_t channel,
                            uint32_t control) {
    uint32_t device;
    uint32_t rw;
    uint32_t length;
    uint32_t cursor;
    uint8_t *memory;
    if (platform == NULL || channel >= 3u) return OPENSPM_ERROR_INVALID_ARGUMENT;
    if ((control & WII_EXI_CR_DMA) == 0u) return OPENSPM_ERROR_UNSUPPORTED;
    if ((control & ~WII_EXI_CR_KNOWN_BITS) != 0u) return OPENSPM_ERROR_UNSUPPORTED;
    if (!exi_selected_device(platform, channel, &device)) return OPENSPM_ERROR_NOT_FOUND;

    rw = (control & WII_EXI_CR_RW_MASK) >> WII_EXI_CR_RW_SHIFT;
    length = platform->exi_length[channel];
    if (rw != 0u || length == 0u) return OPENSPM_ERROR_UNSUPPORTED;

    /* The first observed DMA is OSReadSram: internal ch0/dev1, command
       0x20000100, 64 bytes from SRAM offset 4 into MEM1. Model the device
       generally within its documented SRAM window, but nothing beyond it. */
    if (channel != 0u || device != 1u ||
        platform->exi_stream_mode[channel] != WII_EXI_STREAM_SRAM_READ)
        return OPENSPM_ERROR_NOT_FOUND;
    cursor = platform->exi_stream_offset[channel];
    if (cursor > WII_EXI_SRAM_SIZE || length > WII_EXI_SRAM_SIZE - cursor)
        return OPENSPM_ERROR_NOT_FOUND;
    memory = guest_dma_pointer(platform, platform->exi_mar[channel], length);
    if (memory == NULL) return OPENSPM_ERROR_NOT_FOUND;

    memcpy(memory, platform->exi_sram + cursor, length);
    platform->exi_stream_offset[channel] += length;
    platform->exi_length[channel] = 0u;
    return OPENSPM_OK;
}

void openspm_wii_platform_reset(openspm_wii_platform *platform) {
    if (platform == NULL) return;
    memset(platform, 0, sizeof(*platform));
}

void openspm_wii_platform_set_memory(openspm_wii_platform *platform,
                                     uint8_t *mem1,
                                     uint64_t mem1_size,
                                     uint8_t *mem2,
                                     uint64_t mem2_size) {
    if (platform == NULL) return;
    platform->mem1 = mem1;
    platform->mem1_size = mem1_size;
    platform->mem2 = mem2;
    platform->mem2_size = mem2_size;
}

int openspm_wii_platform_bus_read(openspm_wii_platform *platform,
                                  uint32_t address,
                                  uint32_t width,
                                  uint64_t *out_value) {
    uint32_t index;
    if (platform == NULL || out_value == NULL) return OPENSPM_ERROR_INVALID_ARGUMENT;
    *out_value = 0;

    switch (address) {
        case WII_PI_INTERRUPT_CAUSE:
            if (require_width(width, 4u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            *out_value = platform->pi_interrupt_cause;
            return OPENSPM_OK;
        case WII_PI_INTERRUPT_MASK:
            if (require_width(width, 4u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            *out_value = platform->pi_interrupt_mask;
            return OPENSPM_OK;
        case WII_HW_PPCIRQFLAG:
            if (require_width(width, 4u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            *out_value = platform->hollywood_ppc_irq_flags;
            return OPENSPM_OK;
        case WII_HW_PPCIRQMASK:
            if (require_width(width, 4u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            *out_value = platform->hollywood_ppc_irq_mask;
            return OPENSPM_OK;
        case WII_MI_INTERRUPT_MASK:
            if (require_width(width, 2u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            *out_value = platform->mi_interrupt_mask;
            return OPENSPM_OK;
        case WII_MI_INTERRUPT_CAUSE:
            if (require_width(width, 2u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            *out_value = platform->mi_interrupt_cause;
            return OPENSPM_OK;
        case WII_VI_CLOCK_SELECT:
            if (require_width(width, 2u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            *out_value = platform->vi_clock_select;
            return OPENSPM_OK;
        case WII_SI_POLL:
            if (require_width(width, 4u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            *out_value = platform->si_poll;
            return OPENSPM_OK;
        case WII_SI_COMCSR:
            if (require_width(width, 4u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            *out_value = platform->si_comcsr;
            return OPENSPM_OK;
        case WII_SI_STATUS:
            if (require_width(width, 4u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            *out_value = platform->si_status;
            return OPENSPM_OK;
        case WII_SI_EXI_CLOCK_COUNT:
            if (require_width(width, 4u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            *out_value = platform->si_exi_clock_count;
            return OPENSPM_OK;
        case WII_DSP_CONTROL:
            if (require_width(width, 2u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            *out_value = platform->dsp_control;
            return OPENSPM_OK;
        case WII_AI_CONTROL:
            if (require_width(width, 4u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            *out_value = platform->ai_control;
            return OPENSPM_OK;
        default:
            break;
    }

    if (si_channel_register_index(address, 0u, &index)) {
        if (require_width(width, 4u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
        *out_value = platform->si_channel_out[index];
        return OPENSPM_OK;
    }
    if (si_channel_register_index(address, 4u, &index)) {
        if (require_width(width, 4u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
        *out_value = platform->si_channel_in_hi[index];
        platform->si_status &= ~si_rdst_bit(index);
        si_update_rdst_interrupt(platform);
        return OPENSPM_OK;
    }
    if (si_channel_register_index(address, 8u, &index)) {
        if (require_width(width, 4u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
        *out_value = platform->si_channel_in_lo[index];
        platform->si_status &= ~si_rdst_bit(index);
        si_update_rdst_interrupt(platform);
        return OPENSPM_OK;
    }
    if (si_buffer_word_index(address, &index)) {
        if (require_width(width, 4u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
        *out_value = platform->si_buffer[index];
        return OPENSPM_OK;
    }

    if (exi_register_index(address, 0u, &index)) {
        if (require_width(width, 4u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
        *out_value = platform->exi_csr[index];
        return OPENSPM_OK;
    }
    if (exi_register_index(address, 0x04u, &index)) {
        if (require_width(width, 4u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
        *out_value = platform->exi_mar[index];
        return OPENSPM_OK;
    }
    if (exi_register_index(address, 0x08u, &index)) {
        if (require_width(width, 4u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
        *out_value = platform->exi_length[index];
        return OPENSPM_OK;
    }
    if (exi_register_index(address, 0x0Cu, &index)) {
        if (require_width(width, 4u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
        *out_value = platform->exi_cr[index];
        return OPENSPM_OK;
    }
    if (exi_register_index(address, 0x10u, &index)) {
        if (require_width(width, 4u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
        *out_value = platform->exi_data[index];
        return OPENSPM_OK;
    }
    return OPENSPM_ERROR_NOT_FOUND;
}

int openspm_wii_platform_bus_write(openspm_wii_platform *platform,
                                   uint32_t address,
                                   uint32_t width,
                                   uint64_t value) {
    uint32_t index;
    if (platform == NULL) return OPENSPM_ERROR_INVALID_ARGUMENT;

    switch (address) {
        case WII_PI_INTERRUPT_CAUSE:
            if (require_width(width, 4u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            platform->pi_interrupt_cause &= ~(uint32_t)value;
            return OPENSPM_OK;
        case WII_PI_INTERRUPT_MASK:
            if (require_width(width, 4u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            platform->pi_interrupt_mask = (uint32_t)value;
            return OPENSPM_OK;
        case WII_HW_PPCIRQFLAG:
            if (require_width(width, 4u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            platform->hollywood_ppc_irq_flags &= ~(uint32_t)value;
            return OPENSPM_OK;
        case WII_HW_PPCIRQMASK:
            if (require_width(width, 4u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            platform->hollywood_ppc_irq_mask = (uint32_t)value;
            return OPENSPM_OK;
        case WII_MI_INTERRUPT_MASK:
            if (require_width(width, 2u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            platform->mi_interrupt_mask = (uint16_t)value;
            return OPENSPM_OK;
        case WII_MI_INTERRUPT_CAUSE:
            if (require_width(width, 2u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            platform->mi_interrupt_cause &= (uint16_t)~(uint16_t)value;
            return OPENSPM_OK;
        case WII_VI_CLOCK_SELECT:
            if (require_width(width, 2u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            if (((uint16_t)value & UINT16_C(0xFFFE)) != 0u) return OPENSPM_ERROR_UNSUPPORTED;
            platform->vi_clock_select = (uint16_t)value;
            return OPENSPM_OK;
        case WII_SI_POLL:
            if (require_width(width, 4u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            platform->si_poll = (uint32_t)value;
            return OPENSPM_OK;
        case WII_SI_COMCSR: {
            uint32_t guest_value = (uint32_t)value;
            uint32_t status_bits;
            uint32_t control_bits;
            uint32_t channel;
            if (require_width(width, 4u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            if ((guest_value & ~WII_SI_COMCSR_KNOWN_WRITE_BITS) != 0u)
                return OPENSPM_ERROR_UNSUPPORTED;

            status_bits = platform->si_comcsr &
                          (WII_SI_RDSTINT | WII_SI_COMERR | WII_SI_TCINT);
            if ((guest_value & WII_SI_RDSTINT) != 0u) status_bits &= ~WII_SI_RDSTINT;
            if ((guest_value & WII_SI_TCINT) != 0u) status_bits &= ~WII_SI_TCINT;
            control_bits = guest_value & WII_SI_COMCSR_CONTROL_BITS;
            platform->si_comcsr = status_bits | control_bits;

            if ((guest_value & WII_SI_TSTART) != 0u) {
                channel = (guest_value & WII_SI_CHANNEL_MASK) >> 1u;
                platform->si_comcsr |= WII_SI_TSTART;
                si_complete_absent_device_transfer(platform, channel);
            }
            return OPENSPM_OK;
        }
        case WII_SI_STATUS: {
            uint32_t guest_value = (uint32_t)value;
            if (require_width(width, 4u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            if ((guest_value & ~WII_SI_STATUS_KNOWN_BITS) != 0u)
                return OPENSPM_ERROR_UNSUPPORTED;
            platform->si_status &= ~(guest_value & WII_SI_STATUS_ERROR_BITS);
            if ((guest_value & WII_SI_STATUS_WR) != 0u) {
                /* SDK command-copy writes complete synchronously with no
                   attached legacy SI devices in the current native model. */
                platform->si_status &= ~(WII_SI_STATUS_WR | WII_SI_STATUS_WRST_BITS);
            }
            si_update_rdst_interrupt(platform);
            return OPENSPM_OK;
        }
        case WII_SI_EXI_CLOCK_COUNT:
            if (require_width(width, 4u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            platform->si_exi_clock_count = (uint32_t)value;
            return OPENSPM_OK;
        case WII_DSP_CONTROL: {
            uint16_t guest_value = (uint16_t)value;
            if (require_width(width, 2u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            if ((guest_value & (uint16_t)~(WII_DSP_IRQ_MASK_BITS | WII_DSP_IRQ_STATUS_BITS)) != 0u)
                return OPENSPM_ERROR_UNSUPPORTED;
            platform->dsp_control &= (uint16_t)~(guest_value & WII_DSP_IRQ_STATUS_BITS);
            platform->dsp_control = (uint16_t)((platform->dsp_control & (uint16_t)~WII_DSP_IRQ_MASK_BITS) |
                                               (guest_value & WII_DSP_IRQ_MASK_BITS));
            return OPENSPM_OK;
        }
        case WII_AI_CONTROL: {
            uint32_t guest_value = (uint32_t)value;
            if (require_width(width, 4u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            if ((guest_value & ~(WII_AI_IRQ_MASK_BITS | WII_AI_IRQ_STATUS_BITS)) != 0u)
                return OPENSPM_ERROR_UNSUPPORTED;
            platform->ai_control &= ~(guest_value & WII_AI_IRQ_STATUS_BITS);
            platform->ai_control = (platform->ai_control & ~WII_AI_IRQ_MASK_BITS) |
                                   (guest_value & WII_AI_IRQ_MASK_BITS);
            return OPENSPM_OK;
        }
        default:
            break;
    }

    if (si_channel_register_index(address, 0u, &index)) {
        if (require_width(width, 4u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
        platform->si_channel_out[index] = (uint32_t)value;
        return OPENSPM_OK;
    }
    if (si_channel_register_index(address, 4u, &index)) {
        if (require_width(width, 4u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
        platform->si_channel_in_hi[index] = (uint32_t)value;
        return OPENSPM_OK;
    }
    if (si_channel_register_index(address, 8u, &index)) {
        if (require_width(width, 4u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
        platform->si_channel_in_lo[index] = (uint32_t)value;
        return OPENSPM_OK;
    }
    if (si_buffer_word_index(address, &index)) {
        if (require_width(width, 4u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
        platform->si_buffer[index] = (uint32_t)value;
        return OPENSPM_OK;
    }

    if (exi_register_index(address, 0u, &index)) {
        uint32_t guest_value = (uint32_t)value;
        uint32_t ordinary_bits = WII_EXI_IRQ_MASK_BITS |
                                 WII_EXI_SELECT_FREQ_BITS |
                                 WII_EXI_ROMDIS;
        uint32_t old_select;
        uint32_t new_select;
        if (require_width(width, 4u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
        if ((guest_value & ~WII_EXI_CSR_WRITABLE_BITS) != 0u)
            return OPENSPM_ERROR_UNSUPPORTED;
        old_select = platform->exi_csr[index] & UINT32_C(0x00000380);
        new_select = guest_value & UINT32_C(0x00000380);
        platform->exi_csr[index] &= ~(guest_value & WII_EXI_IRQ_STATUS_BITS);
        platform->exi_csr[index] = (platform->exi_csr[index] & ~ordinary_bits) |
                                   (guest_value & ordinary_bits);
        if (old_select != new_select) exi_clear_stream(platform, index);
        return OPENSPM_OK;
    }
    if (exi_register_index(address, 0x04u, &index)) {
        if (require_width(width, 4u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
        platform->exi_mar[index] = (uint32_t)value & WII_EXI_DMA_FIELD_MASK;
        return OPENSPM_OK;
    }
    if (exi_register_index(address, 0x08u, &index)) {
        if (require_width(width, 4u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
        platform->exi_length[index] = (uint32_t)value & WII_EXI_DMA_FIELD_MASK;
        return OPENSPM_OK;
    }
    if (exi_register_index(address, 0x10u, &index)) {
        if (require_width(width, 4u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
        platform->exi_data[index] = (uint32_t)value;
        return OPENSPM_OK;
    }
    if (exi_register_index(address, 0x0Cu, &index)) {
        uint32_t guest_value = (uint32_t)value;
        int result;
        if (require_width(width, 4u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
        if ((guest_value & ~WII_EXI_CR_KNOWN_BITS) != 0u)
            return OPENSPM_ERROR_UNSUPPORTED;
        if ((guest_value & WII_EXI_CR_START) == 0u) {
            platform->exi_cr[index] = guest_value;
            return OPENSPM_OK;
        }
        result = (guest_value & WII_EXI_CR_DMA) != 0u
                     ? exi_complete_dma(platform, index, guest_value)
                     : exi_complete_immediate(platform, index, guest_value);
        if (result != OPENSPM_OK) return result;
        /* Transfers complete synchronously in this bring-up platform model.
           Clearing TSTART before returning prevents Nintendo poll loops from
           becoming permanent host spins. */
        platform->exi_cr[index] = guest_value & ~WII_EXI_CR_START;
        return OPENSPM_OK;
    }
    return OPENSPM_ERROR_NOT_FOUND;
}
