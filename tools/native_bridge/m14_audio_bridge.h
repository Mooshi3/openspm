/* M14 opt-in audio (OPENSPM_AUDIO=1): stage 2 of docs/AUDIO-PLAN.md.
 *
 * Included once by legacy_c_adapter_v42.c.in (LLVM backend only). With
 * OPENSPM_AUDIO unset nothing here runs: no MMIO callback, host-call service
 * or run path is wrapped, so the silent path is byte-for-byte the previous
 * one (AI DMA programmed but never clocked, the DSP only booted by the v20
 * task-init semantic, no AX frame ever mixed).
 *
 * With OPENSPM_AUDIO=1 the bridge owns the two missing hardware agents
 * through backend/audio/openspm_audio_device.{h,c}, at the register level, so
 * all of the game's SDK code runs unchanged:
 *   - external_write: every DSP-interface store is still forwarded to the
 *     backend's platform model first, then shown to the device (DMA latches,
 *     CPU->DSP mail; the AX command list mailed after 0xBABE0080 is mixed by
 *     the AX HLE right there, on the game thread, before the next
 *     __AXSyncPBs);
 *   - external_read: the device answers the DSP->CPU mail it posted
 *     (0xDCD10001) and the DMA blocks-left register (0xCC00503A, read by
 *     __AXOutNewFrame via 0x8028508C); everything else goes to the backend;
 *   - host-call 0x8028987C: this manifest target is DSPCheckMailFromDSP (see
 *     legacy_c_adapter_v20.c.in). While the device's mail is pending it
 *     answers 1 (the only caller that sees that state is __DSPHandler at
 *     0x80289B74); otherwise the backend's historical no-op still answers,
 *     so the boot path (__DSP_boot_task 0x8028A130) is unchanged;
 *   - run(): at each top-level safepoint, before the underlying chain (and
 *     its retrace service), deliver what the device's clock says is due, as
 *     guest interrupts on scratch event CPUs exactly like the VI retrace:
 *     __DSPHandler(7, context) for the resume mail, then one AI tick:
 *     latch the programmed DMA buffer, push it to the renderer's SDL stream,
 *     and run __AIDHandler(5, context) (-> __AXOutAiCallback, which builds
 *     and mails the next frame and re-arms DMA). Deferred while MSR[EE] is
 *     clear; at most one AI tick per safepoint, a backlog above 48 ms is
 *     dropped. The game never waits for audio and its clock is never changed.
 *
 * The audio clock is the module's guest clock (host->monotonic_ns, which the
 * backend freezes while the OpenSPM menu pauses the game), so ticks stop with
 * the game; the renderer pauses and clears its stream when the menu pauses. */
#ifndef OPENSPM_M14_AUDIO_BRIDGE_H
#define OPENSPM_M14_AUDIO_BRIDGE_H

#include "../../backend/audio/openspm_audio_device.h"
#include "openspm/audio_out.h"

/* R8PE01 rev 2 SDK coordinates (docs/AUDIO-PLAN.md, Stage 2). */
#define OPENSPM_M14_AUDIO_AID_HANDLER UINT32_C(0x8028521C)  /* __AIDHandler (AIInit: interrupt 5) */
#define OPENSPM_M14_AUDIO_DSP_HANDLER UINT32_C(0x80289B38)  /* __DSPHandler (DSPInit: interrupt 7) */
#define OPENSPM_M14_AUDIO_DSP_AI_INTERRUPT UINT32_C(5)      /* __OS_INTERRUPT_DSP_AI */
#define OPENSPM_M14_AUDIO_DSP_DSP_INTERRUPT UINT32_C(7)     /* __OS_INTERRUPT_DSP_DSP */
#define OPENSPM_M14_AUDIO_HANDLER_PROLOGUE UINT32_C(0x9421FD20) /* stwu r1,-736(r1): both handlers */
#define OPENSPM_M14_AUDIO_CHECK_MAIL_FROM_DSP UINT32_C(0x8028987C)
#define OPENSPM_M14_AUDIO_AID_CALLBACK_SDA UINT32_C(0x00007118) /* r13-28952: __AID_Callback */
#define OPENSPM_M14_AUDIO_AX_TASK UINT32_C(0x80511700)          /* AX DSPTaskInfo */
#define OPENSPM_M14_AUDIO_AX_TASK_RES_CB_OFF UINT32_C(44)
#define OPENSPM_M14_AUDIO_REPORT_NS UINT64_C(1000000000)

enum {
    OPENSPM_M14_AUDIO_FIRST_DMA_PROGRAMMED = 1u << 0,
    OPENSPM_M14_AUDIO_FIRST_DMA_STARTED = 1u << 1,
    OPENSPM_M14_AUDIO_FIRST_MAIL = 1u << 2,
    OPENSPM_M14_AUDIO_FIRST_CMDLIST = 1u << 3,
    OPENSPM_M14_AUDIO_FIRST_AI_IRQ = 1u << 4,
    OPENSPM_M14_AUDIO_FIRST_DSP_IRQ = 1u << 5,
    OPENSPM_M14_AUDIO_FIRST_MAIL_CHECK = 1u << 6,
    OPENSPM_M14_AUDIO_FIRST_SOUND = 1u << 7,
    OPENSPM_M14_AUDIO_FIRST_DMA_STOPPED = 1u << 8,
    OPENSPM_M14_AUDIO_FIRST_CMDLIST_ERROR = 1u << 9,
    OPENSPM_M14_AUDIO_FIRST_BLOCKS_LEFT = 1u << 10,
    OPENSPM_M14_AUDIO_FIRST_DEFERRED = 1u << 11
};

typedef struct bridge_m14_audio_state {
    int attached;
    int disabled; /* signature mismatch: inert, the game stays silent */
    int checked;
    openspm_bridge_instance *instance;
    openspm_audio_device dev;
    PPCExternalRead underlying_read;
    PPCExternalWrite underlying_write;
    openspm_m14_hostcall_handle_fn underlying_handle;
    void *underlying_context;
    openspm_renderer_audio_push_fn push;
    int push_resolved;
    int push_failed;
    openspm_audio_out_status out;
    uint32_t first;
    uint64_t next_report_ns;
    uint32_t ai_events;
    uint32_t dsp_events;
    uint32_t deferred_ee;
    uint32_t mail_checks;
    uint32_t blocks_left_reads;
    uint32_t pushed_frames;
    uint32_t prev_ai_events;
    uint32_t prev_cmdlists;
    uint32_t prev_voices_mixed;
    uint32_t prev_underruns;
    /* Diagnostics: host time spent per second in the guest AI / DSP interrupt
       handlers (the AI one includes the AX frame build and the HLE mix it
       mails) and in DSP-interface writes (the HLE mix itself). */
    uint64_t ai_cost_ns, dsp_cost_ns, mmio_cost_ns;
    uint32_t prev_voices_started, prev_voices_ended, prev_voices_short;
    uint32_t notes_logged;
    int16_t pcm[OPENSPM_AUDIO_MAX_FRAMES * 2u];
} bridge_m14_audio_state;

static bridge_m14_audio_state g_m14_audio;

static int bridge_m14_audio_requested(void) {
    char value[16];
    if (!bridge_m14_read_env("OPENSPM_AUDIO", value, sizeof(value))) return 0;
    return strcmp(value, "1") == 0 || strcmp(value, "on") == 0 || strcmp(value, "ON") == 0;
}

static void bridge_m14_audio_log(const char *message) {
    if (g_m14_audio.instance != NULL) bridge_v44_log(g_m14_audio.instance, message);
}

static int bridge_m14_audio_first(uint32_t bit) {
    if ((g_m14_audio.first & bit) != 0u) return 0;
    g_m14_audio.first |= bit;
    return 1;
}

static void bridge_m14_audio_note_events(unsigned events) {
    char message[256];
    const openspm_audio_device *dev = &g_m14_audio.dev;
    if ((events & OPENSPM_AUDIO_EV_DMA_PROGRAMMED) &&
        bridge_m14_audio_first(OPENSPM_M14_AUDIO_FIRST_DMA_PROGRAMMED)) {
        (void)snprintf(message, sizeof(message),
                       "M14 audio: first AIInitDMA seen src=0x%08X blocks=%u (%u bytes)",
                       (unsigned)dev->dma_source, (unsigned)(dev->dma_control & 0x7FFFu),
                       (unsigned)(dev->dma_control & 0x7FFFu) * 32u);
        bridge_m14_audio_log(message);
    }
    if ((events & OPENSPM_AUDIO_EV_DMA_STARTED) &&
        bridge_m14_audio_first(OPENSPM_M14_AUDIO_FIRST_DMA_STARTED)) {
        (void)snprintf(message, sizeof(message),
                       "M14 audio: AIStartDMA seen (DMA enable) src=0x%08X; AI clock armed, one tick per 3 ms of guest time",
                       (unsigned)dev->dma_source);
        bridge_m14_audio_log(message);
    }
    if ((events & OPENSPM_AUDIO_EV_DMA_STOPPED) &&
        bridge_m14_audio_first(OPENSPM_M14_AUDIO_FIRST_DMA_STOPPED))
        bridge_m14_audio_log("M14 audio: AIStopDMA seen; AI clock stopped");
    if ((events & OPENSPM_AUDIO_EV_MAIL) && bridge_m14_audio_first(OPENSPM_M14_AUDIO_FIRST_MAIL)) {
        (void)snprintf(message, sizeof(message), "M14 audio: first CPU->DSP mail 0x%08X",
                       (unsigned)dev->stats.last_mail);
        bridge_m14_audio_log(message);
    }
    if ((events & OPENSPM_AUDIO_EV_CMDLIST) && bridge_m14_audio_first(OPENSPM_M14_AUDIO_FIRST_CMDLIST)) {
        (void)snprintf(message, sizeof(message),
                       "M14 audio: first AX command list 0x%08X mixed by the HLE rc=%d commands=%u voices=%u; DSP resume mail 0xDCD10001 posted",
                       (unsigned)dev->stats.last_cmdlist, (int)dev->stats.last_rc,
                       (unsigned)dev->hle.stats.commands, (unsigned)dev->hle.stats.voices_last);
        bridge_m14_audio_log(message);
    }
    if ((events & OPENSPM_AUDIO_EV_CMDLIST_ERROR) &&
        bridge_m14_audio_first(OPENSPM_M14_AUDIO_FIRST_CMDLIST_ERROR)) {
        (void)snprintf(message, sizeof(message),
                       "M14 audio: AX command list 0x%08X failed rc=%d last_command=0x%04X bad_commands=%u memory_faults=%u (answered anyway)",
                       (unsigned)dev->stats.last_cmdlist, (int)dev->stats.last_rc,
                       (unsigned)dev->hle.stats.last_command, (unsigned)dev->hle.stats.bad_commands,
                       (unsigned)dev->hle.stats.memory_faults);
        bridge_m14_audio_log(message);
    }
}

static u64 bridge_m14_audio_external_read(CPUState *cpu, u32 address, u8 width) {
    uint64_t value = 0u;
    if (!g_m14_audio.disabled &&
        openspm_audio_device_read(&g_m14_audio.dev, (uint32_t)address, (uint32_t)width, &value)) {
        if ((uint32_t)address == OPENSPM_AUDIO_DMA_BLOCKS_LEFT) {
            ++g_m14_audio.blocks_left_reads;
            if (bridge_m14_audio_first(OPENSPM_M14_AUDIO_FIRST_BLOCKS_LEFT))
                bridge_m14_audio_log("M14 audio: first DMA blocks-left read (0xCC00503A) answered by the device");
        }
        return (u64)value;
    }
    return g_m14_audio.underlying_read != NULL ? g_m14_audio.underlying_read(cpu, address, width) : 0u;
}

static void bridge_m14_audio_external_write(CPUState *cpu, u32 address, u64 value, u8 width) {
    if (g_m14_audio.underlying_write != NULL) g_m14_audio.underlying_write(cpu, address, value, width);
    if (!g_m14_audio.disabled && ((uint32_t)address & UINT32_C(0xFFFFFF00)) == UINT32_C(0xCC005000)) {
        const uint64_t start = bridge_m14_now_ns(g_m14_audio.instance);
        const unsigned events =
            openspm_audio_device_write(&g_m14_audio.dev, (uint32_t)address, (uint32_t)width, (uint64_t)value);
        const uint64_t end = bridge_m14_now_ns(g_m14_audio.instance);
        if (end > start) g_m14_audio.mmio_cost_ns += end - start;
        if (events != 0u) bridge_m14_audio_note_events(events);
    }
}

static int OPENSPM_CALL bridge_m14_audio_hostcall(void *context, uint32_t target_address,
                                                  openspm_m14_hostcall_state *state) {
    (void)context;
    if (target_address == OPENSPM_M14_AUDIO_CHECK_MAIL_FROM_DSP && state != NULL && !g_m14_audio.disabled &&
        openspm_audio_device_mail_pending(&g_m14_audio.dev)) {
        ++g_m14_audio.mail_checks;
        if (bridge_m14_audio_first(OPENSPM_M14_AUDIO_FIRST_MAIL_CHECK)) {
            char message[160];
            (void)snprintf(message, sizeof(message),
                           "M14 audio: DSPCheckMailFromDSP (0x8028987C) answered 1 for the pending resume mail, caller lr=0x%08X",
                           (unsigned)state->lr);
            bridge_m14_audio_log(message);
        }
        state->gpr[3] = 1u;
        state->pc = state->lr;
        return OPENSPM_OK;
    }
    if (g_m14_audio.underlying_handle == NULL) return OPENSPM_ERROR_NOT_FOUND;
    return g_m14_audio.underlying_handle(g_m14_audio.underlying_context, target_address, state);
}

static void bridge_m14_audio_resolve_output(void) {
    if (g_m14_audio.push_resolved) return;
    g_m14_audio.push_resolved = 1;
#ifdef _WIN32
    {
        HMODULE renderer = GetModuleHandleA("openspm_renderer.dll");
        if (renderer != NULL)
            g_m14_audio.push = (openspm_renderer_audio_push_fn)(void *)GetProcAddress(
                renderer, OPENSPM_RENDERER_AUDIO_PUSH_EXPORT);
    }
#endif
    bridge_m14_audio_log(g_m14_audio.push != NULL
                             ? "M14 audio: renderer output found (openspm_renderer_audio_push, SDL3 stream 32 kHz stereo s16)"
                             : "M14 audio: renderer has no openspm_renderer_audio_push export; AX runs but nothing is played");
}

static void bridge_m14_audio_output(const int16_t *lr, unsigned frames) {
    bridge_m14_audio_resolve_output();
    if (g_m14_audio.push == NULL || g_m14_audio.push_failed) return;
    if (g_m14_audio.push(lr, (uint32_t)frames, OPENSPM_AUDIO_RATE, &g_m14_audio.out) != 0) {
        g_m14_audio.push_failed = 1;
        bridge_m14_audio_log("M14 audio: renderer could not open an audio device; continuing without sound");
        return;
    }
    g_m14_audio.pushed_frames += frames;
}

/* One-time check of the exact SDK shape before the first guest interrupt. */
static int bridge_m14_audio_check_signatures(CPUState *cpu) {
    char message[320];
    uint32_t aid = 0u, dsp = 0u, callback = 0u, res_cb = 0u;
    if (g_m14_audio.checked) return !g_m14_audio.disabled;
    g_m14_audio.checked = 1;
    if (bridge_m14_context_address_valid(cpu, OPENSPM_M14_AUDIO_AID_HANDLER, 4u))
        aid = mem_read32(cpu, OPENSPM_M14_AUDIO_AID_HANDLER);
    if (bridge_m14_context_address_valid(cpu, OPENSPM_M14_AUDIO_DSP_HANDLER, 4u))
        dsp = mem_read32(cpu, OPENSPM_M14_AUDIO_DSP_HANDLER);
    if (bridge_m14_context_address_valid(cpu, (uint32_t)cpu->gpr[13] - OPENSPM_M14_AUDIO_AID_CALLBACK_SDA, 4u))
        callback = mem_read32(cpu, (uint32_t)cpu->gpr[13] - OPENSPM_M14_AUDIO_AID_CALLBACK_SDA);
    if (bridge_m14_context_address_valid(cpu, OPENSPM_M14_AUDIO_AX_TASK + OPENSPM_M14_AUDIO_AX_TASK_RES_CB_OFF, 4u))
        res_cb = mem_read32(cpu, OPENSPM_M14_AUDIO_AX_TASK + OPENSPM_M14_AUDIO_AX_TASK_RES_CB_OFF);
    if (aid != OPENSPM_M14_AUDIO_HANDLER_PROLOGUE || dsp != OPENSPM_M14_AUDIO_HANDLER_PROLOGUE) {
        g_m14_audio.disabled = 1;
        (void)snprintf(message, sizeof(message),
                       "M14 audio: disabled: R8PE01 __AIDHandler/__DSPHandler signature changed (0x%08X/0x%08X); the game stays silent",
                       (unsigned)aid, (unsigned)dsp);
        bridge_m14_audio_log(message);
        return 0;
    }
    (void)snprintf(message, sizeof(message),
                   "M14 audio: SDK shape verified: __AIDHandler=0x%08X __DSPHandler=0x%08X AI DMA callback (AIRegisterDMACallback)=0x%08X AX task res_cb=0x%08X",
                   (unsigned)OPENSPM_M14_AUDIO_AID_HANDLER, (unsigned)OPENSPM_M14_AUDIO_DSP_HANDLER,
                   (unsigned)callback, (unsigned)res_cb);
    bridge_m14_audio_log(message);
    return 1;
}

/* The game's SFX slots (spsnd, R8PE01 rev 2): 32 x 60 bytes at 0x804F1710 +
   856, +0 sound entry (0 = free), +4 / +8 NW4R sound handles. __spsndSFXOn
   (0x80238C34) only takes a free slot or steals a lower-priority one, so when
   every slot stays taken most sound effects are dropped. Diagnostics: how many
   are taken, and how many of those still hold a live handle. */
#define OPENSPM_M14_SPSND_SFX_SLOTS 0x804F1A68u
#define OPENSPM_M14_SPSND_SFX_SLOT_COUNT 32u
#define OPENSPM_M14_SPSND_SFX_SLOT_SIZE 60u
static void bridge_m14_audio_sfx_slots(CPUState *cpu, unsigned *used, unsigned *live) {
    unsigned i;
    *used = *live = 0u;
    for (i = 0; i < OPENSPM_M14_SPSND_SFX_SLOT_COUNT; ++i) {
        const uint32_t slot = OPENSPM_M14_SPSND_SFX_SLOTS + i * OPENSPM_M14_SPSND_SFX_SLOT_SIZE;
        if (mem_read32(cpu, slot) == 0u) continue;
        ++*used;
        if (mem_read32(cpu, slot + 4u) != 0u || mem_read32(cpu, slot + 8u) != 0u) ++*live;
    }
}

static void bridge_m14_audio_report(openspm_bridge_instance *instance, uint64_t now) {
    char message[512];
    unsigned sfx_used = 0u, sfx_live = 0u;
    const openspm_audio_device *dev = &g_m14_audio.dev;
    uint32_t queued_ms;
    if (g_m14_audio.next_report_ns == 0u) {
        g_m14_audio.next_report_ns = now + OPENSPM_M14_AUDIO_REPORT_NS;
        return;
    }
    if (now < g_m14_audio.next_report_ns) return;
    g_m14_audio.next_report_ns = now + OPENSPM_M14_AUDIO_REPORT_NS;
    if (g_m14_audio.push != NULL && !g_m14_audio.push_failed)
        (void)g_m14_audio.push(NULL, 0u, OPENSPM_AUDIO_RATE, &g_m14_audio.out);
    queued_ms = g_m14_audio.out.queued_frames * 1000u / OPENSPM_AUDIO_RATE;
    (void)snprintf(
        message, sizeof(message),
        "M14 audio: frames=%u(+%u) cmdlists=%u(+%u) voices_active=%u voices_mixed=+%u underruns=%u(+%u) queued_ms=%u resyncs_out=%u dropped=%u trim_ppm=%d playing=%u dsp_irqs=%u mail_reads=%u deferred_ee=%u late_resyncs=%u list_errors=%u mem_faults=%u nonsilent=%u out=%s",
        (unsigned)g_m14_audio.ai_events, (unsigned)(g_m14_audio.ai_events - g_m14_audio.prev_ai_events),
        (unsigned)dev->stats.cmdlists, (unsigned)(dev->stats.cmdlists - g_m14_audio.prev_cmdlists),
        (unsigned)dev->hle.stats.voices_last,
        (unsigned)(dev->hle.stats.voices_mixed - g_m14_audio.prev_voices_mixed),
        (unsigned)g_m14_audio.out.underruns, (unsigned)(g_m14_audio.out.underruns - g_m14_audio.prev_underruns),
        (unsigned)queued_ms, (unsigned)g_m14_audio.out.resyncs, (unsigned)g_m14_audio.out.dropped_frames,
        (int)g_m14_audio.out.trim_ppm, (unsigned)g_m14_audio.out.playing, (unsigned)g_m14_audio.dsp_events,
        (unsigned)dev->stats.mail_reads, (unsigned)g_m14_audio.deferred_ee, (unsigned)dev->stats.resyncs,
        (unsigned)dev->stats.cmdlist_errors, (unsigned)dev->hle.stats.memory_faults,
        (unsigned)dev->stats.nonsilent_ticks,
        g_m14_audio.push == NULL ? "none" : (g_m14_audio.push_failed ? "failed" : "sdl"));
    bridge_m14_audio_log(message);
    {
        const ax_hle_stats *h = &dev->hle.stats;
        bridge_m14_audio_sfx_slots(&instance->cpu, &sfx_used, &sfx_live);
        (void)snprintf(message, sizeof(message),
                       "M14 audio voices: pbs=%u started=+%u (adpcm=%u pcm16=%u pcm8=%u other=%u) ended=+%u "
                       "short=+%u bad_format=%u sfx_slots used=%u live=%u; cost_ms/s: ai_handler=%.2f "
                       "dsp_handler=%.2f hle_mmio=%.2f",
                       (unsigned)h->pbs_last, (unsigned)(h->voices_started - g_m14_audio.prev_voices_started),
                       (unsigned)h->started_format[0], (unsigned)h->started_format[1],
                       (unsigned)h->started_format[2], (unsigned)h->started_format[3],
                       (unsigned)(h->voices_ended - g_m14_audio.prev_voices_ended),
                       (unsigned)(h->voices_short - g_m14_audio.prev_voices_short), (unsigned)h->bad_format,
                       sfx_used, sfx_live, (double)g_m14_audio.ai_cost_ns / 1e6, (double)g_m14_audio.dsp_cost_ns / 1e6,
                       (double)g_m14_audio.mmio_cost_ns / 1e6);
        bridge_m14_audio_log(message);
        while (g_m14_audio.notes_logged < h->notes) {
            const ax_hle_voice_note *n = &h->start_notes[g_m14_audio.notes_logged];
            (void)snprintf(message, sizeof(message),
                           "M14 audio voice start #%u: frame=%u pb=0x%08X format=0x%02X type=%u loop=%u "
                           "cur=0x%08X loop_addr=0x%08X end=0x%08X ratio=0x%08X src=%u ctrl=0x%08X ve=0x%04X "
                           "mix L=0x%04X R=0x%04X AL=0x%04X lpf=%u biq=%u",
                           (unsigned)g_m14_audio.notes_logged + 1u, (unsigned)n->frame, (unsigned)n->pb,
                           (unsigned)n->format, (unsigned)n->type, (unsigned)n->loop, (unsigned)n->cur,
                           (unsigned)n->loop_addr, (unsigned)n->end_addr, (unsigned)n->src_ratio,
                           (unsigned)n->src_type, (unsigned)n->mixer_ctrl, (unsigned)n->ve_vol,
                           (unsigned)n->mix_l, (unsigned)n->mix_r, (unsigned)n->mix_al, (unsigned)n->lpf,
                           (unsigned)n->biq);
            bridge_m14_audio_log(message);
            ++g_m14_audio.notes_logged;
        }
        g_m14_audio.prev_voices_started = h->voices_started;
        g_m14_audio.prev_voices_ended = h->voices_ended;
        g_m14_audio.prev_voices_short = h->voices_short;
        g_m14_audio.ai_cost_ns = g_m14_audio.dsp_cost_ns = g_m14_audio.mmio_cost_ns = 0u;
    }
    g_m14_audio.prev_ai_events = g_m14_audio.ai_events;
    g_m14_audio.prev_cmdlists = dev->stats.cmdlists;
    g_m14_audio.prev_voices_mixed = dev->hle.stats.voices_mixed;
    g_m14_audio.prev_underruns = g_m14_audio.out.underruns;
}

static int bridge_m14_audio_deliver(openspm_bridge_instance *instance, int action, int *delivered) {
    uint32_t args[OPENSPM_M14_HOSTCALL_MAX_EVENT_ARGS] = {0};
    uint32_t context;
    char message[256];
    int result;

    *delivered = 0;
    context = mem_read32(&instance->cpu, OPENSPM_M14_OS_CURRENT_CONTEXT);
    if (context == 0u) return OPENSPM_OK; /* keep it pending */
    if (!bridge_m14_audio_check_signatures(&instance->cpu)) return OPENSPM_OK;
    *delivered = 1;
    args[1] = context;
    if (action == OPENSPM_AUDIO_ACTION_DSP_IRQ) {
        /* Clear the request first: the handler's resume callback may build
           and mail the next frame, which posts a new one. */
        openspm_audio_device_dsp_delivered(&g_m14_audio.dev);
        args[0] = OPENSPM_M14_AUDIO_DSP_DSP_INTERRUPT;
        {
            const uint64_t start = bridge_m14_now_ns(instance);
            result = bridge_run_m14_event(instance, OPENSPM_M14_AUDIO_DSP_HANDLER, 2u, args);
            const uint64_t end = bridge_m14_now_ns(instance);
            if (end > start) g_m14_audio.dsp_cost_ns += end - start;
        }
        if (result != OPENSPM_OK) {
            bridge_m14_audio_log("M14 audio: __DSPHandler event failed before returning; stopping (OPENSPM_AUDIO=0 restores the silent path)");
            return result;
        }
        ++g_m14_audio.dsp_events;
        if (bridge_m14_audio_first(OPENSPM_M14_AUDIO_FIRST_DSP_IRQ)) {
            (void)snprintf(message, sizeof(message),
                           "M14 audio: first DSP interrupt delivered (__DSPHandler 0x%08X, mail 0xDCD10001 read=%s)",
                           (unsigned)OPENSPM_M14_AUDIO_DSP_HANDLER,
                           openspm_audio_device_mail_pending(&g_m14_audio.dev) ? "no" : "yes");
            bridge_m14_audio_log(message);
        }
        return OPENSPM_OK;
    }
    {
        const unsigned frames = openspm_audio_device_ai_tick(&g_m14_audio.dev, g_m14_audio.pcm);
        if (frames != 0u) bridge_m14_audio_output(g_m14_audio.pcm, frames);
        if (g_m14_audio.dev.stats.nonsilent_ticks != 0u &&
            bridge_m14_audio_first(OPENSPM_M14_AUDIO_FIRST_SOUND)) {
            (void)snprintf(message, sizeof(message),
                           "M14 audio: first non-silent AI buffer 0x%08X (tick %u) sent to the PC device",
                           (unsigned)g_m14_audio.dev.playing_source, (unsigned)g_m14_audio.dev.stats.ai_ticks);
            bridge_m14_audio_log(message);
        }
    }
    args[0] = OPENSPM_M14_AUDIO_DSP_AI_INTERRUPT;
    {
        const uint64_t start = bridge_m14_now_ns(instance);
        result = bridge_run_m14_event(instance, OPENSPM_M14_AUDIO_AID_HANDLER, 2u, args);
        const uint64_t end = bridge_m14_now_ns(instance);
        if (end > start) g_m14_audio.ai_cost_ns += end - start;
    }
    if (result != OPENSPM_OK) {
        bridge_m14_audio_log("M14 audio: __AIDHandler event failed before returning; stopping (OPENSPM_AUDIO=0 restores the silent path)");
        return result;
    }
    ++g_m14_audio.ai_events;
    if (bridge_m14_audio_first(OPENSPM_M14_AUDIO_FIRST_AI_IRQ)) {
        (void)snprintf(message, sizeof(message),
                       "M14 audio: first AI DMA interrupt delivered (__AIDHandler 0x%08X, context=0x%08X); DSP mails so far=%u",
                       (unsigned)OPENSPM_M14_AUDIO_AID_HANDLER, (unsigned)context,
                       (unsigned)g_m14_audio.dev.stats.mails_to_dsp);
        bridge_m14_audio_log(message);
    }
    return OPENSPM_OK;
}

/* Top-level safepoint: at most one DSP answer and one AI tick. */
static int bridge_m14_audio_service(openspm_bridge_instance *instance) {
    uint64_t now;
    int pass;
    if (!g_m14_audio.attached || g_m14_audio.disabled || instance == NULL) return OPENSPM_OK;
    now = bridge_m14_now_ns(instance);
    if (now == 0u) return OPENSPM_OK;
    bridge_m14_audio_report(instance, now);
    for (pass = 0; pass < 3; ++pass) {
        const int action = openspm_audio_device_poll(&g_m14_audio.dev, now);
        int result, delivered = 0;
        if (action == OPENSPM_AUDIO_ACTION_NONE) break;
        if ((instance->cpu.msr & OPENSPM_M14_MSR_EE) == 0u) {
            /* Interrupts are off in the guest: keep it pending (like VI). */
            ++g_m14_audio.deferred_ee;
            if (bridge_m14_audio_first(OPENSPM_M14_AUDIO_FIRST_DEFERRED))
                bridge_m14_audio_log("M14 audio: first audio interrupt deferred (guest MSR[EE]=0); delivered at a later safepoint");
            break;
        }
        result = bridge_m14_audio_deliver(instance, action, &delivered);
        if (result != OPENSPM_OK) return result;
        if (!delivered || g_m14_audio.disabled) break;
        if (action == OPENSPM_AUDIO_ACTION_AI_TICK) {
            /* Answer the frame this tick mailed right away, then stop. */
            if (openspm_audio_device_poll(&g_m14_audio.dev, now) == OPENSPM_AUDIO_ACTION_DSP_IRQ) continue;
            break;
        }
    }
    return OPENSPM_OK;
}

static void bridge_m14_audio_attach(openspm_bridge_instance *instance) {
    char message[256];
    memset(&g_m14_audio, 0, sizeof(g_m14_audio));
    if (instance == NULL || !bridge_m14_audio_requested()) return; /* off: nothing is wrapped */
    g_m14_audio.instance = instance;
    openspm_audio_device_init(&g_m14_audio.dev, instance->cpu.ram, instance->cpu.ram_size,
                              instance->cpu.mem2, instance->cpu.mem2_size);
    g_m14_audio.underlying_read = instance->cpu.external_read;
    g_m14_audio.underlying_write = instance->cpu.external_write;
    instance->cpu.external_read = bridge_m14_audio_external_read;
    instance->cpu.external_write = bridge_m14_audio_external_write;
    if (g_m14_have_hostcall && g_m14_hostcall.handle != NULL) {
        g_m14_audio.underlying_handle = g_m14_hostcall.handle;
        g_m14_audio.underlying_context = g_m14_hostcall.context;
        g_m14_hostcall.handle = bridge_m14_audio_hostcall;
        g_m14_hostcall.context = NULL;
    }
    g_m14_event_interrupt_handlers[0] = OPENSPM_M14_AUDIO_AID_HANDLER;
    g_m14_event_interrupt_handlers[1] = OPENSPM_M14_AUDIO_DSP_HANDLER;
    g_m14_audio.attached = 1;
    (void)snprintf(message, sizeof(message),
                   "M14 audio: enabled (OPENSPM_AUDIO=1): AI DMA clock + DSP mailbox AX HLE on the game thread; MEM1=%u MEM2=%u bytes; hostcall 0x8028987C %s",
                   (unsigned)instance->cpu.ram_size, (unsigned)instance->cpu.mem2_size,
                   g_m14_audio.underlying_handle != NULL ? "wrapped" : "unavailable");
    bridge_m14_audio_log(message);
}

static void bridge_m14_audio_detach(openspm_bridge_instance *instance) {
    if (!g_m14_audio.attached) return;
    if (instance != NULL) {
        if (instance->cpu.external_read == bridge_m14_audio_external_read)
            instance->cpu.external_read = g_m14_audio.underlying_read;
        if (instance->cpu.external_write == bridge_m14_audio_external_write)
            instance->cpu.external_write = g_m14_audio.underlying_write;
    }
    if (g_m14_hostcall.handle == bridge_m14_audio_hostcall) {
        g_m14_hostcall.handle = g_m14_audio.underlying_handle;
        g_m14_hostcall.context = g_m14_audio.underlying_context;
    }
    g_m14_event_interrupt_handlers[0] = 0u;
    g_m14_event_interrupt_handlers[1] = 0u;
    g_m14_audio.attached = 0;
    g_m14_audio.instance = NULL;
}

#endif
