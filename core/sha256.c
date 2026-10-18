#include "openspm/sha256.h"

#include <string.h>

static const uint32_t k[64] = {
    UINT32_C(0x428a2f98), UINT32_C(0x71374491), UINT32_C(0xb5c0fbcf), UINT32_C(0xe9b5dba5),
    UINT32_C(0x3956c25b), UINT32_C(0x59f111f1), UINT32_C(0x923f82a4), UINT32_C(0xab1c5ed5),
    UINT32_C(0xd807aa98), UINT32_C(0x12835b01), UINT32_C(0x243185be), UINT32_C(0x550c7dc3),
    UINT32_C(0x72be5d74), UINT32_C(0x80deb1fe), UINT32_C(0x9bdc06a7), UINT32_C(0xc19bf174),
    UINT32_C(0xe49b69c1), UINT32_C(0xefbe4786), UINT32_C(0x0fc19dc6), UINT32_C(0x240ca1cc),
    UINT32_C(0x2de92c6f), UINT32_C(0x4a7484aa), UINT32_C(0x5cb0a9dc), UINT32_C(0x76f988da),
    UINT32_C(0x983e5152), UINT32_C(0xa831c66d), UINT32_C(0xb00327c8), UINT32_C(0xbf597fc7),
    UINT32_C(0xc6e00bf3), UINT32_C(0xd5a79147), UINT32_C(0x06ca6351), UINT32_C(0x14292967),
    UINT32_C(0x27b70a85), UINT32_C(0x2e1b2138), UINT32_C(0x4d2c6dfc), UINT32_C(0x53380d13),
    UINT32_C(0x650a7354), UINT32_C(0x766a0abb), UINT32_C(0x81c2c92e), UINT32_C(0x92722c85),
    UINT32_C(0xa2bfe8a1), UINT32_C(0xa81a664b), UINT32_C(0xc24b8b70), UINT32_C(0xc76c51a3),
    UINT32_C(0xd192e819), UINT32_C(0xd6990624), UINT32_C(0xf40e3585), UINT32_C(0x106aa070),
    UINT32_C(0x19a4c116), UINT32_C(0x1e376c08), UINT32_C(0x2748774c), UINT32_C(0x34b0bcb5),
    UINT32_C(0x391c0cb3), UINT32_C(0x4ed8aa4a), UINT32_C(0x5b9cca4f), UINT32_C(0x682e6ff3),
    UINT32_C(0x748f82ee), UINT32_C(0x78a5636f), UINT32_C(0x84c87814), UINT32_C(0x8cc70208),
    UINT32_C(0x90befffa), UINT32_C(0xa4506ceb), UINT32_C(0xbef9a3f7), UINT32_C(0xc67178f2)
};

static uint32_t rotr32(uint32_t value, unsigned shift) {
    return (value >> shift) | (value << (32u - shift));
}

static uint32_t read_be32(const uint8_t *p) {
    return ((uint32_t)p[0] << 24) |
           ((uint32_t)p[1] << 16) |
           ((uint32_t)p[2] << 8) |
           (uint32_t)p[3];
}

static void write_be32(uint8_t *p, uint32_t value) {
    p[0] = (uint8_t)(value >> 24);
    p[1] = (uint8_t)(value >> 16);
    p[2] = (uint8_t)(value >> 8);
    p[3] = (uint8_t)value;
}

static void transform(openspm_sha256_context *context, const uint8_t block[64]) {
    uint32_t w[64];
    uint32_t a, b, c, d, e, f, g, h;
    uint32_t i;

    for (i = 0; i < 16u; ++i) {
        w[i] = read_be32(block + i * 4u);
    }
    for (i = 16u; i < 64u; ++i) {
        uint32_t s0 = rotr32(w[i - 15u], 7u) ^ rotr32(w[i - 15u], 18u) ^ (w[i - 15u] >> 3u);
        uint32_t s1 = rotr32(w[i - 2u], 17u) ^ rotr32(w[i - 2u], 19u) ^ (w[i - 2u] >> 10u);
        w[i] = w[i - 16u] + s0 + w[i - 7u] + s1;
    }

    a = context->state[0];
    b = context->state[1];
    c = context->state[2];
    d = context->state[3];
    e = context->state[4];
    f = context->state[5];
    g = context->state[6];
    h = context->state[7];

    for (i = 0; i < 64u; ++i) {
        uint32_t s1 = rotr32(e, 6u) ^ rotr32(e, 11u) ^ rotr32(e, 25u);
        uint32_t choose = (e & f) ^ ((~e) & g);
        uint32_t t1 = h + s1 + choose + k[i] + w[i];
        uint32_t s0 = rotr32(a, 2u) ^ rotr32(a, 13u) ^ rotr32(a, 22u);
        uint32_t majority = (a & b) ^ (a & c) ^ (b & c);
        uint32_t t2 = s0 + majority;
        h = g;
        g = f;
        f = e;
        e = d + t1;
        d = c;
        c = b;
        b = a;
        a = t1 + t2;
    }

    context->state[0] += a;
    context->state[1] += b;
    context->state[2] += c;
    context->state[3] += d;
    context->state[4] += e;
    context->state[5] += f;
    context->state[6] += g;
    context->state[7] += h;
}

void openspm_sha256_init(openspm_sha256_context *context) {
    if (context == NULL) {
        return;
    }
    context->state[0] = UINT32_C(0x6a09e667);
    context->state[1] = UINT32_C(0xbb67ae85);
    context->state[2] = UINT32_C(0x3c6ef372);
    context->state[3] = UINT32_C(0xa54ff53a);
    context->state[4] = UINT32_C(0x510e527f);
    context->state[5] = UINT32_C(0x9b05688c);
    context->state[6] = UINT32_C(0x1f83d9ab);
    context->state[7] = UINT32_C(0x5be0cd19);
    context->total_bytes = 0;
    context->block_size = 0;
    memset(context->block, 0, sizeof(context->block));
}

void openspm_sha256_update(openspm_sha256_context *context, const void *data, size_t size) {
    const uint8_t *bytes = (const uint8_t *)data;

    if (context == NULL || (data == NULL && size != 0u)) {
        return;
    }
    context->total_bytes += (uint64_t)size;

    while (size != 0u) {
        size_t available = sizeof(context->block) - context->block_size;
        size_t take = size < available ? size : available;
        memcpy(context->block + context->block_size, bytes, take);
        context->block_size += take;
        bytes += take;
        size -= take;
        if (context->block_size == sizeof(context->block)) {
            transform(context, context->block);
            context->block_size = 0;
        }
    }
}

void openspm_sha256_final(openspm_sha256_context *context, uint8_t digest[32]) {
    uint64_t bit_length;
    uint32_t i;

    if (context == NULL || digest == NULL) {
        return;
    }
    bit_length = context->total_bytes * UINT64_C(8);

    context->block[context->block_size++] = 0x80u;
    if (context->block_size > 56u) {
        memset(context->block + context->block_size, 0, 64u - context->block_size);
        transform(context, context->block);
        context->block_size = 0;
    }
    memset(context->block + context->block_size, 0, 56u - context->block_size);
    context->block[56] = (uint8_t)(bit_length >> 56);
    context->block[57] = (uint8_t)(bit_length >> 48);
    context->block[58] = (uint8_t)(bit_length >> 40);
    context->block[59] = (uint8_t)(bit_length >> 32);
    context->block[60] = (uint8_t)(bit_length >> 24);
    context->block[61] = (uint8_t)(bit_length >> 16);
    context->block[62] = (uint8_t)(bit_length >> 8);
    context->block[63] = (uint8_t)bit_length;
    transform(context, context->block);

    for (i = 0; i < 8u; ++i) {
        write_be32(digest + i * 4u, context->state[i]);
    }
    memset(context, 0, sizeof(*context));
}

void openspm_sha256_bytes(const void *data, size_t size, uint8_t digest[32]) {
    openspm_sha256_context context;
    openspm_sha256_init(&context);
    openspm_sha256_update(&context, data, size);
    openspm_sha256_final(&context, digest);
}
