# M14 LLVM Wii/Broadway PVR correction — 2026-09-14

## Symptom

The first fully built LLVM-native M14 module loaded successfully and entered guest execution, but failed very early (about 211,882 guest cycles) through the Broadway Program exception vector (`0x700`) before any v19 GX display-list seam hit.

The initial investigation treated this as a possible LLVM lowering/native-ABI issue. The player recognized the failure pattern from an earlier milestone and pointed out that the runtime appeared to be in GameCube rather than Wii mode.

## Root cause

The LLVM generator was already correct: `tools/native_bridge/generate_local_llvm.py` invokes the pinned DolRecomp compiler with `--cpu broadway`.

The runtime CPU identity was not correct. `backend/native/openspm_native_backend_v3.c`, inherited by the current dev26 backend, defined:

```c
#define OPENSPM_BROADWAY_PVR UINT32_C(0x00083214)
```

`0x00083214` is the Gekko/GameCube PVR. Wii Broadway software identifies the Wii CPU with PVR `0x00087102`.

This value is live in the LLVM path. Pinned DolRecomp's `ppc_mfspr()` handles SPR 287 (PVR) by invoking `cpu->spr_read` when a callback exists. The OpenSPM native bridge installs `bridge_spr_read`, which routes to `openspm.wii.cpu`; dev26 inherits v3's `v3_spr_read`. Therefore the guest was genuinely being told that a Broadway-compiled Wii program was running on a Gekko/GameCube CPU.

## Fix

Commit `f21b9bf681503a1548d6582dda3afaad7978a9ee` changes the OpenSPM Wii CPU service to:

```c
#define OPENSPM_BROADWAY_PVR UINT32_C(0x00087102)
```

The first PVR read now also emits:

```text
M14 Wii CPU identity: Broadway PVR=0x00087102 (Wii mode); Gekko/GameCube PVR is not exposed.
```

This is a host/backend correction only. It does not alter the 14,414 LLVM guest objects, so the preserved/resumable object tree remains valid. The normal host/backend incremental build must relink the backend DLL, which `M14-LLVM-UPDATE-RUN.cmd` already does in step 3.

## Verification target

On the next LLVM run:

1. Step 3 should incrementally rebuild the native backend due to the v3 source change.
2. Step 5 should reuse the existing LLVM guest objects rather than regenerate them.
3. Runtime should print the explicit Broadway PVR marker above.
4. The previous ~211k-cycle Program-exception failure should either disappear or move materially later. If another guest exception remains, use the already-added `M14 LLVM first guest exception:` dump to diagnose the next independent issue.

Do not weaken the first-fault diagnostic yet; it remains useful until the LLVM path reaches v19 graphics parity and sustained gameplay.
