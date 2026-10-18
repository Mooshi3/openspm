# M14 live debug cockpit

## 2026-09-20: read-only camera follow trace

After dev114 state-memory fixed the heavy-scene CPU collapse, the remaining
player-visible jitter is isolated to camera follow: Mario, enemies and UI move
smoothly while the view trails and periodically catches up.

A client-only probe now reads the public R8PE01 camera layout through existing
safe-frame debug commands:

```text
camera_trace <seconds> [interval-seconds]
```

Canonical launcher:

```bat
M14-DEBUG-CAMERA-TRACE.cmd
```

Public layout used:

```text
camPtrTbl       = 0x804C9F80
camera 5        = 3D
camera 11       = 2D
CamEntry.pos    = +0x15C
CamEntry.target = +0x168
```

The probe uses only `map`, `pos`, `read32` and `dump`; it performs no
guest writes and needs no native rebuild. It writes a timestamped
`camera-follow-trace.csv` plus JSON metadata in the normal
`.openspm/m14/debug-captures` directory.

For the canonical test, stand in Yold Town and hold right steadily for the
8-second trace. Both camera 11 and camera 5 are sampled together. Repeat after
flipping to 3D if desired.

Interpretation:

- camera pos/target stalls while Mario advances, followed by larger catch-up
  steps -> camera update/follow logic or guest update ordering;
- camera pos/target moves smoothly but the visible view still snaps -> renderer
  or presentation transform sampling;
- only camera 11 shows irregularity -> 2D camera path;
- only camera 5 shows irregularity -> `cam3dMain` / 3D path;
- both show the same irregularity -> shared `camMain`, camera-road logic or
  presentation path.

## 2026-09-20: one-NPC active-bit bisector

The console now has a bounded per-actor diagnostic intended to replace repeated
rebuild/instrumentation loops for the current NPC-performance investigation:

```text
npcs pulseone <active-ordinal> <1..120 safe-frame ticks>
npcs pulseone off
```

It resolves the selected currently-active NPC at the normal safe guest-thread
frame boundary, suppresses only `NPCEntry::flag8.active`, and restores only that
bit. The pulse has independent state from the older all-NPC suspend/isolation
controls, refuses to overlap them, validates map/table/id identity before every
write, and auto-restores on expiry. `resetdebug` also restores it.

The canonical automated sweep is:

```bat
M14-DEBUG-YOLD-NPC-SWEEP.cmd
```

This is designed for one running build: it records a Yold baseline, pulses each
of the seven stable townsfolk independently, and produces an automatic ranking.
No rebuild occurs between actors. After the backend containing `pulseone` has
been built once, follow-up pulse lengths/ordinals can be changed entirely from
the console/script layer.

Status: dev110/dev29 source ready; player verification pending.

## Purpose

The repeated rebuild -> replay -> aggregate-log loop is now slower than the
performance investigation itself. dev29 turns the development-only M13 mailbox
idea into a live experiment cockpit for the current standalone native runtime.

The client never maps Wii RAM directly. It publishes one UTF-8 command through:

    Local\OpenSPM.Debug.<OpenSPMNative PID>

The native backend consumes commands only at the existing once-per-frame
OPENSPM_M14_HOST_EVENT_PRESENT_SAFEPOINT, a guest-thread frame boundary while
Broadway execution is paused. The mailbox therefore provides deterministic
runtime data/state experiments without cross-thread MEM1/MEM2 writes.

This is a private development facility, not a public mod API.

## Start

Build and launch normally:

    M14-LLVM-UPDATE-RUN.cmd

Leave the game running, then start the cockpit:

    M14-DEBUG-CONSOLE.cmd

With no arguments the launcher opens the Tk GUI. A command or CLI option keeps
the lightweight command-line workflow:

    M14-DEBUG-CONSOLE.cmd status
    M14-DEBUG-CONSOLE.cmd map
    M14-DEBUG-CONSOLE.cmd --script tools\native_bridge\experiments\cockpit_smoke.spmdbg

Convenience launchers:

    M14-DEBUG-SMOKE.cmd
    M14-DEBUG-NPC-AB.cmd

The GUI provides scene navigation, player controls, NPC performance isolation,
a script editor, transcript, memory lab and launch-log capture controls.

## Semantic runtime commands

    help
    status
    perf
    seq
    map
    resetdebug

    nextroom
    prevroom
    reloadroom
    warp <map>

    pos
    setpos <x> <y> <z>
    nudge <dx> <dy> <dz>
    checkpoint save|load|clear
    hp [current [max]]
    speed [multiplier]
    moonjump ...

    npcs info
    npcs entry <index>
    npcs freeze on|off
    npcs isolate off
    npcs isolate freeze [keep-active-count]
    npcs isolate hide [keep-active-count]
    npcs suspend on|off

    story [value]
    gsw <index> [value]

    freeze8 <address> <value>
    freeze16 <address> <value>
    freeze32 <address> <value>
    unfreeze <address|all>
    freezes

    read8 <address>
    read16 <address>
    read32 <address>
    write8 <address> <value>
    write16 <address> <value>
    write32 <address> <value>
    dump <address> <length>
    mark <text>

Numbers used by raw integer commands use C-style base detection, so decimal and
0x-prefixed hexadecimal are both accepted. dump is capped at 128 bytes.

### Room navigation

warp resolves a map through the live R8PE01/US2 mapData table and publishes the
same next-map sequence state used by the game's semantic map-change path. It is
refused outside gameplay sequence 2.

nextroom and prevroom parse the current numeric map suffix and search forward or
backward within the same prefix, skipping missing numeric map IDs. reloadroom
queues the current map again.

All temporary cockpit state is reset before a cockpit room warp: NPC controls,
movement-speed override, persistent raw-memory freezes and the saved position
checkpoint. NPC suspension is also keyed to the current map name; if normal game
progression changes maps while suspension is active, old snapshots are discarded
instead of being restored into the new room.

### Player tools

pos, setpos and nudge operate on the documented R8PE01 MarioWork position.

checkpoint save stores the current map and XYZ in host debug state. checkpoint
load restores the position only when still in that same map; it refuses after a
map change instead of teleporting into unrelated geometry.

speed captures MarioWork walk/dash speed and enforces a bounded multiplier at
safe frame boundaries. speed 1 restores the captured values. If the active
character changes, enforcement stops rather than applying one character's base
speeds to another.

hp reads the documented MarioPouchWork current/max HP pair. A single value
sets both current and max HP (for example, `hp 100` means 100/100); two values
allow deliberate asymmetric tests such as `hp 5 20`.

moonjump intentionally remains fail-closed for now. Public headers document the
jump/fall parameter block but do not identify its internal fields precisely
enough to safely install a persistent jump multiplier. Position nudge is the
temporary platforming shortcut.

### NPC performance isolation

The public SPM headers document NPCWork::flag bit 0 as "freeze all NPCs",
NPCEntry::flag8 bit 0 as active, and NPCEntry::flag46C bits for per-entry
frozen and hidden+frozen state.

npcs freeze on disables NPC logic globally while leaving entries
active/renderable. This is the clean update-vs-display test.

npcs isolate freeze [keep] snapshots the active entries and applies the game's
own per-entry frozen state after the first `keep` active entries. Rendering
identity stays present. npcs isolate hide [keep] instead applies the documented
hidden+frozen state, removing both normal update and display work while keeping
the NPC entries active structurally. `npcs isolate off` restores only those
two diagnostic flag46C bits. `npcs entry <index>` reports an entry's ID,
tribe, flags, name and position so a bisection can be mapped back to a specific
actor.

The original npcs suspend command remains available as an advanced diagnostic.
It clears NPCEntry::flag8's active bit. The first player test proved that doing
this to every NPC makes the laggy room immediately smooth, which is strong
causal evidence for active-NPC-scaled work, but the room crashed a few seconds
later. Full suspend is therefore no longer part of the default A/B script.

The default three-way A/B is now:

1. baseline — normal update + display;
2. global freeze — display remains while normal NPC logic is stopped;
3. hidden+frozen isolation — entries remain structurally active while their
   normal update/display workload is suppressed.

If needed, repeat `npcs isolate hide N` with different N values to bisect
whether one/few NPCs are pathological or cost scales roughly linearly with
active count.

## Script language

The GUI script editor and --script use the same line-oriented language. Blank
lines and # comments are ignored.

Runtime command lines are sent directly. The client also implements:

    wait <seconds>
    sleep <seconds>
    repeat <count> <runtime command>
    sample <seconds> <count> <runtime command>
    defer <runtime command>
    onexit <runtime command>
    capture start <name>
    capture stop

sample runs a command repeatedly with a delay between samples, which is useful
for short state watches inside a capture.

defer/onexit commands run in reverse order after successful completion, Stop, or
a script failure. Use them for temporary mutations:

    defer npcs isolate off
    defer npcs freeze off
    defer speed 1
    defer unfreeze all

An active capture is also auto-stopped after cleanup if a script fails or the
user presses Stop.

## Scoped launch.log captures

capture start records the current byte offset of:

    .openspm\m14\home-test\launch.log

and emits a runtime CAPTURE-BEGIN marker. capture stop emits CAPTURE-END and
writes only the bytes produced during that interval to:

    .openspm\m14\debug-captures\

This supports experiments such as:

    defer npcs freeze off
    capture start npc-freeze
    npcs freeze on
    wait 5
    capture stop

The capture reader also detects a truncated/restarted launch.log and starts from
the new file beginning instead of producing an empty snippet.

## Checked-in experiment scripts

    tools\native_bridge\experiments\cockpit_smoke.spmdbg
    tools\native_bridge\experiments\npc_performance_ab.spmdbg
    tools\native_bridge\experiments\stop_cleanup_test.spmdbg

cockpit_smoke is non-destructive.

npc_performance_ab creates baseline and logic-frozen five-second snippets plus
a three-second safe hidden+frozen isolation snippet. It intentionally does not
run destructive active-bit suspension.

stop_cleanup_test intentionally waits 30 seconds after changing temporary state;
press Stop during the wait to prove deferred cleanup and capture auto-close.

## GUI workflow

The cockpit includes:

- Prev / Reload / Next room and explicit map warp;
- Mario speed, current/max HP, position read/nudge controls and same-map position checkpoint;
- NPC info, logic-freeze and safe hidden+frozen isolation toggles;
- a script editor with Run, Run Selection, Stop, Open and Save;
- Ctrl+Enter to run the editor and Ctrl+Shift+Enter to run selected lines;
- one-click NPC A/B template;
- scoped launch.log capture and capture-folder button;
- Memory Lab read/write/freeze/dump controls;
- Snapshot for status/map/position/HP/NPC/freeze state;
- Restore uses one `resetdebug` safe-frame command to clear NPC controls,
  speed multiplier, persistent memory freezes and the position checkpoint;
- transcript save/clear controls.

`resetdebug` is also issued best-effort when the GUI closes, so a debugger
window close should not strand the still-running game in an experimental state.

The mailbox client serializes concurrent sends. A timed script releases the GUI
between commands/waits, so manual probes can still be issued while an experiment
is sitting in a timed window. A named Windows mutex also serializes separate
GUI/CLI client processes targeting the same OpenSPMNative PID; a second client
waits rather than overwriting the shared command generation.

## Safety and limitations

- Commands run at the guest-thread frame boundary; the client never performs
  arbitrary cross-thread MEM1/MEM2 writes.
- write8/16/32 and persistent freezes are intentionally sharp reverse-engineering
  tools and can corrupt game state.
- Safe NPC isolation uses the game's documented per-entry frozen/hidden+frozen
  bits and keeps entries active structurally.
- NPC suspend does not call NPC destructors. It is an invasive active-bit
  diagnostic retained for short advanced probes; full-room suspension has
  already been observed to crash after a few seconds and is not used by the
  default A/B.
- Raw guest-code writes do not rewrite already-generated LLVM/x86 code.
  Runtime function interception/skipping still requires a separate native-hook
  layer.
- The rejected asynchronous GX FIFO design remains rejected. Nothing here
  changes FIFO threading, memory visibility, VI timing or simulation cadence.
- If a test changes gameplay correctness, Restore or restart before drawing
  performance conclusions.

## First player validation

After the dev29 build passes its ordinary source/CMake/CTest gates, treat that
binary as the live-debug test checkpoint and avoid rebuilds unless a cockpit
command exposes an actual runtime/backend defect:

1. Launch the game and leave it running.
2. Open M14-DEBUG-CONSOLE.cmd and confirm the GUI reports connected.
3. Run cockpit_smoke.spmdbg.
4. Try nextroom then prevroom once in a simple numeric-map area.
5. Apply speed 1.5, walk briefly, then Restore and confirm normal speed.
6. Run stop_cleanup_test.spmdbg and press Stop during its 30-second wait.
   NPC movement and normal speed must return and a capture must be saved.
7. Reach the known NPC-heavy room and run npc_performance_ab.spmdbg.
8. Compare the three capture snippets using main-loop phase cadence and
   DrawDone/producer timing.
9. After Restore, trigger the previously sensitive pit/Game Over path once to
   smoke-test that the debug layer did not perturb correctness.

Interpret the NPC A/B before adding another optimization:

- global freeze becomes smooth: update/AI/event work is a major owner;
- global freeze remains slow but hidden+frozen becomes smooth: display/render
  work is the major owner;
- both improve materially: update and display both scale with NPCs;
- hidden+frozen is still slow despite full active-bit suspend having been
  smooth: investigate manager/active-entry infrastructure specifically.

Then use `npcs isolate hide N` (and `npcs entry N`) to determine whether the
cost is one pathological actor/tribe or approximately per-NPC linear overhead.
