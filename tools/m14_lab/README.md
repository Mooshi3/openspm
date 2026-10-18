# OpenSPM M14 lab

`tools/m14_lab` is the persistent developer harness for whole-game M14 bring-up. Its purpose is to make the current native-execution milestone reproducible from the repository plus a player-owned SPM image, without depending on a previous chat's `/mnt/data` state.

## Ownership rule

The repository contains only OpenSPM tooling, generic harness code, hashes, addresses, and behavioral metadata. Nintendo-derived C, DOL/REL bytes, extracted files, textures, audio and disc images remain local under `.openspm/` (or another user-selected cache) and must never be committed.

## Files

- `../../state/m14.json` — authoritative identity, milestone, proven fixes, next target, and diagnostic constants.
- `resume_m14.py` — verifies/extracts the player's game, stages generated code, applies the direct-call conversion and requested probes, builds the lab runner, runs it, and writes `latest-run.json` plus per-run logs.
- `probe_generated.py` — deterministic, idempotent generated-source probes. The current `safety-a` probe injects one A press at SPM's already-normalized input boundary; it does not emulate a physical Wii controller.
- `diagnostic_runner.c` — persistent whole-game harness source. Future host-service bring-up belongs here or in promoted `core/` services, never in an ephemeral mystery harness.
- `CMakeLists.txt` — builds the harness against player-local generated chunks and a pinned local CPU helper runtime.

## Cache layout

Default local layout:

```text
.openspm/m14/
  game/                    extracted player-owned game
  generate/                raw local codegen output
  stage/generated-direct/  direct-call generated tree + optional probes
  build/lab/               diagnostic runner build
  runs/<timestamp-hash>/   stdout.log, stderr.log, run.json
  latest-run.json           compact resume/status record
```

The cache is disposable. The *recipe* is the durable state.

## Fresh-session rule

A new session should first read `state/m14.json` and `M14-RESUME.md`, then execute the resume command. If a host service is missing, implement it in the persistent harness/core and update the state after it is proven. Do not encode a magic response just to get past one address when a clean semantic service exists.

## Current architecture

Normal game-to-game calls stay direct/native. OpenSPM intentionally owns selected semantic boundaries: GameFS, input, timing/events, graphics commands, audio and system services. This is what lets the final runtime map PC facilities directly to the game's needs instead of reproducing internal Wii plumbing in the hot path.
