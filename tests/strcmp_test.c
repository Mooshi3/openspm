#include "generated.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

void original_strcmp(CPUState*);
int openspm_r8pe01_strcmp(CPUState*);
static u32 seed = 0x19982007u;
static u32 random32(void) { seed ^= seed << 13; seed ^= seed >> 17; seed ^= seed << 5; return seed; }

int main(void)
{
    CPUState ctx;
    assert(cpu_init(&ctx));
    unsigned native = 0, yielded = 0, declined = 0;
    const s64 budgets[] = {-300, -256, -250, -20, 0, 100, 1000, 100000};
    for (unsigned i = 0; i < 100000; ++i) {
        for (unsigned g = 0; g < 32; ++g) ctx.gpr[g] = random32();
        ctx.cr = random32(); ctx.xer = random32(); ctx.ctr = random32();
        ctx.lr = random32(); ctx.pc = 0x8025c9c8u; ctx.exception = 0;
        ctx.downcount = budgets[i % 8];
        const u32 x = 0x1000u + (i % 4), y = 0x2000u + (i / 4 % 4);
        const unsigned length = i % 4 ? random32() % 128 : random32() % 700;
        for (unsigned k = 0; k < 1024; ++k) {
            ctx.ram[x+k] = 1 + random32() % 255;
            ctx.ram[y+k] = ctx.ram[x+k];
        }
        ctx.ram[x+length] = 0; ctx.ram[y+length] = 0;
        if (i % 3 && length) ctx.ram[y+random32()%length] ^= 0x55u;
        ctx.gpr[3] = (i & 32 ? GC_RAM_UNCACHED : GC_RAM_BASE) + x;
        ctx.gpr[4] = (i & 64 ? GC_RAM_UNCACHED : GC_RAM_BASE) + y;
        CPUState before = ctx, expected = ctx;
        original_strcmp(&expected);
        int result = openspm_r8pe01_strcmp(&ctx);
        if (!result) {
            assert(!memcmp(&ctx, &before, sizeof(ctx)));
            ++declined;
            continue;
        }
        if (memcmp(&ctx, &expected, sizeof(ctx))) {
            fprintf(stderr, "Mismatch case=%u length=%u align=%u/%u budget=%lld result=%d\n",
                    i, length, x&3u, y&3u, (long long)before.downcount, result);
            for (unsigned g = 0; g < 32; ++g)
                if (ctx.gpr[g] != expected.gpr[g])
                    fprintf(stderr, "r%u got=%08x expected=%08x\n", g, ctx.gpr[g], expected.gpr[g]);
            fprintf(stderr, "cr=%08x/%08x xer=%08x/%08x ctr=%08x/%08x cycles=%lld/%lld pc=%08x/%08x\n",
                    ctx.cr,expected.cr,ctx.xer,expected.xer,ctx.ctr,expected.ctr,
                    (long long)ctx.downcount,(long long)expected.downcount,ctx.pc,expected.pc);
            abort();
        }
        ++native; yielded += result == 2;
    }
    /* A long equal search must decline without changing any architectural
       state; the caller then executes the original function. */
    memset(ctx.ram+0x1000, 'a', 800);
    memset(ctx.ram+0x2000, 'a', 800);
    ctx.ram[0x1000+799] = 0; ctx.ram[0x2000+799] = 0;
    ctx.gpr[3] = GC_RAM_BASE+0x1000; ctx.gpr[4] = GC_RAM_BASE+0x2000;
    ctx.downcount = 100000;
    CPUState long_before = ctx;
    assert(!openspm_r8pe01_strcmp(&ctx));
    assert(!memcmp(&ctx, &long_before, sizeof(ctx)));
    const u32 bad_addresses[] = {0, 0xcc008000u, 0x90000000u, 0x817fffffu, 0xffffffffu};
    for (unsigned i = 0; i < sizeof(bad_addresses)/sizeof(*bad_addresses); ++i) {
        ctx.gpr[3] = bad_addresses[i];
        CPUState before = ctx;
        assert(!openspm_r8pe01_strcmp(&ctx));
        assert(!memcmp(&ctx, &before, sizeof(ctx)));
    }
    printf("PASS: 100000 differential cases; native=%u yields=%u conservative_declines=%u; full CPU state/cycles match\n",
           native, yielded, declined);
    /* Representative repeated-prefix name lookup, not a game FPS claim. */
    strcpy((char*)ctx.ram+0x1000, "Mario_body_shape_left");
    strcpy((char*)ctx.ram+0x2000, "Mario_body_shape_right");
    const unsigned repetitions = 2000000;
    clock_t start = clock();
    for (unsigned i = 0; i < repetitions; ++i) {
        ctx.gpr[3] = GC_RAM_BASE+0x1000; ctx.gpr[4] = GC_RAM_BASE+0x2000;
        ctx.downcount = 1000; original_strcmp(&ctx);
    }
    double original_ms = (double)(clock()-start)*1000/CLOCKS_PER_SEC;
    start = clock();
    for (unsigned i = 0; i < repetitions; ++i) {
        ctx.gpr[3] = GC_RAM_BASE+0x1000; ctx.gpr[4] = GC_RAM_BASE+0x2000;
        ctx.downcount = 1000; assert(openspm_r8pe01_strcmp(&ctx));
    }
    double native_ms = (double)(clock()-start)*1000/CLOCKS_PER_SEC;
    printf("Microbenchmark: original=%.2fms native=%.2fms ratio=%.2fx (%u calls; host only)\n",
           original_ms,native_ms,original_ms/native_ms,repetitions);
    cpu_free(&ctx);
    return 0;
}
