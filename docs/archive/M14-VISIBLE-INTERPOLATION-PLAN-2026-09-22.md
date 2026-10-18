# Visible camera interpolation — implementation and validation plan

## Goal and current delivery

Keep authoritative Wii gameplay at 60 Hz and show intermediate camera/object
translations at the display cadence. The new opt-in launcher is
`M14-LLVM-INTERPOLATED-RUN.cmd` (AUTO, 100 Hz by default; supported target 61–120).
This is actual scene replay with modified transform uniforms, not full-image
blending or faster game simulation. It intentionally adds about one game tick
of presentation latency so adjacent authoritative poses are available.

The implementation is ready for a first Windows visual/performance test. No
claim of demonstrated smoothness, GPU image equivalence, or recovered dev140a
performance is made before that test.

## Implemented

1. **Preparation memory access.** Record cacheable CPU mirrors of uniform,
   vertex and index bytes at the existing upload entry point, with identical
   alignment and offsets. Matching and arena cloning use CPU memory. This
   removes repeated reads of mapped upload memory from the normal path. A
   bounded bulk-copy fallback handles incomplete mirrors. Preparation reports
   `read_copy_us`, `snapshot_us`, `match_us`, `clone_us` and total `prep_us`.
   This addresses a concrete access pattern; the Windows cost reduction is
   still unmeasured. Matching and shared-uniform conflict rules remain intact.
2. **Display-copy boundary.** Find the render pass resolving into the exact
   authoritative GXCopyDisp texture. Replay only through that pass, omitting
   later clears/draws. Reproduce its copy rectangle, format conversion and
   scaling into a separate XFB texture using the same copy machinery.
3. **Side-effect isolation.** Replayed GX shaders bind a private bounding-box
   storage buffer. Additional renders cannot modify the PE bounds read by the
   game. Replay never executes authoritative encoder tasks or EFB-copy side
   effects. The accepted bbox-priority fix stays enabled.
4. **Conservative dependency checks.** Fall back to the current native XFB for
   packets containing encoder tasks, explicit texture copies, non-EFB draws,
   palette conversions, other EFB-copy destinations, uploads after an earlier
   draw pass, multiple display copies, or missing display boundaries. Existing
   layout/initial-load/custom-draw rejection remains active. Validate GX
   uniform ranges before starting replay passes. Unsupported effects may run
   at baseline cadence rather than displaying an incorrect replay.
5. **Bounded timed output.** Encode before packet/staging lifetime ends, submit,
   then publish only owned output textures. The render worker chooses due
   images from a bounded queue (at most eight queued images plus the selected
   image). Source phases follow the existing rational display schedule. The
   publication timeline advances in 60-Hz intervals; gaps above 50 ms reset it.
   Resize, unsupported packets, and shutdown clear stale interpolation state.
   No Dawn calls run on the cadence thread.
6. **Identity control and evidence.** Third launcher argument `identity` uses
   current authoritative uniforms through the same replay/copy/presentation
   path. `M14 visible interpolation` reports accepted/fallback frames and
   reasons. `M14 interpolated image` reports selected/fractional poses. The
   report tool summarizes these counters without claiming visual correctness.

## Player validation, in order

1. Run `M14-LLVM-INTERPOLATED-RUN.cmd` and move the camera through the same
   normal gameplay room. Check the actual visual motion and completed source
   FPS. Send the launch log, including any corruption or performance changes.
   An accepted count of zero is a failed visible-path test, even if the native
   fallback looks normal. Do not mistake host presentation count for unique
   interpolated poses.
2. If there is corruption, run
   `M14-LLVM-INTERPOLATED-RUN.cmd AUTO 100 identity` in the same scene.
   Corruption in identity mode points at replay/copy correctness; corruption
   only with intermediate poses points at matching or discontinuity handling.
   Compare the current-pose replay to the accepted native baseline before
   expanding scene coverage.
3. Test 2D/3D flips, room changes, HUD, effects, pause, resize and exit. Unsupported
   packets should visibly fall back without corrupting the EFB or bbox results.
4. Use `M14-LLVM-ASYNC-PRESENTER-RUN.cmd` as the baseline control. No cache wipe.
   Keep interpolation opt-in until source throughput and images pass.

## Remaining work after the first capture

- If CPU preparation still misses the budget, use its new stage timings to
  target matching/allocation rather than guessing. Do not weaken identity or
  shared-uniform safety to raise coverage.
- If source FPS drops only with visible replay, measure CPU encode and GPU cost
  separately. The initial path can render roughly 60 authoritative scenes plus
  100 replay scenes per second. Reusing exact endpoint images and pooling
  output textures are follow-ups once correctness is demonstrated.
- Conservative resource checks may reject many frames. The next step is exact
  dependency capture or replay interleaved with authoritative operations, not
  deleting rejection checks to make counters look better.
- Translation-only coverage deliberately snaps projection/basis changes and
  indexed-matrix draws. Improve identity/coverage only from visual evidence.
- The output schedule is a bounded initial implementation. Dynamic display
  refresh changes, long stalls and deadline recovery need player validation;
  it is not a finished general-purpose variable-refresh scheduler.

## Cloud checks

Generated preparation behavior runs with ASan/UBSan, including shared ranges,
active PN bounds, untouched authoritative bytes, and CPU-mirror selection.
Emitted presentation selection/resource preflight has ASan/UBSan tests for due
images, repeats, queue bounds, fallback reset, display-boundary stopping and
resource rejection. Existing dev138–140 source/migration/report contracts pass.
The complete adapter chain is checked for repeat-configuration source stability.
Affected Aurora C++ translation units are syntax-compiled against pinned headers.
These do not replace MSVC linking, a real GPU run, or player visual validation.
