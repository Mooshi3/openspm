# M14 LLVM dev54 — file-select indirect cold entry + chunk-64 test

Date: 2026-09-16  
Branch: `m14-native-execution`  
Player command: `M14-LLVM-UPDATE-RUN.cmd`

## Why dev54 exists

The first dev53 player run (`launch(20260916-150533).log`, repo revision `4891813e2247`) proved the HOME/native-region admission repair worked. The LLVM build advanced through the opening sequence, reached file select, rendered it, accepted input, and successfully read the PC-backed save files.

The same run exposed two new facts:

1. selecting a file freezes at one deterministic main-DOL PC;
2. the progressing LLVM build is still visibly slow — typically about 30 real `spmarioMain` updates per host second while VI/retrace remains around 60 Hz.

So dev54 is a combined correctness/performance test rather than another boot-only probe.

## Freeze frontier proved by dev53

Immediately after the file-select input transition the persistent CPU becomes stationary at:

```text
pc  = 0x80032B94
lr  = 0x80032B4C
ctr = 0x80032B94
```

Across repeated frontier heartbeats:

- `pc`, `lr`, and `ctr` do not change;
- `exc=0` and `program=0`;
- write-gather count stops at `3675132`;
- native-region query count stays at `5`;
- no new GX completion or other game progress occurs.

The LR implies the linked indirect call instruction is at `0x80032B48`; the target is exactly the live CTR value. Public US2 symbol information places this address range inside `msgdrv.c`.

This is not the previous dev51/dev52 region-admission loop and it is not a NAND failure. The log shows successful `get-home-dir`, `change-dir`, `open`, `read`, and `close` operations for `banner.bin` and `wiimarioXX` before the transition.

## dev54 compiler fix

New tool:

`tools/native_bridge/augment_llvm_observed_indirect_entries.py`

Policy: **measured and player-verified only**.

It adds one compiler partition entry:

```text
0x80032B94 openspm_dev54_filesel_msgdrv_indirect_target
```

Before doing that it verifies from the player's clean DOL that:

- `0x80032B48` is executable;
- the instruction there is a linked `bcctr` family instruction (`bctrl` in the normal unconditional form);
- `0x80032B94` is executable.

If that shape changes, generation fails closed instead of guessing. No player code bytes are persisted; only the verified address/name is added to the local partition map.

Why only the target: DolRecomp already treats the instruction after a linked indirect terminator as a continuation/region leader. The missing piece exposed by dev53 is the dynamic target itself being re-entered as a cold native address. Blanket-partitioning all indirect targets is intentionally rejected because it would destroy native region locality.

`generate_local_llvm.py` runs the new tool's synthetic self-test and then verifies/applies the real player-DOL entry before DolRecomp emits objects.

## First performance change: LLVM chunk 128 -> 64

The pinned DolRecomp revision already contains the merged LLVM dispatcher/chunk/PGO performance work. OpenSPM was using indexed dispatch but still forcing 128-instruction chunks.

Dev54 changes the local LLVM plan to:

```text
DOLRECOMP_LLVM_CHUNK_INSTRUCTIONS=64
DOLRECOMP_DISPATCH_LOOKUP=indexed
```

The upstream LLVM performance campaign found 64 instructions to be the register-allocation knee on all three measured titles, including a Wii title; 64 improved throughput over 128 by roughly 7–8% in those measurements. That does **not** imply the same magnitude for SPM, so the player run remains the authority.

The much larger upstream gain came from LLVM IR-level PGO. Do not enable PGO-use with a fake or unrelated profile. A valid SPM training capture requires a freeze-free run through representative gameplay. Dev54 therefore gets correctness stable and gives us the first chunk-64 A/B; PGO is the next throughput lever if the run advances.

## What the player should run

Run exactly:

```text
M14-LLVM-UPDATE-RUN.cmd
```

The transactional runner still restores the known-good C module after the LLVM process exits.

During generation, expect:

```text
[OpenSPM] LLVM dev54 observed indirect entry: call=0x80032B48 ... target=0x80032B94
...
M14 OpenSPM LLVM generation ready: ... chunk=64 dispatch=indexed ... indirect_entry=0x80032B94 ...
```

If the player's DOL does not match the measured indirect-call shape, the build should stop before launch with a dev54 verification error. That is intentional.

## Highest-value test sequence

1. Let the game boot normally to file select.
2. Select the same save/file that froze dev53.
3. If it passes, keep playing into actual gameplay rather than stopping at the next screen.
4. Visit the same enemy/NPC-heavy area used for the old C-build performance comparison if reachable.
5. Send the complete new `launch.log` and describe how smooth it felt.

## Success / failure interpretation

### Correctness success

- execution leaves `0x80032B94`;
- seq/stage continues after file selection;
- no new stationary PC develops;
- NAND operations remain successful;
- GX/PE-finish and input semantics remain healthy.

### Performance success

Compare real `spmarioMain` update cadence, not only displayed/retrace FPS. The dev53 run commonly sustained about 30 updates/s against about 60 retraces/s. Chunk-64 should be judged against that same metric and against player-visible pacing.

### If still around 30 updates/s

Do not return to micro-optimizing renderer details first. The pinned backend already exposes IR instrumentation PGO, and upstream Wii data says unprofiled LLVM can remain materially slower than a trained LLVM build. Once dev54 is freeze-free, build a proper SPM profile-generation/use path and train it over representative heavy gameplay.

### If it freezes at a new PC

Treat the new stationary frontier the same way: identify whether it is another proven cold entry, semantic host boundary, or true guest wait before changing architecture. Do not blanket-partition all indirect branches.

## Preserved constraints

- no emulator fallback;
- no Nintendo code/assets in the public repo;
- player-local compiler verification only;
- keep dev50 production router and dev53 HOME caller/callee admission correction;
- keep player-derived GX display-list semantics and frame-latched input;
- preserve `milestone/m14-v19-graphics-fixed-2026-09-13` and baseline `f64c4e4023449a60053048393cc4bc1deb3345f8`;
- do not delete `.openspm` or saves for ordinary updates.
