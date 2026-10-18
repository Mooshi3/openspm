# M14 dev101 follow-up

## Current frontier — dev101 restores synthetic partitions after pit-death regression

Player log `launch(20260919-141853).log` tested `b4d85fe28e2f` (dev100).
HP reached zero in motion 24, but final sequence stayed 2/stage 1 with no Game
Over observation. dev99 attempts/handled/normal_returns were all zero. All 222
dev100 owner records had target/site zero. Therefore neither dev99 efficacy nor
per-callback attribution was demonstrated; do not claim either worked.

User noticed ~15k build objects versus ~17k earlier. This is consistent with
removing synthetic partition boundaries, but a launch log does not establish
total object count versus rebuilt/cache-hit count. Smaller object count does
not mean fewer actors. Whole-frame windows still fall near 30 FPS, e.g.
disp=8.7ms + main=7.7ms. No evidence here supports a one-actor-per-frame policy.

**dev101 candidate:** retire dev98's removal of BOTH dev57 address-taken and
dev97 HP-zero synthetic partition boundaries. Restore the original all-symbol
partition collector, preserving legal-entry collection and all semantic fixes.
This is a targeted rollback to test a correctness regression, not a proven
root-cause diagnosis. Broader chunks can change native-ABI eligibility and
restart behavior; interior legal-entry status alone was not sufficient proof.

Fix dev100 observation by allowing host-only markers in CPUState-ABI LLVM
bodies as well as native-register-ABI bodies. Neither type needs profiling
CPUState writes. This is still compiled native code; ABI choice is not emulator
fallback. No draw/update callback is skipped or rate-limited by the sampler.

Codegen: `llvm-inline-wpar-indirect-budget-v12-dev101-partition-restore`.
Module: `m14-dev101-llvm-partition-restore`.

Validation: existing pipeline suite PASS; compiled C behavioral suite PASS;
exact restoration of the original collector in a synthetic fixture PASS;
actual pinned compiler cached-source migration and idempotence PASS. Updated
sampler migration and removal of the native-ABI-only gate are tested.
Full Windows/LLVM build, pit-death recovery and FPS remain player-unverified.

Next: `M14-LLVM-UPDATE-RUN.cmd`, no manual cache wipe. Check pit death/Game Over
first, then a crowded room. If death still sticks, inspect actual generated
HP-zero motion entries; do not force Game Over or assume the rollback worked.
If nonzero owner targets now appear, use them to select the next optimization.

