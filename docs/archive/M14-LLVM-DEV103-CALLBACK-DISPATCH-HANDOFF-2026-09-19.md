# M14 LLVM dev103 callback-dispatch handoff — 2026-09-19

## Player evidence that changed the diagnosis

Player log: `launch(20260919-145908).log`  
Tested repo: `74e6f822a570` (dev101)

dev101 did **not** restore pit death:

- HP reached 0;
- final sequence remained `SEQ_GAME(2)`, stage 1;
- `final_next_seq=2`;
- no Game Over request/observation.

Therefore dev98's synthetic-partition policy was not the cause of the current
Game Over regression. Restoring those partitions did not fix it.

dev101 *did* repair the dev100 profiler enough to expose real call owners.
The important measured shape in NPC/enemy-heavy gameplay is:

- every reported display callback interval originates at exact site
  `0x8005B29C` inside US2 `dispDraw`;
- one representative 2-frame sample invokes target `0x8008533C` 450 times
  and spends 4467 us in those intervals alone;
- other callback targets at the same site add more display time;
- `spmarioMain -> seqMain (0x8017B72C)` owns essentially the entire main
  phase; one heavy two-frame sample measured 21312 us there.

Public SPM source confirms `seqMain` is only the sequence dispatcher. During
`SEQ_GAME(2)` that interval is the complete `seq_gameMain` update, not
evidence that `seqMain` itself is a 5-10 ms helper.

dev99 teardown still reports:

```text
attempts=0 handled=0 normal_returns=0 ...
```

This is now explained: the measured `dispDraw` linked indirect site is emitted
in a legacy **CPUState-ABI** LLVM body. dev99's optimization lived entirely
inside `if (native_abi_)`, so it could never execute at the real hot site.

The residual ordinary-hostcall sketch is also highly concentrated at
`0x800361F0`. Public layout places that PC in ordinary `shadowdrv.c` text.
More importantly, dev87's generator was marking **every compiler-map row** as
"may require host semantics". dev57 added thousands of
`openspm_dev57_addr_taken_*` rows only to make dynamic callback addresses legal
LLVM entries. Those rows are not semantic host hooks. Treating them as such
forces a C host callback before native lookup on dynamic callbacks and naturally
scales with NPC/display-entry count.

## Current candidate: dev103

Private codegen identity:

```text
llvm-inline-wpar-indirect-budget-v14-dev103-entryonly-hostprefilter
```

Runtime module label:

```text
m14-dev103-entryonly-hostprefilter
```

### 1. Retire dev100's generated call-owner CFG markers

`apply_llvm_scene_profile.retire()` restores the pristine pinned
`emitTerminator()` shape. The owner profiler already produced the addresses we
needed. Keeping those extra observe/resume blocks is no longer justified,
especially because the pit regression first appeared after dev100 and survived
the dev101 partition rollback.

The older host-only phase timer remains active. This removes only dev100's
per-call owner CFG instrumentation.

### 2. Nest the real CPUState-ABI dispDraw callback site

The private DolRecomp transform now recognizes only the exact measured US2
linked indirect site:

```text
0x8005B29C
```

For that site only:

1. `materialize(target)` commits the same architectural CPUState and cycle debt
   the historical CPUState-ABI escape would expose;
2. call the existing bounded `openspm_m14_dev99_nested_dispatch()`;
3. that helper reuses generated replacement/semantic/native dispatch and the
   existing recursion guard;
4. resume the parent `dispDraw` body only for handled + no exception/fallback
   + exact PC+4 return;
5. any miss, exception, fallback, unusual return, or exhausted downcount exits
   through the historical outer runtime.

No callback is skipped, coalesced, rate-limited, or reordered.

### 3. Stop host-routing generic dev57 callback entries

dev103 changes dev87's may-host candidate construction:

- map rows named `openspm_dev57_addr_taken_*` are treated as entry-only;
- all other map rows remain host-first;
- platform host targets remain host-first;
- every direct BL source remains host-first;
- retained exact semantic coordinates remain host-first;
- unknown/out-of-image PCs remain host-first;
- if a dev57 address is independently present in one of those real semantic
  sets, set union re-adds it automatically.

**dev97 HP-zero synthetic rows deliberately remain host-first in this
candidate.** Do not broaden the rule until Game Over is revalidated.

This targets the per-instance callback tax without weakening semantic fail-close
rules.

## Validation state

Source contracts now cover:

- dev100 profiler retirement/idempotence;
- native-ABI dev99 transform retained;
- exact CPUState-ABI `0x8005B29C` transform/idempotence;
- dev103 entry-only map filtering and re-add behavior;
- explicit generated-header signature
  `OPENSPM_M14_DEV103_ENTRYONLY_HOSTCALL_FILTER`;
- codegen/module identity checks.

GitHub Actions is not an execution gate right now: recent M14 jobs fail before
steps/log creation because the repository's Actions minutes are exhausted. The
authoritative executable gate remains the local `M14-LLVM-UPDATE-RUN.cmd`.

## Next player test

Run:

```text
M14-LLVM-UPDATE-RUN.cmd
```

No manual cache wipe.

Check in this order:

1. **Pit death first.** Expected success is Nintendo-owned
   `next_seq=4`, SEQ_GAMEOVER progression, and normal exit.
2. Enter the same NPC/enemy-heavy room used in dev101 for 15-20 seconds.
3. Close normally and send the log.

Expected startup markers include:

```text
m14-dev103-entryonly-hostprefilter
M14 LLVM dev102 CPUState dispDraw nesting active:
M14 LLVM dev103 entry-only host filter active:
M14 LLVM dev100 scene call-owner CFG markers retired
```

Expected teardown signal:

```text
M14 LLVM dev102 dispDraw nested stats attempts=...
```

Unlike dev101, `attempts` should now be nonzero and should scale with display
callbacks. High `normal_returns` is the correctness/performance success shape.

Also compare:

- completed frames / host second;
- `disp_avg_us`, `main_avg_us`, `done_avg_us`;
- dev87 bypass count/rate;
- ordinary hostcall rate and heavy-hitter sketch;
- pit/Game Over sequence truth.

If `attempts` is still zero, inspect the generated CPUState terminator rather
than broadening the matcher. If attempts are high but abnormal returns dominate,
remove only the CPUState nesting path. If Game Over is still stuck after the
dev100 marker retirement, the next correctness step is to compare generated
MOT_24/onHpEquals0 control flow directly against the player-proven dev97 build;
do not force `SEQ_GAMEOVER`.
