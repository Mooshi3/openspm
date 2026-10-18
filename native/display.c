#include "display.h"
#include <math.h>

float openspm_target_aspect(uint32_t mode)
{
    switch (mode) {
    case OPENSPM_4_3: return 4.0f / 3.0f;
    case OPENSPM_16_9: return 16.0f / 9.0f;
    case OPENSPM_21_9: return 21.0f / 9.0f;
    case OPENSPM_32_9: return 32.0f / 9.0f;
    default: return 0.0f;
    }
}

int openspm_projection_hor_plus(OpenSPMProjection* p, float source, float target)
{
    if (!p || !isfinite(source) || !isfinite(target) || source <= 0 || target <= 0)
        return 0;
    if (source == target)
        return 0; /* Preserve exact bits in stock mode. */
    const float scale = source / target;
    const float x = p->value[0] * scale;
    const float offset = p->value[1] * scale;
    if (!isfinite(x) || !isfinite(offset))
        return 0;
    p->value[0] = x;
    p->value[1] = offset;
    return 1;
}
