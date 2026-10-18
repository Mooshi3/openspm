/* Unit test for backend/native/openspm_sysconf.h: the emulated SYSCONF image
 * and the SC lookup index that makes SCGetAspectRatio() report 16:9. */
#include "../backend/native/openspm_sysconf.h"

#include <assert.h>
#include <stdio.h>
#include <string.h>

static uint8_t g_buf[OPENSPM_SC_BUFFER_SIZE];

/* A synthetic console-style SYSCONF: IPL.CB (LONG), IPL.LNG (BYTE),
   IPL.NIK (SMALLARRAY), IPL.AR (BYTE, 4:3) in a 0x4000 file. */
static uint32_t build_synthetic(uint8_t *buf, uint32_t size) {
    static const struct {
        uint8_t type;
        const char *name;
        uint8_t data[8];
        uint32_t data_len;
    } items[] = {
        {OPENSPM_SC_TYPE_LONG, "IPL.CB", {0x12, 0x34, 0x56, 0x78}, 4},
        {OPENSPM_SC_TYPE_BYTE, "IPL.LNG", {1}, 1},
        {OPENSPM_SC_TYPE_SMALLARRAY, "IPL.NIK", {3, 'M', 'r', 'i', 'o'}, 5}, /* len-1 = 3, 4 bytes */
        {OPENSPM_SC_TYPE_BYTE, "IPL.AR", {OPENSPM_SC_ASPECT_4_3}, 1},
    };
    const uint32_t count = (uint32_t)(sizeof items / sizeof items[0]);
    uint32_t cursor = OPENSPM_SC_HEADER_SIZE + 2u * count + 2u, i;
    memset(buf, 0, size);
    memcpy(buf, "SCv0", 4);
    openspm_sc_put_be16(buf + 4, count);
    for (i = 0; i < count; ++i) {
        const size_t name_len = strlen(items[i].name);
        openspm_sc_put_be16(buf + 6 + 2 * i, cursor);
        buf[cursor] = (uint8_t)(items[i].type | (name_len - 1u));
        memcpy(buf + cursor + 1, items[i].name, name_len);
        memcpy(buf + cursor + 1 + name_len, items[i].data, items[i].data_len);
        cursor += 1u + (uint32_t)name_len + items[i].data_len;
    }
    openspm_sc_put_be16(buf + 6 + 2 * count, cursor);
    memcpy(buf + size - 4, "SCed", 4);
    return cursor;
}

int main(void) {
    static const openspm_sc_item_id ids[] = {{"IPL.CB", 0}, {"IPL.AR", 1}, {"IPL.LNG", 11}};
    openspm_sc_runtime rt;
    uint8_t value = 0xFF, type = 0;
    uint32_t data, entry = 99, end;

    /* Empty image (what the backend starts from). */
    assert(openspm_sysconf_init_empty(g_buf, sizeof g_buf));
    assert(openspm_sysconf_valid(g_buf, sizeof g_buf));
    assert(!openspm_sysconf_get_u8(g_buf, sizeof g_buf, "IPL.AR", &value));
    assert(openspm_sysconf_set_u8(g_buf, sizeof g_buf, "IPL.AR", OPENSPM_SC_ASPECT_16_9));
    assert(openspm_sysconf_valid(g_buf, sizeof g_buf));
    assert(openspm_sc_be16(g_buf + 4) == 1u);
    assert(openspm_sc_be16(g_buf + 6) == 10u && openspm_sc_be16(g_buf + 8) == 18u); /* 6 + 2*2; 8-byte item */
    assert(g_buf[10] == 0x65 && memcmp(g_buf + 11, "IPL.AR", 6) == 0 && g_buf[17] == 1);
    assert(openspm_sysconf_get_u8(g_buf, sizeof g_buf, "IPL.AR", &value) && value == 1);

    /* Index as the SC parser leaves it: SCGetAspectRatio (id 1) finds the byte. */
    assert(openspm_sysconf_build_index(g_buf, sizeof g_buf, ids, 3, &rt));
    assert(rt.item_count == 1u && rt.index_offset == OPENSPM_SC_BUFFER_SIZE - 6u);
    assert(rt.free_bytes == OPENSPM_SC_BUFFER_SIZE - 4u - 68u - 18u);
    assert(openspm_sc_be16(g_buf + rt.index_offset - 2u) == 6u); /* entry 0's slot */
    assert(openspm_sc_be16(g_buf + rt.index_offset) == 0u);      /* IPL.CB absent */
    data = openspm_sysconf_lookup_indexed(g_buf, &rt, 35u, 1u);
    assert(data == 17u && g_buf[data] == OPENSPM_SC_ASPECT_16_9);
    assert(openspm_sysconf_lookup_indexed(g_buf, &rt, 35u, 0u) == 0u);
    assert(openspm_sysconf_lookup_indexed(g_buf, &rt, 35u, 11u) == 0u);
    assert(openspm_sysconf_lookup_indexed(g_buf, &rt, 1u, 1u) == 0u); /* id >= known ids */
    assert(openspm_sysconf_valid(g_buf, sizeof g_buf)); /* index lives in the reserved area */

    /* Patch an existing console-style SYSCONF in place. */
    end = build_synthetic(g_buf, sizeof g_buf);
    assert(openspm_sysconf_valid(g_buf, sizeof g_buf));
    assert(openspm_sysconf_get_u8(g_buf, sizeof g_buf, "IPL.AR", &value) && value == 0);
    assert(openspm_sysconf_find(g_buf, sizeof g_buf, "IPL.NIK", &type, &entry) != 0u);
    assert(type == OPENSPM_SC_TYPE_SMALLARRAY && entry == 2u);
    assert(openspm_sysconf_set_u8(g_buf, sizeof g_buf, "IPL.AR", OPENSPM_SC_ASPECT_16_9));
    assert(openspm_sc_be16(g_buf + 4) == 4u && openspm_sc_be16(g_buf + 6 + 8) == end); /* no growth */
    assert(openspm_sysconf_get_u8(g_buf, sizeof g_buf, "IPL.AR", &value) && value == 1);
    assert(openspm_sysconf_get_u8(g_buf, sizeof g_buf, "IPL.LNG", &value) && value == 1);
    assert(!openspm_sysconf_set_u8(g_buf, sizeof g_buf, "IPL.CB", 1)); /* LONG, not BYTE */
    assert(!openspm_sysconf_get_u8(g_buf, sizeof g_buf, "IPL.CB", &value));
    assert(openspm_sysconf_build_index(g_buf, sizeof g_buf, ids, 3, &rt) && rt.item_count == 4u);
    data = openspm_sysconf_lookup_indexed(g_buf, &rt, 35u, 1u);
    assert(data != 0u && g_buf[data] == 1u);
    data = openspm_sysconf_lookup_indexed(g_buf, &rt, 35u, 11u);
    assert(data != 0u && g_buf[data] == 1u && g_buf[data - 8] == (OPENSPM_SC_TYPE_BYTE | 6u));
    data = openspm_sysconf_lookup_indexed(g_buf, &rt, 35u, 0u);
    assert(data != 0u && g_buf[data] == 0x12);

    /* Appending to a populated image moves items behind the grown table. */
    build_synthetic(g_buf, sizeof g_buf);
    assert(openspm_sysconf_set_u8(g_buf, sizeof g_buf, "IPL.E60", 1));
    assert(openspm_sysconf_valid(g_buf, sizeof g_buf) && openspm_sc_be16(g_buf + 4) == 5u);
    assert(openspm_sysconf_get_u8(g_buf, sizeof g_buf, "IPL.E60", &value) && value == 1);
    assert(openspm_sysconf_get_u8(g_buf, sizeof g_buf, "IPL.AR", &value) && value == 0);
    assert(openspm_sysconf_find(g_buf, sizeof g_buf, "IPL.NIK", &type, NULL) != 0u);

    /* Malformed images are rejected and never modified. */
    build_synthetic(g_buf, sizeof g_buf);
    g_buf[0] = 'X';
    assert(!openspm_sysconf_valid(g_buf, sizeof g_buf));
    assert(!openspm_sysconf_set_u8(g_buf, sizeof g_buf, "IPL.AR", 1) && g_buf[0] == 'X');
    build_synthetic(g_buf, sizeof g_buf);
    openspm_sc_put_be16(g_buf + 6 + 2, 0x3000); /* non-contiguous offset */
    assert(!openspm_sysconf_valid(g_buf, sizeof g_buf));
    assert(!openspm_sysconf_build_index(g_buf, sizeof g_buf, ids, 3, &rt));
    assert(openspm_sysconf_init_empty(g_buf, sizeof g_buf));
    {
        static const openspm_sc_item_id too_big[] = {{"IPL.AR", 34}};
        assert(!openspm_sysconf_build_index(g_buf, sizeof g_buf, too_big, 1, &rt));
    }
    assert(!openspm_sysconf_init_empty(g_buf, 8));
    puts("PASS: emulated SYSCONF (IPL.AR) image and SC index");
    return 0;
}
