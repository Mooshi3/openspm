/* Host test for include/openspm/fst_overlay.h: a small disc file table gets
   new files (in existing and new directories); every original entry keeps
   its position and length, the new ones get positions no disc file has, and
   paths the game already has (any case), or that collide with a directory,
   are refused. */
#include "openspm/fst_overlay.h"

#include <assert.h>
#include <stdio.h>

/* root
     a/              (dir)
       p_wii_mario   pos 0x100 len 50
     map/            (dir)
       he1_01.bin    pos 0x200 len 1000
       mac_01.bin    pos 0x300 len 2000
     opening.bnr     pos 0x50 len 64 */
static uint32_t build(uint8_t *fst) {
    static const char names[] = "\0a\0p_wii_mario\0map\0he1_01.bin\0mac_01.bin\0opening.bnr";
    const uint32_t n_a = 1, n_mario = 3, n_map = 15, n_he = 19, n_mac = 30, n_bnr = 41;
    const uint32_t e[7][3] = {
        {0x01000000u, 0, 7},
        {0x01000000u | n_a, 0, 3},
        {n_mario, 0x100, 50},
        {0x01000000u | n_map, 0, 6},
        {n_he, 0x200, 1000},
        {n_mac, 0x300, 2000},
        {n_bnr, 0x50, 64},
    };
    uint32_t i;
    for (i = 0; i < 7; ++i) {
        openspm_fst_put32(fst + 12 * i, e[i][0]);
        openspm_fst_put32(fst + 12 * i + 4, e[i][1]);
        openspm_fst_put32(fst + 12 * i + 8, e[i][2]);
    }
    memcpy(fst + 84, names, sizeof names);
    return 84u + (uint32_t)sizeof names;
}

static void file_at(const uint8_t *fst, uint32_t size, const char *path, uint32_t *pos, uint32_t *len) {
    const int32_t i = openspm_fst_find(fst, size, path);
    assert(i > 0 && !(fst[12 * i] & 1u));
    *pos = openspm_fst_be32(fst + 12 * i + 4);
    *len = openspm_fst_be32(fst + 12 * i + 8);
}

int main(void) {
    uint8_t fst[256], out[1024];
    uint32_t size = build(fst), out_size = 0, pos, len, count, i;
    openspm_fst_addition adds[] = {
        {"map/os1_01.bin", 4321, 0},
        {"setup/os1_01.dat", 11204, 0},
        {"msg/US/os1.txt", 99, 0},
        {"map/he1_01.bin", 5, 0},  /* the game's: refused */
        {"MAP/Mac_01.bin", 5, 0},  /* the game's, other case: refused */
        {"map", 5, 0},             /* a directory: refused */
        {"opening.bnr/x", 5, 0},   /* a file where a directory would be: refused */
        {"a/e_kamere_custom", 7, 0},
    };
    assert(openspm_fst_valid(fst, size, &count) && count == 7);
    assert(openspm_fst_find(fst, size, "map/he1_01.bin") == 4 && openspm_fst_find(fst, size, "/MAP/HE1_01.BIN") == 4);
    assert(openspm_fst_find(fst, size, "map/nope.bin") == -1 && openspm_fst_find(fst, size, "a") == 1);

    assert(openspm_fst_extend(fst, size, adds, 8, out, sizeof out, &out_size) == OPENSPM_OK);
    assert(openspm_fst_valid(out, out_size, &count) && count == 7 + 5 + 2); /* 5 files + dirs setup, msg, US = 3 */
    (void)count;
    /* The game's files: unchanged. */
    file_at(out, out_size, "a/p_wii_mario", &pos, &len);
    assert(pos == 0x100 && len == 50);
    file_at(out, out_size, "map/he1_01.bin", &pos, &len);
    assert(pos == 0x200 && len == 1000);
    file_at(out, out_size, "map/mac_01.bin", &pos, &len);
    assert(pos == 0x300 && len == 2000);
    file_at(out, out_size, "opening.bnr", &pos, &len);
    assert(pos == 0x50 && len == 64);
    /* The additions. */
    assert(adds[3].position == 0 && adds[4].position == 0 && adds[5].position == 0 && adds[6].position == 0);
    for (i = 0; i < 8; ++i) {
        if (adds[i].position == 0) continue;
        assert(adds[i].position >= OPENSPM_FST_OVERLAY_FIRST_POSITION);
        file_at(out, out_size, adds[i].path, &pos, &len);
        assert(pos == adds[i].position && len == adds[i].length);
    }
    assert(adds[0].position != adds[1].position && adds[1].position != adds[2].position &&
           adds[2].position != adds[7].position);
    assert(openspm_fst_find(out, out_size, "msg/US") > 0 && (out[12 * openspm_fst_find(out, out_size, "msg/US")] & 1u));
    /* Too small an output: an error, nothing claimed. */
    assert(openspm_fst_extend(fst, size, adds, 8, out, 100, &out_size) == OPENSPM_ERROR_RUNTIME);
    /* Not a table. */
    memset(out, 0, 16);
    assert(openspm_fst_extend(out, 16, adds, 1, out + 16, 512, &out_size) == OPENSPM_ERROR_INVALID_ARGUMENT);
    puts("PASS: fst overlay");
    return 0;
}
