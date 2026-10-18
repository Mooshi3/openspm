#include "generated.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int openspm_r8pe01_strcmp(CPUState* ctx);
int openspm_r8pe01_shape_lookup_loop(CPUState* ctx);

static u32 seed = 0x51a9e123u;
static u32 random32(void)
{
    seed ^= seed << 13;
    seed ^= seed >> 17;
    seed ^= seed << 5;
    return seed;
}

/* ppc_native_region_available() treats the host-call return as "blocked" and
   requires external_rid to be marked handled. This test host exposes the same
   contract as the paired RecompCore patch. */
static int block_native_region = 0;
static bool region_query(CPUState* cpu, u32 address)
{
    if (address != PPC_HOST_CALL_NATIVE_REGION_QUERY)
        return false;
    cpu->external_rid = PPC_NATIVE_REGION_QUERY_HANDLED;
    return block_native_region != 0;
}

static void set_cr0_signed(CPUState* ctx, u32 value)
{
    u32 bits = (s32)value < 0 ? 8u : value ? 4u : 2u;
    bits |= (ctx->xer >> 31) & 1u;
    ctx->cr = (ctx->cr & 0x0fffffffu) | (bits << 28);
}

static void set_cr0_cmpw(CPUState* ctx, u32 left, u32 right)
{
    const s32 a = (s32)left;
    const s32 b = (s32)right;
    u32 bits = a < b ? 8u : a > b ? 4u : 2u;
    bits |= (ctx->xer >> 31) & 1u;
    ctx->cr = (ctx->cr & 0x0fffffffu) | (bits << 28);
}

/* Architectural reference for exact generated instructions 0x8004DD8C through
   0x8004DDB8. strcmp itself is the separately differential-tested M12 helper;
   this test proves that batching repeated calls preserves the surrounding PPC
   registers, CR/XER/CTR, cycle charges and generated yield continuations. */
static int reference_shape_loop(CPUState* ctx)
{
    for (;;) {
        ctx->downcount -= 3;
        ctx->gpr[3] = ctx->gpr[29];
        ctx->gpr[4] = ctx->gpr[31];
        ctx->lr = 0x8004DD98u;

        const int strcmp_result = openspm_r8pe01_strcmp(ctx);
        if (strcmp_result == 2)
            return 2;
        if (strcmp_result == 0) {
            ctx->pc = 0x8025C9C8u;
            return 2;
        }

        ctx->downcount -= 2;
        set_cr0_signed(ctx, ctx->gpr[3]);
        if (ctx->gpr[3] == 0) {
            ctx->downcount -= 2;
            ctx->gpr[3] = ctx->gpr[28];
            return 1;
        }

        ctx->downcount -= 2;
        ctx->gpr[28] += 1u;
        ctx->gpr[29] += 88u;
        ctx->downcount -= 2;
        set_cr0_cmpw(ctx, ctx->gpr[28], ctx->gpr[30]);
        if ((s32)ctx->gpr[28] >= (s32)ctx->gpr[30]) {
            ctx->downcount -= 1;
            ctx->gpr[3] = 0xffffffffu;
            return 1;
        }
        if (ctx->downcount <= -(s64)DOLRECOMP_C_LOOP_CYCLE_BUDGET) {
            ctx->pc = 0x8004DD8Cu;
            return 2;
        }
    }
}

int main(void)
{
    CPUState base;
    assert(cpu_init(&base));
    base.host_call = region_query;

    const s64 budgets[] = {-300, -256, -255, -100, -1, 0, 80, 10000};
    for (unsigned test = 0; test < 200000; ++test) {
        memset(base.ram, 0, base.ram_size);
        for (unsigned g = 0; g < 32; ++g)
            base.gpr[g] = random32();
        base.pc = 0x8004DD8Cu;
        base.lr = random32();
        base.ctr = random32();
        base.cr = random32();
        base.xer = random32();
        base.exception = 0;
        base.external_addr = random32();
        base.external_value = random32();
        base.external_rid = (u8)random32();
        base.downcount = budgets[test % (sizeof(budgets) / sizeof(*budgets))];

        u32 records = 0x10000u + (random32() % 0x10000u);
        records = (records + 3u) & ~3u;
        const u32 query = 0x40000u + (random32() % 0x10000u);
        const unsigned count = 1u + random32() % 48u;
        const unsigned match = random32() % (count + 3u);

        base.gpr[28] = 0;
        base.gpr[29] = GC_RAM_BASE + records;
        base.gpr[30] = count;
        base.gpr[31] = GC_RAM_BASE + query;

        char wanted[80];
        snprintf(wanted, sizeof(wanted), "shape_%08x_%u", random32(), random32() % 16u);
        strcpy((char*)base.ram + query, wanted);
        for (unsigned i = 0; i < count; ++i) {
            char candidate[80];
            if (i == match)
                strcpy(candidate, wanted);
            else
                snprintf(candidate, sizeof(candidate), "shape_%08x_%u", random32(), i);
            assert(strlen(candidate) < 88u);
            strcpy((char*)base.ram + records + i * 88u, candidate);
        }

        CPUState expected = base;
        CPUState actual = base;
        block_native_region = 0;
        const int expected_result = reference_shape_loop(&expected);
        const int actual_result = openspm_r8pe01_shape_lookup_loop(&actual);
        if (expected_result != actual_result || memcmp(&expected, &actual, sizeof(actual)) != 0) {
            fprintf(stderr,
                    "shape-loop mismatch test=%u count=%u match=%u budget=%lld result=%d/%d\n",
                    test, count, match, (long long)base.downcount,
                    actual_result, expected_result);
            fprintf(stderr,
                    "pc=%08x/%08x downcount=%lld/%lld r3=%08x/%08x r28=%u/%u "
                    "r29=%08x/%08x cr=%08x/%08x\n",
                    actual.pc, expected.pc,
                    (long long)actual.downcount, (long long)expected.downcount,
                    actual.gpr[3], expected.gpr[3], actual.gpr[28], expected.gpr[28],
                    actual.gpr[29], expected.gpr[29], actual.cr, expected.cr);
            abort();
        }
    }

    /* The optimization must be completely inert if the runtime cannot prove
       the strcmp region is currently native/verified. */
    CPUState guarded = base;
    block_native_region = 1;
    CPUState guarded_before = guarded;
    assert(openspm_r8pe01_shape_lookup_loop(&guarded) == 0);
    assert(memcmp(&guarded, &guarded_before, sizeof(guarded)) == 0);

    guarded = base;
    guarded.host_call = NULL;
    guarded_before = guarded;
    assert(openspm_r8pe01_shape_lookup_loop(&guarded) == 0);
    assert(memcmp(&guarded, &guarded_before, sizeof(guarded)) == 0);

    printf("PASS: 200000 shape-loop differential cases; verified-region guard falls back cleanly\n");
    cpu_free(&base);
    return 0;
}
