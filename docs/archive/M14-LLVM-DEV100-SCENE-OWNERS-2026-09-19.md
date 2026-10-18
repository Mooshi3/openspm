# M14 dev100 — repair dev99 and measure NPC-sensitive scene owners

Date: 2026-09-19
Base: `2ed70dd59b58fbe3a6719df3e2ceec44eca4fca7`

## Evidence and status

User reports lag increases with NPC count. Latest available player evidence is
still `launch(20260919-031628).log`, revision `d4239388f54f` (dev98).
Do not label dev99 or dev100 player-proven. The heavy baseline remains roughly
7–9 ms in display plus 7–8 ms in main, with frame completion dropping toward
30 Hz once the 16.667 ms deadline is missed.

The branch at session start has a definite dev99 build blocker:
`apply_openspm_llvm_wpar_codegen_patch.py:143` uses a bare C `/* ... */` comment
in Python. `python3 -m py_compile` reproduces SyntaxError. It is now a Python
comment. The existing synthetic pipeline suite now actually runs successfully.

## Changes

1. Repair that syntax error so dev99 callback nesting can reach compilation.
2. Keep dev99's narrow display callback optimization, with its original
   dispatch order, exception checks, recursion guard and abnormal-return escape.
3. Check signed `CPUState.downcount` before nested dispatch and after a normal
   callback return. A new native wrapper starts fresh local guard counters;
   the shared outer slice budget must still stop the parent walker. An exhausted
   callback returns its actual PC/state to the existing outer path, without
   repeating the callback. `budget_yields` reports these cases separately.
4. Add dev100 whole-frame interval sampling, one display/main frame in 64.
   Mark only direct unconditional BL sites in US2 `spmarioMain`
   `[0x801A7AAC,0x801A7D1C)` and taken linked computed calls in `dispDraw`
   `[0x8005B028,0x8005B2F4)`. Values come from compiler IR/SSA, not inferred
   callback tables. Other code and conditional main calls are not instrumented.
5. Preserve the phase profiler and all dev77/dev94/dev97 correctness fixes.

The profile is deliberately independent of dev99's success: its callback marker
runs before dispatch even when a continuation is outside the LLVM chunk or the
callback escapes abnormally. Time remains assigned across cold escapes and
outer re-entry until the next callback/main-call marker or frame-phase boundary.

These are **interval wall times**, not exclusive callee CPU times: each interval
includes the subsequent caller traversal/gap until the next marker. Native
safepoint/event servicing inside the interval is included. The phase preamble
is reported as site/target zero. Do not infer individual function hotspots from
one sample or read intervals as a literal NPC count.

Unselected marker sites do a global flag load/branch; there is no C helper call,
clock read, allocation, CPUState spill, or log at those sites. Selected intervals
use an exact 128-slot fixed table; overflow is counted separately instead of
misattributed. Reports drain between samples about every two seconds, from the
outer run wrapper, and show the top five owners of each phase. No guest state,
culling, NPC updates or animation rates are altered by the profiling.

## Build identity

- Codegen: `llvm-inline-wpar-indirect-budget-v11-dev100-scene-profile`
- Runtime: `m14-dev100-llvm-scene-profile`
- Startup: `M14 LLVM dev100 scene interval sampler active:`
- Reports: `M14 LLVM dev100 scene intervals` / `M14 LLVM dev100 scene owner`
- dev99 teardown counters remain, now with `budget_yields`.

The changed private compiler identity triggers normal regeneration. No cache wipe.

## Validation completed here

- Existing `test_llvm_native_pipeline.py`: PASS, including all retained tests.
- `test_scene_profile.py --cc gcc`: PASS at C11/O2 with warnings as errors.
  This compiles the actual scene accounting header plus the exact nested-dispatch,
  profile bridge and phase helper extracted from the active source. Tests cover
  sample cadence, no extra clock reads on unsampled frames, conservation of
  interval time across cold escapes, overflow, backwards-clock clamping, report
  drain, normal return, budget exhaustion before/after callback, exception,
  semantic fallback, abnormal PC, lookup miss, and recursion refusal.
- Complete transform applied to actual pinned DolRecomp
  `40637c4683bd2820ac5b23607ee344720beb26df`; repeat/check-only is idempotent.

Not completed: LLVM C++ build, MSVC/D3D12 build, actual game run or FPS comparison.
This environment lacks LLVM development tooling and player assets. A system
package refresh also failed on container UID/group permissions; no privilege
escalation or dependency/security-policy changes were attempted.

## Next player run

Run `M14-LLVM-UPDATE-RUN.cmd`. Spend about 15–20 seconds in a light room, then
15–20 seconds in the problematic NPC-heavy room. If practical, stay in the same
room while defeating enemies so geometry stays comparable. Trigger Game Over
once, then close normally so dev99 totals are written. Send `launch.log`.

Compare completed frames/host_ms, disp/main averages and dev99 normal-return
ratio. Read dev100 windows adjacent to those cadence lines, normalize total_us
by sampled_frames, and resolve the retained target PCs against player/public
symbols. If target zero dominates, investigate coverage/caller work instead of
calling it NPC draw time. If overflow is nonzero, account for it explicitly.

If dev99 normal returns are rare, do not broaden nested dispatch blindly. If
returns are common but FPS does not change, use the measured owners to choose
animation, collision, event execution, or a specific display callback. Do not
restart the already-retired synthetic-partition, global VI, or host-router
optimization loops without new evidence.

Public architectural reference: https://github.com/SeekyCt/spm-decomp
Pinned compiler: https://github.com/ExpansionPak/DolRecomp/tree/40637c4683bd2820ac5b23607ee344720beb26df
