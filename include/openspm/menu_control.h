/* OpenSPM menu <-> backend control: HOME opens the menu, the game pauses while
 * it is open (docs/PLAYING.md "OpenSPM menu").
 *
 * The F1 menu lives in the renderer DLL; the backend owns input and the guest
 * frame loop. The renderer exports one function, looked up by the backend with
 * GetModuleHandleA/GetProcAddress like the gamepad and cheat exports:
 *
 *   int openspm_renderer_menu(unsigned op)
 *     OPENSPM_MENU_OP_STATE        -> OPENSPM_MENU_STATE_* bits
 *     OPENSPM_MENU_OP_HOME         HOME pressed (Guide / [Keyboard] home key):
 *                                  toggle the menu; returns 1
 *     OPENSPM_MENU_OP_PAUSE_FRAME  one paused frame: pump window events, present
 *                                  the last game image with the menu on top.
 *                                  Returns 1 while the game should stay paused,
 *                                  0 when the menu closed, the window is closing,
 *                                  or a game frame is still open in the renderer
 *                                  (then the backend retries at the next frame)
 *     OPENSPM_MENU_OP_KEYMAP_GEN   generation of the live [Keyboard] map (0 = the
 *                                  map from startup; bumps when the menu saves)
 *   int openspm_renderer_keyboard_map(char *out, unsigned capacity)
 *                                  copies the live map text, returns its generation
 *
 * All calls happen on the game thread at the backend's PRESENT_SAFEPOINT, the
 * same thread that pumps the SDL window and presents, so nothing races.
 *
 * Pause and guest time: the native module derives the Wii timebase and VI
 * retrace deadlines from host->monotonic_ns (tools/native_bridge
 * legacy_c_adapter_v9.c.in). While paused the backend holds the safepoint and
 * hands the module a clock that stands still (openspm_pause_clock below), so on
 * resume the timebase, retraces, audio and timers continue exactly where they
 * stopped: nothing fast-forwards. With [Overlay] pause_game = 0 the module
 * keeps the launcher's clock and no frame is ever held.
 *
 * The same clock runs faster while fast-forward ("turbo") is engaged; see
 * guest_clock.h and OPENSPM_RENDERER_TURBO_EXPORT below.
 *
 * Portable C: the clock is host unit-tested in tests/m14_keymap_test.c and
 * tests/m14_guest_clock_test.c. */
#ifndef OPENSPM_MENU_CONTROL_H
#define OPENSPM_MENU_CONTROL_H

#include <stdint.h>

#include "openspm/abi.h"
#include "openspm/guest_clock.h"

#ifdef __cplusplus
extern "C" {
#endif

#define OPENSPM_RENDERER_MENU_EXPORT "openspm_renderer_menu"
#define OPENSPM_RENDERER_KEYBOARD_MAP_EXPORT "openspm_renderer_keyboard_map"
typedef int(OPENSPM_CALL *openspm_renderer_menu_fn)(unsigned op);
typedef int(OPENSPM_CALL *openspm_renderer_keyboard_map_fn)(char *out, unsigned capacity);

enum {
    OPENSPM_MENU_OP_STATE = 0,
    OPENSPM_MENU_OP_HOME = 1,
    OPENSPM_MENU_OP_PAUSE_FRAME = 2,
    OPENSPM_MENU_OP_KEYMAP_GEN = 3
};

enum { OPENSPM_MENU_STATE_OPEN = 1 };

/* Fast-forward ("turbo", docs/PLAYING.md "Fast-forward"). The backend owns the
   hotkey and the guest clock; it tells the renderer when turbo starts, changes
   speed or stops (game thread, at PRESENT_SAFEPOINT), so the renderer can
   present at most one image per display refresh, bypass the present timeline /
   frame interpolation, drop audio and show the overlay indicator:
     int openspm_renderer_turbo(unsigned speed)
       speed 0 = off (normal play), 2..4 = N x, OPENSPM_TURBO_UNLIMITED
   Never called while turbo is not engaged. */
#define OPENSPM_RENDERER_TURBO_EXPORT "openspm_renderer_turbo"
#define OPENSPM_TURBO_UNLIMITED 0xFFFFu
typedef int(OPENSPM_CALL *openspm_renderer_turbo_fn)(unsigned speed);

/* Guest clock that stands still while paused: the pause is rate 0 of the
   rate-scaled guest clock (include/openspm/guest_clock.h), which also carries
   the turbo speed. These names are the pause-only view the menu uses. */
typedef openspm_guest_clock openspm_pause_clock;

static inline uint64_t openspm_pause_clock_now(const openspm_pause_clock *clock, uint64_t host_now) {
    return openspm_guest_clock_now(clock, host_now);
}

static inline void openspm_pause_clock_pause(openspm_pause_clock *clock, uint64_t host_now) {
    openspm_guest_clock_pause(clock, host_now);
}

static inline void openspm_pause_clock_resume(openspm_pause_clock *clock, uint64_t host_now) {
    openspm_guest_clock_resume(clock, host_now);
}

#ifdef __cplusplus
}
#endif

#endif
