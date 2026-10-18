# M14 LLVM dev48 — PC-native GX display-list construction

Date: 2026-09-16
Branch: `m14-native-execution`
Player log: `launch(20260916-045805).log`
Parent tested revision: `5122ad39482f0542ce180c6b86421521cf9ee17e`
Active candidate: `m14-dev48-llvm-pc-display-list`

## What dev47 proved

The player reached through the Wii Remote orientation screen and into the opening
"Ahem! Today..." cutscene.  This is the first run past the old first-frame
post-DrawDone deadlock.

The dev47 PE-finish repair is therefore real, not merely diagnostic:

- runtime derived `GXFinishInterruptHandler = 0x8028DB24` from the player's own
  `r13-0x6F70` FinishQueue wake call;
- the real guest handler was delivered with `OS_INTR_PI_PE_FINISH = 19`;
- hundreds of completions drained the FinishQueue and woke the main thread;
- the run reached at least completion #823 before the new frontier.

Do not remove dev47 or return to the old VI/scheduler theories for this failure.

## New deterministic stall

After the orientation screen the game reaches `seq=3 stage=6` and then remains
alive but non-progressing at:

```text
pc=0x80291E20
lr=0x80291E20
exception=0
program_exception=0
native region=[0x80291DC8,0x80291E78)
```

The region-query counter continues increasing into the hundreds of millions, so
this is an active translated busy/stall path rather than a sleeping thread or a
host crash.

The existing player-local seam generator already proves:

```text
GXBeginDisplayList = 0x80291DC8
GXEndDisplayList   = 0x80291E78
```

Therefore the whole stuck LLVM region is retail `GXBeginDisplayList`, and the
observed PC is +0x58 inside that SDK function.

Public/decompiled Revolution GX behavior explains why that is invalid for a PC
native runtime.  `GXBeginDisplayList` updates GX shadow state and then redirects
the physical Wii CPU FIFO to the caller's list buffer, calls `GXSetCPUFifo`, and
resets the Broadway write-gather pipe.  WiiCompiled/Aurora-style ports instead
redirect their host GX FIFO recorder and do not execute those console transport
operations.

## Existing OpenSPM implementation was already correct

OpenSPM did not need a new display-list recorder.  The backend already hosts the
retail public SDK addresses:

- `0x80291DC8`: `openspm_gx_platform_begin_display_list()`
- `0x80291E78`: `openspm_gx_platform_end_display_list()`

The platform implementation validates the entire guest buffer, redirects
subsequent GX FIFO/WPAR bytes into it, tracks overflow, 32-byte aligns and pads
the completed list, and returns the recorded size through the normal SDK result.

The architectural mistake was LLVM bridge v15: at a materialized Begin/End seam
it intentionally ran the old generated-C pre-hook and then granted the *retail
Wii function body* a one-shot permit.  Only after that body returned did it start
or stop the native recorder.  dev48 supersedes that policy at the top of the LLVM
hostcall chain.

## dev48 implementation

`tools/native_bridge/legacy_c_adapter_v38.c.in` wraps dev47 and handles only the
persistent CPU's exact `GXBeginDisplayList` / `GXEndDisplayList` entries.

At either entry it:

1. flushes a partial proven Broadway WPAR gather line so byte ordering cannot
   cross the destination change;
2. converts the live CPUState to the existing M14 hostcall state;
3. invokes the already-existing backend handler at the same retail SDK address;
4. copies the architectural result back (`GXEndDisplayList` therefore receives
   its recorded byte count in r3);
5. arms v15's existing one-shot permit for the original LR, because the old
   generated-C durable-return hook is obsolete when the semantic transition has
   already happened synchronously;
6. never enters Nintendo's physical Wii CPU-FIFO/write-gather body.

Scratch-event ownership is fail-closed: dev48 does not use `bridge_from_cpu()` to
identify the caller.  It requires the exact persistent `&instance->cpu`, retaining
dev42's lesson that stack-local event CPUs share memory but are not embedded in
the bridge instance.

Expected activation marker:

```text
M14 LLVM PC-native GX display-list semantic completion active: ...
```

Expected first cutscene proof:

```text
M14 LLVM PC-native GX display-list begin #1 ... retail Wii CPU-FIFO body skipped.
M14 LLVM PC-native GX display-list end #1 ... retail Wii CPU-FIFO body skipped.
```

The critical success criterion is that PC must not remain at `0x80291E20` and
`seq=3 stage=6` must progress.

## Performance evidence from the dev47 run

The player's report that LLVM is still extremely laggy is supported by the
instrumentation.

During ordinary rendered frames:

- retraces remain approximately 60/s;
- `spmarioMain` updates are only about 19–21/s;
- Aurora DrawDone draw work is roughly tens of microseconds per frame in the
  sampled windows;
- producer-fence/safepoint work is also sub-millisecond in the steady state.

This does **not** look like a GPU/render-thread throughput limit.  The CPU/native
bridge remains the primary performance frontier.

The strongest red flag is native-region interception volume.  The frontier
counter rises by roughly 1.2–2.5 million region queries per second in normal
execution and continues above 150 million in the final stall.  Every LLVM region
currently crosses a deep historical correctness/diagnostic hostcall chain.

Do not prematurely delete correctness guards while dev48 is still establishing
playability.  Once the cutscene advances, the highest-value performance pass is:

1. capture matched steady gameplay `spmarioMain` updates/s and native-region
   queries/s on dev48;
2. profile/prune query wrappers which are now diagnostic-only (dev39–dev46 in
   particular) and collapse proven static seam tests into a low-cost query path;
3. measure again before PGO;
4. only then do the planned LLVM PGO build and generated-C vs LLVM same-scene A/B.

The renderer should not be the first optimization target based on this log.

## Next-run classification

Run `M14-LLVM-UPDATE-RUN.cmd`.

- **Build failure:** patch dev48 only; do not change the display-list diagnosis.
- **Begin/end markers appear and cutscene advances:** preserve the host semantic
  path.  Treat any later stall as a new hardware/SDK frontier.
- **Begin marker appears but End never appears:** inspect GX calls emitted while
  recording and caller control flow; verify the returned LR permit was consumed.
- **Hostcall fails:** inspect the existing bounded recorder's guest-buffer
  validation; do not re-enable the retail Wii body.
- **Graphics state becomes wrong after lists complete:** the likely missing piece
  is guest GX shadow `dlSaveContext`/`inDispList` save/restore parity.  Add that
  narrowly around the native recorder while keeping physical FIFO operations
  skipped.
- **Performance remains ~20 updates/s after correctness progresses:** proceed to
  the native-region query-chain cost pass; renderer timing already argues against
  another GPU optimization detour.
