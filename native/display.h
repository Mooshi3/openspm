/* OpenSPM display policy. No console or graphics-backend headers. */
#ifndef OPENSPM_DISPLAY_H
#define OPENSPM_DISPLAY_H
#include <stdint.h>

typedef enum OpenSPMAspect {
    OPENSPM_AUTO, OPENSPM_4_3, OPENSPM_16_9, OPENSPM_21_9, OPENSPM_32_9
} OpenSPMAspect;

/* Compact projection: x scale, x offset, y scale, y offset, z scale, z offset.
   Consumers own their copy. Simulation/camera matrices must stay unchanged. */
typedef struct OpenSPMProjection { float value[6]; } OpenSPMProjection;

float openspm_target_aspect(uint32_t mode);
int openspm_projection_hor_plus(OpenSPMProjection* projection,
                               float source_aspect, float target_aspect);
#endif
