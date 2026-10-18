# M14 dev91 — staging-backed exact guest-array snapshots

Date: 2026-09-18

## Why this candidate exists

The dev88 player run (`launch(20260918-180757).log`) confirmed a real
performance improvement after retiring the temporary per-draw DrawDone clock
probe, but the deepest enemy/NPC section still falls to roughly 30-33 completed
game frames/s against ~60 VI retraces.

The important threshold is narrow rather than a missing 2x everywhere:

- medium gameplay can already complete about 59-61 frames/s;
- deepest `disp+main` work is roughly 13-17 ms;
- DrawDone producer work is commonly ~2.5-2.9 ms and rises toward ~4 ms in the
  deepest section;
- therefore a few milliseconds of real producer CPU decide whether a frame
  catches the current 16.667 ms VI or waits for the following retrace.

dev89 already targets the two largest measured raw guest-array CPU buckets:
allocation/index lookup and mandatory guest-index scanning.

dev91 removes another redundant full-memory pass from the same proven hot path.

## Upstream Aurora ownership proof

Pinned Aurora:

`encounter/aurora@749d6ee7a22bdfab78c8ece9047bca5d79aa72ca`

The pinned recording path proves:

1. `begin_frame()` obtains one mapped WebGPU staging buffer;
2. `frame.storage` is a `ByteBuffer` view into that mapped buffer;
3. `gfx::push_storage()` appends the exact source bytes directly to
   `frame.storage`;
4. the non-owning `ByteBuffer` cannot relocate: growth beyond its mapped
   capacity aborts rather than reallocating;
5. the staging buffer remains mapped until `end_frame()`, after producer-side
   GX recording for the frame is complete.

The existing exact guest-array cache therefore copied every cache miss twice:

```text
guest bytes -> Aurora mapped storage     (required GPU upload)
guest bytes -> separate CPU snapshot     (cache validation copy)
```

Both copies contain identical bytes and have the same producer-frame lifetime.

## dev91 change

Tool:

`renderer/aurora/apply_guest_array_staging_snapshot_alias_patch.py`

The transform adds a small read-only Aurora recording helper:

```cpp
const uint8_t* storage_range_data(Range range) noexcept;
```

It validates the requested range against the current mapped `frame.storage`
and returns a pointer to the bytes already copied by `push_storage`.

The guest-array stage entry no longer stores:

- `snapshotOffset`;
- `snapshotSize`;
- a separate frame-local snapshot byte pool.

A reuse candidate remains valid only when all of these still hold:

1. exact guest source pointer;
2. exact stride;
3. staged range is at least the requested size;
4. byte-for-byte `memcmp` between current guest bytes and the exact bytes
   already uploaded into Aurora staging succeeds.

No hash or visibility epoch replaces `memcmp`.

Expected runtime marker:

```text
M14 raw guest-array staging bytes are exact snapshots; duplicate CPU snapshot copy retired
```

## dev89 retained underneath

dev89 remains active and must also be present in the exact Aurora source compiled
for the test:

```text
M14 raw guest-array stage-head index + scan-plan active
```

It provides:

- 2048 fixed frame-local stage buckets instead of
  `unordered_multimap` heap nodes;
- stage-vector index chains with exact pointer/stride/memcmp authority;
- once-per-draw vertex-record layout derivation;
- min/max-index interval reduction for normal one-index attributes;
- exact per-slice handling retained for NBT3.

## dev90 retained

The LLVM dispatcher still uses dev87 by default. dev90 tries native execution
first only for the three exact OpenSPM-installed structural entries:

```text
0x8027551C
0x80D1F414
0x80086848
```

Lookup refusal immediately falls back to the historical host route.

Expected teardown:

```text
M14 LLVM dev90 structural native-first stats hits=... fallbacks=...
```

## Local A/B is now fail-closed

`M14-LLVM-UPDATE-RUN.cmd` -> `run_m14_llvm_candidate.py` now:

1. verifies the player DOL/relF identity;
2. runs source-only LLVM + dev89/dev91 transform contracts;
3. explicitly reruns CMake configure against the existing candidate rather than
   relying only on implicit Ninja reconfigure;
4. reads `OPENSPM_AURORA_SOURCE_DIR` from that candidate's real
   `CMakeCache.txt`;
5. refuses to build unless the exact referenced Aurora source contains dev89
   and dev91 markers/helpers and no retired multimap/snapshot-pool residue;
6. builds the host runtime/renderer contracts;
7. builds the private LLVM player module from the player's own game;
8. transactionally launches it and restores the known-good C module afterward.

The candidate proof line is:

```text
[OpenSPM] Renderer performance candidate proof PASS: dev89 fixed-bucket/scan-plan + dev91 staging-backed exact snapshots are present in configured Aurora.
```

If that line is absent, the performance A/B is not allowed to launch.

## CI note

The repository's GitHub Actions jobs are currently failing before any job step
starts (empty step lists across independent Ubuntu and Windows jobs). That is an
Actions runner/account/infrastructure failure, not a compile result. Do not call
those runs either green or red source validation.

The player updater therefore contains the authoritative preflight for this
candidate. It will stop before launch on a Python transform error, partial
Aurora state, missing performance markers, CMake failure, Windows compile
failure, contract-test failure, LLVM generation failure, or transactional
staging failure.

## Player test

Run only:

`M14-LLVM-UPDATE-RUN.cmd`

No cache wipe and no full HOME setup first.

Before the game starts, confirm these appear:

```text
PASS: OpenSPM LLVM native pipeline contracts
Aurora guest-array stage-head + scan-plan optimization applied
  OR ... already active
Aurora staging-backed exact guest-array snapshots applied
  OR ... already active
Renderer performance candidate proof PASS
```

In the runtime log confirm:

```text
M14 raw guest-array stage-head index + scan-plan active
M14 raw guest-array staging bytes are exact snapshots; duplicate CPU snapshot copy retired
M14 LLVM dev90 exact structural native-first active
```

Then pass the wedding and spend at least 30-60 seconds in the same heavy
enemy/NPC area from dev88.

Primary comparison:

- completed `M14 main-loop phases frames/host_ms`;
- `M14 producer sync window kind=draw_done avg_us/p95`;
- subjective Mario/enemy jitter.

Correctness gates:

- normal textures/doors/spinners;
- wedding passes;
- `hang_80065128_max=0`.

Performance success means the deepest windows materially move above the old
~30-33 completed frames/s. Because the old failure is a VI deadline cliff, even
a modest raw CPU reduction can produce a large visible jump when a frame drops
below 16.667 ms.

If dev89/dev91 lower DrawDone substantially but the same deepest windows remain
~30-33, retain the correctness-clean renderer win and move next into dev81's
native `disp/main` ownership rather than changing VI semantics or reviving
broad dev80 dispatch.
