# M14 LLVM dev51 test-ready frontier — 2026-09-16

## Purpose

This is the current handoff for the LLVM-native Super Paper Mario player module. The immediate acceptance target is not another boot-only milestone: the next player run should prove that the lean LLVM route preserves Nintendo/OpenSPM semantics, that frame-latched input definitely reaches KPAD/WPAD, and that removing retired region-query diagnostics materially improves simulation throughput.

Branch: `m14-native-execution`

Code frontier before this documentation commit: `5e8ee0845f6d162d30e82780e35f6259862a7fa3`

Intended module: `m14-dev51-llvm-test-ready-gx-input`

Player command: `M14-LLVM-UPDATE-RUN.cmd`

The LLVM runner is transactional: it fetches/fast-forwards to the exact remote branch head, builds/runs the isolated candidate, and restores the known-good C DLL afterward.

## What changed since the last tested LLVM run

### 1. dev50 production interception router

The old LLVM bring-up chain had accumulated observation wrappers which had already answered their questions but still sat on the live path. The previous player run reached tens of millions of native-region queries while `spmarioMain` stayed around 20 updates/s. In particular, a retired NAND probe still linearly scanned the large hosted-target table on every native-region query, and the old frontier sampler used Windows atomics on every query.

`legacy_c_adapter_v39.c.in` / `m14-dev50-llvm-production-router` keeps the proven semantic layers but bypasses the retired diagnostic stack during live execution.

The production region classifier now keeps only required boundaries:

- player-local hosted direct-call seams;
- PC-native GX display-list entry boundaries;
- HOME/system UI seam;
- deterministic relF/BSS allocation seams;
- proven platform host targets.

Hosted seam and platform-target lookups are logarithmic rather than repeated linear scans. The historical wrappers still initialize and unwind normally, so rollback/debug archaeology remains possible without charging gameplay for it.

The dev50 run path deliberately uses the semantic run layer below the retired NAND/scheduler/VI probes. This preserves fixed-rate VI/retrace delivery, the v8 presentation/input safepoint, renderer synchronization, hosted services, DSP completion, HOME handling, PE-finish completion, relF/BSS placement, and existing migration safety nets.

### 2. dev51 finishes the PC display-list CPU contract

`legacy_c_adapter_v40.c.in` wraps dev50 and retains dev48's PC-native display-list byte recorder, but now also preserves the CPU-visible Revolution GX behavior that retail `GXBeginDisplayList`/`GXEndDisplayList` performs around Wii FIFO transport.

The build derives these facts from the player's verified R8PE01 revision-2 DOL via `derive_gx_dl_semantics.py`; it does not import another revision's private addresses:

- `__GXData` pointer storage expression;
- `__GXSetDirtyState` helper;
- `GXData` size `0x600`;
- `cpCtrlReg` offset `0x008`;
- `dlistActive` offset `0x5F8`;
- `dlistSave` offset `0x5F9`;
- `gxDirtyFlags` offset `0x5FC`.

Begin now performs the CPU semantics in retail order:

1. Resolve and validate the player's `__GXData`.
2. If `gxDirtyFlags != 0`, run the player's own derived `__GXSetDirtyState` in a bounded scratch guest callback **before** display-list recording begins.
3. If `dlistSave != 0`, snapshot the complete 0x600-byte GXData shadow.
4. Enter dev48's bounded PC recorder instead of Wii CPU-FIFO/write-gather redirection.
5. Mark `dlistActive=1` only after recorder setup succeeds.

End:

1. Resolve/validate `__GXData`.
2. Preserve current `cpCtrlReg` if save/restore is active.
3. Finish dev48's PC recorder and return its byte count.
4. Restore the saved 0x600-byte GXData.
5. Reapply current `cpCtrlReg`, matching the retail exception to the memcpy restore.
6. Clear `dlistActive` and the host snapshot.

The path fails closed with explicit detail codes 51–56 rather than wandering into the retail Wii FIFO body if the player-derived contract is invalid.

CMake refuses to link LLVM dev51 unless both player-local headers exist:

- `openspm_local_v19_llvm_seams.h`
- `openspm_local_gx_dl_semantics.h`

Expected healthy run: zero `M14 LLVM GX shadow fail-closed` messages and shutdown `gx_failures=0`; if `dlistSave` is exercised, saves/restores should remain balanced.

### 3. Input path is retained and now explicitly proven

Do not add a second input polling architecture. The current path is coherent and frame-latched:

- v8 emits one `OPENSPM_M14_HOST_EVENT_PRESENT_SAFEPOINT` for each delivered VI/retrace epoch while Nintendo is at scheduler idle;
- backend v9 samples physical keyboard state exactly at that safepoint and computes held/pressed edges once for the complete simulation frame;
- every input getter in that frame reads the same snapshot instead of consuming an edge early;
- backend v10 exposes that snapshot through the game's real `KPADRead` and `WPADProbe` host services;
- backend v12 adds release state and Wii-style repeat cadence.

Current temporary keyboard mapping remains:

- `Left` or `A` — sideways Wiimote left
- `Right` or `D` — sideways Wiimote right
- `Down` or `S` — sideways Wiimote down
- `Up` or `W` — sideways Wiimote up
- `Enter` — Wii button 2
- `Space` or `Z` — Wii A
- `X` — Wii button 1
- `C` — Wii B
- `Backspace` — Wii Minus

Dev51 wraps only the semantic host-service entry and does not alter state/cadence. It counts successful:

- frame/input safepoints;
- pressed-button getters (`0x80235AB8`);
- `WPADProbe` (`0x802CE890`);
- `KPADRead` (`0x802DF4F0`).

Expected first-use markers:

```text
M14 LLVM input frame latch proven: ...
M14 LLVM WPAD route proven: ...
M14 LLVM KPAD route proven: ...
```

Shutdown marker:

```text
M14 LLVM test-ready stats gx_dirty_flushes=... gx_saves=... gx_restores=... gx_failures=... input_frames=... pressed_getters=... wpad_probes=... kpad_reads=... saved_gxdata_live=...
```

For a real gameplay run, `input_frames` and `kpad_reads` must be nonzero. This proves the input reached the Wii API boundary. Whether a particular gameplay action is accepted after that can then be diagnosed as game state rather than PC input transport.

## Timing contract retained

The native scheduler/retrace layer remains fixed-phase at nominal 60 Hz. It advances a 16.666666 ms host-monotonic deadline grid, delivers at most one VI epoch per service pass, and skips expired slots rather than burst-delivering catch-up retraces. This prevents long host stalls from speeding game simulation afterward.

The important distinction for the next run:

- ~60 retraces/s alone is not sufficient;
- `spmarioMain` must approach the retrace rate in the same heavy scene;
- if guest work exceeds 16.667 ms, the fixed-phase scheduler intentionally drops elapsed opportunities rather than hiding the throughput deficit with catch-up.

Do **not** claim the LLVM performance problem solved until the player run demonstrates it.

## Direct-BL rescue correction

The prior experiment to partition every direct PPC `bl+4` return was too broad and is **not active** in this test build.

The old run reported 58,049 direct-BL rescue **executions**, but its bounded log exposed only the first dynamic rescue site (`0x80006124 -> 0x80006290`). That is not evidence for 58,049 distinct missing native entries. Every map symbol is a hard native entry/partition, so globally splitting every function return could damage LLVM region locality/materialization and erase the throughput benefit being tested.

`augment_llvm_bl_continuations.py` is therefore audit-only now and adds zero partitions. The exact runtime direct-BL rescue remains as a bring-up safety net. If its cost remains material after dev50 cleanup, measure distinct hot rescue PCs and partition them surgically.

Likewise, the RVL post-MTMSR recovery remains active at the three exact resumable OSInterrupt PCs. The old run counted roughly 50k dynamic completions, but correctness comes first for this player test. If dev51 still misses 60 updates/s, those three exact entries are a reasonable compiler-side follow-up.

## Next player test

Run:

```text
M14-LLVM-UPDATE-RUN.cmd
```

During the run, verify all of these rather than only watching the title screen:

1. Navigate at least one menu with arrows/WASD and `Enter`.
2. In gameplay, hold left/right for several seconds; tap directions quickly as well.
3. Exercise `Space`/`Z`, `X`, `C`, and `Backspace` where the current game state gives them visible behavior.
4. Let a normal cutscene/display-list-heavy scene render long enough to exercise Begin/End repeatedly.
5. Visit the same enemy/NPC-heavy area used for prior choppy-framerate comparisons if reachable.
6. Close normally if possible and send the complete `launch.log`.

## How to read the next log

### Build-time

Must show successful player-local GX shadow derivation and successful LLVM module link. Any derivation failure is useful: extend the fail-closed scanner for the exact US2 compiler form; do not hard-code an unproven cross-revision address.

### Runtime identity

Must contain:

```text
m14-dev51-llvm-test-ready-gx-input
M14 LLVM production interception router active
M14 LLVM test-ready GX/input layer active
M14 native retrace phase-lock active
```

### Input

Healthy:

- frame-latch marker appears;
- KPAD route marker appears in gameplay;
- WPAD route marker appears when the game probes controller presence;
- shutdown counts show `input_frames > 0` and `kpad_reads > 0`.

If controls visibly fail but those counts advance, inspect game-side button interpretation/state next. If KPAD/WPAD counts remain zero, trace the player host-call routing instead.

### GX

Healthy:

- `gx_failures=0`;
- no detail 51–56 fail-closed event;
- if save/restore is used, `gx_saves` and `gx_restores` are balanced by normal shutdown;
- no return to Wii FIFO/WPAR setup from public Begin/End.

### Performance

Primary result is real game-update cadence, not retrace telemetry alone. Compare `spmarioMain` versus retraces and player-visible smoothness in the same heavy scene.

If dev51 is still around the old ~20 updates/s despite dev50 cleanup:

1. inspect dev50 `region_queries`, lookup steps, ordinary calls and redirects;
2. inspect post-MTMSR and direct-BL rescue counts;
3. measure distinct rescue PCs before adding any new partitions;
4. obtain host CPU sampling on the remaining native code/runtime callbacks;
5. only after playable LLVM parity, do PGO and controlled same-scene C-vs-LLVM A/B.

If dev51 approaches 60 updates/s, keep the lean router and proceed to the same-scene heavy-area A/B before declaring victory.

## Permanent constraints

- No emulator fallback.
- Public project ships no Nintendo game code/assets; the player supplies the WBFS/extracted game.
- Preserve protected milestone `milestone/m14-v19-graphics-fixed-2026-09-13`.
- Preserve protected old baseline `f64c4e4023449a60053048393cc4bc1deb3345f8`.
- Do not delete `.openspm` or saves for ordinary updates.
- Do not reapply the rejected no-guest-index renderer shortcut.
- High-refresh presentation should eventually use interpolation; do not speed simulation above its nominal cadence merely to raise displayed FPS.
- Keep ultrawide Hor+.
