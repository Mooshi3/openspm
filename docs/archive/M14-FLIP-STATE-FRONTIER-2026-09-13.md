# M14 flip/state frontier — 2026-09-13

## Runtime under review

Player log: `launch(20260913-042653).log`
Repo revision in log: `582020048cb8`
Backend: `openspm.native 14.0-dev26`
Renderer: `openspm.aurora-gx 14.0-dev6`
Local module: hosted display-list bridge v17

Player-visible result:

- pre-existing texture/material corruption remained: random black objects, wrong colours/textures, missing outlines;
- 2D looked marginally better at first, but later gameplay converged to the old broken state;
- flipping could turn the scene blue/striped;
- a spinning star-like effect appeared from Mario during flip that the player had not seen before;
- heavy scenes still lag.

## Copy-ownership experiment: rejected, but it exposed the real copy-size bug

The temporary `CopyTextureRef` width/height/format ownership guard fired immediately around the first proven Wii-A/flip transition. The decisive family was:

- copy recorded by the raw renderer: `608x480/fmt=4`;
- sampled GX texture at the same mapped destination: `304x240/fmt=4`;
- the same exact 2:1 relationship repeated hundreds of times.

The ownership heuristic treated this as stale guest-address reuse and fell back to RAM. Runtime disproved that interpretation: the new blue/striped flip corruption appeared with the guard, and the 2:1 relationship is source-backed GX behavior rather than random allocator reuse.

The guard remains retired through `apply_copy_texture_ownership_rollback_patch.py`. The historical `apply_copy_texture_ownership_patch.py` entry point is retained so existing player caches are healed without deleting `.openspm`.

### Source-backed root cause: BP copy-control bit 9

Matching Wii GX behavior resolves the 608x480 -> 304x240 mystery:

- `GXSetTexCopyDst(..., mipmap)` writes the texture-copy control's mip-map filter bit;
- hardware documentation/behavior describes this bit as false = 1:1 copy, true = 2:1 box-filtered copy;
- the SDK also writes BP `0x4D` with destination tile stride;
- OpenSPM's raw BP `0x52` adapter ignored the half-scale bit and always assigned Aurora's logical copy destination the full EFB source width/height.

So for a 608x480 flip feedback source with half-scale enabled, OpenSPM created a 608x480 host copy even though the game correctly initialized/sampled a 304x240 GX texture. This is a direct renderer-semantic mismatch and a much stronger explanation for blue/striped flip feedback than the abandoned ownership heuristic.

## Current candidate: raw GXCopyTex half-scale semantics

`renderer/aurora/apply_raw_copy_half_scale_patch.py` now changes only original-console texture-copy sizing:

- bit 9 clear: preserve the existing 1:1 source -> destination extent;
- bit 9 set: destination width/height become the 2:1 box-filtered extent, rounding up for odd dimensions;
- display-copy/XFB sizing is untouched;
- ordinary SU/texgen coordinates are untouched;
- indirect texturing is untouched;
- FIFO scheduling is untouched;
- copy ownership remains on the pre-experiment policy.

The existing copy-migration entry point now runs the ownership rollback first and this half-scale repair second, so a normal `M14-UPDATE-RUN.cmd` migrates an already-patched player cache automatically.

Expected one-shot runtime marker when SPM first uses the path:

`M14 raw GX half-scale texture copy active src=608x480 dst=304x240 fmt=4 stride_tiles=...`

The BP `0x4D` stride is logged as evidence, not used to guess an arbitrary exact logical width; tile stride can include backing-allocation padding. The half-scale control bit directly defines the produced 2:1 pixel extent.

## Input mapping correction

Do not correlate `buttons=0x00000008` with flip. In the Wii Remote mask used by this build:

- A = `0x00000800`;
- `0x00000008` = D-pad Up.

An earlier timing correlation between the provisional `GX_LO_OR` warning and `0x8` was false. `GX_LO_OR` is still approximated by component-wise Max and remains correctness debt, but the 5820200 log does not prove it causes flip corruption.

## Higher-confidence systemic candidate after copy sizing: hosted display-list GXData leakage

The runtime still hosts the whole `GXBeginDisplayList` / `GXEndDisplayList` pair as a neutral byte recorder. This preserves list bytes but bypasses Nintendo's CPU-side `GXData` save/restore behavior.

Matching RVL SDK source shows Begin dirty-flushes CPU GX state before recording and normally saves the whole GXData shadow; End flushes the list, restores the old CPU FIFO, and restores the saved GXData context while preserving `cpEnable`. `GXInit` enables display-list context saving by default.

That remains a credible explanation for the older inherited-material/black-object/missing-outline problems. The failed bridge-v18 experiment only proved that executing the whole native SDK Begin path against OpenSPM's incomplete physical FIFO/MMIO model can wedge; it did not disprove the CPU shadow/save/restore semantics.

`tools/native_bridge/inspect_display_list_sdk_calls.py` remains wired into player-local configure. Its BL call graph should be used to split CPU-context fidelity from unsafe physical FIFO operations instead of retrying v18 wholesale.

## Other retained frontiers

- **SU scaling:** still demonstrated wrong for at least one captured draw. The first broad candidate changed both ordinary and indirect paths and regressed globally. If revisited, change only the proven ordinary sampling path first.
- **Lines/outlines:** gameplay emits very large numbers of `GX_LINESTRIP` draws. Aurora's line shader is active, so missing outlines are not explained by lines simply being dropped. Depth/material/line-state fidelity remains a named target.
- **GX_LO_OR:** still an inexact component-wise-Max fallback. Do not change it from timing coincidence alone.
- **Performance:** forced Aurora `ProcessingMode::Drain` remains a strong explanation for bursty DrawDone stalls, but graphics correctness should stay isolated from a threading/scheduling experiment.
- **Doors:** rainbow and ordinary door transitions occur but the visible swing animation is absent; keep this as a separate transform/model-state target.
- **Audio:** still a separate missing end-to-end subsystem.

## Next player action

Run `M14-UPDATE-RUN.cmd` normally. Do not delete `.openspm` or saves.

For the next run, verify in this order:

1. configure/build succeeds after the cache migration;
2. `stale copy-texture ownership rejected` no longer appears;
3. the one-shot `M14 raw GX half-scale texture copy active ...` marker appears when flip feedback starts;
4. whether blue/striped flip feedback and the unexpected spinning-star presentation change;
5. whether older black/wrong-material/missing-outline bugs remain separately.

If the half-scale marker reports the expected 608x480 -> 304x240 relationship and flip presentation improves, close raw texture-copy sizing as a proven repair and return to the older systemic material-state bug rather than broadening the copy heuristic again.
