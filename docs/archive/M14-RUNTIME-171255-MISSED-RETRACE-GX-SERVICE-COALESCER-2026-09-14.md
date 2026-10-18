# M14 runtime 171255 / 174136 — missed-retrace quantization and rejected GX service coalescer

Date: 2026-09-14

Baseline log: `launch(20260914-171255).log`
Baseline revision: `2ff99121fa75dfdb66d7c91a98607a029e73dab4`

Coalescer A/B log: `launch(20260914-174136).log`
Coalescer revision: `aef07afde6bb679c997309c6ad19393518e52ff8`

## Decisive mechanism remains unchanged

The real R8PE01 top-level frame loop is measured at:

1. `DEMOBeforeRender` `0x801A8A9C`
2. `spmarioDisp` `0x801A7D1C`
3. `spmarioMain` `0x801A7AAC`
4. `DEMODoneRender` `0x801A8B64`

Light scenes fit under one 16.667 ms VI interval and stay near 60 produced updates/s. Heavy Chapter 1 scenes spend roughly 18–26 ms in `spmarioDisp + spmarioMain` before the authentic DEMO swap path reaches `VIWaitForRetrace()`. The immediately-next retrace has already been missed, so the wait catches the following one and the real game loop quantizes near 33.3 ms / 30 Hz.

Do not delete the Wii wait or accelerate VI. The correct fix is reducing native pre-wait work.

## Baseline 171255 representative heavy windows

Representative phase windows:

- display 9.857 ms + main 9.095 ms + done 14.374 ms = ~33.33 ms; 30 updates / 60 retraces
- display 13.217 ms + main 9.751 ms + done 10.336 ms
- display 14.919 ms + main 10.932 ms + done 7.478 ms

DrawDone work is real but not the whole frame. A representative window had about 3.37 ms/frame of measured DrawDone substage work and ~3.69 ms average producer DrawDone sync.

## Why the 4 KiB coalescer was tested

`legacy_c_adapter_v8.c.in` already preserves Broadway's exact 32-byte WPAR gather locally, but every completed 32-byte line still called the backend GX FIFO service. Backend dev23 then ran the bytes through its command-aware framer before Aurora's later 16 KiB transport batching.

Because `wg_writes` correlated strongly with `spmarioDisp` cost, `legacy_c_adapter_v14.c.in` was added as an overlay which kept the proven 32-byte gather intact but coalesced completed lines into up to 4 KiB before the backend service. All existing semantic/order fences forced a hard flush.

## 174136 result: service-call granularity falsified

The coalescer worked mechanically:

- `input_submits=32,782,716`
- `backend_submits=290,895`
- `bytes=1,011,700,688`
- `max_batch=4096`
- `hard_flushes=588,065`

That is roughly a 112.7x reduction in module -> backend FIFO service submissions.

However the real gameplay bottleneck did not improve.

Matched heavy point:

Baseline 171255:

- display 9.857 ms
- main 9.095 ms
- pre-wait total ~18.952 ms
- 30 updates / 60 retraces

Coalescer 174136:

- display 9.818 ms
- main 9.292 ms
- pre-wait total ~19.110 ms
- still ~30 updates / 60 retraces

Deeper heavy windows were also not improved. One coalescer window reached display 14.551 ms + main 10.691 ms = ~25.242 ms before the wait, compared with a baseline heavy window around display 13.217 ms + main 9.751 ms = ~22.968 ms.

The player's subjective result was that the game felt possibly even more jittery.

Cumulative host presentation statistics did improve slightly (for example p95 ~35.0 ms -> ~34.5 ms and fewer >33 ms intervals), but authoritative game simulation remained near 30 Hz in the bad area. Therefore the improvement was not on the bottleneck that controls enemy/Mario motion.

## Conclusion

Reject the 4 KiB module -> backend FIFO service coalescer as a lag fix.

It proved that the number of 32-byte-line backend service calls is not the dominant cause of the heavy-room collapse. Keep the experiment source for provenance, but do not keep it active in the player runtime.

Active bridge returns to:

`tools/native_bridge/legacy_c_adapter_v13.c.in`

The next producer frontier is deeper than the service seam:

1. per-store generated WPAR/external-write cost inside the local native module;
2. generated display callback / GX SDK CPU-side work;
3. backend per-byte command-framer work itself, if it still consumes significant CPU independent of call granularity;
4. ordinary `spmarioMain` CPU work, which also rises several milliseconds in heavy rooms.

A particularly high-value next check is the generated WPAR path: millions of 1/2/4-byte guest GX stores per second still pass through the module's `external_write` machinery before the 32-byte gather. The rejected coalescer only optimized what happened *after* a complete 32-byte line existed.

## Keep

- true `spmarioMain` cadence counter;
- four-phase frame profiler;
- 250k native slice baseline;
- fixed-phase VI scheduler;
- completed-copy presentation at DrawDone;
- v19 continuation-safe display-list seam;
- exact 32-byte Broadway gather;
- pooled immutable guest-array snapshots and compact lookup;
- renderer dev7 terminal FIFO no-replay behavior.

## Rejected / cleared

- 2,000,000-cycle slice budget as a lag fix;
- presentation-only explanation for slow gameplay;
- deleting / bypassing `VIWaitForRetrace()`;
- D3D12 Present itself, frame/staging acquisition, bbox readback, and debug heap as primary chronic bottlenecks;
- 4 KiB module -> backend GX FIFO service coalescing as the primary performance fix.
