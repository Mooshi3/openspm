# M14 resume entry point

## CURRENT STATE (2026-09-26) — read this first

Docs index: `docs/README.md`; in-game tests waiting for a run:
`docs/TEST-LIST.md`. Confirmed in game on 2026-09-25: both content mods
(Glock Pixl, Koopa Striker character) show and select in the menus, Glock's
2D facing, shots and Z-ignoring hits, map persistence, Striker's animations,
synced kick, voice and icon, sound effects no longer dying (AX HLE loop
address fix), about 58 fps in play.

Waiting for a run, in this order:

1. **First hook in game**: enable `spsndSFXOn` in `state/openspm-hooks.json`
   (docs/HOOKS.md "Turning a hook on"; checked offline, all 348 calls
   reroute) and run with the SFX Log mod: `SFX Log: SFX_...` lines.
2. **Glock in 3D** (turns to Mario's forward via NPC rotation +0xB8; the sign
   may need flipping) and **in buildings** (`Glock: at (...)` lines).
3. **Striker's set animation** (settle wait and double pose set).
4. **Shake** (Q / RB): stylish moves, and the Fire Burst / Ice Storm gauges.
   The game never reads a steady tilt (every accelerometer reader traced), so
   there is no tilt control.

Built since, waiting on step 1: hooks v2 (floating-point arguments and
results, post callbacks through a return address) and the characters v2 /
Pixl entries in the hook registry, all off (docs/HOOKS.md "First hooks").
Next work: characters v2 on those hooks (own model, hitbox and speed first),
the scene-dependent game-update time (4.4 to 7 ms), a quieter default log.
GitHub Actions minutes are used up: tests run locally (cmake + ctest, and
the tools/native_bridge Python tests).

## OVERNIGHT SUMMARY (2026-09-23/24)

`git pull`, then rebuild with the usual launcher (the first build re-patches
your Aurora cache in place; no wipe needed). Note: your first launch still runs
the old `M14-VANILLA-RUN.cmd` while it pulls the new one. If the console window
does something odd after the game closes, just close it. It's now fixed for
later runs (call + exit on one line). Test in this order:

1. **Star doors** (`M14-VANILLA-RUN.cmd AUTO fullscreen`):
   * the right half should swing too (86-wide loads of 88-wide captures are
     now accepted and cropped);
   * the swinging copy should sit exactly on the original, with no sliver above
     the void (pixel-exact copy rect; off: `set OPENSPM_M14_COPY_PIXEL_EXACT=0`).
2. **Flip diagnostic** (`M14-VANILLA-RUN.cmd AUTO fullscreen flipcapture`):
   flip 2-3 times, press **F12** during a flip (screenshot), send `launch.log`
   and the PNG. The run logs 3 full GX frame captures (draw/TEV/blend/depth/
   texture/copy state) plus a bbox read/write trace. The log will be big.
3. **Hotkeys:** F11 toggles windowed <-> borderless fullscreen
   (`OPENSPM_FULLSCREEN_HOTKEY=0` disables). F12 saves
   `screenshots\OpenSPM-<date>-<time>.png`; the log prints the full path.
   The mouse cursor hides in fullscreen (`OPENSPM_HIDE_CURSOR=0` keeps it).
4. **OpenSPM menu (preview): press F1.** An ImGui window with tabs:
   * Graphics: windowed/borderless now, 4:3/stretch live, "Save to OpenSPM.ini"
     for the startup window mode and aspect;
   * Performance: fps, frame-time graph, display-locked clock;
   * Debug: FPS counter toggle, screenshot, GX capture;
   * About.
   The game still takes controller input while it's open (pausing comes with
   the real menu). A **Game** tab shows read-only live state (sequence/stage,
   game frame-rate setting, Mario position). Check the numbers change as you
   move. `OPENSPM_OVERLAY=0` disables F1. Tell me how it looks (font
   size, position) and whether it survives fullscreen toggles.
5. **Gamepad (new):** plug in an Xbox-style controller (others through Steam
   Input). D-pad/left stick move, and by default A=2 (jump), X=1, Y=A, B=B,
   Start=+, Back=-. The keyboard still works alongside it. The log shows
   `OpenSPM gamepad: XInput controller 0 connected`. Remap in OpenSPM.ini
   `[Controller] map = a=2,x=1,...` (tell me which layout feels right for SPM).
   `OPENSPM_PAD=0` disables it.
   The keyboard now only counts while the OpenSPM window is focused, so typing
   elsewhere no longer moves Mario (`OPENSPM_INPUT_BACKGROUND=1` = old behaviour).
6. **Settings file (optional):** copy `OpenSPM.ini.example` to `OpenSPM.ini`
   next to the .cmd files, set e.g. `window_mode = exclusive`, run
   `M14-VANILLA-RUN.cmd AUTO` with no args. The log shows
   `OpenSPM.ini [Display] window_mode=exclusive -> OPENSPM_WINDOW_MODE`.
   Launcher args override it. No ini = the same defaults as before.
7. **bbox fix:** GX-side bbox resets were stored unscaled (the `454,0,454,0`
   reads); now converted like CPU writes. Pipes should still work. Watch for
   any effect that changed (`OPENSPM_M14_BBOX_LOGICAL=0` = old behaviour).
8. **Perf:** the 35-50 fps stretches (disp 10-17 ms) also appear in runs from
   before the copy tags, so the tags didn't cause them. The new per-second
   `M14 renderer budget` line splits renderer time from the game's own time.
   Tell me where you were when it slowed down. (`python tools/diag/summarize_perf.py
   launch.log` prints the per-second table and the slow stretches.)
9. **Audio groundwork (doc only, nothing to test):** `docs/AUDIO-PLAN.md` maps
   the game's AX/AI/DSP path (AX output init 0x8028720C, AI callback 0x80287004,
   AIInitDMA 0x80284FE8, DSP mailbox 0x80289510...) and proposes an HLE that
   keeps all of the game's own sound code running. Next step would be opt-in
   instrumentation.
10. **Interpolation groundwork (doc only):** `docs/PRESENTATION-ARCHITECTURE.md`
    now maps SPM's display list (`dispDraw` 0x8005B028: entry layout, globals,
    callback dispatch at 0x8005B29C) and how the bridge can tag each draw with
    its owner in stream order through an unused BP register. That's the basis
    for object-correct interpolation above 60 Hz.
11. **Modding doc (doc only):** `docs/MODDING.md` describes the existing mod
    ABI (DLLs in `mods/`, `openspm_mod_query`, lifecycle/tick/present events,
    the `openspm.runtime` service, `[Mods]` toggles). The key gap: the M14 native
    runner doesn't load mods yet. The doc also has a roadmap (native loading,
    game-state service, function hooks, asset overrides, Mods tab).
12. **Mods in the native runner (opt-in, off by default):** set
    `[Mods] enabled = 1` in OpenSPM.ini (or `set OPENSPM_MODS=1`), create a
    `mods` folder next to `OpenSPMNative.exe` in the build folder, and copy
    `openspm_debug.dll` (built next to it) into it. The log should show
    `Mods: 1 loaded` plus the debug mod's own lines. Without the setting nothing
    changes.

## 2026-09-23 — player test of EFB copy tags (8390df8a) + next steps

Result: star doors swing at full speed now. Remaining:
* **Right door half missing.** Log: `copy tag shape mismatch tag=88x144/fmt=5
  load=86x144/fmt=5` (hundreds of these). Same GX tile layout (22 tiles per row),
  so the 86-wide load is the left 86 columns of the 88-wide capture. Fix: accept a
  tag when the format and height match and ceil(w/4) (ceil(h/4)) match, then
  freeze a cropped copy (dst = round(load/tag * host size), copy from origin).
  `tag=6x12 load=608x480` is a small copy at the start of a big buffer; keep
  rejecting that.
* **Door offset:** the swinging copy sits slightly down-right of the original,
  so a sliver of the original door shows above the black void. Suspect rounding
  in `map_logical_scissor` / `scale_copy_dst` (copy src rect vs. draw quad at
  2.25x), or the 88-vs-86 size mismatch. Check the scaled rect math.
* **Flip still a black square.** The player notes the black square seems drawn
  *in front of* the flip card border rather than inside it, and it should show
  Mario plus the background. Suspect EFB alpha or draw order / depth.
Player (overnight): prepare renderer fixes for testing; when renderer work
needs a player test, move on to non-intrusive, toggleable QoL/features.

## 2026-09-23 — EFB copy tags: star doors at full resolution without GPU readback

Player run 30c19795 (`efbram`): doors animated for the first time, which
confirmed the RAM theory, but the flush cost ~10-12 ms per DrawDone (it waits
for the GPU to finish the whole frame: done phase 21 ms, frames at 33 ms) and the
door looked jagged (logical-resolution RAM copy). Flip still black (its copy
is an exact bind hit, so it's a separate bug).

Game code (R8PE01 rev 2): GXCopyTex = 0x8028EBA8 (19 call sites; each followed
by GXPixModeSync 0x8028D8DC). Capture system 0x80034BB0 (84-byte entries,
tex obj +32, image +72) copies screen rects. The effect snapshot routine
0x800765D8 (352-byte owners x 3 sub-entries of 68 bytes) allocates a buffer and
**memcpys** a capture on the CPU (0x800766C8 -> memcpy 0x80004000, then
DCFlushRange 0x8026DFA0). The generic rect-capture helper is 0x800764A8. The
door's never-copied "slot 3" is that snapshot.

Fix (`apply_efb_copy_ram_writeback_patch.py` rev 3, default on,
`OPENSPM_M14_COPY_TAGS=0` / launcher `notags` disables): each texture copy
stamps a 32-byte tag (magic, copy id, fmt, logical size, check) into its guest
RAM, and the CPU memcpy carries it along. `resolve_sampled_textures` falls back
to `m14_resolve_tagged_copy` before decoding untracked RAM. A valid,
same-shape tag resolves to a frozen in-stream GPU copy of the tagged host
texture (encoder task CopyTextureToTexture), falling back to aliasing if the
source lacks CopySrc. Registry is bounded (512 tags, 128 frozen). RAM write-back
stays opt-in as a CPU-pixel diagnostic. Migrates v1/v2 in place; checker:
fresh + re-configure + from-v1 + from-v2 all compile and link.

Player test: `M14-VANILLA-RUN.cmd AUTO fullscreen` (no efbram), star door;
log lines `M14 EFB copy tag resolved ... frozen`.

## 2026-09-23 — EFB RAM write-back link fix (LNK2019) + link-level checker

Player build failed to link: regs.cpp declared the write-back hook inside
`aurora::gx::fifo`'s anonymous namespace, so it could never resolve to the
GXFrameBuffer.cpp definition. The hook is now public API
(`aurora::gx::raw::note_efb_copy_writeback` in `gx_raw.hpp`). The patch migrates a cache
already carrying the broken v1 layout in place, so no cache wipe is needed.
`check_renderer_compile.py` now compiles objects and checks symbols: undefined
anonymous-namespace or OpenSPM-named symbols must be defined by a compiled
object. It reproduces the exact v1 error. `--pass1 SCRIPT=PATH` tests migration
from an older patch version (v1 -> current: chain passes, 20/20 compile, links).

## 2026-09-23 — copy trace result: star doors consume RAM the GPU never writes; EFB->RAM write-back ready

Trace (67537160f048, flip + star door). GX fmt 5=RGB5A3, 6=RGBA8.
* Star door: per door the game captures left 88x144, right 86x144 and full
  168x144 in RGB5A3 and RGBA8. RGB5A3 captures sit back-to-back in one buffer
  (slot stride 0x6300 = 25344 = one 88x144 RGB5A3 image). The game then binds
  slot 2 and **slot 3 (B+0x6300)**; slot 3 is never a copy destination in all
  three door instances -> sampled from guest RAM that EFB copies never write.
  Slot 1 is copied but never bound. Consistent with CPU code deriving slot 3
  from copied pixels in RAM (Dolphin: "Store EFB Copies to Texture Only" off).
* Flip: Mario's 72x88 RGBA8 capture is found by exact address and drawn with
  blending off, yet shows black -> content/RAM-consumption issue, not lookup.
* Checked and ruled out: copy-dest 21-bit mask (already widened to 24 bits by
  apply_gx_audit_fixes_patch.py). Shutdown `Native session run failed` is the
  normal window-close path.

New opt-in `apply_efb_copy_ram_writeback_patch.py` (`OPENSPM_M14_EFB_COPY_RAM=1`,
launcher arg `efbram`): records RGBA8/RGB5A3 texture copies in the parser; at
DrawDone (outside the parser, before the paused game resumes) the renderer
splits the frame like a bbox read, reads the copies back on the render worker,
downsamples to logical size, encodes GX 4x4 tiles and writes guest RAM via the
bounded mapper. Host copy textures stay authoritative for exact loads.
Verified: 21-patch chain fresh + re-configure, 20/20 TUs compile, encoders
round-trip through Aurora's own texture_convert decoders (RGBA8 exact, RGB5A3
within format quantization).

Player test: `M14-VANILLA-RUN.cmd AUTO fullscreen efbram` (set
`OPENSPM_M14_EFB_COPY_RAM=1` first in case the launcher self-updates); flip,
star door. Look for `M14 EFB copy RAM write-back active` and the stats line;
note any frame-time cost during the effects.

## 2026-09-23 — configure blocker from copy trace fixed (in-place re-configure)

Player build of 8999445 failed at configure: `Aurora copy-ownership rollback
failed: copy publication: neither experiment nor baseline source shape found`.
The rollback companion (run from apply_fifo_reentrancy_patch.py on every
configure, before the trace patch) pins two exact blocks - the GXCopyTex
publication in GXFrameBuffer.cpp and the copy resolver in texture.cpp - and the
trace had inserted calls inside both. The first build passed only because the
trace was applied after the check; the player's Aurora cache is patched in
place, so the next configure saw the trace inside the pinned blocks.

Fix: trace calls now sit outside both pinned blocks; the rollback normalises
the old layout (moves the publication call, drops the old bind line) and the
trace re-inserts its hook, so the existing player cache heals itself.
`check_renderer_compile.py` now also re-runs the configure chain over the
already-patched tree (the real player flow) - it reproduced this exact error
before the fix. Verified: fresh + re-configure pass; simulated player cache
(8999445 trace) -> two new configure passes -> one call each, compiles.
No cache wipe needed; same copytrace test as before.

## 2026-09-23 — first copytrace run captured nothing (stale self-updating launcher); trace v2

`launch.log` on ad3ee8112c3c has zero `copy trace` lines although display
copies go through the traced copy_tex. Cause: the player started the pre-pull
M14-VANILLA-RUN.cmd, which did not yet understand `copytrace`; the update run
then pulled/built the trace but it stayed disabled. Re-run now that the
launcher on disk is current (or `set OPENSPM_M14_COPY_TRACE=1` first).

Also noted: 55 `provisional GX_LO_OR fallback` warnings (Max approximation of a
bitwise logic op), emitted at pipeline creation, mostly during boot.
Trace v2 therefore records blend mode/factors/logic op on every copy bind so
the flip-card/door draws show whether they rely on a logic op. The patch
migrates an in-place-patched v1 cache (tested: v1 -> migrated -> idempotent,
migrated texture.cpp compiles); renderer checker 20/20.

## 2026-09-23 — bbox fix: pipe effect FIXED; flip card + star doors unchanged; copy trace ready

Player (c64aa9b): pipe enter/exit chunk effect now works windowed and
fullscreen. Unchanged: 2D/3D flip (black square instead of Mario's captured
sprite) and the rainbow/red star doors (no swing). So bbox scaling was one
cause, and flip/doors fail elsewhere in the capture path: Aurora keeps each
GXCopyTex result as a host texture keyed by the exact destination pointer and
binds it only on an exact-address texture load; anything else samples guest
RAM, which EFB copies never write. Candidates: offset/format-mismatched loads
of the copy buffer, CPU/DMA consumption of copied pixels, or copy alpha/format.

New opt-in trace `apply_copy_trace_diag_patch.py` (configure chain, after bbox
logical): `OPENSPM_M14_COPY_TRACE=1` logs each unique EFB copy (dest, logical
src rect, dst/scaled size, format, pixel format, alpha/dst alpha, clear) and
each sampled-texture bind that hits a copy or lands inside one at an offset
(`bind=offset-miss`). Off by default, capped at 4096 lines, no behavior change.

New `renderer/aurora/check_renderer_compile.py --cache <dir outside repo>`:
fetches pinned Aurora + header deps (git + Dawn prebuilt headers), applies the
full CMake-order chain and syntax-compiles every modified Aurora file plus the
OpenSPM adapter. Current result: 20/20 pass after 20 chain patches (first real
compile of the pacing/window/aspect/bbox work).

Player test: `M14-VANILLA-RUN.cmd AUTO fullscreen copytrace`; flip once, walk
through one star door, exit; send launch.log.

## 2026-09-23 — 4:3 pillarbox PASSED; bbox logical-coordinate fix for flip/door/pipe effects

Player: "smooth as hell", sharp/HD-looking at full-height native resolution;
no need for asset upscaling. Long-standing (not regressions) missing effects:
2D/3D flip shows a black square instead of Mario's captured sprite; doors do
not swing (edges occasionally visible); pipe enter/exit "chunk" effect absent.

Diagnosis: these effects draw an object, read the PE bounding box, and EFB-copy
that screen rectangle into a texture. Aurora's EFB follows the window (e.g.
1440x1080) while the bbox shader returned raw EFB pixels clamped at 1023, so
the game received 2.25x+ scaled boxes and copied the wrong region.

Fix: new configure-time `apply_bbox_logical_scale_patch.py` (after bbox
priority). GPU coverage stays in EFB pixels; raw.cpp converts at the game
boundary with the same scale as map_logical_viewport: writes logical->pixel
(min ceil(v*s), max ceil((v+1)*s)-1), reads floor(px/s) clamped 1023; pixel
clamp/mask/reset raised to 16383. Identical values at a 640x480 EFB. Tested:
full 19-patch chain applies to pinned Aurora and re-applies idempotently; the
generated raw.cpp code compiled against Aurora's real math.hpp with stubbed GPU
calls; reset/round-trip/rasterized-box checks at 1x, 2.25x, 4.5x
(box 100..199x50..149 reads 99..199x49..149 at 2.25x; raw would be ~224..449).

Player test (renderer rebuild): `M14-VANILLA-RUN.cmd AUTO fullscreen`, then flip,
open a door, use a pipe. A/B: set `OPENSPM_M14_BBOX_LOGICAL=0` first. If an
effect is still missing even in a 640x480 window, it is a separate EFB-copy
issue (see renderer/aurora/README.md copy-rectangle caveat), not bbox.

## 2026-09-23 — fullscreen PASSED at 100 Hz desktop; 4:3 pillarbox fix ready

Player: `AUTO fullscreen` runs "real smooth" with the desktop left at 100 Hz;
borderless works. Both stretched the game across 2560x1080: Aurora sizes its
EFB to the window's pixels unless a viewport policy is applied, so the 640x480
game space was drawn 4.0x wide by 2.25x tall.

Fix: renderer init applies `AuroraSetViewportPolicy(AURORA_VIEWPORT_FIT)`
(Aurora's own aspect-fit path): EFB = 4:3 at full height (1440x1080 in
2560x1080) and the existing present viewport centres it with black bars.
`OPENSPM_ASPECT=stretch` restores the old behaviour. Player decision: native
ultrawide stays a later Graphics toggle (needs clamping for off-view areas).

Test (renderer rebuild): `M14-VANILLA-RUN.cmd AUTO fullscreen` and `borderless`.
Expect `M14 output aspect: 4:3 fit` and black bars left/right, image not
stretched. Also check the 2D/3D flip still behaves (see bbox note in
docs/PRESENTATION-ARCHITECTURE.md: bbox coordinates are still raw EFB pixels
in any window larger than 640x480, pre-existing).

## 2026-09-23 — exclusive fullscreen auto-selects a vanilla-60 refresh; player test needed

Players should not have to change their Windows refresh rate to get the
judder-free Vanilla 60 result. New renderer settings (env today, OpenSPM menu
later): `OPENSPM_WINDOW_MODE=windowed|borderless|exclusive` (default
windowed, unchanged) and `OPENSPM_REFRESH_HZ=auto|<Hz>`.

Exclusive fullscreen keeps the desktop resolution and in `auto` switches to
the highest refresh vanilla 60 locks to (LG ultrawide: 59.978 Hz from a
99.943 Hz desktop); the display lock then reads that mode. No compatible mode
-> desktop refresh kept, logged. Exclusive failure -> borderless fallback,
logged. SDL restores the desktop mode on exit. Policy unit-tested; glue
exercised against SDL 3.4.10 headers with a fake display (host + mingw).

Player test (renderer rebuild only), desktop left at **99.943 Hz**:
`M14-VANILLA-RUN.cmd AUTO fullscreen`. Expect
`M14 window: exclusive fullscreen 2560x1080 @ 59.978 Hz.` then
`M14 vanilla pacing: display-locked ...`; it should feel identical to the
judder-free 59.978 Hz desktop run, and the desktop returns to 99.943 Hz on exit.
Also sanity-check `M14-VANILLA-RUN.cmd AUTO borderless`.

## 2026-09-23 — Vanilla 60 PASSED: "didn't notice a single judder" (1f46b525)

Player: best version played; no noticed judder across walking, a 2D/3D flip
and a room change at 59.978 Hz. Log confirms display lock plus
`finish_holds=64 hold_expired=3 hold_max_us=3966`: the DrawDone->retrace race
fired ~once per 4 s and is now absorbed. Steady windows are 60/60 frames with
disp ~5.5 ms + main ~5.5 ms. Present p50 17.0 / p95 17.5 / p99 20.0 ms.

Remaining 33 ms frames are real work overruns, clustered in two windows
(retraces ~6500-6700 and ~9350-9600, 53 and 46 fps seconds), where the
**disp** phase spikes to 12-14 ms (vs ~5 ms) with main ~4-5 ms. These match
the player's flip / room change. Next performance target: disp-phase cost
during flips/transitions (profile disp callbacks there), not pacing.

Presentation next steps (docs/PRESENTATION-ARCHITECTURE.md): exclusive
fullscreen with refresh picker (no Windows refresh change needed), present
phase lock, then interpolated >60 Hz modes on top of this baseline.

## 2026-09-23 — 8f4a114 ordering hook never fired; corrected mechanism + bounded hold

Both 8f4a1149 runs (default and `oldorder`) report `finish_before_retrace=0`:
at the due retraces no thread was still waiting on FinishQueue, so that hook
could not change anything and the perceived A/B difference was noise/route.
Tails persist with the same signature (21 of 26 in the default run: work
11-14 ms, total 33.3 ms, busy-due at scheduler idle in DoneRender).

Retail DEMODoneRender (0x801A8B64, read from the player's local generated
tree) is GXCopyDisp -> GXDrawDone -> VISetNextFrameBuffer -> VIFlush ->
VIWaitForRetrace, and VIWaitForRetrace waits for retraceCount to *change*.
Corrected race: the serial DrawDone parse crosses the deadline, dev47 wakes the
main thread, and the next service delivers the due retrace while that thread
is runnable at idle but has not yet reached VIWaitForRetrace (dev68 shows
runq=0x00008000 in this state). The count bump is consumed early -> 33 ms.

Fix: after a finish completion, a due retrace at scheduler idle with runnable
threads is held until the woken thread parks again, bounded at 4 ms; the
deadline grid is unchanged. Shutdown stats add `finish_holds`,
`hold_expired`, `hold_max_us`. Same toggle / launcher A/B (`oldorder`).

New `tools/native_bridge/check_bridge_compile.py` syntax-compiles the whole
active LLVM bridge chain (host gcc + mingw for _WIN32) against a private local
generated tree + DolRecomp pin before any player build. Passes on this change.

Player test: `M14-VANILLA-RUN.cmd` at 59.978 Hz. Gate: `finish_holds` > 0 and
steady-gameplay 33 ms tails with work <16 ms largely gone.

## 2026-09-23 — locked-run residual skips = DrawDone/retrace ordering bug; fix ready for test

Locked-run log `launch.previous.log` (e527ce207a6c) confirms the display lock
(`period=16672780ns`). Of 20 frame tails, 15 are **not** over budget: game work
(before+disp+main) was 11-14 ms, yet the frame took exactly 33.3 ms. Each such
tail coincides 1:1 with a `dev128 busy-due owner` at `pc=0x8027551C` (scheduler
idle) in `phase=3(done)`, 2.5-5 ms after DrawDone began.

Mechanism: the GXDrawDone host fence (Aurora parse, ~2.5 ms) completes
synchronously, the main thread sleeps on FinishQueue, but dev47 delivers the
PE-finish interrupt only when the outer run pass returns. Inner passes can
deliver a due VI retrace first; the game consumes it while still waiting for
DrawDone, then VIWaitForRetrace sleeps an extra refresh. Hardware would raise
PE-finish first. Remaining 5 tails are real work overruns (17-21 ms) or loads.

Fix (bridge v7 + v37): at a due retrace at scheduler idle, deliver an already
completed finish first (same verified dev47 FinishQueue/handler contract) and
hold the retrace for the next idle service; the deadline grid is unchanged.
Toggle: `OPENSPM_M14_FINISH_BEFORE_RETRACE=0`.

Player test (bridge rebuild only): `M14-VANILLA-RUN.cmd` at 59.978 Hz, then
control `M14-VANILLA-RUN.cmd AUTO oldorder`. Expect
`M14 GPU-finish-before-retrace ordering active` once and
`finish_before_retrace=N` in the shutdown stats. Gate: steady-gameplay
33 ms `dev122 frame tail` lines with work <16 ms should mostly disappear.

## 2026-09-23 — display-locked Vanilla 60 player test PASSED (59.978 Hz desktop)

Revision `e527ce207a6c`, desktop set to 59.978 Hz. Player report:
- `M14-VANILLA-RUN.cmd` (locked clock): "looks pretty great"; roughly two
  skipped frames across the whole Yold walk.
- `M14-VANILLA-RUN.cmd AUTO nosync` (60.000 Hz clock): fewer judders than
  60-on-100 Hz, but clearly more than the locked run.

Only the nosync log was captured. It shows 60.000 Hz on 59.978 Hz (expected
~1 collision / 45 s from clock mismatch) plus separate producer-late frames:
around presents 651-661 consecutive `dev122 frame tail` ~33 ms frames
(disp 5-8 ms + main 5-12 ms) miss the refresh. Locked-run residual skips are
most likely those producer tails, not clock drift (period error should be
<~1 slip / several minutes). Needs the locked-run log to confirm: frame tail
before a skip = producer cost; present tail alone = drift/queue -> phase lock.

Vanilla 60 display lock is the accepted default presentation path.

## 2026-09-23 — presentation reset: display-locked Vanilla 60 (default); player test needed

Direction change: see [presentation architecture](docs/PRESENTATION-ARCHITECTURE.md).
Default frame-rate mode is **Vanilla 60**; higher rates become opt-in interpolated
modes. The long-standing judder is 60 game frames on a 99.943 Hz panel (2,2,1
refresh holds) plus a free-running 60.000 Hz clock that never syncs to the
display. The dev132 49.97 Hz run felt best because it was exactly half the panel.

Implemented: after the window exists the renderer reads the display's exact
refresh and, when vanilla 60 fits it (59.94/59.978/60, 119.88/120, 240 Hz...),
publishes `OPENSPM_M14_RETRACE_PERIOD_NS` so the game clock runs at exactly
display/k (59.978 Hz = 0.037% slow). Otherwise it stays 60.000 Hz and logs the
offered modes that would be smooth. Interpolated/async runs are untouched.

Player test (no cache wipe; renderer + bridge rebuild):
1. Windows display refresh -> **59.978 Hz**.
2. `M14-VANILLA-RUN.cmd`, walk the usual Yold camera route.
3. Control: `M14-VANILLA-RUN.cmd AUTO nosync` (same, fixed 60.000 Hz clock).
Expect `M14 display: 2560x1080 @ 59.978 Hz ...`, `M14 vanilla pacing:
display-locked ...` and `display-locked vanilla clock: period=16672780ns`.
Gate: camera scrolling should look console-smooth in the locked run. Present
p95/p99 should sit near 16.7 ms. Also note any occasional single hitch
(phase drift; next step is a present-timestamp phase lock).

dev145 interpolation remains available via `M14-LLVM-INTERPOLATED-RUN.cmd`, but
the architecture doc explains why its timing model and camera-only patching
cannot be judder-free; the next interpolation work follows that design.

## 2026-09-23 — dev145 world-camera replay candidate; player test needed

Player capture `launch(20260923-003829).log` on `cc884b38d23b` proved
dev144's projected camera displacement was nonzero and the 100-Hz presenter
remained healthy, but visually the entire game image jittered. That result is
expected from the architecture: dev144 translated the completed 640x480 XFB,
so HUD/text/screen-space geometry moved together with the world.

dev145 removes final-XFB translation entirely and moves interpolation back
inside scene replay, but without returning to unsafe per-draw object matching:

- the robust matched-draw consensus is used only to infer one global camera
  translation and one dominant moving world projection;
- each source frame records projection metadata even for indexed-matrix GX
  draws, so indexed/composite geometry can participate in camera replay;
- for every uniform arena whose projection exactly matches the dominant world
  projection, **all ten GX position matrices** receive the same camera delta;
- object-local/current transforms are otherwise untouched, so Mario parts,
  doors and other composites remain rigid relative to one another;
- draws using other projections (HUD, menus and other screen-space passes) are
  replayed unchanged;
- the whole completed XFB is never shifted;
- normal mode publishes these camera-only replay phases to the existing 100-Hz
  presenter; legacy identity mode retains the older diagnostic path.

This is the renderer-level form of literal interpolation: source poses A/B stay
authoritative, while extra frames redraw the scene at intermediate camera
positions. It is correctness-first. Full-scene replay cost may again pressure
source FPS; if the visual model is correct, the next optimization target is
reusing/isolating world rendering rather than weakening camera identity.

Next command: **`M14-LLVM-INTERPOLATED-RUN.cmd`**, no cache wipe. Expect
`M14 dev145 world-camera replay interpolation active` and
`M14 dev145 world-camera replay interval=...`.
Primary gates: the completed game image/HUD no longer wobbles, Mario/doors stay
attached, and world camera scrolling becomes smoother.

## 2026-09-23 — dev144 cached-Aurora declaration migration fix; rebuild needed

First dev144 player attempt on `035a679c8790` did not reach runtime. Configure
completed, but `aurora.cpp` failed with C2660 because the retained-present call
was migrated to
`m14_presenter_viewport_adjustment(image.size.width, image.size.height)`
while a cached dev143 forward declaration still exposed the zero-argument
`m14_presenter_viewport_adjustment() noexcept`.

Root cause: the async-presenter patch only inserted the viewport declaration
when `struct M14PresenterViewportAdjustment` was absent. Cached dev143 Aurora
already had that struct, so dev144's new sized declaration was never migrated.

Fix in `cebd9250a863`:
- canonicalize the viewport forward declaration independently of the struct
  insertion guard;
- replace exactly one stale zero-argument declaration with the dev144
  `(uint32_t sourceWidth, uint32_t sourceHeight)` signature;
- fail closed if neither the stale nor canonical declaration is present.

Next command: **`M14-LLVM-INTERPOLATED-RUN.cmd`**. No cache wipe is needed;
the normal configure step should migrate the cached Aurora source before build.
Runtime behavior of dev144 remains unverified until this build succeeds.

## 2026-09-22 — dev144 literal projected camera interpolation; player test needed

Player capture `launch(20260922-231827).log` on `4363bef7b44d` proved the
dev143 rigid-XFB path solved structural interpolation corruption: doors and
Mario remain attached. The player still reports severe camera judder.

The important simplification is that the 100-Hz presenter itself is healthy.
Periodic pacing stays around 10 ms (p95/p99 ~11/11.5 ms) even during gameplay,
so the defect is not lack of host presents. The old dev143 path was producing
many presents of an authoritative XFB with a correction derived directly from
raw GX view/model translation units. That is not the same coordinate space as
retained-image pixels.

dev144 now implements the literal interpolation model:
- source A/B frames remain authoritative;
- matched static-scene transforms are still used only as a camera-motion
  sensor, never for object-piece interpolation;
- each matched transform origin is projected through its exact GX projection
  matrix to NDC for both A and B;
- the robust world-space camera consensus selects static-scene candidates;
  the median projected NDC displacement of those consensus candidates becomes
  the camera's actual screen-space A->B delta;
- every 100-Hz presenter tick computes wall-time alpha and displays the rigid
  current XFB at the corresponding in-between screen position;
- NDC displacement is converted using half the retained XFB width/height,
  giving subpixel framebuffer offsets rather than treating Wii view units as
  pixels;
- full-scene replay/object interpolation remains disabled in normal mode.

This is the direct form of interpolation: if a real source camera step moves
one screen unit from A to B, the additional host presents display fractional
positions between A and B. At 60->100 Hz a constant-speed camera should advance
about 0.6 of a 60-Hz step per 10-ms host present.

Next command: **`M14-LLVM-INTERPOLATED-RUN.cmd`**, same Yold camera route.
No cache wipe. Expect:
`M14 dev144 literal camera interpolation active`
plus sparse
`M14 dev144 camera present ... pixel_x=... pixel_y=...`
and prep telemetry with
`screen_valid=... camera_ndc_dx=... camera_ndc_dy=...`.

Primary gates: attachments remain rigid and camera scrolling is materially
smoother. If screen deltas are valid/nonzero but motion still judders, the next
investigation is phase/latency continuity between consecutive A/B intervals,
not object identity or presenter cadence.

## 2026-09-22 — dev143 rigid-XFB camera presenter; player test needed

Functional dev143 implementation is complete through `640a85bd0de2`; this handoff update follows it.
Runtime behavior is unverified until the Windows player build below completes.

Player capture `launch(20260922-222341).log` on `a8f61c11af44` FAILED
dev142 visually. Doors remain attached, but camera scrolling is still extremely
jittery and Mario's head can separate from his body. That proves draw-level
"matched" identity is not strong enough for composite actors even when the
global camera estimate is good.

The useful success is the camera sensor itself. Around interval 2048 the game
reported camera view dx=-3.685181 while the replay consensus independently
estimated camera_dx=-3.6851838 from 2,758 agreeing draws out of 2,821 samples.
So the remaining problem is presentation/transform policy, not camera-delta
measurement.

The same capture also shows the old replay renderer feeding back into source
cadence. In the heavy section completed-frame windows fall into roughly
35-50 FPS while replay is re-encoding one or two complete ~3,000-draw scenes
per source frame. At interval 2048 the replay encoder issued 6,012 GX draws for
two synthetic phases in addition to the authoritative frame.

dev143 changes architecture:

- normal interpolation no longer patches any object, actor, attachment or GX
  draw transform; the matcher is camera-sensor-only;
- normal visible mode exits preparation after camera consensus and skips uniform
  arena cloning / full-scene replay encoding;
- the async 100-Hz presenter always uses the authoritative retained XFB;
- each new source frame records its measured camera delta and the actual elapsed
  source interval;
- on each host present, the complete current XFB is translated from the previous
  camera position toward the current one using wall-time alpha over that measured
  interval. The other retained XFB is drawn underneath to fill the narrow edge
  exposed by the translation;
- because the entire image moves as one surface, Mario's head/body, doors and
  other composite geometry cannot separate due to interpolation;
- source intervals are clamped to 8-50 ms for camera playback so pathological
  startup/hitch gaps do not create seconds of interpolation latency;
- identity third-argument mode retains the legacy replay path for diagnosis only.

This is intentionally camera-first, not final full object interpolation. NPC and
animation local poses update at authoritative source cadence. Once camera
scrolling is stable and source throughput is restored, future object
interpolation must use explicit higher-level object/bone identity rather than
draw-order/geometry heuristics.

Next command: **`M14-LLVM-INTERPOLATED-RUN.cmd`**, same Yold route. No cache
wipe. Expect:
`M14 dev143 rigid-XFB camera interpolation active`
and sparse
`M14 dev143 camera prep ...` /
`M14 dev143 camera present ...`
logs. Primary gates: (1) Mario remains rigid, (2) doors remain rigid,
(3) camera scrolling becomes materially smoother, and (4) producer windows
recover toward the dev139 baseline because full-scene replay rendering is off.

## 2026-09-22 — dev142 camera-coherent interpolation candidate; player test needed

Player capture `launch(20260922-203205).log` on `43fcf4853de3` improved the
dev141 correctness failure: house doors stayed attached. The remaining visual
failure is now strongly camera-specific: the player reports extremely jittery
camera scrolling while NPC walking and Tippi hover motion look normal.

The log explains why dev141 could not smooth that case. Its fail-closed policy
accepted only a minority of source frames once complex gameplay began; cadence
and partial-transform rejection dominated. The camera-motion section therefore
mostly fell back to native source poses. Preparation itself is no longer the
main cost: the sparse 668-draw interval at 4096 required only 177 us total.

dev142 changes the interpolation model rather than weakening identity:

- derive one global camera translation delta from at least six fully matched
  transforms, using component medians plus a 0.25-unit 3D consensus cluster;
- require at least four agreeing samples and at least half of all candidate
  samples, so a single animated actor cannot masquerade as camera motion;
- fully matched uniform arenas retain exact adjacent-pose object interpolation;
- otherwise-unmatched/conflicted uniform arenas receive only the inferred
  camera translation while their object-local current pose remains
  authoritative;
- only when every formerly snapped unique uniform arena can be camera-completed
  is the packet reclassified transform-coherent. Otherwise the existing
  partial-transform fail-closed path remains active;
- presentation no longer disables interpolation merely because the producer
  exceeds 20 ms. Source cadence is telemetry only. Each adjacent-pose replay
  interval is anchored to the source frame's actual arrival time, preventing a
  fixed-60-Hz wall-clock backlog from producing stale burst presentation.

New sparse prep telemetry:
`camera_original_snapped`, `camera_patched`, `camera_coherent`,
`camera_samples`, `camera_consensus`, and `camera_dx/dy/dz`.
The report helper prints the latest camera-completion state.

Next command: **`M14-LLVM-INTERPOLATED-RUN.cmd`**, same Yold camera route.
No cache wipe. Expect
`M14 dev142 camera-coherent interpolation active`.
Primary gate: camera scrolling should become materially smoother while doors
remain attached. If camera completion remains incoherent, use the new consensus
telemetry before changing thresholds. If it becomes coherent but low-source-FPS
sections still judder, the next step is wall-time-aware phase density rather
than weakening identity.

## 2026-09-22 — dev141 fail-closed interpolation safety candidate; player test needed

Player capture `launch(20260922-200228).log` on `af04ddd675fe` FAILED the
first visible-interpolation test: the player reports extreme judder and house
doors sometimes separating from their houses.

The log identifies two independent hazards. A sparse heavy-scene preparation
sample had 2,982 GX draws, with 435 unique transform arenas matched but 122
snapped; the visible path nevertheless accepted almost every source frame.
That permits one rigid/attached part to use an intermediate pose while a
neighbor uses the authoritative current pose. Later gameplay producer windows
fell to 45/1016 ms and 35/1033 ms while dev139 continued presenting at about
100 Hz. The old interpolation timeline advanced a fixed 16.667 ms per source
frame, so a slow producer could also accumulate already-due synthetic images
and present them in bursts.

dev141 now fails closed in both cases:

- normal interpolation rejects any frame containing a mixture of matched and
  snapped transform arenas (`reason=partial-transform`); identity diagnostic
  mode is exempt because it does not apply intermediate transforms;
- visible replay arms only after 12 consecutive source arrivals no slower than
  20 ms, drops back to the native XFB as soon as that envelope is missed, and
  must re-establish the healthy streak before replay resumes;
- the interpolation source timeline resets once drift exceeds 8 ms instead of
  allowing the previous 50-ms stale window;
- bounded telemetry reports `source_gap_us`, `healthy_source`,
  `partial_fallback`, and `cadence_fallback`.

This is intentionally conservative. Complex frames may look like the accepted
dev139 baseline rather than interpolated motion until stable object/attachment
grouping exists, but they should no longer be allowed to tear themselves apart
or drag a slow source through a stale 100-Hz replay queue.

Next command: **`M14-LLVM-INTERPOLATED-RUN.cmd`**, same Yold route and houses.
No cache wipe. Expect
`M14 dev141 visible interpolation safety active`. Priorities for the player
test are: (1) doors remain attached, (2) the extreme judder is gone, (3) source
producer returns near the dev139 envelope in complex scenes. Send the launch
log even if the result merely feels like the baseline; the new fallback counters
will tell us whether the next step is object-coherent matching or replay-cost
reduction.

## 2026-09-22 — opt-in visible interpolation implemented; player test pending

Next command: **`M14-LLVM-INTERPOLATED-RUN.cmd`**. Default target is 100 Hz;
game simulation remains 60 Hz. This now presents intermediate scene poses.
The accepted async-presenter launcher remains the baseline control.

Preparation uses CPU upload mirrors; replay stops at the actual display-copy
boundary, binds a private bbox target and queues owned XFB textures by source
phase. Unsupported resource/effect packets fall back to the native image.
Actual Windows performance and GPU correctness remain unverified; do not mark
the previous dev140a 30-FPS regression solved merely because cloud checks pass.

Read [implementation, test controls and remaining gates](docs/M14-VISIBLE-INTERPOLATION-PLAN-2026-09-22.md).
An `identity` third launcher argument shows current-pose replay for diagnosis.
Log counters distinguish accepted/fallback frames and selected fractional poses.

## 2026-09-22 — bbox priority player test PASSED

`launch(20260922-184816).log` on `9eb8ab83b906`: player reports normal
baseline feel, slight jitter, **no flip stall**. Median producer rate remains
60 FPS. After the first 19.093-ms read, sampled bbox map waits are 0.071–0.459 ms
instead of roughly 19–20 ms. Maximum display call is 108.947 ms versus 6.764 s
in the prior run. Keep bbox priority enabled. Remaining jitter/short hitches and
the dev140a preparation performance regression are still open.

See [measured result and limitations](docs/M14-BBOX-PRIORITY-PLAYER-RESULT-2026-09-22.md).

## 2026-09-22 — bbox readback priority candidate for flip stalls

`launch(20260922-143225).log` confirms the dev139 baseline: median producer
60 FPS with preparation disabled. Two flip-associated display calls took
6.763930 s and 6.728044 s. The burst contains hundreds of fresh bbox maps,
with sampled map waits approximately 19–20 ms. This is separate from dev140a's
30-FPS regression. The extra readback latency's exact driver cause is unproven.

The bbox priority candidate now serializes bbox copy/map/wait on Aurora's render
worker. It suppresses optional retained-image presents after the first bbox read
until the next XFB publication; already queued presents check this flag before
acquiring the surface. The current image stays displayed. This removes repeated
presentation contention from the queue during CPU-dependent bbox bursts while
preserving fresh GPU bounds, EFB continuation, and the four-register cache.
It does not eliminate the underlying synchronous GPU queries. A pending map
callback now owns its status storage rather than referencing an expired stack
variable if the wait times out.

Test: `M14-LLVM-ASYNC-PRESENTER-RUN.cmd`, same room, flip both directions several
times. No cache wipe. Expect `M14 bbox priority active` at the first read.
Compare bbox `map_us`, total visible flip pause and ordinary gameplay throughput.
For a same-build control, set `OPENSPM_M14_BBOX_PRIORITY=0` in the launching
command prompt, then run the same launcher. Unset it afterward. The candidate
is enabled by default; dev140 replay stays off in this launcher.

Cloud validation: complete 15-adapter chain applied twice with identical final
source; read-only/idempotent/ambiguous-source migration tests; emitted C++ queue
contracts including fresh results, worker reentry and disabled path; syntax
compilation of bbox.cpp and aurora.cpp. Windows runtime and speedup remain pending.
If maps remain ~20 ms, inspect the D3D12 map/queue wait itself and investigate
exact batching of the game's visibility queries; do not return guessed bounds.

## 2026-09-22 — dev140a runtime gate FAILED: sustained 30 FPS

Player log `launch(20260922-142116).log`, revision `0376ed7916a1`, confirms
visible slowdown: 92 producer windows, median 30.0 FPS, minimum 20.3 FPS,
80 windows below 55 FPS. Heavy-scene sparse preparation samples cost 6.260 ms
and 7.684 ms at roughly 3,000 GX draws. Do not advance to dev140b encoding.

The report's HEALTHY result was false reassurance: it only parsed the old
`frames=N/Tms` format, missed real `host_ms=T frames=N` rows, and allowed a
pass without producer timing. The analyzer now supports both, fails closed on
missing timing, flags repeated slow windows, and exposes sparse prep cost.
Counter success is explicitly not visual validation. Re-reporting needs no build.

Use the accepted `M14-LLVM-ASYNC-PRESENTER-RUN.cmd` for the same-scene baseline.
Do not claim causality from a cross-scene comparison. Next renderer work should
split preparation timing into snapshot/hash, candidate matching, alias validation,
and arena copying before optimizing the dominant part. Review per-frame hash-map
allocations and duplicate candidate scans, while retaining conservative matching
and shared-uniform safety. Do not fix the deadline miss by weakening correctness.
The runtime log also reports `640x480 scale 1`; the earlier statement that this
run used native display resolution was not supported. Determine where scale 1
is selected separately from the temporal-performance regression.

## 2026-09-22 — dev140 cumulative-source audit and build repairs

Integrated the newer work through `84ff81ec`, including dev140b, reporting,
discontinuity gates and the outer draw-metadata design. Fixed real cumulative-source configure failures, cached reconfiguration
failures, sidecar integration defects, a dev140b dictionary-slice bug,
and shared-uniform interpolation of unmatched/indexed draws. Existing dev140
migration/report tests now actually run from the source gate. No cache wipe.

The complete Aurora adapter chain now reapplies without source drift. Five
resulting C++ translation units pass GCC syntax compilation; the generated
preparation code passes synthetic ASan/UBSan behavioral checks. Windows/MSVC,
GPU correctness, and player performance remain untested for dev140.

Next command: `M14-LLVM-INTERP-REPLAY-PREP-RUN.cmd`, then
`M14-INTERP-REPORT.cmd`. Only after that gate passes, run the offscreen encode
proof. Both still show dev139 output. `prep_us` now exposes preparation cost;
matched/snapped counters include all GX uniform consumers.

Before publishing interpolated targets, prove alpha=1 image equivalence at the
actual GXCopyDisp boundary and resolve mutable texture/pass dependencies.
See [the audit and ordered gates](docs/M14-DEV140-AUDIT-2026-09-22.md).

## 2026-09-22 — dev140 ABI/upload audit correction

Two late source audits corrected important implementation details before any
dev140 local runtime test.

### GX replay metadata is a DrawCommand sidecar

Pinned Aurora's `gx::DrawData` is already exactly the 128-byte
`InlineDrawPayloadSize`. The rejected intermediate dev140 draft tried to add
uniform-prefix/indexed-matrix fields directly to that struct, which would have
grown it beyond the inline payload and failed compilation.

The canonical dev140 design now leaves `gx::DrawData` byte-for-byte upstream
compatible and stores replay-only metadata on outer `gfx::DrawCommand`:

- `m14ReplayUniformPrefixBytes`
- `m14ReplayIndexedMatrices`
- `m14ReplayGX`

The command processor routes GX draws through a metadata-aware recorder while
the ordinary 128-byte GX payload remains unchanged. A generated C++
`static_assert(sizeof(DrawData) == gfx::InlineDrawPayloadSize)` and source
contracts prevent future accidental growth. Cached copies of the rejected
oversized draft are migrated in place; no cache wipe is intended.

### Replay uniform upload no longer depends on pass high-water timing

Aurora copies mapped staging ranges to shared GPU buffers at each recorded
FrameOp's high-water mark. Merely appending dev140a's cloned uniform arenas
before `gfx::finish()` is not sufficient for every possible frame shape if no
later authoritative pass captures those bytes.

dev140b therefore explicitly emits `CopyBufferToBuffer` for each prepared
phase arena from the unmapped frame staging buffer to Aurora's shared uniform
buffer inside the existing render-worker end-frame callback, immediately before
offscreen replay. This makes replay arena availability independent of whether
an authoritative final EFB pass happened to remain open.

### First replay remains deliberately conservative

Replayable draws must be non-indexed and keep exactly the same projection and
active 3x3 PN basis across adjacent frames. Projection changes, rotation/scale
changes, indexed-matrix geometry, ambiguous duplicates and implausible
translations snap instead of interpolating.

For accepted draws dev140 patches only the active `currentPnMtx` translation;
all unrelated PN matrices, normal matrices, projection and rotation/scale stay
authoritative/current.

### Presentation timing note

At exactly 100.000 Hz, the rational scheduler naturally reduces to
`0.0,0.6 | 0.2,0.8 | 0.4`. dev140a now reads SDL's precise refresh ratio when
it is close to the requested target; on the measured 99.943-Hz display it keeps
the exact rational remainder across every authoritative 60-Hz frame, including
empty/transition/no-match frames. Production presentation must still use
absolute host presentation deadlines when publishing the prepared images, but
the source-time interpolation alphas themselves no longer assume an exact
100.000-Hz panel.

## 2026-09-22 — overnight dev140 checkpoint

dev139 is the accepted stable baseline. New interpolation work remains opt-in;
default launchers are unchanged and all Dawn/WebGPU work stays on Aurora's
render worker.

### dev140a replay preparation

Run:

```bat
M14-LLVM-INTERP-REPLAY-PREP-RUN.cmd
```

Visible output is still dev139. dev140a now prepares a display-rational phase
grid (exactly `0.0,0.6 | 0.2,0.8 | 0.4` only at 100.000 Hz), using sampled
adjacent-draw identity and cloned existing uniform bytes. The true 16/32-byte GX
uniform prefix and indexed-matrix flag live on the outer `DrawCommand` sidecar;
`gx::DrawData` remains the upstream 128-byte payload. Preparation runs before
`gfx::finish()`, reserves Aurora's final uniform scratch space and retains the
FramePacket through the render-worker callback.

Interpolation is intentionally conservative: only position-matrix translation
is interpolated. Projection, rotation/scale basis and normal matrices remain
authoritative/current. Zero-match intervals advance the phase clock but skip
the expensive full-arena copies. Bounded logs now expose no-match and arena
exhaustion totals.

### dev140b offscreen replay encode

Run only after a stable dev140a:

```bat
M14-LLVM-INTERP-REPLAY-ENCODE-RUN.cmd
```

dev140b actually replays prepared GX poses into isolated color/depth targets,
but does not present them. The authoritative EFB, guest-visible EFB-copy side
effects, swapchain and dev139 visible output remain untouched.

The proof fails closed on unsupported pass layouts, MSAA, stencil, prior-target
Load dependence, custom draws and invalid uniform ranges. Telemetry reports
`efb_copy_passes` plus separate layout/prior-load/custom/range rejection
counts.

### Tooling and source safety

`[LLVM 2/7]` now covers both dev140 patch scripts and both launchers. A
synthetic two-stage migration test applies/reapplies dev140a then dev140b and
requires byte-identical idempotency, correct prep-before-finish ordering,
FramePacket callback lifetime, unique GX/EFB tags and correct teardown.

New report helper:

```bat
M14-INTERP-REPORT.cmd
```

It summarizes replay match/skip coverage, arena use, offscreen encode/rejection
coverage, producer FPS and the Aurora render framebuffer size from the normal
launch log.

### Resolution audit

Pinned Aurora defaults `g_frameBufferScale` to zero. At zero it uses the
native SDL framebuffer dimensions; it only derives a scaled framebuffer from
the Wii VI size when a positive framebuffer scale is explicitly requested.
No OpenSPM call to `set_frame_buffer_scale` or `VISetFrameBufferScale` exists.

So the current renderer is not physically raster-locked to 640x480. Logical
game-coordinate quantization may still create spatial stepping, but it does not
explain the 60-on-~100-Hz repeated-pose judder.

Next order: dev140a -> `M14-INTERP-REPORT.cmd` -> dev140b -> report again.
Do not publish interpolated targets to the live presenter until both proofs have
local runtime evidence.

## 2026-09-22 — dev140a replay-preparation build ready

dev139 is accepted as a presentation/lifetime proof, but its repeated 60-Hz XFB
poses cannot make scrolling smooth on the 99.943-Hz display. The next step is
now implemented as a visual-neutral dev140a data-path proof.

New launcher:

```bat
M14-LLVM-INTERP-REPLAY-PREP-RUN.cmd
```

It keeps the authoritative Wii simulation at 60 Hz and keeps dev139's visible
high-refresh retained-XFB presenter active, but additionally prepares
display-rational interpolation replay data. **No intermediate draw replay touches pixels
yet**, so the picture is still expected to look like dev139.

### What dev140a now proves

- scans finalized GX draw commands after FIFO drain, using final merged geometry;
- uses cheap sampled geometry identity (first/middle/last 32-byte samples)
  instead of dev138's expensive full-range hashing;
- pairs adjacent-frame draws conservatively with duplicate transform-distance
  disambiguation;
- uses a rational display/source phase accumulator; exactly 100.000 Hz yields
  `0.0,0.6 | 0.2,0.8 | 0.4`, while 99.943 Hz preserves its precise remainder;
- clones the already-built authoritative uniform arena instead of calling
  `build_uniform()` again;
- keeps projection/rotation/scale/normal state authoritative and interpolates
  only the active non-indexed `currentPnMtx` translation;
- stores the real 16-byte vs 32-byte uniform prefix and indexed-matrix flag on
  `DrawCommand`, leaving GX `DrawData` at its exact upstream 128-byte ABI;
- performs replay preparation **before `gfx::finish()`**, while staging memory
  is mapped and before the final render pass captures its upload high-water
  mark;
- reserves `gx::MaxUniformSize` of tail capacity for Aurora's normal
  `gfx::finish()` depth-snapshot scratch append;
- retains the recorded `FramePacket` until the render-worker end-frame callback
  returns, which is the lifetime dev140b needs to consume the prepared ranges.

Two bugs in the interrupted dev140 draft were fixed before runtime testing:

1. replay arenas had originally been appended after `gfx::finish()`, meaning
   their bytes would not have been included in the pass high-water upload;
2. the first uniform-prefix repair left stale undefined `positionOffset` /
   `normalOffset` locals inside the generated C++ patch function.

Both are now covered by source-only contracts. dev140 is applied after dev139 in
CMake and remains dormant unless `OPENSPM_M14_INTERP_REPLAY=1`.

### Acceptance gate for the next run

Look for:

```text
M14 dev140 replay prep active:
M14 dev140 replay prep interval=...
```

The important fields are `matched_uniforms`, `snapped_uniforms`, `phases`,
`original_uniform_bytes`, and `staged_uniform_bytes`.

Pass dev140a if:

- it boots and plays without corruption/crash;
- source-only contracts and CMake adaptation pass;
- cadence logs show the intended rational display grid (near 2,2,1 at the
  measured ~99.943 Hz, with slowly changing alphas rather than drift);
- matched coverage is high in ordinary gameplay;
- staged uniform bytes remain below the 24-MiB arena without exhaustion;
- producer throughput remains close to the accepted dev139/dev138 58–61
  completed Wii frames/s envelope.

If that gate passes, dev140b should consume the prepared phase arenas in a
render-worker scene replay and replace dev139's repeated-XFB intermediate
presents with real intermediate poses. That is the first build expected to make
scrolling visibly smoother.

Aurora is not physically raster-locked to 640x480 in the current OpenSPM
configuration: framebuffer scale defaults to zero and native SDL framebuffer
dimensions are used. Game-authored logical coordinate quantization may still
leave some spatial stepping after temporal interpolation, but there is no
640x480 physical-pixel floor to solve first.

## 2026-09-22 — dev139 accepted; retained-XFB cadence proof is complete

`launch(20260922-025138).log` is the first clean, sustained run of the
render-worker-owned dev139 presenter at repo revision `831b8a39f0cb`.

The proof passed its architectural gate:

- display mode: 2560x1080 at 99.943 Hz;
- authoritative Wii simulation remains 60 Hz;
- dev139 started at target_hz=100 with visual interpolation explicitly OFF;
- shutdown totals: 7,096 retained Wii source publishes and 12,153 host presents;
- final host-present pacing: avg 10.185 ms, p50/p95 10.5 ms, p99 12.5 ms;
- late-game producer windows remain in the established dev138 envelope, normally
  58–61 completed Wii frames/s with roughly 5–6 ms display/main/done phases;
- no NVIDIA/D3D12 access violation or other presenter crash occurred.

The user's visual report was still jittery, possibly a little worse than the
60-Hz baseline. That is expected for dev139 rather than a failed proof: the
presenter is repeating 60 unique poses across a non-integer ~100-Hz display.
Some source poses therefore occupy one display refresh and others two. A faster
repeat cadence cannot synthesize motion that the 60-Hz source never rendered.

The current 640x480 EFB can contribute a separate spatial-quantization limit,
but it is not the primary temporal defect in this run. Internal-resolution
scaling should be A/B tested after genuine transform replay exists.

### dev140 direction

Stop iterating on retained-XFB repetition. Implement clean-room draw replay on
the pinned MIT `encounter/aurora` revision; do not import WiiCompiled/OpenPete
GPL runtime code.

The next renderer architecture is:

1. keep the 60-Hz authoritative command encoder unchanged;
2. retain the recorded Aurora frame packet through the end-frame callback
   instead of clearing it immediately beforehand;
3. record compact per-GX-draw transform history and exact geometry/material
   identity, reusing dev138's proven matching principles but not its heavyweight
   all-matrix sampled snapshot;
4. clone the already-built authoritative uniform bytes and patch only
   projection + position/normal matrix fields—never call `build_uniform` a
   second time per draw;
5. replay safe EFB draw passes on Aurora's render worker into an isolated
   interpolation target; skip guest-visible replay side effects;
6. for a 100-Hz target against 60-Hz simulation, schedule the exact repeating
   five-present / three-simulation-frame phase pattern:
   `0.0, 0.6 | 0.2, 0.8 | 0.4`;
7. add one simulation tick of presentation latency so both endpoints are known;
8. snap unmatched/indexed/discontinuous/unsafe draws rather than extrapolating.

dev139 remains useful as the proven surface/pacing/lifetime substrate and as a
fallback path, but its repeated-XFB output is no longer the visual target.

## 2026-09-22 — dev139 cached retained-present migration hotfix

The next rerun advanced through the source-only contracts but stopped during
CMake Aurora adaptation with:

```text
OpenSPM M14 Aurora dev139 async-presenter adaptation failed (4):
dev139 migration incomplete:
OpenSPM dev139 retained presenter must run on Aurora render worker
```

This was still configure-time patch migration, not a runtime renderer failure.
The cached Aurora checkout already contained the first dev139
`m14_present_retained_image()` body. The render-worker hotfix had taught the
transformer to canonicalize the cadence function, but not this retained-present
function, so the old body survived without the new render-worker assertion and
the required-marker validation correctly rejected it.

Fix:

- the dev139 transformer now extracts the canonical
  `m14_present_retained_image()` body from `STATE` and replaces any stale
  cached copy in-place before validation;
- `m14_presenter_main()` is still canonicalized independently;
- final validation requires exactly one retained-present function;
- the migration contract now reproduces both stale cached bodies at once
  (old retained-present body with no worker assertion + stale cadence body) and
  requires exact convergence back to canonical dev139 source.

No cache wipe should be required. Next command remains:

```bat
M14-LLVM-ASYNC-PRESENTER-RUN.cmd
```

## 2026-09-22 — dev139 source-contract migration-sentinel hotfix

The first rerun of the render-worker-serialized dev139 proof stopped in
`[LLVM 2/7]` before CMake/build/runtime:

```text
test_dev139_async_presenter_migration_contract
assert migrated == first
```

This was a patcher/test bug, not a renderer runtime failure. The dev139 install
check used `SIGNATURE`, but that signature lives inside
`m14_presenter_main()`—the exact function that the migration contract
deliberately replaces with a stale body. Once the test removed that body, the
transform incorrectly concluded dev139 was absent, inserted a second full state
block, and could no longer converge byte-for-byte.

Fix:

- installation is now detected by the durable
  `struct M14PresenterImage {` state sentinel;
- the state sentinel must be unique before migration proceeds;
- final validation also requires exactly one presenter state block and one
  `m14_presenter_main()`;
- source contracts explicitly forbid returning to the replaceable log signature
  as the install sentinel and verify stale-body migration converges to the exact
  canonical source without duplication.

No runtime data was produced by this attempt. Next command remains:

```bat
M14-LLVM-ASYNC-PRESENTER-RUN.cmd
```

## 2026-09-22 — dev139 first runtime: cross-thread WebGPU crash; render-worker serialization hotfix

`launch(20260922-012024).log` tested the first dev139 retained-XFB
presenter proof at repo revision `c11101d392e0`. The proof reached live Wii
frames and started the 100 Hz presenter, then failed after roughly eight
completed display-copy frames with a second-chance Windows access violation:

```text
0xC0000005
nvwgf2umx.dll+0x6EE79E
```

The captured native stack is entirely inside NVIDIA's D3D12 user-mode driver;
no `openspm_renderer.dll` RVA was recoverable from the crash stack. This does
not by itself prove an NVIDIA driver defect. The first dev139 architecture had a
stronger local lifetime/threading violation: Aurora's normal
GetCurrentTexture/encode/Submit/Present sequence runs on its render worker, while
dev139 moved device/surface/encoder work onto a new cadence thread. The pinned
Aurora source even asserts render-worker ownership for present-resample queue
writes. Serializing only `g_queue.Submit` was therefore insufficient.

The active hotfix keeps the decoupled cadence timer but changes its ownership
model:

- the cadence thread performs timing/scheduling only and never touches Dawn or
  WebGPU objects;
- at most one extra present job may be queued at a time;
- retained-XFB acquire/blit/Submit/Present runs through
  `gfx::render_worker::enqueue_work`, the same worker used by Aurora's normal
  presentation path;
- `m14_present_retained_image` now asserts it is executing on that worker;
- shutdown stops/joins the cadence thread before synchronizing the render worker,
  so no new present can be enqueued during teardown;
- the dev139 source migration canonicalizes an already-patched cached Aurora
  checkout in place, and source-only contracts exercise that migration;
- presenter cadence telemetry now emits at power-of-two counts starting at
  1/2/4/8 instead of waiting for 256 presents, so an early failure still leaves
  direct dev139 publish/present evidence.

Geometry interpolation remains OFF. This is still only the retained-XFB cadence
and lifetime proof. Keeping WebGPU presentation on the render worker may reduce
the amount of producer/presenter overlap, but correctness comes first; the next
runtime will tell whether the 100 Hz presentation cadence still provides enough
decoupling without producer slowdown.

Next player command:

```bat
M14-LLVM-ASYNC-PRESENTER-RUN.cmd
```

Expected startup marker now ends with:

```text
interpolation=off webgpu_thread=render-worker
```

Primary gates: no `nvwgf2umx.dll` access violation, stable boot/gameplay,
producer still near the dev138 envelope, and dev139 presenter telemetry showing
retained source publishes plus repeated host presents. Do not start visual
interpolation until this lifetime/cadence proof is stable.

## 2026-09-22 — dev138 runtime accepted; dev139 decoupled presenter proof ready

`launch(20260922-004449).log` is the successful sampled dev138 run at
revision `9a62bbb3d619`.

Player report: **back to the old jittery baseline**. This is the desired dev138
result: the severe dev137 slideshow/corruption regression is gone, while the
known 60 Hz-on-~100 Hz presentation judder remains.

### dev138 performance result

The sampled probe no longer perturbs normal gameplay materially.

Representative sustained heavy-scene windows:

- 60 frames / 1000 ms: disp ~5.91 ms, main ~5.68 ms, done ~5.07 ms;
- 61 frames / 1016 ms: disp ~5.88 ms, main ~5.71 ms, done ~5.06 ms;
- later windows repeatedly complete 59-61 frames/s with disp/main/done mostly
  in the ~5-6 ms range.

This restores the pre-dev137 ~56-60+ fps producer envelope.

The remaining host presentation cadence is still the old baseline:
shutdown present pacing reports p50 ~17.0 ms, p95 ~18.5 ms, p99 ~31.5 ms on
the measured 99.94 Hz display. This is the cadence problem dev139+ are intended
to decouple from the 60 Hz producer.

### dev138 identity result

The early sparse/transition samples correctly fall back:

- sample 2: 35 candidates, only 6 exact matches (17.1%), 29 unmatched.

Once normal gameplay is established, the large samples are extremely strong:

- sample 4: 2960 / 2960 accepted, 100% exact;
- sample 5: 2983 / 2983 accepted, 100% exact;
- sample 6: 3023 / 3023 accepted, 100% exact;
- sample 7: 2984 / 2987 accepted, 99.9% exact;
- samples 8-10: 100% exact;
- sample 11: 3023 accepted with one unmatched;
- sample 12: 2985 / 2985 accepted, 100% exact.

Across those sustained samples:
- ambiguous = 0;
- fallback_unique = 0;
- hash_failures = 0;
- indexed = 0;
- duplicate groups are resolved deterministically by final geometry/material
  identity instead of the rejected dev137 nearest-transform heuristic.

Important: some exact identity matches have very large transform-distance
outliers (hundreds of thousands to millions in the diagnostic metric). Identity
is therefore necessary but **not sufficient** for interpolation. Production
must snap/fallback on implausible motion, projection changes, scene transitions,
or other discontinuities.

dev138 is accepted. Do not spend more turns tuning the sampled matcher unless a
later replay bug disproves this evidence.

## dev139 — decoupled host presenter proof

The next milestone isolates the other half of the architecture before geometry
interpolation:

- guest/Wii simulation remains authoritative at 60 Hz;
- each completed Wii display-copy XFB is copied into a two-slot immutable
  host-presenter ring;
- a dedicated presenter thread owns swapchain acquire/blit/Present at a
  configurable target (proof launcher defaults to 100 Hz);
- the latest retained XFB may be repeated between 60 Hz producer frames;
- visual interpolation remains OFF, so motion can still visibly judder;
- dev138 identity hashing is disabled in the proof launcher to measure presenter
  overhead cleanly.

The implementation is wired through:
- `renderer/aurora/apply_async_presenter_probe_patch.py`;
- `M14-LLVM-ASYNC-PRESENTER-RUN.cmd`;
- CMake after dev138;
- source-only contracts including a full second-pass cached migration test.

Safety/hardening included before first test:
- producer and presenter queue submissions share a mutex for deterministic GPU
  queue ordering;
- sparse telemetry reports retained source publishes vs actual host presents;
- shutdown joins the presenter before WebGPU teardown;
- Aurora render textures gain `CopySrc` because the Wii XFB resolve texture
  otherwise cannot legally be copied into the retained presenter ring;
- the migration is write-idempotent and tests both `aurora.cpp` and
  `gfx/texture.cpp` on a second pass.

First test:

```bat
M14-LLVM-ASYNC-PRESENTER-RUN.cmd
```

Default target is 100 Hz. A second argument can override it, e.g.
`M14-LLVM-ASYNC-PRESENTER-RUN.cmd AUTO 120`.

### dev139 success criteria

Do **not** judge dev139 by motion smoothness yet. Intermediate poses do not
exist.

A successful proof should show:

- authoritative producer remains roughly ~56-60+ completed frames/s in heavy
  areas;
- presenter telemetry approaches ~60 source publishes/s and ~100 host
  presents/s at the default target;
- host present intervals move toward the display's ~10 ms cadence;
- no black flicker, detached doors, house cracks, stale-XFB corruption, crash,
  or producer slowdown.

If dev139 passes, dev140 should use the retained/replay path for actual
transform-only interpolated presentation slots, with dev138 identity matching
plus strict discontinuity snapping.

## 2026-09-21 — dev138 canonicalizer byte-idempotency hotfix

The latest sampled-probe rerun stopped in `[LLVM 2/7]` on the regression:

```
assert dev138.canonicalize_interpolation_region(canonical_seed) == canonical_seed
```

The semantic duplicate-seed repair was correct, but canonicalization was not
byte-idempotent. The old implementation found the replacement region at
`struct M14InterpProbeDraw`, deleted seed text from the preserved prefix, and
therefore left the seed's surrounding newline behind. A second pass produced
the same C++ with different leading whitespace.

Fix:

- the canonical replacement boundary now starts at the **earliest existing
  exact `M14InterpHashOffset` seed declaration** when one exists before the
  probe struct;
- the seed and its surrounding whitespace are therefore replaced as part of the
  interpolation region instead of edited out of the preserved prefix;
- the existing duplicate-seed regression remains unchanged and now validates
  both single-seed convergence and byte-identical second-pass canonicalization;
- the final transformer uniqueness guard still requires exactly one seed.

This attempt never reached CMake/build/runtime.

Next:

```bat
M14-LLVM-INTERP-IDENTITY-RUN.cmd
```

## 2026-09-21 — dev138 compile hotfix: duplicate hash seed after canonical replay

The next sampled-probe build passed contracts, CMake configure/generate and the
renderer performance proof. MSVC then stopped only in
`command_processor.cpp`:

```
M14InterpHashOffset: redefinition; multiple initialization
```

with the two declarations reported two source lines apart.

Cause: dev138 canonicalization locates the replaceable interpolation region by
the first `struct M14InterpProbeDraw`. The canonical
`M14InterpHashOffset` declaration intentionally sits immediately *before*
that struct. On the next configure, the old seed therefore remained in the
preserved prefix while `DEV138_CANONICAL` inserted a second seed.

Fix:

- before rebuilding the interpolation region, canonicalization removes every
  exact `M14InterpHashOffset` seed declaration from the preserved prefix;
- the canonical dev138 block then inserts exactly one seed;
- the transformer now fails closed unless the final
  `command_processor.cpp` contains exactly one seed declaration;
- source-only regression starts from two adjacent seed declarations and requires
  exactly one afterward plus byte-identical second-pass canonicalization.

No runtime data was produced by this compile failure.

Next:

```bat
M14-LLVM-INTERP-IDENTITY-RUN.cmd
```

## 2026-09-21 — dev138 sampled migration-marker hotfix

The next sampled identity-probe rerun passed source-only contracts and reached
CMake, then stopped in the dev138 source adaptation with:

```
dev138 migration incomplete: M14 dev138 identity frame=
```

This was another stale validator string left from the pre-sampling dev138
implementation. The generated code now logs:

```
M14 dev138 identity sample=...
```

but the transform's required-marker tuple still demanded the retired
`identity frame=` text.

Fix:

- dev138's migration validator now requires
  `M14 dev138 identity sample=`;
- the source-only contract suite explicitly requires the sampled validator marker
  and explicitly forbids the retired `M14 dev138 identity frame=` marker from
  the dev138 patch source.

This attempt stopped during CMake adaptation and produced no new runtime data.

Next:

```bat
M14-LLVM-INTERP-IDENTITY-RUN.cmd
```

## 2026-09-21 — dev138 stale renderer-contract prose hotfix

The next sampled dev138 rerun stopped in `[LLVM 2/7]` before CMake:

```
assert "no second uniform build and no visual substitution" in interp_identity
AssertionError
```

This was a stale literal-text assertion from an earlier dev138 draft. The active
sampled implementation no longer contains that exact prose, even though the
architectural invariant still holds.

The renderer contract now checks the actual invariants instead:

- sampled capture state `sM14InterpCaptureActive` exists;
- sampled result logging `M14 dev138 identity sample={}` exists;
- the dev138 patch contains no `build_uniform(` path;
- the old brittle prose assertion is removed completely.

No CMake/build/runtime work occurred in this attempt.

Next:

```bat
M14-LLVM-INTERP-IDENTITY-RUN.cmd
```

## 2026-09-21 — dev138 sampled-probe source-contract hotfix

The first rerun after the boundary de-duplication/sampling changes stopped in
`[LLVM 2/7]` before CMake:

```
assert dev138.SIGNATURE in canonical
AssertionError
```

Cause: the sampled-probe edit changed the generated startup log from the stable
dev138 marker

```
M14 dev138 geometry-identity interpolation probe active
```

to a new human-readable sampled phrase. The migration and dev136 successor
detection intentionally use the old text as a stable machine signature, so the
generated canonical block no longer contained its own marker.

Fix:

- dev138's generated startup line again begins with the stable machine signature
  and then describes the sampled behavior;
- source contracts now assert `dev138.SIGNATURE` is present directly in
  `DEV138_CANONICAL`;
- the same contract separately requires the sampled wording, keeping the marker
  and behavior description distinct.

This attempt never reached CMake/build/runtime, so it adds no new pacing or
identity evidence.

Next:

```bat
M14-LLVM-INTERP-IDENTITY-RUN.cmd
```

## 2026-09-21 — dev138 first real runtime: jitter traced to 5x boundary accumulation + full-rate diagnostic cost

`launch(20260921-235510).log` is the first successful dev138 runtime.

Player report: game runs, but is very jittery.

Two separate issues are now proven.

### 1. dev136 was re-applying after dev138 and accumulating frame boundaries

dev138 intentionally replaces dev136's helper/signature block. On the next CMake
configure, dev136 therefore believed it had never been installed and re-applied
its GXCopyDisp seam.

The successful run makes the accumulation obvious:

- dev138 identity counter reached `frame=18240`;
- the same run shut down at only 3666 host presents;
- ratio is ~4.98 identity boundaries per presented frame;
- sampled identity lines were almost always `candidates=0 previous=0` because
  the first boundary consumed the frame and the following four immediately
  swapped/cleared empty history.

Fix:

- dev136 now recognizes the dev138 signature as a successor and returns
  `already-applied` instead of re-inserting its seam;
- dev138 now normalizes `command_processor.hpp` to exactly one
  `m14_interp_probe_frame_boundary` declaration;
- dev138 normalizes `regs.cpp` to exactly one
  `command_processor.hpp` include and one boundary call immediately after the
  display-copy notification;
- source contracts reproduce four accumulated boundary calls and require a
  byte-idempotent one-call result.

### 2. full-rate geometry identity probing is itself too expensive

This run spends long stretches at ~30–45 completed game frames/s. Representative
heavy windows report ~5.7 ms display + ~5.4 ms main + ~17–22 ms done/render,
with ~33 ms present intervals.

For comparison, the earlier dev136 observation-only run in a ~3000-draw scene
still completed 59 frames in a 1016 ms window with ~5.95 ms display,
~5.59 ms main and ~5.68 ms done/render.

Therefore dev138 must not hash/match every draw every frame merely to validate
identity correspondence.

dev138 is now a **sampled diagnostic**:

- default/launcher interval: 300 display-copy frames (~5 seconds at 60 Hz);
- only two adjacent frames per interval enable geometry hashing and draw-history
  collection;
- ordinary frames return before geometry hashing or identity matching;
- the first half of a sample waits for a non-empty draw frame;
- the second adjacent frame is matched and logged as
  `M14 dev138 identity sample=...`;
- after the pair, all history is cleared and the probe goes dormant for the
  cooldown;
- `OPENSPM_M14_INTERP_PROBE_INTERVAL` is configurable from the launcher without
  another source change.

The goal of the next run is to recover the pre-dev138 performance envelope while
still obtaining occasional trustworthy identity samples. **No visual
interpolation is active yet**, so the known 60 Hz simulation on the ~99.94 Hz
display will still have cadence judder even after this diagnostic regression is
fixed.

Next:

```bat
M14-LLVM-INTERP-IDENTITY-RUN.cmd
```

Expected:
- dev136 reports already applied/superseded rather than re-applying every run;
- one interpolation boundary per GXCopyDisp;
- ordinary gameplay performance near the dev136 baseline;
- occasional `M14 dev138 identity sample=` lines with real nonzero candidates;
- no dev137 texture/house/door corruption.

## 2026-09-21 — dev138 compile hotfix: restore hash seed constant

The latest `M14-LLVM-INTERP-IDENTITY-RUN.cmd` attempt again advanced through
source contracts, CMake configure/generate and most of the Aurora GX build.

MSVC stopped only in `command_processor.cpp`:

```
error C2065: 'M14InterpHashOffset': undeclared identifier
```

at the two dev138 geometry-signature seed assignments.

Cause: when geometry hashing was moved behind the narrow recording-layer API,
the old local FNV-style append helper (which had also defined
`M14InterpHashOffset`) was removed, but the canonical dev138 record function
still used that seed constant to initialize its vertex/index signatures.

Fix:

- `DEV138_BASE` now defines
  `constexpr u64 M14InterpHashOffset = UINT64_C(1469598103934665603);`
  before `M14InterpProbeDraw` and the record function;
- source-only contracts assert the generated canonical block defines the
  constant before its first record-path use.

No runtime identity telemetry was produced by this failed compile.

Next:

```bat
M14-LLVM-INTERP-IDENTITY-RUN.cmd
```

## 2026-09-21 — dev138 compile hotfix: stale current_frame_packet declaration

The latest `M14-LLVM-INTERP-IDENTITY-RUN.cmd` attempt advanced through source
contracts and CMake successfully. dev136 was already applied and dev138 applied,
then MSVC failed compiling Aurora `recording.cpp`.

The failure was caused by an **abandoned earlier dev138 migration** that had
added this public declaration to `recording.hpp`:

```cpp
detail::FramePacket& current_frame_packet();
```

Aurora's real `current_frame_packet()` is an anonymous-namespace helper inside
`recording.cpp`. The stale public declaration therefore created a second
candidate in `aurora::gfx`, making every existing unqualified
`current_frame_packet()` call in that file ambiguous.

This is not a dev138 identity-probe runtime failure.

Fix:

- `normalize_recording_header()` now removes any stale public
  `current_frame_packet()` declaration before installing the two narrow
  geometry-hash APIs;
- the abandoned GX `frame_packet.hpp` include is removed if still present;
- dev138 fails closed if `recording.hpp` still contains
  `current_frame_packet` after normalization;
- source-only contracts now start from the exact poisoned-header shape and
  require the stale declaration to disappear;
- repeated header normalization remains byte-idempotent.

The active design still exposes only:

```cpp
bool m14_hash_recorded_vertex_range(Range, uint64_t&) noexcept;
bool m14_hash_recorded_index_range(Range, uint64_t&) noexcept;
```

Next test:

```bat
M14-LLVM-INTERP-IDENTITY-RUN.cmd
```

No runtime identity telemetry was produced by this failed build.

## 2026-09-21 — dev138 cached push-state cleanup hotfix

The next identity-probe attempt stopped during dev138 CMake adaptation with:

```
dev138 canonicalization left rejected dev137 state:
m14InterpUniformRange, m14_interp_visual_enabled, const auto* m14InterpPrevious
```

This confirmed the pre-`push_gx_draw` helper region was canonicalized correctly,
but rejected dev137 code still survived **inside** `push_gx_draw` itself.

dev138 now normalizes that function explicitly:

- removes all previous interpolation record statements, including dev137's split
  `const auto* m14InterpPrevious = ...` form;
- removes the complete temporary `m14InterpUniformRange` + visual interpolation
  block;
- restores zCompLoc's `DrawData.uniformRange` initializer from the midpoint
  ternary to authoritative `cache.uniformRange`;
- rejects any leftover dev137 local tokens after cleanup;
- inserts exactly one canonical dev138 geometry-record call before the normal
  uniform-validity path;
- recognizes an already-clean canonical call as a byte-for-byte no-op.

Additional hardening found before rerun:

- the patcher now imports Python `re` explicitly;
- recording-layer replay cleanup removes the shared
  `m14_hash_recorded_range` helper as well as the public vertex/index helpers,
  preventing duplicate helper definitions on repeated configure;
- source-only contracts reproduce the exact cached dev137 push residue
  (previous-pointer assignment, midpoint staging block, visual uniform ternary)
  and require a byte-identical second cleanup pass;
- recording hash API normalization is also required to be idempotent.

This failure occurred before build/runtime and adds no identity-probe evidence.

Next:

```bat
M14-LLVM-INTERP-IDENTITY-RUN.cmd
```

## 2026-09-21 — dev138 direct-from-dev136 canonical migration

The latest identity-probe attempt stopped during CMake with:

```
OpenSPM M14 Aurora dev138 interpolation-identity adaptation failed:
dev137 source layer must be present before dev138
```

This was a stale architectural dependency in the dev138 patcher. Its own
cached-source cleanup could remove the rejected dev137 signature and then a
later guard demanded that signature still exist.

This is now fixed at the design level, not with another signature exception:

- the active Aurora interpolation chain is **dev136 -> dev138**;
- CMake no longer executes `apply_transform_interpolation_visual_patch.py`;
- dev138 requires only the dev136 interpolation seam;
- dev138 replaces the entire cumulative interpolation helper region before
  `push_gx_draw` with one canonical observation-only implementation;
- this automatically removes stale/duplicated dev136/dev137/dev138 helper
  bodies, midpoint state and old correspondence globals;
- dev138 supplies its own required STL includes;
- staged geometry hashing is exposed through two narrow recording APIs:
  `m14_hash_recorded_vertex_range` and
  `m14_hash_recorded_index_range`;
- GX no longer includes `frame_packet.hpp` or calls the anonymous/internal
  `current_frame_packet()`;
- source-only contracts now enforce direct dev136->dev138 ordering and assert
  the dev137 visual patch is absent from CMake.

The dev137 patch file remains in the repository only as rejected experiment
history; it is not part of the active configure path.

This failed attempt produced no runtime identity data.

Next test:

```bat
M14-LLVM-INTERP-IDENTITY-RUN.cmd
```

## 2026-09-21 — dev138 compile hotfix: duplicate full interpolation block + frame-packet accessor

The next `M14-LLVM-INTERP-IDENTITY-RUN.cmd` attempt advanced farther than the
previous configure failures:

- source-only contracts passed;
- dev137 applied;
- dev138 applied;
- CMake configure/generate completed;
- MSVC then failed compiling Aurora `command_processor.cpp`.

This is still **no runtime identity evidence**.

The compiler exposed three independent migration defects:

1. the cached Aurora source contained two complete interpolation implementations,
   not merely a duplicated pair-safe helper. `M14InterpProbeDraw`, history
   vectors, helper functions, finish-frame logic and frame-boundary wrappers were
   all redefined;
2. dev138 referenced `gfx::detail::current_frame_packet()`, but Aurora defines
   the accessor in `aurora::gfx` and does not expose it in `recording.hpp`;
3. the dev138 record-call rewrite had dropped the terminating semicolon.

Repair now performed by the active dev138 patcher before its usual
already-applied check:

- keep the first complete interpolation block before `push_gx_draw`;
- delete any later full duplicate block(s) in that pre-push region;
- remove all duplicate `m14_interp_probe_frame_boundary()` bodies and install
  exactly one canonical wrapper;
- repair a missing semicolon on the geometry record call;
- include `frame_packet.hpp` in GX only for this diagnostic;
- declare `detail::FramePacket& current_frame_packet();` in the real
  `aurora::gfx` namespace and call it as `gfx::current_frame_packet()`;
- assert interpolation struct and frame-boundary uniqueness before completing
  the migration.

The source-only contract suite now reproduces:

- two complete interpolation structs/blocks before `push_gx_draw`;
- duplicate frame-boundary wrappers;
- the exact missing-semicolon record call;
- Aurora's real `gfx::detail` / `gfx` namespace split for the accessor.

No `.openspm` wipe should be necessary.

Next test remains:

```bat
M14-LLVM-INTERP-IDENTITY-RUN.cmd
```

## 2026-09-21 — dev138 first configure hotfix: duplicate dev136 pair-safe helper

First `M14-LLVM-INTERP-IDENTITY-RUN.cmd` attempt stopped during CMake before
build/runtime:

```
OpenSPM M14 Aurora dev137 visual interpolation adaptation failed (4):
dev137 interpolation math/helpers: expected source fragment exactly once, found 2
```

This is another cached-Aurora migration/idempotency issue, not runtime evidence
against dev138.

Diagnosis:

- dev137's transformer expected the exact dev136
  `m14_interp_pair_safe(...)` helper once;
- the cumulative cached Aurora source contains two byte-identical copies of that
  exact helper;
- globally weakening `replace_once` would be unsafe, so the repair is local to
  this known interpolation migration shape.

Fix:

- dev137 now collapses only duplicate **byte-identical**
  `PAIR_SAFE_ANCHOR` fragments;
- it similarly collapses only duplicate byte-identical `PAIR_EXTENSIONS`
  blocks if an interrupted migration left them behind;
- it appends the dev137 helper block only when its unique semantic definition
  markers are all absent;
- partial/non-identical helper drift still fails closed;
- source-only contracts reproduce the duplicate-pair-safe and
  duplicate-extension cached shapes and verify idempotency.

No `.openspm` wipe or CMake directory deletion should be required.

Next test remains:

```bat
M14-LLVM-INTERP-IDENTITY-RUN.cmd
```

The first milestone is simply getting past the dev137 CMake adaptation and into
dev138/build. There is still no new runtime identity data from this failed
attempt.

## 2026-09-21 — dev137 runtime rejected; dev138 safe identity probe ready

dev137 finally ran at `9a009718036d` and is rejected.

Player-visible result:

- presentation became a slideshow;
- camera could not keep up with Mario;
- black texture flicker appeared;
- houses briefly cracked/separated;
- doors detached for several frames.

The log isolates the performance collapse to the renderer/done tail, not guest
gameplay logic. Heavy windows show ~5–7 ms `spmarioDisp`, ~5–6 ms
`spmarioMain`, but roughly 55–105+ ms `DEMODoneRender`. A late 1-second
window completed only 10 frames with `done_avg_us=88037`; shutdown presentation
pacing had p50 ~66 ms, p95 ~92.5 ms, p99 ~102.5 ms.

Root cause: dev137 built a second complete GX uniform block for almost every
matched draw. ~3,000-draw scenes therefore duplicated full uniform staging on
the authoritative producer.

The visible geometry corruption also invalidates dev136/dev137's old "safe"
metric: samples still reported ~99–100% matched and 100% safe while houses and
doors visibly cross-matched.

Read next:

`docs/M14-DEV138-GEOMETRY-IDENTITY-PROBE-2026-09-21.md`

dev138 changes course:

- hard-disables dev137 visual midpoint substitution;
- `M14-LLVM-INTERP-MIDPOINT-RUN.cmd` is retired and redirects to dev138;
- output stays authoritative 60 Hz with FIFO/vsync;
- no second `build_uniform()`;
- no `DrawData`/pipeline ABI changes;
- final staged vertex/index bytes are fingerprinted incrementally;
- merged draws extend the fingerprint using only newly appended ranges;
- exact geometry+material identity is preferred;
- repeated identities are accepted only when the nearest full transform is
  unambiguous;
- conservative material fallback is measured separately.

Next player run:

```bat
M14-LLVM-INTERP-IDENTITY-RUN.cmd
```

Expected visual result: the normal 60 Hz FIFO/vsync baseline, with **none** of
the dev137 cracks/door separation/black flicker. Exercise the same rooms and
flips. The important new telemetry begins with
`M14 dev138 identity frame=`.

## 2026-09-21 — dev137 second configure hotfix / ABI simplification

Second `M14-LLVM-INTERP-MIDPOINT-RUN.cmd` attempt at head
`1b2d2206d018581edf17f78181681451150c2cea` also stopped before build/runtime:

```
dev137 DrawData interpolated range: expected source fragment exactly once, found 0
```

This exposed two design issues before gameplay:

1. cumulative M14 patches (especially zCompLoc's `m14PushDraw` lambda) have
   legitimately reshaped the pristine Aurora draw-emission source, so dev137
   cannot use exact multi-line source fragments there;
2. more importantly, Aurora's 64-bit GX `DrawData` is already 128 bytes, equal
   to `InlineDrawPayloadSize`. Adding another `gfx::Range` would overflow the
   inline draw payload even if the source migration succeeded.

dev137 is now command-processor-only:

- no `pipeline.hpp`, `pipeline.cpp`, or `DrawData` ABI changes;
- midpoint uniforms live in a temporary local `gfx::Range`;
- the existing `DrawData.uniformRange` receives either midpoint or
  authoritative range at record time;
- zCompLoc's nested draw lambda is handled structurally;
- regression contracts exercise duplicate includes, nested uniform rebuilds,
  and the zCompLoc-style draw shape.

Both failed dev137 attempts produced **no interpolation runtime evidence**.

Next action remains:

```bat
M14-LLVM-INTERP-MIDPOINT-RUN.cmd
```

## 2026-09-21 — dev137 first-launch configure hotfix

The first `M14-LLVM-INTERP-MIDPOINT-RUN.cmd` attempt at head
`0888a916417df33790ae5242ce8661bf40652368` did **not** build or run.

CMake stopped while applying dev137 to the cumulative cached Aurora source:

```
command_processor limits include: expected source fragment exactly once, found 2
```

Cause: dev137 used the generic `#include <unordered_map>` line as a
uniqueness anchor. Multiple earlier idempotent M14 migrations can legitimately
leave that STL include in more than one local include block, so the patch
applicator was cache-fragile even though the interpolation implementation had
not failed.

Hotfix:

- dev137 now uses an `ensure_include()` helper anchored on the stable local
  Tracy include and does not require generic include anchors to be unique;
- repeated application is byte-for-byte idempotent;
- the source-only native-pipeline suite now dynamically imports dev137 and
  reproduces the exact duplicate-`unordered_map` shape before CMake can touch
  the cached Aurora checkout.

The failed attempt produced **no gameplay/interpolation evidence**.

Next action remains:

```bat
M14-LLVM-INTERP-MIDPOINT-RUN.cmd
```

## 2026-09-21 — dev136 passes broad play probe; dev137 visual midpoint ready

Player exercised movement, multiple rooms and 2D/3D flips through roughly
10,000 completed display-copy frames with dev136.

Full-log periodic telemetry (excluding startup) observed 217,123 candidate
draws, 212,388 matches (97.82% weighted), and 209,560 interpolation-safe
matches (98.67% of matched draws). Median sampled match and safety rates were
both 100%. Every sampled match in this run was rigid/non-indexed.

The failures are concentrated at real discontinuities rather than steady play:
frame 720 is effectively a full scene replacement (2,387 candidates vs one
prior draw), while frame 6840 falls to 65.2% safe during a heavy transition.
Those draws should snap to current rather than be forced through interpolation.

Read next:

`docs/M14-DEV137-CONFIGURABLE-VISUAL-INTERPOLATION-2026-09-21.md`

dev137 changes repeated-identity matching from occurrence order to nearest
unused previous transform and adds the first visible interpolation proof.

Next player run:

```bat
M14-LLVM-INTERP-MIDPOINT-RUN.cmd
```

Important: this is still one host present per 60 Hz simulation frame. It is a
**visual correctness** test, not the final smooth/high-refresh result. Matched
rigid draws use alpha=0.5 transform uniforms; unmatched/indexed/discontinuous
draws use the authoritative current state. Check camera/world, Mario/NPCs,
rooms, flips, effects and UI for warping, instance swaps, flicker or smearing.

Interpolation is no longer a hardcoded port behavior. `OpenSPM.ini` now has
`interpolation=auto|on|off` and `interpolation_target_fps` where 0 means
follow the active display. CLI equivalents are
`--interpolation` and `--interpolation-fps`.

Do not build the full HOME overlay yet. The existing exact
`spmarioMain -> homebuttonMain` PC system-UI seam remains the intended future
OpenSPM menu entry point. That menu should edit the same persistent settings
backend for graphics, controls, mods, cheats, debug and system actions.

## 2026-09-21 — dev135 closes cadence proof; dev136 interpolation scaffold ready

Player result for `M14-LLVM-100HZ-CADENCE-VSYNC-RUN.cmd`:

- tearing is gone with FIFO/vsync;
- only a tiny residual judder remains; motion is mostly smooth;
- log confirms ~99.940 Hz display, FIFO present mode, vsync on, and the
  ~49.9715 Hz half-display diagnostic grid.

Decision: cadence mismatch is materially responsible for the residual 60 Hz
camera judder. Stop treating this as primarily a scheduler/pacing defect.
Production must restore 60 Hz Wii simulation and decouple/interpolate
presentation.

Read next:

`docs/M14-DEV136-TRANSFORM-INTERPOLATION-BACKPORT-2026-09-21.md`

dev136 is now wired into the cached Aurora configure chain. It is a clean-room
transform-history/pairing scaffold inspired by the public architecture of
OpenPete and WiiCompiled, but it does not copy WiiCompiled's GPL implementation.

Next player run:

```bat
M14-LLVM-INTERP-PROBE-RUN.cmd
```

This run is back at authoritative 60 Hz, FIFO/vsync ON, and does **not** emit
synthetic frames yet. It records projection + full PN matrix palettes per draw,
pairs them at GXCopyDisp boundaries, and reports match/safe-midpoint rates.
No visual improvement is expected from dev136 itself; its job is to prove the
draw correspondence that dev137 retained-scene replay will consume.

Do not return to 49.9715 Hz as a production path. Do not implement framebuffer
crossfade as the normal interpolator.

## 2026-09-21 — dev132 result / dev135 tear-free cadence test

Player result:

- dev130 60 Hz baseline: still visibly jittery;
- dev132 ~49.9715 Hz half-monitor cadence: **less jitter**;
- dev132 also shows visible horizontal shearing/scanline-like tearing.

The logs confirm the display is ~99.940 Hz and both tested paths used
`PresentMode::Immediate` with vsync off. The cadence run definitely activated
the 20,011,407 ns (~49.9715 Hz) fixed VI grid. This is meaningful evidence that
60-on-~100 Hz persistence mismatch contributes to the residual camera judder,
while Immediate presentation is a separate visible tearing artifact.

New no-source-change A/B:

```bat
M14-LLVM-100HZ-CADENCE-VSYNC-RUN.cmd
```

dev135 keeps the exact dev132 half-refresh simulation cadence but forces
`OPENSPM_M14_VSYNC=1` and `OPENSPM_M14_PRESENT_MODE=fifo`. Expected outcome:
similar reduced cadence judder without the horizontal tearing. This remains
diagnostic only because gameplay is intentionally ~16.7% slow.

If dev135 is smooth/even and tear-free, proceed to dev134/P1 production work:
restore authoritative 60 Hz simulation and build the decoupled high-refresh
presenter/history ring. Do not ship 49.9715 Hz simulation.

## 2026-09-21 — dev132 Aurora idempotency hotfix

The next player attempt passed all source-only LLVM contracts but stopped during
CMake configure because the existing patched Aurora checkout no longer matched
`apply_display_copy_snapshot_patch.py`'s exact recognized successor fragment.

Root cause: a documentation-only dev133 lifetime clarification had changed
comment text *inside* `AURORA_FINISH_NEW`. The user's cached Aurora source
still contained the previously applied fragment, so the idempotency guard saw
`old=0, snapshot=0` and correctly refused to guess.

Fix: restore the previously deployed exact Aurora successor fragment in the
patcher's source-matching constant. Renderer behavior is unchanged. The corrected
lifetime model remains documented outside that exact migration fragment.

The failed run reached no player/runtime code and provides no cadence evidence.
Next test remains:

```bat
M14-LLVM-SMOOTH-RUN.cmd
M14-LLVM-100HZ-CADENCE-RUN.cmd
```

## 2026-09-21 — dev132 test-launch hotfix

Player's first dev130/dev132 attempt stopped in source-only contracts before build.
The failure was not a runtime/cadence result: a dev133 comment reflow made
`test_llvm_native_pipeline.py` search for a stale contiguous sentence.

Hotfixes now applied:

- source contract checks the stable semantic substring
  `copy-time resolve, not the live EFB`;
- `M14-LLVM-SMOOTH-RUN.cmd` explicitly forces
  `OPENSPM_M14_DIAG_HALF_99_943HZ=0`;
- the smooth launcher no longer puts `<` in an `echo` line, which CMD had
  parsed as input redirection and printed "The system cannot find the file
  specified.";
- display-copy patch comments now reflect the corrected dev133 lifetime model:
  current async-frame ownership is safe, but the cache handle is not immutable
  cross-frame history.

Next player comparison remains:

```bat
M14-LLVM-SMOOTH-RUN.cmd
M14-LLVM-100HZ-CADENCE-RUN.cmd
```

The prior failed attempt produced no gameplay evidence and should not influence
the cadence diagnosis.

## 2026-09-21 — session handoff / stop point

Chat session stopped intentionally after saving the current frontier.

Read first:
`docs/M14-DEV133-SESSION-HANDOFF-2026-09-21.md`.

Next player comparison:

```bat
M14-LLVM-SMOOTH-RUN.cmd
M14-LLVM-100HZ-CADENCE-RUN.cmd
```

The second run is dev132's deliberately slow ~49.9715 Hz diagnostic, exactly
half the player-reported 99.943 Hz display. If camera scrolling becomes evenly
smooth while slow, proceed to dev134's presenter-owned 3-slot history-ring
proof while restoring authoritative 60 Hz simulation. If it does not, return
to residual producer/presentation ownership rather than building interpolation.

Current-frame Aurora `openspmDisplayCopySnapshot` shared ownership is safe for
the active asynchronous present but is **not** immutable cross-frame history;
later GXCopyDisp can reuse the same cache allocation. Any decoupled presenter
must copy completed XFBs into dedicated presenter-owned history textures.

## 2026-09-21 — dev133 high-refresh presenter design gated on dev132

dev132 is now the decisive cadence test:

```bat
M14-LLVM-SMOOTH-RUN.cmd
M14-LLVM-100HZ-CADENCE-RUN.cmd
```

The second run intentionally slows simulation to ~49.9715 Hz, exactly half the
player-reported 99.943 Hz monitor. It is diagnostic only.

If that run makes camera scrolling evenly smooth, proceed to the dev133
production architecture rather than changing simulation speed:

- keep Wii simulation/VI/input/audio at 60 Hz;
- use the completed GXCopyDisp TextureHandle only for the current async present;
- copy each completed XFB into a dedicated 3-slot presenter history ring;
- retain previous/current/spare history textures without guest-memory readback;
- drive host presentation from actual display cadence;
- enqueue all Dawn/WebGPU work through Aurora's existing render worker;
- start true interpolation with camera transforms, then actors/models.

Simple full-frame crossfade is explicitly *not* the intended normal solution;
it ghosts moving objects/UI. It may be useful only as a hidden proof of the
two-frame presenter.

The renderer source contracts verify that the active compatibility chain
applies the Wii display-copy snapshot migration and moves the TextureHandle into
the asynchronous present callback. Important correction: Aurora reuses that
copy-texture allocation on later GXCopyDisp calls, so it is current-frame-safe
but not cross-frame immutable. A production presenter needs dedicated history
textures; the first proof should use a GPU render-blit into a 3-slot ring.

Read:
`docs/M14-DEV133-HIGH-REFRESH-PRESENTER-DESIGN-2026-09-21.md`.

## 2026-09-21 — dev132 99.943 Hz monitor-half cadence diagnostic

The player reports a 99.943 Hz monitor. 60 Hz source frames cannot map evenly
onto that refresh without interpolation.

New diagnostic-only comparison:

```bat
M14-LLVM-SMOOTH-RUN.cmd
M14-LLVM-100HZ-CADENCE-RUN.cmd
```

The cadence run is identical to dev130 smooth except it changes the VI period
to 20,011,407 ns (~49.9715 Hz), exactly half of 99.943 Hz.

**Gameplay is intentionally ~16.7% slow. This is not a solution.** It is a
controlled cadence test: if camera scrolling becomes evenly smooth while slow,
the remaining camera-only symptom is strongly explained by 60-on-100 display
cadence. The production answer would then be 60 Hz simulation plus decoupled/
interpolated high-refresh presentation.

Expected:

```text
M14 dev132 diagnostic cadence active:
M14 display cadence current=...
```

If the same irregular hitch remains, return to dev129/dev130 producer/present
tails. If it improves partially, both cadence judder and sparse held frames are
contributing.

Read:
`docs/M14-DEV132-99_943HZ-CADENCE-DIAG-HANDOFF-2026-09-21.md`.

## 2026-09-21 — dev131 display-refresh cadence diagnostic

Observation-only diagnostic added to Aurora's real SDL window:

```text
M14 display cadence current=... refresh_hz=... precise=... ratio60=... nearest_multiple=... error_hz=... integer_multiple=...
```

It does not alter presentation, VI, renderer ordering or simulation. The goal
is to distinguish residual host-frame misses from ordinary 60 Hz camera judder
on a desktop refresh that is not an integer multiple of 60.

The six steady `seq=2 stage=1` A/B logs were also quantified:

| variant | busy_due/s | >25 ms Present tails/s |
| --- | ---: | ---: |
| baseline Immediate | 1.405 | 1.094 |
| final-spin | 0.926 | 0.603 |
| ABOVE_NORMAL | 1.165 | 0.952 |
| HighQoS | 1.684 | 1.263 |
| fastcore | 0.798 | 0.677 |
| no-sticky | 2.045 | 1.698 |

This backs the player's impression: fastcore was the best-feeling independent
variant, final-spin also helped, and no-sticky was clearly worse. Keep sticky
pressed-edge retention ON and do not stack HighQoS.

Next test remains deliberately small:

```bat
M14-LLVM-IMMEDIATE-RUN.cmd
M14-LLVM-SMOOTH-RUN.cmd
```

The first isolates dev129's dynamic scheduler-idle side exit. The second adds
only dev126 final-spin + dev127 fastcore. Both now report the display cadence.

Read:
`docs/M14-DEV131-DISPLAY-CADENCE-DIAG-HANDOFF-2026-09-21.md`.

## 2026-09-21 — dev130 combined smooth-pacing candidate

After validating dev129 separately, compare one combined candidate:

```bat
M14-LLVM-SMOOTH-RUN.cmd
```

It keeps dev129 + Immediate + Drain + precise VI + sticky pressed edges, and
combines only the two scheduling A/Bs that moved in the helpful direction:

- dev126 final <=2 ms true-idle spin;
- dev127 highest-EfficiencyClass soft CPU Set preference.

Priority remains normal and HighQoS remains off/system-managed.

Recommended next test is now only:

```bat
M14-LLVM-IMMEDIATE-RUN.cmd
M14-LLVM-SMOOTH-RUN.cmd
```

The first isolates dev129. The second answers whether fastcore+spin improves
the residual tail further.

Read:
`docs/M14-DEV130-COMBINED-SMOOTH-PACING-HANDOFF-2026-09-21.md`.

## 2026-09-21 — dev129 state-sensitive scheduler-idle side exit

Six player A/Bs (baseline, spinwait, priority, HighQoS, fastcore, no-sticky)
left some camera/world-scroll jitter. Fastcore felt best; no-sticky was clearly
worst, so sticky pressed-edge retention remains ON.

dev128 exposed the stronger architectural signal: across every variant, the
dominant single `busy_due` owner is repeatedly phase `done` at exact Revolution
OS scheduler-idle PC/LR `0x8027551C`.

This is not ordinary game/render work. It means LLVM can reach SelectThread's
idle continuation *inside an already-running native slice* and, because dev90
made that entry unconditional native-first, execute the idle loop until the
slice eventually returns. The outer bridge recognizes true idle too late.

dev129 makes only this entry state-sensitive:

- live Nintendo RunQueueBits == 0 -> internal side exit to the host VI-wait path;
- RunQueueBits != 0 -> keep existing native SelectThread re-entry (dev68 preserved);
- unavailable/invalid state -> fail open to Nintendo native execution.

No guest scheduler state is fabricated. A wake that arrives after the side exit
is safe because the next outer `bridge_m14_service_retrace()` re-reads live
RunQueueBits before waiting.

Run first:

```bat
M14-LLVM-IMMEDIATE-RUN.cmd
```

Expected:

```text
M14 LLVM dev129 scheduler-idle dynamic dispatch active:
M14 LLVM dev129 scheduler-idle dynamic stats empty_sideexits=... runnable_native=... unknown_native=...
```

The strongest success signal is collapse of:

```text
M14 dev128 busy-due owner ... pc=0x8027551C
```

along with smoother camera/world scrolling and fewer >25 ms present tails.

Read:
`docs/M14-DEV129-DYNAMIC-SCHEDULER-IDLE-HANDOFF-2026-09-21.md`.

## 2026-09-21 — dev128 tail-only busy_due ownership

Every current LLVM launcher now passively records what the producer is doing at
the exact instant a fixed VI deadline arrives while dev118 classifies the guest
as still busy.

Marker:

```text
M14 dev128 busy-due owner n=... phase=... phase_age_us=... late_us=... pc=... lr=...
```

This reuses the existing four frame-phase timestamps; no new periodic clock or
broad profiler is added. Logging is bounded to the first 64 events and later
powers of two.

Use the normal A/B order:

```bat
M14-LLVM-IMMEDIATE-RUN.cmd
M14-LLVM-SPINWAIT-RUN.cmd
M14-LLVM-PRIORITY-RUN.cmd
M14-LLVM-HIGHQOS-RUN.cmd
M14-LLVM-FASTCORE-RUN.cmd
M14-LLVM-NOSTICKY-INPUT-RUN.cmd
```

If the same phase/PC owns the remaining misses across all scheduling variants,
optimize that exact deterministic tail. If one scheduling variant removes or
moves the ownership, host scheduling has direct evidence.

Read:
`docs/M14-DEV128-BUSY-DUE-OWNERSHIP-HANDOFF-2026-09-21.md`.

## 2026-09-21 — dev127 soft fast-core CPU Set A/B

Another independent host-scheduling test is ready:

```bat
M14-LLVM-FASTCORE-RUN.cmd
```

It leaves priority/QoS normal, dev126 spin OFF, sticky pressed edges ON, and
keeps the current Immediate + Drain + precise-VI baseline.

After Aurora workers already exist, the launcher-side producer thread enumerates
Windows CPU Sets and selects every eligible, nonparked CPU Set in the **highest
reported EfficiencyClass**. No logical CPU numbers are hardcoded and no hard
affinity mask is used. Any prior explicit thread CPU Set selection is restored
after `backend.run()`.

Expected on a heterogeneous machine:

```text
M14 dev127 fast-core CPU Set A/B active:
```

If all processors report one class, it logs a no-op skip instead.

This is the final host-scheduling axis to test before returning to exact
`busy_due` producer ownership. If priority, HighQoS, final-spin and fast-core
are all neutral, stop scheduler guessing and add tail-only phase ownership for
display vs DrawDone.

Independent test order:

```bat
M14-LLVM-IMMEDIATE-RUN.cmd
M14-LLVM-SPINWAIT-RUN.cmd
M14-LLVM-PRIORITY-RUN.cmd
M14-LLVM-HIGHQOS-RUN.cmd
M14-LLVM-FASTCORE-RUN.cmd
M14-LLVM-NOSTICKY-INPUT-RUN.cmd
```

Read:
`docs/M14-DEV127-FASTCORE-CPUSET-HANDOFF-2026-09-21.md`.

## 2026-09-21 — dev126 final true-idle spin A/B

dev125 removed redundant QueryPerformanceFrequency calls from the precise VI
wait, but the same wait still issues roughly 150k-190k `Sleep(0)` yields/sec
in steady Yold. That is thousands of host scheduler handoffs per displayed
frame.

New independent A/B:

```bat
M14-LLVM-SPINWAIT-RUN.cmd
```

It keeps chunk256/state_mem1/heavy_diag0 + fixed 60 Hz VI + Immediate + Drain +
sticky pressed edges + normal producer priority/QoS. Only when Nintendo's real
scheduler is idle and the fixed VI deadline is <=2 ms away, the producer spins
with `YieldProcessor()` to that same deadline instead of repeatedly calling
`Sleep(0)`.

Expected:

```text
M14 dev126 final-idle-spin A/B active:
...
precise=1 spin2ms=1
```

If it improves the remaining camera/world-scroll hitch and reduces
`busy_due`/present tails, replace the brute-force A/B later with a lower-CPU
high-resolution wait + short final spin.

### Sticky-input audit

The historical sticky-input path was also traced. It does **not** replay or
toggle held directions. `g_v9_input_down` is replaced from fresh physical key
state every retrace and feeds KPAD `buttonsHeld`. The sticky dev29 mechanism
carries only unobserved `buttonsPressed` edge bits until SPM consumes them.

A clean falsification run still exists:

```bat
M14-LLVM-NOSTICKY-INPUT-RUN.cmd
```

It disables only pressed-edge carry; held-direction sampling is unchanged.

Recommended independent test order after pulling:

```bat
M14-LLVM-IMMEDIATE-RUN.cmd
M14-LLVM-SPINWAIT-RUN.cmd
M14-LLVM-PRIORITY-RUN.cmd
M14-LLVM-HIGHQOS-RUN.cmd
M14-LLVM-NOSTICKY-INPUT-RUN.cmd
```

Read:
`docs/M14-DEV126-FINAL-IDLE-SPIN-HANDOFF-2026-09-21.md`.

## 2026-09-21 — dev125 cache invariant QPC frequency

A concrete host-side cost was found inside dev118's precise final-2-ms VI wait.

The exact wait loop calls the host monotonic clock before deciding whether VI is
due and again after every Sleep(0)/Sleep(1). Steady Yold runs commonly report
~150k-190k Sleep(0) yields per second.

The Windows host clock was unnecessarily calling
`QueryPerformanceFrequency()` on **every** timestamp even though that frequency
is invariant for the running system. That means the fine-wait path alone could
issue roughly 300k-380k redundant frequency queries/sec.

dev125 caches QPC frequency once before exposing the host API. The timestamp
path still calls `QueryPerformanceCounter()` every time and uses the exact same
whole-seconds/remainder integer conversion. VI deadlines, precise-wait policy,
guest timing and renderer/input semantics are untouched.

Expected:

```text
M14 dev125 cached QPC frequency active:
```

For the first clean subjective check after pulling:

```bat
M14-LLVM-IMMEDIATE-RUN.cmd
```

Then the independent scheduling/input A/Bs remain:

```bat
M14-LLVM-PRIORITY-RUN.cmd
M14-LLVM-HIGHQOS-RUN.cmd
M14-LLVM-NOSTICKY-INPUT-RUN.cmd
```

Read:
`docs/M14-DEV125-CACHED-QPC-FREQUENCY-HANDOFF-2026-09-21.md`.

## 2026-09-21 — dev124 explicit producer HighQoS A/B

dev123 remains the first scheduling test. A second independent host scheduling
axis is now prepared because dev122's steady-gameplay residual tails correlate
very strongly with dev118 `busy_due` (~0.98 Pearson in the measured slice).

Test order, separate launches:

```bat
M14-LLVM-PRIORITY-RUN.cmd
M14-LLVM-HIGHQOS-RUN.cmd
M14-LLVM-NOSTICKY-INPUT-RUN.cmd
```

HighQoS run keeps Win32 priority normal and sticky pressed edges ON. It runtime-
resolves `SetThreadInformation` and explicitly disables execution-speed
throttling only on the producer/main thread after Aurora workers already exist.
It restores system-managed QoS after `backend.run()`.

Expected:

```text
M14 dev124 producer HighQoS A/B active:
```

This is not a broad performance retest. Median first-retrace work has already
been proven below 16.667 ms. The target is only the rare producer/VI tail.

If both priority and HighQoS are negative, the next host-scheduling experiment
should use Windows CPU Sets + reported EfficiencyClass for a soft fast-core
preference, never hardcoded CPU numbers.

Read:
`docs/M14-DEV124-HIGHQOS-HANDOFF-2026-09-21.md`.

## 2026-09-21 — dev123 scheduling + sticky-input isolation

Historical correction after dev122/player reminder:

**Do not reopen the old "get average work below 16.667 ms" diagnosis.**
dev114 already measured the canonical Yold median first-retrace path at
13.514 ms (+3.153 ms margin) while camera judder remained. dev118 then directly
showed most VI deliveries reached true Nintendo idle before deadline. dev119
Immediate presentation removed roughly 75% of the visible judder.

Also do not interpret dev122 `done_us` as producer work. It spans
DEMODoneRender -> next DEMOBeforeRender and includes renderer sync, idle/wait,
VI delivery and resume.

The dev122 run leaves a narrower tail question: ordinary Yold is ~56-60 visual
frames/s, but a few `busy_due` deliveries remain; walking tail display+main is
roughly 14 ms and DrawDone roughly 2.5 ms, so sparse frames can still sit at the
cliff even though the median is safe.

Two **independent** dev123 A/Bs are ready:

```bat
M14-LLVM-PRIORITY-RUN.cmd
M14-LLVM-NOSTICKY-INPUT-RUN.cmd
```

Priority run:
- keeps sticky pressed-edge carry ON;
- raises only the producer/main Windows thread to ABOVE_NORMAL after Aurora
  workers have already been created;
- restores the old priority on exit.

No-sticky run:
- keeps producer priority normal;
- disables only dev27/dev29 unconsumed `buttonsPressed` carry;
- does **not** change held directions.

Input audit result:
- `g_v9_input_down` is the physical held-state sample each native input epoch;
- `g_v9_input_pressed` is an edge mask and is the only sticky state;
- `g_v12_input_repeat` is an SDK repeat marker ORed alongside held bits.

So the source does not support a theory where holding Left/Right repeatedly
cancels itself. The no-sticky launcher exists to prove that experimentally
anyway.

If both A/Bs are negative, next candidates are:
1. P-core/EcoQoS scheduling telemetry/preference;
2. tail-only producer attribution triggered specifically by `busy_due`;
3. produced display-copy serial -> Immediate Present serial identity;
4. camera/world interpolation only as final pacing polish.

Read:
`docs/M14-DEV123-RESIDUAL-TAIL-SCHEDULING-HANDOFF-2026-09-21.md`.

## 2026-09-21 — dev122 camera-vs-presentation tail attribution

Player clarification changes the visible-defect description: everything else feels
very smooth; the remaining small stutter is apparent primarily as world/camera
scrolling while Mario walks. dev121 cleanup did not change it.

Do not discard the earlier camera CSV result. It proved camera-5 pos/target and
viewMtx are numerically smooth in guest memory. The unresolved question is
temporal delivery: this dev121 run still has host Present p99 around 31 ms and
115/6441 intervals over 25 ms, so a held whole frame can look like camera-only
stutter because Mario remains near screen center while the world scrolls.

dev122 adds tail-only attribution with no broad profiler:

- reuse existing four phase clocks and log only >20 ms produced frames;
- attach Mario-X and cam5 pos/target/view-translation deltas to those frame tails;
- augment the existing Present probe to log only >25 ms intervals without any
  additional clock reads.

Run:

```bat
M14-LLVM-CAMERA-TAIL-RUN.cmd
```

Look for:

```text
M14 dev122 frame tail n=...
M14 dev122 camera tail n=...
M14 host present tail n=...
```

Interpretation:
- smooth/nonzero camera delta + frame/present tail = camera state is innocent;
- `view_repeat=1` while Mario moved = reopen guest camera update/order;
- present tail without frame tail = downstream presentation;
- frame tail without present tail = producer lateness absorbed downstream.

Read:
`docs/M14-DEV122-CAMERA-PRESENT-TAIL-HANDOFF-2026-09-21.md`.

## 2026-09-20 — dev121 quiet normal-play candidate

dev120 Inline FIFO parsing was player-tested and did **not** remove the remaining
small stutter. Return ordinary play to the proven Drain parser baseline.

The new highest-value lead is self-inflicted instrumentation overhead. The
dev119 shutdown log reported **10,027,806 timed native slices in ~64.7 s**
(~155k slices/sec). Normal heavy_diag=0 play was still wrapping every one of
those slices with two host monotonic-clock reads plus histogram/pair/cadence
bookkeeping.

dev121 makes heavy_diag=0 bypass that profiler entirely and also retires the old
sequence/HP/bbox observation wrappers from normal play. Guest execution,
dev105 callback semantics, VI timing, state_mem=1, chunk256 and Immediate
presentation are unchanged. Full diagnostics remain available with
heavy_diag=1.

Run:

```bat
M14-LLVM-QUIET-RUN.cmd
```

Expected:

```text
M14 LLVM dev121 quiet normal-play path active
M14 LLVM dev121 quiet normal-play diagnostics mode
```

dev118 VI pacing reporting is retained, but its report check now runs at frame
cadence instead of every native slice.

Read:
`docs/M14-DEV121-QUIET-NORMAL-PLAY-HANDOFF-2026-09-20.md`.

## 2026-09-20 — dev120 same-thread FIFO Inline pacing candidate

dev119 Immediate presentation is a clear visual win: player reports the residual
whole-frame jitter is now roughly one quarter of the pre-dev119 amount. Keep
Immediate as the current presentation default for pacing work.

The remaining occasional hitch still coincides with an upstream producer issue,
not guest camera state. In the dev119 Yold capture, VI delivery is usually ready
before deadline, while DrawDone/parser synchronization remains about 2.5 ms on
average with multi-ms tails.

Do **not** restore the 2026-09-13 Aurora Thread-mode experiment. Its failure note
proved that asynchronous parsing of raw GX commands can dereference live mutable
MEM1/MEM2 after Broadway reuses it, producing rendering corruption.

dev120 instead uses Aurora's existing `ProcessingMode::Inline`: command-aware
prefixes are parsed synchronously on the Broadway/runtime thread before guest RAM
can change. This preserves Drain-mode memory-observation safety while spreading
parser work across complete GX command boundaries.

Test:

```bat
M14-LLVM-INLINE-RUN.cmd
```

Expected markers include:

```text
OpenSPM M14 Aurora FIFO processing mode: Inline
M14 dev120 same-thread GX Inline pacing active
M14 inline GX FIFO publish n=1 bytes=...
M14 Aurora command-complete same-thread FIFO publish active
```

Exact same-source rollback/baseline:

```bat
M14-LLVM-IMMEDIATE-RUN.cmd
```

which explicitly forces `OPENSPM_M14_FIFO_MODE=drain`.

Read:
`docs/M14-DEV120-INLINE-FIFO-PACING-HANDOFF-2026-09-20.md`.

## 2026-09-20 — dev119 moves the jitter investigation downstream to present mode

Player tested dev118's precise final-2-ms VI wait. Scrolling is still jittery,
but subjectively a little less so.

The new dev118 counters now clear the native VI scheduler as the main residual
cause: in sustained heavy-scene windows, ~57-60 of 60 VI deliveries/s usually
arrive from true scheduler idle, `busy_due` is commonly only 0-3/60, ordinary
delivery lateness is near zero/single-digit microseconds, and the VI handler is
only tens of microseconds. Keep the precise-wait improvement, but stop tuning
that boundary.

The same run's desktop-present telemetry is much more suspicious:

```text
presents=5245 avg_us=17189 p50_us=17000 p95_us=19000
p99_us=31000 over25=90 over33=6 present_call_avg_us=199
```

So `Present()` itself is cheap, but the delivered desktop cadence still has a
near-two-refresh tail that matches the visible whole-frame hold/catch-up.

dev119 adds an Aurora runtime present-mode override. Test:

```bat
M14-LLVM-IMMEDIATE-RUN.cmd
```

Expected startup includes `present mode Immediate`. This retains chunk256,
state_mem=1, heavy_diag=0 and dev118 precise VI waiting. The same-source Mailbox
comparison is now explicit via:

```bat
M14-LLVM-PACING-RUN.cmd
```

which forces `OPENSPM_M14_PRESENT_MODE=mailbox`.

Read:
`docs/M14-DEV119-IMMEDIATE-PRESENT-HANDOFF-2026-09-20.md`.

## 2026-09-20 — dev118 targets the remaining whole-frame pacing jitter

The old NPC-heavy ~30 fps collapse is fixed; the remaining player-visible defect
is periodic whole-frame hold/catch-up at roughly 56 completed game frames/s
against ~60 authoritative VI retraces.

Astra's scheduling-boundary diagnosis is now the active direction. Source audit
confirmed that the existing `done` phase is **DEMODoneRender -> next
DEMOBeforeRender**, so its 20-22 ms peaks mix renderer/end-of-frame work with
Nintendo scheduler idle, Windows waiting, VI delivery and thread resumption.
Do not interpret `done_max` as pure rendering CPU cost.

dev118 adds a low-volume pacing probe at the real native VI boundary. About once
per host second it reports whether each due VI arrived after Nintendo was already
truly idle (`from_idle`) or while game/native work was still active
(`busy_due`), plus idle-ready margin, Windows wait deadline crossings,
delivery lateness, VI-handler cost and fixed-grid expired deadlines.

A bounded pacing candidate is included at the same time. The historical idle
path requests `Sleep(1)` on every early poll. With
`OPENSPM_M14_PRECISE_IDLE_WAIT=1`, dev118 keeps `Sleep(1)` while more than
2 ms early but uses `Sleep(0)` for only the final <=2 ms before the exact same
fixed VI deadline. Nintendo scheduler state, the 60 Hz grid, guest simulation,
GX ordering and dev117 callback semantics are unchanged.

Test first:

```bat
M14-LLVM-PACING-RUN.cmd
```

Same-source baseline:

```bat
M14-LLVM-DEV117-RUN.cmd
```

The baseline launcher now explicitly disables precise idle waiting. Both build
the dev118 source family, expected as
`m14-dev118-chunk256-sim1-yold-normal`; only the pacing launcher enables the
wait A/B.

If the pacing launcher visibly smooths scrolling, keeping a stable ~56 fps is
acceptable for this milestone. If not, the new `M14 dev118 pacing` line tells
the next target: `busy_due` -> late DrawDone/frame production;
mostly-`from_idle` with low VI lateness -> Aurora presentation identity;
VI-handler spikes -> Nintendo scheduler wake/context transfer.

Read:
`docs/M14-DEV118-RETRACE-PACING-HANDOFF-2026-09-20.md`.

## 2026-09-20 — chunk512 rejected; dev117 targets dev105 callback fallbacks

Player tested the isolated chunk512/state_mem=1/heavy_diag=0/Mailbox candidate.
Subjectively it felt maybe worse, and the runtime agrees strongly enough to
retire the geometry experiment:

- comparable late Yold `seq=2/stage=1` produced-frame windows are roughly
  53 fps under chunk512 versus roughly 56 fps under chunk256;
- typical heavy `disp+main` is slightly higher, not lower;
- dev105 remains around ~23 generated nested steps per callback;
- therefore generic 256->512 chunks do not remove the callback transition
  pressure. Explicit partitions/function exits dominate.

The one ~1.84 s present-safepoint spike in the chunk512 log is an
`update-events`/window-host stall and should not be treated as steady-state
guest CPU cost.

`M14-LLVM-CHUNK512-RUN.cmd` is now retained only for reproducibility.

### Active dev117 candidate

The chunk512 shutdown also exposed two avoidable dev105 outer-dispatch exits:

- `step_limit_yields=62394` at the old 64-generated-chunk cap;
- `no_progress_yields=17740`, where dev105 previously treated every same-PC
  return as no progress.

In RecompCore mode negative `CPUState.downcount` is accumulated guest-cycle
charge. A generated chunk can legitimately return to the same PC after doing
work. dev117 therefore:

1. treats same PC + **more-negative downcount** as proven guest progress;
2. keeps true same-PC/no-charge progress fail-closed;
3. raises only the generated-chunk cap 64 -> 128;
4. keeps the exact same **32768 guest-cycle quantum** and all semantic,
   exception, miss, recursion and fallback guards.

No callback is skipped or reordered.

Run:

```bat
M14-LLVM-DEV117-RUN.cmd
```

Expected module identity:

```text
m14-dev117-chunk256-sim1-yold-normal
```

Expected startup marker:

```text
M14 LLVM dev117 CPUState dispDraw progress-aware nesting active
```

Shutdown's existing dev105 stats line now appends
`same_pc_progress=<count>`.

Read first:
`docs/M14-DEV117-DISPDRAW-PROGRESS-HANDOFF-2026-09-20.md`.

Known playable baseline remains chunk256/state_mem=1/heavy_diag=0. The original
performance blocker stays CLOSED; dev117 is optional headroom/pacing work aimed
at the remaining whole-frame hold/catch-up judder.


## 2026-09-20 — Mailbox falsifies swapchain pacing; chunk512 A/B staged

Player tested `M14-LLVM-NOVSYNC-RUN.cmd` at repo `2b22a6866de6`.
Aurora successfully selected D3D12 `PresentMode::Mailbox`, so this was a real
presentation-policy A/B rather than a fallback to FIFO.

### Result

Subjective result: scrolling remained **just as jittery**. The player also
clarified that a smaller amount of jitter is visible even when the camera is
clamped.

The log nevertheless shows Mailbox materially cleaned up desktop presentation:

- at 4096 presents: avg ~16.731 ms, p50 17.0 ms, p95 18.5 ms, p99 19.5 ms;
- only one >33 ms present interval by that checkpoint;
- `Present()` itself remains cheap.

Therefore the old FIFO ~33 ms tail was real amplification, but **not the main
perceived jitter**. Do not spend more time on swapchain present mode as the
primary fix.

During the heavy Yold traversal, completed main-loop cadence still repeatedly
falls to roughly 48-57 frames/s while authoritative VI/retrace remains ~60.
Typical heavy windows are about 6 ms `disp` + 6 ms `main`, with a further
~2.5 ms DrawDone producer boundary. Produced-frame throughput is now the main
frontier.

Prior dev111/dev115 evidence already proves:

- Mario X motion is monotonic/smooth on every frame that is actually produced;
- dominant `dispDraw` target `0x8008533C` executes ~305 times/produced frame;
- it represents ~254 distinct callbackParams, all camera 5, i.e. map-object
  traversal rather than one pathological object looping;
- direct NPCEntry-owned callbacks are only ~16/frame.

The current Mailbox shutdown also reports substantial dev105 fallback pressure:

- attempts: 2,114,083;
- nested generated steps: 43,321,462;
- budget yields: 78,725;
- 64-step-limit yields: 70,462;
- no-progress yields: 44,995.

### Next A/B: generic chunk512

Pinned DolRecomp accepts `DOLRECOMP_LLVM_CHUNK_INSTRUCTIONS` values 64..4096.
OpenSPM previously exposed only 128/256. The harness now exposes an isolated
experimental 512 geometry:

```bat
M14-LLVM-CHUNK512-RUN.cmd
```

The launcher deliberately keeps the immediately preceding Mailbox baseline:

- `OPENSPM_M14_VSYNC=0`;
- chunk = 512 (only intended changed variable);
- state_mem = 1;
- heavy_diag = 0.

Unchanged: semantic/dev97 partitions, entry-only restart PCs, exact host routing,
dev105 64-step / 32768-cycle fail-closed callback contract, GX/render semantics
and 60 Hz Wii timing.

Interpretation:

1. If Yold becomes materially smoother / closer to stable 60, retain 512 and
   inspect the reduced dev113 nested-steps-per-attempt ratio.
2. If cadence barely changes, generic partition size is no longer the answer.
   Next target the dev105 `dispDraw` callback execution path itself (especially
   step-limit/budget fallbacks) rather than renderer/camera/presentation.
3. If 512 causes a correctness regression, revert only the geometry candidate;
   normal 256 + state_mem=1 remains the known playable baseline.

## 2026-09-20 — video proves whole-frame holds; presentation A/B staged

Player supplied a 60.0016 fps, 2283-frame (~38.05 s) desktop capture of walking
across Yold Town under dev116 normal mode, plus the matching launch log.

### Video result

The continuous scrolling interval (frames 145..1356, ~20.2 s) is objectively
bimodal rather than merely "camera easing":

- 1211 frame-to-frame transitions;
- 273 near-identical whole-screen transitions (grayscale MAE < 1);
- **22.54% near-duplicate capture frames**;
- almost all holds are one frame long, with a small number of two-frame holds;
- moving-frame image difference is orders of magnitude larger than the held
  frames.

The held frame includes the whole captured image, not only the background.
Therefore the visible pattern is "desktop frame repeats, then catches up", not
"guest camera transform pauses while actors continue animating".

This also explains the player's observation that the defect is obvious only
while the camera scrolls: when the world view is clamped, repeating one desktop
frame is far less perceptually salient.

### Matching runtime evidence

The corresponding dev116 log independently shows the same cadence defect:

- Wii authoritative retraces stay around 60/s;
- completed main-loop frames vary widely through the traversal (many windows in
  the ~40-57/s range);
- at 4096 successful desktop presents:
  avg interval ~19.324 ms, p50 ~17.5 ms, p95 ~33.0 ms, p99 ~35.5 ms;
- at 8192 presents:
  avg ~19.354 ms, p95 ~33.0 ms, p99 ~35.0 ms;
- shutdown aggregate:
  15667 presents, avg ~19.293 ms, p50 17.5 ms, p95 32.5 ms, p99 34.5 ms;
- actual swapchain Present() remains cheap (~0.2-0.25 ms average);
- DrawDone producer synchronization remains only a few milliseconds.

Together with the earlier cam5 pos/target + viewMtx proof, the active defect is
now **frame production/presentation cadence**, not camera logic.

### New isolated A/B: Aurora vsync off

Normal OpenSPM remains `vsync=true` and the current D3D12 run selects
`PresentMode::Fifo`.

Pinned Aurora's exact present-mode selection is:

- vsync ON: prefer FifoRelaxed if supported, otherwise Fifo;
- vsync OFF on non-Metal: prefer Mailbox, then Immediate, otherwise Fifo.

A new opt-in launcher sets only `OPENSPM_M14_VSYNC=0`:

```bat
M14-LLVM-NOVSYNC-RUN.cmd
```

Guest simulation remains 60 Hz; chunk256, state_mem=1, heavy_diag=0, GX/FIFO
semantics and DrawDone presentation boundary are unchanged.

Interpretation:

1. **If the log says Mailbox/Immediate and scrolling becomes much smoother**
   while game logic remains unchanged, Fifo deadline quantization is amplifying
   slightly-late producer frames into 33 ms visible holds.
2. **If 33 ms tails shrink but the pan still has smaller uneven steps**, the
   producer cadence is genuinely irregular and the final solution should be
   decoupled high-refresh interpolation/presentation.
3. **If Aurora still reports Fifo**, the host/backend does not expose a
   non-Fifo mode and this A/B is inconclusive.

Do not promote no-vsync by default from this test; Immediate can tear and even
Mailbox is a presentation policy choice rather than the final interpolation
architecture.

## 2026-09-20 — cam5 viewMtx clears final guest-camera stage; dev116 contract repaired

Player capture `20260920-151513-camera-follow-trace.csv` includes the new
CamEntry +0x54 3x4 view matrix. Result:

- 110 samples across ~7.99 host seconds;
- camera 5 is initially held at the legitimate left room clamp near
  x=-2060.503 while Mario starts moving;
- after camera 5 is free to move, there are **0 intervals** where camera motion
  occurs but its view matrix remains unchanged;
- `viewMtx[0][3]` delta tracks camera target-X delta with correlation
  ~-0.9924 and median delta ratio ~-0.9973;
- the 3x3 orientation block is constant during this straight horizontal run,
  as expected;
- the four exact view-matrix stalls while Mario is moving are all during the
  legitimate room-edge camera clamp, not stale matrix delivery.

Therefore the remaining visible camera "trail then snap" is downstream of the
guest's final camera matrix. Treat renderer/presentation/frame pacing as the
active frontier; do not spend another iteration on CamEntry follow logic absent
new contradictory evidence.

The first dev116 player launch was blocked before compilation by one stale
source-contract assertion still expecting `m14-dev114-chunk`. That bookkeeping
assertion was corrected in commit
`2ea8b928213ad5f5a7468b35a261e8f7c73c0d00`; no runtime semantics changed.

## 2026-09-20 — camera trace clears guest follow logic; dev116 normal-play cleanup staged

Two player-captured Yold camera traces now materially narrow the remaining
jitter:

- `cam11` is inert in both captures;
- `cam5` is the live world camera in both the 2D-looking and 3D-looking runs,
  consistent with dev115's map callback census;
- the second trace has **zero** cam5 pos/target stalls while Mario is moving and
  frame-to-frame cam5 target-X motion correlates ~0.986 with Mario-X motion;
- the first trace's apparent stalls begin only after cam5 reaches the room's
  hard X clamp at roughly 1609.96 while Mario continues farther right, then
  resume normally when Mario returns inside the camera boundary.

Therefore do not treat the remaining visible "trail then snap" as evidence that
`camMain` updates only intermittently. CamEntry pos/target state is smooth.

The live client now also records cam5's actual 3x4 `viewMtx` at `CamEntry
+0x54` in `camera_trace`. This is the final guest-side discriminator:

- smooth pos/target + jerky viewMtx -> guest matrix/update-order issue;
- smooth viewMtx too + visible snap -> renderer/presentation/frame-pacing issue.

### dev116 normal-play diagnostics retirement

The sim1 Yold A/B was measured with substantial observation-only instrumentation
still on the hot path. Normal play now compiles the proven diagnostic probes off:

- dev111 per-dispDraw callback target hash/census;
- dev112 NPC callbackParam ownership/range classification;
- dev115 0x8008533C camera/callbackParam hash census;
- dev111 per-produced-frame NPC-table refresh and Mario-motion sample.

**dev105 callback execution and all fail-closed semantics are unchanged.**
State-memory remains enabled and generated LLVM objects are reusable; the new
switch changes only the native bridge compile/link.

Normal launcher:

```text
M14-LLVM-UPDATE-RUN.cmd
chunk=256 state_mem=1 heavy_diag=0
module identity: m14-dev116-chunk256-sim1-yold-normal
```

Opt-in profiling launcher:

```bat
M14-LLVM-PROFILE-RUN.cmd
```

which selects `chunk=256 state_mem=1 heavy_diag=1` and keeps all dev111/112/115
telemetry available.

This is intentionally the next single-variable headroom/pacing test. The
performance blocker remains closed regardless; the question is whether removing
diagnostic work turns the ~58 fps median instrumented result into a more stable
60 and reduces camera-pan judder.

## 2026-09-20 — performance blocker CLOSED; sim1 promoted; camera jitter is separate

Player validation after the dev114 Yold A/B is strong enough to close the
long-standing heavy-scene performance blocker:

- canonical Yold A/B: 30.00 -> 58.01 median produced fps;
- first-VI critical path: 18.863 -> 13.514 ms;
- VI margin: -2.196 -> +3.153 ms;
- spikeball-heavy room is smooth, including 3D;
- opening cutscenes that historically collapsed are smooth;
- Bowser/minion-heavy opening scene feels ~60 fps;
- UI timers, enemies and Mario movement are smooth;
- live cockpit smoke passes on the sim1 process.

Normal `M14-LLVM-UPDATE-RUN.cmd` now defaults to:

```text
chunk=256
state_mem=1
```

Explicit retained fallbacks:

```bat
M14-LLVM-STATE-MEM0-RUN.cmd   rem chunk256 / state_mem=0
M14-LLVM-CHUNK128-RUN.cmd     rem historical chunk128 / state_mem=0
```

`M14-LLVM-STATE-MEM-RUN.cmd` remains as a compatibility alias for the now
normal sim1 configuration.

Performance is no longer an active blocker. Further CPU/render optimization
belongs under optional polish/headroom unless a new real scene regression is
demonstrated.

### Remaining visible jitter is camera-specific

Player observation after performance recovery:

- UI ticks smoothly;
- enemies walk smoothly;
- Mario walks smoothly;
- camera visibly trails slightly, then periodically catches/snaps toward Mario.

Treat this as a **camera/update/presentation defect**, not as evidence that the
simulation is still missing frame deadlines.

Public R8PE01 camera data gives a clean no-rebuild diagnostic surface:

- `camPtrTbl = 0x804C9F80`;
- camera 5 = 3D;
- camera 11 = normal 2D;
- `CamEntry.pos = +0x15C`;
- `CamEntry.target = +0x168`.

New read-only console directive:

```text
camera_trace <seconds> [interval-seconds]
```

Canonical launcher:

```bat
M14-DEBUG-CAMERA-TRACE.cmd
```

It records Mario plus both camera 5/11 pos+target fields to CSV using only
existing safe-frame `pos/read32/dump` operations. No guest writes and no native
rebuild are required.

Run once in Yold 2D while holding right steadily; optionally repeat after
flipping to 3D. If camera target/position itself stalls and catches up, investigate
`camMain` / camera-road follow logic and update ordering. If camera fields move
smoothly while the view still snaps, the defect is renderer/presentation-side.

### 120/240 Hz direction

Do **not** raise game simulation above its proven ~60 Hz contract just to target
high-refresh displays. SPM logic, timers and many animations are 60-Hz-oriented.

The professional path is:

1. keep authoritative guest simulation at 60 Hz;
2. retain previous/current render transforms for camera and moving scene objects;
3. present interpolated visual frames at 120/144/165/240 Hz;
4. keep UI/gameplay state authoritative at simulation ticks;
5. disable/reset interpolation on warps, flips, cuts, teleports and other
   discontinuities.

Camera jitter should be solved before general interpolation, because the camera
transform is the most visually sensitive interpolated state.

## 2026-09-20 — dev114 state-memory is a player-proven performance breakthrough

Canonical Yold Town A/B is now complete and valid:

```text
baseline:  chunk=256 state_mem=0
candidate: chunk=256 state_mem=1

median fps: 30.00 -> 58.01 (+28.00)
disp:       7.434 -> 5.689 ms (-1.745)
main:       9.182 -> 5.499 ms (-3.683)
disp+main: 16.596 -> 11.227 ms (-5.368)
draw_done:  2.267 -> 2.287 ms (+0.020)
critical:  18.863 -> 13.514 ms (-5.349)
VI margin: -2.196 -> +3.153 ms (+5.349)
nested steps/attempt: 23.67 -> 22.99 (-0.68)
```

Automatic regression gate: **PASS**.

This validates the dev114 hypothesis cleanly: generated callback-walk length
barely changes, DrawDone is neutral, but display/main wall time collapses by
~5.37 ms/frame and first-VI margin flips from a miss to >3 ms headroom.
Therefore the dominant win is cheaper CPUState-ABI state synchronization, not
fewer generated blocks or renderer synchronization.

### dev115 result from the same build

Exact `0x8008533C` remains 4880 calls / 16 produced frames, but dev115 shows:

- every call uses camera id 5;
- roughly 254 distinct callbackParam values appear per 16-frame report window;
- top callback params occur 16 times/window, approximately once per produced
  frame.

This strongly supports a broad map/display-object traversal once per frame,
rather than one pathological object being invoked hundreds of times.

### Promotion status

**Do not flip the default launcher yet.**

dev114 is now the performance promotion candidate, with `state_mem=0` retained
as the known fallback until one correctness smoke passes on sim1.

Required smoke on the currently-running state_mem=1 process:

1. `M14-DEBUG-SMOKE.cmd`;
2. ordinary reloadroom / nextroom / prevroom traversal;
3. one flip transition;
4. jump into a pit and confirm Game Over resolves normally (dev97 invariant);
5. if convenient, exercise a previously-proven wedding/cutscene path long
   enough to ensure no liveness regression.

If those pass, promote `state_mem=1` to the normal default while preserving an
explicit sim0 fallback launcher for bisects.

## 2026-09-20 — group sweep proves distributed NPC cost; state-memory is next fix candidate

The completed Yold group sweep is more informative than the earlier one-NPC
sweep.

Canonical baseline (`he2_03`, seven passive townsfolk, chunk256/state_mem=0):

- completed main-loop cadence: ~30/s;
- median disp+main across the two baseline seconds: ~16.68 ms;
- direct NPCEntry-owned disp callbacks: 256 / 16 produced frames = 16/frame;
- dominant dispDraw target `0x8008533C`: 4800 / 16 = exactly 300/frame;
- dev105 nested generated-dispatch steps/attempt: ~22.88.

As active entries are removed in groups:

- 2 off: direct NPC-owned callbacks settle at 128/16; representative
  disp+main ~14.29–15.04 ms; completed cadence remains near 30–32/s.
- 4 off: direct NPC-owned callbacks settle at 64/16; disp+main
  ~14.48–14.94 ms; cadence ~32–33/s.
- 6 off: direct NPC-owned callbacks reach **0/16**; disp+main falls to
  ~13.24–13.87 ms and completed cadence reaches ~40–44/s.
- 7 off: direct NPC-owned callbacks again reach 0/16; the cleanest active
  window is ~13.48 ms disp+main.

Across every settled group size, `0x8008533C` remains exactly **4800/16**.
Therefore:

1. NPC cost is real and distributed, worth roughly three host milliseconds per
   produced frame in this room;
2. there is no single bad townsfolk and no magical "all NPCs off" threshold;
3. the fixed 300/frame hot callback is not multiplied directly by active NPC
   count;
4. even after direct NPC-owned display callbacks disappear, fixed map/display
   and main-loop work still leaves insufficient headroom for stable 60 Hz.

The public US2 linker map places `0x8008533C` inside the `mapdrv.c` address
envelope (after `mapLoad`, before later named map-group routines). The exact
function is not public-symbol-named, so treat it as a **map-range callback
candidate**, not a proven function name.

### dev115 piggyback diagnostic

The next native build adds observation-only telemetry for exact
`0x8008533C` at the already-existing dev105 dispDraw boundary:

- callback count;
- r3 camera-ID distribution;
- distinct r4 callbackParam count using a bounded 512-slot table;
- collisions;
- top callbackParam values.

It adds no clocks and performs no guest mutation. This will distinguish
"hundreds of map objects once each" from repeated camera/pass traversal or a
small set being revisited pathologically.

### Next performance candidate: dev114 state-in-memory=1

Do **not** add another broad native-first bypass yet.

Pinned DolRecomp's state-in-memory mode is source-audited before every build:

- it applies only to CPUState-ABI LLVM bodies;
- native-ABI bodies are explicitly excluded;
- eligible state slots alias CPUState directly;
- materialization skips dirty aliased slots;
- reload skips aliased slots.

That scope matches the measured hot path unusually well: dev101 proved the
exact dispDraw bctrl at `0x8005B29C` is CPUState ABI, and dev105 still walks
~23 generated dispatcher blocks per callback even at chunk256. The indexed
address lookup itself is already cheap, and dev103 already skips host routing
for definite ordinary PCs. The remaining high-value hypothesis is therefore
repeated CPUState materialize/reload traffic, not another lookup cache.

### Player test order

If a valid canonical state_mem=0 `yold-perf` capture is not already present,
capture it **before closing the current state_mem=0 process**:

    M14-DEBUG-YOLD-BENCH.cmd

Then launch the opt-in candidate:

    git pull
    M14-LLVM-STATE-MEM-RUN.cmd

While that state_mem=1 process is running, enter Yold Town and run from a second
terminal:

    M14-DEBUG-YOLD-BENCH.cmd

Then:

    M14-PERF-STATE-MEM.cmd

The reporter pairs only chunk256 state_mem=0 vs chunk256 state_mem=1 captures.

Success criterion is wall-time recovery, not a lower generated-step count:
state-memory may leave ~23 blocks/callback while making each boundary
substantially cheaper. A useful candidate should reduce disp/main/critical time
without a correctness regression. If state-memory is neutral, dev115's
camera/param census becomes the next architectural lead.

### Benchmark harness hardening

`npc_group_pulse` now emits exact `YOLD-GROUP-ACTIVE-BEGIN/END` markers and
verifies every selected active bit stayed zero for the complete measurement
window before restoration. The group analyzer uses only that marked interval
for new captures; old unmarked captures remain labeled as legacy/spill-prone.

This was prompted by a real semantic observation: after the one-NPC sweep, the
player saw the purple townsfolk reactivate away from her home location and walk
back. Active-bit toggling can re-enter placement/pathing behavior, so group
sweeps reload the room between sizes and should not be treated as transparent
pause/resume.

## 2026-09-20 — one-NPC sweep result; no-rebuild group threshold test next

Player completed the canonical Yold one-NPC active-bit sweep on the chunk256 /
state_mem=0 candidate. All captures came from one PID and one repo revision.

### Result

No individual townsfolk explains the 30 Hz cliff.

Baseline remained about 30 completed frames/s with disp+main around 16.1 ms.
Across all seven one-NPC windows, completed cadence stayed about 30 fps.

The strongest structural signal is in dev112 rather than FPS:

- baseline direct NPCEntry-owned disp callbacks:
  256 / 16 produced frames = 16/frame;
- several one-NPC pulses reduce that to:
  192 / 16 = 12/frame once the pulse is fully active;
- therefore those actors each account for the expected 4 direct callbacks/frame
  (3 x 0x801B43CC + 1 x 0x801B49A0);
- despite that 25% reduction in direct NPC-owned callbacks, cadence does not
  escape ~30 Hz;
- dominant 0x8008533C remains exactly 4800 / 16 = 300 callbacks/frame in every
  uploaded one-NPC capture, including windows where direct NPC callbacks fall.

Interpretation: do not chase one NPC/tribe as the primary bug. The expensive
active-set behavior is distributed, shared, nonlinear, or elsewhere in the
manager/display pipeline. 0x8008533C is not linearly multiplied by removal of
one Yold actor.

dev113 chunk256 also remains around ~22.8 dev105 generated nested
steps/attempt in this run; it has not by itself removed callback chunk churn.

### Important semantic side effect

Player observed that one restored NPC (the purple woman) reappeared far from her
normal home position and then walked all the way back. Clearing/re-setting
NPCEntry::flag8.active therefore is not a perfectly transparent pause/resume for
all actor types; reactivation can re-enter placement/pathing behavior.

Future active-bit benchmarks must not let that side effect accumulate between
measurement windows.

### Next test — no native rebuild

A Python-side safe-frame group pulse now uses only already-existing
`npcs info/list`, `read32` and `write32` commands:

    npc_group_pulse <seconds> name1,name2,...

It snapshots each selected live NPC's flag8, clears only bit 0, waits in wall
time, re-reads the current flag word and restores only bit 0. It validates the
map and NPC table before restoration and refuses stale-address restoration.

Run:

    git pull
    M14-DEBUG-YOLD-GROUP-SWEEP.cmd

No M14 rebuild is required. The current running game is sufficient.

The scripted threshold sweep measures:

- 0 off (baseline);
- 2 off;
- 4 off;
- 6 off;
- all 7 off (shorter 1.25 s window because historical all-off becomes unsafe
  after several seconds).

The room is reloaded between group sizes, specifically to reset actor placement
after the observed purple-NPC reactivation side effect.

Automatic report:
`tools/native_bridge/analyze_yold_npc_groups.py`

Decision:

1. progressively better with 2/4/6/7 off -> per-active-entry scaling;
2. little change through 6 off, then a large all-7 jump -> threshold/shared
   active-set infrastructure;
3. no meaningful all-7 jump in Yold -> historical spikeball result depends on a
   scene/state distinction beyond simple NPC count;
4. callback counts fall but wall time does not -> optimize shared traversal /
   generated dispatch, not actor AI.

## 2026-09-20 — dev30 live one-NPC bisector ready for player test

The next diagnostic is intentionally **console-first**. One native rebuild is
required to add a reusable safe-frame primitive; after that, actor-by-actor
experiments run against the same live process with no rebuilds.

New backend command:

    npcs pulseone <active-ordinal> <1..120 safe-frame ticks>
    npcs pulseone off

Semantics:

- resolves the selected actor from the current active-NPC ordering at the safe
  guest-thread frame boundary;
- records exact table address, NPC id, table index, active ordinal and name;
- clears only `NPCEntry::flag8 bit 0`;
- re-applies only that bit while the pulse is live;
- restores only that bit on explicit `off` or automatic expiry;
- discards rather than restoring stale addresses after map/table/id changes;
- refuses to coexist with the older all-NPC suspend or flag46C isolation modes;
- `resetdebug` restores an active one-NPC pulse too.

Canonical rapid sweep:

    M14-DEBUG-YOLD-NPC-SWEEP.cmd

The sweep requires exactly:

- map `he2_03`;
- 7 active NPCs;
- names `mise,mura_1,mura_2,mura_3,mura_4,mura_5,son`;
- stable NPC table/population before measurement.

It first normalizes all cockpit state, records a 2-second baseline, then pulses
active ordinals 0..6 one at a time. Each actor is held inactive for a 1.85 s
wall-time capture with a 120-safe-frame maximum, then explicitly restored before
the next actor. The backend's automatic expiry is a second safety net.

`analyze_yold_npc_sweep.py` anchors on the newest baseline, accepts only the
seven ordinal captures created within the same 120-second session, proves the
runtime response identified the expected ordinal, requires at least one complete
phase timing window per capture, and reports each actor's FPS / disp+main delta.

Interpretation:

1. one actor produces a large jump -> resolve that NPC/tribe/display path first;
2. all seven produce similar small improvements -> cost scales per active entry;
3. individual removal does almost nothing, while historical all-active removal
   becomes smooth -> shared manager/display infrastructure has nonlinear or
   fixed active-set overhead; build group/subsystem live controls next.

This deliberately does **not** attempt another build-time performance fix before
the bisector result.

### Verification note

The new GitHub workflows are currently not usable as a correctness oracle:
recent `M14 Source Contracts` and the normal native-build workflow both create
jobs with no assigned runner and zero executed steps, then fail immediately.
That is infrastructure-shaped rather than a source-test assertion. Local
`M14-LLVM-UPDATE-RUN.cmd` still runs the source-only contracts before build
and remains the executable gate.

## 2026-09-20 — benchmark hardening + dev114 compiler audit

The performance workflow is now fail-closed enough to use as a durable local
benchmark rather than a hand-read experiment.

Canonical Yold / `he2_03` captures now require all three conditions **before**
measurement:

- exact map: `he2_03`;
- exact active population: **7 NPCs**;
- exact actor-name fingerprint:
  `mise,mura_1,mura_2,mura_3,mura_4,mura_5,son`;
- stable `num/active/entries` identity over the preflight window.

This prevents a different but internally stable story/NPC state — including
another state that also happens to have seven active actors — from being
silently compared against the original seven-townsperson baseline.

Every new scoped debug capture also writes a same-name JSON provenance sidecar
with repo SHA, PID/timing/source-offset metadata, log-rotation status, captured
byte count and capture SHA-256. `analyze_m14_perf.py` verifies any present
manifest; a mismatched/tampered sidecar invalidates the benchmark instead of
being ignored.

New local gates:

```bat
M14-DEBUG-YOLD-BENCH.cmd
M14-PERF-GATE.cmd
```

The Yold launcher now quality-gates the capture automatically. The performance
gate defaults to latest chunk128/sim0 -> chunk256/sim0 and uses distinct exit
codes:

- 0: valid non-regression;
- 3: invalid/incomplete benchmark;
- 4: valid A/B with a material regression.

Default tolerances are explicit/overrideable: -3 median FPS, +0.75 ms
first-retrace critical path and +0.75 ms DrawDone.

### dev114 source-level safety audit

Pinned DolRecomp's `--state-in-memory` implementation was audited against the
exact compiler commit. The important invariants are:

- it is disabled for native-ABI functions;
- eligible CPUState-ABI slots alias CPUState directly;
- dirty-state synchronization skips those aliases;
- reload skips those aliases through the normal `reloadUsedState()` path.

No stale-state conflict was found with dev105's CPUState dispDraw callback
nesting. These assumptions are now executable source contracts in
`prepare_dolrecomp_llvm.py`. Toolchain preparation fails if they drift, records
`state_in_memory_contract=true`, and the transactional LLVM runner refuses to
launch without that proof.

This makes dev114 safer to test later while leaving the default dev113
chunk256/sim0 path unchanged.

## 2026-09-20 — dev114 state-in-memory A/B staged, default remains dev113

A second compiler-side performance candidate is now available **without changing
the default test path**.

Read:

`docs/M14-DEV114-STATE-IN-MEMORY-AB-2026-09-20.md`

Normal/default:

```bat
M14-LLVM-UPDATE-RUN.cmd
```

still means:

```text
chunk=256
state_mem=0
runtime=m14-dev113-chunk256-sim0-yold
```

Opt-in dev114:

```bat
M14-LLVM-STATE-MEM-RUN.cmd
```

means:

```text
chunk=256
state_mem=1
runtime=m14-dev114-chunk256-sim1-yold
```

The main launcher accepts the complete experiment tuple as:

```bat
M14-LLVM-UPDATE-RUN.cmd <graphics> <128|256> <0|1 state-memory>
```

The third argument is explicit and fail-closed; the transactional Python runner
also consumes an explicit `--state-in-memory-mode 0|1`. No empty optional
batch argument is used.

### Why dev114 is high-value

Pinned DolRecomp already has `--state-in-memory`. Its
`slotInMemory()` explicitly disables the mode for native-ABI functions, so the
optimization targets **CPUState-ABI generated bodies**.

That is exactly where dev101 located OpenSPM's hot `dispDraw` callback site
(`0x8005B29C`). With state-memory enabled, most CPUState-ABI guest registers
can live directly in CPUState across generated/runtime boundaries, avoiding
copies in `syncDirtyState()` and `reloadUsedState()`.

This is orthogonal to dev113:

- dev113 chunk256 tries to reduce **how many generated chunks** each callback
  walks;
- dev114 tries to reduce **the cost of state synchronization per CPUState-ABI
  boundary**.

Therefore a good dev114 result may leave `nested_steps/attempt` unchanged.
Judge it primarily by display/main wall time, first-retrace critical-path margin
and produced-frame cadence.

Upstream DolRecomp issue #29 reports substantial state-in-memory gains on several
other games, including turning multiple plain-LLVM results from slower-than-C to
faster-than-C on x86-64 Windows. Treat those numbers only as motivation; SPM
must prove its own result.

### Reproducibility / contamination guards

The generated provenance now records both:

```text
chunk_instructions=128|256
state_in_memory=0|1
```

and the module build fails closed on either mismatch. Pinned DolRecomp's exact
job hash already includes `state_in_memory`, so no manual cache wipe is
required.

The runtime cadence line carries both `chunk=` and `state_mem=`, and the DLL
module version carries `sim0` or `sim1`.

Reporter isolation:

```bat
M14-PERF-REPORT.cmd --compare-latest-chunks
```

compares only:

- chunk128 / state_mem=0
- chunk256 / state_mem=0

so dev114 cannot contaminate dev113.

After a chunk256 sim0 and sim1 Yold capture exist:

```bat
M14-PERF-REPORT.cmd --compare-latest-state-memory
```

compares only those two exact configurations.

### Recommended test order

1. Test default chunk256/sim0 first.
2. Collect a canonical Yold capture.
3. Test chunk128/sim0 when useful and close dev113 with
   `--compare-latest-chunks`.
4. Then test chunk256/sim1 with `M14-LLVM-STATE-MEM-RUN.cmd`.
5. Close dev114 with `--compare-latest-state-memory`.

No manual cache deletion or source editing is part of any A/B.

## 2026-09-20 — dev113 benchmark architecture / chunk A/B harness

Tonight's work intentionally did **not** add another guest/render semantic
optimization on top of dev113. Instead it turned the current chunk256 hypothesis
into a reproducible, self-identifying A/B so the next player test can answer it
cleanly and cheaply.

Read first:

`docs/M14-DEV113-YOLD-CHUNK-AB-HARNESS-2026-09-20.md`

### First-class chunk geometry

The LLVM pipeline no longer hardcodes the test geometry in several unrelated
files. A single `--chunk-instructions 128|256` value now flows through:

`M14-LLVM-UPDATE-RUN.cmd`
-> transactional runner
-> local LLVM module builder
-> DolRecomp generator
-> generated provenance
-> native-module CMake compile definition
-> runtime module identity / cadence telemetry.

Default:

```bat
M14-LLVM-UPDATE-RUN.cmd
```

means chunk256.

Retained baseline:

```bat
M14-LLVM-CHUNK128-RUN.cmd
```

means chunk128.

The generated-tree provenance gate fails closed if the requested chunk geometry
does not match what was actually generated. The native DLL also self-identifies
as either `m14-dev113-chunk128-sim0-yold` or
`m14-dev113-chunk256-sim0-yold`. The periodic dev113 cadence line carries
`chunk=128|256`, so scoped captures remain trustworthy even without the startup
banner.

### Canonical Yold one-command benchmark

With the game running in Yold Town / `he2_03`:

```bat
M14-DEBUG-YOLD-BENCH.cmd
```

The script now refuses the wrong map via `require_map he2_03`, requires the
canonical `active=7` population, verifies a stable NPC table/population before
and after measurement, captures eight seconds of normal unmodified gameplay,
writes a SHA-256 provenance sidecar, and immediately quality-gates/summarizes
the exact scoped capture.

Reporter:

```bat
M14-PERF-REPORT.cmd --latest-capture yold-perf
```

After one chunk128 and one chunk256 capture exist:

```bat
M14-PERF-REPORT.cmd --compare-latest-chunks
```

The analyzer automatically selects the newest self-identified run of each
geometry and compares median FPS, display/main cost, DrawDone cost when present,
first-retrace critical-path margin, and dev105 nested generated steps/attempt.

The exact first-retrace model is kept explicit:

```text
critical ~= disp + main + synchronous DrawDone
margin   = 16.667 ms - critical
```

Do not count the whole `done` phase as CPU; it includes authentic VI waiting.

The analyzer has a synthetic self-test and is now part of
`test_llvm_native_pipeline.py`. The live-debug fake mailbox tests also cover
wrong-room benchmark refusal.

### Next test order

Do **not** manually edit chunk constants or clear caches.

1. Pull/build chunk256 normally with `M14-LLVM-UPDATE-RUN.cmd`.
2. Reach Yold / `he2_03`.
3. Run `M14-DEBUG-YOLD-BENCH.cmd`.
4. On a later launch, build the retained baseline with
   `M14-LLVM-CHUNK128-RUN.cmd`.
5. Repeat the same Yold benchmark.
6. Run `M14-PERF-REPORT.cmd --compare-latest-chunks`.

A useful dev113 result should lower `nested_steps/attempt` and critical-path
time. Because the old Yold path is only roughly 1-2 ms late for the first VI,
even a modest reduction can yield a large 30 -> 60 cadence jump.

## Current frontier — dev113 chunk256 Yold Town deadline-recovery A/B

The final 2026-09-19 player A/B established **Yold Town / he2_03** as the
canonical repeatable performance benchmark. The same NPC table
(`0x8077A0A0`) and the same seven active townsfolk remained present through
both five-second windows while the player ran/jumped in a straight line.

### Stable Yold result

Baseline repeatedly completed only ~30-31 game frames per host second despite
~60 VI retraces:

- display phase was commonly ~6.9-7.7 ms;
- main phase was commonly ~7.7-8.5 ms;
- synchronous DrawDone producer work sampled ~2.31 ms;
- the post-`DEMODoneRender` `done` bucket was ~17.5-18.3 ms.

**Do not interpret the ~18 ms `done` bucket as 18 ms of CPU work.** The
retained phase profiler measures DEMODoneRender -> next DEMOBeforeRender, so it
includes the authentic wait for the next VI retrace. The actionable deadline
picture is roughly:

    disp + main ~= 15 ms
    DrawDone synchronous drain ~= 2.3 ms
    ------------------------------------
    pre-retrace completion ~= 17+ ms > 16.667 ms

Missing the first retrace quantizes presentation to the following retrace,
producing the observed ~30 Hz/jitter. Recovering only roughly 1-2 ms can
therefore be enough to escape the cliff.

Global NPC logic freeze is a clean negative result in Yold Town: the same seven
actors, callback counts, phase cost and ~30-31 frame cadence remain essentially
unchanged. Passive NPC AI/update logic is not the primary owner here.

dev112 also separates direct NPC display callbacks from the larger traversal:

- exact NPCEntry-owned callbacks: 256 / 16 frames = 16/frame;
- targets: `0x801B43CC` = 12/frame, `0x801B49A0` = 4/frame;
- dominant dispDraw callback: `0x8008533C` = 4880 / 16 frames = 305/frame;
- public decomp places `0x8008533C` inside the broad mapdrv text region, not
  inside npcdrv; exact symbol remains unresolved.

Mario's guest X position is nearly monotonic at ~6 units per produced frame
while the player holds one direction. Treat the remaining visible movement
jitter as missed/uneven frame delivery first, not a Mario physics bug.

### Why dev113 changes chunk size

dev105 is mechanically successful but expensive. Prior player evidence showed
~91.7% of dispDraw callbacks can return exactly to the caller, yet the bounded
helper still needed ~20 generated-dispatch chunks per callback after dev108's
partition reduction. Yold Town now measures roughly 370 dispDraw callbacks per
produced frame, so thousands of artificial generated-chunk transitions remain
on the critical path every frame.

dev113 changes exactly one compiler geometry variable:

    DOLRECOMP_LLVM_CHUNK_INSTRUCTIONS=128 -> 256

Everything semantic stays unchanged:

- `--native-abi unrestricted` remains;
- indexed dispatch remains;
- dev107 broad direct-BL native-first remains **disabled**;
- dev103 exact negative host-call prefilter remains;
- dev90's three proven structural native-first entries remain;
- dev97 HP-zero correctness partitions remain;
- dev57/direct-BL synthetic performance entries remain legal cold entries
  without being forced partition boundaries;
- dev105 exact `dispDraw` site, 32768-cycle quantum and fail-closed semantics
  remain;
- renderer/GX/VI semantics are unchanged.

This is the natural follow-up to dev65's successful 64 -> 128 architectural
change, now justified by the measured callback/chunk-walk frontier rather than a
generic larger-is-better assumption.

Codegen identity:

    llvm-inline-wpar-indirect-budget-v20-dev113-chunk256-yold

Runtime module identity:

    m14-dev113-chunk256-sim0-yold

### Next player test (not needed tonight)

Run the ordinary:

    M14-LLVM-UPDATE-RUN.cmd

No manual cache wipe. Chunk geometry is part of the generated job identity and
the codegen provenance was bumped, so affected LLVM objects must rebuild rather
than silently reusing chunk128 output.

Use Yold Town / `he2_03`, run/jump straight through the town, and compare:

1. completed frames per ~1 s phase window;
2. `disp_avg_us + main_avg_us`;
3. DrawDone producer-sync average/p95;
4. dev105 `nested_steps / attempts` at teardown;
5. object count / generation marker must say `chunk=256`;
6. basic boot/gameplay and one Game Over smoke check only if convenient.

A meaningful success is not required to halve CPU time: moving the pre-retrace
critical path comfortably under 16.667 ms should produce a disproportionate
jump from ~30 to ~60 completed frames/s.

## LAST-NIGHT NPC PERFORMANCE FRONTIER — 2026-09-19 23:xx

## Canonical NPC performance benchmark: Yold Town / he2_03

Use **Yold Town (`he2_03`)** as the default human-played NPC-performance
benchmark until superseded.

Why this room is preferred:

- it has a small set of townsfolk that passively walk back and forth;
- the active actor set is stable instead of constantly spawning/despawning;
- the ground is essentially flat;
- the player can reproduce the same run/jump-in-a-straight-line path;
- it has historically shown lag comparable to the noisier spikeball/enemy room;
- the 2026-09-19 stable capture held 7 active NPCs on the same NPC table across
  the entire baseline and logic-freeze A/B.

Do **not** use the spikeball room as the primary precision benchmark: Koopas fall
into the void and spikeballs spawn/roll into the void, so active-NPC population
changes during measurement. Keep that room only as qualitative evidence that
active-bit suppression can immediately restore smoothness.

Player clarified that the historical he2_02 enemy room is not a clean
microbenchmark: green Koopas fall into the void and spikeballs spawn from the
sky and roll into the void. The recent captures confirm active population drift:
baseline had 9 active NPCs while the later hidden/frozen window had 10.

Do not use he2_02 callback counts as precision A/B data. Preserve its qualitative
result only: destructive NPC active-bit suppression makes the scene immediately
smooth, but leaving those entries inactive destabilizes/crashes the game.

The old hidden/frozen experiment is also retired from the standard benchmark:
it did not reproduce the smooth state and increased display cost/callback volume.

### Final player test for tonight

Build current head normally, choose a reasonably crowded room with persistent
actors, then run:

    M14-DEBUG-NPC-AB.cmd

The script now:

- samples NPC num/active/table identity for 2 seconds and aborts on drift;
- records a 5-second stable baseline;
- records status/map/position + compact active NPC index/id/tribe/name inventory;
- records dev111 callback census + Mario produced-frame movement;
- records dev112 dispDraw callbackParam ownership against the live NPCEntry table;
- samples stability again;
- records a 5-second global NPC-logic-freeze window with active/render identity
  unchanged;
- performs a final stability check;
- never uses hidden/frozen isolation or destructive active-bit suppression.

Player should move Mario steadily in one direction during both capture windows
when practical. This makes dev111 movement-step reports useful for the separate
jitter diagnosis.

### Independent next-turn decision tree

After the two stable captures arrive, do not ask for another test before doing
repo work.

1. Compare dev112 NPC-owned callback target counts baseline vs logic-freeze.
2. Correlate those targets with dev111 all-callback census and phase timing.
3. If one/few NPC-owned callback targets dominate display:
   - resolve each target against generated/native map/decomp artifacts;
   - inspect whether it unnecessarily escapes LLVM through dev105/public dispatch;
   - implement a narrowly proven native/direct-call or batching fast path;
   - preserve semantic host seams and fail closed on lookup refusal.
4. If NPC-owned callback counts are modest but active-NPC state still strongly
   predicts display cost:
   - trace npcDispMain / dispEntry registration and active-entry iteration;
   - look for repeated full-table work or callbacks registered multiple times.
5. Use dev111 Mario movement reports:
   - x_reversals while input direction is held => guest simulation/state bug;
   - monotonic guest steps but visible jitter => presentation/frame-delivery bug;
   - large alternating step sizes with no reversals => missed/uneven produced
     frames, fix cadence before interpolation.
6. Keep he2_02 historical data only as qualitative confirmation that the active
   bit gates the expensive path.

### Instrumentation now staged

- dev111: exact dispDraw target census every 16 produced frames + post-main Mario
  X/Z step statistics.
- dev112: classifies dispDraw r4 callbackParam against cached live NPCEntry table,
  reporting param-in-table and exact-entry target counts without mutating NPCs.
- dev29 cockpit logs command BEGIN/END for first 64 commands so any future crash
  pins to the exact command boundary.
- `stable_npcs <seconds> [interval]` aborts scripted A/Bs on population drift.
- `npcs list` returns compact active NPC index/id/tribe/name inventory.
- standard NPC A/B is non-destructive.

## LIVE RESULT — NPC-scaled lag causally localized (2026-09-19 evening)

The dev29 cockpit is player-proven working after the Tk geometry and Windows
shared-memory client fixes. Live commands confirmed working include room
navigation, position nudge, HP, movement speed and NPC controls.

Most important new performance evidence:

- in the known laggy enemy room, `npcs suspend on` (clearing every active
  NPCEntry active bit) made the game immediately run smoothly;
- after a few seconds the room crashed, so full active-bit suspension is too
  invasive for sustained A/B work;
- nevertheless, the immediate cadence recovery is strong causal evidence that
  the remaining slowdown scales with active NPC workload rather than being a
  generic VI cap or scene-independent renderer problem.

New safe-isolation cockpit support is now staged:

- `npcs freeze on|off`: global NPC logic freeze, render identity retained;
- `npcs isolate freeze [keep]`: per-entry documented frozen bit, keeping the
  first N active NPCs normal;
- `npcs isolate hide [keep]`: documented hidden+frozen bit, still leaving the
  entries active structurally;
- `npcs isolate off`: restore only the diagnostic flag46C bits;
- `npcs entry <index>`: report ID/tribe/name/flags/position for actor mapping;
- default NPC A/B no longer performs destructive active-bit suspension;
- `hp N` now sets current and max HP together; `hp current max` is available
  for asymmetric tests.

The next player build should be `M14-LLVM-UPDATE-RUN.cmd` with no cache wipe.
Then, in the same laggy room, run the updated `M14-DEBUG-NPC-AB.cmd`.
The highest-value discriminator is the already-exposed `npcs freeze on`:
smooth freeze => update/AI/event side; slow freeze + smooth hidden isolation =>
display/render side; partial improvement in both => both sides scale with NPCs.

If hidden isolation is smooth, bisect with `npcs isolate hide N` and inspect
boundary actors with `npcs entry N` before touching another global renderer
optimization.

## TEST-READY CHECKPOINT — dev110/dev29 live debug cockpit

The feature surface is frozen for the next player build. The intent is to build
once, leave OpenSPMNative running, and spend the next several days doing
runtime experiments through commands/scripts rather than recompiling for every
hypothesis.

Primary checkpoint guide:

`docs/M14-DEV29-LIVE-COCKPIT-TEST-CHECKPOINT-2026-09-19.md`

Build:

`M14-LLVM-UPDATE-RUN.cmd`

No cache wipe.

Expected preflight/CTest/runtime gates:

- source preflight includes
  `dev110/dev29 live debug cockpit + reversible experiment harness`;
- the source preflight now also executes pure-Python fake-mailbox behavior tests
  for timed `sample`, scoped launch.log capture, deferred cleanup, and
  Stop-during-wait auto-cleanup;
- `m14_exi_diagnostic` requires backend `14.0-dev29`, and the source
  preflight itself verifies that test version so a stale version assertion
  cannot survive to late CTest again;
- runtime marker:
  `M14 dev29 live debug cockpit ready`.

After launch:

- GUI: `M14-DEBUG-CONSOLE.cmd`
- CLI: `M14-DEBUG-CONSOLE.cmd --cli`
- smoke: `M14-DEBUG-SMOKE.cmd`
- heavy-room A/B: `M14-DEBUG-NPC-AB.cmd`

### Final audit fixes included in this checkpoint

- backend/test/runtime diagnostic version pointers normalized to dev29;
- GUI/CLI cross-process mailbox sends serialized by
  `Local\\OpenSPM.Debug.Client.<pid>`;
- response generation must match the submitted generation exactly;
- GUI scripts no longer hold the outer connection lock during waits, so manual
  commands can be issued during a timed window;
- `defer` / `onexit` cleanup runs after success, failure, or Stop;
- active scoped capture auto-closes after cleanup;
- capture handles launch.log truncation/restart;
- GUI close issues one best-effort `resetdebug`;
- `resetdebug` restores NPC controls/speed and clears memory freezes/checkpoint;
- explicit cockpit room changes reset all temporary debug state first;
- next/previous room scans over holes in numeric sibling map IDs;
- same-map position checkpoint added;
- timed `sample <seconds> <count> <command>` added;
- movement speed override stops on character change;
- NPC suspend refuses incomplete snapshots and restores only the active bit it
  changed;
- NPC suspension is keyed to both the NPC table and current map, so normal map
  changes cannot restore stale NPC state into the next room;
- exact new data assumptions were re-checked:
  - dev96 already proved US2 marioWork `0x804CEE58`, MarioPouchWork
    `0x804D0428`, HP `+0x0C`, position `+0x5C`;
  - public US2 symbol map gives `npcdrv_wp=0x8056CB88`;
  - public NPC header gives NPCWork `flag +0x0`, `num +0x4`,
    `entries +0x8`, freeze-all bit `0x1`, NPCEntry size `0x748`,
    flag8 `+0x8`, active bit `0x1`;
  - public Mario header gives character `+0x38`, walk speed `+0x14C`,
    dash speed `+0x150`.

### First live validation order

1. `M14-DEBUG-SMOKE.cmd`
2. Next Room -> Prev Room -> Reload Room in an ordinary numbered map
3. save position -> nudge -> restore position
4. speed 1.5 -> Restore
5. run `stop_cleanup_test.spmdbg`, press Stop during the wait, confirm state
   recovery + saved scoped capture
6. after Restore, jump into a pit and verify Game Over remains correct
7. in the known heavy NPC room run `M14-DEBUG-NPC-AB.cmd`

Do not add another renderer/native optimization before interpreting the three
NPC A/B captures.


### dev29 polish addendum

Additional audit fixes landed after the initial cockpit handoff:

- same-map `checkpoint save|load|clear` for instant position recovery;
- client-side `sample <seconds> <count> <command>` for timed watches;
- cross-process named Windows mutex serializes GUI and separate CLI clients on
  the shared mailbox; responses now require exact generation equality;
- `resetdebug` restores NPC controls, speed, memory freezes and checkpoint in
  one safe-frame command; GUI Restore and window close use it;
- natural (non-cockpit) map changes are detected by NPC table replacement while
  suspend is active; suspend discards stale snapshots and auto-disables rather
  than touching the new room;
- if the live NPC table grows beyond reversible snapshot capacity, suspend
  restores what it changed and auto-disables instead of silently becoming a
  partial experiment;
- scoped capture names are bounded and capture-start state rolls back if its
  begin marker cannot be delivered;
- every C test was swept for stale dev27/dev28 backend-version assertions. The
  only exact backend version assertion is now the intended
  `m14_exi_diagnostic -> 14.0-dev29`;
- source preflight itself now reads that EXI test, so the next backend version
  bump cannot forget it without failing at LLVM step 2/7.

The connector-backed source audit is complete. This environment could not clone
GitHub into the local container (DNS unavailable), so the authoritative compile,
Python AST contract execution, Windows DLL tests and runtime behavior remain the
normal player-side `M14-LLVM-UPDATE-RUN.cmd` gate.


## Current frontier — dev110/dev29 live debug cockpit polish

The live-debug transport has grown into a reversible runtime experiment harness.
Backend version is now **14.0-dev29**; the previous dev28 version remains only
historical context below.

Latest audit/polish changes:

- GUI launches by default from `M14-DEBUG-CONSOLE.cmd`; one-shot commands and
  `--script` still route to the CLI client.
- Python mailbox sends are serialized internally, while GUI scripts no longer
  hold the outer connection lock across waits. Manual probes can therefore run
  during a timed experiment without corrupting mailbox generations.
- Script language adds `defer` / `onexit`. Deferred commands run in reverse
  order after success, failure, or Stop. Active log captures auto-close after
  cleanup.
- launch.log capture detects truncation/restart and writes scoped snippets under
  `.openspm/m14/debug-captures`.
- GUI adds Run Selection, Snapshot, Restore, transcript save, speed reset, and
  safer Stop messaging.
- room navigation: `nextroom` / `prevroom` scan over missing numeric sibling
  IDs; `reloadroom` remains exact-current-map.
- NPC isolation is fail-closed and reversible:
  - `npcs freeze on|off` toggles NPCWork's documented global freeze bit;
  - `npcs suspend on|off` snapshots every active entry and clears only the
    documented NPCEntry active bit;
  - suspend refuses if the complete table exceeds snapshot capacity;
  - restore writes back only the active bit it changed, never an old whole
    flag word;
  - cockpit NPC controls are reset before any room warp so stale NPC addresses
    cannot bleed across maps.
- speed multiplier captures Mario's walk/dash values and now disables itself on
  character change rather than imposing one character's speeds on another.
- `moonjump` remains deliberately unarmed until the exact US2 jump/fall
  parameter fields are proven. Use Y nudge as the safe platforming shortcut.
- backend/runtime diagnostic labels and the EXI lifecycle test now target dev29.

Checked-in experiment scripts:

- `tools/native_bridge/experiments/cockpit_smoke.spmdbg`
- `tools/native_bridge/experiments/npc_performance_ab.spmdbg`
- `tools/native_bridge/experiments/stop_cleanup_test.spmdbg`

Source-only pipeline contracts now AST-parse both Python clients and require the
dev29 backend version, GUI, reversible NPC controls, script cleanup/capture
semantics, and current documentation.

### Next player validation

Run `M14-LLVM-UPDATE-RUN.cmd` with **no cache wipe**.

Expected gates:
1. source-only native-pipeline contracts pass with
   `dev110/dev29 live debug cockpit + reversible experiment harness`;
2. selected CTest batch passes, including `m14_exi_diagnostic` expecting
   `14.0-dev29`;
3. runtime reports
   `M14 dev29 live debug cockpit ready`.

Then leave the game running and open `M14-DEBUG-CONSOLE.cmd`.

Validation order:
1. run `cockpit_smoke.spmdbg`;
2. in an ordinary numbered map, test Next Room then Prev Room;
3. test speed 1.5 then Restore;
4. run `stop_cleanup_test.spmdbg`, press Stop during its wait, and confirm NPC
   motion/speed recover plus a scoped capture is saved;
5. in the known heavy NPC room run `npc_performance_ab.spmdbg`;
6. after Restore, smoke-test the jump-into-pit/Game Over path once.

Interpret the three NPC captures causally:
- freeze helps strongly, suspend little more -> update/AI-heavy;
- freeze little, suspend strongly -> display/render-heavy;
- both similar -> shared active-NPC work;
- neither -> NPC count is probably correlated with another scene subsystem.

Do not add another global renderer/native optimization until this A/B identifies
which side of the NPC workload owns the missed-frame budget.


## Dev110 CTest repair — EXI contract version

The next player build advanced through configure/build and reached CTest. Four of
five selected M14 tests passed; only m14_exi_diagnostic failed because its DLL
lifecycle contract still hardcoded backend version 14.0-dev27 after dev110 moved
the backend to 14.0-dev28. No EXI/GX/runtime behavior failed.

Patched tests/m14_exi_diagnostic_test.c to require dev28 and updated its dev28
wrapper comments/pass text. Commit: e396dbc2acc921a904af7afe96ad2cb76c7d3335.

Next: rerun M14-LLVM-UPDATE-RUN.cmd. No cache wipe.


## Dev110 build repair — fused dev109 Aurora cache idempotency

First player attempt at dev110 stopped during CMake configure before compiling the
new backend. Source-only contracts passed. The failure was entirely inside
renderer/aurora/apply_guest_array_hotpath_v2_patch.py while re-reading an
already-dev109 cached Aurora checkout:

    dev89 applied source is missing zero-vertex guard and the exact one-index
    scan anchor is not unique

Root cause: dev89's historical repair looked specifically for its old
6-space-indented zero-vertex guard inside the one-index branch. dev109's fused
scanner intentionally moved the same semantic guard outward and emits it with
4-space indentation. Because the transform checked the old literal before
checking FUSED_SIGNATURE, an already-correct dev109 checkout was misclassified
as stale dev89 and the migration then searched for an anchor dev109 no longer
contains.

Repair:
- detect the semantic "if (vtxCount == 0u) continue;" independent of indentation;
- never run the legacy dev89 zero-guard repair when FUSED_SIGNATURE is already
  present;
- retain the final required-applied verification, so malformed fused source
  still fails closed;
- regression fixture now models dev109's real 4-space guard.

Commits:
- b78f3efc1e78994a5a4bd41bf07117e76d25314d
- 38c479ff88f36416ca28c763ecd58bf95398e7c2

Next: rerun M14-LLVM-UPDATE-RUN.cmd with no cache wipe. The expected configure
result is "Aurora guest-array hotpath optimization: already-applied" (or an
equivalent successful repaired/applied status), then the build should proceed
far enough to compile openspm.native 14.0-dev28 and launch dev110.


## Current frontier — dev110 live runtime debug console

Player log launch(20260919-214532).log tested dev109 at repo fa49904085b7.
The log proves "M14 raw guest-array fused scan-plan active" was loaded, but the
same NPC-heavy areas remain visibly laggy / quantized around 30 produced frames
per second. dev109 is therefore a real negative performance result, not a stale
build.

The development loop is now the bigger bottleneck. Rebuilding and replaying the
game for every instrumentation or state-isolation hypothesis is too slow.

### dev110

dev110 ports the useful M13 live-debug IPC idea into the current standalone
native backend.

Backend:
- creates Local\OpenSPM.Debug.<OpenSPMNative PID>;
- consumes commands only at the existing once-per-frame
  OPENSPM_M14_HOST_EVENT_PRESENT_SAFEPOINT while the guest CPU is paused;
- launcher/client never maps MEM1/MEM2 directly;
- backend version advances to 14.0-dev28.

Client:
- root M14-DEBUG-CONSOLE.cmd;
- tools/native_bridge/m14_debug_console.py;
- auto-detects a running OpenSPMNative.exe;
- interactive mode, one-shot commands, or --script file.txt;
- script-local "sleep <seconds>" support.

Initial safe-frame commands:
- help / status / perf / seq / map
- story [value]
- gsw <index> [value]
- warp <map>
- read8/16/32 <addr>
- write8/16/32 <addr> <value>
- dump <addr> <len<=128>

warp uses exact R8PE01/US2 mapData and the private seqSetSeq map-change globals;
it refuses outside gameplay sequence 2. Raw writes are guest DATA mutations
only: they do not rewrite already-generated LLVM/x86 code.

Source contracts parse the Python client and assert the mailbox/safepoint,
semantic commands, script mode, and development-only documentation.

Relevant commits:
- backend mailbox/commands: b94fa1272575d92b81f9102de1d47087d72cfba7
- varargs include fix: 59259b09da6644d29d97d97e4a67f081837c9786
- console client: 25d3e6fe0649ab7568a42f9e0d1e0a999d9d031f
- root launcher: 9ef5f6b4e5125e77f933cdbae9319f1c21a8ea92
- docs: 3a887913d662ee1bf195a27ee3ac0279448cde50
- source contracts: 4fe6e19fab258b02158d8d7a3d6b3b9dfd7fc11d

### Next player workflow

1. Run M14-LLVM-UPDATE-RUN.cmd once to build dev110.
2. Leave the game running.
3. Open a second terminal in the repo and run M14-DEBUG-CONSOLE.cmd.
4. In the heavy NPC room run:
       status
       map
       story
5. Send the returned map/door + any new log if useful. From then on, use live
   scripts/commands to identify actor-manager state and add purpose-built
   actor enumeration / update suppression / scoped timing commands.

The intended next performance experiment is NOT another blind renderer tweak.
Use the live console to locate the scene/NPC manager and build a runtime actor
bisector: enumerate actors, suppress one class/group at a time at safe update
boundaries, and correlate immediate frame cadence. That can directly answer
whether lag scales with actor update, display callbacks, GX generation, or some
shared per-NPC subsystem without rebuilding between hypotheses.


## Current frontier — dev109 fused exact guest-array scan

Player log `launch(20260919-211303).log` tested dev108 at repo
`6bbdc7b39025`.

### dev108 result

The partition-only A/B **boots and plays**, proving the reduced ~15k-object
layout is viable. The dev107 boot hang was caused by the broad decoded
direct-BL native-first execution path, not by the reduced partition count
itself. Keep dev108's policy: decoded direct-BL source PCs remain legal
entry-only metadata but use historical host-first / BL-rescue execution;
dev90's three proven native-first entries remain active.

The reduced layout lowered dev105 generated-dispatch walking from the prior
~22.87 nested steps/attempt to ~19.66 (33763420 / 1717013), roughly a 14%
improvement, but it did **not** remove the heavy-room 30 Hz cliff.

Representative bad windows in the new log:

- 29 frames: disp 9.422 ms + main 7.136 ms = 16.558 ms before DoneRender.
- 30 frames: disp 9.309 ms + main 8.037 ms = 17.346 ms.
- 30 frames: disp 8.650 ms + main 8.191 ms = 16.841 ms.
- later 29-frame window: disp 10.910 ms + main 7.617 ms = 18.527 ms.

VI/retrace remains ~60 Hz. This is still the same missed-16.667-ms-deadline
quantization, not a 30 FPS cap.

The renderer clue is now sharper: in the same heavy stretch,
`draw_done` producer synchronization reaches ~2.78 ms average while the
ordinary present safepoint is ~0.04 ms. Renderer dev4 already proves DrawDone
does **not** present or wait for GPU idle here; this time is synchronous
`aurora::gx::raw::drain()` CPU parsing/draw preparation.

Do not revive the old threaded/async FIFO experiment. It let Broadway mutate
guest RAM before Aurora resolved display-list/array/texture references and
caused black-square / invisible-Mario / timing-dependent corruption.

### dev109 candidate

dev109 keeps all synchronous visibility semantics and optimizes only the
mandatory raw guest-array index scan in
`apply_guest_array_hotpath_v2_patch.py`.

dev89 built one descriptor scan plan per draw, but then walked the entire
interleaved vertex stream once **per indexed attribute**. dev109 fuses those
passes: each vertex record is walked once and every indexed attribute's exact
min/max interval is updated during that pass. NBT3 keeps its exact per-slice
offset/bounds semantics. The resulting guest byte intervals, exact snapshot
memcmp policy, staging bytes, FIFO ordering, rendering thread model and
visibility timing are unchanged.

The migration upgrades already-cached dev89 Aurora source automatically and
fresh checkouts emit the fused scanner directly. Source-only contracts cover
the fused marker/loop and the cached dev93 repair fixture.

Commits:
- implementation: `a18fa6c02ad4699cb788c40435d3a0233668da13`
- source contracts: `dc6e95f8f29c89a06b1fba63b8e678bc92491812`

Next: run `M14-LLVM-UPDATE-RUN.cmd`, no cache wipe. Confirm the configure/build
prints/launch log contains `M14 raw guest-array fused scan-plan active`.
Then use the same worst NPC-heavy room and compare:
1. `producer sync window kind=draw_done` average/p95;
2. `disp_avg_us` and `main_avg_us`;
3. completed frames per host second;
4. dev105 nested steps/attempt (should be largely unchanged by this renderer-only A/B).

A successful result needs the pre-wait total comfortably below 16.667 ms;
merely moving presentation timing is not sufficient.



## Current frontier — dev108 isolates the 15k-object layout from the boot hang

Player log `launch(20260919-204920).log` tested dev107 at repo
`77203988c55d`. The candidate becomes unresponsive before normal game/render
startup. From the first heartbeat onward the native CPU is stationary at
`PC=LR=0x8026CA60`, with no BL rescues, no region queries, no write-gather
traffic and after the first second essentially no ordinary host callbacks.
Each run slice still charges exactly 65536 guest cycles. This is a generated
native execution trap, not a renderer stall.

The smaller ~15k generated-object count is expected from dev107 removing generic
dev57 and decoded direct-BL source symbols from LLVM partition boundaries. That
count is not itself evidence of a bad build. dev107 combined two changes,
however:

1. reduced partitions / entry-only legal restart PCs;
2. broad direct-BL source native-first execution.

dev108 separates them. It **retains the reduced partition / legal-entry layout**
(and therefore should retain the lower object count) but disables broad dev107
direct-BL native-first dispatch. Direct-BL source PCs again use historical
host-first / BL-rescue semantics. dev90's three previously proven exact
native-first entries remain unchanged.

If dev108 boots at ~15k objects, the object reduction is vindicated and dev107's
broad native-first execution was the boot regression. If dev108 still stalls,
the next step is to restore only the specific partition family/address needed,
starting from the stationary PC, rather than reverting all performance-oriented
partition reduction.

Codegen:
`llvm-inline-wpar-indirect-budget-v19-dev108-partition-only-ab`

Module:
`m14-dev108-partition-only-ab`

Next: run `M14-LLVM-UPDATE-RUN.cmd`, no cache wipe. First criterion is simply
boot progress past 0x8026CA60. If it boots, immediately test the same heavy-NPC
room and compare object count / dev105 nested steps / disp+main cadence.



## Current frontier — dev107 targets chunk fragmentation behind the 30 FPS cliff

Player log `launch(20260919-190301).log` tested repo `820e31393b17` /
dev106+dev105. Game Over now works on the reproduced jump death. Correctness is
not the priority unless a new performance candidate regresses it.

Performance result:

- dev105 mechanically succeeds: 2,146,113 / 2,340,044 dispDraw callbacks reach
  the exact continuation (~91.7%);
- that success costs 53,522,213 nested generated-dispatch steps in one session;
- sustained heavy NPC windows fall to 30-31 completed frames/s with
  disp ~8.6-8.8 ms + main ~7.5-7.7 ms;
- DoneRender then waits roughly another retrace, producing ~33 ms frames;
- host Present itself remains cheap (~0.207 ms average at shutdown), so the
  bottleneck is pre-present native execution/chunk churn, not GPU presentation.

A prior diagnosis had restored every synthetic symbol as a real LLVM partition
because dev98 was blamed for pit-death failure. The user's later walk-vs-jump
clarification invalidated that causal attribution. Legal restart entry and LLVM
chunk boundary are separate concepts in DolRecomp.

**dev107** therefore keeps exact legal entries while reducing fragmentation:

1. generic `openspm_dev57_addr_taken_*` rows remain legal cold entries but do
   not force chunk boundaries;
2. every decoded direct-BL source is also added as a legal entry without becoming
   a chunk partition;
3. dev97 HP-zero rows remain real partitions for correctness;
4. safe direct-BL source PCs try generated native execution first only when they
   are outside semantic host sets and compiler FALLBACK blocks; refusal falls
   immediately back to the historical BL rescue.

This is the highest-value current experiment because it directly attacks the
53.5M chunk-walk count and the remaining direct-BL rescue churn without removing
restart legality or semantic host handling.

Codegen:

`llvm-inline-wpar-indirect-budget-v18-dev107-direct-bl-native-first`

Module:

`m14-dev107-direct-bl-native-first`

Next test: run `M14-LLVM-UPDATE-RUN.cmd`, no cache wipe. Prioritize the same
NPC-heavy room. Compare completed frames, disp/main averages, dev105
nested_steps per attempt, dev107 native-first hits/fallbacks, direct-BL rescues,
and generated object count. Game Over only needs a quick smoke check.



## Current frontier — dev106 compares native death-entry truth within one build

Player log `launch(20260919-173529).log` tested dev104 at
`1622b99ace4c`. The failed jump-into-pit repro reaches HP=0 / motion 24 /
y=-150 and then remains in SEQ_GAME(2), next_seq=2 while the game keeps
simulating. The user clarified that successful Game Overs were observed when
walking off the edge, whereas the softlocks were observed after jumping in.

That distinction is useful, but prior successes/failures also crossed different
candidate builds. dev106 therefore makes the next test a **within-build**
comparison instead of assuming the input route is already proven causal.

dev105's multiblock `dispDraw` performance A/B remains active for one run:
exact site `0x8005B29C` may walk up to 64 generated chunks / 32768 accumulated
guest cycles, failing closed on fallback, exception, miss, no progress or limits.
dev103's proven generic dev57 host-filter remains active.

The correctness probe is strengthened. Public US2 symbols identify:

```text
onHpEquals0 = 0x801615E4
seqSetSeq   = 0x8017B834
```

Generated LLVM now calls the host-only
`openspm_m14_note_death_entry_native(pc)` at those exact block entries. It
passes only the compile-time guest PC and does **not** materialize CPUState or
modify guest state. The first HP=0/MOT_24 trace line reports cumulative and
since-previous-live-frame deltas:

```text
native_onHp0=<total>(+<delta>)
native_seqSetSeq=<total>(+<delta>)
host_onHp0=<total>
```

This distinguishes "never entered onHpEquals0" from "handler ran but never
entered seqSetSeq" from "seqSetSeq ran but next_seq stayed 2." No Game Over is
forced.

Codegen:

`llvm-inline-wpar-indirect-budget-v17-dev106-native-death-entry-truth`

Module:

`m14-dev106-native-death-entry-truth`

Read first:
`docs/M14-LLVM-DEV106-NATIVE-DEATH-ENTRY-TRUTH-HANDOFF-2026-09-19.md`.

Next test: run `M14-LLVM-UPDATE-RUN.cmd`, no cache wipe. If practical, trigger
**one walking-off death and one jumping-into-pit death in the same dev106
build**, then spend 15–20 seconds in the same NPC-heavy room. Compare the dev106
native-entry deltas and dev96 next_seq transition for the two death routes.
Also inspect dev105 multiblock normal_returns/attempts/nested_steps; if it still
does not materially improve the heavy room, retire that line of optimization.



## Current frontier — dev105 separates jump-death correctness from NPC dispatch cost

Player log `launch(20260919-173529).log` tested dev104 at repo
`1622b99ace4c`. The user clarified that prior successful Game Overs were from
**walking off the edge**, while the reproducible softlock is from **jumping into
the pit**. Those are distinct bottomless-death routes and must not be used as
interchangeable correctness tests.

The latest jump repro is unambiguous: HP reaches 0 in motion 24, but no
`next_seq=4` observation occurs and shutdown remains
`final_seq=2 final_stage=1 final_next_seq=2`. The game continues simulating;
this is a gameplay-state softlock, not a renderer/scheduler deadlock.

dev104 also proves the exact CPUState `dispDraw` site is reachable, but its
one-block nesting strategy is mostly ineffective:

```text
attempts=2961408
handled=2961408
normal_returns=376344
abnormal_returns=2585064
lookup_misses=0
budget_yields=0
```

Only ~12.7% of callbacks reach PC+4 after one generated dispatch. Most stop at
another generated/intermediate PC. dev103's generic dev57 host-filter reduction
still holds and should be retained.

**dev105 candidate** does two bounded, independent things:

1. exact site `0x8005B29C` now walks the existing generated dispatcher for up
   to 64 chunks / 32768 accumulated CPUState guest cycles, stopping only at
   exact PC+4 or failing closed to the historical outer path on fallback,
   exception, miss, no-progress or limit;
2. HP=0/MOT_24 at `spmarioMain` now emits a bounded jump-death trace containing
   previous motion, subMotionId, poseTime, airTimer, position, seq and next_seq,
   plus host-visible entry counts for US2 `onHpEquals0=0x801615E4`.

No Game Over is forced and no callback is skipped/reordered.

Codegen:

`llvm-inline-wpar-indirect-budget-v16-dev105-multiblock-dispdraw-pittrace`

Module:

`m14-dev105-multiblock-dispdraw-pittrace`

Read first:
`docs/M14-LLVM-DEV105-MULTIBLOCK-PITTRACE-HANDOFF-2026-09-19.md`.

Next test: run `M14-LLVM-UPDATE-RUN.cmd`, no cache wipe. **Jump into the pit
first; do not substitute walking off.** Then test the same NPC-heavy room for
15–20 seconds. Inspect dev105 jump-death truth/onHpEquals0 lines and multiblock
normal_returns/nested_steps/limit counters.


## Current frontier — dev104 fixes CPUState nested-budget semantics after dev103 win

Player log `launch(20260919-162912).log` tested repo `d9ae710d0513`.

Two results are now player-proven and should be retained:

- **dev103 works.** Generic `openspm_dev57_addr_taken_*` rows were imposing a
  real per-callback C semantic-router tax. The latest run ends with only
  7,488,744 sampled ordinary hostcalls / 7,474,560 production-router ordinary
  calls, versus 88,347,855 sampled ordinary calls in the prior dev101 run.
  Current heavy-play cadence is mostly low tens of thousands of ordinary
  callbacks/s instead of the old ~0.30–0.48M/s range.
- **retiring dev100 restores pit Game Over.** HP reaches 0 in motion 24,
  `next_seq=4`, SEQ_GAMEOVER advances through stages 1..6, and control returns
  to seq 1. dev101 already showed partition restoration alone did not repair
  this, so do not re-enable dev100's generated per-call CFG instrumentation.

dev102 also finally proves the exact measured CPUState `dispDraw` site
`0x8005B29C`: 6,338,293 attempts. But every one of those attempts reports a
budget yield before useful nested execution:

```text
attempts=6338293 handled=0 normal_returns=0 ... budget_yields=6338293
```

That failure is understood. OpenSPM's CPUState/RecompCore `bridge_run()`
resets `CPUState.downcount=0` before a top-level dispatch and later computes
`charge=-downcount`. DolRecomp materialization subtracts generated cycles from
downcount. Therefore **negative downcount is accumulated cycle charge**, not a
positive remaining budget. dev102's inherited `downcount <= 0` guard rejected
every CPUState callback by construction.

**dev104 candidate:** keep native-ABI dev99 unchanged, but route the exact
CPUState site through `openspm_m14_dev104_cpustate_nested_dispatch`. Permit
nested callbacks while accumulated charge is above -32768 and fail-closed back
to the outer dispatcher at the target/continuation once the 32768-cycle batch
quantum is reached. Misses, fallback requests, exceptions, unusual returns and
recursion-depth failures still use the historical outer path.

Codegen:

`llvm-inline-wpar-indirect-budget-v15-dev104-cpustate-charge-quantum`

Module:

`m14-dev104-cpustate-charge-quantum`

Read first:
`docs/M14-LLVM-DEV104-CPUSTATE-NESTED-BUDGET-HANDOFF-2026-09-19.md`.

Next test: run `M14-LLVM-UPDATE-RUN.cmd`, no cache wipe. Trigger pit death
first, then spend 15–20 seconds in the same NPC-heavy room. Expected dev104
stats: attempts nonzero, handled/normal_returns nonzero, and budget_yields far
below 100% of attempts. Pit death must still request seq 4 and return to seq 1.


## dev103 prep blocker fixed — duplicate synthetic CPUState fixture

First dev103 player attempt at repo `2f7e1899501b` stopped in source-only
contracts before any LLVM candidate was generated:

```text
RuntimeError: src\backend\llvm\control_flow.cpp:
dev102 expected exact CPUState indirect escape once, found 2
```

Cause was test-only. `test_dev99_dispdraw_nested_indirect_transform()` had
accidentally accumulated two copies of `CONTROL_FLOW_CPUSTATE_OLD`, duplicate
CPUState patch invocations, and duplicate assertions during the interrupted
dev102/dev103 test edit. The production transform correctly failed closed.

Commit `002cf6c5f8bd489b85e170b2a23875861400487f` replaces that fixture with one
native-ABI anchor plus one CPUState-ABI anchor and one transform/idempotence
check for each.

The exact pinned DolRecomp source
`ExpansionPak/DolRecomp@40637c4683bd2820ac5b23607ee344720beb26df`
was independently checked: its real `src/backend/llvm/control_flow.cpp`
contains the dev102 CPUState escape anchor exactly once. Therefore do **not**
weaken the production exact-count guard.

No candidate DLL launched in the failed attempt. No cache wipe is needed.
Rerun `M14-LLVM-UPDATE-RUN.cmd`.


## Current frontier — dev103 removes two proven per-callback taxes

Player log `launch(20260919-145908).log` tested dev101
(`74e6f822a570`). The dev101 partition rollback **did not** fix pit death:
HP finished at 0 with `final_seq=2`, `final_next_seq=2`, and no Game Over
observation. Do not keep blaming dev98 partition removal for this regression.

The repaired profiler finally produced useful ownership:

- every display callback interval comes from exact `dispDraw` site
  `0x8005B29C`;
- in one heavy 2-frame sample, target `0x8008533C` ran 450 times and owned
  4467 us by itself;
- `spmarioMain -> seqMain(0x8017B72C)` owns nearly the full main phase
  (21312 us over two frames in one heavy sample);
- dev99 still recorded zero attempts because its nesting code was gated to
  `native_abi_`, while the real hot `dispDraw` site is CPUState ABI;
- the residual ordinary-hostcall heavy-hitter sketch is dominated by
  `0x800361F0`, inside ordinary shadowdrv text.

The second root cause is structural: dev87 treated **all compiler-map rows** as
possible host semantics. dev57 added thousands of
`openspm_dev57_addr_taken_*` rows only as legal dynamic callback entries, so
NPC/display callbacks were needlessly crossing into the C semantic router before
native lookup.

Active candidate combines three targeted changes:

1. retire dev100's per-call generated CFG profiler now that it exposed the hot
   site; keep the older phase timer;
2. at exact CPUState-ABI linked indirect site `0x8005B29C`, materialize the
   same architectural state as the old escape, run the existing bounded
   generated dispatcher in-process, and resume only on an exact normal PC+4
   return; all abnormal cases fall back to the historical outer path;
3. remove only `openspm_dev57_addr_taken_*` rows from dev87's map-derived
   may-host set. Real semantic map rows, direct-BL sources, platform targets,
   exact extra semantic coordinates, aliases, and unknown PCs remain host-first.
   **dev97 HP-zero rows remain host-first until Game Over is revalidated.**

Codegen:

`llvm-inline-wpar-indirect-budget-v14-dev103-entryonly-hostprefilter`

Module:

`m14-dev103-entryonly-hostprefilter`

Read first:
`docs/M14-LLVM-DEV103-CALLBACK-DISPATCH-HANDOFF-2026-09-19.md`.

Next test: run `M14-LLVM-UPDATE-RUN.cmd`, no cache wipe. Test pit death first,
then the same NPC-heavy room for 15-20 seconds, close normally, and send the
log. Expected markers include `M14 LLVM dev102 CPUState dispDraw nesting
active:` and `M14 LLVM dev103 entry-only host filter active:`. Teardown
`dev102 dispDraw nested stats` should finally show nonzero attempts. Compare
completed frames, phase timings, dev87 bypass rate, ordinary-hostcall rate/heavy
PCs, and Game Over truth.

GitHub Actions currently fails before job steps/log creation because Actions
minutes are exhausted; local `M14-LLVM-UPDATE-RUN.cmd` remains the executable
validation gate.


## Current frontier — dev101 restores synthetic partitions after pit-death regression

Player log `launch(20260919-141853).log` tested `b4d85fe28e2f` (dev100).
HP reached zero in motion 24, but final sequence stayed 2/stage 1 with no Game
Over observation. dev99 attempts/handled/normal_returns were all zero. All 222
dev100 owner records had target/site zero. Therefore neither dev99 efficacy nor
per-callback attribution was demonstrated; do not claim either worked.

User noticed ~15k build objects versus ~17k earlier. This is consistent with
removing synthetic partition boundaries, but a launch log does not establish
total object count versus rebuilt/cache-hit count. Smaller object count does
not mean fewer actors. Whole-frame windows still fall near 30 FPS, e.g.
disp=8.7ms + main=7.7ms. No evidence here supports a one-actor-per-frame policy.

**dev101 candidate:** retire dev98's removal of BOTH dev57 address-taken and
dev97 HP-zero synthetic partition boundaries. Restore the original all-symbol
partition collector, preserving legal-entry collection and all semantic fixes.
This is a targeted rollback to test a correctness regression, not a proven
root-cause diagnosis. Broader chunks can change native-ABI eligibility and
restart behavior; interior legal-entry status alone was not sufficient proof.

Fix dev100 observation by allowing host-only markers in CPUState-ABI LLVM
bodies as well as native-register-ABI bodies. Neither type needs profiling
CPUState writes. This is still compiled native code; ABI choice is not emulator
fallback. No draw/update callback is skipped or rate-limited by the sampler.

Codegen: `llvm-inline-wpar-indirect-budget-v12-dev101-partition-restore`.
Module: `m14-dev101-llvm-partition-restore`.

Validation: existing pipeline suite PASS; compiled C behavioral suite PASS;
exact restoration of the original collector in a synthetic fixture PASS;
actual pinned compiler cached-source migration and idempotence PASS. Updated
sampler migration and removal of the native-ABI-only gate are tested.
Full Windows/LLVM build, pit-death recovery and FPS remain player-unverified.

Next: `M14-LLVM-UPDATE-RUN.cmd`, no manual cache wipe. Check pit death/Game Over
first, then a crowded room. If death still sticks, inspect actual generated
HP-zero motion entries; do not force Game Over or assume the rollback worked.
If nonzero owner targets now appear, use them to select the next optimization.

## Previous frontier — dev100 repairs dev99 and samples NPC-sensitive work

Latest available player run is still `launch(20260919-031628).log`, repo
`d4239388f54f` (dev98). **dev99/dev100 have not been player-tested here.**

The user reports that more NPCs make the lag worse. Before extending dev99,
this session reproduced and repaired its actual Python SyntaxError: a bare
C-style comment at line 143 prevented the compiler preparation script loading.

Active candidate:

- dev99 bounded display callback nesting, now buildable at the Python stage;
- shared CPU slice-budget checks before nested calls and after normal returns;
- dev100 sampled display-callback / main direct-call interval ownership, one
  frame in 64, including time across native escapes and re-entry;
- all required dev77, dev94 and dev97 fixes retained.

Read first: `docs/M14-LLVM-DEV100-SCENE-OWNERS-2026-09-19.md`.

Codegen identity: `llvm-inline-wpar-indirect-budget-v11-dev100-scene-profile`.
Runtime label: `m14-dev100-llvm-scene-profile`.

Existing Python pipeline contracts, compiled C behavioral tests and applying the
full transform to the exact pinned compiler source pass. LLVM/MSVC compilation
and real FPS improvement remain unverified; do not present them as completed.

Run `M14-LLVM-UPDATE-RUN.cmd`, no cache wipe. Stay 15–20 seconds each in a light
room and an NPC-heavy room; trigger Game Over once and close normally. New log
lines are `M14 LLVM dev100 scene owner`; dev99 teardown adds `budget_yields`.
They measure call **intervals**, including subsequent caller work, not exclusive
function time or literal NPC counts. Use their target PCs to choose the next
specific optimization instead of another general dispatch guess.

## Previous frontier — dev99 bounded dispDraw indirect callback nesting

Latest completed player run:

```text
launch(20260919-031628).log
tested repo: d4239388f54f
```

That run includes dev98 and predates dev99.

### Correctness baseline

dev97 remains a required keeper. The latest run still reaches Nintendo's own
Game Over request, progresses through SEQ_GAMEOVER, and exits normally.

Keep:

- dev77 wedding restart-safe entry;
- dev94 CPU-cached exact guest-array snapshots;
- dev97 HP-zero `[MOT_24,MOT_30)` legal-entry repair.

Do not replace dev97 with a forced Game Over hook.

### dev98 result

dev98 is mechanically valid and correctness-neutral, but it did **not** fix the
heavy-scene lag. Removing generic dev57/dev97 synthetic names from LLVM
partition boundaries did not materially change the player-visible cliff.

Keep dev98 in the current baseline for this A/B, but retire the hypothesis that
synthetic partition fragmentation is the primary bottleneck.

### Latest measured performance baseline

Representative heavy windows from `launch(20260919-031628).log`:

```text
31 / 1032ms  disp=8.786ms  main=7.627ms  done=16.898ms
31 / 1032ms  disp=9.078ms  main=8.295ms  done=15.927ms
36 / 1032ms  disp=6.706ms  main=7.120ms  done=14.848ms
33 / 1032ms  disp=7.615ms  main=8.269ms  done=15.392ms
```

DrawDone remains around ~2 ms in ordinary gameplay after dev94. The unresolved
owner is still native `spmarioDisp + spmarioMain`, with the display phase alone
often costing ~7-9 ms in enemy/NPC-heavy rooms.

### dev99 architectural target

Public SPM structure is:

```text
spmarioDisp -> camDraw -> dispDraw -> every registered DispEntry callback
```

Derived US2 `dispDraw` range:

```text
0x8005B028 .. 0x8005B2F4
```

Pinned/current DolRecomp LLVM treats a linked computed call in legacy
cold-escape mode as `returnNative(target)`. If the target is not simply the
expected return PC, it materializes CPUState and exits the current LLVM body.

That is a pathological match for `dispDraw`, which is a callback walker.

dev99 changes only linked computed calls inside the exact US2 `dispDraw`
range whose PC+4 continuation remains in the same LLVM body:

1. materialize the same architectural state the old escape path would expose;
2. call `openspm_m14_dev99_nested_dispatch(cpu,target,continuation)`;
3. reuse generated `dolrecomp_call()`, including replacements, dev90, dev87,
   semantic host routing, aliases and indexed native lookup;
4. resume the parent body only if the callback returns normally to PC+4 with no
   exception/fallback;
5. otherwise return to the historical outer path.

Recursion uses DolRecomp's existing bounded 24-deep call guard.

Private codegen identity:

```text
llvm-inline-wpar-indirect-budget-v10-dev99-dispdraw-nested
```

Active module label:

```text
m14-dev99-llvm-dispdraw-nested
```

Source contracts now include a synthetic dev99 transform/idempotence test and
lock the actual outer v42 bridge label.

Read first:

`docs/M14-LLVM-DEV99-DISPDRAW-NESTED-INDIRECT-2026-09-18.md`

### Next test

Run:

```text
M14-LLVM-UPDATE-RUN.cmd
```

No cache wipe.

Expected startup:

```text
M14 LLVM dev99 dispDraw nested indirect A/B active:
```

Expected teardown:

```text
M14 LLVM dev99 dispDraw nested indirect stats
attempts=...
handled=...
normal_returns=...
abnormal_returns=...
depth_fallbacks=...
lookup_misses=...
```

Use the same enemy/NPC-heavy rooms and trigger Game Over once.

Interpretation:

- high attempts + high normal-return ratio + lower `disp_avg_us`: keep dev99;
- zero/tiny attempts: derive actual emitted bctrl callsite(s);
- high attempts + low normal returns: callback control flow is not safely
  nestable at broad dispDraw scope;
- high normal returns + no FPS/disp improvement: move to per-callback ownership
  and top-level spmarioMain attribution.


## dev98 prep blocker repaired at `2aa5ca86278039d86d959141d05b471e875274a8`

The first dev98 player attempt stopped in LLVM toolchain preparation before any
candidate object was built:

```text
error: LLVM DolRecomp preparation failed: LLVM entry/partition collector layout changed
```

Cause: the dev98 post-transform verifier assumed
`collect_llvm_entry_points()` appears before
`collect_llvm_partition_points()`. Pinned DolRecomp
`40637c4683bd2820ac5b23607ee344720beb26df` has the opposite order
(partition collector first, entry collector later).

The dev98 transform target itself was correct; this was a verifier-only false
failure. Commit `d84b1b5f...` removes the ordering assumption, and
`2aa5ca862...` updates the synthetic regression fixture to match the actual
pinned layout and asserts `helper < partition < entry`.

No LLVM candidate launched in the failed attempt. No cache wipe is needed.
Rerun `M14-LLVM-UPDATE-RUN.cmd`.


## Current frontier — dev97 correctness fixed; dev98 attacks synthetic LLVM fragmentation

### dev97 is now player-proven and must be retained

Player run `launch(20260919-020359).log` at repo `9ce180b52d98` proves the
repaired motion-bounded dev97 fix works.

At HP 0:

```text
motion=24
seqWork=2 stage=1 now_seq=2 next_seq=4
seqWork=4 stage=1 now_seq=4 next_seq=4
```

Game Over then progresses through stages 2, 3, 4, 5 and 6 and exits normally.
Shutdown records `next_seq_gameover_observations=1`, `final_seq=1`, and
`final_next_seq=1`.

**Do not remove dev97 and do not replace it with a forced Game Over hack.** It
fixed a real LLVM legal-entry hole in the native HP-zero motion/helper surface
while Nintendo's own sequence logic remained authoritative.

### Latest performance baseline

dev94 remains healthy: DrawDone during gameplay is generally around ~2 ms
instead of dev91's 6-8 ms regression.

The remaining worst windows are still native phase-heavy:

```text
35/1016ms  disp=10.247ms main=6.730ms done=12.057ms
42/1016ms  disp= 7.174ms main=6.097ms done=10.925ms
49/1016ms  disp= 8.978ms main=5.767ms done= 5.985ms
36/1017ms  disp= 7.357ms main=6.954ms done=13.929ms
```

Shutdown counters:

```text
dev87 exact-negative bypassed = 1,407,810,823
dev90 native-first hits       =    28,181,109
dev90 native-first fallbacks  =             0
```

Public decomp confirms `spmarioDisp()` is effectively `camDraw()` plus one
trivial flag write, so display traversal remains a real measured owner.
However, dev98 tests a larger LLVM integration confound before instrumenting
individual draw callbacks.

### dev98 — generic synthetic cold entries are no longer chunk boundaries

dev57's address-taken census adds roughly 8,000 generic legal-entry MAP rows
named `openspm_dev57_addr_taken_<pc>`. dev97 adds a smaller interval of
`openspm_dev97_hp0_motion24_interval_<pc>` rows.

Pinned DolRecomp imports **all MAP symbols into both**:

- `collect_llvm_entry_points()`; and
- `collect_llvm_partition_points()`.

Therefore the safety repair that was intended to mean "this interior PC must be
enterable" was also forcibly ending the current 128-instruction LLVM chunk at
thousands of conservative callback candidates.

Pinned DolRecomp already supports the distinction we need:
`entry_points[]` is passed into the LLVM emitter, and
`FunctionEmitter::scanRegionLeaders()` marks each interior entry PC as a legal
region leader inside the containing chunk.

dev98 patches only `collect_llvm_partition_points()` so these two synthetic
families are **entry-only**:

```text
openspm_dev57_addr_taken_
openspm_dev97_hp0_motion24_interval_
```

`collect_llvm_entry_points()` is explicitly left unchanged.

Measured/semantic entries remain normal partition boundaries, including the
explicit measured dev57 target, dev54/dev56, dev77 wedding, dev79 BL sources,
scheduler entries and all host seams.

Private codegen identity is now:

```text
llvm-inline-wpar-indirect-budget-v9-dev98-entryonly
```

Expected local build marker:

```text
OpenSPM entry-only synthetic symbols: N legal cold entries excluded from chunk partitions
```

A large `N` proves the A/B is actually removing the unintended fragmentation.

Read first:

`docs/M14-LLVM-DEV98-ENTRY-ONLY-COLD-ENTRIES-2026-09-18.md`

### Next test

Run:

```text
M14-LLVM-UPDATE-RUN.cmd
```

No cache wipe.

Use the same enemy/NPC-heavy rooms. Also make sure Game Over still works once;
dev97 is a required correctness gate.

Compare:

- completed `frames/host_ms`;
- `disp_avg_us`;
- `main_avg_us`;
- dev87 bypass/rate;
- dev90 hits/fallbacks.

If dev98 materially improves heavy rooms, retain it and then profile the
remaining `camDraw()/dispDraw()` cost. If it does not, the next target is the
display callback traversal itself rather than VI/retrace or DrawDone.

Branch: `m14-native-execution`

Player command:

```text
M14-LLVM-UPDATE-RUN.cmd
```

Read first:

- `docs/M14-LLVM-DEV92-CONFIGURE-TIMING-RETIREMENT-2026-09-18.md`
- `docs/M14-LLVM-DEV91-STAGING-SNAPSHOT-2026-09-18.md`
- `docs/M14-LLVM-DEV89-DEV90-PERFORMANCE-HANDOFF-2026-09-18.md`
- `docs/M14-LLVM-DEV88-DRAWDONE-PROFILER-RETIREMENT-2026-09-18.md`
- `docs/M14-LLVM-DEV87-HOSTCALL-PREFILTER-2026-09-18.md`
- `docs/M14-LLVM-DEV86-HOT-ORDINARY-DISPATCH-2026-09-18.md`
- `docs/M14-LLVM-DEV85-BUDGET-EXIT-TELEMETRY-2026-09-18.md`
- dev81 phase-profiler section below for interpreting where the remaining frame time lives.

Build notes:

- dev85's source-only contract had one stale dev79 cadence-format assertion
  after the cadence logger was upgraded to the dev85 combined
  BL/ordinary/budget-exit line. Commit `b550538` removes only that retired
  assertion; the dev79 startup/correlation marker remains required.
- The first post-contract build then exposed a compile-only dev85 issue:
  `timing.cpp` uses `module_.getOrInsertGlobal()` for the budget-exit counter
  but pinned DolRecomp's file did not include `llvm/IR/Module.h`. Commit
  `f88cc0e` makes the private transformer inject that concrete definition;
  `19fc0c2` adds a source contract for the repair. No guest semantics changed.

## Latest real compile blocker and dev93 repair

Player attempt at repo `4b843b331c477bdf53952a924f2920ca66bbae6c`
proved the full configure chain now works:

- LLVM source contracts PASS;
- dev88 retirement remains active;
- dev89 applied;
- dev91 applied;
- dev92 timing retirement applied;
- exact configured-Aurora performance candidate proof PASS.

The build then reached the real MSVC compile of
`aurora/lib/gx/command_processor.cpp` and failed only because dev89 had emitted
`const u32 stageBucket = ...` twice in the same `m14_stage_guest_array`
scope (reported at generated lines 1027 and 1078).

dev93:

1. changes fresh dev89 transforms so the miss insertion path reuses the bucket
   computed at lookup entry instead of redeclaring it;
2. migrates an already-applied cached dev89 source with exactly two declarations
   down to exactly one, so **no cache wipe is required**;
3. adds the zero-vertex early continue to the one-index min/max scanner, keeping
   the pre-dev89 behavior for legal zero-vertex primitives;
4. migrates already-applied caches to that zero-vertex-safe shape too;
5. adds a synthetic cached-source regression test for both repairs;
6. makes the pre-build configured-Aurora proof reject any source whose
   `stageBucket` declaration count is not exactly one or lacks the zero-vertex
   guard.

A local standalone C++20 synthetic compile of the corrected bucket/scan path plus
the dev91-style staging-range helper succeeds with warnings treated as errors.

Next command remains:

```text
M14-LLVM-UPDATE-RUN.cmd
```

No cache wipe.

## Latest configure-blocker repair and active test candidate

The first player attempt at repo `70dad13ab5d5d75925aeb1ac75a1cb34d0424f80`
passed the LLVM source contracts but stopped during the explicit CMake
reconfigure before any renderer compilation. The cached Aurora checkout already
had dev88's `M14 DrawDone per-draw profiler retired for normal play` source
shape, while the historical render-worker compatibility hook still relaunched
`apply_drawdone_substage_profile_patch.py`. That old installer expected the
pre-dev88 quiet handler and failed its exact anchor.

This is fixed in two places: the worker-retirement hook no longer reinstalls the
temporary DrawDone profiler, and the old profiler itself treats the dev88
retirement marker as an idempotent successor state.

Tracing that chain also found the older guest-array timing probe still doing
`steady_clock` timing/accounting inside `m14_stage_guest_array`. dev92 retires
that diagnostic-only work. The measurements already served their purpose and
led to dev89/dev91.

The local LLVM runner now refuses to launch unless the exact Aurora checkout
referenced by `CMakeCache.txt` proves dev89 + dev91 + dev92 and has no old
stage multimap, CPU snapshot pool, guest-array timing counters/profile marker,
or `profileFinish` residue.

Expected pre-build proof:

```text
[OpenSPM] Renderer performance candidate proof PASS: dev89 fixed-bucket/scan-plan + dev91 staging-backed exact snapshots + dev92 timing retirement are present in configured Aurora.
```

Run only:

```text
M14-LLVM-UPDATE-RUN.cmd
```

No cache wipe and no HOME reset.

## Latest dev88 player result and active candidate

Player log `launch(20260918-180757).log` confirmed dev88 is a real win:

- user report: "definitely an improvement; definitely needs more";
- the temporary DrawDone substage profiler is absent;
- light/medium gameplay repeatedly reaches ~59-61 completed frames/s;
- ordinary heavy DrawDone windows are commonly ~2.5-2.9 ms after profiler
  retirement;
- the deepest tested enemy-room section still falls to ~30-33 completed
  frames/s when `disp+main` grows to roughly 13-17 ms and DrawDone climbs back
  toward ~4 ms;
- `hang_80065128_max=0` remains intact;
- dev87 bypassed 514,897,575 definite ordinary callbacks in the run;
- the remaining ordinary sampler is dominated by `0x8027551C`
  (scheduler-idle explicit reentry) and `0x800361F0`; only the former is
  promoted by dev90.

Active test candidate:

- **dev89 renderer:** fixed allocation-free stage bucket index + once-per-draw
  vertex scan plan + min/max-index interval reduction for ordinary indexed
  attributes. Exact pointer/stride/snapshot-size/`memcmp` reuse remains
  authoritative.
- **dev90 dispatcher:** native-first attempt only for exact map rows
  `0x8027551C`, `0x80D1F414`, and `0x80086848`. Native lookup refusal
  immediately falls back to the historical dev87/host router.

Expected dev90 teardown:

```text
M14 LLVM dev90 structural native-first stats hits=... fallbacks=...
```

Do not promote `0x800361F0` or other sampled PCs without an independent
compiler/semantic proof.

## Confirmed correctness

The wedding restart bug is fixed by dev77's structural native entry at:

```text
0x80065128
```

Latest long dev78 run `launch(20260918-005733).log` repeatedly passed the
wedding while AFK and ended with:

```text
hang_80065128_max=0
```

Keep dev77.

dev78 removed the superseded dev72 full-state cold-escape materialization and
dev76 every-native-call state checkpoint. The wedding still passed, so those
broad spill experiments remain retired. Active emitter/codegen provenance is now:

```text
llvm-inline-wpar-indirect-budget-v8-dev85-budgetcounter
```

dev86 is a generated-dispatch-header integration patch, not another LLVM object
emitter transform, so it does not change that provenance string.

## Current performance frontier

User report from the dev78 run:

- gameplay still laggy;
- Mario movement very jittery;
- light scenes can still reach ~60 simulation updates/s;
- heavy scenes still spend long windows around ~30-33 updates/s against ~60-62
  retraces/s.

Representative late heavy windows:

```text
spmarioMain=33 retraces=61
spmarioMain=31 retraces=62
spmarioMain=30 retraces=60
spmarioMain=31 retraces=62
```

Renderer work grows in heavy scenes but is still not the missing 2x:

```text
DrawDone avg ~= 4.0-4.5 ms/frame
DrawDone p95 ~= 4.75-5.25 ms
Present() avg ~= 0.208 ms
```

That older dev78 interpretation is superseded by dev87: dispatch traffic can be reduced by almost an order of magnitude without removing the heavy-frame cliff. The current frontier is a narrow 16.7 ms producer/main-thread deadline miss, with synchronous DrawDone work large enough to decide whether a frame catches the current VI or slips to the next one.

## dev78 direct-BL sampler result

The long run recorded:

```text
direct-BL rescues = 71,814,647
ordinary callbacks ~= 8.30 billion
post-MTMSR tails = 10,272,863
```

The 1/4096 rescue heavy-hitter sample retained:

```text
0x80D1F414 -> 0x802543F4 : 6687
0x80086848 -> 0x8012191C : 1704
```

The first pair is contaminated by the long AFK wedding loop, so its aggregate
weight is not assumed to represent gameplay alone. Both are nevertheless exact,
high-frequency missing native-entry source PCs.

## dev79 change

`tools/native_bridge/augment_llvm_bl_continuations.py` now promotes those two
**direct BL source PCs** as native partition entries after verifying the player's
private compiler input.

For each source, generation proves:

1. source is executable;
2. target is executable;
3. source decodes as a direct PowerPC B-form instruction with LK=1;
4. decoded target exactly matches the measured target.

Measured entries:

```text
0x80D1F414 -> 0x802543F4
0x80086848 -> 0x8012191C
```

Partition names:

```text
openspm_dev79_hot_bl_source_80d1f414
openspm_dev79_hot_bl_source_80086848
```

Why source partitions rather than only PC+4 continuations:

the dev32 fallback is reached **before the direct BL executes**. It manually sets
`LR=source+4` and `PC=target`. If top-level redispatch lands on the BL source,
partitioning only the return PC cannot make that BL execute natively.

The policy stays measured-only. Do not partition every BL in the title; excessive
range fragmentation can erase LLVM throughput.

Expected generation marker:

```text
[OpenSPM] LLVM dev79 direct-BL native-entry repair:
... hot=0x80D1F414->0x802543F4,0x80086848->0x8012191C
```

The dev78 rescue sampler remains active. The next run should show these two
sources disappear from the rescue heavy sample and reveal the next boundary if
needed.

The dev68 run wrapper now also reports the direct-BL rescue counter delta about
once per host second:

```text
M14 LLVM dev79 rescue cadence host_ms=... bl_rescues=... rate_per_s=... pc=...
```

This is observation-only and runs outside the public host-call hot path. Compare
it directly with adjacent `M14 simulation cadence` lines.

No manual cache wipe is required. The changed partition map alters affected LLVM
range/job hashes.

## Bigger unresolved performance clue

The same run produced roughly **8.30 billion ordinary host callbacks**.

dev66 proved that collapsing the layered semantic-router lookup work alone was
not enough, but that A/B predates dev68's scheduler-idle repair and did not
remove the LLVM->C callback entry itself.

If dev79 does not materially improve heavy cadence, the next high-value target is
the public dispatch boundary:

- measure host CPU time in the same heavy scene;
- determine how much time is spent entering `ppc_host_call` before native
  lookup;
- design a native-first fast path only for addresses proven to be legal ordinary
  native entries.

Do **not** blindly reorder host-before-native dispatch. dev77 proved that an
arbitrary aligned PC inside a generated LLVM range may not be a legal region
leader and can return zero work.

## Spinner rendering behavior

Updated user clarification:

- spinning devices intentionally pause/freeze Mario's sprite while they animate
  him around;
- they do not permanently turn Mario invisible;
- invisibility only happened once when Mario apparently entered while his sprite
  was already offscreen;
- the issue did not reproduce in the dev78 run.

Treat this as low-priority renderer transition/capture semantics: the frozen
representation can apparently be absent when the sprite was outside the rendered
view at capture time. Do not modify guest visibility flags or actor state.

## Next player test

Run:

```text
M14-LLVM-UPDATE-RUN.cmd
```

No cache wipe.

For a useful A/B, pass the wedding and spend at least 30-60 seconds in the same
heavy enemy/NPC area used for the dev88 run.

Check:

1. configure says the dev89 guest-array hotpath optimization was applied/already active;
2. generation reports `dev90_native_first_entries=3`;
3. runtime prints the dev90 startup marker;
4. wedding still passes and `hang_80065128_max=0`;
5. compare completed `M14 main-loop phases frames/host_ms` and DrawDone producer
   windows against dev88;
6. final dev90 `hits/fallbacks` line;
7. final ordinary hostcall heavy sample.

If dev89 materially lowers DrawDone but the deepest completed cadence stays
~30-33, keep the safe renderer win and move into dev81's `disp/main` ownership.
Do not return to the budget-guard theory or broad native-first reconstruction.

## Preserve

No emulator/general PPC interpreter fallback. Player supplies Nintendo code and
assets. Preserve dev77 wedding restart entry, dev78 spill retirement, dev68
runnable-idle wake, dev69 immutable filters, dev63 positive admission cache,
chunk128/indexed generation, bounded GX/display-list continuation policy, v19 GX
CPU-shadow semantics, deterministic relF/BSS placement, exact GX finish
completion, input edge retention and fixed-phase 60 Hz VI.


## 2026-09-17/18 — dev79 measured, dev80 dispatch A/B + dev81 native phase profiler ready

Player log `launch(20260918-020807).log` tested repo `27741ce34475`.

### dev79 result

The structural BL-source experiment worked mechanically but did **not** fix heavy
gameplay cadence:

- wedding remains fixed: `hang_80065128_max=0`;
- direct-BL rescues fell from dev78's 71,814,647 long-run total to 3,374,898;
- sampled `0x80D1F414 -> 0x802543F4` is almost gone and
  `0x80086848 -> 0x8012191C` is greatly reduced;
- heavy gameplay still falls to roughly 28–31 `spmarioMain` updates against
  60–62 VI retraces;
- bad windows can coincide with ~32–36k rescues/s, but rescue elimination is
  not sufficient to recover the missing frame budget;
- DrawDone producer work remains only a few milliseconds/frame in those windows.

Do not continue adding sampled BL source partitions as the primary performance
strategy.

### New architectural observation

The same run reported:

- `ordinary=691331701` public host callbacks;
- only `region_queries=298948`;
- only `direct_redirects=209046` real hosted direct-call redirects.

dev66 previously proved that reducing work *inside* those callbacks was not
enough, because the LLVM->C callback itself still occurred. dev80 is therefore a
bounded A/B of removing the callback crossing only where static compiler facts
prove it is unnecessary. It is not a learned PC cache.

### dev80 — proven-entry native-first public dispatch

`tools/native_bridge/apply_llvm_dispatch_priority.py` derives an exact packed
2-bit policy over the private combined DOL+relF text:

- bit 0: this exact PC is statically proven to be a legal LLVM native entry;
- bit 1: this exact PC must retain host-first semantic routing.

Only `native-entry && !host-priority` skips `ppc_host_call()` and tries the
normal native lookup first. All other PCs retain the historical host-first
order. If the native lookup unexpectedly fails, generated dispatch falls back to
the old host callback before returning failure.

Legal-entry proof is a conservative under-approximation of pinned DolRecomp:
chunk-128 function/range starts, program entry, private map/symbol entries and
static B/BC targets. Range starts are reconstructed from the same linked-call
target + BLR+4 partition algorithm used by the pinned compiler. OpenSPM's extra
FPU/MTMSR/linked-continuation cold entries that are not reconstructed merely
miss the optimization; they do not become unsafe fast entries.

Host-priority is semantic, not synonymous with compiler partitioning. It retains
platform/service targets, hosted-call seams, relF allocation state machines, GX
display-list interception, HOME, DSP first-task completion and post-MTMSR tails.

Structural entries — wedding/restart PCs, scheduler/context reentries, return
continuations and measured BL-source partitions — are native metadata and are
not automatically host-first. Direct-BL source partitions can therefore execute
the real guest BL natively. If native lookup unexpectedly fails, generated
dispatch immediately retries the unchanged host callback, so the existing BL
rescue remains the fail-closed fallback.

The four frame-phase coordinates are also intentionally native-first in dev81:
the LLVM emitter now calls the phase profiler at the exact native block entry.
The old host-callback telemetry remains available only on native lookup failure,
avoiding duplicate phase observations.

This is intentionally an A/B. If it produces a meaningful win, move the policy
hook into a first-class DolRecomp/OpenSPM dispatch interface rather than keeping
generated-header rewriting as permanent architecture.

### dev81 — exact native frame-phase profiler

The old v21 phase profiler was valid but depended on frame entry PCs escaping
through the public host callback; LLVM native execution does not guarantee that
sequence, so recent logs emitted no complete phase windows.

The compiler patch now emits a host-only call at the exact player-local entries:

- `0x801A8A9C` — DEMOBeforeRender / phase 0
- `0x801A7D1C` — spmarioDisp / phase 1
- `0x801A7AAC` — spmarioMain / phase 2
- `0x801A8B64` — DEMODoneRender / phase 3

The helper receives only the phase id and forwards to the retained monotonic
v21 profiler through the persistent bridge instance. On phase 2 it also advances
the existing `spmarioMain` cadence counter, preserving apples-to-apples 30/60
telemetry. It does **not** receive or materialize guest CPUState. Expected runtime lines:

```text
M14 LLVM dev81 native frame-phase profiler active: ...
M14 main-loop phases host_ms=... frames=... before_avg_us=... disp_avg_us=... main_avg_us=... done_avg_us=... retraces=...
```

This next run is diagnostic even if FPS is unchanged. The dominant phase tells
the next subsystem:

- high `main_avg_us`: guest simulation/native execution/codegen;
- high `disp_avg_us`: display-list generation/GX transport;
- high `done_avg_us`: GX completion/end-of-frame synchronization;
- low summed phase time but low simulation cadence: scheduler/runtime gaps
  outside the four retail frame phases.

### Next player test

Run `M14-LLVM-UPDATE-RUN.cmd`. No manual cache wipe. The dev81 compiler-source
change intentionally invalidates affected LLVM codegen through the existing
source hash; expect a real LLVM rebuild.

Reach a known jittery/heavy gameplay area and move around for roughly 1–2
minutes. One wedding pass is enough. Then close normally and send the log.

Interpret dev80 by comparing `ordinary=` at teardown against dev79's
691,331,701 and checking simulation cadence. Interpret dev81 from
`M14 main-loop phases` lines. Do not add more BL rescue partitions before this
result.


### Final dev80/dev81 preflight hardening

Generation, the standalone LLVM module builder and CMake now require the exact
dispatch-policy artifact in addition to the existing inline-WPAR proof. The
validator rejects a tree unless the generated policy header exists and
`generated.c` contains both address/alias policy lookups plus the native-entry
and host-priority flags. The source-only pipeline test executes the dev80
synthetic policy self-test and a synthetic generated-tree validation.

The preparer hard-resets its owned DolRecomp source to the pinned commit before
applying the active transform, and the codegen provenance is
`llvm-inline-wpar-indirect-budget-v5-dev81-phase`. No manual cache wipe is
required; the compiler-source hash changes invalidate the affected objects.


### dev80 build-preflight correction

The first dev80 player build failed safely after DolRecomp generation with:

```text
generated manifest address host/native pair count 0, expected 1
```

Root cause: LLVM `emit_dispatch_helpers()` writes `dolrecomp_call()` into
`generated.h`; `generated.c` is only the native-object manifest. dev80 was
correctly refusing to patch a file that did not contain the dispatcher.

The dev80 policy now patches `generated.h`, and generation/module/CMake
validation checks the policy markers in `generated.h` while continuing to read
object entries from `generated.c`. Synthetic preflight covers the same split.
No runtime/compiler semantics changed from the intended dev80/dev81 candidate.


### dev80 boot-freeze correction — compiler fallback entries are not cold-restartable

Player log `launch(20260918-040831).log` at repo
`e347626e8025309db4762c774ee1104385925842` built successfully but froze during
early boot at:

```text
PC=0x8026CA60 LR=0x8026CA60 CTR=0
exception=0 program=0
last_hostcall=0
region_queries=0
```

Every top-level window then reported exactly 65,536 synthetic guest cycles, while
dev79 BL rescues fell to zero. That is the established OpenSPM signature for
65,536 zero-progress top-level dispatch attempts, not real guest execution.

Root cause was a remaining mismatch in dev80's static legal-entry reconstruction:
DolRecomp `scanRegionLeaders()` can mark range starts/static targets as region
leaders, but `emitColdEntry()` deliberately excludes any region leader whose
DolIR terminator is `FALLBACK`. `dolrecomp_find_original()` can therefore
find the containing native chunk while that exact PC is not accepted by the
chunk's cold-entry switch. dev80 had considered such a PC native-first, producing
the zero-progress loop.

The fix is generic and compiler-derived. `generate_local_llvm.py` now waits
until DolRecomp emits `<game>_fallbacks.csv`, parses the compiler's authoritative
fallback-block addresses, and passes that set into
`apply_llvm_dispatch_priority.derive_sets()`. All compiler-reported fallback
PCs are subtracted from the native-first legal-entry set before the packed policy
header is emitted.

There is no address-specific behavior for `0x8026CA60`.

The dev80 self-test now includes a synthetic PC that is simultaneously a map /
native-looking entry and a compiler fallback row. The test requires that PC to
lose native-first eligibility. Pipeline source contracts require
`read_fallback_addresses`, the generated fallback report, and the
`fallback_candidates_removed` accounting.

Expected dev80 generation output now includes:

```text
legal_before_fallbacks=...
fallback_blocks=...
fallback_removed=...
legal_entries=...
```

If `fallback_removed=0` on R8PE01 despite this previous boot failure, stop and
inspect the compiler report rather than running the player.


## dev82 — restore proven host-first dispatch, keep dev81 profiler

The dev80 native-first public-dispatch A/B is retired from the active build path.

Player runs at `e347626...` and `e2a35a2...` both froze during early boot at
`PC=LR=0x8026CA60` with no guest exception, no native-region query and
`last_hostcall=0`. Every host-second then reported 256 slices × 65,536 synthetic
guest cycles, the established zero-progress top-level redispatch signature.

Historical audit confirmed this regressed the routing invariant used by the
proven LLVM boot stack. dev47's PE-finish completion, dev29 DSP completion,
post-MTMSR recovery, relF allocation seams, GX display-list completion and the
other migration semantics are still compiled, but dev80 allowed selected PCs to
attempt native execution before the production host router saw them. The exact
`last_hostcall=0` boot frontier proves that ordering change is active at the
regression.

Do not continue refining dev80 legality heuristics in the active player path.
The generated-C backend and the last tested dev79 LLVM dispatcher both retain
host-first top-level dispatch and boot through the wedding/gameplay.

Active dev82 therefore restores the exact dev79 versions of:

- `generate_local_llvm.py`
- `build_local_llvm_module.py`
- `validate_llvm_generated.py`
- `tools/native_bridge/CMakeLists.txt`

The source-only test is likewise restored to the dev79 pipeline contract, with
only the dev81 profiler assertions/provenance retained.

The historical `apply_llvm_dispatch_priority.py` remains in source control for
archaeology only; no active generation/build/CMake path imports, requires or
applies it.

### What remains active

Keep:

- dev77 wedding resume partition;
- dev78 retirement of the unsuccessful dev72/dev76 state-spill experiments;
- dev79 measured BL-source partitions and rescue-rate correlation;
- all existing host-first production semantics (PE-finish, DSP, relF, HOME,
  GX display-list, post-MTMSR, platform services, BL rescue);
- dev81 native frame-phase profiler.

Codegen provenance stays
`llvm-inline-wpar-indirect-budget-v5-dev81-phase` because dev81 is still an
active compiler-source transform. The dispatcher rollback itself is generator/
integration-side, not another LLVM emitter transform.

### Next test

Run `M14-LLVM-UPDATE-RUN.cmd` with no manual cache wipe.

First gate: boot must progress past the previous stationary `0x8026CA60`
frontier. A boot log at that PC with `last_hostcall=0` would mean some dev80
artifact is still being staged, not that dev82 intentionally bypassed the
router.

Once booting, pass one wedding and play a known jittery area long enough to emit
several:

```text
M14 main-loop phases host_ms=... frames=... before_avg_us=...
disp_avg_us=... main_avg_us=... done_avg_us=... retraces=...
```

Those phase timings, not another dispatcher optimization, determine the next
performance target.


## dev83 — 256-cycle LLVM yield storm identified; x86 SJLJ quantum -> 4096

While dev82 remains the active **host-first** dispatch architecture, the dev79
gameplay log exposed a much stronger performance explanation than dev80.

`launch(20260918-020807).log` accumulated 691,331,701 ordinary public host
callbacks over 14,303 input frames. At 60 input frames/s that is about 238.38 s,
or **2.900M ordinary callbacks/s**.

Pinned DolRecomp's legacy LLVM budget guard voluntarily side-exits after exactly
256 guest cycles. `2.900M * 256 = ~742.4M guest cycles/s`, essentially the
Broadway execution scale. The callback storm is therefore tracking the 256-cycle
yield quantum almost one-for-one.

The dominant sampled ordinary PCs provide independent confirmation:
`0x8025CABC` and `0x8025CAD4`. Seeky's R8PE01 US2 symbols place MSL
`strcmp` at `0x8025C9C8` and `strncmp` at `0x8025CAE4`, so both hot PCs
are internal to the same 0x11c-byte `strcmp`. LLVM emits budget guards at loop
headers. A hot string loop repeatedly materializing at those PCs is exactly what
the 256-cycle guard predicts.

This also explains dev66/dev69: lookup work fell dramatically, but the compiler
kept manufacturing millions of dispatcher crossings, so cadence stayed ~30 Hz.

dev83 changes **only** the legacy Windows/x86 SJLJ guard quantum:

```text
native_abi_ && cold_escapes_ && intrinsic_escapes_ ? 4096 : 256
```

Both scalar/direct and dynamic/indirect guard overloads are patched. All other
DolRecomp modes retain 256. Dispatch order stays dev79/dev82 host-first; no
semantic callback is skipped or reordered.

OpenSPM's outer `bridge_run` still owns its 200k-cycle soft budget and the same
VI/scheduler safepoint. 4096 still permits roughly 49 internal voluntary yields
per outer run. Exception, fallback, platform-service and host-call exits are
unchanged.

Expected ordinary budget-exit pressure is roughly 16x lower if the measured
storm is predominantly the guard: ~2.9M/s -> ~181k/s.

Active provenance is now:

`llvm-inline-wpar-indirect-budget-v6-dev83-guard4096`

Detailed handoff:
`docs/M14-LLVM-DEV83-GUARD-QUANTUM-HANDOFF-2026-09-18.md`.

### Next test

Run `M14-LLVM-UPDATE-RUN.cmd`, no manual cache wipe.

First verify dev82 restored boot past `0x8026CA60`; then pass one wedding and
play a known heavy area for >=30 s. Compare:

- `simulation cadence spmarioMain`;
- `production-router ordinary=` growth vs old ~2.9M/s;
- dev81 `M14 main-loop phases ...` timings;
- retraces (should remain ~60 Hz).

If dev83 is the expected primary win, ordinary callbacks should collapse by
about an order of magnitude and heavy simulation should move materially toward
60 Hz without any renderer/scheduler semantic changes.


## dev84 — raise the legacy x86 SJLJ inner yield quantum to 32768

dev83's 4096-cycle candidate was not player-tested before the budget-exit model
was pushed one step further. The old dev79 measurement is strong enough to
quantify the remaining dispatcher pressure:

- ~2.900M ordinary callbacks/s at the upstream 256-cycle guard;
- ~181k/s predicted at 4096;
- ~22.7k/s predicted at 32768.

The old rate multiplied by 256 is ~742M guest cycles/s, essentially Broadway
scale, so the public callback storm is behaving like one callback per voluntary
budget exit.

The active dev84 guard is now:

```text
native_abi_ && cold_escapes_ && intrinsic_escapes_ ? 32768 : 256
```

Both scalar and dynamic/indirect guards use it. All non-legacy-x86 modes keep
256. The dev82 host-first dispatcher remains unchanged; no semantic callback is
skipped or reordered.

This is still conservative relative to OpenSPM's existing 200,000-cycle outer
bridge budget: roughly six inner voluntary yields remain per outer run.
32,768 Broadway cycles are only ~45 microseconds of guest time, far below a
video frame.

Active provenance:

`llvm-inline-wpar-indirect-budget-v7-dev84-guard32768`

Detailed handoff:
`docs/M14-LLVM-DEV84-GUARD-QUANTUM-HANDOFF-2026-09-18.md`.

### Next player test

Run `M14-LLVM-UPDATE-RUN.cmd`, no manual cache wipe.

Verify boot past `0x8026CA60`, pass one wedding, then spend >=30 seconds in a
known heavy NPC area. Look for:

```text
M14 LLVM dev84 guard-quantum A/B active: ... 32768-cycle ...
M14 LLVM dev84 cadence host_ms=... ordinary_rate_per_s=...
M14 main-loop phases host_ms=... frames=... before_avg_us=... disp_avg_us=... main_avg_us=... done_avg_us=... retraces=...
```

A strong result is ordinary callback pressure collapsing toward tens of
thousands/s and heavy `spmarioMain` cadence moving materially toward 60/s.
If callback pressure collapses but simulation does not, dev81 phase ownership
selects the next subsystem rather than another dispatch micro-optimization.


## dev85 — count exact LLVM voluntary budget exits

The active performance semantics remain dev84's 32,768-cycle legacy Windows/x86
SJLJ guard. dev85 adds observation at the actual guard exit so the next run can
separate budget churn from unrelated public-dispatch traffic.

Generated LLVM increments a player-local uint64 only when either scalar or
dynamic budget guard takes its voluntary exit edge. It is single-CPU-thread,
non-atomic, does not touch guest state, and is never read by guest control flow.

The existing ~1 Hz cadence line now reports:

```text
M14 LLVM dev85 cadence ... ordinary_rate_per_s=... budget_exits=... budget_exit_rate_per_s=...
```

Interpretation:

- budget-exit rate ~= ordinary rate: guard churn is the dominant public-dispatch
  source;
- budget-exit rate is low but ordinary remains high: find the other native escape
  path instead of increasing the quantum again;
- both rates collapse but `spmarioMain` remains ~30: dispatch is not the
  remaining frame owner; use dev81 phase timings.

Active provenance:
`llvm-inline-wpar-indirect-budget-v8-dev85-budgetcounter`.

Detailed handoff:
`docs/M14-LLVM-DEV85-BUDGET-EXIT-TELEMETRY-2026-09-18.md`.

Next player command remains `M14-LLVM-UPDATE-RUN.cmd`; no manual cache wipe.



## dev86 — exact hot-ordinary native-first attempt

The long dev85 run `launch(20260918-152140).log` resolves the guard question:

```text
ordinary_rate_per_s ~= 2.0M-4.1M in end gameplay
budget_exit_rate_per_s = 0
```

So dev83/dev84's budget-exit causal model is falsified. The ordinary callback
storm is coming from some other public-dispatch materialization path.

The same run remained wedding-stable:

```text
hang_80065128_max=0
```

and the final ordinary 1/4096 heavy-hitter sample again retained:

```text
0x8025CABC
0x8025CAD4
```

Both are project-known internal PCs in R8PE01 US2 MSL `strcmp`.

### Important dev81 telemetry correction

Under the active host-first dispatcher, phase 2 is observed by both:

1. dev50's public host callback; and
2. dev81's exact native phase helper.

Both advance the legacy `spmarioMain` cadence counter. Therefore a line such as
`spmarioMain=62 retraces=62` does **not** prove 62 completed frames. In the
same late gameplay windows, `M14 main-loop phases` reports about 30-33 complete
phase cycles/s. Use `frames/host_ms` from that line as the completed-frame
cadence.

The last ~60 phase windows are roughly:

```text
completed frames ~= 33/s
disp ~= 7.4 ms
main ~= 8.0 ms
done ~= 15.8 ms
```

That is consistent with the user's visible ~30 FPS/jitter report.

### dev86 change

`tools/native_bridge/apply_llvm_hot_ordinary_fastpath.py` patches only the two
exact measured ordinary PCs in generated `generated.h`.

For either hot PC:

1. try `dolrecomp_call_original()` first;
2. if native lookup succeeds, avoid the no-op public host callback;
3. if native lookup refuses, immediately execute the unchanged historical
   host-first path.

Every other PC remains host-first.

This is intentionally not dev80's broad static native-first policy. Generation
fails closed unless the player's private combined DOL/map proves each hot PC is:

- executable;
- not an LR-writing linked transfer;
- absent from platform host targets;
- absent from semantic hosted-call/relF map rows;
- absent from retained GX/HOME/DSP/post-MTMSR/frame-phase exact boundaries.

Expected generation marker:

```text
[OpenSPM] LLVM dev86 measured hot-ordinary fast path:
0x8025CABC=raw:0x... 0x8025CAD4=raw:0x...
native-first only for these exact PCs, host-first fallback retained.
```

No manual cache wipe is required. LLVM chunk objects can remain stamped; the
generated dispatcher header/bridge rebuild carries the A/B.

### Next player test

Run `M14-LLVM-UPDATE-RUN.cmd`.

Gates:

1. dev86 generation proof succeeds;
2. boot passes the old dev80 `0x8026CA60` stationary frontier;
3. pass at least one wedding;
4. play >=30 seconds in the same laggy NPC area;
5. compare dev85 `ordinary_rate_per_s` and `M14 main-loop phases frames/host_ms`.

Strong success means a large fall from ~2-4M ordinary callbacks/s and completed
frame cadence materially above ~30-33/s. If callbacks collapse but complete
frames do not, stop dispatcher work and optimize the dominant dev81 phase
directly.

Detailed handoff:
`docs/M14-LLVM-DEV86-HOT-ORDINARY-DISPATCH-2026-09-18.md`.


## dev87 — exact negative host-call prefilter

Player log `launch(20260918-163035).log` tested dev86 at repo
`8896170eee29`. The user still reports visible lag.

dev86 worked mechanically but was too narrow: the old `strcmp` PCs
`0x8025CABC/0x8025CAD4` disappeared from the final ordinary heavy-hitter
sample, yet late gameplay still produced roughly 2.1M-2.9M ordinary callbacks/s
with zero exact voluntary budget exits. Completed dev81 phase windows remained
about 30-32/s in the heavy area.

Representative steady heavy windows are now roughly:

```text
disp ~= 8-10 ms
main ~= 7-9 ms
done ~= 16 ms
```

The important shutdown evidence is:

```text
ordinary callbacks = 2,816,258,463
dev69 seam negative rejects = 2,761,324,023
dev69 host-target negative rejects = 2,810,490,694
direct hosted redirects = 1,633,238
direct-BL rescues = 27,142,100
hang_80065128_max = 0
```

The remaining callback pressure is therefore diffuse, but dev69 already proves
inside the C router that the overwhelming majority of those calls cannot match
its immutable semantic tables.

dev87 moves only that conservative negative classification ahead of the
LLVM->C callback crossing. It does **not** revive dev80 native-first execution.

New tool:

`tools/native_bridge/apply_llvm_hostcall_prefilter.py`

Generation emits an exact one-bit-per-instruction table. Host-first is retained
for:

- every private compiler/map entry;
- every checked-in platform host target;
- every direct BL source, preserving generic rescue;
- retained GX/HOME/DSP/post-MTMSR exact semantics;
- every unknown/alias/out-of-image PC.

Only a known in-image zero-bit ordinary PC skips `ppc_host_call()` and proceeds
to the unchanged `dolrecomp_call_original()` lookup. No legal-entry
reconstruction, learned cache or runtime-state inference is used.

Expected markers:

```text
[OpenSPM] LLVM dev87 exact host-call prefilter: ...
M14 LLVM dev87 exact host-call prefilter active: ...
M14 LLVM dev87 host-call prefilter stats bypassed=...
```

Next player command remains `M14-LLVM-UPDATE-RUN.cmd`; no manual cache wipe.
Pass one wedding, then play >=30 seconds in a known heavy NPC area. Strong
success means ordinary callback rate collapses and `disp+main` falls enough
below the 16.7 ms VI deadline to recover toward 60 completed frames/s.

If callbacks collapse without a meaningful phase/cadence win, stop dispatcher
work and profile the remaining native `disp/main` phase directly.

Detailed handoff:
`docs/M14-LLVM-DEV87-HOSTCALL-PREFILTER-2026-09-18.md`.


## dev88 — retire temporary DrawDone per-draw profiling

Player log `launch(20260918-170925).log` tested dev87 at
`498351e0e53b25f2bbeaa4412bda6ca808d317b2`.

dev87 is a strong mechanical success and remains active:

```text
dev87 bypassed callbacks = 468,889,861
production ordinary callbacks = 48,393,282
hang_80065128_max = 0
```

Late ordinary callback cadence fell from roughly 2-3M/s under dev86 to about
0.34-0.46M/s. Exact voluntary budget exits remain zero.

But heavy completed-frame cadence did not recover. Representative phase windows:

```text
frames=35/1016ms disp=5.886ms main=6.517ms done=16.621ms
frames=32/1016ms disp=6.194ms main=6.519ms done=19.048ms
frames=30/1000ms disp=8.187ms main=5.501ms done=19.649ms
frames=31/1033ms disp=10.055ms main=6.486ms done=16.797ms
```

A light startup window remains near 60:

```text
frames=58/1015ms disp=0.343ms main=2.162ms done=14.577ms
```

The phase profiler charges the interval to the phase that just began, so
`done_us` includes DEMODoneRender -> next DEMOBeforeRender. In a fast frame,
that interval is mostly the natural wait to the next 60 Hz VI. In a heavy frame,
`disp+main` grows to ~12-16 ms and synchronous DrawDone adds roughly another
3-4 ms. The producer can therefore cross the 16.667 ms VI deadline and sleep
until the following retrace, producing the sharp ~30 Hz cliff.

The final bounded DrawDone profiler window measured:

```text
943,268 draws / 256 frames ~= 3,684 draws/frame
avg_frame_draw_us = 3,490
scan_us = 213,274 ~= 0.83 ms/frame
arrays_us = 376,552 ~= 1.47 ms/frame
producer DrawDone avg_us = 3,830
```

The profiler producing those numbers is itself no longer acceptable in a
borderline frame budget. It adds several `std::chrono::steady_clock::now()`
calls around thousands of draws per frame.

dev88 adds:

`renderer/aurora/apply_drawdone_profile_retirement_patch.py`

and wires it into the Aurora configure path in root `CMakeLists.txt`. It removes
only the temporary DrawDone per-draw clocks/counters and restores the already
proven quiet draw handler. Guest-array scans/staging, draw merging, pipeline,
bind/uniform logic, FIFO ordering, DrawDone semantics, renderer threading and
presentation remain unchanged.

The configure migration is exact/idempotent so the player's existing cached
Aurora checkout is repaired by the normal incremental command. No home-test
reset/cache deletion is required.

Next command:

`M14-LLVM-UPDATE-RUN.cmd`

Expected configure marker:

```text
Aurora DrawDone per-draw profiler retired for normal play
```

Runtime must no longer emit `M14 GX DrawDone substage profile ...`. The
low-frequency `M14 producer sync window kind=draw_done ...` remains useful.

Pass one wedding and play the same heavy area. If heavy cadence snaps upward,
keep the profiler retired. If it stays ~30-35, optimize real DrawDone CPU next:
the measured exact-array work (~1.47 ms/frame) and mandatory guest-index scan
(~0.83 ms/frame) are the highest-value renderer targets. Do not return to the
budget-guard or broad-dispatch theories.

Detailed handoff:
`docs/M14-LLVM-DEV88-DRAWDONE-PROFILER-RETIREMENT-2026-09-18.md`.



## 2026-09-18 — dev94 player result + dev96 offscreen/death truth probe

Player log `launch(20260918-224450).log` tested repo revision
`2d36d0c35d47` with dev94 CPU-cached exact guest-array snapshots, exact
duplicate CP array-base pipeline reuse, dev90/dev87 dispatch policy and dev95
sequence observation.

### Player result

The player reports:

- first area felt smoother;
- later areas were still as laggy as before;
- after falling into a pit, HP reached 0 but Mario never returned and no Game
  Over appeared;
- the earlier one-off invisible-Mario failure also happened while Mario was
  offscreen.

### Performance result

dev94 is a keeper. The runtime confirmed:

```text
M14 dev94 CPU-cached exact guest-array snapshots restored
M14 raw guest-array pooled exact snapshots single-copy append active
```

DrawDone is back near the pre-dev91 good range instead of the dev91 6-8 ms
regression. Representative gameplay windows are about 1.6-2.9 ms.

The first heavier area can now stay close to 60 completed frames, but later
rooms still become CPU-heavy. Representative late windows have roughly
10-13+ ms of `disp+main` before renderer work, so the remaining performance
frontier is primarily inside dev81's gameplay/display phases rather than
DrawDone, VI timing or ordinary host-call dispatch.

### Pit/death result

dev95 committed sequence observations remained alive through the failure and
shutdown ended at:

```text
SEQ_GAME (2), stage 1
seqSetSeq_calls=1
gameover_requests=0
```

The single host-routed request in the entire run was:

```text
seqSetSeq request=3 ... lr=0x8017BA10
```

That is insufficient to conclude the game never called
`seqSetSeq(SEQ_GAMEOVER)`: direct LLVM native-to-native calls can bypass the
public host callback.

The run also shows real PE bounding-box traffic from the guest, including the
empty/reset sentinel:

```text
bounds=1023,0,1023,0
```

SPM has a real offscreen/render-to-texture subsystem and can observe PE bbox
results, so the player's offscreen correlation is plausible enough to trace but
does **not** justify globally disabling GPU culling.

### dev96

Active bridge now layers an observation-only truth probe over dev95:

- US2 `seqWork = 0x804D0D60` retained;
- US2 sequence globals derived by the same proven relocation:
  - `now_seq  = 0x8056CAB8`
  - `next_seq = 0x8056CABC`
  - `prev_seq = 0x8056CAC0`
- exact native `spmarioMain` entry samples those globals, so
  `next_seq=4` catches direct native Game Over requests;
- the same proven BSS relocation gives US2
  `marioWork=0x804CEE58` and `MarioPouchWork=0x804D0428`;
- HP at pouch `+0x0C` is checked once per frame; HP changes log Mario flags,
  display flags, motion ID and XYZ position;
- the existing external-bus reader is wrapped only to observe complete PE bbox
  reads;
- empty `1023,0,1023,0` results log the first guest PC/LR plus sequence state;
- the underlying bbox value is returned unchanged and the callback is restored
  on destroy.

Expected runtime marker:

```text
M14 LLVM dev96 offscreen/death truth probe active:
```

Expected decisive lines:

```text
M14 LLVM dev96 sequence truth where=spmarioMain ... next_seq=...
M14 LLVM dev96 player state where=spmarioMain hp=... mario_flags=... motion=... pos=(...)
M14 LLVM dev96 empty PE bbox ... caller_pc=... caller_lr=... hp=... pos=(...)
M14 LLVM dev96 sequence/bbox truth stats ...
```

Detailed handoff:

`docs/M14-LLVM-DEV96-OFFSCREEN-DEATH-TRUTH-2026-09-18.md`

### CI note

GitHub Actions runs 831 and 832 still fail before job steps exist
(`portable-core`, `windows-native`, `aurora-patch-contract` all have
`steps=null`). This remains runner/account infrastructure, not a source
compiler verdict. Local `M14-LLVM-UPDATE-RUN.cmd` is authoritative.

### Next player test

Run `M14-LLVM-UPDATE-RUN.cmd`, no cache wipe. Reach the same pit, let HP hit
0, leave the stuck state alive for ~5-10 seconds, then close and upload the log.

Interpretation:

1. `next_seq=4`: Game Over was requested; trace sequence commit/init.
2. no `next_seq=4` + stable empty-bbox caller around the pit: symbolize and
   fix that offscreen/PE semantic path.
3. no `next_seq=4` + no relevant empty-bbox caller: instrument the actual
   Mario HP==0/death-motion/event gate.
4. Game Over works: dev96 is observation-only; do not convert it into a gameplay
   patch.


