# M14 LLVM dev61 — bounded GX display-list continuations

Date: 2026-09-16
Branch: `m14-native-execution`

## Why dev61 exists

Dev59 and dev60 proved that the restored v19 `GXBeginDisplayList` / `GXEndDisplayList`
path was not blocked by one unique Wii transport loop. Instead, LLVM repeatedly
became stationary at valid linked-call return PCs which were not materialized as
native region entries in OpenSPM's no-interpreter runtime.

Observed progression:

1. dev58 restored the real v19 SDK CPU-shadow path and froze at
   `PC=LR=0x80291E20`.
2. dev59 materialized that verified continuation. The real GXBegin body completed
   and the protected v19 durable return hook fired.
3. execution then froze at caller-side `PC=LR=0x80080630`.
4. dev60 materialized that verified caller continuation.
5. latest player run `launch(20260916-190622).log` clears both previous PCs, fires
   the v19 GXBegin return hook, then becomes stationary at
   `PC=LR=0x80291E9C`, inside the restored GXEnd-side SDK body.

The latest run therefore falsifies the idea that one or two isolated return PCs
were the whole problem. It identifies a narrow continuation class inside the two
SDK display-list functions.

## dev61 policy

`tools/native_bridge/augment_llvm_bl_continuations.py` now:

- keeps the whole-game direct-BL census audit-only;
- retains the measured post-GXBegin caller return `0x80080630` after player-local
  linked-transfer verification;
- uses the already-verified SDK entries:
  - `GXBeginDisplayList = 0x80291DC8`
  - `GXEndDisplayList = 0x80291E78`
- treats `[GXBeginDisplayList, GXEndDisplayList)` as the bounded GXBegin body;
- derives GXEnd's exclusive end by scanning at most `0x200` bytes for the first
  non-linking BCLR/BLR return;
- within only those two bodies, finds every legal linked PPC transfer and adds its
  executable `PC+4` as a native map partition;
- fails closed if the SDK shape drifts or GXEnd has no bounded return.

This should include both the already-proven `0x80291E20` and the new
`0x80291E9C` without globally fragmenting the game.

## Why not partition every call return in SPM

A semantic-map entry is a hard LLVM region boundary. Blanket `BL+4` partitioning
would create thousands of extra boundaries and could erase the throughput benefit
we are trying to obtain from LLVM. The display-list functions are tiny, verified,
and only active because v19 deliberately re-enters Nintendo SDK CPU-shadow code,
so bounded partitioning there is the appropriate compromise.

## Latest runtime evidence

Immediately before the latest freeze, the opening remains around 30–31
`spmarioMain` updates per host second for 60–62 retraces. The v19 Begin hook fires,
then the persistent CPU parks at `0x80291E9C` with no exception and unchanged
write-gather state while retraces continue.

This remains a correctness frontier. It is not a performance result.

## Next player test

Run:

```text
M14-LLVM-UPDATE-RUN.cmd
```

Generation should print a line containing:

```text
dev61_gx_entries=<N>
gxbegin=[0x80291DC8,0x80291E78)
gxend=[0x80291E78,0x........)
```

and the existing generation summary should report multiple new direct-BL
continuation partitions if they were not already present.

Then pass the Wii Remote orientation screen again.

Success criteria:

- no stationary `0x80291E20`;
- no stationary `0x80080630`;
- no stationary `0x80291E9C`;
- v19 GXBegin and GXEnd durable hooks both execute;
- continue into gameplay far enough to judge whether the old blue/striped
  corruption is actually gone.

If a new stationary `PC==LR` appears *outside* the bounded GX SDK functions, do
not immediately widen this policy globally. Classify the new address first.

## Preserve

- no emulator/general PPC interpreter fallback;
- protected v19 graphics semantics;
- dev58 positive admission memo;
- dev57 address-taken entry census;
- exact guest-array snapshot coherency;
- fixed 60 Hz retrace semantics;
- whole-game BL continuation census remains audit-only.
