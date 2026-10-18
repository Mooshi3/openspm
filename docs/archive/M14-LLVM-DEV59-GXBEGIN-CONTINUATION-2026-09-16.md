# M14 LLVM dev59 — measured GXBegin continuation repair

Date: 2026-09-16

## New evidence

Player test of dev58:

```text
launch(20260916-181341).log
repo b2158d9c5581
```

The run passes the Wii Remote orientation screen and then becomes permanently stationary at:

```text
seq=3 stage=6
pc=0x80291E20
lr=0x80291E20
ctr=0x00000000
msr=0x0000A032
exc=0
program=0
wg=854673
region_queries=2
```

The same PC/LR and write-gather count repeat while host retraces continue at about 60 Hz. This exactly reproduces the historical frontier which caused dev48 to replace the entire retail GXBeginDisplayList body.

The important new detail is `PC == LR == 0x80291E20`. That is more consistent with a linked call having returned architecturally to PC+4 while that continuation is not a usable LLVM native entry than with a normal guest polling loop. The old dev48 diagnosis identified the containing SDK function correctly but did not distinguish those cases.

The dev58 admission cache is not implicated: the underlying production router has seen only two region queries when the freeze begins.

## dev59 change

`tools/native_bridge/augment_llvm_bl_continuations.py` remains broad-audit-only for ordinary calls, but now promotes one measured continuation:

```text
call/previous PC = 0x80291E1C
measured return  = 0x80291E20
```

Generation fails closed unless the player's private codegen DOL proves:

1. `0x80291E20` is executable; and
2. `0x80291E1C` is an architectural linked PPC transfer (`b[l]`, `bc[l]`, `bclr[l]` or `bcctr[l]`) which writes LR=PC+4.

If verified and not already present, the map gets only:

```text
80291E20 0 openspm_dev59_gxbegin_return_80291e20
```

No Nintendo bytes are stored in source control.

## Why not blanket-partition every call return

Hard map entries fragment LLVM regions. Earlier bring-up deliberately kept the full direct-BL census audit-only because thousands of unconditional PC+4 partitions could erase the locality/performance advantage of the LLVM backend.

Dev59 therefore uses the runtime-measured address as a surgical proof. If the same GXBegin path advances and then exposes another `PC == LR` continuation, use that evidence to generalize the compiler entry policy only as far as required.

## v19 remains authoritative

Do not revert to dev48's whole-function GXBegin/GXEnd shortcut yet.

Dev58 restored the protected v19 ordering because the playable LLVM path had reintroduced global blue/striped material corruption. The intended architecture remains:

- real Nintendo CPU-side dirty/GXData save/restore semantics;
- OpenSPM owns only the physical bounded display-list recorder;
- transition to/from that recorder at durable caller continuations.

Public RVL SDK implementations confirm GXBeginDisplayList is a CPU-shadow setup followed by physical CPU-FIFO/write-gather transport. If a continuation repair is insufficient and a real FIFO helper is then proven to stall, neutralize only that transport helper while retaining the real CPU-shadow code.

## Performance remains separate

Immediately before the freeze, dev58 still reports about:

```text
spmarioMain=30 / host second
retraces=60 / host second
```

So this test does not yet answer the performance problem. First restore a playable v19 path; then return to the dev58 admission-cache and ordinary-hostcall telemetry.

## Next test

Run:

```text
M14-LLVM-UPDATE-RUN.cmd
```

During generation, the existing direct-BL audit line should now report `added=1` unless the map already contained the measured return. If generation refuses the candidate, preserve the reported raw instruction at `0x80291E1C`; that falsifies the continuation hypothesis cleanly.

If it launches:

- pass the Wii Remote orientation screen;
- watch whether `0x80291E20` disappears;
- continue to gameplay if possible;
- check whether blue/striped corruption is gone under restored v19 semantics;
- capture a laggy gameplay window and close normally so dev58 cache/heavy-hitter teardown is emitted.

If another stationary PC appears, send the full launch log. Do not add another address until its PC/LR/control-flow shape is classified.
