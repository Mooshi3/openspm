#include "openspm/gx_platform.h"

#include <stdio.h>
#include <string.h>

typedef struct fake_memory {
    uint8_t bytes[256];
} fake_memory;

typedef struct fake_renderer {
    uint8_t bytes[32];
    uint32_t size;
} fake_renderer;

static int OPENSPM_CALL map_guest(void *context,
                                  uint32_t guest_address,
                                  uint32_t min_size,
                                  openspm_gx_guest_span *out_span) {
    fake_memory *memory = (fake_memory *)context;
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

static int OPENSPM_CALL submit_fifo(
    void *context,
    const uint8_t *bytes,
    uint32_t size,
    const openspm_gx_guest_memory *guest_memory) {
    fake_renderer *renderer = (fake_renderer *)context;
    (void)guest_memory;
    if (renderer == NULL || bytes == NULL ||
        renderer->size + size > sizeof(renderer->bytes))
        return OPENSPM_ERROR_RUNTIME;
    memcpy(renderer->bytes + renderer->size, bytes, size);
    renderer->size += size;
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
    fake_memory memory;
    fake_renderer renderer;
    uint32_t size = UINT32_MAX;
    uint32_t i;

    memset(&memory, 0xCD, sizeof(memory));
    memset(&renderer, 0, sizeof(renderer));
    memset(&sink, 0, sizeof(sink));
    memset(&guest_view, 0, sizeof(guest_view));

    sink.context = &renderer;
    sink.submit_fifo = submit_fifo;
    guest_view.context = &memory;
    guest_view.map = map_guest;

    openspm_gx_platform_reset(&gx, &sink);
    openspm_gx_platform_set_guest_memory(&gx, &guest_view);

    /* Record seven FIFO bytes using mixed PPC store widths. They must land in
       guest RAM in GX byte order and never reach the live renderer. */
    CHECK(openspm_gx_platform_begin_display_list(
              &gx, UINT32_C(0x80000020), 64u) == OPENSPM_OK);
    CHECK(openspm_gx_platform_begin_display_list(
              &gx, UINT32_C(0x80000080), 32u) == OPENSPM_ERROR_RUNTIME);
    CHECK(openspm_gx_platform_bus_write(
              &gx, OPENSPM_GX_FIFO_ADDRESS, 1u, UINT64_C(0xAA)) == OPENSPM_OK);
    CHECK(openspm_gx_platform_bus_write(
              &gx, OPENSPM_GX_FIFO_ADDRESS, 2u, UINT64_C(0xBBCC)) == OPENSPM_OK);
    CHECK(openspm_gx_platform_bus_write(
              &gx, OPENSPM_GX_FIFO_ADDRESS, 4u, UINT64_C(0xDDEEFF00)) == OPENSPM_OK);
    CHECK(renderer.size == 0u);
    CHECK(gx.fifo_bytes == 0u);

    CHECK(openspm_gx_platform_end_display_list(&gx, &size) == OPENSPM_OK);
    CHECK(size == 32u);
    CHECK(memory.bytes[0x20] == 0xAAu);
    CHECK(memory.bytes[0x21] == 0xBBu);
    CHECK(memory.bytes[0x22] == 0xCCu);
    CHECK(memory.bytes[0x23] == 0xDDu);
    CHECK(memory.bytes[0x24] == 0xEEu);
    CHECK(memory.bytes[0x25] == 0xFFu);
    CHECK(memory.bytes[0x26] == 0x00u);
    for (i = 0x27u; i < 0x40u; ++i) CHECK(memory.bytes[i] == 0u);
    CHECK(openspm_gx_platform_end_display_list(&gx, &size) == OPENSPM_ERROR_RUNTIME);

    /* Once recording ends, FIFO traffic is live again. */
    CHECK(openspm_gx_platform_bus_write(
              &gx, OPENSPM_GX_FIFO_ADDRESS, 2u, UINT64_C(0x1234)) == OPENSPM_OK);
    CHECK(renderer.size == 2u);
    CHECK(renderer.bytes[0] == 0x12u && renderer.bytes[1] == 0x34u);
    CHECK(gx.fifo_bytes == 2u);

    /* Overflow is an SDK-visible zero-size result, not a host crash. The
       renderer still must not see bytes written while list recording is active. */
    memset(memory.bytes + 0x80, 0x5A, 32u);
    CHECK(openspm_gx_platform_begin_display_list(
              &gx, UINT32_C(0x80000080), 16u) == OPENSPM_OK);
    CHECK(openspm_gx_platform_bus_write(
              &gx, OPENSPM_GX_FIFO_ADDRESS, 8u, UINT64_C(0x0102030405060708)) == OPENSPM_OK);
    CHECK(openspm_gx_platform_bus_write(
              &gx, OPENSPM_GX_FIFO_ADDRESS, 8u, UINT64_C(0x1112131415161718)) == OPENSPM_OK);
    CHECK(openspm_gx_platform_bus_write(
              &gx, OPENSPM_GX_FIFO_ADDRESS, 1u, UINT64_C(0x19)) == OPENSPM_OK);
    CHECK(renderer.size == 2u);
    size = UINT32_MAX;
    CHECK(openspm_gx_platform_end_display_list(&gx, &size) == OPENSPM_OK);
    CHECK(size == 0u);
    CHECK(memory.bytes[0x80] == 0x01u && memory.bytes[0x87] == 0x08u);
    CHECK(memory.bytes[0x88] == 0x11u && memory.bytes[0x8F] == 0x18u);
    CHECK(memory.bytes[0x90] == 0x5Au);

    /* Invalid guest ranges are rejected before FIFO redirection begins. */
    CHECK(openspm_gx_platform_begin_display_list(
              &gx, UINT32_C(0x800000F0), 32u) == OPENSPM_ERROR_NOT_FOUND);
    CHECK(gx.display_list_active == 0u);

    puts("m14 gx display-list tests passed");
    return 0;
}
