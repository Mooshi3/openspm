/* Game selection for OpenSPMNative: identify the player's disc image and keep
 * the choice in OpenSPM.ini.
 *
 *   [Game]
 *   path = C:\Games\Super Paper Mario.wbfs   ; .iso, .wbfs, .wia, or an
 *                                             ; extracted folder / its sys\main.dol
 *   wit  = C:\Tools\wit\bin\wit.exe           ; optional, the extractor
 *
 * Only headers are read here: the disc header (game id, disc revision) that
 * every Wii image keeps unencrypted. Extraction of the encrypted partition is
 * done by the player's own Wiimms ISO Tools (wit), exactly like the .cmd flow
 * (tools/m14_lab/resume_m14.py); OpenSPM never carries a console key.
 *
 * Portable C (no Windows headers) so it is unit-tested on the host
 * (tests/m14_native_game_test.c). */
#ifndef OPENSPM_NATIVE_GAME_H
#define OPENSPM_NATIVE_GAME_H

#include <ctype.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* The one build OpenSPM targets: Super Paper Mario (USA), disc revision 2. */
#define OPENSPM_GAME_ID "R8PE01"
#define OPENSPM_GAME_REVISION 2u

typedef enum openspm_image_kind {
    OPENSPM_IMAGE_UNKNOWN = 0,
    OPENSPM_IMAGE_ISO,      /* raw disc image, header at 0 */
    OPENSPM_IMAGE_WBFS,     /* WBFS file, header copy in the first disc slot */
    OPENSPM_IMAGE_WIA,      /* Wii ISO Archive (wit reads it) */
    OPENSPM_IMAGE_RVZ,      /* Dolphin RVZ (wit does not read it) */
    OPENSPM_IMAGE_DISC_HEADER /* sys/boot.bin of an extracted game */
} openspm_image_kind;

typedef struct openspm_disc_info {
    openspm_image_kind kind;
    char game_id[7];
    unsigned disc_number;
    unsigned revision;
    int is_wii;
    int is_gamecube;
    char title[65];
} openspm_disc_info;

typedef enum openspm_game_check {
    OPENSPM_GAME_OK = 0,
    OPENSPM_GAME_UNREADABLE,     /* not a disc image OpenSPM recognizes */
    OPENSPM_GAME_NOT_WII,        /* a GameCube disc */
    OPENSPM_GAME_WRONG_GAME,     /* another Wii game (or another region) */
    OPENSPM_GAME_WRONG_REVISION, /* R8PE01, but not revision 2 */
    OPENSPM_GAME_UNSUPPORTED_FORMAT /* right game, container the extractor cannot read */
} openspm_game_check;

/* Reads exactly `size` bytes at `offset`; returns 1 on success, 0 otherwise. */
typedef int (*openspm_image_read_fn)(void *user, uint64_t offset, void *buffer, size_t size);

static uint32_t openspm_be32(const unsigned char *p) {
    return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) | ((uint32_t)p[2] << 8) | (uint32_t)p[3];
}

/* Parses a 0x60+ byte Wii/GameCube disc header. Returns 1 if it carries the
   Wii or GameCube magic. */
static int openspm_parse_disc_header(const unsigned char *h, openspm_disc_info *info) {
    size_t i, n;
    info->is_wii = openspm_be32(h + 0x18) == 0x5D1C9EA3u;
    info->is_gamecube = openspm_be32(h + 0x1C) == 0xC2339F3Du;
    if (!info->is_wii && !info->is_gamecube) return 0;
    for (i = 0; i < 6; ++i) {
        unsigned char c = h[i];
        info->game_id[i] = (c >= 0x20 && c < 0x7F) ? (char)c : '?';
    }
    info->game_id[6] = '\0';
    info->disc_number = h[6];
    info->revision = h[7];
    for (n = 0; n < 64 && h[0x20 + n] != 0; ++n) {
        unsigned char c = h[0x20 + n];
        info->title[n] = (c >= 0x20 && c < 0x7F) ? (char)c : '?';
    }
    info->title[n] = '\0';
    return 1;
}

/* Identifies an image through `read`. Returns 1 and fills `info` when a disc
   header was found (check it with openspm_game_check_info), 0 otherwise. */
static int openspm_image_probe(openspm_image_read_fn read, void *user, openspm_disc_info *info) {
    unsigned char head[0x100];
    memset(info, 0, sizeof *info);
    if (!read || !read(user, 0, head, 0x60)) return 0;

    if (memcmp(head, "WBFS", 4) == 0) {
        /* libwbfs layout: "WBFS", n_hd_sec (BE32), hd_sec_sz_s, wbfs_sec_sz_s,
           2 pad, then one byte per disc slot. Slot i's disc info (a copy of
           the disc header, then the sector map) starts at hd sector
           1 + i * disc_info_sz. */
        unsigned hd_shift = head[8], wbfs_shift = head[9];
        uint64_t hd_sec, per_disc, info_bytes, info_secs;
        size_t slots, slot;
        unsigned char table[512];
        if (hd_shift < 9 || hd_shift > 16 || wbfs_shift < 15 || wbfs_shift > 32 || wbfs_shift < hd_shift) return 0;
        hd_sec = (uint64_t)1 << hd_shift;
        per_disc = ((uint64_t)143432u * 2u) >> (wbfs_shift - 15u); /* dual-layer Wii sectors */
        info_bytes = 0x100u + per_disc * 2u;
        info_bytes = (info_bytes + hd_sec - 1u) & ~(hd_sec - 1u);
        info_secs = info_bytes >> hd_shift;
        slots = (size_t)(hd_sec - 12u);
        if (slots > sizeof table) slots = sizeof table;
        if (!read(user, 12, table, slots)) return 0;
        for (slot = 0; slot < slots; ++slot) {
            if (!table[slot]) continue;
            if (!read(user, hd_sec * (1u + (uint64_t)slot * info_secs), head, 0x100)) return 0;
            if (!openspm_parse_disc_header(head, info)) return 0;
            info->kind = OPENSPM_IMAGE_WBFS;
            return 1;
        }
        return 0;
    }
    if (memcmp(head, "WIA\x01", 4) == 0 || memcmp(head, "RVZ\x01", 4) == 0) {
        /* WIA/RVZ: 0x48-byte header 1, then header 2 whose disc header copy
           starts after four 32-bit fields. */
        int rvz = head[0] == 'R';
        if (!read(user, 0x58, head, 0x80)) return 0;
        if (!openspm_parse_disc_header(head, info)) return 0;
        info->kind = rvz ? OPENSPM_IMAGE_RVZ : OPENSPM_IMAGE_WIA;
        return 1;
    }
    if (openspm_parse_disc_header(head, info)) {
        info->kind = OPENSPM_IMAGE_ISO;
        return 1;
    }
    return 0;
}

static openspm_game_check openspm_game_check_info(const openspm_disc_info *info) {
    if (!info || info->kind == OPENSPM_IMAGE_UNKNOWN) return OPENSPM_GAME_UNREADABLE;
    if (!info->is_wii) return OPENSPM_GAME_NOT_WII;
    if (memcmp(info->game_id, OPENSPM_GAME_ID, 6) != 0) return OPENSPM_GAME_WRONG_GAME;
    if (info->revision != OPENSPM_GAME_REVISION) return OPENSPM_GAME_WRONG_REVISION;
    if (info->kind == OPENSPM_IMAGE_RVZ) return OPENSPM_GAME_UNSUPPORTED_FORMAT;
    return OPENSPM_GAME_OK;
}

/* A player-facing sentence for a failed check. */
static void openspm_game_check_message(openspm_game_check check, const openspm_disc_info *info,
                                       char *out, size_t cap) {
    switch (check) {
    case OPENSPM_GAME_OK:
        snprintf(out, cap, "Super Paper Mario (%s, revision %u).", info->game_id, info->revision);
        break;
    case OPENSPM_GAME_NOT_WII:
        snprintf(out, cap, "This is a GameCube disc (%s). OpenSPM needs the Wii game Super Paper Mario (USA, %s).",
                 info->game_id, OPENSPM_GAME_ID);
        break;
    case OPENSPM_GAME_WRONG_GAME:
        snprintf(out, cap,
                 "This disc is %s \"%s\". OpenSPM needs Super Paper Mario (USA), game ID %s. Other regions are not "
                 "supported yet.",
                 info->game_id, info->title, OPENSPM_GAME_ID);
        break;
    case OPENSPM_GAME_WRONG_REVISION:
        snprintf(out, cap,
                 "This is Super Paper Mario (%s) disc revision %u. OpenSPM is built against revision %u only "
                 "(the most common USA disc); other revisions are not supported yet.",
                 info->game_id, info->revision, OPENSPM_GAME_REVISION);
        break;
    case OPENSPM_GAME_UNSUPPORTED_FORMAT:
        snprintf(out, cap,
                 "This is the right game, but in Dolphin's RVZ format, which the extractor (wit) cannot read. In "
                 "Dolphin, right-click the game > Convert File... > Format: ISO (or WBFS), then pick the new file.");
        break;
    default:
        snprintf(out, cap,
                 "This file is not a Wii disc image OpenSPM recognizes. Pick a .iso or .wbfs of Super Paper Mario "
                 "(USA), or the sys\\main.dol of an extracted copy.");
        break;
    }
}

/* ---- OpenSPM.ini [Game] values (UTF-8 text) ---------------------------- */

static int openspm_ini_name_eq(const char *a, size_t alen, const char *b) {
    size_t i;
    if (strlen(b) != alen) return 0;
    for (i = 0; i < alen; ++i)
        if (tolower((unsigned char)a[i]) != tolower((unsigned char)b[i])) return 0;
    return 1;
}

static void openspm_ini_trim_span(const char **b, const char **e) {
    while (*b < *e && (**b == ' ' || **b == '\t')) ++*b;
    while (*e > *b && ((*e)[-1] == ' ' || (*e)[-1] == '\t' || (*e)[-1] == '\r')) --*e;
}

/* Walks `text` line by line. For the line holding `key` in `[section]`, sets
   the line and value spans and returns 1. Otherwise returns 0 and
   sets *insert_at to the offset after the section's last setting (or its
   header), or to SIZE_MAX when the section is missing. */
static int openspm_ini_find(const char *text, const char *section, const char *key, size_t *line_b, size_t *line_e,
                            const char **val_b, const char **val_e, size_t *insert_at) {
    const char *cursor = text;
    int in_section = 0;
    *insert_at = (size_t)-1;
    if ((unsigned char)cursor[0] == 0xEF && (unsigned char)cursor[1] == 0xBB && (unsigned char)cursor[2] == 0xBF)
        cursor += 3;
    while (*cursor) {
        const char *b = cursor, *e = cursor + strcspn(cursor, "\n"), *next = *e ? e + 1 : e;
        const char *tb = b, *te = e;
        openspm_ini_trim_span(&tb, &te);
        cursor = next;
        if (tb == te || *tb == ';' || *tb == '#') continue;
        if (*tb == '[') {
            const char *close = memchr(tb, ']', (size_t)(te - tb));
            const char *nb = tb + 1, *ne = close ? close : te;
            openspm_ini_trim_span(&nb, &ne);
            in_section = openspm_ini_name_eq(nb, (size_t)(ne - nb), section);
            if (in_section) *insert_at = (size_t)(next - text);
            continue;
        }
        if (!in_section) continue;
        *insert_at = (size_t)(next - text);
        {
            const char *eq = memchr(tb, '=', (size_t)(te - tb));
            const char *kb = tb, *ke = eq ? eq : te;
            if (!eq) continue;
            openspm_ini_trim_span(&kb, &ke);
            if (!openspm_ini_name_eq(kb, (size_t)(ke - kb), key)) continue;
            *line_b = (size_t)(b - text);
            *line_e = (size_t)(e - text);
            if (*line_e > *line_b && text[*line_e - 1] == '\r') --*line_e;
            *val_b = eq + 1;
            *val_e = te;
            openspm_ini_trim_span(val_b, val_e);
            return 1;
        }
    }
    return 0;
}

/* Value of `key` in `[section]`, trimmed. Unlike the display settings no
   inline comment is stripped (a path may contain ';' or '#'); surrounding
   double quotes are removed. Returns 1 when the value is non-empty. */
static int openspm_ini_get_raw(const char *text, const char *section, const char *key, char *out, size_t cap) {
    size_t lb, le, ins, len;
    const char *vb, *ve;
    if (cap) out[0] = '\0';
    if (!text || !cap || !openspm_ini_find(text, section, key, &lb, &le, &vb, &ve, &ins)) return 0;
    if (ve - vb >= 2 && *vb == '"' && ve[-1] == '"') { ++vb; --ve; }
    len = (size_t)(ve - vb);
    if (len >= cap) return 0;
    memcpy(out, vb, len);
    out[len] = '\0';
    return len != 0;
}

/* New text with `key = value` in `[section]`, keeping every other line and
   comment; adds the key or the section when missing. Caller frees. */
static char *openspm_ini_set_raw(const char *text, const char *section, const char *key, const char *value) {
    size_t lb, le, ins, tlen, cap, pos;
    const char *vb, *ve;
    char *out;
    if (!text) text = "";
    tlen = strlen(text);
    cap = tlen + strlen(section) + strlen(key) + strlen(value) + 16u;
    out = (char *)malloc(cap);
    if (!out) return NULL;
    if (openspm_ini_find(text, section, key, &lb, &le, &vb, &ve, &ins)) {
        snprintf(out, cap, "%.*s%s = %s%s", (int)lb, text, key, value, text + le);
        return out;
    }
    if (ins != (size_t)-1) {
        int need_nl = ins > 0 && text[ins - 1] != '\n';
        pos = (size_t)snprintf(out, cap, "%.*s%s%s = %s\r\n", (int)ins, text, need_nl ? "\r\n" : "", key, value);
        snprintf(out + pos, cap - pos, "%s", text + ins);
        return out;
    }
    snprintf(out, cap, "%s%s%s[%s]\r\n%s = %s\r\n", text, (tlen && text[tlen - 1] != '\n') ? "\r\n" : "",
             tlen ? "\r\n" : "", section, key, value);
    return out;
}

#endif
