# M14 LLVM Broadway extended-BAT boot fix — 2026-09-14

## Runtime evidence

After fixing the Wii title classification, Broadway PVR, and HID4/SPR 1011, the first LLVM runtime exception moved forward from the old HID4 fault:

- old fault: `SRR0=0x8026BBD0`, raw `0x7C73FBA6` (`mtspr 1011,...` / HID4)
- new fault: `SRR0=0x80273368`, raw `0x7CF88BA6`, Program/Illegal
- new raw instruction decodes to `mtspr 568,r7`
- SPR 568 is `DBAT4U`

This proves the HID4 fix worked and exposed the next missing Broadway CPU-model register.

## Root cause

Pinned `ExpansionPak/DolRecomp@40637c4683bd2820ac5b23607ee344720beb26df` uses a generic `ppc_spr_access[1024]` table. It admits the classic BAT0-3 registers:

- IBAT0-3: SPR 528-535
- DBAT0-3: SPR 536-543

but omits the 750CL/Broadway extended BAT4-7 banks:

- IBAT4-7: SPR 560-567
- DBAT4-7: SPR 568-575

`ppc_mtspr()` delegates implementation-specific SPR writes to OpenSPM only if the access table first marks the SPR writable. Therefore the omitted Broadway BAT registers raise `PPC_PROGRAM_ILLEGAL` before OpenSPM's Wii SPR service gets the write.

## Fix

`tools/native_bridge/build_local_llvm_module.py` now patches only the private staged CPU runtime copy linked into the opt-in LLVM DLL. It fail-closed patches the pinned access table to admit:

- SPR 560..575 (`IBAT4U/L` through `DBAT7U/L`)
- SPR 1011 (`HID4`, retained from the preceding fix)

The existing OpenSPM Wii SPR callback provides guest-visible storage for these implementation-specific register reads/writes.

The compiler checkout is not mutated, and this does not change the generated guest-object code. The existing 14,414 stamped LLVM objects remain reusable; only the local module runtime needs relinking.

Expected staging marker:

```text
[OpenSPM] Staged DolRecomp runtime admits Broadway BAT4-7 (IBAT SPR 560-567, DBAT SPR 568-575) and HID4 SPR 1011; reads/writes delegate to the existing OpenSPM Wii SPR callback.
```

## Next acceptance

Rerun `M14-LLVM-UPDATE-RUN.cmd`.

The exact `0x80273368 / 0x7CF88BA6 / SPR 568` Program/Illegal fault must disappear. If another Program/Illegal occurs, decode the raw instruction first and compare its SPR/opcode against the 750CL/Broadway programming model before treating it as an LLVM emitter bug.
