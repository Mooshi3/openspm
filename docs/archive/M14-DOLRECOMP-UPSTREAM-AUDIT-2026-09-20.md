# M14 DolRecomp upstream audit

Date: 2026-09-20

## Scope

OpenSPM currently pins DolRecomp at:

```text
40637c4683bd2820ac5b23607ee344720beb26df
```

The upstream `ExpansionPak/DolRecomp` `main` observed during this audit is:

```text
71ce7f97419b1bb1ba9a9596c41507f6629e0fb0
```

Upstream is eight commits ahead of the OpenSPM pin and not diverged behind it.

The goal of this audit was to determine whether OpenSPM is carrying private
performance work that upstream has since implemented more cleanly, or whether
an upstream rebase contains a material performance fix we should prioritize over
dev113.

## Upstream changes after the pin

The eight commits collapse to four substantive changes plus merge commits:

1. Mach-O cold-section naming portability.
2. Post-`mtmsr` region-leader repair.
3. Portable LLVM ABI-policy popcount for MSVC.
4. LLVM CI coverage on GCC, MinGW, MSVC and AppleClang.

Only items 2 and 3 are relevant to the current Windows OpenSPM runtime/compiler
path.

## Post-MTMSR region leader

Upstream added a rule in
`src/backend/llvm/register_state.cpp::scanRegionLeaders()` that marks the block
following an MSR write as a region leader. The upstream commit explains that
`emitStateWrite` can side-exit at `guest_pc+4` when `mtmsr` enables
interrupts; without a legal native re-entry there, execution can detour through
an interpreter/fallback path.

This is a real and potentially enormous performance issue in affected games.
The upstream measurement reports an approximately 1.95x improvement in Pokemon
Colosseum while being essentially neutral in its other tested titles.

OpenSPM already covers the same architectural requirement in its retained
dev71 compiler base:

`_apply_openspm_llvm_codegen_patch_base_dev71.py`

Its `collect_llvm_entry_points()` patch explicitly adds:

```c
if (inst.op == PPC_OP_MTMSR &&
    llvm_code_address(sections, section_count, address + 4u))
    points[count++] = address + 4u;
```

Pinned DolRecomp's existing `FunctionEmitter::scanRegionLeaders()` iterates
all `entry_points_` after its CFG scan and marks the corresponding block as a
region leader. Therefore OpenSPM's player-global `mtmsr+4` entry census already
produces the region-leader property added by upstream.

OpenSPM also retains its own post-MTMSR interrupt-completion host semantics.
Do not delete those runtime semantics merely because upstream now supplies the
compiler-side leader rule.

## MSVC popcount

Upstream replaced `__builtin_popcountll` in LLVM ABI-policy calculation with
a portable C++17 implementation so MSVC can compile the LLVM backend.

OpenSPM independently solved this earlier in the same retained dev71 base:

```text
OPENSPM_M14_PORTABLE_POPCOUNT64
```

The private transform replaces the builtin with an exact C++17
`portablePopcount64()` helper. This is build-time code only and is not part of
the game's runtime critical path.

## Other upstream changes

The Mach-O cold-section fix is irrelevant to the current Windows x64 player.
The expanded upstream CI matrix is valuable project hygiene, but it does not
change generated runtime behavior on the current player path.

## Decision

Do **not** rebase the pinned compiler before the dev113 chunk128/chunk256 Yold
A/B.

Reasons:

- no upstream post-pin runtime optimization is missing from the current Windows
  OpenSPM path;
- the two relevant upstream fixes are already structurally present in OpenSPM;
- the private transform stack intentionally verifies exact pinned source shapes;
- moving the pin would force a migration of several exact source transforms and
  invalidate compiler/cache identity, adding correctness risk immediately before
  a clean performance A/B;
- dev113 is testing a measured OpenSPM-specific frontier: thousands of generated
  chunk transitions per frame in `dispDraw`, which upstream's eight commits do
  not address.

After dev113 is resolved, an upstream-pin refresh is still worthwhile as
maintenance. At that point the clean migration should delete private patches
that have true upstream equivalents rather than stacking duplicate logic.

## Contract recommendation

Keep explicit source contracts for the two upstream-parity fixes while pinned:

- `OPENSPM_M14_PORTABLE_POPCOUNT64`;
- `PPC_OP_MTMSR` -> `address + 4u` in the retained entry-point collector.

That makes a future pin migration fail visibly if one of these architectural
requirements is accidentally dropped.
