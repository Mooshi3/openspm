#include "openspm/gx_platform.h"

#include <stdio.h>
#include <string.h>

typedef struct fake_guest_memory {
    uint8_t bytes[16];
} fake_guest_memory;

typedef struct fake_renderer {
    uint8_t bytes[64];
    uint32_t size;
    uint32_t saw_guest_memory;
    uint8_t mapped_byte;
    uint32_t mapped_span_size;
    openspm_gx_raster_bounds bounds;
    uint32_t bound_writes;
    uint32_t last_bound_address;
    uint16_t last_bound_value;
    uint32_t sync_count;
    uint32_t last_sync_reason;
} fake_renderer;

static int OPENSPM_CALL fake_map_guest(void *context,
                                       uint32_t guest_address,
                                       uint32_t min_size,
                                       openspm_gx_guest_span *out_span) {
    fake_guest_memory *memory = (fake_guest_memory *)context;
    uint32_t offset;
    if (memory == NULL || out_span == NULL || guest_address < UINT32_C(0x80000000))
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    out_span->bytes = NULL;
    out_span->size = 0u;
    offset = guest_address - UINT32_C(0x80000000);
    if (offset > sizeof(memory->bytes) || min_size > sizeof(memory->bytes) - offset)
        return OPENSPM_ERROR_NOT_FOUND;
    out_span->bytes = memory->bytes + offset;
    out_span->size = (uint32_t)(sizeof(memory->bytes) - offset);
    return OPENSPM_OK;
}

static int OPENSPM_CALL fake_submit(void *context,
                                    const uint8_t *bytes,
                                    uint32_t size,
                                    const openspm_gx_guest_memory *guest_memory) {
    fake_renderer *renderer = (fake_renderer *)context;
    openspm_gx_guest_span span;
    if (renderer == NULL || bytes == NULL ||
        renderer->size + size > sizeof(renderer->bytes))
        return OPENSPM_ERROR_RUNTIME;

    memcpy(renderer->bytes + renderer->size, bytes, size);
    renderer->size += size;

    memset(&span, 0, sizeof(span));
    if (guest_memory != NULL && guest_memory->map != NULL &&
        guest_memory->map(guest_memory->context, UINT32_C(0x80000004), 1u,
                          &span) == OPENSPM_OK && span.bytes != NULL) {
        renderer->saw_guest_memory = 1u;
        renderer->mapped_byte = *span.bytes;
        renderer->mapped_span_size = span.size;
    }
    return OPENSPM_OK;
}

static int OPENSPM_CALL fake_sync(void *context, uint32_t reason) {
    fake_renderer *renderer = (fake_renderer *)context;
    if (renderer == NULL) return OPENSPM_ERROR_INVALID_ARGUMENT;
    renderer->sync_count += 1u;
    renderer->last_sync_reason = reason;
    return OPENSPM_OK;
}

static int OPENSPM_CALL fake_bounds(void *context,
                                    openspm_gx_raster_bounds *out_bounds) {
    fake_renderer *renderer = (fake_renderer *)context;
    if (renderer == NULL || out_bounds == NULL)
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    *out_bounds = renderer->bounds;
    return OPENSPM_OK;
}

static int OPENSPM_CALL fake_write_bound(void *context,
                                         uint32_t address,
                                         uint16_t value) {
    fake_renderer *renderer = (fake_renderer *)context;
    if (renderer == NULL) return OPENSPM_ERROR_INVALID_ARGUMENT;
    renderer->bound_writes += 1u;
    renderer->last_bound_address = address;
    renderer->last_bound_value = value;
    if (address == OPENSPM_GX_PE_BBOX_LEFT) renderer->bounds.left = value;
    else if (address == OPENSPM_GX_PE_BBOX_RIGHT) renderer->bounds.right = value;
    else if (address == OPENSPM_GX_PE_BBOX_TOP) renderer->bounds.top = value;
    else if (address == OPENSPM_GX_PE_BBOX_BOTTOM) renderer->bounds.bottom = value;
    else return OPENSPM_ERROR_INVALID_ARGUMENT;
    renderer->bounds.valid = 1u;
    return OPENSPM_OK;
}

#define CHECK(expr) \
    do { \
        if (!(expr)) { \
            fprintf(stderr, "CHECK failed: %s at %s:%d\n", \
                    #expr, __FILE__, __LINE__); \
            return 1; \
        } \
    } while (0)

int main(void) {
    openspm_gx_platform gx;
    openspm_gx_renderer_sink sink;
    openspm_gx_guest_memory guest_view;
    fake_guest_memory guest;
    fake_renderer renderer;
    uint64_t value;
    uint32_t i;

    memset(&guest, 0, sizeof(guest));
    guest.bytes[4] = 0xA5u;

    memset(&renderer, 0, sizeof(renderer));
    renderer.bounds.left = 10u;
    renderer.bounds.right = 127u;
    renderer.bounds.top = 20u;
    renderer.bounds.bottom = 199u;
    renderer.bounds.valid = 1u;

    memset(&sink, 0, sizeof(sink));
    sink.context = &renderer;
    sink.submit_fifo = fake_submit;
    sink.sync = fake_sync;
    sink.read_bounds = fake_bounds;
    sink.write_bound = fake_write_bound;
    openspm_gx_platform_reset(&gx, &sink);

    guest_view.context = &guest;
    guest_view.map = fake_map_guest;
    openspm_gx_platform_set_guest_memory(&gx, &guest_view);

    /* GXSetCPUFifo/GXSetGPFifo configure Wii-visible FIFO bookkeeping before
       actual write-gather commands begin. The native port preserves that state
       while Aurora remains the only graphics executor. The four PI/CP writes
       below are the exact launch(20260911-014802) frontier. */
    CHECK(openspm_gx_platform_bus_read(
              &gx, OPENSPM_GX_CP_STATUS, 2u, &value) == OPENSPM_OK &&
          value == 0x000Cu);
    CHECK(openspm_gx_platform_bus_write(
              &gx, OPENSPM_GX_CP_CONTROL, 2u, 0u) == OPENSPM_OK);
    CHECK(openspm_gx_platform_bus_write(
              &gx, OPENSPM_GX_PI_FIFO_BASE, 4u, 0x00586100u) == OPENSPM_OK);
    CHECK(openspm_gx_platform_bus_write(
              &gx, OPENSPM_GX_PI_FIFO_END, 4u, 0x005E60FCu) == OPENSPM_OK);
    CHECK(openspm_gx_platform_bus_write(
              &gx, OPENSPM_GX_PI_FIFO_WPTR, 4u, 0x00586100u) == OPENSPM_OK);
    CHECK(openspm_gx_platform_bus_read(
              &gx, OPENSPM_GX_PI_FIFO_BASE, 4u, &value) == OPENSPM_OK &&
          value == 0x00586100u);
    CHECK(openspm_gx_platform_bus_read(
              &gx, OPENSPM_GX_PI_FIFO_END, 4u, &value) == OPENSPM_OK &&
          value == 0x005E60FCu);
    CHECK(openspm_gx_platform_bus_read(
              &gx, OPENSPM_GX_PI_FIFO_WPTR, 4u, &value) == OPENSPM_OK &&
          value == 0x00586100u);

    /* GXSetGPFifo programs all base/end/watermark/distance/read/write halves.
       Batch the whole 0x20..0x3A family now instead of discovering fourteen
       halfwords through fourteen player launches. */
    for (i = 0u; i < OPENSPM_GX_CP_FIFO_WORDS; ++i) {
        uint32_t address = OPENSPM_GX_CP_FIFO_FIRST + i * 2u;
        uint16_t sample = (uint16_t)(0x1000u + i);
        CHECK(openspm_gx_platform_bus_write(&gx, address, 2u, sample) == OPENSPM_OK);
        CHECK(openspm_gx_platform_bus_read(&gx, address, 2u, &value) == OPENSPM_OK &&
              value == sample);
    }
    CHECK(openspm_gx_platform_bus_write(
              &gx, OPENSPM_GX_CP_CONTROL, 2u, 0x0011u) == OPENSPM_OK);
    CHECK(openspm_gx_platform_bus_read(
              &gx, OPENSPM_GX_CP_CONTROL, 2u, &value) == OPENSPM_OK &&
          value == 0x0011u);
    CHECK(openspm_gx_platform_bus_write(
              &gx, OPENSPM_GX_CP_CLEAR, 2u, 0x0007u) == OPENSPM_OK);

    /* launch(20260911-020529) reaches the CP performance-selector reset just
       before GXInit starts issuing real write-gather commands. Preserve only
       the documented three selector bits; hardware performance counters are
       not a native-renderer execution model. */
    CHECK(openspm_gx_platform_bus_write(
              &gx, OPENSPM_GX_CP_PERF_SELECT, 2u, 0u) == OPENSPM_OK);
    CHECK(openspm_gx_platform_bus_read(
              &gx, OPENSPM_GX_CP_PERF_SELECT, 2u, &value) == OPENSPM_OK &&
          value == 0u);
    CHECK(openspm_gx_platform_bus_write(
              &gx, OPENSPM_GX_CP_PERF_SELECT, 2u, 0x0007u) == OPENSPM_OK);
    CHECK(openspm_gx_platform_bus_read(
              &gx, OPENSPM_GX_CP_PERF_SELECT, 2u, &value) == OPENSPM_OK &&
          value == 0x0007u);
    CHECK(openspm_gx_platform_bus_write(
              &gx, OPENSPM_GX_CP_PERF_SELECT, 2u, 0x0008u) == OPENSPM_ERROR_UNSUPPORTED);
    CHECK(openspm_gx_platform_bus_read(
              &gx, OPENSPM_GX_CP_PERF_SELECT, 4u, &value) == OPENSPM_ERROR_UNSUPPORTED);

    CHECK(openspm_gx_platform_bus_write(
              &gx, OPENSPM_GX_CP_CONTROL, 2u, 0x0040u) == OPENSPM_ERROR_UNSUPPORTED);
    CHECK(openspm_gx_platform_bus_write(
              &gx, OPENSPM_GX_CP_CLEAR, 2u, 0x0008u) == OPENSPM_ERROR_UNSUPPORTED);
    CHECK(openspm_gx_platform_bus_read(
              &gx, OPENSPM_GX_CP_CONTROL, 4u, &value) == OPENSPM_ERROR_UNSUPPORTED);
    CHECK(openspm_gx_platform_bus_write(
              &gx, OPENSPM_GX_PI_FIFO_BASE, 2u, 0u) == OPENSPM_ERROR_UNSUPPORTED);
    CHECK(openspm_gx_platform_bus_write(
              &gx, UINT32_C(0xCC00003C), 2u, 0u) == OPENSPM_ERROR_NOT_FOUND);

    /* Split BP token packet across separate write-gather stores. The renderer
       also proves it receives only a bounded guest-memory resolver rather than
       reaching into backend-owned MEM1/MEM2 state directly. */
    CHECK(openspm_gx_platform_bus_write(
              &gx, OPENSPM_GX_FIFO_ADDRESS, 1u, 0x61u) == OPENSPM_OK);
    CHECK(openspm_gx_platform_bus_write(
              &gx, OPENSPM_GX_FIFO_ADDRESS, 4u, 0x4700BEEFu) == OPENSPM_OK);
    CHECK(renderer.size == 5u);
    CHECK(renderer.bytes[0] == 0x61u && renderer.bytes[1] == 0x47u &&
          renderer.bytes[4] == 0xEFu);
    CHECK(renderer.saw_guest_memory == 1u && renderer.mapped_byte == 0xA5u);
    CHECK(renderer.mapped_span_size == 12u);
    CHECK(renderer.sync_count == 0u);
    CHECK(openspm_gx_platform_bus_read(
              &gx, OPENSPM_GX_PE_TOKEN, 2u, &value) == OPENSPM_OK);
    CHECK(value == 0xBEEFu);

    /* Draw-done is queued for the scheduler, but the renderer also receives an
       explicit safe drain boundary after the complete five-byte packet has
       already been submitted. */
    CHECK(openspm_gx_platform_bus_write(
              &gx, OPENSPM_GX_FIFO_ADDRESS, 1u, 0x61u) == OPENSPM_OK);
    CHECK(openspm_gx_platform_bus_write(
              &gx, OPENSPM_GX_FIFO_ADDRESS, 4u, 0x45000002u) == OPENSPM_OK);
    CHECK(renderer.sync_count == 1u);
    CHECK(renderer.last_sync_reason == OPENSPM_GX_SYNC_DRAW_DONE);
    CHECK(openspm_gx_platform_take_draw_done(&gx) == 1u);
    CHECK(openspm_gx_platform_take_draw_done(&gx) == 0u);

    /* This is the M14 regression that matters: bbox reads are renderer-owned.
       There is no 64x64 headless fallback in reusable OpenSPM code. */
    CHECK(openspm_gx_platform_bus_read(
              &gx, OPENSPM_GX_PE_BBOX_LEFT, 2u, &value) == OPENSPM_OK &&
          value == 10u);
    CHECK(openspm_gx_platform_bus_read(
              &gx, OPENSPM_GX_PE_BBOX_RIGHT, 2u, &value) == OPENSPM_OK &&
          value == 127u);
    CHECK(openspm_gx_platform_bus_read(
              &gx, OPENSPM_GX_PE_BBOX_TOP, 2u, &value) == OPENSPM_OK &&
          value == 20u);
    CHECK(openspm_gx_platform_bus_read(
              &gx, OPENSPM_GX_PE_BBOX_BOTTOM, 2u, &value) == OPENSPM_OK &&
          value == 199u);

    /* GXClearBoundingBox writes PE state before later fragments expand it. The
       renderer must see the exact guest writes; otherwise a GPU-side bbox would
       monotonically accumulate forever even though reads looked renderer-owned. */
    CHECK(openspm_gx_platform_bus_write(
              &gx, OPENSPM_GX_PE_BBOX_LEFT, 2u, 1023u) == OPENSPM_OK);
    CHECK(openspm_gx_platform_bus_write(
              &gx, OPENSPM_GX_PE_BBOX_RIGHT, 2u, 0u) == OPENSPM_OK);
    CHECK(openspm_gx_platform_bus_write(
              &gx, OPENSPM_GX_PE_BBOX_TOP, 2u, 1023u) == OPENSPM_OK);
    CHECK(openspm_gx_platform_bus_write(
              &gx, OPENSPM_GX_PE_BBOX_BOTTOM, 2u, 0u) == OPENSPM_OK);
    CHECK(renderer.bound_writes == 4u);
    CHECK(renderer.last_bound_address == OPENSPM_GX_PE_BBOX_BOTTOM);
    CHECK(renderer.last_bound_value == 0u);
    CHECK(openspm_gx_platform_bus_read(
              &gx, OPENSPM_GX_PE_BBOX_LEFT, 2u, &value) == OPENSPM_OK &&
          value == 1023u);
    CHECK(openspm_gx_platform_bus_read(
              &gx, OPENSPM_GX_PE_BBOX_RIGHT, 2u, &value) == OPENSPM_OK &&
          value == 0u);

    CHECK(openspm_gx_platform_bus_write(
              &gx, OPENSPM_GX_PE_BASE + 4u, 2u, 0x1234u) == OPENSPM_OK);
    CHECK(openspm_gx_platform_bus_read(
              &gx, OPENSPM_GX_PE_BASE + 4u, 2u, &value) == OPENSPM_OK &&
          value == 0x1234u);

    openspm_gx_platform_set_guest_memory(&gx, NULL);
    renderer.saw_guest_memory = 0u;
    CHECK(openspm_gx_platform_bus_write(
              &gx, OPENSPM_GX_FIFO_ADDRESS, 1u, 0x00u) == OPENSPM_OK);
    CHECK(renderer.saw_guest_memory == 0u);

    openspm_gx_platform_set_renderer(&gx, NULL);
    CHECK(openspm_gx_platform_bus_read(
              &gx, OPENSPM_GX_PE_BBOX_LEFT, 2u, &value) ==
          OPENSPM_ERROR_NOT_FOUND);
    CHECK(openspm_gx_platform_bus_read(
              &gx, OPENSPM_GX_PE_BBOX_LEFT, 4u, &value) ==
          OPENSPM_ERROR_UNSUPPORTED);
    CHECK(openspm_gx_platform_bus_write(
              &gx, OPENSPM_GX_FIFO_ADDRESS, 3u, 0u) ==
          OPENSPM_ERROR_UNSUPPORTED);

    puts("m14 gx platform tests passed");
    return 0;
}
