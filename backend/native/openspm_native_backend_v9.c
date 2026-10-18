/*
 * M14 standalone-native backend dev23 presentation/input/transport layer.
 *
 * dev21 remains the runtime-proven Wii/platform behavior underneath this file.
 * Module dev16 supplies a scheduler-idle presentation safepoint once per native
 * retrace epoch. Keep that proven renderer seam, and use the same once-per-frame
 * boundary to latch PC input exactly like SPM's wpad manager does: the real
 * wpadGetButtonsPressed() is only a getter for a frame-stable buttonsPressed
 * field, so every caller in one simulation frame must observe the same edge.
 *
 * The first natural title-screen proof also showed tens of millions of tiny
 * Broadway write-gather stores. Expose a separate OpenSPM-native bulk GX FIFO
 * service so the player-local module can gather one 32-byte Broadway line before
 * crossing the DLL/service boundary. This does not expand the Wii CPU service
 * ABI and does not change GX bytes or semantic parser boundaries.
 *
 * The first input-responsive run reaches SEQ_LOAD, but the file-select screen is
 * visually incomplete. Keep behavior unchanged and sample SPM's own file-select
 * work/file/TPL state at scheduler-idle safepoints, while tracing the existing
 * hosted GX display-list begin/end seams. Once that state is demonstrably stable,
 * arm the renderer's bounded metadata-only GX audit exactly once. The audit sync
 * is diagnostic-only: it must not drain, submit, present, or clear a GX frame.
 */

#define openspm_backend_query openspm_backend_query_v8_internal
#include "openspm_native_backend_v8.c"
#undef openspm_backend_query

#include "openspm/gx_fifo_service.h"
#include "openspm/m14_gx_audit.h"

#define R8PE01_M14_INPUT_PRESSED_TARGET UINT32_C(0x80235AB8)
#define R8PE01_M14_SEQ_WORK             UINT32_C(0x804D0D60)
#define R8PE01_M14_GP_PTR               UINT32_C(0x8056CB78)
#define R8PE01_M14_SEQ_LOAD_SUB_WP      UINT32_C(0x8056CAD0)
#define R8PE01_M14_GX_BEGIN_DISPLAY_LIST UINT32_C(0x80291DC8)
#define R8PE01_M14_GX_END_DISPLAY_LIST   UINT32_C(0x80291E78)
#define R8PE01_M14_SEQ_LOAD             UINT32_C(5)
#define R8PE01_M14_TPL_VERSION          UINT32_C(0x0020AF30)

/* Raw WPAD button bits from the Revolution controller contract. Keep these
 * local to the keyboard adapter: KPAD receives the exact same button mask that
 * a sideways Wii Remote would expose to SPM. */
#define OPENSPM_WPAD_BTN_LEFT  UINT32_C(0x00000001)
#define OPENSPM_WPAD_BTN_RIGHT UINT32_C(0x00000002)
#define OPENSPM_WPAD_BTN_DOWN  UINT32_C(0x00000004)
#define OPENSPM_WPAD_BTN_UP    UINT32_C(0x00000008)
#define OPENSPM_WPAD_BTN_2     UINT32_C(0x00000100)
#define OPENSPM_WPAD_BTN_1     UINT32_C(0x00000200)
#define OPENSPM_WPAD_BTN_B     UINT32_C(0x00000400)
#define OPENSPM_WPAD_BTN_A     UINT32_C(0x00000800)
#define OPENSPM_WPAD_BTN_MINUS UINT32_C(0x00001000)

#define OPENSPM_V9_GX_CMD_CP_LOAD      UINT8_C(0x08)
#define OPENSPM_V9_GX_CMD_XF_LOAD      UINT8_C(0x10)
#define OPENSPM_V9_GX_CMD_INDX_A       UINT8_C(0x20)
#define OPENSPM_V9_GX_CMD_INDX_B       UINT8_C(0x28)
#define OPENSPM_V9_GX_CMD_INDX_C       UINT8_C(0x30)
#define OPENSPM_V9_GX_CMD_INDX_D       UINT8_C(0x38)
#define OPENSPM_V9_GX_CMD_CALL_DL      UINT8_C(0x40)
#define OPENSPM_V9_GX_CMD_INVAL_VTX    UINT8_C(0x48)
#define OPENSPM_V9_GX_CMD_BP_LOAD      UINT8_C(0x61)
#define OPENSPM_V9_GX_MAX_DL_DEPTH     UINT32_C(32)

static uint32_t g_v9_present_safepoint_reported;
static uint64_t g_v9_input_frame;
static uint32_t g_v9_input_down;
static uint32_t g_v9_input_pressed;
static uint64_t g_v9_input_reported_frame;
static uint64_t g_v9_file_select_samples;
static uint64_t g_v9_display_list_begins;
static uint64_t g_v9_display_list_ends;
static uint64_t g_v9_fifo_submit_failures;
static uint32_t g_v9_seqload_audit_armed;
static uint32_t g_v9_last_display_list_address;
static uint32_t g_v9_fifo_framer_reported;
static uint64_t g_v9_fifo_semantic_draw_done;

static int v9_should_log_count(uint64_t value) {
    return value <= UINT64_C(8) ||
           (value != 0u && (value & (value - UINT64_C(1))) == 0u);
}

static uint64_t v9_hash_bytes(const uint8_t *bytes, uint32_t size) {
    uint64_t hash = UINT64_C(1469598103934665603);
    uint32_t i;
    if (bytes == NULL || size == 0u) return 0u;
    for (i = 0u; i < size; ++i) {
        hash ^= bytes[i];
        hash *= UINT64_C(1099511628211);
    }
    return hash;
}

#include "openspm_pad_xinput.h"
#include "openspm/cheats.h"
#include "openspm/keymap.h"
#include "openspm/menu_control.h"
#include "openspm/guest_memory.h"
#include "openspm/guest_call.h"
#include "openspm/input_service.h"
#include "openspm/runtime_service.h"
#include "openspm/content_roster.h"
#include "openspm/content_followers.h"
#include "openspm/content_enemies.h"
#include "openspm/content_maps.h"
#include "openspm/content_messages.h"
#include "openspm/game_state.h"
#include "openspm/hooks.h"

/* GetAsyncKeyState is global: without this, typing in another application
   moves Mario. Keyboard input counts only while one of this process's windows
   is in the foreground; OPENSPM_INPUT_BACKGROUND=1 restores global input. */
static int v9_keyboard_focus_ok(void) {
    static int background = -1;
    HWND foreground;
    DWORD pid = 0;
    if (background < 0) {
        const char *value = getenv("OPENSPM_INPUT_BACKGROUND");
        background = value != NULL && value[0] == '1';
    }
    if (background) return 1;
    foreground = GetForegroundWindow();
    if (foreground == NULL) return 0;
    (void)GetWindowThreadProcessId(foreground, &pid);
    return pid == GetCurrentProcessId();
}

/* Keyboard map: OpenSPM.ini [Keyboard] map (OPENSPM_KEY_MAP, parsed by
   include/openspm/keymap.h over the default Arrows/WASD, Space/Z = A, C = B,
   X = 1, Enter = 2, Backspace = Minus, Home = HOME). The F1 menu's Controls
   tab can rebind keys live: it exports the saved map with a generation number,
   and the latch below re-reads it when the generation changes. */
static openspm_keymap g_v9_keymap;
static int g_v9_keymap_loaded;
static int g_v9_keymap_generation;

static int v9_vk_down(void *context, unsigned vk) {
    (void)context;
    return (GetAsyncKeyState((int)vk) & 0x8000) != 0;
}

static void v9_keymap_apply(const char *text, const char *origin) {
    char message[400], formatted[320];
    const int invalid = openspm_keymap_parse(text, &g_v9_keymap);
    (void)openspm_keymap_format(&g_v9_keymap, formatted, sizeof formatted);
    snprintf(message, sizeof message, "OpenSPM keyboard (%s): %s%s", origin, formatted,
             invalid ? " (some [Keyboard] map entries ignored)" : "");
    backend_log(OPENSPM_LOG_INFO, message);
}

static uint32_t v9_keyboard_down(void) {
    uint32_t down = openspm_pad_down(); /* XInput controller, same raw WPAD bits */
    if (!g_v9_keymap_loaded) {
        g_v9_keymap_loaded = 1;
        v9_keymap_apply(getenv("OPENSPM_KEY_MAP"), "startup");
    }
    if (!v9_keyboard_focus_ok()) return down;

    /* SPM reads a Wii Remote held sideways; the map's screen-direction slots
       already rotate to the raw WPAD D-pad bits (see keymap.h). HOME comes
       back as OPENSPM_KEYMAP_WPAD_HOME and is stripped by the menu filter. */
    return down | openspm_keymap_down(&g_v9_keymap, v9_vk_down, NULL);
}

/* OpenSPM menu control (include/openspm/menu_control.h). HOME (gamepad Guide
   or the [Keyboard] home key) toggles the F1 menu when [Controller]
   home_opens_menu is on (OPENSPM_HOME_MENU, default 1) and never reaches the
   game either way: the Wii HOME Menu is a separate SDK library this port does
   not provide, and before this the backend never produced the HOME bit at all.
   While the menu is open the game receives no buttons. */
static int g_v9_menu_resolved;
static openspm_renderer_menu_fn g_v9_menu;
static openspm_renderer_keyboard_map_fn g_v9_menu_keymap;
static int g_v9_home_menu;
static uint32_t g_v9_home_prev;
static uint32_t g_v9_menu_open;

static void v9_menu_resolve(void) {
    const char *home = getenv("OPENSPM_HOME_MENU");
    HMODULE renderer;
    if (g_v9_menu_resolved) return;
    g_v9_menu_resolved = 1;
    g_v9_home_menu = !(home != NULL && home[0] == '0');
    renderer = GetModuleHandleA("openspm_renderer.dll");
    if (renderer != NULL) {
        g_v9_menu = (openspm_renderer_menu_fn)(void *)GetProcAddress(renderer, OPENSPM_RENDERER_MENU_EXPORT);
        g_v9_menu_keymap = (openspm_renderer_keyboard_map_fn)(void *)GetProcAddress(
            renderer, OPENSPM_RENDERER_KEYBOARD_MAP_EXPORT);
    }
    backend_log(OPENSPM_LOG_INFO,
                g_v9_menu == NULL ? "OpenSPM menu control: unavailable (renderer has no menu export)."
                : g_v9_home_menu  ? "OpenSPM menu control: HOME (gamepad Guide / [Keyboard] home key) opens the menu; "
                                    "game input is held while it is open."
                                  : "OpenSPM menu control: HOME disabled ([Controller] home_opens_menu=0); game input "
                                    "is held while the menu is open.");
}

static void v9_keymap_poll_live(void) {
    char text[512];
    int generation;
    if (g_v9_menu == NULL || g_v9_menu_keymap == NULL) return;
    generation = g_v9_menu(OPENSPM_MENU_OP_KEYMAP_GEN);
    if (generation == g_v9_keymap_generation) return;
    text[0] = '\0';
    generation = g_v9_menu_keymap(text, (unsigned)sizeof text);
    g_v9_keymap_generation = generation;
    v9_keymap_apply(text, "changed in the OpenSPM menu");
}

/* Fast-forward hotkey ([Keyboard] turbo key, gamepad TURBO button), sampled
   by the filter below; v9_turbo_update() acts on it once per frame. */
static uint32_t g_v9_turbo_held;
/* The Wii Remote shake ([Keyboard] shake key, gamepad SHAKE button): frames
   left of the accelerometer swing v12 writes into the KPAD status (a tap
   gives one whole swing, holding keeps it going), and the swing's phase. */
static uint32_t g_v9_shake_frames, g_v9_shake_phase;
#define OPENSPM_V9_SHAKE_SWING_FRAMES 16u

/* Physical input -> what the game may see. */
static uint32_t v9_menu_filter(uint32_t down) {
    const uint32_t home = down & OPENSPM_KEYMAP_WPAD_HOME;
    g_v9_turbo_held = (down & OPENSPM_KEYMAP_WPAD_TURBO) != 0u ? 1u : 0u;
    if (down & OPENSPM_KEYMAP_WPAD_SHAKE) {
        if (g_v9_shake_frames == 0u) g_v9_shake_phase = 0u;
        g_v9_shake_frames = OPENSPM_V9_SHAKE_SWING_FRAMES;
    } else if (g_v9_shake_frames != 0u) {
        --g_v9_shake_frames;
    }
    if (g_v9_shake_frames != 0u) ++g_v9_shake_phase;
    down &= ~OPENSPM_KEYMAP_WPAD_HOME; /* HOME never reaches the game */
    down &= ~OPENSPM_KEYMAP_WPAD_TURBO; /* neither does the turbo hotkey */
    down &= ~OPENSPM_KEYMAP_WPAD_SHAKE; /* nor the shake: it is motion, below */
    v9_menu_resolve();
    if (g_v9_menu == NULL) return down;
    if (home != 0u && g_v9_home_prev == 0u && g_v9_home_menu) (void)g_v9_menu(OPENSPM_MENU_OP_HOME);
    g_v9_home_prev = home;
    v9_keymap_poll_live();
    g_v9_menu_open = (g_v9_menu(OPENSPM_MENU_OP_STATE) & OPENSPM_MENU_STATE_OPEN) != 0 ? 1u : 0u;
    return g_v9_menu_open ? 0u : down;
}

/* Pause while the menu is open ([Overlay] pause_game, OPENSPM_PAUSE_GAME,
   default 1). The native module gets a copy of the launcher's host API whose
   monotonic_ns stands still while paused (installed through v2's
   g_native_runtime_host_filter when the module is created), so the Wii
   timebase, VI retraces and everything timed from them resume where they
   stopped. With pause_game=0 the module keeps the launcher's host untouched
   and no frame is ever held (unless fast-forward below needs the clock).

   The same clock is the rate-scaled guest clock of fast-forward ("turbo",
   include/openspm/guest_clock.h): while the hotkey is engaged it runs N x as
   fast, so the module's VI deadlines, idle waits, timebase and audio clock all
   run N x without any change in the module; "unlimited" additionally lets the
   module skip its idle waits (host clock_skip_to). Released: 1x again,
   continuing from the current guest time (no jump). Never engaged: the clock
   is the identity and nothing differs from the launcher's clock. */
static int g_v9_pause_enabled;
static int g_v9_clock_installed;
static const openspm_host_api *g_v9_real_host;
static openspm_host_api g_v9_clock_host;
static openspm_pause_clock g_v9_pause_clock;

/* [Game] turbo = 1|0 (OPENSPM_TURBO, default 1: the hotkey works),
   turbo_speed = 2|3|4|unlimited (OPENSPM_TURBO_SPEED, default 2),
   turbo_mode = hold|toggle (OPENSPM_TURBO_MODE, default hold). */
static int g_v9_turbo_enabled;
static int g_v9_turbo_toggle;
static unsigned g_v9_turbo_speed;
static uint32_t g_v9_turbo_prev;
static int g_v9_turbo_latched;
static unsigned g_v9_turbo_active; /* engaged speed, 0 = normal play */
static int g_v9_turbo_resolved;
static openspm_renderer_turbo_fn g_v9_renderer_turbo;
static uint64_t g_v9_turbo_engages;
static uint64_t g_v9_turbo_frames;
static uint64_t g_v9_turbo_started_ns;
static uint64_t g_v9_turbo_guest_started_ns;

static uint64_t v9_real_ns(void) {
    return g_v9_real_host->monotonic_ns(g_v9_real_host->context);
}

static uint64_t OPENSPM_CALL v9_paused_monotonic_ns(void *context) {
    (void)context;
    return openspm_pause_clock_now(&g_v9_pause_clock, v9_real_ns());
}

/* The module is idle until target_ns (its next VI deadline). Called on the
   game thread, which owns the clock. */
static int OPENSPM_CALL v9_clock_skip_to(void *context, uint64_t target_ns) {
    (void)context;
    return openspm_guest_clock_skip_to(&g_v9_pause_clock, v9_real_ns(), target_ns);
}

static int v9_ascii_iequal(const char *a, const char *b) {
    if (a == NULL) return 0;
    while (*a != '\0' && *b != '\0') {
        char x = *a++, y = *b++;
        if (x >= 'A' && x <= 'Z') x = (char)(x - 'A' + 'a');
        if (y >= 'A' && y <= 'Z') y = (char)(y - 'A' + 'a');
        if (x != y) return 0;
    }
    return *a == *b;
}

static void v9_turbo_configure(void) {
    const char *value = getenv("OPENSPM_TURBO");
    const char *speed = getenv("OPENSPM_TURBO_SPEED");
    g_v9_turbo_enabled = !(value != NULL && value[0] == '0');
    g_v9_turbo_speed = 2u;
    if (speed != NULL && speed[0] >= '2' && speed[0] <= '4' && speed[1] == '\0')
        g_v9_turbo_speed = (unsigned)(speed[0] - '0');
    else if (v9_ascii_iequal(speed, "unlimited"))
        g_v9_turbo_speed = OPENSPM_TURBO_UNLIMITED;
    g_v9_turbo_toggle = v9_ascii_iequal(getenv("OPENSPM_TURBO_MODE"), "toggle");
}

static const openspm_host_api *v9_runtime_host(const openspm_host_api *host) {
    const char *value = getenv("OPENSPM_PAUSE_GAME");
    HMODULE renderer = GetModuleHandleA("openspm_renderer.dll");
    size_t size;
    int pause = 1;
    char message[256];
    g_v9_pause_enabled = 0;
    g_v9_clock_installed = 0;
    v9_turbo_configure();
    if (value != NULL && value[0] == '0') {
        backend_log(OPENSPM_LOG_INFO, "OpenSPM menu pause: off ([Overlay] pause_game=0); the game keeps running.");
        pause = 0;
    } else if (host == NULL || host->monotonic_ns == NULL || renderer == NULL ||
               GetProcAddress(renderer, OPENSPM_RENDERER_MENU_EXPORT) == NULL) {
        backend_log(OPENSPM_LOG_INFO, "OpenSPM menu pause: unavailable (no host clock or renderer menu export).");
        pause = 0;
    }
    if (!g_v9_turbo_enabled) backend_log(OPENSPM_LOG_INFO, "OpenSPM turbo: off ([Game] turbo=0).");
    if ((!pause && !g_v9_turbo_enabled) || host == NULL || host->monotonic_ns == NULL) return host;
    size = host->header.struct_size < sizeof g_v9_clock_host ? host->header.struct_size : sizeof g_v9_clock_host;
    memset(&g_v9_clock_host, 0, sizeof g_v9_clock_host);
    memcpy(&g_v9_clock_host, host, size);
    memset(&g_v9_pause_clock, 0, sizeof g_v9_pause_clock); /* identity until paused or turbo */
    g_v9_real_host = host;
    g_v9_clock_host.monotonic_ns = v9_paused_monotonic_ns;
    if (g_v9_turbo_enabled) g_v9_clock_host.clock_skip_to = v9_clock_skip_to;
    g_v9_clock_host.header.struct_size = (uint32_t)sizeof g_v9_clock_host;
    g_v9_pause_enabled = pause;
    g_v9_clock_installed = 1;
    if (pause)
        backend_log(OPENSPM_LOG_INFO,
                    "OpenSPM menu pause: on; the game and its clock stop at the frame safepoint while the menu is open.");
    if (g_v9_turbo_enabled) {
        if (g_v9_turbo_speed == OPENSPM_TURBO_UNLIMITED)
            snprintf(message, sizeof message, "OpenSPM turbo: ready (%s the hotkey: unlimited speed).",
                     g_v9_turbo_toggle ? "press" : "hold");
        else
            snprintf(message, sizeof message, "OpenSPM turbo: ready (%s the hotkey: %ux speed).",
                     g_v9_turbo_toggle ? "press" : "hold", g_v9_turbo_speed);
        backend_log(OPENSPM_LOG_INFO, message);
    }
    return &g_v9_clock_host;
}

static void v9_turbo_apply(unsigned want);

/* Once per frame at PRESENT_SAFEPOINT (right after the input latch): engage
   or release fast-forward. Guest time continues across every change. The
   renderer is told, so it presents at most one image per display refresh,
   bypasses the present timeline / interpolation, drops audio and shows the
   indicator. While the OpenSPM menu is open turbo is released. */

static void v9_turbo_update(void) {
    unsigned want;
    if (!g_v9_turbo_enabled || !g_v9_clock_installed) return;
    if (g_v9_menu_open) {
        g_v9_turbo_latched = 0;
        want = 0u;
    } else if (g_v9_turbo_toggle) {
        if (g_v9_turbo_held && !g_v9_turbo_prev) g_v9_turbo_latched = !g_v9_turbo_latched;
        want = g_v9_turbo_latched ? g_v9_turbo_speed : 0u;
    } else {
        want = g_v9_turbo_held ? g_v9_turbo_speed : 0u;
    }
    g_v9_turbo_prev = g_v9_turbo_held;
    if (g_v9_turbo_active != 0u) ++g_v9_turbo_frames;
    v9_turbo_apply(want);
}

/* The OpenSPM menu is about to pause the game: release turbo first, so the
   paused game resumes at normal speed and the renderer presents every menu
   frame. */
static void v9_turbo_release(void) {
    g_v9_turbo_latched = 0;
    if (g_v9_turbo_active != 0u) v9_turbo_apply(0u);
}

/* Switch to `want` (0 = normal speed): re-anchor the guest clock at the new
   rate (guest time continues), tell the renderer, log. */
static void v9_turbo_apply(unsigned want) {
    uint64_t now;
    char message[224];
    if (want == g_v9_turbo_active) return;

    now = v9_real_ns();
    openspm_guest_clock_set_speed(&g_v9_pause_clock, now,
                                  want == 0u                        ? OPENSPM_GUEST_CLOCK_1X
                                  : want == OPENSPM_TURBO_UNLIMITED ? OPENSPM_GUEST_CLOCK_UNPACED
                                                                    : (uint32_t)want * OPENSPM_GUEST_CLOCK_1X);
    if (!g_v9_turbo_resolved) {
        HMODULE renderer = GetModuleHandleA("openspm_renderer.dll");
        g_v9_turbo_resolved = 1;
        if (renderer != NULL)
            g_v9_renderer_turbo =
                (openspm_renderer_turbo_fn)(void *)GetProcAddress(renderer, OPENSPM_RENDERER_TURBO_EXPORT);
    }
    if (g_v9_renderer_turbo != NULL) (void)g_v9_renderer_turbo(want);
    if (want != 0u && g_v9_turbo_active == 0u) {
        ++g_v9_turbo_engages;
        g_v9_turbo_frames = 0u;
        g_v9_turbo_started_ns = now;
        g_v9_turbo_guest_started_ns = openspm_pause_clock_now(&g_v9_pause_clock, now);
    }
    if (v9_should_log_count(g_v9_turbo_engages)) {
        if (want != 0u) {
            char speed[16];
            if (want == OPENSPM_TURBO_UNLIMITED) snprintf(speed, sizeof speed, "unlimited");
            else snprintf(speed, sizeof speed, "%ux", want);
            snprintf(message, sizeof message, "OpenSPM turbo #%llu: %s%s.", (unsigned long long)g_v9_turbo_engages,
                     speed, g_v9_renderer_turbo != NULL ? "" : " (renderer has no turbo export: presents stay vsynced)");
        } else {
            const double seconds = (double)(now - g_v9_turbo_started_ns) / 1e9;
            const double guest =
                (double)(openspm_pause_clock_now(&g_v9_pause_clock, now) - g_v9_turbo_guest_started_ns) / 1e9;
            snprintf(message, sizeof message,
                     "OpenSPM turbo #%llu off after %.1f s: %llu game frames (%.0f/s), %.1f s of game time; "
                     "normal speed, guest time continues.",
                     (unsigned long long)g_v9_turbo_engages, seconds, (unsigned long long)g_v9_turbo_frames,
                     seconds > 0.0 ? (double)g_v9_turbo_frames / seconds : 0.0, guest);
        }
        backend_log(OPENSPM_LOG_INFO, message);
    }
    g_v9_turbo_active = want;
}

/* At the end of PRESENT_SAFEPOINT (the frame was just handed to the renderer,
   Broadway is between two frames): hold here while the menu is open. The
   renderer keeps presenting the last image with the menu and pumps the
   window; this loop keeps HOME working. The next guest frame then runs with
   the input latched before the pause, as if no time had passed. */
static void v9_menu_pause(void) {
    char message[160];
    uint64_t frames = 1u, started;
    if (!g_v9_pause_enabled || g_v9_menu == NULL ||
        (g_v9_menu(OPENSPM_MENU_OP_STATE) & OPENSPM_MENU_STATE_OPEN) == 0)
        return;
    v9_turbo_release();
    started = v9_real_ns();
    openspm_pause_clock_pause(&g_v9_pause_clock, started);
    if (g_v9_menu(OPENSPM_MENU_OP_PAUSE_FRAME) != 1) {
        /* A game frame is still open in the renderer: retry at the next one. */
        openspm_pause_clock_resume(&g_v9_pause_clock, v9_real_ns());
        return;
    }
    backend_log(OPENSPM_LOG_INFO, "OpenSPM menu: game paused.");
    do {
        (void)v9_menu_filter(v9_keyboard_down()); /* HOME closes; nothing reaches the game */
        ++frames;
    } while (g_v9_menu(OPENSPM_MENU_OP_PAUSE_FRAME) == 1);
    openspm_pause_clock_resume(&g_v9_pause_clock, v9_real_ns());
    snprintf(message, sizeof message,
             "OpenSPM menu: game resumed after %.1f s (%llu menu frames); guest time did not advance.",
             (double)(v9_real_ns() - started) / 1e9, (unsigned long long)frames);
    backend_log(OPENSPM_LOG_INFO, message);
}

/* flipcapture (OPENSPM_M14_COPY_CAPTURE set): on the first in-game press of the
   flip button (Wii A), ask the renderer to arm its GX capture so it records the
   frame where the flip captures Mario's card. */
typedef void(OPENSPM_CALL *v9_arm_capture_fn)(unsigned draws);
static int v9_read_guest_u32(uint32_t address, uint32_t *out_value);
static void v9_maybe_arm_flip_capture(uint32_t pressed) {
    static int state = 0; /* 0 = unchecked, 1 = armed-able, 2 = done/off */
    static v9_arm_capture_fn arm = NULL;
    uint32_t seq = UINT32_MAX;
    if (state == 2 || (pressed & OPENSPM_WPAD_BTN_A) == 0u) return;
    if (state == 0) {
        const char *value = getenv("OPENSPM_M14_COPY_CAPTURE");
        HMODULE renderer = GetModuleHandleA("openspm_renderer.dll");
        if (value != NULL && *value != '\0' && renderer != NULL)
            arm = (v9_arm_capture_fn)(void *)GetProcAddress(renderer, "openspm_renderer_arm_debug_capture");
        state = arm != NULL ? 1 : 2;
        if (state == 2) return;
    }
    if (!v9_read_guest_u32(R8PE01_M14_SEQ_WORK + 0u, &seq) || seq != 2u) return;
    state = 2;
    arm(12000u);
}

static uint32_t g_v9_input_prev_down; /* previous frame's buttons (openspm.input overrides) */

static void v9_latch_input_frame(void) {
    uint32_t down = v9_menu_filter(v9_keyboard_down());
    v9_turbo_update(); /* fast-forward hotkey: speed of the frame that starts now */
    g_v9_input_prev_down = g_v9_input_down;
    g_v9_input_pressed = down & ~g_v9_input_down;
    v9_maybe_arm_flip_capture(g_v9_input_pressed);
    g_v9_input_down = down;
    ++g_v9_input_frame;
}

/* v8_guest_ptr() is a runtime mapper and assumes its MEM1/MEM2 backing has
 * already been attached by backend.run(). dev23 diagnostics can also be reached
 * by DLL-level contract tests before run(), so never call into that mapper for a
 * guest range whose backing store is absent. */
static int v9_guest_backing_attached(uint32_t address) {
    uint32_t offset;

    if (address >= R8PE01_MEM1_CACHED) {
        offset = address - R8PE01_MEM1_CACHED;
        if (offset < OPENSPM_WII_MEM1_SIZE) return g_backend.mem1 != NULL;
    }
    if (address >= R8PE01_MEM1_UNCACHED) {
        offset = address - R8PE01_MEM1_UNCACHED;
        if (offset < OPENSPM_WII_MEM1_SIZE) return g_backend.mem1 != NULL;
    }
    if (address >= R8PE01_MEM2_CACHED) {
        offset = address - R8PE01_MEM2_CACHED;
        if (offset < OPENSPM_WII_MEM2_SIZE) return g_backend.mem2 != NULL;
    }
    if (address >= R8PE01_MEM2_UNCACHED) {
        offset = address - R8PE01_MEM2_UNCACHED;
        if (offset < OPENSPM_WII_MEM2_SIZE) return g_backend.mem2 != NULL;
    }
    return 0;
}

static int v9_read_guest_u8(uint32_t address, uint8_t *out_value) {
    const uint8_t *p;
    if (out_value == NULL || !v9_guest_backing_attached(address)) return 0;
    p = (const uint8_t *)v8_guest_ptr(address, 1u);
    if (p == NULL) return 0;
    *out_value = p[0];
    return 1;
}

static int v9_read_guest_u32(uint32_t address, uint32_t *out_value) {
    const uint8_t *p;
    if (out_value == NULL || !v9_guest_backing_attached(address)) return 0;
    p = (const uint8_t *)v8_guest_ptr(address, 4u);
    if (p == NULL) return 0;
    *out_value = v8_load_be32(p);
    return 1;
}

/* OpenSPM cheats (include/openspm/cheats.h). The F1 menu keeps the enabled
   cheats in a table the renderer DLL exports; this backend copies it and
   applies it only here, at the once-per-frame PRESENT_SAFEPOINT, while the
   guest is paused between frames. No renderer export, OPENSPM_CHEATS=0, or
   every cheat off: nothing is written. */
static int v9_cheat_read32(void *context, uint32_t address, uint32_t *value) {
    (void)context;
    return v9_read_guest_u32(address, value);
}

static int v9_cheat_write32(void *context, uint32_t address, uint32_t value) {
    (void)context;
    if (!openspm_cheat_address_ok(address) || !v9_guest_backing_attached(address)) return 0;
    return v8_write32(address, value);
}

/* openspm.guest_call's queue (defined with the service below); the F1 menu's
   one-shot gift also goes through it. */
static openspm_guest_call_queue g_v9_guest_calls;

static void v9_apply_cheats(void) {
    static int state = 0; /* 0 = unresolved, 1 = ready, 2 = off */
    static openspm_renderer_cheats_fn get_cheats = NULL;
    static openspm_cheat_table table;
    static openspm_cheat_state cheat_state;
    static int reported = 0;
    unsigned changed;
    if (state == 2) return;
    if (state == 0) {
        const char *off = getenv("OPENSPM_CHEATS");
        HMODULE renderer = GetModuleHandleA("openspm_renderer.dll");
        if (!(off != NULL && off[0] == '0') && renderer != NULL)
            get_cheats = (openspm_renderer_cheats_fn)(void *)GetProcAddress(renderer, OPENSPM_RENDERER_CHEATS_EXPORT);
        state = get_cheats != NULL ? 1 : 2;
        backend_log(OPENSPM_LOG_INFO, state == 1
                        ? "OpenSPM cheats: F1 menu cheat table found; enabled cheats apply at the frame safepoint."
                        : "OpenSPM cheats: unavailable (no renderer cheat table or OPENSPM_CHEATS=0).");
        if (state == 2) return;
    }
    memset(&table, 0, sizeof table);
    table.struct_size = (uint32_t)sizeof table;
    if (!get_cheats(&table)) return; /* every cheat off */
    changed = openspm_cheats_apply(&table, &cheat_state, v9_cheat_read32, v9_cheat_write32, NULL);
    {
        /* "Give Gold Bar x3": the game adds the item itself (pouchAddItem),
           once per press, from the next frame's guest call. */
        uint32_t seq = 0u;
        if (v9_cheat_read32(NULL, OPENSPM_CHEAT_SEQ_WORK, &seq) &&
            openspm_cheats_gift_due(&table, &cheat_state, seq)) {
            const uint32_t item = OPENSPM_CHEAT_GIFT_ITEM;
            const int rc = openspm_guest_call_push(&g_v9_guest_calls, OPENSPM_CHEAT_POUCH_ADD_ITEM, 1u, &item);
            backend_log(rc == OPENSPM_OK ? OPENSPM_LOG_INFO : OPENSPM_LOG_WARN,
                        rc == OPENSPM_OK ? "OpenSPM cheats: giving a Gold Bar x3 (pouchAddItem 72)."
                                         : "OpenSPM cheats: gift not queued (guest call queue full).");
        }
    }
    if (changed != 0u && !reported) {
        char message[160];
        reported = 1;
        snprintf(message, sizeof message,
                 "OpenSPM cheats active: flags=0x%X custom writes=%u (first frame changed %u words).",
                 (unsigned)table.flags, (unsigned)table.write_count, changed);
        backend_log(OPENSPM_LOG_INFO, message);
    }
}

/* Mods (docs/MODDING.md): once per frame, right after the cheats, the
   backend emits OPENSPM_RUNTIME_EVENT_TICK_BEGIN through host->runtime_event;
   OpenSPMNative sets that sink only when at least one mod is loaded and maps
   it to OPENSPM_MOD_EVENT_TICK_BEGIN. While that callback runs (and only then,
   on this thread) the "openspm.guest_memory" service accepts reads/writes.
   After the renderer took the frame at the safepoint, PRESENT_END follows.
   No sink: state 2 and the safepoint pays one flag check. */
static int g_v9_mod_state; /* 0 = unresolved, 1 = sink ready, 2 = no mods */
static openspm_runtime_event_sink_fn g_v9_mod_sink;
static void *g_v9_mod_sink_context;
static uint64_t g_v9_mod_presents;
static DWORD g_v9_mod_tick_thread;

static uint8_t *v9_guest_resolve(void *context, uint32_t address, uint32_t size) {
    (void)context;
    if (!v9_guest_backing_attached(address) || !v9_guest_backing_attached(address + size - 1u)) return NULL;
    return (uint8_t *)v8_guest_ptr(address, size);
}

static openspm_guest_window g_v9_guest_window = {0, v9_guest_resolve, NULL};

static const openspm_guest_window *v9_guest_window_for_caller(void *context) {
    if (context != &g_v9_guest_window || GetCurrentThreadId() != g_v9_mod_tick_thread) return NULL;
    return &g_v9_guest_window;
}

static int OPENSPM_CALL v9_guest_read8(void *context, uint32_t address, uint8_t *out_value) {
    uint32_t value = 0u;
    int rc;
    if (out_value == NULL) return OPENSPM_ERROR_INVALID_ARGUMENT;
    rc = openspm_guest_window_read(v9_guest_window_for_caller(context), address, 1u, &value);
    if (rc == OPENSPM_OK) *out_value = (uint8_t)value;
    return rc;
}

static int OPENSPM_CALL v9_guest_read16(void *context, uint32_t address, uint16_t *out_value) {
    uint32_t value = 0u;
    int rc;
    if (out_value == NULL) return OPENSPM_ERROR_INVALID_ARGUMENT;
    rc = openspm_guest_window_read(v9_guest_window_for_caller(context), address, 2u, &value);
    if (rc == OPENSPM_OK) *out_value = (uint16_t)value;
    return rc;
}

static int OPENSPM_CALL v9_guest_read32(void *context, uint32_t address, uint32_t *out_value) {
    return openspm_guest_window_read(v9_guest_window_for_caller(context), address, 4u, out_value);
}

static int OPENSPM_CALL v9_guest_write8(void *context, uint32_t address, uint8_t value) {
    return openspm_guest_window_write(v9_guest_window_for_caller(context), address, 1u, value);
}

static int OPENSPM_CALL v9_guest_write16(void *context, uint32_t address, uint16_t value) {
    return openspm_guest_window_write(v9_guest_window_for_caller(context), address, 2u, value);
}

static int OPENSPM_CALL v9_guest_write32(void *context, uint32_t address, uint32_t value) {
    return openspm_guest_window_write(v9_guest_window_for_caller(context), address, 4u, value);
}

static int v9_query_guest_memory(uint32_t requested_version, void *out_service) {
    openspm_guest_memory_service *service = (openspm_guest_memory_service *)out_service;
    int rc = openspm_guest_memory_accept(service, requested_version);
    if (rc != OPENSPM_OK) return rc;
    memset(service, 0, sizeof(*service));
    service->header.struct_size = (uint32_t)sizeof(*service);
    service->header.abi_version = OPENSPM_GUEST_MEMORY_SERVICE_VERSION;
    service->capabilities = OPENSPM_GUEST_MEMORY_CAP_READ | OPENSPM_GUEST_MEMORY_CAP_WRITE;
    service->context = &g_v9_guest_window;
    service->read8 = v9_guest_read8;
    service->read16 = v9_guest_read16;
    service->read32 = v9_guest_read32;
    service->write8 = v9_guest_write8;
    service->write16 = v9_guest_write16;
    service->write32 = v9_guest_write32;
    return OPENSPM_OK;
}

/* ---- openspm.game_state (include/openspm/game_state.h): read-only ---- */
static int v9_game_state_read(void *context, uint32_t address, uint32_t *value) {
    return v9_guest_read32(context, address, value) == OPENSPM_OK;
}
static int OPENSPM_CALL v9_game_state_loaded(void *context) {
    return v9_guest_window_for_caller(context) != NULL && openspm_game_state_loaded(v9_game_state_read, context);
}
static int OPENSPM_CALL v9_game_state_story(void *context, int32_t *out) {
    return openspm_game_state_story(v9_game_state_read, context, out);
}
static int OPENSPM_CALL v9_game_state_flag(void *context, uint32_t n, int *out) {
    return openspm_game_state_flag(v9_game_state_read, context, n, out);
}
static int OPENSPM_CALL v9_game_state_byte(void *context, uint32_t n, uint32_t *out) {
    return openspm_game_state_byte(v9_game_state_read, context, n, out);
}
static int OPENSPM_CALL v9_game_state_pouch(void *context, openspm_game_pouch *out) {
    return openspm_game_state_pouch(v9_game_state_read, context, out);
}

static int v9_query_game_state(uint32_t requested_version, void *out_service) {
    openspm_game_state_service *service = (openspm_game_state_service *)out_service;
    int rc = openspm_game_state_accept(service, requested_version);
    if (rc != OPENSPM_OK) return rc;
    memset(service, 0, sizeof(*service));
    service->header.struct_size = (uint32_t)sizeof(*service);
    service->header.abi_version = OPENSPM_GAME_STATE_SERVICE_VERSION;
    service->context = &g_v9_guest_window;
    service->file_loaded = v9_game_state_loaded;
    service->story = v9_game_state_story;
    service->flag = v9_game_state_flag;
    service->byte = v9_game_state_byte;
    service->pouch = v9_game_state_pouch;
    return OPENSPM_OK;
}

/* ---- openspm.input (include/openspm/input_service.h) ---- */
static int OPENSPM_CALL v9_input_get(void *context, uint32_t *held, uint32_t *pressed) {
    (void)context;
    if (held != NULL) *held = g_v9_input_down & OPENSPM_WII_BTN_MASK;
    if (pressed != NULL) *pressed = g_v9_input_pressed & OPENSPM_WII_BTN_MASK;
    return OPENSPM_OK;
}

static int OPENSPM_CALL v9_input_set(void *context, uint32_t held) {
    (void)context;
    if (GetCurrentThreadId() != g_v9_mod_tick_thread || g_v9_mod_tick_thread == 0) return OPENSPM_ERROR_UNSUPPORTED;
    held &= OPENSPM_WII_BTN_MASK;
    g_v9_input_down = held;
    g_v9_input_pressed = held & ~g_v9_input_prev_down;
    return OPENSPM_OK;
}

static int OPENSPM_CALL v9_input_key_down(void *context, const char *key_name) {
    unsigned vk;
    (void)context;
    if (key_name == NULL) return OPENSPM_ERROR_INVALID_ARGUMENT;
    vk = openspm_key_find(key_name, strlen(key_name));
    if (vk == 0u) return OPENSPM_ERROR_INVALID_ARGUMENT;
    return v9_keyboard_focus_ok() && v9_vk_down(NULL, vk) ? 1 : 0;
}

static int v9_query_input(uint32_t requested_version, void *out_service) {
    openspm_input_service *service = (openspm_input_service *)out_service;
    int rc = openspm_input_accept(service, requested_version);
    if (rc != OPENSPM_OK) return rc;
    memset(service, 0, sizeof(*service));
    service->header.struct_size = (uint32_t)sizeof(*service);
    service->header.abi_version = OPENSPM_INPUT_SERVICE_VERSION;
    service->capabilities = OPENSPM_INPUT_CAP_READ | OPENSPM_INPUT_CAP_INJECT | OPENSPM_INPUT_CAP_KEYS;
    service->get_wii_buttons = v9_input_get;
    service->set_wii_buttons = v9_input_set;
    service->key_down = v9_input_key_down;
    return OPENSPM_OK;
}

/* ---- openspm.guest_call (include/openspm/guest_call.h) ---- */
static uint32_t g_v9_guest_scratch_used;
static uint64_t g_v9_guest_calls_run;

static int OPENSPM_CALL v9_guest_call(void *context, uint32_t target, uint32_t argc, const uint32_t *args) {
    (void)context;
    if (GetCurrentThreadId() != g_v9_mod_tick_thread || g_v9_mod_tick_thread == 0) return OPENSPM_ERROR_UNSUPPORTED;
    return openspm_guest_call_push(&g_v9_guest_calls, target, argc, args);
}

static int OPENSPM_CALL v9_guest_scratch(void *context, uint32_t size, uint32_t *out_address) {
    (void)context;
    return openspm_guest_scratch_take(&g_v9_guest_scratch_used, size, out_address);
}

static int v9_query_guest_call(uint32_t requested_version, void *out_service) {
    openspm_guest_call_service *service = (openspm_guest_call_service *)out_service;
    int rc = openspm_guest_call_accept(service, requested_version);
    if (rc != OPENSPM_OK) return rc;
    memset(service, 0, sizeof(*service));
    service->header.struct_size = (uint32_t)sizeof(*service);
    service->header.abi_version = OPENSPM_GUEST_CALL_SERVICE_VERSION;
    service->capabilities = OPENSPM_GUEST_CALL_CAP_CALL | OPENSPM_GUEST_CALL_CAP_SCRATCH;
    service->call = v9_guest_call;
    service->scratch = v9_guest_scratch;
    return OPENSPM_OK;
}

/* At the end of PRESENT_SAFEPOINT: hand the oldest queued mod call to the
   bridge, which runs it on a scratch CPU context right after this event
   returns (like an interrupt handler), before the next guest frame. */
static void v9_emit_guest_call(openspm_m14_hostcall_state *state) {
    openspm_guest_call_entry call;
    char message[160];
    if (state == NULL || !openspm_guest_call_pop(&g_v9_guest_calls, &call)) return;
    state->event_target = call.target;
    state->event_argc = call.argc;
    memset(state->event_args, 0, sizeof(state->event_args));
    memcpy(state->event_args, call.args, call.argc * sizeof(call.args[0]));
    ++g_v9_guest_calls_run;
    if (g_v9_guest_calls_run <= 4u || (g_v9_guest_calls_run & (g_v9_guest_calls_run - 1u)) == 0u) {
        snprintf(message, sizeof message, "OpenSPM mods: guest call #%llu to 0x%08X (%u args) at the frame safepoint.",
                 (unsigned long long)g_v9_guest_calls_run, (unsigned)call.target, (unsigned)call.argc);
        backend_log(OPENSPM_LOG_INFO, message);
    }
}

/* ---- openspm.content (include/openspm/content_service.h) ----
   Characters mods register at startup; the roster (content_roster.h) runs
   inside the mod tick below and does nothing while none is registered. */
static openspm_roster g_v9_roster;
static openspm_followers g_v9_followers;
static openspm_maps g_v9_maps;
static openspm_enemies g_v9_enemies;
static openspm_messages g_v9_messages;
static CRITICAL_SECTION g_v9_messages_lock; /* registrations from any thread vs the tick */
static int g_v9_messages_lock_ready;
static int OPENSPM_CALL v9_content_register_map(void *context, const openspm_map_def *def, uint32_t *out_slot);
static int OPENSPM_CALL v9_content_go_to_map(void *context, const char *name, const char *door);
static int OPENSPM_CALL v9_content_register_message(void *context, const char *key, const char *text);
static int OPENSPM_CALL v9_content_register_enemy(void *context, const openspm_enemy_def *def, uint32_t *out_template);

static int OPENSPM_CALL v9_content_register_character(void *context, const openspm_character_def *def,
                                                      uint32_t *out_id) {
    char why[200], message[260];
    int rc;
    (void)context;
    why[0] = '\0';
    rc = openspm_roster_register(&g_v9_roster, def, out_id, why, sizeof why);
    if (rc == OPENSPM_OK)
        snprintf(message, sizeof message, "OpenSPM content: character %s registered (roster id %u).", def->key,
                 (unsigned)(out_id ? *out_id : 0u));
    else
        snprintf(message, sizeof message, "OpenSPM content: character rejected: %s", why);
    backend_log(rc == OPENSPM_OK ? OPENSPM_LOG_INFO : OPENSPM_LOG_WARN, message);
    return rc;
}

static int OPENSPM_CALL v9_content_character_active(void *context, uint32_t id) {
    (void)context;
    return openspm_roster_character_active(&g_v9_roster, id);
}

static int OPENSPM_CALL v9_content_register_pixl(void *context, const openspm_pixl_def *def, uint32_t *out_id) {
    char why[200], message[260];
    int rc;
    (void)context;
    why[0] = '\0';
    rc = openspm_roster_register_pixl(&g_v9_roster, def, out_id, why, sizeof why);
    if (rc == OPENSPM_OK)
        snprintf(message, sizeof message, "OpenSPM content: Pixl %s registered (item %u).", def->key,
                 (unsigned)(out_id ? *out_id : 0u));
    else
        snprintf(message, sizeof message, "OpenSPM content: Pixl rejected: %s", why);
    backend_log(rc == OPENSPM_OK ? OPENSPM_LOG_INFO : OPENSPM_LOG_WARN, message);
    return rc;
}

static int OPENSPM_CALL v9_content_pixl_active(void *context, uint32_t item_id) {
    (void)context;
    return openspm_roster_pixl_active(&g_v9_roster, item_id);
}

static int OPENSPM_CALL v9_content_register_follower(void *context, const openspm_follower_def *def,
                                                     uint32_t *out_id) {
    char why[200], message[260];
    int rc;
    (void)context;
    why[0] = '\0';
    rc = openspm_followers_register(&g_v9_followers, def, out_id, why, sizeof why);
    if (rc == OPENSPM_OK)
        snprintf(message, sizeof message, "OpenSPM content: follower %s registered (follower %u).", def->key,
                 (unsigned)(out_id ? *out_id : 0u));
    else
        snprintf(message, sizeof message, "OpenSPM content: follower rejected: %s", why);
    backend_log(rc == OPENSPM_OK ? OPENSPM_LOG_INFO : OPENSPM_LOG_WARN, message);
    return rc;
}

static int OPENSPM_CALL v9_content_set_follower(void *context, uint32_t id, int shown) {
    (void)context;
    return openspm_followers_set(&g_v9_followers, id, shown);
}

static int v9_query_content(uint32_t requested_version, void *out_service) {
    openspm_content_service *service = (openspm_content_service *)out_service;
    uint32_t size;
    int rc = openspm_content_accept(service, requested_version);
    if (rc != OPENSPM_OK) return rc;
    /* A mod built against an older header passes a smaller struct: fill
       only what it has room for. */
    size = service->header.struct_size < sizeof(*service) ? service->header.struct_size : (uint32_t)sizeof(*service);
    memset(service, 0, size);
    service->header.struct_size = size;
    service->header.abi_version = OPENSPM_CONTENT_SERVICE_VERSION;
    service->register_character = v9_content_register_character;
    service->character_active = v9_content_character_active;
    if (size >= offsetof(openspm_content_service, pixl_active) + sizeof(service->pixl_active)) {
        service->register_pixl = v9_content_register_pixl;
        service->pixl_active = v9_content_pixl_active;
    }
    if (size >= offsetof(openspm_content_service, set_follower) + sizeof(service->set_follower)) {
        service->register_follower = v9_content_register_follower;
        service->set_follower = v9_content_set_follower;
    }
    if (size >= offsetof(openspm_content_service, go_to_map) + sizeof(service->go_to_map)) {
        service->register_map = v9_content_register_map;
        service->go_to_map = v9_content_go_to_map;
    }
    if (size >= offsetof(openspm_content_service, register_message) + sizeof(service->register_message))
        service->register_message = v9_content_register_message;
    if (size >= offsetof(openspm_content_service, register_enemy) + sizeof(service->register_enemy))
        service->register_enemy = v9_content_register_enemy;
    return OPENSPM_OK;
}

/* OpenSPM function hooks (docs/HOOKS.md, include/openspm/hooks.h): the
   registry's enabled entries (generated table; empty in a build without
   hooks), checked against guest RAM on the first host call to one of them,
   and the openspm.hooks service mods register pre callbacks through. */
#include "openspm/hooks_table.inc"
static openspm_hook_runtime g_v9_hooks;
static int g_v9_hooks_ready;

static void v9_hooks_prepare(void) {
    if (g_v9_hooks_ready) return;
    g_v9_hooks_ready = 1;
    openspm_hooks_init(&g_v9_hooks, k_openspm_hook_table, OPENSPM_HOOK_TABLE_COUNT);
}

static int v9_hooks_read_guest(void *context, uint32_t address, uint32_t *value) {
    const uint8_t *p = (const uint8_t *)v8_guest_ptr(address, 4u);
    (void)context;
    if (p == NULL) return 0;
    *value = (uint32_t)p[0] << 24 | (uint32_t)p[1] << 16 | (uint32_t)p[2] << 8 | p[3];
    return 1;
}

static int OPENSPM_CALL v9_hooks_find(void *context, const char *name, uint32_t *out_id) {
    (void)context;
    v9_hooks_prepare();
    return openspm_hooks_find(&g_v9_hooks, name, out_id);
}

static int OPENSPM_CALL v9_hooks_add_pre(void *context, uint32_t id, openspm_hook_fn fn, void *user) {
    char message[160];
    int rc;
    (void)context;
    v9_hooks_prepare();
    rc = openspm_hooks_add_pre(&g_v9_hooks, id, fn, user);
    if (rc == OPENSPM_OK) {
        snprintf(message, sizeof message, "OpenSPM hooks: a mod hooks %s.", g_v9_hooks.entries[id].name);
        backend_log(OPENSPM_LOG_INFO, message);
    }
    return rc;
}

static int OPENSPM_CALL v9_hooks_add_post(void *context, uint32_t id, openspm_hook_fn fn, void *user) {
    char message[160];
    int rc;
    (void)context;
    v9_hooks_prepare();
    rc = openspm_hooks_add_post(&g_v9_hooks, id, fn, user);
    if (rc == OPENSPM_OK) {
        snprintf(message, sizeof message, "OpenSPM hooks: a mod hooks the return of %s.", g_v9_hooks.entries[id].name);
        backend_log(OPENSPM_LOG_INFO, message);
    }
    return rc;
}

static int v9_query_hooks(uint32_t requested_version, void *out_service) {
    openspm_hook_service *service = (openspm_hook_service *)out_service;
    openspm_hook_service full;
    uint32_t size;
    const int rc = openspm_hook_accept(service, requested_version);
    if (rc != OPENSPM_OK) return rc;
    /* Fill no more than the mod's struct holds (a version 1 mod has no add_post). */
    size = service->header.struct_size < sizeof full ? service->header.struct_size : (uint32_t)sizeof full;
    memset(&full, 0, sizeof full);
    full.header.struct_size = size;
    full.header.abi_version = requested_version;
    full.find = v9_hooks_find;
    full.add_pre = v9_hooks_add_pre;
    full.add_post = v9_hooks_add_post;
    memcpy(service, &full, size);
    return OPENSPM_OK;
}

/* A host call to a hooked address: 1 when handled here (callbacks ran; the
   call was replaced or continues into the game's body), 0 when the address
   is not a hook. Callbacks may use openspm.guest_memory like the mod tick. */
static int v9_hooks_dispatch(uint32_t target, openspm_m14_hostcall_state *state) {
    int32_t id;
    const DWORD saved_thread = g_v9_mod_tick_thread;
    const int saved_active = g_v9_guest_window.active;
    if (OPENSPM_HOOK_TABLE_COUNT == 0u || state == NULL) return 0;
    v9_hooks_prepare();
    if (target == OPENSPM_HOOK_RETURN_ADDRESS) {
        /* A hooked body returned (post callbacks). */
        int returned;
        g_v9_mod_tick_thread = GetCurrentThreadId();
        g_v9_guest_window.active = 1;
        returned = openspm_hooks_return(&g_v9_hooks, state);
        g_v9_guest_window.active = saved_active;
        g_v9_mod_tick_thread = saved_thread;
        if (!returned) {
            backend_log(OPENSPM_LOG_ERROR, "OpenSPM hooks: a hooked call returned with no record of its caller.");
            return 0; /* unhandled host target: the backend's usual failure path */
        }
        return 1;
    }
    id = openspm_hooks_index(&g_v9_hooks, target);
    if (id < 0) return 0;
    if (!g_v9_hooks.verified) {
        char message[200];
        const uint32_t good = openspm_hooks_verify(&g_v9_hooks, v9_hooks_read_guest, NULL);
        snprintf(message, sizeof message,
                 "OpenSPM hooks: %u of %u registry hooks match this game (the rest continue untouched).",
                 (unsigned)good, (unsigned)g_v9_hooks.count);
        backend_log(good == g_v9_hooks.count ? OPENSPM_LOG_INFO : OPENSPM_LOG_WARN, message);
    }
    g_v9_mod_tick_thread = GetCurrentThreadId();
    g_v9_guest_window.active = 1;
    (void)openspm_hooks_dispatch(&g_v9_hooks, (uint32_t)id, state);
    g_v9_guest_window.active = saved_active;
    g_v9_mod_tick_thread = saved_thread;
    return 1;
}

static int v9_roster_read32(void *c, uint32_t a, uint32_t *v) { return v9_guest_read32(c, a, v); }
static int v9_roster_write32(void *c, uint32_t a, uint32_t v) { return v9_guest_write32(c, a, v); }
static int v9_roster_call(void *c, uint32_t t, uint32_t n, const uint32_t *args) { return v9_guest_call(c, t, n, args); }
static int v9_roster_scratch(void *c, uint32_t s, uint32_t *o) { return v9_guest_scratch(c, s, o); }
static int v9_roster_get(void *c, uint32_t *h, uint32_t *p) { return v9_input_get(c, h, p); }
static int v9_roster_set(void *c, uint32_t h) { return v9_input_set(c, h); }
static void v9_roster_log(void *c, int warn, const char *text) {
    (void)c;
    backend_log(warn ? OPENSPM_LOG_WARN : OPENSPM_LOG_INFO, text);
}

static const openspm_roster_io g_v9_content_io = {&g_v9_guest_window, v9_roster_read32, v9_roster_write32,
                                                  v9_roster_call,     v9_roster_scratch, v9_roster_get,
                                                  v9_roster_set,      v9_roster_log};

static void v9_roster_tick(void) {
    if (g_v9_roster.count + g_v9_roster.pixl_count != 0u) openspm_roster_tick(&g_v9_roster, &g_v9_content_io);
    if (g_v9_followers.count != 0u) openspm_followers_tick(&g_v9_followers, &g_v9_content_io);
    if (g_v9_maps.count != 0u) openspm_maps_camera_tick(&g_v9_maps, &g_v9_content_io);
    if (g_v9_messages.count != 0u && g_v9_messages_lock_ready) {
        EnterCriticalSection(&g_v9_messages_lock);
        openspm_messages_tick(&g_v9_messages, &g_v9_content_io);
        LeaveCriticalSection(&g_v9_messages_lock);
    }
}

static int OPENSPM_CALL v9_content_register_message(void *context, const char *key, const char *text) {
    int rc;
    (void)context;
    if (!g_v9_messages_lock_ready) return OPENSPM_ERROR_RUNTIME;
    EnterCriticalSection(&g_v9_messages_lock);
    rc = openspm_messages_register(&g_v9_messages, key, text);
    LeaveCriticalSection(&g_v9_messages_lock);
    if (rc != OPENSPM_OK) {
        char message[160];
        snprintf(message, sizeof message, "OpenSPM content: message %.60s rejected (a key is 1-63 characters, no spaces; "
                 "a text under 2048 bytes).", key ? key : "?");
        backend_log(OPENSPM_LOG_WARN, message);
    }
    return rc;
}

/* openspm.content maps (include/openspm/content_maps.h): called by a mod from
   its tick, while guest memory is open. */
static int OPENSPM_CALL v9_content_register_map(void *context, const openspm_map_def *def, uint32_t *out_slot) {
    char why[200], message[300];
    int rc;
    (void)context;
    why[0] = '\0';
    if (!g_v9_guest_window.active) {
        backend_log(OPENSPM_LOG_WARN, "OpenSPM content: register_map outside a mod tick (guest memory is closed).");
        return OPENSPM_ERROR_RUNTIME;
    }
    rc = openspm_maps_register(&g_v9_maps, &g_v9_content_io, def, out_slot, why, sizeof why);
    if (rc == OPENSPM_OK)
        snprintf(message, sizeof message, "OpenSPM content: map %s registered as %s (file %s, registry slot %u).",
                 def->key, def->name, def->file ? def->file : def->name, (unsigned)(out_slot ? *out_slot : 0u));
    else
        snprintf(message, sizeof message, "OpenSPM content: map %s rejected: %s", def && def->key ? def->key : "?", why);
    backend_log(rc == OPENSPM_OK ? OPENSPM_LOG_INFO : OPENSPM_LOG_WARN, message);
    return rc;
}

/* openspm.content enemies (include/openspm/content_enemies.h): from a mod
   tick, while guest memory is open. */
static int OPENSPM_CALL v9_content_register_enemy(void *context, const openspm_enemy_def *def, uint32_t *out_template) {
    char why[200], message[300];
    uint32_t id = 0u;
    int rc;
    (void)context;
    why[0] = '\0';
    if (!g_v9_guest_window.active) {
        backend_log(OPENSPM_LOG_WARN, "OpenSPM content: register_enemy outside a mod tick (guest memory is closed).");
        return OPENSPM_ERROR_RUNTIME;
    }
    rc = openspm_enemies_register(&g_v9_enemies, &g_v9_content_io, def, &id, why, sizeof why);
    if (rc == OPENSPM_OK) {
        snprintf(message, sizeof message, "OpenSPM content: enemy %s registered as template %u (tribe %u, base template %u).",
                 def->key, (unsigned)id, (unsigned)(id - OPENSPM_ENEMIES_GAME_TEMPLATES + OPENSPM_ENEMIES_GAME_TRIBES),
                 (unsigned)def->base_template);
        if (out_template) *out_template = id;
    } else {
        snprintf(message, sizeof message, "OpenSPM content: enemy %s rejected: %s", def && def->key ? def->key : "?", why);
    }
    backend_log(rc == OPENSPM_OK ? OPENSPM_LOG_INFO : OPENSPM_LOG_WARN, message);
    return rc;
}

static int OPENSPM_CALL v9_content_go_to_map(void *context, const char *name, const char *door) {
    char message[200];
    int rc;
    (void)context;
    if (!g_v9_guest_window.active) return OPENSPM_ERROR_RUNTIME;
    rc = openspm_maps_go(&g_v9_content_io, name, door);
    snprintf(message, sizeof message, rc == OPENSPM_OK ? "OpenSPM content: going to map %s (door %s)."
                                                       : "OpenSPM content: cannot go to map %s (door %s).",
             name ? name : "?", door ? door : "the map's fallback");
    backend_log(rc == OPENSPM_OK ? OPENSPM_LOG_INFO : OPENSPM_LOG_WARN, message);
    return rc;
}

static void v9_resolve_mod_sink(void) {
    const openspm_host_api *host = g_backend.host;
    g_v9_mod_state = 2;
    if (host == NULL ||
        host->header.struct_size < offsetof(openspm_host_api, runtime_event) + sizeof(host->runtime_event) ||
        host->runtime_event == NULL)
        return;
    g_v9_mod_sink = host->runtime_event;
    g_v9_mod_sink_context = host->context;
    g_v9_mod_state = 1;
    backend_log(OPENSPM_LOG_INFO,
                "OpenSPM mods: per-frame TICK_BEGIN / PRESENT_END events active at the frame safepoint; "
                "openspm.guest_memory is open during TICK_BEGIN.");
}

static void v9_mod_tick(void) {
    openspm_runtime_tick_event event;
    if (g_v9_mod_state == 0) v9_resolve_mod_sink();
    if (g_v9_mod_state != 1) return;
    memset(&event, 0, sizeof event);
    event.header.struct_size = (uint32_t)sizeof event;
    event.header.abi_version = OPENSPM_RUNTIME_SERVICE_VERSION;
    event.tick_count = g_v9_input_frame;
    g_v9_mod_tick_thread = GetCurrentThreadId();
    g_v9_guest_window.active = 1;
    g_v9_mod_sink(g_v9_mod_sink_context, OPENSPM_RUNTIME_EVENT_TICK_BEGIN, &event);
    /* After the mods: the roster sees their input changes and gets the last
       word on button 2 while the character menu is open. */
    v9_roster_tick();
    g_v9_guest_window.active = 0;
    g_v9_mod_tick_thread = 0;
}

static void v9_mod_present(void) {
    openspm_runtime_present_event event;
    memset(&event, 0, sizeof event);
    event.header.struct_size = (uint32_t)sizeof event;
    event.header.abi_version = OPENSPM_RUNTIME_SERVICE_VERSION;
    event.frame_count = g_v9_input_frame;
    event.present_count = ++g_v9_mod_presents;
    g_v9_mod_sink(g_v9_mod_sink_context, OPENSPM_RUNTIME_EVENT_PRESENT_END, &event);
}

static void v9_copy_guest_string(uint32_t address, char *out, size_t capacity) {
    const uint8_t *p;
    size_t i;
    if (out == NULL || capacity == 0u) return;
    out[0] = '\0';
    if (!v9_guest_backing_attached(address)) return;
    p = (const uint8_t *)v8_guest_ptr(address, 1u);
    if (p == NULL) return;
    for (i = 0u; i + 1u < capacity; ++i) {
        const uint32_t ch_address = address + (uint32_t)i;
        const uint8_t *ch;
        if (!v9_guest_backing_attached(ch_address)) break;
        ch = (const uint8_t *)v8_guest_ptr(ch_address, 1u);
        if (ch == NULL || *ch == 0u) break;
        out[i] = (*ch >= 0x20u && *ch < 0x7Fu) ? (char)*ch : '?';
        out[i + 1u] = '\0';
    }
}

static int v9_file_select_ready(void) {
    uint32_t seq = UINT32_MAX;
    uint32_t work = 0u;
    uint32_t work_state = UINT32_MAX;
    uint32_t file = 0u;
    uint32_t smart = 0u;
    uint32_t data = 0u;
    uint32_t tpl_version = 0u;
    uint32_t tpl_images = 0u;

    if (!v9_read_guest_u32(R8PE01_M14_SEQ_WORK, &seq) ||
        seq != R8PE01_M14_SEQ_LOAD) return 0;
    if (!v9_read_guest_u32(R8PE01_M14_SEQ_LOAD_SUB_WP, &work) || work == 0u)
        return 0;
    if (!v9_read_guest_u32(work + UINT32_C(0x04), &work_state) ||
        work_state != 5u) return 0;
    if (!v9_read_guest_u32(work + UINT32_C(0x08), &file) || file == 0u)
        return 0;
    if (!v9_read_guest_u32(file + UINT32_C(0xA4), &smart) || smart == 0u)
        return 0;
    if (!v9_read_guest_u32(smart + UINT32_C(0x00), &data) || data == 0u)
        return 0;
    if (!v9_read_guest_u32(data + UINT32_C(0x00), &tpl_version) ||
        !v9_read_guest_u32(data + UINT32_C(0x04), &tpl_images)) return 0;
    return tpl_version == R8PE01_M14_TPL_VERSION && tpl_images == 32u;
}

/* Astra's second gameplay audit demonstrated that the old rolling five-byte
 * matcher can see 61 45 00 00 02 inside a legal XF payload and falsely split
 * the command stream. Track command lengths instead. Only a decoded BP 0x45
 * write is DrawDone; payload bytes and vertex bytes can never create an event. */
typedef enum v9_gx_framer_phase {
    V9_GX_FRAME_OPCODE = 0,
    V9_GX_FRAME_BP,
    V9_GX_FRAME_CP,
    V9_GX_FRAME_XF_HEADER,
    V9_GX_FRAME_SKIP,
    V9_GX_FRAME_DRAW_COUNT,
    V9_GX_FRAME_CALL_DL
} v9_gx_framer_phase;

typedef struct v9_gx_framer {
    uint8_t phase;
    uint8_t opcode;
    uint8_t header[8];
    uint8_t header_used;
    uint32_t remaining;
    uint32_t display_list_depth;
    uint32_t vcd_lo;
    uint32_t vcd_hi;
    uint32_t vat_a[8];
    uint32_t vat_b[8];
    uint32_t vat_c[8];
    uint8_t vcd_lo_valid;
    uint8_t vcd_hi_valid;
    uint8_t vat_a_valid[8];
    uint8_t vat_b_valid[8];
    uint8_t vat_c_valid[8];
} v9_gx_framer;

static v9_gx_framer g_v9_gx_framer;

static uint32_t v9_framer_be32(const uint8_t *p) {
    return ((uint32_t)p[0] << 24) |
           ((uint32_t)p[1] << 16) |
           ((uint32_t)p[2] << 8) |
           (uint32_t)p[3];
}

static int v9_framer_numeric_component_size(uint32_t type, uint32_t *out_size) {
    if (out_size == NULL) return 0;
    switch (type) {
    case 0u: /* GX_U8 */
    case 1u: /* GX_S8 */
        *out_size = 1u;
        return 1;
    case 2u: /* GX_U16 */
    case 3u: /* GX_S16 */
        *out_size = 2u;
        return 1;
    case 4u: /* GX_F32 */
        *out_size = 4u;
        return 1;
    default:
        return 0;
    }
}

static int v9_framer_color_component_size(uint32_t type, uint32_t *out_size) {
    static const uint8_t sizes[6] = {2u, 3u, 4u, 2u, 3u, 4u};
    if (out_size == NULL || type >= 6u) return 0;
    *out_size = sizes[type];
    return 1;
}

static int v9_framer_add_descriptor(uint32_t descriptor,
                                    uint32_t direct_size,
                                    uint32_t *io_size) {
    if (io_size == NULL) return 0;
    switch (descriptor) {
    case 0u: /* GX_NONE */ return 1;
    case 1u: /* GX_DIRECT */ *io_size += direct_size; return 1;
    case 2u: /* GX_INDEX8 */ *io_size += 1u; return 1;
    case 3u: /* GX_INDEX16 */ *io_size += 2u; return 1;
    default: return 0;
    }
}

static int v9_framer_vertex_size(v9_gx_framer *framer,
                                 uint32_t format,
                                 uint32_t *out_size) {
    uint32_t size = 0u;
    uint32_t a;
    uint32_t b;
    uint32_t c;
    uint32_t descriptor;
    uint32_t component_size;
    uint32_t component_count;
    uint32_t type;
    uint32_t i;

    if (framer == NULL || out_size == NULL || format >= 8u ||
        !framer->vcd_lo_valid || !framer->vcd_hi_valid)
        return 0;

    a = framer->vat_a[format];
    b = framer->vat_b[format];
    c = framer->vat_c[format];

    /* PNMTXIDX + TEX0..7MTXIDX are one byte whenever enabled. */
    for (i = 0u; i < 9u; ++i)
        if (((framer->vcd_lo >> i) & 1u) != 0u) ++size;

    descriptor = (framer->vcd_lo >> 9) & 3u; /* GX_VA_POS */
    if (descriptor == 1u) {
        if (!framer->vat_a_valid[format] ||
            !v9_framer_numeric_component_size((a >> 1) & 7u, &component_size))
            return 0;
        component_count = ((a >> 0) & 1u) != 0u ? 3u : 2u;
        if (!v9_framer_add_descriptor(descriptor,
                                      component_size * component_count, &size))
            return 0;
    } else if (!v9_framer_add_descriptor(descriptor, 0u, &size)) {
        return 0;
    }

    descriptor = (framer->vcd_lo >> 11) & 3u; /* GX_VA_NRM */
    if (descriptor == 1u) {
        if (!framer->vat_a_valid[format] ||
            !v9_framer_numeric_component_size((a >> 10) & 7u, &component_size))
            return 0;
        component_count = (((a >> 31) & 1u) != 0u ||
                           ((a >> 9) & 1u) != 0u) ? 9u : 3u;
        size += component_size * component_count;
    } else if (descriptor == 2u || descriptor == 3u) {
        uint32_t index_size;
        if (!framer->vat_a_valid[format]) return 0;
        index_size = descriptor == 2u ? 1u : 2u;
        if (((a >> 31) & 1u) != 0u) index_size *= 3u; /* GX_NRM_NBT3 */
        size += index_size;
    }

    descriptor = (framer->vcd_lo >> 13) & 3u; /* GX_VA_CLR0 */
    if (descriptor == 1u) {
        if (!framer->vat_a_valid[format] ||
            !v9_framer_color_component_size((a >> 14) & 7u, &component_size))
            return 0;
        size += component_size;
    } else if (!v9_framer_add_descriptor(descriptor, 0u, &size)) {
        return 0;
    }

    descriptor = (framer->vcd_lo >> 15) & 3u; /* GX_VA_CLR1 */
    if (descriptor == 1u) {
        if (!framer->vat_a_valid[format] ||
            !v9_framer_color_component_size((a >> 18) & 7u, &component_size))
            return 0;
        size += component_size;
    } else if (!v9_framer_add_descriptor(descriptor, 0u, &size)) {
        return 0;
    }

    for (i = 0u; i < 8u; ++i) {
        descriptor = (framer->vcd_hi >> (i * 2u)) & 3u;
        if (descriptor == 0u) continue;
        if (descriptor == 2u) { size += 1u; continue; }
        if (descriptor == 3u) { size += 2u; continue; }

        if (i == 0u) {
            if (!framer->vat_a_valid[format]) return 0;
            component_count = ((a >> 21) & 1u) != 0u ? 2u : 1u;
            type = (a >> 22) & 7u;
        } else if (i <= 4u) {
            static const uint8_t count_bits[4] = {0u, 9u, 18u, 27u};
            static const uint8_t type_bits[4] = {1u, 10u, 19u, 28u};
            if (!framer->vat_b_valid[format]) return 0;
            component_count = ((b >> count_bits[i - 1u]) & 1u) != 0u ? 2u : 1u;
            type = (b >> type_bits[i - 1u]) & 7u;
        } else {
            static const uint8_t count_bits[3] = {5u, 14u, 23u};
            static const uint8_t type_bits[3] = {6u, 15u, 24u};
            if (!framer->vat_c_valid[format]) return 0;
            component_count = ((c >> count_bits[i - 5u]) & 1u) != 0u ? 2u : 1u;
            type = (c >> type_bits[i - 5u]) & 7u;
        }
        if (!v9_framer_numeric_component_size(type, &component_size)) return 0;
        size += component_size * component_count;
    }

    *out_size = size;
    return 1;
}

static void v9_framer_cp_write(v9_gx_framer *framer,
                               uint8_t address,
                               uint32_t value) {
    uint32_t index;
    if (framer == NULL) return;
    if (address == 0x50u) {
        framer->vcd_lo = value;
        framer->vcd_lo_valid = 1u;
    } else if (address == 0x60u) {
        framer->vcd_hi = value;
        framer->vcd_hi_valid = 1u;
    } else if (address >= 0x70u && address <= 0x77u) {
        index = address - 0x70u;
        framer->vat_a[index] = value;
        framer->vat_a_valid[index] = 1u;
    } else if (address >= 0x80u && address <= 0x87u) {
        index = address - 0x80u;
        framer->vat_b[index] = value;
        framer->vat_b_valid[index] = 1u;
    } else if (address >= 0x90u && address <= 0x97u) {
        index = address - 0x90u;
        framer->vat_c[index] = value;
        framer->vat_c_valid[index] = 1u;
    }
}

static int v9_framer_scan_bytes(v9_gx_framer *framer,
                                const uint8_t *bytes,
                                uint32_t size,
                                uint32_t *out_draw_done_count);

static int v9_framer_feed_byte(v9_gx_framer *framer,
                               uint8_t byte,
                               uint32_t *out_draw_done_count) {
    uint32_t value;
    uint32_t count;
    uint32_t vertex_size;

    if (framer == NULL || out_draw_done_count == NULL)
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    *out_draw_done_count = 0u;

    if (framer->phase == V9_GX_FRAME_SKIP) {
        if (framer->remaining == 0u) return OPENSPM_ERROR_RUNTIME;
        --framer->remaining;
        if (framer->remaining == 0u) framer->phase = V9_GX_FRAME_OPCODE;
        return OPENSPM_OK;
    }

    if (framer->phase == V9_GX_FRAME_OPCODE) {
        framer->opcode = byte;
        framer->header_used = 0u;
        if (byte == 0u || byte == OPENSPM_V9_GX_CMD_INVAL_VTX)
            return OPENSPM_OK;
        if (byte == OPENSPM_V9_GX_CMD_BP_LOAD) {
            framer->phase = V9_GX_FRAME_BP;
            return OPENSPM_OK;
        }
        if (byte == OPENSPM_V9_GX_CMD_CP_LOAD) {
            framer->phase = V9_GX_FRAME_CP;
            return OPENSPM_OK;
        }
        if (byte == OPENSPM_V9_GX_CMD_XF_LOAD) {
            framer->phase = V9_GX_FRAME_XF_HEADER;
            return OPENSPM_OK;
        }
        if (byte == OPENSPM_V9_GX_CMD_INDX_A ||
            byte == OPENSPM_V9_GX_CMD_INDX_B ||
            byte == OPENSPM_V9_GX_CMD_INDX_C ||
            byte == OPENSPM_V9_GX_CMD_INDX_D) {
            framer->phase = V9_GX_FRAME_SKIP;
            framer->remaining = 4u;
            return OPENSPM_OK;
        }
        if (byte == OPENSPM_V9_GX_CMD_CALL_DL) {
            framer->phase = V9_GX_FRAME_CALL_DL;
            return OPENSPM_OK;
        }
        if (byte >= 0x80u) {
            framer->phase = V9_GX_FRAME_DRAW_COUNT;
            return OPENSPM_OK;
        }
        return OPENSPM_ERROR_UNSUPPORTED;
    }

    if (framer->header_used >= sizeof(framer->header))
        return OPENSPM_ERROR_RUNTIME;
    framer->header[framer->header_used++] = byte;

    if (framer->phase == V9_GX_FRAME_BP && framer->header_used == 4u) {
        value = v9_framer_be32(framer->header);
        framer->phase = V9_GX_FRAME_OPCODE;
        if ((value >> 24) == 0x47u)
            g_v6_gx.pe_token = (uint16_t)(value & UINT32_C(0xFFFF));
        if ((value >> 24) == 0x45u &&
            (value & UINT32_C(0x00FFFFFF)) == UINT32_C(2))
            *out_draw_done_count = 1u;
        return OPENSPM_OK;
    }

    if (framer->phase == V9_GX_FRAME_CP && framer->header_used == 5u) {
        v9_framer_cp_write(framer, framer->header[0],
                           v9_framer_be32(framer->header + 1u));
        framer->phase = V9_GX_FRAME_OPCODE;
        return OPENSPM_OK;
    }

    if (framer->phase == V9_GX_FRAME_XF_HEADER && framer->header_used == 4u) {
        value = v9_framer_be32(framer->header);
        count = ((value >> 16) & UINT32_C(0xFFFF)) + 1u;
        framer->phase = V9_GX_FRAME_SKIP;
        framer->remaining = count * 4u;
        return OPENSPM_OK;
    }

    if (framer->phase == V9_GX_FRAME_DRAW_COUNT && framer->header_used == 2u) {
        count = ((uint32_t)framer->header[0] << 8) | framer->header[1];
        if (!v9_framer_vertex_size(framer, framer->opcode & 7u, &vertex_size))
            return OPENSPM_ERROR_UNSUPPORTED;
        if (vertex_size != 0u && count > UINT32_MAX / vertex_size)
            return OPENSPM_ERROR_RUNTIME;
        framer->remaining = count * vertex_size;
        framer->phase = framer->remaining != 0u
                            ? V9_GX_FRAME_SKIP : V9_GX_FRAME_OPCODE;
        return OPENSPM_OK;
    }

    if (framer->phase == V9_GX_FRAME_CALL_DL && framer->header_used == 8u) {
        uint32_t address = v9_framer_be32(framer->header);
        uint32_t list_size = v9_framer_be32(framer->header + 4u);
        const uint8_t *list_bytes;
        uint32_t nested_draw_done = 0u;
        int result;

        framer->phase = V9_GX_FRAME_OPCODE;
        if (list_size == 0u) return OPENSPM_OK;
        if (framer->display_list_depth >= OPENSPM_V9_GX_MAX_DL_DEPTH)
            return OPENSPM_ERROR_RUNTIME;
        if (!v9_guest_backing_attached(address))
            return OPENSPM_ERROR_NOT_FOUND;
        list_bytes = (const uint8_t *)v8_guest_ptr(address, list_size);
        if (list_bytes == NULL) return OPENSPM_ERROR_NOT_FOUND;

        ++framer->display_list_depth;
        result = v9_framer_scan_bytes(framer, list_bytes, list_size,
                                      &nested_draw_done);
        --framer->display_list_depth;
        if (result != OPENSPM_OK) return result;
        if (framer->phase != V9_GX_FRAME_OPCODE)
            return OPENSPM_ERROR_RUNTIME;
        *out_draw_done_count = nested_draw_done;
        return OPENSPM_OK;
    }

    return OPENSPM_OK;
}

static int v9_framer_scan_bytes(v9_gx_framer *framer,
                                const uint8_t *bytes,
                                uint32_t size,
                                uint32_t *out_draw_done_count) {
    uint32_t i;
    uint32_t total = 0u;
    if (framer == NULL || (size != 0u && bytes == NULL) ||
        out_draw_done_count == NULL)
        return OPENSPM_ERROR_INVALID_ARGUMENT;

    for (i = 0u; i < size; ++i) {
        uint32_t events = 0u;
        int result = v9_framer_feed_byte(framer, bytes[i], &events);
        if (result != OPENSPM_OK) return result;
        if (events > UINT32_MAX - total) total = UINT32_MAX;
        else total += events;
    }
    *out_draw_done_count = total;
    return OPENSPM_OK;
}

static int v9_framer_self_test(void) {
    static const uint8_t xf_false_positive[] = {
        0x10u, 0x00u, 0x01u, 0x10u, 0x40u,
        0x61u, 0x45u, 0x00u, 0x00u, 0x02u, 0x00u, 0x00u, 0x00u
    };
    static const uint8_t real_draw_done[] = {0x61u, 0x45u, 0x00u, 0x00u, 0x02u};
    static const uint8_t vertex_payload_case[] = {
        0x08u, 0x50u, 0x00u, 0x00u, 0x04u, 0x00u, /* POS INDEX8 */
        0x08u, 0x60u, 0x00u, 0x00u, 0x00u, 0x00u, /* no tex attrs */
        0x80u, 0x00u, 0x05u,                         /* five one-byte vertices */
        0x61u, 0x45u, 0x00u, 0x00u, 0x02u,         /* payload, not BP */
        0x61u, 0x45u, 0x00u, 0x00u, 0x02u          /* real BP DrawDone */
    };
    v9_gx_framer framer;
    uint32_t events;

    memset(&framer, 0, sizeof(framer));
    if (v9_framer_scan_bytes(&framer, xf_false_positive,
                             (uint32_t)sizeof(xf_false_positive), &events) != OPENSPM_OK ||
        events != 0u || framer.phase != V9_GX_FRAME_OPCODE)
        return 0;

    memset(&framer, 0, sizeof(framer));
    if (v9_framer_scan_bytes(&framer, real_draw_done, 2u, &events) != OPENSPM_OK ||
        events != 0u)
        return 0;
    if (v9_framer_scan_bytes(&framer, real_draw_done + 2u, 3u, &events) != OPENSPM_OK ||
        events != 1u || framer.phase != V9_GX_FRAME_OPCODE)
        return 0;

    memset(&framer, 0, sizeof(framer));
    if (v9_framer_scan_bytes(&framer, vertex_payload_case,
                             (uint32_t)sizeof(vertex_payload_case), &events) != OPENSPM_OK ||
        events != 1u || framer.phase != V9_GX_FRAME_OPCODE)
        return 0;
    return 1;
}

static void v9_log_latched_press(const openspm_m14_hostcall_state *state,
                                 uint32_t controller) {
    uint32_t seq = UINT32_MAX;
    uint32_t stage = UINT32_MAX;
    uint32_t gp_ptr = 0u;
    uint32_t fps = 0u;
    char message[256];

    if (state == NULL || g_v9_input_pressed == 0u ||
        g_v9_input_reported_frame == g_v9_input_frame) {
        return;
    }
    (void)v9_read_guest_u32(R8PE01_M14_SEQ_WORK + 0u, &seq);
    (void)v9_read_guest_u32(R8PE01_M14_SEQ_WORK + 4u, &stage);
    if (v9_read_guest_u32(R8PE01_M14_GP_PTR, &gp_ptr) && gp_ptr != 0u)
        (void)v9_read_guest_u32(gp_ptr + 4u, &fps);

    snprintf(message, sizeof(message),
             "M14 native frame-latched input frame=%llu buttons=0x%08X controller=%u first_getter_lr=0x%08X seq=%u stage=%u fps=%u.",
             (unsigned long long)g_v9_input_frame,
             g_v9_input_pressed,
             controller,
             state->lr,
             seq,
             stage,
             fps);
    backend_log(OPENSPM_LOG_INFO, message);
    g_v9_input_reported_frame = g_v9_input_frame;
}

static void v9_log_file_select_state(void) {
    uint32_t seq = UINT32_MAX;
    uint32_t work = 0u;
    uint32_t work_state = UINT32_MAX;
    uint32_t selected = UINT32_MAX;
    uint32_t file = 0u;
    uint32_t length = 0u;
    uint32_t smart = 0u;
    uint32_t data = 0u;
    uint32_t smart_size = 0u;
    uint32_t tpl_version = 0u;
    uint32_t tpl_images = 0u;
    uint32_t tpl_table = 0u;
    uint8_t file_state = 0xFFu;
    uint8_t file_type = 0xFFu;
    char path[65];
    char message[640];

    if (!v9_read_guest_u32(R8PE01_M14_SEQ_WORK + 0u, &seq) ||
        seq != R8PE01_M14_SEQ_LOAD) {
        return;
    }

    ++g_v9_file_select_samples;
    if (!v9_should_log_count(g_v9_file_select_samples)) return;

    (void)v9_read_guest_u32(R8PE01_M14_SEQ_LOAD_SUB_WP, &work);
    if (work != 0u) {
        (void)v9_read_guest_u32(work + UINT32_C(0x04), &work_state);
        (void)v9_read_guest_u32(work + UINT32_C(0x08), &file);
        (void)v9_read_guest_u32(work + UINT32_C(0x0C), &selected);
    }

    path[0] = '\0';
    if (file != 0u) {
        (void)v9_read_guest_u8(file + UINT32_C(0x00), &file_state);
        (void)v9_read_guest_u8(file + UINT32_C(0x01), &file_type);
        v9_copy_guest_string(file + UINT32_C(0x20), path, sizeof(path));
        (void)v9_read_guest_u32(file + UINT32_C(0xA0), &length);
        (void)v9_read_guest_u32(file + UINT32_C(0xA4), &smart);
    }
    if (smart != 0u) {
        (void)v9_read_guest_u32(smart + UINT32_C(0x00), &data);
        (void)v9_read_guest_u32(smart + UINT32_C(0x04), &smart_size);
    }
    if (data != 0u) {
        (void)v9_read_guest_u32(data + UINT32_C(0x00), &tpl_version);
        (void)v9_read_guest_u32(data + UINT32_C(0x04), &tpl_images);
        (void)v9_read_guest_u32(data + UINT32_C(0x08), &tpl_table);
    }

    snprintf(message, sizeof(message),
             "M14 file-select asset sample=%llu work=0x%08X state=%u selected=%u file=0x%08X file_state=%u type=%u len=%u smart=0x%08X data=0x%08X smart_size=%u tpl=0x%08X images=%u table=0x%08X valid_tpl=%u path='%s'.",
             (unsigned long long)g_v9_file_select_samples,
             work,
             work_state,
             selected,
             file,
             (unsigned)file_state,
             (unsigned)file_type,
             length,
             smart,
             data,
             smart_size,
             tpl_version,
             tpl_images,
             tpl_table,
             tpl_version == R8PE01_M14_TPL_VERSION ? 1u : 0u,
             path);
    backend_log(OPENSPM_LOG_INFO, message);
}

static int v9_submit_gx_segment(const uint8_t *bytes, uint32_t size) {
    int result = OPENSPM_OK;
    if (size == 0u) return OPENSPM_OK;

    g_v6_gx.fifo_bytes += size;
    if (g_v6_gx.renderer.submit_fifo != NULL) {
        const openspm_gx_guest_memory *guest_memory =
            g_v6_gx.guest_memory.map != NULL ? &g_v6_gx.guest_memory : NULL;
        result = g_v6_gx.renderer.submit_fifo(g_v6_gx.renderer.context,
                                              bytes, size, guest_memory);
    }
    if (result != OPENSPM_OK) {
        char message[256];
        ++g_v9_fifo_submit_failures;
        snprintf(message, sizeof(message),
                 "M14 renderer FIFO submit failed count=%llu result=%d segment=%u fifo_total=%llu.",
                 (unsigned long long)g_v9_fifo_submit_failures,
                 result,
                 size,
                 (unsigned long long)g_v6_gx.fifo_bytes);
        backend_log(OPENSPM_LOG_WARN, message);
    }
    return result;
}

/* Bulk equivalent of openspm_gx_platform's FIFO-address write path. The player-
 * local bridge has already serialized PPC stores into exact big-endian GX bytes.
 * Display-list recording remains delegated byte-for-byte to the neutral platform
 * because it writes into caller-owned guest RAM.
 *
 * DrawDone is a parsed command boundary. The framer tracks CP VCD/VAT state so
 * draw payload lengths are known and recursively scans called display lists so
 * their CP state remains visible to following parent commands. This replaces
 * the old rolling byte-pattern matcher disproved by Astra's XF counterexample.
 */
static int OPENSPM_CALL v9_submit_gx_fifo(void *context,
                                          const uint8_t *bytes,
                                          uint32_t size) {
    uint32_t i;
    uint32_t segment_start = 0u;
    int result = OPENSPM_OK;
    (void)context;

    if (size != 0u && bytes == NULL) return OPENSPM_ERROR_INVALID_ARGUMENT;
    if (size == 0u) return OPENSPM_OK;

    if (g_v6_gx.display_list_active) {
        for (i = 0u; i < size; ++i) {
            result = openspm_gx_platform_bus_write(&g_v6_gx,
                                                   OPENSPM_GX_FIFO_ADDRESS,
                                                   1u, bytes[i]);
            if (result != OPENSPM_OK) return result;
        }
        return OPENSPM_OK;
    }

    if (!g_v9_fifo_framer_reported) {
        backend_log(OPENSPM_LOG_INFO,
                    "M14 command-aware GX FIFO framing active; DrawDone/token are recognized only at parsed BP command boundaries.");
        g_v9_fifo_framer_reported = 1u;
    }

    for (i = 0u; i < size; ++i) {
        uint32_t draw_done_count = 0u;

        /* Keep the historical shift register for diagnostics only. It no longer
           controls token/completion semantics. */
        g_v6_gx.fifo_shift =
            ((g_v6_gx.fifo_shift << 8u) | bytes[i]) & UINT64_C(0xFFFFFFFFFF);

        result = v9_framer_feed_byte(&g_v9_gx_framer, bytes[i],
                                     &draw_done_count);
        if (result != OPENSPM_OK) {
            char message[320];
            snprintf(message, sizeof(message),
                     "M14 GX FIFO framer rejected stream result=%d byte=0x%02X index=%u phase=%u opcode=0x%02X header=%u remaining=%u fifo_total=%llu.",
                     result, (unsigned)bytes[i], (unsigned)i,
                     (unsigned)g_v9_gx_framer.phase,
                     (unsigned)g_v9_gx_framer.opcode,
                     (unsigned)g_v9_gx_framer.header_used,
                     (unsigned)g_v9_gx_framer.remaining,
                     (unsigned long long)g_v6_gx.fifo_bytes);
            backend_log(OPENSPM_LOG_ERROR, message);
            return result;
        }

        if (draw_done_count != 0u) {
            const uint32_t segment_size = i + 1u - segment_start;
            uint32_t available = UINT32_MAX - g_v6_gx.pending_draw_done;
            uint32_t add = draw_done_count < available
                               ? draw_done_count : available;
            g_v6_gx.pending_draw_done += add;
            g_v9_fifo_semantic_draw_done += draw_done_count;

            result = v9_submit_gx_segment(bytes + segment_start, segment_size);
            if (result != OPENSPM_OK) return result;

            if (g_v6_gx.renderer.sync != NULL) {
                result = g_v6_gx.renderer.sync(g_v6_gx.renderer.context,
                                               OPENSPM_GX_SYNC_DRAW_DONE);
                if (result != OPENSPM_OK) return result;
            }
            segment_start = i + 1u;
        }
    }

    if (segment_start < size) {
        result = v9_submit_gx_segment(bytes + segment_start,
                                      size - segment_start);
    }
    return result;
}

static int OPENSPM_CALL v9_handle_hostcall(
    void *context,
    uint32_t target_address,
    openspm_m14_hostcall_state *state) {
    if (target_address == OPENSPM_M14_HOST_EVENT_PRESENT_SAFEPOINT) {
        int result;
        (void)context;
        (void)state;

        /* This event is emitted once per retrace epoch before the next guest
           frame is woken. Latch input here so buttonsPressed remains stable for
           every getter during that complete simulation frame. */
        v9_latch_input_frame();
        /* Cheats: guest writes land here, before the next frame runs. */
        v9_apply_cheats();
        /* Mods: per-frame tick (guest memory open) right after the cheats. */
        if (g_v9_mod_state != 2) v9_mod_tick();
        v9_log_file_select_state();

        if (g_v6_bound_renderer.sync == NULL) return OPENSPM_OK;
        if (!g_v9_seqload_audit_armed && v9_file_select_ready()) {
            result = g_v6_bound_renderer.sync(g_v6_bound_renderer.context,
                                              OPENSPM_M14_GX_SYNC_SEQLOAD_AUDIT);
            if (result != OPENSPM_OK) return result;
            g_v9_seqload_audit_armed = 1u;
            backend_log(OPENSPM_LOG_INFO,
                        "M14 stable SEQ_LOAD detected; bounded renderer GX audit armed for the next 32 effective draws.");
        }
        result = g_v6_bound_renderer.sync(g_v6_bound_renderer.context,
                                          OPENSPM_GX_SYNC_PRESENT_SAFEPOINT);
        if (result == OPENSPM_OK && g_v9_mod_state == 1) v9_mod_present();
        /* OpenSPM menu open: hold the game here (pause_game), after the frame. */
        if (result == OPENSPM_OK && g_v9_pause_enabled) v9_menu_pause();
        /* Mods: at most one queued guest call per frame, run by the bridge
           as soon as this event returns. */
        if (result == OPENSPM_OK && g_v9_guest_calls.count != 0u) v9_emit_guest_call(state);
        if (result == OPENSPM_OK && !g_v9_present_safepoint_reported) {
            backend_log(OPENSPM_LOG_INFO,
                        "M14 native scheduler-idle renderer/input safepoint active; GX presentation and frame-latched buttons share the native retrace epoch.");
            g_v9_present_safepoint_reported = 1u;
        }
        return result;
    }

    if (target_address == R8PE01_M14_INPUT_PRESSED_TARGET) {
        uint32_t controller;
        if (state == NULL || state->header.struct_size < OPENSPM_M14_HOSTCALL_STATE_V1_SIZE)
            return OPENSPM_ERROR_INVALID_ARGUMENT;

        controller = state->gpr[3];
        state->event_target = 0u;
        state->event_argc = 0u;
        memset(state->event_args, 0, sizeof(state->event_args));
        ++g_v8_hostcall.handled_calls;

        /* Real wpadGetButtonsPressed is only:
             return wp->statuses[controller]->buttonsPressed;
           Do not consume a Windows edge here. The once-per-frame latch above
           deliberately returns the same value to every caller in this frame. */
        state->gpr[3] = g_v9_input_pressed;
        state->pc = state->lr;
        v9_log_latched_press(state, controller);
        return OPENSPM_OK;
    }

    if (target_address == R8PE01_M14_GX_BEGIN_DISPLAY_LIST) {
        int result;
        char message[256];
        if (state == NULL || state->header.struct_size < OPENSPM_M14_HOSTCALL_STATE_V1_SIZE)
            return OPENSPM_ERROR_INVALID_ARGUMENT;
        ++g_v9_display_list_begins;
        g_v9_last_display_list_address = state->gpr[3];
        if (v9_should_log_count(g_v9_display_list_begins)) {
            snprintf(message, sizeof(message),
                     "M14 GX display-list begin count=%llu addr=0x%08X capacity=%u lr=0x%08X.",
                     (unsigned long long)g_v9_display_list_begins,
                     state->gpr[3], state->gpr[4], state->lr);
            backend_log(OPENSPM_LOG_INFO, message);
        }
        result = v8_handle_hostcall(context, target_address, state);
        return result;
    }

    if (target_address == R8PE01_M14_GX_END_DISPLAY_LIST) {
        int result;
        char message[320];
        uint32_t size;
        uint64_t hash = 0u;
        const uint8_t *bytes = NULL;
        if (state == NULL || state->header.struct_size < OPENSPM_M14_HOSTCALL_STATE_V1_SIZE)
            return OPENSPM_ERROR_INVALID_ARGUMENT;
        result = v8_handle_hostcall(context, target_address, state);
        size = state->gpr[3];
        ++g_v9_display_list_ends;
        if (g_v9_last_display_list_address != 0u && size != 0u &&
            size <= UINT32_C(8) * UINT32_C(1024) * UINT32_C(1024) &&
            v9_guest_backing_attached(g_v9_last_display_list_address)) {
            bytes = (const uint8_t *)v8_guest_ptr(g_v9_last_display_list_address, size);
            if (bytes != NULL) hash = v9_hash_bytes(bytes, size);
        }
        if (v9_should_log_count(g_v9_display_list_ends) || g_v9_seqload_audit_armed) {
            snprintf(message, sizeof(message),
                     "M14 GX display-list end count=%llu result=%d addr=0x%08X size=%u hash=0x%016llX lr=0x%08X.",
                     (unsigned long long)g_v9_display_list_ends,
                     result, g_v9_last_display_list_address, size,
                     (unsigned long long)hash, state->lr);
            backend_log(OPENSPM_LOG_INFO, message);
        }
        return result;
    }

    return v8_handle_hostcall(context, target_address, state);
}

static int OPENSPM_CALL v9_query_service(void *backend_context,
                                         const char *service_id,
                                         uint32_t requested_version,
                                         void *out_service) {
    int result;

    if (service_id != NULL &&
        strcmp(service_id, OPENSPM_GUEST_MEMORY_SERVICE_ID) == 0) {
        return v9_query_guest_memory(requested_version, out_service);
    }
    if (service_id != NULL && strcmp(service_id, OPENSPM_GAME_STATE_SERVICE_ID) == 0)
        return v9_query_game_state(requested_version, out_service);
    if (service_id != NULL && strcmp(service_id, OPENSPM_INPUT_SERVICE_ID) == 0)
        return v9_query_input(requested_version, out_service);
    if (service_id != NULL && strcmp(service_id, OPENSPM_GUEST_CALL_SERVICE_ID) == 0)
        return v9_query_guest_call(requested_version, out_service);
    if (service_id != NULL && strcmp(service_id, OPENSPM_CONTENT_SERVICE_ID) == 0)
        return v9_query_content(requested_version, out_service);
    if (service_id != NULL && strcmp(service_id, OPENSPM_HOOK_SERVICE_ID) == 0)
        return v9_query_hooks(requested_version, out_service);
    if (service_id != NULL &&
        strcmp(service_id, OPENSPM_GX_FIFO_SERVICE_ID) == 0) {
        openspm_gx_fifo_service *service = (openspm_gx_fifo_service *)out_service;
        if (service == NULL ||
            requested_version != OPENSPM_GX_FIFO_SERVICE_VERSION ||
            service->header.struct_size < sizeof(*service)) {
            return OPENSPM_ERROR_ABI_MISMATCH;
        }
        memset(service, 0, sizeof(*service));
        service->header.struct_size = sizeof(*service);
        service->header.abi_version = OPENSPM_GX_FIFO_SERVICE_VERSION;
        service->capabilities = OPENSPM_GX_FIFO_CAP_SUBMIT;
        service->context = backend_context;
        service->submit = v9_submit_gx_fifo;
        return OPENSPM_OK;
    }

    result = v8_query_service(backend_context, service_id,
                              requested_version, out_service);
    if (result != OPENSPM_OK) return result;

    if (service_id != NULL &&
        strcmp(service_id, OPENSPM_M14_HOSTCALL_SERVICE_ID) == 0) {
        openspm_m14_hostcall_service *service =
            (openspm_m14_hostcall_service *)out_service;
        service->handle = v9_handle_hostcall;
    }
    return OPENSPM_OK;
}

OPENSPM_EXPORT int OPENSPM_CALL openspm_backend_query(
    uint32_t requested_abi,
    const openspm_host_api *host,
    openspm_backend_api *out_api) {
    int result = openspm_backend_query_v8_internal(requested_abi, host, out_api);
    if (result != OPENSPM_OK) return result;

    if (!v9_framer_self_test()) {
        backend_log(OPENSPM_LOG_ERROR,
                    "M14 command-aware GX FIFO framer self-test failed; refusing unsafe raw-byte DrawDone fallback.");
        return OPENSPM_ERROR_RUNTIME;
    }

    g_v9_present_safepoint_reported = 0u;
    g_v9_input_frame = 0u;
    g_v9_input_down = 0u;
    g_v9_input_pressed = 0u;
    g_v9_input_reported_frame = UINT64_MAX;
    g_v9_file_select_samples = 0u;
    g_v9_display_list_begins = 0u;
    g_v9_display_list_ends = 0u;
    g_v9_fifo_submit_failures = 0u;
    g_v9_seqload_audit_armed = 0u;
    g_v9_last_display_list_address = 0u;
    g_v9_fifo_framer_reported = 0u;
    g_v9_fifo_semantic_draw_done = 0u;
    memset(&g_v9_gx_framer, 0, sizeof(g_v9_gx_framer));
    g_v9_mod_state = 0;
    openspm_roster_init(&g_v9_roster);
    openspm_followers_init(&g_v9_followers);
    openspm_maps_init(&g_v9_maps);
    openspm_enemies_init(&g_v9_enemies);
    openspm_messages_init(&g_v9_messages);
    if (!g_v9_messages_lock_ready) {
        InitializeCriticalSection(&g_v9_messages_lock);
        g_v9_messages_lock_ready = 1;
    }
    {
        /* OPENSPM_CHARACTER_SLOTS=1: added characters play on a game
           character's slot even when real ids are available. */
        const char *slots = getenv("OPENSPM_CHARACTER_SLOTS");
        g_v9_roster.slots_only = slots != NULL && strcmp(slots, "1") == 0;
    }
    g_v9_mod_sink = NULL;
    g_v9_mod_sink_context = NULL;
    g_v9_mod_presents = 0u;
    g_v9_mod_tick_thread = 0;
    g_v9_guest_window.active = 0;
    g_v9_keymap_loaded = 0;
    g_v9_keymap_generation = 0;
    g_v9_menu_resolved = 0;
    g_v9_menu = NULL;
    g_v9_menu_keymap = NULL;
    g_v9_home_prev = 0u;
    g_v9_menu_open = 0u;
    g_v9_pause_enabled = 0;
    g_v9_clock_installed = 0;
    g_v9_turbo_enabled = 0;
    g_v9_turbo_held = 0u;
    g_v9_turbo_prev = 0u;
    g_v9_turbo_latched = 0;
    g_v9_turbo_active = 0u;
    g_v9_turbo_resolved = 0;
    g_v9_renderer_turbo = NULL;
    g_v9_turbo_engages = 0u;
    g_native_runtime_host_filter = v9_runtime_host;
    out_api->backend_id = "openspm.native";
    out_api->backend_version = "14.0-dev23";
    out_api->query_service = v9_query_service;
    return OPENSPM_OK;
}
