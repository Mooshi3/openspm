#include "openspm/wii_boot_platform.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int require(int condition, const char *message) {
    if (!condition) {
        fprintf(stderr, "m14_wii_boot_platform_test: %s\n", message);
        return 0;
    }
    return 1;
}

static int read_eq(openspm_wii_boot_platform *platform,
                   uint32_t address,
                   uint32_t width,
                   uint64_t expected,
                   const char *message) {
    uint64_t value = UINT64_C(0xDEADBEEFDEADBEEF);
    int result = openspm_wii_boot_platform_bus_read(platform, address, width, &value);
    return require(result == OPENSPM_OK && value == expected, message);
}

int main(void) {
    openspm_wii_platform base;
    openspm_wii_boot_platform boot;
    uint8_t *mem1;
    uint8_t mem2[512];
    uint32_t i;

    mem1 = (uint8_t *)calloc(UINT32_C(0x01000200), 1u);
    if (!require(mem1 != NULL, "could not allocate MEM1")) return 1;
    memset(mem2, 0, sizeof(mem2));

    openspm_wii_platform_reset(&base);
    openspm_wii_boot_platform_reset(&boot, &base);
    openspm_wii_boot_platform_set_memory(&boot,
                                         mem1, UINT32_C(0x01000200),
                                         mem2, sizeof(mem2));

    /* Unknown-to-this-layer traffic still reaches the proven base platform. */
    if (!require(openspm_wii_boot_platform_bus_write(&boot, UINT32_C(0xCC003004), 4u,
                                                     UINT64_C(0xF0)) == OPENSPM_OK,
                 "base PI delegation failed")) return 1;
    if (!read_eq(&boot, UINT32_C(0xCC003004), 4u, UINT64_C(0xF0),
                 "base PI delegation did not round-trip")) return 1;

    /* R8PE01 reaches __IPCCltInit after the early syscall/SI bootstrap and
       writes PPCCTRL=0x38: X2 plus both Broadway interrupt enables. This does
       not submit an IOS command (X1 remains clear), so the bounded native
       platform can model the PPC-side latch without inventing Starlet work. */
    if (!read_eq(&boot, UINT32_C(0xCD000004), 4u, 0u,
                 "IPC PPCCTRL did not reset clear")) return 1;
    if (!require(openspm_wii_boot_platform_bus_write(&boot, UINT32_C(0xCD000004), 4u,
                                                     UINT64_C(0x38)) == OPENSPM_OK,
                 "IPC PPCCTRL startup write failed")) return 1;
    if (!read_eq(&boot, UINT32_C(0xCD000004), 4u, UINT64_C(0x38),
                 "IPC PPCCTRL startup latch did not round-trip")) return 1;
    if (!require(openspm_wii_boot_platform_bus_write(&boot, UINT32_C(0xCD000000), 4u,
                                                     UINT64_C(0x81234560)) == OPENSPM_OK,
                 "IPC PPCMSG staging write failed")) return 1;
    if (!read_eq(&boot, UINT32_C(0xCD000008), 4u, 0u,
                 "IPC ARMMSG did not reset empty")) return 1;
    if (!require(openspm_wii_boot_platform_bus_write(&boot, UINT32_C(0xCD000004), 4u,
                                                     UINT64_C(0x39)) ==
                 OPENSPM_ERROR_UNSUPPORTED,
                 "raw IPC X1 command submission was silently accepted")) return 1;
    {
        uint64_t wrong_width_value = 0u;
        if (!require(openspm_wii_boot_platform_bus_read(&boot, UINT32_C(0xCD000004), 2u,
                                                        &wrong_width_value) ==
                     OPENSPM_ERROR_UNSUPPORTED,
                     "wrong-width IPC PPCCTRL read was accepted")) return 1;
    }

    /* __OSInitAudioSystem's Broadway-visible Hollywood setup. */
    if (!require(openspm_wii_boot_platform_bus_write(&boot, UINT32_C(0xCD800180), 4u,
                                                     UINT64_C(0x12345678)) == OPENSPM_OK,
                 "HW_COMPAT write failed")) return 1;
    if (!read_eq(&boot, UINT32_C(0xCD800180), 4u, UINT64_C(0x12345678),
                 "HW_COMPAT did not round-trip")) return 1;
    if (!require(openspm_wii_boot_platform_bus_write(&boot, UINT32_C(0xCD8001CC), 4u,
                                                     UINT64_C(0x4B00000E)) == OPENSPM_OK,
                 "HW_PLLAI write failed")) return 1;
    if (!require(openspm_wii_boot_platform_bus_write(&boot, UINT32_C(0xCD8001D0), 4u,
                                                     UINT64_C(0xC0000000)) == OPENSPM_OK,
                 "HW_PLLAIEXT write failed")) return 1;

    /* Later OSInit stages configure memory protection and sample the reset
       status. These are ordinary boot-state registers, not reasons to side-exit. */
    if (!require(openspm_wii_boot_platform_bus_write(&boot, UINT32_C(0xCC004020), 2u, 0u)
                 == OPENSPM_OK, "MI interrupt clear write failed")) return 1;
    if (!require(openspm_wii_boot_platform_bus_write(&boot, UINT32_C(0xCC004010), 2u,
                                                     UINT64_C(0x00FF)) == OPENSPM_OK,
                 "MI protection setup failed")) return 1;
    if (!require(openspm_wii_boot_platform_bus_write(&boot, UINT32_C(0xCC004028), 2u,
                                                     UINT64_C(2)) == OPENSPM_OK,
                 "MI memory config write failed")) return 1;
    if (!read_eq(&boot, UINT32_C(0xCC004010), 2u, UINT64_C(0x00FF),
                 "MI protection control did not round-trip")) return 1;
    if (!read_eq(&boot, UINT32_C(0xCC004028), 2u, UINT64_C(2),
                 "MI memory config did not round-trip")) return 1;
    if (!read_eq(&boot, UINT32_C(0xCC003024), 4u, 0u,
                 "cold-reset status did not start at zero")) return 1;
    {
        uint64_t wrong_width_value = 0u;
        if (!require(openspm_wii_boot_platform_bus_read(&boot, UINT32_C(0xCC003024), 2u,
                                                        &wrong_width_value) ==
                     OPENSPM_ERROR_UNSUPPORTED,
                     "wrong-width reset-status read was accepted")) return 1;
    }

    /* R8PE01 0x8026CF98 reads the Wii DVD-interface config register during
       startup. DI_CONFIG is a 32-bit read-only register whose cold CONFIG bit
       is set, so the game's low-byte probe observes 1. */
    if (!read_eq(&boot, UINT32_C(0xCD006024), 4u, UINT64_C(1),
                 "DI config reset value was not visible")) return 1;
    {
        uint64_t wrong_width_value = 0u;
        if (!require(openspm_wii_boot_platform_bus_read(&boot, UINT32_C(0xCD006024), 2u,
                                                        &wrong_width_value) ==
                     OPENSPM_ERROR_UNSUPPORTED,
                     "wrong-width DI config read was accepted")) return 1;
    }
    if (!require(openspm_wii_boot_platform_bus_write(&boot, UINT32_C(0xCD006024), 4u, 0u)
                 == OPENSPM_ERROR_UNSUPPORTED,
                 "read-only DI config write was accepted")) return 1;

    /* Replay the bounded DSP/ARAM sequence at SPM 0x8026DCCC. */
    for (i = 0; i < 32u; ++i)
        mem1[UINT32_C(0x01000000) + i] = (uint8_t)(0xA0u + i);

    if (!require(openspm_wii_boot_platform_bus_write(&boot, UINT32_C(0xCC005012), 2u,
                                                     UINT64_C(0x43)) == OPENSPM_OK,
                 "AR_INFO write failed")) return 1;
    if (!read_eq(&boot, UINT32_C(0xCC005016), 2u, UINT64_C(1),
                 "ARAM controller did not reset initialized")) return 1;

    if (!require(openspm_wii_boot_platform_bus_write(&boot, UINT32_C(0xCC00500A), 2u,
                                                     UINT64_C(0x08AC)) == OPENSPM_OK,
                 "DSP startup control write failed")) return 1;
    if (!read_eq(&boot, UINT32_C(0xCC00500A), 2u, UINT64_C(0x0804),
                 "DSP W1C/halt/init state wrong")) return 1;

    if (!require(openspm_wii_boot_platform_bus_write(&boot, UINT32_C(0xCC00500A), 2u,
                                                     UINT64_C(0x0805)) == OPENSPM_OK,
                 "DSP reset pulse failed")) return 1;
    if (!read_eq(&boot, UINT32_C(0xCC00500A), 2u, UINT64_C(0x0804),
                 "DSP reset bit did not self-clear")) return 1;

    if (!require(openspm_wii_boot_platform_bus_write(&boot, UINT32_C(0xCC005000), 2u, 0u)
                 == OPENSPM_OK, "DSP input mailbox clear failed")) return 1;

    /* DSPCheckMailToDSP() polls bit 15 of the high half after DSPSendMailToDSP
       writes high then low. The real DSP consumes the committed mailbox word;
       OpenSPM has no DSP microcode runtime, so the low-half write must expose
       the same completion by clearing only the full flag. */
    if (!require(openspm_wii_boot_platform_bus_write(&boot, UINT32_C(0xCC005000), 2u,
                                                     UINT64_C(0x80F4)) == OPENSPM_OK,
                 "DSP CPU->DSP mailbox high write failed")) return 1;
    if (!read_eq(&boot, UINT32_C(0xCC005000), 2u, UINT64_C(0x80F4),
                 "DSP CPU->DSP mailbox did not report full before commit")) return 1;
    if (!require(openspm_wii_boot_platform_bus_write(&boot, UINT32_C(0xCC005002), 2u,
                                                     UINT64_C(0x9F01)) == OPENSPM_OK,
                 "DSP CPU->DSP mailbox low commit failed")) return 1;
    if (!read_eq(&boot, UINT32_C(0xCC005000), 2u, UINT64_C(0x00F4),
                 "DSP CPU->DSP mailbox full flag did not clear after commit")) return 1;
    if (!read_eq(&boot, UINT32_C(0xCC005002), 2u, UINT64_C(0x9F01),
                 "DSP CPU->DSP mailbox payload low half was not preserved")) return 1;

    if (!read_eq(&boot, UINT32_C(0xCC005004), 2u, 0u,
                 "DSP output mailbox high did not start empty")) return 1;
    if (!read_eq(&boot, UINT32_C(0xCC005006), 2u, 0u,
                 "DSP output mailbox low did not start empty")) return 1;

    if (!require(openspm_wii_boot_platform_bus_write(&boot, UINT32_C(0xCC005020), 4u,
                                                     UINT64_C(0x01000000)) == OPENSPM_OK,
                 "ARAM MMADDR write failed")) return 1;
    if (!require(openspm_wii_boot_platform_bus_write(&boot, UINT32_C(0xCC005024), 4u, 0u)
                 == OPENSPM_OK, "ARAM ARADDR write failed")) return 1;
    if (!require(openspm_wii_boot_platform_bus_write(&boot, UINT32_C(0xCC005028), 4u,
                                                     UINT64_C(32)) == OPENSPM_OK,
                 "ARAM DMA failed")) return 1;
    if (!require(memcmp(mem2, mem1 + UINT32_C(0x01000000), 32u) == 0,
                 "Wii ARAM DMA did not land in MEM2")) return 1;
    if (!read_eq(&boot, UINT32_C(0xCC005028), 4u, 0u,
                 "ARAM DMA count did not drain")) return 1;
    if (!read_eq(&boot, UINT32_C(0xCC00500A), 2u, UINT64_C(0x0824),
                 "ARAM DMA did not report transfer completion")) return 1;
    if (!require(openspm_wii_boot_platform_bus_write(&boot, UINT32_C(0xCC00500A), 2u,
                                                     UINT64_C(0x0824)) == OPENSPM_OK,
                 "ARAM completion acknowledge failed")) return 1;
    if (!read_eq(&boot, UINT32_C(0xCC00500A), 2u, UINT64_C(0x0804),
                 "ARAM completion status did not clear W1C")) return 1;

    /* Clearing DSPInit makes the subsequent SDK init-code poll complete. */
    if (!require(openspm_wii_boot_platform_bus_write(&boot, UINT32_C(0xCC00500A), 2u,
                                                     UINT64_C(0x0004)) == OPENSPM_OK,
                 "DSP init flag clear failed")) return 1;
    if (!read_eq(&boot, UINT32_C(0xCC00500A), 2u, UINT64_C(0x0004),
                 "DSP init-code poll would not complete")) return 1;

    /* Releasing the SDK init microcode from HALT produces its one boot-ready
       mailbox word. Reading the low half consumes the mailbox-full flag. */
    if (!require(openspm_wii_boot_platform_bus_write(&boot, UINT32_C(0xCC00500A), 2u,
                                                     UINT64_C(0x0000)) == OPENSPM_OK,
                 "DSP init microcode release failed")) return 1;
    if (!read_eq(&boot, UINT32_C(0xCC005004), 2u, UINT64_C(0x8054),
                 "DSP init ready mail did not become visible")) return 1;
    if (!read_eq(&boot, UINT32_C(0xCC005006), 2u, UINT64_C(0x4348),
                 "DSP init ready mail payload low half wrong")) return 1;
    if (!read_eq(&boot, UINT32_C(0xCC005004), 2u, UINT64_C(0x0054),
                 "DSP init mail full flag did not clear after low read")) return 1;

    /* R8PE01 reaches AIInitDMA immediately after AX DSP-task init. The DSP
       interface exposes the source as two 16-bit registers and the block
       count/enable latch at 0x5036. On Wii the high address is 13 writable
       bits and the low address is 32-byte aligned. This is only register
       programming state; host audio consumption is intentionally separate. */
    if (!read_eq(&boot, UINT32_C(0xCC005030), 2u, 0u,
                 "audio DMA source high did not reset clear")) return 1;
    if (!read_eq(&boot, UINT32_C(0xCC005032), 2u, 0u,
                 "audio DMA source low did not reset clear")) return 1;
    if (!read_eq(&boot, UINT32_C(0xCC005036), 2u, 0u,
                 "audio DMA control did not reset clear")) return 1;
    if (!require(openspm_wii_boot_platform_bus_write(&boot, UINT32_C(0xCC005030), 2u,
                                                     UINT64_C(0x8051)) == OPENSPM_OK,
                 "audio DMA source high write failed")) return 1;
    if (!require(openspm_wii_boot_platform_bus_write(&boot, UINT32_C(0xCC005032), 2u,
                                                     UINT64_C(0x129F)) == OPENSPM_OK,
                 "audio DMA source low write failed")) return 1;
    if (!read_eq(&boot, UINT32_C(0xCC005030), 2u, UINT64_C(0x0051),
                 "audio DMA Wii high-address mask was not applied")) return 1;
    if (!read_eq(&boot, UINT32_C(0xCC005032), 2u, UINT64_C(0x1280),
                 "audio DMA low address was not 32-byte aligned")) return 1;
    if (!require(boot.dsp_audio_dma_source == UINT32_C(0x00511280),
                 "audio DMA source halves did not combine")) return 1;
    if (!require(openspm_wii_boot_platform_bus_write(&boot, UINT32_C(0xCC005036), 2u,
                                                     UINT64_C(0x000C)) == OPENSPM_OK,
                 "audio DMA control/length write failed")) return 1;
    if (!read_eq(&boot, UINT32_C(0xCC005036), 2u, UINT64_C(0x000C),
                 "audio DMA control/length did not round-trip")) return 1;

    if (!require(openspm_wii_boot_platform_bus_write(&boot, UINT32_C(0xCC00500A), 2u,
                                                     UINT64_C(0x8000)) == OPENSPM_ERROR_UNSUPPORTED,
                 "unknown DSP control bit was accepted")) return 1;

    free(mem1);
    puts("PASS m14_wii_boot_platform_test");
    return 0;
}
