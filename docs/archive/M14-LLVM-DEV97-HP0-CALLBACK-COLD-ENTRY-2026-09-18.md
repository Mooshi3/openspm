# M14 LLVM dev97 — HP-zero motion-bounded cold-entry repair (2026-09-18)

## Player proof

`launch(20260919-003923).log` at repo `b7ba267899c4` narrowed the pit
softlock to the HP-zero motion path:

- HP changes from 1 to 0;
- immediately before zero, motion id is 25 = `MOT_BOTTOMLESS`;
- at HP 0, motion id is 24 = `MOT_24`;
- the process, retraces and `spmarioMain` continue;
- `next_seq` never becomes 4;
- shutdown remains `SEQ_GAME (2), stage 1, next_seq=2`.

Therefore Game Over is never requested. This is upstream of sequence commit and
is not a scheduler/GPU deadlock.

The PE-bbox correlation is secondary evidence only. Empty
`1023,0,1023,0` results also occur during healthy HP=10 gameplay, so an empty
bbox is not specific enough to justify disabling culling.

## First dev97 attempt and what it proved

The first dev97 generator revision derived `MOT_24` from US2
`marioMotTbl=0x803E8718`, then searched a bounded local neighborhood for a
unique direct `seqSetSeq(4,0,0)` call.

The player's real US2 build rejected that assumption:

```text
error: OpenSPM LLVM generation failed:
dev97 expected exactly one local seqSetSeq(4,0,0) call in the MOT_24 neighborhood;
found 0: none
```

This was a safe fail-closed result. No LLVM candidate was generated or launched.

## Correct structural relationship

Seeky's public symbols provide:

- `MOT_24 = 0x18`;
- `MOT_BOTTOMLESS = 0x19`;
- US2 `marioMotTbl = 0x803E8718`.

The public EU0 map gives the local ordering:

```text
mot_bottomless
mot_bottomless_post
mot_24
mot_24_post
onHpEquals0
mot_30
```

The addresses are regional and are not reused. The ordering is the useful part.

Instead of guessing how `onHpEquals0` reaches Game Over, the repaired dev97
reads these three function pointers directly from the player's own US2 motion
table:

- `marioMotTbl[25].mainFunc` = `MOT_BOTTOMLESS`;
- `marioMotTbl[24].mainFunc` = `MOT_24`;
- `marioMotTbl[30].mainFunc` = `MOT_30`.

Generation requires the player DOL to prove:

```text
MOT_BOTTOMLESS < MOT_24 < MOT_30
```

with a bounded bottomless-to-MOT24 gap and a bounded
`MOT_24 -> MOT_30` interval.

## Repaired dev97

Tool:

`tools/native_bridge/augment_llvm_hp0_callback_entries.py`

The transform now promotes **every aligned executable instruction in
`[MOT_24, MOT_30)`** as a legal LLVM partition entry.

Why this window:

- it begins at the exact player-owned MOT_24 main function;
- public symbol ordering says the HP-zero helper is immediately after MOT_24;
- it ends at the exact player-owned next known motion main, MOT_30;
- it therefore includes `mot_24`, `mot_24_post`, and the adjacent
  `onHpEquals0` helper surface without assuming its call instruction shape;
- it does not blanket-partition unrelated gameplay code.

This remains a recompiler/native-entry repair only. No guest HP, sequence,
motion, register, renderer or timing value is changed.

Expected generation marker:

```text
[OpenSPM] LLVM dev97 HP-zero callback cold-entry repair:
mot24=0x........ bottomless=0x........ mot30=0x........
window=[0x........,0x........) added=... already_present=...
```

The standalone tool marker is:

```text
M14 dev97 HP-zero motion interval ready:
bottomless=... mot24=... mot30=... window=[...,...) ...
```

## Next test

Run:

```text
M14-LLVM-UPDATE-RUN.cmd
```

No cache wipe.

First gate: dev97 should print real `bottomless/mot24/mot30` addresses and
continue into DolRecomp generation rather than rejecting the direct-call shape.

If the game launches:

- **Game Over works:** keep dev97; this strongly supports a missing interior
  cold-entry/callback path in the HP-zero motion surface.
- **HP still reaches 0 and hangs:** use the now-derived exact
  `[MOT_24,MOT_30)` interval for targeted runtime control-flow observation.
  Do not force `SEQ_GAMEOVER` and do not disable culling globally.

## Performance frontier

Keep this A/B correctness-only.

The latest player run still shows DrawDone around ~2 ms while deepest misses
spend roughly 12–15 ms in `spmarioDisp + spmarioMain`.

Public decomp reduces `spmarioDisp()` to:

```c
camDraw();
g_bFirstSmartAlloc = false;
```

So the 6–9 ms display phase is essentially `camDraw()` / display traversal.
That remains the next performance owner after this correctness A/B.
