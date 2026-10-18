# M14 LLVM HOME/system-UI frontier — 2026-09-15

## Authoritative run

`launch(20260915-020345).log`, repo `7b4fb6862a02`.

The prior DSP Audio-DMA register fix worked: the candidate no longer exits at `0xCC005030/32/36`. It passes DSP task INIT completion, reaches GameFS/KPAD, keeps fixed-phase retraces near 60 Hz, raises no guest exception, and returns from LLVM slices normally.

The sustained state is instead:

```text
pc=0x801A7ABC
region=[0x801A7AAC,0x801A7CAC)
query_stationary=0
seq=0 stage=0
```

`region_queries` rises continuously, so this is not a native-region deadlock.

## Exact R8PE01 control flow

Player-owned exact DOL disassembly around `spmarioMain`:

```text
801A7AAC  stwu  r1,-16(r1)
801A7AB8  bl    0x80234F00
801A7ABC  bl    0x8023F994      ; homebuttonMain
801A7AC0  cmpwi r3,0
801A7AC4  bf    2,0x801A7B50
801A7AC8  bl    0x8017B72C      ; seqMain
```

For `cmpwi` followed by `bf 2`, CR bit 2 is EQ. Therefore:

- `homebuttonMain() == 0`: fall through and execute `seqMain()` plus the normal game-update stack.
- `homebuttonMain() != 0`: branch to `0x801A7B50` and skip that stack for the frame.

`seq_logoMain` stage 0 increments the sequence stage on its first real execution. Remaining at `seq=0 stage=0` for tens of seconds therefore proves the normal sequence main is being skipped, rather than merely running slowly.

`0x8023F994` is `homebuttonMain` in the US2 map. Its exact verified prologue is:

```text
9421FF60  stwu r1,-160(r1)
7C0802A6  mflr r0
900100A4  stw  r0,164(r1)
396100A0  addi r11,r1,160
```

The generated-C rollback path has historically executed the Wii HOME driver and advanced through logo/title/gameplay, so do not modify that authority merely to bring LLVM to parity.

## v21 native PC system-UI seam

`tools/native_bridge/legacy_c_adapter_v21.c.in` is LLVM-only and wraps v20.

It treats the Wii HOME library as a system-UI platform dependency rather than game logic:

1. native-region queries containing `0x8023F994` are blocked so a direct LLVM call cannot bypass the seam;
2. at the exact materialized entry, the verified four-word R8PE01 prologue must match;
3. if it matches, return `r3=0` and `pc=lr`, meaning no Wii HOME overlay currently owns the game frame;
4. if it does not match, fail closed and delegate to the underlying guest path.

Expected one-shot marker:

```text
M14 LLVM native system-UI seam active: Wii homebuttonMain reports HOME overlay inactive; OpenSPM will own PC pause/system UI.
```

Module version:

```text
m14-dev30-llvm-native-system-ui
```

This is intentionally a PC-port boundary. A future OpenSPM pause/mod/system UI should own this seam; Nintendo's Wii HOME Menu internals are not intended to be a permanent dependency.

The generated-C path remains on `legacy_c_adapter_v13.c.in` and is unchanged.

## Next test

Run:

```bat
M14-LLVM-UPDATE-RUN.cmd
```

No LLVM object regeneration should be required; this is bridge/CMake-only.

Acceptance criteria:

1. source-only contracts pass;
2. cached 14,414 native objects are reused;
3. module reports `m14-dev30-llvm-native-system-ui`;
4. native system-UI seam marker appears;
5. `seq=0 stage=0` does not remain the sustained state;
6. ideally logo stages advance immediately toward the established title/gameplay frontier.

If stage 0 still persists after the seam marker, inspect the exact post-homebutton branch/register state before adding another subsystem shim. Do not infer another hardware dependency from the PC alone.
