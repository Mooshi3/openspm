/* OpenSPM emulated Wii system configuration (SYSCONF) for the native backend.
 *
 * On a console the RVL SDK SC library reads /shared2/sys/SYSCONF (via NAND /
 * IOS) into a 0x4000-byte cache and parses it; SCGetAspectRatio() and the
 * other SC getters then look items up in that cache. OpenSPM hosts SCInit and
 * SCCheckStatus (state/m14-host-targets.json "settings"), so without help the
 * cache stays empty and every getter returns its default (4:3 for the aspect).
 *
 * This header builds an OpenSPM-owned SYSCONF image and the lookup index the
 * SC parser leaves behind, so the game's own SC getters find the items. It is
 * our implementation of the documented SYSCONF container format:
 *
 *   0x0000  "SCv0"
 *   0x0004  u16 item count N (big endian)
 *   0x0006  u16 offsets[N + 1]  item i starts at offsets[i]; offsets[N] = end
 *   ...     items: u8 (type << 5 | (name length - 1)), name, payload
 *           type 3 = BYTE (1), 4 = SHORT (2), 5 = LONG (4), 6 = LONGLONG (8),
 *           7 = BOOL (1), 1 = BIGARRAY (u16 len-1 + data), 2 = SMALLARRAY
 *           (u8 len-1 + data)
 *   size-4  "SCed"
 *
 * After a successful parse the SC library keeps (R8PE01, SDA r13 0x80574900):
 *   index   u16 per item id at (size - 6 - 2 * id): the offset of that item's
 *           entry in offsets[] (0 = absent); the 68 bytes below "SCed" are the
 *           reserved index area
 *   globals item count, free bytes before the index area, index offset, and
 *           the number of known ids (lookups need id < that number)
 *
 * Portable C (no Windows headers) so it is unit-tested on the host
 * (tests/m14_sysconf_test.c). Contains no game code or data. */
#ifndef OPENSPM_SYSCONF_H
#define OPENSPM_SYSCONF_H

#include <stdint.h>
#include <string.h>

#define OPENSPM_SC_BUFFER_SIZE UINT32_C(0x4000)
#define OPENSPM_SC_HEADER_SIZE UINT32_C(6)
#define OPENSPM_SC_TAIL_SIZE UINT32_C(4)
#define OPENSPM_SC_INDEX_AREA UINT32_C(68)
#define OPENSPM_SC_MAX_INDEXED_ID UINT32_C(33) /* (68 - 2) / 2: ids that fit the reserved area */

#define OPENSPM_SC_TYPE_BIGARRAY UINT8_C(0x20)
#define OPENSPM_SC_TYPE_SMALLARRAY UINT8_C(0x40)
#define OPENSPM_SC_TYPE_BYTE UINT8_C(0x60)
#define OPENSPM_SC_TYPE_SHORT UINT8_C(0x80)
#define OPENSPM_SC_TYPE_LONG UINT8_C(0xA0)
#define OPENSPM_SC_TYPE_LONGLONG UINT8_C(0xC0)
#define OPENSPM_SC_TYPE_BOOL UINT8_C(0xE0)

/* IPL.AR: 0 = 4:3, 1 = 16:9 (SCGetAspectRatio treats anything else as 4:3). */
#define OPENSPM_SC_ASPECT_4_3 UINT8_C(0)
#define OPENSPM_SC_ASPECT_16_9 UINT8_C(1)

typedef struct openspm_sc_item_id {
    const char *name;
    uint32_t id; /* SC item id the game's getters pass (IPL.AR = 1) */
} openspm_sc_item_id;

/* What the SC parser leaves in its globals for a parsed cache. */
typedef struct openspm_sc_runtime {
    uint32_t item_count;
    uint32_t free_bytes;
    uint32_t index_offset;
} openspm_sc_runtime;

static uint32_t openspm_sc_be16(const uint8_t *p) { return ((uint32_t)p[0] << 8) | p[1]; }

static void openspm_sc_put_be16(uint8_t *p, uint32_t v) {
    p[0] = (uint8_t)(v >> 8);
    p[1] = (uint8_t)v;
}

/* Length of the item at `offset` (header byte through payload), or 0 when it
   is malformed or runs past `limit`. */
static uint32_t openspm_sc_item_length(const uint8_t *buf, uint32_t offset, uint32_t limit) {
    uint32_t name_len, pos, data_len;
    uint8_t type;
    if (offset >= limit) return 0u;
    type = (uint8_t)(buf[offset] & 0xE0u);
    name_len = (uint32_t)(buf[offset] & 0x1Fu) + 1u;
    pos = offset + 1u + name_len;
    if (pos > limit) return 0u;
    switch (type) {
    case OPENSPM_SC_TYPE_BIGARRAY:
        if (pos + 2u > limit) return 0u;
        data_len = 2u + openspm_sc_be16(buf + pos) + 1u;
        break;
    case OPENSPM_SC_TYPE_SMALLARRAY:
        if (pos + 1u > limit) return 0u;
        data_len = 1u + (uint32_t)buf[pos] + 1u;
        break;
    case OPENSPM_SC_TYPE_BYTE:
    case OPENSPM_SC_TYPE_BOOL: data_len = 1u; break;
    case OPENSPM_SC_TYPE_SHORT: data_len = 2u; break;
    case OPENSPM_SC_TYPE_LONG: data_len = 4u; break;
    case OPENSPM_SC_TYPE_LONGLONG: data_len = 8u; break;
    default: return 0u;
    }
    if (pos + data_len > limit) return 0u;
    return 1u + name_len + data_len;
}

/* Same structural checks as the SC parser: magic, contiguous offsets, a valid
   item at each one, the end offset, and room for the index area. */
static int openspm_sysconf_valid(const uint8_t *buf, uint32_t size) {
    uint32_t count, i, cursor, limit;
    if (buf == NULL || size < 12u || size > OPENSPM_SC_BUFFER_SIZE) return 0;
    if (memcmp(buf, "SCv0", 4) != 0 || memcmp(buf + size - 4u, "SCed", 4) != 0) return 0;
    count = openspm_sc_be16(buf + 4);
    cursor = OPENSPM_SC_HEADER_SIZE + 2u * count + 2u;
    limit = size - OPENSPM_SC_TAIL_SIZE;
    if (cursor > limit) return 0;
    for (i = 0; i < count; ++i) {
        const uint32_t length = openspm_sc_item_length(buf, cursor, limit);
        if (openspm_sc_be16(buf + OPENSPM_SC_HEADER_SIZE + 2u * i) != cursor || length == 0u) return 0;
        cursor += length;
    }
    if (openspm_sc_be16(buf + OPENSPM_SC_HEADER_SIZE + 2u * count) != cursor) return 0;
    return cursor <= limit - OPENSPM_SC_INDEX_AREA;
}

/* An empty configuration filling `size` bytes (the SC library's own reset). */
static int openspm_sysconf_init_empty(uint8_t *buf, uint32_t size) {
    if (buf == NULL || size < 12u || size > OPENSPM_SC_BUFFER_SIZE) return 0;
    memset(buf, 0, size);
    memcpy(buf, "SCv0", 4);
    openspm_sc_put_be16(buf + OPENSPM_SC_HEADER_SIZE, OPENSPM_SC_HEADER_SIZE + 2u); /* end offset */
    memcpy(buf + size - 4u, "SCed", 4);
    return 1;
}

/* Finds item `name`; returns its data offset and sets its entry index. */
static uint32_t openspm_sysconf_find(const uint8_t *buf, uint32_t size, const char *name, uint8_t *out_type,
                                     uint32_t *out_entry) {
    uint32_t count, i;
    const size_t name_len = name != NULL ? strlen(name) : 0u;
    if (!openspm_sysconf_valid(buf, size) || name_len == 0u || name_len > 32u) return 0u;
    count = openspm_sc_be16(buf + 4);
    for (i = 0; i < count; ++i) {
        const uint32_t offset = openspm_sc_be16(buf + OPENSPM_SC_HEADER_SIZE + 2u * i);
        if ((uint32_t)(buf[offset] & 0x1Fu) + 1u != name_len || memcmp(buf + offset + 1u, name, name_len) != 0)
            continue;
        if (out_type != NULL) *out_type = (uint8_t)(buf[offset] & 0xE0u);
        if (out_entry != NULL) *out_entry = i;
        return offset + 1u + (uint32_t)name_len;
    }
    return 0u;
}

static int openspm_sysconf_get_u8(const uint8_t *buf, uint32_t size, const char *name, uint8_t *out_value) {
    uint8_t type = 0u;
    const uint32_t data = openspm_sysconf_find(buf, size, name, &type, NULL);
    if (data == 0u || type != OPENSPM_SC_TYPE_BYTE) return 0;
    if (out_value != NULL) *out_value = buf[data];
    return 1;
}

/* Sets BYTE item `name` to `value`, appending it when absent. Returns 0 (and
   leaves the image unchanged) when the image is invalid, the item exists with
   another type, or there is no room. */
static int openspm_sysconf_set_u8(uint8_t *buf, uint32_t size, const char *name, uint8_t value) {
    uint8_t type = 0u;
    uint32_t data, count, end, table_end, i, item_len;
    const size_t name_len = name != NULL ? strlen(name) : 0u;
    if (!openspm_sysconf_valid(buf, size) || name_len == 0u || name_len > 32u) return 0;
    data = openspm_sysconf_find(buf, size, name, &type, NULL);
    if (data != 0u) {
        if (type != OPENSPM_SC_TYPE_BYTE) return 0;
        buf[data] = value;
        return 1;
    }
    count = openspm_sc_be16(buf + 4);
    if (count >= 0xFFFFu) return 0;
    table_end = OPENSPM_SC_HEADER_SIZE + 2u * count + 2u;
    end = openspm_sc_be16(buf + OPENSPM_SC_HEADER_SIZE + 2u * count);
    item_len = 1u + (uint32_t)name_len + 1u;
    /* The table grows by one entry (2 bytes) and the new item goes at the end. */
    if (end + 2u + item_len > size - OPENSPM_SC_TAIL_SIZE - OPENSPM_SC_INDEX_AREA) return 0;
    memmove(buf + table_end + 2u, buf + table_end, end - table_end);
    for (i = 0; i <= count; ++i) {
        uint8_t *entry = buf + OPENSPM_SC_HEADER_SIZE + 2u * i;
        openspm_sc_put_be16(entry, openspm_sc_be16(entry) + 2u);
    }
    end += 2u;
    buf[end] = (uint8_t)(OPENSPM_SC_TYPE_BYTE | (uint8_t)(name_len - 1u));
    memcpy(buf + end + 1u, name, name_len);
    buf[end + 1u + name_len] = value;
    openspm_sc_put_be16(buf + OPENSPM_SC_HEADER_SIZE + 2u * (count + 1u), end + item_len);
    openspm_sc_put_be16(buf + 4, count + 1u);
    return 1;
}

/* Writes the lookup index the SC parser builds for a cache of `size` bytes and
   reports its globals. `ids` maps item names to the ids the game's getters use;
   names missing from the image stay absent (index entry 0). */
static int openspm_sysconf_build_index(uint8_t *buf, uint32_t size, const openspm_sc_item_id *ids, uint32_t id_count,
                                       openspm_sc_runtime *out) {
    uint32_t count, end, area, base, i;
    if (!openspm_sysconf_valid(buf, size) || (id_count != 0u && ids == NULL)) return 0;
    count = openspm_sc_be16(buf + 4);
    end = openspm_sc_be16(buf + OPENSPM_SC_HEADER_SIZE + 2u * count);
    area = size - OPENSPM_SC_TAIL_SIZE - OPENSPM_SC_INDEX_AREA;
    base = size - OPENSPM_SC_TAIL_SIZE - 2u;
    memset(buf + area, 0, OPENSPM_SC_INDEX_AREA);
    for (i = 0; i < id_count; ++i) {
        uint32_t entry = 0u;
        if (ids[i].id > OPENSPM_SC_MAX_INDEXED_ID) return 0;
        if (openspm_sysconf_find(buf, size, ids[i].name, NULL, &entry) == 0u) continue;
        openspm_sc_put_be16(buf + base - 2u * ids[i].id, OPENSPM_SC_HEADER_SIZE + 2u * entry);
    }
    if (out != NULL) {
        out->item_count = count;
        out->free_bytes = area - end;
        out->index_offset = base;
    }
    return 1;
}

/* Guest-side lookup the way the SC getters do it (used by the unit test to
   prove the index): returns the item's data offset, or 0 when absent. */
static uint32_t openspm_sysconf_lookup_indexed(const uint8_t *buf, const openspm_sc_runtime *rt, uint32_t known_ids,
                                               uint32_t id) {
    uint32_t entry, item;
    if (buf == NULL || rt == NULL || id >= known_ids || rt->index_offset == 0u || 2u * id > rt->index_offset)
        return 0u;
    entry = openspm_sc_be16(buf + rt->index_offset - 2u * id);
    if (entry == 0u) return 0u;
    item = openspm_sc_be16(buf + entry);
    return item + 1u + (uint32_t)(buf[item] & 0x1Fu) + 1u;
}

#endif
