/* Host test for backend/audio/openspm_ax_hle.{h,c}: DSP-ADPCM decode against
   hand-computed values and a self-encoded sine, loop / stream / end handling,
   PCM16 (MEM2) and PCM8, SRC ratio 1.0 / 0.5 / 2.0, envelope and mix ramps,
   saturation of a two-voice mix, PB write-back, and one full synthetic
   command list (SETUP, PROCESS over a linked PB list, AUX A send/return,
   compressor, OUTPUT, WM_OUTPUT, END) in fake guest memory. */
#include "../backend/audio/openspm_ax_hle.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int g_failures;
#define CHECK(cond)                                                               \
    do {                                                                          \
        if (!(cond)) {                                                            \
            printf("%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond);      \
            g_failures++;                                                         \
        }                                                                         \
    } while (0)
#define CHECK_EQ(a, b)                                                            \
    do {                                                                          \
        long long a_ = (long long)(a), b_ = (long long)(b);                       \
        if (a_ != b_) {                                                           \
            printf("%s:%d: %s = %lld, expected %lld\n", __FILE__, __LINE__, #a, a_, b_); \
            g_failures++;                                                         \
        }                                                                         \
    } while (0)

/* ---------------------------------------------------------- fake memory */

#define MEM1_SIZE 0x40000u
#define MEM2_SIZE 0x10000u
static uint8_t g_mem1[MEM1_SIZE];
static uint8_t g_mem2[MEM2_SIZE];
static ax_hle_flat_memory g_flat = {g_mem1, MEM1_SIZE, g_mem2, MEM2_SIZE};

static uint8_t *gp(uint32_t phys) {
    return phys >= 0x10000000u ? g_mem2 + (phys - 0x10000000u) : g_mem1 + phys;
}
static void p16(uint32_t a, uint16_t v) { gp(a)[0] = (uint8_t)(v >> 8); gp(a)[1] = (uint8_t)v; }
static void p32(uint32_t a, uint32_t v) { p16(a, (uint16_t)(v >> 16)); p16(a + 2u, (uint16_t)v); }
static uint16_t g16(uint32_t a) { return (uint16_t)(gp(a)[0] << 8 | gp(a)[1]); }
static uint32_t g32(uint32_t a) { return (uint32_t)g16(a) << 16 | g16(a + 2u); }
static int16_t gs16(uint32_t a) { return (int16_t)g16(a); }

/* Layout (physical). Command-list / PB pointers are written as cached
   virtual addresses (0x80xxxxxx), as the game does. */
#define CL 0x1000u
#define SETUP_BLK 0x2000u
#define PB0 0x3000u
#define PB1 0x3200u
#define OUT 0x4000u
#define SURR 0x5000u
#define AUXA_SEND 0x6000u
#define AUXA_RET 0x7000u
#define RMT 0x8000u
#define SAMPLES 0x10000u
#define VIRT(a) (0x80000000u | (a))

static ax_hle_state g_ax;

static void reset_memory(void) {
    memset(g_mem1, 0, sizeof g_mem1);
    memset(g_mem2, 0, sizeof g_mem2);
}

static void init_ax(const ax_hle_hooks *hooks) {
    ax_hle_memory m;
    ax_hle_memory_flat(&m, &g_flat);
    ax_hle_init(&g_ax, &m, hooks);
}

/* A running voice: src none, ratio 1.0, VE 1.0, main L at 1.0. */
static void pb_basic(uint32_t pb, unsigned format, uint32_t start, uint32_t end) {
    memset(gp(pb), 0, AX_HLE_PB_SIZE);
    p32(pb + AXPB_THIS_HI, VIRT(pb));
    p16(pb + AXPB_SRC_TYPE, AX_SRC_NONE);
    p32(pb + AXPB_MIXER_CTRL, AX_MIXCTRL_L);
    p16(pb + AXPB_STATE, 1);
    p16(pb + AXPB_MIX + 0, 0x8000);
    p16(pb + AXPB_VE_VOL, 0x8000);
    p16(pb + AXPB_FORMAT, (uint16_t)format);
    p32(pb + AXPB_LOOP_ADDR, start);
    p32(pb + AXPB_END_ADDR, end);
    p32(pb + AXPB_CUR_ADDR, start);
    p16(pb + AXPB_ADPCM_GAIN, format == AX_FORMAT_PCM8 ? 0x0100 : format == AX_FORMAT_PCM16 ? 0x0800 : 0);
    p32(pb + AXPB_SRC_RATIO, 0x00010000u);
}

/* Minimal list: SETUP (zero block), PROCESS pb, OUTPUT, END. */
static void cl_simple(uint32_t pb) {
    uint32_t a = CL;
    memset(gp(SETUP_BLK), 0, 120);
    p16(a, AX_CMD_SETUP); p32(a + 2, VIRT(SETUP_BLK)); a += 6;
    p16(a, AX_CMD_PROCESS); p32(a + 2, VIRT(pb)); a += 6;
    p16(a, AX_CMD_OUTPUT); p16(a + 2, 0x8000); p32(a + 4, VIRT(SURR)); p32(a + 8, VIRT(OUT)); a += 12;
    p16(a, AX_CMD_END);
}

static int run_frame(void) { return ax_hle_process_command_list(&g_ax, VIRT(CL)); }

/* ------------------------------------------------ reference ADPCM codec */

static const int16_t k_coefs[16] = {0, 0, 2048, 0, 3584, -1536, 3072, -1024,
                                    4096, -2048, 1536, 0, 3840, -1920, 2560, -512};

/* Independent reference of the public DSP-ADPCM formula. */
static int16_t ref_adpcm(int nib, int ps, int16_t *h1, int16_t *h2) {
    const long c1 = k_coefs[((ps >> 4) & 7) * 2], c2 = k_coefs[((ps >> 4) & 7) * 2 + 1];
    long v = (long)nib * (1L << (ps & 15)) * 2048 + c1 * *h1 + c2 * *h2;
    v = (v + 1024) >> 11;
    if (v > 32767) v = 32767;
    if (v < -32768) v = -32768;
    *h2 = *h1;
    *h1 = (int16_t)v;
    return (int16_t)v;
}

/* Brute-force encoder: per 14-sample frame try every predictor/scale. */
static void encode_adpcm(const int16_t *pcm, unsigned n, uint8_t *out) {
    int16_t h1 = 0, h2 = 0;
    unsigned f, frames = (n + 13u) / 14u;
    for (f = 0; f < frames; ++f) {
        int best_ps = 0;
        double best_err = 1e300;
        int8_t best_nib[14];
        int16_t best_h1 = 0, best_h2 = 0;
        int ps;
        for (ps = 0; ps < 0x80; ++ps) {
            int16_t t1 = h1, t2 = h2;
            int8_t nib[14];
            double err = 0.0;
            unsigned i;
            if ((ps & 15) > 12) continue;
            for (i = 0; i < 14u; ++i) {
                const unsigned k = f * 14u + i;
                const long x = k < n ? pcm[k] : 0;
                const long c1 = k_coefs[(ps >> 4) * 2], c2 = k_coefs[(ps >> 4) * 2 + 1];
                const double pred = (double)(c1 * t1 + c2 * t2) / 2048.0;
                long q = lround(((double)x - pred) / (double)(1 << (ps & 15)));
                int16_t y;
                if (q > 7) q = 7;
                if (q < -8) q = -8;
                nib[i] = (int8_t)q;
                y = ref_adpcm((int)q, ps, &t1, &t2);
                err += ((double)y - (double)x) * ((double)y - (double)x);
            }
            if (err < best_err) {
                best_err = err;
                best_ps = ps;
                memcpy(best_nib, nib, sizeof nib);
                best_h1 = t1;
                best_h2 = t2;
            }
        }
        out[f * 8u] = (uint8_t)best_ps;
        {
            unsigned i;
            for (i = 0; i < 7u; ++i)
                out[f * 8u + 1u + i] = (uint8_t)((best_nib[2 * i] & 15) << 4 | (best_nib[2 * i + 1] & 15));
        }
        h1 = best_h1;
        h2 = best_h2;
    }
}

/* Decode sample k of an encoded stream with explicit history. */
static int16_t ref_sample(const uint8_t *enc, unsigned k, int16_t *h1, int16_t *h2) {
    const uint8_t b = enc[(k / 14u) * 8u + 1u + (k % 14u) / 2u];
    int nib = (k % 2u) ? (b & 15) : (b >> 4);
    if (nib >= 8) nib -= 16;
    return ref_adpcm(nib, enc[(k / 14u) * 8u], h1, h2);
}

static uint32_t nibble_addr(uint32_t byte_base, unsigned k) { return byte_base * 2u + (k / 14u) * 16u + 2u + k % 14u; }

static void set_coefs(uint32_t pb) {
    unsigned i;
    for (i = 0; i < 16u; ++i) p16(pb + AXPB_ADPCM_COEFS + 2u * i, (uint16_t)k_coefs[i]);
}

/* ------------------------------------------------------------------ tests */

static void test_adpcm_hand_vector(void) {
    int16_t coefs[16] = {0};
    int16_t y1 = 100, y2 = 0;
    coefs[2] = 2048; /* pair 1: c1 = 1.0, c2 = 0 */
    coefs[4] = 4096; /* pair 2: c1 = 2.0, c2 = -1.0 */
    coefs[5] = -2048;
    /* ps 0x11: pair 1, scale 2. (3*2*2048 + 2048*100 + 1024) >> 11 = 106 */
    CHECK_EQ(ax_hle_adpcm_sample(3, 0x11, coefs, &y1, &y2), 106);
    CHECK_EQ(y1, 106);
    CHECK_EQ(y2, 100);
    /* (-8*2*2048 + 2048*106 + 1024) >> 11 = 185344 >> 11 = 90 */
    CHECK_EQ(ax_hle_adpcm_sample(-8, 0x11, coefs, &y1, &y2), 90);
    /* ps 0x20: pair 2, scale 1; y1=10, y2=4: (2048 + 40960 - 8192 + 1024) >> 11 = 17 */
    y1 = 10;
    y2 = 4;
    CHECK_EQ(ax_hle_adpcm_sample(1, 0x20, coefs, &y1, &y2), 17);
    /* Saturation: 7 << 15 */
    y1 = y2 = 0;
    CHECK_EQ(ax_hle_adpcm_sample(7, 0x0F, coefs, &y1, &y2), 32767);
    CHECK_EQ(ax_hle_adpcm_sample(-8, 0x0F, coefs, &y1, &y2), -32768);
}

#define SINE_N 420u /* 30 frames */
static int16_t g_sine[SINE_N];
static uint8_t g_enc[(SINE_N / 14u) * 8u];

static void make_sine(void) {
    unsigned i;
    double err = 0.0, sig = 0.0;
    int16_t h1 = 0, h2 = 0;
    for (i = 0; i < SINE_N; ++i) g_sine[i] = (int16_t)lround(12000.0 * sin(2.0 * 3.14159265358979 * 440.0 * i / 32000.0));
    encode_adpcm(g_sine, SINE_N, g_enc);
    for (i = 0; i < SINE_N; ++i) {
        const double d = (double)ref_sample(g_enc, i, &h1, &h2) - g_sine[i];
        err += d * d;
        sig += (double)g_sine[i] * g_sine[i];
    }
    /* The encoder is sane: > 30 dB SNR. */
    CHECK(10.0 * log10(sig / err) > 30.0);
}

/* 96 samples of ADPCM through the full pipeline equal the reference decode
   (three-sample SRC latency), and the decoder state is written back. */
static void test_adpcm_pipeline_and_writeback(void) {
    int16_t h1 = 0, h2 = 0, ref[200];
    unsigned i;
    reset_memory();
    init_ax(NULL);
    memcpy(gp(SAMPLES), g_enc, sizeof g_enc);
    pb_basic(PB0, AX_FORMAT_ADPCM, nibble_addr(SAMPLES, 0), nibble_addr(SAMPLES, SINE_N - 1u));
    set_coefs(PB0);
    p16(PB0 + AXPB_ADPCM_PS, g_enc[0]);
    p16(PB0 + AXPB_LOOP_FLAG, 0);
    cl_simple(PB0);
    CHECK_EQ(run_frame(), AX_HLE_OK);
    for (i = 0; i < 200u; ++i) ref[i] = ref_sample(g_enc, i, &h1, &h2);
    CHECK_EQ(g_ax.bus[AX_BUS_MAIN_L][0], 0);
    CHECK_EQ(g_ax.bus[AX_BUS_MAIN_L][2], 0);
    for (i = 3; i < 96u; ++i) CHECK_EQ(g_ax.bus[AX_BUS_MAIN_L][i], ref[i - 3u]);
    /* 96 samples consumed; next is sample 96. */
    CHECK_EQ(g32(PB0 + AXPB_CUR_ADDR), nibble_addr(SAMPLES, 96));
    CHECK_EQ(g16(PB0 + AXPB_ADPCM_PS), g_enc[(95u / 14u) * 8u]);
    h1 = h2 = 0;
    for (i = 0; i < 96u; ++i) ref_sample(g_enc, i, &h1, &h2);
    CHECK_EQ(gs16(PB0 + AXPB_ADPCM_YN1), h1);
    CHECK_EQ(gs16(PB0 + AXPB_ADPCM_YN2), h2);
    CHECK_EQ(g16(PB0 + AXPB_SRC_FRAC), 0);
    for (i = 0; i < 4u; ++i) CHECK_EQ(gs16(PB0 + AXPB_SRC_LAST + 2u * i), ref[92u + i]);
    CHECK_EQ(g16(PB0 + AXPB_STATE), 1);
    CHECK_EQ(g16(PB0 + AXPB_VE_VOL), 0x8000);
    /* depop: last contribution to main L; other buses 0. */
    CHECK_EQ(gs16(PB0 + AXPB_DPOP + 0), ref[92]);
    CHECK_EQ(gs16(PB0 + AXPB_DPOP + 8), 0);
    /* Output buffer: L = bus, R = 0 (big-endian, L first). */
    CHECK_EQ(gs16(OUT + 4u * 50u), ref[47]);
    CHECK_EQ(gs16(OUT + 4u * 50u + 2u), 0);
    /* Second frame continues seamlessly. */
    CHECK_EQ(run_frame(), AX_HLE_OK);
    for (i = 0; i < 96u; ++i) CHECK_EQ(g_ax.bus[AX_BUS_MAIN_L][i], ref[93u + i]);
}

/* Loop: 2 frames (28 samples), loop start at sample 14. Non-stream voices
   reload loop pred/scale + history; streams keep their history. End without
   loop stops the voice. */
static void test_adpcm_loop(void) {
    unsigned i, stream;
    for (stream = 0; stream < 2u; ++stream) {
        int16_t h1 = 0, h2 = 0, lh1, lh2, expect[200];
        unsigned k = 0;
        reset_memory();
        init_ax(NULL);
        memcpy(gp(SAMPLES), g_enc, 16);
        pb_basic(PB0, AX_FORMAT_ADPCM, nibble_addr(SAMPLES, 0), nibble_addr(SAMPLES, 27));
        set_coefs(PB0);
        p16(PB0 + AXPB_TYPE, (uint16_t)stream);
        p16(PB0 + AXPB_ADPCM_PS, g_enc[0]);
        p16(PB0 + AXPB_LOOP_FLAG, 1);
        p32(PB0 + AXPB_LOOP_ADDR, nibble_addr(SAMPLES, 14));
        p16(PB0 + AXPB_LOOP_PS, g_enc[8]);
        /* Loop history = the true history before sample 14, perturbed so the
           test can see whether it was loaded. */
        for (i = 0; i < 14u; ++i) ref_sample(g_enc, i, &h1, &h2);
        lh1 = (int16_t)(h1 + 500);
        lh2 = (int16_t)(h2 - 300);
        p16(PB0 + AXPB_LOOP_YN1, (uint16_t)lh1);
        p16(PB0 + AXPB_LOOP_YN2, (uint16_t)lh2);
        cl_simple(PB0);
        CHECK_EQ(run_frame(), AX_HLE_OK);
        /* Expected: 0..27, then 14..27 repeatedly. */
        h1 = h2 = 0;
        for (i = 0; i < 200u; ++i) {
            if (i >= 28u && k == 28u) {
                k = 14u;
                if (!stream) {
                    h1 = lh1;
                    h2 = lh2;
                }
            }
            expect[i] = ref_sample(g_enc, k, &h1, &h2);
            ++k;
        }
        for (i = 3; i < 96u; ++i) CHECK_EQ(g_ax.bus[AX_BUS_MAIN_L][i], expect[i - 3u]);
        CHECK_EQ(g16(PB0 + AXPB_STATE), 1);
    }
    /* No loop: stops after sample 27, rest silent, state 0 written back, and
       the address is at the loop address (NW4R's zero buffer: that is how it
       tells a one-shot finished). */
    reset_memory();
    init_ax(NULL);
    memcpy(gp(SAMPLES), g_enc, 16);
    pb_basic(PB0, AX_FORMAT_ADPCM, nibble_addr(SAMPLES, 0), nibble_addr(SAMPLES, 27));
    p32(PB0 + AXPB_LOOP_ADDR, 0x00001002u); /* a zero buffer's first sample nibble */
    set_coefs(PB0);
    p16(PB0 + AXPB_ADPCM_PS, g_enc[0]);
    cl_simple(PB0);
    CHECK_EQ(run_frame(), AX_HLE_OK);
    {
        int16_t h1 = 0, h2 = 0;
        for (i = 0; i < 28u; ++i) CHECK_EQ(g_ax.bus[AX_BUS_MAIN_L][i + 3u], ref_sample(g_enc, i, &h1, &h2));
    }
    for (i = 31; i < 96u; ++i) CHECK_EQ(g_ax.bus[AX_BUS_MAIN_L][i], 0);
    CHECK_EQ(g16(PB0 + AXPB_STATE), 0);
    CHECK_EQ(g32(PB0 + AXPB_CUR_ADDR), 0x00001002u);
    CHECK_EQ(g_ax.stats.voices_ended, 1);
    /* A stopped voice is skipped next frame. */
    CHECK_EQ(run_frame(), AX_HLE_OK);
    CHECK_EQ(g_ax.stats.voices_last, 0);
}

static void test_pcm(void) {
    unsigned i;
    /* PCM16 in MEM2: addresses in 16-bit units. Loops over 40 samples. */
    reset_memory();
    init_ax(NULL);
    for (i = 0; i < 40u; ++i) p16(0x10000100u + 2u * i, (uint16_t)(int16_t)(i * 700 - 14000));
    pb_basic(PB0, AX_FORMAT_PCM16, 0x10000100u / 2u, 0x10000100u / 2u + 39u);
    p16(PB0 + AXPB_LOOP_FLAG, 1);
    cl_simple(PB0);
    CHECK_EQ(run_frame(), AX_HLE_OK);
    for (i = 3; i < 96u; ++i) CHECK_EQ(g_ax.bus[AX_BUS_MAIN_L][i], (int32_t)((i - 3u) % 40u) * 700 - 14000);
    CHECK_EQ(g32(PB0 + AXPB_CUR_ADDR), 0x10000100u / 2u + (96u % 40u));
    /* PCM16 gain 0x0400 halves. */
    p16(PB0 + AXPB_ADPCM_GAIN, 0x0400);
    p32(PB0 + AXPB_CUR_ADDR, 0x10000100u / 2u + 39u);
    memset(gp(PB0 + AXPB_SRC_LAST), 0, 8);
    CHECK_EQ(run_frame(), AX_HLE_OK);
    CHECK_EQ(g_ax.bus[AX_BUS_MAIN_L][3], (39 * 700 - 14000) / 2);
    /* PCM8: byte addresses, value << 8 with gain 0x0100. */
    reset_memory();
    init_ax(NULL);
    for (i = 0; i < 16u; ++i) gp(SAMPLES)[i] = (uint8_t)(int8_t)(i * 16 - 128);
    pb_basic(PB0, AX_FORMAT_PCM8, SAMPLES, SAMPLES + 15u);
    p16(PB0 + AXPB_LOOP_FLAG, 1);
    cl_simple(PB0);
    CHECK_EQ(run_frame(), AX_HLE_OK);
    for (i = 3; i < 96u; ++i) CHECK_EQ(g_ax.bus[AX_BUS_MAIN_L][i], (int32_t)((((i - 3u) % 16u) * 16) - 128) * 256);
}

static void src_voice(unsigned type, unsigned sel, uint32_t ratio) {
    unsigned i;
    reset_memory();
    init_ax(NULL);
    for (i = 0; i < 400u; ++i) p16(SAMPLES + 2u * i, (uint16_t)(int16_t)(i * 100));
    pb_basic(PB0, AX_FORMAT_PCM16, SAMPLES / 2u, SAMPLES / 2u + 399u);
    p16(PB0 + AXPB_SRC_TYPE, (uint16_t)type);
    p16(PB0 + AXPB_COEF_SELECT, (uint16_t)sel);
    p32(PB0 + AXPB_SRC_RATIO, ratio);
    cl_simple(PB0);
    CHECK_EQ(run_frame(), AX_HLE_OK);
}

static void test_src(void) {
    unsigned t, i;
    /* Ratio 1.0: identity (three-sample latency) for every SRC type. */
    for (t = 0; t < 3u; ++t) {
        src_voice(t, 2, 0x10000u);
        for (i = 3; i < 96u; ++i) CHECK_EQ(g_ax.bus[AX_BUS_MAIN_L][i], (int32_t)(i - 3u) * 100);
    }
    /* Ratio 0.5: 48 inputs per frame; a ramp interpolates to 50*i - 300. */
    for (t = 0; t < 2u; ++t) {
        src_voice(t == 0 ? AX_SRC_LINEAR : AX_SRC_POLYPHASE, 2, 0x8000u);
        for (i = 8; i < 96u; ++i) { /* from i = 8 all four taps see the ramp */
            const int32_t d = g_ax.bus[AX_BUS_MAIN_L][i] - (int32_t)(50 * i - 300);
            CHECK(d >= -2 && d <= 2);
        }
        CHECK_EQ(g32(PB0 + AXPB_CUR_ADDR), SAMPLES / 2u + 48u);
        CHECK_EQ(g16(PB0 + AXPB_SRC_FRAC), 0);
    }
    /* Ratio 0.75 leaves a fraction behind: 96 * 0.75 = 72 exactly, try 0.7:
       0x0000B333 * 96 = 0x433320 -> 67 samples consumed, frac 0x3320. */
    src_voice(AX_SRC_LINEAR, 0, 0xB333u);
    CHECK_EQ(g32(PB0 + AXPB_CUR_ADDR), SAMPLES / 2u + 67u);
    CHECK_EQ(g16(PB0 + AXPB_SRC_FRAC), 0x3320);
    /* Ratio 2.0: decimation, out[i] = in[2i - 3]. */
    for (t = 0; t < 3u; ++t) {
        src_voice(t, 2, 0x20000u);
        for (i = 2; i < 96u; ++i) CHECK_EQ(g_ax.bus[AX_BUS_MAIN_L][i], (int32_t)(2 * i - 3) * 100);
        CHECK_EQ(g32(PB0 + AXPB_CUR_ADDR), SAMPLES / 2u + 192u);
        for (i = 0; i < 4u; ++i) CHECK_EQ(gs16(PB0 + AXPB_SRC_LAST + 2u * i), (int32_t)(188 + i) * 100);
    }
    /* Low-pass selections keep DC exactly and attenuate Nyquist. */
    {
        int16_t dc[4] = {1000, 1000, 1000, 1000}, alt[4] = {1000, -1000, 1000, -1000};
        init_ax(NULL);
        for (t = 0; t < 3u; ++t) {
            CHECK_EQ(ax_hle_src_sample(&g_ax, AX_SRC_POLYPHASE, t, dc, 0x5555), 1000);
            CHECK_EQ(ax_hle_src_sample(&g_ax, AX_SRC_POLYPHASE, t, dc, 0x0000), 1000);
        }
        CHECK(abs(ax_hle_src_sample(&g_ax, AX_SRC_POLYPHASE, 0, alt, 0)) < 600);
        CHECK_EQ(ax_hle_src_sample(&g_ax, AX_SRC_POLYPHASE, 2, alt, 0), -1000);
    }
}

static void dc_voice(uint32_t pb, int16_t value) {
    unsigned i;
    p16(SAMPLES + 0x800u * (pb == PB1), (uint16_t)value);
    pb_basic(pb, AX_FORMAT_PCM16, (SAMPLES + 0x800u * (pb == PB1)) / 2u, (SAMPLES + 0x800u * (pb == PB1)) / 2u);
    p16(pb + AXPB_LOOP_FLAG, 1);
    for (i = 0; i < 4u; ++i) p16(pb + AXPB_SRC_LAST + 2u * i, (uint16_t)value); /* no start-up latency */
}

static void test_ramps(void) {
    unsigned i;
    /* Envelope: VE 0x4000 + 64/sample on DC 16384 -> 8192 + 32 i. */
    reset_memory();
    init_ax(NULL);
    dc_voice(PB0, 16384);
    p16(PB0 + AXPB_VE_VOL, 0x4000);
    p16(PB0 + AXPB_VE_DELTA, 64);
    cl_simple(PB0);
    CHECK_EQ(run_frame(), AX_HLE_OK);
    for (i = 0; i < 96u; ++i) CHECK_EQ(g_ax.bus[AX_BUS_MAIN_L][i], 8192 + 32 * (int32_t)i);
    CHECK_EQ(g16(PB0 + AXPB_VE_VOL), 0x4000 + 96 * 64); /* what NW4R expects (0x802F4A68) */
    /* Mix ramp: L 0x2000 + 16/sample (ctrl L | LR ramp). */
    reset_memory();
    init_ax(NULL);
    dc_voice(PB0, 16384);
    p32(PB0 + AXPB_MIXER_CTRL, AX_MIXCTRL_L | AX_MIXCTRL_LR_RAMP);
    p16(PB0 + AXPB_MIX + 0, 0x2000);
    p16(PB0 + AXPB_MIX + 2, 16);
    cl_simple(PB0);
    CHECK_EQ(run_frame(), AX_HLE_OK);
    for (i = 0; i < 96u; ++i) CHECK_EQ(g_ax.bus[AX_BUS_MAIN_L][i], (16384 * (0x2000 + 16 * (int32_t)i)) >> 15);
    CHECK_EQ(g16(PB0 + AXPB_MIX + 0), 0x2000 + 96 * 16);
    CHECK_EQ(gs16(PB0 + AXPB_DPOP + 0), (16384 * (0x2000 + 16 * 95)) >> 15);
    /* Downward ramp clamps at 0. */
    p16(PB0 + AXPB_MIX + 0, 0x0100);
    p16(PB0 + AXPB_MIX + 2, (uint16_t)-16);
    CHECK_EQ(run_frame(), AX_HLE_OK);
    CHECK_EQ(g16(PB0 + AXPB_MIX + 0), 0);
    CHECK_EQ(g_ax.bus[AX_BUS_MAIN_L][95], 0);
    /* Without the ramp bit, delta is ignored and the volume is not rewritten. */
    p32(PB0 + AXPB_MIXER_CTRL, AX_MIXCTRL_L);
    p16(PB0 + AXPB_MIX + 0, 0x4000);
    p16(PB0 + AXPB_MIX + 2, 100);
    CHECK_EQ(run_frame(), AX_HLE_OK);
    CHECK_EQ(g_ax.bus[AX_BUS_MAIN_L][95], 8192);
    CHECK_EQ(g16(PB0 + AXPB_MIX + 0), 0x4000);
}

static void test_two_voice_saturation(void) {
    unsigned i;
    reset_memory();
    init_ax(NULL);
    dc_voice(PB0, 30000);
    dc_voice(PB1, 30000);
    p32(PB0 + AXPB_NEXT_HI, VIRT(PB1));
    /* Both on L at 1.0; voice 0 also on R at 0.5. */
    p32(PB0 + AXPB_MIXER_CTRL, AX_MIXCTRL_L | AX_MIXCTRL_R);
    p16(PB0 + AXPB_MIX + 4, 0x4000);
    cl_simple(PB0);
    CHECK_EQ(run_frame(), AX_HLE_OK);
    CHECK_EQ(g_ax.stats.voices_last, 2);
    CHECK_EQ(g_ax.bus[AX_BUS_MAIN_L][10], 60000);
    for (i = 0; i < 96u; ++i) {
        CHECK_EQ(gs16(OUT + 4u * i), 32767);
        CHECK_EQ(gs16(OUT + 4u * i + 2u), 15000);
    }
    /* Negative side and the R-then-L output order option. */
    dc_voice(PB0, -30000);
    dc_voice(PB1, -30000);
    p32(PB0 + AXPB_NEXT_HI, VIRT(PB1));
    p32(PB0 + AXPB_MIXER_CTRL, AX_MIXCTRL_L | AX_MIXCTRL_R);
    p16(PB0 + AXPB_MIX + 4, 0x4000);
    g_ax.output_order = AX_HLE_OUTPUT_RL;
    CHECK_EQ(run_frame(), AX_HLE_OK);
    CHECK_EQ(gs16(OUT + 0), -15000);
    CHECK_EQ(gs16(OUT + 2), -32768);
}

/* ------------------------------------------------------------- full list */

static unsigned g_aux_calls, g_out_calls, g_rmt_calls;
static int32_t g_aux_seen_send[3];
static int16_t g_out_first_l;
static void hook_aux(void *user, unsigned bus, int32_t *const send[4], int32_t *const ret[4], unsigned ch) {
    (void)user;
    g_aux_calls++;
    CHECK(bus < 3u);
    CHECK_EQ(ch, 3);
    CHECK(ret[0] != NULL && ret[2] != NULL);
    if (bus < 3u) g_aux_seen_send[bus] = send[0] != NULL ? send[0][5] : -1;
}
static void hook_out(void *user, const int16_t *lr, unsigned frames, uint32_t addr) {
    (void)user;
    g_out_calls++;
    CHECK_EQ(frames, 96);
    CHECK_EQ(addr, OUT);
    g_out_first_l = lr[0];
}
static void hook_rmt(void *user, const int16_t *const s[4], unsigned count) {
    (void)user;
    g_rmt_calls++;
    CHECK_EQ(count, 18);
    CHECK(s[0] != NULL && s[3] != NULL);
}

static void test_full_command_list(void) {
    const ax_hle_hooks hooks = {NULL, hook_aux, hook_out, hook_rmt};
    uint32_t a = CL;
    unsigned i, r;
    reset_memory();
    init_ax(&hooks);

    /* SETUP: main L depop ramp 960 - 10 i; remote 0 main 50 constant. */
    p32(SETUP_BLK + 0, 960);
    p16(SETUP_BLK + 4, (uint16_t)-10);
    p32(SETUP_BLK + 72, 50);
    /* PB0: DC 1000; L 1.0, R 0.5, S 1.0, AUX A L 1.0; remote 0 main 1.0. */
    dc_voice(PB0, 1000);
    p32(PB0 + AXPB_MIXER_CTRL, AX_MIXCTRL_L | AX_MIXCTRL_R | AX_MIXCTRL_S | AX_MIXCTRL_AL);
    p16(PB0 + AXPB_MIX + 4, 0x4000);
    p16(PB0 + AXPB_MIX + 32, 0x8000);
    p16(PB0 + AXPB_MIX + 8, 0x8000);
    p16(PB0 + AXPB_RMT_ON, 1);
    p16(PB0 + AXPB_RMT_MIXER_CTRL, 1);
    p16(PB0 + AXPB_RMT_MIX, 0x8000);
    p32(PB0 + AXPB_NEXT_HI, VIRT(PB1));
    /* PB1 linked but stopped: skipped, list ends at its next == 0. */
    dc_voice(PB1, 12345);
    p16(PB1 + AXPB_STATE, 0);
    /* AUX A return: L 2000 constant (the game's callback output). */
    for (i = 0; i < 96u; ++i) p32(AUXA_RET + 4u * i, 2000);

    p16(a, AX_CMD_SETUP); p32(a + 2, VIRT(SETUP_BLK)); a += 6;
    p16(a, AX_CMD_ADD_TO_LR); p32(a + 2, VIRT(SURR)); a += 6;
    p16(a, AX_CMD_PROCESS); p32(a + 2, VIRT(PB0)); a += 6;
    p16(a, AX_CMD_MIX_AUXA); p16(a + 2, 0x4000); p32(a + 4, VIRT(AUXA_SEND)); p32(a + 8, VIRT(AUXA_RET)); a += 12;
    p16(a, AX_CMD_MIX_AUXB); p16(a + 2, 0x8000); p32(a + 4, 0); p32(a + 8, 0); a += 12;
    p16(a, AX_CMD_COMPRESSOR); p16(a + 2, 0x8000); p16(a + 4, 10); p32(a + 6, 0x804B7FE0u); a += 10;
    p16(a, AX_CMD_OUTPUT); p16(a + 2, 0x8000); p32(a + 4, VIRT(SURR)); p32(a + 8, VIRT(OUT)); a += 12;
    p16(a, AX_CMD_WM_OUTPUT);
    for (r = 0; r < 4u; ++r) p32(a + 2u + 4u * r, VIRT(RMT + 0x40u * r));
    a += 18;
    p16(a, AX_CMD_END);

    CHECK_EQ(ax_hle_process_command_list(&g_ax, VIRT(CL)), AX_HLE_OK);
    CHECK_EQ(g_ax.stats.commands, 9);
    CHECK_EQ(g_ax.stats.voices_last, 1);
    CHECK_EQ(g_ax.stats.bad_commands, 0);
    CHECK_EQ(g_ax.stats.memory_faults, 0);
    /* AUX A send: L = 1000 (int32 BE), R = S = 0. */
    for (i = 0; i < 96u; ++i) {
        CHECK_EQ((int32_t)g32(AUXA_SEND + 4u * i), 1000);
        CHECK_EQ((int32_t)g32(AUXA_SEND + 384u + 4u * i), 0);
        CHECK_EQ((int32_t)g32(AUXA_SEND + 768u + 4u * i), 0);
    }
    /* Output: L = depop + dry + AUX return * 0.5 + S * 0.7071;
               R = 500 + 707 (stereo fold of S = 1000). */
    for (i = 0; i < 96u; ++i) {
        CHECK_EQ(gs16(OUT + 4u * i), 960 - 10 * (int32_t)i + 1000 + 1000 + 707);
        CHECK_EQ(gs16(OUT + 4u * i + 2u), 500 + 707);
    }
    /* Remotes: 18 BE samples; remote 0 = voice 1000 + depop 50. */
    for (i = 0; i < 18u; ++i) {
        CHECK_EQ(gs16(RMT + 2u * i), 1050);
        CHECK_EQ(gs16(RMT + 0x40u + 2u * i), 0);
    }
    CHECK_EQ(gs16(RMT + 36u), 0); /* nothing past 18 samples */
    /* PB write-back: dpop (L, AL, R, S slots), state still running. */
    CHECK_EQ(gs16(PB0 + AXPB_DPOP + 0), 1000);
    CHECK_EQ(gs16(PB0 + AXPB_DPOP + 2), 1000);
    CHECK_EQ(gs16(PB0 + AXPB_DPOP + 8), 500);
    CHECK_EQ(gs16(PB0 + AXPB_DPOP + 16), 1000);
    CHECK_EQ(gs16(PB0 + AXPB_RMT_DPOP + 0), 1000);
    CHECK_EQ(g16(PB1 + AXPB_STATE), 0);
    CHECK_EQ(gs16(PB1 + AXPB_DPOP + 0), 0); /* stopped PB untouched */
    /* Hooks. */
    CHECK_EQ(g_aux_calls, 2);
    CHECK_EQ(g_aux_seen_send[0], 1000); /* AUX A bus as sent */
    CHECK_EQ(g_aux_seen_send[1], -1);   /* AUX B: no send address */
    CHECK_EQ(g_out_calls, 1);
    CHECK_EQ(g_out_first_l, 960 + 1000 + 1000 + 707);
    CHECK_EQ(g_rmt_calls, 1);
    CHECK_EQ(g_ax.stats.last_output_addr, OUT);
}

static void test_dpl2_and_errors(void) {
    uint32_t a = CL;
    reset_memory();
    init_ax(NULL);
    dc_voice(PB0, 1000);
    p32(PB0 + AXPB_MIXER_CTRL, AX_MIXCTRL_S);
    p16(PB0 + AXPB_MIX + 32, 0x8000);
    p16(a, AX_CMD_ADD_SUB_TO_LR); p32(a + 2, VIRT(SURR)); a += 6;
    p16(a, AX_CMD_PROCESS); p32(a + 2, VIRT(PB0)); a += 6;
    p16(a, AX_CMD_OUTPUT_DPL2); p16(a + 2, 0x8000); p32(a + 4, VIRT(SURR)); p32(a + 8, VIRT(OUT)); a += 12;
    p16(a, AX_CMD_END);
    CHECK_EQ(run_frame(), AX_HLE_OK);
    CHECK_EQ(gs16(OUT + 0), -((1000 * 26755) >> 15));
    CHECK_EQ(gs16(OUT + 2), (1000 * 18919) >> 15);
    /* Unknown opcode stops the list. */
    p16(CL, 0x0077);
    CHECK_EQ(run_frame(), AX_HLE_ERR_BAD_COMMAND);
    CHECK_EQ(g_ax.stats.bad_commands, 1);
    /* Unmapped PB address: faults counted, no crash, list ends. */
    a = CL;
    p16(a, AX_CMD_PROCESS); p32(a + 2, 0x81F00000u); a += 6;
    p16(a, AX_CMD_END);
    CHECK_EQ(run_frame(), AX_HLE_OK);
    CHECK(g_ax.stats.memory_faults > 0);
}

int main(void) {
    make_sine();
    test_adpcm_hand_vector();
    test_adpcm_pipeline_and_writeback();
    test_adpcm_loop();
    test_pcm();
    test_src();
    test_ramps();
    test_two_voice_saturation();
    test_full_command_list();
    test_dpl2_and_errors();
    if (g_failures != 0) {
        printf("m14_ax_hle_test: %d failure(s)\n", g_failures);
        return 1;
    }
    printf("m14_ax_hle_test: ok\n");
    return 0;
}
