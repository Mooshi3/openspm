# M14 LLVM DSP audio-DMA frontier — 2026-09-15

## Result from `launch(20260915-015423).log`

The v20 AX DSP-task INIT completion worked exactly once and removed the prior `0x802871E4` wait. The run emitted:

```text
M14 LLVM DSP task-init semantic completion active: synthesized the first 0xDCD10000 SDK effect (task RUN + verified init_cb flag); Nintendo DSP microcode is not executed.
```

The next boundary was not a native crash or stationary LLVM loop. OpenSPM intentionally failed closed on previously unimplemented DSP-interface MMIO:

```text
pc=0x80285010 read  0xCC005030 width=2
pc=0x80285024 write 0xCC005030 width=2 value=0x8051
pc=0x80285028 read  0xCC005032 width=2
pc=0x80285034 write 0xCC005032 width=2 value=0x1280
pc=0x80285038 read  0xCC005036 width=2
pc=0x80285044 write 0xCC005036 width=2 value=0x000C
Native migration boundary reached reason=3 detail=1 source=0x80285010
```

This appears immediately after `__AXOutInitDSP` and is the Revolution DSP-interface Audio DMA programming block, not another compiler-entry problem.

## Register identity

Cross-check against Dolphin's Wii DSP interface:

- `0xCC005030` = `AUDIO_DMA_START_HI`
- `0xCC005032` = `AUDIO_DMA_START_LO`
- `0xCC005036` = `AUDIO_DMA_CONTROL_LEN`

Wii semantics used by the patch:

- high source half has writable mask `0x1FFF`,
- low source half has writable mask `0xFFE0` (32-byte alignment),
- control/length is a 16-bit latch: `NumBlocks[14:0]`, `Enable[15]`.

Therefore R8PE01's observed `0x8051 : 0x1280` programming becomes physical source `0x00511280`; `0x000C` configures 12 blocks with DMA not yet enabled.

## OpenSPM implementation

`core/wii_boot_platform.c` now owns these three Broadway-visible registers as part of the existing DSP bootstrap/platform service. `include/openspm/wii_boot_platform.h` stores:

```text
dsp_audio_dma_source
dsp_audio_dma_control
```

This patch intentionally models only the real register programming contract. It does **not** yet claim host audio playback, consume blocks, synthesize AID interrupt timing, or execute Nintendo DSP microcode. Those become a separate native audio-service problem once runtime evidence requires them.

`tests/m14_wii_boot_platform_test.c` now replays the exact family and verifies:

1. reset values are zero,
2. high half masks `0x8051 -> 0x0051`,
3. low half aligns `0x129F -> 0x1280`,
4. combined physical source is `0x00511280`,
5. control/length `0x000C` round-trips.

The LLVM runner already gates `m14_wii_boot_platform`, so this behavior is tested before launch.

## Next run

Run:

```bat
M14-LLVM-UPDATE-RUN.cmd
```

Expected:

- no LLVM object regeneration; this is core/platform code only,
- `m14_wii_boot_platform` passes,
- the `0xCC005030/32/36` migration boundary disappears,
- the next visible boot state or first unsupported platform boundary becomes authoritative.

If the game later writes `0x8000 | blocks` to `0x5036` and then depends on Audio-DMA block completion/AID interrupts, do not fake a title-specific callback. Implement that timing as the next native Wii audio-service layer.
