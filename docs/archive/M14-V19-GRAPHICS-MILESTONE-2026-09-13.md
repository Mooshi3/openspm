# M14 v19 graphics milestone — 2026-09-13

## Milestone

Player runtime at repo revision **`a928d97de420c23600c5cdc72013c159670cca7f`** is the first M14 build reported to render gameplay with the long-standing material/texture/outline corruption gone.

Preserved exact branch:

`milestone/m14-v19-graphics-fixed-2026-09-13`

Do not rewrite or delete that milestone branch while later M14 work is in flight.

## What changed

The decisive change from the previous visually-broken runtime was promotion of **continuation-safe display-list bridge v19**:

- R8PE01 `GXBeginDisplayList` / `GXEndDisplayList` are no longer whole-function hosted exits;
- Nintendo's recompiled CPU-side GX SDK code now executes, including the real `GXData` dirty/save/restore behavior;
- OpenSPM's neutral bounded recorder still owns the physical display-list payload/count;
- recorder handoff happens at durable guest return labels, so it survives DolRecomp yield/resume;
- End substitutes the recorder's authoritative list size before the game examines `r3`.

This is strong runtime evidence that the old graphics corruption was caused primarily by **display-list CPU shadow/context leakage**, not generic texture decoding.

## Player-observed fixes

The player explicitly reports all of the following fixed on the v19 build:

- no random black objects;
- no objects inheriting another object's texture or colour;
- no texture flicker;
- previously missing object outlines are present;
- the opening Dark Prognosticus sequence has its drawn outline;
- flip no longer causes the prior global blue/striped corruption;
- general scene rendering now looks as intended.

The screenshot supplied with the runtime report is consistent with the new state: normal Paper Mario scene materials/line work are present rather than the prior cross-object texture corruption.

## Remaining visual/gameplay defects

Do **not** reopen broad texture/material debugging unless a new reproducible regression appears. The remaining defects are narrower:

1. **Doors do not visibly animate open/closed.** The transition itself works. Useful US2 anchors remain:
   - `evt_map_playanim = 0x800EDD84`
   - `evt_map_checkanim = 0x800EDE2C`
   - `evt_mapobj_trans = 0x800ED814`
   - `animPoseSetAnim = 0x80043298`
   Trace guest animation state versus emitted pose/matrix updates before changing renderer semantics.

2. **Chapter 1-2 rotating transport bars corrupt/distort Mario's sprite while he is being carried.** Treat this as a named transform/attachment regression, not evidence that the old global material bug returned. Capture the transport interval and compare Mario's model/pose/texture-matrix state before, during, and after attachment.

3. **Heavy enemy-filled areas still lag.** The pre-v19 profile already identified a concrete CPU cost: approximately 15,478,488 bytes of raw guest vertex-array staging per Aurora frame across 335 distinct misses, costing roughly 9-16 ms CPU before DrawDone. This is now the highest-confidence performance target.

4. Native game audio remains silent.

5. PC input mapping is still temporary/incomplete and needs a user-facing remappable mapping layer.

## Performance next step

Now that v19 fixes the systemic graphics state leak, it is safe to isolate the compact raw-array staging optimization.

Current shader address math is:

`arrayStart + guestIndex * stride + within`

For a draw whose referenced guest indices span `[minIndex,maxIndex]`, upload only the referenced byte interval rather than the entire prefix from index zero. If the uploaded interval starts at `minByte`, use:

`arrayStart = uploadedRange.offset - minByte`

The shader then resolves exactly the same final byte address for every referenced element while avoiding unused prefixes. Keep exact content/visibility semantics and fail closed on arithmetic/range overflow.

Do this as an isolated performance candidate before changing Aurora FIFO processing mode.

## Interpretation of earlier experiments

Keep the raw GX half-scale texture-copy correction; it is a real Wii semantic but was visually negative as the root cause. Keep the command-aware FIFO, nested CALL_DL continuation, TLUT/EFB/copy-format, component-aware TEV, DrawDone/presentation, bbox and other retained repairs. They remain useful supporting correctness work.

The main lesson from this milestone is stronger: **the renderer could decode most individual GX state correctly and still look globally broken because hosted display-list boundaries corrupted Nintendo's CPU-side GX shadow state. v19 restoring that context made the scene coherent.**
