/* Host test for include/openspm/guest_clock.h: the rate-scaled guest clock
   behind the menu pause and fast-forward ("turbo"). */
#include "openspm/guest_clock.h"

#include <assert.h>
#include <stdio.h>
#include <string.h>

#define X1 OPENSPM_GUEST_CLOCK_1X
#define UNPACED OPENSPM_GUEST_CLOCK_UNPACED

static uint64_t now(const openspm_guest_clock *c, uint64_t host) { return openspm_guest_clock_now(c, host); }

static uint32_t lcg(uint32_t *state) {
    *state = *state * 1664525u + 1013904223u;
    return *state >> 8;
}

int main(void) {
    openspm_guest_clock c;

    /* Zero-initialized = identity; 1x speed changes nothing. */
    memset(&c, 0, sizeof c);
    assert(now(&c, 0u) == 0u && now(&c, 123456789u) == 123456789u);
    openspm_guest_clock_set_speed(&c, 1000u, X1);
    assert(c.sequence == 0u && now(&c, 5000u) == 5000u); /* no re-anchor at all */
    openspm_guest_clock_set_speed(&c, 1000u, 0u);         /* 0 = 1x as well */
    assert(c.sequence == 0u);

    /* N x: continuous at every change, N x as fast in between. */
    openspm_guest_clock_set_speed(&c, 1000u, 2u * X1);
    assert(now(&c, 1000u) == 1000u && now(&c, 1100u) == 1200u);
    openspm_guest_clock_set_speed(&c, 1100u, 4u * X1);
    assert(now(&c, 1100u) == 1200u && now(&c, 1150u) == 1400u);
    openspm_guest_clock_set_speed(&c, 1150u, 3u * X1);
    assert(now(&c, 1150u) == 1400u && now(&c, 1160u) == 1430u);
    openspm_guest_clock_set_speed(&c, 1160u, X1); /* back to normal: no jump */
    assert(now(&c, 1160u) == 1430u && now(&c, 2160u) == 2430u);
    openspm_guest_clock_set_speed(&c, 2160u, X1 + X1 / 2u); /* fractional rates work too */
    assert(now(&c, 2160u) == 2430u && now(&c, 2260u) == 2580u);
    openspm_guest_clock_set_speed(&c, 2260u, X1);
    assert(now(&c, 2260u) == 2580u);
    /* A reader with an older host timestamp never sees time before the anchor. */
    assert(now(&c, 100u) == 2580u);

    /* Pause = rate 0 on top of the speed: a pause during turbo resumes at turbo. */
    openspm_guest_clock_set_speed(&c, 3000u, 2u * X1);
    assert(now(&c, 3000u) == 3320u);
    openspm_guest_clock_pause(&c, 3100u);
    assert(now(&c, 3100u) == 3520u && now(&c, 900000u) == 3520u);
    openspm_guest_clock_pause(&c, 950000u); /* nested: no effect */
    openspm_guest_clock_set_speed(&c, 960000u, 4u * X1); /* while paused: stays frozen */
    assert(now(&c, 970000u) == 3520u);
    openspm_guest_clock_resume(&c, 1000000u);
    assert(now(&c, 1000000u) == 3520u && now(&c, 1000010u) == 3560u); /* 4x now */
    openspm_guest_clock_resume(&c, 1000010u); /* not paused: no effect */
    assert(now(&c, 1000020u) == 3600u);
    openspm_guest_clock_set_speed(&c, 1000020u, X1);
    assert(now(&c, 1000030u) == 3610u);

    /* Unlimited: 1x while the game works, idle waits skip to the deadline. */
    memset(&c, 0, sizeof c);
    assert(openspm_guest_clock_skip_to(&c, 100u, 500u) == 0 && now(&c, 100u) == 100u); /* paced: wait */
    openspm_guest_clock_set_speed(&c, 100u, UNPACED);
    assert(openspm_guest_clock_unpaced(&c));
    assert(now(&c, 200u) == 200u);                                  /* real time between waits */
    assert(openspm_guest_clock_skip_to(&c, 200u, 16867u) == 1);
    assert(now(&c, 200u) == 16867u && now(&c, 210u) == 16877u);
    assert(openspm_guest_clock_skip_to(&c, 210u, 1000u) == 1);      /* already due: never backwards */
    assert(now(&c, 210u) == 16877u);
    openspm_guest_clock_pause(&c, 220u);                             /* the menu pause wins */
    assert(!openspm_guest_clock_unpaced(&c) && openspm_guest_clock_skip_to(&c, 230u, 90000u) == 0);
    assert(now(&c, 5000u) == 16887u);
    openspm_guest_clock_resume(&c, 5000u);
    assert(now(&c, 5010u) == 16897u && openspm_guest_clock_unpaced(&c));
    openspm_guest_clock_set_speed(&c, 5010u, X1);                   /* turbo released */
    assert(!openspm_guest_clock_unpaced(&c) && openspm_guest_clock_skip_to(&c, 5010u, 40000u) == 0);
    assert(now(&c, 5020u) == 16907u);

    /* Long sessions: no overflow in the scaled delta (10 days at 4x). */
    memset(&c, 0, sizeof c);
    openspm_guest_clock_set_speed(&c, 0u, 4u * X1);
    assert(now(&c, UINT64_C(864000000000000)) == UINT64_C(3456000000000000));
    assert(openspm_guest_clock_scale(UINT64_C(0xFFFFFFFFFFFF), 3u * X1) == UINT64_C(0xFFFFFFFFFFFF) * 3u);

    /* Random rate changes, pauses and skips: guest time is monotonic in host
       time and continuous at every change. */
    {
        uint32_t seed = 12345u;
        uint64_t host = 1u, last = 0u;
        int step;
        memset(&c, 0, sizeof c);
        for (step = 0; step < 200000; ++step) {
            const uint32_t op = lcg(&seed) % 8u;
            const uint64_t before = now(&c, host);
            static const uint32_t speeds[] = {0u, X1, 2u * X1, 3u * X1, 4u * X1, UNPACED};
            assert(before >= last);
            if (op == 0u) openspm_guest_clock_set_speed(&c, host, speeds[lcg(&seed) % 6u]);
            else if (op == 1u) openspm_guest_clock_pause(&c, host);
            else if (op == 2u) openspm_guest_clock_resume(&c, host);
            else if (op == 3u) {
                const uint64_t target = before + lcg(&seed) % 20000000u;
                const int skipped = openspm_guest_clock_skip_to(&c, host, target);
                assert(skipped == openspm_guest_clock_unpaced(&c));
                assert(now(&c, host) == (skipped ? target : before));
                last = now(&c, host);
                host += lcg(&seed) % 5000000u;
                continue;
            }
            assert(now(&c, host) == before); /* continuity */
            last = before;
            host += lcg(&seed) % 5000000u;
        }
    }
    puts("PASS: OpenSPM guest clock (rate changes, pause, unlimited skip)");
    return 0;
}
