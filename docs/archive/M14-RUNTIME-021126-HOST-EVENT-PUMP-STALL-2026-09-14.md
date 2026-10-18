# M14 runtime 021126: host event-pump stall and bounded throttle

Run: `launch(20260914-021126).log`
Tested repo: `da0be95292f0`
Renderer: `openspm.aurora-gx 14.0-dev7`
Backend: `openspm.native 14.0-dev26`

## What the phase diagnostic proved

The prior frame-acquisition probe had already ruled out >=16 ms waits in Aurora's two render frame slots, staging-slot acquisition, and staging-buffer mapping.

The finer 021126 phase probe isolated the remaining large gameplay hitch:

- `update-events`: 154,634 us
- `update-total`: 154,661 us
- outer OpenSPM `begin_frame`: 154,704 us
- enclosing FIFO submit: 154,713 us

Those timings are effectively identical. The stall is therefore inside Aurora's host window/event pump (`window::poll_events()` / SDL message processing), not GX parsing, frame-slot ownership, staging mapping, or Aurora recording-frame acquisition.

The same one-second timing sample fell to 50 retraces, while ordinary nearby samples reached about 58. A later ~71 ms `update-events` wait occurred immediately with the host exit event and remains shutdown fallout, not a gameplay GX diagnosis.

## Architectural cause

OpenSPM's original `ensure_frame()` deliberately called `pump_events()` before testing `state.frame_open`. This was useful during early bring-up because the first native GX frame could remain open for a long Nintendo SDK/game initialization sequence; pumping on every FIFO submission kept Windows from declaring the process hung before presentation existed.

That safeguard is now far too aggressive for live gameplay. By the 154 ms hitch, the outer renderer profiler had reached 81,197 `begin_frame` calls at renderer packet 12,076. Since `ensure_frame()` pumps events on every underlying FIFO submission even when the Aurora frame is already open, SDL/Win32 event processing is invoked vastly more often than the Wii/presentation cadence requires.

This also provides a plausible steady-state explanation for the persistent ~53-55 retraces/sec despite no single dominant GX hotpath: many individually sub-16-ms host event-pump calls were invisible to the slow-call logger but still accumulated CPU/message-pump overhead.

## Candidate applied after this run

Commit: `e7150120e2f5a7435d81d955603adf51ca975044`

The historical dev7 wrapper now intercepts only OpenSPM's included calls to `aurora_update()` and rate-limits real host event pumps to at most one per 16 ms while GX work is flowing. Calls inside the interval return an empty Aurora event batch.

Preserved behavior:

- first/startup event pump is immediate;
- host events continue at roughly frame-rate cadence;
- pause/begin-frame retry still pumps events on that cadence;
- keyboard/mouse/controller input remains serviced at frame-rate cadence;
- resize, focus, and exit events remain serviced;
- no GX bytes, parser state, frame counts, display-copy semantics, guest timing, vsync setting, or renderer ABI contract changed;
- renderer version remains `14.0-dev7` because this is host-service scheduling, not a GX/ABI contract revision.

A shutdown marker reports `M14 Aurora host event pump stats real=... skipped=... cadence_ms=16.` so the next run can quantify how many redundant polls were removed.

## Next-run acceptance / rollback criteria

On the next ordinary `M14-UPDATE-RUN.cmd` run:

1. Confirm the throttle marker appears.
2. Compare authoritative retraces in the same heavy gameplay area. A useful win should move the steady-state distribution upward, not merely remove one rare hitch.
3. Check that input, Tippi pointer, focus/resize, and close-window behavior remain normal.
4. Check the shutdown event-pump stats to quantify real vs skipped pumps.
5. If responsiveness regresses or gameplay retraces do not improve, revert `e7150120...`; do not compensate by changing Wii timebase, FIFO semantics, or frame-slot counts.
6. If large `update-events` waits remain despite the bounded cadence, instrument inside `window::poll_events()` to distinguish `SDL_PollEvent` itself from per-event `process_event` work (especially resize/surface refresh), rather than returning to GX hotpath tuning.
