/* OpenSPM AX HLE implementation. See openspm_ax_hle.h and docs/AUDIO-PLAN.md.
 *
 * Sources: public descriptions of the AX voice parameter block, DSP-ADPCM
 * (8-byte frames: one predictor/scale header byte + 14 4-bit samples;
 * sample = ((nibble << scale) << 11 + c1*yn1 + c2*yn2 + 1024) >> 11, clamped)
 * and the DSP accelerator's PCM formats/gain, plus the game's own CPU code
 * for every offset and opcode (addresses cited next to each use). Where the
 * exact DSP behaviour is not observable from the CPU side (interpolation
 * kernels, surround fold-down gains, DPL2 matrix) this file uses its own
 * documented choice; those spots are marked "HLE choice". */
#include "openspm_ax_hle.h"

#include <math.h>
#include <string.h>

#define PHYS(a) ((uint32_t)(a) & 0x1FFFFFFFu)
#define N AX_HLE_FRAME_SAMPLES
#define RN AX_HLE_RMT_FRAME_SAMPLES
#define AUX_CH_BYTES (N * 4u) /* 384: one int32 channel of an AUX buffer */
#define MAX_CMD_WORDS 256u
#define MAX_SRC_RATIO 0x00100000u /* 16.0: bounds input consumed per frame */

/* ---------------------------------------------------------------- helpers */

static uint16_t rd16(const uint8_t *p) { return (uint16_t)((unsigned)p[0] << 8 | p[1]); }
static uint32_t rd32(const uint8_t *p) { return (uint32_t)rd16(p) << 16 | rd16(p + 2); }
static void wr16(uint8_t *p, uint16_t v) { p[0] = (uint8_t)(v >> 8); p[1] = (uint8_t)v; }
static void wr32(uint8_t *p, uint32_t v) { wr16(p, (uint16_t)(v >> 16)); wr16(p + 2, (uint16_t)v); }

static int16_t sat16(int32_t v) {
    if (v > 32767) return 32767;
    if (v < -32768) return -32768;
    return (int16_t)v;
}

static int32_t mul15(int32_t sample, int32_t vol) { return (int32_t)(((int64_t)sample * vol) >> 15); }

static void mem_read(ax_hle_state *s, uint32_t phys, void *dst, uint32_t len) {
    if (s->mem.read == NULL || s->mem.read(s->mem.user, phys, dst, len) != 0) {
        memset(dst, 0, len);
        s->stats.memory_faults++;
    }
}

static void mem_write(ax_hle_state *s, uint32_t phys, const void *src, uint32_t len) {
    if (s->mem.write == NULL || s->mem.write(s->mem.user, phys, src, len) != 0) s->stats.memory_faults++;
}

/* Write one field of the local PB copy back to guest memory. */
static void pb_put(ax_hle_state *s, uint32_t pb_phys, const uint8_t *pb, unsigned off, unsigned len) {
    mem_write(s, pb_phys + off, pb + off, len);
}

/* 32-byte aligned read cache for sample and command fetches. */
typedef struct byte_cache {
    ax_hle_state *s;
    uint32_t base;
    int valid;
    uint8_t data[32];
} byte_cache;

static uint8_t cache_byte(byte_cache *c, uint32_t addr) {
    const uint32_t base = addr & ~31u;
    if (!c->valid || c->base != base) {
        mem_read(c->s, base, c->data, sizeof c->data);
        c->base = base;
        c->valid = 1;
    }
    return c->data[addr - base];
}

/* ------------------------------------------------------- building blocks */

int16_t ax_hle_adpcm_sample(int nibble, uint8_t ps, const int16_t coefs[16], int16_t *yn1, int16_t *yn2) {
    const unsigned idx = (ps >> 4) & 7u;
    const int64_t c1 = coefs[idx * 2u], c2 = coefs[idx * 2u + 1u];
    const int64_t v = (int64_t)nibble * (1 << (ps & 15u)) * 2048 + c1 * *yn1 + c2 * *yn2;
    const int64_t y = (v + 1024) >> 11;
    const int16_t out = (int16_t)(y > 32767 ? 32767 : (y < -32768 ? -32768 : y));
    *yn2 = *yn1;
    *yn1 = out;
    return out;
}

int16_t ax_hle_src_sample(const ax_hle_state *state, unsigned src_type, unsigned coef_select,
                          const int16_t h[4], uint16_t frac) {
    if (src_type == AX_SRC_NONE) return h[1];
    if (src_type == AX_SRC_LINEAR) return (int16_t)(h[1] + (((int32_t)h[2] - h[1]) * (int32_t)frac >> 16));
    {
        const int32_t *t = state->src_taps[coef_select < 3u ? coef_select : 2u][frac >> 9];
        const int32_t acc = h[0] * t[0] + h[1] * t[1] + h[2] * t[2] + h[3] * t[3];
        return sat16((acc + 16384) >> 15);
    }
}

/* HLE choice for the 4-tap polyphase SRC: the ucode's own tables are not
 * reproduced. Taps are a 4-point windowed sinc (Lanczos window, a = 2)
 * evaluated at 128 phases, with the passband set by coef_select (0/1/2 =
 * 0.5/0.75/1.0 of the input Nyquist, matching NW4R's 8k/12k/16k naming),
 * normalised to unity DC gain in Q15. Selection 2 is exactly interpolating
 * (phase 0 = identity). */
static void build_src_tables(ax_hle_state *s) {
    static const double cutoff[3] = {0.5, 0.75, 1.0};
    const double pi = 3.14159265358979323846;
    unsigned sel, p, k;
    for (sel = 0; sel < 3u; ++sel) {
        for (p = 0; p < AX_HLE_SRC_PHASES; ++p) {
            const double t = (double)p / AX_HLE_SRC_PHASES;
            double w[4], sum = 0.0;
            int32_t q[4], qsum = 0, big = 0;
            for (k = 0; k < 4u; ++k) {
                const double d = (double)k - 1.0 - t; /* tap distance from the point */
                const double x = cutoff[sel] * d;
                const double sinc = fabs(x) < 1e-12 ? 1.0 : sin(pi * x) / (pi * x);
                const double u = d / 2.0;
                const double win = fabs(u) >= 1.0 ? 0.0 : (fabs(u) < 1e-12 ? 1.0 : sin(pi * u) / (pi * u));
                w[k] = sinc * win;
                sum += w[k];
            }
            for (k = 0; k < 4u; ++k) {
                q[k] = (int32_t)floor(w[k] / sum * 32768.0 + 0.5);
                qsum += q[k];
                if (q[k] > q[big]) big = (int32_t)k;
            }
            q[big] += 32768 - qsum;
            for (k = 0; k < 4u; ++k) s->src_taps[sel][p][k] = q[k];
        }
    }
}

/* ------------------------------------------------------------ init / flat */

void ax_hle_init(ax_hle_state *state, const ax_hle_memory *mem, const ax_hle_hooks *hooks) {
    memset(state, 0, sizeof *state);
    if (mem != NULL) state->mem = *mem;
    if (hooks != NULL) state->hooks = *hooks;
    state->mode = AX_MODE_STEREO;
    state->output_order = AX_HLE_OUTPUT_LR;
    build_src_tables(state);
}

static uint8_t *flat_span(ax_hle_flat_memory *f, uint32_t phys, uint32_t len) {
    if (f->mem1 != NULL && phys < f->mem1_size && len <= f->mem1_size - phys) return f->mem1 + phys;
    if (f->mem2 != NULL && phys >= 0x10000000u) {
        const uint32_t off = phys - 0x10000000u;
        if (off < f->mem2_size && len <= f->mem2_size - off) return f->mem2 + off;
    }
    return NULL;
}

static int flat_read(void *user, uint32_t phys, void *dst, uint32_t len) {
    uint8_t *p = flat_span((ax_hle_flat_memory *)user, phys, len);
    if (p == NULL) return -1;
    memcpy(dst, p, len);
    return 0;
}

static int flat_write(void *user, uint32_t phys, const void *src, uint32_t len) {
    uint8_t *p = flat_span((ax_hle_flat_memory *)user, phys, len);
    if (p == NULL) return -1;
    memcpy(p, src, len);
    return 0;
}

void ax_hle_memory_flat(ax_hle_memory *out, ax_hle_flat_memory *flat) {
    out->user = flat;
    out->read = flat_read;
    out->write = flat_write;
}

/* ------------------------------------------------------------------ voice */

typedef struct voice {
    unsigned format;
    int loop, stream, stopped, bad_format;
    uint32_t loop_addr, end_addr, cur;
    int16_t coefs[16];
    uint16_t gain;
    uint8_t ps, loop_ps;
    int16_t yn1, yn2, loop_yn1, loop_yn2;
    byte_cache cache;
} voice;

static int16_t voice_next(voice *v) {
    int16_t out = 0;
    if (v->stopped) return 0;
    switch (v->format) {
    case AX_FORMAT_ADPCM: {
        uint8_t b;
        int nib;
        if ((v->cur & 15u) == 0u) { /* frame start: header byte, then 14 samples */
            v->ps = cache_byte(&v->cache, v->cur >> 1);
            v->cur += 2u;
        }
        b = cache_byte(&v->cache, v->cur >> 1);
        nib = (v->cur & 1u) ? (b & 15) : (b >> 4);
        if (nib >= 8) nib -= 16;
        out = ax_hle_adpcm_sample(nib, v->ps, v->coefs, &v->yn1, &v->yn2);
        break;
    }
    case AX_FORMAT_PCM16: {
        const uint32_t a = v->cur * 2u;
        const int16_t raw = (int16_t)((unsigned)cache_byte(&v->cache, a) << 8 | cache_byte(&v->cache, a + 1u));
        out = sat16((int32_t)raw * v->gain >> 11); /* format 0x0A: gain / 2048 */
        v->yn2 = v->yn1;
        v->yn1 = out;
        break;
    }
    case AX_FORMAT_PCM8: {
        const int8_t raw = (int8_t)cache_byte(&v->cache, v->cur);
        out = sat16((int32_t)raw * v->gain); /* format 0x19: gain / 1 */
        v->yn2 = v->yn1;
        v->yn1 = out;
        break;
    }
    default:
        v->bad_format = 1;
        v->stopped = 1;
        return 0;
    }
    if (v->cur == v->end_addr) {
        if (v->loop) {
            v->cur = v->loop_addr;
            if (v->format == AX_FORMAT_ADPCM) {
                v->ps = v->loop_ps;
                if (!v->stream) { /* streams keep their running history */
                    v->yn1 = v->loop_yn1;
                    v->yn2 = v->loop_yn2;
                }
            }
        } else {
            /* Like the DSP: the address still goes to the loop address, and
               the voice stops there. NW4R never reads the stop: its
               AxVoice::IsPlayFinished (0x802F3FA8) calls a voice finished
               when the current address is inside its zero buffer, which it
               sets as every one-shot's loop address. Left at the end, no
               sound effect ever finished for NW4R and the game's 32 SFX
               slots filled up (sound effects died until the next map). */
            v->cur = v->loop_addr;
            v->stopped = 1;
        }
    } else {
        v->cur++;
    }
    return out;
}

/* Per bus: mix-field index (u16 index into AXPB_MIX; delta follows), the
 * mixer-control enable and ramp bits, and the bus's slot in AXPB_DPOP.
 * Mix order from NW4R's setter 0x802F614C; dpop order from __AXDepopVoice
 * 0x802878A8 (pb+0x52.. accumulate into the SETUP entries 0,3,6,9,1,4,...). */
static const struct {
    uint8_t field;
    uint8_t dpop;
    uint32_t on, ramp;
} k_bus[AX_BUS_COUNT] = {
    {0, 0, AX_MIXCTRL_L, AX_MIXCTRL_LR_RAMP},    {2, 4, AX_MIXCTRL_R, AX_MIXCTRL_LR_RAMP},
    {16, 8, AX_MIXCTRL_S, AX_MIXCTRL_S_RAMP},    {4, 1, AX_MIXCTRL_AL, AX_MIXCTRL_ALR_RAMP},
    {6, 5, AX_MIXCTRL_AR, AX_MIXCTRL_ALR_RAMP},  {18, 9, AX_MIXCTRL_AS, AX_MIXCTRL_AS_RAMP},
    {8, 2, AX_MIXCTRL_BL, AX_MIXCTRL_BLR_RAMP},  {10, 6, AX_MIXCTRL_BR, AX_MIXCTRL_BLR_RAMP},
    {20, 10, AX_MIXCTRL_BS, AX_MIXCTRL_BS_RAMP}, {12, 3, AX_MIXCTRL_CL, AX_MIXCTRL_CLR_RAMP},
    {14, 7, AX_MIXCTRL_CR, AX_MIXCTRL_CLR_RAMP}, {22, 11, AX_MIXCTRL_CS, AX_MIXCTRL_CS_RAMP},
};

static int32_t clamp_vol(int32_t v) { return v < 0 ? 0 : (v > 0xFFFF ? 0xFFFF : v); }

/* Diagnostics only: which voices start, and which the HLE ends at once. */
static unsigned track_voice(ax_hle_state *s, uint32_t pb_phys, const uint8_t *pb) {
    const unsigned slot = (unsigned)((pb_phys / AX_HLE_PB_SIZE) & (AX_HLE_TRACK_SLOTS - 1u));
    const uint32_t frame = s->stats.frames + 1u; /* 0 = never seen */
    if (s->track_pb[slot] != pb_phys || s->track_seen[slot] + 1u != frame) {
        const unsigned format = rd16(pb + AXPB_FORMAT);
        s->track_pb[slot] = pb_phys;
        s->track_start[slot] = frame;
        s->stats.voices_started++;
        s->stats.started_format[format == AX_FORMAT_ADPCM ? 0 : format == AX_FORMAT_PCM16 ? 1
                                : format == AX_FORMAT_PCM8 ? 2 : 3]++;
        if (s->stats.notes < AX_HLE_START_NOTES) {
            ax_hle_voice_note *n = &s->stats.start_notes[s->stats.notes++];
            n->frame = s->stats.frames;
            n->pb = pb_phys;
            n->cur = rd32(pb + AXPB_CUR_ADDR);
            n->loop_addr = rd32(pb + AXPB_LOOP_ADDR);
            n->end_addr = rd32(pb + AXPB_END_ADDR);
            n->src_ratio = rd32(pb + AXPB_SRC_RATIO);
            n->mixer_ctrl = rd32(pb + AXPB_MIXER_CTRL);
            n->format = (uint16_t)format;
            n->loop = rd16(pb + AXPB_LOOP_FLAG);
            n->type = rd16(pb + AXPB_TYPE);
            n->src_type = rd16(pb + AXPB_SRC_TYPE);
            n->ve_vol = rd16(pb + AXPB_VE_VOL);
            n->mix_l = rd16(pb + AXPB_MIX);
            n->mix_r = rd16(pb + AXPB_MIX + 4u);
            n->mix_al = rd16(pb + AXPB_MIX + 8u);
            n->lpf = rd16(pb + AXPB_LPF_ON);
            n->biq = rd16(pb + AXPB_BIQ_ON);
        }
    }
    s->track_seen[slot] = frame;
    return slot;
}

static void process_voice(ax_hle_state *s, uint32_t pb_phys, uint8_t *pb) {
    voice v;
    int16_t smp[N];
    int16_t h[4];
    unsigned i, k, b;
    uint16_t frac;
    uint32_t ratio, ctrl;
    int ramped;
    const unsigned src_type = rd16(pb + AXPB_SRC_TYPE), coef_sel = rd16(pb + AXPB_COEF_SELECT);

    memset(&v, 0, sizeof v);
    v.cache.s = s;
    v.format = rd16(pb + AXPB_FORMAT);
    v.loop = rd16(pb + AXPB_LOOP_FLAG) != 0;
    v.stream = rd16(pb + AXPB_TYPE) == 1;
    v.loop_addr = rd32(pb + AXPB_LOOP_ADDR);
    v.end_addr = rd32(pb + AXPB_END_ADDR);
    v.cur = rd32(pb + AXPB_CUR_ADDR);
    for (k = 0; k < 16u; ++k) v.coefs[k] = (int16_t)rd16(pb + AXPB_ADPCM_COEFS + 2u * k);
    v.gain = rd16(pb + AXPB_ADPCM_GAIN);
    /* AXSetVoiceAddr (0x80288604) always sets 0x0800 / 0x0100 for PCM16 /
     * PCM8; a zero gain can only come from a hand-built PB, use the same. */
    if (v.gain == 0u) v.gain = v.format == AX_FORMAT_PCM8 ? 0x0100u : 0x0800u;
    v.ps = (uint8_t)rd16(pb + AXPB_ADPCM_PS);
    v.yn1 = (int16_t)rd16(pb + AXPB_ADPCM_YN1);
    v.yn2 = (int16_t)rd16(pb + AXPB_ADPCM_YN2);
    v.loop_ps = (uint8_t)rd16(pb + AXPB_LOOP_PS);
    v.loop_yn1 = (int16_t)rd16(pb + AXPB_LOOP_YN1);
    v.loop_yn2 = (int16_t)rd16(pb + AXPB_LOOP_YN2);

    /* 1. Decode + sample-rate convert: output first, then consume input.
     * Output i sits at fraction frac between h[1] and h[2]: a fixed
     * three-sample latency (~94 us) for every SRC type, so voices with
     * different SRC types stay aligned. */
    ratio = rd32(pb + AXPB_SRC_RATIO);
    if (ratio > MAX_SRC_RATIO) ratio = MAX_SRC_RATIO;
    frac = rd16(pb + AXPB_SRC_FRAC);
    for (k = 0; k < 4u; ++k) h[k] = (int16_t)rd16(pb + AXPB_SRC_LAST + 2u * k);
    for (i = 0; i < N; ++i) {
        uint32_t acc, n;
        smp[i] = ax_hle_src_sample(s, src_type, coef_sel, h, frac);
        acc = (uint32_t)frac + ratio;
        frac = (uint16_t)acc;
        for (n = acc >> 16; n != 0u; --n) {
            h[0] = h[1];
            h[1] = h[2];
            h[2] = h[3];
            h[3] = voice_next(&v);
        }
    }

    /* 2. One-pole low-pass: y = (a0*x + b0*y1) >> 15 (AXGetLpfCoefs at
     * 0x802886FC stores a0 = 32767 - b0). */
    if (rd16(pb + AXPB_LPF_ON) != 0u) {
        const int32_t a0 = (int16_t)rd16(pb + AXPB_LPF_A0), b0 = (int16_t)rd16(pb + AXPB_LPF_B0);
        int16_t y1 = (int16_t)rd16(pb + AXPB_LPF_YN1);
        for (i = 0; i < N; ++i) smp[i] = y1 = sat16((a0 * smp[i] + b0 * y1) >> 15);
        wr16(pb + AXPB_LPF_YN1, (uint16_t)y1);
        pb_put(s, pb_phys, pb, AXPB_LPF_YN1, 2);
    }

    /* 3. Biquad, Q14 coefficients, a1/a2 added (sign convention to confirm,
     * see docs/AUDIO-PLAN.md). */
    if (rd16(pb + AXPB_BIQ_ON) != 0u) {
        const int32_t b0 = (int16_t)rd16(pb + AXPB_BIQ_B0), b1 = (int16_t)rd16(pb + AXPB_BIQ_B1);
        const int32_t b2 = (int16_t)rd16(pb + AXPB_BIQ_B2), a1 = (int16_t)rd16(pb + AXPB_BIQ_A1);
        const int32_t a2 = (int16_t)rd16(pb + AXPB_BIQ_A2);
        int32_t x1 = (int16_t)rd16(pb + AXPB_BIQ_XN1), x2 = (int16_t)rd16(pb + AXPB_BIQ_XN2);
        int32_t y1 = (int16_t)rd16(pb + AXPB_BIQ_YN1), y2 = (int16_t)rd16(pb + AXPB_BIQ_YN2);
        for (i = 0; i < N; ++i) {
            const int32_t x = smp[i];
            const int16_t y = sat16((int32_t)(((int64_t)b0 * x + (int64_t)b1 * x1 + (int64_t)b2 * x2 +
                                               (int64_t)a1 * y1 + (int64_t)a2 * y2) >> 14));
            x2 = x1;
            x1 = x;
            y2 = y1;
            y1 = y;
            smp[i] = y;
        }
        wr16(pb + AXPB_BIQ_XN1, (uint16_t)x1);
        wr16(pb + AXPB_BIQ_XN2, (uint16_t)x2);
        wr16(pb + AXPB_BIQ_YN1, (uint16_t)y1);
        wr16(pb + AXPB_BIQ_YN2, (uint16_t)y2);
        pb_put(s, pb_phys, pb, AXPB_BIQ_XN1, 8);
    }

    /* 4. Volume envelope: per-sample delta (NW4R advances its copy by
     * 96 * delta per frame, 0x802F4A68). */
    {
        int32_t ve = rd16(pb + AXPB_VE_VOL);
        const int32_t dve = (int16_t)rd16(pb + AXPB_VE_DELTA);
        for (i = 0; i < N; ++i) {
            smp[i] = sat16(mul15(smp[i], ve));
            ve = clamp_vol(ve + dve);
        }
        wr16(pb + AXPB_VE_VOL, (uint16_t)ve);
        pb_put(s, pb_phys, pb, AXPB_VE_VOL, 2);
    }

    /* 5. Bus mixing with per-sample volume ramps; depop = last contribution. */
    ctrl = rd32(pb + AXPB_MIXER_CTRL);
    ramped = 0;
    for (b = 0; b < AX_BUS_COUNT; ++b) {
        uint8_t *vp = pb + AXPB_MIX + 2u * k_bus[b].field;
        int32_t last = 0;
        if (ctrl & k_bus[b].on) {
            int32_t vol = rd16(vp);
            const int32_t dv = (ctrl & k_bus[b].ramp) ? (int16_t)rd16(vp + 2) : 0;
            for (i = 0; i < N; ++i) {
                last = mul15(smp[i], vol);
                s->bus[b][i] += last;
                vol = clamp_vol(vol + dv);
            }
            if (dv != 0) {
                wr16(vp, (uint16_t)vol);
                ramped = 1;
            }
        }
        wr16(pb + AXPB_DPOP + 2u * k_bus[b].dpop, (uint16_t)sat16(last));
    }
    if (ramped) pb_put(s, pb_phys, pb, AXPB_MIX, 48);
    pb_put(s, pb_phys, pb, AXPB_DPOP, 24);

    /* 6. Remote speakers: main/aux per remote, mixed at 32 kHz, decimated by
     * WM_OUTPUT. Ramps step once per 6 kHz output sample. */
    if (rd16(pb + AXPB_RMT_ON) != 0u) {
        const unsigned rctrl = rd16(pb + AXPB_RMT_MIXER_CTRL);
        unsigned r, a;
        for (r = 0; r < AX_HLE_REMOTES; ++r) {
            for (a = 0; a < 2u; ++a) {
                const unsigned field = 4u * r + 2u * a, slot = a * AX_HLE_REMOTES + r;
                uint8_t *vp = pb + AXPB_RMT_MIX + 2u * field;
                int32_t last = 0;
                if (rctrl & (1u << field)) {
                    const int32_t vol0 = rd16(vp);
                    const int32_t dv = (rctrl & (2u << field)) ? (int16_t)rd16(vp + 2) : 0;
                    for (i = 0; i < N; ++i) {
                        last = mul15(smp[i], clamp_vol(vol0 + dv * (int32_t)(i * RN / N)));
                        s->rmt_bus[slot][i] += last;
                    }
                    if (dv != 0) wr16(vp, (uint16_t)clamp_vol(vol0 + dv * (int32_t)RN));
                }
                wr16(pb + AXPB_RMT_DPOP + 2u * slot, (uint16_t)sat16(last));
            }
        }
        pb_put(s, pb_phys, pb, AXPB_RMT_MIX, 32);
        pb_put(s, pb_phys, pb, AXPB_RMT_DPOP, 16);
    }

    /* 7. Write back decoder / SRC state where the CPU reads it
     * (__AXSyncPBs reads back state, VE volume and current address). */
    wr32(pb + AXPB_CUR_ADDR, v.cur);
    pb_put(s, pb_phys, pb, AXPB_CUR_ADDR, 4);
    wr16(pb + AXPB_ADPCM_PS, v.ps);
    wr16(pb + AXPB_ADPCM_YN1, (uint16_t)v.yn1);
    wr16(pb + AXPB_ADPCM_YN2, (uint16_t)v.yn2);
    pb_put(s, pb_phys, pb, AXPB_ADPCM_PS, 6);
    wr16(pb + AXPB_SRC_FRAC, frac);
    for (k = 0; k < 4u; ++k) wr16(pb + AXPB_SRC_LAST + 2u * k, (uint16_t)h[k]);
    pb_put(s, pb_phys, pb, AXPB_SRC_FRAC, 10);
    if (v.stopped) {
        const unsigned slot = (unsigned)((pb_phys / AX_HLE_PB_SIZE) & (AX_HLE_TRACK_SLOTS - 1u));
        wr16(pb + AXPB_STATE, 0);
        pb_put(s, pb_phys, pb, AXPB_STATE, 2);
        s->stats.voices_ended++;
        if (s->track_pb[slot] == pb_phys && s->stats.frames + 1u - s->track_start[slot] < 2u)
            s->stats.voices_short++;
    }
    if (v.bad_format) {
        s->stats.bad_commands++;
        s->stats.bad_format++;
    }
}

static void process_pb_list(ax_hle_state *s, uint32_t first) {
    uint8_t pb[AX_HLE_PB_SIZE];
    uint32_t addr = PHYS(first), count = 0;
    s->stats.voices_last = 0;
    s->stats.pbs_last = 0;
    while (addr != 0u) {
        if (count++ == AX_HLE_MAX_VOICES) {
            s->stats.pb_list_overflow++;
            break;
        }
        mem_read(s, addr, pb, sizeof pb);
        s->stats.pbs_last++;
        if (rd16(pb + AXPB_STATE) == 1u) {
            (void)track_voice(s, addr, pb);
            process_voice(s, addr, pb);
            s->stats.voices_mixed++;
            s->stats.voices_last++;
        }
        addr = PHYS(rd32(pb + AXPB_NEXT_HI));
    }
}

/* --------------------------------------------------------------- commands */

static void setup_frame(ax_hle_state *s, uint32_t addr) {
    /* 120 bytes built by 0x802876D4: 12 x {s32 init, s16 delta} for the
     * main/AUX buses (bus order), then remote main r at 72 + 12r and remote
     * aux r at 78 + 12r (18-sample ramps). */
    uint8_t blk[120];
    unsigned b, i, r, a;
    mem_read(s, addr, blk, sizeof blk);
    for (b = 0; b < AX_BUS_COUNT; ++b) {
        const int32_t init = (int32_t)rd32(blk + 6u * b);
        const int32_t delta = (int16_t)rd16(blk + 6u * b + 4u);
        for (i = 0; i < N; ++i) s->bus[b][i] = init + delta * (int32_t)i;
    }
    for (r = 0; r < AX_HLE_REMOTES; ++r) {
        for (a = 0; a < 2u; ++a) {
            const unsigned off = 72u + 12u * r + 6u * a;
            const int32_t init = (int32_t)rd32(blk + off), delta = (int16_t)rd16(blk + off + 4u);
            for (i = 0; i < RN; ++i) s->rmt_depop[a * AX_HLE_REMOTES + r][i] = init + delta * (int32_t)i;
        }
    }
}

static void write_i32_channel(ax_hle_state *s, uint32_t addr, const int32_t *src) {
    uint8_t buf[AUX_CH_BYTES];
    unsigned i;
    for (i = 0; i < N; ++i) wr32(buf + 4u * i, (uint32_t)src[i]);
    mem_write(s, addr, buf, sizeof buf);
}

static void read_i32_channel(ax_hle_state *s, uint32_t addr, int32_t *dst) {
    uint8_t buf[AUX_CH_BYTES];
    unsigned i;
    if (addr == 0u) {
        memset(dst, 0, N * sizeof *dst);
        return;
    }
    mem_read(s, addr, buf, sizeof buf);
    for (i = 0; i < N; ++i) dst[i] = (int32_t)rd32(buf + 4u * i);
}

/* AUX bus group g (1..3). send: channel send addresses (0 = none); ret:
 * return addresses; dst: main buses the return is added into. */
static void mix_aux(ax_hle_state *s, unsigned g, uint16_t vol, const unsigned *src_bus,
                    const uint32_t *send, const uint32_t *ret, const unsigned *dst_bus, unsigned channels) {
    int32_t retbuf[4][N];
    int32_t *sendp[4] = {NULL, NULL, NULL, NULL}, *retp[4] = {NULL, NULL, NULL, NULL};
    unsigned c, i;
    for (c = 0; c < channels; ++c) {
        if (send[c] != 0u) {
            write_i32_channel(s, send[c], s->bus[src_bus[c]]);
            sendp[c] = s->bus[src_bus[c]];
        }
        read_i32_channel(s, ret[c], retbuf[c]);
        retp[c] = retbuf[c];
    }
    if (s->hooks.aux != NULL) s->hooks.aux(s->hooks.user, g - 1u, sendp, retp, channels);
    for (c = 0; c < channels; ++c)
        for (i = 0; i < N; ++i) s->bus[dst_bus[c]][i] += mul15(retbuf[c][i], vol);
}

/* HLE choice: the surround scratch buffer passed to 0x01/0x02/0x03 and
 * OUTPUT is private to the DSP (nothing else in the game references it), so
 * the fold-down is done on the current frame inside OUTPUT and the scratch
 * buffer is left untouched. Gains (Q15): stereo S -> L and R at -3 dB;
 * surround: Dolby Surround style matrix (L - 0.707 S, R + 0.707 S); DPL2:
 * L - 0.8165 Ls - 0.5774 Rs, R + 0.5774 Ls + 0.8165 Rs (no 90-degree phase
 * shift). */
#define G707 23170
#define G816 26755
#define G577 18919

static void output_frame(ax_hle_state *s, uint16_t vol, uint32_t out_addr, int dpl2) {
    int16_t lr[2u * N];
    uint8_t buf[4u * N];
    unsigned i;
    for (i = 0; i < N; ++i) {
        int32_t l = s->bus[AX_BUS_MAIN_L][i], r = s->bus[AX_BUS_MAIN_R][i];
        const int32_t sv = s->bus[AX_BUS_MAIN_S][i];
        if (dpl2) {
            const int32_t rs = s->bus[AX_BUS_C_L][i];
            l -= mul15(sv, G816) + mul15(rs, G577);
            r += mul15(sv, G577) + mul15(rs, G816);
        } else if (s->mode == AX_MODE_SURROUND) {
            l -= mul15(sv, G707);
            r += mul15(sv, G707);
        } else {
            l += mul15(sv, G707);
            r += mul15(sv, G707);
        }
        lr[2u * i] = sat16(mul15(l, vol));
        lr[2u * i + 1u] = sat16(mul15(r, vol));
    }
    for (i = 0; i < N; ++i) {
        const int16_t first = s->output_order == AX_HLE_OUTPUT_RL ? lr[2u * i + 1u] : lr[2u * i];
        const int16_t second = s->output_order == AX_HLE_OUTPUT_RL ? lr[2u * i] : lr[2u * i + 1u];
        wr16(buf + 4u * i, (uint16_t)first);
        wr16(buf + 4u * i + 2u, (uint16_t)second);
    }
    if (out_addr != 0u) mem_write(s, out_addr, buf, sizeof buf);
    s->stats.last_output_addr = out_addr;
    if (s->hooks.output != NULL) s->hooks.output(s->hooks.user, lr, N, out_addr);
}

/* HLE choice: remote SRC is a box-filter decimation of the 32 kHz remote
 * buses by 96/18 inside each frame; the PB remote SRC state (0x10A) is left
 * as the CPU wrote it, and remote aux is summed with main (no remote AUX
 * command exists in this game's command list). */
static void output_remotes(ax_hle_state *s, const uint32_t addr[4]) {
    int16_t out[AX_HLE_REMOTES][RN];
    const int16_t *outp[4];
    unsigned r, j, i;
    for (r = 0; r < AX_HLE_REMOTES; ++r) {
        uint8_t buf[2u * RN];
        for (j = 0; j < RN; ++j) {
            const unsigned lo = j * N / RN, hi = (j + 1u) * N / RN;
            int64_t sum = 0;
            for (i = lo; i < hi; ++i) sum += (int64_t)s->rmt_bus[r][i] + s->rmt_bus[AX_HLE_REMOTES + r][i];
            out[r][j] = sat16((int32_t)(sum / (int64_t)(hi - lo)) + s->rmt_depop[r][j] +
                              s->rmt_depop[AX_HLE_REMOTES + r][j]);
            wr16(buf + 2u * j, (uint16_t)out[r][j]);
        }
        outp[r] = addr[r] != 0u ? out[r] : NULL;
        if (addr[r] != 0u) mem_write(s, PHYS(addr[r]), buf, sizeof buf);
    }
    if (s->hooks.remote_output != NULL) s->hooks.remote_output(s->hooks.user, outp, RN);
}

int ax_hle_process_command_list(ax_hle_state *s, uint32_t guest_addr) {
    byte_cache cl;
    uint32_t pos = PHYS(guest_addr), words = 0;
    int rc = AX_HLE_ERR_NO_END;

    memset(s->bus, 0, sizeof s->bus);
    memset(s->rmt_bus, 0, sizeof s->rmt_bus);
    memset(s->rmt_depop, 0, sizeof s->rmt_depop);
    s->mode = AX_MODE_STEREO;
    memset(&cl, 0, sizeof cl);
    cl.s = s;

#define WORD() (words++, pos += 2u, (uint16_t)((unsigned)cache_byte(&cl, pos - 2u) << 8 | cache_byte(&cl, pos - 1u)))
#define ADDR() (hi_ = WORD(), PHYS((uint32_t)hi_ << 16 | WORD()))
    while (words < MAX_CMD_WORDS) {
        uint16_t hi_;
        const uint16_t cmd = WORD();
        s->stats.last_command = cmd;
        s->stats.commands++;
        switch (cmd) {
        case AX_CMD_SETUP:
            setup_frame(s, ADDR());
            break;
        case AX_CMD_ADD_TO_LR:
        case AX_CMD_SUB_TO_LR:
        case AX_CMD_ADD_SUB_TO_LR:
            (void)ADDR(); /* surround scratch, see output_frame */
            s->mode = cmd == AX_CMD_ADD_TO_LR ? AX_MODE_STEREO
                      : cmd == AX_CMD_SUB_TO_LR ? AX_MODE_SURROUND : AX_MODE_DPL2;
            break;
        case AX_CMD_PROCESS:
            process_pb_list(s, ADDR());
            break;
        case AX_CMD_MIX_AUXA:
        case AX_CMD_MIX_AUXB:
        case AX_CMD_MIX_AUXC: {
            const unsigned g = cmd - AX_CMD_MIX_AUXA + 1u;
            const uint16_t vol = WORD();
            const uint32_t send0 = ADDR(), ret0 = ADDR();
            const unsigned src[3] = {g * 3u, g * 3u + 1u, g * 3u + 2u};
            const unsigned dst[3] = {AX_BUS_MAIN_L, AX_BUS_MAIN_R, AX_BUS_MAIN_S};
            const uint32_t send[3] = {send0, send0 ? send0 + AUX_CH_BYTES : 0u, send0 ? send0 + 2u * AUX_CH_BYTES : 0u};
            const uint32_t ret[3] = {ret0, ret0 ? ret0 + AUX_CH_BYTES : 0u, ret0 ? ret0 + 2u * AUX_CH_BYTES : 0u};
            mix_aux(s, g, vol, src, send, ret, dst, 3u);
            break;
        }
        case AX_CMD_MIX_AUXA_DPL2:
        case AX_CMD_MIX_AUXB_DPL2: {
            const unsigned g = cmd - AX_CMD_MIX_AUXA_DPL2 + 1u;
            const uint16_t vol = WORD();
            const uint32_t send0 = ADDR(), send_rs = ADDR();
            uint32_t ret[4];
            unsigned c;
            const unsigned src[4] = {g * 3u, g * 3u + 1u, g * 3u + 2u, g == 1u ? AX_BUS_C_R : AX_BUS_C_S};
            const unsigned dst[4] = {AX_BUS_MAIN_L, AX_BUS_MAIN_R, AX_BUS_MAIN_S, AX_BUS_C_L};
            const uint32_t send[4] = {send0, send0 ? send0 + AUX_CH_BYTES : 0u,
                                      send0 ? send0 + 2u * AUX_CH_BYTES : 0u, send0 ? send_rs : 0u};
            for (c = 0; c < 4u; ++c) ret[c] = ADDR();
            mix_aux(s, g, vol, src, send, ret, dst, 4u);
            break;
        }
        case AX_CMD_COMPRESSOR:
            (void)WORD();
            (void)WORD();
            (void)ADDR();
            break;
        case AX_CMD_OUTPUT:
        case AX_CMD_OUTPUT_DPL2: {
            const uint16_t vol = WORD();
            uint32_t out;
            (void)ADDR(); /* surround scratch */
            out = ADDR();
            output_frame(s, vol, out, cmd == AX_CMD_OUTPUT_DPL2);
            break;
        }
        case AX_CMD_WM_OUTPUT: {
            uint32_t a[4];
            unsigned r;
            for (r = 0; r < 4u; ++r) a[r] = ADDR();
            output_remotes(s, a);
            break;
        }
        case AX_CMD_END:
            rc = AX_HLE_OK;
            goto done;
        default:
            s->stats.bad_commands++;
            rc = AX_HLE_ERR_BAD_COMMAND;
            goto done;
        }
    }
    s->stats.bad_commands++;
done:
#undef WORD
#undef ADDR
    s->stats.frames++;
    return rc;
}
