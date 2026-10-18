/* OpenSPM audio output: the renderer's PC audio stream (docs/AUDIO-PLAN.md).
 *
 * The renderer DLL owns SDL, so it owns the audio device too. It exports one
 * function, looked up by the native module with GetModuleHandleA/GetProcAddress
 * like the gamepad/cheats/menu exports:
 *
 *   int openspm_renderer_audio_push(const int16_t *lr, uint32_t frames,
 *                                   uint32_t rate, openspm_audio_out_status *st)
 *     lr     interleaved host-order s16 frames, L then R (NULL with frames 0)
 *     frames 0 = only report the status
 *     rate   sample rate of lr (the Wii AI rate, 32000)
 *     st     optional, filled after the push
 *     returns 0, or -1 when no audio device could be opened (then the caller
 *     keeps running and simply has no sound).
 *
 * The call never blocks: the samples go into an SDL audio stream opened with
 * SDL_OpenAudioDeviceStream (32 kHz stereo s16). Audio is subordinate to the
 * game clock (docs/PRESENTATION-ARCHITECTURE.md): the producer pushes at its
 * own guest-clock pace and the stream absorbs the difference, via the pacer
 * below:
 *   - prebuffer: the device stays paused until OPENSPM_AUDIO_TARGET_MS are
 *     queued, so a start (or the end of a pause) never plays in stutters;
 *   - underrun: the queue ran dry while playing -> pause the device (silence)
 *     and prebuffer again; counted unless the producer was away for longer
 *     than OPENSPM_AUDIO_GAP_MS (menu pause, loading stall = a resync);
 *   - overflow: more than OPENSPM_AUDIO_MAX_MS queued -> drop the push;
 *   - drift: a small playback-rate trim (at most +-OPENSPM_AUDIO_TRIM_PPM)
 *     from the smoothed queue level keeps it at the target, so the device
 *     clock and the game clock never have to agree.
 *
 * Portable C (no SDL, no Windows headers): the pacer is unit-tested in
 * tests/m14_audio_device_test.c. */
#ifndef OPENSPM_AUDIO_OUT_H
#define OPENSPM_AUDIO_OUT_H

#include <stdint.h>

#include "openspm/abi.h"

#ifdef __cplusplus
extern "C" {
#endif

#define OPENSPM_RENDERER_AUDIO_PUSH_EXPORT "openspm_renderer_audio_push"

typedef struct openspm_audio_out_status {
    uint32_t queued_frames;  /* frames waiting in the stream after this push */
    uint32_t rate;
    uint32_t underruns;      /* queue ran dry while the producer was active   */
    uint32_t resyncs;        /* queue ran dry after a producer gap (pause)     */
    uint32_t dropped_frames; /* overflow drops                                 */
    int32_t trim_ppm;        /* current playback-rate trim                     */
    uint32_t playing;        /* device running (not prebuffering)              */
} openspm_audio_out_status;

typedef int(OPENSPM_CALL *openspm_renderer_audio_push_fn)(const int16_t *lr, uint32_t frames,
                                                          uint32_t rate,
                                                          openspm_audio_out_status *status);

#define OPENSPM_AUDIO_TARGET_MS 40u
#define OPENSPM_AUDIO_MAX_MS 200u
#define OPENSPM_AUDIO_GAP_MS 100u
#define OPENSPM_AUDIO_TRIM_PPM 2000

enum {
    OPENSPM_AUDIO_PACE_PUT = 1u,   /* queue these frames                    */
    OPENSPM_AUDIO_PACE_DROP = 2u,  /* discard them (overflow)               */
    OPENSPM_AUDIO_PACE_START = 4u, /* resume the device (prebuffer reached) */
    OPENSPM_AUDIO_PACE_STOP = 8u,  /* pause the device (ran dry)            */
    OPENSPM_AUDIO_PACE_TRIM = 16u  /* trim_ppm changed                      */
};

typedef struct openspm_audio_pacer {
    uint32_t rate;
    uint32_t target_frames;
    uint32_t max_frames;
    int playing;
    uint64_t last_push_ns;
    int64_t level_x16; /* smoothed queue level, frames * 16 */
    int32_t trim_ppm;
    uint32_t underruns;
    uint32_t resyncs;
    uint32_t dropped_frames;
} openspm_audio_pacer;

static inline void openspm_audio_pacer_init(openspm_audio_pacer *p, uint32_t rate) {
    p->rate = rate != 0u ? rate : 32000u;
    p->target_frames = p->rate * OPENSPM_AUDIO_TARGET_MS / 1000u;
    p->max_frames = p->rate * OPENSPM_AUDIO_MAX_MS / 1000u;
    p->playing = 0;
    p->last_push_ns = 0u;
    p->level_x16 = (int64_t)p->target_frames * 16;
    p->trim_ppm = 0;
    p->underruns = 0u;
    p->resyncs = 0u;
    p->dropped_frames = 0u;
}

/* The device was paused or cleared from outside (menu opened). */
static inline void openspm_audio_pacer_reset(openspm_audio_pacer *p) {
    p->playing = 0;
    p->level_x16 = (int64_t)p->target_frames * 16;
    p->trim_ppm = 0;
}

/* One push of `frames` with `queued` frames still in the stream. Returns
   OPENSPM_AUDIO_PACE_* flags for the caller to apply, in the order STOP, PUT,
   START, TRIM. */
static inline unsigned openspm_audio_pacer_push(openspm_audio_pacer *p, uint32_t queued, uint32_t frames,
                                                uint64_t now_ns) {
    unsigned flags = 0u;
    const uint64_t gap = p->last_push_ns != 0u && now_ns > p->last_push_ns ? now_ns - p->last_push_ns : 0u;
    p->last_push_ns = now_ns;
    if (frames == 0u) return 0u;
    if (p->playing && queued == 0u) {
        if (gap < (uint64_t)OPENSPM_AUDIO_GAP_MS * 1000000u)
            p->underruns++;
        else
            p->resyncs++;
        openspm_audio_pacer_reset(p);
        flags |= OPENSPM_AUDIO_PACE_STOP | OPENSPM_AUDIO_PACE_TRIM;
    }
    if ((uint64_t)queued + frames > p->max_frames) {
        p->dropped_frames += frames;
        return flags | OPENSPM_AUDIO_PACE_DROP;
    }
    flags |= OPENSPM_AUDIO_PACE_PUT;
    queued += frames;
    if (!p->playing) {
        if (queued >= p->target_frames) {
            p->playing = 1;
            p->level_x16 = (int64_t)queued * 16;
            flags |= OPENSPM_AUDIO_PACE_START;
        }
        return flags;
    }
    {
        /* Proportional trim on a slow average (time constant ~64 pushes):
           above the target play slightly faster, below slightly slower. */
        int64_t err, ppm;
        p->level_x16 += ((int64_t)queued * 16 - p->level_x16) / 64;
        err = p->level_x16 / 16 - (int64_t)p->target_frames;
        ppm = err * OPENSPM_AUDIO_TRIM_PPM / (int64_t)(p->target_frames != 0u ? p->target_frames : 1u);
        if (ppm > OPENSPM_AUDIO_TRIM_PPM) ppm = OPENSPM_AUDIO_TRIM_PPM;
        if (ppm < -OPENSPM_AUDIO_TRIM_PPM) ppm = -OPENSPM_AUDIO_TRIM_PPM;
        ppm = ppm / 100 * 100; /* 100 ppm steps: avoid touching the stream every push */
        if ((int32_t)ppm != p->trim_ppm) {
            p->trim_ppm = (int32_t)ppm;
            flags |= OPENSPM_AUDIO_PACE_TRIM;
        }
    }
    return flags;
}

#ifdef __cplusplus
}
#endif

#endif
