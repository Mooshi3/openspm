#ifndef OPENSPM_ABI_H
#define OPENSPM_ABI_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#if defined(_WIN32)
#define OPENSPM_EXPORT __declspec(dllexport)
#define OPENSPM_CALL __cdecl
#else
#define OPENSPM_EXPORT __attribute__((visibility("default")))
#define OPENSPM_CALL
#endif

#define OPENSPM_ABI_VERSION 1u
#define OPENSPM_ABI_MIN_VERSION 1u

typedef struct openspm_abi_header {
    uint32_t struct_size;
    uint32_t abi_version;
} openspm_abi_header;

typedef enum openspm_result {
    OPENSPM_OK = 0,
    OPENSPM_ERROR_INVALID_ARGUMENT = -1,
    OPENSPM_ERROR_ABI_MISMATCH = -2,
    OPENSPM_ERROR_NOT_FOUND = -3,
    OPENSPM_ERROR_LOAD_FAILED = -4,
    OPENSPM_ERROR_UNSUPPORTED = -5,
    OPENSPM_ERROR_RUNTIME = -6,
    OPENSPM_ERROR_SOURCE_MISMATCH = -7
} openspm_result;

typedef enum openspm_log_level {
    OPENSPM_LOG_DEBUG = 0,
    OPENSPM_LOG_INFO = 1,
    OPENSPM_LOG_WARN = 2,
    OPENSPM_LOG_ERROR = 3
} openspm_log_level;

#ifdef __cplusplus
}
#endif

#endif
