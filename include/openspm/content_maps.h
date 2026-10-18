/* OpenSPM maps: new maps in the game's map registry (provider side of
 * openspm.content's register_map / go_to_map, include/openspm/content_service.h;
 * docs/CUSTOM-LEVELS.md "Map registry").
 *
 * With the map registry patches (state/openspm-code-patches.json,
 * tools/content/map_registry.py) the game's 468 map pointers live in MEM2
 * followed by spare slots that start as copies of slot 0. Registering a map
 * writes a MapData record (name, file, fallback door, spawn position, init
 * script) into guest scratch memory and its pointer into a spare slot; the
 * game's mapDataPtr / mapDataEvtPtr then find it like its own. go_to_map
 * asks for the game's map change (seqSetSeq, what evt_seq_mapchange does).
 *
 * The camera: the game's main camera (camPtrTbl[5]) follows a map's camera
 * road (camera_road.bin) in mode 1; a map without one leaves it in mode 0,
 * where it keeps the position and target it has. For a registered map with
 * a camera distance, openspm_maps_camera_tick sets them each frame after the
 * player: side on in 2D (looking along -z), from the player's left in 3D
 * (the camera's 3D flag, camera +10; half as far, a little higher, kept
 * inside the level's start), easing between the two on a flip.
 *
 * Runs inside a mod tick with the roster's guest access (openspm_roster_io).
 * Portable C, header-only. tests/m14_content_maps_test.c. */
#ifndef OPENSPM_CONTENT_MAPS_H
#define OPENSPM_CONTENT_MAPS_H

#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "openspm/content_roster.h"
#include "openspm/content_service.h"
#include "openspm/fst_overlay.h"

#define OPENSPM_MAPS_MAX 32u
#define OPENSPM_MAPS_GAME_COUNT 468u
#define OPENSPM_MAPS_SLOTS 532u                    /* with the spare ones */
#define OPENSPM_MAPS_REGISTRY UINT32_C(0x93E55098)  /* where the patches moved mapData */
#define OPENSPM_MAPS_LOOKUP_LIS UINT32_C(0x8002936C) /* mapDataPtr's lis of the registry */
#define OPENSPM_MAPS_LOOKUP_LIS_MOVED UINT32_C(0x3C8093E5)
#define OPENSPM_MAPS_SEQ_SET UINT32_C(0x8017B834)    /* seqSetSeq(seq, p0, p1) */
#define OPENSPM_MAPS_RECORD_SIZE 0x1Cu               /* MapData */
#define OPENSPM_MAPS_FST_PTR UINT32_C(0x80000038)      /* the game's file table: address, size */
#define OPENSPM_MAPS_FST_MAX 0x00100000u
#define OPENSPM_MAPS_GP_PTR UINT32_C(0x8056CB78)       /* -> the game's globals; +0x44 the map's name */
#define OPENSPM_MAPS_GP_MAP_NAME 0x44u
#define OPENSPM_MAPS_CAMERA_3D UINT32_C(0x804C9F94)    /* camPtrTbl[5]: the main camera */
#define OPENSPM_MAPS_CAMERA_MODE 0x08u                 /* u16 mode (0: none, 1-2: road), u16 3D flag */
#define OPENSPM_MAPS_CAMERA_POS 0x15Cu
#define OPENSPM_MAPS_CAMERA_TARGET 0x168u
#define OPENSPM_MAPS_CAMERA_UP 0x174u
#define OPENSPM_MAPS_FLIP_FRAMES 24.0f                 /* the camera's swing on a flip */

typedef struct openspm_map_entry {
    char key[48], name[32];
    uint32_t record, slot;
    float camera[5]; /* distance, height, look height, x min, x max */
} openspm_map_entry;

typedef struct openspm_maps {
    openspm_map_entry m[OPENSPM_MAPS_MAX];
    uint32_t count;
    /* The camera on a registered map: which entry (-1: none), the eased
       height it follows and the 2D (0) to 3D (1) swing. */
    int camera_map;
    float camera_y, camera_turn;
} openspm_maps;

static inline void openspm_maps_init(openspm_maps *s) {
    memset(s, 0, sizeof *s);
    s->camera_map = -1;
}

static inline int openspm_maps_name_ok(const char *n) {
    size_t i, len = n ? strlen(n) : 0u;
    if (len < 4u || len >= 32u) return 0;
    for (i = 0; i < len; ++i)
        if (!((n[i] >= 'a' && n[i] <= 'z') || (n[i] >= '0' && n[i] <= '9') || n[i] == '_')) return 0;
    return 1;
}

static inline uint32_t openspm_maps_put_str(const openspm_roster_io *io, const char *text) {
    uint32_t at = 0u;
    const size_t len = strlen(text) + 1u;
    size_t i;
    if (io->scratch(io->context, (uint32_t)((len + 3u) & ~(size_t)3u), &at) != OPENSPM_OK) return 0u;
    for (i = 0; i < len; i += 4u) {
        uint32_t w = 0u, k;
        for (k = 0; k < 4u; ++k) w |= (uint32_t)(i + k < len ? (uint8_t)text[i + k] : 0u) << (24u - 8u * k);
        if (io->write32(io->context, at + (uint32_t)i, w) != OPENSPM_OK) return 0u;
    }
    return at;
}

/* The registry slot whose record is named `name`, or UINT32_MAX. */
static inline uint32_t openspm_maps_find(const openspm_roster_io *io, const char *name) {
    uint32_t i;
    int ok = 1;
    for (i = 0; i < OPENSPM_MAPS_SLOTS; ++i) {
        const uint32_t rec = openspm_roster_rd(io, OPENSPM_MAPS_REGISTRY + 4u * i, &ok);
        if (ok && rec && openspm_roster_str_is(io, openspm_roster_rd(io, rec, &ok), name)) return i;
        if (!ok) return UINT32_MAX;
    }
    return UINT32_MAX;
}

/* 1: the game's file table has map/<file>.bin or map/<file>/map.dat (what
   mapLoad reads); 0: neither (loading the map would fail); -1: the table
   cannot be read here (not checked). */
static inline int openspm_maps_file_present(const openspm_roster_io *io, const char *file) {
    int ok = 1, found;
    char path[64];
    uint8_t *fst;
    uint32_t i;
    const uint32_t at = openspm_roster_rd(io, OPENSPM_MAPS_FST_PTR, &ok);
    const uint32_t size = openspm_roster_rd(io, OPENSPM_MAPS_FST_PTR + 4u, &ok);
    if (!ok || at < 0x80000000u || size < 12u || size > OPENSPM_MAPS_FST_MAX) return -1;
    fst = (uint8_t *)malloc(size + 4u);
    if (fst == NULL) return -1;
    for (i = 0; i < size && ok; i += 4u) openspm_fst_put32(fst + i, openspm_roster_rd(io, at + i, &ok));
    if (!ok) {
        free(fst);
        return -1;
    }
    snprintf(path, sizeof path, "map/%s.bin", file);
    found = openspm_fst_find(fst, size, path) > 0;
    if (!found) {
        snprintf(path, sizeof path, "map/%s/map.dat", file);
        found = openspm_fst_find(fst, size, path) > 0;
    }
    free(fst);
    return found;
}

static inline int openspm_maps_register(openspm_maps *s, const openspm_roster_io *io, const openspm_map_def *def,
                                        uint32_t *out_slot, char *why, size_t why_size) {
    int ok = 1;
    uint32_t slot0, slot, rec, s_name, s_file, s_door = 0u, i;
    const char *file;
    if (def == NULL || def->header.struct_size < OPENSPM_MAP_DEF_V1_SIZE || def->key == NULL || def->name == NULL) {
        snprintf(why, why_size, "needs a key and a name");
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    }
    file = def->file ? def->file : def->name;
    if (!openspm_maps_name_ok(def->name) || !openspm_maps_name_ok(file)) {
        snprintf(why, why_size, "map name / file \"%s\" / \"%s\": 4 to 31 characters of a-z, 0-9, _", def->name, file);
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    }
    if (def->fallback_door && (strlen(def->fallback_door) == 0u || strlen(def->fallback_door) >= 32u)) {
        snprintf(why, why_size, "fallback door name: 1 to 31 characters");
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    }
    if (def->init_script && (def->init_script < 0x80000000u || (def->init_script & 3u))) {
        snprintf(why, why_size, "init script 0x%08X is not a guest address", (unsigned)def->init_script);
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    }
    if (def->header.struct_size >= OPENSPM_MAP_DEF_V2_SIZE && def->camera_distance != 0.0f &&
        !(def->camera_distance >= 100.0f && def->camera_distance <= 5000.0f &&
          def->camera_x_range[0] <= def->camera_x_range[1] && fabsf(def->camera_height) <= 5000.0f &&
          fabsf(def->camera_look_height) <= 5000.0f)) {
        snprintf(why, why_size, "camera: a distance of 100 to 5000, heights within 5000, x range low to high");
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    }
    if (openspm_roster_rd(io, OPENSPM_MAPS_LOOKUP_LIS, &ok) != OPENSPM_MAPS_LOOKUP_LIS_MOVED || !ok) {
        snprintf(why, why_size, "this build has no map registry (the native module predates it: rebuild it)");
        return OPENSPM_ERROR_UNSUPPORTED;
    }
    for (i = 0; i < s->count; ++i)
        if (strcmp(s->m[i].key, def->key) == 0 || strcmp(s->m[i].name, def->name) == 0) {
            snprintf(why, why_size, "key or map name already registered");
            return OPENSPM_ERROR_RUNTIME;
        }
    if (openspm_maps_find(io, def->name) != UINT32_MAX) {
        snprintf(why, why_size, "the game has a map named %s", def->name);
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    }
    if (openspm_maps_file_present(io, file) == 0) {
        snprintf(why, why_size, "map/%s.bin is not in the game's files (put it in the mod's files folder, "
                                "mods\\<mod>\\files\\map\\)", file);
        return OPENSPM_ERROR_NOT_FOUND;
    }
    if (s->count == OPENSPM_MAPS_MAX || strlen(def->key) >= sizeof s->m[0].key) {
        snprintf(why, why_size, s->count == OPENSPM_MAPS_MAX ? "the map list is full" : "key too long");
        return OPENSPM_ERROR_RUNTIME;
    }
    /* A spare slot: one that still holds slot 0's record. */
    slot0 = openspm_roster_rd(io, OPENSPM_MAPS_REGISTRY, &ok);
    for (slot = OPENSPM_MAPS_GAME_COUNT; slot < OPENSPM_MAPS_SLOTS && ok; ++slot)
        if (openspm_roster_rd(io, OPENSPM_MAPS_REGISTRY + 4u * slot, &ok) == slot0) break;
    if (!ok || slot0 == 0u || slot == OPENSPM_MAPS_SLOTS) {
        snprintf(why, why_size, "no spare map slot left");
        return OPENSPM_ERROR_RUNTIME;
    }
    s_name = openspm_maps_put_str(io, def->name);
    s_file = openspm_maps_put_str(io, file);
    if (def->fallback_door) s_door = openspm_maps_put_str(io, def->fallback_door);
    if (!s_name || !s_file || (def->fallback_door && !s_door) ||
        io->scratch(io->context, OPENSPM_MAPS_RECORD_SIZE, &rec) != OPENSPM_OK) {
        snprintf(why, why_size, "no guest memory left");
        return OPENSPM_ERROR_RUNTIME;
    }
    {
        uint32_t f[3];
        memcpy(&f[0], &def->spawn[0], 4);
        memcpy(&f[1], &def->spawn[1], 4);
        memcpy(&f[2], &def->spawn[2], 4);
        if (io->write32(io->context, rec + 0x00u, s_name) != OPENSPM_OK ||
            io->write32(io->context, rec + 0x04u, s_file) != OPENSPM_OK ||
            io->write32(io->context, rec + 0x08u, s_door) != OPENSPM_OK ||
            io->write32(io->context, rec + 0x0Cu, f[0]) != OPENSPM_OK ||
            io->write32(io->context, rec + 0x10u, f[1]) != OPENSPM_OK ||
            io->write32(io->context, rec + 0x14u, f[2]) != OPENSPM_OK ||
            io->write32(io->context, rec + 0x18u, def->init_script) != OPENSPM_OK ||
            io->write32(io->context, OPENSPM_MAPS_REGISTRY + 4u * slot, rec) != OPENSPM_OK) {
            snprintf(why, why_size, "guest memory is closed (register from a mod tick)");
            return OPENSPM_ERROR_RUNTIME;
        }
    }
    snprintf(s->m[s->count].key, sizeof s->m[0].key, "%s", def->key);
    snprintf(s->m[s->count].name, sizeof s->m[0].name, "%s", def->name);
    s->m[s->count].record = rec;
    s->m[s->count].slot = slot;
    memset(s->m[s->count].camera, 0, sizeof s->m[0].camera);
    if (def->header.struct_size >= OPENSPM_MAP_DEF_V2_SIZE) {
        s->m[s->count].camera[0] = def->camera_distance;
        s->m[s->count].camera[1] = def->camera_height;
        s->m[s->count].camera[2] = def->camera_look_height;
        s->m[s->count].camera[3] = def->camera_x_range[0];
        s->m[s->count].camera[4] = def->camera_x_range[1];
    }
    ++s->count;
    if (out_slot) *out_slot = slot;
    return OPENSPM_OK;
}

/* The game's map change to `name` (any map, the game's or a registered one),
   entered by `door` (NULL: the map's fallback door / spawn position). */
static inline int openspm_maps_go(const openspm_roster_io *io, const char *name, const char *door) {
    uint32_t args[3];
    if (!openspm_maps_name_ok(name) || (door && strlen(door) >= 32u)) return OPENSPM_ERROR_INVALID_ARGUMENT;
    if (openspm_maps_find(io, name) == UINT32_MAX) return OPENSPM_ERROR_NOT_FOUND;
    args[0] = SPM_R8PE01_SEQ_MAPCHANGE;
    args[1] = openspm_maps_put_str(io, name);
    args[2] = door ? openspm_maps_put_str(io, door) : 0u;
    if (!args[1] || (door && !args[2])) return OPENSPM_ERROR_RUNTIME;
    return io->call(io->context, OPENSPM_MAPS_SEQ_SET, 3u, args);
}

static inline float openspm_maps_rdf(const openspm_roster_io *io, uint32_t a, int *ok) {
    const uint32_t w = openspm_roster_rd(io, a, ok);
    float f;
    memcpy(&f, &w, 4);
    return f;
}
static inline void openspm_maps_wrf(const openspm_roster_io *io, uint32_t a, float f) {
    uint32_t w;
    memcpy(&w, &f, 4);
    (void)openspm_roster_wr(io, a, w);
}

/* The registered map being played (its entry), or -1. */
static inline int openspm_maps_current(const openspm_maps *s, const openspm_roster_io *io) {
    int ok = 1;
    uint32_t i;
    const uint32_t gp = openspm_roster_rd(io, OPENSPM_MAPS_GP_PTR, &ok);
    if (!ok || gp < 0x80000000u || gp >= 0x81800000u) return -1;
    for (i = 0; i < s->count; ++i)
        if (openspm_roster_str_is(io, gp + OPENSPM_MAPS_GP_MAP_NAME, s->m[i].name)) return (int)i;
    return -1;
}

/* Each frame (a mod tick): on a registered map with a camera, the game's
   main camera after the player. */
static inline void openspm_maps_camera_tick(openspm_maps *s, const openspm_roster_io *io) {
    int ok = 1, map;
    uint32_t cam, mode;
    float px, py, pz, d, lx, look[3], pos[3], eased, angle;
    const float *c;
    if (s->count == 0u) return;
    map = openspm_maps_current(s, io);
    if (map < 0 || s->m[map].camera[0] == 0.0f) {
        s->camera_map = -1;
        return;
    }
    c = s->m[map].camera;
    cam = openspm_roster_rd(io, OPENSPM_MAPS_CAMERA_3D, &ok);
    if (!ok || cam < 0x80000000u || cam >= 0x81800000u) return;
    mode = openspm_roster_rd(io, cam + OPENSPM_MAPS_CAMERA_MODE, &ok);
    if (!ok || (mode >> 16) != 0u) return; /* the map has a camera road after all */
    px = openspm_maps_rdf(io, SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_POSITION + 0u, &ok);
    py = openspm_maps_rdf(io, SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_POSITION + 4u, &ok);
    pz = openspm_maps_rdf(io, SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_POSITION + 8u, &ok);
    if (!ok || !isfinite(px) || !isfinite(py) || !isfinite(pz)) return;
    if (s->camera_map != map) { /* just arrived: no easing */
        s->camera_map = map;
        s->camera_y = py;
        s->camera_turn = (mode & 0xFFFFu) ? 1.0f : 0.0f;
    } else {
        const float want = (mode & 0xFFFFu) ? 1.0f : 0.0f, step = 1.0f / OPENSPM_MAPS_FLIP_FRAMES;
        s->camera_y += (py - s->camera_y) * 0.15f;
        if (s->camera_turn < want) s->camera_turn = s->camera_turn + step > want ? want : s->camera_turn + step;
        if (s->camera_turn > want) s->camera_turn = s->camera_turn - step < want ? want : s->camera_turn - step;
    }
    eased = s->camera_turn * s->camera_turn * (3.0f - 2.0f * s->camera_turn);
    lx = px;
    if (c[3] != 0.0f || c[4] != 0.0f) lx = px < c[3] ? c[3] : px > c[4] ? c[4] : px;
    look[0] = lx + (px - lx) * eased; /* the x range holds in 2D only */
    look[1] = s->camera_y + c[2];
    look[2] = pz;
    angle = eased * 1.5707963f;
    /* In 3D: half as far, a little higher, and never behind the level's
       start (the x range's low end less a third of the distance). */
    d = c[0] * (1.0f - 0.5f * eased);
    pos[0] = look[0] - d * sinf(angle);
    if ((c[3] != 0.0f || c[4] != 0.0f) && pos[0] < c[3] - 0.35f * c[0]) pos[0] = c[3] - 0.35f * c[0];
    pos[1] = s->camera_y + c[1] + 0.15f * c[0] * eased;
    pos[2] = pz + d * cosf(angle);
    openspm_maps_wrf(io, cam + OPENSPM_MAPS_CAMERA_POS + 0u, pos[0]);
    openspm_maps_wrf(io, cam + OPENSPM_MAPS_CAMERA_POS + 4u, pos[1]);
    openspm_maps_wrf(io, cam + OPENSPM_MAPS_CAMERA_POS + 8u, pos[2]);
    openspm_maps_wrf(io, cam + OPENSPM_MAPS_CAMERA_TARGET + 0u, look[0]);
    openspm_maps_wrf(io, cam + OPENSPM_MAPS_CAMERA_TARGET + 4u, look[1]);
    openspm_maps_wrf(io, cam + OPENSPM_MAPS_CAMERA_TARGET + 8u, look[2]);
    openspm_maps_wrf(io, cam + OPENSPM_MAPS_CAMERA_UP + 0u, 0.0f);
    openspm_maps_wrf(io, cam + OPENSPM_MAPS_CAMERA_UP + 4u, 1.0f);
    openspm_maps_wrf(io, cam + OPENSPM_MAPS_CAMERA_UP + 8u, 0.0f);
}

#endif
