# M14 LLVM dev98 — entry-only synthetic cold entries (2026-09-18)

## Why this candidate exists

The dev97 player run at repo `9ce180b52d98` fixed the pit-death softlock
without changing Nintendo game state. At HP 0 the game now sets
`next_seq=SEQ_GAMEOVER (4)`, commits sequence 4, advances Game Over through
stages 1-6, and then leaves Game Over normally.

That establishes dev97's HP-zero native-entry repair as a correctness keeper.

The same run still exhibits the heavy-scene performance cliff. Renderer
DrawDone is generally back around ~2 ms after dev94, while the worst gameplay
windows are dominated by native display/main work, for example:

```text
frames=35/1016ms disp=10.247ms main=6.730ms done=12.057ms
frames=42/1016ms disp= 7.174ms main=6.097ms done=10.925ms
frames=49/1016ms disp= 8.978ms main=5.767ms done= 5.985ms
frames=36/1017ms disp= 7.357ms main=6.954ms done=13.929ms
```

The same shutdown reports:

```text
dev87 prefilter bypassed = 1,407,810,823
dev90 native-first hits  =    28,181,109
dev90 fallbacks          =             0
```

The bypass counter is not itself CPU time, but it proves the runtime is still
crossing ordinary translated-code dispatch surfaces at enormous frequency.

## Concrete OpenSPM-specific fragmentation bug

The dev57 correctness repair scans DOL data for executable function pointers.
On the current player binary it finds roughly 8,173 candidates and adds generic
MAP rows named:

```text
openspm_dev57_addr_taken_<pc>
```

dev97 similarly adds legal-entry MAP rows for every aligned PC in its small
HP-zero interval:

```text
openspm_dev97_hp0_motion24_interval_<pc>
```

These rows were intended to mean only: **top-level/native redispatch must be
allowed to enter this PC**.

Pinned DolRecomp `40637c...` has two separate concepts:

1. `collect_llvm_entry_points()`
2. `collect_llvm_partition_points()`

Before dev98, both collectors unconditionally imported every executable MAP
symbol.

That means every conservative dev57 callback candidate was not merely a legal
cold entry. It also forcibly ended the current LLVM chunk.

The 128-instruction chunk plan was therefore being fragmented by thousands of
OpenSPM synthetic safety names. This works directly against the reason the LLVM
backend uses 128-instruction chunks: register allocation and native call
propagation need useful function-sized scopes.

## Why entry-only is supported, not a hack

Pinned DolRecomp passes the complete `entry_points[]` array separately into
the LLVM emitter.

`FunctionEmitter::scanRegionLeaders()` explicitly walks every entry point
inside the current chunk and marks that interior instruction as a region leader:

```cpp
for (u32 i = 0; i < entry_point_count_; i++) {
    u32 address = entry_points_[i];
    ...
    region_leaders_[(address - source_.guest_start) / 4u] = true;
}
```

Therefore an address can remain a legal arbitrary/cold native entry without
also being an LLVM function/chunk boundary.

That is exactly what the generic dev57 and dev97 synthetic entries require.

## dev98 policy

The private DolRecomp patch now filters only these two name prefixes from
`collect_llvm_partition_points()`:

```text
openspm_dev57_addr_taken_
openspm_dev97_hp0_motion24_interval_
```

They remain untouched in `collect_llvm_entry_points()`.

Everything with stronger semantics remains a normal partition boundary,
including:

- the explicit measured dev57 target
  `openspm_dev57_measured_indirect_target_800332a8`;
- dev54/dev56 measured indirect targets;
- dev77 wedding restart entry;
- dev79 measured hot BL sources / bounded continuations;
- scheduler-idle/context entries;
- semantic host seams;
- phase markers and all other non-matching MAP symbols.

This is deliberately not a global "ignore MAP symbols for partitioning"
experiment.

## Private codegen marker

The patched DolRecomp source contains:

```text
OPENSPM_M14_LLVM_ENTRY_ONLY_SYNTHETIC_COLD_ENTRIES
```

and generation prints:

```text
OpenSPM entry-only synthetic symbols: N legal cold entries excluded from chunk partitions
```

For the first player build, `N` is important evidence. We expect thousands,
primarily from dev57. If it is tiny or zero, this hypothesis is not actually
being tested.

The private codegen identity is bumped to:

```text
llvm-inline-wpar-indirect-budget-v9-dev98-entryonly
```

so the transactional updater cannot accept a toolchain manifest built with the
pre-dev98 transform. DolRecomp's own source/object hashes also naturally
invalidate objects whose partition plan changes. **No manual cache wipe.**

## Source contracts

`test_llvm_native_pipeline.py` includes a synthetic dev98 transform that
proves:

- `collect_llvm_entry_points()` remains broad and unfiltered;
- dev57/dev97 synthetic names are filtered only from the partition collector;
- the underlying MAP names remain present;
- the transform is idempotent;
- the generation policy and v9 codegen identity are both locked.

## Player A/B

Run:

```text
M14-LLVM-UPDATE-RUN.cmd
```

No cache wipe.

Expected build evidence:

```text
OpenSPM LLVM private codegen patch ... entry-only-cold-entries=applied
OpenSPM entry-only synthetic symbols: <large N> legal cold entries excluded from chunk partitions
```

Test the same enemy/NPC-heavy rooms used for the dev94/dev97 baseline.

### Success criteria

Primary:

- completed `M14 main-loop phases frames/host_ms` rises materially in the same
  heavy rooms;
- `disp_avg_us` and/or `main_avg_us` falls;
- dev87 bypass rate drops substantially if chunk/dispatch fragmentation was the
  dominant source.

Correctness:

- wedding remains live (`hang_80065128_max=0`);
- Game Over still works;
- ordinary input/rendering remains correct;
- dev90 fallbacks remain zero or otherwise explainable.

### If performance does not improve

Keep dev98 only if it is correctness-neutral and mechanically reduces
fragmentation; otherwise retire it.

The next measured display target is `camDraw()/dispDraw()`: public decomp
shows `spmarioDisp()` is effectively only `camDraw()` plus one trivial flag
write. But do not instrument individual draw callbacks until this much larger
LLVM partitioning confound is tested.
