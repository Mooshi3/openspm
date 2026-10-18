# M13 Phase 4 Windows parity gate

Phase 4 is **not accepted** merely because CI builds the in-process backend. The first acceptance gate is user-side parity with the proven M12 runtime.

## What changed

The candidate `runtime/moderngekko_backend.dll` links the pinned ModernGekko Runtime directly into the OpenSPM process. It no longer launches `moderngekko-run.exe` as the normal backend path.

Two migration fallbacks remain packaged:

- `runtime/moderngekko_legacy_backend.dll` — OpenSPM ABI adapter that starts the proven M12 child process;
- `runtime/legacy/moderngekko-run.exe` — byte-identical direct M12 runner.

The generated R8PE01 game module is also copied from the exact proven M12 artifact and hash-verified in CI.

## Gate A — normal boot parity

1. Extract the Phase 4 artifact to a fresh directory.
2. Start `OpenSPM.exe` / `RUN-OPENSPM.cmd`.
3. Select the same extracted R8PE01 game root used for M12/M13 testing.
4. Use the **normal** startup target first.
5. Confirm the console identifies backend `moderngekko-inprocess` version `13.2-phase4`.
6. Confirm the game reaches the same playable state as M12: video, audio, input, timing, and normal save/user-directory behavior.
7. Play far enough to verify ordinary scene transitions rather than treating title-screen output as success.

Do not use debug boot redirection as a Phase 4 requirement yet; startup-target plumbing exists, but real in-process game services are the next slice after parity.

## Gate B — lifecycle/relaunch

1. Exit the game normally.
2. Confirm OpenSPM returns/exits cleanly rather than hanging with an active runtime.
3. Launch OpenSPM again from the same extracted candidate.
4. Confirm a second in-process runtime can initialize and play normally.

This is important because ModernGekko intentionally permits only one active Runtime per process; its destructor must fully release the singleton/global runtime state before another run.

## Gate C — M12 behavior checks

These are regression observations, not fixes expected from Phase 4:

- Mario's house remains approximately 60 FPS.
- Ultrawide expands the visible world rather than stretching the 16:9 image.
- Existing ultrawide exposure bugs may remain; Phase 4 must not hide them by changing simulation behavior.
- The Bowser minion-crowd scene may still fall to roughly 24-31 FPS. That slowdown is the canonical post-parity profiling target.

If Phase 4 unexpectedly changes any of these before deliberate performance/presentation work begins, treat it as a parity regression.

## Gate D — fallback isolation

If the in-process backend fails, verify that the failure is isolated:

- `RUN-LEGACY-BACKEND.cmd` should use the process-backed M13 adapter;
- `RUN-DIRECT-M12-FALLBACK.cmd` should launch the exact proven M12 runtime directly.

A Phase 4 failure must never require repairing the R8PE01 dump or discarding the accepted M12/M13 baselines.

## After green parity

Only after Gates A-B are green should the project start depending on live in-process services. The immediate next work is:

1. expose honest lifecycle/game-service boundaries;
2. make the debug startup target capable of real direct boot/warp;
3. instrument the Bowser minion scene and classify the 24-31 FPS bottleneck;
4. begin additive item/save feasibility in parallel where it does not interfere with performance diagnosis;
5. build toward the Goomba Capsule vertical slice.

The project north star remains: **stable 60 FPS everywhere and accepts our custom code.**