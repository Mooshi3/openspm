# M14 LLVM dev63: inline positive admission cache

Date: 2026-09-16
Branch: `m14-native-execution`

## Why this exists

Dev62 restored playable gameplay with the protected v19 GX display-list semantics. The first long gameplay log after that correctness milestone (`launch(20260916-194906).log`, repo `dddfbfacef35`) finally exposed the hot-path scale clearly.

Heavy gameplay remains simulation-starved: common windows are about 20 `spmarioMain` updates per host second while retraces remain 60/s, with at least one ~1.2 s window at 17 updates / 47 retraces. The player also reports eaten inputs and slow gameplay/enemies.

The same run's teardown counters are grossly out of scale:

```text
M14 LLVM dev58 stats
  queries       = 1,418,420,332
  cache_hits    = 1,418,120,330
  misses        =       218,796
  inserts       =       216,768
  collisions    =       211,233
  bypasses      =        74,478

M14 LLVM ordinary hostcall sample total = 564,027,824
M14 LLVM production-router ordinary    = 564,021,096
  seam lookup  = 563,979,889 calls / 4,908,393,816 comparisons
  host lookup  = 563,619,170 calls / 3,114,766,709 comparisons
  BL rescues   = 2,351,876
```

The dev58 cache was semantically successful but architecturally incomplete: it removed almost all repeated *classification work* after the first positive admission, yet every one of those 1.418 billion hits still crossed from generated LLVM into `ppc_native_region_available()`, through `CPUState::host_call`, into the bridge, hashed/looked up the cache, and returned.

That is the first dev63 target.

## dev62 correctness result to preserve

Dev62 is a success. Gameplay works again. Keep:

- dev61 bounded linked-call entries inside real `GXBeginDisplayList`/`GXEndDisplayList`;
- dev62 bounded linked-call entries inside the verified display-list builder caller;
- protected v19 CPU-shadow / GXData semantics;
- dev58 positive-only admission cache policy;
- all existing dynamic seam behavior.

Do not reopen the freeze-PC loop unless a new correctness regression actually appears.

## dev63 design

### Runtime side

New bridge wrapper:

```text
tools/native_bridge/legacy_c_adapter_v42.c.in
```

v42 includes v41 unchanged in the same translation unit and exposes only:

```c
void *openspm_m14_llvm_region_cache_ptr =
    (void *)g_m14_dev58_region_cache;
```

The array remains owned and written exclusively by dev58. Its entry ABI remains:

```text
uint32 start
uint32 end
uint32 valid
```

No blocked or dynamic range is ever inserted by dev58. v42 performs no new admission decision.

### Compiler side

`tools/native_bridge/apply_openspm_llvm_wpar_codegen_patch.py` now patches pinned DolRecomp `src/backend/llvm/exits.cpp::FunctionEmitter::emitEntry()`.

For the legacy/recompcore runtime only:

1. if `CPUState::host_call == NULL`, enter native as before;
2. otherwise load `openspm_m14_llvm_region_cache_ptr`;
3. reproduce `bridge_dev58_region_hash(start,end)` at **code generation time**, because each emitted function's guest `[start,end)` is constant;
4. generated code reads the one 12-byte cache entry and compares `valid/start/end`;
5. positive hit -> `nativeEntry` directly;
6. miss/null cache -> the exact old `ppc_native_region_available()` query;
7. blocked/dynamic ranges therefore remain fail-closed and authoritative in C.

This is deliberately not a static semantic classifier. The compiler does not guess which SPM regions are safe. It trusts only entries already admitted by the existing runtime semantic router.

## Why this A/B is safe

A dev63 hit can occur only after v41/dev58 previously inserted the exact `[start,end)` tuple. dev58 inserts only when:

- the range is cacheable (not v19/frame-phase/relF dynamic seam geometry);
- the complete underlying production router returned allowed;
- `external_rid` proves the region query was handled.

Collisions overwrite entries and therefore cause future misses/reclassification, never false allows. Dynamic ranges are never inserted and therefore never bypass C.

The intended semantic result is identical to a dev58 positive cache hit; only the C transition/hash/lookup is removed.

## Expected next-run signal

Correctness should remain playable.

The decisive teardown A/B is:

```text
M14 LLVM dev58 stats queries=...
```

`queries` and `cache_hits` should collapse from ~1.418 billion toward the actual miss/bypass/collision-recheck scale. If they remain near the old value, the generated fast path is not being hit and must be debugged before drawing performance conclusions.

Measure gameplay cadence in the same heavy area. The baseline from dev62 is commonly:

```text
spmarioMain ~20/s
retraces    ~60/s
```

with a worst observed window around 17 updates in ~1.2 s.

A large simulation-rate jump would validate native-region C crossings as a major bottleneck. Little/no change despite collapsed query traffic would immediately promote the remaining ~564M ordinary public-dispatch host calls as the next target.

## Ordinary-call frontier after dev63

The dev62 heavy-hitter sampler identifies:

```text
0x8025CABC : sampled 5419
0x8025CAD4 : sampled 1588
```

Do not hardcode an optimization for those addresses yet. Public DolRecomp dispatch checks `ppc_host_call(ctx,address)` before ordinary native lookup, so the 564M count likely represents the next generic dispatcher tax. Resolve/replace that path systemically after the dev63 admission A/B.

Also retained:

```text
direct-BL rescues = 2,351,876
post-MTMSR cold resumes = ~501k
```

These are meaningful but much smaller than the billion-scale callback classes.

## Input interpretation

The input path is alive in the same gameplay run:

```text
input_frames    = 12,411
pressed_getters = 15,378
kpad_reads      = 24,828
```

Do not change frame-latched KPAD/WPAD semantics yet. At 17-20 actual game updates/s, short pressed windows are much easier for game logic to miss. First restore CPU/simulation throughput. If input is still eaten once simulation approaches 60 updates/s, instrument pressed/released lifetime and getter timing separately.

## Files changed for dev63

- `tools/native_bridge/legacy_c_adapter_v42.c.in`
- `tools/native_bridge/CMakeLists.txt`
- `tools/native_bridge/apply_openspm_llvm_wpar_codegen_patch.py`
- this handoff
- `M14-RESUME.md`
- `state/m14-runtime-frontier.json`

## Player test

Run:

```text
M14-LLVM-UPDATE-RUN.cmd
```

If the compiler/build fails, preserve the exact error; likely locations are the patched LLVM `emitEntry()` source contract or the new cache-pointer symbol link. Do not revert the entire LLVM correctness stack.

If it launches, play the same heavy area long enough to judge cadence and inputs, then send the complete `launch.log`. The most important comparison is dev58 query/cache-hit traffic and `spmarioMain` updates/s.
