#ifndef OPENSPM_WII_BOOT_PLATFORM_H
#define OPENSPM_WII_BOOT_PLATFORM_H

#include "openspm/wii_platform.h"

#ifdef __cplusplus
extern "C" {
#endif

/*
 * Incremental startup layer above openspm_wii_platform.
 *
 * M14 keeps the already-tested interrupt/EXI/SI model stable while bringing
 * up the next observed OSInit boundaries. This layer owns the bounded
 * post-SI boot state observed in retail Wii software: audio bootstrap,
 * Broadway-side IPC startup, plus memory-protection/reset-status setup.
 * Unknown accesses delegate to the base platform and ultimately still fail
 * closed.
 */
typedef struct openspm_wii_boot_platform {
    openspm_wii_platform *base;

    uint32_t hollywood_compat;
    uint32_t hollywood_pll_ai;
    uint32_t hollywood_pll_ai_ext;
    uint32_t pi_reset_code;
    uint16_t mi_protection_control;
    uint16_t mi_interrupt_clear;
    uint16_t mi_config;

    /* Broadway-visible Starlet IPC bootstrap state. M14 models only the
       PPC-side message/control latch required by retail startup; issuing an
       actual X1 IOS request still fails closed until it is lifted to a named
       OpenSPM service. */
    uint32_t ipc_ppc_msg;
    uint32_t ipc_arm_msg;
    uint32_t ipc_ppc_ctrl;

    uint16_t dsp_control;
    uint16_t dsp_mail_to_hi;
    uint16_t dsp_mail_to_lo;
    uint16_t dsp_mail_from_hi;
    uint16_t dsp_mail_from_lo;
    uint16_t dsp_ar_info;
    uint16_t dsp_ar_mode;
    uint16_t dsp_ar_refresh;
    uint32_t dsp_aram_mmaddr;
    uint32_t dsp_aram_araddr;
    uint32_t dsp_aram_count;

    /* DSP interface audio-DMA programming state. This is the Broadway-visible
       producer side of AX output DMA, not host audio playback. M14 tracks the
       real Wii register masks/latches while native mixer/output timing remains
       a separate later service. */
    uint32_t dsp_audio_dma_source;
    uint16_t dsp_audio_dma_control;

    uint8_t *mem1;
    uint64_t mem1_size;
    uint8_t *mem2;
    uint64_t mem2_size;
} openspm_wii_boot_platform;

void openspm_wii_boot_platform_reset(openspm_wii_boot_platform *platform,
                                     openspm_wii_platform *base);

void openspm_wii_boot_platform_set_memory(openspm_wii_boot_platform *platform,
                                          uint8_t *mem1,
                                          uint64_t mem1_size,
                                          uint8_t *mem2,
                                          uint64_t mem2_size);

int openspm_wii_boot_platform_bus_read(openspm_wii_boot_platform *platform,
                                       uint32_t address,
                                       uint32_t width,
                                       uint64_t *out_value);

int openspm_wii_boot_platform_bus_write(openspm_wii_boot_platform *platform,
                                        uint32_t address,
                                        uint32_t width,
                                        uint64_t value);

#ifdef __cplusplus
}
#endif

#endif