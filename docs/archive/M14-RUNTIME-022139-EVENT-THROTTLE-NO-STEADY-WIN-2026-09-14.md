# M14 runtime 022139: event throttle did not fix steady choppiness

Runtime: `launch(20260914-022139).log`
Repo at runtime: `02c8f2a25f71`
Renderer: `openspm.aurora-gx 14.0-dev7`
Backend: `openspm.native 14.0-dev26`

## Result

The 16 ms host-event-pump throttle worked mechanically but did not materially
change the user's perceived choppiness or the aggregate retrace rate.

Shutdown stats reported:

- real host event pumps: 7,366
- skipped redundant pumps: 61,017
- cadence: 16 ms

Across 142 one-second authoritative timing samples, retraces averaged about
53.33/s (median 54, range 42-59), effectively unchanged from the prior run.
Therefore the old per-FIFO SDL pumping was wasteful and can remain throttled,
but it was not the primary steady-state slowdown.

## Remaining evidence

A real gameplay event-pump hitch remains possible: one `update-events` call took
139.256 ms and the enclosing begin-frame/FIFO submission matched it. A later
85.996 ms event-pump wait coincided with host exit and is shutdown fallout.

DrawDone drains also show pressure in the heavy gameplay section: by draw-done
call ~4,930 there were 95 waits >=16 ms, with a 41.834 ms maximum. One bbox
submit/readback took ~17.988 ms (14.052 ms submit + 3.936 ms map). These spikes
are real but are still too sparse to explain losing roughly 6-7 retraces every
second by themselves.

## New frontier

The prior Aurora frame-acquire diagnostic ignored waits below 16 ms. That can
hide the dominant steady cost: thousands of repeated 1-3 ms frame-slot,
staging-slot, or staging-map waits would never be logged as slow but could
consume many seconds over a run.

Commit `480eddd99ccec6fbb28c589902a1e188a9563ed4` upgrades
`apply_frame_acquire_wait_diag_patch.py` so cached v1 Aurora checkouts are
migrated in place to cumulative v2 stats. It records every wait and sparsely
reports calls, total_us, avg_us, max_us, slow count, and last_us for:

- `frame-slot`
- `staging-slot`
- `staging-map`

This is diagnostic-only and does not change frame/staging ownership, counts,
GX parsing, presentation, or Wii timing.

Next local run: ordinary `M14-UPDATE-RUN.cmd`, no clean build and no `.openspm`
reset. Inspect the largest cumulative `total_us` / `avg_us` category before
changing buffering or synchronization semantics.
