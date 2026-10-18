# M14 performance 055407 — WiiCompiled/OpenPete CPU frontier

Date: 2026-09-14
Player log: `launch(20260914-055407).log`
Baseline tested repo: `a1ab0539adb57c5053688d0ad348090cf8677a0b`
Resize-causality documentation head: `5776874a32a26dfcaf2e8adc8f46461eaab5d6b4`
First CPU A/B candidate: `9b148a04f67ebb0b188e05dbddb089c667d69860`

## What the latest log proves

The scene-dependent lag remains real after the WPAR direct-store experiment and is
not primarily swapchain Present, frame acquisition, staging-map waits, bbox readback,
or a single giant DrawDone fence.

At 4096 successful presents in the latest run:

- host presentation interval avg ~19.39 ms;
- p50 ~17.5 ms;
- p95 ~31.5 ms;
- p99 ~34.0 ms;
- the actual host `Present()` call averages only ~0.208 ms;
- the scheduler-idle presentation safepoint is ~7 us average;
- DrawDone averages ~2.82 ms, p95 ~3.5 ms, max ~4.08 ms.

The DrawDone substage profiler reports roughly 2.58 ms/frame of draw CPU in that
window. Indexed guest-array staging is the largest bucket (~0.95 ms/frame), with
guest-index scanning next (~0.66 ms/frame). Pipeline/bind/uniform work is much
smaller.

Later gameplay windows can push DrawDone into roughly 3-5 ms/frame, but that still
cannot account for visible 30-35 ms presentation intervals. Therefore a substantial
scene-dependent producer cost remains outside DrawDone.

## Retired/cleared leading hypotheses

- Windows debug heap: disabled for the child and not the main cause.
- Swapchain `Present()`: cheap; frames arrive late before Present.
- render-worker queue/end-frame work: light-region measurement was tiny; the probe
  itself was behavior-changing and is retired.
- frame/staging slot acquisition: effectively free in normal gameplay.
- bbox readback: low and `slow=0` in representative gameplay.
- frequent parser-progress drains: actively harmful; permanently rejected.
- WPAR generic resolver/indirect callback: the private staged direct WPAR path is
  active, but the player still reports the same choppiness. Keep as hygiene only
  unless later measurements prove otherwise.

## WiiCompiled architectural comparison

Reference repository: `patchzyy/Wiicompiled`.

WiiCompiled is not merely an OpenSPM-style raw FIFO bridge with a few local
optimizations. It has a substantial `runtime/src/hle/gx/` layer and declares its GX
runtime directory as a native ABI/replacement surface for generated game code.
Representative native GX implementations include copy, FIFO, display-list, vertex,
state, texture, TEV and guest-write tracking paths.

Important implications:

1. Wii SDK functions can be replaced at known guest function boundaries rather
   than forcing every operation through the original translated SDK implementation.
2. Its GX copies/DrawDone can call Aurora-level semantics directly while preserving
   ordering.
3. It still has immediate FIFO/vertex traffic where appropriate, so GX HLE is not a
   magic elimination of every FIFO byte.
4. Its translator/runtime are designed together around known guest-memory fast
   paths and cold slow paths instead of treating every memory access identically.

This suggests a long-term OpenSPM direction: retain the exact raw/native Wii path as
correctness fallback, but progressively replace proven SDK boundaries with host
semantics where the SDK shadow-state contract can be reproduced exactly. Do not
replace the protected `GXBeginDisplayList`/`GXEndDisplayList` v19 seam casually.

### Important WiiCompiled performance caution

WiiCompiled issue #102 reports that full LTO by itself did not improve their Linux
build and that moving GPU work to another thread made performance worse due to
synchronization. Therefore OpenSPM must not assume either technique is a universal
solution.

The current OpenSPM LTO/IPO A/B is still justified because the specific source
shape is materially different: DolRecomp emits calls from hundreds of generated C
translation units into out-of-line `cpu.c` memory helpers, while WiiCompiled already
has translator/runtime memory fast paths. We are testing one concrete cross-TU tax,
not treating LTO as a generic performance switch.

## OpenPete architectural comparison

Public OpenPete documentation describes a static recomp where:

- the retail executable is translated to C;
- console SDK/library calls are serviced by a native runtime;
- matched decompiled C can surgically replace translated functions;
- its custom renderer reads semantic game state rather than reconstructing the PSX
  GPU command stream;
- simulation remains pinned to the console tick while presentation is decoupled and
  interpolated;
- presentation enhancements do not modify gameplay-readable guest RAM;
- mods use explicit per-function override chains and sim/present ownership rules.

OpenPete v0.2.0 also reports 2-5x overall performance improvement depending on scene
and settings after a substantial performance pass, plus improved sim/frame pacing.
Its public author comments mention memoization and stall reduction as part of that
work. The exact source for those optimizations is not public yet, so do not invent
implementation details beyond the published behavior.

The strongest OpenPete lesson for OpenSPM is architectural: semantic native
boundaries and immutable presentation state are first-class. Do not keep paying a
console command-stream reconstruction cost merely because it is the easiest first
correctness bridge.

## DolRecomp CPU/memory finding

Current `ExpansionPak/DolRecomp` C backend emits ordinary guest loads/stores as calls
such as `mem_read32(ctx, ea)` / `mem_write32(...)`.

The runtime declares those helpers in `cpu.h` and defines them out-of-line in
`cpu.c`. `resolve_addr()` performs cached-MEM1, uncached-MEM1, cached-MEM2 and
uncached-MEM2 classification plus available-byte calculation. Each normal memory
helper then performs another width/availability check before endian load/store or
falls back to the external bus path.

OpenSPM's local module currently builds:

- active bridge source;
- staged DolRecomp `cpu.c`;
- roughly 218 generated game C chunks;

as separate translation units with MSVC `/O2`, but previously without `/GL`/LTCG or
CMake IPO. Consequently MSVC could not inline/specialize the hot memory helper chain
across those translation-unit boundaries.

This is a plausible explanation for scene scaling: more NPCs/objects/effects mean
more translated CPU execution and ordinary MEM1/MEM2 traffic, while the measured
renderer boundary still accounts for only a minority of the long frame.

## Candidate A/B: whole-program optimization

Commit `9b148a04f67ebb0b188e05dbddb089c667d69860` changes only
`tools/native_bridge/CMakeLists.txt`.

For MSVC Release builds it:

- checks CMake IPO support;
- fails closed if unsupported;
- enables `INTERPROCEDURAL_OPTIMIZATION_RELEASE` for `openspm_local_native`;
- therefore allows CMake/MSVC to use whole-program optimization (`/GL` + `/LTCG`).

No Wii memory semantics, GX state, scheduler behavior, renderer state, guest-array
lifetime or host API contract changes.

Expected interpretation:

- **Large improvement:** broad generated-CPU/cross-TU helper overhead is a major
  bottleneck. Keep IPO and then inspect generated assembly/profile data before doing
  more invasive RAM fast paths.
- **Small/no improvement:** ordinary helper-call boundaries alone are not enough.
  The next A/B should move the direct MEM1/MEM2 path into generated-code-visible
  inline helpers or teach the translator about known RAM regions, preserving
  external/MMIO behavior exactly.
- **Build/toolchain failure:** rollback this one CMake commit and use the staged
  header/runtime fast-path experiment instead. Do not weaken correctness contracts
  to force LTCG through.

The first IPO build may be substantially slower and may use more memory because the
linker sees hundreds of generated translation units at once.

## Higher-value roadmap if IPO is insufficient

### A. Direct MEM1/MEM2 generated-code fast path

Build on the existing private staged CPU runtime mechanism. The safe goal is to
remove repeated out-of-line `resolve_addr()` calls for ordinary RAM while preserving
all aliasing and bounds semantics:

- cached MEM1 `0x80000000...`
- uncached MEM1 `0xC0000000...`
- cached MEM2 `0x90000000...`
- uncached MEM2 `0xD0000000...`
- exact endian behavior
- reservation/write-journal behavior on writes
- slow external/MMIO fallback unchanged

Prefer inline/generated-code-visible helpers or translator-side known-range lowering
over another callback layer. Unknown addresses must retain the current path.

### B. Host CPU sampling before further micro-optimization

If IPO does not materially move the heavy-area present curve, obtain a host CPU
sample profile during smooth first area vs big choppy area. We need time, not call
counts. Candidate symbols include `mem_read*`, `mem_write*`, generated chunk bodies,
`resolve_addr`, GX parsing/staging, and dispatcher/indirect-call boundaries.

### C. Selective GX SDK HLE/native replacements

Borrow the WiiCompiled pattern carefully. OpenSPM already has host-target and native
direct-call machinery; DolRecomp itself also supports module-local replacement
routing.

Start only with GX functions where:

- the R8PE01 address is known;
- the full GXData/shadow-state side effect is understood;
- ordering relative to raw FIFO/display lists is explicit;
- a direct Aurora semantic equivalent exists;
- behavior can be A/B checked against the real translated SDK path.

Do **not** replace the protected v19 display-list Begin/End seam. State-setting/copy
functions are better early candidates than anything whose CPU-side GXData behavior
is not completely understood.

### D. Renderer array staging after producer CPU is under control

Array staging + guest-index scanning are still the largest measured DrawDone
sub-stages. There are likely more gains available through cached per-VAT scan plans,
immutable array versions and better draw merging. But even eliminating DrawDone
entirely cannot explain/fix the current 30-35 ms heavy frame alone, so it is no
longer the only top-priority performance target.

## Next home test

Run ordinary `M14-UPDATE-RUN.cmd` from the current branch. No cache/save wipe.

Because local-module compile/link flags changed, expect the native local-module
build/link step to be much fuller than a normal incremental build.

For a clean performance A/B:

1. avoid interactive window resizing during the measured path (SDL/OS live-drag
   stalls still contaminate pacing even though resize correctness is fixed);
2. traverse the same first area -> big choppy area -> later area path;
3. confirm spinner and door correctness remains intact;
4. send the resulting `launch(...).log` and subjective comparison.

Key success signal is not only average Wii retrace rate; compare host-present p95/p99
and the player's visible smoothness in the same scene.
