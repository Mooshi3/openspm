/* Experimental R8PE01 animPoseGetShapeIdx inner-loop specialization.
   Entered only at 0x8004DD8C, after the original function has validated the
   pose and resolved the shape table. This batches repeated calls to the
   already-proven native strcmp helper while preserving PPC scratch state,
   cycle accounting, generated 256-cycle yield points, and conservative
   fallback to the original dispatched strcmp path. */
#include "generated.h"
#include <stdio.h>
#include <stdlib.h>

int openspm_r8pe01_strcmp(CPUState* ctx);

static int shape_lookup_enabled = 1;

void openspm_configure_native_shape_lookup(void)
{
    const char* value = getenv("OPENSPM_NATIVE_SHAPE_LOOKUP");
    shape_lookup_enabled = !value || *value != '0';
    fprintf(stderr, "[OPENSPM-NATIVE] shape_lookup=%s\n",
            shape_lookup_enabled ? "on" : "off");
    fflush(stderr);
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

/* Return values:
   0 = untouched/disabled: execute generated loop normally.
   1 = search completed: r3 is result; caller jumps to 0x8004DDBC epilogue.
   2 = yielded/redirected: ctx->pc names the exact generated continuation. */
int openspm_r8pe01_shape_lookup_loop(CPUState* ctx)
{
    if (!shape_lookup_enabled || !ctx || ctx->exception || !ctx->host_call)
        return 0;

    /* Do not bypass RecompCore's chunk/SMC verification or guest interception.
       The paired runtime handles this CPU-ABI query even when no guest mods
       are active. Older runtimes leave host_call NULL (or leave the query
       unhandled), which conservatively disables this optimization. */
    if (!ppc_native_region_available(ctx, 0x8025C9C8u, 0x8025CAE4u))
        return 0;

    for (;;) {
        /* 8004DD8C..8004DD94: mr r3,r29; mr r4,r31; bl strcmp. */
        ctx->downcount -= 3;
        ctx->gpr[3] = ctx->gpr[29];
        ctx->gpr[4] = ctx->gpr[31];
        ctx->lr = 0x8004DD98u;

        const int strcmp_result = openspm_r8pe01_strcmp(ctx);
        if (strcmp_result == 2)
            return 2;
        if (strcmp_result == 0) {
            /* State already exactly matches the original BL dispatch point. */
            ctx->pc = 0x8025C9C8u;
            return 2;
        }

        /* 8004DD98..8004DD9C: cmpwi r3,0 / bne. */
        ctx->downcount -= 2;
        set_cr0_signed(ctx, ctx->gpr[3]);
        if (ctx->gpr[3] == 0) {
            /* 8004DDA0: mr r3,r28; then branch to epilogue. */
            ctx->downcount -= 2;
            ctx->gpr[3] = ctx->gpr[28];
            return 1;
        }

        /* 8004DDA8..8004DDB4: advance record and test loop bound. */
        ctx->downcount -= 2;
        ctx->gpr[28] += 1u;
        ctx->gpr[29] += 88u;
        ctx->downcount -= 2;
        set_cr0_cmpw(ctx, ctx->gpr[28], ctx->gpr[30]);

        if ((s32)ctx->gpr[28] >= (s32)ctx->gpr[30]) {
            /* 8004DDB8: li r3,-1. */
            ctx->downcount -= 1;
            ctx->gpr[3] = 0xffffffffu;
            return 1;
        }

        /* Same native-loop budget used by the generated C backend. */
        if (ctx->downcount <= -(s64)DOLRECOMP_C_LOOP_CYCLE_BUDGET) {
            ctx->pc = 0x8004DD8Cu;
            return 2;
        }
    }
}
