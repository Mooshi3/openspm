/* Native specialization of R8PE01's strcmp at 0x8025C9C8.
   Preserve the exact PPC scratch registers, CR/XER/CTR, return magnitude and
   generated cycle/yield boundaries. No libc strcmp, pointer cache or SMC bypass.
   Only direct MEM1 mappings already used by this module's mem_read* qualify;
   all other addresses and oversized searches use the original generated path. */
#include "generated.h"
#include <stdio.h>
#include <stdlib.h>

static int enabled = 1;
void openspm_configure_native_helpers(void)
{
    const char* value = getenv("OPENSPM_NATIVE_HELPERS");
    enabled = !value || *value != '0';
    fprintf(stderr, "[OPENSPM-NATIVE] strcmp=%s\n", enabled ? "on" : "off");
    fflush(stderr);
}

static const u8* mem1_span(CPUState* ctx, u32 address)
{
    u32 offset = address - GC_RAM_BASE;
    if (offset >= ctx->ram_size)
        offset = address - GC_RAM_UNCACHED;
    if (!ctx->ram || ctx->ram_size < 512u || offset > ctx->ram_size - 512u)
        return NULL;
    return ctx->ram + offset;
}

static u32 be_word(const u8* p)
{
    return ((u32)p[0] << 24) | ((u32)p[1] << 16) | ((u32)p[2] << 8) | p[3];
}
static u32 compare_signed(u32 value)
{
    return (s32)value < 0 ? 8u : value ? 4u : 2u;
}

/* 0: untouched/declined; 1: returned; 2: yielded to original continuation. */
int openspm_r8pe01_strcmp(CPUState* ctx)
{
    if (!enabled || ctx->exception)
        return 0;
    const u8* a = mem1_span(ctx, ctx->gpr[3]);
    const u8* b = mem1_span(ctx, ctx->gpr[4]);
    if (!a || !b)
        return 0;
    u32 r0 = ctx->gpr[0], r3 = ctx->gpr[3], r4 = ctx->gpr[4];
    u32 r5 = ctx->gpr[5], r6 = ctx->gpr[6], r7 = ctx->gpr[7], r8 = ctx->gpr[8];
    u32 ctr = ctx->ctr, xer = ctx->xer, cr0 = ctx->cr >> 28;
    u32 offset = 0, pc = ctx->lr & ~3u;
    s64 cycles = ctx->downcount;
    int result = 1;
#define CR_SIGNED(v) (cr0 = compare_signed(v) | (xer >> 31))
#define CR_UNSIGNED(x,y) (cr0 = ((x)<(y) ? 8u : (x)>(y) ? 4u : 2u) | (xer >> 31))
#define ADVANCE(n) do { offset += (n); r3 += (n); r4 += (n); if (offset > 508u) return 0; } while (0)
#define YIELD(address) do { if (cycles <= -(s64)DOLRECOMP_C_LOOP_CYCLE_BUDGET) { pc = (address); result = 2; goto commit; } } while (0)

    cycles -= 4;
    r5 = a[0]; r0 = r5 - b[0]; CR_SIGNED(r0);
    if (r0) goto unequal;
    cycles -= 4;
    r0 = r4 & 3u; r6 = r3 & 3u; CR_UNSIGNED(r0, r6);
    if (r0 != r6) goto byte_check;
    cycles -= 2; CR_SIGNED(r6);
    if (!r6) goto word_start;
    cycles -= 2; CR_SIGNED(r5);
    if (!r5) goto equal;
    cycles -= 5;
    r0 = 3u - r6;
    xer |= 0x20000000u; /* subfic carry is set for r6 in [1,3]. */
    ctr = r0; CR_SIGNED(r0);
    if (!r0) goto aligned;
align_loop:
    cycles -= 4; ADVANCE(1);
    r5 = a[offset]; r0 = r5 - b[offset]; CR_SIGNED(r0);
    if (r0) goto unequal;
    cycles -= 2; CR_SIGNED(r5);
    if (!r5) goto equal;
    cycles -= 1; --ctr;
    if (ctr) { YIELD(0x8025CA18u); goto align_loop; }
aligned:
    cycles -= 2; ADVANCE(1);
word_start:
    cycles -= 9;
    r7 = be_word(a + offset); r6 = 0x80808080u;
    r8 = be_word(b + offset);
    r5 = (r7 - 0x01010101u) & ~r7;
    r0 = r5 & r6; CR_SIGNED(r0);
    if (r0) goto word_tail;
    cycles -= 1;
    goto word_compare;
word_loop:
    cycles -= 6; ADVANCE(4);
    r7 = be_word(a + offset); r8 = be_word(b + offset);
    r5 = r7 - 0x01010000u;
    r0 = (r5 - 257u) & r6; CR_SIGNED(r0);
    if (r0) goto word_tail;
word_compare:
    cycles -= 2; CR_UNSIGNED(r7, r8);
    if (r7 == r8) { YIELD(0x8025CA74u); goto word_loop; }
word_tail:
    cycles -= 4;
    r5 = a[offset]; r0 = r5 - b[offset]; CR_SIGNED(r0);
    if (r0) goto unequal;
byte_check:
    cycles -= 2; CR_SIGNED(r5);
    if (!r5) goto equal;
byte_loop:
    cycles -= 4; ADVANCE(1);
    r5 = a[offset]; r0 = r5 - b[offset]; CR_SIGNED(r0);
    if (r0) goto unequal;
    cycles -= 2; CR_SIGNED(r5);
    if (r5) { YIELD(0x8025CABCu); goto byte_loop; }
equal:
    cycles -= 2; r3 = 0;
    goto commit;
unequal:
    cycles -= 2; r3 = r0;
commit:
    ctx->gpr[0] = r0; ctx->gpr[3] = r3; ctx->gpr[4] = r4;
    ctx->gpr[5] = r5; ctx->gpr[6] = r6; ctx->gpr[7] = r7; ctx->gpr[8] = r8;
    ctx->ctr = ctr; ctx->xer = xer;
    ctx->cr = (ctx->cr & 0x0fffffffu) | (cr0 << 28);
    ctx->downcount = cycles; ctx->pc = pc;
    return result;
#undef CR_SIGNED
#undef CR_UNSIGNED
#undef ADVANCE
#undef YIELD
}
