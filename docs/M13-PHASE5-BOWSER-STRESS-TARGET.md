# M13 Phase 5 Bowser stress target

The canonical OpenSPM performance stress fixture is **not** a generic Bowser room. It is the opening-story Bowser's Castle interior cutscene where Bowser addresses the crowd of minions, a minion reports that the castle gate was left open, Mario and Luigi storm in, Count Bleck appears, and the crowd is ultimately removed by Bleck's void sequence.

## Exact map

Independent public SPM map catalogs identify:

```text
aa2_01 = Bowser's Castle exterior
aa2_02 = Bowser's Castle interior
```

SeekyCt's Ghidra symbol export for the map REL names these `aa2_02` events:

```text
aa2_02_crowd_cheer_msgs_evt
aa2_02_mario_attacked_evt
aa2_02_cutscene_1_evt
aa2_02_cutscene_2_evt
aa2_02_init_evt
```

This strongly anchors the user-observed slow scene to `aa2_02`, with `aa2_02_cutscene_1_evt` / the crowd-cheer path as the first event sequence to inspect for the full-cast benchmark. Do not substitute another Bowser map or a later player-controlled room.

## Benchmark semantics

The useful measurement window is the **full-cast/minion-heavy portion before Bleck removes the crowd**, because that is where the current static-recomp runtime falls to roughly 24–31 FPS while normal Dolphin remains full speed on the same machine.

Historical OpenSPM measurements also showed that performance improves substantially after most actors leave. That before/after contrast is valuable and should be preserved as a profiler phase boundary if practical.

A future `bowser_minions` startup profile should therefore aim to reproduce:

1. correct engine initialization;
2. load `aa2_02` through SPM's normal sequence/map-change machinery;
3. enter the opening cutscene at the full-crowd state, rather than merely warping to an inert copy of the room;
4. begin the deterministic profiling capture before/at the crowd-heavy dialogue;
5. optionally mark the post-crowd state as a comparison phase;
6. never mutate the immutable extracted R8PE01 game files.

## Known event evidence

Public reverse-engineering symbols currently provide the following REL data labels (addresses are loaded-REL addresses from the external research export and are **not** public OpenSPM API identities):

```text
0x80E4B890 aa2_02_crowd_cheer_msgs_evt
0x80E4BC3C aa2_02_mario_attacked_evt
0x80E4C098 aa2_02_cutscene_1_evt
0x80E4DBB8 aa2_02_cutscene_2_evt
0x80E4E308 aa2_02_init_evt
```

The final direct-boot implementation must derive/validate the active REL/event representation against the project's exact R8PE01 runtime before invoking any event directly. These labels are evidence for target identification, not permission to bake external loaded addresses into the SDK.

## Profiling questions

The fixture should collect enough data to distinguish at minimum:

- time/dispatch traffic inside animation-pose/name lookup work;
- cache/SMC hook traffic;
- GX/render submission work;
- generic actor/object/event work;
- simulation tick duration versus present duration;
- before-crowd-removal versus after-crowd-removal performance.

The immediate goal remains measured stable-60 work, not a broad profiler framework.
