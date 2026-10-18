/* Host test for include/openspm/sample_profile.h: counting sampled
   addresses, parsing a COFF linker map (lld-link /MAP), naming addresses,
   and the report (top functions with their game address, share per module). */
#include "openspm/sample_profile.h"

#include <assert.h>

/* What lld-link /MAP writes (a small DLL of ours, preferred base 0x180000000). */
static const char k_map[] =
    " a\r\n\r\n Timestamp is 6ac2c784 (Sun Oct  4 21:39:16 2026)\r\n\r\n"
    " Preferred load address is 0000000180000000\r\n\r\n"
    " Start         Length     Name                   Class\r\n"
    " 0001:00000000 0000007bH .text                   CODE\r\n\r\n"
    "  Address         Publics by Value              Rva+Base               Lib:Object\r\n\r\n"
    " 0001:00000010       func_80002000_budget       0000000180001010     a.obj\r\n"
    " 0001:00000000       func_80001000_budget       0000000180001000     a.obj\r\n"
    " 0001:00000030       entry                      0000000180001030     a.obj\r\n\r\n"
    " entry point at         0000:00000000\r\n\r\n"
    " Static symbols\r\n\r\n"
    " 0001:00000050       helper                     0000000180001050     a.obj\r\n";

static openspm_profile_map g_map;
static char g_lines[16][320];
static int g_nlines;

/* Module "game" at 0x10000000 (mapped), "gpu" at 0x20000000 (no map). */
static int resolve(void *user, uint64_t address, openspm_profile_place *out) {
    (void)user;
    if (address >= 0x10000000u && address < 0x10010000u) {
        const openspm_profile_symbol *s = openspm_profile_map_find(&g_map, address - 0x10000000u);
        out->module = "game";
        out->symbol = s ? s->name : NULL;
        return 1;
    }
    if (address >= 0x20000000u && address < 0x20010000u) {
        out->module = "gpu";
        out->symbol = NULL;
        return 1;
    }
    return 0;
}
static void emit(void *user, const char *line) {
    (void)user;
    assert(g_nlines < 16);
    snprintf(g_lines[g_nlines++], sizeof g_lines[0], "%s", line);
}

int main(void) {
    static openspm_profile_counts counts;
    char *text = malloc(sizeof k_map);
    uint32_t i;
    assert(text);
    memcpy(text, k_map, sizeof k_map);
    /* The map: four symbols, sorted, module-relative. */
    assert(openspm_profile_map_parse(&g_map, text) == 4u);
    assert(g_map.symbols[0].rva == 0x1000u && strcmp(g_map.symbols[0].name, "func_80001000_budget") == 0);
    assert(g_map.symbols[1].rva == 0x1010u && strcmp(g_map.symbols[3].name, "helper") == 0);
    assert(openspm_profile_map_find(&g_map, 0x0FFFu) == NULL);
    assert(strcmp(openspm_profile_map_find(&g_map, 0x100Fu)->name, "func_80001000_budget") == 0);
    assert(strcmp(openspm_profile_map_find(&g_map, 0x1010u)->name, "func_80002000_budget") == 0);
    assert(strcmp(openspm_profile_map_find(&g_map, 0x1058u)->name, "helper") == 0);

    /* Samples: 60 in func_80002000 (three addresses), 30 in the gpu module,
       10 outside any module. */
    for (i = 0; i < 60u; ++i) openspm_profile_add(&counts, 0x10001010u + 4u * (i % 3u));
    for (i = 0; i < 30u; ++i) openspm_profile_add(&counts, 0x20000100u + i);
    for (i = 0; i < 10u; ++i) openspm_profile_add(&counts, 0x30000000u);
    assert(counts.samples == 100u && counts.used == 3u + 30u + 1u && counts.dropped == 0u);
    assert(openspm_profile_report(&counts, "last 30 s", 10u, resolve, NULL, emit, NULL) == 3u);
    assert(g_nlines == 5);
    assert(strstr(g_lines[0], "last 30 s: 100 samples (34 addresses)") != NULL);
    assert(strstr(g_lines[1], " 60.0%  game!func_80002000_budget (game 0x80002000)") != NULL);
    assert(strstr(g_lines[2], " 30.0%  gpu!(no map)") != NULL);
    assert(strstr(g_lines[3], " 10.0%  ?!(no map)") != NULL);
    assert(strstr(g_lines[4], "modules: game 60.0%, gpu 30.0%, ? 10.0%") != NULL);

    /* A full table drops (and says so) rather than probing forever. */
    openspm_profile_clear(&counts);
    for (i = 1; i <= OPENSPM_PROFILE_SLOTS; ++i) openspm_profile_add(&counts, (uint64_t)i * 4096u);
    assert(counts.used <= OPENSPM_PROFILE_SLOTS / 2u && counts.dropped > 0u &&
           counts.samples == OPENSPM_PROFILE_SLOTS);
    openspm_profile_map_free(&g_map);
    puts("PASS: sample profile");
    return 0;
}
