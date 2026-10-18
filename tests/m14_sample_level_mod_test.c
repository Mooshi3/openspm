/* Host test for the Sample Level mod (mods/sample_level): on its first tick
   it registers its two enemies (a Goomba and a Koopa Troopa underneath, its
   own look and numbers), its messages (every line and tattle an NPC uses),
   and the map os1_01 with a camera and an init script that sets the
   collision groups, places the blocks and coins (no save block: a save in a
   mod's map would not load without the mod), the two pipes
   back to Flipside, the arrival, three NPCs with their own talk scripts, the
   new enemies by their template numbers (each under a known name with its
   own tattle) and the music. F3 goes there from
   the game and back from the level, once per press, only in play. */
#include "../mods/sample_level/openspm_sample_level.c"

#include <assert.h>
#include <stdlib.h>

#define MEM1_SIZE 0x01800000u
static uint8_t *g_mem, *g_mem2;
static uint32_t g_scratch_used;
static int g_f3;

static openspm_map_def g_map;
static int g_maps;
static openspm_enemy_def g_enemy[4];
static int g_enemies;
static char g_keys[32][64];
static int g_messages;
static char g_go_map[32], g_go_door[32];
static int g_gos;

static uint8_t *P(uint32_t a) {
    if (a >= 0x80000000u && a < 0x80000000u + MEM1_SIZE) return g_mem + (a - 0x80000000u);
    if (a >= 0x90000000u && a < 0x94000000u) return g_mem2 + (a - 0x90000000u);
    return NULL;
}
static void w32(uint32_t a, uint32_t v) { uint8_t *p = P(a); p[0] = v >> 24; p[1] = v >> 16; p[2] = v >> 8; p[3] = v; }
static uint32_t r32(uint32_t a) { const uint8_t *p = P(a); return (uint32_t)p[0] << 24 | p[1] << 16 | p[2] << 8 | p[3]; }
static const char *gstr(uint32_t a) { return (const char *)P(a); }

static int OPENSPM_CALL f_r32(void *c, uint32_t a, uint32_t *v) { (void)c; if (!P(a)) return -1; *v = r32(a); return 0; }
static int OPENSPM_CALL f_w32(void *c, uint32_t a, uint32_t v) { (void)c; if (!P(a)) return -1; w32(a, v); return 0; }
static int OPENSPM_CALL f_call(void *c, uint32_t t, uint32_t n, const uint32_t *a) { (void)c; (void)t; (void)n; (void)a; return 0; }
static int OPENSPM_CALL f_scratch(void *c, uint32_t s, uint32_t *o) { (void)c; return openspm_guest_scratch_take(&g_scratch_used, s, o); }
static int OPENSPM_CALL f_key(void *c, const char *n) { (void)c; return strcmp(n, "F3") == 0 ? g_f3 : 0; }

static int OPENSPM_CALL f_register_map(void *c, const openspm_map_def *d, uint32_t *slot) {
    (void)c;
    g_map = *d;
    ++g_maps;
    *slot = 468u;
    return OPENSPM_OK;
}
static int OPENSPM_CALL f_go(void *c, const char *name, const char *door) {
    (void)c;
    snprintf(g_go_map, sizeof g_go_map, "%s", name);
    snprintf(g_go_door, sizeof g_go_door, "%s", door ? door : "");
    ++g_gos;
    return OPENSPM_OK;
}
static int OPENSPM_CALL f_message(void *c, const char *key, const char *text) {
    (void)c;
    assert(strlen(text) > 0u && strlen(text) < 2048u && strstr(text, "<k>") != NULL);
    snprintf(g_keys[g_messages++], 64, "%s", key);
    return OPENSPM_OK;
}
static int OPENSPM_CALL f_enemy(void *c, const openspm_enemy_def *d, uint32_t *t) {
    (void)c;
    g_enemy[g_enemies] = *d;
    *t = 435u + (uint32_t)g_enemies++;
    return OPENSPM_OK;
}
static int OPENSPM_CALL f_query(void *context, const char *id, uint32_t version, void *out) {
    (void)context;
    if (strcmp(id, OPENSPM_CONTENT_SERVICE_ID) == 0) {
        openspm_content_service *s = out;
        if (openspm_content_accept(s, version) != OPENSPM_OK) return OPENSPM_ERROR_ABI_MISMATCH;
        s->register_map = f_register_map;
        s->go_to_map = f_go;
        s->register_message = f_message;
        s->register_enemy = f_enemy;
        return OPENSPM_OK;
    }
    if (strcmp(id, OPENSPM_GUEST_MEMORY_SERVICE_ID) == 0) {
        openspm_guest_memory_service *s = out;
        s->read32 = f_r32;
        s->write32 = f_w32;
        return OPENSPM_OK;
    }
    if (strcmp(id, OPENSPM_GUEST_CALL_SERVICE_ID) == 0) {
        openspm_guest_call_service *s = out;
        s->call = f_call;
        s->scratch = f_scratch;
        return OPENSPM_OK;
    }
    if (strcmp(id, OPENSPM_INPUT_SERVICE_ID) == 0) {
        openspm_input_service *s = out;
        s->key_down = f_key;
        return OPENSPM_OK;
    }
    return OPENSPM_ERROR_NOT_FOUND;
}
static void OPENSPM_CALL f_log(void *c, openspm_log_level l, const char *t) { (void)c; (void)l; (void)t; }

static openspm_host_api g_host;
static openspm_mod_api g_api;
static void frame(void) { g_api.on_event(g_api.mod_context, OPENSPM_MOD_EVENT_TICK_BEGIN, NULL); }

/* One decoded instruction of a script. */
typedef struct op {
    uint32_t code, argc, args[16];
} op;

static uint32_t decode(uint32_t script, op *ops, uint32_t max) {
    uint32_t n = 0u, a = script;
    for (;;) {
        const uint32_t w = r32(a), argc = w >> 16, code = w & 0xFFFFu;
        uint32_t i;
        assert(n < max && argc <= 16u);
        ops[n].code = code;
        ops[n].argc = argc;
        for (i = 0; i < argc; ++i) ops[n].args[i] = r32(a + 4u + 4u * i);
        ++n;
        a += 4u + 4u * argc;
        if (code == SPM_EVT_OP_END_SCRIPT) return n;
    }
}

static int is_message(const char *key) {
    int i;
    for (i = 0; i < g_messages; ++i)
        if (strcmp(g_keys[i], key) == 0) return 1;
    return 0;
}

int main(void) {
    static op ops[512];
    uint32_t n, i, gp = 0x80500000u;
    int blocks = 0, coins = 0, saves = 0, npcs = 0, enemies[2] = {0, 0}, bgm = 0, arrival = 0, descs = 0, attrs = 0;
    g_mem = calloc(1, MEM1_SIZE);
    g_mem2 = calloc(1, 0x04000000u);
    assert(g_mem && g_mem2);
    memset(&g_host, 0, sizeof g_host);
    g_host.header.struct_size = sizeof g_host;
    g_host.header.abi_version = OPENSPM_ABI_VERSION;
    g_host.log = f_log;
    g_host.query_service = f_query;
    memset(&g_api, 0, sizeof g_api);
    g_api.header.struct_size = sizeof g_api;
    assert(openspm_mod_query(OPENSPM_ABI_VERSION, &g_host, &g_api) == OPENSPM_OK);
    g_api.on_event(g_api.mod_context, OPENSPM_MOD_EVENT_STARTUP, NULL);
    assert(g_maps == 0); /* nothing before the first tick (guest memory is open there) */
    frame();

    /* The enemies: a Goomba and a Koopa Troopa underneath, own numbers. */
    assert(g_enemies == 2);
    assert(g_enemy[0].base_template == 2u && g_enemy[0].max_hp == 3u && g_enemy[0].scale > 1.0f && g_enemy[0].color[0] == 255u);
    assert(g_enemy[1].base_template == 8u && g_enemy[1].max_hp == 2u && g_enemy[1].color[2] == 255u && g_enemy[1].scale == 0.0f);
    assert(g_enemy[0].catch_card == 0u && g_enemy[1].catch_card == 0u);

    /* The map. */
    assert(g_maps == 1 && strcmp(g_map.name, "os1_01") == 0 && g_map.file == NULL);
    assert(strcmp(g_map.fallback_door, "dokan_1") == 0 && g_map.init_script >= OPENSPM_GUEST_SCRATCH_BEGIN);
    assert(g_map.header.struct_size >= OPENSPM_MAP_DEF_V2_SIZE && g_map.camera_distance == 700.0f &&
           g_map.camera_x_range[0] < g_map.camera_x_range[1]);

    /* Its init script. */
    n = decode(g_map.init_script, ops, 512u);
    assert(ops[n - 2u].code == SPM_EVT_OP_END_EVT);
    for (i = 0; i < n; ++i) {
        const op *o = &ops[i];
        if (o->code == SPM_EVT_OP_RUN_CHILD_EVT) {
            assert(o->args[0] == SPM_R8PE01_DOOR_INIT_EVT && descs == 1); /* after the pipes */
            ++arrival;
        }
        if (o->code != SPM_EVT_OP_USER_FUNC) continue;
        if (o->args[0] == SPM_R8PE01_EVT_HITOBJ_ATTR_ONOFF) {
            assert(i < 2u); /* first */
            assert(strcmp(gstr(o->args[3]), i == 0 ? "A2D" : "A3D") == 0);
            assert(o->args[4] == (i == 0 ? SPM_R8PE01_HIT_ATTR_2D_ONLY : SPM_R8PE01_HIT_ATTR_3D_ONLY));
            ++attrs;
        } else if (o->args[0] == SPM_R8PE01_EVT_MOBJ_BLK) {
            assert(o->argc == 9u && gstr(o->args[2])[0] == 'b');
            ++blocks;
        } else if (o->args[0] == SPM_R8PE01_EVT_ITEM_ENTRY) {
            assert(o->argc == 11u && o->args[2] == SPM_R8PE01_ITEM_COIN);
            ++coins;
        } else if (o->args[0] == SPM_R8PE01_EVT_MOBJ_SAVE_BLK) {
            ++saves;
        } else if (o->args[0] == SPM_R8PE01_EVT_DOOR_SET_DOKAN_DESCS) {
            const uint32_t d = o->args[1];
            uint32_t k;
            assert(o->args[2] == 2u);
            for (k = 0; k < 2u; ++k) {
                const uint32_t e = d + SPM_R8PE01_DOKAN_DESC_SIZE * k;
                char want[16];
                snprintf(want, sizeof want, "dokan_%u", (unsigned)k + 1u);
                assert(r32(e) == 0u && r32(e + 4u) == 0u);
                assert(strcmp(gstr(r32(e + 8u)), want) == 0 && strcmp(gstr(r32(e + 12u)), "os1_01") == 0);
                assert(strncmp(gstr(r32(e + 16u)), "A2D_", 4) == 0 && strcmp(gstr(r32(e + 16u)) + 4, want) == 0);
                assert(strncmp(gstr(r32(e + 20u)), "A3D_", 4) == 0 && strcmp(gstr(r32(e + 20u)) + 4, want) == 0);
                assert(strcmp(gstr(r32(e + 24u)), "mac_02") == 0 && strcmp(gstr(r32(e + 28u)), "dokan_2") == 0);
            }
            ++descs;
        } else if (o->args[0] == SPM_R8PE01_EVT_NPC_ENTRY) {
            assert(strncmp(gstr(o->args[1]), "os1_", 4) == 0 && strncmp(gstr(o->args[2]), "n_", 2) == 0);
            ++npcs;
        } else if (o->args[0] == SPM_R8PE01_EVT_NPC_SET_PROPERTY && o->args[2] == SPM_R8PE01_NPC_PROPERTY_TALK) {
            static op talk[16];
            char tattle[64];
            const uint32_t m = decode(o->args[3], talk, 16u);
            assert(m == 5u && talk[0].args[0] == SPM_R8PE01_EVT_MARIO_KEY_OFF && talk[1].args[0] == SPM_R8PE01_EVT_MSG_PRINT &&
                   talk[2].args[0] == SPM_R8PE01_EVT_MARIO_KEY_ON);
            assert(is_message(gstr(talk[1].args[2])) && strcmp(gstr(talk[1].args[4]), "me") == 0);
            snprintf(tattle, sizeof tattle, "os1_01_%s", gstr(o->args[1])); /* Tippi's tattle */
            assert(is_message(tattle));
        } else if (o->args[0] == SPM_R8PE01_EVT_NPC_SET_PROPERTY && o->args[2] == SPM_R8PE01_NPC_PROPERTY_ANIM_DEFS) {
            const uint32_t d = o->args[3];
            assert(r32(d) == 0u && gstr(r32(d + 4u))[0] == 'S' && r32(d + 8u) == 3u && gstr(r32(d + 12u))[0] == 'T' &&
                   r32(d + 16u) == UINT32_MAX);
        } else if (o->args[0] == SPM_R8PE01_EVT_NPC_ENTRY_FROM_TEMPLATE) {
            char tattle[64];
            assert(o->args[2] == 435u || o->args[2] == 436u);
            assert(o->args[6] == 0u && o->args[7] == SPM_EVT_NULLPTR);
            /* Spawned as "npc_%08x" of 0x10000000 + the instance: its own tattle. */
            assert(o->args[1] != 0u);
            snprintf(tattle, sizeof tattle, "os1_01_npc_%08x", (unsigned)(0x10000000u + o->args[1]));
            assert(is_message(tattle));
            ++enemies[o->args[2] - 435u];
        } else if (o->args[0] == SPM_R8PE01_EVT_SND_BGMON) {
            assert(strncmp(gstr(o->args[2]), "BGM_", 4) == 0);
            ++bgm;
        }
    }
    assert(attrs == 2 && blocks == 7 && coins == 11 && saves == 0 && descs == 1 && arrival == 1);
    assert(npcs == 3 && enemies[0] == 3 && enemies[1] == 2 && bgm == 1);

    /* F3: only in play, once per press; to the level, and back. */
    w32(LEVEL_GP_PTR, gp);
    snprintf((char *)P(gp + LEVEL_GP_MAP_NAME), 32, "mac_02");
    w32(SPM_R8PE01_SEQ_WORK, 0u); /* the title screen */
    g_f3 = 1;
    frame();
    assert(g_gos == 0);
    g_f3 = 0;
    frame();
    w32(SPM_R8PE01_SEQ_WORK, SPM_R8PE01_SEQ_GAME);
    w32(SPM_R8PE01_SYSTEM_LEVEL, 0u);
    g_f3 = 1;
    frame();
    frame(); /* held: once */
    assert(g_gos == 1 && strcmp(g_go_map, "os1_01") == 0 && strcmp(g_go_door, "dokan_1") == 0);
    g_f3 = 0;
    frame();
    snprintf((char *)P(gp + LEVEL_GP_MAP_NAME), 32, "os1_01");
    w32(SPM_R8PE01_SYSTEM_LEVEL, 1u); /* a menu open: nothing */
    g_f3 = 1;
    frame();
    assert(g_gos == 1);
    g_f3 = 0;
    frame();
    w32(SPM_R8PE01_SYSTEM_LEVEL, 0u);
    g_f3 = 1;
    frame();
    assert(g_gos == 2 && strcmp(g_go_map, "mac_02") == 0 && strcmp(g_go_door, "dokan_2") == 0);
    /* Registered once. */
    assert(g_maps == 1 && g_enemies == 2);
    free(g_mem);
    free(g_mem2);
    puts("PASS: sample level mod");
    return 0;
}
