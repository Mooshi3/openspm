/* OpenSPM guest clock: the monotonic_ns the backend hands the native module.
 *
 * The module derives the Wii timebase, the VI retrace deadlines, its idle
 * waits and the audio clock from host->monotonic_ns (tools/native_bridge
 * legacy_c_adapter_v7/v9.c.in). The backend gives it this clock instead of
 * the launcher's:
 *
 *     guest_ns = base + (host_ns - anchor) * rate
 *
 * re-anchored (base = guest now, anchor = host now) at every rate change, so
 * guest time is continuous: it never jumps back or forward when the rate
 * changes. rate = speed while running, 0 while paused (the OpenSPM menu pause),
 * so pausing and fast-forward ("turbo", docs/PLAYING.md) compose: a pause
 * during turbo resumes at the turbo speed.
 *
 * speed is Q16 (OPENSPM_GUEST_CLOCK_1X = 1.0). OPENSPM_GUEST_CLOCK_UNPACED
 * ("unlimited") runs at 1x and additionally lets the module skip its idle
 * wait: openspm_guest_clock_skip_to() moves guest time forward to the next VI
 * deadline instead of the module sleeping until it (host_api.h clock_skip_to).
 *
 * A zero-initialized clock is the identity (guest == host, 1x), so nothing
 * changes until the owner changes the speed or pauses.
 *
 * One writer (the game thread); readers on any thread see a consistent
 * (base, anchor, rate) through a sequence counter.
 *
 * Portable C, host unit-tested in tests/m14_guest_clock_test.c. */
#ifndef OPENSPM_GUEST_CLOCK_H
#define OPENSPM_GUEST_CLOCK_H

#include <stdint.h>

#if defined(_MSC_VER)
#include <intrin.h>
#endif

#ifdef __cplusplus
extern "C" {
#endif

#define OPENSPM_GUEST_CLOCK_1X UINT32_C(65536)
#define OPENSPM_GUEST_CLOCK_UNPACED UINT32_C(0xFFFFFFFF)

#if defined(_MSC_VER)
#define OPENSPM_GUEST_CLOCK_ACQUIRE() _ReadWriteBarrier()
#define OPENSPM_GUEST_CLOCK_RELEASE() _ReadWriteBarrier()
#elif defined(__GNUC__) || defined(__clang__)
#define OPENSPM_GUEST_CLOCK_ACQUIRE() __atomic_thread_fence(__ATOMIC_ACQUIRE)
#define OPENSPM_GUEST_CLOCK_RELEASE() __atomic_thread_fence(__ATOMIC_RELEASE)
#else
#define OPENSPM_GUEST_CLOCK_ACQUIRE() ((void)0)
#define OPENSPM_GUEST_CLOCK_RELEASE() ((void)0)
#endif

typedef struct openspm_guest_clock {
    volatile uint32_t sequence;   /* odd while the owner rewrites the fields below */
    volatile uint32_t configured; /* 0 = identity (never re-anchored) */
    volatile uint32_t rate;       /* effective Q16 rate: 0 paused */
    volatile uint64_t base;       /* guest ns at anchor */
    volatile uint64_t anchor;     /* host ns of the last re-anchor */
    /* owner thread only */
    uint32_t speed;               /* Q16, OPENSPM_GUEST_CLOCK_UNPACED, 0 = 1x */
    uint32_t paused;
} openspm_guest_clock;

/* delta * rate / 65536 without overflowing for any realistic delta. */
static inline uint64_t openspm_guest_clock_scale(uint64_t delta, uint32_t rate) {
    return (delta >> 16) * rate + (((delta & UINT64_C(0xFFFF)) * rate) >> 16);
}

static inline uint64_t openspm_guest_clock_now(const openspm_guest_clock *clock, uint64_t host_now) {
    uint32_t before, after, configured, rate;
    uint64_t base, anchor;
    do {
        before = clock->sequence;
        OPENSPM_GUEST_CLOCK_ACQUIRE();
        configured = clock->configured;
        rate = clock->rate;
        base = clock->base;
        anchor = clock->anchor;
        OPENSPM_GUEST_CLOCK_ACQUIRE();
        after = clock->sequence;
    } while ((before & 1u) != 0u || before != after);
    if (!configured) return host_now;
    if (host_now <= anchor || rate == 0u) return base;
    if (rate == OPENSPM_GUEST_CLOCK_1X) return base + (host_now - anchor);
    return base + openspm_guest_clock_scale(host_now - anchor, rate);
}

static inline uint32_t openspm_guest_clock_rate(const openspm_guest_clock *clock) {
    if (clock->paused) return 0u;
    if (clock->speed == 0u || clock->speed == OPENSPM_GUEST_CLOCK_UNPACED) return OPENSPM_GUEST_CLOCK_1X;
    return clock->speed;
}

/* Owner: guest time `base` now (host_now), at the current speed/pause state. */
static inline void openspm_guest_clock_reanchor(openspm_guest_clock *clock, uint64_t host_now, uint64_t base) {
    const uint32_t rate = openspm_guest_clock_rate(clock);
    clock->sequence = clock->sequence + 1u;
    OPENSPM_GUEST_CLOCK_RELEASE();
    clock->base = base;
    clock->anchor = host_now;
    clock->rate = rate;
    clock->configured = 1u;
    OPENSPM_GUEST_CLOCK_RELEASE();
    clock->sequence = clock->sequence + 1u;
}

/* Owner: change the running speed (Q16, 0 = 1x, or UNPACED). Guest time
   continues from its current value. */
static inline void openspm_guest_clock_set_speed(openspm_guest_clock *clock, uint64_t host_now, uint32_t speed) {
    uint64_t now;
    if (speed == 0u) speed = OPENSPM_GUEST_CLOCK_1X;
    if ((clock->speed == 0u ? OPENSPM_GUEST_CLOCK_1X : clock->speed) == speed) return;
    now = openspm_guest_clock_now(clock, host_now);
    clock->speed = speed;
    openspm_guest_clock_reanchor(clock, host_now, now);
}

/* Owner: stop guest time (nested pauses are one pause). */
static inline void openspm_guest_clock_pause(openspm_guest_clock *clock, uint64_t host_now) {
    uint64_t now;
    if (clock->paused) return;
    now = openspm_guest_clock_now(clock, host_now);
    clock->paused = 1u;
    openspm_guest_clock_reanchor(clock, host_now, now);
}

/* Owner: continue from where the pause stopped, at the current speed. */
static inline void openspm_guest_clock_resume(openspm_guest_clock *clock, uint64_t host_now) {
    uint64_t now;
    if (!clock->paused) return;
    now = openspm_guest_clock_now(clock, host_now);
    clock->paused = 0u;
    openspm_guest_clock_reanchor(clock, host_now, now);
}

/* Unpaced (turbo "unlimited") and running: the module may skip idle waits. */
static inline int openspm_guest_clock_unpaced(const openspm_guest_clock *clock) {
    return !clock->paused && clock->speed == OPENSPM_GUEST_CLOCK_UNPACED;
}

/* Owner: the module is idle until guest time target_ns. Unpaced: move guest
   time forward to it (never backwards) and return 1, the module then delivers
   the deadline without waiting. Otherwise return 0: wait as usual. */
static inline int openspm_guest_clock_skip_to(openspm_guest_clock *clock, uint64_t host_now, uint64_t target_ns) {
    uint64_t now;
    if (!openspm_guest_clock_unpaced(clock)) return 0;
    now = openspm_guest_clock_now(clock, host_now);
    if (target_ns > now) openspm_guest_clock_reanchor(clock, host_now, target_ns);
    return 1;
}

#ifdef __cplusplus
}
#endif

#endif
