/* OpenSPM sample mod: "Sample Level", a new playable level with its own
 * map, its own enemies and its own NPCs (openspm.content maps, enemies and
 * messages; docs/CUSTOM-LEVELS.md). Nothing of the game's is replaced.
 *
 *   The map "os1_01" (the area "os1"): a meadow built by
 *   tools/content/sample_level.py into the mod's files folder
 *   (mods/openspm_sample_level/files/map/os1_01.bin), with an entrance and an
 *   exit pipe, a step up, platforms, "?" blocks and coins (three hidden
 *   behind, for flipping to 3D). OpenSPM moves the camera there (the map has
 *   no camera road). No save block on purpose: a save made in a mod's map
 *   cannot be loaded once the mod is off.
 *
 *   Two new enemies: the Brick Goomba (a Goomba's behaviour, tinted brick
 *   red, bigger, 3 HP, hits for 2) and the Frost Koopa (a Koopa Troopa's,
 *   tinted ice blue, 2 HP, hits for 2). They get new template numbers; the
 *   game's Goombas and Koopas are untouched.
 *
 *   Three NPCs with their own lines and tattles: Marigold by the entrance,
 *   Sprout up on a platform, Old Rowan by the exit. Each placed enemy has
 *   its own tattle too (it is spawned under a known name).
 *
 * F3 (outside menus) takes the player there through the entrance pipe;
 * either pipe, or F3 again, goes back to Flipside (the main square's pipe).
 *
 * Positions repeat tools/content/sample_level.py's LAYOUT.
 *
 * Portable C: Windows DLL for OpenSPMNative, host object for
 * tests/m14_sample_level_mod_test.c. */
#include "openspm/content_service.h"
#include "openspm/guest_call.h"
#include "openspm/guest_memory.h"
#include "openspm/input_service.h"
#include "openspm/mod_api.h"
#include "openspm/spm_evt.h"
#include "openspm/spm_r8pe01.h"

#include <stddef.h>
#include <stdio.h>
#include <string.h>

#define MEMBER_END(type, member) (offsetof(type, member) + sizeof(((type *)0)->member))

#define LEVEL_MAP "os1_01"
#define LEVEL_ENTRANCE "dokan_1"
#define LEVEL_HOTKEY "F3"
#define FLIPSIDE_MAP "mac_02"   /* Flipside's main square */
#define FLIPSIDE_PIPE "dokan_2" /* its pipe to the lower town */
#define LEVEL_SCRIPT_WORDS 2048u
#define LEVEL_GP_PTR UINT32_C(0x8056CB78) /* -> the game's globals; +0x44 the map's name */
#define LEVEL_GP_MAP_NAME 0x44u
#define LEVEL_BGM "BGM_MAP_STG1"
/* Enemies spawned with instance N are named "npc_%08x" of 0x10000000 + N
   (npcEntryFromSetupEnemy), which is what Tippi's tattle looks for first
   ("<map>_<NPC name>"): each placed enemy gets its own tattle. */
#define LEVEL_ENEMY_INSTANCE 0x00051000u
#define LEVEL_ENEMY_NAME_BIAS 0x10000000u

typedef struct level_block {
    uint32_t type;
    const char *name;
    int32_t x, y, z;
    uint32_t item;
} level_block;

typedef struct level_coin {
    const char *name;
    int32_t x, y, z;
} level_coin;

typedef struct level_npc {
    const char *name, *model, *stand, *talk, *message, *tattle;
    int32_t x, y, z;
} level_npc;

typedef struct level_enemy {
    int brick; /* 1: Brick Goomba, 0: Frost Koopa */
    int32_t x, y, z;
} level_enemy;

static const level_block k_blocks[] = {
    {SPM_R8PE01_BLK_BRICK, "b_00", -300, 75, 0, 0u},
    {SPM_R8PE01_BLK_QUESTION, "b_01", -275, 75, 0, SPM_R8PE01_ITEM_COIN},
    {SPM_R8PE01_BLK_QUESTION, "b_02", -250, 75, 0, SPM_R8PE01_ITEM_MUSHROOM},
    {SPM_R8PE01_BLK_QUESTION, "b_03", -225, 75, 0, SPM_R8PE01_ITEM_COIN},
    {SPM_R8PE01_BLK_BRICK, "b_04", -200, 75, 0, 0u},
    {SPM_R8PE01_BLK_QUESTION, "b_05", 360, 125, 0, SPM_R8PE01_ITEM_COIN},
    {SPM_R8PE01_BLK_QUESTION, "b_06", 1050, 75, 0, SPM_R8PE01_ITEM_COIN},
};

static const level_coin k_coins[] = {
    {"c_00", 445, 160, 0}, {"c_01", 470, 170, 0}, {"c_02", 495, 160, 0},  /* over the first platform */
    {"c_03", 0, 0, -100},  {"c_04", 25, 0, -100}, {"c_05", 50, 0, -100},  /* behind: flip to 3D */
    {"c_06", 640, 215, 0}, {"c_07", 665, 215, 0},                         /* over the second platform */
    {"c_08", 880, 0, 0},   {"c_09", 905, 0, 0},   {"c_10", 930, 0, 0},
};

static const level_npc k_npcs[] = {
    {"os1_marigold", "n_machi_madam01b", "S_1", "T_1", "os1_01_marigold_01", "os1_01_os1_marigold", -420, 0, 0},
    {"os1_sprout", "n_kingyo", "S_1", "T_1", "os1_01_sprout_01", "os1_01_os1_sprout", 650, 190, 0},
    {"os1_rowan", "n_machi_man02a", "S_5", "T_5", "os1_01_rowan_01", "os1_01_os1_rowan", 990, 0, 0},
};

static const level_enemy k_enemies[] = {
    {1, -150, 0, 0}, {1, 150, 0, 0}, {0, 520, 50, 0}, {0, 740, 50, 0}, {1, 1120, 0, 0},
};

/* The mod's messages: the NPCs' lines (key, text) and their tattles
   (Tippi's tattle looks for "<map>_<NPC name>" first). */
static const char *const k_messages[][2] = {
    {"os1_01_marigold_01",
     "Oh! You came up through the new\npipe too?<k>\n<p>\nThis meadow just appeared one\nmorning. It isn't on any map\nin Flipside.<k>\n<p>\n"
     "Do be careful. The Goombas here\nare made of tougher stuff.<k>"},
    {"os1_01_sprout_01",
     "I climbed up here to watch the\nclouds roll by.<k>\n<p>\nThose ice-blue Koopas can't jump,\nso nobody bothers me up here!<k>"},
    {"os1_01_rowan_01",
     "That pipe goes back to Flipside.<k>\n<p>\nSomeone made this whole place from\nscratch, they say. Even us!<k>\n<p>\nCome back any time. The meadow\nwill be here.<k>"},
    {"os1_01_os1_marigold",
     "That's Marigold. She runs a flower\nstall in Flipside, so she came to\nsee the new meadow's flowers.<k>\n<p>\nShe says none of them match the\nones she knows. Odd, huh?<k>"},
    {"os1_01_os1_sprout",
     "That's Sprout. He found the best\nspot in the whole meadow.<k>\n<p>\nHe's been up there all day. I don't\nthink he knows how to get down.<k>"},
    {"os1_01_os1_rowan",
     "That's Old Rowan. He was the first\none here, and he seems to know a\nlot about this place.<k>\n<p>\nMaybe a little too much...<k>"},
};

static const char k_brick_tattle[] =
    "That's a Brick Goomba. A Goomba that\nate a whole brick wall once and\nnever got over it.<k>\n<p>\n"
    "Max HP is 3. Attack is 2. Stomp it\nthree times, or just go around!<k>";
static const char k_frost_tattle[] =
    "That's a Frost Koopa. A Koopa Troopa\nthat took a wrong turn somewhere\ncold.<k>\n<p>\n"
    "Max HP is 2. Attack is 2. Its shell\nis extra slippery when kicked!<k>";

static struct {
    const openspm_host_api *host;
    openspm_content_service content;
    openspm_guest_memory_service mem;
    openspm_guest_call_service calls;
    openspm_input_service input;
    int ready, have_keys, built, failed, key_was_down;
    uint32_t brick_goomba, frost_koopa, map_slot, init_script;
} g_mod;

static void mod_log(openspm_log_level level, const char *message) {
    if (g_mod.host && g_mod.host->log) g_mod.host->log(g_mod.host->context, level, message);
}

static int query(const char *id, uint32_t version, void *service, uint32_t size) {
    openspm_abi_header *header = (openspm_abi_header *)service;
    memset(service, 0, size);
    header->struct_size = size;
    header->abi_version = version;
    return g_mod.host->query_service(g_mod.host->context, id, version, service) == OPENSPM_OK;
}

static int read32(uint32_t address, uint32_t *value) {
    return g_mod.mem.read32(g_mod.mem.context, address, value) == OPENSPM_OK;
}

static uint32_t put_bytes(const void *bytes, uint32_t n) {
    const uint8_t *b = (const uint8_t *)bytes;
    const uint32_t size = (n + 3u) & ~3u;
    uint32_t at = 0u, i;
    if (size == 0u || g_mod.calls.scratch(g_mod.calls.context, size, &at) != OPENSPM_OK) return 0u;
    for (i = 0; i < size; i += 4u) {
        uint32_t w = 0u, k;
        for (k = 0; k < 4u; ++k) w = w << 8 | (i + k < n ? b[i + k] : 0u);
        if (g_mod.mem.write32(g_mod.mem.context, at + i, w) != OPENSPM_OK) return 0u;
    }
    return at;
}

static uint32_t put_words(const uint32_t *words, uint32_t count) {
    uint32_t at = 0u, i;
    if (count == 0u || g_mod.calls.scratch(g_mod.calls.context, 4u * count, &at) != OPENSPM_OK) return 0u;
    for (i = 0; i < count; ++i)
        if (g_mod.mem.write32(g_mod.mem.context, at + 4u * i, words[i]) != OPENSPM_OK) return 0u;
    return at;
}

/* Guest strings, made once each. */
#define LEVEL_STRINGS 96u
static struct {
    const char *text;
    uint32_t at;
} g_strings[LEVEL_STRINGS];
static uint32_t g_string_count;

static uint32_t str(const char *text) {
    uint32_t i;
    for (i = 0; i < g_string_count; ++i)
        if (strcmp(g_strings[i].text, text) == 0) return g_strings[i].at;
    if (g_string_count == LEVEL_STRINGS) {
        g_mod.failed = 1;
        return 0u;
    }
    g_strings[g_string_count].text = text;
    g_strings[g_string_count].at = put_bytes(text, (uint32_t)strlen(text) + 1u);
    if (g_strings[g_string_count].at == 0u) g_mod.failed = 1;
    return g_strings[g_string_count++].at;
}

static void call(spm_evt_builder *b, uint32_t function, uint32_t argc, const uint32_t *args) {
    spm_evt_user_func(b, function, argc, args);
}

static uint32_t finish(spm_evt_builder *b) {
    const uint32_t n = spm_evt_finish(b);
    if (n == 0u) {
        g_mod.failed = 1;
        return 0u;
    }
    return put_words(b->words, n);
}

/* An NPC's talk script: hold the player, its line, let go. */
static uint32_t talk_script(const level_npc *npc) {
    uint32_t words[32];
    spm_evt_builder b;
    const uint32_t off[1] = {0u};
    const uint32_t print[4] = {0u, str(npc->message), 0u, str("me")};
    spm_evt_init(&b, words, 32u);
    call(&b, SPM_R8PE01_EVT_MARIO_KEY_OFF, 1u, off);
    call(&b, SPM_R8PE01_EVT_MSG_PRINT, 4u, print);
    call(&b, SPM_R8PE01_EVT_MARIO_KEY_ON, 0u, NULL);
    return finish(&b);
}

/* An NPC's animations: stand (0) and talk (3), as the game's townsfolk. */
static uint32_t anim_defs(const level_npc *npc) {
    const uint32_t words[6] = {0u, str(npc->stand), 3u, str(npc->talk), UINT32_MAX, 0u};
    return put_words(words, 6u);
}

/* The map's pipes: both lead back to Flipside's main square. */
static uint32_t dokan_descs(void) {
    static const char *const names[2] = {"dokan_1", "dokan_2"};
    static const char *const hit2d[2] = {"A2D_dokan_1", "A2D_dokan_2"};
    static const char *const hit3d[2] = {"A3D_dokan_1", "A3D_dokan_2"};
    uint32_t words[2 * SPM_R8PE01_DOKAN_DESC_SIZE / 4u], i;
    for (i = 0; i < 2u; ++i) {
        uint32_t *d = words + i * (SPM_R8PE01_DOKAN_DESC_SIZE / 4u);
        d[0] = 0u; /* the plain upright pipe */
        d[1] = 0u; /* flags */
        d[2] = str(names[i]);
        d[3] = str(LEVEL_MAP);
        d[4] = str(hit2d[i]);
        d[5] = str(hit3d[i]);
        d[6] = str(FLIPSIDE_MAP);
        d[7] = str(FLIPSIDE_PIPE);
    }
    return put_words(words, (uint32_t)(sizeof words / sizeof words[0]));
}

static uint32_t init_script(void) {
    static uint32_t words[LEVEL_SCRIPT_WORDS];
    spm_evt_builder b;
    uint32_t i, descs;
    spm_evt_init(&b, words, LEVEL_SCRIPT_WORDS);
    {
        const uint32_t a2d[4] = {1u, 1u, str("A2D"), SPM_R8PE01_HIT_ATTR_2D_ONLY};
        const uint32_t a3d[4] = {1u, 1u, str("A3D"), SPM_R8PE01_HIT_ATTR_3D_ONLY};
        call(&b, SPM_R8PE01_EVT_HITOBJ_ATTR_ONOFF, 4u, a2d);
        call(&b, SPM_R8PE01_EVT_HITOBJ_ATTR_ONOFF, 4u, a3d);
    }
    for (i = 0; i < sizeof k_blocks / sizeof k_blocks[0]; ++i) {
        const level_block *k = &k_blocks[i];
        const uint32_t a[8] = {k->type, str(k->name), (uint32_t)k->x, (uint32_t)k->y, (uint32_t)k->z, k->item, 0u, 0u};
        call(&b, SPM_R8PE01_EVT_MOBJ_BLK, 8u, a);
    }
    for (i = 0; i < sizeof k_coins / sizeof k_coins[0]; ++i) {
        const level_coin *c = &k_coins[i];
        const uint32_t a[10] = {str(c->name), SPM_R8PE01_ITEM_COIN, 0u, (uint32_t)c->x, (uint32_t)c->y, (uint32_t)c->z,
                                0u, 0u, 0u, 0u};
        call(&b, SPM_R8PE01_EVT_ITEM_ENTRY, 10u, a);
    }
    descs = dokan_descs();
    if (descs == 0u) g_mod.failed = 1;
    {
        const uint32_t a[2] = {descs, 2u};
        call(&b, SPM_R8PE01_EVT_DOOR_SET_DOKAN_DESCS, 2u, a);
    }
    spm_evt_run_child(&b, SPM_R8PE01_DOOR_INIT_EVT);
    for (i = 0; i < sizeof k_npcs / sizeof k_npcs[0]; ++i) {
        const level_npc *n = &k_npcs[i];
        const uint32_t name = str(n->name), anims = anim_defs(n), talk = talk_script(n);
        const uint32_t entry[3] = {name, str(n->model), 0u};
        const uint32_t axis[2] = {name, 1u};
        const uint32_t prop_anims[3] = {name, SPM_R8PE01_NPC_PROPERTY_ANIM_DEFS, anims};
        const uint32_t prop_talk[3] = {name, SPM_R8PE01_NPC_PROPERTY_TALK, talk};
        const uint32_t anim[3] = {name, 0u, 1u};
        const uint32_t flag8[3] = {name, 1u, SPM_R8PE01_NPC_FLAG8_TOWNSPERSON};
        const uint32_t animflag[3] = {name, 1u, SPM_R8PE01_NPC_ANIMFLAG_TOWNSPERSON};
        const uint32_t flip[1] = {name};
        const uint32_t move[2] = {name, 1u};
        const uint32_t pos[4] = {name, (uint32_t)n->x, (uint32_t)n->y, (uint32_t)n->z};
        if (anims == 0u || talk == 0u) g_mod.failed = 1;
        call(&b, SPM_R8PE01_EVT_NPC_ENTRY, 3u, entry);
        call(&b, SPM_R8PE01_EVT_NPC_SET_AXIS, 2u, axis);
        call(&b, SPM_R8PE01_EVT_NPC_SET_PROPERTY, 3u, prop_anims);
        call(&b, SPM_R8PE01_EVT_NPC_SET_PROPERTY, 3u, prop_talk);
        call(&b, SPM_R8PE01_EVT_NPC_SET_ANIM, 3u, anim);
        call(&b, SPM_R8PE01_EVT_NPC_FLAG8_ONOFF, 3u, flag8);
        call(&b, SPM_R8PE01_EVT_NPC_ANIMFLAG_ONOFF, 3u, animflag);
        call(&b, SPM_R8PE01_EVT_NPC_ADD_FLIP_PART, 1u, flip);
        call(&b, SPM_R8PE01_EVT_NPC_SET_MOVE_MODE, 2u, move);
        call(&b, SPM_R8PE01_EVT_NPC_SET_POSITION, 4u, pos);
    }
    for (i = 0; i < sizeof k_enemies / sizeof k_enemies[0]; ++i) {
        const level_enemy *e = &k_enemies[i];
        /* (instance, template, x, y, z, no name out, no id out) */
        const uint32_t a[7] = {LEVEL_ENEMY_INSTANCE + i, e->brick ? g_mod.brick_goomba : g_mod.frost_koopa,
                               (uint32_t)e->x, (uint32_t)e->y, (uint32_t)e->z, 0u, SPM_EVT_NULLPTR};
        call(&b, SPM_R8PE01_EVT_NPC_ENTRY_FROM_TEMPLATE, 7u, a);
    }
    {
        const uint32_t a[2] = {0u, str(LEVEL_BGM)};
        call(&b, SPM_R8PE01_EVT_SND_BGMON, 2u, a);
    }
    return finish(&b);
}

static int register_enemies(void) {
    openspm_enemy_def e;
    memset(&e, 0, sizeof e);
    e.header.struct_size = sizeof e;
    e.header.abi_version = OPENSPM_CONTENT_SERVICE_VERSION;
    e.key = "openspm.sample.level:brick_goomba";
    e.base_template = 2u; /* a Goomba */
    e.look_tribe = -1;
    e.max_hp = 3u;
    e.attack = 2u;
    e.contact_damage = 2u;
    e.score = 300;
    e.color[0] = 255u;
    e.color[1] = 130u;
    e.color[2] = 90u;
    e.color[3] = 255u;
    e.scale = 1.35f;
    if (g_mod.content.register_enemy(g_mod.content.context, &e, &g_mod.brick_goomba) != OPENSPM_OK) return 0;
    e.key = "openspm.sample.level:frost_koopa";
    e.base_template = 8u; /* a Koopa Troopa */
    e.max_hp = 2u;
    e.attack = 2u;
    e.contact_damage = 2u;
    e.score = 400;
    e.color[0] = 150u;
    e.color[1] = 215u;
    e.color[2] = 255u;
    e.scale = 0.0f;
    return g_mod.content.register_enemy(g_mod.content.context, &e, &g_mod.frost_koopa) == OPENSPM_OK;
}

static void build(void) {
    openspm_map_def map;
    char message[200];
    uint32_t i;
    g_mod.built = 1;
    for (i = 0; i < sizeof k_messages / sizeof k_messages[0]; ++i)
        (void)g_mod.content.register_message(g_mod.content.context, k_messages[i][0], k_messages[i][1]);
    for (i = 0; i < sizeof k_enemies / sizeof k_enemies[0]; ++i) {
        char key[48];
        snprintf(key, sizeof key, "%s_npc_%08x", LEVEL_MAP, (unsigned)(LEVEL_ENEMY_NAME_BIAS + LEVEL_ENEMY_INSTANCE + i));
        (void)g_mod.content.register_message(g_mod.content.context, key,
                                             k_enemies[i].brick ? k_brick_tattle : k_frost_tattle);
    }
    if (!register_enemies()) {
        mod_log(OPENSPM_LOG_WARN, "Sample Level: the new enemies were refused (see above); the level is off.");
        g_mod.failed = 1;
        return;
    }
    g_mod.init_script = init_script();
    if (g_mod.failed || g_mod.init_script == 0u) {
        mod_log(OPENSPM_LOG_WARN, "Sample Level: no guest memory for the level's scripts; the level is off.");
        g_mod.failed = 1;
        return;
    }
    memset(&map, 0, sizeof map);
    map.header.struct_size = sizeof map;
    map.header.abi_version = OPENSPM_CONTENT_SERVICE_VERSION;
    map.key = "openspm.sample.level:os1_01";
    map.name = LEVEL_MAP;
    map.fallback_door = LEVEL_ENTRANCE;
    map.spawn[0] = -400.0f;
    map.init_script = g_mod.init_script;
    map.camera_distance = 700.0f;
    map.camera_height = 70.0f;
    map.camera_look_height = 60.0f;
    map.camera_x_range[0] = -425.0f;
    map.camera_x_range[1] = 1225.0f;
    if (g_mod.content.register_map(g_mod.content.context, &map, &g_mod.map_slot) != OPENSPM_OK) {
        mod_log(OPENSPM_LOG_WARN, "Sample Level: the map was refused (see above); the level is off.");
        g_mod.failed = 1;
        return;
    }
    snprintf(message, sizeof message,
             "Sample Level: %s is ready (press %s to go there); Brick Goomba = template %u, Frost Koopa = template %u.",
             LEVEL_MAP, LEVEL_HOTKEY, (unsigned)g_mod.brick_goomba, (unsigned)g_mod.frost_koopa);
    mod_log(OPENSPM_LOG_INFO, message);
}

/* 1 while the player is on `name`. */
static int on_map(const char *name) {
    uint32_t gp = 0u, i;
    if (!read32(LEVEL_GP_PTR, &gp) || gp < 0x80000000u || gp >= 0x81800000u) return 0;
    for (i = 0; i <= strlen(name); i += 4u) {
        uint32_t w = 0u, k;
        if (!read32(gp + LEVEL_GP_MAP_NAME + i, &w)) return 0;
        for (k = 0; k < 4u && i + k <= strlen(name); ++k)
            if ((char)(w >> (24u - 8u * k)) != name[i + k]) return 0;
    }
    return 1;
}

static void tick(void) {
    uint32_t seq = 0u, level = 1u;
    int key;
    if (!g_mod.ready || g_mod.failed) return;
    if (!g_mod.built) build();
    if (g_mod.failed || !g_mod.have_keys) return;
    key = g_mod.input.key_down(g_mod.input.context, LEVEL_HOTKEY) == 1;
    if (key && !g_mod.key_was_down && read32(SPM_R8PE01_SEQ_WORK, &seq) && seq == SPM_R8PE01_SEQ_GAME &&
        read32(SPM_R8PE01_SYSTEM_LEVEL, &level) && level == 0u) {
        const int here = on_map(LEVEL_MAP);
        const int rc = here ? g_mod.content.go_to_map(g_mod.content.context, FLIPSIDE_MAP, FLIPSIDE_PIPE)
                            : g_mod.content.go_to_map(g_mod.content.context, LEVEL_MAP, LEVEL_ENTRANCE);
        mod_log(rc == OPENSPM_OK ? OPENSPM_LOG_INFO : OPENSPM_LOG_WARN,
                here ? "Sample Level: back to Flipside." : "Sample Level: off to the meadow.");
    }
    g_mod.key_was_down = key;
}

static void startup(void) {
    if (!g_mod.host || g_mod.host->header.struct_size < MEMBER_END(openspm_host_api, query_service) ||
        !g_mod.host->query_service)
        return;
    if (!query(OPENSPM_CONTENT_SERVICE_ID, OPENSPM_CONTENT_SERVICE_VERSION, &g_mod.content, sizeof g_mod.content) ||
        g_mod.content.header.struct_size < MEMBER_END(openspm_content_service, register_enemy) ||
        !g_mod.content.register_map || !g_mod.content.go_to_map || !g_mod.content.register_message ||
        !g_mod.content.register_enemy) {
        mod_log(OPENSPM_LOG_WARN, "Sample Level: this OpenSPM has no custom maps / enemies yet; off.");
        return;
    }
    if (!query(OPENSPM_GUEST_MEMORY_SERVICE_ID, OPENSPM_GUEST_MEMORY_SERVICE_VERSION, &g_mod.mem, sizeof g_mod.mem) ||
        !g_mod.mem.read32 || !g_mod.mem.write32 ||
        !query(OPENSPM_GUEST_CALL_SERVICE_ID, OPENSPM_GUEST_CALL_SERVICE_VERSION, &g_mod.calls, sizeof g_mod.calls) ||
        !g_mod.calls.scratch) {
        mod_log(OPENSPM_LOG_WARN, "Sample Level: needs guest memory and guest scratch memory; off.");
        return;
    }
    g_mod.have_keys = query(OPENSPM_INPUT_SERVICE_ID, OPENSPM_INPUT_SERVICE_VERSION, &g_mod.input, sizeof g_mod.input) &&
                      g_mod.input.key_down;
    g_mod.ready = 1;
}

static void OPENSPM_CALL level_event(void *context, openspm_mod_event event_id, const void *event_data) {
    (void)context;
    (void)event_data;
    if (event_id == OPENSPM_MOD_EVENT_STARTUP) startup();
    else if (event_id == OPENSPM_MOD_EVENT_TICK_BEGIN) tick();
}

OPENSPM_EXPORT int OPENSPM_CALL openspm_mod_query(uint32_t requested_abi, const openspm_host_api *host,
                                                  openspm_mod_api *out_api) {
    if (requested_abi != OPENSPM_ABI_VERSION || !host || !out_api || host->header.abi_version != OPENSPM_ABI_VERSION ||
        out_api->header.struct_size < sizeof(*out_api))
        return OPENSPM_ERROR_ABI_MISMATCH;
    memset(&g_mod, 0, sizeof g_mod);
    g_string_count = 0u;
    g_mod.host = host;
    memset(out_api, 0, sizeof(*out_api));
    out_api->header.struct_size = sizeof(*out_api);
    out_api->header.abi_version = OPENSPM_ABI_VERSION;
    out_api->mod_id = "openspm.sample.level";
    out_api->mod_version = "1.0";
    out_api->on_event = level_event;
    return OPENSPM_OK;
}
