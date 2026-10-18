@echo off
setlocal
set "GRAPHICS=%~1"
if not defined GRAPHICS set "GRAPHICS=AUTO"
rem Optional 2nd argument: native-module toolchain msvc (default), clang or
rem clang-freestanding (docs/RELEASE-PLAN.md section 5). Any other 2nd argument
rem is one of the A/B flags below (e.g. widescreen) and keeps msvc.
set "NATIVE_TOOLCHAIN=msvc"
if /i "%~2"=="clang" set "NATIVE_TOOLCHAIN=clang"
if /i "%~2"=="clang-freestanding" set "NATIVE_TOOLCHAIN=clang-freestanding"
rem OPENSPM_LAUNCH_FLAGS (set by OPENSPM-TEST-LAUNCHER.cmd): any number of the
rem flags below, space separated, on top of the 2nd-4th arguments.
for %%A in (%OPENSPM_LAUNCH_FLAGS%) do (
  if /i "%%~A"=="clang" set "NATIVE_TOOLCHAIN=clang"
  if /i "%%~A"=="clang-freestanding" set "NATIVE_TOOLCHAIN=clang-freestanding"
)

rem OpenSPM "Vanilla 60" frame-rate mode (default presentation target).
rem - one presented image per simulated game frame, FIFO vsync, no interpolation
rem - the renderer reads the window's exact display refresh; when vanilla 60 fits
rem   it (59.94/59.978/60 Hz, or 119.88/120/240 Hz) the game clock is locked to
rem   that refresh so every game frame is held for the same number of refreshes
rem - on a display that is not a whole multiple of 60 (e.g. 99.943 / 74.991 Hz)
rem   the clock stays 60.000 Hz and the log names the display modes that would fix it
rem Optional A/B controls as 2nd/3rd arguments:
rem   nosync     = fixed 60.000 Hz clock instead of the display-locked clock
rem   fullscreen = exclusive fullscreen; auto-selects a refresh vanilla 60 locks to
rem                (e.g. 59.978 Hz) without changing the Windows desktop setting
rem   borderless = borderless fullscreen at the desktop refresh
rem   copytrace  = log EFB texture copies/binds (flip, door effect diagnosis)
rem   efbram     = also write EFB texture copies back to guest RAM (slow GPU readback diagnostic)
rem   notags     = disable EFB copy tags (door-swing fix; A/B only)
rem   bboxmemo   = reuse bbox results for identical bbox draws (door lag fix, experimental)
rem   bboxfast   = answer bbox reads after provably empty draws without a GPU sync
rem                (star door start hitch fix, experimental; exact, no guessing)
rem   bboxfastverify = same proofs, but every read still goes to the GPU and any
rem                difference is logged ("M14 bbox fast verify MISMATCH")
rem   nopixelexact = legacy EFB copy source rectangle (A/B for the star-door down-right shift)
rem   snapdump   = save each star-door/flip snapshot image to screenshots\ (diagnostic)
rem   snapcapture = GX capture from the first full-screen snapshot (flip start, ~2 frames)
rem   flipcapture = log full GX state from the first in-game flip (A) press (~3 frames)
rem   doorcapture = log full GX state from the first door/flip start (bbox burst, ~6 frames)
rem   oldorder   = legacy ordering where a due retrace can pre-empt an already
rem                completed DrawDone (the 33 ms dropped-frame bug)
rem   timeline   = Stage A present timeline (experimental): vblank-driven async
rem                presenter; each refresh shows the game frame nearest to its
rem                predicted scan-out and logs the interpolation alpha it would use
rem   timelinevanilla = same presenter in vanilla mode (alpha 0, newest frame,
rem                each frame held k refreshes on 60/120/240 Hz)
rem   drawtags   = Stage B draw tags (diagnostic, no visual change): the bridge marks
rem                every dispDraw callback's draws with its owner in the GX FIFO and
rem                the renderer logs owner coverage once per second
rem   interp     = Stage C frame interpolation (experimental, above 60 Hz displays):
rem                timeline + draw tags + an interpolated image for every refresh
rem                (camera and objects matched by owner; unsafe frames show natively)
rem   interpdirect = interp, but interpolate matrices without the game's camera views
rem   widescreen = native 16:9: the game renders a wider anamorphic view (the backend
rem                reports the Wii TV setting SYSCONF IPL.AR = 16:9) shown at 16:9 with
rem                black bars on other windows (OpenSPM.ini [Display] widescreen = 1)
rem   safe       = turn off the newer default-on extras (menu pause, HOME menu, stick
rem                pointer, rumble, turbo, cheats, texture packs) for an A/B when
rem                the normal launch misbehaves; overrides OpenSPM.ini
rem   audio      = game audio (experimental AX HLE, docs/AUDIO-PLAN.md); same as
rem                OpenSPM.ini [Audio] enabled = 1. Look for "M14 audio:" in the log
rem Without these flags the presenter follows OpenSPM.ini [Display] frame_mode
rem (vanilla | smooth | vanilla_timeline, F1 menu -> Display -> Frame mode); the
rem presenter variables below are cleared, not set to 0, so the setting can apply.
rem Player settings: OpenSPM.ini in this folder (copy OpenSPM.ini.example). Launcher
rem arguments below and variables already set in the console take precedence.
if not defined OPENSPM_SETTINGS set "OPENSPM_SETTINGS=%~dp0OpenSPM.ini"
set "OPENSPM_M14_FINISH_BEFORE_RETRACE=1"
if /i "%~2"=="fullscreen" set "OPENSPM_WINDOW_MODE=exclusive"
if /i "%~3"=="fullscreen" set "OPENSPM_WINDOW_MODE=exclusive"
if /i "%~2"=="borderless" set "OPENSPM_WINDOW_MODE=borderless"
if /i "%~3"=="borderless" set "OPENSPM_WINDOW_MODE=borderless"
if /i "%~2"=="widescreen" set "OPENSPM_WIDESCREEN=1"
if /i "%~3"=="widescreen" set "OPENSPM_WIDESCREEN=1"
if /i "%~4"=="widescreen" set "OPENSPM_WIDESCREEN=1"
if /i "%~2"=="notags" set "OPENSPM_M14_COPY_TAGS=0"
if /i "%~3"=="notags" set "OPENSPM_M14_COPY_TAGS=0"
if /i "%~4"=="notags" set "OPENSPM_M14_COPY_TAGS=0"
if /i "%~2"=="flipcapture" set "OPENSPM_M14_COPY_CAPTURE=72x88"
if /i "%~3"=="flipcapture" set "OPENSPM_M14_COPY_CAPTURE=72x88"
if /i "%~4"=="flipcapture" set "OPENSPM_M14_COPY_CAPTURE=72x88"
if /i "%~2"=="doorcapture" set "OPENSPM_M14_CAPTURE_ON_BBOX_BURST=1"
if /i "%~3"=="doorcapture" set "OPENSPM_M14_CAPTURE_ON_BBOX_BURST=1"
if /i "%~4"=="doorcapture" set "OPENSPM_M14_CAPTURE_ON_BBOX_BURST=1"
if /i "%~2"=="bboxmemo" set "OPENSPM_M14_BBOX_MEMO=1"
if /i "%~3"=="bboxmemo" set "OPENSPM_M14_BBOX_MEMO=1"
if /i "%~4"=="bboxmemo" set "OPENSPM_M14_BBOX_MEMO=1"
if /i "%~2"=="nopixelexact" set "OPENSPM_M14_COPY_PIXEL_EXACT=0"
if /i "%~3"=="nopixelexact" set "OPENSPM_M14_COPY_PIXEL_EXACT=0"
if /i "%~4"=="nopixelexact" set "OPENSPM_M14_COPY_PIXEL_EXACT=0"
if /i "%~2"=="snapdump" set "OPENSPM_M14_SNAPSHOT_DUMP=1"
if /i "%~3"=="snapdump" set "OPENSPM_M14_SNAPSHOT_DUMP=1"
if /i "%~4"=="snapdump" set "OPENSPM_M14_SNAPSHOT_DUMP=1"
if /i "%~2"=="snapcapture" set "OPENSPM_M14_CAPTURE_ON_SNAPSHOT=1"
if /i "%~3"=="snapcapture" set "OPENSPM_M14_CAPTURE_ON_SNAPSHOT=1"
if /i "%~4"=="snapcapture" set "OPENSPM_M14_CAPTURE_ON_SNAPSHOT=1"
if /i "%~2"=="efbram" set "OPENSPM_M14_EFB_COPY_RAM=1"
if /i "%~3"=="efbram" set "OPENSPM_M14_EFB_COPY_RAM=1"
if /i "%~4"=="efbram" set "OPENSPM_M14_EFB_COPY_RAM=1"
if /i "%~4"=="copytrace" set "OPENSPM_M14_COPY_TRACE=1"
if /i "%~2"=="copytrace" set "OPENSPM_M14_COPY_TRACE=1"
if /i "%~3"=="copytrace" set "OPENSPM_M14_COPY_TRACE=1"
if /i "%~2"=="nosync" set "OPENSPM_M14_DISPLAY_SYNC=off"
if /i "%~3"=="nosync" set "OPENSPM_M14_DISPLAY_SYNC=off"
if /i "%~2"=="oldorder" set "OPENSPM_M14_FINISH_BEFORE_RETRACE=0"
if /i "%~3"=="oldorder" set "OPENSPM_M14_FINISH_BEFORE_RETRACE=0"
for %%A in (%OPENSPM_LAUNCH_FLAGS%) do (
  if /i "%%~A"=="fullscreen" set "OPENSPM_WINDOW_MODE=exclusive"
  if /i "%%~A"=="borderless" set "OPENSPM_WINDOW_MODE=borderless"
  if /i "%%~A"=="widescreen" set "OPENSPM_WIDESCREEN=1"
  if /i "%%~A"=="notags" set "OPENSPM_M14_COPY_TAGS=0"
  if /i "%%~A"=="flipcapture" set "OPENSPM_M14_COPY_CAPTURE=72x88"
  if /i "%%~A"=="doorcapture" set "OPENSPM_M14_CAPTURE_ON_BBOX_BURST=1"
  if /i "%%~A"=="bboxmemo" set "OPENSPM_M14_BBOX_MEMO=1"
  if /i "%%~A"=="nopixelexact" set "OPENSPM_M14_COPY_PIXEL_EXACT=0"
  if /i "%%~A"=="snapdump" set "OPENSPM_M14_SNAPSHOT_DUMP=1"
  if /i "%%~A"=="snapcapture" set "OPENSPM_M14_CAPTURE_ON_SNAPSHOT=1"
  if /i "%%~A"=="efbram" set "OPENSPM_M14_EFB_COPY_RAM=1"
  if /i "%%~A"=="copytrace" set "OPENSPM_M14_COPY_TRACE=1"
  if /i "%%~A"=="nosync" set "OPENSPM_M14_DISPLAY_SYNC=off"
  if /i "%%~A"=="oldorder" set "OPENSPM_M14_FINISH_BEFORE_RETRACE=0"
)
set "OPENSPM_M14_RETRACE_PERIOD_NS="
set "OPENSPM_M14_DIAG_HALF_99_943HZ=0"
set "OPENSPM_INTERPOLATION=off"
set "OPENSPM_M14_ASYNC_PRESENTER="
set "OPENSPM_M14_PRESENT_TIMELINE="
set "OPENSPM_M14_PRESENT_TIMELINE_MODE="
set "OPENSPM_M14_DISPLAY_REFRESH="
set "OPENSPM_M14_INTERP_PRESENT=0"
set "OPENSPM_M14_INTERP_REPLAY=0"
set "OPENSPM_M14_INTERP_REPLAY_ENCODE=0"
set "OPENSPM_M14_INTERP_PROBE=0"
set "OPENSPM_M14_INTERP_VISUAL=off"
set "OPENSPM_M14_VSYNC=1"
set "OPENSPM_M14_PRESENT_MODE=fifo"
set "OPENSPM_M14_PRECISE_IDLE_WAIT=1"
set "OPENSPM_M14_FINAL_IDLE_SPIN=1"
set "OPENSPM_M14_FIFO_MODE=drain"
set "OPENSPM_M14_STICKY_PRESSED=1"
set "OPENSPM_M14_MAIN_PRIORITY=off"
set "OPENSPM_M14_MAIN_QOS=off"
set "OPENSPM_M14_MAIN_CPUSET=fast"
set "OPENSPM_M14_DRAW_TAGS="
set "OPENSPM_M14_INTERP="
set "OPENSPM_M14_INTERP_VIEW="
set "OPENSPM_TIMELINE_ARG="
for %%A in ("%~2" "%~3" "%~4" %OPENSPM_LAUNCH_FLAGS%) do (
  if /i "%%~A"=="timeline" set "OPENSPM_TIMELINE_ARG=smooth"
  if /i "%%~A"=="timelinevanilla" set "OPENSPM_TIMELINE_ARG=vanilla"
  if /i "%%~A"=="drawtags" set "OPENSPM_M14_DRAW_TAGS=1"
  if /i "%%~A"=="bboxfast" set "OPENSPM_M14_BBOX_FAST=1"
  if /i "%%~A"=="bboxfastverify" set "OPENSPM_M14_BBOX_FAST=verify"
  if /i "%%~A"=="interp" set "OPENSPM_M14_INTERP=1"
  if /i "%%~A"=="interpdirect" set "OPENSPM_M14_INTERP=1"
  if /i "%%~A"=="interpdirect" set "OPENSPM_M14_INTERP_VIEW=0"
  if /i "%%~A"=="audio" set "OPENSPM_AUDIO=1"
  if /i "%%~A"=="safe" (
    set "OPENSPM_PAUSE_GAME=0"
    set "OPENSPM_HOME_MENU=0"
    set "OPENSPM_STICK_POINTER=0"
    set "OPENSPM_RUMBLE=0"
    set "OPENSPM_TURBO=0"
    set "OPENSPM_CHEATS=0"
    set "OPENSPM_TEXTURE_PACKS=0"
  )
)
if "%OPENSPM_M14_INTERP%"=="1" (
  set "OPENSPM_TIMELINE_ARG=smooth"
  set "OPENSPM_M14_DRAW_TAGS=1"
)
if defined OPENSPM_TIMELINE_ARG (
  set "OPENSPM_M14_ASYNC_PRESENTER=1"
  set "OPENSPM_M14_PRESENT_TIMELINE=1"
  set "OPENSPM_M14_PRESENT_TIMELINE_MODE=%OPENSPM_TIMELINE_ARG%"
)

echo [OpenSPM] Vanilla 60 presentation: one frame per game tick, FIFO vsync, no interpolation.
if not defined OPENSPM_TIMELINE_ARG echo [OpenSPM] Frame mode: OpenSPM.ini [Display] frame_mode ^(default vanilla; F1 menu -^> Display^).
if defined OPENSPM_LAUNCH_FLAGS echo [OpenSPM] Launch flags: %OPENSPM_LAUNCH_FLAGS%
echo [OpenSPM] Settings file: %OPENSPM_SETTINGS% ^(missing = defaults^)
if defined OPENSPM_M14_DISPLAY_SYNC echo [OpenSPM] Display clock lock: %OPENSPM_M14_DISPLAY_SYNC%
if defined OPENSPM_WINDOW_MODE echo [OpenSPM] Window mode: %OPENSPM_WINDOW_MODE% ^(refresh: %OPENSPM_REFRESH_HZ%^)
if defined OPENSPM_ASPECT echo [OpenSPM] Output aspect: %OPENSPM_ASPECT%
if defined OPENSPM_WIDESCREEN echo [OpenSPM] Widescreen 16:9: %OPENSPM_WIDESCREEN%
echo [OpenSPM] GPU-finish before retrace: %OPENSPM_M14_FINISH_BEFORE_RETRACE%
echo [OpenSPM] For a judder-free test set Windows to 59.978 Hz first:
echo [OpenSPM]   Settings ^> System ^> Display ^> Advanced display ^> Choose a refresh rate.
echo [OpenSPM] Look for "M14 vanilla pacing:" and "display-locked vanilla clock" in the log.
if defined OPENSPM_TIMELINE_ARG echo [OpenSPM] Present timeline ^(%OPENSPM_TIMELINE_ARG%^): look for "M14 present timeline" once per second in the log.
if "%OPENSPM_M14_DRAW_TAGS%"=="1" echo [OpenSPM] Draw tags: look for "M14 draw tags:" and "M14 draw tags bridge:" once per second in the log.
if defined OPENSPM_M14_BBOX_FAST echo [OpenSPM] bbox fast path ^(%OPENSPM_M14_BBOX_FAST%^): look for "M14 bbox fast:" once per second in the log.
if "%OPENSPM_AUDIO%"=="1" echo [OpenSPM] Audio ^(experimental^): look for "M14 audio:" once per second in the log.
if "%OPENSPM_M14_INTERP%"=="1" echo [OpenSPM] Frame interpolation ^(Stage C^): look for "M14 frame interp:" once per second in the log.
echo.
rem The update step may git-pull a new version of this file while it runs; cmd
rem reads batch files by offset, so call and exit share one pre-parsed line.
call "%~dp0M14-LLVM-UPDATE-RUN.cmd" "%GRAPHICS%" 256 1 0 "%NATIVE_TOOLCHAIN%" & exit /b
