/* OpenSPM freestanding C runtime pieces for the player-local native module.
 *
 * See m14_freestanding_crt.h. This file is OpenSPM-authored. It is compiled
 * as a native object and never as LTO bitcode: the code generator may emit
 * calls to memset/memcpy/__chkstk after LTO has already internalized or
 * dropped bitcode definitions, so these must be ordinary COFF symbols.
 */
#include "m14_freestanding_crt.h"

/* ---- mem/str --------------------------------------------------------------
 * rep movsb/stosb: correct for every size, cannot be turned back into a call
 * to themselves by the optimizer, and fast on ERMSB CPUs. */
void *memset(void *dst, int value, size_t size) {
    void *out = dst;
    __asm__ volatile("rep stosb"
                     : "+D"(dst), "+c"(size)
                     : "a"(value)
                     : "memory");
    return out;
}

void *memcpy(void *__restrict dst, const void *__restrict src, size_t size) {
    void *out = dst;
    __asm__ volatile("rep movsb"
                     : "+D"(dst), "+S"(src), "+c"(size)
                     :
                     : "memory");
    return out;
}

void *memmove(void *dst, const void *src, size_t size) {
    unsigned char *d = (unsigned char *)dst;
    const unsigned char *s = (const unsigned char *)src;
    if (d <= s || d >= s + size) return memcpy(dst, src, size);
    d += size - 1u;
    s += size - 1u;
    __asm__ volatile("std\n\trep movsb\n\tcld"
                     : "+D"(d), "+S"(s), "+c"(size)
                     :
                     : "memory");
    return dst;
}

int memcmp(const void *a, const void *b, size_t size) {
    const unsigned char *x = (const unsigned char *)a;
    const unsigned char *y = (const unsigned char *)b;
    for (size_t i = 0; i < size; ++i) {
        if (x[i] != y[i]) return x[i] < y[i] ? -1 : 1;
    }
    return 0;
}

size_t strlen(const char *text) {
    size_t n = 0;
    while (text[n] != '\0') ++n;
    return n;
}

int strcmp(const char *a, const char *b) {
    const unsigned char *x = (const unsigned char *)a;
    const unsigned char *y = (const unsigned char *)b;
    while (*x != 0u && *x == *y) {
        ++x;
        ++y;
    }
    return (int)*x - (int)*y;
}

int strncmp(const char *a, const char *b, size_t size) {
    const unsigned char *x = (const unsigned char *)a;
    const unsigned char *y = (const unsigned char *)b;
    for (size_t i = 0; i < size; ++i) {
        if (x[i] != y[i] || x[i] == 0u) return (int)x[i] - (int)y[i];
    }
    return 0;
}

char *strcpy(char *__restrict dst, const char *__restrict src) {
    char *out = dst;
    while ((*dst++ = *src++) != '\0') {
    }
    return out;
}

/* ---- stdio: ucrtbase's formatting engine -------------------------------- */
OPENSPM_CRT_IMPORT int __stdio_common_vsprintf(unsigned long long options,
                                               char *buffer, size_t count,
                                               const char *format,
                                               void *locale, va_list args);
OPENSPM_CRT_IMPORT int __stdio_common_vfprintf(unsigned long long options,
                                               FILE *stream, const char *format,
                                               void *locale, va_list args);

/* _CRT_INTERNAL_PRINTF_STANDARD_SNPRINTF_BEHAVIOR: C99 truncation/return
 * semantics, exactly what MSVC's inline snprintf passes. */
#define OPENSPM_UCRT_PRINTF_STANDARD_SNPRINTF 0x0002ull

int vsnprintf(char *__restrict buffer, size_t size,
              const char *__restrict format, va_list args) {
    int result = __stdio_common_vsprintf(OPENSPM_UCRT_PRINTF_STANDARD_SNPRINTF,
                                         buffer, size, format, NULL, args);
    return result < 0 ? -1 : result;
}

int snprintf(char *__restrict buffer, size_t size,
             const char *__restrict format, ...) {
    va_list args;
    int result;
    va_start(args, format);
    result = vsnprintf(buffer, size, format, args);
    va_end(args);
    return result;
}

int vfprintf(FILE *__restrict stream, const char *__restrict format, va_list args) {
    return __stdio_common_vfprintf(0u, stream, format, NULL, args);
}

int fprintf(FILE *__restrict stream, const char *__restrict format, ...) {
    va_list args;
    int result;
    va_start(args, format);
    result = vfprintf(stream, format, args);
    va_end(args);
    return result;
}

/* ---- fenv: rounding mode on both SSE (MXCSR.RC, bits 13-14) and x87
 * (CW.RC, bits 10-11). FE_* are RC << 8, so RC = (mode >> 8) & 3. */
int fesetround(int mode) {
    unsigned int mxcsr;
    unsigned short cw;
    unsigned int rc;
    if ((mode & ~0x0300) != 0) return 1;
    rc = ((unsigned int)mode >> 8) & 3u;
    __asm__ volatile("stmxcsr %0" : "=m"(mxcsr));
    mxcsr = (mxcsr & ~(3u << 13)) | (rc << 13);
    __asm__ volatile("ldmxcsr %0" : : "m"(mxcsr));
    __asm__ volatile("fnstcw %0" : "=m"(cw));
    cw = (unsigned short)((cw & ~(3u << 10)) | (rc << 10));
    __asm__ volatile("fldcw %0" : : "m"(cw));
    return 0;
}

int fegetround(void) {
    unsigned int mxcsr;
    __asm__ volatile("stmxcsr %0" : "=m"(mxcsr));
    return (int)(((mxcsr >> 13) & 3u) << 8);
}

/* ---- toolchain contract symbols ------------------------------------------
 * _fltused: referenced by every MSVC-target object that uses floating point.
 * __chkstk: called by functions with frames over one page (rax = frame size);
 * it touches each page from the current stack pointer downward so the guard
 * page grows the stack in order, and leaves rsp unchanged (x64 contract). */
int _fltused = 0x9875;

__asm__(".text\n"
        ".globl __chkstk\n"
        ".p2align 4\n"
        "__chkstk:\n"
        "  pushq %rcx\n"
        "  pushq %rax\n"
        "  leaq 24(%rsp), %rcx\n"
        "  cmpq $0x1000, %rax\n"
        "  jb 2f\n"
        "1:\n"
        "  subq $0x1000, %rcx\n"
        "  testq %rcx, (%rcx)\n"
        "  subq $0x1000, %rax\n"
        "  cmpq $0x1000, %rax\n"
        "  ja 1b\n"
        "2:\n"
        "  subq %rax, %rcx\n"
        "  testq %rcx, (%rcx)\n"
        "  popq %rax\n"
        "  popq %rcx\n"
        "  retq\n");

/* No CRT startup: the host process has already initialized ucrtbase, and the
 * module has no C++ constructors or TLS callbacks. */
int WINAPI _DllMainCRTStartup(void *module, DWORD reason, void *reserved) {
    (void)module;
    (void)reason;
    (void)reserved;
    return TRUE;
}
