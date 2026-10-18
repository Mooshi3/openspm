# M14 runtime 233530: hotpath experiment retired

Authoritative Windows player log: `launch(20260913-233530).log`

Tested repo revision: `16d2b30d6454`

Runtime remained strict-native with backend `openspm.native 14.0-dev26`, renderer `openspm.aurora-gx 14.0-dev6`, local module v19, synchronous Aurora `ProcessingMode::Drain`, and no emulator fallback.

## Player-visible regression

Compared with the immediately preceding safe synchronous build, the new 65536-draw hotpath breakdown/no-guest-index fast-path candidate regressed correctness:

- Chapter 1-2 rotating/spinning devices smeared Mario's sprite again;
- entering a door could leave Mario standing in the interaction while the game/event softlocked instead of completing the transition.

The process itself did not hard-crash at the door. The log continued to deliver retraces and heartbeats until the player closed the window. Treat this as an event/render-state correctness regression, not a host crash.

Because the hotpath candidate was the only renderer semantic delta introduced after the previously player-proven synchronous state, it is retired first. Do not keep the scan fast path merely because it looked mathematically safe; player A/B is authoritative here.

## Useful profiling result retained from the rejected build

The breakdown still answered the performance question.

In sustained gameplay, `arrays_us` dominates the measured raw GX draw-preparation time. Representative 65,536-draw windows include:

- total ~387 ms, arrays ~310 ms;
- total ~385 ms, arrays ~308 ms;
- total ~360 ms, arrays ~280 ms;
- total ~324 ms, arrays ~255 ms;
- later lighter windows around total ~195-210 ms still spend ~137-155 ms in array staging.

Guest-index scanning is much smaller (~13-18 ms/window). Pipeline, bind and uniform work are also secondary in the same windows. Therefore the next performance investigation should focus on the indexed-array staging/cache/snapshot path itself — especially repeated exact snapshot/memcmp/cache work at enormous draw counts — rather than further reducing guest-index scan overhead.

This does **not** license weaker coherency. Previous async-worker attempts already proved that live mutable guest references cannot be consumed later without versioning/snapshotting.

## Rollback architecture

Added:

`renderer/aurora/apply_draw_hotpath_breakdown_rollback_patch.py`

It reverses the hotpath instrumentation and no-guest-index scan fast path back to the exact historical coarse-profiler source shape.

Updated:

`renderer/aurora/apply_copy_texture_ownership_patch.py`

The compatibility entry now detects an already-patched cached Aurora checkout, runs the rollback before the historical coarse-profiler exact transform, and leaves fresh pinned checkouts on the ordinary path. This is specifically designed so `M14-UPDATE-RUN.cmd` heals the player's cached `.openspm` checkout without a wipe.

## Next player proof

Run `M14-UPDATE-RUN.cmd` normally; do not wipe `.openspm` or saves.

Before doing any more performance work, verify:

1. rotating/spinning Chapter 1-2 devices no longer smear Mario;
2. entering an ordinary door completes the transition again;
3. v19 systemic graphics remain clean.

Only after that A/B passes should the next optimization target the now-measured array-staging hot path.