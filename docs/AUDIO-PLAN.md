# OpenSPM audio: plan and AX HLE status

OpenSPM runs silent by default. Wii audio is produced by the DSP running
Nintendo's AX microcode; OpenSPM has no DSP. On the generated-C rollback path
the boundary is stubbed (`tools/native_bridge/guard_hosted_direct_entries.py`:
`__AXOutInitDSP` returns immediately, `AIInitDMA` / `AIStartDMA` are silent).
On the LLVM path the players use, those functions run natively against the
backend's register model (`core/wii_boot_platform.c`: DSP mailbox, audio-DMA
latches) and the DSP task boot is completed by the v20 bridge semantic
(docs/archive/M14-LLVM-DSP-TASK-INIT-FRONTIER-2026-09-14.md). Either way the game,
NW4R snd and AX keep running on the CPU; they just never get a frame
interrupt, so no voice is ever mixed. **Stage 2 below (opt-in,
`OPENSPM_AUDIO=1`) closes that loop.** This document maps that path in the
player's R8PE01 rev-2 `main.dol` and proposes a high-level emulation (HLE) that
keeps all of the game's own sound code (NW4R snd, AX voice management, AUX
effect callbacks) running unchanged.

## What the binary looks like (addresses from the recompiled image)

Found by scanning the generated code for DSP (0xCC0050xx) and AI (0xCD006C00)
register access and by call structure. The SDK names are identifications from
the call pattern, not symbols from the game.

| Address | Identification | Evidence |
| --- | --- | --- |
| 0x8028509C | AIInit | registers __AIDHandler for interrupt 5 (__OS_INTERRUPT_DSP_AI), unmasks it |
| 0x8028521C | __AIDHandler | acks AID in 0xCC00500A, exception context, calls the r13-28952 callback |
| 0x8028508C | AIGetDMABytesLeft | reads 0xCC00503A (blocks left); called at the start of __AXOutNewFrame |
| 0x80284FA4 | AIRegisterDMACallback | called by AX output init right before the first AIInitDMA; stores r13-28952 |
| 0x80284FE8 | AIInitDMA | writes AI DMA regs 0xCC005030/32/36/3A |
| 0x80285064 | AIStartDMA | inside the same block, sets the DMA enable bit |
| 0x80285078 | AIStopDMA | called from AX output quit |
| 0x80285328.. | AI control (sample rate etc.) | 0xCD006C00 access |
| 0x8028986C, 0x8028987C, 0x8028988C, 0x802898A0 | DSPCheckMailToDSP, DSPCheckMailFromDSP, DSPReadMailFromDSP, DSPSendMailToDSP | 0xCC005000/02/04/06 (0x80289510 is not the mailbox; earlier guess) |
| 0x802898B4, 0x8028997C | DSPInit, DSPAddTask | DSP control 0xCC00500A; DSPInit registers __DSPHandler for interrupt 7 |
| 0x80289B38 | __DSPHandler | acks DSP int, exception context, reads the mail: 0xDCD10000 init_cb, 0xDCD10001 res_cb, 0xDCD10002 yield, 0xDCD10003 done, 0xDCD10004 req_cb |
| 0x80289A2C | DSPAssertTask | called by __AXOutAiCallback when the DSP is still busy |
| 0x80511700 | AX DSPTaskInfo | init_cb 0x802870A8 (+40), res_cb 0x802870B4 (+44), done_cb 0x80287108, req_cb 0x80287118 |
| 0x80286DA4 | __AXOutNewFrame (build command list for the next frame) | called by both AI and DSP callbacks |
| 0x80287004 | __AXOutAiCallback (AI DMA done interrupt) | ready (r13-28776) == 1: ready = 0, __AXOutNewFrame; else ready = 2, DSPAssertTask; mode 1 re-arms AIInitDMA here |
| 0x802870B4 | __AXDSPResumeCallback (task res_cb) | ready == 2: ready = 0, __AXOutNewFrame, user callback; else ready = 1 |
| 0x8028711C | __AXOutInitDSP | DSPAddTask, then busy-waits on __AXDSPInitFlag (r13+0x8F90) |
| 0x8028720C | __AXOutInit | 3x DCFlushRange, __AXOutInitDSP, AIRegisterDMACallback, 2x AIInitDMA, AIStartDMA |
| 0x80287404 | __AXOutQuit | AIStopDMA |
| 0x80286354 | __AXOutBuildCommandList | emits the command list (opcodes below) into two 128-byte lists at 0x805108A0 |
| 0x80285E08 | __AXProcessAux | runs the game's AUX A/B/C callbacks on the CPU over triple-buffered DSP send buffers |
| 0x802876D4 | depop SETUP block builder | 120 bytes at 0x805117A0 |
| 0x80287F6C | __AXSyncPBs | invalidates the PB array, depops stopped voices (0x802878A8), syncs each voice (0x80287A1C), flushes |
| 0x802874D4 | remote-speaker sample reader | copies from the 4 x 360-byte remote rings at 0x805109E0 (earlier mislabelled __AXSyncPBs; 0x802874F8 was an old seam) |

On hardware, each AX frame the AI DMA-done interrupt runs `__AXOutAiCallback`.
That builds the next command list from the voice parameter blocks (PBs), mails
its address to the DSP, and re-arms AI DMA on the buffer the DSP mixes into. The
DSP executes the command list (resample, decode ADPCM/PCM, volume ramps, mix
buses, AUX send/return through CPU callbacks) and writes the stereo output.

## HLE design

Keep the CPU side exactly as the game ships it; replace only the two hardware
agents (AI and DSP).

1. **AI clock.** Instead of silencing AIInitDMA/AIStartDMA, host them:
   remember the DMA buffer address and length, and deliver the AI "DMA done"
   interrupt (call the callback registered through 0x80284FA4) from the native
   scheduler at the audio frame rate, the same way retraces are delivered now.
   Pace it from the PC audio device's consumption, not a timer, so audio never
   drifts. It must stay subordinate to the display-locked game clock (see
   PRESENTATION-ARCHITECTURE.md: audio is resampled to the game rate, never the
   other way round).
2. **DSP boot.** Host `__AXOutInitDSP`: set `__AXDSPInitFlag` and invoke the
   task-init callback the game passed to DSPAddTask, exactly what the ucode
   boot would do, so AX believes the DSP is running.
3. **DSP command lists.** Host the mailbox send (0x80289510 block): when AX
   mails a command-list address, execute the AX command list in C on the host
   (an "AX HLE"): walk the PB list, decode ADPCM/PCM8/PCM16 from MEM1/MEM2,
   sample-rate convert, apply volume envelopes and mixer routing, run the AUX
   callbacks the game registered, and write the mixed output where the AI DMA
   buffer points. Then raise the DSP "done" interrupt/mail the ucode would.
4. **PC output.** Push each finished AI buffer (big-endian 16-bit stereo) to an
   SDL3 audio stream opened by the renderer (it already owns SDL). This needs a
   small renderer-API addition (`audio_submit(pcm, frames, rate)`), or the
   backend could use WASAPI directly.

Everything above the DSP (NW4R sequence/stream players, sound archives, BGM
streaming from disc, sound effects) keeps working unmodified. Music streaming
uses ordinary AX voices fed by DVD reads, so it comes for free once voices mix.

## Order of work

1. Instrument (opt-in): log AX init, AI DMA setup, the registered callbacks and
   the first command lists (addresses, lengths), to confirm the table above on
   the player's machine.
2. AI clock + DSP boot only, with a silent "DSP" that just marks each frame
   done: proves AX/NW4R run their frame loop without stalls or timing changes.
3. AX HLE mixer for PCM16/PCM8 voices, then ADPCM, envelopes, AUX.
   **Done as a standalone, host-tested library** (see "Stage 3" below).
2+4. AI clock, DSP answer, AX HLE in the game, SDL output and an Audio tab:
   **implemented opt-in** (see "Stage 2: integration" at the end); waiting for
   the first player run (docs/TEST-LIST.md item 15).

## Constraints

* Clean-room: implement from the public AX data layouts (parameter block
  fields, ADPCM format). Do not copy GPL emulator code (e.g. Dolphin's AX HLE)
  unless the project picks a compatible license.
* Every step behind a switch (e.g. `OPENSPM_AUDIO=1`) until it is proven, and
  the current silent path stays the fallback.
* No Nintendo code or data is shipped; the ucode is never needed (HLE).

## Stage 3: the AX HLE library (implemented, host-tested, not integrated)

`backend/audio/openspm_ax_hle.{h,c}` (portable C11, no allocation, one state
per DSP) and `tests/m14_ax_hle_test.c` (built and run by
`renderer/aurora/test_display_pacing.py`, `test_ax_hle_contract`). Written
from public format descriptions (AX parameter block fields, DSP-ADPCM 8-byte
frames with a predictor/scale header byte and 8 coefficient pairs, the DSP
accelerator's PCM8/PCM16 gain formats, the AX command-list idea) and checked
field by field against what this game's CPU code writes. No emulator code.

```c
ax_hle_state st;                                          /* ~15 KB */
ax_hle_memory mem = {user, read, write};  /* physical byte addrs, raw BE bytes */
ax_hle_hooks hooks = {user, aux, output, remote_output};  /* all optional */
ax_hle_init(&st, &mem, &hooks);
int rc = ax_hle_process_command_list(&st, cmdlist_addr);  /* one 3 ms frame */
```

`ax_hle_memory_flat()` builds the accessor over flat MEM1/MEM2 arrays.
Command-list, PB and buffer pointers are reduced to physical with
`& 0x1FFFFFFF` (the game writes cached 0x80xxxxxx pointers). After a frame the
12 mix buses stay in the state; `st.stats` counts frames, commands, voices
mixed/ended, memory faults and bad commands. The hooks are host-side only:
the game's own AUX callbacks already run on the CPU (__AXProcessAux) over the
guest buffers the HLE fills, so the HLE never calls guest code.

### Frame format (confirmed)

96 samples per channel at 32 kHz = **3 ms** per AX frame (not 5 ms / 160):
the AI DMA buffers are 3 x 384 bytes at 0x80511280 (`mulli 384`,
0x80286F00..0x80286F68 = 96 stereo s16 frames), AUX buffers hold 96 x int32
per channel (1536 = 4 x 384 per AUX A/B buffer, 1152 for AUX C;
0x80285C18..0x80285E04), and the depop ramps divide by 96 (0x802875FC,
`mulhw 0x2AAAAAAB, >> 4`). NW4R advances its envelope copy by `96 * delta`
per frame (0x802F4A68). Remote speakers: 18 samples per frame (0x80287668
divides by 18; 4 rings of 360 bytes = 10 frames at 0x805109E0).

### Command list (confirmed from 0x80286354; 16-bit words, addr = hi, lo)

| Op | Args | Emitted when |
| --- | --- | --- |
| 0x00 SETUP | addr of the 120-byte depop block | always |
| 0x01 / 0x02 / 0x03 | addr of a 768-byte surround scratch (0x80510F80) | mode stereo / surround / DPL2 (AXSetMode 0x80286D54; NW4R maps mono to stereo, 0x802F3368) |
| 0x04 PROCESS | addr of the first PB (PB array 0x80511820, list linked through PB+0) | always |
| 0x05 / 0x06 / 0x07 | return vol, send addr (0 if no callback), return addr | AUX A / B / C, not DPL2 |
| 0x08 / 0x09 | vol, send L-R-Ls, send Rs, return L, R, Ls, Rs | AUX A / B in DPL2 |
| 0x0A | 0x8000, 10, table addr 0x804B7FE0 | compressor on (default, 0x80286D38) |
| 0x0B / 0x0C | master vol, surround scratch addr, AI buffer addr | output stereo-surround / DPL2 |
| 0x0D | 4 remote buffer addrs | always |
| 0x0E END | | always |

Volumes are 1.15 fixed point (0x8000 = 1.0; AXSetMasterVolume clamps to
0x8000, 0x80286D74). AUX buffers: A at 0x8050D720, B at 0x8050E920
(3 x 1536 each), C at 0x8050FB20 (3 x 1152); channels L, R, S (+ Rs) at
+0/+384/+768/+1152. The DSP writes the send buffer (index r13-28920) and reads
the return buffer (index r13-28924); the CPU runs the callbacks on the third
(r13-28928) and zeroes the return when no callback is registered. The frame
order in __AXOutNewFrame (0x80286DA4): __AXSyncPBs, SETUP depop, mail
0xBABE0080 + the list built last frame, __AXProcessAux, user callback, build
the next list, AIInitDMA on the next 384-byte buffer.

SETUP block (0x802876D4): 12 x {s32 init, s16 delta} in bus order main L, R,
S, AUX A L, R, S, B L, R, S, C L, R, S; then remote r main at 72 + 12r and
remote r aux at 78 + 12r. The values are the depop sums of stopped voices,
ramped towards 0 by at most 20 per sample.

### Parameter block (Wii, 0x140 bytes; confirmed)

PB size: `mulli 320` in the per-voice sync (0x80287A58) and the 96 x 80-word
PB array clear (0x8028832C). The CPU voice struct is 360 bytes with its PB
copy at +0x28, the PB index at +0x18 and the sync mask at +0x1C. Each field is
copied by one sync bit in 0x80287A1C (bit in brackets) unless noted.

| Off | Field | Evidence |
| --- | --- | --- |
| 0x00 / 0x04 | next PB, this PB (hi, lo) | voice init 0x802884E0..0x80288520 (last voice: next = 0) |
| 0x08 / 0x0A | SRC type, coef select [0x1] | NW4R 0x802F63F8: none -> 2, linear -> 1, 4-tap 8k/12k/16k -> 0 with 0/1/2 |
| 0x0C | u32 mixer control [0x2] | NW4R mix setter 0x802F614C (bit values in the header) |
| 0x10 | state, 1 = run [0x4]; otherwise read back from the DSP PB | AXSetVoiceState 0x80288594 |
| 0x12 | type, 1 = stream [0x8] | |
| 0x14 | 24 u16 (vol, delta) for L, R, AL, AR, BL, BR, CL, CR, S, AS, BS, CS [0x10] | 0x802F614C |
| 0x44 | ITD on, buffer hi/lo, shift L/R, target L/R [0x20; targets only 0x40] | voice init stores the 64-byte ITD buffer at +0x46 |
| 0x52 | 12 s16 depop L, AL, BL, CL, R, AR, BR, CR, S, AS, BS, CS [0x80] | __AXDepopVoice 0x802878A8 |
| 0x6A / 0x6C | VE volume 0..0x7FFF, per-sample delta [0x200; delta only 0x100] | NW4R 0x802F5F7C |
| 0x6E..0x7C | loop flag, format, loop / end / current addr [0x400; 0x800/0x1000/0x2000/0x4000 singly] | AXSetVoiceAddr 0x80288604; NW4R loop setters 0x802F40E8.. |
| 0x7E..0xA4 | 16 coefs, gain, pred/scale, yn1, yn2 [0x8000] | AXSetVoiceAddr: gain 0x0800 for PCM16 (0x0A), 0x0100 for PCM8 (0x19) |
| 0xA6..0xB2 | ratio 16.16, frac, 4 last samples [0x10000; ratio only 0x20000] | NW4R 0x802F5928 (ratio = float * 65536) |
| 0xB4..0xB8 | loop pred/scale, yn1, yn2 [0x40000] | |
| 0xBA..0xC0 | LPF on, yn1, a0, b0 [0x80000; coefs 0x100000] | AXGetLpfCoefs 0x802886FC: a0 = 32767 - b0 |
| 0xC2..0xD4 | biquad on, xn1, xn2, yn1, yn2, b0, b1, b2, a1, a2 [0x200000; coefs 0x400000] | |
| 0xD6 / 0xD8 | remote on [0x800000], remote mixer control [0x1000000] | NW4R 0x802F64A4: bit i set when remote mix field i is nonzero |
| 0xDA | 16 u16 remote mix [0x2000000] | |
| 0xFA | 8 s16 remote depop: main 0..3, aux 0..3 [0x4000000] | 0x8028799C..; SETUP offsets 72.. |
| 0x10A | remote SRC frac + 4 last samples [0x8000000] | cleared by voice init 0x802884BC |
| 0x114 | remote LPF / biquad union [0x10000000; LPF coefs 0x20000000; biquad coefs 0x40000000] | |
| 0x128..0x13F | not written by any CPU code found | |

Bit 0x80000000 copies the whole PB. Sample addresses are physical and
format-scaled: NW4R computes ADPCM nibble addresses as
`2 * phys + 16 * (n / 14) + 2 + n % 14`, PCM16 as `phys / 2 + n` and PCM8 as
`phys + n` (0x802F415C..0x802F41A4).

### What the library does per frame

Buses are cleared, SETUP loads the depop ramps, PROCESS walks the PB list
(max 256; the game has 96) and for each running PB: decode (ADPCM: header
byte at each 16-nibble boundary, `((n << s) << 11 + c1*yn1 + c2*yn2 + 1024)
>> 11` clamped; PCM16 `s * gain >> 11`; PCM8 `s * gain`), loop (non-stream
ADPCM reloads loop pred/scale and history, streams only pred/scale; no loop
-> state 0), SRC, LPF, biquad, envelope, per-bus mix gated by the
mixer-control bits with per-sample ramps, remote mix. It writes back only
DSP-owned fields: current address, pred/scale, yn1/yn2, SRC frac + history,
VE volume, ramped mix volumes, depop (last contribution per bus), LPF/biquad
state, state = 0 at the end. AUX commands write the bus as int32 BE to the
send buffer, read the return and add it into main at the return volume.
OUTPUT folds S, applies the master volume, saturates and writes 96 BE s16
frames; WM_OUTPUT writes 18 BE s16 per remote.

HLE choices (not observable from the CPU side; own design, marked in the
source):

* 4-tap SRC: own 128-phase Lanczos-2 windowed-sinc tables with cutoff 0.5 /
  0.75 / 1.0 of the input Nyquist for coef select 0/1/2 (the ucode's tables
  are not reproduced); unity DC gain, select 2 is exactly interpolating. A
  fixed 3-sample latency for every SRC type; ratio capped at 16.
* Surround: the fold-down happens on the current frame inside OUTPUT (stereo:
  S at -3 dB into L and R; surround: L - 0.707 S, R + 0.707 S; DPL2:
  L - 0.8165 Ls - 0.5774 Rs, R + 0.5774 Ls + 0.8165 Rs, no phase shift). The
  scratch buffer (referenced by nothing else in the game) is not written.
* DPL2 Rs routing through the AUX C slots (main Rs = C L, AUX A Rs = C R,
  AUX B Rs = C S).
* Compressor (0x0A): parsed, no-op (the output saturates instead).
* Remote speaker: box-filter decimation 96 -> 18, remote aux summed with
  main, remote SRC/IIR state left as written. ITD: not applied.
* AI buffer sample order: L then R by default (`output_order`); it only has
  to match the stage-2 AI consumer, nothing else reads it.

Still to confirm (listening / more disassembly): the biquad sign convention
(a1/a2 added, Q14) and the filter-before-envelope order; the DPL2 Rs mapping
(NW4R's mix computation for output mode 2); the remote mix field order (main,
dmain, aux, daux per remote is assumed; only bit-per-field is confirmed); the
unit of remote ramps (per 6 kHz sample assumed).

Tests (all green): hand-computed ADPCM samples incl. saturation; a 440 Hz sine
encoded by a brute-force encoder in the test (> 30 dB SNR) decoded through the
full pipeline over two frames; loop (normal reloads history, stream keeps it)
and end-of-sample stop; PCM16 from MEM2 with loop and gain, PCM8; SRC ratio
1.0 identity for all three types, 0.5 on a ramp, 0.7 fraction carry, 2.0
decimation, low-pass DC/Nyquist; envelope and mix ramps incl. clamping and the
ramp bit; a two-voice mix saturating both ways and the R-L order option; PB
write-back of every DSP-owned field; a full list (SETUP depop, ADD_TO_LR,
linked PBs with a stopped one, AUX A send/return, AUX B without send,
compressor, OUTPUT with stereo fold, WM_OUTPUT, END) with hooks; DPL2 output;
unknown opcode and unmapped PB handling.

## What stage 2 (integration) must provide

(Implemented as described in "Stage 2: integration" below; the resume
mail turned out to be 0xDCD10001 handled by __DSPHandler 0x80289B38, and
the mailbox functions live at 0x8028986C..0x802898A0.)

* **DSP boot**: host `__AXOutInitDSP` (0x8028711C): set `__AXDSPInitFlag`
  (r13+0x8F90) and call the task-init callback given to DSPAddTask.
* **Mailbox hook** (0x80289510 block): __AXOutNewFrame mails 0xBABE0080, then
  the command-list address (0x80286E0C..0x80286E38). On the second mail call
  `ax_hle_process_command_list(&st, addr)` with big-endian guest accessors
  (physical addresses; MEM1 and MEM2), then raise what the ucode would (the
  DSP interrupt / "done" mail the resume callback 0x802870B4 expects).
* **AI clock**: host AIRegisterDMACallback / AIInitDMA / AIStartDMA; every
  3 ms of consumed audio call the registered callback (__AXOutAiCallback
  re-arms DMA on the next 384-byte buffer and calls __AXOutNewFrame). Push
  the buffer the HLE just wrote (AIInitDMA address, `st.stats.last_output_addr`
  or the `output` hook's host-order samples) to the SDL stream, paced from the
  device and subordinate to the game clock.
* **Ordering**: run the HLE inside the guest's AI/DSP interrupt path (same
  thread as the CPU code), so its PB write-back lands before the next
  __AXSyncPBs (0x80287F6C) reads state, VE volume and current address.
* Remote speaker rings (WM_OUTPUT) can be ignored at first.
* Everything behind `OPENSPM_AUDIO=1`, the silent path as the fallback.

## Stage 2: integration (implemented, opt-in, first player run pending)

Switch: `OPENSPM_AUDIO=1` (launcher flag `audio` in M14-VANILLA-RUN.cmd,
OpenSPM.ini `[Audio] enabled = 1`, F1 menu -> Audio). `[Audio] volume = 0..100`
(`OPENSPM_AUDIO_VOLUME`, live from the Audio tab). Unset = the previous silent
path byte for byte: nothing below is installed. LLVM path only; the generated-C
rollback keeps its silent stubs. No codegen change and no LLVM object
regeneration: bridge + backend/audio sources + renderer only (the native module
relinks).

Rather than replacing SDK functions, the bridge models the two hardware agents
at the register level, so every SDK function (AIInitDMA/AIStartDMA/
AIRegisterDMACallback, __AXOutInitDSP, DSPSendMailToDSP, __AIDHandler,
__DSPHandler, __AXOutNewFrame, NW4R) runs unchanged:

| Piece | Where | What |
| --- | --- | --- |
| Device model | `backend/audio/openspm_audio_device.{h,c}` | DSP-interface DMA latches (0x5030/32/36, hardware masks), CPU->DSP mail (0x5000/02), the DSP->CPU answer (0x5004/06), DMA blocks left (0x503A), the 3 ms AI clock; runs `ax_hle_process_command_list` on the mail after 0xBABExxxx |
| Bridge glue | `tools/native_bridge/m14_audio_bridge.h` (from `legacy_c_adapter_v42.c.in`) | wraps `external_read/write` (every store still reaches the backend model first), the host-call service for 0x8028987C, and the dev70 top-level run |
| Output | `openspm_renderer_audio_push` in `renderer/aurora/openspm_renderer_aurora_v6.cpp`, ABI + pacer in `include/openspm/audio_out.h` | SDL3 `SDL_OpenAudioDeviceStream`, 32 kHz stereo s16 |

Protocol as it runs (the game side is the verified SDK code above):

1. DSP boot: unchanged (v20 semantic completes the 0xDCD10000 init; the boot
   mails reach the device and are ignored).
2. `__AXOutInit`: list 0 built (output -> AI buffer 1), `AIInitDMA(buffer 0,
   384)`, `AIStartDMA` -> the device arms its clock.
3. Every 3 ms of guest time (host->monotonic_ns, frozen by the backend while
   the menu pauses), at a top-level safepoint with MSR[EE] set: the device
   latches the programmed DMA buffer, its 96 frames go to the SDL stream, and
   the bridge runs `__AIDHandler(5, OS_CURRENT_CONTEXT)` on a scratch event
   CPU exactly like the VI retrace. `__AXOutAiCallback` -> `__AXOutNewFrame`
   reads 0x503A, mails 0xBABE0080 and the list built last frame; the device
   executes that list at once (an infinitely fast DSP, on the game thread,
   before the next `__AXSyncPBs`), posts mail 0xDCD10001 and requests the DSP
   interrupt; NewFrame builds the next list into the other buffer and
   re-arms DMA on it.
4. Right after (same safepoint): `__DSPHandler(7, context)`.
   `DSPCheckMailFromDSP` (0x8028987C, a manifest host target whose backend
   answer is a no-op) is answered 1 by the bridge while the device's mail is
   pending, otherwise the backend answers as before (the boot path is
   unchanged). The handler reads 0xDCD10001 and calls res_cb, which marks the
   DSP ready (or starts the next frame if the AI tick came first).
5. So buffer k mixed at tick k is latched and played at tick k+1; the first
   tick plays the zeroed buffer.

Clocking: the guest clock drives the ticks, never the device. At most one AI
tick per safepoint; a backlog over 48 ms (interrupts off, a stall) is dropped,
never burst. The renderer's pacer absorbs the rest: 40 ms prebuffer (device
paused until then), pause + prebuffer again on underrun (silence), drop on
overflow (> 200 ms), and a playback-rate trim of at most 0.2 % from the
smoothed queue level, so a device clock that differs from the game clock
never changes game timing. The F1 menu pause clears the stream at once.

Failure behaviour: the first interrupt checks both handler prologues
(`stwu r1,-736(r1)`) and logs the registered AI callback and the AX task's
res_cb; a mismatch disables audio (silent, game continues). A guest handler
event that does not return is fatal like a failed retrace (log names it;
`OPENSPM_AUDIO=0` restores the silent path). A broken command list is logged
and still answered, so AX never hangs.

Diagnostics: first-time lines `M14 audio: ...` for each step (enabled,
first CPU->DSP mail, first AIInitDMA, AIStartDMA, SDK shape verified, renderer
output found, first command list mixed, first AI interrupt, DSPCheckMailFromDSP
answered, first DSP interrupt, first blocks-left read, first non-silent
buffer, first deferral, list failure) and once per second
`M14 audio: frames= cmdlists= voices_active= voices_mixed= underruns= queued_ms=
resyncs_out= dropped= trim_ppm= playing= dsp_irqs= mail_reads= deferred_ee=
late_resyncs= list_errors= mem_faults= nonsilent= out=`. Healthy: +333 frames
and cmdlists per second, underruns 0, queued_ms 35..60, list_errors and
mem_faults 0.

Tests: `tests/m14_audio_device_test.c` (built by
`renderer/aurora/test_display_pacing.py`, `test_audio_device_contract`) plays
the game's side of the protocol against fake MEM1/MEM2 at the game's own
addresses (AI buffers 0x80511280, lists 0x805108A0/0x80510920, PB 0x80511820)
with a looping PCM16 voice, and checks the PC stream is the voice, continuous
across buffers, L then R, one tick late; plus DMA stop, backlog resync,
ignored boot mails, register views, overruns, a broken list, and the pacer.
`tools/native_bridge/test_llvm_native_pipeline.py` (`test_audio_bridge_contract`)
pins "off by default: nothing wrapped, generated-C stubs unchanged".

Open points for the first run: the HLE's unconfirmed choices listed under
Stage 3 (biquad sign, DPL2 Rs, remote mix order) and whether any other AX/NW4R
path touches an unmodelled register (it would show as a migration boundary in
the log with the address).
