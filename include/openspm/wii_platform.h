#ifndef OPENSPM_WII_PLATFORM_H
#define OPENSPM_WII_PLATFORM_H

#include "openspm/abi.h"

#ifdef __cplusplus
extern "C" {
#endif

/*
 * Small, OpenSPM-owned Wii platform model used by the standalone native
 * backend. M14 intentionally grows this one observed hardware boundary at a
 * time. Unimplemented MMIO addresses return OPENSPM_ERROR_NOT_FOUND instead
 * of silently inventing device behavior.
 *
 * The state below covers interrupt routing, the VI/SI startup registers and
 * bounded SI communication RAM used by the SDK's legacy-controller probe,
 * plus the EXI immediate/DMA traffic observed during early OS initialization.
 * It is not a claim of complete PI/MI/VI/SI/DSP/AI/EXI emulation.
 */
typedef struct openspm_wii_platform {
    uint32_t pi_interrupt_cause;
    uint32_t pi_interrupt_mask;
    uint32_t hollywood_ppc_irq_flags;
    uint32_t hollywood_ppc_irq_mask;
    uint16_t mi_interrupt_mask;
    uint16_t mi_interrupt_cause;
    uint16_t vi_clock_select;
    uint32_t si_poll;
    uint32_t si_comcsr;
    uint32_t si_status;
    uint32_t si_channel_out[4];
    uint32_t si_channel_in_hi[4];
    uint32_t si_channel_in_lo[4];
    uint32_t si_exi_clock_count;
    uint32_t si_buffer[32];
    uint16_t dsp_control;
    uint32_t ai_control;
    uint32_t exi_csr[3];
    uint32_t exi_mar[3];
    uint32_t exi_length[3];
    uint32_t exi_cr[3];
    uint32_t exi_data[3];
    uint32_t exi_last_command[3];
    uint32_t exi_stream_offset[3];
    uint8_t exi_last_command_size[3];
    uint8_t exi_stream_mode[3];
    uint8_t exi_sram[68];
    uint8_t *mem1;
    uint64_t mem1_size;
    uint8_t *mem2;
    uint64_t mem2_size;
} openspm_wii_platform;

void openspm_wii_platform_reset(openspm_wii_platform *platform);

/* Attach the backend-owned guest RAM used by hardware DMA. The platform never
   owns or frees these buffers. */
void openspm_wii_platform_set_memory(openspm_wii_platform *platform,
                                     uint8_t *mem1,
                                     uint64_t mem1_size,
                                     uint8_t *mem2,
                                     uint64_t mem2_size);

int openspm_wii_platform_bus_read(openspm_wii_platform *platform,
                                  uint32_t address,
                                  uint32_t width,
                                  uint64_t *out_value);

int openspm_wii_platform_bus_write(openspm_wii_platform *platform,
                                   uint32_t address,
                                   uint32_t width,
                                   uint64_t value);

#ifdef __cplusplus
}
#endif

#endif
