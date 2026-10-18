# M14 — Natural title and load milestone

## Result

The standalone no-Dolphin whole-game diagnostic path has now completed the real startup flow naturally far enough to cross the first Windows-candidate stability gate.

Using the verified R8PE01 `main.dol` (`316fe3a88654cf55f164a493fdc7d872fb7362298613e335a2240b4921bfe7a0`) and 218 locally generated chunks, the run completed:

```text
boot
 -> warning/logo
 -> opening replay / map changes
 -> SEQ_TITLE
 -> title.bin.us loaded
 -> PC-native Start/A input
 -> SEQ_LOAD
 -> 600 additional stable load-screen retraces
```

There was no forced sequence transition in this proof. The opening replay returned to the title state through SPM's own sequence/event logic.

Observed milestones from the private validation log:

- retrace 6637: natural `SEQ_TITLE` (`seq=1`, `stage=-1`)
- title resource: `./lyt/title.bin.us`, 238781-byte payload
- retrace 6757: controlled PC-native press-start pulse
- retrace 6779: `SEQ_LOAD` (`seq=5`, `stage=0`)
- then 600 more stable `SEQ_LOAD` retraces with no panic or exception

Dispatcher/yield counts are not FPS measurements. This proof establishes runtime correctness/progress, not final performance.

## Runtime contracts proven during bring-up

The private diagnostic path now has working behavior for:

- correct Wii disc bootstrap and DOL load ordering;
- direct native game-to-game calls with explicit host boundaries;
- same-chunk hosted BLs forced back to host side exits when required;
- per-dispatch loop budget replenishment;
- lazy-FPU recovery and hosted cache/system-call barriers;
- PC-native system settings readiness;
- controller initialization/readiness and frame-snapshot button input;
- real guest thread sleep/wake scheduling driven by host retrace events;
- SPM's registered post-retrace callback;
- synchronous and asynchronous GameFS reads from the player's extracted disc;
- DSP/JAudio task-bootstrap completion semantics;
- deferred GX draw-done completion after the guest arms its wait;
- GX draw-sync token completion;
- 32-byte-aligned display-list completion size;
- fresh-PC-install save semantics sufficient for startup;
- host cache-maintenance fast paths for coherent PC memory.

## Renderer caveat

The natural-title proof still uses one diagnostic approximation: the headless runner supplies a bounded 64x64 GX bounding box when SPM asks for raster bounds. This replaced an invalid zero-register result that decoded as `-2 x -2`, which had caused malformed 1022x1022 texture descriptors and smart-heap failures.

The 64x64 value is evidence that the remaining failure was missing renderer bounding-box feedback, not corrupted game state. It is **not** the production renderer solution. A Windows candidate should get real bounding-box/raster feedback from the native renderer rather than shipping this fallback as correctness behavior.

## Persistence status

The semantic behavior above is proven in the private whole-game diagnostic harness. The repository's persistent M14 lab/backend still needs the later host services promoted into reusable OpenSPM-owned code before a fresh repository-only run can reproduce the complete natural-title proof.

`tools/native_bridge/direct_calls.py` and its tests already contain the durable hosted-boundary rewrite, including the same-chunk case.

## Next milestone

Build the first M14 Windows-native candidate after promoting the proven host-service layer into the persistent runtime and connecting it to the existing Windows launcher/display path. The candidate success gate is:

1. player supplies/extracts their own R8PE01 game;
2. M14 boots naturally to the real title screen;
3. real keyboard/controller input advances to file select;
4. the Windows renderer supplies real draw completion, draw-sync and bounding-box results;
5. no diagnostic forced sequence jumps or fixed bounding-box fallback are required.

M12 remains frozen at `f64c4e4023449a60053048393cc4bc1deb3345f8` and must not be rewritten or used as the M14 development target.

No Nintendo-derived generated source, assets, DOL/REL bytes, textures or disc image are committed by this milestone.
