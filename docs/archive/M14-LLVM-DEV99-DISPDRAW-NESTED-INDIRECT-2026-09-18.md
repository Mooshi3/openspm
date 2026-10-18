# M14 LLVM dev99 — bounded dispDraw indirect callback nesting (2026-09-18)

## Player baseline

The latest completed player run is:

```text
launch(20260919-031628).log
repo d4239388f54f
```

It includes dev98 but predates dev99.

Correctness remains good:

- dev97 still reaches Nintendo's own Game Over request;
- `next_seq_gameover_observations=1`;
- final sequence returns to title (`final_seq=1`);
- dev90 remains fail-closed with `fallbacks=0`.

Performance remains bad in the same enemy/NPC-heavy rooms. Representative
completed-frame windows from that run include:

```text
31 / 1032ms  disp=8.786ms  main=7.627ms  done=16.898ms
31 / 1032ms  disp=9.078ms  main=8.295ms  done=15.927ms
36 / 1032ms  disp=6.706ms  main=7.120ms  done=14.848ms
33 / 1032ms  disp=7.615ms  main=8.269ms  done=15.392ms
```

DrawDone itself is still much healthier than the old dev91 regression; ordinary
gameplay windows are commonly around ~1.8-2.0 ms. The heavy cliff therefore
remains primarily native `disp + main` work followed by a missed 16.667ms VI
deadline.

dev98 removed synthetic cold-entry names as LLVM partition boundaries but did
not materially improve the player-visible heavy-scene cliff. Keep it for this
A/B because it is correctness-neutral and mechanically restores the intended
entry-vs-partition distinction, but do not treat it as the performance solution.

## New architectural finding

Public SPM documentation describes:

```text
spmarioDisp()
    camDraw()
        dispDraw(camera)
            runs every registered DispEntry callback
```

The US2 display function is independently derived from public regional symbols:

```text
dispDraw US2 = 0x8005B028
size           0x000002CC
end            0x8005B2F4
```

Pinned DolRecomp's LLVM backend handles a linked computed branch through
`DOLIR_TERM_INDIRECT`. In legacy native-ABI cold-escape mode, the stock path
does:

```text
returnNative(target)
```

and `returnNative(Value *pc)` only stays inside the current native frame when
`pc == return_pc_`. Any other computed target is materialized and returned to
the outer dispatcher.

That is especially expensive for SPM's display architecture: `dispDraw` is
literally a callback walker. A room with many NPC/enemy/map/effect display
entries can therefore repeatedly perform:

```text
dispDraw bctrl
  -> materialize CPUState
  -> escape LLVM body
  -> outer bridge dispatch
  -> callback native chunk
  -> return / redispatch
  -> resume dispDraw
```

This is a substantially better match for the scene-dependent slowdown than
another global VI or renderer-sync theory.

Current upstream DolRecomp still uses the same indirect cold-escape behavior;
there is no upstream generic indirect native-call chaining to adopt.

## dev99 scope

dev99 deliberately does **not** change the generic indirect-branch policy.

The private LLVM transform catches only a linked computed transfer satisfying:

```text
cold_escapes_
term.linked
0x8005B028 <= term.guest_pc < 0x8005B2F4
continuation = term.guest_pc + 4
continuation is inside the same current LLVM body
```

The codegen marker is:

```text
OPENSPM_M14_LLVM_DISPDRAW_NESTED_INDIRECT
```

Before the nested call, LLVM executes `materialize(target)`, which is the same
architectural boundary used by the historical escape path. This settles the
parent's accumulated cycles and commits the guest state that the callback must
observe.

LLVM then calls:

```c
openspm_m14_dev99_nested_dispatch(cpu, target, continuation)
```

The bridge helper:

1. uses DolRecomp's existing `dolrecomp_call_enter()/leave()` recursion guard
   (24-deep default);
2. invokes the exact generated `dolrecomp_call(cpu, target)`;
3. therefore retains replacement routing, dev90 exact structural native-first,
   dev87 exact host-call filtering, semantic host callbacks, physical aliases,
   and indexed native lookup in their existing generated order;
4. accepts a nested return as normal only when:
   - the call was handled,
   - no OpenSPM fallback was requested,
   - no guest exception is pending,
   - `CPUState.pc == continuation`;
5. otherwise returns to the historical outer dispatcher.

On a normal nested return, the parent LLVM body executes `reloadUsedState()`
before branching to the exact local continuation block.

No renderer callback is skipped. No callback table is cached or inferred.
No guest drawing, culling, sequence, HP, timing or object state is fabricated.

## Why this is bounded

This is not a new broad native-first policy.

- Only indirect **linked** calls in `dispDraw` qualify.
- Unknown/semantic targets still run through the same generated dispatcher.
- Host semantics are still guarded by dev87's conservative may-host map.
- Recursion is bounded by DolRecomp's existing depth guard.
- Lookup misses, exceptions, fallback requests, unusual control returns and
  non-local continuations all return to the old path.
- dev77 wedding and dev97 Game Over entry surfaces are untouched.

## Build identity and tests

Private codegen identity:

```text
llvm-inline-wpar-indirect-budget-v10-dev99-dispdraw-nested
```

Active runtime module label:

```text
m14-dev99-llvm-dispdraw-nested
```

`test_llvm_native_pipeline.py` now locks:

- the v10 preparer and runner identities;
- the exact US2 `dispDraw` range;
- the nested-dispatch codegen marker/helper name;
- materialize -> nested call -> normal/escape split -> state reload shape;
- bridge recursion/dispatcher/fallback checks;
- active outer v42 module label;
- a synthetic transform/idempotence fixture.

## Runtime counters

Startup marker:

```text
M14 LLVM dev99 dispDraw nested indirect A/B active:
```

Teardown:

```text
M14 LLVM dev99 dispDraw nested indirect stats
    attempts=...
    handled=...
    normal_returns=...
    abnormal_returns=...
    depth_fallbacks=...
    lookup_misses=...
```

Interpretation:

- `attempts` large: the expected display callback shape is actually hot.
- `normal_returns / attempts` high: most callbacks can safely stay nested.
- `depth_fallbacks` near zero: recursion limit is not constraining the A/B.
- `lookup_misses` low: callback targets are covered by the generated native
  image/semantic dispatcher.
- large `abnormal_returns`: display callbacks often intentionally escape or hit
  semantics, so this optimization is less broadly applicable.

## Next player test

Run only:

```text
M14-LLVM-UPDATE-RUN.cmd
```

No cache wipe.

Use the same enemy/NPC-heavy rooms and trigger Game Over once.

Primary performance comparison:

- completed `frames / host_ms`;
- `disp_avg_us` — this is the primary dev99 target;
- `main_avg_us`;
- dev99 attempt/normal-return ratio;
- dev87 bypass count/rate;
- dev90 fallbacks.

Correctness gates:

- Game Over still reaches sequence 4 and completes;
- wedding remains live (`hang_80065128_max=0`);
- rendering/input remain visually normal;
- no new fallback/exception loop.

## Decision tree

### If disp time drops materially

Keep dev99. Then extend the same mechanism only from measured evidence, not by
globally nesting every indirect branch.

### If attempts are zero/tiny

The public function range is correct but the actual callback terminator is not
represented as a linked indirect in the emitted body we expected. Derive the
exact `bctrl` callsite(s) from the player DOL/compiler IR and adjust the
compile-time predicate.

### If attempts are large but normal returns are rare

Retire or narrow dev99. Profile callback target/control classes before another
optimization.

### If normal returns are high but heavy FPS barely changes

Indirect escape/re-entry is not the dominant cost. Keep the telemetry result and
move to per-callback ownership inside `dispDraw` plus top-level
`spmarioMain` call attribution.
