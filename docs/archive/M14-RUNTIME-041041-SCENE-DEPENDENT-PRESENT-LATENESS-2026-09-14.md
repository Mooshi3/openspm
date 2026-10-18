# M14 runtime 041041: scene-dependent host-present lateness

Tested revision: `fe8842f4b768ba2f36a8339b2e378ab78ad5c7cf`
Player report: presentation is smooth before the level begins, a little choppy in the first gameplay area, much choppier in the large area, and somewhat choppy again in the final area.

## What the first real swapchain-present probe proved

The present-pacing probe measures successful `g_surface.Present()` calls on Aurora's render worker. This is the desktop-visible presentation boundary; it is not Wii VI/retrace delivery and not the main-thread `aurora_end_frame()` enqueue point.

Cumulative checkpoints from `launch(20260914-041041).log`:

| Presents | avg interval | p50 | p95 | p99 | >18 ms | >25 ms | >33 ms | Present call avg/max |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 256 | 18.383 ms | 17.0 | 19.0 | 19.5 | 37 | 1 | 1 | 0.182 / 1.196 ms |
| 512 | 17.519 ms | 17.0 | 19.0 | 19.5 | 82 | 1 | 1 | 0.182 / 1.196 ms |
| 1024 | 17.315 ms | 17.0 | 19.0 | 22.5 | 180 | 9 | 5 | 0.187 / 1.196 ms |
| 2048 | 17.726 ms | 17.0 | 22.0 | 33.0 | 449 | 99 | 16 | 0.202 / 1.196 ms |
| 4096 | 18.107 ms | 17.0 | 30.5 | 33.5 | 943 | 323 | 45 | 0.207 / 1.663 ms |
| 8192 | 20.702 ms | 17.5 | 34.5 | 36.5 | 2835 | 1908 | 858 | 0.211 / 1.663 ms |
| shutdown 8362 | 20.989 ms | 17.5 | 34.5 | 36.5 | 3005 | 2078 | 959 | 0.211 / 1.663 ms |

Incremental tail rates make the progression clearer:

- presents 0–256: >25 ms = 0.4%
- 256–512: >25 ms = 0.0%
- 512–1024: >25 ms = 1.6%
- 1024–2048: >25 ms = 8.8%
- 2048–4096: >25 ms = 10.9%
- 4096–8192: >25 ms = 38.7%; >33 ms = 19.8%

This matches the player's scene-dependent report. Do not characterize the bug as globally choppy or as a constant desktop/compositor pacing issue.

## Important exclusions

Wii authoritative timing continues to report many 60-retrace seconds during the degraded host presentation. VI/retrace counts therefore do not measure visible frame delivery for this bug.

`Surface::Present()` itself remains cheap (~0.2 ms average, 1.663 ms maximum). Therefore D3D12 FIFO-vsync blocking in the swapchain Present call is not the source of the 25–36 ms frame gaps.

Frame-slot, staging-slot and staging-map waits remain effectively zero in the measured run. Bbox submit/readback samples are generally sub-2 ms and `slow=0`. The late SDL event-pump stalls remain real but sparse; they do not explain the progressive heavy-scene present distribution.

The debug-heap policy fix is active (`_NO_DEBUG_HEAP=1`) and should remain, but this run further falsifies it as the main choppiness cause.

## New diagnostic at branch head

Commits:

- `fdc231e204ad36d58ef8216979de8af6b389e99b` — add `apply_render_worker_pacing_diag_patch.py`
- `a13c6b54bf51e510cbf6f187e0e443013725cedd` — wire it into the ordinary cached-Aurora configure migration

The new probe instruments `render_worker::enqueue_end_frame()` once per host frame only. It records:

1. **producer interval** — time between successive completed-frame enqueue calls from the producer/main thread;
2. **queue wait** — time from enqueue until Aurora's render worker starts that end-frame item, which captures worker backlog from begin/pass work ahead of it;
3. **end-frame worker time** — duration of the end-frame work callback itself;
4. **queue depth max** at end-frame enqueue.

It emits sparse power-of-two summaries and shutdown only. No per-draw clocks and no GX/parser/shader/presentation semantic changes.

Expected line:

`M14 render-worker pacing reason=periodic frames=... producer_avg_us=... producer_p95_us=... producer_p99_us=... producer_max_us=... producer_over25=... queue_avg_us=... queue_p95_us=... queue_p99_us=... queue_max_us=... queue_over25=... work_avg_us=... work_p95_us=... work_p99_us=... work_max_us=... work_over25=... queue_depth_max=...`

## How to interpret the next run

Run normal `M14-UPDATE-RUN.cmd`; no cache/save reset and no special flags.

- If **producer p95/p99** rises with the heavy area while queue/work remain low, the bottleneck is on Broadway/main-thread frame construction before Aurora queues the frame. Next profile GX parsing/index decoding/snapshot traffic and translated game/dispatcher work at frame aggregate granularity.
- If **queue wait** rises with the heavy area, Aurora's render worker cannot consume queued begin/pass work fast enough. Next measure draw/pass counts, merge ratio and per-pass worker CPU/GPU timing; Astra's early-Z draw amplification and fragment-side bbox atomics become relevant only if the counts/timing point there.
- If **end-frame worker time** rises, split the end-frame callback around surface acquisition, render-pass encoding, queue submit, GPU-prof handling and callbacks. `Present()` itself is already exonerated.
- If producer, queue and work are all individually low while present intervals are still late, inspect the gap between worker item completion and successful present plus Dawn/GPU callback scheduling; this would be unexpected given Aurora's current end-frame callback structure.

Do not change the 60.75 MHz timebase, disable vsync, resurrect async FIFO, restore the rejected no-guest-index shortcut, or optimize the early-Z/bbox hypotheses without measurement.