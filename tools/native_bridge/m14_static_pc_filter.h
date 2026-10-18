#ifndef OPENSPM_M14_STATIC_PC_FILTER_H
#define OPENSPM_M14_STATIC_PC_FILTER_H

#include <stdint.h>

/* dev69: conservative negative filter for immutable address tables ONLY.
 * A zero bit proves absence; a set bit always falls through to exact lookup.
 * Hash collisions cannot skip a semantic call. Never learn from host_call
 * return values or cache a decision about dynamic guest/device state. */
#define OPENSPM_M14_PC_FILTER_WORDS 512u
static uint32_t openspm_m14_pc_filter_bit(uint32_t pc) {
    return ((pc >> 2) * UINT32_C(2654435761)) >> 18;
}
static void openspm_m14_pc_filter_add(uint32_t *bits, uint32_t pc) {
    const uint32_t bit = openspm_m14_pc_filter_bit(pc);
    bits[bit >> 5] |= UINT32_C(1) << (bit & 31u);
}
static int openspm_m14_pc_filter_maybe(const uint32_t *bits, uint32_t pc) {
    const uint32_t bit = openspm_m14_pc_filter_bit(pc);
    return (bits[bit >> 5] & (UINT32_C(1) << (bit & 31u))) != 0u;
}
#endif
