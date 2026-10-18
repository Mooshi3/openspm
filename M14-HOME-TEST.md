# M14 home test

This is the shortest path from the repository to the **persistent strict-native M14 candidate**. It intentionally does not use Dolphin, ModernGekko, or any emulator fallback.

## When you get home

Open a terminal in the OpenSPM checkout and make sure this branch is current:

```powershell
git switch m14-native-execution
git pull
```

Then either double-click `RUN-M14-HOME-TEST.cmd` and paste the WBFS path when asked, or run:

```powershell
.\RUN-M14-HOME-TEST.cmd "D:\path\to\Super Paper Mario.wbfs"
```

The CMD wrapper automatically enters the latest installed Visual Studio 2022 x64 C++ developer environment before invoking the PowerShell driver.

## Important generated-source provenance

The proven M14 migration input is **218 generated C chunks**, but that number is not a main-DOL-only count. It is the historical combined tree:

```text
193 chunks  main.dol (text0 + text1)
 25 chunks  live relF text mapped at 0x80C0401C...
-----------
218 chunks  proven combined migration tree
```

Fresh generation of only `sys/main.dol` with the currently pinned DolRecomp C backend therefore produces **193 chunks**. Do not weaken the 218-chunk guard or use `--allow-chunk-count-mismatch` for the M14 title/load proof: doing so would silently omit the live REL code present in the proven tree.

The exact private player-derived rescue artifact is named:

```text
SPM-M14-whole-DOL-generated-direct-218chunks.zip
```

Despite the historical `whole-DOL` filename, it contains the combined DOL + one live REL generated tree. After extracting it, pass the inner directory that directly contains `generated.h` and `chunks\` via `-GeneratedDirect`.

Until deterministic REL extraction/relocation/merge is wired into the bring-your-own-WBFS generator, **`-GeneratedDirect` is the correct reproduction path for the current M14 proof**. The fresh DolRecomp path remains useful for main-DOL codegen experiments, but a 193-chunk result is not equivalent to the proven 218-chunk runtime input.

## What the driver does

`RUN-M14-HOME-TEST.ps1` connects the existing persistent M14 pieces into one deterministic local pipeline:

1. verifies that the checkout is on `m14-native-execution`;
2. creates/reuses only OpenSPM-owned cache paths under `.openspm/m14/`;
3. fetches the pinned MIT Aurora revision `749d6ee7a22bdfab78c8ece9047bca5d79aa72ca` into the local ignored cache, resets only that owned checkout, and applies the exact OpenSPM raw-GX + raster transforms;
4. builds `OpenSPMNative.exe`, `runtime/openspm_native_backend.dll`, `runtime/openspm_renderer.dll`, and the Aurora/SDL/Dawn runtime dependency set with Ninja/MSVC;
5. runs the OpenSPM `m13_*` / `m14_*` contract tests unless `-SkipTests` was requested;
6. verifies/extracts the player's WBFS and verifies the known R8PE01 `main.dol`; for the current 218-chunk proof it stages the supplied already-direct combined DOL+REL tree, while fresh main-DOL-only generation is a separate 193-chunk development path;
7. builds the player-local `openspm_local_native.dll` into `windows-candidate/User-OpenSPM/native/`;
8. verifies the four strict candidate files, records SHA-256 hashes, and launches the game unless `-NoLaunch` was requested.

Nothing Nintendo-derived is added to git or to the public candidate artifact path. The generated module and extracted game stay in the gitignored player-local cache.

## Inputs it may ask for

The script discovers `git`, CMake, Ninja, Python 3, `wit`, and `dolrecomp` from PATH when possible. It also honors:

```text
OPENSPM_CPU_RUNTIME
OPENSPM_WIT
OPENSPM_DOLRECOMP
```

If `OPENSPM_CPU_RUNTIME` is not set, paste the local compiler-runtime directory that contains `cpu\cpu.c` when prompted.

For the current known-good 218-chunk already-direct tree, DolRecomp generation can and should be skipped:

```powershell
.\RUN-M14-HOME-TEST.ps1 `
  -Wbfs "D:\path\SPM.wbfs" `
  -CpuRuntime "D:\path\DolRecomp\src" `
  -Wit "D:\path\wit.exe" `
  -GeneratedDirect "D:\path\m14-generated-direct-rescue"
```

Or through the CMD wrapper:

```cmd
set "OPENSPM_CPU_RUNTIME=D:\path\DolRecomp\src"
RUN-M14-HOME-TEST.cmd "D:\path\SPM.wbfs" -GeneratedDirect "D:\path\m14-generated-direct-rescue"
```

`-GeneratedDirect` must point to the directory containing `generated.h` and `chunks\` directly. The known rescue archive extracts that tree as `m14-generated-direct-rescue\`.

## The files that matter

A successful preparation produces:

```text
.openspm/m14/windows-candidate/OpenSPMNative.exe
.openspm/m14/windows-candidate/runtime/openspm_native_backend.dll
.openspm/m14/windows-candidate/runtime/openspm_renderer.dll
.openspm/m14/windows-candidate/User-OpenSPM/native/openspm_local_native.dll
.openspm/m14/game/sys/main.dol
.openspm/m14/home-test/last-run.txt
.openspm/m14/home-test/launch.log
```

The first launcher line should identify **`backend openspm.native`** and end with **`no emulator fallback`**. If that line does not appear, it is not a valid M14 native proof.

## What we are trying to prove tonight

The success ladder is deliberately incremental:

```text
candidate builds
-> strict launcher binds native backend + Aurora renderer
-> real visible boot frames
-> natural title screen
-> real PC Start input accepted
-> SEQ_LOAD/file-select path reached
-> no panic/exception
-> no synthetic 64x64 bbox
```

Do not call performance yet. The old diagnostic fast-forward and dispatcher rates were never FPS measurements. First prove the faithful persistent renderer/runtime path; only after that do we measure native performance and start the high-resolution/ultrawide/interpolation work.

Pay special attention to graphical corruption around effects or render-to-texture content. The new original-GX EFB copy-source/destination/format reconstruction is committed, but padded/sub-rectangle copy semantics still need real-SPM validation.

## If it fails

If the launcher started, send `launch.log` and `last-run.txt` plus a screenshot of the last visible frame. If preparation fails before launch, send the last console error. Do not try random DLL swaps or restore an emulator backend: the whole point of this test is to expose the next missing native contract honestly.

Useful switches:

```powershell
-NoLaunch                 # build/prepare everything, stop before game launch
-SkipTests                # skip OpenSPM contract tests for a quick retry
-Graphics D3D12           # force D3D12
-Graphics D3D11           # force D3D11
-Graphics VULKAN          # force Vulkan
-Graphics WEBGPU          # force WebGPU
```

Default graphics selection is `AUTO`.
