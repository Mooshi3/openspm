#ifndef OPENSPM_WII_CPU_SERVICE_H
#define OPENSPM_WII_CPU_SERVICE_H

#include "openspm/abi.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Internal native-runtime service. This is an OpenSPM contract, not a Dolphin
   API. Ordinary MEM1/MEM2 traffic bypasses it; only actual Wii CPU/platform
   boundaries should arrive here. */
#define OPENSPM_WII_CPU_SERVICE_ID "openspm.wii.cpu"
#define OPENSPM_WII_CPU_SERVICE_VERSION 1u

enum {
    OPENSPM_WII_CPU_CAP_EXTERNAL_BUS = UINT64_C(1) << 0,
    OPENSPM_WII_CPU_CAP_SPR = UINT64_C(1) << 1,
    OPENSPM_WII_CPU_CAP_CACHE_CONTROL = UINT64_C(1) << 2
};

typedef enum openspm_wii_cache_operation {
    OPENSPM_WII_CACHE_DCBST = 0,
    OPENSPM_WII_CACHE_DCBF = 1,
    OPENSPM_WII_CACHE_DCBI = 2,
    OPENSPM_WII_CACHE_ICBI = 3
} openspm_wii_cache_operation;

typedef int (OPENSPM_CALL *openspm_wii_bus_read_fn)(
    void *context,
    uint32_t address,
    uint32_t width,
    uint64_t *out_value);

typedef int (OPENSPM_CALL *openspm_wii_bus_write_fn)(
    void *context,
    uint32_t address,
    uint32_t width,
    uint64_t value);

typedef int (OPENSPM_CALL *openspm_wii_spr_read_fn)(
    void *context,
    uint32_t spr,
    uint32_t source_address,
    uint32_t *out_value);

typedef int (OPENSPM_CALL *openspm_wii_spr_write_fn)(
    void *context,
    uint32_t spr,
    uint32_t source_address,
    uint32_t value);

typedef int (OPENSPM_CALL *openspm_wii_cache_control_fn)(
    void *context,
    uint32_t operation,
    uint32_t address,
    uint32_t source_address);

typedef struct openspm_wii_cpu_service {
    openspm_abi_header header;
    uint64_t capabilities;
    void *context;
    openspm_wii_bus_read_fn read_bus;
    openspm_wii_bus_write_fn write_bus;
    openspm_wii_spr_read_fn read_spr;
    openspm_wii_spr_write_fn write_spr;
    openspm_wii_cache_control_fn cache_control;
} openspm_wii_cpu_service;

#ifdef __cplusplus
}
#endif

#endif
