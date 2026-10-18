/* Host test for include/openspm/content_maps.h: a registered map's record
   (name, file, fallback door, spawn position, init script) lands in a spare
   registry slot, a lookup like the game's (first name match) finds it, the
   game's maps keep theirs; bad definitions, a game map's name, a build
   without the registry patches and a full registry are refused; go_to_map
   queues the game's map change; on a registered map with a camera the
   main camera follows the player (side on in 2D within the x range, from
   the left in 3D after the swing), and is left alone on other maps and
   when the map has a camera road. */
#include "openspm/content_maps.h"
#include "openspm/guest_call.h"

#include <assert.h>
#include <math.h>
#include <stdlib.h>

#define MEM1 0x01800000u
static uint8_t *g_mem1, *g_mem2;
static openspm_guest_call_queue g_calls;
static uint32_t g_scratch_used;
static char g_log[4096];

static uint8_t *P(uint32_t a) {
    if (a >= 0x80000000u && a < 0x80000000u + MEM1) return g_mem1 + (a - 0x80000000u);
    if (a >= 0x90000000u && a < 0x94000000u) return g_mem2 + (a - 0x90000000u);
    return NULL;
}
static void w32(uint32_t a, uint32_t v) { uint8_t *p = P(a); p[0] = v >> 24; p[1] = v >> 16; p[2] = v >> 8; p[3] = v; }
static uint32_t r32(uint32_t a) { const uint8_t *p = P(a); return (uint32_t)p[0] << 24 | p[1] << 16 | p[2] << 8 | p[3]; }
static float rf(uint32_t a) { const uint32_t w = r32(a); float f; memcpy(&f, &w, 4); return f; }
static void wf(uint32_t a, float f) { uint32_t w; memcpy(&w, &f, 4); w32(a, w); }
static int near(float a, float b) { return fabsf(a - b) < 0.01f; }
static const char *str(uint32_t a) { return (const char *)P(a); }
static int f_read32(void *c, uint32_t a, uint32_t *v) { (void)c; if (!P(a)) return OPENSPM_ERROR_INVALID_ARGUMENT; *v = r32(a); return OPENSPM_OK; }
static int f_write32(void *c, uint32_t a, uint32_t v) { (void)c; if (!P(a)) return OPENSPM_ERROR_INVALID_ARGUMENT; w32(a, v); return OPENSPM_OK; }
static int f_call(void *c, uint32_t t, uint32_t n, const uint32_t *args) { (void)c; return openspm_guest_call_push(&g_calls, t, n, args); }
static int f_scratch(void *c, uint32_t s, uint32_t *o) { (void)c; return openspm_guest_scratch_take(&g_scratch_used, s, o); }
static void f_log(void *c, int warn, const char *t) { (void)c; (void)warn; strncat(g_log, t, sizeof g_log - strlen(g_log) - 1); }
static const openspm_roster_io k_io = {NULL, f_read32, f_write32, f_call, f_scratch, NULL, NULL, f_log};

/* The game's lookup: the first slot whose record's name matches. */
static uint32_t game_lookup(const char *name) {
    uint32_t i;
    for (i = 0; i < OPENSPM_MAPS_SLOTS; ++i) {
        const uint32_t rec = r32(OPENSPM_MAPS_REGISTRY + 4u * i);
        if (rec && strcmp(str(r32(rec)), name) == 0) return rec;
    }
    return 0u;
}

static openspm_map_def def(const char *key, const char *name) {
    openspm_map_def d;
    memset(&d, 0, sizeof d);
    d.header.struct_size = sizeof d;
    d.key = key;
    d.name = name;
    return d;
}

int main(void) {
    openspm_maps s;
    openspm_map_def d;
    char why[160];
    uint32_t slot = 0, i, rec;
    float f;
    g_mem1 = calloc(1, MEM1);
    g_mem2 = calloc(1, 0x04000000u);
    assert(g_mem1 && g_mem2);
    /* The game's 468 maps ("g000_00" ...) and the spare slots (slot 0's). */
    for (i = 0; i < OPENSPM_MAPS_GAME_COUNT; ++i) {
        const uint32_t r = 0x80400000u + 0x40u * i;
        snprintf((char *)P(r + 0x20u), 16, "g%03u_00", (unsigned)i);
        w32(r, r + 0x20u);
        w32(OPENSPM_MAPS_REGISTRY + 4u * i, r);
    }
    for (; i < OPENSPM_MAPS_SLOTS; ++i) w32(OPENSPM_MAPS_REGISTRY + 4u * i, r32(OPENSPM_MAPS_REGISTRY));
    openspm_maps_init(&s);

    /* Without the registry patches: refused. */
    d = def("m:level", "os1_01");
    assert(openspm_maps_register(&s, &k_io, &d, &slot, why, sizeof why) == OPENSPM_ERROR_UNSUPPORTED);
    w32(OPENSPM_MAPS_LOOKUP_LIS, OPENSPM_MAPS_LOOKUP_LIS_MOVED);

    /* Bad definitions. */
    d = def("m:x", "OS1_01");
    assert(openspm_maps_register(&s, &k_io, &d, &slot, why, sizeof why) == OPENSPM_ERROR_INVALID_ARGUMENT);
    d = def("m:x", "os1");
    assert(openspm_maps_register(&s, &k_io, &d, &slot, why, sizeof why) == OPENSPM_ERROR_INVALID_ARGUMENT);
    d = def("m:x", "g012_00"); /* a game map's name */
    assert(openspm_maps_register(&s, &k_io, &d, &slot, why, sizeof why) == OPENSPM_ERROR_INVALID_ARGUMENT);
    d = def("m:x", "os1_01");
    d.init_script = 0x1234u;
    assert(openspm_maps_register(&s, &k_io, &d, &slot, why, sizeof why) == OPENSPM_ERROR_INVALID_ARGUMENT);

    /* A map. */
    d = def("m:level", "os1_01");
    d.file = "os1_01_map";
    d.fallback_door = "dokan_1";
    d.spawn[0] = -400.0f;
    d.spawn[1] = 25.0f;
    d.init_script = 0x93F01000u;
    assert(openspm_maps_register(&s, &k_io, &d, &slot, why, sizeof why) == OPENSPM_OK);
    assert(slot == OPENSPM_MAPS_GAME_COUNT);
    rec = game_lookup("os1_01");
    assert(rec != 0u && rec == r32(OPENSPM_MAPS_REGISTRY + 4u * slot));
    assert(strcmp(str(r32(rec)), "os1_01") == 0 && strcmp(str(r32(rec + 4u)), "os1_01_map") == 0 &&
           strcmp(str(r32(rec + 8u)), "dokan_1") == 0 && r32(rec + 0x18u) == 0x93F01000u);
    {
        const uint32_t b = r32(rec + 0x0Cu);
        memcpy(&f, &b, 4);
        assert(f == -400.0f);
    }
    /* The game's maps keep their records; slot 0's copies never match first. */
    assert(game_lookup("g000_00") == r32(OPENSPM_MAPS_REGISTRY) && game_lookup("g467_00") == 0x80400000u + 0x40u * 467u);
    /* No file: the name; no door: NULL. */
    d = def("m:second", "os1_02");
    assert(openspm_maps_register(&s, &k_io, &d, &slot, why, sizeof why) == OPENSPM_OK && slot == 469u);
    rec = game_lookup("os1_02");
    assert(strcmp(str(r32(rec + 4u)), "os1_02") == 0 && r32(rec + 8u) == 0u);
    /* Twice: refused. */
    assert(openspm_maps_register(&s, &k_io, &d, &slot, why, sizeof why) == OPENSPM_ERROR_RUNTIME);
    /* Full: every spare slot taken. */
    for (i = 470; i < OPENSPM_MAPS_SLOTS; ++i) w32(OPENSPM_MAPS_REGISTRY + 4u * i, 0x80400040u); /* someone's */
    d = def("m:third", "os1_03");
    assert(openspm_maps_register(&s, &k_io, &d, &slot, why, sizeof why) == OPENSPM_ERROR_RUNTIME);

    /* Going there: seqSetSeq(3, "os1_01", "dokan_1"); an unknown map: refused. */
    assert(openspm_maps_go(&k_io, "os1_01", "dokan_1") == OPENSPM_OK);
    {
        openspm_guest_call_entry call;
        assert(openspm_guest_call_pop(&g_calls, &call) && call.target == OPENSPM_MAPS_SEQ_SET && call.argc == 3u &&
               call.args[0] == SPM_R8PE01_SEQ_MAPCHANGE && strcmp(str(call.args[1]), "os1_01") == 0 &&
               strcmp(str(call.args[2]), "dokan_1") == 0);
    }
    assert(openspm_maps_go(&k_io, "os1_02", NULL) == OPENSPM_OK);
    {
        openspm_guest_call_entry call;
        assert(openspm_guest_call_pop(&g_calls, &call) && call.args[2] == 0u);
    }
    assert(openspm_maps_go(&k_io, "zz9_99", NULL) == OPENSPM_ERROR_NOT_FOUND);

    /* With the game's file table readable: a map whose file is missing is
       refused, one whose file is there (map/<file>.bin) is taken. */
    {
        /* root, map/ (dir), map/os1_06.bin */
        static const char names[] = "\0map\0os1_06.bin";
        uint8_t fst[64];
        uint32_t k, at = 0x80700000u;
        const uint32_t e[3][3] = {{0x01000000u, 0, 3}, {0x01000000u | 1u, 0, 3}, {5u, 0x3C000000u, 100u}};
        for (k = 0; k < 3; ++k) {
            openspm_fst_put32(fst + 12 * k, e[k][0]);
            openspm_fst_put32(fst + 12 * k + 4, e[k][1]);
            openspm_fst_put32(fst + 12 * k + 8, e[k][2]);
        }
        memcpy(fst + 36, names, sizeof names);
        memcpy(P(at), fst, 36 + sizeof names);
        w32(OPENSPM_MAPS_FST_PTR, at);
        w32(OPENSPM_MAPS_FST_PTR + 4u, 36u + (uint32_t)sizeof names);
        for (i = 470; i < OPENSPM_MAPS_SLOTS; ++i) w32(OPENSPM_MAPS_REGISTRY + 4u * i, r32(OPENSPM_MAPS_REGISTRY));
        d = def("m:nofile", "os1_07");
        assert(openspm_maps_register(&s, &k_io, &d, &slot, why, sizeof why) == OPENSPM_ERROR_NOT_FOUND);
        assert(strstr(why, "map/os1_07.bin") != NULL);
        d = def("m:file", "os1_06");
        assert(openspm_maps_register(&s, &k_io, &d, &slot, why, sizeof why) == OPENSPM_OK);
        w32(OPENSPM_MAPS_FST_PTR, 0u); /* the rest of the test: no table */
    }

    /* The camera. A bad camera: refused. */
    for (i = 470; i < OPENSPM_MAPS_SLOTS; ++i) w32(OPENSPM_MAPS_REGISTRY + 4u * i, r32(OPENSPM_MAPS_REGISTRY));
    d = def("m:cam_bad", "os1_09");
    d.camera_distance = 50.0f;
    assert(openspm_maps_register(&s, &k_io, &d, &slot, why, sizeof why) == OPENSPM_ERROR_INVALID_ARGUMENT);
    d = def("m:cam", "os1_05");
    d.camera_distance = 700.0f;
    d.camera_height = 70.0f;
    d.camera_look_height = 60.0f;
    d.camera_x_range[0] = -400.0f;
    d.camera_x_range[1] = 1200.0f;
    assert(openspm_maps_register(&s, &k_io, &d, &slot, why, sizeof why) == OPENSPM_OK);
    {
        const uint32_t gp = 0x80500000u, cam = 0x80510000u, mario = SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_POSITION;
        int k;
        w32(OPENSPM_MAPS_GP_PTR, gp);
        w32(OPENSPM_MAPS_CAMERA_3D, cam);
        w32(cam + OPENSPM_MAPS_CAMERA_MODE, 0u); /* no road, 2D */
        wf(cam + OPENSPM_MAPS_CAMERA_POS, 5.0f);
        wf(mario, -600.0f);
        wf(mario + 4u, 25.0f);
        wf(mario + 8u, 0.0f);
        /* Another map: left alone. */
        snprintf((char *)P(gp + OPENSPM_MAPS_GP_MAP_NAME), 32, "mac_02");
        openspm_maps_camera_tick(&s, &k_io);
        assert(rf(cam + OPENSPM_MAPS_CAMERA_POS) == 5.0f);
        /* os1_01 has no camera: left alone too. */
        snprintf((char *)P(gp + OPENSPM_MAPS_GP_MAP_NAME), 32, "os1_01");
        openspm_maps_camera_tick(&s, &k_io);
        assert(rf(cam + OPENSPM_MAPS_CAMERA_POS) == 5.0f);
        /* os1_05: side on, the look point held at the x range's low end. */
        snprintf((char *)P(gp + OPENSPM_MAPS_GP_MAP_NAME), 32, "os1_05");
        openspm_maps_camera_tick(&s, &k_io);
        assert(near(rf(cam + OPENSPM_MAPS_CAMERA_TARGET), -400.0f) && near(rf(cam + OPENSPM_MAPS_CAMERA_TARGET + 4u), 85.0f) &&
               near(rf(cam + OPENSPM_MAPS_CAMERA_TARGET + 8u), 0.0f));
        assert(near(rf(cam + OPENSPM_MAPS_CAMERA_POS), -400.0f) && near(rf(cam + OPENSPM_MAPS_CAMERA_POS + 4u), 95.0f) &&
               near(rf(cam + OPENSPM_MAPS_CAMERA_POS + 8u), 700.0f) && near(rf(cam + OPENSPM_MAPS_CAMERA_UP + 4u), 1.0f));
        /* Walking right: x follows at once, a jump eases in. */
        wf(mario, 300.0f);
        wf(mario + 4u, 125.0f);
        openspm_maps_camera_tick(&s, &k_io);
        assert(near(rf(cam + OPENSPM_MAPS_CAMERA_TARGET), 300.0f) && near(rf(cam + OPENSPM_MAPS_CAMERA_TARGET + 4u), 100.0f));
        /* A flip to 3D: the camera swings round to the player's left. */
        w32(cam + OPENSPM_MAPS_CAMERA_MODE, 1u);
        openspm_maps_camera_tick(&s, &k_io);
        assert(rf(cam + OPENSPM_MAPS_CAMERA_POS) < 300.0f && rf(cam + OPENSPM_MAPS_CAMERA_POS + 8u) < 700.0f);
        for (k = 0; k < 40; ++k) openspm_maps_camera_tick(&s, &k_io);
        assert(near(rf(cam + OPENSPM_MAPS_CAMERA_POS), 300.0f - 350.0f) && near(rf(cam + OPENSPM_MAPS_CAMERA_POS + 8u), 0.0f));
        assert(rf(cam + OPENSPM_MAPS_CAMERA_POS + 4u) > rf(cam + OPENSPM_MAPS_CAMERA_TARGET + 4u) + 100.0f); /* above */
        assert(near(rf(cam + OPENSPM_MAPS_CAMERA_TARGET), 300.0f));
        /* Near the start in 3D: the camera stays inside the level. */
        wf(mario, -500.0f);
        openspm_maps_camera_tick(&s, &k_io);
        assert(near(rf(cam + OPENSPM_MAPS_CAMERA_POS), -400.0f - 245.0f) && near(rf(cam + OPENSPM_MAPS_CAMERA_TARGET), -500.0f));
        /* A camera road (mode 1): left alone. */
        wf(cam + OPENSPM_MAPS_CAMERA_POS, 5.0f);
        w32(cam + OPENSPM_MAPS_CAMERA_MODE, 0x00010000u);
        openspm_maps_camera_tick(&s, &k_io);
        assert(rf(cam + OPENSPM_MAPS_CAMERA_POS) == 5.0f);
    }
    free(g_mem1);
    free(g_mem2);
    puts("PASS: content maps");
    return 0;
}
