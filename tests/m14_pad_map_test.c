#include "../backend/native/openspm_pad_xinput.h"

#include <assert.h>
#include <stdio.h>

int main(void) {
    openspm_pad_map map;
    assert(openspm_pad_parse_map(NULL, &map) == 0);
    assert(map.bits[OPENSPM_PAD_SLOT_A] == OPENSPM_PAD_WPAD_2 && map.bits[OPENSPM_PAD_SLOT_START] == OPENSPM_PAD_WPAD_PLUS);
    /* Screen directions follow the keyboard's sideways-remote rotation. */
    assert(openspm_pad_translate(&map, OPENSPM_XI_DPAD_LEFT, 0, 0, 0, 0) == OPENSPM_PAD_WPAD_UP);
    assert(openspm_pad_translate(&map, 0, 30000, 0, 0, 0) == OPENSPM_PAD_WPAD_DOWN);
    assert(openspm_pad_translate(&map, 0, 0, -30000, 0, 0) == OPENSPM_PAD_WPAD_LEFT);
    assert(openspm_pad_translate(&map, OPENSPM_XI_DPAD_UP, 0, 0, 0, 0) == OPENSPM_PAD_WPAD_RIGHT);
    assert(openspm_pad_translate(&map, 0, 8000, -8000, 0, 0) == 0u); /* inside the deadzone */
    assert(openspm_pad_translate(&map, OPENSPM_XI_A | OPENSPM_XI_Y, 0, 0, 0, 0) ==
           (OPENSPM_PAD_WPAD_2 | OPENSPM_PAD_WPAD_A));
    assert(openspm_pad_parse_map(" a = 1 , rt=2,lb=minus, x=none, bogus=1, y=Q", &map) == 2);
    assert(map.bits[OPENSPM_PAD_SLOT_A] == OPENSPM_PAD_WPAD_1 && map.bits[OPENSPM_PAD_SLOT_RT] == OPENSPM_PAD_WPAD_2);
    assert(map.bits[OPENSPM_PAD_SLOT_LB] == OPENSPM_PAD_WPAD_MINUS && map.bits[OPENSPM_PAD_SLOT_X] == 0u);
    assert(map.bits[OPENSPM_PAD_SLOT_Y] == OPENSPM_PAD_WPAD_A); /* invalid value keeps the default */
    assert(openspm_pad_translate(&map, 0, 0, 0, 0, 200) == OPENSPM_PAD_WPAD_2);
    /* Guide is always HOME (the OpenSPM menu), whatever the map says. */
    assert(openspm_pad_translate(&map, OPENSPM_XI_GUIDE, 0, 0, 0, 0) == OPENSPM_PAD_WPAD_HOME);
    /* Right stick click = fast-forward (never a game button) unless remapped. */
    assert(openspm_pad_parse_map(NULL, &map) == 0);
    assert(openspm_pad_translate(&map, OPENSPM_XI_RTHUMB, 0, 0, 0, 0) == OPENSPM_PAD_WPAD_TURBO);
    assert(openspm_pad_parse_map("rs=none,lb=turbo", &map) == 0);
    assert(openspm_pad_translate(&map, OPENSPM_XI_RTHUMB, 0, 0, 0, 0) == 0u);
    assert(openspm_pad_translate(&map, OPENSPM_XI_LB, 0, 0, 0, 0) == OPENSPM_PAD_WPAD_TURBO);
    /* RB = the Wii Remote shake by default (never a game button). */
    assert(openspm_pad_parse_map(NULL, &map) == 0);
    assert(openspm_pad_translate(&map, OPENSPM_XI_RB, 0, 0, 0, 0) == OPENSPM_PAD_WPAD_SHAKE);
    assert(openspm_pad_parse_map("a=NONE,b=NONE,x=NONE,y=NONE,start=NONE,back=NONE,rb=NONE,rs=NONE", &map) == 0);
    assert(openspm_pad_translate(&map, 0xFFFFu & ~OPENSPM_XI_GUIDE & ~0x000Fu, 0, 0, 0, 0) == 0u);
    puts("PASS: OpenSPM gamepad map");
    return 0;
}
