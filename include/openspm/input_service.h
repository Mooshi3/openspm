/* OpenSPM input service for mods ("openspm.input", docs/MODDING.md).
 *
 * What the game sees as Wii Remote buttons this frame, host keyboard keys for
 * mod hotkeys, and (inside TICK_BEGIN) replacing this frame's buttons: input
 * display, macros, replays, accessibility remaps.
 *
 * Timing: the backend latches the Wii buttons once per game frame at its
 * PRESENT_SAFEPOINT, right before it delivers TICK_BEGIN. A set_wii_buttons()
 * call inside that TICK_BEGIN replaces the latched frame before the guest runs
 * it; "pressed" is recomputed against the previous frame so edges stay
 * consistent. Outside TICK_BEGIN set_wii_buttons() returns
 * OPENSPM_ERROR_UNSUPPORTED and changes nothing. The F1 menu still owns input
 * while it is open (the game is paused then and no tick runs).
 *
 * key_down() reads the PC keyboard by the names the F1 menu's key rebinding
 * uses (include/openspm/keymap.h: "F5", "K", "Numpad 1", ...), only while an
 * OpenSPM window has focus. Prefer keys the default game map does not use
 * (F2..F7, F9, F10, the number row); F1 (menu), F8 (GX capture), F11
 * (fullscreen), F12 (screenshot) and Tab (turbo) are taken.
 *
 * Portable C; version 1; later versions only append fields. */
#ifndef OPENSPM_INPUT_SERVICE_H
#define OPENSPM_INPUT_SERVICE_H

#include <stdint.h>

#include "openspm/abi.h"

#ifdef __cplusplus
extern "C" {
#endif

#define OPENSPM_INPUT_SERVICE_ID "openspm.input"
#define OPENSPM_INPUT_SERVICE_VERSION 1u

enum {
    OPENSPM_INPUT_CAP_READ = UINT64_C(1) << 0,
    OPENSPM_INPUT_CAP_INJECT = UINT64_C(1) << 1,
    OPENSPM_INPUT_CAP_KEYS = UINT64_C(1) << 2
};

/* Wii Remote button bits (WPAD, sideways-held remote as SPM uses it). */
#define OPENSPM_WII_BTN_LEFT UINT32_C(0x00000001)
#define OPENSPM_WII_BTN_RIGHT UINT32_C(0x00000002)
#define OPENSPM_WII_BTN_DOWN UINT32_C(0x00000004)
#define OPENSPM_WII_BTN_UP UINT32_C(0x00000008)
#define OPENSPM_WII_BTN_PLUS UINT32_C(0x00000010)
#define OPENSPM_WII_BTN_2 UINT32_C(0x00000100)
#define OPENSPM_WII_BTN_1 UINT32_C(0x00000200)
#define OPENSPM_WII_BTN_B UINT32_C(0x00000400)
#define OPENSPM_WII_BTN_A UINT32_C(0x00000800)
#define OPENSPM_WII_BTN_MINUS UINT32_C(0x00001000)
#define OPENSPM_WII_BTN_MASK UINT32_C(0x00001F1F)

typedef int(OPENSPM_CALL *openspm_input_get_wii_fn)(void *context, uint32_t *held, uint32_t *pressed);
typedef int(OPENSPM_CALL *openspm_input_set_wii_fn)(void *context, uint32_t held);
typedef int(OPENSPM_CALL *openspm_input_key_down_fn)(void *context, const char *key_name);

typedef struct openspm_input_service {
    openspm_abi_header header;
    uint64_t capabilities;
    void *context;
    /* This frame's buttons as the game sees them (after any mod override).
       Either pointer may be NULL. Any time on the guest thread. */
    openspm_input_get_wii_fn get_wii_buttons;
    /* TICK_BEGIN only: this frame's held buttons become `held` (bits outside
       OPENSPM_WII_BTN_MASK are ignored). Several mods: the last call wins. */
    openspm_input_set_wii_fn set_wii_buttons;
    /* 1 = down, 0 = up (or no OpenSPM window focused),
       OPENSPM_ERROR_INVALID_ARGUMENT = unknown key name. */
    openspm_input_key_down_fn key_down;
} openspm_input_service;

static inline int openspm_input_accept(const openspm_input_service *out, uint32_t requested_version) {
    if (out == 0) return OPENSPM_ERROR_INVALID_ARGUMENT;
    if (requested_version != OPENSPM_INPUT_SERVICE_VERSION || out->header.struct_size < sizeof(*out))
        return OPENSPM_ERROR_ABI_MISMATCH;
    return OPENSPM_OK;
}

#ifdef __cplusplus
}
#endif

#endif
