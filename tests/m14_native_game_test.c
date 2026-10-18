/* Host test for launcher/native_game.h: disc image identification and the
   OpenSPM.ini [Game] path round trip. Synthetic headers only. */
#include "../launcher/native_game.h"

#include <assert.h>
#include <stdio.h>
#include <string.h>

typedef struct mem_image {
    unsigned char bytes[0x200000];
    size_t size;
} mem_image;

static mem_image g_img;

static int mem_read(void *user, uint64_t offset, void *buffer, size_t size) {
    mem_image *img = (mem_image *)user;
    if (offset > img->size || size > img->size - offset) return 0;
    memcpy(buffer, img->bytes + offset, size);
    return 1;
}

static void put_be32(unsigned char *p, uint32_t v) {
    p[0] = (unsigned char)(v >> 24); p[1] = (unsigned char)(v >> 16);
    p[2] = (unsigned char)(v >> 8); p[3] = (unsigned char)v;
}

static void disc_header(unsigned char *h, const char *id, unsigned revision, int wii) {
    memset(h, 0, 0x100);
    memcpy(h, id, 6);
    h[7] = (unsigned char)revision;
    if (wii) put_be32(h + 0x18, 0x5D1C9EA3u);
    else put_be32(h + 0x1C, 0xC2339F3Du);
    memcpy(h + 0x20, "SUPER PAPER MARIO", 17);
}

static openspm_game_check probe(openspm_disc_info *info) {
    if (!openspm_image_probe(mem_read, &g_img, info)) return OPENSPM_GAME_UNREADABLE;
    return openspm_game_check_info(info);
}

static void test_images(void) {
    openspm_disc_info info;
    char msg[512];

    /* ISO */
    memset(&g_img, 0, sizeof g_img);
    g_img.size = 0x8000;
    disc_header(g_img.bytes, "R8PE01", 2, 1);
    assert(probe(&info) == OPENSPM_GAME_OK);
    assert(info.kind == OPENSPM_IMAGE_ISO && strcmp(info.game_id, "R8PE01") == 0 && info.revision == 2);
    assert(strcmp(info.title, "SUPER PAPER MARIO") == 0);

    disc_header(g_img.bytes, "R8PE01", 0, 1);
    assert(probe(&info) == OPENSPM_GAME_WRONG_REVISION);
    openspm_game_check_message(OPENSPM_GAME_WRONG_REVISION, &info, msg, sizeof msg);
    assert(strstr(msg, "revision 0") && strstr(msg, "revision 2"));

    disc_header(g_img.bytes, "R8PP01", 2, 1);
    assert(probe(&info) == OPENSPM_GAME_WRONG_GAME);
    disc_header(g_img.bytes, "GM4E01", 0, 0);
    assert(probe(&info) == OPENSPM_GAME_NOT_WII);
    memset(g_img.bytes, 0x5A, 0x100);
    assert(probe(&info) == OPENSPM_GAME_UNREADABLE);
    g_img.size = 0x10; /* truncated */
    assert(probe(&info) == OPENSPM_GAME_UNREADABLE);

    /* WBFS: 512-byte hd sectors, 2 MiB wbfs sectors; header copy of slot 0 at
       hd sector 1. */
    memset(&g_img, 0, sizeof g_img);
    g_img.size = sizeof g_img.bytes;
    memcpy(g_img.bytes, "WBFS", 4);
    put_be32(g_img.bytes + 4, 0x1000);
    g_img.bytes[8] = 9;
    g_img.bytes[9] = 21;
    g_img.bytes[12] = 1;
    disc_header(g_img.bytes + 512, "R8PE01", 2, 1);
    assert(probe(&info) == OPENSPM_GAME_OK && info.kind == OPENSPM_IMAGE_WBFS);

    /* Only slot 1 used: disc info size = align(0x100 + (286864 >> 6) * 2, 512)
       = 9728 bytes = 19 hd sectors, so slot 1 is at hd sector 20. */
    g_img.bytes[12] = 0;
    g_img.bytes[13] = 1;
    memset(g_img.bytes + 512, 0, 0x100);
    disc_header(g_img.bytes + 20u * 512u, "R8PE01", 1, 1);
    assert(probe(&info) == OPENSPM_GAME_WRONG_REVISION && info.kind == OPENSPM_IMAGE_WBFS);

    g_img.bytes[13] = 0; /* empty WBFS */
    assert(probe(&info) == OPENSPM_GAME_UNREADABLE);
    g_img.bytes[13] = 1;
    g_img.bytes[8] = 40; /* corrupt geometry */
    assert(probe(&info) == OPENSPM_GAME_UNREADABLE);

    /* WIA and RVZ: disc header copy at 0x58. */
    memset(&g_img, 0, sizeof g_img);
    g_img.size = 0x1000;
    memcpy(g_img.bytes, "WIA\x01", 4);
    disc_header(g_img.bytes + 0x58, "R8PE01", 2, 1);
    assert(probe(&info) == OPENSPM_GAME_OK && info.kind == OPENSPM_IMAGE_WIA);
    memcpy(g_img.bytes, "RVZ\x01", 4);
    assert(probe(&info) == OPENSPM_GAME_UNSUPPORTED_FORMAT && info.kind == OPENSPM_IMAGE_RVZ);
    openspm_game_check_message(OPENSPM_GAME_UNSUPPORTED_FORMAT, &info, msg, sizeof msg);
    assert(strstr(msg, "Convert File"));
}

static void test_ini(void) {
    char value[512];
    char *a, *b, *c, *d;
    const char *ini = "\xEF\xBB\xBF; comment\r\n[Display]\r\nwindow_mode = borderless\r\n\r\n[Game]\r\n"
                      "; where the game is\r\npath = \"C:\\Games\\SPM #1; US.wbfs\"\r\n\r\n[Mods]\r\nenabled = 1\r\n";
    assert(openspm_ini_get_raw(ini, "game", "PATH", value, sizeof value));
    assert(strcmp(value, "C:\\Games\\SPM #1; US.wbfs") == 0);
    assert(!openspm_ini_get_raw(ini, "Game", "wit", value, sizeof value) && value[0] == '\0');
    assert(!openspm_ini_get_raw(ini, "Display", "path", value, sizeof value));

    a = openspm_ini_set_raw(ini, "Game", "path", "D:\\SPM.iso");
    assert(a && strstr(a, "\r\npath = D:\\SPM.iso\r\n\r\n[Mods]"));
    assert(strstr(a, "; where the game is\r\n") && strstr(a, "window_mode = borderless"));
    assert(openspm_ini_get_raw(a, "Game", "path", value, sizeof value) && strcmp(value, "D:\\SPM.iso") == 0);

    b = openspm_ini_set_raw(a, "Game", "wit", "C:\\wit\\wit.exe"); /* new key after the last setting */
    assert(b && strstr(b, "path = D:\\SPM.iso\r\nwit = C:\\wit\\wit.exe\r\n\r\n[Mods]"));

    c = openspm_ini_set_raw("[Display]\r\naspect = 4:3", "Game", "path", "E:\\x.wbfs"); /* new section */
    assert(c && strcmp(c, "[Display]\r\naspect = 4:3\r\n\r\n[Game]\r\npath = E:\\x.wbfs\r\n") == 0);
    d = openspm_ini_set_raw("", "Game", "path", "E:\\x.wbfs");
    assert(d && strcmp(d, "[Game]\r\npath = E:\\x.wbfs\r\n") == 0);
    free(a); free(b); free(c); free(d);

    c = openspm_ini_set_raw("[Game]", "Game", "path", "F:\\y.iso"); /* header without newline */
    assert(c && strcmp(c, "[Game]\r\npath = F:\\y.iso\r\n") == 0);
    free(c);
}

int main(void) {
    test_images();
    test_ini();
    printf("PASS: m14_native_game_test\n");
    return 0;
}
