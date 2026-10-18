# M14 dev29 live cockpit — test checkpoint (2026-09-19)

This is the feature-frozen player-validation checkpoint for the live runtime
experiment harness. The goal is to spend the next several days changing game
state and running controlled A/B experiments **without rebuilding OpenSPM**.

## Build once

Run:

    M14-LLVM-UPDATE-RUN.cmd

Do not wipe caches.

Expected build gates:

1. source-only native-pipeline contracts pass and include:

       dev110/dev29 live debug cockpit + reversible experiment harness

2. the selected CTest batch passes, including `m14_exi_diagnostic` against
   backend version `14.0-dev29`;

3. the game launches and logs:

       M14 dev29 live debug cockpit ready

Leave the game process running.

## Open the cockpit

GUI:

    M14-DEBUG-CONSOLE.cmd

Interactive CLI:

    M14-DEBUG-CONSOLE.cmd --cli

One-shot command examples:

    M14-DEBUG-CONSOLE.cmd status
    M14-DEBUG-CONSOLE.cmd map
    M14-DEBUG-CONSOLE.cmd npcs info

One-click scripted checks:

    M14-DEBUG-SMOKE.cmd
    M14-DEBUG-NPC-AB.cmd

## First validation pass

Run these once before relying on the cockpit for longer experiments:

1. `M14-DEBUG-SMOKE.cmd`.
   Expected: status/map/seq/position/HP/NPC/freezes responses and a
   `COCKPIT-SMOKE-PASS` mark.

2. In a simple numbered gameplay map:
   - Next Room;
   - Prev Room;
   - Reload Room.
   Expected: semantic map changes only while `seq=2`.

3. Player controls:
   - save position;
   - nudge Y;
   - restore position;
   - speed 1.5;
   - Restore.
   Expected: checkpoint refuses cross-map restore; Restore returns normal speed
   and clears temporary debug state.

4. Stop cleanup:
   run `tools/native_bridge/experiments/stop_cleanup_test.spmdbg`, then press
   Stop during its wait.
   Expected: deferred cleanup runs, active capture auto-closes, NPC movement and
   normal speed return.

5. Correctness smoke:
   after Restore, jump into a pit once.
   Expected: normal Game Over path still works.

If any one of those fails, fix the cockpit/runtime adapter before using its
performance conclusions.

## NPC performance experiment

Reach the known NPC-heavy room and remain still. Run:

    M14-DEBUG-NPC-AB.cmd

It creates three five-second launch-log snippets under:

    .openspm\m14\debug-captures\

Windows:

- baseline;
- NPC logic globally frozen while render identity remains;
- NPCs hidden+frozen with entries still active structurally.

The first live test already established that clearing every active bit makes the
heavy room immediately smooth, but also crashes after a few seconds. Do not use
full suspend as the normal timed window.

Interpretation:

- global freeze becomes smooth -> NPC update/AI/event work dominates;
- global freeze remains slow but hidden+frozen becomes smooth -> NPC
  display/render work dominates;
- both improve -> both sides scale with NPC count;
- hidden+frozen remains slow even though destructive suspend was smooth ->
  investigate active-entry manager/infrastructure overhead.

Use `npcs isolate hide N` to bisect active actors safely, then
`npcs entry <index>` to identify the boundary actor/tribe.

Use the existing main-loop phase and DrawDone/producer timing lines in those
snippets. Do not infer from subjective smoothness alone.

## Useful live controls

Scene:

    nextroom
    prevroom
    reloadroom
    warp <map>

Player:

    pos
    setpos <x> <y> <z>
    nudge <dx> <dy> <dz>
    checkpoint save
    checkpoint load
    checkpoint clear
    hp [current [max]]
    speed [0.1..10]
    resetdebug

NPC:

    npcs info
    npcs entry <index>
    npcs freeze on|off
    npcs isolate freeze [keep-active-count]
    npcs isolate hide [keep-active-count]
    npcs isolate off
    npcs suspend on|off

Memory/reverse engineering:

    read8/16/32 <addr>
    write8/16/32 <addr> <value>
    dump <addr> <1..128>
    freeze8/16/32 <addr> <value>
    unfreeze <addr|all>
    freezes

Script pseudo-commands:

    wait <seconds>
    sample <seconds> <count> <runtime-command>
    repeat <count> <runtime-command>
    defer <runtime-command>
    capture start <name>
    capture stop

## Safety invariants

- runtime commands are consumed only at the native present safe-frame boundary;
- GUI/CLI clients serialize through a named Windows mutex;
- scripts run deferred cleanup on success, failure, or Stop;
- an active capture auto-closes after cleanup;
- safe NPC isolation snapshots/restores only documented flag46C freeze/hide
  bits while leaving entries active structurally;
- destructive NPC suspend snapshots the documented active bit only and is
  retained only for short advanced probes;
- both control families fail closed across room/table changes;
- explicit cockpit warps reset all temporary debug controls first;
- speed override auto-disables on player-character change;
- raw memory freezes remain sharp tools; use `resetdebug` before normal play;
- moonjump remains intentionally unarmed until the exact jump parameter field is
  proven.

## Rebuild policy after this checkpoint

Do **not** rebuild for ordinary investigation. Prefer:

1. cockpit command;
2. script;
3. scoped capture;
4. only then a code change if the live harness cannot answer the question.

The next code rebuild should be driven by evidence from a live experiment, not
another broad performance guess.
