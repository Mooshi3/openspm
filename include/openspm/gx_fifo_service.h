#ifndef OPENSPM_GX_FIFO_SERVICE_H
#define OPENSPM_GX_FIFO_SERVICE_H

#include "openspm/abi.h"

#ifdef __cplusplus
extern "C" {
#endif

/*
 * Native hot-path transport for the already-serialized GX FIFO byte stream.
 *
 * This is an OpenSPM service, not a Wii hardware API and not an Aurora API.
 * Player-local recompiled code may gather the tiny Broadway write-gather stores
 * before crossing the module/backend ABI, while the backend remains responsible
 * for GX-visible token/DrawDone state, display-list capture, and renderer
 * submission. The byte stream is still original-console GX order verbatim.
 */
#define OPENSPM_GX_FIFO_SERVICE_ID "openspm.gx-fifo"
#define OPENSPM_GX_FIFO_SERVICE_VERSION 1u

enum {
    OPENSPM_GX_FIFO_CAP_SUBMIT = UINT64_C(1) << 0
};

typedef int (OPENSPM_CALL *openspm_gx_fifo_submit_fn)(
    void *context,
    const uint8_t *bytes,
    uint32_t size);

typedef struct openspm_gx_fifo_service {
    openspm_abi_header header;
    uint64_t capabilities;
    void *context;
    openspm_gx_fifo_submit_fn submit;
} openspm_gx_fifo_service;

#ifdef __cplusplus
}
#endif

#endif
