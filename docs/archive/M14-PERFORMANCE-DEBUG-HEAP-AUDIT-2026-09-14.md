# M14 performance audit: normal launches lacked a debug-heap opt-out

Audited branch: `m14-native-execution`, base `34da266c0e882b1faf7b33f0e7f028a23cb4f1c4`.
Player attachment: `launch(20260914-032153).log`, reporting repo `5b60f3512903`.
The player explicitly confirmed all tests use `M14-UPDATE-RUN.cmd`.

## First fix: repair the launch environment before another renderer experiment

The complete ordinary launch chain is:

1. `M14-UPDATE-RUN.cmd` builds and invokes `LAUNCH-M14-CANDIDATE.cmd`.
2. That script always invokes `tools/native_bridge/run_windows_debug.py`.
3. The wrapper called `CreateProcessW` with `DEBUG_ONLY_THIS_PROCESS` and an
   inherited environment, without setting `_NO_DEBUG_HEAP=1` anywhere in this chain.

This is not a special diagnostic run the player opted into. It is how the
ordinary player executable has been launched.

Microsoft documents that starting under a debugger enables heap debug options
which prevent the low-fragmentation heap (LFH), and prescribes
`_NO_DEBUG_HEAP=1` to allow LFH under the debugger:

- https://learn.microsoft.com/en-us/windows/win32/api/heapapi/nf-heapapi-heapsetinformation
- LLDB independently defaults its Windows disable-debug-heap setting to true:
  https://lldb.llvm.org/use/settings.html#platform-plugin-windows-disable-debug-heap

Release `/O2` compilation does not correct this process-launch policy. The
policy also affects host libraries, including Aurora/Dawn, rather than only
the recompiled game.

**Code defect confirmed; magnitude of player speedup not measured yet.** The
uploaded log does not record the inherited `_NO_DEBUG_HEAP` value or actual heap
mode. An externally set environment variable could already have opted out. Do
not claim that all historic runs definitely used debug heaps or that this alone
has fixed every hitch before the next Windows result.

## Why this takes precedence over the renderer candidates

The prior strongest measured win was removing hundreds/thousands of per-frame
snapshot allocations/frees. Indexed-array staging uses a node-allocating
`std::unordered_multimap` which is cleared and rebuilt every packet. Aurora's
rendering also allocates draw/pass records and retires packet containers; Dawn
performs its own allocations. The debug heap can make ordinary allocation
traffic appear to be a much deeper renderer bottleneck.

This is a plausible common cause of the observed allocation sensitivity, but
it is an inference, not a substitute for a Windows A/B.

## Implemented change

`run_windows_debug.py` now constructs a child-only Unicode environment block:

- normal mode sets `_NO_DEBUG_HEAP=1` **before CreateProcessW**;
- `CREATE_UNICODE_ENVIRONMENT` accompanies the explicit environment block;
- native `GetEnvironmentStringsW` preserves inherited variables, including
  hidden drive-directory entries and non-BMP Unicode;
- inherited policy is matched case-insensitively and replaced once;
- the parent environment is not modified;
- explicit `--debug-heap` sets `_NO_DEBUG_HEAP=0` for allocation diagnostics;
- the crash debugger, exception disposition, native frontier capture, inherited
  log handles, and exact 32-bit child exit status are retained.

Expected startup log suffix:

```text
heap_policy=normal _NO_DEBUG_HEAP=1
```

No GX parsing, shader, guest-array coherence, presentation, VI timing, or game
code changes accompany this fix. No cache/save reset is needed. The existing
parser-progress rollback and frame-boundary resize candidate remain present.

## Validation

`test_windows_debug_heap.py` covers:

- case-insensitive override, explicit diagnostic mode, and double-NUL blocks;
- Unicode, embedded equals signs, and hidden drive directories;
- native environment traversal using UTF-16 byte lengths and cleanup on failure;
- actual `debug_run` control flow with a mocked second-chance access violation:
  `CreateProcessW` receives the environment/flags, crash capture runs, the
  exception is passed onward, and `0xC0000005` is preserved;
- on native Windows, real debugger child processes use a private heap and
  `HeapSetInformation` / `HeapQueryInformation`. Normal mode must enable LFH and
  report type 2, preserve Unicode, remain debugged, and return exit status 7.
  Explicit debug mode is also launched and reports its observed heap type.

Local result: **5 portable tests passed, 2 native Windows tests skipped** on the
Linux work host. Python compilation and whitespace checks passed. No Windows
gameplay, full renderer build, or FPS improvement is claimed.

The test runs automatically during `M14-UPDATE-RUN.cmd`, after Python discovery.
The native Windows probe will therefore validate the real API behavior on the
player's machine before the game launches; no extra manual setup is required.

## Current runtime evidence and chronology

The attached 032153 log has 191 authoritative timing samples: mean 59.827
retraces/s, median 60, minimum 47, maximum 61. These are retrace deliveries,
not host frame-time or GPU throughput measurements. The player still reports
heavy-area choppiness.

It ends with an approximately 161.7 ms `update-events` wait and a second-chance
`0xC0000005` read from `0x9`. Its tested revision predates these five commits:

- rollback of the failed 32 KiB parser-progress experiment (`cd795088...`);
- native access-violation frontier capture (`238f04ad...`);
- regression handoff;
- defer resize/event processing while a recording frame is open (`116b80b...`);
- documentation of that resize candidate (`34da266c...`).

The old shutdown-only `0x81349E80` nested-DL diagnosis is still retired. This
attachment does not prove the newer resize fix succeeds or fails.

## Renderer findings retained for the next measured baseline

These were inspected, **not changed**. Do not stack them over the heap fix just
to claim a larger performance patch.

1. **Bounding-box contention.** `apply_raster_patch.py` injects four unconditional
   atomic min/max operations to the same four storage words in every surviving
   fragment. This is a serialization concern. A safe next experiment can use
   monotonic atomic-load guards or an exactly reduced sharded accumulator while
   preserving ordered component writes and synchronous readback. Do not disable
   bbox or invent stale/asynchronous bounds: Tippi and effects consume them.
   No GPU measurement currently identifies its share of total frame time.
2. **Early-depth draw expansion.** `apply_zcomp_loc_patch.py` emits two host draws
   and disables merging for an early-Z alpha case. Its predicate uses
   `AlphaCompare::operator bool`, which only checks whether either comparator
   differs from ALWAYS. For example GEQUAL(0) AND ALWAYS can never reject an
   8-bit alpha but enters this workaround. The shader generator itself already
   simplifies certain such comparisons. Exact alpha predicate classification
   could avoid needless pairs, but frequency in the player's scene is unknown.
   Do not merge arbitrary depth/color pairs: overlapping primitives and blends
   make reordering observably different.
3. **Do not blindly implement the old FramePacket-storage snapshot proposal.**
   Inspection of pinned `lib/gfx/frame.cpp::begin_frame` shows `frame.storage`
   is a **non-owning ByteBuffer over `stagingBuf.GetMappedRange`**, not an ordinary
   reallocating CPU vector. On D3D12 mapped upload memory may have poor CPU read
   characteristics. Removing the separate CPU snapshot would move exact memcmp
   reads into that memory. The old handoff's realloc rationale does not describe
   this active implementation. Measure CPU read behavior before proposing that
   change; keep the existing CPU-owned immutable snapshots for now.
4. **Index decoding.** The raw-array requirements scan decodes all attribute
   descriptors for every vertex, even when most attributes are absent. A cached
   per-VAT scan plan is a possible deeper CPU optimization. The previous
   no-guest-index shortcut regressed correctness and must not be resurrected.

If normal heaps do not resolve the heavy-frame deficit, obtain a host CPU sample
profile plus actual packet draw/merge counts and GPU times. Existing slow-call
maxima and retrace counts cannot distinguish CPU submission cost, unmerged draw
amplification, and fragment-side atomic contention. Preserve low-frequency
aggregate measurement; do not reinstall per-draw host clocks.

## Next player order

Run ordinary `M14-UPDATE-RUN.cmd`, confirm its heap tests pass and the startup
marker is in launch.log, then revisit the same heavy area. Check motion
smoothness, spinner/door behavior, resize, and close. The next comparison must
be described as a combined update from 5b60 (rollback + resize changes + heap
policy), not a controlled heap-only A/B unless the same current revision is
explicitly compared with and without `--debug-heap`.

High refresh remains decoupled/interpolated presentation, not accelerating Wii
simulation. No claim of 120/240 FPS has been established by this audit.

## Bounded test plan for Sol and Mooshi

1. Run the usual update command. Keep its console output if the new Windows
   heap test fails; do not start changing renderer code to address a launch-test
   failure. Save the resulting launch.log and the tested commit ID.
2. Use the same save, heavy area, camera, resolution and refresh settings. Allow
   shader/cache warm-up, then record 30 seconds of frame times and repeat once.
   Report median/p95/p99 frame time and long hitches separately from VI retraces.
   Check spinner, door, flip, Tippi bounds, resize and close for regressions.
3. For a controlled heap A/B, keep that exact executable and revision. Locally
   add `--debug-heap` immediately before `--` in the existing Python debugger
   invocation in LAUNCH-M14-CANDIDATE.cmd, and launch that script directly so
   update-run does not replace the edit. Repeat the same warmed scene. Restore
   the launcher afterward; do not commit the diagnostic change. Compare logs
   for `heap_policy=debug` versus `heap_policy=normal` to verify the two modes.
4. If the difference is small, stop allocator speculation. Capture a CPU sample
   profile and GPU frame timing for the heavy scene. CPU time in allocation or
   descriptor scanning points toward packet/index work; CPU draw submission
   plus inflated draw counts points toward the early-Z predicate; GPU fragment
   time points toward the bbox atomics. Change only the measured dominant path.

For bbox work, first compare exact readback results on identical inputs. For
early-Z work, test alpha predicate truth tables across all 256 alpha values
before dropping a pass, then validate overlapping depth/blend cases in gameplay.
These are follow-up experiments, not part of the current fix.
