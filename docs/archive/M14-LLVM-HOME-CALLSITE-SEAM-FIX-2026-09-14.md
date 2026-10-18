# M14 LLVM HOME callsite seam fix — 2026-09-14

## Live result

Runtime `launch(20260915-022137).log` used repo revision `32cee735d091` and proved the first v21 HOME-system-UI interception did **not** fire. The runtime remained healthy at 60 Hz but stayed at `seq=0 stage=0`, repeatedly materializing `pc=0x801A7ABC`, `lr=0x801A7ABC`, with `query_stationary=0`. No HOME seam marker appeared.

This does not invalidate the game-state diagnosis. It proves the original v21 plumbing was wrong: blocking a native region containing `homebuttonMain` does not force a direct LLVM-to-LLVM linked call through the public host-call path.

## Exact player-DOL proof

The verified R8PE01 `main.dol` has exactly one direct linked call to `homebuttonMain` (`0x8023F994`):

- `0x801A7AB8`: preceding linked call, which leaves `LR=0x801A7ABC`
- `0x801A7ABC`: raw `0x48097ED9`, `bl 0x8023F994`
- `0x801A7AC0`: `cmpwi r3,0`
- `0x801A7AC4`: conditional branch which skips the normal game-update stack when HOME owns the frame

This explains the live heartbeat exactly: `PC=LR=0x801A7ABC` means LLVM materialized the second `bl` before executing it; LR still contains the previous call's return address.

## Fix

Commit `9216a74e653c8ba81a1d027e6a441426d44e6977` updates `legacy_c_adapter_v21.c.in` to intercept the already-observed materialized callsite itself.

The seam fails closed unless both the exact call word and the verified `homebuttonMain` prologue match. On success it reproduces the completed PPC call state without entering the Wii HOME library:

- `r3 = 0` — HOME overlay inactive
- `lr = 0x801A7AC0` — exact link value the original `bl` would write
- `pc = 0x801A7AC0` — resume at the original continuation

The original callee-region interception remains only as a fail-safe for a future compiler layout which materializes `homebuttonMain` directly.

This is bridge-only and does not alter the clean player DOL, generated-C rollback path, LLVM codegen input, partition map, or native-object cache.

## Next test

Run `M14-LLVM-UPDATE-RUN.cmd`.

Expected one-shot marker:

`M14 LLVM native system-UI seam active: verified spmarioMain->homebuttonMain call reports HOME overlay inactive; OpenSPM will own PC pause/system UI.`

Primary acceptance criterion: `seq=0 stage=0` must stop being the sustained frontier. If it does not, inspect the post-seam `r3`, `pc`, and next materialized hostcall before changing any additional game/platform behavior.
