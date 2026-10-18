# M14 runtime 145508 — LTCG buys headroom; inline RAM/PSQ fastpath candidate

Date: 2026-09-14
Player log: `launch(20260914-145508).log`
Tested repo: `8a59004f178f`

## Player result

The player reports the game is still visibly choppy/slow in the gameplay areas.
That subjective result is authoritative: `/GL` + `/LTCG` is not the complete fix.

The telemetry nevertheless shows a real change in where the degradation begins.
At 4096 successful host presents this run is still close to the 60 Hz envelope:

- average present interval: 16.853 ms
- p50: 17.0 ms
- p95: 19.0 ms
- p99: 20.0 ms
- intervals >25 ms: 12
- intervals >33 ms: 5
- `Present()` call average: 0.181 ms

The same run later grows into the familiar load-dependent tail. At 8192 presents:

- average: 18.133 ms
- p50: 17.5 ms
- p95: 30.5 ms
- p99: 33.0 ms
- intervals >25 ms: 678
- intervals >33 ms: 55
- `Present()` average: 0.194 ms

At shutdown after 10469 presents the accumulated histogram is p95 34.5 ms / p99
36 ms. Therefore whole-program optimization appears to delay the throughput cliff
but does not change the underlying scene-scaling law enough to make gameplay feel
smooth.

Do not describe LTCG as a complete negative. Keep it provisionally while the next
CPU A/B is tested; it is semantics-neutral and the present curve is materially
better early in gameplay than the pre-LTCG 041041 curve.

## What remains exonerated

In the heavy gameplay windows:

- `Present()` itself remains ~0.2 ms;
- presentation safepoint sync averages only ~7-8 us;
- frame-slot/staging-slot/staging-map acquisition remains effectively zero;
- authoritative Wii timing remains overwhelmingly 60 retraces/sec;
- DrawDone is scene-dependent but normally around 2.8-3.3 ms average, with p95
  around 3.5-4.25 ms and only isolated long outliers.

At the 4096 window DrawDone averages 2.921 ms. Its producer draw profile is about
2.568 ms/frame, dominated by indexed-array staging (~0.91 ms/frame) and the
mandatory guest-index scan (~0.67 ms/frame).

At the 8192 window DrawDone averages 3.095 ms; the draw profile is about 2.729
ms/frame, with indexed arrays ~1.02 ms/frame and scan ~0.68 ms/frame. One isolated
~24.8 ms DrawDone occurs, but that does not explain the recurring 30-33 ms present
interval tail.

Thus the missing recurring frame time is still primarily outside Present, frame
acquisition and the measured DrawDone body.

## New architectural candidate

WiiCompiled/DolRecomp research exposed a remaining mismatch that generic LTCG
cannot reliably erase: translated C uses generic scalar `mem_read*`/`mem_write*`
helpers, while stronger recomp runtimes put the normal guest-RAM proof directly in
the generated hot path and reserve a cold/general path for MMIO and unusual
mappings.

DolRecomp's own `cpu.h` also deliberately emits `ppc_psq_load_inline` /
`ppc_psq_store_inline` hooks and states that a hosting runtime can specialize the
unquantized GQR type-0 case. Its standalone runtime simply forwards those helpers.

The next M14 candidate therefore patches **only the private staged DolRecomp
runtime** and compiles **only generated chunk sources** with an opt-in inline RAM
path.

### Scalar RAM path

For generated chunks only:

- canonicalize Broadway cached/uncached RAM aliases by clearing bit 30;
- prove the complete access lies inside MEM1 or MEM2;
- perform the exact big-endian read/write directly from `CPUState::ram` or
  `CPUState::mem2`;
- otherwise call the original DolRecomp helper unchanged.

Therefore MMIO, WPAR, unmapped addresses and boundary-crossing accesses still use
the authoritative existing path.

Writes duplicate the two guest-visible side effects from stock DolRecomp before
the write:

1. invalidate an active reservation when the store aliases the same 32-byte
   reservation block (with cached/uncached bit 30 ignored), and
2. invoke the existing MEM1 write journal with the same offset and size.

MEM2 remains unjournaled exactly as stock DolRecomp does today. Endian behavior is
still DolRecomp's `read_be*` / `write_be*` implementation.

The existing exact WPAR transform remains underneath. A `0xCC008000` access does
not prove as MEM1/MEM2 and therefore falls through to the staged stock helper,
where the proven direct WPAR hook consumes it.

### Paired-single type-0 path

The generated `_inline` PSQ helpers now directly handle only the stock common
case where:

- HID2.PSE is enabled;
- HID2.LSQE is also enabled for non-indexed instructions;
- GQR type is 0 (unquantized float);
- the effective address is 4-byte aligned.

Loads reproduce DolRecomp's exact `extend_f32_bits` conversion. Stores retain the
stock f64->f32 conversion and flush an f32 denormal to zero before the big-endian
store. W=1 second-lane behavior is preserved.

Every disabled, misaligned or quantized case falls back to stock
`ppc_psq_load/store`, preserving exception behavior and uncommon GQR semantics.

## Files / commits

- `tools/native_bridge/stage_cpu_memory_fastpath.py`
  - initial commit `ce70695e057c94b969fb695670edb375f714fcbd`
- `tools/native_bridge/build_local_module.py`
  - stages both WPAR and RAM/PSQ transforms
  - commit `e81c9a8428b8c820909d485898b4c4d77ec6eef7`
- `tools/native_bridge/CMakeLists.txt`
  - defines `OPENSPM_M14_INLINE_MEMORY_FASTPATH=1` only for generated chunk TUs
  - `cpu.c` and the OpenSPM bridge remain stock-helper callers/fallbacks
  - commit `0c090d14f0f34979bcd681b5f034a5efe0521ea7`
- `tools/native_bridge/refresh_staged_cpu_runtime.py`
  - normal `M14-UPDATE-RUN.cmd` incremental refresh now applies both transforms
  - commit `30db74944ee4e017cd9a073645cc5d056f18b87f`

The last refresh fix is essential: ordinary update/run does not invoke the full
`build_local_module.py`; it refreshes the cached private CPU runtime through
`refresh_staged_cpu_runtime.py` before rebuilding.

## Next player test

Run ordinary `M14-UPDATE-RUN.cmd`; do not wipe caches/saves and do not use GitHub
Actions.

Expected configure/update markers include:

- `OpenSPM M14 generated CPU inline MEM1/MEM2 + PSQ0 fastpath active`
- `M14 inline MEM1/MEM2 + PSQ0 CPU transform: applied`
  (or the equivalent idempotent state on a repeated staging pass)
- the existing MSVC whole-program optimization marker.

Traverse the same progression far enough to compare the light first gameplay
area, the large/heavy area, and the later area. Avoid manual live window dragging
during the performance comparison because SDL/window dragging still introduces
large host stalls even though it no longer corrupts Wii graphics state.

Primary success criterion is subjective smoothness. Secondary telemetry criterion
is whether the 4096->8192 host-present histogram stops developing the ~30-35 ms
p95 tail.

If the game fails before launch, capture the updater output: the staged transform
is deliberately source-shape checked and should fail closed rather than partially
patching an incompatible local DolRecomp header.

If it launches but correctness regresses, first distinguish ordinary gameplay
from explicit host resize. The new RAM path itself should be rolled back as a
unit rather than weakening reservation/journal/MMIO safeguards.

## Shutdown noise

The end of 145508 contains repeated GX framer rejects followed by Ctrl-C exit
`0xC000013A`. They occur in the shutdown tail after the player terminates the run;
continue treating them as shutdown fallout unless the same FIFO rejection is
observed before exit during ordinary gameplay.
