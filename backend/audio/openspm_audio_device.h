/* OpenSPM audio device: the two Wii hardware agents behind AX (the DSP
 * interface's audio DMA and the DSP running the AX ucode), modelled at the
 * register level so the game's own SDK code (AIInitDMA/AIStartDMA,
 * __AIDHandler, DSPSendMailToDSP, __DSPHandler, __AXOutNewFrame ...) runs
 * unchanged. docs/AUDIO-PLAN.md "Stage 2" describes the protocol and the
 * addresses it was read from.
 *
 * What it does
 *   - Watches CPU writes to the DSP interface (0xCC0050xx): the audio DMA
 *     source/length/enable latches (0x5030/32/36) and the CPU->DSP mailbox
 *     (0x5000/02). AX mails 0xBABExxxx followed by the command-list address;
 *     on that second mail the list is executed at once by the AX HLE
 *     (openspm_ax_hle.h) over guest memory, and the "DSP" answers with the
 *     resume mail 0xDCD10001 plus a DSP interrupt, as the ucode would when it
 *     finishes a frame (__DSPHandler 0x80289B38 calls the task's resume
 *     callback for that mail).
 *   - Answers the DSP->CPU mailbox reads (0x5004/06) while that mail is
 *     pending, and the audio-DMA "blocks left" register (0x503A).
 *   - Keeps the audio clock: once DMA is enabled, one AI tick per 3 ms of
 *     guest time (96 stereo frames at 32 kHz). At a tick the currently
 *     programmed DMA buffer is latched (read out for the PC device) and the
 *     host must raise the AI DMA interrupt, whose handler (__AIDHandler
 *     0x8028521C -> __AXOutAiCallback) builds the next frame and programs the
 *     next buffer. Elapsed ticks are not burst-delivered: at most one per poll,
 *     and a backlog above OPENSPM_AUDIO_MAX_LAG_NS is dropped (resync).
 *
 * It never runs guest code and never blocks. The host (the native bridge)
 * forwards every access to the real platform model as before, asks
 * openspm_audio_device_poll() at its safepoints what to deliver, and runs
 * the guest interrupt handlers itself.
 *
 * Portable C11, no allocation; one device per game; not thread-safe. */
#ifndef OPENSPM_AUDIO_DEVICE_H
#define OPENSPM_AUDIO_DEVICE_H

#include <stdint.h>

#include "openspm_ax_hle.h"

#ifdef __cplusplus
extern "C" {
#endif

#define OPENSPM_AUDIO_RATE 32000u
#define OPENSPM_AUDIO_FRAME_NS UINT64_C(3000000)  /* 96 frames at 32 kHz */
#define OPENSPM_AUDIO_MAX_LAG_NS UINT64_C(48000000)
#define OPENSPM_AUDIO_MAX_FRAMES 1024u           /* largest latched DMA buffer handed out */

/* DSP interface registers (Broadway view). */
#define OPENSPM_AUDIO_DSP_MAIL_TO_HI UINT32_C(0xCC005000)
#define OPENSPM_AUDIO_DSP_MAIL_TO_LO UINT32_C(0xCC005002)
#define OPENSPM_AUDIO_DSP_MAIL_FROM_HI UINT32_C(0xCC005004)
#define OPENSPM_AUDIO_DSP_MAIL_FROM_LO UINT32_C(0xCC005006)
#define OPENSPM_AUDIO_DSP_CONTROL UINT32_C(0xCC00500A)
#define OPENSPM_AUDIO_DMA_START_HI UINT32_C(0xCC005030)
#define OPENSPM_AUDIO_DMA_START_LO UINT32_C(0xCC005032)
#define OPENSPM_AUDIO_DMA_CONTROL UINT32_C(0xCC005036)
#define OPENSPM_AUDIO_DMA_BLOCKS_LEFT UINT32_C(0xCC00503A)

/* AX <-> ucode mail words. */
#define OPENSPM_AUDIO_MAIL_AX_LIST_HI 0xBABEu            /* 0xBABExxxx, then the list address */
#define OPENSPM_AUDIO_MAIL_DSP_RESUME UINT32_C(0xDCD10001) /* __DSPHandler: task->res_cb(task) */

/* Actions returned by openspm_audio_device_poll(). */
enum {
    OPENSPM_AUDIO_ACTION_NONE = 0,
    OPENSPM_AUDIO_ACTION_DSP_IRQ = 1, /* raise the DSP interrupt (resume mail is pending) */
    OPENSPM_AUDIO_ACTION_AI_TICK = 2  /* call openspm_audio_device_ai_tick(), then raise AI DMA */
};

/* Event bits returned by openspm_audio_device_write() (for first-time logs). */
enum {
    OPENSPM_AUDIO_EV_DMA_PROGRAMMED = 1u << 0,
    OPENSPM_AUDIO_EV_DMA_STARTED = 1u << 1,
    OPENSPM_AUDIO_EV_DMA_STOPPED = 1u << 2,
    OPENSPM_AUDIO_EV_MAIL = 1u << 3,
    OPENSPM_AUDIO_EV_CMDLIST = 1u << 4,
    OPENSPM_AUDIO_EV_CMDLIST_ERROR = 1u << 5
};

typedef struct openspm_audio_device_stats {
    uint32_t ai_ticks;        /* AI ticks handed out (buffers latched)            */
    uint32_t dsp_irqs;        /* resume interrupts delivered                       */
    uint32_t cmdlists;        /* command lists executed by the HLE                 */
    uint32_t cmdlist_errors;  /* lists that returned an error                      */
    uint32_t mails_to_dsp;    /* every committed CPU->DSP mail                     */
    uint32_t list_mails;      /* 0xBABExxxx announcements                          */
    uint32_t mail_reads;      /* DSP->CPU mail words answered by the device        */
    uint32_t resyncs;         /* backlogs dropped (guest could not take AI ticks)   */
    uint32_t overruns;        /* a new list arrived while the last resume was unread */
    uint32_t nonsilent_ticks; /* latched buffers that were not all zero            */
    uint32_t last_mail;
    uint32_t last_cmdlist;
    int32_t last_rc;
} openspm_audio_device_stats;

typedef struct openspm_audio_device {
    ax_hle_state hle;
    ax_hle_flat_memory flat;
    ax_hle_memory mem;
    /* DSP interface audio-DMA latches, with the hardware write masks. */
    uint32_t dma_source; /* physical */
    uint16_t dma_control; /* NumBlocks[14:0], Enable[15] */
    uint32_t playing_source;
    uint32_t playing_bytes;
    int running;
    uint64_t next_tick_ns; /* 0 = arm at the next poll */
    /* CPU->DSP mailbox. */
    uint16_t mail_to_hi;
    int expect_list;
    /* DSP->CPU mailbox answer and interrupt. */
    uint32_t mail_from;
    int mail_pending;
    int dsp_irq_pending;
    openspm_audio_device_stats stats;
} openspm_audio_device;

/* mem1: MEM1 at physical 0, mem2: MEM2 at physical 0x10000000 (either may be NULL). */
void openspm_audio_device_init(openspm_audio_device *dev, uint8_t *mem1, uint32_t mem1_size,
                               uint8_t *mem2, uint32_t mem2_size);

/* A CPU store the host has already forwarded to its platform model. Returns
 * OPENSPM_AUDIO_EV_* bits. Addresses outside the DSP interface are ignored. */
unsigned openspm_audio_device_write(openspm_audio_device *dev, uint32_t address, uint32_t width,
                                    uint64_t value);

/* A CPU load. Returns 1 and sets *value when the device answers it (pending
 * DSP->CPU mail, DMA blocks left); 0 = let the platform model answer. */
int openspm_audio_device_read(openspm_audio_device *dev, uint32_t address, uint32_t width,
                              uint64_t *value);

/* DSPCheckMailFromDSP: 1 while the device's DSP->CPU mail is unread. */
int openspm_audio_device_mail_pending(const openspm_audio_device *dev);

/* What the host should deliver now (at most one action per call). */
int openspm_audio_device_poll(openspm_audio_device *dev, uint64_t now_ns);

/* Latch the programmed DMA buffer as "now playing" and advance the clock.
 * Writes up to OPENSPM_AUDIO_MAX_FRAMES interleaved host-order L,R frames to
 * out and returns the frame count (0: no valid buffer). */
unsigned openspm_audio_device_ai_tick(openspm_audio_device *dev, int16_t *out);

/* The host ran the DSP interrupt handler (the mail itself stays pending until
 * the handler reads it). */
void openspm_audio_device_dsp_delivered(openspm_audio_device *dev);

#ifdef __cplusplus
}
#endif

#endif
