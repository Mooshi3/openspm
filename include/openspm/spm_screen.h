/* Game world -> screen for mods that draw over the game (openspm.overlay
 * version 2 boxes): the camera matrices camDraw builds (CamEntry +0x54 view,
 * +0x94 projection; see spm_r8pe01.h) and the projection to fractions of the
 * game picture. Pure C; the caller reads the camera with its own guest memory
 * access (spm_screen_read_camera takes a read32 callback). */
#ifndef OPENSPM_SPM_SCREEN_H
#define OPENSPM_SPM_SCREEN_H

#include <math.h>
#include <stdint.h>
#include <string.h>

#include "openspm/overlay_service.h"
#include "openspm/spm_r8pe01.h"

typedef struct spm_screen_camera {
    float view[3][4];
    float proj[4][4];
} spm_screen_camera;

typedef int (*spm_screen_read32_fn)(void *context, uint32_t address, uint32_t *value);

/* Mario's camera (MarioWork +0x120): 1 when both matrices read as finite
   numbers and the projection type is perspective or orthographic. */
static inline int spm_screen_read_camera(spm_screen_camera *c, spm_screen_read32_fn read32, void *context) {
    uint32_t id = 0u, cam = 0u, type = 1u, bits;
    int r, k;
    if (!read32(context, SPM_R8PE01_MARIO_WORK + SPM_R8PE01_MARIO_CAMERA_ID, &id) || id >= 16u ||
        !read32(context, SPM_R8PE01_CAMERA_TABLE + 4u * id, &cam) || cam == 0u ||
        !read32(context, cam + SPM_R8PE01_CAMERA_PROJ_TYPE, &type) || type > 1u)
        return 0;
    for (r = 0; r < 3; ++r)
        for (k = 0; k < 4; ++k) {
            if (!read32(context, cam + SPM_R8PE01_CAMERA_VIEW_MTX + 16u * (uint32_t)r + 4u * (uint32_t)k, &bits))
                return 0;
            memcpy(&c->view[r][k], &bits, sizeof bits);
            if (!isfinite(c->view[r][k])) return 0;
        }
    for (r = 0; r < 4; ++r)
        for (k = 0; k < 4; ++k) {
            if (!read32(context, cam + SPM_R8PE01_CAMERA_PROJ_MTX + 16u * (uint32_t)r + 4u * (uint32_t)k, &bits))
                return 0;
            memcpy(&c->proj[r][k], &bits, sizeof bits);
            if (!isfinite(c->proj[r][k])) return 0;
        }
    return 1;
}

/* World point -> picture fractions (0..1, y down). The viewport is taken as
   the whole picture (the game's main cameras). 0 when the point is behind
   the camera. */
static inline int spm_screen_project(const spm_screen_camera *c, float x, float y, float z, float *fx, float *fy) {
    float v[4], clip[4];
    int r;
    for (r = 0; r < 3; ++r) v[r] = c->view[r][0] * x + c->view[r][1] * y + c->view[r][2] * z + c->view[r][3];
    v[3] = 1.0f;
    for (r = 0; r < 4; ++r)
        clip[r] = c->proj[r][0] * v[0] + c->proj[r][1] * v[1] + c->proj[r][2] * v[2] + c->proj[r][3] * v[3];
    if (!(clip[3] > 1.0e-4f)) return 0;
    *fx = (clip[0] / clip[3] + 1.0f) * 0.5f;
    *fy = (1.0f - clip[1] / clip[3]) * 0.5f;
    return isfinite(*fx) && isfinite(*fy);
}

/* The screen rectangle around the world box from (x0, y0, z0) to (x1, y1,
   z1). 0 if a corner is behind the camera or it is off the picture. */
static inline int spm_screen_box(const spm_screen_camera *c, float x0, float y0, float z0, float x1, float y1,
                                 float z1, uint32_t rgba, openspm_overlay_box *out) {
    float a = 2.0f, b = 2.0f, e = -1.0f, f = -1.0f;
    int i;
    for (i = 0; i < 8; ++i) {
        float fx, fy;
        if (!spm_screen_project(c, (i & 1) ? x1 : x0, (i & 2) ? y1 : y0, (i & 4) ? z1 : z0, &fx, &fy)) return 0;
        if (fx < a) a = fx;
        if (fx > e) e = fx;
        if (fy < b) b = fy;
        if (fy > f) f = fy;
    }
    if (e < 0.0f || f < 0.0f || a > 1.0f || b > 1.0f) return 0;
    out->x0 = a;
    out->y0 = b;
    out->x1 = e;
    out->y1 = f;
    out->rgba = rgba;
    return 1;
}

/* A box centred on (x, z) standing on y: width w (x), height h, depth d (z). */
static inline int spm_screen_standing_box(const spm_screen_camera *c, float x, float y, float z, float w, float h,
                                          float d, uint32_t rgba, openspm_overlay_box *out) {
    return spm_screen_box(c, x - 0.5f * w, y, z - 0.5f * d, x + 0.5f * w, y + h, z + 0.5f * d, rgba, out);
}

#endif
