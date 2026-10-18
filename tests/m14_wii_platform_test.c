#include "openspm/wii_platform.h"

#include <stdio.h>
#include <string.h>

static int require(int condition, const char *message) {
    if (!condition) {
        fprintf(stderr, "m14_wii_platform_test: %s\n", message);
        return 0;
    }
    return 1;
}

static int read_eq(openspm_wii_platform *platform,
                   uint32_t address,
                   uint32_t width,
                   uint64_t expected,
                   const char *message) {
    uint64_t value = UINT64_C(0xDEADBEEFDEADBEEF);
    int result = openspm_wii_platform_bus_read(platform, address, width, &value);
    return require(result == OPENSPM_OK && value == expected, message);
}

int main(void) {
    openspm_wii_platform platform;
    uint8_t mem1[512];
    uint64_t value = 0;
    uint32_t i;
    int result;

    openspm_wii_platform_reset(&platform);
    memset(mem1, 0, sizeof(mem1));
    openspm_wii_platform_set_memory(&platform, mem1, sizeof(mem1), NULL, 0u);

    result = openspm_wii_platform_bus_write(&platform, UINT32_C(0xCC003004), 4u,
                                            UINT64_C(0x000000F0));
    if (!require(result == OPENSPM_OK, "PI interrupt mask write failed")) return 1;
    result = openspm_wii_platform_bus_write(&platform, UINT32_C(0xCD000034), 4u,
                                            UINT64_C(0x40000000));
    if (!require(result == OPENSPM_OK, "Hollywood PPC IRQ mask write failed")) return 1;
    if (!read_eq(&platform, UINT32_C(0xCC003004), 4u, UINT64_C(0xF0),
                 "PI interrupt mask did not round-trip")) return 1;
    if (!read_eq(&platform, UINT32_C(0xCD000034), 4u, UINT64_C(0x40000000),
                 "Hollywood PPC IRQ mask did not round-trip")) return 1;

    if (!require(openspm_wii_platform_bus_write(&platform, UINT32_C(0xCC00401C), 2u,
                                                UINT64_C(0x001F)) == OPENSPM_OK,
                 "MI interrupt mask write failed")) return 1;
    if (!require(openspm_wii_platform_bus_write(&platform, UINT32_C(0xCC00500A), 2u,
                                                UINT64_C(0x0150)) == OPENSPM_OK,
                 "DSP control write failed")) return 1;
    if (!require(openspm_wii_platform_bus_write(&platform, UINT32_C(0xCD006C00), 4u,
                                                UINT64_C(0x00000004)) == OPENSPM_OK,
                 "AI control write failed")) return 1;
    if (!require(openspm_wii_platform_bus_write(&platform, UINT32_C(0xCD006800), 4u,
                                                UINT64_C(0x00000405)) == OPENSPM_OK,
                 "EXI0 IRQ CSR write failed")) return 1;
    if (!require(openspm_wii_platform_bus_write(&platform, UINT32_C(0xCD006814), 4u,
                                                UINT64_C(0x00000405)) == OPENSPM_OK,
                 "EXI1 IRQ CSR write failed")) return 1;
    if (!require(openspm_wii_platform_bus_write(&platform, UINT32_C(0xCD006828), 4u,
                                                UINT64_C(0x00000005)) == OPENSPM_OK,
                 "EXI2 IRQ CSR write failed")) return 1;

    platform.pi_interrupt_cause = UINT32_C(0x00003004);
    if (!require(openspm_wii_platform_bus_write(&platform, UINT32_C(0xCC003000), 4u,
                                                UINT64_C(0x00001000)) == OPENSPM_OK,
                 "PI cause acknowledge failed")) return 1;
    if (!read_eq(&platform, UINT32_C(0xCC003000), 4u, UINT64_C(0x00002004),
                 "PI cause write-one-to-clear semantics wrong")) return 1;

    platform.hollywood_ppc_irq_flags = UINT32_C(0x40000400);
    if (!require(openspm_wii_platform_bus_write(&platform, UINT32_C(0xCD000030), 4u,
                                                UINT64_C(0x40000000)) == OPENSPM_OK,
                 "Hollywood IRQ flag acknowledge failed")) return 1;
    if (!read_eq(&platform, UINT32_C(0xCD000030), 4u, UINT64_C(0x00000400),
                 "Hollywood IRQ flag write-one-to-clear semantics wrong")) return 1;

    platform.dsp_control |= UINT16_C(0x0028);
    if (!require(openspm_wii_platform_bus_write(&platform, UINT32_C(0xCC00500A), 2u,
                                                UINT64_C(0x0018)) == OPENSPM_OK,
                 "DSP mask/status update failed")) return 1;
    if (!read_eq(&platform, UINT32_C(0xCC00500A), 2u, UINT64_C(0x0030),
                 "DSP write-one-to-clear/mask semantics wrong")) return 1;
    result = openspm_wii_platform_bus_write(&platform, UINT32_C(0xCC00500A), 2u,
                                            UINT64_C(0x0800));
    if (!require(result == OPENSPM_ERROR_UNSUPPORTED,
                 "DSP bootstrap/control bit was silently accepted")) return 1;

    /* EXIInit first polls all three transfer-control registers for TSTART=0. */
    if (!read_eq(&platform, UINT32_C(0xCD00680C), 4u, 0u,
                 "EXI0 did not reset idle")) return 1;
    if (!read_eq(&platform, UINT32_C(0xCD006820), 4u, 0u,
                 "EXI1 did not reset idle")) return 1;
    if (!read_eq(&platform, UINT32_C(0xCD006834), 4u, 0u,
                 "EXI2 did not reset idle")) return 1;

    /* Then EXIInit toggles EXI0 ROMDIS and later EXISelect chooses SP1/dev2. */
    if (!require(openspm_wii_platform_bus_write(&platform, UINT32_C(0xCD006800), 4u,
                                                UINT64_C(0x00002000)) == OPENSPM_OK,
                 "EXI0 ROMDIS startup write failed")) return 1;
    if (!require(openspm_wii_platform_bus_write(&platform, UINT32_C(0xCD006800), 4u,
                                                UINT64_C(0x00000200)) == OPENSPM_OK,
                 "EXI0 SP1 select failed")) return 1;

    /* EXIGetID(0,2) writes command 0x0000 as 2 bytes, then reads 4 bytes. */
    if (!require(openspm_wii_platform_bus_write(&platform, UINT32_C(0xCD006810), 4u, 0u)
                 == OPENSPM_OK, "EXI ID command data write failed")) return 1;
    if (!require(openspm_wii_platform_bus_write(&platform, UINT32_C(0xCD00680C), 4u,
                                                UINT64_C(0x00000015)) == OPENSPM_OK,
                 "EXI ID command transfer failed")) return 1;
    if (!read_eq(&platform, UINT32_C(0xCD00680C), 4u, UINT64_C(0x14),
                 "EXI write TSTART did not complete")) return 1;
    if (!require(openspm_wii_platform_bus_write(&platform, UINT32_C(0xCD00680C), 4u,
                                                UINT64_C(0x00000031)) == OPENSPM_OK,
                 "EXI ID read transfer failed")) return 1;
    if (!read_eq(&platform, UINT32_C(0xCD00680C), 4u, UINT64_C(0x30),
                 "EXI read TSTART did not complete")) return 1;
    if (!read_eq(&platform, UINT32_C(0xCD006810), 4u, 0u,
                 "absent Wii SP1 probe did not return zero ID")) return 1;

    /* R8PE01's SI transfer helper at 0x8026AE5C clears the selected channel's
       status errors, copies the request into SI communication RAM, then writes
       the real COMCSR bit layout. For its first probe the observed value is
       0xC0010301: TCINT W1C + TCINT mask + 1-byte out + 3-byte in + TSTART. */
    if (!read_eq(&platform, UINT32_C(0xCC00206C), 2u, 0u,
                 "VI clock select did not reset to 27 MHz")) return 1;
    if (!require(openspm_wii_platform_bus_write(&platform, UINT32_C(0xCD006430), 4u,
                                                UINT64_C(0x00010300)) == OPENSPM_OK,
                 "SI poll startup write failed")) return 1;
    if (!read_eq(&platform, UINT32_C(0xCD006430), 4u, UINT64_C(0x00010300),
                 "SI poll register did not round-trip")) return 1;
    if (!read_eq(&platform, UINT32_C(0xCD006438), 4u, 0u,
                 "SI status did not reset clear")) return 1;
    if (!require(openspm_wii_platform_bus_write(&platform, UINT32_C(0xCD006438), 4u, 0u)
                 == OPENSPM_OK, "SI zero status acknowledge failed")) return 1;
    if (!require(openspm_wii_platform_bus_write(&platform, UINT32_C(0xCD006480), 4u, 0u)
                 == OPENSPM_OK, "SI communication-buffer write failed")) return 1;
    if (!read_eq(&platform, UINT32_C(0xCD006480), 4u, 0u,
                 "SI communication-buffer word did not round-trip")) return 1;
    if (!read_eq(&platform, UINT32_C(0xCD006434), 4u, 0u,
                 "SI COMCSR did not reset idle")) return 1;
    if (!require(openspm_wii_platform_bus_write(&platform, UINT32_C(0xCD006434), 4u,
                                                UINT64_C(0xC0010301)) == OPENSPM_OK,
                 "observed SI probe transfer failed")) return 1;
    if (!read_eq(&platform, UINT32_C(0xCD006434), 4u, UINT64_C(0xE0010300),
                 "SI probe did not complete with absent-device status")) return 1;
    if (!read_eq(&platform, UINT32_C(0xCD006438), 4u, UINT64_C(0x08000000),
                 "SI no-response status was not reported for channel 0")) return 1;
    if (!require(openspm_wii_platform_bus_write(&platform, UINT32_C(0xCD006438), 4u,
                                                UINT64_C(0x08000000)) == OPENSPM_OK,
                 "SI no-response acknowledge failed")) return 1;
    if (!read_eq(&platform, UINT32_C(0xCD006438), 4u, 0u,
                 "SI no-response status did not clear W1C")) return 1;

    /* The same SDK init sequence stages default per-port commands. Keep those
       ordinary channel registers as bounded state even though M14 exposes no
       legacy GameCube controller; SPM gameplay input comes through WPAD. */
    for (i = 0; i < 4u; ++i) {
        uint32_t address = UINT32_C(0xCD006400) + i * UINT32_C(0x0C);
        if (!require(openspm_wii_platform_bus_write(&platform, address, 4u,
                                                    UINT64_C(0x00400300)) == OPENSPM_OK,
                     "SI channel output write failed")) return 1;
        if (!read_eq(&platform, address, 4u, UINT64_C(0x00400300),
                     "SI channel output did not round-trip")) return 1;
    }
    if (!require(openspm_wii_platform_bus_write(&platform, UINT32_C(0xCD006438), 4u,
                                                UINT64_C(0x80000000)) == OPENSPM_OK,
                 "SI command-copy pulse failed")) return 1;
    if (!read_eq(&platform, UINT32_C(0xCD006438), 4u, 0u,
                 "SI command-copy pulse did not complete synchronously")) return 1;

    result = openspm_wii_platform_bus_write(&platform, UINT32_C(0xCD006434), 4u,
                                            UINT64_C(0x00000008));
    if (!require(result == OPENSPM_ERROR_UNSUPPORTED,
                 "reserved SI COMCSR bit was silently accepted")) return 1;

    /* Unknown immediate commands must fail on the initiating write and must
       not mutate transfer/stream provenance. dev16's diagnostic wrapper reads
       this exact state after the core rejects the transaction. */
    if (!require(openspm_wii_platform_bus_write(&platform, UINT32_C(0xCD006810), 4u,
                                                UINT64_C(0x12340000)) == OPENSPM_OK,
                 "EXI unknown command staging write failed")) return 1;
    result = openspm_wii_platform_bus_write(&platform, UINT32_C(0xCD00680C), 4u,
                                            UINT64_C(0x00000015));
    if (!require(result == OPENSPM_ERROR_NOT_FOUND,
                 "unknown EXI immediate command was silently accepted")) return 1;
    if (!read_eq(&platform, UINT32_C(0xCD00680C), 4u, UINT64_C(0x30),
                 "rejected EXI command mutated transfer-control state")) return 1;
    if (!read_eq(&platform, UINT32_C(0xCD006810), 4u, UINT64_C(0x12340000),
                 "rejected EXI command lost staged DATA provenance")) return 1;
    if (!require(platform.exi_last_command[0] == 0u &&
                 platform.exi_last_command_size[0] == 2u &&
                 platform.exi_stream_mode[0] == 0u,
                 "rejected EXI command mutated prior command/stream provenance")) return 1;

    /* Immediately after SIInit, OS SRAM setup selects the internal MX chip,
       sends read command 0x20000100, then DMA-reads 64 bytes from SRAM+4. */
    for (i = 0; i < 64u; ++i) platform.exi_sram[4u + i] = (uint8_t)(i + 1u);
    if (!require(openspm_wii_platform_bus_write(&platform, UINT32_C(0xCD006800), 4u, 0u)
                 == OPENSPM_OK, "EXI0 deselect before SRAM failed")) return 1;
    if (!require(openspm_wii_platform_bus_write(&platform, UINT32_C(0xCD006800), 4u,
                                                UINT64_C(0x00000130)) == OPENSPM_OK,
                 "EXI0 internal device select failed")) return 1;
    if (!require(openspm_wii_platform_bus_write(&platform, UINT32_C(0xCD006810), 4u,
                                                UINT64_C(0x20000100)) == OPENSPM_OK,
                 "SRAM read command staging failed")) return 1;
    if (!require(openspm_wii_platform_bus_write(&platform, UINT32_C(0xCD00680C), 4u,
                                                UINT64_C(0x00000035)) == OPENSPM_OK,
                 "SRAM read command transfer failed")) return 1;
    if (!require(openspm_wii_platform_bus_write(&platform, UINT32_C(0xCD006804), 4u,
                                                UINT64_C(0x80000100)) == OPENSPM_OK,
                 "EXI DMA MAR write failed")) return 1;
    if (!read_eq(&platform, UINT32_C(0xCD006804), 4u, UINT64_C(0x00000100),
                 "EXI DMA MAR did not expose physical address field")) return 1;
    if (!require(openspm_wii_platform_bus_write(&platform, UINT32_C(0xCD006808), 4u,
                                                UINT64_C(64)) == OPENSPM_OK,
                 "EXI DMA length write failed")) return 1;
    if (!require(openspm_wii_platform_bus_write(&platform, UINT32_C(0xCD00680C), 4u,
                                                UINT64_C(0x00000003)) == OPENSPM_OK,
                 "SRAM DMA read failed")) return 1;
    if (!read_eq(&platform, UINT32_C(0xCD00680C), 4u, UINT64_C(0x02),
                 "EXI DMA TSTART did not complete")) return 1;
    if (!read_eq(&platform, UINT32_C(0xCD006808), 4u, 0u,
                 "EXI DMA length did not drain to zero")) return 1;
    if (!require(memcmp(mem1 + 0x100, platform.exi_sram + 4, 64u) == 0,
                 "SRAM DMA did not copy into MEM1")) return 1;

    /* OS SRAM repair/writeback uses 0xA0000100 + (offset << 6), followed by
       sequential immediate writes. Exercise the first four bytes. */
    if (!require(openspm_wii_platform_bus_write(&platform, UINT32_C(0xCD006800), 4u, 0u)
                 == OPENSPM_OK, "EXI0 deselect before SRAM write failed")) return 1;
    if (!require(openspm_wii_platform_bus_write(&platform, UINT32_C(0xCD006800), 4u,
                                                UINT64_C(0x00000130)) == OPENSPM_OK,
                 "EXI0 internal device reselect failed")) return 1;
    if (!require(openspm_wii_platform_bus_write(&platform, UINT32_C(0xCD006810), 4u,
                                                UINT64_C(0xA0000100)) == OPENSPM_OK,
                 "SRAM write command staging failed")) return 1;
    if (!require(openspm_wii_platform_bus_write(&platform, UINT32_C(0xCD00680C), 4u,
                                                UINT64_C(0x00000035)) == OPENSPM_OK,
                 "SRAM write command transfer failed")) return 1;
    if (!require(openspm_wii_platform_bus_write(&platform, UINT32_C(0xCD006810), 4u,
                                                UINT64_C(0x11223344)) == OPENSPM_OK,
                 "SRAM payload staging failed")) return 1;
    if (!require(openspm_wii_platform_bus_write(&platform, UINT32_C(0xCD00680C), 4u,
                                                UINT64_C(0x00000035)) == OPENSPM_OK,
                 "SRAM immediate writeback failed")) return 1;
    if (!require(platform.exi_sram[4] == UINT8_C(0x11) &&
                 platform.exi_sram[5] == UINT8_C(0x22) &&
                 platform.exi_sram[6] == UINT8_C(0x33) &&
                 platform.exi_sram[7] == UINT8_C(0x44),
                 "SRAM immediate payload byte order was wrong")) return 1;

    /* DMA writes remain closed until a real startup path requires them. */
    if (!require(openspm_wii_platform_bus_write(&platform, UINT32_C(0xCD006808), 4u,
                                                UINT64_C(32)) == OPENSPM_OK,
                 "EXI DMA write length staging failed")) return 1;
    result = openspm_wii_platform_bus_write(&platform, UINT32_C(0xCD00680C), 4u,
                                            UINT64_C(0x00000007));
    if (!require(result == OPENSPM_ERROR_UNSUPPORTED,
                 "unobserved EXI DMA write was silently accepted")) return 1;
    result = openspm_wii_platform_bus_read(&platform, UINT32_C(0xCC003004), 2u, &value);
    if (!require(result == OPENSPM_ERROR_UNSUPPORTED,
                 "wrong-width PI access was accepted")) return 1;

    puts("PASS m14_wii_platform_test");
    return 0;
}
