/* OpenSPM freestanding C runtime surface for the player-local native module.
 *
 * Stage 2 of docs/RELEASE-PLAN.md builds openspm_local_native.dll without the
 * MSVC CRT headers or the Windows SDK. The bridge (legacy_c_adapter_v42.c.in
 * chain) and DolRecomp's cpu.c are compiled with clang in MSVC mode against
 * clang's own builtin headers (stdint/stddef/stdbool/stdatomic/limits/float/
 * stdarg/intrin/xmmintrin) plus the one-line wrappers in freestanding/include,
 * which all resolve here. Nothing in this file is copied from a Microsoft
 * header: the declarations are the C standard prototypes, and the few Win32
 * types/constants are the documented ABI values, pinned by static asserts.
 *
 * Implementations come from three places, none of them the MSVC toolchain:
 *   - m14_freestanding_crt.c: mem and str helpers, snprintf/vsnprintf/
 *     fprintf wrappers, fesetround, __chkstk, _fltused;
 *   - ucrtbase.dll (an OS component since Windows 10) through an import
 *     library that llvm-dlltool makes from m14_freestanding_ucrtbase.def;
 *   - kernel32.dll through m14_freestanding_kernel32.def.
 *
 * Only what the bridge and cpu.c actually reference is declared (census in
 * docs/RELEASE-PLAN.md, Stage 1/2 spike). A new CRT or Win32 call in the
 * bridge fails this build at compile time (undeclared identifier) rather
 * than at link time.
 */
#ifndef OPENSPM_M14_FREESTANDING_CRT_H
#define OPENSPM_M14_FREESTANDING_CRT_H

#if !defined(_WIN64) || !defined(_MSC_VER)
#error "m14_freestanding_crt.h targets x86_64-pc-windows-msvc (clang-cl or clang --target=x86_64-pc-windows-msvc)"
#endif

#include <stdarg.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define OPENSPM_CRT_IMPORT __declspec(dllimport)

/* ---- <string.h> (implemented in m14_freestanding_crt.c) ---------------- */
void *memset(void *dst, int value, size_t size);
void *memcpy(void *__restrict dst, const void *__restrict src, size_t size);
void *memmove(void *dst, const void *src, size_t size);
int memcmp(const void *a, const void *b, size_t size);
size_t strlen(const char *text);
int strcmp(const char *a, const char *b);
int strncmp(const char *a, const char *b, size_t size);
char *strcpy(char *__restrict dst, const char *__restrict src);

/* ---- <stdlib.h> (ucrtbase.dll) ------------------------------------------ */
OPENSPM_CRT_IMPORT void *calloc(size_t count, size_t size);
OPENSPM_CRT_IMPORT void *malloc(size_t size);
OPENSPM_CRT_IMPORT void free(void *block);
OPENSPM_CRT_IMPORT char *getenv(const char *name);
OPENSPM_CRT_IMPORT unsigned long long strtoull(const char *__restrict text,
                                               char **__restrict end, int base);
/* <malloc.h>: clang's builtin <mm_malloc.h> (via <xmmintrin.h>) wraps these. */
OPENSPM_CRT_IMPORT void *_aligned_malloc(size_t size, size_t alignment);
OPENSPM_CRT_IMPORT void _aligned_free(void *block);

/* ---- <stdio.h> ----------------------------------------------------------
 * The formatting engine is ucrtbase's __stdio_common_* family (the same one
 * MSVC's inline stdio.h wrappers call), so %llu/%X/%.3f/%p behave exactly as
 * in the MSVC build. FILE stays opaque and never crosses native_api.h. */
typedef struct _iobuf FILE;
OPENSPM_CRT_IMPORT FILE *__acrt_iob_func(unsigned index);
#define stdin (__acrt_iob_func(0))
#define stdout (__acrt_iob_func(1))
#define stderr (__acrt_iob_func(2))
int vsnprintf(char *__restrict buffer, size_t size,
              const char *__restrict format, va_list args);
int snprintf(char *__restrict buffer, size_t size,
             const char *__restrict format, ...);
int vfprintf(FILE *__restrict stream, const char *__restrict format, va_list args);
int fprintf(FILE *__restrict stream, const char *__restrict format, ...);

/* ---- <math.h> -------------------------------------------------------------
 * Classification is compiler-builtin; the arithmetic comes from ucrtbase. */
OPENSPM_CRT_IMPORT double floor(double x);
OPENSPM_CRT_IMPORT double ceil(double x);
OPENSPM_CRT_IMPORT double trunc(double x);
OPENSPM_CRT_IMPORT double fmod(double x, double y);
OPENSPM_CRT_IMPORT double fma(double x, double y, double z);
OPENSPM_CRT_IMPORT double ldexp(double x, int exponent);
OPENSPM_CRT_IMPORT double sqrt(double x);
OPENSPM_CRT_IMPORT double sin(double x); /* AX HLE SRC tables (backend/audio) */
static __inline double fabs(double x) { return __builtin_fabs(x); }
static __inline float fabsf(float x) { return __builtin_fabsf(x); }
static __inline double copysign(double x, double y) { return __builtin_copysign(x, y); }
static __inline float copysignf(float x, float y) { return __builtin_copysignf(x, y); }
#define INFINITY (__builtin_inff())
#define NAN (__builtin_nanf(""))
#define HUGE_VAL (__builtin_huge_val())
#define FP_INFINITE 1
#define FP_NAN 2
#define FP_NORMAL (-1)
#define FP_SUBNORMAL (-2)
#define FP_ZERO 0
#define fpclassify(x) \
    __builtin_fpclassify(FP_NAN, FP_INFINITE, FP_NORMAL, FP_SUBNORMAL, FP_ZERO, (x))
#define isnan(x) __builtin_isnan(x)
#define isinf(x) __builtin_isinf(x)
#define isfinite(x) __builtin_isfinite(x)
#define signbit(x) __builtin_signbit(x)

/* ---- <fenv.h> -------------------------------------------------------------
 * Implemented in m14_freestanding_crt.c directly on MXCSR and the x87 control
 * word, so these values only need to be self-consistent. They equal UCRT's
 * _RC_* encodings (RC field << 8). */
#define FE_TONEAREST 0x0000
#define FE_DOWNWARD 0x0100
#define FE_UPWARD 0x0200
#define FE_TOWARDZERO 0x0300
int fesetround(int mode);
int fegetround(void);

/* ---- <setjmp.h> -----------------------------------------------------------
 * Only the type: clang's builtin <intrin.h> names jmp_buf in hosted mode.
 * Neither the bridge nor cpu.c calls setjmp/longjmp. */
typedef struct __declspec(align(16)) _OPENSPM_JBTYPE {
    unsigned long long part[2];
} _JBTYPE;
typedef _JBTYPE jmp_buf[16];

/* ---- <windows.h> subset (kernel32.dll) ----------------------------------- */
#define WINAPI __stdcall
#define CALLBACK __stdcall
#define TRUE 1
#define FALSE 0
typedef int BOOL;
typedef unsigned char BYTE;
typedef unsigned short WORD;
typedef unsigned long DWORD;
typedef long LONG;
typedef unsigned long ULONG;
typedef unsigned long long ULONG_PTR;
typedef unsigned long long SIZE_T;
typedef void *PVOID;
typedef void *LPVOID;
typedef const void *LPCVOID;
typedef void *HANDLE;
typedef struct HINSTANCE__ *HMODULE;
typedef const unsigned short *LPCWSTR;
typedef DWORD *LPDWORD;
typedef struct _SECURITY_ATTRIBUTES *LPSECURITY_ATTRIBUTES;
typedef DWORD(WINAPI *LPTHREAD_START_ROUTINE)(LPVOID parameter);
typedef long long(WINAPI *FARPROC)(void);

typedef struct _EXCEPTION_RECORD {
    DWORD ExceptionCode;
    DWORD ExceptionFlags;
    struct _EXCEPTION_RECORD *ExceptionRecord;
    PVOID ExceptionAddress;
    DWORD NumberParameters;
    ULONG_PTR ExceptionInformation[15];
} EXCEPTION_RECORD, *PEXCEPTION_RECORD;
typedef struct _CONTEXT CONTEXT, *PCONTEXT; /* opaque: never dereferenced */
typedef struct _EXCEPTION_POINTERS {
    PEXCEPTION_RECORD ExceptionRecord;
    PCONTEXT ContextRecord;
} EXCEPTION_POINTERS, *PEXCEPTION_POINTERS;
typedef LONG(WINAPI *PVECTORED_EXCEPTION_HANDLER)(EXCEPTION_POINTERS *info);
#define EXCEPTION_CONTINUE_SEARCH 0

typedef struct _MEMORY_BASIC_INFORMATION {
    PVOID BaseAddress;
    PVOID AllocationBase;
    DWORD AllocationProtect;
    WORD PartitionId;
    SIZE_T RegionSize;
    DWORD State;
    DWORD Protect;
    DWORD Type;
} MEMORY_BASIC_INFORMATION, *PMEMORY_BASIC_INFORMATION;

_Static_assert(sizeof(EXCEPTION_RECORD) == 152, "EXCEPTION_RECORD x64 ABI");
_Static_assert(offsetof(EXCEPTION_RECORD, ExceptionAddress) == 16,
               "EXCEPTION_RECORD.ExceptionAddress x64 ABI");
_Static_assert(sizeof(MEMORY_BASIC_INFORMATION) == 48,
               "MEMORY_BASIC_INFORMATION x64 ABI (VirtualQuery checks the size)");
_Static_assert(offsetof(MEMORY_BASIC_INFORMATION, AllocationBase) == 8,
               "MEMORY_BASIC_INFORMATION.AllocationBase x64 ABI");
_Static_assert(sizeof(DWORD) == 4 && sizeof(LONG) == 4, "LLP64");

#define INFINITE 0xFFFFFFFFu
#define WAIT_OBJECT_0 0x00000000u
#define WAIT_TIMEOUT 0x00000102u
#define GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT 0x00000002u
#define GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS 0x00000004u

OPENSPM_CRT_IMPORT PVOID WINAPI AddVectoredExceptionHandler(
    ULONG first, PVECTORED_EXCEPTION_HANDLER handler);
OPENSPM_CRT_IMPORT ULONG WINAPI RemoveVectoredExceptionHandler(PVOID handle);
OPENSPM_CRT_IMPORT BOOL WINAPI CloseHandle(HANDLE handle);
OPENSPM_CRT_IMPORT HANDLE WINAPI CreateEventW(LPSECURITY_ATTRIBUTES attributes,
                                              BOOL manual_reset,
                                              BOOL initial_state,
                                              LPCWSTR name);
OPENSPM_CRT_IMPORT BOOL WINAPI SetEvent(HANDLE event);
OPENSPM_CRT_IMPORT HANDLE WINAPI CreateThread(LPSECURITY_ATTRIBUTES attributes,
                                              SIZE_T stack_size,
                                              LPTHREAD_START_ROUTINE start,
                                              LPVOID parameter,
                                              DWORD flags,
                                              LPDWORD thread_id);
OPENSPM_CRT_IMPORT DWORD WINAPI WaitForSingleObject(HANDLE handle, DWORD milliseconds);
OPENSPM_CRT_IMPORT DWORD WINAPI GetCurrentThreadId(void);
OPENSPM_CRT_IMPORT DWORD WINAPI GetEnvironmentVariableA(const char *name,
                                                        char *buffer,
                                                        DWORD size);
OPENSPM_CRT_IMPORT BOOL WINAPI GetModuleHandleExW(DWORD flags, LPCWSTR name,
                                                  HMODULE *module);
/* Renderer exports (openspm_renderer_audio_push, m14_audio_bridge.h). */
OPENSPM_CRT_IMPORT HMODULE WINAPI GetModuleHandleA(const char *name);
OPENSPM_CRT_IMPORT FARPROC WINAPI GetProcAddress(HMODULE module, const char *name);
OPENSPM_CRT_IMPORT SIZE_T WINAPI VirtualQuery(LPCVOID address,
                                              PMEMORY_BASIC_INFORMATION buffer,
                                              SIZE_T length);
OPENSPM_CRT_IMPORT void WINAPI Sleep(DWORD milliseconds);

/* Interlocked*: clang provides the MSVC _Interlocked* family as builtins in
 * MSVC mode, so no import and no <intrin.h> dependency. */
long _InterlockedCompareExchange(long volatile *target, long exchange, long comparand);
long _InterlockedExchange(long volatile *target, long value);
long _InterlockedExchangeAdd(long volatile *target, long value);
long _InterlockedIncrement(long volatile *target);
long _InterlockedDecrement(long volatile *target);
#define InterlockedCompareExchange _InterlockedCompareExchange
#define InterlockedExchange _InterlockedExchange
#define InterlockedExchangeAdd _InterlockedExchangeAdd
#define InterlockedIncrement _InterlockedIncrement
#define InterlockedDecrement _InterlockedDecrement
#define YieldProcessor() __builtin_ia32_pause()

#ifdef __cplusplus
}
#endif

#endif /* OPENSPM_M14_FREESTANDING_CRT_H */
