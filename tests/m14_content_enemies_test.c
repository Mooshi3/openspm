/* Host test for include/openspm/content_enemies.h: a registered enemy takes
   the first spare tribe and template rows (535 and 435 on), its tribe is the
   look tribe with the definition's model, HP, attack, score and catch card,
   its template the base template naming the new tribe; a tint, a size or a
   contact damage gets a spawn script that runs the base's as a child first;
   the game's rows are untouched; bad definitions, a build without the
   enemy patches and a full table are refused. */
#include "openspm/content_enemies.h"
#include "openspm/guest_call.h"

#include <assert.h>
#include <stdlib.h>

#define MEM1 0x01800000u
static uint8_t *g_mem1, *g_mem2;
static uint32_t g_scratch_used;

static uint8_t *P(uint32_t a) {
    if (a >= 0x80000000u && a < 0x80000000u + MEM1) return g_mem1 + (a - 0x80000000u);
    if (a >= 0x90000000u && a < 0x94000000u) return g_mem2 + (a - 0x90000000u);
    return NULL;
}
static void w32(uint32_t a, uint32_t v) { uint8_t *p = P(a); p[0] = v >> 24; p[1] = v >> 16; p[2] = v >> 8; p[3] = v; }
static uint32_t r32(uint32_t a) { const uint8_t *p = P(a); return (uint32_t)p[0] << 24 | p[1] << 16 | p[2] << 8 | p[3]; }
static uint32_t r16(uint32_t a) { const uint8_t *p = P(a); return (uint32_t)p[0] << 8 | p[1]; }
static uint32_t r8(uint32_t a) { return *P(a); }
static int f_read32(void *c, uint32_t a, uint32_t *v) { (void)c; if (!P(a)) return OPENSPM_ERROR_INVALID_ARGUMENT; *v = r32(a); return OPENSPM_OK; }
static int f_write32(void *c, uint32_t a, uint32_t v) { (void)c; if (!P(a)) return OPENSPM_ERROR_INVALID_ARGUMENT; w32(a, v); return OPENSPM_OK; }
static int f_scratch(void *c, uint32_t s, uint32_t *o) { (void)c; return openspm_guest_scratch_take(&g_scratch_used, s, o); }
static const openspm_roster_io k_io = {NULL, f_read32, f_write32, NULL, f_scratch, NULL, NULL, NULL};

static openspm_enemy_def def(const char *key, uint32_t base) {
    openspm_enemy_def d;
    memset(&d, 0, sizeof d);
    d.header.struct_size = sizeof d;
    d.key = key;
    d.base_template = base;
    d.look_tribe = -1;
    d.score = -1;
    return d;
}

int main(void) {
    static openspm_enemies s;
    openspm_enemy_def d;
    char why[160];
    uint32_t id = 0, i, k;
    uint8_t *game_tribes, *game_templates;
    g_mem1 = calloc(1, MEM1);
    g_mem2 = calloc(1, 0x04000000u);
    assert(g_mem1 && g_mem2);
    /* The game's rows (row i: bytes of i), the spare ones copies of row 0;
       tribe 7 is a Goomba-like one, template 2 uses it with spawn script
       0x80454504. */
    for (i = 0; i < OPENSPM_ENEMIES_GAME_TRIBES + OPENSPM_ENEMIES_SPARE; ++i)
        for (k = 0; k < OPENSPM_ENEMIES_ROW; ++k)
            *P(openspm_enemies_tribe(i) + k) = (uint8_t)(i < OPENSPM_ENEMIES_GAME_TRIBES ? i * 3u + k : k);
    for (i = 0; i < OPENSPM_ENEMIES_GAME_TEMPLATES + OPENSPM_ENEMIES_SPARE; ++i)
        for (k = 0; k < OPENSPM_ENEMIES_ROW; ++k)
            *P(openspm_enemies_template(i) + k) = (uint8_t)(i < OPENSPM_ENEMIES_GAME_TEMPLATES ? i * 5u + k : k);
    w32(openspm_enemies_tribe(7) + OPENSPM_TRIBE_MODEL, 0x80300000u);
    w32(openspm_enemies_tribe(7) + OPENSPM_TRIBE_CARD, 282u);
    w32(openspm_enemies_template(2) + OPENSPM_TEMPLATE_TRIBE, 7u);
    w32(openspm_enemies_template(2) + OPENSPM_TEMPLATE_SPAWN_SCRIPT, 0x80454504u);
    w32(openspm_enemies_template(3) + OPENSPM_TEMPLATE_TRIBE, 7u);
    w32(openspm_enemies_template(3) + OPENSPM_TEMPLATE_SPAWN_SCRIPT, 0u);
    game_tribes = malloc(OPENSPM_ENEMIES_GAME_TRIBES * OPENSPM_ENEMIES_ROW);
    game_templates = malloc(OPENSPM_ENEMIES_GAME_TEMPLATES * OPENSPM_ENEMIES_ROW);
    memcpy(game_tribes, P(OPENSPM_ENEMIES_TRIBES), OPENSPM_ENEMIES_GAME_TRIBES * OPENSPM_ENEMIES_ROW);
    memcpy(game_templates, P(OPENSPM_ENEMIES_TEMPLATES), OPENSPM_ENEMIES_GAME_TEMPLATES * OPENSPM_ENEMIES_ROW);
    openspm_enemies_init(&s);

    /* Without the enemy patches: refused. */
    d = def("m:brick", 2u);
    assert(openspm_enemies_register(&s, &k_io, &d, &id, why, sizeof why) == OPENSPM_ERROR_UNSUPPORTED);
    w32(OPENSPM_ENEMIES_TRIBE_LIS, OPENSPM_ENEMIES_TRIBE_LIS_MOVED);
    w32(OPENSPM_ENEMIES_TEMPLATE_LIS, OPENSPM_ENEMIES_TEMPLATE_LIS_MOVED);
    assert(openspm_enemies_register(&s, &k_io, &d, &id, why, sizeof why) == OPENSPM_ERROR_UNSUPPORTED);
    w32(OPENSPM_ENEMIES_TEMPLATE_BOUND, OPENSPM_ENEMIES_TEMPLATE_BOUND_MOVED);

    /* Bad definitions. */
    d = def("m:x", 0u);
    assert(openspm_enemies_register(&s, &k_io, &d, &id, why, sizeof why) == OPENSPM_ERROR_INVALID_ARGUMENT);
    d = def("m:x", 435u);
    assert(openspm_enemies_register(&s, &k_io, &d, &id, why, sizeof why) == OPENSPM_ERROR_INVALID_ARGUMENT);
    d = def("m:x", 2u);
    d.look_tribe = 535;
    assert(openspm_enemies_register(&s, &k_io, &d, &id, why, sizeof why) == OPENSPM_ERROR_INVALID_ARGUMENT);
    d = def("m:x", 2u);
    d.max_hp = 256u;
    assert(openspm_enemies_register(&s, &k_io, &d, &id, why, sizeof why) == OPENSPM_ERROR_INVALID_ARGUMENT);
    d = def("m:x", 2u);
    d.model = "e kuribo";
    assert(openspm_enemies_register(&s, &k_io, &d, &id, why, sizeof why) == OPENSPM_ERROR_INVALID_ARGUMENT);
    d = def("m:x", 2u);
    d.scale = 20.0f;
    assert(openspm_enemies_register(&s, &k_io, &d, &id, why, sizeof why) == OPENSPM_ERROR_INVALID_ARGUMENT);
    d = def("m:x", 2u);
    d.catch_card = 600u; /* an added item: not a game card */
    assert(openspm_enemies_register(&s, &k_io, &d, &id, why, sizeof why) == OPENSPM_ERROR_INVALID_ARGUMENT);
    d = def("m:x", 2u);
    d.flags = 1u;
    assert(openspm_enemies_register(&s, &k_io, &d, &id, why, sizeof why) == OPENSPM_ERROR_INVALID_ARGUMENT);
    assert(s.count == 0u);

    /* A plain re-statted one: no spawn script of its own. */
    d = def("m:tough", 2u);
    d.max_hp = 6u;
    d.attack = 3u;
    d.score = 500;
    assert(openspm_enemies_register(&s, &k_io, &d, &id, why, sizeof why) == OPENSPM_OK && id == 435u);
    {
        const uint32_t t = openspm_enemies_tribe(535u), m = openspm_enemies_template(435u);
        assert(r32(m + OPENSPM_TEMPLATE_TRIBE) == 535u && r32(m + OPENSPM_TEMPLATE_SPAWN_SCRIPT) == 0x80454504u);
        for (k = 0; k < OPENSPM_ENEMIES_ROW; k += 4u)
            if (k != OPENSPM_TEMPLATE_TRIBE) assert(r32(m + k) == r32(openspm_enemies_template(2u) + k));
        assert(r32(t + OPENSPM_TRIBE_MODEL) == 0x80300000u && r32(t + OPENSPM_TRIBE_CARD) == 0u); /* no card */
        assert(r8(t + OPENSPM_TRIBE_HP) == 6u && r8(t + OPENSPM_TRIBE_ATTACK) == 3u && r16(t + OPENSPM_TRIBE_SCORE) == 500u);
        /* The rest is the look tribe's (the base template's tribe 7). */
        assert(r8(t + OPENSPM_TRIBE_HP + 1u) == r8(openspm_enemies_tribe(7u) + OPENSPM_TRIBE_HP + 1u));
        assert(r32(t + 0x1Cu) == r32(openspm_enemies_tribe(7u) + 0x1Cu) && r32(t + 0x50u) == r32(openspm_enemies_tribe(7u) + 0x50u));
        assert(r16(t + OPENSPM_TRIBE_SCORE + 2u) == r16(openspm_enemies_tribe(7u) + OPENSPM_TRIBE_SCORE + 2u));
    }

    /* A tinted, bigger, harder-hitting one with the look of tribe 9, its own
       model and the look tribe's card. */
    d = def("m:brick", 2u);
    d.look_tribe = 9;
    d.model = "e_kuribo_y";
    d.catch_card = OPENSPM_ENEMY_CARD_LOOK;
    d.color[0] = 255u;
    d.color[1] = 96u;
    d.color[2] = 64u;
    d.color[3] = 255u;
    d.scale = 1.5f;
    d.contact_damage = 4u;
    assert(openspm_enemies_register(&s, &k_io, &d, &id, why, sizeof why) == OPENSPM_OK && id == 436u);
    {
        const uint32_t t = openspm_enemies_tribe(536u), m = openspm_enemies_template(436u);
        const uint32_t sc = r32(m + OPENSPM_TEMPLATE_SPAWN_SCRIPT);
        const uint32_t f15 = SPM_EVT_FLOAT(1.5f);
        uint32_t me;
        assert(strcmp((const char *)P(r32(t + OPENSPM_TRIBE_MODEL)), "e_kuribo_y") == 0);
        assert(r32(t + OPENSPM_TRIBE_CARD) == r32(openspm_enemies_tribe(9u) + OPENSPM_TRIBE_CARD));
        assert(r8(t + OPENSPM_TRIBE_HP) == r8(openspm_enemies_tribe(9u) + OPENSPM_TRIBE_HP)); /* 0: the look tribe's */
        assert(r32(m + OPENSPM_TEMPLATE_TRIBE) == 536u && sc >= OPENSPM_GUEST_SCRATCH_BEGIN);
        /* RUN_CHILD_EVT base; USER_FUNC set_color me 255 96 64 255;
           USER_FUNC set_scale me 1.5 1.5 1.5; USER_FUNC set_part_attack_power me -1 4; END. */
        assert(r32(sc) == ((1u << 16) | SPM_EVT_OP_RUN_CHILD_EVT) && r32(sc + 4u) == 0x80454504u);
        assert(r32(sc + 8u) == ((6u << 16) | SPM_EVT_OP_USER_FUNC) && r32(sc + 12u) == SPM_R8PE01_EVT_NPC_SET_COLOR);
        me = r32(sc + 16u);
        assert(strcmp((const char *)P(me), "me") == 0);
        assert(r32(sc + 20u) == 255u && r32(sc + 24u) == 96u && r32(sc + 28u) == 64u && r32(sc + 32u) == 255u);
        assert(r32(sc + 36u) == ((5u << 16) | SPM_EVT_OP_USER_FUNC) && r32(sc + 40u) == SPM_R8PE01_EVT_NPC_SET_SCALE);
        assert(r32(sc + 44u) == me && r32(sc + 48u) == f15 && r32(sc + 52u) == f15 && r32(sc + 56u) == f15);
        assert(r32(sc + 60u) == ((4u << 16) | SPM_EVT_OP_USER_FUNC) &&
               r32(sc + 64u) == SPM_R8PE01_EVT_NPC_SET_PART_ATTACK_POWER);
        assert(r32(sc + 68u) == me && r32(sc + 72u) == UINT32_MAX && r32(sc + 76u) == 4u);
        assert(r32(sc + 80u) == SPM_EVT_OP_END_EVT && r32(sc + 84u) == SPM_EVT_OP_END_SCRIPT);
    }

    /* A base with no spawn script and a tint: the tint alone. */
    d = def("m:pale", 3u);
    d.color[3] = 128u;
    assert(openspm_enemies_register(&s, &k_io, &d, &id, why, sizeof why) == OPENSPM_OK && id == 437u);
    {
        const uint32_t sc = r32(openspm_enemies_template(437u) + OPENSPM_TEMPLATE_SPAWN_SCRIPT);
        assert(r32(sc) == ((6u << 16) | SPM_EVT_OP_USER_FUNC) && r32(sc + 28u) == SPM_EVT_OP_END_EVT);
    }
    /* The mod's own spawn script, run as the child. */
    d = def("m:own", 2u);
    d.spawn_script = 0x93F02000u;
    assert(openspm_enemies_register(&s, &k_io, &d, &id, why, sizeof why) == OPENSPM_OK && id == 438u);
    {
        const uint32_t sc = r32(openspm_enemies_template(438u) + OPENSPM_TEMPLATE_SPAWN_SCRIPT);
        assert(r32(sc + 4u) == 0x93F02000u && r32(sc + 8u) == SPM_EVT_OP_END_EVT);
    }
    /* The same key twice: refused. */
    assert(openspm_enemies_register(&s, &k_io, &d, &id, why, sizeof why) == OPENSPM_ERROR_RUNTIME);

    /* The game's rows are as they were. */
    assert(memcmp(game_tribes, P(OPENSPM_ENEMIES_TRIBES), OPENSPM_ENEMIES_GAME_TRIBES * OPENSPM_ENEMIES_ROW) == 0);
    assert(memcmp(game_templates, P(OPENSPM_ENEMIES_TEMPLATES), OPENSPM_ENEMIES_GAME_TEMPLATES * OPENSPM_ENEMIES_ROW) == 0);

    /* Full: 64 enemies, then refused. */
    for (i = s.count; i < OPENSPM_ENEMIES_SPARE; ++i) {
        char key[32];
        snprintf(key, sizeof key, "m:e%u", (unsigned)i);
        d = def(key, 2u);
        assert(openspm_enemies_register(&s, &k_io, &d, &id, why, sizeof why) == OPENSPM_OK && id == 435u + i);
    }
    assert(s.count == OPENSPM_ENEMIES_SPARE);
    d = def("m:one_more", 2u);
    assert(openspm_enemies_register(&s, &k_io, &d, &id, why, sizeof why) == OPENSPM_ERROR_RUNTIME);
    free(game_tribes);
    free(game_templates);
    free(g_mem1);
    free(g_mem2);
    puts("PASS: content enemies");
    return 0;
}
