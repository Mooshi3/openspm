/* Host test for the OpenSPM content roster (include/openspm/content_roster.h):
   registration rules, the revision check, the message table (looked up with
   the game's msgSearch algorithm), menu entries added after the game's own,
   the name under the cursor, picking an added character (button 2 taken,
   model tables swapped, cursor moved, 2 pressed for the base character, model
   reload when the base was already playing), picking a game character, a
   character change by the game, leaving the game, and the list limit without
   OpenSPM's build patch. */
#include "openspm/content_roster.h"
#include "openspm/guest_call.h"

#include <assert.h>
#include <stdlib.h>

#define MEM1_SIZE 0x01800000u
static uint8_t *g_mem;
static openspm_guest_call_queue g_calls;
static uint32_t g_scratch_used;
static uint32_t g_held, g_prev, g_set_held;
static int g_set_calls;
static char g_log[512];

static uint8_t *P(uint32_t a) {
    if (a >= 0x80000000u && a < 0x80000000u + MEM1_SIZE) return g_mem + (a - 0x80000000u);
    if (a >= OPENSPM_GUEST_SCRATCH_BEGIN && a < OPENSPM_GUEST_SCRATCH_BEGIN + OPENSPM_GUEST_SCRATCH_SIZE)
        return g_mem + 0x01000000u + (a - OPENSPM_GUEST_SCRATCH_BEGIN); /* fake: scratch mapped high in the buffer */
    if (a >= 0x93E00000u && a < 0x93E20000u) return g_mem + MEM1_SIZE + (a - 0x93E00000u); /* relocated items */
    return NULL;
}
static void w32(uint32_t a, uint32_t v) { uint8_t *p = P(a); p[0] = v >> 24; p[1] = v >> 16; p[2] = v >> 8; p[3] = v; }
static uint32_t r32(uint32_t a) { const uint8_t *p = P(a); return (uint32_t)p[0] << 24 | p[1] << 16 | p[2] << 8 | p[3]; }
static void wstr(uint32_t a, const char *s) { memcpy(P(a), s, strlen(s) + 1u); }
static const char *str(uint32_t a) { return (const char *)P(a); }
static float prop_rd(uint32_t a) { const uint32_t b = r32(a); float v; memcpy(&v, &b, sizeof v); return v; }
static float prop(uint32_t ch, uint32_t f) {
    return prop_rd(SPM_R8PE01_CHAR_PROPERTIES + SPM_R8PE01_CHAR_PROPERTIES_SIZE * ch + 4u * f);
}

static int f_read32(void *c, uint32_t a, uint32_t *v) { (void)c; if (!P(a)) return OPENSPM_ERROR_INVALID_ARGUMENT; *v = r32(a); return OPENSPM_OK; }
static int f_write32(void *c, uint32_t a, uint32_t v) { (void)c; if (!P(a)) return OPENSPM_ERROR_INVALID_ARGUMENT; w32(a, v); return OPENSPM_OK; }
static int f_call(void *c, uint32_t t, uint32_t n, const uint32_t *args) { (void)c; return openspm_guest_call_push(&g_calls, t, n, args); }
static int f_scratch(void *c, uint32_t s, uint32_t *o) { (void)c; return openspm_guest_scratch_take(&g_scratch_used, s, o); }
static int f_get(void *c, uint32_t *h, uint32_t *p) { (void)c; *h = g_held; *p = g_held & ~g_prev; return OPENSPM_OK; }
static int f_set(void *c, uint32_t h) { (void)c; g_set_held = h; ++g_set_calls; return OPENSPM_OK; }
static char g_all[8192]; /* every line, for checks across several */
static void f_log(void *c, int warn, const char *t) {
    const size_t used = strlen(g_all);
    (void)c;
    (void)warn;
    snprintf(g_log, sizeof g_log, "%s", t);
    if (used + strlen(t) + 2u < sizeof g_all) snprintf(g_all + used, sizeof g_all - used, "%s\n", t);
}

static const openspm_roster_io k_io = {NULL, f_read32, f_write32, f_call, f_scratch, f_get, f_set, f_log};

/* Fake game layout. */
#define STRINGS 0x80700000u
#define MSG_WORK 0x80710000u
#define CHARTAB 0x80720000u
#define PAUSEWIN 0x80730000u
#define PW_ENTRIES 0x80731000u
#define LIST 0x80740000u
#define ITEM232 (SPM_R8PE01_ITEM_DATA + 232u * SPM_R8PE01_ITEM_DATA_SIZE)

static void setup(int patched) {
    static const char *models[4] = {"p_wii_mario", "p_wii_peach", "p_wii_kuppa", "p_wii_luigi"};
    uint32_t i;
    memset(g_mem, 0, MEM1_SIZE + 0x00200000u);
    memset(&g_calls, 0, sizeof g_calls);
    g_scratch_used = 0u;
    w32(SPM_R8PE01_EVT_ENTRY, 0x9421FFF0u);
    w32(SPM_R8PE01_EVT_MARIO_SET_CHARACTER, 0x9421FFF0u);
    w32(OPENSPM_ROSTER_PAUSEWIN_SET_MESSAGE, 0x9421FF60u);
    for (i = 0; i < 4u; ++i) {
        wstr(STRINGS + 32u * i, models[i]);
        w32(SPM_R8PE01_CHAR_MODEL_TABLE + 8u * i, STRINGS + 32u * i);
        w32(SPM_R8PE01_CHAR_PRELOAD_TABLE + 8u * i, STRINGS + 32u * i);
    }
    wstr(STRINGS + 0x100u, "ICON");
    wstr(STRINGS + 0x110u, "msg_unknown_item");
    w32(ITEM232 + SPM_R8PE01_ITEM_NAME, STRINGS + 0x100u);
    w32(ITEM232 + SPM_R8PE01_ITEM_NAME_MSG, STRINGS + 0x110u);
    w32(ITEM232 + SPM_R8PE01_ITEM_DESC_MSG, STRINGS + 0x110u);
    w32(k_openspm_code_patches[0].address, patched ? k_openspm_code_patches[0].replacement : k_openspm_code_patches[0].expected);
    w32(OPENSPM_ROSTER_MSG_WORK_PTR, MSG_WORK);
    w32(MSG_WORK + 16u * 0u + 12u, 0x80711000u); /* global messages */
    w32(MSG_WORK + 16u * 1u + 12u, 0x80712000u); /* this map's messages */
    w32(SPM_R8PE01_CHARTAB_WP, CHARTAB);
    w32(SPM_R8PE01_PAUSEWIN_WP, PAUSEWIN);
    w32(PAUSEWIN + SPM_R8PE01_PAUSEWIN_ENTRIES, PW_ENTRIES);
    w32(CHARTAB + SPM_R8PE01_CHARTAB_WINDOW_ID, 3u);
    w32(CHARTAB + OPENSPM_ROSTER_CHARTAB_MSG_WINDOW, 5u);
    w32(SPM_R8PE01_SEQ_WORK, 2u);
    for (i = 0; i < 4u; ++i) { /* characterProperties (made-up sizes and speeds) */
        const float row[5] = {10.0f + (float)i, 30.0f + (float)i, 1.0f, 2.0f, 1.0f};
        uint32_t f, b;
        for (f = 0; f < 5u; ++f) {
            memcpy(&b, &row[f], sizeof b);
            w32(SPM_R8PE01_CHAR_PROPERTIES + SPM_R8PE01_CHAR_PROPERTIES_SIZE * i + 4u * f, b);
        }
    }
    {
        /* The player voice tables: rows of "SFX_P_V_<who>_<kind><n>". */
        static const char *const who[4] = {"MARIO", "PEACH", "KOOPA", "LUIGI"};
        static const char *const kind[3] = {"DAMEGE", "ACRO", "ATTACK"};
        static const uint32_t table[3] = {SPM_R8PE01_VOICE_DAMAGE_TABLE, SPM_R8PE01_VOICE_ACRO_TABLE,
                                          SPM_R8PE01_VOICE_ATTACK_TABLE};
        static const uint32_t count[3] = {3u, 4u, 4u};
        uint32_t v, c, s, at = STRINGS + 0x1000u;
        char name[40];
        for (v = 0; v < 3u; ++v)
            for (c = 0; c < 4u; ++c)
                for (s = 0; s < count[v]; ++s) {
                    snprintf(name, sizeof name, "SFX_P_V_%s_%s%u", who[c], kind[v], (unsigned)s + 1u);
                    wstr(at, name);
                    w32(table[v] + 4u * (c * count[v] + s), at);
                    at += 32u;
                }
    }
}

/* The game's msgSearch over the installed tables (tables 1..8). */
static const char *msg_search(const char *key) {
    uint32_t i;
    for (i = 1; i < 9u; ++i) {
        const uint32_t slot = MSG_WORK + 16u * i, handle = r32(slot + 12u);
        uint32_t lo = 0u, hi, base, entries;
        if (handle < 0x93F00000u) continue; /* the fake game's own tables are empty */
        entries = r32(handle);
        base = r32(slot + 4u);
        hi = r32(slot + 8u);
        while (lo < hi) {
            const uint32_t mid = (lo + hi) / 2u;
            const int c = strcmp(key, str(base + r32(entries + 8u * mid)));
            if (c == 0) return str(base + r32(entries + 8u * mid + 4u));
            if (c < 0) hi = mid; else lo = mid + 1u;
        }
    }
    return "anna_no_message";
}

static void open_menu(uint32_t chars, uint32_t current) {
    uint32_t i, n = 0u;
    memset(P(LIST), 0, 768u);
    for (i = 0; i < 4u; ++i)
        if (chars & (1u << i)) {
            w32(LIST + 12u * n, 0x01000000u);
            w32(LIST + 12u * n + 4u, 216u + i);
            if (i == current) w32(CHARTAB + SPM_R8PE01_CHARTAB_CURSOR, n);
            ++n;
        }
    w32(CHARTAB + SPM_R8PE01_CHARTAB_LIST, LIST);
    w32(CHARTAB + SPM_R8PE01_CHARTAB_COUNT, n);
    w32(CHARTAB + SPM_R8PE01_CHARTAB_LIVE_LISTS, 0u); /* 3 only while the menu closes */
    w32(PW_ENTRIES + 3u * 120u + OPENSPM_ROSTER_PAUSEWIN_INIT_FN, OPENSPM_ROSTER_CHARTAB_INIT);
    w32(PW_ENTRIES + 3u * 120u, 0x00010000u); /* in use */
    w32(PW_ENTRIES + 3u * 120u + 60u, 10u);  /* visible */
    w32(SPM_R8PE01_SYSTEM_LEVEL, 1u);
}
static void close_menu(void) {
    /* The game's close: the windows are deleted (flags 0); the list pointer
       stays behind (freed) and the next open may get the same address. */
    w32(PW_ENTRIES + 3u * 120u, 0u);
    w32(CHARTAB + SPM_R8PE01_CHARTAB_LIVE_LISTS, 0u);
    w32(SPM_R8PE01_SYSTEM_LEVEL, 0u);
}
static void set_char(uint32_t c) { g_mem[SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_CHARACTER - 0x80000000u] = (uint8_t)c; }

static void wf(uint32_t a, float f) { uint32_t b; memcpy(&b, &f, 4); w32(a, b); }
static float rf(uint32_t a) { uint32_t b = r32(a); float f; memcpy(&f, &b, 4); return f; }

static openspm_roster g_r;
static void tick(void) { openspm_roster_tick(&g_r, &k_io); g_prev = g_held; }
static int pop(openspm_guest_call_entry *e) { return openspm_guest_call_pop(&g_calls, e); }

static openspm_character_def def(const char *key, const char *name, const char *model, uint32_t base) {
    openspm_character_def d;
    memset(&d, 0, sizeof d);
    d.header.struct_size = sizeof d;
    d.key = key; d.name = name; d.description = "Kicks <shells>."; d.model = model; d.base_character = base;
    return d;
}

int main(void) {
    char why[160];
    uint32_t id = 0u, id2 = 0u, i;
    openspm_guest_call_entry call;
    openspm_character_def d;
    g_mem = calloc(1, MEM1_SIZE + 0x00200000u);
    assert(g_mem);
    setup(1);
    openspm_roster_init(&g_r);

    /* Registration rules. */
    d = def("striker", "Koopa Striker", "e_k_shooter", 0u);
    assert(openspm_roster_register(&g_r, &d, &id, why, sizeof why) == OPENSPM_ERROR_INVALID_ARGUMENT); /* no ':' */
    d = def("striker:koopa", "Koopa Striker", "e_k_shooter", 4u);
    assert(openspm_roster_register(&g_r, &d, &id, why, sizeof why) == OPENSPM_ERROR_INVALID_ARGUMENT); /* base */
    d = def("striker:koopa", "Koopa Striker", "E_K", 0u);
    assert(openspm_roster_register(&g_r, &d, &id, why, sizeof why) == OPENSPM_ERROR_INVALID_ARGUMENT); /* model */
    d = def("striker:koopa", "Koopa Striker", "e_k_shooter", 0u);
    assert(openspm_roster_register(&g_r, &d, &id, why, sizeof why) == OPENSPM_OK && id == 4u);
    assert(openspm_roster_register(&g_r, &d, &id, why, sizeof why) == OPENSPM_ERROR_RUNTIME); /* taken */
    d = def("other:ghost", "Boo Buddy", "e_teresa", 3u);
    assert(openspm_roster_register(&g_r, &d, &id2, why, sizeof why) == OPENSPM_OK && id2 == 5u);

    /* Title screen: nothing touched. */
    w32(SPM_R8PE01_SEQ_WORK, 1u);
    tick();
    assert(!g_r.closed && g_scratch_used == 0u);

    /* In game: built, messages found by the game's own lookup, registration closed. */
    w32(SPM_R8PE01_SEQ_WORK, 2u);
    set_char(0u);
    close_menu();
    tick();
    assert(g_r.built && g_r.capacity == 64u && strstr(g_log, "2 added") != NULL);
    assert(strcmp(msg_search("openspm_chr_00_n"), "Koopa Striker") == 0);
    assert(strcmp(msg_search("openspm_chr_00_d"), "Kicks  shells .") == 0);
    assert(strcmp(msg_search("openspm_chr_01_n"), "Boo Buddy") == 0);
    assert(r32(MSG_WORK + 16u * 8u + 12u) == g_r.msg_handle); /* the highest free table */
    d = def("late:one", "Late", "e_kuribo", 0u);
    assert(openspm_roster_register(&g_r, &d, &id, why, sizeof why) == OPENSPM_ERROR_RUNTIME);
    /* A map load zeroes the tables: installed again. */
    w32(MSG_WORK + 16u * 8u + 12u, 0u);
    tick();
    assert(strcmp(msg_search("openspm_chr_00_n"), "Koopa Striker") == 0);

    /* Menu: Mario, Peach, Bowser unlocked (no Luigi): the Mario-based
       character is listed, the Luigi-based one waits. */
    open_menu(0x7u, 0u);
    tick();
    assert(r32(CHARTAB + SPM_R8PE01_CHARTAB_COUNT) == 4u);
    assert(r32(LIST + 36u + 4u) == 232u && (r32(LIST + 36u) >> 24) == 1u);
    close_menu();
    tick();
    open_menu(0xFu, 0u);
    wf(PW_ENTRIES + 3u * 120u + OPENSPM_ROSTER_PAUSEWIN_HEIGHT, 150.0f); /* the game's 4 rows */
    tick();
    assert(r32(CHARTAB + SPM_R8PE01_CHARTAB_COUNT) == 6u);
    assert(rf(PW_ENTRIES + 3u * 120u + OPENSPM_ROSTER_PAUSEWIN_HEIGHT) == 200.0f); /* grown to 6 rows */
    assert(r32(LIST + 48u + 4u) == 232u && r32(LIST + 60u + 4u) == 232u);

    /* Cursor on the added entries: the game's placeholder item shows their
       names (message box refreshed through pausewinSetMessage). */
    w32(CHARTAB + SPM_R8PE01_CHARTAB_CURSOR, 4u);
    tick();
    assert(strcmp(msg_search(str(r32(ITEM232 + SPM_R8PE01_ITEM_NAME_MSG))), "Koopa Striker") == 0);
    assert(pop(&call) && call.target == OPENSPM_ROSTER_PAUSEWIN_SET_MESSAGE && call.args[0] == PW_ENTRIES + 5u * 120u &&
           call.args[1] == 232u);
    w32(CHARTAB + SPM_R8PE01_CHARTAB_CURSOR, 5u);
    tick();
    assert(strcmp(msg_search(str(r32(ITEM232 + SPM_R8PE01_ITEM_NAME_MSG))), "Boo Buddy") == 0);
    assert(pop(&call) && !pop(&call));

    /* Pick the Koopa Striker (Mario is playing): 2 never reaches the game this
       frame, Mario's model entry points at e_k_shooter, the cursor moves to
       Mario and the game gets 2 on the next frame. */
    w32(CHARTAB + SPM_R8PE01_CHARTAB_CURSOR, 4u);
    tick();
    (void)pop(&call);
    g_held = OPENSPM_WII_BTN_2;
    g_set_calls = 0;
    tick();
    assert(g_set_calls == 1 && !(g_set_held & OPENSPM_WII_BTN_2));
    assert(strcmp(str(r32(SPM_R8PE01_CHAR_MODEL_TABLE)), "e_k_shooter") == 0);
    assert(strcmp(str(r32(SPM_R8PE01_CHAR_PRELOAD_TABLE)), "e_k_shooter") == 0);
    assert(r32(CHARTAB + SPM_R8PE01_CHARTAB_CURSOR) == 0u);
    tick(); /* still held: the game gets 2 now (injected) */
    assert(g_set_held & OPENSPM_WII_BTN_2);
    tick(); /* then masked while held */
    assert(!(g_set_held & OPENSPM_WII_BTN_2));
    g_held = 0u;
    /* The game closes the menu; Mario was playing, so the game did not
       switch: OpenSPM asks for the game's own flip (what 0x80146DB0 does for
       another character): MarioWork +0x1480 = Mario, move 71, which loads
       his model at the flip. */
    w32(SPM_R8PE01_MARIO_WORK + OPENSPM_ROSTER_MARIO_CHANGE_TARGET, 0xFFFFFFFFu);
    close_menu();
    tick();
    assert(pop(&call) && call.target == OPENSPM_ROSTER_MARIO_CHG_MOT && call.args[0] == 71u && call.args[1] == 1u);
    assert(!pop(&call) && r32(SPM_R8PE01_MARIO_WORK + OPENSPM_ROSTER_MARIO_CHANGE_TARGET) == 0u);
    assert(openspm_roster_character_active(&g_r, 4u) && !openspm_roster_character_active(&g_r, 5u));
    /* The flag the game sets with the move goes on only once move 71 runs
       (marioChgMot refuses every change while it is set). */
    tick();
    assert(!(r32(SPM_R8PE01_MARIO_WORK) & OPENSPM_ROSTER_MARIO_LOCKED));
    w32(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_MOTION_ID, 71u << 16);
    tick();
    assert(r32(SPM_R8PE01_MARIO_WORK) & OPENSPM_ROSTER_MARIO_LOCKED);
    assert(g_r.change_wait == 0u && !pop(&call));
    /* The move ends (the game clears both). */
    w32(SPM_R8PE01_MARIO_WORK, 0u);
    w32(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_MOTION_ID, 0u);
    assert(r32(ITEM232 + SPM_R8PE01_ITEM_NAME_MSG) == STRINGS + 0x110u); /* placeholder restored */
    tick();
    assert(!pop(&call) && openspm_roster_character_active(&g_r, 4u));

    /* Menu again: the cursor starts on the Koopa Striker. Picking Peach puts
       Mario's tables back before the game switches. */
    open_menu(0xFu, 0u);
    tick();
    assert(r32(CHARTAB + SPM_R8PE01_CHARTAB_CURSOR) == 4u);
    (void)pop(&call);
    w32(CHARTAB + SPM_R8PE01_CHARTAB_CURSOR, 1u);
    tick();
    g_held = OPENSPM_WII_BTN_2;
    tick();
    g_held = 0u;
    assert(strcmp(str(r32(SPM_R8PE01_CHAR_MODEL_TABLE)), "p_wii_mario") == 0 && !openspm_roster_character_active(&g_r, 4u));
    close_menu();
    set_char(1u);
    tick();
    assert(!pop(&call));

    /* From Peach, pick the Luigi-based character: the game itself switches to
       Luigi (with the swapped model), no reload script. */
    open_menu(0xFu, 1u);
    tick();
    w32(CHARTAB + SPM_R8PE01_CHARTAB_CURSOR, 5u);
    tick();
    while (pop(&call)) {}
    g_held = OPENSPM_WII_BTN_2;
    tick();
    assert(r32(CHARTAB + SPM_R8PE01_CHARTAB_CURSOR) == 3u);
    assert(strcmp(str(r32(SPM_R8PE01_CHAR_MODEL_TABLE + 24u)), "e_teresa") == 0);
    tick();
    g_held = 0u;
    close_menu();
    tick(); /* the switch motion is still running */
    assert(!openspm_roster_character_active(&g_r, 5u));
    set_char(3u);
    tick();
    assert(openspm_roster_character_active(&g_r, 5u) && !pop(&call));

    /* A story event switches to Mario: Luigi's tables go back. */
    set_char(0u);
    tick();
    assert(!openspm_roster_character_active(&g_r, 5u) && strcmp(str(r32(SPM_R8PE01_CHAR_MODEL_TABLE + 24u)), "p_wii_luigi") == 0);

    /* Picking the Mario entry while playing the Mario-based character reloads
       the real Mario. */
    open_menu(0xFu, 0u);
    tick();
    w32(CHARTAB + SPM_R8PE01_CHARTAB_CURSOR, 4u);
    tick();
    g_held = OPENSPM_WII_BTN_2;
    tick();
    tick();
    g_held = 0u;
    close_menu();
    tick();
    while (pop(&call)) {}
    assert(openspm_roster_character_active(&g_r, 4u));
    open_menu(0xFu, 0u);
    tick();
    w32(CHARTAB + SPM_R8PE01_CHARTAB_CURSOR, 0u);
    tick();
    while (pop(&call)) {}
    g_held = OPENSPM_WII_BTN_2;
    tick();
    g_held = 0u;
    /* Back to the real Mario (the game sees no change either): the flip with
       the game's model. If move 71 never starts, the plain reload after 10
       frames. */
    close_menu();
    tick();
    assert(pop(&call) && call.target == OPENSPM_ROSTER_MARIO_CHG_MOT && call.args[0] == 71u);
    for (i = 0; i < 9u; ++i) {
        tick();
        assert(!pop(&call));
    }
    tick();
    assert(pop(&call) && call.target == SPM_R8PE01_EVT_ENTRY && call.args[0] == g_r.reload_script[0]);
    assert(r32(call.args[0] + 4u) == SPM_R8PE01_EVT_MARIO_SET_CHARACTER && r32(call.args[0] + 8u) == 1u);
    assert(r32(call.args[0] + 16u) == SPM_R8PE01_EVT_MARIO_SET_CHARACTER && r32(call.args[0] + 20u) == 0u);
    assert(strstr(g_all, "transition did not start") != NULL);
    assert(strcmp(str(r32(SPM_R8PE01_CHAR_MODEL_TABLE)), "p_wii_mario") == 0 && !openspm_roster_character_active(&g_r, 4u));

    /* A map change keeps the character; leaving the game restores everything. */
    open_menu(0xFu, 0u);
    tick();
    w32(CHARTAB + SPM_R8PE01_CHARTAB_CURSOR, 4u);
    tick();
    g_held = OPENSPM_WII_BTN_2;
    tick();
    g_held = 0u;
    for (i = 0; i < 4u; ++i) tick();
    close_menu();
    for (i = 0; i < 4u; ++i) {
        tick();
        while (pop(&call)) {}
    }
    assert(openspm_roster_character_active(&g_r, 4u));
    w32(SPM_R8PE01_SEQ_WORK, 3u);
    for (i = 0; i < 10u; ++i) tick();
    w32(SPM_R8PE01_SEQ_WORK, 2u);
    tick();
    assert(openspm_roster_character_active(&g_r, 4u));
    assert(strcmp(str(r32(SPM_R8PE01_CHAR_MODEL_TABLE)), "p_wii_mario") != 0);
    w32(SPM_R8PE01_SEQ_WORK, 1u);
    tick();
    assert(!openspm_roster_character_active(&g_r, 4u));
    assert(strcmp(str(r32(SPM_R8PE01_CHAR_MODEL_TABLE)), "p_wii_mario") == 0);
    assert(r32(ITEM232 + SPM_R8PE01_ITEM_NAME_MSG) == STRINGS + 0x110u);

    /* Without the build patch the list holds 5: one added character. */
    setup(0);
    openspm_roster_init(&g_r);
    d = def("a:one", "One", "e_kuribo", 0u);
    assert(openspm_roster_register(&g_r, &d, &id, why, sizeof why) == OPENSPM_OK);
    d = def("a:two", "Two", "e_kuribo", 0u);
    assert(openspm_roster_register(&g_r, &d, &id, why, sizeof why) == OPENSPM_OK);
    close_menu();
    tick();
    assert(g_r.capacity == 5u && strstr(g_log, "rebuild") != NULL);
    open_menu(0xFu, 0u);
    tick();
    assert(r32(CHARTAB + SPM_R8PE01_CHARTAB_COUNT) == 5u);

    /* The rebuilt native module (item table relocated): each character gets
       his own item after the Pixls (none here: 538) with his name,
       description and the icon asked for; no placeholder patching. A
       no-flip character never passes A to the game in play. */
    setup(1);
    for (i = 0; i < 538u; ++i) {
        uint32_t k2;
        for (k2 = 0; k2 < SPM_R8PE01_ITEM_DATA_SIZE; k2 += 4u)
            w32(openspm_roster_item(i) + k2, r32(SPM_R8PE01_ITEM_DATA + i * SPM_R8PE01_ITEM_DATA_SIZE + k2));
    }
    w32(SPM_R8PE01_ITEM_DATA + 216u * SPM_R8PE01_ITEM_DATA_SIZE, STRINGS + 0x100u);
    w32(openspm_roster_item(216u), STRINGS + 0x100u);
    w32(openspm_roster_item(216u) + SPM_R8PE01_ITEM_ICON, 0x00050000u); /* Mario's icon 5 */
    w32(openspm_roster_item(319u) + SPM_R8PE01_ITEM_ICON, 0x01230000u); /* the card's icon */
    openspm_roster_init(&g_r);
    d = def("a:striker", "Koopa Striker", "e_k_shooter", 0u);
    d.flags = OPENSPM_CHARACTER_NO_FLIP;
    d.icon_item = 319u;
    d.anim_map = "J_1B=J_1_a";
    d.voice = "damage=SFX_E_HIT_KOURA1,acro=none";
    {
        /* properties: known names, numbers in range, each once */
        static const char *const bad[] = {"width=500", "speed=1", "walk=-1", "walk=1.2.3", "walk=1,walk=2",
                                          "walk", "walk=", "dash=1e2", "width=24;walk=1"};
        uint32_t b;
        for (b = 0; b < sizeof bad / sizeof bad[0]; ++b) {
            d.properties = bad[b];
            assert(openspm_roster_register(&g_r, &d, &id, why, sizeof why) == OPENSPM_ERROR_INVALID_ARGUMENT);
            assert(strstr(why, "properties") != NULL);
        }
    }
    d.properties = "width=30,walk=1.25";
    assert(openspm_roster_register(&g_r, &d, &id, why, sizeof why) == OPENSPM_OK);
    d = def("a:plain", "Plain", "e_kuribo", 0u);
    assert(openspm_roster_register(&g_r, &d, &id2, why, sizeof why) == OPENSPM_OK);
    close_menu();
    tick();
    assert(g_r.char_items && g_r.chars[0].item_id == 538u && g_r.chars[1].item_id == 539u);
    assert(strcmp(msg_search(str(r32(openspm_roster_item(538u) + SPM_R8PE01_ITEM_NAME_MSG))), "Koopa Striker") == 0);
    assert(r32(openspm_roster_item(538u) + SPM_R8PE01_ITEM_ICON) >> 16 == 0x123u);
    assert(r32(openspm_roster_item(539u) + SPM_R8PE01_ITEM_ICON) >> 16 == 5u); /* the base's */
    open_menu(0xFu, 0u);
    tick();
    assert(r32(LIST + 48u + 4u) == 538u && r32(LIST + 60u + 4u) == 539u);
    w32(CHARTAB + SPM_R8PE01_CHARTAB_CURSOR, 4u);
    tick();
    assert(pop(&call) && call.target == OPENSPM_ROSTER_PAUSEWIN_SET_MESSAGE && call.args[1] == 538u);
    assert(r32(ITEM232 + SPM_R8PE01_ITEM_NAME_MSG) == STRINGS + 0x110u); /* placeholder untouched */
    g_held = OPENSPM_WII_BTN_2;
    tick();
    tick();
    tick();
    g_held = 0u;
    close_menu();
    for (i = 0; i < 3u; ++i) {
        tick();
        while (pop(&call)) {}
    }
    assert(openspm_roster_character_active(&g_r, 4u));
    /* His voice: Mario's hurt and acrobatics voices are his (acro silent),
       attacks keep Mario's; the others' rows are untouched. */
    for (i = 0; i < 3u; ++i) assert(strcmp(str(r32(SPM_R8PE01_VOICE_DAMAGE_TABLE + 4u * i)), "SFX_E_HIT_KOURA1") == 0);
    for (i = 0; i < 4u; ++i) assert(strcmp(str(r32(SPM_R8PE01_VOICE_ACRO_TABLE + 4u * i)), "SFX_OPENSPM_SILENT") == 0);
    assert(strcmp(str(r32(SPM_R8PE01_VOICE_ATTACK_TABLE)), "SFX_P_V_MARIO_ATTACK1") == 0);
    assert(strcmp(str(r32(SPM_R8PE01_VOICE_DAMAGE_TABLE + 12u)), "SFX_P_V_PEACH_DAMEGE1") == 0);
    /* His size and speed in Mario's row (height and dash stay Mario's); the
       other rows are untouched. */
    assert(g_r.prop_ok && prop(0u, 0u) == 30.0f && prop(0u, 2u) == 1.25f);
    assert(prop(0u, 1u) == 30.0f && prop(0u, 3u) == 2.0f && prop(0u, 4u) == 1.0f);
    assert(prop(1u, 0u) == 11.0f && prop(1u, 2u) == 1.0f);
    assert(strstr(g_all, "width 30.0 (base 10.0), height 30.0 (base 30.0), walk 1.25 (base 1.00), dash 2.00 (base 2.00).") != NULL);
    /* Half a second in, the hitbox the game applied goes to the log. */
    wf(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_HIT_WIDTH, 30.0f);
    wf(SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_HIT_HEIGHT, 42.0f);
    for (i = 0; i < 30u; ++i) tick();
    assert(strstr(g_all, "hitbox in play: width 30.0, height 42.0.") != NULL);
    /* His model is loaded (a fake animdrv with a 3-animation model): the
       names the game asks Mario's model for get added, each playing the
       model's own animation, the mapped one, or S_1. */
    {
        const uint32_t work = 0x80760000u, header = 0x80761000u, anims = 0x80762000u;
        static const char *const own[3] = {"S_1", "W_1", "J_1_a"};
        w32(OPENSPM_ROSTER_ANIM_WORK_PTR, work);
        w32(work, work + 0x100u);                  /* groups */
        w32(work + 0x10u, work + 0x200u);          /* poses; pose 0 -> group 0 */
        w32(work + 0x100u + 8u, work + 0x400u);    /* group 0 file */
        w32(work + 0x400u + 0xA4u, work + 0x500u); /* file data */
        w32(work + 0x500u, header);
        w32(header + 0x148u, 3u);
        w32(header + 0x1ACu, anims);
        for (i = 0; i < 3u; ++i) {
            wstr(anims + 64u * i, own[i]);
            w32(anims + 64u * i + 60u, 0x11110000u * (i + 1u));
        }
        tick();
        assert(r32(header + 0x1ACu) != anims && r32(header + 0x148u) > 3u && strstr(g_log, "animation names") != NULL);
        /* His model's own names are logged once, before the aliases. */
        assert(strstr(g_all, "has 3 animations of its own: S_1 W_1 J_1_a\n") != NULL);
        {
            const uint32_t table = r32(header + 0x1ACu), n = r32(header + 0x148u);
            uint32_t found = 0u;
            for (i = 0; i < n; ++i) {
                const char *name = str(table + 64u * i);
                const uint32_t data = r32(table + 64u * i + 60u);
                if (i < 3u) assert(strcmp(name, own[i]) == 0 && data == 0x11110000u * (i + 1u));
                if (strcmp(name, "mario_W_1R") == 0) { assert(data == 0x22220000u); found |= 1u; }
                if (strcmp(name, "mario_J_1B") == 0) { assert(data == 0x33330000u); found |= 2u; } /* anim_map */
                if (strcmp(name, "Big_mario_O_2B") == 0) { assert(data == 0x11110000u); found |= 4u; } /* S_1 */
                if (strcmp(name, "mario_J_1_a") == 0) { assert(data == 0x33330000u); found |= 8u; } /* his own */
            }
            assert(found == 15u);
            tick(); /* built once per model load */
            assert(r32(header + 0x1ACu) == table && r32(header + 0x148u) == n);
            assert(strstr(strstr(g_all, "of its own") + 1, "of its own") == NULL);
        }
    }
    g_held = OPENSPM_WII_BTN_A | OPENSPM_WII_BTN_2;
    g_set_calls = 0;
    tick();
    assert(g_set_calls == 1 && g_set_held == OPENSPM_WII_BTN_2);
    g_held = 0u;
    /* Leaving the game gives Mario his voice back. */
    w32(SPM_R8PE01_SEQ_WORK, 1u);
    tick();
    assert(strcmp(str(r32(SPM_R8PE01_VOICE_DAMAGE_TABLE)), "SFX_P_V_MARIO_DAMEGE1") == 0);
    assert(strcmp(str(r32(SPM_R8PE01_VOICE_ACRO_TABLE + 12u)), "SFX_P_V_MARIO_ACRO4") == 0);
    assert(prop(0u, 0u) == 10.0f && prop(0u, 2u) == 1.0f); /* and his size and speed */

    /* Another revision: off, nothing written. */
    setup(1);
    wstr(STRINGS + 32u, "p_other");
    openspm_roster_init(&g_r);
    d = def("a:one", "One", "e_kuribo", 0u);
    assert(openspm_roster_register(&g_r, &d, &id, why, sizeof why) == OPENSPM_OK);
    close_menu();
    tick();
    assert(g_r.disabled && g_scratch_used == 0u && strstr(g_log, "not R8PE01") != NULL);
    open_menu(0xFu, 0u);
    tick();
    assert(r32(CHARTAB + SPM_R8PE01_CHARTAB_COUNT) == 4u);

    free(g_mem);
    puts("PASS: content roster");
    return 0;
}
