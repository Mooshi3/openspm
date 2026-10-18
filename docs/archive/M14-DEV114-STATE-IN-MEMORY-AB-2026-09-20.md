# M14 dev114: opt-in LLVM state-in-memory A/B

Date: 2026-09-20  
Branch: `m14-native-execution`

## Status

**PROMOTED DEFAULT after player performance A/B + broad gameplay/cutscene smoke.**

The normal LLVM launcher is now:

```bat
M14-LLVM-UPDATE-RUN.cmd
```

which means:

```text
chunk=256
state_mem=1
```

The old sim0 path is retained explicitly for bisects:

```bat
M14-LLVM-STATE-MEM0-RUN.cmd
```

`M14-LLVM-STATE-MEM-RUN.cmd` is retained as a compatibility alias for sim1.

Do not use dev114 to judge the chunk128/chunk256 dev113 experiment. Finish or
collect the dev113 `state_mem=0` comparison first when practical.

## New causal evidence from the Yold active-set sweep

The 2026-09-20 live group sweep sharpens the reason to test this compiler mode.

At chunk256/state_mem=0, Yold's seven-NPC baseline is about 30 completed
frames/s with median disp+main about 16.68 ms. Suppressing active entries in
groups progressively lowers CPU work:

- two off: settled direct NPC-owned callbacks 128/16 frames;
- four off: 64/16;
- six off: 0/16, with representative disp+main about 13.24–13.87 ms and
  completed cadence about 40–44/s.

The dominant `dispDraw` target `0x8008533C` remains exactly 4800/16 =
300 callbacks/frame even after direct NPC-owned callbacks fall to zero. The
public US2 linker map places that address inside the `mapdrv.c` address
envelope, though not on a named public symbol.

This proves both a distributed active-NPC cost and a substantial fixed
map/display callback burden. It also shows that chunk256 did not materially
reduce dev105's ~23 generated steps/attempt. Address lookup is already indexed
and dev103 already removes host routing for definite ordinary PCs, so repeated
CPUState materialization/reload across CPUState-ABI boundaries is now a more
specific hypothesis than another generic dispatch fast path.

The same candidate build carries dev115 observation-only telemetry for exact
`0x8008533C`: camera IDs, callbackParam cardinality, collision count and top
params. That diagnostic adds no host clocks and no guest mutation.

## Why this is interesting

DolRecomp already exposes an opt-in LLVM option:

```text
--state-in-memory
```

The pinned compiler's implementation is highly relevant to OpenSPM's measured
hot path.

In `FunctionEmitter::slotInMemory()`:

```cpp
if (!state_in_memory_ || native_abi_)
    return false;
```

Therefore the option does **not** change native-ABI functions. It changes
non-native-ABI / CPUState-ABI generated bodies by allowing most guest state slots
to refer directly to their CPUState storage rather than creating local state
allocas that must be materialized and reloaded across runtime/dispatcher
boundaries.

That matches the OpenSPM frontier unusually well:

- dev101 proved the hot `dispDraw` linked callback site at `0x8005B29C`
  is a **CPUState-ABI** body;
- Yold Town executes roughly 370 dispDraw callbacks per produced frame;
- dev105 intentionally materializes callback state before entering its bounded
  generated dispatcher and reloads parent state after normal return;
- even after partition cleanup, callbacks still cross many generated boundaries.

With state-in-memory enabled, pinned DolRecomp's existing code:

- points most used CPUState-ABI state slots directly at CPUState in
  `emitEntry()`;
- skips redundant dirty-state stores for those memory-resident slots in
  `syncDirtyState()`;
- skips redundant reloads for those slots in `reloadState()` /
  `reloadUsedState()`.

This is not a guessed OpenSPM-specific fast path. It is an existing upstream
compiler mode whose scope naturally targets the exact ABI class we measured.

## External evidence, not an OpenSPM result

Upstream DolRecomp issue #29 reports repeated x86-64 Windows measurements where
plain LLVM versus LLVM+`--state-in-memory` changed from:

| Title | LLVM / C | LLVM state-in-memory / C |
| --- | ---: | ---: |
| Luigi's Mansion | 1.185 | 1.472 |
| Skyward Sword | 0.896 | 1.194 |
| Mario Kart: Double Dash | 0.906 | 1.145 |
| Pokémon Colosseum | 0.827 | 1.040 |

Those are upstream measurements on other games and runtimes. They are useful
motivation, **not evidence that OpenSPM will gain the same amount**.

The option remains experimental here until the player-owned SPM build proves
correctness and performance directly.

## Reproducible experiment identity

The state-memory axis is threaded through:

`M14-LLVM-UPDATE-RUN.cmd`
-> transactional runner
-> local LLVM module builder
-> DolRecomp generator
-> DolRecomp exact object-job hash
-> OpenSPM generated provenance
-> native-module CMake compile definition
-> runtime module identity / cadence telemetry.

The generated provenance contains:

```text
chunk_instructions=...
state_in_memory=0|1
dispatch_lookup=indexed
```

The toolchain preparation step now also verifies the exact pinned compiler
semantics that make this experiment safe to interpret:

- `slotInMemory()` must return false for native-ABI functions;
- eligible CPUState-ABI slots must directly alias `CPUState`;
- dirty-state materialization must skip those aliases;
- reload must skip those aliases and continue through the normal
  `reloadUsedState()` path.

Those source invariants are checked against the actual prepared DolRecomp
checkout on every toolchain preparation. The toolchain manifest records
`state_in_memory_contract=true`, and the transactional runner refuses to launch
unless that proof is present. A future compiler-pin update therefore fails
closed instead of silently changing dev114's storage semantics.

The local module build fails closed if either requested geometry or state mode
does not match the generated tree.

DolRecomp's pinned object hash itself already includes
`job->state_in_memory`, so switching this A/B does not require a manual cache
wipe and cannot legitimately reuse an object generated for the opposite mode.

Runtime identities are:

```text
m14-dev113-chunk256-sim0-yold   # default / state memory off
m14-dev114-chunk256-sim1-yold   # opt-in state memory on
```

The periodic cadence line also carries both:

```text
chunk=256 state_mem=0|1
```

so scoped captures are self-identifying.

## Test procedure

Baseline/default:

```bat
M14-LLVM-UPDATE-RUN.cmd
```

Enter Yold Town / `he2_03`, then:

```bat
M14-DEBUG-YOLD-BENCH.cmd
```

On a separate launch, state-memory candidate:

```bat
M14-LLVM-STATE-MEM-RUN.cmd
```

Enter the same room and repeat:

```bat
M14-DEBUG-YOLD-BENCH.cmd
```

Then:

```bat
M14-PERF-STATE-MEM.cmd
```

The reporter automatically chooses the newest valid:

- chunk256 / `state_mem=0` Yold capture as baseline;
- chunk256 / `state_mem=1` Yold capture as candidate.

It will not substitute a chunk128 run or a capture with missing/conflicting
experiment identity.

## Player result

The canonical Yold A/B passed on 2026-09-20:

| Metric | state_mem=0 | state_mem=1 | Delta |
| --- | ---: | ---: | ---: |
| Median produced FPS | 30.00 | 58.01 | +28.00 |
| Display | 7.434 ms | 5.689 ms | -1.745 ms |
| Main | 9.182 ms | 5.499 ms | -3.683 ms |
| Display + main | 16.596 ms | 11.227 ms | -5.368 ms |
| DrawDone | 2.267 ms | 2.287 ms | +0.020 ms |
| First-retrace critical path | 18.863 ms | 13.514 ms | -5.349 ms |
| VI margin | -2.196 ms | +3.153 ms | +5.349 ms |
| dev105 nested steps/attempt | 23.67 | 22.99 | -0.68 |

Regression gate: **PASS**.

The near-constant nested-step count and neutral DrawDone cost make the causal
interpretation strong: state-memory is reducing CPUState-ABI
materialize/reload cost rather than changing callback count, generated walk
length, or renderer synchronization.

dev115 simultaneously reports exact `0x8008533C` as camera 5 only with roughly
254 distinct callback params per 16-frame report window; the top params each
appear 16 times. Treat this as evidence for a broad map/display-object traversal
once per frame, not a single callbackParam being revisited hundreds of times.

## Success criteria

For dev114, **do not require nested generated steps/attempt to fall**.

Chunk256 tries to reduce how many generated chunks the callback traverses.
State-in-memory instead tries to reduce the cost of materialization/reload
inside CPUState-ABI paths. The step count can stay essentially identical while
wall time improves.

The useful metrics are therefore, in order:

1. basic boot/gameplay correctness;
2. Yold produced-frame cadence;
3. display and main phase time;
4. first-retrace critical-path margin;
5. DrawDone staying neutral/non-regressed;
6. dev105 nested steps/attempt as a structural cross-check, not the primary
   success metric;
7. one Game Over smoke check when convenient because dev97 correctness remains
   a required invariant.

A strong result would lower display/main wall time enough to put the current
~17+ ms first-retrace critical path below 16.667 ms without changing callback
count or guest behavior.

## Safety / unchanged architecture

dev114 changes one compiler storage policy flag only. It does not alter:

- game code or NPC state;
- native ABI policy (`unrestricted` remains);
- chunk geometry for this A/B (256 remains);
- dev103 host-call filtering;
- dev90 exact structural native-first entries;
- dev107 broad direct-BL native-first execution (still disabled);
- dev97 HP-zero / Game Over legal-entry repair;
- dev105 exact dispDraw callback site, callback order, bounds or fail-closed
  behavior;
- renderer/GX semantics;
- VI/retrace pacing;
- player-owned source identity;
- transactional restoration of the known-good C DLL.

If state-memory produces any correctness regression, leave it opt-in/off and
retain the dev113 default.

A source audit of pinned DolRecomp found no stale-state conflict with OpenSPM's
dev105 CPUState callback nesting. Memory-resident slots alias CPUState directly;
`syncDirtyState()` and `reloadState()` explicitly skip those slots. Some
generic CPUState exit code can still perform redundant self-load/store work, but
that is a performance question rather than a semantic divergence. The player
A/B remains authoritative.
