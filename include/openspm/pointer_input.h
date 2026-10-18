/* OpenSPM Wii Remote pointer (IR / DPD) and rumble plumbing.
 *
 * The backend feeds the game's KPADRead with a frame-latched pointer position
 * (backend/native/openspm_native_backend_v10.c). Two sources drive it:
 *
 *   - the mouse, mapped over the game IMAGE inside the window, not the whole
 *     client area: with [Display] aspect = 4:3 (or widescreen 16:9) the picture
 *     is pillarboxed / letterboxed and the black bars are not part of the Wii
 *     screen. Outside the image the pointer is off-screen (DPD invalid), like a
 *     Wii Remote pointed past the TV. The renderer reports the current image
 *     rectangle (it follows window resizes, F11 and live aspect changes);
 *   - the gamepad's right stick ([Controller] stick_pointer, pointer_speed,
 *     pointer_deadzone): it moves the pointer relative to where it is; moving
 *     the mouse hands the pointer back to the mouse.
 *
 * Rumble: the backend reads the game's rumble request once per frame and hands
 * on/off to the renderer, which drives the SDL gamepad's motors with [Controller]
 * rumble / rumble_strength (applied live from the F1 menu).
 *
 * Renderer exports (looked up with GetModuleHandleA/GetProcAddress like the
 * gamepad and menu exports; all calls on the game thread at PRESENT_SAFEPOINT):
 *
 *   int openspm_renderer_image_rect(float *left, float *top, float *width, float *height)
 *       the game image inside the window as fractions (0..1) of the window's
 *       pixel size; returns 1 when valid
 *   int openspm_renderer_pointer_stick(float *vx, float *vy)
 *       right-stick pointer velocity for this frame in pointer units (the
 *       screen is -1..1 on both axes, y down), after deadzone / speed; returns
 *       1 while the stick is deflected and stick_pointer is on
 *   void openspm_renderer_gamepad_rumble(unsigned on)
 *       the game wants the Wii Remote motor on (1) or off (0)
 *
 * Portable C (no Windows headers): host unit-tested in tests/m14_pointer_input_test.c. */
#ifndef OPENSPM_POINTER_INPUT_H
#define OPENSPM_POINTER_INPUT_H

#include <math.h>
#include <stddef.h>

#include "openspm/abi.h"

#ifdef __cplusplus
extern "C" {
#endif

#define OPENSPM_RENDERER_IMAGE_RECT_EXPORT "openspm_renderer_image_rect"
#define OPENSPM_RENDERER_POINTER_STICK_EXPORT "openspm_renderer_pointer_stick"
#define OPENSPM_RENDERER_GAMEPAD_RUMBLE_EXPORT "openspm_renderer_gamepad_rumble"
typedef int(OPENSPM_CALL *openspm_renderer_image_rect_fn)(float *left, float *top, float *width, float *height);
typedef int(OPENSPM_CALL *openspm_renderer_pointer_stick_fn)(float *vx, float *vy);
typedef void(OPENSPM_CALL *openspm_renderer_gamepad_rumble_fn)(unsigned on);

/* Defaults of the [Controller] settings (percent). */
#define OPENSPM_POINTER_SPEED_DEFAULT 50
#define OPENSPM_POINTER_DEADZONE_DEFAULT 20
#define OPENSPM_RUMBLE_STRENGTH_DEFAULT 70

/* The game image inside the window, in fractions of the window size. */
typedef struct openspm_image_rect {
    float left, top, width, height;
} openspm_image_rect;

/* Content of content_w x content_h scaled to fit a surface_w x surface_h window
   and centred (Aurora's present viewport: letterbox or pillarbox bars).
   Invalid sizes give the whole window. */
static inline openspm_image_rect openspm_image_rect_fit(double surface_w, double surface_h, double content_w,
                                                 double content_h) {
    openspm_image_rect rect = {0.f, 0.f, 1.f, 1.f};
    double w, h;
    if (!(surface_w > 0.0 && surface_h > 0.0 && content_w > 0.0 && content_h > 0.0)) return rect;
    w = surface_w;
    h = surface_w * content_h / content_w;
    if (h > surface_h) {
        h = surface_h;
        w = surface_h * content_w / content_h;
        if (w > surface_w) w = surface_w;
    }
    rect.width = (float)(w / surface_w);
    rect.height = (float)(h / surface_h);
    rect.left = (float)((surface_w - w) * 0.5 / surface_w);
    rect.top = (float)((surface_h - h) * 0.5 / surface_h);
    return rect;
}

/* A client-area pixel (cx, cy) of a client_w x client_h window -> pointer
   position over the game image, -1..1 on both axes (y down; pixel centres).
   Returns 1 inside the image, 0 over the bars / outside the window (the
   outputs then hold the position clamped to the image edge). A NULL or empty
   rect means the image fills the window (the mapping before the fix). */
static inline int openspm_pointer_from_client(const openspm_image_rect *rect, double cx, double cy, double client_w,
                                       double client_h, float *out_x, float *out_y) {
    double left = 0.0, top = 0.0, width = 1.0, height = 1.0, u, v;
    int inside;
    if (!(client_w > 0.0 && client_h > 0.0)) {
        *out_x = 0.f;
        *out_y = 0.f;
        return 0;
    }
    if (rect != NULL && rect->width > 0.f && rect->height > 0.f) {
        left = rect->left;
        top = rect->top;
        width = rect->width;
        height = rect->height;
    }
    u = ((cx + 0.5) / client_w - left) / width;  /* 0..1 across the image */
    v = ((cy + 0.5) / client_h - top) / height;
    inside = u >= 0.0 && u < 1.0 && v >= 0.0 && v < 1.0;
    if (u < 0.0) u = 0.0;
    if (u > 1.0) u = 1.0;
    if (v < 0.0) v = 0.0;
    if (v > 1.0) v = 1.0;
    *out_x = (float)(u * 2.0 - 1.0);
    *out_y = (float)(v * 2.0 - 1.0);
    return inside;
}

/* Right stick (-32768..32767, up positive) -> pointer velocity per game frame.
   Radial deadzone (percent of full tilt, clamped to 0..90), then a squared
   response curve for fine aiming; full tilt moves speed_pct * 0.0008 pointer
   units per frame (default 50: 0.04/frame, about 0.8 s across the screen at
   60 frames/s). Returns 1 outside the deadzone. */
static inline int openspm_stick_pointer_velocity(int rx, int ry, int speed_pct, int deadzone_pct, float *vx, float *vy) {
    const double full = 32767.0;
    double x = (double)rx / full, y = (double)ry / full, mag, dz, t, scale;
    *vx = 0.f;
    *vy = 0.f;
    if (x < -1.0) x = -1.0;
    if (y < -1.0) y = -1.0;
    mag = sqrt(x * x + y * y);
    if (deadzone_pct < 0) deadzone_pct = 0;
    if (deadzone_pct > 90) deadzone_pct = 90;
    if (speed_pct < 1) speed_pct = 1;
    if (speed_pct > 100) speed_pct = 100;
    dz = deadzone_pct / 100.0;
    if (mag <= dz || mag <= 0.0) return 0;
    t = (mag > 1.0 ? 1.0 : mag);
    t = (t - dz) / (1.0 - dz);
    scale = t * t * speed_pct * 0.0008 / mag;
    *vx = (float)(x * scale);
    *vy = (float)(-y * scale); /* stick up = pointer up (pointer y grows downwards) */
    return 1;
}

/* Which source owns the pointer. The stick takes over when it leaves its
   deadzone (starting from the current pointer, or the centre when there is
   none) and keeps the pointer where it left it; the mouse takes it back as soon
   as it moves by a pixel. */
typedef struct openspm_pointer_arbiter {
    int stick_mode;
    int have_mouse;
    double mouse_x, mouse_y; /* last client position seen */
    float x, y;              /* stick-driven position */
} openspm_pointer_arbiter;

/* mouse_known: the cursor position (mx, my, any consistent pixel space) could
   be read; mouse_valid/mouse_px/mouse_py: the mapped mouse pointer.
   stick_active/vx/vy: openspm_stick_pointer_velocity's result. Writes the
   pointer the game sees; returns 1 when it is on screen. */
static inline int openspm_pointer_arbitrate(openspm_pointer_arbiter *a, int mouse_known, double mx, double my,
                                     int mouse_valid, float mouse_px, float mouse_py, int stick_active, float vx,
                                     float vy, float *out_x, float *out_y) {
    int mouse_moved = 0;
    if (mouse_known) {
        mouse_moved = !a->have_mouse || fabs(mx - a->mouse_x) >= 1.0 || fabs(my - a->mouse_y) >= 1.0;
        a->have_mouse = 1;
        a->mouse_x = mx;
        a->mouse_y = my;
    }
    if (stick_active) {
        if (!a->stick_mode) {
            a->stick_mode = 1;
            a->x = mouse_valid ? mouse_px : 0.f;
            a->y = mouse_valid ? mouse_py : 0.f;
        }
        a->x += vx;
        a->y += vy;
        if (a->x < -1.f) a->x = -1.f;
        if (a->x > 1.f) a->x = 1.f;
        if (a->y < -1.f) a->y = -1.f;
        if (a->y > 1.f) a->y = 1.f;
    } else if (mouse_moved && a->stick_mode) {
        a->stick_mode = 0;
    }
    if (a->stick_mode) {
        *out_x = a->x;
        *out_y = a->y;
        return 1;
    }
    *out_x = mouse_px;
    *out_y = mouse_py;
    return mouse_valid;
}

/* Rumble strength (percent) -> SDL motor intensity 0..0xFFFF. */
static inline unsigned openspm_rumble_intensity(int strength_pct) {
    if (strength_pct <= 0) return 0u;
    if (strength_pct >= 100) return 0xFFFFu;
    return (unsigned)(strength_pct * 0xFFFF / 100);
}

/* The game's Wii Remote motor request for controller 0, from the two R8PE01
   (rev 2) sources that switch it (see openspm_native_backend_v10.c):
     wpadmgr     rumble_request (1 = on) unless the game's per-controller
                 rumble-suppress flag is set (then wpadMain forces it off);
     ui tick     the menu cursor's short pulse counter (> 0 = on, it counts
                 down to 0, then a negative cooldown). */
static inline int openspm_rumble_requested(unsigned wpad_request, unsigned suppressed, int ui_tick_counter) {
    return (wpad_request == 1u && suppressed == 0u) || ui_tick_counter > 0;
}

#ifdef __cplusplus
}
#endif

#endif
