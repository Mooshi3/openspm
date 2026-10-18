/* OpenSPM gamepad input: XInput controller -> sideways Wii Remote buttons.
 *
 * The backend's keyboard latch (v9_keyboard_down) produces raw WPAD bits for a
 * Wii Remote held sideways. This adds the first connected XInput controller
 * (prefers the renderer's SDL gamepads: PS4/PS5/Switch/Xbox; XInput fallback)
 * to the same bits, so keyboard
 * and pad work together. xinput1_4.dll / xinput9_1_0.dll are loaded at run
 * time; without them nothing changes.
 *
 * D-pad and left stick move in screen space (rotated like the keyboard arrows).
 * Buttons are configurable with OPENSPM_PAD_MAP (OpenSPM.ini [Controller] map):
 *   "a=2,b=B,x=1,y=A,start=PLUS,back=MINUS,rb=SHAKE,rs=TURBO"      (default)
 * names: a b x y start back lb rb lt rt ls rs; values: 1 2 A B PLUS MINUS TURBO SHAKE NONE.
 * The Guide / PS / Home button is always Wii Remote HOME (the OpenSPM menu).
 * TURBO is the fast-forward hotkey (docs/PLAYING.md "Fast-forward"); it never
 * reaches the game. Default: clicking the right stick (no game button).
 * SHAKE is the Wii Remote shake (an accelerometer swing, docs/PLAYING.md);
 * default RB.
 * OPENSPM_PAD=0 disables the controller.
 *
 * The mapping/translation part is portable C and host unit-tested
 * (tests/m14_pad_map_test.c); only polling is Windows-specific. */
#ifndef OPENSPM_PAD_XINPUT_H
#define OPENSPM_PAD_XINPUT_H

#include <ctype.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* Raw WPAD core bits. */
#define OPENSPM_PAD_WPAD_LEFT  UINT32_C(0x00000001)
#define OPENSPM_PAD_WPAD_RIGHT UINT32_C(0x00000002)
#define OPENSPM_PAD_WPAD_DOWN  UINT32_C(0x00000004)
#define OPENSPM_PAD_WPAD_UP    UINT32_C(0x00000008)
#define OPENSPM_PAD_WPAD_PLUS  UINT32_C(0x00000010)
#define OPENSPM_PAD_WPAD_2     UINT32_C(0x00000100)
#define OPENSPM_PAD_WPAD_1     UINT32_C(0x00000200)
#define OPENSPM_PAD_WPAD_B     UINT32_C(0x00000400)
#define OPENSPM_PAD_WPAD_A     UINT32_C(0x00000800)
#define OPENSPM_PAD_WPAD_MINUS UINT32_C(0x00001000)
/* Wii Remote HOME: the Guide button. The backend turns it into "open the
   OpenSPM menu" ([Controller] home_opens_menu) and never passes it to the game. */
#define OPENSPM_PAD_WPAD_HOME  UINT32_C(0x00008000)
/* OpenSPM-private (no WPAD button, = OPENSPM_KEYMAP_WPAD_TURBO): fast-forward. */
#define OPENSPM_PAD_WPAD_TURBO UINT32_C(0x40000000)
/* = OPENSPM_KEYMAP_WPAD_SHAKE: the Wii Remote shake. */
#define OPENSPM_PAD_WPAD_SHAKE UINT32_C(0x20000000)

/* XInput wButtons bits (XINPUT_GAMEPAD_*), duplicated for the portable part. */
#define OPENSPM_XI_DPAD_UP    0x0001u
#define OPENSPM_XI_DPAD_DOWN  0x0002u
#define OPENSPM_XI_DPAD_LEFT  0x0004u
#define OPENSPM_XI_DPAD_RIGHT 0x0008u
#define OPENSPM_XI_START      0x0010u
#define OPENSPM_XI_BACK       0x0020u
#define OPENSPM_XI_LTHUMB     0x0040u
#define OPENSPM_XI_RTHUMB     0x0080u
#define OPENSPM_XI_LB         0x0100u
#define OPENSPM_XI_RB         0x0200u
#define OPENSPM_XI_GUIDE      0x0400u /* SDL export only; XInputGetState never reports it */
#define OPENSPM_XI_A          0x1000u
#define OPENSPM_XI_B          0x2000u
#define OPENSPM_XI_X          0x4000u
#define OPENSPM_XI_Y          0x8000u

enum { OPENSPM_PAD_SLOT_A, OPENSPM_PAD_SLOT_B, OPENSPM_PAD_SLOT_X, OPENSPM_PAD_SLOT_Y, OPENSPM_PAD_SLOT_START,
       OPENSPM_PAD_SLOT_BACK, OPENSPM_PAD_SLOT_LB, OPENSPM_PAD_SLOT_RB, OPENSPM_PAD_SLOT_LT, OPENSPM_PAD_SLOT_RT,
       OPENSPM_PAD_SLOT_LS, OPENSPM_PAD_SLOT_RS, OPENSPM_PAD_SLOT_COUNT };

static const char *const openspm_pad_slot_names[OPENSPM_PAD_SLOT_COUNT] = {
    "a", "b", "x", "y", "start", "back", "lb", "rb", "lt", "rt", "ls", "rs"};

typedef struct openspm_pad_map {
    uint32_t bits[OPENSPM_PAD_SLOT_COUNT];
} openspm_pad_map;

static int openspm_pad_iequal(const char *a, size_t alen, const char *b) {
    size_t i;
    if (alen != strlen(b)) return 0;
    for (i = 0; i < alen; ++i)
        if (tolower((unsigned char)a[i]) != tolower((unsigned char)b[i])) return 0;
    return 1;
}

static int openspm_pad_value_bits(const char *value, size_t len, uint32_t *out) {
    static const struct { const char *name; uint32_t bits; } values[] = {
        {"1", OPENSPM_PAD_WPAD_1}, {"2", OPENSPM_PAD_WPAD_2}, {"A", OPENSPM_PAD_WPAD_A},
        {"B", OPENSPM_PAD_WPAD_B}, {"PLUS", OPENSPM_PAD_WPAD_PLUS}, {"+", OPENSPM_PAD_WPAD_PLUS},
        {"MINUS", OPENSPM_PAD_WPAD_MINUS}, {"-", OPENSPM_PAD_WPAD_MINUS}, {"TURBO", OPENSPM_PAD_WPAD_TURBO},
        {"SHAKE", OPENSPM_PAD_WPAD_SHAKE}, {"NONE", 0u}};
    size_t i;
    for (i = 0; i < sizeof values / sizeof values[0]; ++i) {
        if (openspm_pad_iequal(value, len, values[i].name)) {
            *out = values[i].bits;
            return 1;
        }
    }
    return 0;
}

static void openspm_pad_default_map(openspm_pad_map *map) {
    memset(map, 0, sizeof *map);
    map->bits[OPENSPM_PAD_SLOT_A] = OPENSPM_PAD_WPAD_2;       /* jump */
    map->bits[OPENSPM_PAD_SLOT_B] = OPENSPM_PAD_WPAD_B;
    map->bits[OPENSPM_PAD_SLOT_X] = OPENSPM_PAD_WPAD_1;
    map->bits[OPENSPM_PAD_SLOT_Y] = OPENSPM_PAD_WPAD_A;
    map->bits[OPENSPM_PAD_SLOT_START] = OPENSPM_PAD_WPAD_PLUS;
    map->bits[OPENSPM_PAD_SLOT_BACK] = OPENSPM_PAD_WPAD_MINUS;
    map->bits[OPENSPM_PAD_SLOT_RB] = OPENSPM_PAD_WPAD_SHAKE; /* the Wii Remote shake */
    map->bits[OPENSPM_PAD_SLOT_RS] = OPENSPM_PAD_WPAD_TURBO; /* hold: fast-forward */
}

/* Parses "name=value,..." over the defaults. Returns the number of invalid
   entries (they are ignored). */
static int openspm_pad_parse_map(const char *text, openspm_pad_map *map) {
    int invalid = 0;
    openspm_pad_default_map(map);
    while (text != NULL && *text) {
        const char *end = strchr(text, ',');
        const char *eq;
        size_t len = end ? (size_t)(end - text) : strlen(text);
        const char *name = text, *value;
        size_t name_len, value_len;
        int slot, found = 0;
        uint32_t bits = 0u;
        while (len > 0 && isspace((unsigned char)*name)) { ++name; --len; }
        eq = (const char *)memchr(name, '=', len);
        if (len > 0 && eq != NULL) {
            name_len = (size_t)(eq - name);
            value = eq + 1;
            value_len = len - name_len - 1u;
            while (name_len > 0 && isspace((unsigned char)name[name_len - 1])) --name_len;
            while (value_len > 0 && isspace((unsigned char)*value)) { ++value; --value_len; }
            while (value_len > 0 && isspace((unsigned char)value[value_len - 1])) --value_len;
            for (slot = 0; slot < OPENSPM_PAD_SLOT_COUNT; ++slot) {
                if (openspm_pad_iequal(name, name_len, openspm_pad_slot_names[slot]) &&
                    openspm_pad_value_bits(value, value_len, &bits)) {
                    map->bits[slot] = bits;
                    found = 1;
                    break;
                }
            }
            if (!found) ++invalid;
        } else if (len > 0) {
            ++invalid;
        }
        if (!end) break;
        text = end + 1;
    }
    return invalid;
}

/* Translate one controller sample. Sticks are -32768..32767 (up positive),
   triggers 0..255. Screen directions map to the sideways-remote raw bits like
   the keyboard arrows: screen left -> UP, right -> DOWN, down -> LEFT, up -> RIGHT. */
static uint32_t openspm_pad_translate(const openspm_pad_map *map, unsigned buttons, int lx, int ly,
                                      unsigned lt, unsigned rt) {
    const int dead = 12000; /* ~37% */
    uint32_t down = 0u;
    if ((buttons & OPENSPM_XI_DPAD_LEFT) || lx < -dead) down |= OPENSPM_PAD_WPAD_UP;
    if ((buttons & OPENSPM_XI_DPAD_RIGHT) || lx > dead) down |= OPENSPM_PAD_WPAD_DOWN;
    if ((buttons & OPENSPM_XI_DPAD_DOWN) || ly < -dead) down |= OPENSPM_PAD_WPAD_LEFT;
    if ((buttons & OPENSPM_XI_DPAD_UP) || ly > dead) down |= OPENSPM_PAD_WPAD_RIGHT;
    if (buttons & OPENSPM_XI_A) down |= map->bits[OPENSPM_PAD_SLOT_A];
    if (buttons & OPENSPM_XI_B) down |= map->bits[OPENSPM_PAD_SLOT_B];
    if (buttons & OPENSPM_XI_X) down |= map->bits[OPENSPM_PAD_SLOT_X];
    if (buttons & OPENSPM_XI_Y) down |= map->bits[OPENSPM_PAD_SLOT_Y];
    if (buttons & OPENSPM_XI_START) down |= map->bits[OPENSPM_PAD_SLOT_START];
    if (buttons & OPENSPM_XI_BACK) down |= map->bits[OPENSPM_PAD_SLOT_BACK];
    if (buttons & OPENSPM_XI_LB) down |= map->bits[OPENSPM_PAD_SLOT_LB];
    if (buttons & OPENSPM_XI_RB) down |= map->bits[OPENSPM_PAD_SLOT_RB];
    if (buttons & OPENSPM_XI_LTHUMB) down |= map->bits[OPENSPM_PAD_SLOT_LS];
    if (buttons & OPENSPM_XI_RTHUMB) down |= map->bits[OPENSPM_PAD_SLOT_RS];
    if (buttons & OPENSPM_XI_GUIDE) down |= OPENSPM_PAD_WPAD_HOME; /* fixed: the OpenSPM menu */
    if (lt > 100u) down |= map->bits[OPENSPM_PAD_SLOT_LT];
    if (rt > 100u) down |= map->bits[OPENSPM_PAD_SLOT_RT];
    return down;
}

#if defined(_WIN32)
#include <xinput.h>

typedef DWORD(WINAPI *openspm_xinput_get_state_fn)(DWORD, XINPUT_STATE *);
typedef int(OPENSPM_CALL *openspm_sdl_pad_state_fn)(unsigned *, int *, int *, unsigned *, unsigned *);

/* Once per game frame (input latch). */
static uint32_t openspm_pad_down(void) {
    static int initialized = 0, enabled = 1;
    static openspm_xinput_get_state_fn get_state = NULL;
    static openspm_sdl_pad_state_fn sdl_state = NULL;
    static openspm_pad_map map;
    static int connected = -1;          /* user index, -1 = none */
    static unsigned rescan_countdown = 0u;
    XINPUT_STATE state;
    char message[256];
    if (!initialized) {
        const char *off = getenv("OPENSPM_PAD");
        HMODULE module;
        int invalid;
        initialized = 1;
        enabled = !(off != NULL && off[0] == '0');
        invalid = openspm_pad_parse_map(getenv("OPENSPM_PAD_MAP"), &map);
        /* Prefer the renderer's SDL gamepads (PS4/PS5/Switch/Xbox...); XInput
           (Xbox-style only) is the fallback when the export is missing. */
        module = GetModuleHandleA("openspm_renderer.dll");
        if (module != NULL)
            sdl_state = (openspm_sdl_pad_state_fn)(void *)GetProcAddress(module, "openspm_renderer_gamepad_state");
        module = LoadLibraryA("xinput1_4.dll");
        if (module == NULL) module = LoadLibraryA("xinput9_1_0.dll");
        if (module != NULL) get_state = (openspm_xinput_get_state_fn)(void *)GetProcAddress(module, "XInputGetState");
        snprintf(message, sizeof message, "OpenSPM gamepad: %s (map a=0x%X b=0x%X x=0x%X y=0x%X start=0x%X back=0x%X%s)",
                 !enabled ? "disabled (OPENSPM_PAD=0)"
                 : sdl_state != NULL ? "SDL gamepads ready (PS/Xbox/Switch)"
                 : get_state != NULL ? "XInput ready" : "XInput unavailable",
                 (unsigned)map.bits[OPENSPM_PAD_SLOT_A], (unsigned)map.bits[OPENSPM_PAD_SLOT_B],
                 (unsigned)map.bits[OPENSPM_PAD_SLOT_X], (unsigned)map.bits[OPENSPM_PAD_SLOT_Y],
                 (unsigned)map.bits[OPENSPM_PAD_SLOT_START], (unsigned)map.bits[OPENSPM_PAD_SLOT_BACK],
                 invalid ? ", some OPENSPM_PAD_MAP entries ignored" : "");
        backend_log(OPENSPM_LOG_INFO, message);
    }
    if (!enabled) return 0u;
    if (sdl_state != NULL) {
        unsigned buttons = 0u, lt = 0u, rt = 0u;
        int lx = 0, ly = 0;
        if (!sdl_state(&buttons, &lx, &ly, &lt, &rt)) return 0u;
        return openspm_pad_translate(&map, buttons, lx, ly, lt, rt);
    }
    if (get_state == NULL) return 0u;
    if (connected >= 0) {
        memset(&state, 0, sizeof state);
        if (get_state((DWORD)connected, &state) == ERROR_SUCCESS) {
            return openspm_pad_translate(&map, state.Gamepad.wButtons, state.Gamepad.sThumbLX, state.Gamepad.sThumbLY,
                                         state.Gamepad.bLeftTrigger, state.Gamepad.bRightTrigger);
        }
        snprintf(message, sizeof message, "OpenSPM gamepad: controller %d disconnected", connected);
        backend_log(OPENSPM_LOG_INFO, message);
        connected = -1;
        rescan_countdown = 0u;
    }
    /* Polling empty slots is slow on some systems; rescan about once a second. */
    if (rescan_countdown != 0u) {
        --rescan_countdown;
        return 0u;
    }
    rescan_countdown = 60u;
    {
        DWORD user;
        for (user = 0; user < 4u; ++user) {
            memset(&state, 0, sizeof state);
            if (get_state(user, &state) == ERROR_SUCCESS) {
                connected = (int)user;
                snprintf(message, sizeof message, "OpenSPM gamepad: XInput controller %lu connected", (unsigned long)user);
                backend_log(OPENSPM_LOG_INFO, message);
                return openspm_pad_translate(&map, state.Gamepad.wButtons, state.Gamepad.sThumbLX,
                                             state.Gamepad.sThumbLY, state.Gamepad.bLeftTrigger,
                                             state.Gamepad.bRightTrigger);
            }
        }
    }
    return 0u;
}
#endif

#endif
