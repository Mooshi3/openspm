# M14 LLVM dev54 root-call correction — 2026-09-16

## Why the first dev54 local build stopped

The first dev54 compiler plan intentionally failed closed while trying to verify the file-select cold-entry hypothesis. The player's local DOL reported:

```text
0x80032B48 = 0x4BFFC731
```

The first verifier incorrectly expected that instruction itself to be a linked `bcctr`/`bctrl` and stopped generation rather than admitting an unverified target.

That was a verifier mistake, not a new game/runtime failure.

## Correct decode

`0x4BFFC731` at `0x80032B48` is a direct linked branch:

```text
0x80032B48: bl 0x8002F278
return LR:  0x80032B4C
```

The preceding dev53 player run had frozen with:

```text
pc  = 0x80032B94
lr  = 0x80032B4C
ctr = 0x80032B94
```

Because LR still contains the original direct-BL return while PC and CTR both equal `0x80032B94`, the computed transfer occurs downstream of `0x8002F278` and preserves LR. That is consistent with a non-linking computed tail transfer such as `bctr`; it is not consistent with the first dev54 interpretation that `0x80032B48` itself was `bctrl`.

We do not guess the internal `bctr` instruction address from the available state.

## Corrected compiler proof

`tools/native_bridge/augment_llvm_observed_indirect_entries.py` now verifies only facts actually established by the player's DOL + runtime log:

1. `0x80032B48` is a linked direct branch to `0x8002F278`;
2. `0x8002F278` is executable DOL text;
3. the measured cold target `0x80032B94` is executable DOL text;
4. the semantic partition map has no conflicting entry at `0x80032B94`.

Only then does it add:

```text
80032B94 0 openspm_dev54_filesel_msgdrv_indirect_target
```

This remains a one-target measured repair, not a blanket indirect-branch partitioning policy.

## Performance arm remains unchanged

The same candidate retains:

```text
DOLRECOMP_LLVM_CHUNK_INSTRUCTIONS=64
DOLRECOMP_DISPATCH_LOOKUP=indexed
```

The dev53 run remains the performance control: roughly 28–31 real `spmarioMain` updates/s while VI/retrace stayed near 60 Hz. The first useful performance comparison is only after the corrected dev54 build passes file select and reaches representative gameplay.

## Player test

Run:

```text
M14-LLVM-UPDATE-RUN.cmd
```

The local source-contract stage should now pass the corrected indirect-entry self-test. During LLVM generation the verifier should accept raw `0x4BFFC731`, decode root target `0x8002F278`, and add cold target `0x80032B94`.

If runtime gets past the prior file-select freeze, continue into gameplay and send the complete `launch.log`.