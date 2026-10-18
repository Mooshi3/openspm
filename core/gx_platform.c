#include "openspm/gx_platform.h"

#include <limits.h>
#include <string.h>

/* BP command packets are five bytes: 0x61 followed by one 32-bit register
   write. These two observations are all the CPU side needs during the M14
   renderer bring-up; the renderer still receives every FIFO byte unchanged. */
#define OPENSPM_GX_BP_DRAW_DONE    UINT64_C(0x6145000002)
#define OPENSPM_GX_BP_TOKEN_MASK   UINT64_C(0xFFFFFF0000)
#define OPENSPM_GX_BP_TOKEN_PREFIX UINT64_C(0x6147000000)

#define OPENSPM_GX_CP_CONTROL_BITS UINT16_C(0x003F)
#define OPENSPM_GX_CP_CLEAR_BITS   UINT16_C(0x0007)
#define OPENSPM_GX_CP_PERF_SELECT_BITS UINT16_C(0x0007)
#define OPENSPM_GX_CP_STATUS_OVERFLOW  UINT16_C(0x0001)
#define OPENSPM_GX_CP_STATUS_UNDERFLOW UINT16_C(0x0002)
#define OPENSPM_GX_CP_STATUS_IDLE      UINT16_C(0x000C)

static int fifo_width_supported(uint32_t width) {
    return width == 1u || width == 2u || width == 4u || width == 8u;
}

static int pe_index(uint32_t address, uint32_t width, uint32_t *out_index) {
    uint32_t offset;
    if (out_index == NULL || address < OPENSPM_GX_PE_BASE ||
        address >= OPENSPM_GX_PE_END)
        return 0;

    offset = address - OPENSPM_GX_PE_BASE;
    if ((offset & 1u) != 0u || width != 2u) return 0;
    *out_index = offset >> 1;
    return *out_index < 32u;
}

static int cp_fifo_index(uint32_t address, uint32_t *out_index) {
    uint32_t offset;
    uint32_t index;
    if (address < OPENSPM_GX_CP_FIFO_FIRST ||
        address > OPENSPM_GX_CP_FIFO_LAST ||
        (address & UINT32_C(1)) != 0u)
        return 0;
    offset = address - OPENSPM_GX_CP_FIFO_FIRST;
    index = offset >> 1;
    if (index >= OPENSPM_GX_CP_FIFO_WORDS) return 0;
    if (out_index != NULL) *out_index = index;
    return 1;
}

static int renderer_bounds(openspm_gx_platform *platform,
                           openspm_gx_raster_bounds *out_bounds) {
    int result;
    if (platform == NULL || out_bounds == NULL)
        return OPENSPM_ERROR_INVALID_ARGUMENT;

    memset(out_bounds, 0, sizeof(*out_bounds));
    if (platform->renderer.read_bounds == NULL)
        return OPENSPM_ERROR_NOT_FOUND;

    result = platform->renderer.read_bounds(platform->renderer.context, out_bounds);
    if (result != OPENSPM_OK) return result;
    return out_bounds->valid ? OPENSPM_OK : OPENSPM_ERROR_NOT_FOUND;
}

static int map_guest(openspm_gx_platform *platform,
                     uint32_t address,
                     uint32_t min_size,
                     openspm_gx_guest_span *out_span) {
    if (platform == NULL || out_span == NULL)
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    memset(out_span, 0, sizeof(*out_span));
    if (platform->guest_memory.map == NULL)
        return OPENSPM_ERROR_NOT_FOUND;
    return platform->guest_memory.map(platform->guest_memory.context,
                                      address, min_size, out_span);
}

void openspm_gx_platform_reset(openspm_gx_platform *platform,
                               const openspm_gx_renderer_sink *renderer) {
    if (platform == NULL) return;
    memset(platform, 0, sizeof(*platform));
    /* The native renderer consumes submitted GX work synchronously at the
       OpenSPM command-stream boundary, so from Nintendo's CPU-side GX library
       the command processor begins both read-idle and command-idle. This is a
       native completion contract, not a simulated GPU execution timeline. */
    platform->cp_status = OPENSPM_GX_CP_STATUS_IDLE;
    if (renderer != NULL) platform->renderer = *renderer;
}

void openspm_gx_platform_set_renderer(openspm_gx_platform *platform,
                                      const openspm_gx_renderer_sink *renderer) {
    if (platform == NULL) return;
    memset(&platform->renderer, 0, sizeof(platform->renderer));
    if (renderer != NULL) platform->renderer = *renderer;
}

void openspm_gx_platform_set_guest_memory(
    openspm_gx_platform *platform,
    const openspm_gx_guest_memory *guest_memory) {
    if (platform == NULL) return;
    memset(&platform->guest_memory, 0, sizeof(platform->guest_memory));
    if (guest_memory != NULL) platform->guest_memory = *guest_memory;
}

int openspm_gx_platform_begin_display_list(openspm_gx_platform *platform,
                                           uint32_t guest_address,
                                           uint32_t capacity) {
    openspm_gx_guest_span span;
    int result;

    if (platform == NULL || capacity == 0u)
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    if (platform->display_list_active)
        return OPENSPM_ERROR_RUNTIME;

    /* Validate the full caller-provided recording buffer before redirecting the
       FIFO. This keeps an invalid game pointer from becoming a late partial
       write and matches the reusable platform's bounded-memory contract. */
    result = map_guest(platform, guest_address, capacity, &span);
    if (result != OPENSPM_OK) return result;
    if (span.bytes == NULL || span.size < capacity)
        return OPENSPM_ERROR_NOT_FOUND;

    platform->display_list_address = guest_address;
    platform->display_list_capacity = capacity;
    platform->display_list_bytes = 0u;
    platform->display_list_overflow = 0u;
    platform->display_list_active = 1u;
    return OPENSPM_OK;
}

int openspm_gx_platform_end_display_list(openspm_gx_platform *platform,
                                         uint32_t *out_size) {
    uint32_t raw;
    uint32_t aligned;
    openspm_gx_guest_span span;
    int result = OPENSPM_OK;

    if (platform == NULL || out_size == NULL)
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    *out_size = 0u;
    if (!platform->display_list_active)
        return OPENSPM_ERROR_RUNTIME;

    raw = platform->display_list_bytes;
    if (raw > UINT32_MAX - 31u) {
        platform->display_list_overflow = 1u;
        aligned = 0u;
    } else {
        aligned = (raw + 31u) & ~UINT32_C(31);
    }

    if (!platform->display_list_overflow && aligned <= platform->display_list_capacity) {
        if (aligned > raw) {
            result = map_guest(platform,
                               platform->display_list_address + raw,
                               aligned - raw,
                               &span);
            if (result == OPENSPM_OK && span.bytes != NULL &&
                span.size >= aligned - raw) {
                memset(span.bytes, 0, aligned - raw);
            } else {
                platform->display_list_overflow = 1u;
            }
        }
    } else {
        platform->display_list_overflow = 1u;
    }

    if (!platform->display_list_overflow)
        *out_size = aligned;

    platform->display_list_active = 0u;
    platform->display_list_address = 0u;
    platform->display_list_capacity = 0u;
    platform->display_list_bytes = 0u;

    /* Overflow is part of GXEndDisplayList's observable result (size zero), not
       a host runtime failure. A failed guest-memory map is likewise collapsed
       to that SDK-visible result after recording has been safely disabled. */
    platform->display_list_overflow = 0u;
    return OPENSPM_OK;
}

int openspm_gx_platform_bus_read(openspm_gx_platform *platform,
                                 uint32_t address,
                                 uint32_t width,
                                 uint64_t *out_value) {
    uint32_t index;
    openspm_gx_raster_bounds bounds;
    int result;

    if (platform == NULL || out_value == NULL)
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    *out_value = 0u;

    if (address == OPENSPM_GX_CP_STATUS) {
        if (width != 2u) return OPENSPM_ERROR_UNSUPPORTED;
        *out_value = platform->cp_status;
        return OPENSPM_OK;
    }
    if (address == OPENSPM_GX_CP_CONTROL) {
        if (width != 2u) return OPENSPM_ERROR_UNSUPPORTED;
        *out_value = platform->cp_control;
        return OPENSPM_OK;
    }
    if (address == OPENSPM_GX_CP_PERF_SELECT) {
        if (width != 2u) return OPENSPM_ERROR_UNSUPPORTED;
        *out_value = platform->cp_perf_select;
        return OPENSPM_OK;
    }
    if (cp_fifo_index(address, &index)) {
        if (width != 2u) return OPENSPM_ERROR_UNSUPPORTED;
        *out_value = platform->cp_fifo[index];
        return OPENSPM_OK;
    }
    if (address == OPENSPM_GX_PI_FIFO_BASE ||
        address == OPENSPM_GX_PI_FIFO_END ||
        address == OPENSPM_GX_PI_FIFO_WPTR) {
        if (width != 4u) return OPENSPM_ERROR_UNSUPPORTED;
        if (address == OPENSPM_GX_PI_FIFO_BASE) *out_value = platform->pi_fifo_base;
        else if (address == OPENSPM_GX_PI_FIFO_END) *out_value = platform->pi_fifo_end;
        else *out_value = platform->pi_fifo_wptr;
        return OPENSPM_OK;
    }

    if (address == OPENSPM_GX_PE_TOKEN) {
        if (width != 2u) return OPENSPM_ERROR_UNSUPPORTED;
        *out_value = platform->pe_token;
        return OPENSPM_OK;
    }

    if (address == OPENSPM_GX_PE_BBOX_LEFT ||
        address == OPENSPM_GX_PE_BBOX_RIGHT ||
        address == OPENSPM_GX_PE_BBOX_TOP ||
        address == OPENSPM_GX_PE_BBOX_BOTTOM) {
        if (width != 2u) return OPENSPM_ERROR_UNSUPPORTED;
        result = renderer_bounds(platform, &bounds);
        if (result != OPENSPM_OK) return result;

        if (address == OPENSPM_GX_PE_BBOX_LEFT) *out_value = bounds.left;
        else if (address == OPENSPM_GX_PE_BBOX_RIGHT) *out_value = bounds.right;
        else if (address == OPENSPM_GX_PE_BBOX_TOP) *out_value = bounds.top;
        else *out_value = bounds.bottom;
        return OPENSPM_OK;
    }

    if (pe_index(address, width, &index)) {
        *out_value = platform->pe_registers[index];
        return OPENSPM_OK;
    }

    return OPENSPM_ERROR_NOT_FOUND;
}

int openspm_gx_platform_bus_write(openspm_gx_platform *platform,
                                  uint32_t address,
                                  uint32_t width,
                                  uint64_t value) {
    uint32_t index;

    if (platform == NULL) return OPENSPM_ERROR_INVALID_ARGUMENT;

    if (address == OPENSPM_GX_CP_CONTROL) {
        if (width != 2u) return OPENSPM_ERROR_UNSUPPORTED;
        if (((uint16_t)value & (uint16_t)~OPENSPM_GX_CP_CONTROL_BITS) != 0u)
            return OPENSPM_ERROR_UNSUPPORTED;
        platform->cp_control = (uint16_t)value;
        return OPENSPM_OK;
    }
    if (address == OPENSPM_GX_CP_CLEAR) {
        uint16_t clear;
        if (width != 2u) return OPENSPM_ERROR_UNSUPPORTED;
        clear = (uint16_t)value;
        if ((clear & (uint16_t)~OPENSPM_GX_CP_CLEAR_BITS) != 0u)
            return OPENSPM_ERROR_UNSUPPORTED;
        if ((clear & UINT16_C(1)) != 0u)
            platform->cp_status &= (uint16_t)~OPENSPM_GX_CP_STATUS_OVERFLOW;
        if ((clear & UINT16_C(2)) != 0u)
            platform->cp_status &= (uint16_t)~OPENSPM_GX_CP_STATUS_UNDERFLOW;
        /* ClearMetrics has no separate CPU-visible counter state in OpenSPM. */
        return OPENSPM_OK;
    }
    if (address == OPENSPM_GX_CP_PERF_SELECT) {
        if (width != 2u) return OPENSPM_ERROR_UNSUPPORTED;
        if (((uint16_t)value & (uint16_t)~OPENSPM_GX_CP_PERF_SELECT_BITS) != 0u)
            return OPENSPM_ERROR_UNSUPPORTED;
        /* GXSetGPMetric selects console performance counters here. Preserve the
           guest-visible 3-bit selector, but do not create a GPU timing model:
           native rendering and completion remain owned by Aurora. */
        platform->cp_perf_select = (uint16_t)value;
        return OPENSPM_OK;
    }
    if (cp_fifo_index(address, &index)) {
        if (width != 2u) return OPENSPM_ERROR_UNSUPPORTED;
        platform->cp_fifo[index] = (uint16_t)value;
        return OPENSPM_OK;
    }
    if (address == OPENSPM_GX_PI_FIFO_BASE ||
        address == OPENSPM_GX_PI_FIFO_END ||
        address == OPENSPM_GX_PI_FIFO_WPTR) {
        if (width != 4u) return OPENSPM_ERROR_UNSUPPORTED;
        if (address == OPENSPM_GX_PI_FIFO_BASE) platform->pi_fifo_base = (uint32_t)value;
        else if (address == OPENSPM_GX_PI_FIFO_END) platform->pi_fifo_end = (uint32_t)value;
        else platform->pi_fifo_wptr = (uint32_t)value;
        return OPENSPM_OK;
    }

    if (address == OPENSPM_GX_FIFO_ADDRESS) {
        uint8_t bytes[8];
        uint32_t i;
        uint32_t saw_draw_done = 0u;
        int result = OPENSPM_OK;

        if (!fifo_width_supported(width)) return OPENSPM_ERROR_UNSUPPORTED;

        /* CPU stores into the write-gather pipe are observed by GX in PPC
           big-endian byte order. Preserve that exact stream regardless of host
           endianness, whether bytes are being recorded or rendered live. */
        for (i = 0; i < width; ++i) {
            uint32_t shift = (width - 1u - i) * 8u;
            bytes[i] = (uint8_t)(value >> shift);
        }

        if (platform->display_list_active) {
            if (!platform->display_list_overflow &&
                platform->display_list_bytes <= platform->display_list_capacity &&
                width <= platform->display_list_capacity - platform->display_list_bytes) {
                openspm_gx_guest_span span;
                uint32_t target = platform->display_list_address +
                                  platform->display_list_bytes;
                result = map_guest(platform, target, width, &span);
                if (result == OPENSPM_OK && span.bytes != NULL && span.size >= width) {
                    memcpy(span.bytes, bytes, width);
                } else {
                    platform->display_list_overflow = 1u;
                }
            } else {
                platform->display_list_overflow = 1u;
            }

            if (platform->display_list_bytes <= UINT32_MAX - width)
                platform->display_list_bytes += width;
            else {
                platform->display_list_bytes = UINT32_MAX;
                platform->display_list_overflow = 1u;
            }
            return OPENSPM_OK;
        }

        for (i = 0; i < width; ++i) {
            platform->fifo_shift =
                ((platform->fifo_shift << 8u) | bytes[i]) & UINT64_C(0xFFFFFFFFFF);

            if (platform->fifo_shift == OPENSPM_GX_BP_DRAW_DONE) {
                if (platform->pending_draw_done != UINT32_MAX)
                    ++platform->pending_draw_done;
                saw_draw_done = 1u;
            }

            if ((platform->fifo_shift & OPENSPM_GX_BP_TOKEN_MASK) ==
                OPENSPM_GX_BP_TOKEN_PREFIX) {
                platform->pe_token = (uint16_t)(platform->fifo_shift & UINT64_C(0xFFFF));
            }
        }

        platform->fifo_bytes += width;
        if (platform->renderer.submit_fifo != NULL) {
            const openspm_gx_guest_memory *guest_memory =
                platform->guest_memory.map != NULL ? &platform->guest_memory : NULL;
            result = platform->renderer.submit_fifo(
                platform->renderer.context, bytes, width, guest_memory);
        }
        if (result == OPENSPM_OK && saw_draw_done && platform->renderer.sync != NULL) {
            result = platform->renderer.sync(
                platform->renderer.context, OPENSPM_GX_SYNC_DRAW_DONE);
        }
        return result;
    }

    if (address == OPENSPM_GX_PE_BBOX_LEFT ||
        address == OPENSPM_GX_PE_BBOX_RIGHT ||
        address == OPENSPM_GX_PE_BBOX_TOP ||
        address == OPENSPM_GX_PE_BBOX_BOTTOM) {
        int result = OPENSPM_OK;
        if (width != 2u) return OPENSPM_ERROR_UNSUPPORTED;
        if (!pe_index(address, width, &index)) return OPENSPM_ERROR_NOT_FOUND;
        platform->pe_registers[index] = (uint16_t)value;
        if (platform->renderer.write_bound != NULL) {
            result = platform->renderer.write_bound(
                platform->renderer.context, address, (uint16_t)value);
        }
        return result;
    }

    if (pe_index(address, width, &index)) {
        platform->pe_registers[index] = (uint16_t)value;
        return OPENSPM_OK;
    }

    return OPENSPM_ERROR_NOT_FOUND;
}

uint32_t openspm_gx_platform_take_draw_done(openspm_gx_platform *platform) {
    if (platform == NULL || platform->pending_draw_done == 0u) return 0u;
    --platform->pending_draw_done;
    return 1u;
}
