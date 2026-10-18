# Pending in-game tests

Only what still needs a run is listed here. Pull, then double-click
`OPENSPM-TEST-LAUNCHER.cmd`: pick a test on the left, click one of its launch
buttons (they set the flags and settings), play, close the game window, check
the Log lines tab and click **Save result**. Send `test-results\RESULTS.md`
(each run's log is kept in `test-results\logs\`). The `<!-- launch: ... -->`
lines are the launcher's buttons.

Done and confirmed: star door (no shift, no missing chunk), texture packs,
present timeline (incl. resize), draw tags, F1 menu pause and controls
detection, audio sound effects mixing.

If a normal launch misbehaves (crash, stuck, odd input), try the `safe` flag
(Options tab) or `M14-VANILLA-RUN.cmd AUTO safe`: it turns off the newer
default-on extras (menu pause, HOME menu, stick pointer, rumble, turbo,
cheats, texture packs). Tell me which launch worked.

## 00. Since your last runs (do this one first)

<!-- launch: label="mods on" env=OPENSPM_MODS=1 -->
<!-- launch: label="no mods (vanilla check)" env=OPENSPM_MODS=0 -->
<!-- grep: OpenSPM content|OpenSPM followers|OpenSPM panic|OSPanic -->

The first launch rebuilds the native module (the character id patches
changed). Use the window mode you had before (borderless or windowed): in
exclusive fullscreen at 59.978 Hz the game drops to 30 fps (a known pacing
bug, see the end of this section).

1. **Switching back and forth.** As Mario pick Francis (flip into him).
   Then pick Mario: he flips back (before, nothing happened). Pick Francis,
   then Francis again: the second pick does nothing (before, Mario's
   "wahoo" and a flip that kept Francis). Same with the Koopa Striker, and
   Francis -> Striker directly.
2. **Menu entries.** While you pick, Francis's and Striker's entries keep
   their own name and icon (before, they turned into Tippi and Thudley for
   a moment).
3. **Francis's tongue.** As Francis press F4 (his animation browser) and
   step through his animations: tell me which ones, if any, show the tongue.
   The tongue animation does play (the log counts tongues and hits), but
   nothing shows; if no animation shows it, the boss draws it some other way.
4. **Francis's size.** F9 (Hitbox View): is his green box big (72 x 72) or
   Mario-sized? The log line `Francis's hitbox in play: width 20.0, height
   39.0` reads a field the game fills with Mario's numbers; I think the real
   hit test uses his, the box will tell.
5. **Flint and scenes without Mario.** Watch the opening ("Ahem! Today...")
   again: Flint should be gone while Mario is not in the scene (the log
   says `OpenSPM followers: the player is hidden ...; followers leave`) and
   come back behind Mario afterwards. Scenes Mario is in keep him.
6. **If the game freezes**, the log now has lines starting with
   `OpenSPM panic:` (the game's own check that failed, with its file and
   line). Send the log.
7. (Optional) **Exclusive fullscreen pacing.** Only if you want to help
   with the 30 fps bug: `[Display] window_mode = exclusive`, play a minute,
   send the log. Then switch back.

## 00b. The sample level (new): a new map with new enemies and NPCs

<!-- launch: label="mods on" env=OPENSPM_MODS=1 -->
<!-- grep: Sample Level|OpenSPM content|OpenSPM mod files|OpenSPM panic|OSPanic -->

A mod (mods/sample_level) adds a whole level, `os1_01`: a meadow with its
own map, two new enemies (the Brick Goomba and the Frost Koopa) and three
NPCs. Nothing of the game's is replaced. The first launch rebuilds the
native module (new code patches for the enemy tables).

1. **It loads.** Load a save and stand in Flipside. The log should say
   `Sample Level: os1_01 is ready (press F3 to go there)` and
   `OpenSPM mod files: ... map/os1_01.bin`. If it says "refused" or "off",
   send the log.
2. **Getting there.** Press F3 (no menu open). You should come up out of
   a green pipe in a meadow. Tell me: does it load at all? Is the ground
   textured (grass, dirt, bricks) or plain white / black? Is there a sky
   behind?
3. **The camera.** It is OpenSPM's (the map has no camera road): it
   should follow you side on. Too close, too far, too high? Does it hide
   anything?
4. **Walk to the right.** Hit the blocks (one has a mushroom). Stomp the
   orange-red Brick Goombas (bigger, take three hits?) and the ice-blue
   Frost Koopas up on the step. Coins over the wooden platforms.
5. **The NPCs.** Talk to Marigold (by the start), Sprout (on the high
   platform) and Old Rowan (near the end): do their lines show, and do they
   turn to talk? Tattle one with Tippi: her line should be about that NPC.
6. **3D.** Flip by the start (just right of the crate): three coins hide
   behind the path. Does the camera swing round to the side, and back when
   you flip back?
7. **Leaving.** Go down the pipe at the far right (or the one you came out
   of, or press F3): you should come out of a pipe in Flipside's main
   square. (No save block in the level on purpose: a save made in a mod's
   map would not load with the mod off.)
8. **If the game freezes or crashes**, send the log (`OpenSPM panic:` lines
   say where).

Also tattle a Brick Goomba and a Frost Koopa: each should get its own
text (with its HP and attack in the words). They have no catch cards, so
if the tattle shows a card picture it may be blank or odd: tell me.

## 0. Where the frame time goes (sampling profiler, new)

<!-- launch: label="profile (mods on)" env=OPENSPM_PROFILE=1 env=OPENSPM_MODS=1 -->
<!-- launch: label="profile (mods off)" env=OPENSPM_PROFILE=1 env=OPENSPM_MODS=0 -->
<!-- grep: OpenSPM profile -->

In a level the game's own work takes about 12 ms of each 16.7 ms frame
(`disp` ~6 ms + `main` ~6 ms in the `main-loop phases` lines), so anything
extra (a big character, bbox reads, a pipe) misses the refresh and the
frame rate drops to 40-50. The log can't show which game functions those
12 ms go to; this can. The first launch rebuilds the native module (it now
comes with a link map, `User-OpenSPM\native\openspm_local_native.map`).

1. Log at start: `OpenSPM profile: sampling the game thread`. Then the first
   report names the modules: `OpenSPM profile: openspm_local_native.dll has
   <n> named functions (its .map)`.
2. Play about 30 s in each of: a quiet level, a busy one, and going in and
   out of pipes (or doors) several times in a row. Each 30 s the log gets a
   block of `OpenSPM profile` lines: the top 40 functions with their share
   of the samples, and the share per module.
3. Close the game: a final `whole run` block. Send the log. Tell me roughly
   what you were doing in each 30 s window (the windows are numbered).

The game runs a few percent slower while profiling; without
`OPENSPM_PROFILE=1` (or `[Debug] profile = 1` in OpenSPM.ini) nothing runs.

## 0a. Real character ids (new, do this one first)

<!-- launch: label="real ids (mods on)" env=OPENSPM_MODS=1 -->
<!-- launch: label="old slot mode (compare)" env=OPENSPM_MODS=1 env=OPENSPM_CHARACTER_SLOTS=1 -->
<!-- launch: label="no mods (vanilla check)" env=OPENSPM_MODS=0 -->
<!-- grep: OpenSPM content|guest call|transition did not start -->

Added characters are now their own characters in the game (ids 4, 8, ...)
instead of Mario with a different model (docs/CHARACTER-IDS.md). This
changes about 240 places in the game's code, so the first thing to check is
that **the game itself is unchanged**. The first launch rebuilds the native
module.

1. **No mods**: play a few minutes as each character you have: switch
   characters, flip to 3D, take damage, stomp, go through doors and pipes,
   an 8-bit section if you can. Everything should look and sound exactly as
   before. Tell me anything different.
2. **Mods on**: the log says `OpenSPM content: real character ids: Francis
   = 4, Koopa Striker = 8`. As Mario, pick Francis: Mario should flip into
   him like a normal character switch; the log says `now playing Francis
   (..., id 4, ...)`. Walk, jump, take damage (his boss sounds), use doors
   and pipes, crouch for the tongue. Then pick Mario again (flip back), then
   the Koopa Striker, then Peach.
3. Save while playing Francis, quit to the title, load the save: tell me
   which character you are and what he looks like.
4. If anything breaks, the **old slot mode** button runs the old way
   (`OPENSPM_CHARACTER_SLOTS=1`): tell me whether that one still works.

## 0c. Followers: Flint Cragley tags along (new)

<!-- launch: label="mods on" env=OPENSPM_MODS=1 -->
<!-- grep: OpenSPM followers|follower -->

`mods\openspm_flint_follower.dll`. Load a save and walk around.

1. The log says `OpenSPM followers: openspm.sample.flint_follower:flint
   (model n_stg5_hiroshi; S_1 / W_1 / R_1) follows 50 units behind`, then
   `... joins (1)` and `...'s model n_stg5_hiroshi has <n> animations: ...`.
   **Is it Flint Cragley?** If not, send me that line: I picked the model by
   its name.
2. Walk, run, jump, stop: he walks behind you (behind the Pixl), runs to
   catch up, stays on the ground when you jump, stands when you stop, faces
   where he walks. Flip to 3D and back.
3. Go through doors and pipes, and into a few cutscenes: he should be there
   after each. Tell me where he vanishes or does something odd (floats,
   sinks into the floor, walks through walls, stands in a weird spot).

## 0b. Added characters and Pixls switch with the game's flip (new)

<!-- launch: label="mods on" env=OPENSPM_MODS=1 -->
<!-- grep: OpenSPM content|transition did not start -->

Picking an added character or Pixl in the pause menu now plays the same
card-flip transition the game plays for its own.

1. As Mario, pick Francis (or the Koopa Striker): the menu closes, and
   Mario flips into him, as when you pick Peach.
2. Pick Mario again: he flips back into Mario.
3. As Peach, pick Francis: also a flip (the game's own switch, as before).
4. Pick Glock in the Pixl tab: Tippi (or whoever is out) flips into Glock.
   Then pick another added Pixl, or a game Pixl: also a flip each time.
5. Tell me if any of them switches without the flip, freezes, or shows the
   wrong model during the flip (a frame of the old model is normal). The log
   says `the switch transition did not start` if OpenSPM fell back to the
   instant switch.

## 1. Flip (Mario missing from the card)

<!-- launch: flags="snapcapture" -->
<!-- analyze: --bbox-wide 500 --limit 40 --passes > flip8.txt -->
<!-- grep: M14 capture-request|M14 snapshot -->

```
M14-VANILLA-RUN.cmd AUTO snapcapture
python tools\diag\analyze_gx_capture.py launch.log --bbox-wide 500 --limit 40 --passes > flip8.txt
```

Do one flip, then send `flip8.txt`. The log should contain
`M14 capture-request GX capture started at the next bbox read`.

No new run needed first: your earlier snapcapture log is kept in
`test-results\logs\` (the `...-test1.log` from 14:2x). In the launcher click
**Analyze log -> flip8.txt**, answer **No** to "latest launch.log?" and pick
that file. `--passes` lists what every pass between two EFB copies drew and
sampled, so we can see which copy the flip card really shows and whether
Mario is drawn into it.

## 2. F1 menu in a small window

<!-- launch: label="normal launch" -->
<!-- grep: OpenSPM menu -->

F1 -> Display -> Window size 640x480 (and the smallest preset): the menu text
stays at its normal size and the menu scrolls inside the window instead of
shrinking. Also check the Esc / F1 close, and that the menu still pauses.

## 3. Game picker (`OpenSPMNative.exe` without `--game`)

<!-- launch: label="M14-VANILLA-RUN.cmd still works" -->

The launch scripts always pass `--game`, so they never show the picker: run
`OpenSPMNative.exe` itself from `.openspm\m14\windows-candidate\`.

- Remove `[Game] path` from `OpenSPM.ini`, double-click `OpenSPMNative.exe`,
  pick your `.wbfs`: the dialog, the extraction confirmation, and on the second
  launch reuse of the extracted copy.
- A wrong file gives a clear message. If your correct disc is refused, the
  revision check (disc header byte 7 = 2) is wrong - tell me.
- `M14-VANILLA-RUN.cmd` still behaves as before.

## 4. Give Gold Bar x3 (F1 -> Cheats), replaces Max coins

<!-- launch: label="normal launch" -->
<!-- grep: OpenSPM cheats -->

Max coins is gone (setting the coin count kept softlocking shops). In a level,
open F1 -> Cheats and press **Give Gold Bar x3** a couple of times, close the
menu: the items should be in your pause menu's Items tab (log: `OpenSPM
cheats: giving a Gold Bar x3 (pouchAddItem 72)`). Sell them in a shop for
coins and buy something: the purchase must finish. A press on the title
screen waits until you are in a level. Infinite HP and the custom code
`044D0444 000003E7` as before.

## 5. Frame interpolation (Stage C, opt-in, displays above 60 Hz)

<!-- launch: flags="interp" -->
<!-- launch: flags="interpdirect" -->
<!-- launch: label="plain launch (compare)" -->
<!-- grep: M14 frame interp|M14 present timeline|M14 draw tags -->

```
M14-VANILLA-RUN.cmd AUTO interp
M14-VANILLA-RUN.cmd AUTO interpdirect
```

`interp` turns on the Stage A timeline (smooth), the Stage B draw tags and the
interpolated images together. The update run recompiles the renderer and the
bridge file (the bridge now also sends each 3D camera's view matrix). Try it on
your 99.943 Hz mode first, then 75/100/144 Hz if you have them.

What to try: walk and run left/right on a flat stretch (camera scrolling),
stand still, go through a door (room change), do one 2D/3D flip, open a
menu/pause screen.

Log, once per second:

```
M14 frame interp: presents= interpolated= native_a= native_b= fallbacks=<reason:count,...>
  frames= unsafe_frames= pairs= owners= matched_owners=
  snapped[spawn,count,structure,teleport,rotation]=... view_draws=x% view_static=y%
  replay_us_avg= replay_us_max= draws_per_replay= upload_kb_per_replay= plans= plan_us_avg=
```

plus `M14 frame interp active: ...` once, and in the draw tags lines
`views=` (renderer) and `views= view_failures=` (bridge).

Healthy while walking on a scrolling stretch:

- Motion looks smooth at 99.943 Hz: no 2-2-1 stepping of the world or Mario,
  HUD rock steady.
- `interpolated` is most of `presents` (the rest is `native_a`/`native_b`:
  refreshes that land on a game frame); `fallbacks=none` or small.
- `matched_owners` close to `owners`; `snapped` small (a few spawn/count while
  enemies appear or animate).
- `view_draws` near 100% and `view_static` well above 0 while the camera
  pans over static scenery (it proves the game camera matrices are the ones the
  scene uses). If `view_static` stays near 0% while panning, or objects wobble
  against the background, compare with `interpdirect` and send both logs.
- `replay_us_avg` well below one refresh (6900 us at 144 Hz, 10000 us at
  100 Hz) and `late`/`missed_vblanks`/`busy_skips` in the `M14 present timeline:`
  line near 0.

Expected fallbacks (these frames show the newer game frame as is, never an
older one): `efb-copy`/`bbox-read`/`encoder-task` during flips and door
effects, `camera-cut` or `scene-change` for one frame at a room change,
`prior-load` next to bounding-box reads. If one reason appears every second
during ordinary walking (for example `efb-copy` or `mid-frame-upload`), send
the line: that effect is drawn every frame and blocks interpolation.

Also check: no visual difference with the plain launch (no argument), and
`timeline` alone still behaves as before (its log line is unchanged).

## 6. Frame mode setting (F1 -> Display -> Frame mode)

<!-- launch: label="Smooth (frame_mode=smooth)" env=OPENSPM_FRAME_MODE=smooth -->
<!-- launch: label="Vanilla 60 (frame_mode=vanilla)" env=OPENSPM_FRAME_MODE=vanilla -->
<!-- launch: label="Smooth saved + timelinevanilla" flags="timelinevanilla" env=OPENSPM_FRAME_MODE=smooth -->
<!-- grep: frame_mode|M14 frame interp|M14 present timeline -->

```
M14-VANILLA-RUN.cmd AUTO
```

Smooth is now a player setting (`[Display] frame_mode`) instead of only the
`interp` flag. Launch with no flag, pick **Smooth (interpolated)** under F1 ->
Display -> Frame rate, restart. Check:

- The launch log shows `OpenSPM.ini [Display] frame_mode=smooth:
  OPENSPM_M14_ASYNC_PRESENTER=1 ... OPENSPM_M14_INTERP=1`, then the same
  `M14 frame interp:` lines as item 5 (motion as with `interp`).
- The menu says `Running: Smooth: present timeline + draw tags + interpolation`
  and `Presenter clock: <your refresh> Hz display`.
- Switch back to **Vanilla 60**, restart: no `M14 present timeline` lines,
  `Running: Vanilla 60`.
- With Smooth saved, `M14-VANILLA-RUN.cmd AUTO timelinevanilla` wins: the log
  says `frame_mode=smooth overridden by OPENSPM_M14_ASYNC_PRESENTER already
  set` and the menu shows the timeline in vanilla mode and "restart to apply".

## 7. Native module built with clang/lld instead of MSVC (optional, release Stage 1)

<!-- launch: flags="clang" -->
<!-- launch: flags="clang-freestanding" -->
<!-- grep: toolchain|clang|Yold -->

The default build is unchanged (MSVC). To try the pinned LLVM's clang-cl +
lld-link (VS Build Tools still installed):

```
M14-LLVM-UPDATE-RUN.cmd AUTO 256 1 0 clang
M14-LLVM-UPDATE-RUN.cmd AUTO 256 1 0 clang-freestanding
M14-VANILLA-RUN.cmd AUTO clang
```

Check: the build finishes, the game boots and plays as usual, and the Yold
benchmark / fps is about the same as the MSVC build. For `clang-freestanding`,
`llvm-readobj --coff-imports .openspm\m14\llvm-candidate\native-output\openspm_local_native.dll`
must list only `kernel32.dll` and `ucrtbase.dll`. Send the build log's last
lines if either toolchain fails.

**New: the same module without CMake** (what a release will do). In the
console, before the second command: `set OPENSPM_M14_NO_CMAKE=1`. The log
says `module built without CMake (direct_module_build.py)` and
`module_toolchain=clang-freestanding (no CMake)`; the game should play the
same. A `openspm_local_native.stamp.json` now sits next to every built
module (the cache stamp). The known-good C DLL is restored after the
launch, as with MSVC. Details: docs/RELEASE-PLAN.md section 5.

## 8. Star door start hitch (bbox fast path, opt-in)

<!-- launch: flags="bboxfastverify" -->
<!-- launch: flags="bboxfast" -->
<!-- grep: M14 bbox fast|M14 renderer budget -->

```
M14-VANILLA-RUN.cmd AUTO bboxfastverify
M14-VANILLA-RUN.cmd AUTO bboxfast
```

When a star door (or the flip) starts, the game reads the GX bounding box
about a thousand times in one frame; each read waited for the GPU (the
~0.1 s hitch, ~50 fps while the door runs). The fast path answers a read on
the CPU when it can prove no draw since the box was cleared produced a pixel
(no draw at all, every vertex outside the same screen edge / behind the
camera, or all vertices identical). Anything else still asks the GPU.

1. First `bboxfastverify`: open a star door two or three times, flip once.
   Every read still goes to the GPU; the log shows once per second
   `M14 bbox fast: mode=verify reads=... fast=... (no_draws=... offscreen=...
   degenerate=...) gpu=... verify_mismatches=0 bbox_ms=...`.
   Healthy = `verify_mismatches=0` on every line and no
   `M14 bbox fast verify MISMATCH` line. Send any MISMATCH line.
   `fast` / `reads` in the door-start second is the share the fast path
   will skip.
2. Then `bboxfast`: open the same doors. Healthy = the door starts without
   the freeze, runs at a steady 60 fps, and looks the same as before (door
   halves, captured characters). The door-start second's line shows
   `mode=on`, most reads under `fast=`, few under `gpu=`, and `bbox_ms` far
   below the ~100 ms of the old hitch.

Send both door-start `M14 bbox fast:` lines (and the `M14 renderer budget`
line of the same second).

## 8b. Wii Remote shake (new)

Q on the keyboard or RB on a gamepad shakes the Remote. Jump on an enemy and
press Q (or RB) right after the stomp: Mario should do a stylish move. Also try
holding it. Then use a Fire Burst (or Ice Storm) and hold Q / RB while its
gauge is up: the gauge should fill as it does when you shake a real Remote. Tell me whether stylish moves come out, how reliably, and whether
anything else reacts to Q / RB (it should not reach the game as a button). The
log says `OpenSPM input: Wii Remote shake` the first time.

## 9. Mods: per-frame tick, guest memory, Shell Crouch, Glock and Koopa Striker samples (opt-in)

<!-- launch: label="mods on" env=OPENSPM_MODS=1 -->
<!-- launch: label="mods off" env=OPENSPM_MODS=0 -->
<!-- grep: OpenSPM Debug|OpenSPM mods|OpenSPM mod guest call|Shell Crouch|Glock|Striker|OpenSPM content|True SPM simulation tick -->

Build `openspm_debug.dll`, copy it to the `mods` folder next to
`OpenSPMNative.exe`, and set `OpenSPM.ini [Mods] enabled = 1` (or
`OPENSPM_MODS=1`). Play for a minute: title screen, file select, into a level.

Healthy = the log shows once `OpenSPM Debug acquired openspm.guest_memory`,
`OpenSPM mods: per-frame TICK_BEGIN / PRESENT_END events active at the frame
safepoint`, `True SPM simulation tick confirmed` and `OpenSPM Debug received
its first live present event`, then about
every 10 seconds `OpenSPM Debug tick=<n> seq=<s>`: `n` grows by 600 each time,
`s` is a small number that changes with the screen (2 once you are in a level).
No stutter compared with the same run without mods.

**Shell Crouch** (`mods\openspm_shell_crouch.dll`, built into the mods folder
automatically): in a level, crouch (hold down while standing) or press F6. A
Koopa shell (the soccer Koopa's) should drop in front of Mario; kick it. Log:
`Shell Crouch: script ready at 0x93F0...`, then per shell `Shell Crouch: shell
#n (...)`, `OpenSPM mods: guest call #n to 0x800D8CDC` and `OpenSPM mod guest
call #n to 0x800D8CDC completed`. Send any `guest call ... failed` line, and
tell me where the shell appeared (in front? inside a wall? in 3D?).

**Glock** (`mods\openspm_glock.dll`, also built into the mods folder): a
Frackle partner who fires Squirps blasts. Shell Crouch also reacts to crouching,
so for this part turn it off (`[Mods] openspm_shell_crouch.dll = 0`, or the F1
Mods tab). In a level with enemies (1-1 is fine):

1. Open the pause menu's Pixl tab: after the game's Pixls there should be
   **Glock** (Fire Burst's fireball icon; his name and description should read
   "Glock ..."), and the tab's window should be one row taller so his row is
   inside it. Pick him with 2: the menu closes by itself with the pick sound
   (like a game Pixl); your Pixl goes away and after
   half a second a small Frackle (the cannon Fracktail spawns) floats behind
   and above Mario's head, bobbing a little, following him and turning when
   he turns. (Without the rebuilt module the log says `added Pixls need the
   rebuilt native module` and Glock falls back to F7 instead.)
2. Press 1. Glock should move in front of Mario. Walk around; he stays in
   front. Your current Pixl should *not* do anything when you press 1 while
   Glock is out. Idle he sits *behind* Mario's head; armed he is *in front*;
   blasts fly the way Mario faces (these were reversed before).
3. Press 1 again, several times: each press fires a blast (Wrackle cannonball
   model) straight ahead with Squirps' shot sound. A blast that reaches an
   enemy should damage it like a Squirps shot (Goombas die); townsfolk must
   never be hurt. Turn around and fire the other way. In 2D an enemy in the
   line of fire is hit wherever it stands in depth (no lining up needed); in
   3D only what is in the blast's lane.
4. Crouch: Glock goes back behind Mario's head. Press 1 to arm him again.
5. Go through a door: Glock stays your Pixl (no "see you!") and comes back
   in the next room about half a second after it has loaded. Walk into one of
   the buildings where the view turns sideways: he should stay beside Mario
   on screen there too, and his blasts fly along the screen. If he does not,
   stand inside for 5 s and send the `Glock: at (...)` log lines (his and
   Mario's position against the camera, every 3 s).
   In 3D: walk around; armed Glock should stay in front of Mario in the way
   he walks, and blasts fly that way.
6. Open the pause menu and use 1 in it: it should work as normal (Glock
   leaves the 1 button alone while the game is frozen).
7. Pick another Pixl in the menu: Glock disappears and that Pixl is back
   (1 uses it again). Save while Glock is out and reload: no Pixl is
   selected (expected for now); pick one.

Log: `OpenSPM content: Pixl openspm.sample.glock:glock registered (item 538)`,
`OpenSPM content: 1 added Pixl(s) as items 538-538`, `OpenSPM content: Pixl
Glock ... in play`, `Glock: R8PE01 rev 2 checks passed`, `Glock: called.`,
`Glock: blast #1 (right)` ... and at the end `Glock: <n> blasts, <m> hits`.
Tell me: what Glock and the blasts look like (right model? invisible? giant?
facing the wrong way?), whether blasts hit, whether anything crashed or
froze (send the last 50 log lines), and what happens in a 3D flip.

**Francis, a 6th character (new)** (`mods\openspm_francis.dll`). Pick
"Francis" in the character tab (his catch card's icon).
1. He should look like Francis and be big and slow (the log says
   `openspm.sample.francis:francis has its own size / speed: width ...
   (base ...)`, and after the switch `Francis's hitbox in play`). Walk and run
   should use his own animations; jumping will probably just show him
   standing (he has no jump animation).
2. **Find his tongue with F4** (stand still): each press plays the next of
   his own 44 animations and shows its name at the top ("Francis animation
   8/44: A_1B"). Tell me which name(s) show the tongue out, if any. If none
   does, the boss's tongue is a separate object, not part of his body.
3. Crouch (or F5): he stops, opens his mouth, the tongue sound plays, then
   the pull-back sound. Tell me what the animation looks like: does a tongue
   come out, or only his mouth open? Does anything show at all?
4. An enemy up to about two Marios in front of him at his mouth height takes
   a hit (one per lash). With F9 on (Hitbox View) the reach shows as a blue
   box while the tongue is out: tell me whether the hits match the box.
5. Hurt, stylish moves and attacks use his boss sounds instead of Mario's
   voice; tell me if any of them sound wrong.

**Hitbox View (new, debug)** (`mods\openspm_hitbox_view.dll`). In a level
press **F9**: "Hitboxes on", and outlines appear: green around Mario, red
around enemies, yellow around other NPCs. Walk, jump, flip to 3D, go through
a door: the boxes should stay on their owners (3D included). F9 again: gone.
Tell me whether they line up with the sprites (a screenshot with F12 helps),
and whether anything is missing or doubled. The log says `Hitbox View: on`.

**Koopa Striker, a 5th character** (`mods\openspm_striker.dll`). This one
needs the native module rebuilt once (OpenSPM now patches the character menu
to hold 64 entries; the build log shows `[OpenSPM] content code patches:
chartab-list-alloc, chartab-list-clear` and the game log `OpenSPM content
patches: 2 applied to the boot image`). Turn Shell Crouch off for this test.

1. In a level, open the pause menu's character tab. After the game's
   characters there should be one more entry, inside the window, named
   "Koopa Striker" with his description and his catch card's icon (tell me
   what the icon looks like). Scroll up and down through the list.
2. Pick him while playing Mario: the menu closes and Mario should turn into the
   Koopa Striker (the soccer Koopa). Walk, run, jump: he should now use his
   own walk / run / jump animations (the log says `OpenSPM content: Koopa
   Striker: <n> animation names of the base character mapped`). A (flip)
   should do nothing while he is played. Tell me which moves look wrong.
   **New:** he walks and runs a bit slower than Mario (his own speed; the log
   says `OpenSPM content: openspm.sample.striker:koopa_striker has its own
   size / speed`). Switch back to Mario: Mario's normal speed again.
3. Press down while standing: he stops, the set sound plays, he sets a shell
   down in front of him (the way he faces), then slides into it with his kick
   animation and the shell rolls off (the game's own contact kick) and later
   bursts. Try both directions. Four shells out at once is the
   limit ("shells are all out"). No black bars (no cutscene mode): he just
   ignores the d-pad, jump and 1 while it plays, like Bowser breathing fire.
   Tell me whether the kick lands, how the timing looks (he should get
   control back about 1.3 s after pressing), and
   the fps with a few shells out.
4. Open the menu: the cursor should start on him. Pick Peach: Peach, normal.
   From Peach pick him again: the game's own character-change animation,
   then the Koopa Striker.
5. Pick Mario while playing him: back to the real Mario.
6. Go through a door and to the title screen and back into your file while
   playing him: after loading you should be the normal Mario.

Log: `OpenSPM content: character openspm.sample.striker:koopa_striker
registered (roster id 4)`, `OpenSPM content: 1 added character(s); character
list holds 64 entries`, `OpenSPM content: now playing Koopa Striker`, `Striker:
shell #1 (crouch)`, and while he is played `OpenSPM content anim:` lines
(what Mario asks for, the pose's animation index and frame, and once the
model's animation names): send those, they decide how his walk / run / jump
animations get mapped. If the kick does not launch the shell, say so (the kick
uses the Squirps shot's damage on it). Send any `OpenSPM content:` warning, what the entry and
his model look like, and the last 50 log lines after any crash or freeze.

Then with `[Mods] enabled = 0`: none of those lines, and the game runs as
before. Send the `OpenSPM Debug tick=` lines and anything that says
`seq=unavailable`.

## 10. HOME opens the menu, pause, keyboard rebinding (F1 -> Controls)

<!-- launch: label="normal launch" -->
<!-- launch: label="pause + HOME off (step 4)" env=OPENSPM_PAUSE_GAME=0 env=OPENSPM_HOME_MENU=0 -->
<!-- grep: OpenSPM menu|OpenSPM keyboard -->

Menu pause (step 2) already works. Defaults, no OpenSPM.ini changes. In a level:

1. Press Home (keyboard), then the gamepad's Guide / PS button: each opens and
   closes the OpenSPM menu; the game never reacts to it. Log:
   `OpenSPM menu control: HOME (gamepad Guide / [Keyboard] home key) opens the menu`
   and `OpenSPM menu pause: on`.
2. Open the menu while Mario walks or music plays: the picture freezes under
   the menu (it still redraws, the window stays responsive, the mouse works),
   nothing moves, no sound jumps. Wait about 30 seconds, close it (Esc, F1 or
   Home). Healthy = the game continues from the same moment with no fast-
   forward, no skipped frames and no burst of movement. Log:
   `OpenSPM menu: game paused.` then `game resumed after <s> s ... guest time did
   not advance`. Holding a direction while opening the menu: Mario stops.
3. F1 -> Controls: click **Set** next to 2 (jump), press K: K now jumps and
   Enter does not, without restarting; the log shows
   `OpenSPM keyboard (changed in the OpenSPM menu): ...2=K...`. **Add** a
   second key, **Clear** one, then **Default keys**: Enter jumps again. Restart:
   the saved map is used (`OpenSPM keyboard (startup): ...`).
4. `[Overlay] pause_game = 0` and `[Controller] home_opens_menu = 0`, restart:
   the log says `OpenSPM menu pause: off` and `HOME disabled`; Home / Guide do
   nothing; F1 opens the menu and the game keeps running behind it (without
   input).

Send the `OpenSPM menu` / `OpenSPM keyboard` log lines and whether anything
jumped after a long pause. If the menu never pauses, send the line
`OpenSPM menu pause deferred`.

## 11. Widescreen 16:9 (opt-in)

<!-- launch: flags="widescreen" -->
<!-- launch: label="widescreen, borderless" flags="widescreen borderless" -->
<!-- launch: label="without widescreen" env=OPENSPM_WIDESCREEN=0 -->
<!-- grep: M14 SYSCONF|M14 output aspect -->

```
M14-VANILLA-RUN.cmd AUTO widescreen
```

(or `OpenSPM.ini [Display] widescreen = 1`, or F1 -> Display -> Widescreen
16:9 and restart). Use a 16:9 window (F1 -> Display -> Window size 1920x1080)
or borderless fullscreen on a 16:9 display.

The log shows once `M14 SYSCONF: IPL.AR=1 (16:9 TV) published to the SC cache`
and `M14 output aspect: widescreen; the game's anamorphic 16:9 image is shown
at 16:9`. Healthy = the title screen, file select and a level fill the 16:9
window with correct proportions (round coins and heads, not stretched), and
more of the level is visible left and right than without the flag. On a 4:3
window the picture has black bars above and below. Check the pause menu, a
flip (2D -> 3D and back), a star door and a pipe: nothing squeezed, cut off
or misaligned. F1 -> Display -> Stretch fills the window live; 16:9 (black
bars) comes back.

Then without the flag (and `widescreen` empty/0): no `M14 SYSCONF` line, the
usual 4:3 picture. Send an F12 screenshot of the title screen and of a level
in both modes, plus anything that looks wrong.

## 12. Game audio (experimental, opt-in)

<!-- launch: flags="audio" -->
<!-- grep: M14 audio|OpenSPM audio -->

The first run of the AX HLE mixer in the game (docs/AUDIO-PLAN.md, stage 2).
Off by default: without the flag nothing changes and the game stays silent.

```
M14-VANILLA-RUN.cmd AUTO audio
```

(or `[Audio] enabled = 1` in OpenSPM.ini, or F1 -> Audio). Play the title
screen, a level with music, jump/coins/menus for sound effects, and open the
F1 menu once for a few seconds.

Healthy:
- Log, once each, roughly in this order: `M14 audio: enabled (OPENSPM_AUDIO=1)`,
  `first CPU->DSP mail 0x80F3A001` (the DSP task boot), `first AIInitDMA seen
  src=0x00511280 blocks=12`, `AIStartDMA seen`, `SDK shape verified`,
  `renderer output found`, `OpenSPM audio: SDL stream open ...`, `first AX
  command list 0x805108A0 (or 0x80510920) mixed by the HLE rc=0`, `first AI DMA interrupt
  delivered`, `DSPCheckMailFromDSP (0x8028987C) answered 1`, `first DSP
  interrupt delivered ... read=yes`, `first DMA blocks-left read`, and once
  music starts `first non-silent AI buffer`.
- Once per second: `M14 audio: frames=... (+333) cmdlists=...(+333)
  voices_active=N ... underruns=0(+0) queued_ms=35..60 ... list_errors=0
  mem_faults=0`. +333 per second is one AX frame per 3 ms.
- Music and sound effects audible, at the right pitch and speed, no stutter or
  crackle; the game runs as smoothly as without audio (same fps / pacing lines).
- F1 menu open (pause): silence at once; after closing, sound resumes without
  a burst (`resyncs_out` may go up by 1, `underruns` must not).
- F1 -> Audio -> Volume changes the loudness live.

Send the `M14 audio:` / `OpenSPM audio:` lines (the first ~20 and a few per-
second lines from gameplay) and what you heard. How far the chain got is the
last first-time line that appears. Specific failures worth sending:
`__AIDHandler event failed` / `__DSPHandler event failed` (the game stops;
`OPENSPM_AUDIO=0` restores the silent path), `AX command list ... failed`,
`list_errors` or `mem_faults` above 0, `underruns` growing during normal play,
wrong pitch or noise (then also say whether it is music, effects or both).

**Retest (round 2):** after a star door the output stopped for the rest of the
run (`queued_ms=0 playing=0`). A watchdog now reopens the stream after 1 s of
silence with sound pending and logs `OpenSPM audio: output stalled ...`. Go
through two or three star doors and keep playing: do sound effects and music
keep working? Send any `output stalled` / `SDL_...failed` line and a few
`M14 audio voices:` lines from after a door.

## 13. Wii pointer over the game image, right-stick pointer, rumble (F1 -> Controls)

<!-- launch: label="normal launch" -->
<!-- launch: label="4:3 with bars" env=OPENSPM_ASPECT=4:3 -->
<!-- launch: flags="widescreen" -->
<!-- grep: OpenSPM Wii pointer|rumble|OpenSPM gamepad -->

Normal launch. The log shows once `OpenSPM Wii pointer: mouse over the game
image (black bars are off-screen); right-stick pointer available ...; rumble
forwarded to the gamepad ...`.

- Pointer mapping (fix, always on): with `aspect = 4:3` on a 16:9 window (black
  bars left and right), and again with `widescreen = 1` on a 4:3 or 21:9 window,
  Tippi / the pointer sits exactly under the mouse cursor everywhere in the
  picture, including near the edges. Over a black bar the pointer disappears
  (like pointing past the TV). Resize the window, press F11 and switch the
  aspect live in F1 -> Display: the pointer stays under the cursor. Stretch:
  the whole window is the screen, as before.
- Right stick (gamepad): moves the pointer smoothly from where it is; release
  it and the pointer stays; move the mouse and the pointer jumps back to the
  cursor. F1 -> Controls -> Pointer speed / Stick deadzone apply live;
  unticking "Right stick moves the pointer" stops it at once.
- Rumble: F1 -> Controls -> Test rumble buzzes the pad for half a second. In
  game, whatever rumbles the Wii Remote should rumble the pad (the short pulse
  when the pointer moves onto a new entry in menus, plus the game's own
  rumble events); the log shows once `M14 native Wii Remote rumble: the game
  switched the motor on` and `OpenSPM gamepad: rumble active`. Rumble never
  gets stuck: it stops when the F1 menu opens and while the game is paused.
  Strength and the Rumble checkbox apply live.

Send: whether the pointer lines up in each aspect, whether and when the pad
rumbled (and where you expected rumble but got none), and the lines above.

## 14. Fast-forward (turbo, available by default, active only while held)

<!-- launch: label="turbo 2x (FPS on)" env=OPENSPM_SHOW_FPS=1 -->
<!-- launch: label="turbo 4x" env=OPENSPM_SHOW_FPS=1 env=OPENSPM_TURBO_SPEED=4 -->
<!-- launch: label="turbo unlimited" env=OPENSPM_SHOW_FPS=1 env=OPENSPM_TURBO_SPEED=unlimited -->
<!-- launch: label="turbo toggle" env=OPENSPM_SHOW_FPS=1 env=OPENSPM_TURBO_MODE=toggle -->
<!-- launch: label="turbo off" env=OPENSPM_TURBO=0 -->
<!-- grep: OpenSPM turbo|M14 present timeline -->

Defaults, no OpenSPM.ini changes (the per-player module relinks for this
build: its idle wait learned the "unlimited" skip). F1 -> Debug -> FPS counter
on. In a level with music (`audio` flag if you test sound too):

1. Log at start: `OpenSPM turbo: ready (hold the hotkey: 2x speed).` Walk
   normally: nothing differs from before (same fps / pacing lines, no
   `OpenSPM turbo` lines).
2. Hold **Tab** for ~10 s while Mario walks: the game runs twice as fast
   (walk, animations, music timing), "Turbo 2x" shows under the FPS counter,
   the counter reads ~120 fps, the picture stays smooth (no tearing), sound is
   muted. Release: normal speed at once, no jump, no burst, no frozen frame;
   sound returns within a fraction of a second. Log: `OpenSPM turbo #1: 2x.`,
   `OpenSPM turbo presentation: at most one image per display refresh ...`,
   then `OpenSPM turbo #1 off after 10.0 s: ~1200 game frames (120/s), ~20 s
   of game time`.
3. Gamepad: hold the right stick click: same as Tab.
4. `[Game] turbo_speed = 4`, then `unlimited`, restart: 4x (~240 fps) and as
   fast as the PC can (log `unlimited`, fps well above 240 on a fast PC). The
   play-time counter on the file select advances with game time (faster in
   turbo) and never jumps back.
5. `[Game] turbo_mode = toggle`, restart: one Tab press starts turbo, the next
   stops it. Open the menu (F1 / Home) during turbo: the game pauses and turbo
   is released; after closing it runs at normal speed.
6. F1 -> Controls: **Set** 2 (jump) to Tab: Tab jumps and no longer fast-
   forwards (the turbo row shows `none`); **Default keys** restores Tab = turbo.
7. `[Display] frame_mode = smooth` on a >60 Hz display: turbo shows the newest
   game frame (no interpolation); after release, Smooth motion is back within
   a second (present timeline lines show one re-anchor, no stall).
8. `[Game] turbo = 0`, restart: `OpenSPM turbo: off ([Game] turbo=0).`, Tab does
   nothing.

Send the `OpenSPM turbo` lines, the fps during turbo per speed, and anything
that jumped, stuttered or stayed fast after releasing the key.

## 15. Mario drawn behind a shopkeeper while talking

<!-- launch: label="normal launch" -->
<!-- analyze: --passes --limit 40 > talk.txt -->
<!-- grep: M14 F8 GX capture -->

In gameplay Mario is in front of the shopkeeper; while you talk to him Mario
is drawn behind him. Talk to the shopkeeper and, while the dialogue is on
screen, press **F8** once (GX frame capture). Close the game, then click
**Analyze log -> talk.txt** and send it (plus an F12 screenshot of the scene).

## 16. Content patches: item table moved, bigger menu lists (do this first)

<!-- launch: label="normal launch" -->
<!-- grep: content code patches|OpenSPM content patches|OpenSPM content -->

OpenSPM now patches 90 single instructions in your own copy of the game when
the native module is built (the first launch after pulling recompiles about 90
functions, so it takes a little longer) and moves the item table to spare
memory at boot. The game should play exactly as before. Mods off (default).

1. Launch. The build output shows `[OpenSPM] content code patches:` followed
   by 90 names (80 fixed + 10 found in relF); the log shows `OpenSPM content patches: 80 applied to the boot
   image; item table relocated to MEM2`.
2. Play for ten minutes, touching items as much as you can: open the pause
   menu's Items, Pixls and Characters tabs (names, icons, descriptions right?),
   use a mushroom, pick up items and a catch card, buy something in a shop and
   check the price, sell something if you can, go through a door.
3. Anything wrong with an item name, icon, price or description, a crash, or a
   log line `this main.dol is not R8PE01 rev 2` means the patches misbehave:
   send the log and stop there (tests 9's Koopa Striker needs this one to
   pass).

