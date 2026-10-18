# M14 LLVM dev57 — address-taken cold-entry repair

Date: 2026-09-16  
Branch: `m14-native-execution`

## Player evidence

Dev56 was tested at repo revision `c8536a0cf91b` through two independent paths:

- `launch(20260916-170829).log` — opening story / wedding sequence;
- `launch(20260916-171626).log` — file select / load existing save.

Dev56 definitely advanced both paths beyond the previous stationary target
`0x800331AC`, proving that hard compiler entry was effective. Both runs then
converged on a new identical stationary state:

```text
pc  = 0x800332A8
ctr = 0x800332A8
lr  = 0x80038964
msr = 0x0000A032
exc = 0
program = 0
region_queries = 38
last_hostcall = 0
```

The story run reached `seq=2 stage=1` and roughly 2,950 healthy GX-finish
completions before becoming stationary. The save path reached file select,
`SEQ_LOAD`, `seq=5 stage=0`, and more than 1,570 healthy GX-finish completions.
Therefore the failure is not wedding-, NAND-, file-select-, input-, or
renderer-specific.

The common LR is decisive:

```text
linked indirect dispatcher = 0x80038960
return                     = 0x80038964
old dev56 target           = 0x800331AC
new dev57 target           = 0x800332A8
```

The same linked BCCTR is producing multiple legitimate destinations. Continuing
to add one observed target after every player run would only reveal the next one.

## Root cause

Pinned DolRecomp LLVM has two different assumptions for entry PCs.

`FunctionEmitter::emitEntry()` switches directly only to LLVM `region_leaders_`.
A valid aligned PC inside the generated function which is not a region leader
goes to `emitColdEntry()` and then to the fallback-dispatch loop. In the
RecompCore runtime that loop calls `ppc_fallback_instruction()` one guest
instruction at a time until execution reaches a native region leader.

That contract assumes the runtime's instruction-fallback callback can interpret
an arbitrary ordinary PowerPC instruction.

OpenSPM deliberately does not have a general PPC interpreter. Its bridge
`instruction_fallback` is fail-closed: known cache/SPR/order/timebase operations
are handled explicitly, but an ordinary unsupported instruction requests
fallback detail 11 and leaves `cpu->pc` at the same source address.

Those contracts are incompatible for arbitrary cold entries:

1. `bctrl` produces a valid address-taken target which LLVM did not mark as a
   region leader;
2. LLVM cold-entry dispatch invokes one-instruction fallback;
3. OpenSPM refuses to interpret the ordinary instruction and keeps PC unchanged;
4. the same target repeats forever;
5. manually partitioning that exact PC fixes only that one destination.

This precisely matches dev54 -> dev56 -> dev57 progression.

## dev57 repair

New tool:

`tools/native_bridge/augment_llvm_address_taken_entries.py`

The player-local generator now promotes address-taken executable code to LLVM
native entry points before DolRecomp codegen.

For every naturally aligned 32-bit word in the clean DOL's data sections:

- decode the word as a big-endian address;
- require 4-byte alignment;
- require the address to lie inside an executable DOL text section;
- add it to the private LLVM partition map if no semantic entry already owns
  that address.

Existing semantic entries always win. False-positive pointer-like constants can
therefore add a harmless native boundary, but cannot replace HOME/GX/NAND or
other established seams.

Safety bound: generation fails closed if more than 32,768 address-taken
candidates are found.

The newest measured target `0x800332A8` is also added explicitly after verifying
that `0x80038960` is still a legal linked BCCTR and the target is executable.
This guarantees the exact player regression is covered even if that function
pointer is constructed dynamically rather than stored literally in a DOL data
section.

The older measured anchors remain:

- dev54 `0x80032B94`;
- dev56 `0x800331AC`.

No interpreter, emulator fallback, Nintendo code bytes, or Nintendo assets are
introduced or committed.

## Expected generation proof

`M14-LLVM-UPDATE-RUN.cmd` should print a line like:

```text
[OpenSPM] LLVM dev57 address-taken native entries: dispatcher=0x80038960 raw=0x........ measured_target=0x800332A8 measured_in_data=yes|no data_words=... candidates=... added=... already_present=...
```

Interpretation:

- `measured_in_data=yes`: `0x800332A8` was found in a literal DOL pointer table;
- `measured_in_data=no`: the explicit measured anchor covers it, while the
  generic census still covers other literal address-taken functions;
- an implausibly huge census fails closed rather than fragmenting LLVM blindly.

## Performance remains separate

Both dev56 logs still show the established half-rate simulation pattern:

```text
spmarioMain ~= 30-31 / host second
retraces    ~= 60-62 / host second
```

This is not fixed by dev57 and should not be confused with the cold-entry stall.
The established heavy-scene mechanism remains missed-retrace quantization: native
`spmarioDisp + spmarioMain` work exceeds 16.667 ms before the authentic
`VIWaitForRetrace()`, so the loop wakes on the following retrace and runs near
30 Hz.

Do not delete `VIWaitForRetrace()`, accelerate VI, or return to a renderer-only
performance diagnosis. Once a representative path is freeze-free, prioritize
SPM-specific LLVM CPU profiling/PGO/hot-code optimization.

## Graphics regression remains separate

The missing Dark Prognosticus white outline remains a distinct correctness
frontier. Protected v19 proved that exact effect with full continuation-safe
Nintendo GX display-list CPU-shadow behavior. LLVM dev48/dev51 instead skips the
physical Wii Begin/End body and manually reproduces selected GXData semantics.
Dev57 does not change that layer.

## Next player test

Run:

```text
M14-LLVM-UPDATE-RUN.cmd
```

Verify the dev57 address-taken census line appears, then load the same save.

Strong correctness success is no stationary `0x800331AC` or `0x800332A8` and no
immediate new `PC=CTR` target from the same `LR=0x80038964` dispatcher. If that
class is cleared, continue into gameplay and send the complete log so the LLVM
performance pass can finally use a representative scene.
