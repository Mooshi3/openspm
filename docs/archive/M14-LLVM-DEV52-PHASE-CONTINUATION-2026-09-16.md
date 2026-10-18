# M14 LLVM dev52 — frame-phase continuation repair

Date: 2026-09-16
Branch: `m14-native-execution`

## Player evidence

First dev51 player run: `launch(20260916-142845).log`, repo revision `282deeb55056`.

Startup itself was healthy:

- Aurora/D3D12 initialized.
- `m14-dev51-llvm-test-ready-gx-input` activated.
- the production interception router activated.
- the first scheduler-idle presentation/input safepoint occurred.
- the dev51 input-frame latch proof fired.
- VI retraces then continued at approximately 60 Hz.

The run nevertheless never progressed past the initial boot frame. The decisive signature was not low throughput: it was impossible telemetry. After startup, the persistent CPU remained at `spmarioMain` entry `0x801A7AAC`, `seq=0`, `stage=0`, while the phase counter reported roughly 15 million `spmarioMain` entries per host second. Region-query telemetry remained zero in the retained frontier heartbeat and retraces remained ~60/s.

That proves the game was redispatching the diagnostic frame entry itself rather than executing millions of real game updates.

## Root cause

`legacy_c_adapter_v39.c.in` / dev50 replaced the complete historical `CPUState::host_call` chain with the production router. Its exact-frame-phase shortcut called `openspm_m14_note_frame_phase_v20()` / `openspm_m14_note_spmario_main_v20()` and then returned `false`.

The old proven LLVM phase path did more than count. v15/v23 treated each phase entry as a diagnostic seam and armed a one-shot continuation before letting the native region execute; v23/v33 were specifically added after an earlier self-inflicted `spmarioMain` re-entry stall.

By manually duplicating the counter but not the continuation handshake, dev50 recreated that old failure mode at `0x801A7AAC`.

## dev52 repair

Added `tools/native_bridge/legacy_c_adapter_v41.c.in` and selected it for LLVM builds.

The overlay keeps dev50/dev51 intact and changes only four ordinary host-call coordinates:

- `0x801A8A9C` — `DEMOBeforeRender`
- `0x801A7D1C` — `spmarioDisp`
- `0x801A7AAC` — `spmarioMain`
- `0x801A8B64` — `DEMODoneRender`

When one of those four diagnostic entries reaches `CPUState::host_call`, dev52 delegates that single call to `g_m14_dev50_historical_host_call`, the exact pre-dev50 host-call stack saved during production-router creation. That restores the proven v23 one-shot continuation handshake and existing cadence counters.

Everything else stays on the production path:

- native-region queries do **not** re-enter the old probe stack;
- hosted direct-call classification remains dev50's sorted/binary path;
- relF/BSS, DSP/HOME, GX display-list, PE-finish and direct-BL rescue semantics are unchanged;
- dev51 GXData dirty/save/restore remains active;
- dev51 frame-latched WPAD/KPAD counting remains active.

The cost of the compatibility path is bounded to at most four historical ordinary-host-call traversals per produced game frame, rather than millions of historical region-query traversals per second.

## Expected next-run markers

The next player log must show:

- module `m14-dev52-llvm-frame-phase-continuation`;
- `M14 LLVM dev52 phase-continuation guard active...` at startup;
- after the first phase fallback, `M14 LLVM frame-phase continuation repair active...`;
- `spmarioMain` cadence returning to a plausible frame-scale value rather than millions/second;
- sequence/stage and display-copy activity progressing beyond the single first-frame copy.

Input should still show the dev51 input-frame latch proof and, once the title/game reaches controller polling, WPAD/KPAD route proof markers.

## If dev52 still stalls

Do not reopen GX or VI first. Compare the exact PC and phase counter:

- If PC remains `0x801A7AAC` and phase count is still enormous, the historical call did not establish the continuation; instrument the phase callback return plus following region query and inspect `g_m14_v23_permit_armed/pc`.
- If phase count becomes normal but stage still remains 0, dev50's run-wrapper bypass is then the next isolation target. Swap only `bridge_run_dev50` from `g_m14_v37_underlying_run` to the saved dev48 API run (`g_m14_dev50_api_run`) for a controlled A/B; do not revert the production host-call router.
- If gameplay progresses, keep dev52 and resume the throughput/input A/B before any new timing work.
