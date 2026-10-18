#ifndef OPENSPM_DEBUG_IPC_H
#define OPENSPM_DEBUG_IPC_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/*
 * DEVELOPMENT-ONLY IPC between the Windows launcher and the in-process R8PE01
 * adapter. This is deliberately NOT an OpenSPM mod SDK service: commands such
 * as GSW inspection are revision-specific debugging tools and must not become
 * persistent mod ABI.
 */
#define OPENSPM_DEBUG_IPC_VERSION 1u
#define OPENSPM_DEBUG_COMMAND_BYTES 256u
#define OPENSPM_DEBUG_RESPONSE_BYTES 768u

/* Mapping name is Local\\OpenSPM.Debug.<OpenSPM game process PID>. */
typedef struct openspm_debug_mailbox {
    uint32_t version;
    volatile int32_t command_generation;
    volatile int32_t response_generation;
    char command[OPENSPM_DEBUG_COMMAND_BYTES];
    char response[OPENSPM_DEBUG_RESPONSE_BYTES];
} openspm_debug_mailbox;

#ifdef __cplusplus
}
#endif

#endif
