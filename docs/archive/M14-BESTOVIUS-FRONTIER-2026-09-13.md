# M14 Bestovius renderer / display-list frontier — 2026-09-13

Player branch: `m14-native-execution`.
Protected baseline `m12-native-display` remains untouched.
No GitHub Actions were used.

## Latest authoritative Windows proof

Player log: `launch(20260913-025453).log`, repo revision `d62c7de75d65`.
Backend: `openspm.native 14.0-dev26`; renderer: `openspm.aurora-gx 14.0-dev6`; no emulator fallback.

The d62 run proves the command-aware Broadway GX framer is active. The log contains:

`M14 command-aware GX FIFO framing active; DrawDone/token are recognized only at parsed BP command boundaries.`

The previous half-second blocking-pipeline experiment is absent. Independent renderer stalls remain, including sampled `begin_frame` / `fifo_submit` maxima around 417 ms and DrawDone drain around 142 ms; those are a separate performance frontier.

## Bestovius visual result after command-stream repairs

The player reported a real positive change: **entering Bestovius' house now shows the loading animation that was previously missing.** This is strong runtime evidence that eliminating false GX completion boundaries / lost display-list remainder restored legitimate guest work. Keep those repairs.

Graphics are not fixed yet:

- textures are still wonky;
- the house background is now drawing in front of everything again;
- the earlier scene split remains important: the first room can render correctly, while exterior/grass and Bestovius' room have shown white/Tippi-dependent materials;
- no EFB trails/ghosting have been observed.

This is no longer a good fit for a universal texture-decode/upload failure. The renderer can render ordinary rooms and deep captures have shown real source hashes/bindings. The remaining failure follows material/state/list paths.

## Command-stream repairs: runtime-advanced

### 1. Command-aware bulk FIFO framing

The active 32-byte Broadway bulk service no longer recognizes DrawDone by searching arbitrary payload bytes for `61 45 00 00 02`.

It tracks BP/CP/XF/indexed-XF/CALL_DL/primitive lengths, VCD/VAT state and vertex payload sizes, recursively scans called lists for CP/VAT effects, and emits token/DrawDone only from parsed BP commands.

Fail-closed fixtures cover Astra's exact XF false-positive, a real split DrawDone and the false byte pattern inside a known vertex payload.

### 2. Nested called-list remainder preservation

Aurora now consumes the complete declared called-list range across an internal DrawDone instead of permanently losing the nested remainder.

Current limitation: multiple internal DrawDone notifications are coalesced after preserving all command bytes. No such warning has been observed in the current Bestovius run. If exact nested completion timing later matters, replace the narrow loop with an explicit stream stack rather than undoing the byte-preservation fix.

## Why the next target is hosted SDK display-list semantics

Astra's gameplay audit explicitly left this item open:

> whole-function Begin/End hosting redirects/counts/pads list bytes but omits GX SDK dirty/context effects; prefer recompiled SDK non-device logic with only physical FIFO redirection hosted.

The d62 log shows **thousands** of small display lists, commonly 96/128 bytes with changing hashes. That makes state fidelity around list construction a first-class issue rather than an obscure edge case.

The exact RVL SDK implementation confirms the important ordering:

### GXBeginDisplayList

1. if GX `dirtyState` is nonzero, call the SDK dirty-state flusher **before recording**, so deferred state reaches the current live FIFO;
2. optionally save GXData shadow context;
3. initialize the list FIFO object and mark `inDispList`;
4. GXFlush;
5. save the old CPU FIFO;
6. switch the CPU FIFO to the list buffer;
7. reset the write-gather pipe.

### GXEndDisplayList

1. GXFlush;
2. read back list FIFO state / wrap;
3. restore the old CPU FIFO;
4. optionally restore saved GXData while preserving `cpEnable`;
5. clear `inDispList`;
6. return the list count, or zero if wrapped.

The previous OpenSPM whole-function shim skipped those CPU-side semantics entirely.

## Current candidate: keep SDK logic native, host only the physical recorder

The player branch now implements the audit's recommended boundary:

- `GXBeginDisplayList` (`0x80291DC8`) and `GXEndDisplayList` (`0x80291E78`) are removed from the guest host-target manifest;
- the generated guest functions execute player-local native recompiled code;
- exact stale first-statement host guards from older cached trees are retired when a target leaves the manifest;
- `display_list_native_calls.py` attaches a post-return physical hook to every Begin/End BL and fails closed if any call is unhooked;
- clean same-chunk goto lowering and older cached native-call lowering both converge to the same hooked form;
- after native Begin returns, OpenSPM starts the existing bounded neutral list recorder with the caller's original buffer/capacity;
- native End executes while recording remains active, then OpenSPM closes the neutral recorder and substitutes its aligned/overflow size;
- the old backend Begin/End cases remain available only as the manually invoked **physical recorder service**, not as guest host exits.

This is **local-module bridge v18 (`m14-dev18`)**. It does not roll the backend backward; the backend remains dev26.

Current semantic routing is 40 backend guest host exits + 5 inline semantics = 45 total. The two GX display-list SDK functions are intentionally outside that set.

## Fail-closed coverage added before player test

`M14-UPDATE-RUN.cmd` runs `test_display_list_native_calls.py` before rebuilding the local module. It now verifies:

- stale cached Begin/End host guards are removed without erasing original function bodies;
- every Begin and End BL gets exactly one recorder hook;
- hook normalization is idempotent;
- fresh same-chunk generated lowering is promoted and hooked correctly;
- Begin/End are absent from `m14-host-targets.json`;
- the backend still contains both neutral physical recorder handlers that local bridge v18 calls manually.

`test_event_bridge_contract.py` separately distinguishes backend dev26 from the local-module bridge v18 so the version counters cannot be confused.

## Acceptance / rejection criteria

### Strong positive

If the loading animation stays restored **and** the house background/layering or Tippi-off materials improve, keep the candidate and inspect remaining named bad draws rather than reverting the architectural boundary.

### Architecturally successful but not the material fix

If both native Begin/End runtime markers appear, loading animation stays restored, but visuals are otherwise unchanged, keep the native SDK boundary unless it causes another concrete defect. Astra's hosted-SDK checklist item is then closed narrowly and the next investigation becomes SU/depth/material state.

### Regression

If the build fails, send the exact error. Do not wipe `.openspm`.

If runtime reaches an unsupported MMIO/service path specifically inside native Begin/End, inspect that exact physical FIFO boundary and add the minimum hardware semantic. Do not guess GX shadow offsets and do not immediately return to whole-function hosting.

## Next graphics frontier if native Begin/End do not fix it

### SU coordinate scaling

Astra captured an ordinary material binding a 16x16 texture with SU 48x32 while ordinary sampling omitted logical-image normalization. For that captured case the expected conversion includes 3x horizontally and 2x vertically.

The first SU patch was regressive and stays rolled back. The next attempt must use a **named affected Bestovius draw** plus a synthetic checkerboard and distinguish raw-Wii normalized/texel-space/indirect paths so scale is not applied twice.

### Depth / wrong-layer state

The audited path decodes `zCompLocBeforeTex` but does not consume it in shader/pipeline semantics. That can affect early-depth vs alpha-rejection behavior. Separately, many 2D draws have depth disabled entirely, so their correct layer order is pure FIFO/painter order. Capture both before blaming game sorting.

## Next Windows run

Run only:

`M14-UPDATE-RUN.cmd`

Do not switch branches, wipe `.openspm`, or delete saves.

Expected new runtime markers:

`M14 native GXBeginDisplayList SDK semantics active; pre-list dirty flush/context save stay native and neutral recording starts after Begin returns.`

`M14 native GXEndDisplayList FIFO/context restore active; neutral recorder closes after recompiled End and supplies aligned size.`

Report:

1. whether the loading animation remains;
2. whether the house background is still in front;
3. whether Bestovius' room is correct after dismissing Tippi;
4. whether exterior/grass still need flip + Tippi to recover;
5. whether the first room stays correct;
6. the new launch log, or the exact pre-launch build/configure error.

Audio is a separate missing subsystem and is not expected to change in this test.
