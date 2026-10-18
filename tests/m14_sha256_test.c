#include "openspm/sha256.h"

#include <stdio.h>
#include <string.h>

static int from_hex(const char *hex, unsigned char out[32]) {
    unsigned i;
    for (i = 0; i < 32u; ++i) {
        unsigned value;
        if (sscanf(hex + i * 2u, "%2x", &value) != 1) return 0;
        out[i] = (unsigned char)value;
    }
    return 1;
}

static int check_vector(const void *data, size_t size, const char *expected_hex) {
    unsigned char actual[32];
    unsigned char expected[32];
    if (!from_hex(expected_hex, expected)) return 0;
    openspm_sha256_bytes(data, size, actual);
    return memcmp(actual, expected, sizeof(actual)) == 0;
}

int main(void) {
    static const char abc[] = "abc";
    static const char long_message[] =
        "abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq";
    openspm_sha256_context streaming;
    unsigned char actual[32];
    unsigned char expected[32];

    if (!check_vector("", 0,
                      "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")) {
        fprintf(stderr, "empty SHA-256 vector failed\n");
        return 1;
    }
    if (!check_vector(abc, 3,
                      "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")) {
        fprintf(stderr, "abc SHA-256 vector failed\n");
        return 1;
    }
    if (!check_vector(long_message, sizeof(long_message) - 1u,
                      "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1")) {
        fprintf(stderr, "long SHA-256 vector failed\n");
        return 1;
    }

    openspm_sha256_init(&streaming);
    openspm_sha256_update(&streaming, "a", 1);
    openspm_sha256_update(&streaming, "b", 1);
    openspm_sha256_update(&streaming, "c", 1);
    openspm_sha256_final(&streaming, actual);
    if (!from_hex("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad", expected) ||
        memcmp(actual, expected, sizeof(actual)) != 0) {
        fprintf(stderr, "streaming SHA-256 vector failed\n");
        return 1;
    }

    puts("PASS m14_sha256_test");
    return 0;
}
