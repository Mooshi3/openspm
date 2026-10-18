#ifndef OPENSPM_SHA256_H
#define OPENSPM_SHA256_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct openspm_sha256_context {
    uint32_t state[8];
    uint64_t total_bytes;
    uint8_t block[64];
    size_t block_size;
} openspm_sha256_context;

void openspm_sha256_init(openspm_sha256_context *context);
void openspm_sha256_update(openspm_sha256_context *context, const void *data, size_t size);
void openspm_sha256_final(openspm_sha256_context *context, uint8_t digest[32]);
void openspm_sha256_bytes(const void *data, size_t size, uint8_t digest[32]);

#ifdef __cplusplus
}
#endif

#endif
