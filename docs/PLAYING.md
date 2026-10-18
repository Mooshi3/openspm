# Playing OpenSPM (development builds)

OpenSPM is a native PC port of Super Paper Mario (US, R8PE01). You supply your
own game; no Nintendo code or assets are part of this project.

## Launching

From the repository folder:

```
M14-VANILLA-RUN.cmd AUTO                 windowed
M14-VANILLA-RUN.cmd AUTO fullscreen      exclusive fullscreen (auto-picks a 60-friendly refresh)
M14-VANILLA-RUN.cmd AUTO borderless      borderless fullscreen at the desktop refresh
```

The launcher pulls, builds and runs, and writes `launch.log`. Extra arguments
for testing: `nosync`, `oldorder`, `copytrace`, `efbram`, `notags`,
`flipcapture` (see the comments at the top of the .cmd).

### Your game files

OpenSPM runs Super Paper Mario (USA) **R8PE01, disc revision 2** only. The
first `RUN-M14-HOME-TEST.cmd` run extracts your WBFS with Wiimms ISO Tools
(`wit extract --psel DATA --pmode NONE`) into `.openspm\m14\game` and builds
the native game module from that `sys\main.dol`; the .cmd launchers then pass
that folder with `--game`.

### Starting OpenSPMNative.exe directly (first-run game picker)

Started without `--game` (e.g. by double-clicking), `OpenSPMNative.exe` uses
`[Game] path` from the settings file (`OPENSPM_SETTINGS`, else `OpenSPM.ini`
next to the exe). If that is empty or unusable it opens a file dialog; pick
your `.iso`, `.wbfs` or `.wia`, or the `sys\main.dol` of an extracted copy.
The choice is saved to `[Game] path` (the file is created from
`OpenSPM.ini.example` when missing).

* The disc header is checked first: another game, another region, a GameCube
  disc or another revision of SPM is refused with a message saying which it is.
  Dolphin `.rvz` files are recognized but must be converted to ISO/WBFS in
  Dolphin (right-click > Convert File...), because wit cannot read them.
* A disc image is extracted **once** to `User-OpenSPM\game` next to the exe
  (about 4 GB). OpenSPM does not contain any Wii decryption key; the
  extraction is done by your own Wiimms ISO Tools (wit.wiimm.de). Put it in
  `wit\bin\wit.exe` next to `OpenSPMNative.exe`, on `PATH`, or name it with
  `[Game] wit = ...` or `OPENSPM_WIT`. Later launches reuse the extracted copy
  as long as the image's path, size and date are unchanged.
* The native game module (`User-OpenSPM\native\openspm_local_native.dll`) is
  still built by the .cmd pipeline; without it OpenSPMNative explains that and
  exits.
* `--game <folder>` works exactly as before; `--game <image>` validates and
  extracts the image the same way (without saving it to OpenSPM.ini).

## Controls

Keyboard (only while the OpenSPM window is focused), default keys:

| Key | Wii Remote (sideways) |
| --- | --- |
| Arrows / WASD | move |
| Enter | 2 (jump) |
| Space / Z | A |
| X | 1 |
| C | B |
| Backspace | - |
| Home | HOME: opens / closes the OpenSPM menu |
| Tab (hold) | turbo: fast-forward (see Fast-forward below) |
| Q | shake the Wii Remote (stylish moves; hold to keep shaking) |
| Mouse | pointer |

Rebind keys in F1 -> Controls: **Set** waits for the next key you press
(Esc cancels), **Add** adds a second key to the same button (up to 4),
**Clear** unbinds it, **Default keys** restores the table above. Changes apply
at once and are saved as `[Keyboard] map`, e.g.
`up=Up/W,down=Down/S,left=Left/A,right=Right/D,a=Space/Z,b=C,1=X,2=Enter,minus=Backspace,plus=none,home=Home,turbo=Tab,shake=Q`
(slots `up down left right a b 1 2 minus plus home turbo shake`; keys separated by `/`,
`none` = unbound; key names as in the menu, e.g. `Space`, `Enter`, `LeftShift`,
`Keypad0`, `Semicolon`). F1, F8, F11, F12 and Esc stay OpenSPM keys.

Gamepad (SDL: Xbox, PlayStation, Switch Pro ...; XInput fallback): D-pad /
left stick move; default buttons A=2, B=B, X=1, Y=A, Start=+, Back=-, RB =
shake (`rb=SHAKE`), right stick click = turbo (`rs=TURBO`; `rs=NONE` turns it
off). Remap in
F1 -> Controls or with `[Controller] map` in OpenSPM.ini. The Guide / PS / Home
button is HOME (the OpenSPM menu). On Windows the Xbox Game Bar or Steam may
take the Guide button first; use the keyboard's Home key or F1 then.

The OpenSPM menu works without a mouse while it pauses the game (the default,
F1 -> Controls -> "Pause the game while this menu is open"): D-pad / left
stick move, A selects or toggles, B backs out, LB / RB switch tabs, Start
closes; on the keyboard arrows, Enter / Space and Esc. With the pause off the
game keeps reading those buttons, so the menu is mouse-only then.

### Wii pointer and rumble

The mouse is the Wii Remote pointer over the game picture: with black bars
(aspect 4:3, or widescreen 16:9 on a differently shaped window) the bars are
outside the Wii screen, so the pointer disappears there like a Wii Remote
pointed past the TV. It follows window resizes, F11 and aspect changes.

The gamepad's right stick moves the pointer too (`[Controller] stick_pointer =
1`, default): it moves from where the pointer is and leaves it there; moving
the mouse gives the pointer back to the mouse. Speed and deadzone are
`pointer_speed` (1..100, default 50) and `pointer_deadzone` (0..90, default 20).

When the game rumbles the Wii Remote, the gamepad rumbles (`[Controller] rumble
= 1`, default; `rumble_strength` 0..100, default 70). F1 -> Controls has all
of these, applied live, plus **Test rumble**. OpenSPM follows the game's own
rumble requests (its Wii Remote manager and the short menu-pointer pulse); the
Wii HOME Menu's rumble does not exist here (see below). Rumble stops while the
OpenSPM menu is open.

### HOME and the Wii HOME Menu

The Wii's own HOME Menu is a separate system library the game calls; this port
does not provide it, and OpenSPM never sends the HOME button to the game. HOME
opens the OpenSPM menu instead (`[Controller] home_opens_menu = 0` makes HOME do
nothing; F1 always works).

### Shake

The Wii version asks you to shake the Remote (stylish moves after stomping an
enemy). **Q** or **RB** does it: OpenSPM feeds the game an accelerometer swing
(it never reaches the game as a button). A tap is one swing; hold it to keep
shaking. The log says `OpenSPM input: Wii Remote shake` the first time.

Items whose gauge fills by shaking (Fire Burst, Ice Storm and the like) take a
held Q / RB. The game reads the accelerometer only for shakes (stylish moves,
those item gauges, a shake prompt), never for a steady tilt, so there is no
tilt control.

## Hotkeys

| Key | Action |
| --- | --- |
| F1 / HOME | OpenSPM menu: display, graphics, texture packs, controls, debug, cheats, mods (Esc closes) |
| Tab (hold) / right stick click | fast-forward (turbo, see Fast-forward) |
| F11 | windowed <-> borderless fullscreen |
| F12 | screenshot to `screenshots\OpenSPM-<date>-<time>.png` |
| F8 | GX frame capture into the log (debugging) |

## Settings: OpenSPM.ini

Copy `OpenSPM.ini.example` to `OpenSPM.ini` next to the launcher and edit it,
or change settings in the OpenSPM menu (F1), which saves every change to the
file at once. Launcher arguments and variables set in the console take
precedence over the file.

| Section | Key | Values |
| --- | --- | --- |
| Display | window_mode | windowed (default), borderless, exclusive |
| Display | refresh_hz | auto (default) or Hz, for exclusive fullscreen |
| Display | aspect | 4:3 (default, black bars), stretch; with widescreen, 4:3 keeps the 16:9 picture's aspect |
| Display | widescreen | 0 (default), 1: native 16:9 (see Widescreen below; applies on restart) |
| Display | display_sync | auto (default: lock the game clock to the display), off |
| Display | frame_mode | vanilla (default), smooth (interpolated, experimental), vanilla_timeline (diagnostic) |
| Display | interp_camera_views | 1 (default), 0: Smooth interpolates matrices without the game's camera views |
| Effects | copy_tags | 1 (default), 0: CPU-moved EFB copies (star doors) |
| Effects | copy_pixel_exact | 1 (default), 0: pixel-exact EFB copies |
| Effects | bbox_logical | 1 (default), 0: bounding boxes in game units (pipes) |
| Controller | enabled | 1 (default), 0 |
| Controller | map | e.g. `a=2,b=B,x=1,y=A,start=PLUS,back=MINUS` |
| Controller | home_opens_menu | 1 (default): HOME (Guide / Home key) opens the OpenSPM menu, 0: HOME does nothing |
| Controller | stick_pointer | 1 (default): the right stick moves the Wii pointer, 0: off |
| Controller | pointer_speed | 1..100 (default 50) |
| Controller | pointer_deadzone | 0..90 (default 20), percent of full tilt |
| Controller | rumble | 1 (default): the gamepad rumbles with the game's Wii Remote, 0: off |
| Controller | rumble_strength | 0..100 (default 70) |
| Keyboard | map | e.g. `up=Up/W,...,2=Enter,home=Home` (see Controls) |
| Overlay | show_fps | 0 (default), 1 |
| Overlay | pause_game | 1 (default): the game pauses while the OpenSPM menu is open, 0: it keeps running |
| Game | turbo | 1 (default): the turbo hotkey works (see Fast-forward), 0: off (applies on restart) |
| Game | turbo_speed | 2 (default), 3, 4, unlimited: game speed while turbo is engaged (applies on restart) |
| Game | turbo_mode | hold (default): turbo while the key is held, toggle: press once on, once off (applies on restart) |
| Mods | enabled | 0 (default), 1: load `mods\*.dll` (see MODDING.md) |
| Game | path | your .iso / .wbfs / .wia or extracted folder (OpenSPMNative.exe without `--game`) |
| Game | wit | path to wit.exe used to extract a disc image once |
| Cheats | infinite_hp | 0 (default), 1 (see Cheats below) |
| Cheats | code1 ... code16 | `<0/1> \| <name> \| <Gecko 04 codes>` |
| TexturePacks | enabled | 1 (default), 0: Dolphin HD texture packs (see TEXTURE-PACKS.md) |
| TexturePacks | folder | pack folder, default `texturepacks` next to the exe |
| TexturePacks | cache_mb | 512, 1024, 2048 (default), 4096: GPU memory for pack textures |
| TexturePacks | dump | 0 (default), 1: dump textures as Dolphin `tex1_` PNGs |

## OpenSPM menu (F1)

F1 or HOME opens it; it is used with the mouse; Esc, F1 or HOME closes it.
While it is open the game is paused: it stops between two frames, the last
image stays on screen under the menu, and the game's clock (timers, music
timing, the play-time counter) stops too, so after closing the game continues
exactly where it was instead of catching up. `[Overlay] pause_game = 0` (F1 ->
Controls, applies on restart) keeps the game running behind the menu instead.
Either way the game receives no buttons and no pointer motion while the menu
is open.

* **Display**: windowed / borderless now, window size presets, start-up
  window mode and exclusive-fullscreen refresh rate, 4:3 or stretch,
  widescreen 16:9, display sync, frame mode (Vanilla 60 / Smooth), FPS counter, current frame rate.
* **Graphics**: the star-door / copy / bounding-box effect fixes.
* **Texture Packs**: Dolphin HD texture packs: on/off and priority per pack,
  texture counts, open the packs folder, texture dumping (TEXTURE-PACKS.md).
* **Controls**: keyboard rebinding (live), HOME / pause switches, turbo
  (fast-forward) settings, the connected gamepad (held buttons light up) and
  its button mapping.
* **Debug**: frame-time graph, screenshot, GX frame capture, read-only game
  state.
* **Cheats**: infinite HP, max coins and your own Gecko codes (below).
* **Mods**: load mods on/off and per-mod toggles.

Aspect, FPS counter, window mode and size apply immediately. Everything
marked "applies on restart" is read at launch; it turns into "restart to
apply" when the saved value differs from the running one.

## Widescreen (16:9)

Super Paper Mario supports 16:9 TVs: on a Wii set to 16:9 it renders a wider
view (more of the level left and right) squeezed into its 4:3 picture, and the
TV stretches it back. `[Display] widescreen = 1` (F1 -> Display -> Widescreen
16:9, or the launcher flag `widescreen`, e.g. `M14-VANILLA-RUN.cmd AUTO widescreen`)
does the same on PC:

* OpenSPM reports the console's TV setting (SYSCONF `IPL.AR`) as 16:9, so the
  game itself picks its 16:9 camera. No game code is changed.
* The renderer shows that picture at 16:9: black bars on 4:3 or ultrawide
  windows, none on a 16:9 window or fullscreen on a 16:9 display. The game is
  drawn at the full 16:9 window resolution (e.g. 1920x1080), not upscaled.
  `aspect = stretch` fills any window instead.

It is read at launch (restart after changing it). Menus and 2D screens are
drawn by the game for 16:9 too; anything that looks squeezed or cut off is
worth a report with a screenshot (F12).

## Cheats

The Cheats tab of the F1 menu. Every cheat is off by default and does nothing
while unchecked; checked cheats apply at once and are saved in `OpenSPM.ini`
`[Cheats]`, so they are back on the next launch. Saving the game while a
cheat is on keeps its effect in your save (for example 999 coins).

* **Infinite HP**: HP is refilled to max HP every frame in game. A hit that
  takes all remaining HP at once still counts: HP 0 is left to the game's own
  death / Game Over flow so it never breaks.
* **Give Gold Bar x3** (a button): adds one Gold Bar x3 to your items, the
  game's own way (its item code), while you are in a level. Sell it in a shop
  for coins. (It replaces "Max coins": changing the coin count directly
  softlocked shop purchases.)
* **Custom codes**: Gecko `04XXXXXX YYYYYYYY` codes, one per line, each
  writing the 32-bit value `YYYYYYYY` at `80XXXXXX` every frame (`05` for
  `81XXXXXX`). Other code types are rejected, as is any address outside the
  Wii's RAM or not 4-byte aligned; a rejected entry is shown in orange and not
  applied. Only codes that change game data work: the game is recompiled to
  PC code, so codes that patch the game's instructions have no effect. Use
  codes made for R8PE01 revision 2 (US); other versions use other addresses.
  Up to 16 entries, 128 codes in total.

How it works: the menu never touches game memory. It hands its list to the
game core, which writes it once per frame at the frame boundary where input
is latched, while the game is paused between frames. `OPENSPM_CHEATS=0`
turns the whole feature off. The addresses used (and how they were checked
against the game's code) are listed in `include/openspm/cheats.h`.

## Frame rate

The default is Vanilla 60: one image per game frame, like the console. On a
display whose refresh fits 60 Hz (59.94 / 59.978 / 60 / 120 / 240 Hz) the game
clock locks to the display so motion is judder-free. On other refresh rates
(e.g. 100 Hz) prefer exclusive fullscreen, which switches to a 60-friendly mode.

F1 -> Display -> **Frame mode** (`[Display] frame_mode`, applies on restart):

| Mode | What you get |
| --- | --- |
| Vanilla 60 (`vanilla`, default) | One image per game frame with vsync, exactly like the console. |
| Smooth (interpolated) (`smooth`, experimental) | A new image on every display refresh, blending the camera and each object between the last two game frames. Only visible on displays above 60 Hz (100 / 120 / 144 / 240 Hz); at 60 Hz it looks like Vanilla 60. Effects that cannot be replayed safely (flips, door effects) show the plain game frame. |
| Vanilla 60 via present timeline (`vanilla_timeline`) | Diagnostic: the Smooth presenter showing only real game frames. |

The game logic always runs at 60 Hz; nothing here changes game speed. Smooth
is the launcher's `interp` flag (present timeline + draw tags + frame
interpolation) as a setting; the flags `timeline`, `timelinevanilla`,
`drawtags`, `interp` and `interpdirect` still work and override the setting.
The menu shows which presenter is running and the display clock it follows.

Not available yet (they need more presenter work, see
`PRESENTATION-ARCHITECTURE.md`): a custom frame cap (e.g. 90 fps on a 144 Hz
display), unlimited / VRR presentation, and vsync off. A plain vsync-off
switch exists only as a test launcher (`M14-LLVM-NOVSYNC-RUN.cmd`,
`M14-LLVM-IMMEDIATE-RUN.cmd`): with a 60 Hz game it adds tearing and loses the
display clock lock, so it is not offered as a setting.

## Fast-forward (turbo)

Hold **Tab** (or click the right stick of a gamepad) to run the game faster,
like an emulator's fast-forward; release it and the game continues at normal
speed. `[Game] turbo_speed` picks 2x (default), 3x, 4x or unlimited (as fast as
the PC can run it); `[Game] turbo_mode = toggle` makes one press start and the
next stop. "Turbo 2x" (or "Turbo unlimited") shows at the top-left, under the
FPS counter when that is on. Rebind the key in F1 -> Controls (the `turbo`
slot, `[Keyboard] map ... turbo=Tab`); the turbo key never reaches the game,
and the default Tab gives way if your map uses Tab for a game button.

It is available by default but only active while engaged: without the key
nothing differs from normal play. `[Game] turbo = 0` removes the hotkey.

How it works: the game's whole clock runs N x as fast (the Wii timebase, the
60 Hz retraces the game waits for, timers, music timing), so the game simply
plays faster; releasing the key continues from the current game time, there is
no jump or catch-up. Unlimited additionally lets the game skip the wait for its
next retrace. The screen shows at most one image per display refresh (the
newest), the other frames are rendered but not shown, so vsync does not hold
the game back; Smooth mode shows the newest frame without interpolation while
turbo runs. Audio is muted during turbo. Opening the OpenSPM menu releases
turbo.

## Known issues

* The 2D/3D flip shows a black card instead of Mario (under investigation).
* No audio yet.
