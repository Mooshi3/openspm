/* Host test for added Pixls in the OpenSPM content roster
   (include/openspm/content_roster.h): registration rules, items written into
   the relocated item table (name/description through OpenSPM's messages, the
   model, the icon), Pixl-tab entries after the game's, picking one (button 2
   kept from the game, OpenSPM's slot selected, the pouch left alone until the
   menu is closed, then no game Pixl selected and the game's Pixl flip, or
   evt_fairy_reset when Mario is busy),
   picking a game Pixl again, leaving the game, and the fallback without the
   rebuilt module. */
#include "openspm/content_roster.h"
#include "openspm/guest_call.h"

#include <assert.h>
#include <stdlib.h>

#define MEM1_SIZE 0x01800000u
#define MEM2_SIZE 0x04000000u
static uint8_t *g_mem1, *g_mem2;
static openspm_guest_call_queue g_calls;
static uint32_t g_scratch_used, g_held, g_prev, g_set_held;
static int g_set_calls;
static char g_log[256];

static uint8_t *P(uint32_t a) {
    if (a >= 0x80000000u && a < 0x80000000u + MEM1_SIZE) return g_mem1 + (a - 0x80000000u);
    if (a >= 0x90000000u && a < 0x90000000u + MEM2_SIZE) return g_mem2 + (a - 0x90000000u);
    return NULL;
}
static void w32(uint32_t a, uint32_t v) { uint8_t *p = P(a); p[0] = v >> 24; p[1] = v >> 16; p[2] = v >> 8; p[3] = v; }
static uint32_t r32(uint32_t a) { const uint8_t *p = P(a); return (uint32_t)p[0] << 24 | p[1] << 16 | p[2] << 8 | p[3]; }
static void wstr(uint32_t a, const char *s) { memcpy(P(a), s, strlen(s) + 1u); }
static const char *str(uint32_t a) { return (const char *)P(a); }

static int f_read32(void *c, uint32_t a, uint32_t *v) { (void)c; if (!P(a)) return -1; *v = r32(a); return 0; }
static int f_write32(void *c, uint32_t a, uint32_t v) { (void)c; if (!P(a)) return -1; w32(a, v); return 0; }
static int f_call(void *c, uint32_t t, uint32_t n, const uint32_t *args) { (void)c; return openspm_guest_call_push(&g_calls, t, n, args); }
static int f_scratch(void *c, uint32_t s, uint32_t *o) { (void)c; return openspm_guest_scratch_take(&g_scratch_used, s, o); }
static int f_get(void *c, uint32_t *h, uint32_t *p) { (void)c; *h = g_held; *p = g_held & ~g_prev; return 0; }
static int f_set(void *c, uint32_t h) { (void)c; g_set_held = h; ++g_set_calls; return 0; }
static void f_log(void *c, int warn, const char *t) { (void)c; (void)warn; snprintf(g_log, sizeof g_log, "%s", t); }
static const openspm_roster_io k_io = {NULL, f_read32, f_write32, f_call, f_scratch, f_get, f_set, f_log};

#define STRINGS 0x80700000u
#define MSG_WORK 0x80710000u
#define CHARTAB 0x80720000u
#define PAUSEWIN 0x80730000u
#define PW_ENTRIES 0x80731000u
#define PLIST 0x80740000u
#define POUCH_PIXLS OPENSPM_ROSTER_POUCH_PIXLS

static void setup(int relocated) {
    static const char *models[4] = {"p_wii_mario", "p_wii_peach", "p_wii_kuppa", "p_wii_luigi"};
    uint32_t i;
    memset(g_mem1, 0, MEM1_SIZE);
    memset(g_mem2, 0, MEM2_SIZE);
    memset(&g_calls, 0, sizeof g_calls);
    g_scratch_used = 0u;
    w32(SPM_R8PE01_EVT_ENTRY, 0x9421FFF0u);
    w32(SPM_R8PE01_EVT_MARIO_SET_CHARACTER, 0x9421FFF0u);
    w32(OPENSPM_ROSTER_PAUSEWIN_SET_MESSAGE, 0x9421FF60u);
    w32(OPENSPM_ROSTER_EVT_FAIRY_RESET, 0x9421FFF0u);
    for (i = 0; i < 4u; ++i) {
        wstr(STRINGS + 32u * i, models[i]);
        w32(SPM_R8PE01_CHAR_MODEL_TABLE + 8u * i, STRINGS + 32u * i);
        w32(SPM_R8PE01_CHAR_PRELOAD_TABLE + 8u * i, STRINGS + 32u * i);
    }
    wstr(STRINGS + 0x100u, "ICON");
    wstr(STRINGS + 0x120u, "MARIO");
    w32(SPM_R8PE01_ITEM_DATA + 232u * 0x2Cu, STRINGS + 0x100u);
    w32(SPM_R8PE01_ITEM_DATA + 216u * 0x2Cu, STRINGS + 0x120u);
    /* Thoreau's entry as the template; icons. */
    w32(SPM_R8PE01_ITEM_DATA + 221u * 0x2Cu + 4u, 140u << 16);
    w32(SPM_R8PE01_ITEM_DATA + 221u * 0x2Cu + 40u, 0x00000007u);
    w32(SPM_R8PE01_ITEM_DATA + 222u * 0x2Cu + 4u, 141u << 16);
    if (relocated) {
        memcpy(P(OPENSPM_ITEM_DATA_RELOCATED), P(SPM_R8PE01_ITEM_DATA), 538u * 0x2Cu);
        w32(OPENSPM_ROSTER_PIXLTAB_PATCH, OPENSPM_ROSTER_PIXLTAB_PATCHED);
    }
    w32(k_openspm_code_patches[0].address, k_openspm_code_patches[0].replacement);
    w32(OPENSPM_ROSTER_MSG_WORK_PTR, MSG_WORK);
    w32(SPM_R8PE01_CHARTAB_WP, CHARTAB);
    w32(SPM_R8PE01_PAUSEWIN_WP, PAUSEWIN);
    w32(PAUSEWIN + SPM_R8PE01_PAUSEWIN_ENTRIES, PW_ENTRIES);
    w32(CHARTAB + SPM_R8PE01_CHARTAB_WINDOW_ID, 3u);
    w32(CHARTAB + OPENSPM_ROSTER_PIXLTAB_WINDOW_ID, 4u);
    w32(SPM_R8PE01_SEQ_WORK, 2u);
    /* The pouch: Tippi (selected), Thoreau, Boomer. */
    w32(POUCH_PIXLS + 0u, 0x010100DCu);
    w32(POUCH_PIXLS + 4u, 0x010000DDu);
    w32(POUCH_PIXLS + 8u, 0x010000DEu);
}

static const char *msg_search(const char *key) {
    uint32_t i;
    for (i = 1; i < 9u; ++i) {
        const uint32_t slot = MSG_WORK + 16u * i, handle = r32(slot + 12u);
        uint32_t lo = 0u, hi, base, entries;
        if (!handle) continue;
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

/* The game's Pixl tab builder: one entry per pouch slot with a Pixl. */
static void open_menu(void) {
    uint32_t i, n = 0u;
    memset(P(PLIST), 0, 768u);
    for (i = 0; i < 16u; ++i) {
        const uint32_t w = r32(POUCH_PIXLS + 4u * i);
        if (!(w & 0xFFFFu)) continue;
        w32(PLIST + 12u * n, w & 0xFF000000u);
        w32(PLIST + 12u * n + 4u, w & 0xFFFFu);
        w32(PLIST + 12u * n + 8u, POUCH_PIXLS + 4u * i);
        if (w & 0x00FF0000u) w32(CHARTAB + OPENSPM_ROSTER_PIXLTAB_CURSOR, n);
        ++n;
    }
    w32(CHARTAB + OPENSPM_ROSTER_PIXLTAB_LIST, PLIST);
    w32(CHARTAB + OPENSPM_ROSTER_PIXLTAB_COUNT, n);
    w32(CHARTAB + SPM_R8PE01_CHARTAB_LIVE_LISTS, 0u); /* 3 only while the menu closes */
    w32(PW_ENTRIES + 4u * 120u + OPENSPM_ROSTER_PAUSEWIN_INIT_FN, OPENSPM_ROSTER_PIXLTAB_INIT);
    w32(PW_ENTRIES + 4u * 120u, 0x00010000u);
    w32(PW_ENTRIES + 4u * 120u + 60u, 10u);
    w32(SPM_R8PE01_SYSTEM_LEVEL, 1u);
}
static void close_menu(void) {
    /* The game's close: the windows are deleted (flags 0); the list pointer
       stays behind (freed) and the next open may get the same address. */
    w32(PW_ENTRIES + 4u * 120u, 0u);
    w32(CHARTAB + SPM_R8PE01_CHARTAB_LIVE_LISTS, 0u);
    w32(SPM_R8PE01_SYSTEM_LEVEL, 0u);
}
/* The game's own confirm on a listed entry: every listed slot unselected,
   the chosen one selected. */
static void game_confirm(uint32_t index) {
    uint32_t i, n = r32(CHARTAB + OPENSPM_ROSTER_PIXLTAB_COUNT);
    for (i = 0; i < n; ++i) {
        const uint32_t slot = r32(PLIST + 12u * i + 8u);
        w32(slot, r32(slot) & 0xFF00FFFFu);
    }
    w32(r32(PLIST + 12u * index + 8u), r32(r32(PLIST + 12u * index + 8u)) | 0x00010000u);
}

static openspm_roster g_r;
static void tick(void) { openspm_roster_tick(&g_r, &k_io); g_prev = g_held; }
static int pop(openspm_guest_call_entry *e) { return openspm_guest_call_pop(&g_calls, e); }

static openspm_pixl_def pdef(const char *key, const char *name, const char *model) {
    openspm_pixl_def d;
    memset(&d, 0, sizeof d);
    d.header.struct_size = sizeof d;
    d.key = key; d.name = name; d.description = "Fires blasts."; d.model = model; d.icon_item = 222u;
    return d;
}

int main(void) {
    char why[160];
    uint32_t id = 0u, item;
    openspm_guest_call_entry call;
    openspm_pixl_def d;
    g_mem1 = calloc(1, MEM1_SIZE);
    g_mem2 = calloc(1, MEM2_SIZE);
    assert(g_mem1 && g_mem2);

    /* Without the rebuilt module: the Pixl stays out, nothing is written. */
    setup(0);
    openspm_roster_init(&g_r);
    d = pdef("glock:glock", "Glock", "e_kozun");
    assert(openspm_roster_register_pixl(&g_r, &d, &id, why, sizeof why) == OPENSPM_OK && id == 538u);
    close_menu();
    tick();
    assert(!g_r.pixls_ready && strstr(g_log, "rebuilt native module") != NULL);
    open_menu();
    tick();
    assert(r32(CHARTAB + OPENSPM_ROSTER_PIXLTAB_COUNT) == 3u);

    /* Registration rules. */
    setup(1);
    openspm_roster_init(&g_r);
    d = pdef("glock", "Glock", "e_kozun");
    assert(openspm_roster_register_pixl(&g_r, &d, &id, why, sizeof why) == OPENSPM_ERROR_INVALID_ARGUMENT);
    d = pdef("glock:glock", "Glock", "E-KOZUN");
    assert(openspm_roster_register_pixl(&g_r, &d, &id, why, sizeof why) == OPENSPM_ERROR_INVALID_ARGUMENT);
    d = pdef("glock:glock", "Glock", "e_kozun");
    assert(openspm_roster_register_pixl(&g_r, &d, &id, why, sizeof why) == OPENSPM_OK && id == 538u);
    assert(openspm_roster_register_pixl(&g_r, &d, &id, why, sizeof why) == OPENSPM_ERROR_RUNTIME);

    /* Built: item 538 in the relocated table, looked up the game's way. */
    close_menu();
    tick();
    assert(g_r.pixls_ready && strstr(g_log, "items 538-538") != NULL);
    item = OPENSPM_ITEM_DATA_RELOCATED + 538u * 0x2Cu;
    assert(strcmp(str(r32(item)), "glock:glock") == 0);
    assert(strcmp(str(r32(item + 8u)), "e_kozun") == 0 && strcmp(str(r32(item + 12u)), "S_1") == 0);
    assert(r32(item + 4u) >> 16 == 141u);          /* Boomer's icon, as asked */
    assert(r32(item + 40u) == 7u);                 /* the rest from a real Pixl entry */
    assert(strcmp(msg_search(str(r32(item + 16u))), "Glock") == 0);
    assert(strcmp(msg_search(str(r32(item + 20u))), "Fires blasts.") == 0);
    assert(r32(SPM_R8PE01_ITEM_DATA + 216u * 0x2Cu) == STRINGS + 0x120u); /* original untouched */

    /* Menu: Glock after the game's three Pixls; the window grows a row (the
       game sized it for three: 25 * 3 + 50). */
    open_menu();
    {
        const float h = 125.0f;
        uint32_t bits;
        memcpy(&bits, &h, 4);
        w32(PW_ENTRIES + 4u * 120u + OPENSPM_ROSTER_PAUSEWIN_HEIGHT, bits);
    }
    tick();
    assert(r32(CHARTAB + OPENSPM_ROSTER_PIXLTAB_COUNT) == 4u);
    {
        float h;
        const uint32_t bits = r32(PW_ENTRIES + 4u * 120u + OPENSPM_ROSTER_PAUSEWIN_HEIGHT);
        memcpy(&h, &bits, 4);
        assert(h == 150.0f);
    }
    assert(r32(PLIST + 36u) == 0x01000000u && r32(PLIST + 36u + 4u) == 538u);
    assert(r32(r32(PLIST + 36u + 8u)) == 0x0100021Au); /* enabled, not selected, item 538 */

    /* Pick Glock: the press stays with OpenSPM; the pouch keeps Tippi until
       the menu is closed (the game compares on close). */
    w32(OPENSPM_ROSTER_FOLLOWERS + 840u, 1u); /* Tippi's follower is out */
    w32(OPENSPM_ROSTER_FOLLOWERS + 4u + 4u, 220u);
    w32(CHARTAB + OPENSPM_ROSTER_PIXLTAB_CURSOR, 3u);
    g_held = OPENSPM_WII_BTN_2;
    g_set_calls = 0;
    tick();
    assert(g_set_calls == 1 && !(g_set_held & OPENSPM_WII_BTN_2));
    assert(r32(g_r.pixls[0].slot) & 0x00FF0000u);
    /* ...and the game's own confirm tail: the pick sound, then "Pixl picked"
       (the pause menu closes). */
    assert(r32(CHARTAB) & OPENSPM_ROSTER_CHARTAB_PIXL_PICKED);
    assert(pop(&call) && call.target == OPENSPM_ROSTER_SFX_PLAY && call.args[0] == OPENSPM_ROSTER_SFX_DECIDE);
    assert(r32(POUCH_PIXLS) & 0x00FF0000u);
    tick(); /* still held: masked */
    assert(!(g_set_held & OPENSPM_WII_BTN_2));
    g_held = 0u;
    tick();
    close_menu();
    tick();
    assert(!(r32(POUCH_PIXLS) & 0x00FF0000u) && !(r32(POUCH_PIXLS + 4u) & 0x00FF0000u));
    /* The game's Pixl flip from Tippi; a game Pixl's item stands in for his
       sizes (the game's lookup only knows its own Pixls). */
    assert(pop(&call) && call.target == OPENSPM_ROSTER_PIXL_CHANGE && call.argc == 2u && call.args[0] == 220u &&
           call.args[1] == OPENSPM_ROSTER_PIXL_STANDIN);
    assert(openspm_roster_pixl_active(&g_r, 538u) && strstr(g_log, "Glock") != NULL);
    tick();
    assert(!pop(&call) && openspm_roster_pixl_active(&g_r, 538u));

    /* Menu again: the cursor starts on Glock; pick Thoreau: the game switches
       (clears every listed slot, ours too); Glock steps back after close. */
    open_menu();
    tick();
    assert(r32(CHARTAB + OPENSPM_ROSTER_PIXLTAB_CURSOR) == 3u);
    w32(CHARTAB + OPENSPM_ROSTER_PIXLTAB_CURSOR, 1u);
    g_held = OPENSPM_WII_BTN_2;
    tick();
    game_confirm(1u);
    g_held = 0u;
    tick();
    close_menu();
    tick();
    assert(!openspm_roster_pixl_active(&g_r, 538u) && !pop(&call));
    assert(r32(POUCH_PIXLS + 4u) & 0x00FF0000u);

    /* Pick Glock again while Mario is mid-transition: no flip (the game's
       request would not start it), the plain rebuild. Thoreau's follower is
       not in the fake list, so the flip would not name him either. */
    open_menu();
    tick();
    w32(CHARTAB + OPENSPM_ROSTER_PIXLTAB_CURSOR, 3u);
    g_held = OPENSPM_WII_BTN_2;
    tick();
    g_held = 0u;
    tick();
    assert(g_r.pixl_old_item == 221u && openspm_roster_pixl_out(&k_io, 221u) == 0u &&
           openspm_roster_pixl_out(&k_io, 220u) == 220u && openspm_roster_pixl_out(&k_io, 538u) == 0u);
    while (pop(&call)) {}
    w32(SPM_R8PE01_MARIO_WORK, OPENSPM_ROSTER_MARIO_LOCKED);
    close_menu();
    tick();
    assert(pop(&call) && call.target == SPM_R8PE01_EVT_ENTRY && r32(call.args[0] + 4u) == OPENSPM_ROSTER_EVT_FAIRY_RESET);
    w32(SPM_R8PE01_MARIO_WORK, 0u);
    assert(openspm_roster_pixl_active(&g_r, 538u));
    /* Picking him once more while he is out: nothing to switch. */
    open_menu();
    tick();
    w32(CHARTAB + OPENSPM_ROSTER_PIXLTAB_CURSOR, 3u);
    g_held = OPENSPM_WII_BTN_2;
    tick();
    g_held = 0u;
    tick();
    while (pop(&call)) {}
    close_menu();
    tick();
    assert(!pop(&call) && openspm_roster_pixl_active(&g_r, 538u));
    /* A door: Glock stays picked through the map change. */
    w32(SPM_R8PE01_SEQ_WORK, 3u);
    tick();
    w32(SPM_R8PE01_SEQ_WORK, 2u);
    tick();
    assert(openspm_roster_pixl_active(&g_r, 538u) && (r32(g_r.pixls[0].slot) & 0x00FF0000u));
    w32(SPM_R8PE01_SEQ_WORK, 1u);
    tick();
    assert(!openspm_roster_pixl_active(&g_r, 538u) && !(r32(g_r.pixls[0].slot) & 0x00FF0000u));

    free(g_mem1);
    free(g_mem2);
    puts("PASS: content pixls");
    return 0;
}
