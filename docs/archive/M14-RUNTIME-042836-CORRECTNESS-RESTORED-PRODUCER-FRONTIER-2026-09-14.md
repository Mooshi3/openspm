# M14 runtime 042836: correctness restored; producer-side pacing frontier

Tested revision: `16ef58b460c733cc3354125b0f8d45489170a3a2`

Player report: rotating devices no longer make Mario invisible and the previously softlocking door works again. This confirms the render-worker pacing probe regression is fully rolled back and the cached Aurora inverse migration restored the known-good worker callback behavior.

## Clean-run performance evidence

The remaining choppiness is scene/load dependent, not global. The real swapchain-present probe is healthy early and degrades only as gameplay complexity rises:

- 256 presents: p95 19 ms, p99 20 ms, only 1 interval over 25 ms.
- 512 presents: p95 19 ms, p99 20 ms, only 1 interval over 25 ms.
- 1024 presents: p95 19 ms, p99 20 ms, only 1 interval over 25 ms.
- 2048 presents: p95 19 ms, p99 20 ms, 4 intervals over 25 ms.
- 4096 presents: p95 30 ms, p99 33.5 ms, 264 intervals over 25 ms.
- shutdown at 7957 presents: p95 34.5 ms, p99 36 ms, 1948 intervals over 25 ms and 946 over 33 ms.

`Surface::Present()` itself remains cheap (~0.2 ms average, <=1.43 ms max), so swapchain/vsync is not the source of the late frames.

The clean run also keeps the already-exonerated paths cheap:

- frame-slot acquisition: 302 us total through 4096 frames and 632 us total through 8192 acquisitions;
- staging-slot/map waits: effectively zero;
- PE bbox submit/map samples: generally sub-millisecond to low-millisecond, all `slow=0`;
- authoritative Wii timing: overwhelmingly 60/61 retraces per second throughout gameplay.

One late 194 ms SDL event-pump hitch is real, but it is sparse and cannot explain the progressive scene-dependent present distribution.

## Important new shape of the bug

There are no ordinary `draw_done` slow-threshold (>~16 ms) logs in this clean run even while desktop presentation p95 rises into the 30-35 ms range. Therefore the missing frame time is likely distributed among sub-threshold producer-side work rather than one recurring giant DrawDone/bbox/frame-slot/Present stall.

The cumulative `wg_writes` heartbeat counter also grows dramatically with gameplay load. Opening/title phases add only roughly 0.07-0.20 million writes per second, while `seq=2 stage=1` commonly adds roughly 2-4 million per second. This correlates strongly with scene complexity. Do not yet interpret that as DLL crossings: dev23 already gathers Broadway stores into 32-byte FIFO lines before the bulk GX service, so the exact counter semantics must be verified before optimization.

## Next diagnostic: safe sync-boundary timing

Do not touch Aurora render-worker callbacks again. The failed worker probe proved that even a diagnostic callback wrapper can perturb guest-backed renderer lifetime/order and recreate spinner invisibility + door softlock.

Instead, wrap only the existing dev7 `gx_sink.sync` entry at the OpenSPM renderer boundary. Time the full existing call (transport flush + existing underlying sync) for:

- `OPENSPM_GX_SYNC_DRAW_DONE`
- `OPENSPM_GX_SYNC_PRESENT_SAFEPOINT`

Use fixed 256-call windows with average/p50/p95/p99/max. This adds two host clock reads per semantic boundary and changes no parser, callback, guest-memory, GPU-resource, scheduling, or presentation semantics.

Interpretation:

- If DrawDone/present-boundary p95/p99 rise materially with scene load (even if each remains below 16 ms), synchronous GX producer work remains the main frontier.
- If both stay low while host-present p95 still rises to ~30-35 ms, move upstream into translated Broadway/write-gather/service overhead between renderer boundaries. The `wg_writes` scaling then becomes the highest-value target.

Keep the real Present pacing probe, fixed-phase 60 Hz retrace scheduler, `_NO_DEBUG_HEAP=1`, host-event frame-boundary deferral, snapshot pooling/single-copy staging, and all v19 graphics correctness work.
