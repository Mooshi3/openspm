# M14 LLVM dev56 — common computed-entry frontier

Date: 2026-09-16
Branch: `m14-native-execution`
Player-tested parent: `8d73293e32c344722c70194852c51c7a93702e5b`
Player logs:

- `launch(20260916-163047).log` — opening story / Dark Prognosticus path
- `launch(20260916-163604).log` — file-select / save-load path

## Result

Dev55's compiler-level indirect native-transfer budget guard did **not** solve the
freeze class. Two unrelated player paths now converge on the same exact stationary
architectural state:

```text
pc  = 0x800331AC
ctr = 0x800331AC
lr  = 0x80038964
msr = 0x0000A032
exc = 0
program = 0
```

Once this state is reached, write-gather and native-region-query counters remain
stationary. The story run reaches it after more than two thousand healthy GX-finish
completions; the save-load run reaches it after successful file-select assets/NAND,
input and hundreds of GX completions. This is therefore neither file-select-specific
nor a NAND failure.

## What dev55 falsified

Pinned DolRecomp's native-ABI indirect terminator previously lacked the soft-budget
guard used by direct native transfers. Dev55 added the dynamic guard before
`returnNative(target)` and forced the local toolchain manifest to rebuild the
compiler with that patch.

The player ran repo revision `8d73293e32c3`, yet both paths still become stationary
at `0x800331AC`. Keep the guard as harmless scheduling hygiene for now, but retire it
as the explanation for this freeze.

## Why dev56 returns to a measured hard entry

Dev54 added the previously measured `0x80032B94` target as a compiler map entry.
That old target no longer appears as the terminal frontier; execution progressed to
`0x800331AC` instead. This is direct evidence that the map partition changed the
cold-entry behavior.

The new state is stronger than the first one because LR directly identifies a
probable linked computed caller:

```text
caller = LR - 4 = 0x80038960
return = 0x80038964
target = CTR = PC = 0x800331AC
```

We still do not trust that inference blindly. The local build must prove it against
the player's clean DOL.

## Dev56 compiler-map repair

`tools/native_bridge/augment_llvm_observed_indirect_entries.py` now retains dev54's
verified `0x80032B94` entry and adds a second measured entry only if all of these
player-local checks pass:

1. `0x80038960` is executable DOL text;
2. its raw instruction decodes as opcode 19 / XO 528, i.e. BCCTR;
3. BCCTR's required BO bit is valid;
4. LK is set, proving a linked `bcctr` / `bctrl` form and therefore
   `LR = 0x80038964`;
5. `0x800331AC` is executable DOL text;
6. no existing semantic map entry owns `0x800331AC` under a different name.

If any check fails, generation stops and prints the raw caller instruction. We do
not ship or persist player code bytes.

Expected successful generation summary includes both:

```text
indirect_entry=0x80032B94
common_indirect_entry=0x800331AC
```

and the detailed dev56 line prints the locally verified raw instruction at
`0x80038960`.

## Performance classification from both runs

The lag remains real and remains simulation-side:

```text
spmarioMain ~= 28-31 / host second
retraces    ~= 60-62 / host second
```

GX finish completions advance approximately with produced game updates, not at a
mysterious 2x rate. The earlier scheduler/GX 2:1 suspicion is therefore retired.

Historical generated-C phase profiling already established the relevant mechanism:
when `spmarioDisp + spmarioMain` native CPU work exceeds the 16.667 ms VI interval,
the authentic `VIWaitForRetrace()` misses the immediately-next retrace and wakes on
the following one. The real loop therefore quantizes near 33.3 ms / 30 Hz. Do not
remove the Wii wait or accelerate VI; reduce pre-wait native CPU work.

The new LLVM logs continue to argue against Aurora as the primary throughput limit:
steady DrawDone/render work is sub-millisecond to low-single-millisecond while the
simulation remains near 30 Hz.

After correctness is stable, prioritize an SPM-specific LLVM CPU profile/PGO/hot
code pass over renderer micro-optimization. Chunk-64 by itself has not demonstrated
a material SPM improvement.

## Graphics regression discovered by the story run

The player reports that the white drawn outline in the opening Dark Prognosticus
sequence is missing again. This exact effect was explicitly present at protected
milestone:

`milestone/m14-v19-graphics-fixed-2026-09-13`
`a928d97de420c23600c5cdc72013c159670cca7f`

The milestone tied that repair to continuation-safe display-list semantics: the
entire Nintendo `GXBeginDisplayList/GXEndDisplayList` CPU-side SDK behavior ran, and
OpenSPM only redirected the physical recorder at durable return labels.

Current LLVM dev48/dev51 instead skips the Wii GXBegin/GXEnd bodies and reproduces a
selected CPU-shadow subset around the PC recorder: dirty flush, 0x600-byte GXData
save/restore, cpCtrlReg preservation and dlistActive. The missing milestone outline
is therefore concrete evidence that this manual equivalence needs a parity audit.

Do not blame `GX_LO_OR` or generic texture decoding without proof. Audit the skipped
Nintendo Begin/End CPU-side FIFO/shadow transitions against v19 first.

## Next player test

Run:

```text
M14-LLVM-UPDATE-RUN.cmd
```

Build interpretation:

- If dev56 rejects `0x80038960`, send the build output containing its raw opcode;
  do not weaken the verifier.
- If it accepts, confirm generation prints
  `common_indirect_entry=0x800331AC`.

Runtime interpretation:

1. try loading the same save first;
2. if it passes, continue into gameplay far enough to establish whether another
   stationary computed target appears;
3. if convenient, let the opening story run again and report whether the white
   Prognosticus outline remains missing (dev56 is not intended to fix that visual
   regression yet);
4. send the complete launch log.

## Preserved constraints

- no emulator fallback;
- no Nintendo code/assets in the public repo;
- player-owned DOL is used only for local verification/codegen;
- preserve the v19 graphics milestone;
- preserve exact fixed-phase VI behavior;
- preserve real PE-finish completion, PC-native display-list recorder and input;
- do not delete `.openspm` or saves for ordinary updates.
