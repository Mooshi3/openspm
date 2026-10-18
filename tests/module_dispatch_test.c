/* Integration check against the linked game module, including entry dispatch
   and the original continuation on native-helper decline. */
#define _POSIX_C_SOURCE 200809L
#include "cpu/cpu.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

void original_strcmp(CPUState*);
void func_80259940(CPUState*);
void openspm_configure_native_helpers(void);

int main(void)
{
    CPUState cpu;
    assert(cpu_init(&cpu));
    const s64 budgets[] = {-300, -256, -20, 0, 100, 1000, 100000};
    unsigned cases = 0;
    for (unsigned enabled = 0; enabled < 2; ++enabled) {
        assert(!setenv("OPENSPM_NATIVE_HELPERS", enabled ? "1" : "0", 1));
        openspm_configure_native_helpers();
        for (unsigned i = 0; i < 10000; ++i) {
            const u32 a = 0x1000u + i % 4, b = 0x2000u + (i / 4) % 4;
            const unsigned length = i % 900;
            memset(cpu.ram + a, 'a', 1024);
            memset(cpu.ram + b, 'a', 1024);
            cpu.ram[a + length] = cpu.ram[b + length] = 0;
            if (i % 3 && length) cpu.ram[b + length / 2] = 'b';
            for (unsigned r = 0; r < 32; ++r) cpu.gpr[r] = 0x12345678u + i * r;
            cpu.gpr[3] = (i & 32 ? GC_RAM_UNCACHED : GC_RAM_BASE) + a;
            cpu.gpr[4] = (i & 64 ? GC_RAM_UNCACHED : GC_RAM_BASE) + b;
            cpu.pc = 0x8025c9c8u;
            cpu.lr = 0xdeadbeecu; /* Outside this chunk's return dispatch. */
            cpu.cr = 0xfedcba98u; cpu.xer = 0xe0000000u; cpu.ctr = 123;
            cpu.exception = 0; cpu.downcount = budgets[i % 7];
            CPUState expected = cpu;
            original_strcmp(&expected);
            func_80259940(&cpu);
            if (memcmp(&cpu, &expected, sizeof(cpu))) {
                fprintf(stderr, "Linked dispatch mismatch: enabled=%u case=%u\n", enabled, i);
                return 1;
            }
            ++cases;
        }
    }
    printf("PASS: %u linked-module dispatch comparisons, helper on/off, yields and long declines\n", cases);
    cpu_free(&cpu);
    return 0;
}
