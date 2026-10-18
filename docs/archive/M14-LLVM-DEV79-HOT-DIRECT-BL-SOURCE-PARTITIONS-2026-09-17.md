# M14 LLVM dev79 — measured hot direct-BL source partitions

Date: 2026-09-17  
Branch: `m14-native-execution`

## Player result that triggered dev79

Player run:

`launch(20260918-005733).log`

Tested repo revision:

`b25484ad1818`

User result:

- wedding passed repeatedly while AFK;
- later gameplay remained visibly laggy and Mario movement was very jittery;
- the spinning-device issue is not persistent invisibility: the game intentionally
  pauses/freezes Mario's sprite while the device spins him, and the renderer can
  make that frozen sprite appear absent when the sprite was already offscreen at
  transition entry. It did not reproduce in this run.

The old wedding frontier remains fixed:

```text
hang_80065128_max=0
```

So dev77 is still the correctness fix, and dev78's removal of the dev72/dev76
state-spill experiments is safe. dev78 did not materially cure heavy-scene
performance.

## Performance evidence

Late heavy gameplay still contains long runs near:

```text
spmarioMain=30-33
retraces=60-62
```

with transitions through intermediate 40-50 Hz and occasional 60 Hz windows.
That quantization matches the subjective jitter: active simulation sometimes
finishes before the next VI deadline and sometimes misses it, falling to the
following retrace.

Renderer producer work rises in the heavier segment but remains a minority of
the missing frame:

```text
DrawDone avg ~= 4.0-4.5 ms/frame
p95         ~= 4.75-5.25 ms
```

Host `Present()` remains cheap (~0.208 ms average at shutdown).

## dev78 rescue sampler result

The dev78 1/4096 direct-BL sampler finally classified the long-standing dev32
safety-net traffic.

Total direct-BL rescues:

```text
71,814,647
```

Dominant retained pairs:

```text
0x80D1F414 -> 0x802543F4  sample count 6687
0x80086848 -> 0x8012191C  sample count 1704
```

The first pair occurred in a long run that spent substantial time looping the
wedding while AFK, so its aggregate weight is not assumed to represent gameplay
alone. It is still an objectively hot missing native entry. The second source is
in main-DOL code and is also independently hot.

The sampler is Misra-Gries over a 1/4096 stream, so retained counts are not exact
global event counts. They are sufficient to identify the dominant source PCs;
do not treat count*4096 as a precise performance measurement.

## Why the source PC matters

The old dev32 bridge documents the exact failure class:

a public redispatch can land on a PC which is simultaneously the previous
call's continuation and the next direct BL instruction. The dispatcher reaches
OpenSPM's host-call path before that BL executes. The dev32 fallback then
manually applies only the architectural BL effect:

```text
LR = source + 4
PC = decoded target
```

and sends the target back through ordinary native dispatch.

Existing dev61/dev62 partition work mostly promoted selected **PC+4 return
continuations**. That does not repair an event which arrives at the BL source
itself.

## dev79 implementation

`tools/native_bridge/augment_llvm_bl_continuations.py`

now contains exactly two measured hot source entries:

```text
0x80D1F414 -> 0x802543F4
0x80086848 -> 0x8012191C
```

Generation uses the private compiler-only DOL which contains the clean main DOL
plus the player's relocated relF executable text. Before adding either partition,
the script proves:

1. source PC is executable;
2. expected target is executable;
3. source instruction is a direct PowerPC B-form instruction with LK=1;
4. decoding that exact instruction produces the sampled target.

Any mismatch aborts generation. No instruction bytes are committed.

Only the **source PCs** are added to the existing private partition map:

```text
openspm_dev79_hot_bl_source_80d1f414
openspm_dev79_hot_bl_source_80086848
```

DolRecomp therefore enters an ordinary analysed native region at the BL itself
and executes the real instruction/callee normally. No call is skipped, replaced,
or HLE'd.

The policy remains deliberately measured-only. Do not blanket-partition every BL
source/continuation: that can fragment LLVM regions and destroy locality.

## Generation / provenance

`generate_local_llvm.py` now reports:

```text
[OpenSPM] LLVM dev79 direct-BL native-entry repair:
... hot=0x80D1F414->0x802543F4,0x80086848->0x8012191C
```

Provenance now records:

```text
direct_bl_partition_policy=measured-hot-source-plus-bounded-continuation
dev79_hot_bl_source_0=0x80D1F414
dev79_hot_bl_target_0=0x802543F4
dev79_hot_bl_source_1=0x80086848
dev79_hot_bl_target_1=0x8012191C
```

No global compiler-source patch changed, so the dev78 codegen identity remains
`llvm-inline-wpar-indirect-budget-v4-dev78`. The partition map changes the LLVM
function/range geometry and therefore invalidates the affected stamped objects;
no manual cache wipe is required.

The source-only native-pipeline contract now asserts the dev79 measured-source
policy and both exact source/target pairs.

## Remaining larger clue

The same long run recorded roughly:

```text
ordinary host callbacks = 8.30 billion
direct-BL rescues       = 71.8 million
post-MTMSR tails        = 10.27 million
```

dev66 previously proved that removing almost all *router lookup work* behind the
ordinary callbacks did not fix performance, but that experiment happened before
dev68 removed the scheduler-idle ceiling. It also did not remove the
LLVM->C host callback itself.

Therefore, if dev79 does not materially raise heavy-scene cadence, the next
highest-value performance question is the public dispatch boundary itself:
measure or structurally bypass host-callback entry for addresses proven to be
ordinary legal native entries, without letting semantic GX/platform/host targets
run native first.

Do not blindly reorder host-before-native dispatch. Arbitrary aligned addresses
inside an LLVM range are not necessarily legal entry leaders; dev77's wedding
bug proved that.

## Spinner rendering note

Do not mutate Mario visibility state.

The current report is consistent with the game's intended spinner transition
freezing/holding the current sprite while the device animates the actor. If the
sprite is outside the rendered viewport at the instant that representation is
frozen, the current renderer may have no visible frozen sprite to carry through
the transition. It is temporary and did not reproduce in this run.

Treat this later as a renderer transition/capture/offscreen-sprite semantics
issue, not actor deletion or a permanent cull flag.

## Rescue-rate correlation added before the A/B

The runtime now also reports the delta of the already-existing direct-BL rescue
counter about once per host second:

```text
M14 LLVM dev79 rescue cadence host_ms=... bl_rescues=... rate_per_s=... pc=...
```

This measurement is outside the public host-call hot path and changes no guest
state. Compare each line with nearby `M14 simulation cadence` output. It lets a
short gameplay run tell whether 30 Hz windows actually coincide with rescue
pressure, rather than relying on a whole-session aggregate polluted by AFK
wedding loops.

## Next player test

Run:

```text
M14-LLVM-UPDATE-RUN.cmd
```

No cache wipe.

Expected generation marker:

```text
LLVM dev79 direct-BL native-entry repair
hot=0x80D1F414->0x802543F4,0x80086848->0x8012191C
```

For the most useful A/B, do not leave the wedding looping for most of the run.
Reach the same laggy gameplay area and play there for a minute or two.

Success signals:

1. wedding remains fixed;
2. total direct-BL rescue rate falls sharply;
3. the two dev79 source PCs disappear from the teardown heavy sample;
4. heavy `spmarioMain` cadence moves materially above the old ~30-33/60-62
   pattern and Mario motion becomes less jittery.

If FPS is unchanged, use the new rescue sample to pick the next exact boundary,
but prioritize a host CPU/profile or safe public-dispatch fast path over further
table-lookup micro-optimization.

## Preserve

No emulator/general PPC interpreter fallback. Player supplies Nintendo code and
assets. Preserve dev77 wedding restart partition, dev78 retirement of broad
state spills, dev68 runnable-idle wake, dev69 immutable filters, dev63 inline
positive admission cache, chunk128/indexed generation, v19 GX CPU-shadow and
display-list semantics, deterministic relF/BSS placement, exact GX finish
completion, input edge retention and fixed-phase 60 Hz VI.
