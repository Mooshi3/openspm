# M14 LLVM HOME seam / phase-resume interaction — 2026-09-15

## Runtime proof

Tested head `098d4a95315feced0292bd751e3b4733591b241c` successfully fired the LLVM-native system-UI seam at the sole verified `spmarioMain -> homebuttonMain` callsite:

- callsite: `0x801A7ABC`
- continuation: `0x801A7AC0`
- log marker: `M14 LLVM native system-UI seam active: verified spmarioMain->homebuttonMain call reports HOME overlay inactive; OpenSPM will own PC pause/system UI.`

KPAD also initialized successfully before this point. There were no guest exceptions or unsupported-MMIO migration exits.

However, execution then repeatedly materialized at `0x801A7AC0`, still with `seq=0 stage=0`, while native-region queries continued to rise.

## Root cause

This is not another invalid cold-entry address. The private DolRecomp compiler already admits every linked-call `PC+4` continuation, so `0x801A7AC0` is a legal native cold entry.

The interaction is with the older LLVM v23 phase seam. `spmarioMain` has a diagnostic phase seam at `0x801A7AAC`. v23 deliberately blocks any native-region query containing that seam until a one-shot permit for it has been armed/consumed.

The queried range after HOME completion is still:

`[0x801A7AAC, 0x801A7CAC)`

so returning to `0x801A7AC0` without a new permit causes v23 to reject the whole region again. The public dispatcher therefore keeps resurfacing the continuation without allowing it to execute.

## Fix

Commit `523549929f6be9964358c6006d3caf3e312b161f` updates `legacy_c_adapter_v21.c.in` (module version `m14-dev32-llvm-native-system-ui-resume`).

Whenever the HOME seam completes the verified call, it now arms v23's existing one-shot permit for the `spmarioMain` phase seam (`0x801A7AAC`) before returning to `0x801A7AC0`.

This permits exactly one re-entry into the already-known `spmarioMain` native region. The permit is consumed by the next region query, so normal frame-entry phase instrumentation remains intact on subsequent frames.

The same permit is armed by the future callee-entry fail-safe only when its LR is the verified `0x801A7AC0` continuation.

No generated C path, clean DOL, renderer, input service, Wii platform model, or LLVM object code changed. This is bridge-only and should require only relinking the local module.

## Next acceptance test

Run `M14-LLVM-UPDATE-RUN.cmd`.

Expected:

1. HOME seam marker appears once.
2. `0x801A7AC0` does not remain the sustained frontier.
3. `seq=0 stage=0` should finally begin advancing if HOME gating was the last pre-sequence blocker.

If a new frontier appears, use its exact materialized PC and live guest state rather than extending the HOME seam speculatively.
