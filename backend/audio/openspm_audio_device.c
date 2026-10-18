/* OpenSPM audio device (see openspm_audio_device.h, docs/AUDIO-PLAN.md). */
#include "openspm_audio_device.h"

#include <string.h>

#define DMA_HI_MASK 0x1FFFu /* the DSP interface's write masks for the DMA source */
#define DMA_LO_MASK 0xFFE0u
#define DMA_ENABLE 0x8000u
#define DMA_BLOCKS 0x7FFFu

void openspm_audio_device_init(openspm_audio_device *dev, uint8_t *mem1, uint32_t mem1_size,
                               uint8_t *mem2, uint32_t mem2_size) {
    if (dev == NULL) return;
    memset(dev, 0, sizeof(*dev));
    dev->flat.mem1 = mem1;
    dev->flat.mem1_size = mem1_size;
    dev->flat.mem2 = mem2;
    dev->flat.mem2_size = mem2_size;
    ax_hle_memory_flat(&dev->mem, &dev->flat);
    ax_hle_init(&dev->hle, &dev->mem, NULL);
}

static unsigned commit_mail(openspm_audio_device *dev, uint32_t mail) {
    unsigned events = OPENSPM_AUDIO_EV_MAIL;
    dev->stats.mails_to_dsp++;
    dev->stats.last_mail = mail;
    if (dev->expect_list) {
        /* __AXOutNewFrame (0x80286E0C..0x80286E38): 0xBABE0080, then the list
           built last frame. Execute it now: an infinitely fast DSP. Its PB
           write-back lands before the next __AXSyncPBs. */
        int rc;
        dev->expect_list = 0;
        rc = ax_hle_process_command_list(&dev->hle, mail);
        dev->stats.cmdlists++;
        dev->stats.last_cmdlist = mail;
        dev->stats.last_rc = rc;
        events |= OPENSPM_AUDIO_EV_CMDLIST;
        if (rc != AX_HLE_OK) {
            dev->stats.cmdlist_errors++;
            events |= OPENSPM_AUDIO_EV_CMDLIST_ERROR;
        }
        /* Frame done: the ucode mails DSP_RESUME and interrupts the CPU. */
        if (dev->mail_pending) dev->stats.overruns++;
        dev->mail_from = OPENSPM_AUDIO_MAIL_DSP_RESUME;
        dev->mail_pending = 1;
        dev->dsp_irq_pending = 1;
    } else if ((mail >> 16) == OPENSPM_AUDIO_MAIL_AX_LIST_HI) {
        dev->expect_list = 1;
        dev->stats.list_mails++;
    }
    /* Anything else (task boot/yield mails) needs no answer from this HLE. */
    return events;
}

unsigned openspm_audio_device_write(openspm_audio_device *dev, uint32_t address, uint32_t width,
                                    uint64_t value) {
    unsigned events = 0u;
    if (dev == NULL) return 0u;
    switch (address) {
    case OPENSPM_AUDIO_DSP_MAIL_TO_HI:
        if (width == 4u) return commit_mail(dev, (uint32_t)value);
        dev->mail_to_hi = (uint16_t)value;
        return 0u;
    case OPENSPM_AUDIO_DSP_MAIL_TO_LO:
        return commit_mail(dev, (uint32_t)dev->mail_to_hi << 16 | (uint16_t)value);
    case OPENSPM_AUDIO_DMA_START_HI:
        if (width == 4u) {
            dev->dma_source = ((uint32_t)((value >> 16) & DMA_HI_MASK) << 16) |
                              ((uint32_t)value & DMA_LO_MASK);
            return OPENSPM_AUDIO_EV_DMA_PROGRAMMED;
        }
        dev->dma_source = (dev->dma_source & 0x0000FFFFu) | ((uint32_t)(value & DMA_HI_MASK) << 16);
        return 0u;
    case OPENSPM_AUDIO_DMA_START_LO:
        dev->dma_source = (dev->dma_source & 0xFFFF0000u) | ((uint32_t)value & DMA_LO_MASK);
        return 0u;
    case OPENSPM_AUDIO_DMA_CONTROL: {
        dev->dma_control = (uint16_t)value;
        events |= OPENSPM_AUDIO_EV_DMA_PROGRAMMED;
        if ((dev->dma_control & DMA_ENABLE) != 0u && !dev->running) {
            dev->running = 1;
            dev->next_tick_ns = 0u;
            events |= OPENSPM_AUDIO_EV_DMA_STARTED;
        } else if ((dev->dma_control & DMA_ENABLE) == 0u && dev->running) {
            dev->running = 0;
            events |= OPENSPM_AUDIO_EV_DMA_STOPPED;
        }
        return events;
    }
    default:
        return 0u;
    }
}

int openspm_audio_device_read(openspm_audio_device *dev, uint32_t address, uint32_t width,
                              uint64_t *value) {
    if (dev == NULL || value == NULL) return 0;
    switch (address) {
    case OPENSPM_AUDIO_DSP_MAIL_FROM_HI:
        if (!dev->mail_pending) return 0;
        if (width == 4u) {
            /* One 32-bit load reads both halves and consumes the mail. */
            *value = dev->mail_from | 0x80000000u;
            dev->mail_pending = 0;
        } else {
            *value = (dev->mail_from >> 16) | 0x8000u; /* bit 15: mail full */
        }
        dev->stats.mail_reads++;
        return 1;
    case OPENSPM_AUDIO_DSP_MAIL_FROM_LO:
        if (!dev->mail_pending) return 0;
        *value = dev->mail_from & 0xFFFFu;
        dev->mail_pending = 0; /* reading the low half empties the mailbox */
        dev->stats.mail_reads++;
        return 1;
    case OPENSPM_AUDIO_DMA_BLOCKS_LEFT:
        /* Read by __AXOutNewFrame through 0x8028508C right after a tick: the
           buffer that just started is still whole. */
        *value = dev->running ? (dev->playing_bytes >> 5) : 0u;
        return 1;
    default:
        return 0;
    }
}

int openspm_audio_device_mail_pending(const openspm_audio_device *dev) {
    return dev != NULL && dev->mail_pending;
}

int openspm_audio_device_poll(openspm_audio_device *dev, uint64_t now_ns) {
    if (dev == NULL || now_ns == 0u) return OPENSPM_AUDIO_ACTION_NONE;
    /* The DSP answer first: AX must see its frame done before the next tick. */
    if (dev->dsp_irq_pending) return OPENSPM_AUDIO_ACTION_DSP_IRQ;
    if (!dev->running) return OPENSPM_AUDIO_ACTION_NONE;
    if (dev->next_tick_ns == 0u) {
        dev->next_tick_ns = now_ns + OPENSPM_AUDIO_FRAME_NS;
        return OPENSPM_AUDIO_ACTION_NONE;
    }
    if (now_ns < dev->next_tick_ns) return OPENSPM_AUDIO_ACTION_NONE;
    if (now_ns - dev->next_tick_ns > OPENSPM_AUDIO_MAX_LAG_NS) {
        /* The guest could not take ticks (interrupts off, a long stall):
           drop the backlog instead of bursting it. Never changes game time. */
        dev->next_tick_ns = now_ns;
        dev->stats.resyncs++;
    }
    return OPENSPM_AUDIO_ACTION_AI_TICK;
}

unsigned openspm_audio_device_ai_tick(openspm_audio_device *dev, int16_t *out) {
    uint8_t raw[4u * 64u];
    unsigned frames, done = 0u;
    int nonzero = 0;
    if (dev == NULL) return 0u;
    dev->next_tick_ns += OPENSPM_AUDIO_FRAME_NS;
    dev->stats.ai_ticks++;
    dev->playing_source = dev->dma_source;
    dev->playing_bytes = (uint32_t)(dev->dma_control & DMA_BLOCKS) * 32u;
    frames = dev->playing_bytes / 4u;
    if (frames > OPENSPM_AUDIO_MAX_FRAMES) frames = OPENSPM_AUDIO_MAX_FRAMES;
    if (out == NULL || frames == 0u) return 0u;
    while (done < frames) {
        unsigned chunk = frames - done, i;
        if (chunk > 64u) chunk = 64u;
        if (dev->mem.read == NULL ||
            dev->mem.read(dev->mem.user, dev->playing_source + done * 4u, raw, chunk * 4u) != 0)
            return 0u;
        /* Big-endian s16, L then R: the order the HLE's OUTPUT writes. */
        for (i = 0; i < chunk * 2u; ++i) {
            const int16_t s = (int16_t)(uint16_t)((unsigned)raw[i * 2u] << 8 | raw[i * 2u + 1u]);
            out[(done * 2u) + i] = s;
            nonzero |= s != 0;
        }
        done += chunk;
    }
    if (nonzero) dev->stats.nonsilent_ticks++;
    return frames;
}

void openspm_audio_device_dsp_delivered(openspm_audio_device *dev) {
    if (dev == NULL) return;
    dev->dsp_irq_pending = 0;
    dev->stats.dsp_irqs++;
}
