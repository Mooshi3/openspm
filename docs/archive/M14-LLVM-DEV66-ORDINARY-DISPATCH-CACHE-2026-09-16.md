# M14 LLVM dev66 — ordinary host-dispatch decision cache

Date: 2026-09-16
Branch: `m14-native-execution`

## Player result that triggered this change

The first dev65/chunk-128 run was playable and game-over worked normally again, but gameplay was still visibly slow. The player also had Binding of Isaac open, so host wall-clock cadence is not a clean A/B by itself.

The teardown counters are not explained by another game being open:

```text
ordinary host callbacks ~= 503.25 million
production ordinary     ~= 503.24 million
seam lookup calls        ~= 503.03 million
seam comparisons         ~= 4.385 billion
host lookup calls        ~= 502.81 million
host comparisons         ~= 2.765 billion
direct-BL rescues        ~= 1.956 million
```

Gameplay windows remained roughly 20-30 `spmarioMain` updates/s against about 60 retraces/s.

Game-over correctness is no longer an active defect from this run: the same HP=0 test completed normally.

## Architectural finding

Pinned DolRecomp emits the public dispatcher in this order:

```c
ctx->pc = address;
if (dolrecomp_dispatch_replacement(ctx, address)) return 1;
if (ctx->host_call && ppc_host_call(ctx, address)) return 1;
if (dolrecomp_call_original(ctx, address)) return 1;
```

The physical-PC alias path uses the same host-before-native ordering.

That ordering is conservative for a generic recomp runtime, but it is pathological for OpenSPM's current LLVM integration. Every materialized LLVM side exit enters a layered semantic C router before DolRecomp even checks whether the destination is ordinary generated native code. dev63 already proved the analogous native-region-entry callback storm was real; dev65 shows the ordinary callback path is now the dominant remaining router pressure.

A blind native-first reorder is not safe yet. Some exact PCs intentionally require OpenSPM semantics, and older LLVM cold-entry failures proved that an arbitrary in-range PC is not automatically a legal native entry.

## dev66 implementation

`tools/native_bridge/legacy_c_adapter_v42.c.in` now adds an outer exact-PC decision cache above the unchanged dev58/dev50 semantic router.

The cache is direct-mapped with exact address tags. Each entry is one of:

- `BYPASS`: a complete underlying host-router pass returned `false` and left the relevant CPU/fallback state unchanged. Repeated callbacks at that exact PC immediately return `false`, allowing DolRecomp to continue to `dolrecomp_call_original()` without repeating the semantic searches.
- `DELEGATE`: the PC is dynamic/stateful, was handled, or changed architectural/fallback state. Every repeat still traverses the original semantic router.
- unknown/collision: run the original router once and classify again.

Collisions cannot create a false bypass because the full guest address is tagged and compared.

## False-return semantic coordinates kept delegated

Several callbacks can return `false` while still doing work or requiring state-dependent behavior. dev66 refuses to learn these as ordinary bypasses:

- the four frame-phase telemetry PCs;
- v19 `GXBeginDisplayList` / `GXEndDisplayList` and durable return hooks;
- deterministic relF/BSS allocator seams;
- first DSP task-init wait completion `0x802871E4`;
- HOME/system-UI call/main coordinates;
- all 40 exact platform host targets.

Hosted direct-call seams, post-MTMSR completion and direct-BL rescue return handled on their first visit, so they naturally become `DELEGATE` entries.

Input remains protected: WPAD/KPAD/pressed getters are semantic host-service targets beneath dev51 and are never learned as ordinary bypasses.

## Why this is bridge-only first

This keeps the exact dev65 LLVM objects/chunk-128 geometry for a clean A/B. No LLVM object regeneration should be required for dev66; only the local native bridge should rebuild/relink.

If this works, the same proven classification can move down into DolRecomp's emitted dispatcher so cache-positive ordinary PCs skip the function-pointer callback entirely. That would remove the remaining C transition as a second-stage optimization.

## Next test

Run:

```text
M14-LLVM-UPDATE-RUN.cmd
```

No cache/save wipe.

Expected runtime marker:

```text
M14 LLVM dev66 ordinary dispatch decision cache active
```

Expected teardown marker:

```text
M14 LLVM dev66 ordinary decision stats callbacks=... bypass_hits=... delegate_hits=... misses=... bypass_inserts=... delegate_inserts=... collisions=... changed_false=... forced_dynamic=...
```

### Strong success

A good result should show:

1. `bypass_hits` dominating dev66 callbacks;
2. dev58 `ordinary hostcall sample total` collapsing by orders of magnitude relative to the 503M dev65 run;
3. dev50 seam/host lookup calls and comparison counts collapsing similarly;
4. `spmarioMain` cadence materially rising toward 60/s;
5. no input, v19 graphics, GX, scheduler, death/game-over, or hosted-service regression.

If router counts collapse but simulation remains around 30 Hz, the next step is no longer router archaeology: capture host CPU-time profile / LLVM native hot regions and compare corrected LLVM against the protected generated-C baseline.

If router counts do not collapse, inspect dev66 `bypass_hits` vs `delegate_hits/misses/collisions`; do not add another one-function specialization.
