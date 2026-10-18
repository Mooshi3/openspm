# M14 LLVM dev77 — wedding restart-entry repair

Date: 2026-09-17  
Branch: `m14-native-execution`

## Control experiment

The protected generated-C / non-LLVM build passes the wedding cutscene. LLVM
freezes deterministically. Therefore the retail effect loop, scheduler, VI and
scene logic are capable of completing; the remaining defect is LLVM-specific.

The LLVM freeze remains:

```text
PC  = 0x80065128
LR  = 0x80064E34
CTR = 0x80064E00
exc = 0
program = 0
```

with stationary loop locals:

```text
r21 = 0x00000000
r22 = 0x81609580
r23 = 0x811AEFA0
r24 = 0x00000000
```

## Decisive new evidence: 65,536 is the bridge dispatch cap

Once fully wedged, the player repeatedly reports:

```text
calls=512 guest_cycles=33554432 avg_cycles=65536 budget=250000
```

`65536` is `OPENSPM_BRIDGE_MAX_TOPLEVEL_DISPATCHES`, not a DolRecomp guest
budget. The bridge resets `cpu.downcount=0` before every `dolrecomp_call()`, then
charges a minimum of one cycle when the call returns without charging anything.

Therefore the stationary state means:

```text
dolrecomp_call(0x80065128)
  -> reports success
  -> executes/charges zero guest work
  -> leaves PC at 0x80065128
  -> repeats until the 65,536-dispatch safety cap
```

This explains why dev72/dev73/dev74/dev75/dev76 state-propagation experiments did
not alter the final stationary registers: after the bad restart is established,
the retail loop instructions are not executing at all.

## Retail identity / loop tail

The clean R8PE01 DOL must match this exact window before dev77 is installed:

```text
0x80065118  3B180004  addi r24,r24,4
0x8006511C  3AB50001  addi r21,r21,1
0x80065120  3AF7000C  addi r23,r23,0x0c
0x80065124  3AD60034  addi r22,r22,0x34
0x80065128  806D80A0  lwz  r3,-0x7f60(r13)
0x8006512C  80030004
0x80065130  7C150000
0x80065134  4180FD68  bc -> 0x80064E9C
```

This proves `0x80065128` is an ordinary retail instruction, not a host-service
callsite, and it is not the loop header.

## LLVM restart contract

Pinned DolRecomp's LLVM dispatcher can find the generated chunk containing an
aligned PC, but `FunctionEmitter::emitEntry()` directly admits only native
`region_leaders_`. An arbitrary interior PC goes through the cold-entry fallback
path.

That fallback assumes a runtime capable of interpreting ordinary PowerPC
instructions until a legal native entry is reached. OpenSPM intentionally has no
general PPC interpreter; its fallback handles only narrow migration semantics.
The same incompatibility previously produced the dev54/dev56/dev57 computed-entry
stalls.

OpenSPM's LLVM integration also permits runtime-computed transfer targets to be
materialized at scheduling boundaries. Any such externally visible PC must be a
legal native restart coordinate. The wedding run proves `0x80065128` is one such
coordinate regardless of which native transfer first exposed it.

## dev77 repair

`tools/native_bridge/augment_llvm_observed_indirect_entries.py` now:

1. verifies the complete retail instruction window above;
2. decodes the branch at `0x80065134` and requires target `0x80064E9C`;
3. adds a private zero-size partition-map entry:

```text
80065128 0 openspm_dev77_wedding_resume_80065128
```

4. keeps the existing dev54/dev56 measured targets and dev57 address-taken code
   census unchanged.

The map is generated locally from the player's clean DOL and passed to DolRecomp
before LLVM range construction. Therefore `0x80065128` becomes a real native
range start with normal ABI/liveness analysis instead of an interior cold-entry
fallback.

This is intentionally one structural split, not a global "every instruction is
an entry" policy. It should have negligible performance cost and does not skip,
fabricate or emulate any guest instruction.

No Nintendo bytes or assets are committed.

## Cache behavior

No manual cache wipe should be required. DolRecomp's LLVM job identity hashes the
function ranges; adding this partition changes the affected range geometry and
invalidates the relevant stamped objects while preserving unrelated cached
objects.

## Next player test

Run:

```text
M14-LLVM-UPDATE-RUN.cmd
```

Generation should print a dev77 line containing:

```text
wedding_resume=0x80065128
wedding_backedge=0x80065134->0x80064E9C
```

Primary success criterion: the wedding advances past the previous freeze and the
`avg_cycles=65536` zero-progress redispatch signature disappears.

If it still returns to `0x80065128` with zero progress, verify the generated
partition map and emitted LLVM range actually start at `0x80065128` before making
another state-propagation change. If the frontier moves, treat the new PC as the
next restartability/correctness frontier rather than resurrecting the dev72-76
hypotheses.
