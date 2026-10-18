/* Host test for real character ids in the content roster
   (include/openspm/content_roster.h, docs/CHARACTER-IDS.md): with the boot
   patches and table moves applied (include/openspm/code_patches.h), added
   characters get ids 4, 8, ... and their own rows (model, no second model,
   preload, voice, size and speed; the rest copied from the base character),
   the menu pick hands the game the id through the entry's item (216 + id)
   and lets the game's own confirm through, the character is in play exactly
   while MarioWork holds his id, and the old slot mode stays available. */
#include "openspm/code_patches.h"
#include "openspm/content_roster.h"
#include "openspm/guest_call.h"

#include <assert.h>
#include <stdlib.h>

#define MEM1 0x01800000u
#define MEM2 0x04000000u
static uint8_t *g_mem1, *g_mem2;
static openspm_guest_call_queue g_calls;
static uint32_t g_scratch_used, g_held, g_prev;
static int g_set_calls;
static char g_all[8192];

static uint8_t *P(uint32_t a) {
    if (a >= 0x80000000u && a < 0x80000000u + MEM1) return g_mem1 + (a - 0x80000000u);
    if (a >= 0x90000000u && a < 0x90000000u + MEM2) return g_mem2 + (a - 0x90000000u);
    return NULL;
}
static void w32(uint32_t a, uint32_t v) { uint8_t *p = P(a); p[0] = v >> 24; p[1] = v >> 16; p[2] = v >> 8; p[3] = v; }
static uint32_t r32(uint32_t a) { const uint8_t *p = P(a); return (uint32_t)p[0] << 24 | p[1] << 16 | p[2] << 8 | p[3]; }
static void wstr(uint32_t a, const char *s) { memcpy(P(a), s, strlen(s) + 1u); }
static const char *str(uint32_t a) { return a ? (const char *)P(a) : "(null)"; }
static float rf(uint32_t a) { uint32_t b = r32(a); float f; memcpy(&f, &b, 4); return f; }

static int f_read32(void *c, uint32_t a, uint32_t *v) { (void)c; if (!P(a)) return OPENSPM_ERROR_INVALID_ARGUMENT; *v = r32(a); return OPENSPM_OK; }
static int f_write32(void *c, uint32_t a, uint32_t v) { (void)c; if (!P(a)) return OPENSPM_ERROR_INVALID_ARGUMENT; w32(a, v); return OPENSPM_OK; }
static int f_call(void *c, uint32_t t, uint32_t n, const uint32_t *args) { (void)c; return openspm_guest_call_push(&g_calls, t, n, args); }
static int f_scratch(void *c, uint32_t s, uint32_t *o) { (void)c; return openspm_guest_scratch_take(&g_scratch_used, s, o); }
static int f_get(void *c, uint32_t *h, uint32_t *p) { (void)c; *h = g_held; *p = g_held & ~g_prev; return OPENSPM_OK; }
static int f_set(void *c, uint32_t h) { (void)c; (void)h; ++g_set_calls; return OPENSPM_OK; }
static void f_log(void *c, int warn, const char *t) {
    const size_t used = strlen(g_all);
    (void)c;
    (void)warn;
    if (used + strlen(t) + 2u < sizeof g_all) snprintf(g_all + used, sizeof g_all - used, "%s\n", t);
}
static const openspm_roster_io k_io = {NULL, f_read32, f_write32, f_call, f_scratch, f_get, f_set, f_log};

#define STRINGS 0x80700000u
#define MSG_WORK 0x80710000u
#define CHARTAB 0x80720000u
#define PAUSEWIN 0x80730000u
#define PW_ENTRIES 0x80731000u
#define LIST 0x80740000u

static openspm_roster g_r;
static void tick(void) { openspm_roster_tick(&g_r, &k_io); g_prev = g_held; }
static void set_char(uint32_t c) { g_mem1[SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_CHARACTER - 0x80000000u] = (uint8_t)c; }

/* The fake game: the code words the patches expect, the per-character
   tables (rows of made-up strings and numbers), then the boot patches and
   moves as the backend applies them. */
static void game(int apply) {
    static const char *who[4] = {"mario", "peach", "kuppa", "luigi"};
    static const char *voice_who[4] = {"MARIO", "PEACH", "KOOPA", "LUIGI"};
    static const char *voice_kind[3] = {"DAMEGE", "ACRO", "ATTACK"};
    static const uint32_t voice[3] = {SPM_R8PE01_VOICE_DAMAGE_TABLE, SPM_R8PE01_VOICE_ACRO_TABLE,
                                      SPM_R8PE01_VOICE_ATTACK_TABLE};
    static const uint32_t vcount[3] = {3u, 4u, 4u};
    uint32_t i, v, s, at = STRINGS;
    char name[48];
    memset(g_mem1, 0, MEM1);
    memset(g_mem2, 0, MEM2);
    memset(&g_calls, 0, sizeof g_calls);
    g_scratch_used = 0u;
    for (i = 0; i < OPENSPM_CODE_PATCH_COUNT; ++i)
        if (k_openspm_code_patches[i].boot) w32(k_openspm_code_patches[i].address, k_openspm_code_patches[i].expected);
    for (i = 0; i < 4u; ++i) {
        snprintf(name, sizeof name, "p_wii_%s", who[i]);
        wstr(at, name);
        w32(SPM_R8PE01_CHAR_MODEL_TABLE + 8u * i, at);
        w32(SPM_R8PE01_CHAR_PRELOAD_TABLE + 8u * i, at);
        at += 32u;
        snprintf(name, sizeof name, "p_dot_%s", who[i]);
        wstr(at, name);
        w32(OPENSPM_ROSTER_DOT_MODEL_TABLE + 8u * i, at);
        at += 32u;
        snprintf(name, sizeof name, "P_dokan_%s", who[i]);
        wstr(at, name);
        w32(OPENSPM_ROSTER_PIPE_MODEL_TABLE + 4u * i, at);
        at += 32u;
        w32(OPENSPM_ROSTER_LANDING_TABLE + 4u * i, 0x1000u + i);
        for (s = 0; s < 5u; ++s) {
            const float f = (float)(10u * i + s + 1u);
            uint32_t b;
            memcpy(&b, &f, 4);
            w32(SPM_R8PE01_CHAR_PROPERTIES + SPM_R8PE01_CHAR_PROPERTIES_SIZE * i + 4u * s, b);
        }
        for (v = 0; v < 3u; ++v)
            for (s = 0; s < vcount[v]; ++s) {
                snprintf(name, sizeof name, "SFX_P_V_%s_%s%u", voice_who[i], voice_kind[v], (unsigned)s + 1u);
                wstr(at, name);
                w32(voice[v] + 4u * (vcount[v] * i + s), at);
                at += 32u;
            }
    }
    wstr(at, "ICON"); /* item 232 (the placeholder): the revision check */
    w32(SPM_R8PE01_ITEM_DATA + SPM_R8PE01_ITEM_PLACEHOLDER * SPM_R8PE01_ITEM_DATA_SIZE + SPM_R8PE01_ITEM_NAME, at);
    at += 32u;
    wstr(at, "p_wii_mario_r");
    w32(SPM_R8PE01_CHAR_MODEL_TABLE_EXTRA, at);
    w32(SPM_R8PE01_EVT_ENTRY, 0x9421FFF0u);
    w32(SPM_R8PE01_EVT_MARIO_SET_CHARACTER, 0x9421FFF0u);
    w32(OPENSPM_ROSTER_PAUSEWIN_SET_MESSAGE, 0x9421FF60u);
    w32(OPENSPM_ROSTER_MSG_WORK_PTR, MSG_WORK);
    w32(MSG_WORK + 12u, 0x80711000u);
    w32(SPM_R8PE01_CHARTAB_WP, CHARTAB);
    w32(SPM_R8PE01_PAUSEWIN_WP, PAUSEWIN);
    w32(PAUSEWIN + SPM_R8PE01_PAUSEWIN_ENTRIES, PW_ENTRIES);
    w32(CHARTAB + SPM_R8PE01_CHARTAB_WINDOW_ID, 3u);
    w32(CHARTAB + OPENSPM_ROSTER_CHARTAB_MSG_WINDOW, 5u);
    w32(SPM_R8PE01_SEQ_WORK, 2u);
    if (apply) assert(openspm_code_patches_apply(g_mem1, MEM1, g_mem2, MEM2) > 0);
}

static void open_menu(void) {
    memset(P(LIST), 0, 768u);
    w32(LIST, 0x01000000u); /* Mario */
    w32(LIST + 4u, 216u);
    w32(CHARTAB + SPM_R8PE01_CHARTAB_LIST, LIST);
    w32(CHARTAB + SPM_R8PE01_CHARTAB_COUNT, 1u);
    w32(CHARTAB + SPM_R8PE01_CHARTAB_CURSOR, 0u);
    w32(PW_ENTRIES + 3u * 120u + OPENSPM_ROSTER_PAUSEWIN_INIT_FN, OPENSPM_ROSTER_CHARTAB_INIT);
    w32(PW_ENTRIES + 3u * 120u, 0x00010000u);
    w32(PW_ENTRIES + 3u * 120u + 60u, 10u);
    w32(SPM_R8PE01_SYSTEM_LEVEL, 1u);
}
static void close_menu(void) {
    w32(PW_ENTRIES + 3u * 120u, 0u);
    w32(SPM_R8PE01_SYSTEM_LEVEL, 0u);
}

static void add(const char *key, const char *nm, const char *model, uint32_t base, const char *voice,
                const char *props, uint32_t want_id) {
    openspm_character_def d;
    char why[160];
    uint32_t id = 0u;
    memset(&d, 0, sizeof d);
    d.header.struct_size = sizeof d;
    d.key = key; d.name = nm; d.description = "x"; d.model = model; d.base_character = base;
    d.voice = voice;
    d.properties = props;
    assert(openspm_roster_register(&g_r, &d, &id, why, sizeof why) == OPENSPM_OK && id == want_id);
}

int main(void) {
    uint32_t t_model, t_dot, t_props, t_voice;
    g_mem1 = calloc(1, MEM1);
    g_mem2 = calloc(1, MEM2);
    assert(g_mem1 && g_mem2);
    game(1);
    openspm_roster_init(&g_r);
    add("a:francis", "Francis", "e_kamere", 0u, "damage=SFX_BS_CMK_DOWN1", "width=40,walk=0.5", 4u);
    add("a:ghost", "Boo", "e_teresa", 3u, NULL, NULL, 5u);
    set_char(0u);
    close_menu();
    tick();
    if (!(g_r.built && g_r.real_ids)) fprintf(stderr, "built %d real %d log:\n%s", g_r.built, g_r.real_ids, g_all);
    assert(g_r.built && g_r.real_ids && strstr(g_all, "real character ids: Francis = 4, Boo = 8") != NULL);
    assert(g_r.chars[0].game_id == 4u && g_r.chars[1].game_id == 8u);
    t_model = openspm_code_relocation_of(SPM_R8PE01_CHAR_MODEL_TABLE)->to;
    t_dot = openspm_code_relocation_of(OPENSPM_ROSTER_DOT_MODEL_TABLE)->to;
    t_props = openspm_code_relocation_of(SPM_R8PE01_CHAR_PROPERTIES)->to;
    t_voice = openspm_code_relocation_of(SPM_R8PE01_VOICE_DAMAGE_TABLE)->to;
    assert(g_r.t_model == t_model && g_r.t_dot == t_dot);

    /* His rows: his model (also with a Mega Star), his base's second model, his
       preload; Mario's 8-bit model; his hurt voice; his width and walk on
       Mario's row. The Luigi-based one copies Luigi's rows. Unused ids keep
       Mario's (the boot fill). */
    assert(strcmp(str(r32(t_model + 8u * 4u)), "e_kamere") == 0 && strcmp(str(r32(t_model + 8u * 4u + 4u)), "e_kamere") == 0);
    assert(strcmp(str(r32(g_r.t_model2 + 8u * 4u)), "p_wii_mario_r") == 0 &&
           r32(g_r.t_model2 + 8u * 8u) == r32(g_r.t_model2 + 8u * 3u));
    assert(strcmp(str(r32(g_r.t_preload + 8u * 4u)), "e_kamere") == 0);
    assert(strcmp(str(r32(t_dot + 8u * 4u)), "p_dot_mario") == 0 && strcmp(str(r32(t_dot + 8u * 8u)), "p_dot_luigi") == 0);
    assert(strcmp(str(r32(g_r.t_pipe + 4u * 8u)), "P_dokan_luigi") == 0 && r32(g_r.t_landing + 4u * 8u) == 0x1003u);
    assert(strcmp(str(r32(t_voice + 4u * 3u * 4u)), "SFX_BS_CMK_DOWN1") == 0);
    assert(strcmp(str(r32(g_r.t_voice[1] + 4u * 4u * 4u)), "SFX_P_V_MARIO_ACRO1") == 0); /* acro: Mario's */
    assert(rf(t_props + SPM_R8PE01_CHAR_PROPERTIES_SIZE * 4u) == 40.0f &&
           rf(t_props + SPM_R8PE01_CHAR_PROPERTIES_SIZE * 4u + 8u) == 0.5f &&
           rf(t_props + SPM_R8PE01_CHAR_PROPERTIES_SIZE * 4u + 4u) == 2.0f); /* height: Mario's */
    assert(strcmp(str(r32(t_model + 8u * 12u)), "p_wii_mario") == 0);
    /* The game's own tables were never written. */
    assert(strcmp(str(r32(SPM_R8PE01_CHAR_MODEL_TABLE)), "p_wii_mario") == 0 &&
           strcmp(str(r32(SPM_R8PE01_CHAR_MODEL_TABLE + 32u)), "p_wii_mario_r") == 0); /* no write past it */

    /* Menu: Francis listed after Mario; picking him lets the press through,
       keeps his entry (his own item, so the menu still draws him) and, once
       the menu has copied that item (pause work +44), gives the game his id
       there (216 + 4). */
    open_menu();
    tick();
    assert(r32(CHARTAB + SPM_R8PE01_CHARTAB_COUNT) == 2u);
    w32(CHARTAB + SPM_R8PE01_CHARTAB_CURSOR, 1u);
    tick();
    while (openspm_guest_call_pop(&g_calls, &(openspm_guest_call_entry){0})) {}
    g_held = OPENSPM_WII_BTN_2;
    g_set_calls = 0;
    tick();
    {
        const uint32_t own_item = r32(LIST + 12u + 4u);
        assert(own_item != 216u + 4u && g_set_calls == 0);
        g_held = 0u;
        tick();
        assert(r32(LIST + 12u + 4u) == own_item);
        w32(PAUSEWIN + SPM_R8PE01_PAUSE_PICK_ITEM, own_item); /* the game's copy (0x80195640) */
        tick();
        assert(r32(PAUSEWIN + SPM_R8PE01_PAUSE_PICK_ITEM) == 216u + 4u && r32(LIST + 12u + 4u) == own_item);
        tick();
        assert(r32(PAUSEWIN + SPM_R8PE01_PAUSE_PICK_ITEM) == 216u + 4u);
    }
    /* The game switches (move 71, _marioChangeCharacter(4)). */
    close_menu();
    tick();
    assert(!openspm_roster_character_active(&g_r, 4u));
    set_char(4u);
    tick();
    assert(openspm_roster_character_active(&g_r, 4u) && !openspm_roster_character_active(&g_r, 5u));
    assert(strstr(g_all, "now playing Francis (a:francis, id 4, model e_kamere)") != NULL);
    /* A story change to Peach: he steps back; nothing to restore. */
    set_char(1u);
    tick();
    assert(!openspm_roster_character_active(&g_r, 4u));
    set_char(8u);
    tick();
    assert(openspm_roster_character_active(&g_r, 5u));

    /* An older module (no moves) or OPENSPM_CHARACTER_SLOTS=1: slot mode. */
    game(0);
    openspm_roster_init(&g_r);
    add("a:francis", "Francis", "e_kamere", 0u, NULL, NULL, 4u);
    memset(g_all, 0, sizeof g_all);
    set_char(0u);
    tick();
    assert(g_r.built && !g_r.real_ids && strstr(g_all, "no real character ids") != NULL);
    game(1);
    openspm_roster_init(&g_r);
    g_r.slots_only = 1;
    add("a:francis", "Francis", "e_kamere", 0u, NULL, NULL, 4u);
    memset(g_all, 0, sizeof g_all);
    tick();
    assert(g_r.built && !g_r.real_ids && strstr(g_all, "OPENSPM_CHARACTER_SLOTS=1") != NULL);
    free(g_mem1);
    free(g_mem2);
    puts("PASS: content ids");
    return 0;
}
