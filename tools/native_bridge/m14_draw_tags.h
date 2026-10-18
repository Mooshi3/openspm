/*
 * OpenSPM draw tags (Stage B of docs/PRESENTATION-ARCHITECTURE.md): the owner
 * identity of every dispDraw callback, carried into the GX FIFO stream.
 *
 * Pure C99, no CPUState: the LLVM bridge (legacy_c_adapter_v42_dev69.c.in)
 * computes owners and emits markers; tests/m14_draw_tags_test.cpp drives the
 * same code against the renderer-side decoder (renderer/aurora/draw_tags.hpp).
 *
 * Wire format: two BP register writes the Wii SDK never issues and Aurora does
 * not decode (0x6A..0x7F are unassigned on Flipper/Hollywood; Aurora maps them
 * to bp_unhandled):
 *   0x7E  owner bits [23:0]              (FRAME: game frame sequence [23:0])
 *   0x7F  [7:0]   owner bits [31:24]
 *         [11:8]  camera index (camPtrTbl slot 0..14, 15 = none)
 *         [12]    camera uses an orthographic projection (2D/UI)
 *         [17:16] kind: 0 = end (no owner), 1 = begin owner, 2 = frame start
 * Each write is the 5-byte FIFO command 0x61 + big-endian (reg << 24 | value).
 *
 * Camera view records (Stage C interpolation): before the first owner of a
 * 3D camera in a frame, the camera's view matrix (CamEntry+0x54, the 3x4
 * world->view matrix camDraw uses) as 12 IEEE floats, element e = row * 4 +
 * column, each as two writes:
 *   0x7C  [23:20] camera, [19:16] element, [15:0] float bits [31:16]
 *   0x7D  [23:20] camera, [19:16] element, [15:0] float bits [15:0]
 * The renderer attaches the completed record to every later draw of that
 * camera in the frame.
 *
 * Owner identity = hash(callback, callbackParam, camera, occurrence), where
 * occurrence counts earlier entries of the same (callback, param, camera) in
 * the frame's REGISTRATION order (the dispEntry pool order), which the z sort
 * cannot change. The display-list entry pointer itself is deliberately NOT
 * part of it: dispEntry() allocates entries sequentially (pool + index * 16),
 * so the pointer shifts for every later object whenever one object
 * spawns/despawns, and dispDraw's qsort by z changes the draw order every
 * frame.
 */
#ifndef OPENSPM_M14_DRAW_TAGS_H
#define OPENSPM_M14_DRAW_TAGS_H

#include <stdint.h>
#include <string.h>

#define OPENSPM_M14_DRAW_TAG_OWNER_REG UINT32_C(0x7E)
#define OPENSPM_M14_DRAW_TAG_CONTROL_REG UINT32_C(0x7F)
#define OPENSPM_M14_DRAW_TAG_VIEW_HI_REG UINT32_C(0x7C)
#define OPENSPM_M14_DRAW_TAG_VIEW_LO_REG UINT32_C(0x7D)
#define OPENSPM_M14_DRAW_TAG_VIEW_ELEMENTS 12u
#define OPENSPM_M14_DRAW_TAG_VIEW_WORDS 24u
#define OPENSPM_M14_DRAW_TAG_KIND_END UINT32_C(0)
#define OPENSPM_M14_DRAW_TAG_KIND_BEGIN UINT32_C(1)
#define OPENSPM_M14_DRAW_TAG_KIND_FRAME UINT32_C(2)
#define OPENSPM_M14_DRAW_TAG_CAMERA_NONE UINT32_C(0xF)
#define OPENSPM_M14_DRAW_TAG_BP_CMD UINT32_C(0x61)
#define OPENSPM_M14_DRAW_TAG_WORD_BYTES 5u
#define OPENSPM_M14_DRAW_TAG_OCC_SLOTS 4096u /* > dispEntry's 2048-entry cap */
#define OPENSPM_M14_DRAW_TAG_OCC_PROBES 64u
#define OPENSPM_M14_DRAW_TAG_MAX_ENTRIES 2048u /* dispEntry's pool size */

typedef struct m14_draw_tag_occ_slot {
    uint32_t callback;
    uint32_t param;
    uint32_t camera;
    uint32_t generation;
    uint32_t count;
} m14_draw_tag_occ_slot;

typedef struct m14_draw_tag_occurrences {
    m14_draw_tag_occ_slot slots[OPENSPM_M14_DRAW_TAG_OCC_SLOTS];
    uint32_t generation;
    uint32_t overflows;
    uint32_t scanned; /* registration indices [0, scanned) are resolved */
    uint16_t by_index[OPENSPM_M14_DRAW_TAG_MAX_ENTRIES];
} m14_draw_tag_occurrences;

static inline uint32_t m14_draw_tag_mix32(uint32_t x) {
    x ^= x >> 16;
    x *= UINT32_C(0x85EBCA6B);
    x ^= x >> 13;
    x *= UINT32_C(0xC2B2AE35);
    x ^= x >> 16;
    return x;
}

/* Start a new frame: every (callback, param, camera) starts at occurrence 0.
 * Generation 0 means "never used", so it is skipped on wrap. */
static inline void m14_draw_tags_new_frame(m14_draw_tag_occurrences *o) {
    if (o == NULL) return;
    o->scanned = 0u;
    ++o->generation;
    if (o->generation == 0u) {
        memset(o->slots, 0, sizeof(o->slots));
        o->generation = 1u;
    }
}

/* Occurrence index (0-based) of this key within the current frame. */
static inline uint32_t m14_draw_tags_occurrence(m14_draw_tag_occurrences *o,
                                                uint32_t callback,
                                                uint32_t param,
                                                uint32_t camera) {
    uint32_t index, probe;
    if (o == NULL) return 0u;
    if (o->generation == 0u) m14_draw_tags_new_frame(o);
    index = m14_draw_tag_mix32(callback ^ m14_draw_tag_mix32(param ^ (camera << 28))) &
            (OPENSPM_M14_DRAW_TAG_OCC_SLOTS - 1u);
    for (probe = 0u; probe < OPENSPM_M14_DRAW_TAG_OCC_PROBES; ++probe) {
        m14_draw_tag_occ_slot *slot =
            &o->slots[(index + probe) & (OPENSPM_M14_DRAW_TAG_OCC_SLOTS - 1u)];
        if (slot->generation != o->generation) {
            slot->callback = callback;
            slot->param = param;
            slot->camera = camera;
            slot->generation = o->generation;
            slot->count = 1u;
            return 0u;
        }
        if (slot->callback == callback && slot->param == param &&
            slot->camera == camera)
            return slot->count++;
    }
    ++o->overflows;
    return 0u;
}

/* Resolve the next registration index (entries must be fed in pool order,
 * index == o->scanned). Returns 0 when the index is out of range. */
static inline int m14_draw_tags_scan_entry(m14_draw_tag_occurrences *o,
                                           uint32_t index,
                                           uint32_t callback,
                                           uint32_t param,
                                           uint32_t camera) {
    uint32_t occurrence;
    if (o == NULL || index != o->scanned ||
        index >= OPENSPM_M14_DRAW_TAG_MAX_ENTRIES)
        return 0;
    occurrence = m14_draw_tags_occurrence(o, callback, param, camera);
    o->by_index[index] = (uint16_t)(occurrence < 0xFFFFu ? occurrence : 0xFFFFu);
    o->scanned = index + 1u;
    return 1;
}

/* Occurrence of an already scanned registration index (0 if not scanned). */
static inline uint32_t m14_draw_tags_registered_occurrence(
    const m14_draw_tag_occurrences *o, uint32_t index) {
    if (o == NULL || index >= o->scanned) return 0u;
    return o->by_index[index];
}

/* Stable, never-zero 32-bit owner id. */
static inline uint32_t m14_draw_tags_owner(uint32_t callback,
                                           uint32_t param,
                                           uint32_t camera,
                                           uint32_t occurrence) {
    uint32_t h = UINT32_C(0x9E3779B9);
    h = m14_draw_tag_mix32(h ^ callback) + UINT32_C(0x7F4A7C15);
    h = m14_draw_tag_mix32(h ^ param) + UINT32_C(0x94D049BB);
    h = m14_draw_tag_mix32(h ^ ((camera & 0xFu) | (occurrence << 4)));
    return h != 0u ? h : 1u;
}

static inline uint32_t m14_draw_tags_control(uint32_t kind,
                                             uint32_t owner,
                                             uint32_t camera,
                                             uint32_t ortho) {
    return (OPENSPM_M14_DRAW_TAG_CONTROL_REG << 24) |
           ((kind & 3u) << 16) | ((ortho ? 1u : 0u) << 12) |
           ((camera & 0xFu) << 8) | (owner >> 24);
}

/* BP words for "draws from here on belong to owner" (2 words). */
static inline uint32_t m14_draw_tags_begin_words(uint32_t owner,
                                                 uint32_t camera,
                                                 uint32_t ortho,
                                                 uint32_t out[2]) {
    out[0] = (OPENSPM_M14_DRAW_TAG_OWNER_REG << 24) | (owner & UINT32_C(0x00FFFFFF));
    out[1] = m14_draw_tags_control(OPENSPM_M14_DRAW_TAG_KIND_BEGIN, owner,
                                   camera, ortho);
    return 2u;
}

/* BP word for "no owner" (1 word). */
static inline uint32_t m14_draw_tags_end_words(uint32_t out[1]) {
    out[0] = m14_draw_tags_control(OPENSPM_M14_DRAW_TAG_KIND_END, 0u,
                                   OPENSPM_M14_DRAW_TAG_CAMERA_NONE, 0u);
    return 1u;
}

/* BP words for "a new game frame starts; no owner" (2 words). */
static inline uint32_t m14_draw_tags_frame_words(uint32_t frame_seq,
                                                 uint32_t out[2]) {
    out[0] = (OPENSPM_M14_DRAW_TAG_OWNER_REG << 24) | (frame_seq & UINT32_C(0x00FFFFFF));
    out[1] = m14_draw_tags_control(OPENSPM_M14_DRAW_TAG_KIND_FRAME, 0u,
                                   OPENSPM_M14_DRAW_TAG_CAMERA_NONE, 0u);
    return 2u;
}

/* BP words for one camera view record (24 words): bits[e] = raw IEEE bits
 * of element e (row-major 3x4). */
static inline uint32_t m14_draw_tags_view_words(uint32_t camera,
                                                const uint32_t bits[12],
                                                uint32_t out[24]) {
    uint32_t e;
    for (e = 0u; e < OPENSPM_M14_DRAW_TAG_VIEW_ELEMENTS; ++e) {
        const uint32_t tag = ((camera & 0xFu) << 20) | (e << 16);
        out[2u * e] = (OPENSPM_M14_DRAW_TAG_VIEW_HI_REG << 24) | tag | (bits[e] >> 16);
        out[2u * e + 1u] = (OPENSPM_M14_DRAW_TAG_VIEW_LO_REG << 24) | tag | (bits[e] & 0xFFFFu);
    }
    return OPENSPM_M14_DRAW_TAG_VIEW_WORDS;
}

/* Serialize BP words as FIFO commands; returns bytes written (5 per word). */
static inline uint32_t m14_draw_tags_encode(const uint32_t *words,
                                            uint32_t count,
                                            uint8_t *out) {
    uint32_t i, used = 0u;
    for (i = 0u; i < count; ++i) {
        out[used++] = (uint8_t)OPENSPM_M14_DRAW_TAG_BP_CMD;
        out[used++] = (uint8_t)(words[i] >> 24);
        out[used++] = (uint8_t)(words[i] >> 16);
        out[used++] = (uint8_t)(words[i] >> 8);
        out[used++] = (uint8_t)words[i];
    }
    return used;
}

#endif
