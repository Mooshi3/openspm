# M14 runtime 003347: pooled exact snapshots are a real performance win

Player log: `launch(20260914-003347).log`

Repo tested: `5d24d64880baf26b56155f7a1dedf466da6d39b0`

Comparison baseline: `launch(20260914-002303).log` at rollback-proven `bed1dcdec007aa9d9e1551b5164e0e6322220e35`.

## Player result

Player described the build as still imperfect but noticeably smoother. This is strongly supported by the coarse runtime data. Keep the pooled exact snapshot architecture unless a later correctness regression is specifically attributed to it.

The runtime marker proves all three retained array layers were active together:

- compact referenced-byte intervals;
- pooled immutable exact snapshots;
- pointer-indexed exact lookup.

No retired 65,536-draw/no-guest-index fast path was active.

## Objective A/B

Whole-log coarse statistics are not perfectly scene-aligned, but the runs cover the same prologue/Chapter 1 path and both include the enemy-room workload. The change is too large and consistent to be noise.

### 2,048-draw CPU windows

Rollback baseline -> pooled snapshots:

- median: ~9.745 ms -> ~3.932 ms (**~60% lower**)
- p90: ~37.152 ms -> ~8.310 ms (**~78% lower**)
- p95: ~46.745 ms -> ~9.686 ms (**~79% lower**)
- p99: ~66.541 ms -> ~11.491 ms (**~83% lower**)
- worst complete window: ~100.237 ms -> ~49.232 ms (**~51% lower**)
- worst individual draw observed: ~60.335 ms -> ~36.306 ms

### DrawDone synchronous drains

- median logged slow drain: ~53.813 ms -> ~18.252 ms (**~66% lower**)
- p90 logged slow drain: ~105.294 ms -> ~27.068 ms (**~74% lower**)
- worst logged drain: ~269.411 ms -> ~54.511 ms (**~80% lower**)

### Delivered retraces

- whole-log median: 46/s -> 52/s
- whole-log mean: ~40.98/s -> ~52.17/s
- worst one-second sample: 12/s -> 36/s

The 60.75 MHz Broadway timebase remains correct; improvement comes from reducing synchronous renderer CPU work, not from retiming simulation.

## Conclusion

Per-entry `std::vector<u8>` snapshot allocation/free churn was a major component of the indexed guest-array staging bottleneck. Pooling snapshots while retaining exact byte-for-byte `memcmp` validation is therefore runtime-proven valuable and correctness-compatible with the restored v19 path.

Do not weaken guest-memory coherency to chase the remaining gap.

## Next narrow candidate

The first pool implementation uses:

1. `vector::resize(old_size + uploadSize)`, which value-initializes the new `u8` range to zero;
2. `memcpy` of the source bytes over that freshly-zeroed range.

That unnecessarily writes each new snapshot range twice. Added `apply_guest_array_snapshot_single_copy_patch.py`, which changes only the append operation to `vector::insert(end, source, source + uploadSize)` after the same pre-reserve. Snapshot offsets/sizes, exact `memcmp`, upload ranges and GX semantics remain unchanged.

Expected marker:

`M14 raw guest-array pooled exact snapshots single-copy append active; cache semantics remain byte-exact`

Status: runtime-unproven successor optimization. No `.openspm` or save wipe required.
