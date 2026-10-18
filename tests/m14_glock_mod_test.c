/* Host test for the Glock sample mod (mods/glock): a fake game provides guest
   memory, the NPC table, the tribes, input and the guest-call queue, and runs
   the EVT scripts the mod starts (one queued guest call per frame, like the
   backend) with a tiny interpreter for the user functions Glock uses. The test
   plays: revision check, calling Glock, the Pixl spot, arming, the consumed 1
   button, blasts that hit an enemy (Squirps' damage call) and never a
   townsperson, crouch, frozen world, map change, dismissal and a wrong game
   revision. */
#include "../mods/glock/openspm_glock.c"

#include <assert.h>
#include <stdlib.h>

#define MEM1_BASE 0x80000000u
#define MEM1_SIZE 0x01800000u
#define MEM2_BASE 0x90000000u
#define MEM2_SIZE 0x04000000u

#define FAKE_NPC_WORK 0x80600000u
#define FAKE_NPC_ENTRIES 0x80610000u
#define FAKE_NPC_CAPACITY 32u
#define FAKE_STRINGS 0x80700000u
#define FAKE_PARTS 0x80710000u

static uint8_t *g_mem1, *g_mem2;
static openspm_guest_call_queue g_queue;
static uint32_t g_scratch_used;
static int g_hotkey;
static uint32_t g_held, g_prev_held, g_set_held;
static int g_set_calls;
static char g_last_log[256];
static uint32_t g_sounds, g_damage_calls, g_damage_target, g_damage_part, g_damage_type, g_damage_power;
static uint32_t g_damage_attacker, g_npc_entries, g_deletes;
/* Fake openspm.content (Pixl mode) */
static int g_content, g_pixl_active, g_pixl_registered;
static openspm_pixl_def g_pixl_def;
static int OPENSPM_CALL f_register_pixl(void *c, const openspm_pixl_def *d, uint32_t *id) {
    (void)c; g_pixl_def = *d; ++g_pixl_registered; *id = 538u; return OPENSPM_OK;
}
static int OPENSPM_CALL f_pixl_active(void *c, uint32_t id) { (void)c; return id == 538u && g_pixl_active; }

static uint8_t *fake_ptr(uint32_t a, uint32_t n) {
    if (a >= MEM1_BASE && a + n <= MEM1_BASE + MEM1_SIZE) return g_mem1 + (a - MEM1_BASE);
    if (a >= MEM2_BASE && a + n <= MEM2_BASE + MEM2_SIZE) return g_mem2 + (a - MEM2_BASE);
    return NULL;
}
static void put32(uint32_t a, uint32_t v) {
    uint8_t *p = fake_ptr(a, 4);
    p[0] = (uint8_t)(v >> 24); p[1] = (uint8_t)(v >> 16); p[2] = (uint8_t)(v >> 8); p[3] = (uint8_t)v;
}
static uint32_t get32(uint32_t a) {
    const uint8_t *p = fake_ptr(a, 4);
    return (uint32_t)p[0] << 24 | (uint32_t)p[1] << 16 | (uint32_t)p[2] << 8 | p[3];
}
static void put16(uint32_t a, uint16_t v) {
    uint8_t *p = fake_ptr(a, 2);
    p[0] = (uint8_t)(v >> 8); p[1] = (uint8_t)v;
}
static void put_f(uint32_t a, float f) {
    uint32_t bits;
    memcpy(&bits, &f, sizeof bits);
    put32(a, bits);
}
static float get_f(uint32_t a) {
    uint32_t bits = get32(a);
    float f;
    memcpy(&f, &bits, sizeof f);
    return f;
}
static void put_str(uint32_t a, const char *s) { memcpy(fake_ptr(a, (uint32_t)strlen(s) + 1u), s, strlen(s) + 1u); }
static const char *get_str(uint32_t a) { return (const char *)fake_ptr(a, 1); }

static int OPENSPM_CALL f_read32(void *c, uint32_t a, uint32_t *v) {
    (void)c;
    if (!openspm_guest_address_ok(a, 4) || !fake_ptr(a, 4)) return OPENSPM_ERROR_INVALID_ARGUMENT;
    *v = get32(a);
    return OPENSPM_OK;
}
static int OPENSPM_CALL f_read16(void *c, uint32_t a, uint16_t *v) {
    const uint8_t *p;
    (void)c;
    if (!openspm_guest_address_ok(a, 2) || !(p = fake_ptr(a, 2))) return OPENSPM_ERROR_INVALID_ARGUMENT;
    *v = (uint16_t)(p[0] << 8 | p[1]);
    return OPENSPM_OK;
}
static int OPENSPM_CALL f_write32(void *c, uint32_t a, uint32_t v) {
    (void)c;
    if (!openspm_guest_address_ok(a, 4) || !fake_ptr(a, 4)) return OPENSPM_ERROR_INVALID_ARGUMENT;
    put32(a, v);
    return OPENSPM_OK;
}
static int OPENSPM_CALL f_call(void *c, uint32_t t, uint32_t n, const uint32_t *args) {
    (void)c;
    return openspm_guest_call_push(&g_queue, t, n, args);
}
static int OPENSPM_CALL f_scratch(void *c, uint32_t size, uint32_t *out) {
    (void)c;
    return openspm_guest_scratch_take(&g_scratch_used, size, out);
}
static int OPENSPM_CALL f_key(void *c, const char *name) {
    (void)c;
    return strcmp(name, "F7") == 0 ? g_hotkey : 0;
}
static int OPENSPM_CALL f_get_wii(void *c, uint32_t *held, uint32_t *pressed) {
    (void)c;
    if (held) *held = g_held;
    if (pressed) *pressed = g_held & ~g_prev_held;
    return OPENSPM_OK;
}
static int OPENSPM_CALL f_set_wii(void *c, uint32_t held) {
    (void)c;
    g_set_held = held;
    ++g_set_calls;
    return OPENSPM_OK;
}

static int OPENSPM_CALL f_query(void *context, const char *id, uint32_t version, void *out) {
    (void)context;
    if (strcmp(id, OPENSPM_GUEST_MEMORY_SERVICE_ID) == 0) {
        openspm_guest_memory_service *s = out;
        if (openspm_guest_memory_accept(s, version) != OPENSPM_OK) return OPENSPM_ERROR_ABI_MISMATCH;
        s->read32 = f_read32;
        s->read16 = f_read16;
        s->write32 = f_write32;
        return OPENSPM_OK;
    }
    if (strcmp(id, OPENSPM_GUEST_CALL_SERVICE_ID) == 0) {
        openspm_guest_call_service *s = out;
        if (openspm_guest_call_accept(s, version) != OPENSPM_OK) return OPENSPM_ERROR_ABI_MISMATCH;
        s->call = f_call;
        s->scratch = f_scratch;
        return OPENSPM_OK;
    }
    if (strcmp(id, OPENSPM_INPUT_SERVICE_ID) == 0) {
        openspm_input_service *s = out;
        if (openspm_input_accept(s, version) != OPENSPM_OK) return OPENSPM_ERROR_ABI_MISMATCH;
        s->key_down = f_key;
        s->get_wii_buttons = f_get_wii;
        s->set_wii_buttons = f_set_wii;
        return OPENSPM_OK;
    }
    if (g_content && strcmp(id, OPENSPM_CONTENT_SERVICE_ID) == 0) {
        openspm_content_service *s = out;
        if (openspm_content_accept(s, version) != OPENSPM_OK) return OPENSPM_ERROR_ABI_MISMATCH;
        s->register_pixl = f_register_pixl;
        s->pixl_active = f_pixl_active;
        return OPENSPM_OK;
    }
    return OPENSPM_ERROR_NOT_FOUND;
}

static void OPENSPM_CALL f_log(void *c, openspm_log_level level, const char *text) {
    (void)c;
    (void)level;
    snprintf(g_last_log, sizeof g_last_log, "%s", text);
}

/* ---- fake game ------------------------------------------------------------ */
static uint32_t npc_addr(uint32_t i) { return FAKE_NPC_ENTRIES + i * SPM_R8PE01_NPC_ENTRY_SIZE; }

static uint32_t find_npc(const char *name) {
    uint32_t i;
    for (i = 0u; i < FAKE_NPC_CAPACITY; ++i)
        if ((get32(npc_addr(i) + SPM_R8PE01_NPC_FLAG8) & 1u) && strcmp(get_str(npc_addr(i) + 0x24u), name) == 0)
            return npc_addr(i);
    return 0u;
}

static uint32_t add_npc(const char *name, const char *pose, uint32_t tribe, uint32_t move, float x, float y) {
    uint32_t i, npc, part;
    for (i = 0u; i < FAKE_NPC_CAPACITY; ++i)
        if (!(get32(npc_addr(i) + SPM_R8PE01_NPC_FLAG8) & 1u)) break;
    assert(i < FAKE_NPC_CAPACITY);
    npc = npc_addr(i);
    memset(fake_ptr(npc, SPM_R8PE01_NPC_ENTRY_SIZE), 0, SPM_R8PE01_NPC_ENTRY_SIZE);
    put32(npc + SPM_R8PE01_NPC_FLAG8, 1u);
    put_str(npc + SPM_R8PE01_NPC_NAME, name);
    put_str(npc + SPM_R8PE01_NPC_POSE_NAME, pose);
    put32(npc + SPM_R8PE01_NPC_TRIBE_ID, tribe);
    put32(npc + SPM_R8PE01_NPC_MOVE_MODE, move);
    put_f(npc + SPM_R8PE01_NPC_POSITION, x);
    put_f(npc + SPM_R8PE01_NPC_POSITION + 4u, y);
    put32(npc + SPM_R8PE01_NPC_HP, 1u);
    part = FAKE_PARTS + i * 0x400u;
    memset(fake_ptr(part, 0x400u), 0, 0x400u);
    put16(part + SPM_R8PE01_NPC_PART_ID, 1u);
    put_f(part + SPM_R8PE01_NPC_PART_HITBOX, 24.0f);
    put_f(part + SPM_R8PE01_NPC_PART_HITBOX + 4u, 24.0f);
    put32(npc + SPM_R8PE01_NPC_PARTS, part);
    return npc;
}

static int32_t evt_int(uint32_t w) { return (int32_t)w; }
static float evt_float(uint32_t w) { return (float)((int32_t)w + 240000000) / 1024.0f; }

/* Runs one script's USER_FUNCs (no waits in Glock's scripts). */
static void run_script(uint32_t script) {
    uint32_t pc = script, guard = 0u;
    for (;;) {
        const uint32_t head = get32(pc), op = head & 0xFFFFu, argc = head >> 16;
        assert(++guard < 64u);
        if (op == SPM_EVT_OP_END_EVT) return;
        assert(op == SPM_EVT_OP_USER_FUNC);
        {
            const uint32_t fn = get32(pc + 4u), a0 = get32(pc + 8u);
            if (fn == SPM_R8PE01_EVT_NPC_ENTRY) {
                assert(find_npc(get_str(a0)) == 0u); /* never twice */
                assert(strcmp(get_str(get32(pc + 12u)), "e_kozun") == 0);
                (void)add_npc(get_str(a0), "e_kozun", 0u, 0u, 0.0f, 0.0f);
                ++g_npc_entries;
            } else if (fn == SPM_R8PE01_EVT_NPC_TAKE_DAMAGE) {
                g_damage_attacker = a0;
                g_damage_target = get32(pc + 12u);
                g_damage_part = get32(pc + 16u);
                g_damage_type = get32(pc + 24u);
                g_damage_power = get32(pc + 28u);
                ++g_damage_calls;
            } else if (fn == SPM_R8PE01_EVT_SND_SFXON) {
                assert(a0 == SPM_R8PE01_SFX_SQUIRPS_SHOT_STRING);
                ++g_sounds;
            } else {
                /* Everything else asserts in the real game on a missing name. */
                const uint32_t npc = find_npc(get_str(a0));
                assert(npc != 0u);
                if (fn == SPM_R8PE01_EVT_NPC_SET_POSITION) {
                    put_f(npc + SPM_R8PE01_NPC_POSITION, evt_float(get32(pc + 12u)));
                    put_f(npc + SPM_R8PE01_NPC_POSITION + 4u, evt_float(get32(pc + 16u)));
                    put_f(npc + SPM_R8PE01_NPC_POSITION + 8u, evt_float(get32(pc + 20u)));
                } else if (fn == SPM_R8PE01_EVT_NPC_SET_AXIS) {
                    put32(npc + SPM_R8PE01_NPC_AXIS, (uint32_t)evt_int(get32(pc + 12u)));
                } else if (fn == SPM_R8PE01_EVT_NPC_DELETE) {
                    put32(npc + SPM_R8PE01_NPC_FLAG8, 0u);
                    ++g_deletes;
                } else if (fn == SPM_R8PE01_EVT_NPC_SET_PROPERTY) {
                    assert(get32(pc + 12u) == 14u);
                } else {
                    assert(fn == SPM_R8PE01_EVT_NPC_SET_ANIM || fn == SPM_R8PE01_EVT_NPC_FLAG8_ONOFF ||
                           fn == SPM_R8PE01_EVT_NPC_ANIMFLAG_ONOFF || fn == SPM_R8PE01_EVT_NPC_ADD_FLIP_PART);
                }
            }
        }
        pc += 4u * (1u + argc);
    }
}

static openspm_host_api g_host;
static openspm_mod_api g_api;

static void load(void) {
    memset(&g_host, 0, sizeof g_host);
    g_host.header.struct_size = sizeof g_host;
    g_host.header.abi_version = OPENSPM_ABI_VERSION;
    g_host.log = f_log;
    g_host.query_service = f_query;
    memset(&g_api, 0, sizeof g_api);
    g_api.header.struct_size = sizeof g_api;
    assert(openspm_mod_query(OPENSPM_ABI_VERSION, &g_host, &g_api) == OPENSPM_OK);
    g_api.on_event(g_api.mod_context, OPENSPM_MOD_EVENT_STARTUP, NULL);
}

/* One game frame: the mod's TICK_BEGIN, then the backend emits one queued
   guest call, which (for evtEntry) the fake evtmgr runs. */
static void frame(void) {
    openspm_runtime_tick_event tick;
    openspm_guest_call_entry call;
    memset(&tick, 0, sizeof tick);
    tick.header.struct_size = sizeof tick;
    g_api.on_event(g_api.mod_context, OPENSPM_MOD_EVENT_TICK_BEGIN, &tick);
    g_prev_held = g_held;
    if (openspm_guest_call_pop(&g_queue, &call)) {
        assert(call.target == SPM_R8PE01_EVT_ENTRY && call.argc == 3u);
        run_script(call.args[0]);
    }
}
static void frames(uint32_t n) {
    while (n--) frame();
}

static void game_state(uint32_t seq, uint32_t level, uint16_t motion) {
    put32(SPM_R8PE01_SEQ_WORK, seq);
    put32(SPM_R8PE01_SYSTEM_LEVEL, level);
    put16(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_MOTION_ID, motion);
}

static void setup_game(void) {
    static const uint32_t functions[] = {
        SPM_R8PE01_EVT_ENTRY, SPM_R8PE01_EVT_NPC_ENTRY, SPM_R8PE01_EVT_NPC_SET_PROPERTY,
        SPM_R8PE01_EVT_NPC_SET_ANIM, SPM_R8PE01_EVT_NPC_FLAG8_ONOFF, SPM_R8PE01_EVT_NPC_ANIMFLAG_ONOFF,
        SPM_R8PE01_EVT_NPC_ADD_FLIP_PART, SPM_R8PE01_EVT_NPC_SET_POSITION, SPM_R8PE01_EVT_NPC_SET_AXIS,
        SPM_R8PE01_EVT_NPC_DELETE, SPM_R8PE01_EVT_NPC_TAKE_DAMAGE, SPM_R8PE01_EVT_SND_SFXON};
    uint32_t i;
    memset(g_mem1, 0, MEM1_SIZE);
    memset(g_mem2, 0, MEM2_SIZE);
    for (i = 0u; i < sizeof functions / sizeof functions[0]; ++i) put32(functions[i], 0x9421FFF0u);
    put_str(FAKE_STRINGS, "e_kozun");
    put_str(FAKE_STRINGS + 0x20u, "e_kuribo");
    put32(SPM_R8PE01_NPC_TRIBES + SPM_R8PE01_TRIBE_FRACKLE * SPM_R8PE01_NPC_TRIBE_SIZE, FAKE_STRINGS);
    put32(SPM_R8PE01_NPC_TRIBES + SPM_R8PE01_TRIBE_FRACKLE * SPM_R8PE01_NPC_TRIBE_SIZE + 4u, 0x80408000u);
    put32(SPM_R8PE01_NPC_TRIBES + SPM_R8PE01_TRIBE_WRACKLE_SHOT * SPM_R8PE01_NPC_TRIBE_SIZE, FAKE_STRINGS);
    put32(SPM_R8PE01_NPC_TRIBES + SPM_R8PE01_TRIBE_WRACKLE_SHOT * SPM_R8PE01_NPC_TRIBE_SIZE + 4u, 0x80408100u);
    put32(SPM_R8PE01_NPC_TRIBES, FAKE_STRINGS + 0x20u); /* tribe 0: Goomba */
    put_str(SPM_R8PE01_SFX_SQUIRPS_SHOT_STRING, "SFX_MAP_TAMARA_SHOT1");
    put32(SPM_R8PE01_NPC_WORK_PTR, FAKE_NPC_WORK);
    put32(FAKE_NPC_WORK + 4u, FAKE_NPC_CAPACITY);
    put32(FAKE_NPC_WORK + 8u, FAKE_NPC_ENTRIES);
    put_f(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_POSITION, 100.0f);
    put_f(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_POSITION + 4u, 0.0f);
    put_f(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_FACING_ANGLE, 180.0f); /* facing right */
    memset(&g_queue, 0, sizeof g_queue);
    g_scratch_used = 0u;
}

static void press_f7(void) {
    g_hotkey = 1;
    frame();
    g_hotkey = 0;
}
static void press_1(void) {
    g_held = OPENSPM_WII_BTN_1;
    frame();
    g_held = 0u;
    frame();
}

int main(void) {
    uint32_t glock, goomba, townie, i;
    float gx;
    g_mem1 = calloc(1, MEM1_SIZE);
    g_mem2 = calloc(1, MEM2_SIZE);
    assert(g_mem1 && g_mem2);
    setup_game();
    load();

    /* Title screen: nothing. */
    game_state(0u, 0u, SPM_R8PE01_MOT_STAY);
    press_f7();
    frames(40);
    assert(g_scratch_used == 0u && g_npc_entries == 0u);

    /* In a level, not called: scripts built, nothing in the game. */
    game_state(2u, 0u, SPM_R8PE01_MOT_STAY);
    frames(40);
    assert(g_scratch_used != 0u && g_npc_entries == 0u && g_queue.count == 0u);

    /* F7: Glock and his four blasts are made once, Glock behind Mario's head. */
    press_f7();
    frames(10);
    assert(g_npc_entries == 5u);
    glock = find_npc("glock");
    assert(glock && find_npc("glock_b0") && find_npc("glock_b3"));
    assert(get_f(find_npc("glock_b0") + SPM_R8PE01_NPC_POSITION + 4u) == GLOCK_PARK_Y);
    frames(30);
    assert(g_npc_entries == 5u);
    gx = get_f(glock + SPM_R8PE01_NPC_POSITION);
    assert(gx < 100.0f && get_f(glock + SPM_R8PE01_NPC_POSITION + 4u) > 25.0f); /* behind, above */
    assert(get32(glock + SPM_R8PE01_NPC_AXIS) == 1u);

    /* 1: armed, in front; the 1 button never reaches the game. */
    g_set_calls = 0;
    press_1();
    assert(g_set_calls >= 1 && (g_set_held & OPENSPM_WII_BTN_1) == 0u);
    frames(20);
    assert(get_f(glock + SPM_R8PE01_NPC_POSITION) > 115.0f);
    assert(g_mod.armed && g_mod.shots == 0u);

    /* An enemy ahead and a townsperson (a tribe-0 NPC with its own model) in
       the line of fire: a blast flies, hits the enemy with Squirps' damage call
       and parks; the townsperson is never hit. */
    townie = add_npc("townie", "n_machi_roten", 0u, 1u, 160.0f, 0.0f);
    goomba = add_npc("goomba", "e_kuribo", 0u, 1u, 220.0f, 0.0f);
    press_1();
    assert(g_mod.shots == 1u && g_sounds == 1u);
    for (i = 0u; i < 30u && g_damage_calls == 0u; ++i) frame();
    assert(g_damage_calls == 1u);
    assert(strcmp(get_str(g_damage_attacker), "mario") == 0);
    assert(g_damage_target == goomba + SPM_R8PE01_NPC_NAME && g_damage_part == 1u);
    assert(g_damage_type == 32u && g_damage_power == GLOCK_POWER);
    assert(get_f(find_npc("glock_b0") + SPM_R8PE01_NPC_POSITION + 4u) == GLOCK_PARK_Y);
    (void)townie;

    /* 2D: depth does not count, a goomba far behind the blast's plane is hit;
       in 3D (the game's camera test) the same goomba is out of the lane. */
    put_f(goomba + SPM_R8PE01_NPC_POSITION + 8u, 150.0f);
    frames(GLOCK_FIRE_COOLDOWN);
    press_1();
    for (i = 0u; i < 30u && g_damage_calls == 1u; ++i) frame();
    assert(g_damage_calls == 2u);
    put32(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_CAMERA_ID, 1u);
    put32(SPM_R8PE01_CAMERA_TABLE + 4u, 0x80780000u);
    put32(0x80780000u + 8u, 0x00000001u); /* camera +10 (u16) = 1: 3D */
    frames(GLOCK_FIRE_COOLDOWN);
    press_1();
    frames(GLOCK_BLAST_LIFE + 2u);
    assert(g_mod.shots == 3u && g_damage_calls == 2u);
    put32(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_CAMERA_ID, 0u);
    put_f(goomba + SPM_R8PE01_NPC_POSITION + 8u, 0.0f);

    /* Facing left: blasts fly left, Glock turns. */
    put_f(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_FACING_ANGLE, 0.0f);
    frames(20);
    assert(get_f(glock + SPM_R8PE01_NPC_POSITION) < 85.0f && get32(glock + SPM_R8PE01_NPC_AXIS) == 0xFFFFFFFFu);
    frames(GLOCK_FIRE_COOLDOWN);
    press_1();
    assert(g_mod.shots == 4u);
    frames(3);
    assert(g_mod.blasts[0].active && g_mod.blasts[0].rx < 0.0f);
    frames(GLOCK_BLAST_LIFE);
    assert(!g_mod.blasts[0].active && g_damage_calls == 2u);

    /* A building whose 2D plane is turned (the camera's right axis is -z):
       armed Glock sits in front along that axis, and a blast flies along it
       and hits an enemy standing on it. */
    {
        const uint32_t cam = 0x80781000u;
        const float mx = get_f(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_POSITION);
        const float mz = get_f(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_POSITION + 8u);
        uint32_t hits = g_damage_calls;
        put32(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_CAMERA_ID, 2u);
        put32(SPM_R8PE01_CAMERA_TABLE + 8u, cam);
        put_f(cam + SPM_R8PE01_CAMERA_VIEW_MTX, 0.0f);
        put_f(cam + SPM_R8PE01_CAMERA_VIEW_MTX + 8u, -1.0f);
        put_f(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_FACING_ANGLE, 180.0f); /* right */
        frames(30);
        assert(fabsf(get_f(glock + SPM_R8PE01_NPC_POSITION + 8u) - (mz - 26.0f)) < 2.0f);
        assert(fabsf(get_f(glock + SPM_R8PE01_NPC_POSITION) - (mx + 1.0f)) < 2.0f);
        put_f(goomba + SPM_R8PE01_NPC_POSITION, mx);
        put_f(goomba + SPM_R8PE01_NPC_POSITION + 8u, mz - 100.0f);
        frames(GLOCK_FIRE_COOLDOWN);
        press_1();
        for (i = 0u; i < 30u && g_damage_calls == hits; ++i) frame();
        assert(g_damage_calls == hits + 1u);
        g_damage_calls = hits; /* the counts below are the flat-map ones */
        --g_mod.shots;
        put32(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_CAMERA_ID, 0u);
        put_f(goomba + SPM_R8PE01_NPC_POSITION, 220.0f);
        put_f(goomba + SPM_R8PE01_NPC_POSITION + 8u, 0.0f);
        frames(GLOCK_BLAST_LIFE);
    }

    /* 3D: his facing value only says left / right on screen; forward is the
       way Mario walks. Walking along +z, armed Glock is ahead on +z and the
       blast flies that way. */
    {
        float mz = get_f(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_POSITION + 8u);
        const float mx = get_f(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_POSITION);
        put32(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_CAMERA_ID, 1u); /* the 3D camera from above */
        for (i = 0u; i < 30u; ++i) {
            mz += 2.0f;
            put_f(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_POSITION + 8u, mz);
            frame();
        }
        assert(g_mod.fz > 0.99f && fabsf(g_mod.fx) < 0.01f);
        assert(fabsf(get_f(glock + SPM_R8PE01_NPC_POSITION + 8u) - (mz + 26.0f)) < 6.0f);
        assert(fabsf(get_f(glock + SPM_R8PE01_NPC_POSITION) - mx) < 2.0f);
        /* Facing: the flip stays +1 and he turns about y to +z (270 deg). */
        assert(get32(glock + SPM_R8PE01_NPC_AXIS) == 1u);
        assert(fabsf(get_f(glock + SPM_R8PE01_NPC_ROTATION + 4u) - 270.0f) < 0.5f);
        frames(GLOCK_FIRE_COOLDOWN);
        press_1();
        frames(2);
        assert(g_mod.blasts[0].active && g_mod.blasts[0].rz > 0.99f);
        --g_mod.shots;
        frames(GLOCK_BLAST_LIFE);
        put32(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_CAMERA_ID, 0u);
        put_f(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_POSITION + 8u, 0.0f);
        frames(5);
        assert(get_f(glock + SPM_R8PE01_NPC_ROTATION + 4u) == 0.0f); /* 2D again: no turn */
    }

    /* Crouch: back to the Pixl spot. */
    game_state(2u, 0u, SPM_R8PE01_MOT_CROUCH);
    frame();
    assert(!g_mod.armed);
    game_state(2u, 0u, SPM_R8PE01_MOT_STAY);
    press_1(); /* re-arms, no blast */
    assert(g_mod.armed && g_mod.shots == 4u);

    /* Frozen world: 1 is left to the game (menus need it) and nothing moves. */
    game_state(2u, 1u, SPM_R8PE01_MOT_STAY);
    gx = get_f(glock + SPM_R8PE01_NPC_POSITION);
    g_set_calls = 0;
    put_f(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_POSITION, 300.0f);
    press_1();
    assert(g_set_calls == 0 && g_mod.shots == 4u && get_f(glock + SPM_R8PE01_NPC_POSITION) == gx);

    /* Map change: the game drops every NPC; Glock comes back once the new
       map has settled, exactly once. */
    for (i = 0u; i < FAKE_NPC_CAPACITY; ++i) put32(npc_addr(i) + SPM_R8PE01_NPC_FLAG8, 0u);
    game_state(3u, 1u, SPM_R8PE01_MOT_STAY);
    frames(20);
    game_state(2u, 0u, SPM_R8PE01_MOT_STAY);
    frames(GLOCK_SETTLE_FRAMES - 2u);
    assert(g_npc_entries == 5u);
    frames(20);
    assert(g_npc_entries == 10u && find_npc("glock") && !g_mod.armed);

    /* F7: sent away, every NPC of his deleted, nothing afterwards. */
    press_f7();
    frames(10);
    assert(g_deletes == 5u && !find_npc("glock") && !find_npc("glock_b2"));
    frames(100);
    assert(g_npc_entries == 10u && g_queue.count == 0u);

    /* A full map: he waits instead of taking the last slots. */
    for (i = 0u; i < FAKE_NPC_CAPACITY - 6u; ++i) (void)add_npc("crowd", "e_kuribo", 0u, 1u, 0.0f, -500.0f);
    press_f7();
    frames(5);
    assert(g_npc_entries == 10u && strstr(g_last_log, "NPC slots") != NULL);
    frames(100);
    assert(g_npc_entries == 10u);
    press_f7();

    /* Pixl mode: Glock registers as a Pixl, comes out when picked in the Pixl
       menu and goes away when another Pixl is picked; F7 does nothing. */
    setup_game();
    for (i = 0u; i < FAKE_NPC_CAPACITY; ++i) put32(npc_addr(i) + SPM_R8PE01_NPC_FLAG8, 0u);
    g_content = 1;
    g_npc_entries = 0u;
    g_deletes = 0u;
    load();
    assert(g_pixl_registered == 1 && strcmp(g_pixl_def.model, "e_kozun") == 0 && strcmp(g_pixl_def.name, "Glock") == 0);
    assert(g_mod.pixl_mode && g_mod.pixl_item == 538u);
    game_state(2u, 0u, SPM_R8PE01_MOT_STAY);
    press_f7();
    frames(60);
    assert(g_npc_entries == 0u);
    g_pixl_active = 1;
    frames(GLOCK_SETTLE_FRAMES + 10u);
    assert(g_npc_entries == 5u && find_npc("glock"));
    press_1();
    assert(g_mod.armed);
    g_pixl_active = 0;
    frames(10);
    assert(g_deletes == 5u && !find_npc("glock") && !g_mod.wanted);
    g_content = 0;

    /* Another revision: the Frackle tribe has another model -> off, logged. */
    setup_game();
    put_str(FAKE_STRINGS, "e_other");
    load();
    game_state(2u, 0u, SPM_R8PE01_MOT_STAY);
    press_f7();
    frames(60);
    assert(g_scratch_used == 0u && g_queue.count == 0u && strstr(g_last_log, "off") != NULL);

    free(g_mem1);
    free(g_mem2);
    puts("PASS: glock mod");
    return 0;
}
