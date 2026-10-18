# M14 runtime 054129/055407 — live resize correctness proof and architecture

Date: 2026-09-14
Broken-resize log: `launch(20260914-054129).log`
Confirmed-fixed log: `launch(20260914-055407).log`
Broken-resize repo: `afb6941af22d99cf543e852077644144bdd1a405`
Confirmed-fixed repo: `a1ab0539adb57c5053688d0ad348090cf8677a0b`

## Player-controlled A/B

At `afb6941...`, the spinning device and door both work normally when the host
window is left alone. Resizing the host window while Mario is inside the spinning
device immediately causes Mario to smear or disappear, and the subsequent door
interaction softlocks.

At `a1ab0539...`, ordinary live OS resize is decoupled from Wii graphics apply.
The same player abuse test was repeated: resize while Mario is inside the spinning
device, then use the door. Neither corruption nor the door softlock occurs.

This is now a clean causal A/B: spinner corruption + door softlock are **live
Aurora graphics-reconfiguration symptoms**, not reliable evidence that an
unrelated renderer/cache optimization regressed gameplay.

## Important correction: resize stalls were correlated, not the correctness cause

The first broken run showed resize events followed by very large `update-events`
stalls (roughly 200-475 ms), and the fixed-phase native retrace scheduler skipped
elapsed VI deadlines during those stalls. That initially looked like a plausible
shared cause for the spinner and door failures.

The `055407` fixed run falsifies that explanation for correctness. Ordinary live
resize no longer calls `webgpu::resize_swapchain`, but SDL/window-event handling
can still block for large intervals during a drag (including ~170-485 ms and one
~1.09 s drag), with corresponding reduced retrace delivery. Despite those stalls,
Mario remains correct and the door completes normally.

Therefore:

- skipped VI epochs during a host drag are still a responsiveness/pacing issue;
- they are **not sufficient to cause** the spinner corruption or door softlock;
- the correctness failure came from the actual Aurora GPU/render-target resize
  mutation path (GPU synchronization / framebuffer-surface recreation / cache and
  resource lifetime changes), not merely elapsed host time.

Do not use spinner/door failure as a regression signal unless a run is controlled
for whether live graphics reconfiguration occurred.

## Architectural decision

OpenSPM should not treat live OS window dragging as a Wii graphics
reconfiguration.

Manual host resize is presentation-shell state. Wii EFB/render size/aspect should
change only through an explicit OpenSPM graphics setting. The eventual user-facing
model should be preset/menu driven: selected output resolution, aspect ratio,
render scale, ultrawide mode, etc. Applying a setting can intentionally recreate
host render resources at a known safe boundary.

The current raw-Wii adapter implements that split:

- ordinary SDL pixel-size/display-scale changes update Aurora's window metadata but
  do not call `webgpu::resize_swapchain`;
- the existing internal `FutureResize` event is retained as the explicit graphics-
  apply path and forces the reconfiguration;
- explicit apply bypasses the old `size == g_windowSize` early-out so a prior shell
  resize cannot swallow a deliberate graphics change;
- GXCopyTex persistence protection remains as defense for deliberate graphics
  apply;
- normal non-raw-Wii Aurora behavior is unchanged.

## Current visual tradeoff

Because live drag no longer changes the raw-Wii framebuffer, enlarging the shell
currently stretches the existing render target. The player correctly described
this as similar to enlarging raster art rather than re-rendering vector art: image
quality becomes softer when the window is made much larger.

This is intentional for the temporary live-resize policy. It is not the final
resolution design.

Future OpenSPM graphics presets should deliberately set the render target / output
view to match the chosen window or fullscreen preset. For example, selecting a
1280x720 or ultrawide preset should explicitly apply the corresponding host render
size/aspect at a controlled boundary rather than relying on arbitrary OS drag
size. Hor+ remains the intended ultrawide policy.

## Confirmed markers

During ordinary manual resize in the fixed run, expect:

`M14 raw-Wii live window resize deferred ... graphics apply unchanged`

The old:

`M14 raw-Wii resize preserves guest-visible EFB copy textures ...`

must not fire for ordinary live drags; it is reserved for deliberate graphics
apply.

## Remaining resize-related work

1. Keep live OS drag presentation-only for correctness.
2. Eventually implement OpenSPM-owned preset resolution/aspect controls using the
   explicit `FutureResize` seam.
3. Decide whether arbitrary live dragging remains supported, becomes aspect-
   constrained, or is replaced by preset/window modes in the final UI.
4. The long SDL/window-event stalls during interactive drag remain a separate
   responsiveness issue, but are no longer a blocker for spinner/door correctness.
5. Return performance work to the scene-dependent producer/DrawDone frontier;
   resize correctness is now isolated from that investigation.
