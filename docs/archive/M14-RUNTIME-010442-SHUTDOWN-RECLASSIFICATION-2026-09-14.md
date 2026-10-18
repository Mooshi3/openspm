# M14 runtime 010442: shutdown reclassification + dev7 cleanup

Player log: `launch(20260914-010442).log`

Player-tested repo: `1c7e2aeeee17cd95192e03397a55c44402bb8217`

This log materially changes the interpretation of the recurring late
`0x81349E80` nested-display-list fatal documented after 003347/005106.

## Key finding: the recurring late fatal is shutdown fallout in 010442

The renderer records the host window exit/quit event **before** the sequence of
`OPENSPM_ERROR_RUNTIME (-6)` FIFO-submit failures and before the final
`GX_CMD_CALL_DL references unmapped guest memory: address=0x81349E80, size=224`
fatal. The renderer's own message explicitly classifies later service `-6`
results as shutdown fallout, not a GX diagnosis.

The repeated failures then carry the same 32-byte leading bytes across nearby
translated PCs, converge on native migration detail `0x00000004`, and finally
reach the same stale/unmapped CP-array bases and nested-DL address previously
seen at the ends of 003347 and 005106.

Therefore the old evidence does **not** establish `0x81349E80` as an ordinary
in-game correctness frontier. Treat it as a shutdown-order/parser-tail bug unless
a future run reproduces the same fatal **before** any host-exit/quit marker.
Do not add an address-specific guest mapping.

## Root cause in the host renderer

Aurora dev6 batches FIFO bytes into a 16 KiB host buffer. On an underlying sink
error, `flush_dev6_fifo()` returned the error without clearing that buffer, so
later guest stores could retry the same rejected submission. Separately, the
base renderer shutdown path calls `close_frame()`, which drains the raw GX parser.
If the player closes the window while Broadway is part-way through a translated
write-gather sequence, that drain can parse an intentionally interrupted command
tail as if it were a complete game stream.

This explains both major symptoms in 010442:

1. repeated identical `-6` submissions after host exit;
2. a scary parser fatal only during teardown.

## Dev7 shutdown/failure guard

The active build entry remains `renderer/aurora/openspm_renderer_aurora_v6.cpp`
so existing CMake/player caches rebuild normally, but it now reports renderer
`14.0-dev7`.

Dev7 changes only failure/teardown behavior:

- latch the first non-OK underlying GX sink result;
- discard the failed dev6 host batch instead of replaying it;
- return the latched error from later sink calls without re-entering Aurora;
- on host exit or a latched renderer error, discard the pending host batch;
- mark the interrupted host frame closed before the base shutdown path so
  teardown cannot `raw::drain()` a partial Broadway command tail;
- still run Aurora's actual resource/window/GPU shutdown.

Successful gameplay submissions, DrawDone semantics, presentation safepoints,
bbox behavior, synchronous `ProcessingMode::Drain`, v19 display-list semantics,
and guest-memory coherency are unchanged.

Expected shutdown marker:

```text
M14 Aurora shutdown abandoning interrupted GX tail bytes=... exit=1 fifo_error=...; no parser drain.
```

A clean player close should no longer end in the fake nested-DL fatal.

## Normal-run diagnostics cleanup

010442 still contains the high-frequency 2,048-draw CPU profiler. At roughly
59.6 million draws it is continuing to perform a host `steady_clock` measurement
on every draw and to emit aggregate lines throughout gameplay. That is no longer
an acceptable baseline after the guest-array staging optimizations reduced the
hot path substantially.

`renderer/aurora/apply_normal_run_diagnostic_quiet_patch.py` now restores
`handle_draw` to the unprofiled decode + `draw_prim` shape. The existing CMake
FIFO-reentrancy configure hook invokes this migration, so an ordinary
`M14-UPDATE-RUN.cmd` updates the cached Aurora checkout; no `.openspm` wipe is
required. The old profiler script remains available for deliberate/manual
investigations.

The legacy automatic stage-7/SEQ_LOAD audit is also retired at the renderer
boundary. Its dedicated sync reason is diagnostic-only by contract, so dev7
acknowledges that reason without flushing, draining, presenting, or arming the
multi-thousand-line deep audit. Manual renderer capture remains separate.

Expected normal-run marker:

```text
M14 automatic SEQ_LOAD deep GX audit retired for normal runs; diagnostic sync acknowledged without drain/present.
```

The backend may still print its two historical "audit armed" request messages;
those requests are now harmless acknowledgements and should not generate the
large Aurora deep-audit payload.

## What the next player run should establish

1. Renderer reports `openspm.aurora-gx 14.0-dev7`.
2. No `M14 GX draw CPU profile` spam appears.
3. No large automatic `M14 GX deep ...` SEQ_LOAD dump appears.
4. Gameplay/graphics retain the protected v19 correctness baseline.
5. Enemy-heavy areas can now be judged without the old per-draw timing/logging
   instrumentation contaminating the result.
6. Closing the host window exits without the `0x81349E80` parser fatal.

If a genuine renderer `-6` happens **before** host exit, dev7 should log the
terminal FIFO latch once and avoid repeated submission replay. That would be a
new actionable runtime frontier rather than shutdown noise.

## Next optimization after a clean baseline

Do not stack another semantic optimization before measuring dev7. If residual
indexed-array staging still dominates after diagnostics are quiet, the next
exact candidate remains reusing the bytes already stored in Aurora
`FramePacket::storage` as the immutable exact snapshot. Preserve exact `memcmp`,
pointer-index candidate lookup, stride checks, compact interval addressing and
same-address mutation detection; resolve the storage data pointer fresh from the
saved range offset after possible `realloc`.

No `.openspm` or save wipe is required.
