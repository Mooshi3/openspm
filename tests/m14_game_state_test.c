/* Host test for openspm.game_state (include/openspm/game_state.h): story,
   flags (bit n & 31 of word n >> 5, as swGet reads them), byte variables
   (index 0 refused: that is story), the pouch, and every call refusing while
   no save file is loaded (title screen leftovers). */
#include "openspm/game_state.h"

#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MEM1_SIZE 0x01800000u
#define GP 0x80600000u
static uint8_t *g_mem;

static void w32(uint32_t a, uint32_t v) {
    uint8_t *p = g_mem + (a - 0x80000000u);
    p[0] = (uint8_t)(v >> 24); p[1] = (uint8_t)(v >> 16); p[2] = (uint8_t)(v >> 8); p[3] = (uint8_t)v;
}
static int rd(void *c, uint32_t a, uint32_t *v) {
    const uint8_t *p;
    (void)c;
    if (a < 0x80000000u || a + 4u > 0x80000000u + MEM1_SIZE) return 0;
    p = g_mem + (a - 0x80000000u);
    *v = (uint32_t)p[0] << 24 | (uint32_t)p[1] << 16 | (uint32_t)p[2] << 8 | p[3];
    return 1;
}

int main(void) {
    int32_t story = 0;
    int flag = -1;
    uint32_t byte = 0u;
    openspm_game_pouch pouch;
    g_mem = calloc(1, MEM1_SIZE);
    assert(g_mem);
    w32(OPENSPM_GAME_STATE_GP_PTR, GP);
    w32(GP + OPENSPM_GAME_STATE_GSW0, 423u);
    w32(GP + OPENSPM_GAME_STATE_GSWF + 4u * 3u, 1u << 5);  /* flag 3 * 32 + 5 = 101 */
    g_mem[GP + OPENSPM_GAME_STATE_GSW + 7u - 0x80000000u] = 200u;
    w32(OPENSPM_GAME_STATE_POUCH + 0x04u, 12u);
    w32(OPENSPM_GAME_STATE_POUCH + 0x08u, 3u);
    w32(OPENSPM_GAME_STATE_POUCH + 0x0Cu, 25u);
    w32(OPENSPM_GAME_STATE_POUCH + 0x10u, 40u);
    w32(OPENSPM_GAME_STATE_POUCH + 0x1Cu, 999u);
    memset(&pouch, 0, sizeof pouch);
    pouch.header.struct_size = sizeof pouch;

    /* Title screen (sequence 1): no file, nothing answers. */
    w32(OPENSPM_GAME_STATE_SEQ_WORK, 1u);
    assert(!openspm_game_state_loaded(rd, NULL));
    assert(openspm_game_state_story(rd, NULL, &story) == OPENSPM_ERROR_RUNTIME);
    assert(openspm_game_state_flag(rd, NULL, 101u, &flag) == OPENSPM_ERROR_RUNTIME);
    assert(openspm_game_state_byte(rd, NULL, 7u, &byte) == OPENSPM_ERROR_RUNTIME);
    assert(openspm_game_state_pouch(rd, NULL, &pouch) == OPENSPM_ERROR_RUNTIME);

    /* In a map, and between maps. */
    w32(OPENSPM_GAME_STATE_SEQ_WORK, 2u);
    assert(openspm_game_state_loaded(rd, NULL));
    assert(openspm_game_state_story(rd, NULL, &story) == OPENSPM_OK && story == 423);
    assert(openspm_game_state_flag(rd, NULL, 101u, &flag) == OPENSPM_OK && flag == 1);
    assert(openspm_game_state_flag(rd, NULL, 100u, &flag) == OPENSPM_OK && flag == 0);
    assert(openspm_game_state_flag(rd, NULL, 8192u, &flag) == OPENSPM_ERROR_INVALID_ARGUMENT);
    assert(openspm_game_state_byte(rd, NULL, 7u, &byte) == OPENSPM_OK && byte == 200u);
    assert(openspm_game_state_byte(rd, NULL, 6u, &byte) == OPENSPM_OK && byte == 0u);
    assert(openspm_game_state_byte(rd, NULL, 0u, &byte) == OPENSPM_ERROR_INVALID_ARGUMENT); /* use story */
    assert(openspm_game_state_byte(rd, NULL, 2048u, &byte) == OPENSPM_ERROR_INVALID_ARGUMENT);
    w32(OPENSPM_GAME_STATE_SEQ_WORK, 3u);
    assert(openspm_game_state_pouch(rd, NULL, &pouch) == OPENSPM_OK);
    assert(pouch.level == 12 && pouch.attack == 3 && pouch.hp == 25 && pouch.max_hp == 40 && pouch.coins == 999);

    /* A broken globals pointer: refused, not read through. */
    w32(OPENSPM_GAME_STATE_GP_PTR, 0u);
    assert(openspm_game_state_story(rd, NULL, &story) == OPENSPM_ERROR_RUNTIME);
    free(g_mem);
    puts("PASS: OpenSPM game state");
    return 0;
}
