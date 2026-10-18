#ifndef OPENSPM_M14_GX_AUDIT_H
#define OPENSPM_M14_GX_AUDIT_H

#include <stdint.h>

/* M14-only diagnostic renderer sync reason. This is deliberately outside the
 * neutral GX platform contract: the backend emits it only after SPM has entered
 * a stable SEQ_LOAD/file-select state, and the Aurora development renderer uses
 * it only to arm a metadata/hash capture. It must not drain, present, clear, or
 * otherwise change guest-visible GX behavior. */
#define OPENSPM_M14_GX_SYNC_SEQLOAD_AUDIT UINT32_C(3)

/* Zero is a diagnostic sentinel meaning: capture subsequent effective draws
 * until the next real GXDrawDone boundary. The Aurora audit still enforces its
 * own fixed draw cap so a malformed/no-boundary stream cannot create an
 * unbounded log. */
#define OPENSPM_M14_GX_AUDIT_DRAW_LIMIT   UINT32_C(0)

#endif
