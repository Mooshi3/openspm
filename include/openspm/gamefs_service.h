#ifndef OPENSPM_GAMEFS_SERVICE_H
#define OPENSPM_GAMEFS_SERVICE_H

#include "openspm/abi.h"

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/*
 * Read-only view of the player-supplied extracted disc filesystem.
 *
 * This is an OpenSPM platform service, not a Wii/DVD ABI. Native compatibility
 * adapters translate guest SDK calls onto this small interface. Paths are
 * relative to the extracted disc's files/ directory and must not escape it.
 */
#define OPENSPM_GAMEFS_SERVICE_ID "openspm.gamefs"
#define OPENSPM_GAMEFS_SERVICE_VERSION 1u

enum {
    OPENSPM_GAMEFS_CAP_OPEN_READ = UINT64_C(1) << 0,
    OPENSPM_GAMEFS_CAP_RANDOM_ACCESS = UINT64_C(1) << 1,
    OPENSPM_GAMEFS_CAP_SIZE = UINT64_C(1) << 2
};

typedef uint64_t openspm_gamefs_handle;
#define OPENSPM_GAMEFS_INVALID_HANDLE UINT64_C(0)

typedef int (OPENSPM_CALL *openspm_gamefs_open_fn)(
    void *context,
    const char *relative_path_utf8,
    openspm_gamefs_handle *out_handle,
    uint64_t *out_size);

typedef int (OPENSPM_CALL *openspm_gamefs_read_fn)(
    void *context,
    openspm_gamefs_handle handle,
    uint64_t offset,
    void *destination,
    uint64_t size,
    uint64_t *out_read);

typedef int (OPENSPM_CALL *openspm_gamefs_close_fn)(
    void *context,
    openspm_gamefs_handle handle);

typedef struct openspm_gamefs_service {
    openspm_abi_header header;
    uint64_t capabilities;
    void *context;
    openspm_gamefs_open_fn open;
    openspm_gamefs_read_fn read;
    openspm_gamefs_close_fn close;
} openspm_gamefs_service;

#ifdef __cplusplus
}
#endif

#endif
