#include "openspm/wii_boot_platform.h"

#include <string.h>

#define WII_HW_COMPAT          UINT32_C(0xCD800180)
#define WII_HW_PLLAI           UINT32_C(0xCD8001CC)
#define WII_HW_PLLAIEXT        UINT32_C(0xCD8001D0)

#define WII_IPC_PPCMSG         UINT32_C(0xCD000000)
#define WII_IPC_PPCCTRL        UINT32_C(0xCD000004)
#define WII_IPC_ARMMSG         UINT32_C(0xCD000008)

#define WII_IPC_CTRL_X1        UINT32_C(0x01)
#define WII_IPC_CTRL_Y2        UINT32_C(0x02)
#define WII_IPC_CTRL_Y1        UINT32_C(0x04)
#define WII_IPC_CTRL_X2        UINT32_C(0x08)
#define WII_IPC_CTRL_IY1       UINT32_C(0x10)
#define WII_IPC_CTRL_IY2       UINT32_C(0x20)
#define WII_IPC_CTRL_PPC_BITS  UINT32_C(0x3F)
#define WII_IPC_CTRL_STATUS_BITS (WII_IPC_CTRL_Y1 | WII_IPC_CTRL_Y2)
#define WII_IPC_CTRL_LATCH_BITS  (WII_IPC_CTRL_X2 | WII_IPC_CTRL_IY1 | WII_IPC_CTRL_IY2)

#define WII_PI_RESET_CODE       UINT32_C(0xCC003024)
#define WII_MI_PROTECTION       UINT32_C(0xCC004010)
#define WII_MI_INTERRUPT_CLEAR  UINT32_C(0xCC004020)
#define WII_MI_CONFIG           UINT32_C(0xCC004028)
#define WII_DI_CONFIG           UINT32_C(0xCD006024)

#define WII_DSP_MAIL_TO_HI     UINT32_C(0xCC005000)
#define WII_DSP_MAIL_TO_LO     UINT32_C(0xCC005002)
#define WII_DSP_MAIL_FROM_HI   UINT32_C(0xCC005004)
#define WII_DSP_MAIL_FROM_LO   UINT32_C(0xCC005006)
#define WII_DSP_CONTROL        UINT32_C(0xCC00500A)
#define WII_DSP_AR_INFO        UINT32_C(0xCC005012)
#define WII_DSP_AR_MODE        UINT32_C(0xCC005016)
#define WII_DSP_AR_REFRESH     UINT32_C(0xCC00501A)
#define WII_DSP_ARAM_MMADDR    UINT32_C(0xCC005020)
#define WII_DSP_ARAM_ARADDR    UINT32_C(0xCC005024)
#define WII_DSP_ARAM_COUNT     UINT32_C(0xCC005028)
#define WII_DSP_AUDIO_DMA_HI   UINT32_C(0xCC005030)
#define WII_DSP_AUDIO_DMA_LO   UINT32_C(0xCC005032)
#define WII_DSP_AUDIO_DMA_CTL  UINT32_C(0xCC005036)

#define WII_DSP_RESET          UINT16_C(0x0001)
#define WII_DSP_ASSERT_INT     UINT16_C(0x0002)
#define WII_DSP_HALT           UINT16_C(0x0004)
#define WII_DSP_IRQ_STATUS     UINT16_C(0x00A8)
#define WII_DSP_IRQ_MASK       UINT16_C(0x0150)
#define WII_DSP_DMA_STATE      UINT16_C(0x0200)
#define WII_DSP_INIT_CODE      UINT16_C(0x0400)
#define WII_DSP_INIT           UINT16_C(0x0800)
#define WII_DSP_CONTROL_MUTABLE \
    (WII_DSP_ASSERT_INT | WII_DSP_HALT | WII_DSP_IRQ_MASK | \
     WII_DSP_INIT_CODE | WII_DSP_INIT)
#define WII_DSP_CONTROL_KNOWN_WRITE \
    (WII_DSP_RESET | WII_DSP_CONTROL_MUTABLE | WII_DSP_IRQ_STATUS)

#define WII_DSP_ARAM_ADDRESS_MASK UINT32_C(0x03FFFFE0)
#define WII_DSP_ARAM_COUNT_MASK   UINT32_C(0x7FFFFFE0)
#define WII_DSP_ARAM_DIRECTION    UINT32_C(0x80000000)
#define WII_DSP_AUDIO_HI_MASK     UINT16_C(0x1FFF)
#define WII_DSP_AUDIO_LO_MASK     UINT16_C(0xFFE0)
#define WII_MEM2_PHYSICAL_BASE    UINT32_C(0x10000000)

static int require_width(uint32_t actual, uint32_t expected) {
    return actual == expected ? OPENSPM_OK : OPENSPM_ERROR_UNSUPPORTED;
}

static int write_ipc_ppcctrl(openspm_wii_boot_platform *platform,
                             uint32_t guest_value) {
    uint32_t status;

    if ((guest_value & ~WII_IPC_CTRL_PPC_BITS) != 0u)
        return OPENSPM_ERROR_UNSUPPORTED;

    /* X1 submits the command pointer in PPCMSG to Starlet/IOS. OpenSPM's M14
       IOS calls are already lifted to named host services, so do not pretend
       to execute a raw IPC request here. Keep that path explicitly closed
       until a real native IPC service owns it. */
    if ((guest_value & WII_IPC_CTRL_X1) != 0u)
        return OPENSPM_ERROR_UNSUPPORTED;

    /* PPC writes Y1/Y2 as write-one-to-clear status bits, while X2 and the two
       Broadway interrupt enables are ordinary latches. At the observed cold
       startup boundary no Starlet reply/ack status is pending. */
    status = platform->ipc_ppc_ctrl & WII_IPC_CTRL_STATUS_BITS;
    status &= ~(guest_value & WII_IPC_CTRL_STATUS_BITS);
    platform->ipc_ppc_ctrl = status | (guest_value & WII_IPC_CTRL_LATCH_BITS);
    return OPENSPM_OK;
}

static uint8_t *physical_memory(openspm_wii_boot_platform *platform,
                                uint32_t address,
                                uint32_t size) {
    uint64_t end;
    if (platform == NULL) return NULL;

    end = (uint64_t)address + (uint64_t)size;
    if (end >= address && platform->mem1 != NULL && end <= platform->mem1_size)
        return platform->mem1 + address;

    if (address >= WII_MEM2_PHYSICAL_BASE) {
        uint32_t offset = address - WII_MEM2_PHYSICAL_BASE;
        end = (uint64_t)offset + (uint64_t)size;
        if (end >= offset && platform->mem2 != NULL && end <= platform->mem2_size)
            return platform->mem2 + offset;
    }
    return NULL;
}

static int complete_aram_dma(openspm_wii_boot_platform *platform,
                             uint32_t count_value) {
    uint32_t count = count_value & WII_DSP_ARAM_COUNT_MASK;
    uint32_t ar_offset = platform->dsp_aram_araddr & WII_DSP_ARAM_ADDRESS_MASK;
    uint8_t *main_memory;
    uint8_t *exram;

    if (count == 0u) {
        platform->dsp_aram_count = 0u;
        return OPENSPM_OK;
    }
    if (platform->mem2 == NULL ||
        (uint64_t)ar_offset + (uint64_t)count > platform->mem2_size)
        return OPENSPM_ERROR_NOT_FOUND;

    main_memory = physical_memory(platform,
                                  platform->dsp_aram_mmaddr & WII_DSP_ARAM_ADDRESS_MASK,
                                  count);
    if (main_memory == NULL) return OPENSPM_ERROR_NOT_FOUND;
    exram = platform->mem2 + ar_offset;

    /* SPM's first two ARAM transfers are tiny and immediately polled. Complete
       synchronously for M14 bring-up while preserving the hardware-visible
       busy bit contract around the transfer itself. On Wii, ARAM maps to the
       external RAM backing store rather than a GameCube ARAM chip. */
    platform->dsp_control |= WII_DSP_DMA_STATE;
    if ((count_value & WII_DSP_ARAM_DIRECTION) != 0u)
        memcpy(main_memory, exram, count);
    else
        memcpy(exram, main_memory, count);
    platform->dsp_control &= (uint16_t)~WII_DSP_DMA_STATE;
    /* Completion raises the ARAM transfer-complete status bit. Nintendo's
       startup code polls this bit and then acknowledges it by writing the
       control register back (W1C). */
    platform->dsp_control |= UINT16_C(0x0020);
    platform->dsp_aram_count = 0u;
    return OPENSPM_OK;
}

void openspm_wii_boot_platform_reset(openspm_wii_boot_platform *platform,
                                     openspm_wii_platform *base) {
    if (platform == NULL) return;
    memset(platform, 0, sizeof(*platform));
    platform->base = base;
    platform->dsp_control = WII_DSP_HALT;
    platform->dsp_ar_mode = UINT16_C(1);
    platform->dsp_ar_refresh = UINT16_C(156);
}

void openspm_wii_boot_platform_set_memory(openspm_wii_boot_platform *platform,
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

int openspm_wii_boot_platform_bus_read(openspm_wii_boot_platform *platform,
                                       uint32_t address,
                                       uint32_t width,
                                       uint64_t *out_value) {
    if (platform == NULL || out_value == NULL) return OPENSPM_ERROR_INVALID_ARGUMENT;
    *out_value = 0u;

    switch (address) {
        case WII_HW_COMPAT:
            if (require_width(width, 4u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            *out_value = platform->hollywood_compat;
            return OPENSPM_OK;
        case WII_HW_PLLAI:
            if (require_width(width, 4u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            *out_value = platform->hollywood_pll_ai;
            return OPENSPM_OK;
        case WII_HW_PLLAIEXT:
            if (require_width(width, 4u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            *out_value = platform->hollywood_pll_ai_ext;
            return OPENSPM_OK;
        case WII_IPC_PPCMSG:
            if (require_width(width, 4u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            return OPENSPM_ERROR_UNSUPPORTED;
        case WII_IPC_PPCCTRL:
            if (require_width(width, 4u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            *out_value = platform->ipc_ppc_ctrl;
            return OPENSPM_OK;
        case WII_IPC_ARMMSG:
            if (require_width(width, 4u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            *out_value = platform->ipc_arm_msg;
            return OPENSPM_OK;
        case WII_PI_RESET_CODE:
            if (require_width(width, 4u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            *out_value = platform->pi_reset_code;
            return OPENSPM_OK;
        case WII_MI_PROTECTION:
            if (require_width(width, 2u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            *out_value = platform->mi_protection_control;
            return OPENSPM_OK;
        case WII_MI_INTERRUPT_CLEAR:
            if (require_width(width, 2u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            *out_value = platform->mi_interrupt_clear;
            return OPENSPM_OK;
        case WII_MI_CONFIG:
            if (require_width(width, 2u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            *out_value = platform->mi_config;
            return OPENSPM_OK;
        case WII_DI_CONFIG:
            if (require_width(width, 4u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            *out_value = UINT32_C(1);
            return OPENSPM_OK;
        case WII_DSP_MAIL_TO_HI:
            if (require_width(width, 2u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            *out_value = platform->dsp_mail_to_hi;
            return OPENSPM_OK;
        case WII_DSP_MAIL_TO_LO:
            if (require_width(width, 2u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            *out_value = platform->dsp_mail_to_lo;
            return OPENSPM_OK;
        case WII_DSP_MAIL_FROM_HI:
            if (require_width(width, 2u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            *out_value = platform->dsp_mail_from_hi;
            return OPENSPM_OK;
        case WII_DSP_MAIL_FROM_LO:
            if (require_width(width, 2u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            *out_value = platform->dsp_mail_from_lo;
            platform->dsp_mail_from_hi &= UINT16_C(0x7FFF);
            return OPENSPM_OK;
        case WII_DSP_CONTROL:
            if (require_width(width, 2u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            *out_value = platform->dsp_control;
            return OPENSPM_OK;
        case WII_DSP_AR_INFO:
            if (require_width(width, 2u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            *out_value = platform->dsp_ar_info;
            return OPENSPM_OK;
        case WII_DSP_AR_MODE:
            if (require_width(width, 2u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            *out_value = platform->dsp_ar_mode;
            return OPENSPM_OK;
        case WII_DSP_AR_REFRESH:
            if (require_width(width, 2u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            *out_value = platform->dsp_ar_refresh;
            return OPENSPM_OK;
        case WII_DSP_ARAM_MMADDR:
            if (require_width(width, 4u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            *out_value = platform->dsp_aram_mmaddr;
            return OPENSPM_OK;
        case WII_DSP_ARAM_ARADDR:
            if (require_width(width, 4u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            *out_value = platform->dsp_aram_araddr;
            return OPENSPM_OK;
        case WII_DSP_ARAM_COUNT:
            if (require_width(width, 4u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            *out_value = platform->dsp_aram_count;
            return OPENSPM_OK;
        case WII_DSP_AUDIO_DMA_HI:
            if (require_width(width, 2u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            *out_value = (uint16_t)(platform->dsp_audio_dma_source >> 16);
            return OPENSPM_OK;
        case WII_DSP_AUDIO_DMA_LO:
            if (require_width(width, 2u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            *out_value = (uint16_t)platform->dsp_audio_dma_source;
            return OPENSPM_OK;
        case WII_DSP_AUDIO_DMA_CTL:
            if (require_width(width, 2u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            *out_value = platform->dsp_audio_dma_control;
            return OPENSPM_OK;
        default:
            break;
    }

    if (platform->base == NULL) return OPENSPM_ERROR_NOT_FOUND;
    return openspm_wii_platform_bus_read(platform->base, address, width, out_value);
}

int openspm_wii_boot_platform_bus_write(openspm_wii_boot_platform *platform,
                                        uint32_t address,
                                        uint32_t width,
                                        uint64_t value) {
    if (platform == NULL) return OPENSPM_ERROR_INVALID_ARGUMENT;

    switch (address) {
        case WII_HW_COMPAT:
            if (require_width(width, 4u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            platform->hollywood_compat = (uint32_t)value;
            return OPENSPM_OK;
        case WII_HW_PLLAI:
            if (require_width(width, 4u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            platform->hollywood_pll_ai = (uint32_t)value;
            return OPENSPM_OK;
        case WII_HW_PLLAIEXT:
            if (require_width(width, 4u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            platform->hollywood_pll_ai_ext = (uint32_t)value;
            return OPENSPM_OK;
        case WII_IPC_PPCMSG:
            if (require_width(width, 4u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            platform->ipc_ppc_msg = (uint32_t)value;
            return OPENSPM_OK;
        case WII_IPC_PPCCTRL:
            if (require_width(width, 4u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            return write_ipc_ppcctrl(platform, (uint32_t)value);
        case WII_IPC_ARMMSG:
            (void)value;
            if (require_width(width, 4u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            return OPENSPM_ERROR_UNSUPPORTED;
        case WII_PI_RESET_CODE:
            if (require_width(width, 4u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            platform->pi_reset_code = (uint32_t)value;
            return OPENSPM_OK;
        case WII_MI_PROTECTION:
            if (require_width(width, 2u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            platform->mi_protection_control = (uint16_t)value;
            return OPENSPM_OK;
        case WII_MI_INTERRUPT_CLEAR:
            if (require_width(width, 2u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            platform->mi_interrupt_clear = (uint16_t)value;
            return OPENSPM_OK;
        case WII_MI_CONFIG:
            if (require_width(width, 2u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            platform->mi_config = (uint16_t)value;
            return OPENSPM_OK;
        case WII_DI_CONFIG:
            (void)value;
            if (require_width(width, 4u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            return OPENSPM_ERROR_UNSUPPORTED;
        case WII_DSP_MAIL_TO_HI:
            if (require_width(width, 2u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            platform->dsp_mail_to_hi = (uint16_t)value;
            return OPENSPM_OK;
        case WII_DSP_MAIL_TO_LO:
            if (require_width(width, 2u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            platform->dsp_mail_to_lo = (uint16_t)value;
            platform->dsp_mail_to_hi &= UINT16_C(0x7FFF);
            return OPENSPM_OK;
        case WII_DSP_CONTROL: {
            uint16_t guest_value = (uint16_t)value;
            uint16_t old_control;
            uint16_t dma_state;
            if (require_width(width, 2u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            if ((guest_value & (uint16_t)~WII_DSP_CONTROL_KNOWN_WRITE) != 0u)
                return OPENSPM_ERROR_UNSUPPORTED;

            old_control = platform->dsp_control;
            dma_state = platform->dsp_control & WII_DSP_DMA_STATE;
            platform->dsp_control &= (uint16_t)~(guest_value & WII_DSP_IRQ_STATUS);
            platform->dsp_control = (uint16_t)((platform->dsp_control &
                                                (uint16_t)~WII_DSP_CONTROL_MUTABLE) |
                                               (guest_value & WII_DSP_CONTROL_MUTABLE));
            platform->dsp_control = (uint16_t)((platform->dsp_control &
                                                (uint16_t)~WII_DSP_DMA_STATE) |
                                               dma_state);
            platform->dsp_control &= (uint16_t)~WII_DSP_RESET;

            if ((old_control & WII_DSP_HALT) != 0u &&
                (platform->dsp_control & WII_DSP_HALT) == 0u &&
                (platform->dsp_control & WII_DSP_INIT) == 0u &&
                (platform->dsp_mail_from_hi & UINT16_C(0x8000)) == 0u &&
                platform->dsp_mail_from_hi == 0u &&
                platform->dsp_mail_from_lo == 0u) {
                platform->dsp_mail_from_hi = UINT16_C(0x8054);
                platform->dsp_mail_from_lo = UINT16_C(0x4348);
            }
            return OPENSPM_OK;
        }
        case WII_DSP_AR_INFO:
            if (require_width(width, 2u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            if (((uint16_t)value & UINT16_C(0xFF80)) != 0u) return OPENSPM_ERROR_UNSUPPORTED;
            platform->dsp_ar_info = (uint16_t)value;
            return OPENSPM_OK;
        case WII_DSP_AR_REFRESH:
            if (require_width(width, 2u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            if (((uint16_t)value & UINT16_C(0xF800)) != 0u) return OPENSPM_ERROR_UNSUPPORTED;
            platform->dsp_ar_refresh = (uint16_t)value;
            return OPENSPM_OK;
        case WII_DSP_ARAM_MMADDR:
            if (require_width(width, 4u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            platform->dsp_aram_mmaddr = (uint32_t)value & WII_DSP_ARAM_ADDRESS_MASK;
            return OPENSPM_OK;
        case WII_DSP_ARAM_ARADDR:
            if (require_width(width, 4u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            platform->dsp_aram_araddr = (uint32_t)value & WII_DSP_ARAM_ADDRESS_MASK;
            return OPENSPM_OK;
        case WII_DSP_ARAM_COUNT:
            if (require_width(width, 4u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            platform->dsp_aram_count = (uint32_t)value &
                (WII_DSP_ARAM_DIRECTION | WII_DSP_ARAM_COUNT_MASK);
            return complete_aram_dma(platform, platform->dsp_aram_count);
        case WII_DSP_AUDIO_DMA_HI:
            if (require_width(width, 2u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            platform->dsp_audio_dma_source =
                (platform->dsp_audio_dma_source & UINT32_C(0x0000FFFF)) |
                ((uint32_t)((uint16_t)value & WII_DSP_AUDIO_HI_MASK) << 16);
            return OPENSPM_OK;
        case WII_DSP_AUDIO_DMA_LO:
            if (require_width(width, 2u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            platform->dsp_audio_dma_source =
                (platform->dsp_audio_dma_source & UINT32_C(0xFFFF0000)) |
                ((uint32_t)(uint16_t)value & WII_DSP_AUDIO_LO_MASK);
            return OPENSPM_OK;
        case WII_DSP_AUDIO_DMA_CTL:
            if (require_width(width, 2u) != OPENSPM_OK) return OPENSPM_ERROR_UNSUPPORTED;
            /* NumBlocks[14:0] + Enable[15]. This layer only owns the guest-visible
               programming latch. Host audio consumption/interrupt timing remains
               intentionally separate until OpenSPM's native audio service exists. */
            platform->dsp_audio_dma_control = (uint16_t)value;
            return OPENSPM_OK;
        default:
            break;
    }

    if (platform->base == NULL) return OPENSPM_ERROR_NOT_FOUND;
    return openspm_wii_platform_bus_write(platform->base, address, width, value);
}
