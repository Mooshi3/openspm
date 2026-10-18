/* OpenSPM AX HLE: a host-side implementation of what the Wii DSP does with an
 * AX command list (voice decode, sample-rate conversion, envelopes, bus
 * mixing, AUX send/return, final output), so the game's own CPU-side AX /
 * NW4R snd code can run unchanged without a DSP.
 *
 * Clean-room: written from public descriptions of the AX data layouts (voice
 * parameter blocks, DSP-ADPCM frames, PCM formats, command-list opcodes) and
 * checked against what the game's CPU code writes (addresses and evidence in
 * docs/AUDIO-PLAN.md). No ucode, no emulator code. Portable C11, no libc
 * beyond <stdint.h>/<string.h>/<math.h>; no allocation; not thread-safe per
 * state (one state per DSP).
 *
 * Frame: 96 samples per channel at 32 kHz (3 ms). The game's AI DMA buffers
 * are 384 bytes = 96 stereo 16-bit frames (0x80286F50..0x80286F68), its AUX
 * buffers are 96 x int32 per channel, and its depop ramps divide by 96
 * (0x802875FC). Remote speakers: 18 samples per frame (6 kHz).
 *
 * Addresses: command-list, PB and buffer pointers arrive as the CPU wrote
 * them (usually cached virtual 0x80xxxxxx/0x90xxxxxx); they are reduced to
 * physical with & 0x1FFFFFFF. Sample addresses in a PB are physical and
 * format-scaled: ADPCM in nibbles, PCM16 in 16-bit units, PCM8 in bytes
 * (confirmed by NW4R's loop-address math at 0x802F415C..0x802F41A4). The
 * memory callbacks always receive physical byte addresses (MEM1 0x00000000..,
 * MEM2 0x10000000..). All guest data is big-endian.
 */
#ifndef OPENSPM_AX_HLE_H
#define OPENSPM_AX_HLE_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define AX_HLE_SAMPLE_RATE 32000u
#define AX_HLE_FRAME_SAMPLES 96u
#define AX_HLE_RMT_FRAME_SAMPLES 18u
#define AX_HLE_REMOTES 4u
#define AX_HLE_PB_SIZE 0x140u /* 320: mulli 320 in __AXSyncPBs 0x80287A58 */
#define AX_HLE_MAX_VOICES 256u /* the game allocates 96 (0x802882D0) */

/* ---- AX command-list opcodes (16-bit words), as __AXOutBuildCommandList
 * (0x80286354) emits them. Arguments are 16-bit words; "addr" = hi, lo. ---- */
enum {
    AX_CMD_SETUP = 0x00,          /* addr: 120-byte depop init/delta block   */
    AX_CMD_ADD_TO_LR = 0x01,      /* addr: surround scratch (stereo mode)    */
    AX_CMD_SUB_TO_LR = 0x02,      /* addr: surround scratch (surround mode)  */
    AX_CMD_ADD_SUB_TO_LR = 0x03,  /* addr: surround scratch (DPL2 mode)      */
    AX_CMD_PROCESS = 0x04,        /* addr: first PB of the linked list       */
    AX_CMD_MIX_AUXA = 0x05,       /* vol, addr send (0 = none), addr return  */
    AX_CMD_MIX_AUXB = 0x06,
    AX_CMD_MIX_AUXC = 0x07,
    AX_CMD_MIX_AUXA_DPL2 = 0x08,  /* vol, send LRLs, send Rs, ret L,R,Ls,Rs  */
    AX_CMD_MIX_AUXB_DPL2 = 0x09,
    AX_CMD_COMPRESSOR = 0x0A,     /* u16, u16, addr table (parsed, no-op)    */
    AX_CMD_OUTPUT = 0x0B,         /* vol, addr surround scratch, addr LR out */
    AX_CMD_OUTPUT_DPL2 = 0x0C,
    AX_CMD_WM_OUTPUT = 0x0D,      /* 4 x addr: remote speaker buffers        */
    AX_CMD_END = 0x0E
};

/* ---- AXPB (Wii, 0x140 bytes) field offsets. Each confirmed by the game's
 * __AXSyncPBs (0x80287A1C: copies fields from the CPU voice copy at
 * voice+0x28 into the DSP PB array per sync bit), its voice init
 * (0x80288480..), AXSetVoiceAddr (0x80288604), __AXDepopVoice (0x802878A8)
 * and NW4R's inlined mix setter (0x802F614C). See docs/AUDIO-PLAN.md. ---- */
enum {
    AXPB_NEXT_HI = 0x00, AXPB_NEXT_LO = 0x02, /* 0 terminates the list     */
    AXPB_THIS_HI = 0x04, AXPB_THIS_LO = 0x06,
    AXPB_SRC_TYPE = 0x08,     /* 0 = 4-tap polyphase, 1 = linear, 2 = none */
    AXPB_COEF_SELECT = 0x0A,  /* 4-tap table 0/1/2 (NW4R 8k/12k/16k)       */
    AXPB_MIXER_CTRL = 0x0C,   /* u32, AX_MIXCTRL_* bits                    */
    AXPB_STATE = 0x10,        /* 1 = running, 0 = stopped                  */
    AXPB_TYPE = 0x12,         /* 0 = normal, 1 = stream (keep ADPCM hist)  */
    AXPB_MIX = 0x14,          /* 24 u16: (vol, delta) x 12, see ax_hle_bus */
    AXPB_ITD = 0x44,          /* on, buf hi/lo, shiftL/R, targetL/R        */
    AXPB_DPOP = 0x52,         /* 12 s16: L,AL,BL,CL, R,AR,BR,CR, S,AS,BS,CS */
    AXPB_VE_VOL = 0x6A,       /* u16 0..0x7FFF, 0x8000 = 1.0               */
    AXPB_VE_DELTA = 0x6C,     /* s16 per sample                            */
    AXPB_LOOP_FLAG = 0x6E,
    AXPB_FORMAT = 0x70,       /* 0x00 ADPCM, 0x0A PCM16, 0x19 PCM8         */
    AXPB_LOOP_ADDR = 0x72,    /* u32 (hi, lo)                              */
    AXPB_END_ADDR = 0x76,
    AXPB_CUR_ADDR = 0x7A,
    AXPB_ADPCM_COEFS = 0x7E,  /* 16 s16 = 8 (c1, c2) pairs                 */
    AXPB_ADPCM_GAIN = 0x9E,   /* PCM gain: 0x0800 PCM16, 0x0100 PCM8        */
    AXPB_ADPCM_PS = 0xA0,     /* predictor (bits 4-6) / scale (bits 0-3)   */
    AXPB_ADPCM_YN1 = 0xA2,
    AXPB_ADPCM_YN2 = 0xA4,
    AXPB_SRC_RATIO = 0xA6,    /* u32 16.16 (hi, lo)                        */
    AXPB_SRC_FRAC = 0xAA,     /* u16 position fraction                     */
    AXPB_SRC_LAST = 0xAC,     /* 4 s16 history, oldest first               */
    AXPB_LOOP_PS = 0xB4,
    AXPB_LOOP_YN1 = 0xB6,
    AXPB_LOOP_YN2 = 0xB8,
    AXPB_LPF_ON = 0xBA, AXPB_LPF_YN1 = 0xBC, AXPB_LPF_A0 = 0xBE, AXPB_LPF_B0 = 0xC0,
    AXPB_BIQ_ON = 0xC2, AXPB_BIQ_XN1 = 0xC4, AXPB_BIQ_XN2 = 0xC6,
    AXPB_BIQ_YN1 = 0xC8, AXPB_BIQ_YN2 = 0xCA, AXPB_BIQ_B0 = 0xCC,
    AXPB_BIQ_B1 = 0xCE, AXPB_BIQ_B2 = 0xD0, AXPB_BIQ_A1 = 0xD2, AXPB_BIQ_A2 = 0xD4,
    AXPB_RMT_ON = 0xD6,
    AXPB_RMT_MIXER_CTRL = 0xD8, /* u16: bit i = remote mix field i nonzero */
    AXPB_RMT_MIX = 0xDA,      /* 16 u16: per remote main, dmain, aux, daux */
    AXPB_RMT_DPOP = 0xFA,     /* 8 s16: main0..3, aux0..3                  */
    AXPB_RMT_SRC = 0x10A,     /* frac + 4 last samples (kept, see .c)      */
    AXPB_RMT_IIR = 0x114      /* union LPF/biquad for the remote path      */
    /* 0x128..0x13F: not written by the CPU code found (padding).          */
};

/* Mixer-control bits (AXPB_MIXER_CTRL), from NW4R's mix setter 0x802F614C
 * which sets a bus bit when its volume is nonzero and bus|ramp when its
 * delta is nonzero. */
enum {
    AX_MIXCTRL_L = 0x00000001u, AX_MIXCTRL_R = 0x00000002u, AX_MIXCTRL_LR_RAMP = 0x00000004u,
    AX_MIXCTRL_S = 0x00000008u, AX_MIXCTRL_S_RAMP = 0x00000010u,
    AX_MIXCTRL_AL = 0x00010000u, AX_MIXCTRL_AR = 0x00020000u, AX_MIXCTRL_ALR_RAMP = 0x00040000u,
    AX_MIXCTRL_AS = 0x00080000u, AX_MIXCTRL_AS_RAMP = 0x00100000u,
    AX_MIXCTRL_BL = 0x00200000u, AX_MIXCTRL_BR = 0x00400000u, AX_MIXCTRL_BLR_RAMP = 0x00800000u,
    AX_MIXCTRL_BS = 0x01000000u, AX_MIXCTRL_BS_RAMP = 0x02000000u,
    AX_MIXCTRL_CL = 0x04000000u, AX_MIXCTRL_CR = 0x08000000u, AX_MIXCTRL_CLR_RAMP = 0x10000000u,
    AX_MIXCTRL_CS = 0x20000000u, AX_MIXCTRL_CS_RAMP = 0x40000000u
};

enum { AX_FORMAT_ADPCM = 0x00, AX_FORMAT_PCM16 = 0x0A, AX_FORMAT_PCM8 = 0x19 };
enum { AX_SRC_POLYPHASE = 0, AX_SRC_LINEAR = 1, AX_SRC_NONE = 2 };
enum { AX_MODE_STEREO = 0, AX_MODE_SURROUND = 1, AX_MODE_DPL2 = 2 };

/* Mix buses: group * 3 + channel (group main/A/B/C, channel L/R/S). This is
 * also the order of the 12 entries in the SETUP block. In DPL2 mode S is
 * Ls, and the AUX C buses carry Rs: C_L = main Rs, C_R = AUX A Rs,
 * C_S = AUX B Rs (AUX C itself is not available in DPL2). */
enum {
    AX_BUS_MAIN_L, AX_BUS_MAIN_R, AX_BUS_MAIN_S,
    AX_BUS_A_L, AX_BUS_A_R, AX_BUS_A_S,
    AX_BUS_B_L, AX_BUS_B_R, AX_BUS_B_S,
    AX_BUS_C_L, AX_BUS_C_R, AX_BUS_C_S,
    AX_BUS_COUNT
};

/* Guest memory access. Physical byte addresses, raw (big-endian) bytes.
 * Return 0 on success; on failure reads are treated as zeros and writes are
 * dropped (counted in ax_hle_stats.memory_faults). */
typedef struct ax_hle_memory {
    void *user;
    int (*read)(void *user, uint32_t phys, void *dst, uint32_t len);
    int (*write)(void *user, uint32_t phys, const void *src, uint32_t len);
} ax_hle_memory;

/* Optional host hooks (any may be NULL). The game's own AUX callbacks run on
 * the CPU through __AXProcessAux (0x80285E08) on the guest buffers the DSP
 * fills, so the HLE does not call guest code; these hooks let the host
 * observe or add processing. */
typedef struct ax_hle_hooks {
    void *user;
    /* After the AUX bus was written to the guest send buffer (send may be
     * NULL when the game passed no send address) and the return was read
     * from guest memory; ret may be modified before it is mixed. bus 0..2 =
     * A/B/C, channels 3 (L,R,S) or 4 (DPL2: L,R,Ls,Rs). */
    void (*aux)(void *user, unsigned bus, int32_t *const send[4], int32_t *const ret[4],
                unsigned channels);
    /* After OUTPUT wrote the frame: host-order interleaved L,R. */
    void (*output)(void *user, const int16_t *lr, unsigned frames, uint32_t guest_addr);
    /* After WM_OUTPUT: 18 samples per remote (NULL entries: no buffer). */
    void (*remote_output)(void *user, const int16_t *const samples[4], unsigned count);
} ax_hle_hooks;

/* Diagnostics: the first voices seen starting (a PB that was not running in
 * the previous frame), as the DSP saw their PB. */
#define AX_HLE_START_NOTES 24u
typedef struct ax_hle_voice_note {
    uint32_t frame, pb, cur, loop_addr, end_addr, src_ratio, mixer_ctrl;
    uint16_t format, loop, type, src_type, ve_vol, mix_l, mix_r, mix_al, lpf, biq;
} ax_hle_voice_note;

#define AX_HLE_TRACK_SLOTS 512u

typedef struct ax_hle_stats {
    uint32_t frames;           /* command lists processed          */
    uint32_t commands;         /* commands executed                */
    uint32_t voices_mixed;     /* running PBs processed (total)    */
    uint32_t voices_last;      /* running PBs in the last frame    */
    uint32_t voices_ended;     /* PBs stopped by reaching the end  */
    uint32_t memory_faults;
    uint32_t bad_commands;     /* unknown opcode / overlong list   */
    uint32_t pb_list_overflow; /* list longer than AX_HLE_MAX_VOICES */
    uint32_t last_output_addr;
    uint16_t last_command;
    uint32_t pbs_last;         /* PBs in the list last frame (running or not) */
    uint32_t voices_started;   /* running PBs not running the frame before     */
    uint32_t voices_short;     /* ended by the HLE within 2 frames of starting */
    uint32_t started_format[4]; /* ADPCM, PCM16, PCM8, other                  */
    uint32_t bad_format;       /* running PBs with an unknown sample format    */
    uint32_t notes;            /* entries used in start_notes                  */
    ax_hle_voice_note start_notes[AX_HLE_START_NOTES];
} ax_hle_stats;

enum {
    AX_HLE_OK = 0,
    AX_HLE_ERR_BAD_COMMAND = -1,
    AX_HLE_ERR_NO_END = -2,
    AX_HLE_ERR_MEMORY = -3
};

/* Output sample order in the AI buffer: the HLE is also the only writer and
 * the integration's AI consumer the only reader, so this only has to agree
 * with that consumer. Default L then R (see docs/AUDIO-PLAN.md). */
enum { AX_HLE_OUTPUT_LR = 0, AX_HLE_OUTPUT_RL = 1 };

#define AX_HLE_SRC_PHASES 128u

typedef struct ax_hle_state {
    ax_hle_memory mem;
    ax_hle_hooks hooks;
    int32_t bus[AX_BUS_COUNT][AX_HLE_FRAME_SAMPLES];
    int32_t rmt_bus[2u * AX_HLE_REMOTES][AX_HLE_FRAME_SAMPLES]; /* main0..3, aux0..3 */
    int32_t rmt_depop[2u * AX_HLE_REMOTES][AX_HLE_RMT_FRAME_SAMPLES];
    int32_t src_taps[3][AX_HLE_SRC_PHASES][4]; /* Q15 */
    uint32_t track_pb[AX_HLE_TRACK_SLOTS];    /* per PB slot: address,         */
    uint32_t track_seen[AX_HLE_TRACK_SLOTS];  /* frame it last ran (+1),       */
    uint32_t track_start[AX_HLE_TRACK_SLOTS]; /* frame it started              */
    unsigned mode;        /* AX_MODE_*, from the 0x01/0x02/0x03 command   */
    unsigned output_order;
    ax_hle_stats stats;
} ax_hle_state;

/* hooks may be NULL. */
void ax_hle_init(ax_hle_state *state, const ax_hle_memory *mem, const ax_hle_hooks *hooks);

/* Execute one AX frame's command list at guest_addr (as mailed to the DSP).
 * Reads/writes guest memory only through state->mem. */
int ax_hle_process_command_list(ax_hle_state *state, uint32_t guest_addr);

/* ---- Building blocks, exposed for host tests. ---- */

/* One DSP-ADPCM sample: nibble (-8..7), predictor/scale byte, 16 coefs,
 * history updated in place. */
int16_t ax_hle_adpcm_sample(int nibble, uint8_t ps, const int16_t coefs[16], int16_t *yn1,
                            int16_t *yn2);

/* 4-tap interpolation at fraction frac (0..0xFFFF) between h[1] and h[2]
 * (h[0] oldest). src_type/coef_select as in the PB. */
int16_t ax_hle_src_sample(const ax_hle_state *state, unsigned src_type, unsigned coef_select,
                          const int16_t h[4], uint16_t frac);

/* Convenience accessor over two flat big-endian arrays (MEM1 at physical 0,
 * MEM2 at physical 0x10000000); either may be NULL. */
typedef struct ax_hle_flat_memory {
    uint8_t *mem1;
    uint32_t mem1_size;
    uint8_t *mem2;
    uint32_t mem2_size;
} ax_hle_flat_memory;
void ax_hle_memory_flat(ax_hle_memory *out, ax_hle_flat_memory *flat);

#ifdef __cplusplus
}
#endif

#endif
