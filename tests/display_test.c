#include "generated.h"
#include "display.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>

void original_projection(CPUState*);
void patched_projection(CPUState*);
void openspm_set_aspect_ratio(uint32_t);

static void put_float(CPUState* c, u32 p, float f) {
    mem_write32(c, p, dolrecomp_f32_to_bits(f));
}
static float get_float(CPUState* c, u32 p) {
    return (float)dolrecomp_f32_from_bits(mem_read32(c, p));
}

int main(void)
{
    CPUState cpu;
    assert(cpu_init(&cpu));
    const u32 cam = 0x80010000u, gx = 0x80020000u, world = 0x80030000u;
    unsigned cases = 0;
    for (u32 mode = 0; mode <= 4; ++mode)
    for (u32 wide = 0; wide <= 1; ++wide)
    for (u32 type = 0; type <= 1; ++type)
    for (u32 id = 0; id < 15; ++id)
    for (u32 recognized = 0; recognized <= 1; ++recognized) {
        memset(cpu.ram, 0, cpu.ram_size);
        cpu.pc = 0x80292164u;
        cpu.lr = recognized ? 0x80055674u : 0x80123450u;
        cpu.gpr[2] = 0x80040000u;
        cpu.gpr[3] = cam + 148u;
        cpu.gpr[4] = type;
        cpu.gpr[29] = cam;
        cpu.msr = 0x2000u; /* FP enabled */
        cpu.cr = 0x12345678u;
        cpu.xer = 0xe0000000u;
        cpu.exception = 0;
        cpu.downcount = 1200;
        const int hud = type == 1 && (id == 0 || id == 1 || id == 13);
        mem_write32(&cpu, cam, wide && !hud ? 0x10000000u : 0);
        mem_write32(&cpu, cam + 4u, id);
        mem_write32(&cpu, cam + 0x158u, type);
        mem_write32(&cpu, 0x804d9facu, world);
        mem_write32(&cpu, world, wide ? 0x10000000u : 0);
        mem_write32(&cpu, cpu.gpr[2] - 480u, gx);
        const float source = wide ? 16.0f / 9.0f : 4.0f / 3.0f;
        /* Include an asymmetric projection: scaling just m00 is insufficient. */
        float matrix[16] = {1.8f/source,0,0.15f,-0.75f, 0,1.8f,0.2f,0.6f,
                            0,0,-1.01f,-2.01f, 0,0,-1,0};
        for (unsigned i = 0; i < 16; ++i)
            put_float(&cpu, cam + 148u + 4u*i, matrix[i]);
        unsigned char camera_before[752];
        memcpy(camera_before, cpu.ram + cam - GC_RAM_BASE, sizeof(camera_before));
        CPUState input = cpu;
        original_projection(&cpu);
        CPUState expected_cpu = cpu;
        unsigned char expected_gx[1600];
        memcpy(expected_gx, cpu.ram + gx - GC_RAM_BASE, sizeof(expected_gx));
        const float before_x = get_float(&cpu, gx + 1324u);
        const float before_offset = get_float(&cpu, gx + 1328u);
        memset(cpu.ram + gx - GC_RAM_BASE, 0, sizeof(expected_gx));
        cpu = input;
        openspm_set_aspect_ratio(mode);
        patched_projection(&cpu);
        assert(memcmp(&cpu, &expected_cpu, sizeof(cpu)) == 0);
        assert(memcmp(camera_before, cpu.ram + cam - GC_RAM_BASE, sizeof(camera_before)) == 0);
        const float target = openspm_target_aspect(mode);
        const float scale = recognized && target ? source / target : 1.0f;
        assert(fabsf(get_float(&cpu, gx + 1324u) - before_x*scale) < 1e-6f);
        assert(fabsf(get_float(&cpu, gx + 1328u) - before_offset*scale) < 1e-6f);
        /* Every other graphics-state byte must match the original. */
        memcpy(expected_gx + 1324u, cpu.ram + gx - GC_RAM_BASE + 1324u, 8);
        assert(memcmp(expected_gx, cpu.ram + gx - GC_RAM_BASE, sizeof(expected_gx)) == 0);
        if (recognized && target) {
            /* Horizontal displacement measured in pixels stays the same at
               equal image height, while the available world width grows. */
            assert(fabsf(get_float(&cpu, gx + 1324u)*target - before_x*source) < 1e-6f);
        }
        ++cases;
    }
    OpenSPMProjection p = {{1,2,3,4,5,6}}, before = p;
    assert(!openspm_projection_hor_plus(&p, 1, NAN));
    assert(!openspm_projection_hor_plus(&p, 0, 2));
    assert(!memcmp(&p, &before, sizeof(p)));
    assert(openspm_projection_hor_plus(&p, 4.0f/3, 32.0f/9));
    assert(p.value[2] == 3 && p.value[3] == 4 && p.value[4] == 5 && p.value[5] == 6);
    cpu_free(&cpu);
    printf("PASS: %u generated-function comparisons; camera/CPU state unchanged; horizontal pixel scale invariant\n", cases);
    return 0;
}
