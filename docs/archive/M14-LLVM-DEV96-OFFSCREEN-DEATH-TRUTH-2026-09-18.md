# M14 LLVM dev96 — offscreen/death truth probe (2026-09-18)

## Why this exists

Player run `launch(20260918-224450).log` at repo revision
`2d36d0c35d47` tested the active dev94 CPU-snapshot rollback plus exact
duplicate CP array-base pipeline reuse and dev95 sequence watch.

The player reported:

- the first area felt smoother;
- later areas were still as laggy as before;
- after falling into the pit, HP reached 0 but Mario never returned and no Game
  Over appeared;
- a previous one-off invisible-Mario bug also happened when Mario was offscreen.

Do **not** treat that correlation as proof that generic GPU frustum/backface
culling is wrong. SPM has its own offscreen/render-to-texture subsystem and also
reads the Wii PE bounding-box registers. Those GPU-visible results are observable
game state and can legitimately affect game logic.

## What the dev94/dev95 run proved

dev94 is a real renderer win and stays active:

- the runtime marker
  `M14 dev94 CPU-cached exact guest-array snapshots restored` was present;
- DrawDone commonly returned to roughly the pre-dev91 good range instead of the
  dev91 6-8 ms regression;
- representative good/heavy DrawDone windows were around 1.6-2.9 ms;
- the first gameplay area was subjectively smoother.

The remaining late-scene slowdown is no longer primarily DrawDone. In the
heavier rooms, dev81 still shows roughly 10-13+ ms of combined
`spmarioDisp + spmarioMain` CPU work, enough to hover around the 16.667 ms VI
deadline once renderer work is added.

The pit failure stayed logically alive:

- retraces continued near 60 Hz;
- `spmarioMain` continued;
- final committed sequence remained `SEQ_GAME (2), stage 1`;
- dev95 saw only one host-routed `seqSetSeq` call in the entire run, a map
  transition request, and saw zero host-routed Game Over requests.

That does **not** prove `seqSetSeq(SEQ_GAMEOVER)` never ran. LLVM direct
native-to-native calls can bypass the public host callback entirely, so dev95's
call hook is incomplete.

## dev96 change

dev96 keeps all game/render values unchanged and adds two observation paths.

### 1. Native sequence truth via US2 globals

Seeky's EU0 data map gives:

- `seqWork = 0x80512360`
- `now_seq = 0x805AE0B8`
- `next_seq = 0x805AE0BC`
- `prev_seq = 0x805AE0C0`

The already-proven US2 `seqWork` address is `0x804D0D60`, a constant
`-0x41600` relocation. Applying that same relocation gives:

- `now_seq  = 0x8056CAB8`
- `next_seq = 0x8056CABC`
- `prev_seq = 0x8056CAC0`

That location is also independently plausible from the US2 symbol map because
neighboring sequence globals/pointers sit in the same `0x8056CAxx` block.

At every exact native `spmarioMain` phase entry, dev96 samples
`seqWork/now_seq/next_seq/prev_seq`. Therefore a direct native
`seqSetSeq(4,...)` is observable as `next_seq=4` even if it never crosses
`CPUState::host_call`.

The same relocation also maps two nearby EU0 BSS objects:

- EU0 `marioWork = 0x80510458` -> US2 `0x804CEE58`
- EU0 `MarioPouchWork = 0x80511A28` -> US2 `0x804D0428`

`MarioPouchWork.hp` is offset `0x0C`. dev96 therefore performs one HP
read/compare at the existing once-per-frame phase marker. Only when HP changes
does it read/log the larger Mario snapshot:

- Mario general flags at `+0x0000`;
- display flags at `+0x0008`;
- motion id at `+0x002C`;
- XYZ position at `+0x005C`.

This directly records the HP->0 transition and whether Mario state/position is
abnormal at the same time, without adding a hot per-draw diagnostic.

Runtime marker:

```text
M14 LLVM dev96 offscreen/death truth probe active:
```

Transition marker:

```text
M14 LLVM dev96 sequence truth where=spmarioMain
    seqWork=... stage=... now_seq=... next_seq=... prev_seq=...
```

HP/player transition marker:

```text
M14 LLVM dev96 player state where=spmarioMain hp=...
    mario_flags=0x........ disp_flags=0x........ motion=...
    pos=(x,y,z)
```

### 2. Empty PE bounding-box caller trace

OpenSPM already services the guest's PE bbox MMIO reads through Aurora:

- left   `0xCC001010`
- right  `0xCC001012`
- top    `0xCC001014`
- bottom `0xCC001016`

The hardware reset/empty sentinel is:

```text
1023,0,1023,0
```

dev96 wraps the **existing** external read callback after creation. It always
calls the underlying reader first and returns that exact value unchanged. It
only observes complete four-register bbox transactions. For empty transactions,
it logs the first 16 and then powers of two, recording:

- guest PC of the first bbox register read;
- LR/caller;
- `seqWork` sequence/stage;
- `now_seq`;
- `next_seq`;
- HP;
- Mario flags/display flags/motion ID;
- Mario XYZ position.

Marker:

```text
M14 LLVM dev96 empty PE bbox n=... total=...
caller_pc=0x........ caller_lr=0x........
seq=... stage=... now_seq=... next_seq=...
bounds=1023,0,1023,0
```

The wrapper is restored on destroy. It does not alter bbox values, renderer
ordering, culling, GX state, guest registers, or sequence state.

## Why this is safer than disabling culling

A broad "turn culling off" experiment would conflate several unrelated systems:

- ordinary GPU rasterizer cull mode;
- camera visibility;
- SPM's `offscreendrv` render-to-texture system;
- PE bounding-box feedback;
- NPC/map offscreen draw callbacks.

That could easily hide the real bug while changing gameplay and rendering.

dev96 instead tells us which guest code is actually reading an empty render
result at the time the pit/death path fails.

## Build contracts

`tools/native_bridge/test_llvm_native_pipeline.py` now requires:

- exact US2 sequence-global addresses;
- exact PE bbox addresses;
- dev96 startup/transition/empty-bbox/stats strings;
- installation and restoration of the external-read wrapper;
- the wrapper forwarding the underlying return value unchanged;
- module version `m14-dev96-llvm-offscreen-death-truth`.

GitHub Actions is still not a source verdict: runs 831/832 failed before any job
steps were created, matching the existing runner/account infrastructure failure.
Use the local updater as the authoritative gate.

## Next player test

Run:

```text
M14-LLVM-UPDATE-RUN.cmd
```

No cache wipe.

For the fastest diagnosis:

1. boot normally and make sure wedding/ordinary gameplay remain correct;
2. reach a known laggy room briefly;
3. fall into the same pit and let HP reach 0;
4. leave the process running for ~5-10 seconds after the stuck state;
5. close it and upload the log.

### How to interpret the result

**A. `next_seq=4` appears.**

The death logic did request Game Over. The fault is after the request: sequence
commit/exit/init or an event/system-level gate. Follow `next_seq=4` to the next
`spmarioMain` samples and instrument `seqMain`/game-over init.

**B. `next_seq` never becomes 4, and empty bbox reads cluster at the pit with
a stable caller PC/LR.**

This is strong evidence that a render-derived/offscreen condition is gating the
death path. Symbolize that caller and fix the PE/offscreen semantic mismatch;
do not globally disable culling.

**C. `next_seq` never becomes 4, and no relevant empty bbox caller appears.**

The offscreen correlation is probably incidental to the death failure. Trace
Mario's HP==0/death motion/event gate directly.

**D. Game Over works under dev96.**

Treat dev96 as observation-only and suspect timing/heisenbug only if reproducible;
do not turn the probe into a permanent gameplay fix.

## Performance frontier remains separate

Do not use dev96 to explain the remaining heavy-scene lag unless the log proves
a hot bbox/offscreen loop. dev94 has already moved DrawDone back down; the next
performance owner is primarily inside the exact dev81 `disp/main` phases.
Once the pit correctness bug is localized, profile those phases directly rather
than changing VI timing, broad native-first dispatch, or renderer culling.
