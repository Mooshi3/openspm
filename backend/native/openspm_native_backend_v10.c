/*
 * M14 standalone-native backend dev24 fidelity/input layer.
 *
 * dev23 is runtime-proven through title and stable file select. Keep its
 * transport, timing, renderer, and frame-latched input behavior intact.
 *
 * dev24 repairs narrow native-platform gaps:
 *   1. dev21 intentionally exposed a structurally valid but blank ANSI OSFont.
 *      Generate OpenSPM-owned 5x7 replacement glyphs in the same legal Yay0
 *      stream; no Nintendo font artwork or font bytes are embedded.
 *   2. SPM's original wpadMain calls R8PE01/US2 KPADRead at 0x802DF4F0 and
 *      checks KPADStatus.error. Host exactly KPADRead as the physical sample
 *      transport boundary. Controller 0 is a connected plain Wii Remote backed
 *      by dev23's frame latch; channels 1-3 report no controller.
 *   3. SPM separately calls WPADProbe at 0x802CE890 while constructing the
 *      controller records used by the UI. A healthy KPAD sample alone cannot
 *      make that Wii Bluetooth control block report a controller on PC, so host
 *      this second physical-device boundary too. Channel 0 is WPAD_DEV_CORE;
 *      channels 1-3 are absent.
 *   4. Native gameplay reaches pointer-gated progression in Bestovius' house.
 *      Feed the PC mouse through the same frame-latched KPAD snapshot as the
 *      Wii Remote DPD/IR position instead of special-casing game logic.
 *
 * The opening seq=0/stage=7 DrawDone-bounded audits remain diagnostic-only.
 *
 * v9 is a legacy C-file wrapper whose internal #undef prevents another ordinary
 * macro-renaming wrapper. Compile its query internally, then export dev24 through
 * an explicit MSVC x64 export alias. OpenSPM's M14 Windows runner is x64/MSVC.
 */

#include "openspm/abi.h"

/* Suppress v9's dllexport; its function remains callable inside this TU. */
#undef OPENSPM_EXPORT
#define OPENSPM_EXPORT
#include "openspm_native_backend_v9.c"
#undef OPENSPM_EXPORT
#define OPENSPM_EXPORT __declspec(dllexport)

#include "openspm/pointer_input.h"

#if !defined(_MSC_VER) || !defined(_M_X64)
#error "OpenSPM M14 dev24 backend wrapper currently requires MSVC x64"
#endif
#pragma comment(linker, "/EXPORT:openspm_backend_query=openspm_backend_query_v10")

#define R8PE01_WPAD_PROBE UINT32_C(0x802CE890)
#define R8PE01_KPAD_READ UINT32_C(0x802DF4F0)
#define OPENSPM_KPAD_STATUS_SIZE UINT32_C(0x84)
#define OPENSPM_KPAD_BUTTONS_HELD UINT32_C(0x00)
#define OPENSPM_KPAD_BUTTONS_PRESSED UINT32_C(0x04)
#define OPENSPM_KPAD_BUTTONS_RELEASED UINT32_C(0x08)
#define OPENSPM_KPAD_POS_X UINT32_C(0x20)
#define OPENSPM_KPAD_POS_Y UINT32_C(0x24)
#define OPENSPM_KPAD_POS_DIFF_X UINT32_C(0x28)
#define OPENSPM_KPAD_POS_DIFF_Y UINT32_C(0x2C)
#define OPENSPM_KPAD_POS_DIFF_MAG UINT32_C(0x30)
#define OPENSPM_KPAD_DIST UINT32_C(0x48)
#define OPENSPM_KPAD_DIST_DIFF UINT32_C(0x4C)
#define OPENSPM_KPAD_DIST_DIFF_MAG UINT32_C(0x50)
#define OPENSPM_KPAD_DOWN_X UINT32_C(0x54)
#define OPENSPM_KPAD_DOWN_Y UINT32_C(0x58)
#define OPENSPM_KPAD_EXTENSION_TYPE UINT32_C(0x5C)
#define OPENSPM_KPAD_ERROR UINT32_C(0x5D)
#define OPENSPM_KPAD_DPD_STATUS UINT32_C(0x5E)
#define OPENSPM_KPAD_DATA_FORMAT UINT32_C(0x5F)
#define OPENSPM_KPAD_DPD_VALID UINT8_C(2)
#define OPENSPM_WPAD_DEV_CORE UINT32_C(0)
#define OPENSPM_WPAD_DEV_NOT_FOUND UINT32_C(253)
#define OPENSPM_WPAD_ERR_NONE 0
#define OPENSPM_WPAD_ERR_NO_CONTROLLER (-1)

/* Wii Remote rumble (R8PE01 rev 2, r13 = 0x80574900). WPADControlMotor
   (0x802CEDC0) only records the motor in the SDK's WPAD control block and
   queues a Bluetooth report; in this port no Wii Remote is behind that block
   (it returns early while the block reports WPAD_ERR_NO_CONTROLLER, the reason
   WPADProbe/KPADRead are hosted above), so nothing reaches a motor. Instead the
   backend reads the game's own rumble state once per frame, from the two
   places that decide the motor:
     - wpadmgr: wpadRumbleOn/Off (0x80235A58/0x80235A6C) set
       WpadWork.rumbleRequest[chan] (u8 at +0x44); wpadMain (0x80235914..)
       turns it into WPADControlMotor(chan, 1/0) and forces it off while the
       per-controller suppress byte (+0x1B00 of the r13-32136 work) is set;
     - the menu cursor's pulse (0x80242CB0): WPADControlMotor(0, 1) directly
       with a frame counter at +0x18 of the r13-32064 work (4..1 = on, then
       off at 0x80243B7C and a negative cooldown).
   Not covered: the Wii HOME Menu / reset paths (0x801A7xxx, 0x80319528),
   which this port replaces with the OpenSPM menu. */
#define R8PE01_WPADMGR_WP_PTR        UINT32_C(0x8056CB98) /* r13-32104 */
#define R8PE01_WPAD_RUMBLE_REQUEST   UINT32_C(0x44)
#define R8PE01_RUMBLE_SUPPRESS_WP    UINT32_C(0x8056CB78) /* r13-32136 */
#define R8PE01_RUMBLE_SUPPRESS       UINT32_C(0x1B00)
#define R8PE01_CURSOR_PULSE_WP       UINT32_C(0x8056CBC0) /* r13-32064 */
#define R8PE01_CURSOR_PULSE_COUNTER  UINT32_C(0x18)

static uint32_t g_v10_stage7_frames;
static uint32_t g_v10_stage7_captures;
static uint32_t g_v10_font_reported;
static uint32_t g_v10_kpad_reported;
static uint32_t g_v10_wpad_probe_reported;
static uint32_t g_v10_pointer_valid;
static uint32_t g_v10_pointer_reported;
static float g_v10_pointer_x;
static float g_v10_pointer_y;
static float g_v10_pointer_dx;
static float g_v10_pointer_dy;
static int g_v10_pointer_resolved;
static openspm_renderer_image_rect_fn g_v10_image_rect;
static openspm_renderer_pointer_stick_fn g_v10_pointer_stick;
static openspm_renderer_gamepad_rumble_fn g_v10_rumble;
static openspm_pointer_arbiter g_v10_arbiter;
static uint32_t g_v10_rumble_reported;

static const uint8_t g_v10_digits[10][7] = {
    {0x0E,0x11,0x13,0x15,0x19,0x11,0x0E}, /* 0 */
    {0x04,0x0C,0x04,0x04,0x04,0x04,0x0E}, /* 1 */
    {0x0E,0x11,0x01,0x02,0x04,0x08,0x1F}, /* 2 */
    {0x1E,0x01,0x01,0x0E,0x01,0x01,0x1E}, /* 3 */
    {0x02,0x06,0x0A,0x12,0x1F,0x02,0x02}, /* 4 */
    {0x1F,0x10,0x10,0x1E,0x01,0x01,0x1E}, /* 5 */
    {0x06,0x08,0x10,0x1E,0x11,0x11,0x0E}, /* 6 */
    {0x1F,0x01,0x02,0x04,0x08,0x08,0x08}, /* 7 */
    {0x0E,0x11,0x11,0x0E,0x11,0x11,0x0E}, /* 8 */
    {0x0E,0x11,0x11,0x0F,0x01,0x02,0x0C}  /* 9 */
};

static const uint8_t g_v10_upper[26][7] = {
    {0x0E,0x11,0x11,0x1F,0x11,0x11,0x11}, /* A */
    {0x1E,0x11,0x11,0x1E,0x11,0x11,0x1E}, /* B */
    {0x0F,0x10,0x10,0x10,0x10,0x10,0x0F}, /* C */
    {0x1E,0x11,0x11,0x11,0x11,0x11,0x1E}, /* D */
    {0x1F,0x10,0x10,0x1E,0x10,0x10,0x1F}, /* E */
    {0x1F,0x10,0x10,0x1E,0x10,0x10,0x10}, /* F */
    {0x0F,0x10,0x10,0x13,0x11,0x11,0x0F}, /* G */
    {0x11,0x11,0x11,0x1F,0x11,0x11,0x11}, /* H */
    {0x0E,0x04,0x04,0x04,0x04,0x04,0x0E}, /* I */
    {0x07,0x02,0x02,0x02,0x02,0x12,0x0C}, /* J */
    {0x11,0x12,0x14,0x18,0x14,0x12,0x11}, /* K */
    {0x10,0x10,0x10,0x10,0x10,0x10,0x1F}, /* L */
    {0x11,0x1B,0x15,0x15,0x11,0x11,0x11}, /* M */
    {0x11,0x19,0x15,0x13,0x11,0x11,0x11}, /* N */
    {0x0E,0x11,0x11,0x11,0x11,0x11,0x0E}, /* O */
    {0x1E,0x11,0x11,0x1E,0x10,0x10,0x10}, /* P */
    {0x0E,0x11,0x11,0x11,0x15,0x12,0x0D}, /* Q */
    {0x1E,0x11,0x11,0x1E,0x14,0x12,0x11}, /* R */
    {0x0F,0x10,0x10,0x0E,0x01,0x01,0x1E}, /* S */
    {0x1F,0x04,0x04,0x04,0x04,0x04,0x04}, /* T */
    {0x11,0x11,0x11,0x11,0x11,0x11,0x0E}, /* U */
    {0x11,0x11,0x11,0x11,0x11,0x0A,0x04}, /* V */
    {0x11,0x11,0x11,0x15,0x15,0x1B,0x11}, /* W */
    {0x11,0x11,0x0A,0x04,0x0A,0x11,0x11}, /* X */
    {0x11,0x11,0x0A,0x04,0x04,0x04,0x04}, /* Y */
    {0x1F,0x01,0x02,0x04,0x08,0x10,0x1F}  /* Z */
};

static uint8_t v10_punctuation_row(uint8_t ch, uint32_t row) {
    switch (ch) {
    case '!': return row < 5u ? 0x04u : (row == 6u ? 0x04u : 0u);
    case '"': return row < 2u ? 0x0Au : 0u;
    case '\'': return row < 2u ? 0x04u : 0u;
    case ',': return row == 5u ? 0x04u : (row == 6u ? 0x08u : 0u);
    case '-': return row == 3u ? 0x0Eu : 0u;
    case '.': return row == 6u ? 0x04u : 0u;
    case '/': return row < 5u ? (uint8_t)(1u << (4u - row)) : 0u;
    case ':': return row == 2u || row == 5u ? 0x04u : 0u;
    case '?': {
        static const uint8_t p[7] = {0x0E,0x11,0x01,0x02,0x04,0x00,0x04};
        return p[row];
    }
    default: return 0u;
    }
}

static uint8_t v10_glyph_row(uint8_t ch, uint32_t row) {
    if (row >= 7u || ch == ' ') return 0u;
    if (ch >= '0' && ch <= '9') return g_v10_digits[ch - '0'][row];
    if (ch >= 'a' && ch <= 'z') ch = (uint8_t)(ch - ('a' - 'A'));
    if (ch >= 'A' && ch <= 'Z') return g_v10_upper[ch - 'A'][row];
    if (ch >= 0x20u && ch <= 0x7Eu) return v10_punctuation_row(ch, row);
    return v10_punctuation_row('?', row);
}

static void v10_write_glyph_i2(uint8_t *sheet, uint32_t slot, uint8_t ch) {
    uint8_t *tile = sheet + slot * 16u; /* one 8x8 I2 tile */
    uint32_t y;
    memset(tile, 0, 16u);
    for (y = 0u; y < 7u; ++y) {
        const uint8_t row = v10_glyph_row(ch, y);
        uint32_t x;
        for (x = 0u; x < 5u; ++x) {
            if ((row & (uint8_t)(1u << (4u - x))) != 0u) {
                const uint32_t px = x + 1u;
                const uint32_t byte_index = y * 2u + px / 4u;
                const uint32_t shift = 6u - 2u * (px & 3u);
                tile[byte_index] |= (uint8_t)(3u << shift);
            }
        }
    }
}

static void v10_seed_open_ansi_font(void) {
    uint8_t *decoded = g_v8_ansi_font.rom + OPENSPM_ANSI_FONT_YAY0_DATA_OFFSET;
    uint8_t *sheet = decoded + OPENSPM_ANSI_FONT_IMAGE_OFFSET;
    uint32_t slot;

    memset(sheet, 0, OPENSPM_ANSI_FONT_IMAGE_BYTES);
    for (slot = 0u; slot < OPENSPM_ANSI_FONT_WIDTH_COUNT; ++slot) {
        const uint32_t code = 0x20u + slot;
        v10_write_glyph_i2(sheet, slot,
                           (uint8_t)(code <= 0x7Eu ? code : '?'));
    }
    if (!g_v10_font_reported) {
        backend_log(OPENSPM_LOG_INFO,
                    "M14 OpenSPM-owned ANSI OSFont replacement glyphs active; no Nintendo font artwork embedded.");
        g_v10_font_reported = 1u;
    }
}

static void v10_store_be_float(uint8_t *p, float value) {
    uint32_t bits;
    memcpy(&bits, &value, sizeof(bits));
    v8_store_be32(p, bits);
}

/* Renderer exports for the pointer and rumble (include/openspm/pointer_input.h).
   [Controller] enabled = 0 (OPENSPM_PAD=0) leaves the stick and rumble out. */
static void v10_resolve_pointer_exports(void) {
    const char *pad = getenv("OPENSPM_PAD");
    const int pad_on = !(pad != NULL && pad[0] == '0');
    HMODULE renderer;
    char message[256];
    if (g_v10_pointer_resolved) return;
    g_v10_pointer_resolved = 1;
    renderer = GetModuleHandleA("openspm_renderer.dll");
    if (renderer != NULL) {
        g_v10_image_rect = (openspm_renderer_image_rect_fn)(void *)GetProcAddress(
            renderer, OPENSPM_RENDERER_IMAGE_RECT_EXPORT);
        if (pad_on) {
            g_v10_pointer_stick = (openspm_renderer_pointer_stick_fn)(void *)GetProcAddress(
                renderer, OPENSPM_RENDERER_POINTER_STICK_EXPORT);
            g_v10_rumble = (openspm_renderer_gamepad_rumble_fn)(void *)GetProcAddress(
                renderer, OPENSPM_RENDERER_GAMEPAD_RUMBLE_EXPORT);
        }
    }
    snprintf(message, sizeof message,
             "OpenSPM Wii pointer: mouse over the %s; right-stick pointer %s; rumble %s.",
             g_v10_image_rect != NULL ? "game image (black bars are off-screen)" : "whole window (no image-rect export)",
             g_v10_pointer_stick != NULL ? "available ([Controller] stick_pointer)" : "unavailable",
             g_v10_rumble != NULL ? "forwarded to the gamepad ([Controller] rumble)" : "unavailable");
    backend_log(OPENSPM_LOG_INFO, message);
}

static void v10_latch_pointer(void) {
    POINT screen = {0, 0};
    POINT client;
    RECT rect;
    HWND window;
    DWORD process_id = 0u;
    const uint32_t previous_valid = g_v10_pointer_valid;
    const float previous_x = g_v10_pointer_x;
    const float previous_y = g_v10_pointer_y;
    uint32_t valid = 0u;
    float x = previous_x;
    float y = previous_y;
    int mouse_known = 0, mouse_valid = 0, stick_active = 0;
    float mouse_x = previous_x, mouse_y = previous_y, vx = 0.0f, vy = 0.0f;

    /* The OpenSPM menu is open (v9 menu filter): the mouse belongs to the
       menu, so the Wii pointer holds still. */
    if (g_v9_menu_open) {
        g_v10_pointer_dx = 0.0f;
        g_v10_pointer_dy = 0.0f;
        return;
    }
    v10_resolve_pointer_exports();

    if (GetCursorPos(&screen)) {
        mouse_known = 1;
        window = WindowFromPoint(screen);
        if (window != NULL) window = GetAncestor(window, GA_ROOT);
        if (window != NULL) {
            (void)GetWindowThreadProcessId(window, &process_id);
            client = screen;
            if (process_id == GetCurrentProcessId() &&
                GetClientRect(window, &rect) &&
                rect.right > rect.left && rect.bottom > rect.top &&
                ScreenToClient(window, &client) &&
                client.x >= rect.left && client.x < rect.right &&
                client.y >= rect.top && client.y < rect.bottom) {
                /* Map over the game image, not the window: with 4:3 / 16:9
                   fit the black bars are outside the Wii screen. */
                openspm_image_rect image;
                const int have_image = g_v10_image_rect != NULL &&
                                       g_v10_image_rect(&image.left, &image.top, &image.width, &image.height);
                mouse_valid = openspm_pointer_from_client(have_image ? &image : NULL,
                                                          (double)(client.x - rect.left),
                                                          (double)(client.y - rect.top),
                                                          (double)(rect.right - rect.left),
                                                          (double)(rect.bottom - rect.top), &mouse_x, &mouse_y);
            }
        }
    }
    if (g_v10_pointer_stick != NULL) stick_active = g_v10_pointer_stick(&vx, &vy);
    /* Right stick moves the pointer; moving the mouse takes it back. */
    valid = openspm_pointer_arbitrate(&g_v10_arbiter, mouse_known, (double)screen.x, (double)screen.y,
                                      mouse_valid, mouse_x, mouse_y, stick_active, vx, vy, &x, &y)
                ? 1u : 0u;
    if (!valid) {
        x = previous_x;
        y = previous_y;
    }

    g_v10_pointer_dx = valid && previous_valid ? x - previous_x : 0.0f;
    g_v10_pointer_dy = valid && previous_valid ? y - previous_y : 0.0f;
    g_v10_pointer_x = x;
    g_v10_pointer_y = y;
    g_v10_pointer_valid = valid;

    if (valid && !g_v10_pointer_reported) {
        backend_log(OPENSPM_LOG_INFO,
                    "M14 native Wii pointer active; PC mouse maps to frame-latched KPAD DPD position (Space/Z remains Wii A).");
        g_v10_pointer_reported = 1u;
    }
}

/* Once per frame: the game's controller-0 motor request -> the renderer (which
   applies [Controller] rumble / rumble_strength). Off while the menu is open. */
static void v10_forward_rumble(void) {
    uint32_t work = 0u, pulse = 0u;
    uint8_t request = 0u, suppressed = 0u;
    int on = 0;
    v10_resolve_pointer_exports();
    if (g_v10_rumble == NULL) return;
    if (!g_v9_menu_open) {
        if (v9_read_guest_u32(R8PE01_WPADMGR_WP_PTR, &work) && work != 0u)
            (void)v9_read_guest_u8(work + R8PE01_WPAD_RUMBLE_REQUEST, &request);
        work = 0u;
        if (v9_read_guest_u32(R8PE01_RUMBLE_SUPPRESS_WP, &work) && work != 0u)
            (void)v9_read_guest_u8(work + R8PE01_RUMBLE_SUPPRESS, &suppressed);
        work = 0u;
        if (v9_read_guest_u32(R8PE01_CURSOR_PULSE_WP, &work) && work != 0u)
            (void)v9_read_guest_u32(work + R8PE01_CURSOR_PULSE_COUNTER, &pulse);
        on = openspm_rumble_requested(request, suppressed, (int)(int32_t)pulse);
    }
    g_v10_rumble(on ? 1u : 0u);
    if (on && !g_v10_rumble_reported) {
        backend_log(OPENSPM_LOG_INFO, "M14 native Wii Remote rumble: the game switched the motor on; "
                                      "forwarded to the gamepad.");
        g_v10_rumble_reported = 1u;
    }
}

static int OPENSPM_CALL v10_run(void *backend_context,
                                const openspm_launch_desc *launch) {
    memset(&g_v8_hostcall, 0, sizeof(g_v8_hostcall));
    memset(&g_v8_ai, 0, sizeof(g_v8_ai));
    g_v8_sensor_bar_power = 0u;
    v8_build_ansi_font_rom();
    v10_seed_open_ansi_font();
    return v6_run(backend_context, launch);
}

static int v10_should_capture_stage7(uint32_t frame) {
    return frame == 1u || frame == 60u;
}

static int v10_handle_wpad_probe(openspm_m14_hostcall_state *state) {
    uint32_t controller;
    uint32_t type_address;
    uint8_t *type;

    if (state == NULL || state->header.struct_size < OPENSPM_M14_HOSTCALL_STATE_V1_SIZE)
        return OPENSPM_ERROR_INVALID_ARGUMENT;

    controller = state->gpr[3];
    type_address = state->gpr[4];
    if (type_address != 0u) {
        type = (uint8_t *)v8_guest_ptr(type_address, 4u);
        if (type == NULL) return OPENSPM_ERROR_NOT_FOUND;
        v8_store_be32(type, controller == 0u ? OPENSPM_WPAD_DEV_CORE
                                             : OPENSPM_WPAD_DEV_NOT_FOUND);
    }

    if (controller == 0u) {
        state->gpr[3] = (uint32_t)OPENSPM_WPAD_ERR_NONE;
        if (!g_v10_wpad_probe_reported) {
            backend_log(OPENSPM_LOG_INFO,
                        "M14 native WPADProbe active; controller 0 reports connected core Wii Remote.");
            g_v10_wpad_probe_reported = 1u;
        }
    } else {
        state->gpr[3] = UINT32_MAX; /* WPAD_ERR_NO_CONTROLLER == -1 */
    }
    state->pc = state->lr;
    return OPENSPM_OK;
}

static int v10_handle_kpad_read(openspm_m14_hostcall_state *state) {
    uint32_t controller;
    uint32_t dest_address;
    uint32_t max_statuses;
    uint8_t *status;

    if (state == NULL) return OPENSPM_ERROR_INVALID_ARGUMENT;
    controller = state->gpr[3];
    dest_address = state->gpr[4];
    max_statuses = state->gpr[5];

    if (controller >= 4u || max_statuses == 0u) {
        state->gpr[3] = 0u;
        state->pc = state->lr;
        return OPENSPM_OK;
    }

    status = (uint8_t *)v8_guest_ptr(dest_address, OPENSPM_KPAD_STATUS_SIZE);
    if (status == NULL) return OPENSPM_ERROR_NOT_FOUND;
    memset(status, 0, OPENSPM_KPAD_STATUS_SIZE);

    if (controller == 0u) {
        v8_store_be32(status + OPENSPM_KPAD_BUTTONS_HELD, g_v9_input_down);
        v8_store_be32(status + OPENSPM_KPAD_BUTTONS_PRESSED, g_v9_input_pressed);
        /* dev23 does not retain the prior-down mask after latching. Released
           stays neutral for this first exact transport frontier; held/pressed
           are the runtime-proven values SPM already consumes. */
        v8_store_be32(status + OPENSPM_KPAD_BUTTONS_RELEASED, 0u);
        if (g_v10_pointer_valid) {
            v10_store_be_float(status + OPENSPM_KPAD_POS_X, g_v10_pointer_x);
            v10_store_be_float(status + OPENSPM_KPAD_POS_Y, g_v10_pointer_y);
            v10_store_be_float(status + OPENSPM_KPAD_POS_DIFF_X, g_v10_pointer_dx);
            v10_store_be_float(status + OPENSPM_KPAD_POS_DIFF_Y, g_v10_pointer_dy);
            v10_store_be_float(status + OPENSPM_KPAD_POS_DIFF_MAG, 0.0f);
            v10_store_be_float(status + OPENSPM_KPAD_DIST, 1.0f);
            v10_store_be_float(status + OPENSPM_KPAD_DIST_DIFF, 0.0f);
            v10_store_be_float(status + OPENSPM_KPAD_DIST_DIFF_MAG, 0.0f);
            v10_store_be_float(status + OPENSPM_KPAD_DOWN_X, 0.0f);
            v10_store_be_float(status + OPENSPM_KPAD_DOWN_Y, 1.0f);
        }
        status[OPENSPM_KPAD_EXTENSION_TYPE] = 0u; /* plain Wii Remote */
        status[OPENSPM_KPAD_ERROR] = (uint8_t)OPENSPM_WPAD_ERR_NONE;
        status[OPENSPM_KPAD_DPD_STATUS] = g_v10_pointer_valid ? OPENSPM_KPAD_DPD_VALID : 0u;
        status[OPENSPM_KPAD_DATA_FORMAT] = 0u;
        if (!g_v10_kpad_reported) {
            backend_log(OPENSPM_LOG_INFO,
                        "M14 native KPAD transport active; controller 0 connected to frame-latched PC input.");
            g_v10_kpad_reported = 1u;
        }
    } else {
        status[OPENSPM_KPAD_ERROR] = (uint8_t)OPENSPM_WPAD_ERR_NO_CONTROLLER;
    }

    /* SPM asks for 16 buffered entries but only consumes however many KPADRead
       says are fresh. Supply one coherent snapshot per native input frame. */
    state->gpr[3] = 1u;
    state->pc = state->lr;
    return OPENSPM_OK;
}

static int OPENSPM_CALL v10_handle_hostcall(
    void *context,
    uint32_t target_address,
    openspm_m14_hostcall_state *state) {
    if (target_address == R8PE01_WPAD_PROBE)
        return v10_handle_wpad_probe(state);
    if (target_address == R8PE01_KPAD_READ)
        return v10_handle_kpad_read(state);

    if (target_address == OPENSPM_M14_HOST_EVENT_PRESENT_SAFEPOINT) {
        uint32_t seq = UINT32_MAX;
        uint32_t stage = UINT32_MAX;
        v10_latch_pointer();
        v10_forward_rumble();
        if (v9_read_guest_u32(R8PE01_M14_SEQ_WORK + 0u, &seq) &&
            v9_read_guest_u32(R8PE01_M14_SEQ_WORK + 4u, &stage) &&
            seq == 0u && stage == 7u) {
            ++g_v10_stage7_frames;
            if (g_v10_stage7_captures < 2u &&
                v10_should_capture_stage7(g_v10_stage7_frames) &&
                g_v6_bound_renderer.sync != NULL) {
                int result = g_v6_bound_renderer.sync(
                    g_v6_bound_renderer.context,
                    OPENSPM_M14_GX_SYNC_SEQLOAD_AUDIT);
                if (result != OPENSPM_OK) return result;
                ++g_v10_stage7_captures;
                {
                    char message[192];
                    snprintf(message, sizeof(message),
                             "M14 opening seq=0 stage=7 deep GX audit armed sample=%u stage7_frame=%u; DrawDone-bounded diagnostic only.",
                             g_v10_stage7_captures, g_v10_stage7_frames);
                    backend_log(OPENSPM_LOG_INFO, message);
                }
            }
        }
    }
    return v9_handle_hostcall(context, target_address, state);
}

static int OPENSPM_CALL v10_query_service(void *backend_context,
                                          const char *service_id,
                                          uint32_t requested_version,
                                          void *out_service) {
    int result = v9_query_service(backend_context, service_id,
                                  requested_version, out_service);
    if (result != OPENSPM_OK) return result;
    if (service_id != NULL &&
        strcmp(service_id, OPENSPM_M14_HOSTCALL_SERVICE_ID) == 0) {
        openspm_m14_hostcall_service *service =
            (openspm_m14_hostcall_service *)out_service;
        service->handle = v10_handle_hostcall;
    }
    return OPENSPM_OK;
}

OPENSPM_EXPORT int OPENSPM_CALL openspm_backend_query_v10(
    uint32_t requested_abi,
    const openspm_host_api *host,
    openspm_backend_api *out_api) {
    int result = openspm_backend_query(requested_abi, host, out_api);
    if (result != OPENSPM_OK) return result;

    g_v10_stage7_frames = 0u;
    g_v10_stage7_captures = 0u;
    g_v10_font_reported = 0u;
    g_v10_kpad_reported = 0u;
    g_v10_wpad_probe_reported = 0u;
    g_v10_pointer_valid = 0u;
    g_v10_pointer_reported = 0u;
    g_v10_pointer_x = 0.0f;
    g_v10_pointer_y = 0.0f;
    g_v10_pointer_dx = 0.0f;
    g_v10_pointer_dy = 0.0f;
    g_v10_pointer_resolved = 0;
    g_v10_image_rect = NULL;
    g_v10_pointer_stick = NULL;
    g_v10_rumble = NULL;
    memset(&g_v10_arbiter, 0, sizeof g_v10_arbiter);
    g_v10_rumble_reported = 0u;
    out_api->backend_version = "14.0-dev24";
    out_api->run = v10_run;
    out_api->query_service = v10_query_service;
    return OPENSPM_OK;
}