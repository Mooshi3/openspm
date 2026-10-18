# M14 dev138 — geometry identity probe after dev137 rejection

> **Active migration architecture:** dev138 now applies directly after dev136.
> The rejected dev137 visual patch is not executed by CMake. dev138 canonicalizes
> the entire interpolation helper region and uses narrow recording-layer geometry
> hash APIs; it does not access Aurora FramePacket internals from GX.

Date: 2026-09-21
Branch: `m14-native-execution`

## Runtime update — first successful dev138 run

The first successful dev138 runtime (`launch(20260921-235510).log`) showed two
probe-induced problems that are now fixed.

First, dev136 was re-applying after dev138 replaced its signature block, causing
about five interpolation frame-boundary calls per actual present. That both
inflated the identity frame counter and destroyed previous-frame history via
repeated empty swaps.

Second, full-rate geometry hashing/matching materially reduced performance in
heavy scenes. The probe is now sampled: two adjacent capture frames every 300
display copies by default. Ordinary frames perform no identity geometry hashing
or matching. The interval is controlled by
`OPENSPM_M14_INTERP_PROBE_INTERVAL`.

This sampling is diagnostic only. Production retained-scene interpolation will
need a separate low-overhead/off-critical-path identity/replay design.


## dev137 runtime verdict: rejected

dev137 finally built and ran at revision `9a009718036d`. The player reported:

- presentation collapsing to roughly slideshow speed;
- camera unable to keep up with Mario;
- intermittent black texture flicker;
- one-frame cracks/separation in house geometry;
- doors detaching from buildings for several frames.

The runtime log confirms this is not subjective pacing.

In representative heavy gameplay windows:

- `spmarioDisp` remained roughly 5–7 ms;
- `spmarioMain` remained roughly 5–6 ms;
- `DEMODoneRender` grew to roughly 55–105+ ms;
- completed main-loop frames fell to about 8–15/s in several windows;
- late in the run a 1-second window completed only 10 frames with
  `done_avg_us=88037`;
- shutdown present pacing had p50 ~66 ms, p95 ~92.5 ms and p99 ~102.5 ms.

The cause is architectural: dev137 temporarily mutated GX transforms and called
`build_uniform(cache.shaderInfo)` a second time for nearly every matched draw.
Heavy scenes contain roughly 3,000 logical GX draws, so this duplicated the
expensive full-uniform staging path thousands of times on the authoritative
60 Hz producer.

That design is retired. Do not optimize or threshold-tune it.

## Correspondence verdict: dev136/dev137 "safe" was not safe

The visible house cracks and detached doors occurred while dev137 telemetry still
reported nearly all draws as matched and interpolation-safe. Examples in the
same run include ~3,000-draw samples with 99–100% match and 100% "safe".

Therefore the old identity:

- pipeline;
- texture bind group;
- primitive;
- vertex format/count;
- projection type;
- indexed/non-indexed state;
- nearest previous transform;

is insufficient to prove object correspondence. Multiple submeshes/instances can
share those values. A distance threshold cannot repair an identity collision.

## dev138 goal

dev138 returns to authoritative 60 Hz output and makes **no visual change**.

It strengthens correspondence before any retained-scene replay is enabled:

1. fingerprint the actual final staged vertex and index bytes for each logical
   GX draw;
2. include material/pipeline/texture state in the identity;
3. refresh the fingerprint when Aurora merges adjacent GX draws;
4. match at the completed `GXCopyDisp` frame boundary rather than doing
   cross-frame lookup inside every live draw;
5. reject repeated identities whose nearest full transform is ambiguous;
6. measure a conservative material-only fallback only when that material is
   unique in both adjacent frames.

No second uniform is built. No `DrawData` or pipeline ABI changes are made.

## Geometry hashing cost

Merged geometry is hashed incrementally. Each newly appended vertex/index range
extends separate rolling vertex/index fingerprints. dev138 does **not** re-hash
the entire accumulated draw after every merge.

This avoids turning long merge chains into quadratic CPU work.

## dev138 telemetry

Expected periodic line:

```
M14 dev138 identity frame=...
  candidates=...
  previous=...
  accepted=...
  accepted_pct=...
  exact=...
  exact_pct=...
  exact_unique=...
  duplicate_resolved=...
  ambiguous=...
  fallback_unique=...
  unmatched=...
  duplicate_groups=...
  moving=...
  indexed=...
  hash_failures=...
  distance_avg=...
  distance_max=...
```

Important fields:

- `exact`: geometry+material identity;
- `duplicate_resolved`: repeated exact identities that had one clearly nearest
  transform;
- `ambiguous`: repeated identities deliberately rejected;
- `fallback_unique`: geometry-changing draw accepted only because its material
  group was unique in both frames;
- `unmatched`: draw that must snap/current-frame fallback in a future replay.

The target is **not** 100% acceptance. A lower trustworthy rate is better than
dev137's false 100%.

## Safety / retirement

`m14_interp_visual_enabled()` is hard-disabled by dev138. Even a stale private
developer environment requesting `midpoint` cannot reactivate the rejected
live substitution.

`M14-LLVM-INTERP-MIDPOINT-RUN.cmd` is retired and redirects to the safe dev138
probe.

Use:

```bat
M14-LLVM-INTERP-IDENTITY-RUN.cmd
```

This should look like the ordinary 60 Hz FIFO/vsync baseline. Judge whether
performance has returned to normal and whether the dev137 geometry/texture
artifacts are gone. Exercise the same rooms, flips, houses and doors so the
identity telemetry covers the previously failing cases.

## After dev138

If geometry identity is trustworthy and the producer returns to normal speed,
the next implementation is retained-scene interpolation:

- build the authoritative uniform block once;
- preserve immutable/replayable draw data beyond the guest tick;
- create interpolation-slot uniform data from already-staged uniform bytes,
  patching only transform regions rather than rebuilding the full uniform;
- do cross-frame matching/finalization after the scene is sealed;
- keep interpolation/replay work off the 60 Hz producer critical path where
  practical;
- drop an inserted presentation slot if the producer is late rather than
  slowing simulation;
- schedule arbitrary host cadence (`target_fps=0` follows the display, currently
  ~99.94 Hz) instead of assuming only integer 120/180/240 multiples;
- unmatched/ambiguous/discontinuous draws use authoritative current state.

The future HOME/OpenSPM menu continues to own the same persistent interpolation
settings backend; no UI work is required for dev138.
