/* OpenSPM keyboard map: PC keys -> sideways Wii Remote buttons.
 *
 * OpenSPM.ini [Keyboard] map (environment OPENSPM_KEY_MAP), e.g.
 *   up=Up/W,down=Down/S,left=Left/A,right=Right/D,a=Space/Z,b=C,1=X,2=Enter,
 *   minus=Backspace,plus=none,home=Home,turbo=Tab,shake=Q
 * Slots are screen directions and Wii Remote buttons; each takes up to
 * OPENSPM_KEYMAP_MAX_KEYS keys separated by '/', or "none". Entries parse over
 * the defaults (the mapping OpenSPM always had, plus Home = HOME), so a partial
 * map changes only what it names. The "home" slot never reaches the game: it
 * opens the OpenSPM menu (docs/PLAYING.md, [Controller] home_opens_menu). The
 * "turbo" slot never reaches the game either: it is the fast-forward hotkey
 * (docs/PLAYING.md "Fast-forward", [Game] turbo). Its default key (Tab) is no
 * game key; when a map binds that key to another slot and does not name
 * turbo itself, turbo drops it, so the default never collides with a game key.
 * The "shake" slot is the Wii Remote shake (stylish moves): no button either,
 * the backend turns it into an accelerometer swing; its default key (Q)
 * yields the same way.
 *
 * Keys are named like ImGui names them (the F1 menu's rebinding editor captures
 * a key through ImGui::GetKeyName and stores the canonical name here), with
 * short aliases (Up = UpArrow, Enter = Return, ...). Codes are Windows virtual
 * keys, the backend reads them with GetAsyncKeyState. F1, F8, F11, F12 and
 * Escape are OpenSPM hotkeys and cannot be bound.
 *
 * Portable C (no Windows headers): used by the backend
 * (backend/native/openspm_native_backend_v9.c), the renderer menu
 * (renderer/aurora/openspm_overlay.hpp) and tests/m14_keymap_test.c. */
#ifndef OPENSPM_KEYMAP_H
#define OPENSPM_KEYMAP_H

#include <ctype.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Raw WPAD core bits (Revolution controller contract). */
#define OPENSPM_KEYMAP_WPAD_LEFT UINT32_C(0x00000001)
#define OPENSPM_KEYMAP_WPAD_RIGHT UINT32_C(0x00000002)
#define OPENSPM_KEYMAP_WPAD_DOWN UINT32_C(0x00000004)
#define OPENSPM_KEYMAP_WPAD_UP UINT32_C(0x00000008)
#define OPENSPM_KEYMAP_WPAD_PLUS UINT32_C(0x00000010)
#define OPENSPM_KEYMAP_WPAD_2 UINT32_C(0x00000100)
#define OPENSPM_KEYMAP_WPAD_1 UINT32_C(0x00000200)
#define OPENSPM_KEYMAP_WPAD_B UINT32_C(0x00000400)
#define OPENSPM_KEYMAP_WPAD_A UINT32_C(0x00000800)
#define OPENSPM_KEYMAP_WPAD_MINUS UINT32_C(0x00001000)
#define OPENSPM_KEYMAP_WPAD_HOME UINT32_C(0x00008000) /* OpenSPM menu; stripped before the game */
/* OpenSPM-private bits (no WPAD button), stripped before the game: the
   fast-forward hotkey and the shake (an accelerometer swing, not a button). */
#define OPENSPM_KEYMAP_WPAD_TURBO UINT32_C(0x40000000)
#define OPENSPM_KEYMAP_WPAD_SHAKE UINT32_C(0x20000000)

#define OPENSPM_KEYMAP_MAX_KEYS 4

enum {
    OPENSPM_KEY_SLOT_UP, OPENSPM_KEY_SLOT_DOWN, OPENSPM_KEY_SLOT_LEFT, OPENSPM_KEY_SLOT_RIGHT,
    OPENSPM_KEY_SLOT_A, OPENSPM_KEY_SLOT_B, OPENSPM_KEY_SLOT_1, OPENSPM_KEY_SLOT_2,
    OPENSPM_KEY_SLOT_MINUS, OPENSPM_KEY_SLOT_PLUS, OPENSPM_KEY_SLOT_HOME, OPENSPM_KEY_SLOT_TURBO,
    OPENSPM_KEY_SLOT_SHAKE, OPENSPM_KEY_SLOT_COUNT
};

static const char *const openspm_key_slot_names[OPENSPM_KEY_SLOT_COUNT] = {
    "up", "down", "left", "right", "a", "b", "1", "2", "minus", "plus", "home", "turbo", "shake"};

/* SPM reads a Wii Remote held sideways, so the raw D-pad bits rotate against
   the screen (runtime proof: raw LEFT -> down, UP -> left, DOWN -> right,
   RIGHT -> up). Screen-direction slots map through the exact inverse. */
static const uint32_t openspm_key_slot_bits[OPENSPM_KEY_SLOT_COUNT] = {
    OPENSPM_KEYMAP_WPAD_RIGHT, OPENSPM_KEYMAP_WPAD_LEFT, OPENSPM_KEYMAP_WPAD_UP, OPENSPM_KEYMAP_WPAD_DOWN,
    OPENSPM_KEYMAP_WPAD_A, OPENSPM_KEYMAP_WPAD_B, OPENSPM_KEYMAP_WPAD_1, OPENSPM_KEYMAP_WPAD_2,
    OPENSPM_KEYMAP_WPAD_MINUS, OPENSPM_KEYMAP_WPAD_PLUS, OPENSPM_KEYMAP_WPAD_HOME, OPENSPM_KEYMAP_WPAD_TURBO,
    OPENSPM_KEYMAP_WPAD_SHAKE};

typedef struct openspm_key_name {
    const char *name;  /* canonical: stored in OpenSPM.ini, shown in the menu */
    const char *alias; /* ImGui key name when it differs, or another spelling */
    uint8_t vk;        /* Windows virtual-key code */
} openspm_key_name;

static const openspm_key_name openspm_key_names[] = {
    {"Up", "UpArrow", 0x26}, {"Down", "DownArrow", 0x28}, {"Left", "LeftArrow", 0x25}, {"Right", "RightArrow", 0x27},
    {"Space", NULL, 0x20}, {"Enter", "Return", 0x0D}, {"KeypadEnter", NULL, 0x0D}, {"Backspace", NULL, 0x08},
    {"Tab", NULL, 0x09}, {"Home", NULL, 0x24}, {"End", NULL, 0x23}, {"PageUp", NULL, 0x21},
    {"PageDown", NULL, 0x22}, {"Insert", NULL, 0x2D}, {"Delete", NULL, 0x2E},
    {"LeftShift", "Shift", 0xA0}, {"RightShift", NULL, 0xA1}, {"LeftCtrl", "Ctrl", 0xA2}, {"RightCtrl", NULL, 0xA3},
    {"LeftAlt", "Alt", 0xA4}, {"RightAlt", NULL, 0xA5}, {"CapsLock", NULL, 0x14},
    {"A", NULL, 0x41}, {"B", NULL, 0x42}, {"C", NULL, 0x43}, {"D", NULL, 0x44}, {"E", NULL, 0x45},
    {"F", NULL, 0x46}, {"G", NULL, 0x47}, {"H", NULL, 0x48}, {"I", NULL, 0x49}, {"J", NULL, 0x4A},
    {"K", NULL, 0x4B}, {"L", NULL, 0x4C}, {"M", NULL, 0x4D}, {"N", NULL, 0x4E}, {"O", NULL, 0x4F},
    {"P", NULL, 0x50}, {"Q", NULL, 0x51}, {"R", NULL, 0x52}, {"S", NULL, 0x53}, {"T", NULL, 0x54},
    {"U", NULL, 0x55}, {"V", NULL, 0x56}, {"W", NULL, 0x57}, {"X", NULL, 0x58}, {"Y", NULL, 0x59},
    {"Z", NULL, 0x5A},
    {"0", NULL, 0x30}, {"1", NULL, 0x31}, {"2", NULL, 0x32}, {"3", NULL, 0x33}, {"4", NULL, 0x34},
    {"5", NULL, 0x35}, {"6", NULL, 0x36}, {"7", NULL, 0x37}, {"8", NULL, 0x38}, {"9", NULL, 0x39},
    {"Keypad0", NULL, 0x60}, {"Keypad1", NULL, 0x61}, {"Keypad2", NULL, 0x62}, {"Keypad3", NULL, 0x63},
    {"Keypad4", NULL, 0x64}, {"Keypad5", NULL, 0x65}, {"Keypad6", NULL, 0x66}, {"Keypad7", NULL, 0x67},
    {"Keypad8", NULL, 0x68}, {"Keypad9", NULL, 0x69}, {"KeypadMultiply", NULL, 0x6A}, {"KeypadAdd", NULL, 0x6B},
    {"KeypadSubtract", NULL, 0x6D}, {"KeypadDecimal", NULL, 0x6E}, {"KeypadDivide", NULL, 0x6F},
    {"F2", NULL, 0x71}, {"F3", NULL, 0x72}, {"F4", NULL, 0x73}, {"F5", NULL, 0x74}, {"F6", NULL, 0x75},
    {"F7", NULL, 0x76}, {"F9", NULL, 0x78}, {"F10", NULL, 0x79},
    {"Semicolon", NULL, 0xBA}, {"Equal", NULL, 0xBB}, {"Comma", NULL, 0xBC}, {"Minus", NULL, 0xBD},
    {"Period", NULL, 0xBE}, {"Slash", NULL, 0xBF}, {"GraveAccent", NULL, 0xC0}, {"LeftBracket", NULL, 0xDB},
    {"Backslash", NULL, 0xDC}, {"RightBracket", NULL, 0xDD}, {"Apostrophe", NULL, 0xDE},
};

#define OPENSPM_KEY_NAME_COUNT (sizeof openspm_key_names / sizeof openspm_key_names[0])

typedef struct openspm_keymap {
    uint8_t keys[OPENSPM_KEY_SLOT_COUNT][OPENSPM_KEYMAP_MAX_KEYS]; /* virtual keys, 0 = unused */
} openspm_keymap;

static inline int openspm_keymap_iequal(const char *a, size_t alen, const char *b) {
    size_t i;
    if (b == NULL || alen != strlen(b)) return 0;
    for (i = 0; i < alen; ++i)
        if (tolower((unsigned char)a[i]) != tolower((unsigned char)b[i])) return 0;
    return 1;
}

/* Virtual key for a key name or alias (case-insensitive); 0 when unknown or
   reserved. */
static inline unsigned openspm_key_find(const char *name, size_t len) {
    size_t i;
    if (name == NULL) return 0u;
    for (i = 0; i < OPENSPM_KEY_NAME_COUNT; ++i)
        if (openspm_keymap_iequal(name, len, openspm_key_names[i].name) ||
            openspm_keymap_iequal(name, len, openspm_key_names[i].alias))
            return openspm_key_names[i].vk;
    return 0u;
}

/* Canonical name of a virtual key, NULL when not bindable. */
static inline const char *openspm_key_name_of(unsigned vk) {
    size_t i;
    for (i = 0; i < OPENSPM_KEY_NAME_COUNT; ++i)
        if (openspm_key_names[i].vk == vk) return openspm_key_names[i].name;
    return NULL;
}

/* The mapping OpenSPM always had: Arrows/WASD = D-pad, Space/Z = A, C = B,
   X = 1, Enter = 2, Backspace = Minus; plus Home = HOME (the OpenSPM menu),
   Tab = turbo (fast-forward while held) and Q = shake. */
static inline void openspm_keymap_default(openspm_keymap *map) {
    memset(map, 0, sizeof *map);
    map->keys[OPENSPM_KEY_SLOT_UP][0] = 0x26;    map->keys[OPENSPM_KEY_SLOT_UP][1] = 'W';
    map->keys[OPENSPM_KEY_SLOT_DOWN][0] = 0x28;  map->keys[OPENSPM_KEY_SLOT_DOWN][1] = 'S';
    map->keys[OPENSPM_KEY_SLOT_LEFT][0] = 0x25;  map->keys[OPENSPM_KEY_SLOT_LEFT][1] = 'A';
    map->keys[OPENSPM_KEY_SLOT_RIGHT][0] = 0x27; map->keys[OPENSPM_KEY_SLOT_RIGHT][1] = 'D';
    map->keys[OPENSPM_KEY_SLOT_A][0] = 0x20;     map->keys[OPENSPM_KEY_SLOT_A][1] = 'Z';
    map->keys[OPENSPM_KEY_SLOT_B][0] = 'C';
    map->keys[OPENSPM_KEY_SLOT_1][0] = 'X';
    map->keys[OPENSPM_KEY_SLOT_2][0] = 0x0D;
    map->keys[OPENSPM_KEY_SLOT_MINUS][0] = 0x08;
    map->keys[OPENSPM_KEY_SLOT_HOME][0] = 0x24;
    map->keys[OPENSPM_KEY_SLOT_TURBO][0] = 0x09;
    map->keys[OPENSPM_KEY_SLOT_SHAKE][0] = 'Q';
}

/* Is vk bound to any slot other than `except`? */
static inline int openspm_keymap_bound_elsewhere(const openspm_keymap *map, int except, unsigned vk) {
    int slot, i;
    for (slot = 0; slot < OPENSPM_KEY_SLOT_COUNT; ++slot)
        for (i = 0; slot != except && i < OPENSPM_KEYMAP_MAX_KEYS; ++i)
            if (map->keys[slot][i] == vk) return 1;
    return 0;
}

/* Remove vk from one slot (keeps the remaining keys in order). */
static inline void openspm_keymap_unbind(openspm_keymap *map, int slot, unsigned vk) {
    int i, out = 0;
    uint8_t kept[OPENSPM_KEYMAP_MAX_KEYS] = {0};
    for (i = 0; i < OPENSPM_KEYMAP_MAX_KEYS; ++i)
        if (map->keys[slot][i] != 0u && map->keys[slot][i] != vk) kept[out++] = map->keys[slot][i];
    memcpy(map->keys[slot], kept, OPENSPM_KEYMAP_MAX_KEYS);
}

static inline void openspm_keymap_trim(const char **text, size_t *len) {
    while (*len > 0 && isspace((unsigned char)**text)) { ++*text; --*len; }
    while (*len > 0 && isspace((unsigned char)(*text)[*len - 1])) --*len;
}

/* Parses "value" ("Up/W", "none") into keys[]. Returns 0 when any key is
   unknown (keys[] untouched). */
static inline int openspm_keymap_parse_keys(const char *value, size_t len, uint8_t keys[OPENSPM_KEYMAP_MAX_KEYS]) {
    uint8_t parsed[OPENSPM_KEYMAP_MAX_KEYS] = {0};
    int count = 0;
    openspm_keymap_trim(&value, &len);
    if (len == 0 || openspm_keymap_iequal(value, len, "none")) {
        memset(keys, 0, OPENSPM_KEYMAP_MAX_KEYS);
        return 1;
    }
    while (len > 0) {
        const char *slash = (const char *)memchr(value, '/', len);
        size_t part = slash != NULL ? (size_t)(slash - value) : len;
        const char *name = value;
        size_t name_len = part;
        unsigned vk;
        int i, dup = 0;
        openspm_keymap_trim(&name, &name_len);
        vk = openspm_key_find(name, name_len);
        if (vk == 0u) return 0;
        for (i = 0; i < count; ++i) dup |= parsed[i] == vk;
        if (!dup) {
            if (count == OPENSPM_KEYMAP_MAX_KEYS) return 0;
            parsed[count++] = (uint8_t)vk;
        }
        if (slash == NULL) break;
        value = slash + 1;
        len -= part + 1u;
    }
    memcpy(keys, parsed, OPENSPM_KEYMAP_MAX_KEYS);
    return 1;
}

/* Parses "slot=keys,..." over the defaults. Returns the number of invalid
   entries (ignored: that slot keeps its default). */
static inline int openspm_keymap_parse(const char *text, openspm_keymap *map) {
    int invalid = 0, named_turbo = 0, named_shake = 0, i;
    openspm_keymap_default(map);
    while (text != NULL && *text) {
        const char *end = strchr(text, ',');
        size_t len = end ? (size_t)(end - text) : strlen(text);
        const char *eq = (const char *)memchr(text, '=', len);
        if (eq != NULL) {
            const char *name = text;
            size_t name_len = (size_t)(eq - text);
            int slot, found = 0;
            openspm_keymap_trim(&name, &name_len);
            for (slot = 0; slot < OPENSPM_KEY_SLOT_COUNT; ++slot) {
                if (openspm_keymap_iequal(name, name_len, openspm_key_slot_names[slot])) {
                    found = openspm_keymap_parse_keys(eq + 1, len - (size_t)(eq - text) - 1u, map->keys[slot]);
                    named_turbo |= found && slot == OPENSPM_KEY_SLOT_TURBO;
                    named_shake |= found && slot == OPENSPM_KEY_SLOT_SHAKE;
                    break;
                }
            }
            if (!found) ++invalid;
        } else {
            const char *rest = text;
            size_t rest_len = len;
            openspm_keymap_trim(&rest, &rest_len);
            if (rest_len > 0) ++invalid;
        }
        if (!end) break;
        text = end + 1;
    }
    /* The default turbo and shake keys yield to a key the map gives the game. */
    for (i = OPENSPM_KEYMAP_MAX_KEYS - 1; !named_turbo && i >= 0; --i) {
        const unsigned vk = map->keys[OPENSPM_KEY_SLOT_TURBO][i];
        if (vk != 0u && openspm_keymap_bound_elsewhere(map, OPENSPM_KEY_SLOT_TURBO, vk))
            openspm_keymap_unbind(map, OPENSPM_KEY_SLOT_TURBO, vk);
    }
    for (i = OPENSPM_KEYMAP_MAX_KEYS - 1; !named_shake && i >= 0; --i) {
        const unsigned vk = map->keys[OPENSPM_KEY_SLOT_SHAKE][i];
        if (vk != 0u && openspm_keymap_bound_elsewhere(map, OPENSPM_KEY_SLOT_SHAKE, vk))
            openspm_keymap_unbind(map, OPENSPM_KEY_SLOT_SHAKE, vk);
    }
    return invalid;
}

/* "Up/W" or "none" for one slot. */
static inline void openspm_keymap_format_slot(const openspm_keymap *map, int slot, char *out, size_t capacity) {
    size_t used = 0;
    int i;
    if (capacity == 0) return;
    out[0] = '\0';
    for (i = 0; i < OPENSPM_KEYMAP_MAX_KEYS; ++i) {
        const char *name = openspm_key_name_of(map->keys[slot][i]);
        int n;
        if (map->keys[slot][i] == 0u || name == NULL) continue;
        n = snprintf(out + used, capacity - used, "%s%s", used ? "/" : "", name);
        if (n < 0 || (size_t)n >= capacity - used) break;
        used += (size_t)n;
    }
    if (used == 0) snprintf(out, capacity, "none");
}

/* Every slot, explicitly (the file then reads the same whatever the defaults).
   Returns the length written (fits in 200 chars: native_settings.h limit). */
static inline size_t openspm_keymap_format(const openspm_keymap *map, char *out, size_t capacity) {
    size_t used = 0;
    int slot;
    if (capacity == 0) return 0;
    out[0] = '\0';
    for (slot = 0; slot < OPENSPM_KEY_SLOT_COUNT; ++slot) {
        char keys[64];
        int n;
        openspm_keymap_format_slot(map, slot, keys, sizeof keys);
        n = snprintf(out + used, capacity - used, "%s%s=%s", slot ? "," : "", openspm_key_slot_names[slot], keys);
        if (n < 0 || (size_t)n >= capacity - used) break;
        used += (size_t)n;
    }
    return used;
}

/* WPAD bits for the keys held now (key_down(vk) != 0). HOME appears as
   OPENSPM_KEYMAP_WPAD_HOME and turbo as OPENSPM_KEYMAP_WPAD_TURBO; the caller
   strips both before the game. */
typedef int (*openspm_keymap_key_down_fn)(void *context, unsigned vk);
static inline uint32_t openspm_keymap_down(const openspm_keymap *map, openspm_keymap_key_down_fn key_down,
                                           void *context) {
    uint32_t down = 0u;
    int slot, i;
    for (slot = 0; slot < OPENSPM_KEY_SLOT_COUNT; ++slot)
        for (i = 0; i < OPENSPM_KEYMAP_MAX_KEYS; ++i)
            if (map->keys[slot][i] != 0u && key_down(context, map->keys[slot][i])) {
                down |= openspm_key_slot_bits[slot];
                break;
            }
    return down;
}

#ifdef __cplusplus
}
#endif

#endif
