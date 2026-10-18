/* Sampling profiler core: where the game thread spends its time.
 *
 * The launcher (OPENSPM_PROFILE=1, OpenSPM.ini [Debug] profile=1) samples the
 * game thread's instruction pointer about once a millisecond and, every 30 s
 * and at exit, logs the functions that took the most samples. Addresses are
 * named from the linker map next to each module (lld-link / link.exe /MAP:
 * openspm_local_native.map, openspm_renderer.map, ...). A recompiled game
 * function is named func_<guest address>_budget, so the report also gives
 * the game address, to look up in a disassembly.
 *
 * This header is the portable part (counting, map parsing, the report) so
 * tests/m14_sample_profile_test.c can check it; the sampling itself is
 * Windows code in launcher/native_main.c.
 */
#ifndef OPENSPM_SAMPLE_PROFILE_H
#define OPENSPM_SAMPLE_PROFILE_H

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define OPENSPM_PROFILE_SLOTS 65536u /* distinct addresses per window */

/* ---- counting (no allocation: safe while the game thread is suspended) ---- */

typedef struct openspm_profile_counts {
    uint64_t address[OPENSPM_PROFILE_SLOTS]; /* 0 = empty */
    uint32_t count[OPENSPM_PROFILE_SLOTS];
    uint32_t used, samples, dropped;
} openspm_profile_counts;

static inline void openspm_profile_add(openspm_profile_counts *c, uint64_t address) {
    uint32_t i = (uint32_t)((address * UINT64_C(0x9E3779B97F4A7C15)) >> 48) & (OPENSPM_PROFILE_SLOTS - 1u), n;
    ++c->samples;
    if (address == 0u) address = 1u;
    for (n = 0; n < 64u; ++n, i = (i + 1u) & (OPENSPM_PROFILE_SLOTS - 1u)) {
        if (c->address[i] == address) {
            ++c->count[i];
            return;
        }
        if (c->address[i] == 0u) {
            if (c->used >= OPENSPM_PROFILE_SLOTS / 2u) break; /* keep probes short */
            c->address[i] = address;
            c->count[i] = 1u;
            ++c->used;
            return;
        }
    }
    ++c->dropped;
}

static inline void openspm_profile_clear(openspm_profile_counts *c) { memset(c, 0, sizeof *c); }

/* ---- linker maps ------------------------------------------------------------ */

typedef struct openspm_profile_symbol {
    uint64_t rva;
    const char *name;
} openspm_profile_symbol;

typedef struct openspm_profile_map {
    char *text; /* owned: names point into it */
    openspm_profile_symbol *symbols;
    size_t count;
} openspm_profile_map;

static inline int openspm_profile_hex(const char *s, size_t n, uint64_t *out) {
    uint64_t v = 0u;
    size_t i;
    if (n == 0u) return 0;
    for (i = 0; i < n; ++i) {
        const char ch = s[i];
        const int d = ch >= '0' && ch <= '9' ? ch - '0' : ch >= 'a' && ch <= 'f' ? ch - 'a' + 10 :
                      ch >= 'A' && ch <= 'F' ? ch - 'A' + 10 : -1;
        if (d < 0) return 0;
        v = v << 4 | (uint64_t)d;
    }
    *out = v;
    return 1;
}

static inline int openspm_profile_symbol_order(const void *a, const void *b) {
    const uint64_t x = ((const openspm_profile_symbol *)a)->rva, y = ((const openspm_profile_symbol *)b)->rva;
    return x < y ? -1 : x > y;
}

/* Parses a COFF linker map (the "Publics by Value" and "Static symbols"
   lines: " 0001:00000010  name  0000000180001010  obj"), taking ownership of
   `text` (malloc'd, NUL-terminated). Returns the number of symbols. */
static inline size_t openspm_profile_map_parse(openspm_profile_map *m, char *text) {
    static const char base_key[] = "Preferred load address is ";
    uint64_t base = 0u;
    size_t cap = 0u;
    char *line = text, *found;
    memset(m, 0, sizeof *m);
    m->text = text;
    if (text == NULL) return 0u;
    if ((found = strstr(text, base_key)) != NULL) {
        const char *h = found + sizeof base_key - 1u;
        size_t n = 0u;
        while (n < 16u && ((h[n] >= '0' && h[n] <= '9') || (h[n] >= 'a' && h[n] <= 'f') || (h[n] >= 'A' && h[n] <= 'F')))
            ++n;
        (void)openspm_profile_hex(h, n, &base);
    }
    while (*line) {
        char *end = strchr(line, '\n'), *p = line, *name, *name_end, *va;
        uint64_t section, value;
        if (end == NULL) end = line + strlen(line);
        while (p < end && *p == ' ') ++p;
        /* "SSSS:OOOOOOOO" */
        if (end - p > 14 && p[4] == ':' && openspm_profile_hex(p, 4u, &section) && section != 0u &&
            openspm_profile_hex(p + 5, 8u, &value) && p[13] == ' ') {
            name = p + 13;
            while (name < end && *name == ' ') ++name;
            name_end = name;
            while (name_end < end && *name_end != ' ' && *name_end != '\r') ++name_end;
            va = name_end;
            while (va < end && *va == ' ') ++va;
            if (name_end > name && end - va >= 16 && openspm_profile_hex(va, 16u, &value) && value >= base) {
                if (m->count == cap) {
                    const size_t next = cap ? cap * 2u : 4096u;
                    openspm_profile_symbol *grown = (openspm_profile_symbol *)realloc(m->symbols, next * sizeof *grown);
                    if (grown == NULL) break;
                    m->symbols = grown;
                    cap = next;
                }
                *name_end = '\0';
                m->symbols[m->count].rva = value - base;
                m->symbols[m->count].name = name;
                ++m->count;
            }
        }
        line = *end ? end + 1 : end;
    }
    if (m->count) qsort(m->symbols, m->count, sizeof *m->symbols, openspm_profile_symbol_order);
    return m->count;
}

/* The symbol an address (module-relative) falls in: the last one at or
   before it; NULL before the first. */
static inline const openspm_profile_symbol *openspm_profile_map_find(const openspm_profile_map *m, uint64_t rva) {
    size_t lo = 0u, hi = m->count;
    while (lo < hi) {
        const size_t mid = lo + (hi - lo) / 2u;
        if (m->symbols[mid].rva <= rva) lo = mid + 1u;
        else hi = mid;
    }
    return lo ? &m->symbols[lo - 1u] : NULL;
}

static inline void openspm_profile_map_free(openspm_profile_map *m) {
    free(m->symbols);
    free(m->text);
    memset(m, 0, sizeof *m);
}

/* ---- the report ----------------------------------------------------------- */

/* What one sampled address is: its module's short name and, when the module
   has a map, the symbol (else NULL). */
typedef struct openspm_profile_place {
    const char *module;
    const char *symbol;
} openspm_profile_place;

typedef int (*openspm_profile_resolve_fn)(void *user, uint64_t address, openspm_profile_place *out);
typedef void (*openspm_profile_line_fn)(void *user, const char *line);

typedef struct openspm_profile_row {
    const char *module, *symbol;
    uint32_t count;
} openspm_profile_row;

static inline int openspm_profile_row_order(const void *a, const void *b) {
    const uint32_t x = ((const openspm_profile_row *)a)->count, y = ((const openspm_profile_row *)b)->count;
    return x > y ? -1 : x < y;
}

/* " (game 0x80012345)" for a recompiled game function, else "". */
static inline void openspm_profile_guest(const char *symbol, char *out, size_t cap) {
    uint64_t pc;
    out[0] = '\0';
    if (symbol && strncmp(symbol, "func_", 5) == 0 && openspm_profile_hex(symbol + 5, 8u, &pc))
        snprintf(out, cap, " (game 0x%08X)", (unsigned)pc);
}

/* Logs the top `top` places of a window, then the share per module. Each
   address is resolved once; places with the same module and symbol add up
   (rows are matched by the pointers the resolver returns, which must stay
   valid for the call). Returns the rows made, 0 on no samples or no memory. */
static inline size_t openspm_profile_report(const openspm_profile_counts *c, const char *title, uint32_t top,
                                            openspm_profile_resolve_fn resolve, void *resolve_user,
                                            openspm_profile_line_fn emit, void *emit_user) {
    openspm_profile_row *rows;
    size_t nrows = 0u, i, k;
    char line[320], guest[32];
    if (c->samples == 0u || (rows = (openspm_profile_row *)calloc(c->used + 1u, sizeof *rows)) == NULL) return 0u;
    for (i = 0; i < OPENSPM_PROFILE_SLOTS; ++i) {
        openspm_profile_place place = {"?", NULL};
        if (c->address[i] == 0u) continue;
        if (!resolve(resolve_user, c->address[i], &place) || place.module == NULL) place.module = "?";
        for (k = 0; k < nrows; ++k)
            if (rows[k].module == place.module && rows[k].symbol == place.symbol) break;
        if (k == nrows) {
            rows[nrows].module = place.module;
            rows[nrows].symbol = place.symbol;
            ++nrows;
        }
        rows[k].count += c->count[i];
    }
    qsort(rows, nrows, sizeof *rows, openspm_profile_row_order);
    snprintf(line, sizeof line, "OpenSPM profile %s: %u samples (%u addresses%s).", title, (unsigned)c->samples,
             (unsigned)c->used, c->dropped ? ", some not counted: table full" : "");
    emit(emit_user, line);
    for (i = 0; i < nrows && i < top; ++i) {
        openspm_profile_guest(rows[i].symbol, guest, sizeof guest);
        snprintf(line, sizeof line, "OpenSPM profile %5.1f%%  %s!%s%s", 100.0 * rows[i].count / c->samples,
                 rows[i].module, rows[i].symbol ? rows[i].symbol : "(no map)", guest);
        emit(emit_user, line);
    }
    /* Per module (rows are already merged by module + symbol). */
    {
        size_t used = 0u;
        int n;
        line[0] = '\0';
        for (i = 0; i < nrows; ++i) {
            uint32_t total = 0u;
            for (k = 0; k < i; ++k)
                if (rows[k].module == rows[i].module) break;
            if (k < i) continue;
            for (k = i; k < nrows; ++k)
                if (rows[k].module == rows[i].module) total += rows[k].count;
            n = snprintf(line + used, sizeof line - used, "%s%s %.1f%%", used ? ", " : "OpenSPM profile modules: ",
                         rows[i].module, 100.0 * total / c->samples);
            if (n < 0 || used + (size_t)n >= sizeof line) break;
            used += (size_t)n;
        }
        if (used) emit(emit_user, line);
    }
    free(rows);
    return nrows;
}

#endif
