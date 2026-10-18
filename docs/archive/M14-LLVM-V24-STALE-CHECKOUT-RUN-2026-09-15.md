# M14 LLVM v24 stale-checkout proof attempt — 2026-09-15

## Observed run

Player log: `launch(20260915-164306).log`.

The launch identified its repository revision as:

```text
37f6bb327c7a
```

and later reproduced the already-known v23 frontier:

```text
M14 LLVM frame-phase telemetry is nonblocking: ...
Native migration boundary reached reason=3 detail=0x0000000C source=0x80AF1DDC
```

The log contains none of the v24/dev34 proof evidence:

```text
m14-dev34-llvm-relf-tail-proof
M14 LLVM relF deterministic-tail allocation active: ...
M14 LLVM relF tail allocation result: ...
```

Therefore this run did **not** test the deterministic relF tail allocator. It provides no new relF base measurement and must not be used to change `REL_BASE`.

## Diagnosis

`37f6bb327c7a` is an older v23-era checkout. The remote branch had already advanced through the v24 relocation work and the v24 source-contract hardening.

The LLVM update wrapper previously trusted the environment flag `OPENSPM_M14_LLVM_POSTPULL` to mean that the checkout had already been refreshed. An interrupted nested batch / Ctrl+C path can leave this assumption stale while a later invocation continues in the same command environment. The supplied runtime log ended with `^C`, so this stale-checkout path is credible and now eliminated regardless of exact shell lifetime behavior.

## Fix

Commit:

```text
7994c699d318abe2b3b41753865576d76c8bbe45
M14 LLVM: fail closed on stale local checkout
```

`M14-LLVM-UPDATE-RUN.cmd` now:

1. fetches `origin m14-native-execution` on every worker run;
2. records local `HEAD` before synchronization;
3. fast-forwards specifically to `origin/m14-native-execution`;
4. resolves local and remote heads again and fails closed unless they are identical;
5. prints the exact verified SHA;
6. if the checkout advanced, copies and invokes the newly fetched runner before building;
7. no longer trusts `OPENSPM_M14_LLVM_POSTPULL`.

The generated-C rollback path is unchanged.

## Next run

Use a fresh command shell (or manually update the repo once if the currently open shell is still executing the pre-fix script), then run:

```bat
M14-LLVM-UPDATE-RUN.cmd
```

Before the build it must print an exact checkout verification line for the current remote head.

The resulting `launch.log` must no longer report `37f6bb327c7a` and must reach the v24/dev34 relF-tail proof markers. Only the logged v24 `base=...` / `match=yes` result is valid evidence for changing the LLVM relF codegen base.

## Existing post-proof caveat

After a clean deterministic relF base measurement, still audit executable-section self-relocations into separately allocated REL BSS before declaring live OSLink and codegen relocation state fully aligned.