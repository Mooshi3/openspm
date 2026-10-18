/* OpenSPM game state service for mods ("openspm.game_state", docs/MODDING.md):
 * read-only questions about the save file being played, by name instead of
 * raw addresses, so a mod can say "unlocked once you have beaten the game".
 *
 *   story(out)        GSW(0), the story progress value the game's scripts
 *                     advance (evt "swByteGet(0)"); it only grows through a
 *                     playthrough
 *   flag(n, out)      GSWF(n), n < 8192: the game's saved on/off flags
 *                     (swGet 0x80037E70)
 *   byte(n, out)      GSW(n), 0 < n < 2048: the saved byte variables
 *                     (swByteGet 0x80037F7C; n = 0 is story)
 *   pouch(out)        level, attack, hp, max hp, coins (MarioPouchWork)
 *   file_loaded()     1 while a save file is being played (in a map or
 *                     between maps), 0 on the title screen / file select
 *
 * All of it is the save as it is now in memory (what the next save writes).
 * Every call returns OPENSPM_ERROR_RUNTIME while no file is loaded (the
 * memory then holds leftovers from the title screen). Calls are for
 * TICK_BEGIN on the guest thread, like openspm.guest_memory. There is no
 * write: a mod keeps its own state in its own save space.
 *
 * R8PE01 rev 2 (US). Layout (SpmarioGlobals via r13-0x7D88 = 0x8056CB78,
 * cleared by swInit 0x80037D30): +0x140 s32 GSW(0), +0x144 u32 GSWF[256]
 * (flag n = bit n & 31 of word n >> 5), +0x544 u8 GSW[2048]. MarioPouchWork
 * 0x804D0428: +0x04 level, +0x08 attack, +0x0C hp, +0x10 max hp, +0x1C coins.
 *
 * The logic is portable C over a read32 callback (tests/m14_game_state_test.c);
 * the backend provides the service. Version 1; later versions only append. */
#ifndef OPENSPM_GAME_STATE_H
#define OPENSPM_GAME_STATE_H

#include <stddef.h>
#include <stdint.h>

#include "openspm/abi.h"

#ifdef __cplusplus
extern "C" {
#endif

#define OPENSPM_GAME_STATE_SERVICE_ID "openspm.game_state"
#define OPENSPM_GAME_STATE_SERVICE_VERSION 1u
#define OPENSPM_GAME_STATE_FLAGS 8192u
#define OPENSPM_GAME_STATE_BYTES 2048u

typedef struct openspm_game_pouch {
    openspm_abi_header header;
    int32_t level, attack, hp, max_hp, coins;
} openspm_game_pouch;

typedef int(OPENSPM_CALL *openspm_game_state_loaded_fn)(void *context);
typedef int(OPENSPM_CALL *openspm_game_state_story_fn)(void *context, int32_t *out_value);
typedef int(OPENSPM_CALL *openspm_game_state_flag_fn)(void *context, uint32_t index, int *out_value);
typedef int(OPENSPM_CALL *openspm_game_state_byte_fn)(void *context, uint32_t index, uint32_t *out_value);
typedef int(OPENSPM_CALL *openspm_game_state_pouch_fn)(void *context, openspm_game_pouch *out_pouch);

typedef struct openspm_game_state_service {
    openspm_abi_header header;
    void *context;
    openspm_game_state_loaded_fn file_loaded;
    openspm_game_state_story_fn story;
    openspm_game_state_flag_fn flag;
    openspm_game_state_byte_fn byte;
    openspm_game_state_pouch_fn pouch;
} openspm_game_state_service;

static inline int openspm_game_state_accept(const openspm_game_state_service *out, uint32_t requested_version) {
    if (out == NULL) return OPENSPM_ERROR_INVALID_ARGUMENT;
    if (requested_version != OPENSPM_GAME_STATE_SERVICE_VERSION || out->header.struct_size < sizeof(*out))
        return OPENSPM_ERROR_ABI_MISMATCH;
    return OPENSPM_OK;
}

/* ---- the reads (host side) ------------------------------------------------ */

#define OPENSPM_GAME_STATE_GP_PTR UINT32_C(0x8056CB78)
#define OPENSPM_GAME_STATE_SEQ_WORK UINT32_C(0x804D0D60)
#define OPENSPM_GAME_STATE_POUCH UINT32_C(0x804D0428)
#define OPENSPM_GAME_STATE_GSW0 0x140u
#define OPENSPM_GAME_STATE_GSWF 0x144u
#define OPENSPM_GAME_STATE_GSW 0x544u

/* read32: 1 on success (the backend's guest window). */
typedef int (*openspm_game_state_read32_fn)(void *context, uint32_t address, uint32_t *value);

/* A file is loaded: the game (2) or a map change (3). */
static inline int openspm_game_state_loaded(openspm_game_state_read32_fn read32, void *context) {
    uint32_t seq = 0u;
    return read32(context, OPENSPM_GAME_STATE_SEQ_WORK, &seq) && (seq == 2u || seq == 3u);
}

static inline int openspm_game_state_globals(openspm_game_state_read32_fn read32, void *context, uint32_t *gp) {
    if (!openspm_game_state_loaded(read32, context)) return OPENSPM_ERROR_RUNTIME;
    if (!read32(context, OPENSPM_GAME_STATE_GP_PTR, gp) || *gp < 0x80000000u || *gp >= 0x81800000u)
        return OPENSPM_ERROR_RUNTIME;
    return OPENSPM_OK;
}

static inline int openspm_game_state_story(openspm_game_state_read32_fn read32, void *context, int32_t *out) {
    uint32_t gp = 0u, v = 0u;
    int rc;
    if (out == NULL) return OPENSPM_ERROR_INVALID_ARGUMENT;
    if ((rc = openspm_game_state_globals(read32, context, &gp)) != OPENSPM_OK) return rc;
    if (!read32(context, gp + OPENSPM_GAME_STATE_GSW0, &v)) return OPENSPM_ERROR_RUNTIME;
    *out = (int32_t)v;
    return OPENSPM_OK;
}

static inline int openspm_game_state_flag(openspm_game_state_read32_fn read32, void *context, uint32_t n, int *out) {
    uint32_t gp = 0u, word = 0u;
    int rc;
    if (out == NULL || n >= OPENSPM_GAME_STATE_FLAGS) return OPENSPM_ERROR_INVALID_ARGUMENT;
    if ((rc = openspm_game_state_globals(read32, context, &gp)) != OPENSPM_OK) return rc;
    if (!read32(context, gp + OPENSPM_GAME_STATE_GSWF + 4u * (n >> 5), &word)) return OPENSPM_ERROR_RUNTIME;
    *out = (int)((word >> (n & 31u)) & 1u);
    return OPENSPM_OK;
}

static inline int openspm_game_state_byte(openspm_game_state_read32_fn read32, void *context, uint32_t n,
                                          uint32_t *out) {
    uint32_t gp = 0u, word = 0u;
    const uint32_t at = OPENSPM_GAME_STATE_GSW + n;
    int rc;
    if (out == NULL || n == 0u || n >= OPENSPM_GAME_STATE_BYTES) return OPENSPM_ERROR_INVALID_ARGUMENT;
    if ((rc = openspm_game_state_globals(read32, context, &gp)) != OPENSPM_OK) return rc;
    if (!read32(context, gp + (at & ~3u), &word)) return OPENSPM_ERROR_RUNTIME;
    *out = (word >> (24u - 8u * (at & 3u))) & 0xFFu;
    return OPENSPM_OK;
}

static inline int openspm_game_state_pouch(openspm_game_state_read32_fn read32, void *context,
                                           openspm_game_pouch *out) {
    static const uint32_t offsets[5] = {0x04u, 0x08u, 0x0Cu, 0x10u, 0x1Cu};
    int32_t *fields[5];
    uint32_t i, v;
    if (out == NULL || out->header.struct_size < sizeof(*out)) return OPENSPM_ERROR_INVALID_ARGUMENT;
    if (!openspm_game_state_loaded(read32, context)) return OPENSPM_ERROR_RUNTIME;
    fields[0] = &out->level;
    fields[1] = &out->attack;
    fields[2] = &out->hp;
    fields[3] = &out->max_hp;
    fields[4] = &out->coins;
    for (i = 0; i < 5u; ++i) {
        if (!read32(context, OPENSPM_GAME_STATE_POUCH + offsets[i], &v)) return OPENSPM_ERROR_RUNTIME;
        *fields[i] = (int32_t)v;
    }
    return OPENSPM_OK;
}

#ifdef __cplusplus
}
#endif

#endif
