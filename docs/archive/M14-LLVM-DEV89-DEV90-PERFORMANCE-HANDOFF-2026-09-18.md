# M14 dev89/dev90 — exact guest-array hot path + structural dispatch trim

Date: 2026-09-18

## Player result selecting this frontier

Player log: `launch(20260918-180757).log`.

Player report: **definite improvement, definitely still needs more**.

dev88 successfully retired the temporary per-draw DrawDone profiler. The runtime
no longer emitted `M14 GX DrawDone substage profile` lines. Correctness remained
stable through the tested path and shutdown still reported:

```text
hang_80065128_max=0
```

The A/B is meaningful rather than cosmetic. Early/light gameplay repeatedly
completed about 59-61 frames per host second. Several medium gameplay windows
also recovered to 57-61 completed frames/s. In the deepest tested enemy-room
segment the old alternate-retrace cliff still returned:

```text
frames ~= 30-33 / host second
retraces ~= 60 / host second
disp ~= 6-9 ms average, with some higher windows
main ~= 6.7-8.1 ms average
done ~= 16-20 ms average
```

The synchronous DrawDone producer window after profiler retirement is normally
around 2.5-2.9 ms in medium-heavy play, compared with roughly 3.8-4.4 ms in the
profiled dev87 run, but the heaviest section still climbs back to about
3.8-4.4 ms.

This is now a narrow deadline problem. Heavy `disp+main` can consume roughly
13-17 ms before the synchronous renderer work. A 2-4 ms DrawDone then pushes the
producer past the 16.667 ms VI deadline; the completed game loop waits for the
following retrace and appears as ~30 FPS. The dev88 result therefore justifies
optimizing real renderer CPU work rather than returning to the already-falsified
budget-guard theory.

dev87 remains mechanically successful in the same run:

```text
dev87 bypassed = 514,897,575
ordinary callback sample total = 52,811,584
heavy sample:
  0x8027551C : 996
  0x800361F0 : 528
  0x801BBF80 : 7
  0x80132730 : 4
```

The first survivor is the explicit scheduler-idle reentry. The other sampled
addresses are not assumed safe merely because they are hot.

## dev89 — exact raw guest-array hot-path optimization

Tool:

`renderer/aurora/apply_guest_array_hotpath_v2_patch.py`

CMake applies it idempotently to the cached pinned Aurora checkout after dev88's
profiler retirement.

The previous dev87 DrawDone profiler measured roughly:

- exact guest-array staging/cache: ~1.47 ms/frame;
- mandatory guest-index scan: ~0.83 ms/frame.

dev89 changes only host representation and loop structure.

### 1. Fixed stage-bucket index

The proven cache previously used:

```text
unordered_multimap<const void*, stage_index>
```

Every staged interval therefore allocated a hash node. Heavy frames have hundreds
of exact staging misses.

dev89 replaces that map with 2048 fixed bucket heads. Every stage entry stores the
previous entry in its bucket inside the already-existing stage vector.

A candidate still reuses storage only when all of these match:

1. exact source pointer;
2. exact stride;
3. snapshot size covers the requested interval;
4. byte-for-byte `memcmp` succeeds.

Hash collisions only add metadata candidates. They cannot create reuse. Snapshot
bytes, upload bytes, upload ranges, draw ordering and guest mutation visibility
are unchanged.

### 2. Per-draw scan plan

The old mandatory bounds scan rediscovered every vertex descriptor and attribute
layout for every vertex.

dev89 derives the fixed vertex-record layout once per draw, then scans only raw
guest-index fields.

For ordinary one-index attributes, the requested byte interval is exactly:

```text
[min_index * stride,
 max_index * stride + element_size)
```

so the inner loop tracks only min/max index. The two address multiplications and
range proof happen once after the scan rather than once for every vertex index.

NBT3 retains the general exact per-slice computation because N/B/T have distinct
within-record offsets.

No vertex bytes are skipped and the transformed routine proves that
`recordBytes * vtxCount == vertexData.size()`.

### Explicit non-changes

dev89 does **not**:

- trust a visibility epoch instead of exact memcmp;
- hash snapshots instead of comparing bytes;
- change GXInvalidateVtxCache behavior;
- change CP base semantics;
- change draw merging;
- change FIFO ordering;
- thread the renderer;
- change presentation/retrace timing.

Those boundaries matter because earlier broad gameplay-GX experiments regressed
textures/spinners/doors.

Expected configure result:

```text
Aurora guest-array stage-head + scan-plan optimization applied
```

or on a repeat run:

```text
Aurora guest-array stage-head + scan-plan optimization already active
```

A fresh untouched Aurora source intentionally prints a skipped/not-ready message
rather than making recovery fail before the older proven patch layers are present.

## dev90 — three exact structural native-first entries

Tool:

`tools/native_bridge/apply_llvm_hostcall_prefilter.py`

dev87 remains the default public-dispatch policy. dev90 adds a native-first
attempt for exactly three structural compiler-map rows OpenSPM itself installs:

```text
0x8027551C  openspm_scheduler_idle_reentry
0x80D1F414  openspm_dev79_hot_bl_source_80d1f414
0x80086848  openspm_dev79_hot_bl_source_80086848
```

This is deliberately **not** dev80's reconstructed legal-entry policy.

Generation proves:

- each coordinate is executable;
- each exact expected map row exists;
- none collides with semantic map rows, platform host targets or retained exact
  host-semantic coordinates;
- both dev79 BL sources still decode to their measured executable targets;
- scheduler idle is not an LR-writing linked transfer.

Dispatch behavior for one of those exact PCs is:

```text
try dolrecomp_call_original(pc)
if it succeeds:
    count dev90 hit and return
if it refuses:
    count dev90 fallback
    run unchanged dev87/host-first routing
```

Every other map entry remains under dev87's conservative policy. In particular,
the hot sampled `0x800361F0` is **not** promoted merely because it was observed.

Expected startup marker:

```text
M14 LLVM dev90 exact structural native-first active:
```

Expected teardown marker:

```text
M14 LLVM dev90 structural native-first stats hits=... fallbacks=...
```

A healthy run should have nonzero hits and ideally zero or very few fallbacks.
A large fallback count means one of the supposedly explicit restart entries is
not accepted by the generated cold-entry lookup and dev90 should be removed or
narrowed rather than papered over.

## Next player A/B

Run:

`M14-LLVM-UPDATE-RUN.cmd`

No cache wipe.

Primary gates:

1. configure applies/already-has dev89;
2. generation reports `dev90_native_first_entries=3`;
3. runtime prints the dev90 startup marker;
4. wedding/path correctness remains intact and `hang_80065128_max=0`;
5. play the same heavy enemy/NPC area for at least ~30-60 seconds;
6. compare `M14 main-loop phases` and DrawDone producer windows to dev88;
7. on exit capture dev90 hit/fallback totals and the remaining ordinary heavy sample.

Strong dev89 success means the deepest producer windows stop living above the
16.667 ms threshold and completed frames materially move above ~30-33/s, ideally
toward 60.

If DrawDone falls materially but the deepest completed cadence remains ~30, use
dev81 phase ownership to optimize `disp/main` native work next. Do not restore
broad speculative host-call caches or dev80-style reconstructed native-first
ranges.

If dev89 produces any graphics/door/spinner regression, remove only the dev89
configure step and keep dev88/dev87; its semantics are intended to be identical,
so any visual change is a bug in the transform, not an acceptable tradeoff.
