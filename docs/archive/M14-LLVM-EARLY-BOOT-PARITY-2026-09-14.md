# M14 LLVM — recovered early standalone boot parity

**Date:** 2026-09-14

This note supersedes the narrow one-register interpretation in `M14-LLVM-BROADWAY-EXTENDED-BAT-FIX-2026-09-14.md`. The HID4 and DBAT4U faults were symptoms of a broader LLVM-vs-generated-C boundary mismatch that early M14 had already solved.

## Why the same boot crashes reappeared

The current LLVM backend and the proven generated-C M14 bridge do not lower privileged SPR instructions through the same path.

Generated-C M14 deliberately side-exited a class of privileged/control instructions into `CPUState::instruction_fallback`. Early M14 then taught that fallback to preserve core CPUState SPR semantics and route less-common Wii/Broadway SPRs through OpenSPM's Wii CPU service.

Current DolRecomp LLVM instead emits direct calls to `ppc_mfspr()` / `ppc_mtspr()`. Pinned DolRecomp `40637c4` places a Gekko-era `ppc_spr_access[]` whitelist in those helpers. A Broadway-only SPR therefore raises Program/Illegal *inside the helper*, before the historical M14 fallback gets a chance to run.

That exactly explains the observed sequence:

- `0x8026BBD0`, raw `0x7C73FBA6`: `mtspr 1011,...` (Broadway HID4)
- after admitting HID4, `0x80273368`, raw `0x7CF88BA6`: `mtspr 568,r7` (Broadway DBAT4U)

The correct fix is not to enumerate Broadway registers one at a time. It is to restore the historical M14 SPR delegation contract at LLVM's runtime-helper boundary.

## Recovered early-M14 boot ladder

### `ab689536a3f889bca4fb63ab480baa2c86e6abe6`
**M14: initialize Broadway execution state in local bridge**

Established Wii application-entry CPU state:

- MSR `0x00002032`
- HID2 `0xE0000000`

These semantics remain inherited by the current bridge.

### `097e58bcd286a3b5e349cc52b8a2ee580415a8aa`
**M14: initialize Wii application stack for standalone boot**

Established the high-MEM1 application stack (`r1=0x816FFFF0`). Current bridge ancestry still carries this.

### `d0cc8c3b7749256ddee343f1fab5b12bce12b1f5`
**M14: handle privileged SPR codegen side exits natively**

This is the key recovered design. Generated-C's generic `mfspr` / `mtspr` side exits were decoded and routed to OpenSPM's Wii SPR service instead of being treated as an interpreter requirement.

The commit explicitly described the backend's shadow SPR bank plus real PVR identity as sufficient for application bootstrap state that static native memory execution did not otherwise consume.

### `3a308817951bd79a1f39d76a30345e15355520b8`
**M14: preserve core SPR semantics in native bridge**

Refined the policy:

- architectural/core SPRs remain in private `CPUState` (XER/LR/CTR/DSISR/DAR/SRR0/SRR1, TBL/TBU, GQRs, HID2)
- less-common SPRs route through OpenSPM's Wii CPU service
- unknown *opcodes* still fail closed

This is the semantic contract the LLVM runtime must preserve.

### `1731f278c943f347b0f336fc4784f6095354a3b5`
**M14: route cache side exits through standalone service**

Routed `dcbst`, `dcbf`, `dcbi`, and `icbi` through the standalone Wii cache-control service.

Pinned LLVM already lowers cache-runtime boundaries through `ppc_cache_control()`, which in turn calls the same installed OpenSPM cache-control callback after privilege validation. No new LLVM parity patch is needed for this part.

### `e38504b534ec0e10904c1aa48565c31647964be6`
**M14: handle Broadway control side exits natively**

Handled `isync`, `mfmsr`, `mtmsr`, `mftb`, `sync`, and `eieio` without interpreter fallback.

Current DolRecomp IR/LLVM represents these CPU/control operations directly or through existing runtime helpers, so they are not the cause of the observed Program/Illegal SPR faults.

## Later standalone boot fixes

After this CPU-side sequence, M14's bring-up moved primarily into bus/MMIO/platform behavior: MEM2 arena bounds, interrupt routing, SI/EXI, audio/DSP/ARAM, late OSInit registers, hosted SDK boundaries, GX, timing, NAND, etc. Those fixes live in the current OpenSPM backend/bridge ancestry and have not been lost by the LLVM module swap.

The important distinction is therefore:

- **OpenSPM platform/host semantics:** still inherited today
- **generated-C privileged SPR side-exit policy:** bypassed by LLVM's direct `ppc_mfspr/ppc_mtspr` calls and needed to be restored at that helper boundary

## Current parity fix

Commit `a365d743caf3949a9099e38c2aecbaeeb989ac24` changes only the private staged DolRecomp CPU runtime linked into the opt-in LLVM DLL.

It leaves intact:

- supervisor/user privilege checks at the start of `ppc_mfspr/ppc_mtspr`
- every core SPR switch case and its exact CPUState behavior
- PVR callback handling
- Program/Illegal behavior when no OpenSPM Wii SPR callback exists

After the core switch, any remaining `spr < 1024` is delegated to the installed OpenSPM Wii SPR callback. This recreates the proven `d0cc8c3` + `3a308817` policy at the point LLVM actually reaches.

This replaces the temporary HID4/BAT4-7 whitelist approach. It does **not** alter the generated LLVM guest objects, so the 14,414 exact-stamped objects remain reusable; only the staged CPU runtime/local DLL relinks.

Expected staging marker:

```text
[OpenSPM] Staged DolRecomp runtime restored historical M14 Broadway SPR delegation: core SPRs stay CPUState-native; other supervisor mfspr/mtspr route to the OpenSPM Wii SPR service.
```

## Next acceptance

Run `M14-LLVM-UPDATE-RUN.cmd`.

The previous HID4 and DBAT4U Program/Illegal faults should both be impossible under the restored historical policy. If boot still stops, classify the next raw instruction against this recovered ladder before adding any new one-off compatibility behavior.
