#ifndef OPENSPM_M14_SCENE_PROFILE_H
#define OPENSPM_M14_SCENE_PROFILE_H

#include <stdint.h>
#include <string.h>

/* Host-only, single CPU-thread interval accounting. No guest state access.
 * Exact fixed-capacity keys; overflow is reported, never attributed to a victim.
 * kind 1 = display, kind 2 = main. target/site zero = phase preamble.
 * Intervals INCLUDE caller traversal after a callee returns. They are not
 * exclusive function CPU times, and continue across native cold escapes. */
#define M14_SCENE_SLOTS 128u
typedef struct m14_scene_slot {
    uint32_t kind, site, target;
    uint64_t intervals, ns, max_ns;
} m14_scene_slot;
typedef struct m14_scene_profile {
    m14_scene_slot slots[M14_SCENE_SLOTS];
    uint64_t frame, sampled[3], total_ns[3], overflow_ns[3];
    uint64_t since_ns, report_ns;
    uint32_t kind, site, target, active;
} m14_scene_profile;

static void m14_scene_close(m14_scene_profile *p, uint64_t now) {
    uint32_t n;
    uint32_t bucket;
    uint64_t elapsed;
    m14_scene_slot *slot;
    if (!p->active || p->kind == 0u) return;
    elapsed = now >= p->since_ns ? now - p->since_ns : 0u;
    p->total_ns[p->kind] += elapsed;
    bucket = ((p->site >> 2) ^ (p->target >> 2) ^ p->kind) % M14_SCENE_SLOTS;
    for (n = 0u; n < M14_SCENE_SLOTS; ++n) {
        slot = &p->slots[(bucket + n) % M14_SCENE_SLOTS];
        if (slot->kind == 0u || (slot->kind == p->kind &&
            slot->site == p->site && slot->target == p->target)) {
            slot->kind = p->kind;
            slot->site = p->site;
            slot->target = p->target;
            ++slot->intervals;
            slot->ns += elapsed;
            if (elapsed > slot->max_ns) slot->max_ns = elapsed;
            return;
        }
    }
    p->overflow_ns[p->kind] += elapsed;
}

static void m14_scene_boundary(m14_scene_profile *p, uint32_t kind,
                               uint32_t site, uint32_t target, uint64_t now) {
    if (!p->active || kind != p->kind) return;
    m14_scene_close(p, now);
    p->site = site;
    p->target = target;
    p->since_ns = now;
}

static void m14_scene_phase(m14_scene_profile *p, uint32_t phase, uint64_t now) {
    m14_scene_close(p, now);
    p->kind = 0u;
    if (phase == 1u) {
        p->active = ((p->frame++ & UINT64_C(63)) == 0u);
        if (p->active) p->kind = 1u;
    } else if (phase == 2u && p->active) {
        p->kind = 2u;
    } else {
        p->active = 0u;
    }
    p->site = p->target = 0u;
    p->since_ns = now;
    if (p->kind) ++p->sampled[p->kind];
}

/* Preserve sampling cadence when a report window is drained. Only drain
 * between sampled phases: clearing an in-flight interval would lose ownership. */
static void m14_scene_drain(m14_scene_profile *p, uint64_t now) {
    uint64_t frame = p->frame;
    memset(p, 0, sizeof(*p));
    p->frame = frame;
    p->report_ns = now;
}

#endif
