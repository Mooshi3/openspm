/* OpenSPM on-screen overlay service for mods ("openspm.overlay",
 * docs/MODDING.md).
 *
 * A mod can show small text panels over the game (a HUD, an input display,
 * a timer) and short notifications ("Position saved"). The renderer draws them
 * every presented frame with the OpenSPM menu's font, top-right, stacked in
 * the order they were first set; notifications fade out at the bottom.
 *
 * Text is UTF-8, at most OPENSPM_OVERLAY_MAX_TEXT bytes (longer is cut), "\n"
 * starts a new line. Panel ids are per mod-chosen strings ("practice.hud");
 * setting a panel again replaces its text, NULL or "" removes it. Calls copy
 * the text and return at once; any thread. Up to OPENSPM_OVERLAY_MAX_PANELS
 * panels; OPENSPM_ERROR_RUNTIME when full.
 *
 * Provided by OpenSPMNative, which forwards to the renderer's
 * openspm_renderer_overlay export; a host without a renderer overlay answers
 * the query with an error. Later versions only append fields.
 *
 * Version 2 adds boxes: draw_boxes(layer, boxes, count) replaces a layer's
 * outlined rectangles (a hitbox view, a target marker). Coordinates are
 * fractions of the game picture (0,0 top-left, 1,1 bottom-right; outside is
 * clipped), so they follow window size, aspect and letterbox bars. A layer
 * keeps its boxes until set again; count 0 clears it. At most
 * OPENSPM_OVERLAY_MAX_BOX_LAYERS layers of OPENSPM_OVERLAY_MAX_BOXES. */
#ifndef OPENSPM_OVERLAY_SERVICE_H
#define OPENSPM_OVERLAY_SERVICE_H

#include <stddef.h>
#include <stdint.h>

#include "openspm/abi.h"

#ifdef __cplusplus
extern "C" {
#endif

#define OPENSPM_OVERLAY_SERVICE_ID "openspm.overlay"
#define OPENSPM_OVERLAY_SERVICE_VERSION 2u /* 1 still accepted (no draw_boxes) */
#define OPENSPM_OVERLAY_MAX_PANELS 16u
#define OPENSPM_OVERLAY_MAX_TEXT 1024u
#define OPENSPM_OVERLAY_MAX_ID 64u
#define OPENSPM_OVERLAY_MAX_BOX_LAYERS 8u
#define OPENSPM_OVERLAY_MAX_BOXES 256u

/* One outlined rectangle; rgba = 0xRRGGBBAA. */
typedef struct openspm_overlay_box {
    float x0, y0, x1, y1;
    uint32_t rgba;
} openspm_overlay_box;

typedef int(OPENSPM_CALL *openspm_overlay_set_panel_fn)(void *context, const char *panel_id, const char *utf8_text);
typedef int(OPENSPM_CALL *openspm_overlay_notify_fn)(void *context, const char *utf8_text, uint32_t milliseconds);
typedef int(OPENSPM_CALL *openspm_overlay_draw_boxes_fn)(void *context, const char *layer_id,
                                                         const openspm_overlay_box *boxes, uint32_t count);

typedef struct openspm_overlay_service {
    openspm_abi_header header;
    uint64_t capabilities;
    void *context;
    openspm_overlay_set_panel_fn set_panel;
    openspm_overlay_notify_fn notify; /* milliseconds 0 = 2500 */
    openspm_overlay_draw_boxes_fn draw_boxes; /* version 2 */
} openspm_overlay_service;

#define OPENSPM_OVERLAY_SERVICE_V1_SIZE (offsetof(openspm_overlay_service, notify) + sizeof(void *))
#define OPENSPM_OVERLAY_SERVICE_V2_SIZE (offsetof(openspm_overlay_service, draw_boxes) + sizeof(void *))

static inline int openspm_overlay_accept(const openspm_overlay_service *out, uint32_t requested_version) {
    if (out == 0) return OPENSPM_ERROR_INVALID_ARGUMENT;
    if (requested_version == 1u && out->header.struct_size >= OPENSPM_OVERLAY_SERVICE_V1_SIZE) return OPENSPM_OK;
    if (requested_version == 2u && out->header.struct_size >= OPENSPM_OVERLAY_SERVICE_V2_SIZE) return OPENSPM_OK;
    return OPENSPM_ERROR_ABI_MISMATCH;
}

/* Renderer side (openspm_renderer.dll). op: 1 = set panel (id, text), 2 =
   notify (text, milliseconds). Returns OPENSPM_OK or an error. */
#define OPENSPM_RENDERER_OVERLAY_EXPORT "openspm_renderer_overlay"
#define OPENSPM_RENDERER_OVERLAY_SET_PANEL 1u
#define OPENSPM_RENDERER_OVERLAY_NOTIFY 2u
typedef int(OPENSPM_CALL *openspm_renderer_overlay_fn)(uint32_t op, const char *id, const char *utf8_text,
                                                       uint32_t milliseconds);
/* Version 2: boxes (copied; any thread). */
#define OPENSPM_RENDERER_OVERLAY_BOXES_EXPORT "openspm_renderer_overlay_boxes"
typedef int(OPENSPM_CALL *openspm_renderer_overlay_boxes_fn)(const char *layer_id, const openspm_overlay_box *boxes,
                                                             uint32_t count);

#ifdef __cplusplus
}
#endif

#endif
