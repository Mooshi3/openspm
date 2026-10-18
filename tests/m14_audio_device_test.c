/* Host integration test for backend/audio/openspm_audio_device.{h,c} and the
   renderer-side pacer in include/openspm/audio_out.h.

   A fake guest (MEM1 + MEM2 arrays) plays the game's side of the protocol the
   way R8PE01's SDK code does it (docs/AUDIO-PLAN.md "Stage 2"):
     __AXOutInit       build list 0 (output -> AI buffer 1), AIInitDMA(buffer 0),
                       AIStartDMA, DSP ready = 1
     AI DMA interrupt  __AXOutAiCallback: ready == 1 -> __AXOutNewFrame, else
                       ready = 2
     __AXOutNewFrame   read DMA blocks left, mail 0xBABE0080 + the list built
                       last frame, build the next list into the other buffer,
                       AIInitDMA(next buffer)
     DSP interrupt     __DSPHandler: DSPCheckMailFromDSP, read the mail
                       (expects 0xDCD10001), resume callback: ready == 2 ->
                       __AXOutNewFrame, else ready = 1
   with one looping PCM16 voice in MEM2 through real AX command lists at the
   game's own addresses. The PC side collects every latched buffer and checks
   that the voice comes out continuous, L then R, one tick late (the first
   tick plays the silent initial buffer). Also: DMA stop, backlog resync,
   ignored boot mails, register views, and the audio_out pacer (prebuffer,
   underrun vs pause gap, overflow drop, drift trim). */
#include "../backend/audio/openspm_audio_device.h"
#include "../include/openspm/audio_out.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int g_failures;
#define CHECK(cond)                                                          \
    do {                                                                     \
        if (!(cond)) {                                                       \
            printf("%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond); \
            g_failures++;                                                    \
        }                                                                    \
    } while (0)
#define CHECK_EQ(a, b)                                                                   \
    do {                                                                                 \
        long long a_ = (long long)(a), b_ = (long long)(b);                              \
        if (a_ != b_) {                                                                  \
            printf("%s:%d: %s = %lld, expected %lld\n", __FILE__, __LINE__, #a, a_, b_); \
            g_failures++;                                                                \
        }                                                                                \
    } while (0)

#define MEM1_SIZE 0x00600000u
#define MEM2_SIZE 0x00040000u
static uint8_t g_mem1[MEM1_SIZE];
static uint8_t g_mem2[MEM2_SIZE];

static uint8_t *gp(uint32_t a) {
    a &= 0x1FFFFFFFu;
    return a >= 0x10000000u ? g_mem2 + (a - 0x10000000u) : g_mem1 + a;
}
static void p16(uint32_t a, uint16_t v) { gp(a)[0] = (uint8_t)(v >> 8); gp(a)[1] = (uint8_t)v; }
static void p32(uint32_t a, uint32_t v) { p16(a, (uint16_t)(v >> 16)); p16(a + 2u, (uint16_t)v); }

/* The game's own layout (virtual, as the CPU code writes it). */
#define CL0 0x805108A0u
#define CL1 0x80510920u
#define SURR 0x80510F80u
#define AI_BUF 0x80511280u
#define SETUP_BLK 0x805117A0u
#define PB 0x80511820u
#define SAMPLES 0x10001000u /* MEM2, physical */
#define SAMPLE_COUNT 4000u
#define AI_BYTES 384u

static openspm_audio_device g_dev;

/* ---- fake guest ----------------------------------------------------- */

static int g_ready;       /* __AXOutDspReady (r13-28776)          */
static unsigned g_build;  /* index of the list built last          */
static unsigned g_outidx; /* __AXOutFrame (r13-28768), mode != 1   */
static uint32_t g_boot_mails;

static void mmio_w(uint32_t addr, uint32_t width, uint32_t value) {
    (void)openspm_audio_device_write(&g_dev, addr, width, value);
}

static uint32_t mmio_r(uint32_t addr, uint32_t width, int *answered) {
    uint64_t v = 0;
    *answered = openspm_audio_device_read(&g_dev, addr, width, &v);
    return (uint32_t)v;
}

static void send_mail(uint32_t mail) {
    mmio_w(OPENSPM_AUDIO_DSP_MAIL_TO_HI, 2u, mail >> 16);
    mmio_w(OPENSPM_AUDIO_DSP_MAIL_TO_LO, 2u, mail & 0xFFFFu);
}

static void ai_init_dma(uint32_t addr, uint32_t len) {
    /* AIInitDMA 0x80284FE8: hi, lo, then blocks with the enable bit kept. */
    mmio_w(OPENSPM_AUDIO_DMA_START_HI, 2u, addr >> 16);
    mmio_w(OPENSPM_AUDIO_DMA_START_LO, 2u, addr & 0xFFFFu);
    mmio_w(OPENSPM_AUDIO_DMA_CONTROL, 2u, (g_dev.dma_control & 0x8000u) | (len >> 5));
}

static void build_list(unsigned which, uint32_t out) {
    uint32_t a = which ? CL1 : CL0;
    p16(a, AX_CMD_SETUP); p32(a + 2, SETUP_BLK); a += 6;
    p16(a, AX_CMD_PROCESS); p32(a + 2, PB); a += 6;
    p16(a, AX_CMD_OUTPUT); p16(a + 2, 0x8000); p32(a + 4, SURR); p32(a + 8, out); a += 12;
    p16(a, AX_CMD_END);
}

static void new_frame(void) {
    int answered = 0;
    const uint32_t left = mmio_r(OPENSPM_AUDIO_DMA_BLOCKS_LEFT, 2u, &answered);
    CHECK(answered);
    CHECK_EQ(left, AI_BYTES / 32u);
    send_mail(0xBABE0080u);
    send_mail(g_build ? CL1 : CL0);
    g_build ^= 1u;
    build_list(g_build, AI_BUF + g_outidx * AI_BYTES);
    g_outidx = (g_outidx + 1u) & 1u;
    ai_init_dma(AI_BUF + g_outidx * AI_BYTES, AI_BYTES);
}

static void ax_out_init(void) {
    uint32_t a;
    memset(gp(SETUP_BLK), 0, 120);
    memset(gp(AI_BUF), 0, 3u * AI_BYTES);
    /* One looping PCM16 voice: SRC none, ratio 1.0, VE 1.0, main L 1.0. */
    memset(gp(PB), 0, AX_HLE_PB_SIZE);
    p32(PB + AXPB_THIS_HI, PB);
    p16(PB + AXPB_SRC_TYPE, AX_SRC_NONE);
    p32(PB + AXPB_MIXER_CTRL, AX_MIXCTRL_L);
    p16(PB + AXPB_STATE, 1);
    p16(PB + AXPB_MIX + 0, 0x8000);
    p16(PB + AXPB_VE_VOL, 0x8000);
    p16(PB + AXPB_LOOP_FLAG, 1);
    p16(PB + AXPB_FORMAT, AX_FORMAT_PCM16);
    p32(PB + AXPB_LOOP_ADDR, SAMPLES / 2u);
    p32(PB + AXPB_END_ADDR, SAMPLES / 2u + SAMPLE_COUNT - 1u);
    p32(PB + AXPB_CUR_ADDR, SAMPLES / 2u);
    p16(PB + AXPB_ADPCM_GAIN, 0x0800);
    p32(PB + AXPB_SRC_RATIO, 0x00010000u);
    for (a = 0; a < SAMPLE_COUNT; ++a) p16(SAMPLES + a * 2u, (uint16_t)(int16_t)((int)(a % 997u) * 13 - 6000));
    /* __DSP_boot_task style mails before AX: must not be taken for lists. */
    send_mail(0x80F3A001u);
    send_mail(0x00000CD2u);
    send_mail(0x80F3C002u);
    g_boot_mails = 3u;
    /* __AXOutInit 0x8028720C (mode != 1). */
    g_build = 0u;
    build_list(0u, AI_BUF + AI_BYTES);
    g_outidx = 0u;
    ai_init_dma(AI_BUF, AI_BYTES);
    mmio_w(OPENSPM_AUDIO_DMA_CONTROL, 2u, g_dev.dma_control | 0x8000u); /* AIStartDMA */
    g_ready = 1;
}

static void ai_interrupt(void) { /* __AIDHandler -> __AXOutAiCallback */
    if (g_ready == 1) {
        g_ready = 0;
        new_frame();
    } else {
        g_ready = 2;
    }
}

static void dsp_interrupt(void) { /* __DSPHandler, mail 0xDCD10001 -> res_cb */
    int a1 = 0, a2 = 0;
    uint32_t hi, lo;
    CHECK(openspm_audio_device_mail_pending(&g_dev));
    hi = mmio_r(OPENSPM_AUDIO_DSP_MAIL_FROM_HI, 2u, &a1);
    lo = mmio_r(OPENSPM_AUDIO_DSP_MAIL_FROM_LO, 2u, &a2);
    CHECK(a1 && a2);
    CHECK_EQ((hi << 16) | lo, 0xDCD10001u);
    CHECK(!openspm_audio_device_mail_pending(&g_dev));
    openspm_audio_device_dsp_delivered(&g_dev);
    if (g_ready == 2) {
        g_ready = 0;
        new_frame();
    } else {
        g_ready = 1;
    }
}

/* ---- PC side -------------------------------------------------------- */

#define MAX_OUT (96u * 2000u)
static int16_t g_pc[MAX_OUT * 2u];
static unsigned g_pc_frames;

static void run_until(uint64_t *now, uint64_t end, uint64_t step) {
    int16_t buf[OPENSPM_AUDIO_MAX_FRAMES * 2u];
    for (; *now < end; *now += step) {
        int action;
        /* Deliver everything due at this safepoint, one action per poll. */
        while ((action = openspm_audio_device_poll(&g_dev, *now)) != OPENSPM_AUDIO_ACTION_NONE) {
            if (action == OPENSPM_AUDIO_ACTION_DSP_IRQ) {
                dsp_interrupt();
            } else {
                const unsigned n = openspm_audio_device_ai_tick(&g_dev, buf);
                CHECK_EQ(n, 96u);
                if (g_pc_frames + n <= MAX_OUT) {
                    memcpy(g_pc + g_pc_frames * 2u, buf, n * 4u);
                    g_pc_frames += n;
                }
                ai_interrupt();
            }
        }
    }
}

/* The HLE's SRC has a fixed 3-sample latency (docs/AUDIO-PLAN.md, HLE choices). */
#define SRC_LATENCY 3u
static int16_t expected_sample(unsigned i) {
    if (i < SRC_LATENCY) return 0;
    i -= SRC_LATENCY;
    return (int16_t)((int)((i % SAMPLE_COUNT) % 997u) * 13 - 6000);
}

static void test_protocol(void) {
    uint64_t now = 1000000000u;
    unsigned i, mismatches = 0;
    openspm_audio_device_init(&g_dev, g_mem1, MEM1_SIZE, g_mem2, MEM2_SIZE);
    CHECK_EQ(openspm_audio_device_poll(&g_dev, now), OPENSPM_AUDIO_ACTION_NONE); /* DMA off */
    ax_out_init();
    CHECK(g_dev.running);
    CHECK_EQ(g_dev.stats.mails_to_dsp, g_boot_mails);
    CHECK_EQ(g_dev.stats.cmdlists, 0);
    CHECK_EQ(g_dev.dma_source, AI_BUF & 0x1FFFFFFFu);

    /* 300 ms of guest time polled every 1 ms: one tick per 3 ms. */
    run_until(&now, now + 300000000u, 1000000u);
    CHECK(g_dev.stats.ai_ticks >= 98u && g_dev.stats.ai_ticks <= 100u);
    CHECK_EQ(g_dev.stats.cmdlists, g_dev.stats.ai_ticks);
    CHECK_EQ(g_dev.stats.dsp_irqs, g_dev.stats.cmdlists);
    CHECK_EQ(g_dev.stats.cmdlist_errors, 0);
    CHECK_EQ(g_dev.stats.overruns, 0);
    CHECK_EQ(g_dev.stats.resyncs, 0);
    CHECK_EQ(g_dev.hle.stats.voices_last, 1);
    CHECK_EQ(g_dev.stats.nonsilent_ticks, g_dev.stats.ai_ticks - 1u);

    /* First tick: the zeroed initial buffer. Then the voice, continuous. */
    CHECK(g_pc_frames >= 96u * 90u);
    for (i = 0; i < 96u; ++i) CHECK(g_pc[i * 2u] == 0 && g_pc[i * 2u + 1u] == 0);
    for (i = 96u; i < g_pc_frames; ++i) {
        if (g_pc[i * 2u] != expected_sample(i - 96u) || g_pc[i * 2u + 1u] != 0) {
            if (mismatches < 4u)
                printf("frame %u: got L=%d R=%d, expected L=%d R=0\n", i, g_pc[i * 2u], g_pc[i * 2u + 1u],
                       expected_sample(i - 96u));
            mismatches++;
        }
    }
    CHECK_EQ(mismatches, 0);

    /* A stall longer than the lag bound: one resync, no burst. */
    {
        const uint32_t ticks = g_dev.stats.ai_ticks;
        now += 200000000u;
        run_until(&now, now + 1000000u, 1000000u);
        CHECK_EQ(g_dev.stats.resyncs, 1);
        CHECK(g_dev.stats.ai_ticks - ticks <= 1u);
    }

    /* AIStopDMA 0x80285078: no more ticks. */
    mmio_w(OPENSPM_AUDIO_DMA_CONTROL, 2u, g_dev.dma_control & 0x7FFFu);
    CHECK(!g_dev.running);
    {
        const uint32_t ticks = g_dev.stats.ai_ticks;
        run_until(&now, now + 30000000u, 1000000u);
        CHECK_EQ(g_dev.stats.ai_ticks, ticks);
    }
}

static void test_registers(void) {
    uint64_t v = 0;
    openspm_audio_device_init(&g_dev, g_mem1, MEM1_SIZE, g_mem2, MEM2_SIZE);
    /* No pending mail: the platform model answers mailbox reads. */
    CHECK(!openspm_audio_device_read(&g_dev, OPENSPM_AUDIO_DSP_MAIL_FROM_HI, 2u, &v));
    CHECK(!openspm_audio_device_read(&g_dev, OPENSPM_AUDIO_DSP_MAIL_FROM_LO, 2u, &v));
    CHECK(!openspm_audio_device_read(&g_dev, OPENSPM_AUDIO_DSP_CONTROL, 2u, &v));
    CHECK(openspm_audio_device_read(&g_dev, OPENSPM_AUDIO_DMA_BLOCKS_LEFT, 2u, &v) && v == 0u);
    /* Hardware write masks of the DMA source. */
    mmio_w(OPENSPM_AUDIO_DMA_START_HI, 2u, 0x8051u);
    mmio_w(OPENSPM_AUDIO_DMA_START_LO, 2u, 0x129Fu);
    CHECK_EQ(g_dev.dma_source, 0x00511280u);
    /* A 0xBABE announcement followed by a list: the list runs even when it is
       empty (END only), and the resume mail becomes readable as one word. */
    memset(gp(CL0), 0, 16);
    p16(CL0, AX_CMD_END);
    CHECK(openspm_audio_device_write(&g_dev, OPENSPM_AUDIO_DSP_MAIL_TO_HI, 4u, 0xBABE0080u) &
          OPENSPM_AUDIO_EV_MAIL);
    CHECK(openspm_audio_device_write(&g_dev, OPENSPM_AUDIO_DSP_MAIL_TO_HI, 4u, CL0) &
          OPENSPM_AUDIO_EV_CMDLIST);
    CHECK_EQ(g_dev.stats.cmdlists, 1);
    CHECK_EQ(openspm_audio_device_poll(&g_dev, 5u), OPENSPM_AUDIO_ACTION_DSP_IRQ);
    CHECK(openspm_audio_device_read(&g_dev, OPENSPM_AUDIO_DSP_MAIL_FROM_HI, 4u, &v));
    CHECK_EQ(v, 0xDCD10001u);
    CHECK(!openspm_audio_device_mail_pending(&g_dev));
    /* A second list before the first resume was read counts an overrun. */
    (void)openspm_audio_device_write(&g_dev, OPENSPM_AUDIO_DSP_MAIL_TO_HI, 4u, 0xBABE0080u);
    (void)openspm_audio_device_write(&g_dev, OPENSPM_AUDIO_DSP_MAIL_TO_HI, 4u, CL0);
    (void)openspm_audio_device_write(&g_dev, OPENSPM_AUDIO_DSP_MAIL_TO_HI, 4u, 0xBABE0080u);
    (void)openspm_audio_device_write(&g_dev, OPENSPM_AUDIO_DSP_MAIL_TO_HI, 4u, CL0);
    CHECK_EQ(g_dev.stats.overruns, 1);
    /* A broken list is reported, still answered (AX must not hang). */
    p16(CL1, 0x00FFu);
    openspm_audio_device_init(&g_dev, g_mem1, MEM1_SIZE, g_mem2, MEM2_SIZE);
    (void)openspm_audio_device_write(&g_dev, OPENSPM_AUDIO_DSP_MAIL_TO_HI, 4u, 0xBABE0080u);
    CHECK(openspm_audio_device_write(&g_dev, OPENSPM_AUDIO_DSP_MAIL_TO_HI, 4u, CL1) &
          OPENSPM_AUDIO_EV_CMDLIST_ERROR);
    CHECK(openspm_audio_device_mail_pending(&g_dev));
}

static void test_pacer(void) {
    openspm_audio_pacer p;
    uint32_t queued = 0;
    uint64_t now = 1000000000u;
    unsigned flags, i, started_after = 0;
    openspm_audio_pacer_init(&p, 32000u);
    CHECK_EQ(p.target_frames, 1280u);
    /* Prebuffer: paused until 40 ms are queued. */
    for (i = 1; i <= 20u; ++i) {
        flags = openspm_audio_pacer_push(&p, queued, 96u, now);
        CHECK(flags & OPENSPM_AUDIO_PACE_PUT);
        queued += 96u;
        now += 3000000u;
        if ((flags & OPENSPM_AUDIO_PACE_START) && started_after == 0u) started_after = i;
    }
    CHECK_EQ(started_after, 14u); /* 14 * 96 = 1344 >= 1280 */
    CHECK(p.playing);
    /* Steady state at the target: no trim. */
    for (i = 0; i < 200u; ++i) {
        (void)openspm_audio_pacer_push(&p, 1280u, 96u, now);
        now += 3000000u;
    }
    CHECK(p.trim_ppm <= 100 && p.trim_ppm >= -100);
    /* Queue drifting high: play faster; low: slower (bounded). */
    for (i = 0; i < 400u; ++i) {
        (void)openspm_audio_pacer_push(&p, 2560u, 96u, now);
        now += 3000000u;
    }
    CHECK(p.trim_ppm > 0 && p.trim_ppm <= OPENSPM_AUDIO_TRIM_PPM);
    for (i = 0; i < 400u; ++i) {
        (void)openspm_audio_pacer_push(&p, 200u, 96u, now);
        now += 3000000u;
    }
    CHECK(p.trim_ppm < 0 && p.trim_ppm >= -OPENSPM_AUDIO_TRIM_PPM);
    /* Ran dry while pushing every 3 ms: an underrun, device paused. */
    flags = openspm_audio_pacer_push(&p, 0u, 96u, now);
    CHECK(flags & OPENSPM_AUDIO_PACE_STOP);
    CHECK_EQ(p.underruns, 1);
    CHECK(!p.playing);
    /* Refill, then a long producer gap (menu pause): a resync, not an underrun. */
    for (queued = 96u; queued < 1400u; queued += 96u) {
        now += 3000000u;
        (void)openspm_audio_pacer_push(&p, queued, 96u, now);
    }
    CHECK(p.playing);
    now += 500000000u;
    flags = openspm_audio_pacer_push(&p, 0u, 96u, now);
    CHECK(flags & OPENSPM_AUDIO_PACE_STOP);
    CHECK_EQ(p.underruns, 1);
    CHECK_EQ(p.resyncs, 1);
    /* Overflow: dropped, never blocks. */
    flags = openspm_audio_pacer_push(&p, p.max_frames, 96u, now + 3000000u);
    CHECK(flags & OPENSPM_AUDIO_PACE_DROP);
    CHECK(!(flags & OPENSPM_AUDIO_PACE_PUT));
    CHECK_EQ(p.dropped_frames, 96u);
    /* Status query (0 frames) changes nothing. */
    CHECK_EQ(openspm_audio_pacer_push(&p, 500u, 0u, now + 6000000u), 0u);
}

int main(void) {
    test_protocol();
    test_registers();
    test_pacer();
    if (g_failures != 0) {
        printf("m14_audio_device_test: %d failure(s)\n", g_failures);
        return 1;
    }
    printf("m14_audio_device_test: PASS\n");
    return 0;
}
