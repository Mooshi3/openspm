/* OpenSPM guest-memory service for mods ("openspm.guest_memory").
 *
 * A mod acquires it through host->query_service (usually at STARTUP) and may
 * then read or write 8/16/32-bit big-endian values in the Wii's MEM1/MEM2, but
 * only from inside its OPENSPM_MOD_EVENT_TICK_BEGIN callback. That callback
 * runs at the native backend's PRESENT_SAFEPOINT: Broadway is paused between
 * two guest frames, input for the next frame is latched and the F1 cheats have
 * just been applied, so a write lands before the next frame runs and never
 * races the game. Outside the tick (another event, another thread, after the
 * callback returned) every call fails with OPENSPM_ERROR_UNSUPPORTED and
 * touches nothing.
 *
 * Addresses are validated like the cheats (include/openspm/cheats.h): the
 * cached MEM1 (0x80000000..0x817FFFFF) and MEM2 (0x90000000..0x93FFFFFF)
 * views, naturally aligned to the access size, with the backing store
 * attached. Anything else fails with OPENSPM_ERROR_INVALID_ARGUMENT.
 *
 * Portable C (no Windows headers): the validation and the access window are
 * host unit-tested by tests/m14_mod_tick_test.c. */
#ifndef OPENSPM_GUEST_MEMORY_H
#define OPENSPM_GUEST_MEMORY_H

#include <stddef.h>
#include <stdint.h>

#include "openspm/abi.h"
#include "openspm/cheats.h"

#ifdef __cplusplus
extern "C" {
#endif

#define OPENSPM_GUEST_MEMORY_SERVICE_ID "openspm.guest_memory"
#define OPENSPM_GUEST_MEMORY_SERVICE_VERSION 1u

enum {
    OPENSPM_GUEST_MEMORY_CAP_READ = UINT64_C(1) << 0,
    OPENSPM_GUEST_MEMORY_CAP_WRITE = UINT64_C(1) << 1
};

typedef int(OPENSPM_CALL *openspm_guest_read8_fn)(void *context, uint32_t address, uint8_t *out_value);
typedef int(OPENSPM_CALL *openspm_guest_read16_fn)(void *context, uint32_t address, uint16_t *out_value);
typedef int(OPENSPM_CALL *openspm_guest_read32_fn)(void *context, uint32_t address, uint32_t *out_value);
typedef int(OPENSPM_CALL *openspm_guest_write8_fn)(void *context, uint32_t address, uint8_t value);
typedef int(OPENSPM_CALL *openspm_guest_write16_fn)(void *context, uint32_t address, uint16_t value);
typedef int(OPENSPM_CALL *openspm_guest_write32_fn)(void *context, uint32_t address, uint32_t value);

/* Version 1. Callers set header.struct_size (>= sizeof) before querying; later
   versions only append fields. Every call returns OPENSPM_OK or an error. */
typedef struct openspm_guest_memory_service {
    openspm_abi_header header;
    uint64_t capabilities;
    void *context;
    openspm_guest_read8_fn read8;
    openspm_guest_read16_fn read16;
    openspm_guest_read32_fn read32;
    openspm_guest_write8_fn write8;
    openspm_guest_write16_fn write16;
    openspm_guest_write32_fn write32;
} openspm_guest_memory_service;

/* 1 when [address, address + size) is a naturally aligned 1/2/4-byte access in
   cached MEM1 or MEM2. For size 4 this is exactly openspm_cheat_address_ok. */
static inline int openspm_guest_address_ok(uint32_t address, uint32_t size) {
    if (size != 1u && size != 2u && size != 4u) return 0;
    if ((address & (size - 1u)) != 0u) return 0;
    if (address >= OPENSPM_CHEAT_MEM1_BEGIN && address <= OPENSPM_CHEAT_MEM1_END - size) return 1;
    if (address >= OPENSPM_CHEAT_MEM2_BEGIN && address <= OPENSPM_CHEAT_MEM2_END - size) return 1;
    return 0;
}

/* Checks the caller's service request. Returns OPENSPM_OK when the caller asked
   for version 1 (or a later version whose layout starts with v1) with room for
   the v1 struct; the provider then fills it. */
static inline int openspm_guest_memory_accept(const openspm_guest_memory_service *out, uint32_t requested_version) {
    if (out == NULL) return OPENSPM_ERROR_INVALID_ARGUMENT;
    if (requested_version != OPENSPM_GUEST_MEMORY_SERVICE_VERSION ||
        out->header.struct_size < sizeof(*out))
        return OPENSPM_ERROR_ABI_MISMATCH;
    return OPENSPM_OK;
}

/* Provider side: the access window the backend opens around the mod tick. The
   resolver maps a validated guest range to host bytes (big-endian guest order)
   or returns NULL when the backing store is not attached. */
typedef uint8_t *(*openspm_guest_resolve_fn)(void *context, uint32_t address, uint32_t size);

typedef struct openspm_guest_window {
    int active; /* nonzero only while the TICK callback runs */
    openspm_guest_resolve_fn resolve;
    void *resolve_context;
} openspm_guest_window;

static inline int openspm_guest_window_map(const openspm_guest_window *w, uint32_t address, uint32_t size,
                                           uint8_t **out) {
    uint8_t *p;
    if (w == NULL || !w->active || w->resolve == NULL) return OPENSPM_ERROR_UNSUPPORTED;
    if (!openspm_guest_address_ok(address, size)) return OPENSPM_ERROR_INVALID_ARGUMENT;
    p = w->resolve(w->resolve_context, address, size);
    if (p == NULL) return OPENSPM_ERROR_RUNTIME;
    *out = p;
    return OPENSPM_OK;
}

static inline int openspm_guest_window_read(const openspm_guest_window *w, uint32_t address, uint32_t size,
                                            uint32_t *out_value) {
    uint8_t *p = NULL;
    uint32_t value = 0u, i;
    int rc;
    if (out_value == NULL) return OPENSPM_ERROR_INVALID_ARGUMENT;
    rc = openspm_guest_window_map(w, address, size, &p);
    if (rc != OPENSPM_OK) return rc;
    for (i = 0u; i < size; ++i) value = (value << 8) | p[i];
    *out_value = value;
    return OPENSPM_OK;
}

static inline int openspm_guest_window_write(const openspm_guest_window *w, uint32_t address, uint32_t size,
                                             uint32_t value) {
    uint8_t *p = NULL;
    uint32_t i;
    int rc = openspm_guest_window_map(w, address, size, &p);
    if (rc != OPENSPM_OK) return rc;
    for (i = 0u; i < size; ++i) p[i] = (uint8_t)(value >> (8u * (size - 1u - i)));
    return OPENSPM_OK;
}

#ifdef __cplusplus
}
#endif

#endif
