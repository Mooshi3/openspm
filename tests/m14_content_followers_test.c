/* Host test for followers (include/openspm/content_followers.h): the
   registration rules, the spawn script (the model, an animation table of
   stand / walk / run, decorative flags, the flip part, the position), the
   follower walking the player's trail at his distance (walk, run when far
   behind, stand when there), facing his heading, keeping to the ground while
   the player jumps (or floating with him), made again after a map change and
   after a cutscene deletes him, hidden and shown, away while a scene hides
   the player, and the NPC slots a busy
   map needs. */
#include "openspm/content_followers.h"
#include "openspm/guest_call.h"

#include <assert.h>
#include <stdlib.h>

#define MEM1 0x01800000u
static uint8_t *g_mem;
static openspm_guest_call_queue g_calls;
static uint32_t g_scratch_used;
static char g_all[8192];

static uint8_t *P(uint32_t a) {
    if (a >= 0x80000000u && a < 0x80000000u + MEM1) return g_mem + (a - 0x80000000u);
    if (a >= OPENSPM_GUEST_SCRATCH_BEGIN && a < OPENSPM_GUEST_SCRATCH_BEGIN + OPENSPM_GUEST_SCRATCH_SIZE)
        return g_mem + 0x01000000u + (a - OPENSPM_GUEST_SCRATCH_BEGIN);
    return NULL;
}
static void w32(uint32_t a, uint32_t v) { uint8_t *p = P(a); p[0] = v >> 24; p[1] = v >> 16; p[2] = v >> 8; p[3] = v; }
static uint32_t r32(uint32_t a) { const uint8_t *p = P(a); return (uint32_t)p[0] << 24 | p[1] << 16 | p[2] << 8 | p[3]; }
static void wf(uint32_t a, float f) { uint32_t b; memcpy(&b, &f, 4); w32(a, b); }
static float rf(uint32_t a) { uint32_t b = r32(a); float f; memcpy(&f, &b, 4); return f; }
static const char *str(uint32_t a) { return (const char *)P(a); }

static int f_read32(void *c, uint32_t a, uint32_t *v) { (void)c; if (!P(a)) return OPENSPM_ERROR_INVALID_ARGUMENT; *v = r32(a); return OPENSPM_OK; }
static int f_write32(void *c, uint32_t a, uint32_t v) { (void)c; if (!P(a)) return OPENSPM_ERROR_INVALID_ARGUMENT; w32(a, v); return OPENSPM_OK; }
static int f_call(void *c, uint32_t t, uint32_t n, const uint32_t *args) { (void)c; return openspm_guest_call_push(&g_calls, t, n, args); }
static int f_scratch(void *c, uint32_t s, uint32_t *o) { (void)c; return openspm_guest_scratch_take(&g_scratch_used, s, o); }
static void f_log(void *c, int warn, const char *t) {
    const size_t used = strlen(g_all);
    (void)c;
    (void)warn;
    if (used + strlen(t) + 2u < sizeof g_all) snprintf(g_all + used, sizeof g_all - used, "%s\n", t);
}
static const openspm_roster_io k_io = {NULL, f_read32, f_write32, f_call, f_scratch, NULL, NULL, f_log};

#define NPC_WORK 0x80600000u
#define NPC_ENTRIES 0x80610000u
#define NPC_SLOTS 16u
static openspm_followers g_s;

static void tick(void) { openspm_followers_tick(&g_s, &k_io); }
static void mario(float x, float y, float z, uint32_t motion) {
    wf(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_POSITION, x);
    wf(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_POSITION + 4u, y);
    wf(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_POSITION + 8u, z);
    w32(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_MOTION_ID, motion << 16);
}
static uint32_t npc(uint32_t i) { return NPC_ENTRIES + i * SPM_R8PE01_NPC_ENTRY_SIZE; }
static void kill_all(void) {
    uint32_t i;
    for (i = 0; i < NPC_SLOTS; ++i) w32(npc(i) + SPM_R8PE01_NPC_FLAG8, 0u);
}

/* The game runs a queued spawn script: an NPC with the script's name and
   position appears in slot `slot`. Returns the script. */
static uint32_t run_spawn(uint32_t slot) {
    openspm_guest_call_entry call;
    uint32_t script = 0u;
    while (openspm_guest_call_pop(&g_calls, &call))
        if (call.target == SPM_R8PE01_EVT_ENTRY && r32(call.args[0] + 4u) == SPM_R8PE01_EVT_NPC_ENTRY) script = call.args[0];
    assert(script != 0u);
    w32(npc(slot) + SPM_R8PE01_NPC_FLAG8, 1u);
    memcpy(P(npc(slot) + SPM_R8PE01_NPC_NAME), str(r32(script + 8u)), 16u);
    return script;
}
/* The animation scripts the follower started since the last call. */
static uint32_t last_anim(void) {
    openspm_guest_call_entry call;
    uint32_t anim = 0xFFu;
    while (openspm_guest_call_pop(&g_calls, &call))
        if (call.target == SPM_R8PE01_EVT_ENTRY && r32(call.args[0] + 4u) == SPM_R8PE01_EVT_NPC_SET_ANIM)
            anim = r32(call.args[0] + 12u);
    return anim;
}

static openspm_follower_def def(const char *key, const char *model) {
    openspm_follower_def d;
    memset(&d, 0, sizeof d);
    d.header.struct_size = sizeof d;
    d.key = key;
    d.model = model;
    return d;
}

int main(void) {
    char why[160];
    uint32_t id = 9u, i, script;
    openspm_follower_def d;
    float x;
    g_mem = calloc(1, MEM1);
    assert(g_mem);
    {
        static const uint32_t functions[] = {SPM_R8PE01_EVT_ENTRY, SPM_R8PE01_EVT_NPC_ENTRY, SPM_R8PE01_EVT_NPC_SET_PROPERTY,
                                             SPM_R8PE01_EVT_NPC_SET_ANIM, SPM_R8PE01_EVT_NPC_FLAG8_ONOFF,
                                             SPM_R8PE01_EVT_NPC_ANIMFLAG_ONOFF, SPM_R8PE01_EVT_NPC_SET_POSITION,
                                             SPM_R8PE01_EVT_NPC_DELETE};
        for (i = 0; i < sizeof functions / sizeof functions[0]; ++i) w32(functions[i], 0x9421FFF0u);
    }
    w32(SPM_R8PE01_NPC_WORK_PTR, NPC_WORK);
    w32(NPC_WORK + SPM_R8PE01_NPC_WORK_NUM, NPC_SLOTS);
    w32(NPC_WORK + SPM_R8PE01_NPC_WORK_ENTRIES, NPC_ENTRIES);
    openspm_followers_init(&g_s);

    /* Registration rules. */
    d = def("nokey", "n_stg5_hiroshi");
    assert(openspm_followers_register(&g_s, &d, &id, why, sizeof why) == OPENSPM_ERROR_INVALID_ARGUMENT);
    d = def("m:flint", "N_STG5");
    assert(openspm_followers_register(&g_s, &d, &id, why, sizeof why) == OPENSPM_ERROR_INVALID_ARGUMENT);
    d = def("m:flint", "n_stg5_hiroshi");
    d.walk_anim = "W 1";
    assert(openspm_followers_register(&g_s, &d, &id, why, sizeof why) == OPENSPM_ERROR_INVALID_ARGUMENT);
    d.walk_anim = NULL;
    d.distance = 500.0f;
    assert(openspm_followers_register(&g_s, &d, &id, why, sizeof why) == OPENSPM_ERROR_INVALID_ARGUMENT);
    d.distance = 0.0f;
    d.flags = 0x80u;
    assert(openspm_followers_register(&g_s, &d, &id, why, sizeof why) == OPENSPM_ERROR_INVALID_ARGUMENT);
    d.flags = 0u;
    assert(openspm_followers_register(&g_s, &d, &id, why, sizeof why) == OPENSPM_OK && id == 0u);
    assert(openspm_followers_register(&g_s, &d, &id, why, sizeof why) == OPENSPM_ERROR_RUNTIME); /* taken */
    assert(g_s.f[0].distance == 45.0f && strcmp(g_s.f[0].anim[1], "W_1") == 0);

    /* In game: built, then he is made behind Mario (Mario has not moved:
       right where Mario stands). */
    w32(SPM_R8PE01_SEQ_WORK, SPM_R8PE01_SEQ_GAME);
    mario(100.0f, 0.0f, 0.0f, SPM_R8PE01_MOT_STAY);
    tick();
    assert(g_s.built && strstr(g_all, "m:flint (model n_stg5_hiroshi; S_1 / W_1 / R_1) follows 45 units behind") != NULL);
    script = run_spawn(3u);
    assert(strcmp(str(r32(script + 12u)), "n_stg5_hiroshi") == 0);
    {
        /* The animation table: 0 stand, 1 walk, 2 run, -1 last. */
        uint32_t w = script + 4u * 5u, table;
        assert(r32(w) == ((4u << 16) | 0x5Cu) && r32(w + 4u) == SPM_R8PE01_EVT_NPC_SET_PROPERTY);
        table = r32(w + 16u);
        assert(r32(table) == 0u && strcmp(str(r32(table + 4u)), "S_1") == 0);
        assert(r32(table + 8u) == 1u && strcmp(str(r32(table + 12u)), "W_1") == 0);
        assert(r32(table + 16u) == 2u && strcmp(str(r32(table + 20u)), "R_1") == 0 && r32(table + 24u) == 0xFFFFFFFFu);
    }
    assert(strstr(g_all, "m:flint joins (1)") != NULL);

    /* Mario walks right 300 units: Flint walks behind him, facing right,
       and ends 45 units behind, standing. */
    for (x = 100.0f; x <= 400.0f; x += 2.0f) {
        mario(x, 0.0f, 0.0f, SPM_R8PE01_MOT_WALK);
        tick();
    }
    assert(g_s.f[0].state == OPENSPM_FOLLOWER_WALK && last_anim() == OPENSPM_FOLLOWER_WALK);
    assert(r32(npc(3) + SPM_R8PE01_NPC_AXIS) == 1u);
    for (i = 0; i < 60u; ++i) {
        mario(400.0f, 0.0f, 0.0f, SPM_R8PE01_MOT_STAY);
        tick();
    }
    assert(fabsf(rf(npc(3) + SPM_R8PE01_NPC_POSITION) - (400.0f - 45.0f)) < 3.5f);
    assert(g_s.f[0].state == OPENSPM_FOLLOWER_STAND && last_anim() == OPENSPM_FOLLOWER_STAND);

    /* Mario dashes off: Flint falls behind and runs. */
    for (x = 400.0f; x <= 700.0f; x += 8.0f) {
        mario(x, 0.0f, 0.0f, SPM_R8PE01_MOT_DASH);
        tick();
    }
    assert(g_s.f[0].state == OPENSPM_FOLLOWER_RUN);

    /* A jump: Mario goes up 60, Flint keeps to the ground. */
    for (i = 0; i < 30u; ++i) {
        mario(700.0f + 2.0f * (float)i, 2.0f * (float)i, 0.0f, SPM_R8PE01_MOT_JUMP);
        tick();
    }
    assert(fabsf(rf(npc(3) + SPM_R8PE01_NPC_POSITION + 4u)) < 0.01f);
    /* He lands on a ledge 40 up and walks on: Flint eases up to it. */
    for (i = 0; i < 120u; ++i) {
        mario(760.0f + 2.0f * (float)i, 40.0f, 0.0f, SPM_R8PE01_MOT_WALK);
        tick();
    }
    assert(fabsf(rf(npc(3) + SPM_R8PE01_NPC_POSITION + 4u) - 40.0f) < 0.5f);

    /* Back to the left: Flint turns. */
    for (i = 0; i < 80u; ++i) {
        mario(1000.0f - 2.0f * (float)i, 40.0f, 0.0f, SPM_R8PE01_MOT_WALK);
        tick();
    }
    assert(r32(npc(3) + SPM_R8PE01_NPC_AXIS) == 0xFFFFFFFFu);

    /* A door: the map's NPCs go; on the new map he is made again at Mario. */
    w32(SPM_R8PE01_SEQ_WORK, SPM_R8PE01_SEQ_MAPCHANGE);
    kill_all();
    tick();
    while (openspm_guest_call_pop(&g_calls, &(openspm_guest_call_entry){0})) {}
    w32(SPM_R8PE01_SEQ_WORK, SPM_R8PE01_SEQ_GAME);
    mario(-50.0f, 10.0f, 30.0f, SPM_R8PE01_MOT_STAY);
    tick();
    script = run_spawn(5u);
    tick();
    assert(fabsf(rf(npc(5) + SPM_R8PE01_NPC_POSITION) + 50.0f) < 0.01f && strstr(g_all, "joins (2)") != NULL);

    /* A cutscene deletes him: made again after a short wait. */
    kill_all();
    for (i = 0; i < OPENSPM_FOLLOWER_SPAWN_WAIT + 2u; ++i) tick();
    (void)run_spawn(2u);
    assert(strstr(g_all, "joins (3)") != NULL);

    /* Hidden: deleted once; shown: back. */
    assert(openspm_followers_set(&g_s, 0u, 0) == OPENSPM_OK && openspm_followers_set(&g_s, 7u, 1) != OPENSPM_OK);
    tick();
    {
        openspm_guest_call_entry call;
        int deleted = 0;
        while (openspm_guest_call_pop(&g_calls, &call))
            if (call.target == SPM_R8PE01_EVT_ENTRY && r32(call.args[0] + 4u) == SPM_R8PE01_EVT_NPC_DELETE) ++deleted;
        assert(deleted == 1);
    }
    kill_all();
    for (i = 0; i < OPENSPM_FOLLOWER_SPAWN_WAIT + 2u; ++i) tick();
    assert(!openspm_guest_call_pop(&g_calls, &(openspm_guest_call_entry){0}));
    (void)openspm_followers_set(&g_s, 0u, 1);
    tick();
    (void)run_spawn(2u);

    /* A scene without the player (Mario hidden): a short hide changes
       nothing; a long one sends him away (deleted once, not made again);
       Mario back: made again. */
    w32(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_FLAG8, SPM_R8PE01_MARIO_FLAG8_HIDDEN);
    {
        openspm_guest_call_entry call;
        int deleted = 0, spawned = 0;
        for (i = 0; i + 1u < OPENSPM_FOLLOWER_AWAY_FRAMES; ++i) tick();
        while (openspm_guest_call_pop(&g_calls, &call))
            if (call.target == SPM_R8PE01_EVT_ENTRY && r32(call.args[0] + 4u) == SPM_R8PE01_EVT_NPC_DELETE) ++deleted;
        assert(deleted == 0);
        tick();
        while (openspm_guest_call_pop(&g_calls, &call))
            if (call.target == SPM_R8PE01_EVT_ENTRY && r32(call.args[0] + 4u) == SPM_R8PE01_EVT_NPC_DELETE) ++deleted;
        assert(deleted == 1 && strstr(g_all, "the player is hidden") != NULL);
        kill_all();
        for (i = 0; i < OPENSPM_FOLLOWER_SPAWN_WAIT + 2u; ++i) tick();
        while (openspm_guest_call_pop(&g_calls, &call))
            if (call.target == SPM_R8PE01_EVT_ENTRY && r32(call.args[0] + 4u) == SPM_R8PE01_EVT_NPC_ENTRY) ++spawned;
        assert(spawned == 0);
    }
    w32(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_FLAG8, 0u);
    tick();
    (void)run_spawn(2u);

    /* A busy map (all but the margin in use): he waits. */
    kill_all();
    for (i = 4u; i < NPC_SLOTS; ++i) w32(npc(i) + SPM_R8PE01_NPC_FLAG8, 1u);
    for (i = 0; i < OPENSPM_FOLLOWER_SPAWN_WAIT + 2u; ++i) tick();
    assert(!openspm_guest_call_pop(&g_calls, &(openspm_guest_call_entry){0}));

    /* Floating: he rises with a jump. */
    openspm_followers_init(&g_s);
    memset(g_all, 0, sizeof g_all);
    kill_all();
    w32(SPM_R8PE01_SEQ_WORK, 1u);
    d = def("m:luvbi", "n_angeko");
    d.flags = OPENSPM_FOLLOWER_FLOATING;
    assert(openspm_followers_register(&g_s, &d, &id, why, sizeof why) == OPENSPM_OK);
    w32(SPM_R8PE01_SEQ_WORK, SPM_R8PE01_SEQ_GAME);
    mario(0.0f, 0.0f, 0.0f, SPM_R8PE01_MOT_STAY);
    tick();
    (void)run_spawn(1u);
    for (i = 0; i < 30u; ++i) {
        mario(0.0f, 2.0f * (float)i, 0.0f, SPM_R8PE01_MOT_JUMP);
        tick();
    }
    assert(rf(npc(1) + SPM_R8PE01_NPC_POSITION + 4u) > 50.0f);
    free(g_mem);
    puts("PASS: content followers");
    return 0;
}
