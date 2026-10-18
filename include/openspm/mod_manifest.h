/* OpenSPM mod info file (docs/MODDING.md "Mod info files", the first step of
 * docs/MOD-PACKAGES.md): mods\<dll name>.toml next to the DLL.
 *
 *   id = "mooshi.francis"            # required: the DLL's mod_id
 *   name = "Francis"                 # shown in F1 -> Mods
 *   version = "1.0.0"
 *   author = "Mooshi"
 *   description = "A playable Francis with a tongue attack."
 *   requires = ["openspm.content>=1", "openspm.overlay>=2"]
 *
 * A small TOML subset: one `key = value` per line, values are "strings",
 * integers, or a one-line array of strings; '#' starts a comment; unknown keys
 * are ignored (later versions add some). OpenSPMNative refuses a mod whose
 * file does not parse, whose id differs from the DLL's, or that requires a
 * service this OpenSPM does not provide at that version; a mod without the
 * file loads as before.
 *
 * Portable C, no allocation (tests/m14_mod_manifest_test.c). */
#ifndef OPENSPM_MOD_MANIFEST_H
#define OPENSPM_MOD_MANIFEST_H

#include <ctype.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef __cplusplus
extern "C" {
#endif

#define OPENSPM_MANIFEST_MAX_TEXT 256u
#define OPENSPM_MANIFEST_MAX_REQUIRES 16u

typedef struct openspm_manifest_require {
    char service[64];
    uint32_t min_version;
} openspm_manifest_require;

typedef struct openspm_mod_manifest {
    char id[64], name[64], version[32], author[64];
    char description[OPENSPM_MANIFEST_MAX_TEXT];
    openspm_manifest_require needs[OPENSPM_MANIFEST_MAX_REQUIRES]; /* the "requires" key */
    uint32_t require_count;
} openspm_mod_manifest;

static inline int openspm_manifest_id_ok(const char *s) {
    size_t n = 0;
    for (; s[n]; ++n)
        if (!((s[n] >= 'a' && s[n] <= 'z') || (s[n] >= '0' && s[n] <= '9') || s[n] == '_' || s[n] == '.' ||
              s[n] == '-'))
            return 0;
    return n >= 3u && n < 64u;
}

/* One "..." string at *p (basic escapes \" and \\); advances *p. */
static inline int openspm_manifest_string(const char **p, char *out, size_t size) {
    const char *s = *p;
    size_t n = 0;
    if (*s != '"') return 0;
    ++s;
    while (*s && *s != '"' && *s != '\n') {
        char c = *s++;
        if (c == '\\' && (*s == '"' || *s == '\\')) c = *s++;
        if ((unsigned char)c < 0x20u) return 0;
        if (n + 1u >= size) return 0;
        out[n++] = c;
    }
    if (*s != '"') return 0;
    out[n] = 0;
    *p = s + 1;
    return 1;
}

static inline int openspm_manifest_require_parse(const char *text, openspm_manifest_require *out) {
    const char *ge = strstr(text, ">=");
    size_t n;
    char *end = NULL;
    unsigned long v;
    if (ge == NULL) return 0;
    n = (size_t)(ge - text);
    if (n == 0u || n >= sizeof out->service) return 0;
    memcpy(out->service, text, n);
    out->service[n] = 0;
    if (!openspm_manifest_id_ok(out->service)) return 0;
    v = strtoul(ge + 2, &end, 10);
    if (end == ge + 2 || *end != 0 || v == 0u || v > 1000u) return 0;
    out->min_version = (uint32_t)v;
    return 1;
}

/* Parses text (NUL-terminated). 1 on success; 0 with a reason in why. */
static inline int openspm_manifest_parse(const char *text, openspm_mod_manifest *m, char *why, size_t why_size) {
    const char *p = text;
    unsigned line = 0u;
    memset(m, 0, sizeof *m);
    while (*p) {
        const char *key, *eol = strchr(p, '\n');
        size_t klen;
        char value[OPENSPM_MANIFEST_MAX_TEXT];
        ++line;
        if (eol == NULL) eol = p + strlen(p);
        while (p < eol && (*p == ' ' || *p == '\t' || *p == '\r')) ++p;
        if (p == eol || *p == '#') {
            p = *eol ? eol + 1 : eol;
            continue;
        }
        key = p;
        while (p < eol && (isalnum((unsigned char)*p) || *p == '_')) ++p;
        klen = (size_t)(p - key);
        while (p < eol && (*p == ' ' || *p == '\t')) ++p;
        if (klen == 0u || *p != '=') {
            snprintf(why, why_size, "line %u: expected key = value", line);
            return 0;
        }
        ++p;
        while (p < eol && (*p == ' ' || *p == '\t')) ++p;
        if (klen == 8u && strncmp(key, "requires", 8) == 0) {
            if (*p != '[') {
                snprintf(why, why_size, "line %u: requires = [\"service>=version\", ...]", line);
                return 0;
            }
            ++p;
            for (;;) {
                while (p < eol && (*p == ' ' || *p == '\t' || *p == ',')) ++p;
                if (*p == ']') {
                    ++p;
                    break;
                }
                if (m->require_count >= OPENSPM_MANIFEST_MAX_REQUIRES || !openspm_manifest_string(&p, value, sizeof value) ||
                    !openspm_manifest_require_parse(value, &m->needs[m->require_count])) {
                    snprintf(why, why_size, "line %u: requires entries are \"service>=version\"", line);
                    return 0;
                }
                ++m->require_count;
            }
        } else if (*p == '"') {
            char *dst = NULL;
            size_t size = 0;
            if (!openspm_manifest_string(&p, value, sizeof value)) {
                snprintf(why, why_size, "line %u: bad string (one line, at most %u bytes)", line,
                         (unsigned)OPENSPM_MANIFEST_MAX_TEXT - 1u);
                return 0;
            }
#define OPENSPM_MANIFEST_KEY(k) (klen == sizeof(#k) - 1u && strncmp(key, #k, klen) == 0)
            if (OPENSPM_MANIFEST_KEY(id)) { dst = m->id; size = sizeof m->id; }
            else if (OPENSPM_MANIFEST_KEY(name)) { dst = m->name; size = sizeof m->name; }
            else if (OPENSPM_MANIFEST_KEY(version)) { dst = m->version; size = sizeof m->version; }
            else if (OPENSPM_MANIFEST_KEY(author)) { dst = m->author; size = sizeof m->author; }
            else if (OPENSPM_MANIFEST_KEY(description)) { dst = m->description; size = sizeof m->description; }
#undef OPENSPM_MANIFEST_KEY
            if (dst != NULL) {
                if (strlen(value) >= size) {
                    snprintf(why, why_size, "line %u: value too long", line);
                    return 0;
                }
                memcpy(dst, value, strlen(value) + 1u);
            }
        } else {
            /* A number or anything else: an unknown key's business. */
            while (p < eol && *p != '#') ++p;
        }
        while (p < eol && (*p == ' ' || *p == '\t' || *p == '\r')) ++p;
        if (p < eol && *p != '#') {
            snprintf(why, why_size, "line %u: unexpected text after the value", line);
            return 0;
        }
        p = *eol ? eol + 1 : eol;
    }
    if (!openspm_manifest_id_ok(m->id)) {
        snprintf(why, why_size, "id is required: 3-63 of [a-z0-9_.-]");
        return 0;
    }
    return 1;
}

#ifdef __cplusplus
}
#endif

#endif
