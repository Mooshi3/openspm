# dev70: wedding eff_sub liveness diagnostic

Base: `108f353e58721be65539b7d9c1d7b3ac320a86f9` (dev69).

## Player evidence

Two dev69 player logs settle the current questions.

1. `launch(20260917-001651).log` reproduces the wedding failure. It is not an
   Aurora/D3D/native crash at onset: guest execution becomes indefinitely
   stationary at `PC=0x80065128`, `LR=0x80064E34`, `CTR=0x80064E00` while VI
   continues near 60 Hz, native slices continue returning, and no guest program
   exception is reported. The eventual Windows termination follows the long
   stationary period and is not the initiating fault.
2. `launch(20260917-002156).log` confirms dev69 is mechanically active but does
   not recover heavy-scene cadence. It rejects roughly 479M immutable seam misses
   and 484M immutable host-target misses, while heavy scenes still execute about
   30-33 `spmarioMain` updates/s against about 60 retraces/s. DrawDone producer
   synchronization remains only roughly 3-5 ms/frame in those windows.

Together with dev66's earlier experiment, which removed almost all ordinary
semantic-router calls without a meaningful cadence gain, this rules out another
router/table micro-optimization as the main missing 2x. Keep dev69 because its
filter is conservative, but move performance work toward actual host CPU/frame
profiling after the wedding correctness fault is understood.

## Wedding loop decode

The dev67 stationary snapshot around `0x80065128` is:

- `0x80065118`: increment an address/index register by 4
- `0x8006511C`: increment `r21` by 1
- `0x80065120`: increment another stride by 0x0C
- `0x80065124`: increment another stride by 0x34
- `0x80065128`: `lwz r3,-0x7F60(r13)`
- `0x8006512C`: `lwz r0,4(r3)`
- `0x80065130`: compare `r21` with `r0`
- `0x80065134`: conditional branch back into the loop

The address lies in the game's `eff_sub` region according to the public US2/EU0
linker maps from the SPM decomp project. This is therefore an effect-subsystem
counted loop, not a sequence/event wait. The old snapshot omitted exactly the
state needed to decide whether the loop bound is corrupt or LLVM is failing to
make architectural loop progress: `r13`, `r21-r28`, the `r13-0x7F60` slot, the
pointer stored there, and the count at pointer+4.

This is not the dev54-dev57 `PC=CTR` unresolved indirect-entry failure. Here CTR
is `0x80064E00`, but execution reaches deep into that function and repeatedly
returns at `0x80065128`.

## Change

The preserved dev69 v42 source is copied byte-for-byte to
`tools/native_bridge/legacy_c_adapter_v42_dev69.c.in`. The active
`legacy_c_adapter_v42.c.in` becomes a thin ABI wrapper, module
`m14-dev70-llvm-effsub-loop-diagnostic`, around that exact source.

When the existing soft-slice return PC stays at `0x80065128`, dev70 emits extra
observation-only snapshots at 64, 128 and 1024 consecutive slices. Each records:

- `r13` and `r21-r28`;
- validated address `r13-0x7F60` and the word stored there;
- eight words at the resulting effect pointer when mapped;
- the count at current `r3+4` when mapped;
- PC/LR/CTR/CR/XER/downcount.

Every guest address is validated before reading. No guest register, memory,
queue, callback, timer, renderer state, VI deadline, LLVM object, or generated-C
path is modified.

## Next player run

Run `M14-LLVM-UPDATE-RUN.cmd`; no cache or save wipe and no LLVM regeneration is
needed. Startup must include:

`M14 LLVM dev70 eff_sub wedding-hang diagnostic active:`

Reproduce the wedding stall. Once the log contains
`M14 LLVM dev70 eff_sub loop diagnostic count=128`, the useful evidence is
already captured; there is no reason to leave the process hung for minutes.
Send that log.

Interpretation:

- sane/stable effect count with `r21` failing to advance across 64/128 snapshots:
  investigate LLVM chunk/resume/codegen state for this loop;
- absurd/corrupt count or pointer: trace the effect-work writer/caller before this
  loop; do not skip the loop or fabricate an effect count;
- `r21` genuinely increases but count is merely enormous: find why effect state
  created that workload/corruption; the 1024 snapshot is optional supporting data.

## Performance next

Do not spend another iteration on static routing tables. dev66 and dev69 are now
controlled negative results for that hypothesis. After the wedding fault is
classified, collect a real Windows host CPU profile in the same heavy scene and
perform the protected generated-C versus corrected LLVM A/B. The current logs
show real simulation work missing alternate VI opportunities; they do not show
GPU presentation cost large enough to explain the missing ~16 ms by itself.
