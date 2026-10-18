# M14 Chapter 1-2 renderer frontier — 2026-09-13

## Authoritative player run

Player run `launch(20260913-040646).log` at repo revision `b39deea8cccb` is the latest successful runtime proof before the copy-texture ownership candidate below.

Runtime identity:

- backend `openspm.native 14.0-dev26`
- renderer `openspm.aurora-gx 14.0-dev6`
- local-module bridge restored to runtime-proven v17 after the failed v18 native GX display-list experiment
- no emulator fallback
- command-aware bulk GX FIFO framing active
- hosted `GXBeginDisplayList` / `GXEndDisplayList` restored and producing thousands of display lists

Player reached Chapter 1-2. New/confirmed observations:

- objects can flash black;
- objects can transiently inherit another object's texture or colour;
- large enemy-filled areas become visibly laggy;
- the rainbow level doors perform the transition but do not visibly swing open;
- the same general missing-door-animation symptom was observed earlier at Bestovius' house.

These are being treated as three separate frontiers: resource/material ownership, heavy-scene renderer cost, and door/map-object animation visibility.

## Material corruption: copy-texture ownership is now a concrete candidate

Pinned Aurora stores host-side EFB texture copies in `g_gxState.copyTextures`, keyed by the destination host pointer. OpenSPM intentionally maps both original-Wii BP copy destinations and Image3 texture addresses through the same bounded guest-memory mapper, so the host pointer is also the identity of a guest RAM address.

Before the current candidate, `GXState::CopyTextureRef` retained only:

- the host GPU texture handle;
- a revision counter.

`resolve_sampled_textures()` looked up `copyTextures[obj.data]` and, if present, selected that GPU copy **before** considering the ordinary guest-RAM texture bytes. The copy lookup did not verify width, height, or texture format.

That means guest allocator reuse can produce the exact visible failure now reported: if address A once held an EFB copy and SPM later reuses address A for another texture, the old GPU image can shadow the new RAM texture and make the later object inherit unrelated texture/colour content.

This is stronger evidence than the still-open SU scaling defect for the specific cross-object contamination symptom.

### Candidate repair

`renderer/aurora/apply_copy_texture_ownership_patch.py` is now wired into the cached Aurora patch chain.

It preserves logical Wii copy ownership metadata in `CopyTextureRef`:

- logical copy width;
- logical copy height;
- logical GX copy format.

At sampled-texture resolution, an old virtual EFB copy is accepted only if those three fields match the current GXTexObj. Otherwise OpenSPM rejects the stale virtual owner and falls back to the actual guest-RAM texture path.

The repair deliberately does **not** clear all copy textures on `GXInvalidateTexAll`. OpenSPM does not currently materialize EFB-copy pixels back into guest RAM, so global eviction would destroy legitimate feedback textures whose authoritative contents exist only in the GPU copy.

Rate-limited runtime marker:

`OpenSPM M14 stale copy-texture ownership rejected ...`

Acceptance evidence for the next run:

1. configure/build succeeds against the existing cached Aurora checkout;
2. normal EFB-copy effects remain intact;
3. one or more rejection markers occur in affected gameplay;
4. black flashing / cross-object texture-colour inheritance materially decreases.

If same-width/height/format allocator reuse still produces corruption, the next layer should add a bounded RAM-content generation/fingerprint to copy ownership rather than broad cache invalidation.

## Performance frontier from Chapter 1-2

The authoritative Wii timebase remains healthy; this is not an intentional 30 Hz simulation cap.

The latest log shows bursty renderer cost:

- `draw_done_drain` reaches about 58.8 ms, 80.6 ms and a maximum around **207.7 ms**;
- later `fifo_submit` calls reach about **85.0 ms**, with several additional 16–55 ms samples;
- retrace delivery can fall into the low 40s during these bursts while otherwise remaining near 57–58 per second.

The old >7 MiB raw guest-array staging high-water probe did **not** trigger in this 1-2 run, so that older Flipside bottleneck is not sufficient to explain the current enemy-area slowdown.

Tippi/bbox synchronization is still large in aggregate: the run reached roughly 32,765 bbox component reads / 8,192 actual GPU maps, but sampled individual map waits are only about 0.08–0.12 ms. Bbox is a synchronization multiplier, not a complete explanation for a 208 ms DrawDone drain.

### Important architectural clue: synchronous FIFO Drain mode

OpenSPM still forcibly changes pinned Aurora from `ProcessingMode::Thread` to `ProcessingMode::Drain` in root CMake. In Drain mode, Aurora's `publish()` intentionally does no command processing during the frame; `fifo::drain()` processes the complete buffered target synchronously.

This strongly explains why heavy scenes appear as large `GXDrawDone` spikes: the work is accumulated and paid at the synchronization boundary instead of being processed incrementally/asynchronously.

Do **not** simply restore Thread mode yet. An earlier dev4 runtime wedged forever in `fifo::drain()` at first DrawDone. OpenSPM has since added a raw-FIFO no-recursion encoder-task path, but the previous thread-mode failure should be understood before concurrency is re-enabled.

Highest-value performance next steps:

1. profile `process_to()` / `process()` internals under the current safe Drain mode to split pipeline configuration, texture binding, uniform work, guest-array staging, draw recording, and any host/GPU wait;
2. determine whether the old Thread wedge was caused by the now-fixed encoder-task reentrancy or another FIFO concurrency invariant;
3. if safe, trial incremental/worker processing behind a narrow reversible candidate rather than making it the new baseline blindly.

Do not reintroduce the old blocking-pipeline experiment; it caused 0.44–0.52 second stalls without fixing materials.

## Rainbow-door animation frontier

Seeky's symbol/header data includes US2 mappings, so it can be used as a semantic oracle without changing the runtime revision. Relevant US2 door symbols include:

- `evtDoorGetActiveDoorDesc` at `0x800E1304`;
- `evt_door_wait_flag` at `0x800E1334`;
- `evt_door_set_door_descs` at `0x800E2764`;
- `evt_door_set_dokan_descs` at `0x800E36DC`.

`DoorDesc` contains separate open/close case names plus map/hit group names and move/init scripts. The fact that transitions succeed while both ordinary and rainbow doors fail to visibly swing suggests the event/transition logic is alive and the missing visual may sit in map-object/group transform or animation state propagation.

Next door investigation should distinguish two cases with a named, repeatable door transition:

1. guest door/map-object transform or animation state never changes;
2. guest state changes correctly but corresponding XF/matrix/draw state is stale or dropped in Aurora.

Do not fold this into the copy-texture fix without evidence; it is deliberately tracked as a separate visual-animation frontier.

## Retained repairs / non-regressions

Keep all of the following while investigating the new candidate:

- command-aware bulk FIFO framing;
- nested CALL_DL remainder preservation;
- hosted GX display-list recorder (v18 native Begin/End remains retired);
- DrawDone/presentation separation;
- display-copy XFB snapshot semantics;
- bbox readback/presentation separation;
- legal zero/small primitive handling;
- vertex-cache visibility/merge barrier;
- NBT3 guest-array bound repair;
- TLUT routing/size repairs and BP 0x66 texture invalidation;
- component-aware TEV liveness;
- raw 1024 dimensions;
- EFB copy-format and MEM2 identity repairs.
