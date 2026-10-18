#ifndef OPENSPM_GX_PLATFORM_H
#define OPENSPM_GX_PLATFORM_H

#include "openspm/abi.h"

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* OpenSPM-owned bridge between the statically recompiled Wii CPU side and a
   native renderer. The renderer consumes the GX command stream, resolves
   guest RAM only through the bounded view below, and owns raster results; the
   game only sees the few PE/GX observations that are architecturally visible
   to it. */
#define OPENSPM_GX_FIFO_ADDRESS      UINT32_C(0xCC008000)
#define OPENSPM_GX_PE_BASE           UINT32_C(0xCC001000)
#define OPENSPM_GX_PE_END            UINT32_C(0xCC001040)
#define OPENSPM_GX_PE_TOKEN          UINT32_C(0xCC00100E)
#define OPENSPM_GX_PE_BBOX_LEFT      UINT32_C(0xCC001010)
#define OPENSPM_GX_PE_BBOX_RIGHT     UINT32_C(0xCC001012)
#define OPENSPM_GX_PE_BBOX_TOP       UINT32_C(0xCC001014)
#define OPENSPM_GX_PE_BBOX_BOTTOM    UINT32_C(0xCC001016)

/* CPU/GP FIFO configuration is guest-visible GX state, not a second GPU. The
   native renderer still receives commands directly from the write-gather pipe;
   these registers exist only so Nintendo's GX library can configure/query its
   FIFO bookkeeping without falling into an emulated Flipper command processor. */
#define OPENSPM_GX_CP_STATUS         UINT32_C(0xCC000000)
#define OPENSPM_GX_CP_CONTROL        UINT32_C(0xCC000002)
#define OPENSPM_GX_CP_CLEAR          UINT32_C(0xCC000004)
#define OPENSPM_GX_CP_PERF_SELECT    UINT32_C(0xCC000006)
#define OPENSPM_GX_CP_FIFO_FIRST     UINT32_C(0xCC000020)
#define OPENSPM_GX_CP_FIFO_LAST      UINT32_C(0xCC00003A)
#define OPENSPM_GX_CP_FIFO_WORDS     UINT32_C(14)
#define OPENSPM_GX_PI_FIFO_BASE      UINT32_C(0xCC00300C)
#define OPENSPM_GX_PI_FIFO_END       UINT32_C(0xCC003010)
#define OPENSPM_GX_PI_FIFO_WPTR      UINT32_C(0xCC003014)

/* Renderer synchronization reasons are semantic safe points, not arbitrary
   FIFO fragment boundaries. DRAW_DONE is Nintendo's explicit GX completion
   fence. PRESENT_SAFEPOINT is emitted only after SPM has returned to the
   runtime-proven Revolution OS scheduler idle with no runnable guest work. At
   that point no guest thread can be halfway through a GX command, so Aurora may
   safely drain its FIFO parser and decide from the parsed stream whether an
   actual display-copy occurred before recycling/presenting the native frame.
   FIFO_PUBLISH is weaker: the backend guarantees only that the submitted prefix
   ends at a complete GX command. The renderer may parse that prefix immediately,
   but must not present/recycle or expose guest completion because of it. */
#define OPENSPM_GX_SYNC_DRAW_DONE         UINT32_C(1)
#define OPENSPM_GX_SYNC_PRESENT_SAFEPOINT UINT32_C(2)
#define OPENSPM_GX_SYNC_FIFO_PUBLISH      UINT32_C(3)

typedef struct openspm_gx_raster_bounds {
    uint16_t left;
    uint16_t right;
    uint16_t top;
    uint16_t bottom;
    uint32_t valid;
} openspm_gx_raster_bounds;

/* A bounded contiguous host view beginning exactly at the requested guest
   address. size is the maximum number of bytes the renderer may access from
   bytes before requesting another mapping. This lets renderer adapters pass a
   legal array/display-list span to native graphics libraries without guessing
   where MEM1/MEM2 ends. */
typedef struct openspm_gx_guest_span {
    uint8_t *bytes;
    uint32_t size;
} openspm_gx_guest_span;

/* Resolve a Wii RAM address to a mutable host span. min_size is the amount the
   caller already knows it needs; implementations reject mappings that cannot
   satisfy it and, on success, return the full legal contiguous remainder of
   that RAM bank. The native backend owns the storage and lifetime. */
typedef int (OPENSPM_CALL *openspm_gx_map_guest_memory_fn)(
    void *context,
    uint32_t guest_address,
    uint32_t min_size,
    openspm_gx_guest_span *out_span);

typedef struct openspm_gx_guest_memory {
    void *context;
    openspm_gx_map_guest_memory_fn map;
} openspm_gx_guest_memory;

/* submit_fifo may receive arbitrarily small write-gather fragments (including
   a one-byte fragment). A renderer/adapter must preserve and buffer the stream
   until complete GX commands are available; it must not assume one callback
   equals one command. guest_memory remains owned by openspm_gx_platform. */
typedef int (OPENSPM_CALL *openspm_gx_submit_fifo_fn)(
    void *context,
    const uint8_t *bytes,
    uint32_t size,
    const openspm_gx_guest_memory *guest_memory);

/* sync is called only at a semantic boundary where all FIFO bytes produced by
   the guest before that boundary have already been submitted. DRAW_DONE and
   PRESENT_SAFEPOINT are draining/observable boundaries; FIFO_PUBLISH is only a
   complete-command parsing point and must not itself expose completion. */
typedef int (OPENSPM_CALL *openspm_gx_sync_fn)(
    void *context,
    uint32_t reason);

typedef int (OPENSPM_CALL *openspm_gx_read_bounds_fn)(
    void *context,
    openspm_gx_raster_bounds *out_bounds);

/* PE bounding-box registers are writable. GXClearBoundingBox normally also
   reaches the renderer through BP 0x55/0x56, but direct PE writes are observable
   too and must not disappear into a host-only shadow register. */
typedef int (OPENSPM_CALL *openspm_gx_write_bound_fn)(
    void *context,
    uint32_t address,
    uint16_t value);

typedef struct openspm_gx_renderer_sink {
    void *context;
    openspm_gx_submit_fifo_fn submit_fifo;
    openspm_gx_sync_fn sync;
    openspm_gx_read_bounds_fn read_bounds;
    openspm_gx_write_bound_fn write_bound;
} openspm_gx_renderer_sink;

typedef struct openspm_gx_platform {
    openspm_gx_renderer_sink renderer;
    openspm_gx_guest_memory guest_memory;
    uint16_t pe_registers[32];
    uint16_t pe_token;
    uint16_t cp_status;
    uint16_t cp_control;
    uint16_t cp_perf_select;
    uint16_t cp_fifo[OPENSPM_GX_CP_FIFO_WORDS];
    uint32_t pi_fifo_base;
    uint32_t pi_fifo_end;
    uint32_t pi_fifo_wptr;
    uint64_t fifo_shift;
    uint64_t fifo_bytes;
    uint32_t pending_draw_done;

    /* GXBeginDisplayList redirects write-gather FIFO stores into a caller-owned
       guest RAM buffer until GXEndDisplayList. Keeping this in the neutral GX
       platform removes the private diagnostic's sentinel/FIFO interception and
       gives any renderer the same original-console display-list bytes later. */
    uint32_t display_list_address;
    uint32_t display_list_capacity;
    uint32_t display_list_bytes;
    uint32_t display_list_active;
    uint32_t display_list_overflow;
} openspm_gx_platform;

void openspm_gx_platform_reset(
    openspm_gx_platform *platform,
    const openspm_gx_renderer_sink *renderer);

void openspm_gx_platform_set_renderer(
    openspm_gx_platform *platform,
    const openspm_gx_renderer_sink *renderer);

void openspm_gx_platform_set_guest_memory(
    openspm_gx_platform *platform,
    const openspm_gx_guest_memory *guest_memory);

/* Begin/end original GX display-list recording. FIFO stores made while active
   are copied to guest memory in PPC/GX byte order and are not submitted to the
   renderer. End pads the used length to the SDK's 32-byte alignment with zero
   bytes and returns zero on overflow. These are semantic platform operations;
   raw R8PE01 function addresses belong only in migration glue. */
int openspm_gx_platform_begin_display_list(
    openspm_gx_platform *platform,
    uint32_t guest_address,
    uint32_t capacity);

int openspm_gx_platform_end_display_list(
    openspm_gx_platform *platform,
    uint32_t *out_size);

int openspm_gx_platform_bus_read(
    openspm_gx_platform *platform,
    uint32_t address,
    uint32_t width,
    uint64_t *out_value);

int openspm_gx_platform_bus_write(
    openspm_gx_platform *platform,
    uint32_t address,
    uint32_t width,
    uint64_t value);

/* Draw-done is intentionally consumed separately from FIFO submission. The
   M14 proof showed that waking the guest when the marker is written is too
   early: GXWaitDrawDone clears/arms its wait after the marker. The scheduler
   can consume this pending completion only after that wait is actually armed. */
uint32_t openspm_gx_platform_take_draw_done(openspm_gx_platform *platform);

#ifdef __cplusplus
}
#endif

#endif
