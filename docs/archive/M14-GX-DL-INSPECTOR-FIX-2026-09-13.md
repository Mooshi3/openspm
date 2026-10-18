# M14 GX display-list inspector build fix — 2026-09-13

## Symptom

`M14-UPDATE-RUN.cmd` reached the player-local module rebuild after all active v17 normalization/contracts passed, then CMake regeneration stopped with:

```text
OpenSPM M14 native GX display-list call-graph inspection failed (4):
expected one generated source for 80291DC8, found 0
```

No runtime candidate launched. The failure was diagnostic/configure-only; the active v17 generated tree was already stable:

```text
staged-renormalize changed_files=0 ... host_targets=42 ... verify=stable
```

## Root cause

`inspect_display_list_sdk_calls.py` assumed the R8PE01 SDK entry address would either:

1. be the base encoded in a chunk filename such as `chunk_*_80291DC8.c`, or
2. appear as a standalone `func_80291DC8(CPUState* ctx)`.

That assumption does not match the actual DolRecomp 218-chunk layout. Chunks represent address ranges. Both display-list SDK routines live inside the larger player-local translation unit rooted at `0x80291940` and appear as internal continuations:

```text
label_80291DC8:
...
label_80291E78:
...
```

The public repository still contains no generated Nintendo source; this was established using the user's private recovered generated tree only.

## Fix

Commit `e595e47495c218dd4cc5acbf7a341f015079b1a7` changes the inspector to:

- locate exactly one `label_<entry>:` across the generated chunk set;
- verify the first instruction comment at that label has the same guest PC;
- scan only from that entry through the routine's first guest `blr`;
- report BL addresses/targets from that bounded routine window;
- provide a source-free `--self-test` using a synthetic chunk where both SDK routines are internal labels.

The corrected inspector passes both the synthetic self-test and the exact recovered US2 218-chunk tree. On the private tree it reports 7 BL sites in `GXBeginDisplayList` and 9 in `GXEndDisplayList`.

## Build-safety correction

Commit `a7c7059885d40923d2579136b8fb1f9678269430` also changes the local-module CMake probe from fatal to warning-only.

This inspection is diagnostic research for dormant continuation-safe v19 and has no role in runtime-proven v17 behavior. A future generated-layout mismatch must therefore never prevent the player from building or launching v17 again.

The active module remains:

```text
legacy_c_adapter_v9.c.in
local-module bridge v17 / m14-dev17
```

Continuation-safe v19 remains dormant and is not part of CMake or staged normalization.

## Next player action

Run `M14-UPDATE-RUN.cmd` normally again. Do not delete `.openspm` or saves.

Expected configure behavior now:

```text
-- M14 player-local GXBeginDisplayList call graph: ...
-- M14 player-local GXEndDisplayList call graph: ...
-- OpenSPM local native bridge: 218 generated chunks
```

If the inspector unexpectedly fails, CMake should emit a warning and continue the v17 build instead of stopping before launch.
