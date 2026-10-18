/* Host test for include/openspm/keymap.h ([Keyboard] map) and the pause clock
   in include/openspm/menu_control.h. */
#include "openspm/keymap.h"
#include "openspm/menu_control.h"

#include <assert.h>
#include <stdio.h>
#include <string.h>

typedef struct held_keys {
    unsigned vk[8];
    unsigned count;
} held_keys;

static int key_down(void *context, unsigned vk) {
    const held_keys *held = (const held_keys *)context;
    unsigned i;
    for (i = 0; i < held->count; ++i)
        if (held->vk[i] == vk) return 1;
    return 0;
}

static uint32_t press(const openspm_keymap *map, unsigned a, unsigned b) {
    held_keys held;
    memset(&held, 0, sizeof held);
    if (a) held.vk[held.count++] = a;
    if (b) held.vk[held.count++] = b;
    return openspm_keymap_down(map, key_down, &held);
}

int main(void) {
    openspm_keymap map, round;
    char text[512];
    unsigned i;

    /* Defaults = the mapping the backend always had (backend v9 before the map). */
    assert(openspm_keymap_parse(NULL, &map) == 0);
    assert(press(&map, 0x25 /* VK_LEFT */, 0) == OPENSPM_KEYMAP_WPAD_UP);
    assert(press(&map, 'A', 0) == OPENSPM_KEYMAP_WPAD_UP);
    assert(press(&map, 0x27 /* VK_RIGHT */, 0) == OPENSPM_KEYMAP_WPAD_DOWN && press(&map, 'D', 0) == OPENSPM_KEYMAP_WPAD_DOWN);
    assert(press(&map, 0x28 /* VK_DOWN */, 0) == OPENSPM_KEYMAP_WPAD_LEFT && press(&map, 'S', 0) == OPENSPM_KEYMAP_WPAD_LEFT);
    assert(press(&map, 0x26 /* VK_UP */, 0) == OPENSPM_KEYMAP_WPAD_RIGHT && press(&map, 'W', 0) == OPENSPM_KEYMAP_WPAD_RIGHT);
    assert(press(&map, 0x0D /* VK_RETURN */, 0) == OPENSPM_KEYMAP_WPAD_2);
    assert(press(&map, 0x20 /* VK_SPACE */, 0) == OPENSPM_KEYMAP_WPAD_A && press(&map, 'Z', 0) == OPENSPM_KEYMAP_WPAD_A);
    assert(press(&map, 'X', 0) == OPENSPM_KEYMAP_WPAD_1 && press(&map, 'C', 0) == OPENSPM_KEYMAP_WPAD_B);
    assert(press(&map, 0x08 /* VK_BACK */, 0) == OPENSPM_KEYMAP_WPAD_MINUS);
    assert(press(&map, 0x24 /* VK_HOME */, 0) == OPENSPM_KEYMAP_WPAD_HOME);
    assert(press(&map, 'P', 0x70 /* F1 */) == 0u); /* unbound; F1 is the menu key */
    assert(press(&map, 'Q', 0) == OPENSPM_KEYMAP_WPAD_SHAKE); /* the Wii Remote shake, not a game key */
    assert(press(&map, 0x20, 'W') == (OPENSPM_KEYMAP_WPAD_A | OPENSPM_KEYMAP_WPAD_RIGHT));
    (void)openspm_keymap_format(&map, text, sizeof text);
    assert(strcmp(text, "up=Up/W,down=Down/S,left=Left/A,right=Right/D,a=Space/Z,b=C,1=X,2=Enter,"
                        "minus=Backspace,plus=none,home=Home,turbo=Tab,shake=Q") == 0);
    assert(strlen(text) < 200u); /* native_settings.h free-form limit */
    assert(press(&map, 0x09 /* VK_TAB */, 0) == OPENSPM_KEYMAP_WPAD_TURBO); /* fast-forward, not a game key */
    for (i = 0; i < OPENSPM_KEY_SLOT_COUNT; ++i)
        assert(i == OPENSPM_KEY_SLOT_TURBO || (openspm_key_slot_bits[i] & OPENSPM_KEYMAP_WPAD_TURBO) == 0u);

    /* The default turbo key yields to a game binding of the same key... */
    assert(openspm_keymap_parse("a=Tab", &map) == 0);
    assert(press(&map, 0x09, 0) == OPENSPM_KEYMAP_WPAD_A);
    assert(map.keys[OPENSPM_KEY_SLOT_TURBO][0] == 0u);
    assert(openspm_keymap_parse("up=W/Up, home=Tab", &map) == 0); /* also to HOME */
    assert(press(&map, 0x09, 0) == OPENSPM_KEYMAP_WPAD_HOME);
    /* ...but a map that names turbo keeps exactly what it says. */
    assert(openspm_keymap_parse("turbo=Q/Tab,a=Tab", &map) == 0);
    assert(press(&map, 'Q', 0) == OPENSPM_KEYMAP_WPAD_TURBO);
    assert(press(&map, 0x09, 0) == (OPENSPM_KEYMAP_WPAD_TURBO | OPENSPM_KEYMAP_WPAD_A));
    assert(openspm_keymap_parse("turbo=none", &map) == 0 && press(&map, 0x09, 0) == 0u);
    /* A map saved before the turbo slot existed gets the default (Tab). */
    assert(openspm_keymap_parse("up=Up/W,down=Down/S,left=Left/A,right=Right/D,a=Space/Z,b=C,1=X,2=Enter,"
                                "minus=Backspace,plus=none,home=Home", &map) == 0);
    assert(press(&map, 0x09, 0) == OPENSPM_KEYMAP_WPAD_TURBO);

    /* Partial maps change only what they name; bad entries keep the default. */
    assert(openspm_keymap_parse(" a = J , 2=space/K, plus = P, home=none, bogus=W, b=Nope, left=F1, 1=", &map) == 3);
    assert(press(&map, 'J', 0) == OPENSPM_KEYMAP_WPAD_A && press(&map, 'Z', 0) == 0u);
    assert(press(&map, 0x20, 0) == OPENSPM_KEYMAP_WPAD_2 && press(&map, 'K', 0) == OPENSPM_KEYMAP_WPAD_2);
    assert(press(&map, 'P', 0) == OPENSPM_KEYMAP_WPAD_PLUS && press(&map, 0x24, 0) == 0u);
    assert(press(&map, 'C', 0) == OPENSPM_KEYMAP_WPAD_B);    /* b=Nope ignored */
    assert(press(&map, 0x25, 0) == OPENSPM_KEYMAP_WPAD_UP);  /* left=F1 ignored (reserved) */
    assert(press(&map, 'X', 0) == 0u);                       /* 1= empty: unbound */
    (void)openspm_keymap_format(&map, text, sizeof text);
    assert(openspm_keymap_parse(text, &round) == 0 && memcmp(&map, &round, sizeof map) == 0);

    /* Names: case-insensitive, ImGui spellings accepted, stored canonically. */
    assert(openspm_key_find("uparrow", 7) == 0x26 && openspm_key_find("LeftArrow", 9) == 0x25);
    assert(openspm_key_find("KeypadEnter", 11) == 0x0D && openspm_key_find("LeftShift", 9) == 0xA0);
    assert(openspm_key_find("Escape", 6) == 0u && openspm_key_find("F12", 3) == 0u && openspm_key_find("", 0) == 0u);
    assert(strcmp(openspm_key_name_of(0x0D), "Enter") == 0 && openspm_key_name_of(0x70) == NULL);
    for (i = 0; i < OPENSPM_KEY_NAME_COUNT; ++i) {
        const char *name = openspm_key_names[i].name;
        assert(openspm_key_find(name, strlen(name)) == openspm_key_names[i].vk);
    }
    /* Too many keys, duplicates collapse. */
    assert(openspm_keymap_parse("a=A/B/C/D/E", &map) == 1);
    assert(openspm_keymap_parse("a=A/a/B", &map) == 0 && map.keys[OPENSPM_KEY_SLOT_A][2] == 0u);

    /* Pause clock: guest time stands still while paused and resumes seamlessly. */
    {
        openspm_pause_clock clock;
        memset(&clock, 0, sizeof clock);
        assert(openspm_pause_clock_now(&clock, 1000u) == 1000u);
        openspm_pause_clock_pause(&clock, 1500u);
        assert(openspm_pause_clock_now(&clock, 1500u) == 1500u);
        assert(openspm_pause_clock_now(&clock, 9000000u) == 1500u); /* frozen */
        openspm_pause_clock_pause(&clock, 9500000u);                /* nested pause: no effect */
        openspm_pause_clock_resume(&clock, 10000000u);
        assert(openspm_pause_clock_now(&clock, 10000000u) == 1500u);  /* continues from the pause */
        assert(openspm_pause_clock_now(&clock, 10000016u) == 1516u);
        openspm_pause_clock_resume(&clock, 20000000u);                /* not paused: no effect */
        assert(openspm_pause_clock_now(&clock, 20000000u) == 10001500u);
        openspm_pause_clock_pause(&clock, 20000000u);
        openspm_pause_clock_resume(&clock, 20000000u);                /* zero-length pause */
        assert(openspm_pause_clock_now(&clock, 20000001u) == 10001501u);
    }
    puts("PASS: OpenSPM keyboard map and pause clock");
    return 0;
}
