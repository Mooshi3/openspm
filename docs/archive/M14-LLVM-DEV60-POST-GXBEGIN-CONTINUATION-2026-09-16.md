# M14 LLVM dev60 — post-GXBegin caller continuation

Date: 2026-09-16

## Latest player result

Player log: `launch(20260916-184121).log`

Dev59 succeeded in clearing the historical restored-v19 stall at `0x80291E20`.
The critical proof is that the real Nintendo `GXBeginDisplayList` CPU-side path
returned far enough for OpenSPM's protected v19 durable caller-return hook to run:

```text
M14 GX display-list begin count=1 addr=0x810D8B80 capacity=65536 lr=0x80080624.
M14 v19 continuation-safe GXBeginDisplayList return hook active; native GXData/FIFO CPU semantics completed and neutral payload recording began at the durable guest continuation.
```

Immediately after that successful hook, execution becomes permanently stationary
at a new PC:

```text
seq=3 stage=6
pc=0x80080630
lr=0x80080630
ctr=0x00000000
exc=0
program=0
wg=905424
region_queries=2
```

Retraces continue at about 60 Hz and native slices continue returning, but the PC,
LR and write-gather count remain unchanged. This is the same no-interpreter
cold-entry signature as the prior measured continuation class, now one caller-side
step after GXBegin has already completed.

## Interpretation

Do not describe this as the old `0x80291E20` GXBegin stall. Dev59 demonstrably
cleared that frontier.

The new `PC == LR == 0x80080630` strongly suggests the instruction at
`0x8008062C` was a linked PPC transfer whose architectural return is
`0x80080630`, but the return is not yet a usable LLVM native entry.

This remains a hypothesis until the player's DOL proves the opcode shape.

## dev60 repair

`tools/native_bridge/augment_llvm_bl_continuations.py` now verifies and promotes
two measured continuations:

- `0x80291E20` (dev59, already proven useful);
- `0x80080630` (dev60, new measured frontier).

For each return PC the local codegen DOL must prove:

1. the return is executable;
2. the immediately previous instruction is a legal linked PPC transfer
   (`b[l]`, `bc[l]`, `bclr[l]`, or `bcctr[l]`).

If either proof fails, generation refuses and reports the raw instruction.

The new map entry, only if verified, is:

```text
80080630 0 openspm_dev60_post_gxbegin_return_80080630
```

The broad call-return census remains audit-only. Do not blanket-partition every
BL+4: region fragmentation remains a throughput risk.

## Graphics significance

This run is encouraging for the v19 restoration itself. The exact protected
GXBegin return hook fired, proving the real Nintendo CPU-shadow path can now
complete under LLVM once the missing continuation at `0x80291E20` is supplied.

Therefore do not revert to dev48's whole-function GXBegin/GXEnd shortcut.
The current bug is later caller-side control flow, not failure of the v19
GXData save/restore contract.

## Performance

Before the new freeze the opening remains around 30-31 `spmarioMain` updates/s
for 60-62 retraces/s. This candidate is still a correctness repair only; it does
not claim the LLVM performance problem is solved.

## Next test

Run:

```text
M14-LLVM-UPDATE-RUN.cmd
```

If generation rejects dev60, preserve the reported raw opcode at `0x8008062C`.
If it launches, pass the orientation screen again. Success means the frontier is
no longer `0x80080630`; continue until gameplay or a new stationary PC, then send
the complete log.
