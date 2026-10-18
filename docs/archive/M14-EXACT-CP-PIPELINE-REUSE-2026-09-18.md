# Exact duplicate CP pipeline reuse and performance assessment

Branch: `m14-native-execution`. Run `M14-LLVM-UPDATE-RUN.cmd`; no cache wipe.

## Evidence and limits

Reviewed `launch(20260918-215622).log` (player revision `7de3e6307969`),
the current handoff/frontier, Aurora's pinned implementation, and the concurrent
dev94 CPU-snapshot rollback. The dev94/dev95 changes are separate work and must
be preserved. This patch does not claim a measured gameplay FPS improvement.

Representative sustained heavy windows in the player's log:

| Measurement | Observed |
| --- | --- |
| Completed main-loop frames | 30 per ~1,000 ms |
| Display phase | ~5.8–6.5 ms |
| Main phase | ~6.3–6.7 ms |
| Synchronous DrawDone producer work | ~6.3–7.3 ms in nearby 256-call windows |
| VI retraces | ~60/s |
| Legacy `spmarioMain` counter | ~60/s, known double observation; not completed FPS |

The DrawDone windows and phase windows are different aggregations, not an exact
per-frame decomposition. Nevertheless, producer work near 19–20 ms explains a
60-to-30 deadline cliff on a 16.667 ms retrace grid. The large `done_avg_us`
includes synchronization/waiting; it is not all GPU rendering cost. A cheap
Present() call alone also does not prove the GPU is idle.

The latest tested dev91 path compares CPU data directly against mapped GPU
staging bytes. Microsoft documents that CPU reads from D3D12 UPLOAD/write-combined
memory can be prohibitively slow:
https://learn.microsoft.com/en-us/windows/win32/api/d3d12/nf-d3d12-id3d12resource-map
This is a supported explanation for its regression, not a measurement of the
driver's actual page policy. Concurrent dev94 restores CPU-cached snapshots.

LLVM is not evidence of an automatic fast PC port: it accelerates translated
guest code, while OpenSPM still serializes Wii GX commands and synchronously
decodes/prepares thousands of draws on the producer thread. The current evidence
supports fixing adapter costs; it does not establish a fundamental tooling or
hardware ceiling. Avoid promising that these particular changes finish the job.

## Independent fix in this change

`cp_arraybase` already detects an exactly identical mapped pointer, span,
endianness and raw-array mode and returns without changing array state. However,
the `kCpRegs` table sets `DirtyPipeline` in `handle_cp` *before* that guard runs.
Consequently an otherwise clean draw repeats pipeline-configuration population,
shader-info construction and pipeline lookup, then sets `DirtyUniform` and
rebuilds uniforms. It also loses an opportunity for ordinary adjacent-draw merging.

`apply_cp_duplicate_pipeline_patch.py` moves the same conservative pipeline-dirty
operation into the decoder. The existing exact duplicate branch restores the
dirty flags saved at entry. No bit that was previously set is cleared. All
nonduplicate paths, failed maps and unsupported source-port calls still receive
the original pipeline dirty bit. Real base transitions still invalidate staged
ranges and set immediate dirtiness. Explicit vertex-cache invalidation, exact
CPU snapshots, stride/format writes and FIFO ordering are unchanged.

This is narrower than the earlier rolled-back experiment that removed pipeline
dirtiness from **all** base changes and also changed visibility/cache semantics.
The earlier experiment is not re-enabled.

The existing configure companion chain applies this patch after its other
migrations. It checks exact source fragments and rejects partial/drifted source.
Runtime reports powers-of-two counts only, with no per-draw clocks:

```
M14 exact duplicate CP array-base pipeline reuse active skipped=...
```

The counter measures duplicate writes whose unnecessary invalidation was
suppressed. It is not a count of avoided draws, pipeline builds, or saved time:
several writes can precede a draw and another register can still dirty it.

## Configure recovery repair

An older, already-quiet adapted checkout exposed another actual configure
failure: dev88 returned `not-present` when its temporary profiler had never been
installed, but dev89 required the retirement marker. The retirement script now
recognizes the exact quiet handler, rejects residual profiler symbols, and adds
only the zero-cost retirement comment. Fresh upstream source stays untouched.
No diagnostic profiler needs to be installed merely to retire it.

## Validation performed

- Compiled the actual patched Aurora `regs.cpp` and `command_processor.cpp` at
  `-O3` using available Linux dependencies and the real Aurora headers.
- Executed 2,304 C++ cases using the actual extracted production decoder and
  `handle_cp`, covering all 256 initial dirty masks across nine scenarios:
  duplicate, explicitly invalidated range, pointer change, span change,
  endian change, raw-mode transition, map failure, absent mapper and mutation.
- Verified fresh/check-only/cached/idempotent migration and partial-source
  rejection; verified the quiet-handler retirement migration and residue rejection.
- Ran the full configure companion chain twice on an isolated adapted checkout.
- Verified the current dev89/dev94/dev92 configured-renderer candidate proof.

Reproduce decoder and migration checks after configuring Aurora:

```
python renderer/aurora/test_cp_duplicate_pipeline.py <adapted-Aurora-checkout>
```

The Windows/MSVC executable, D3D12 runtime, and retail heavy gameplay were not
available in this workspace. No actual FPS improvement is claimed yet.

## Next player measurement

Use the ordinary LLVM update/run command and spend 30–60 seconds in the same
heavy room. Check the CPU snapshot restore/baseline marker, the exact duplicate
pipeline reuse counter, completed `M14 main-loop phases` FPS, and nearby DrawDone
windows. Do not read the legacy doubled simulation counter as FPS.

If heavy gameplay still misses 60 Hz after these changes, the remaining work is
the actual CPU cost inside display/main/DrawDone. Obtain a host sampling profile
with native module and renderer symbols in that scene before adding more
address-specific dispatch changes. Prior reductions in callback/rescue counts
did not establish those counts as the dominant time cost. Any later asynchronous
FIFO design must snapshot guest data at defined visibility boundaries; merely
moving the parser to a worker can race mutable guest memory and repeat the old
correctness problems.
