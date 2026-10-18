/* R8PE01 adapter for the existing compatibility backend.
   Exact-source anchor: camDraw -> GXSetProjection, LR 0x80055674.
   Override the submitted packet, never the camera's matrix or aspect.
   This catches matrices built by callbacks, initialization, and camDraw alike. */
#include "generated.h"
#include "display.h"
#include <stdio.h>

#if defined(_WIN32)
#define EXPORT __declspec(dllexport)
#else
#define EXPORT __attribute__((visibility("default")))
#endif

static uint32_t aspect_mode;
static uint32_t reported_cameras;

/* Host delivers requests on the game CPU thread (M11C contract). */
EXPORT void openspm_set_aspect_ratio(uint32_t mode)
{
    if (mode <= OPENSPM_32_9 && mode != aspect_mode) {
        aspect_mode = mode;
        reported_cameras = 0;
    }
}

void openspm_r8pe01_submit_projection(CPUState* ctx)
{
    const float target = openspm_target_aspect(aspect_mode);
    if (!target || ctx->exception || ctx->lr != 0x80055674u)
        return;
    const u32 camera = ctx->gpr[29];
    if (camera < 0x80000000u || camera > 0x817ffd10u || (camera & 3u) ||
        ctx->gpr[3] != camera + 0x94u || ctx->gpr[4] > 1u)
        return;
    const u32 camera_id = mem_read32(ctx, camera + 4u);
    if (camera_id >= 15u)
        return;
    const u32 flags = mem_read32(ctx, camera);
    const u32 type = mem_read32(ctx, camera + 0x158u);
    if (type != ctx->gpr[4] || ctx->exception)
        return;

    float source = (flags & 0x10000000u) ? 16.0f / 9.0f : 4.0f / 3.0f;
    /* These stock ortho cameras intentionally skip SPM's widescreen widening.
       Retain their stock on-screen proportions in a centered safe area.
       Camera 11 records the game's selected 4:3/16:9 mode even when the
       current world camera switches between perspective and orthographic. */
    if (type == 1u && (camera_id == 0u || camera_id == 1u || camera_id == 13u)) {
        const u32 world = mem_read32(ctx, 0x804d9facu);
        if (world >= 0x80000000u && world <= 0x817ffd10u && !(world & 3u)) {
            const u32 world_flags = mem_read32(ctx, world);
            source = (world_flags & 0x10000000u) ? 16.0f / 9.0f : 4.0f / 3.0f;
        }
    }
    if (ctx->exception)
        return;
    const u32 packet = ctx->gpr[5] + 1324u;
    OpenSPMProjection projection = {{0}};
    /* Only the two horizontal coefficients are needed. Leave all other
       packet bytes and all PPC registers/flags/cycle accounting intact. */
    projection.value[0] = (float)dolrecomp_f32_from_bits(mem_read32(ctx, packet));
    projection.value[1] = (float)dolrecomp_f32_from_bits(mem_read32(ctx, packet + 4u));
    if (ctx->exception || !openspm_projection_hor_plus(&projection, source, target))
        return;
    mem_write32(ctx, packet, dolrecomp_f32_to_bits(projection.value[0]));
    mem_write32(ctx, packet + 4u, dolrecomp_f32_to_bits(projection.value[1]));
    if (!(reported_cameras & (1u << camera_id))) {
        reported_cameras |= 1u << camera_id;
        fprintf(stderr, "[OPENSPM-DISPLAY] submit cam=%u type=%u mode=%u source=%.6f target=%.6f xscale=%.6f\n",
                camera_id, type, aspect_mode, (double)source, (double)target,
                (double)(source / target));
        fflush(stderr);
    }
}
