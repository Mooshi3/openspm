# OpenSPM M11C / M11D performance checkpoint

## Known control

Regular Super Paper Mario in regular Dolphin on the same Windows machine holds ~59.95 FPS. OpenSPM M11B is scene-dependent and can fall to ~20 FPS, so this is an OpenSPM/RecompCore path problem rather than game or host hardware capacity.

## M11B full-run telemetry

User ran from boot through Bestovius's house in 1-1 using `RUN-OPENSPM-PERF.cmd`.

Shutdown counters:

- native dispatches: `10,855,784,605`
- forced fallback steps: `0`
- native exceptions: `3,462,233`
- instruction-hook fallbacks: `656,825,936`
- SMC-failed chunks: `0`
- chunk verifications: `14,865`
- reverify events: `115`
- native bursts: `87,151,815`
- charged guest cycles: `501,208,632,672`

This rules out the original hypothesis that slow scenes are primarily falling back to the interpreter. The generated module is executing natively; the problem is overhead within/around native execution.

Top sampled native dispatch PCs:

| PC | Samples | Identification / meaning |
|---|---:|---|
| `0x8025C9C8` | 488,446 | exact US2 `strcmp` entry |
| `0x8004DD98` | 451,188 | hot animation loop continuation immediately after a `strcmp` call |
| `0x8027551C` | 307,185 | Nintendo OS `SelectThread` no-runnable-thread idle spin |
| `0x8026DFC0` | 81,165 | SDK/cache area; lower priority |
| `0x8026DF94` | 76,720 | SDK/cache area; lower priority |

Sampling is one sample per 4096 native dispatches. `0x8025C9C8` therefore corresponds to roughly 2.0 billion sampled-equivalent entries; `0x8027551C` roughly 1.26 billion.

## M11C — current A/B baseline

Branch: `m11c-runtime-fastpath`

Build run: `34091670172` (pinned build head `99be8bfeb94497318ad3b4beb08d38ac990c9bc0`)

Changes relative to M11B:

1. Do not install/query dormant guest-mod interception when `HasGuestInterception()` is false. M11B recorded `host_calls=0` throughout the run.
2. Use an O(1) direct non-REL DOL lookup before entering generic REL address translation. Actual REL/unmapped addresses keep the existing resolver.
3. Fix OpenSPM aspect request handoff by queueing the requested mode atomically and applying it from the StaticRecomp CPU loop.
4. Add `[OPENSPM-ASPECT]` and `[OPENSPM-PROJ]` logs so host selection and actual game-camera projection are independently observable.
5. Keep M11B perf telemetry for direct comparison.

Do not fold more performance changes into this artifact; it is the control for M11D.

## M11D — staged scheduler-idle experiment

Branch: `m11d-scheduler-idle`

No build should be triggered until M11C is available as an artifact/control.

Change: set RecompCore's existing `MAIN_STATICRECOMP_IDLE_PC` to exact R8PE01 PC `0x8027551C`.

Why this PC is safe to test:

- at `0x80275518`, Nintendo OS calls `OSEnableInterrupts` (`0x80271E3C`)
- `0x8027551C` loads `RunQueueBits` from `-29552(r13)`
- `0x80275520` compares it with zero
- `0x80275524` branches back to `0x8027551C` while zero
- after a wake, `0x80275528` calls `OSDisableInterrupts` (`0x80271E28`)
- the scheduler rechecks `RunQueueBits` and continues thread selection

This is a canonical guest idle wait. RecompCore already checks `m_idle_pc` and calls `CoreTiming::Idle()` for exactly this class of loop.

`ci/m11d_validate_idle_signature.py` gates the hardcoded PC on the complete surrounding R8PE01 instruction signature.

`.github/workflows/build-m11d-scheduler-idle.yml` is manual-only and runtime-only. It downloads the exact M11C artifact and reuses `gR8PE01_recomp.dll` byte-for-byte, compiling only the M11D frontend. This keeps the A/B attributable and avoids rebuilding all 218 game chunks.

## Follow-up candidates — deliberately not combined with M11D

### Native `strcmp` replacement (possible M11E)

`strcmp` is the biggest measured dispatch site. DolRecomp has a supported module-local replacement facility (`DOLRECOMP_ENABLE_REPLACEMENTS` / `dolrecomp_dispatch_replacement`), and its normal memory helpers preserve the same direct-RAM/external-hook behavior as generated C.

Do not enable until M11C and M11D have been measured. A replacement must account for observable register/CR/XER/CTR side effects and guest cycle charging; returning only a C-library result is not automatically an exact PPC replacement.

### Cross-chunk direct calls — not production safe as-is

DolRecomp upstream PR #13 measured +10.6% from direct C-backend cross-chunk calls, but the maintainer changed the feature to default-off `DOLRECOMP_UNSAFE_DIRECT_CALLS=1` because it bypasses chassis dispatch-time mutable-code validation. OpenSPM/RecompCore relies on those SMC verification boundaries.

Do not enable this production-side unless a verified-target gate is added that preserves RecompCore's mutable-code checks.

### LLVM backend / PGO

Newer DolRecomp has substantial LLVM/PGO improvements, but the current portable CI payload contains generated C rather than the user's original DOL/REL inputs. Regenerating the module with a newer backend should be treated as a later importer/build-pipeline milestone rather than silently changing this performance experiment.

## Next Windows test

M11C first:

1. Run `RUN-OPENSPM-PERF.cmd`.
2. At boot/title, note FPS before replaying the whole intro.
3. Set 21:9 and confirm the log contains `[OPENSPM-ASPECT] module_mode=3 applied` plus `[OPENSPM-PROJ]` entries with a non-1 scale.
4. If performance is still poor, close and send the short log; do not spend another 20+ minutes reaching gameplay.
5. If M11C is dramatically faster, continue to the previously slow Bowser interior for a representative comparison.

Then build/test M11D as the isolated scheduler-idle A/B if needed.
