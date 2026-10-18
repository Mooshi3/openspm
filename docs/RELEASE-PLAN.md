# Release plan: from "run the .cmd" to "unzip, run the exe, pick your game"

Target experience: download `OpenSPM-<version>.zip`, unpack, start
`OpenSPMNative.exe`, pick a `.wbfs`/`.iso`. After a one-time wait with a progress
window, the game runs. There is no Visual Studio, git, CMake, Python install,
or network access involved.

The hard constraint is legal. The zip contains OpenSPM's code and
redistributable third-party runtimes only. It carries nothing derived from
`main.dol` or `relF`: no generated code, no generated headers, no per-player
native module, no extracted files and no keys. Everything game-derived is made
on the player's PC from their own copy.

## 1. The pipeline today

### Entry points

| Script | Role |
| --- | --- |
| `RUN-M14-HOME-TEST.cmd/.ps1` (once) | Clones Aurora @`749d6ee` and applies OpenSPM's patches. Configures and builds the host (CMake + Ninja + MSVC; Aurora fetches Dawn/SDL3 packages) and runs ctest. `tools/m14_lab/resume_m14.py` then runs `wit extract` on the WBFS into `.openspm/m14/game` and runs DolRecomp's **C** backend. Finally `build_local_module.py` compiles the "known-good C module" with MSVC. |
| `M14-VANILLA-RUN.cmd` | Sets the presentation env vars, then calls `M14-LLVM-UPDATE-RUN.cmd AUTO 256 1 0`. |
| `M14-LLVM-UPDATE-RUN.cmd` | Finds VS Build Tools (vswhere / VsDevCmd x64: `cl.exe`, and VS's CMake, CTest and Ninja). Requires `git` and branch `m14-native-execution`, then runs `git fetch` + `merge --ff-only` (network) and restarts itself if HEAD moved. Requires Python 3, then runs `run_m14_llvm_candidate.py`. |
| `run_m14_llvm_candidate.py` | Seven steps, listed in the next table. |
| `LAUNCH-M14-CANDIDATE.cmd` | Python fail-fast debugger self-test. Relinks the renderer once with an MSVC `/MAP` (CMake) and runs `OpenSPMNative.exe --game .openspm\m14\game` under the debugger. |
| `OpenSPMNative.exe` alone | First-run picker (`launcher/native_game.h`) checks the disc header. The player's own `wit` extracts to `User-OpenSPM\game`. The exe then needs `User-OpenSPM\native\openspm_local_native.dll` and stops with a message if it is missing. |

### What `run_m14_llvm_candidate.py` does

| Step | Work | Needs | Per player? |
| --- | --- | --- | --- |
| 1/7 | Preflight. Checks the `main.dol` SHA-256 against the C module's identity, LZ10-decodes `relF.bin` and checks the relF layout fingerprint and the v19 seam shape. | Python, the known-good C module | yes (reads the game) |
| 2/7 | `test_llvm_native_pipeline.py` | Python | no |
| 3/7 | Re-runs CMake and rebuilds OpenSPMNative, the backend and the renderer (patched Aurora), then runs ctest. | MSVC, CMake, Ninja | **no** |
| 4/7 | `prepare_dolrecomp_llvm.py` clones DolRecomp @`40637c4` (network) and applies the OpenSPM codegen patch. It finds LLVM 19/20 or downloads the pinned LLVM 20.1.8 developer archive (about 940 MB, SHA-256 pinned), repairs its DIA SDK path, and builds `dolrecomp.exe` (CMake, Ninja, MSVC). | git, network, MSVC | **no** |
| 5/7 | `build_local_llvm_module.py`. First `generate_local_llvm.py`: builds a private codegen DOL (`main.dol` plus the relocated relF text), derives the seam and GX display-list headers, runs `dolrecomp --backend llvm --targets host` (about 14k native COFF objects plus `generated.h`), runs the augmentation passes and validates the result. Then it stages DolRecomp's `cpu.c` with the Broadway SPR patch, writes the identity header, and has CMake + MSVC compile the bridge (the `legacy_c_adapter_v42.c.in` chain, about 600 KB of C) and `cpu.c` (`/O2 /GL /LTCG`). The link produces `openspm_local_native.dll`. | Python, dolrecomp, MSVC (cl + link + CRT/SDK libs), CMake | **yes** |
| 6-7/7 | Swaps the DLL into `windows-candidate\User-OpenSPM\native` for one launch, then restores the C DLL. | Python | dev-only |

Only steps 1 and 5 depend on the game. The host exe, the backend, the renderer,
Aurora and even `dolrecomp.exe` are game-independent. CI already builds the host
part (`build-m14-native.yml`, job `windows-aurora-renderer`) and audits it.

### Why the per-player build needs a compiler at all

The DolRecomp objects are already native code. A C compiler is still needed
because the bridge `#include`s headers generated from the player's DOL:
`generated.h`, `openspm_local_v19_llvm_seams.h`,
`openspm_local_gx_dl_semantics.h` and `openspm_local_identity.h`. Those headers
are game-derived and cannot be shipped precompiled, even though they are
deterministic for the one supported disc. A linker and import libraries are
needed to turn the objects into a DLL.

## 2. Options

### (a) Ship the build toolchain and drive it from the exe on first run

What the per-player step needs:

- `dolrecomp.exe` with LLVM linked in. It is game-independent, so CI builds it
  once from the pinned commit plus OpenSPM's patch.
- A C compiler for the bridge and `cpu.c`. `clang`/`clang-cl` from the pinned
  LLVM 20.1.8 can do this; the project already downloads that archive.
- A linker. `lld-link` comes from the same archive.
- CRT and SDK headers and import libraries. This is the real blocker (below).
- Python 3 for the pipeline scripts. The official embeddable Python is about
  10 MB and is redistributable under the PSF license. Alternatively the scripts
  can be ported (Stage 4).
- **Not** needed: git, network, CMake or Ninja. CMake only orchestrates three
  compile units and one link; a direct command list with a linker response file
  for the 14k objects does the same.

**Can DolRecomp's output be linked with lld-link?** It should be. LLVM itself
emits the objects as COFF for the host triple, and lld-link is the linker
LLVM's own Windows toolchain uses. The bridge's MSVC-specific flags have direct
equivalents. `/experimental:c11atomics` is not needed because clang has
`<stdatomic.h>`. `/GL /LTCG` becomes `-flto=thin` with lld-link, and it only
affects the bridge and `cpu.c`, since the generated objects are final code.
This still has to be proven (Stage 1). Things to check with `llvm-nm`:

- which CRT symbols the objects and `cpu.c` reference: `memcpy`, `memset`,
  `__chkstk`, `_setjmp`/`longjmp` (the SJLJ yield quantum), `__security_check_cookie`
  and math functions;
- that no CRT object (a `FILE*`, heap memory freed on the other side, `errno`)
  crosses the `native_api.h` boundary.

**The CRT/SDK problem.** clang-cl still wants MSVC's CRT headers and the
Windows SDK import libraries, and those are not redistributable. There are
four ways around it:

1. Keep requiring VS Build Tools. That is today's situation and fails the goal.
2. Download the SDK/CRT from Microsoft on first run, the way `xwin` does, with
   the player accepting Microsoft's license. It needs network and several
   hundred MB, and it is fragile.
3. Use llvm-mingw, whose mingw-w64 headers and libraries are permissive
   (ZPL/public domain). DolRecomp would have to emit `x86_64-w64-windows-gnu`
   objects so the whole DLL uses one ABI. The DLL is then a GNU-ABI module
   loaded by an MSVC host. That works for a pure C ABI, but it adds a second
   runtime and toolchain.
4. **Make the module freestanding.** The bridge and `cpu.c` use a small,
   fixed set of CRT and kernel32 calls (the census is in section 5). These are
   served by a small OpenSPM-authored shim compiled into the module, plus
   import libraries that `llvm-dlltool` makes from OpenSPM-written `.def`
   files for `kernel32.dll` and `ucrtbase.dll`. The Universal CRT is part of
   Windows 10 and later, so `snprintf` keeps the real CRT formatting engine
   without shipping one. The link is `lld-link /DLL /NODEFAULTLIB` with clang's
   own builtin headers only. No SDK is needed, the whole toolchain is LLVM, and
   the build is checked on Linux with a cross-compile to
   `x86_64-pc-windows-msvc`. Section 5 shows this working in the spike.

**Size.** The official clang and lld binaries are statically linked, about
100 MB or more each uncompressed, plus `dolrecomp.exe`. That makes a roughly
150 MB zip, to be measured. A later single recompiler exe that links clang,
lld and DolRecomp as libraries would share one copy of LLVM and cut this down.

**UX.** After extraction, OpenSPMNative checks for
`User-OpenSPM\native\openspm_local_native.dll`. It rebuilds the DLL when it is
missing or when the cache key changes. The key is the `main.dol` and relF
SHA-256 plus the recompiler/bridge version, stored in a stamp next to the DLL.
The rebuild shows a Win32 progress window:

- `recompiler\openspm-recompile.exe --game ... --out ...` runs with piped
  stdout;
- the window parses the existing `[LLVM n/7]` step lines, plus a new
  `objects i/N` line from generation;
- it has a Cancel button, and failures show the log path;
- output goes to a `.partial` folder that is renamed into place, as the wit
  extraction already does.

Build time on a normal PC has not been measured in this repo; Stage 1 must
record it.

### (b) Change the architecture so no per-player compile is needed

- **b1: a generic recompiler run at first launch, with no C compiler.** Split
  the bridge into:
  - a game-independent part, prebuilt and shipped (a static object linked by
    lld, or code moved into `openspm_native_backend.dll`);
  - per-player data that DolRecomp emits as objects or tables: the function
    table by guest address, seam addresses, the dispatch index and the identity
    hash, in place of today's generated headers.

  The per-player step becomes DolRecomp codegen plus an lld link. Both can be
  libraries inside one `openspm-recompile.exe`, or inside OpenSPMNative. Python
  can be ported to C++ at the same time. This is the best end state, but it
  touches the bridge, which is about 40 stacked adapter versions, so it is
  weeks of work.
- **b2: JIT or object loading.** Embed LLVM ORC/JITLink in the runtime. DolRecomp
  output is cached as objects on first run and linked into memory at every
  launch, with no DLL and no system linker. The costs:
  - 30-60 MB of LLVM inside the process;
  - seconds of linking at every start;
  - antivirus and W^X heuristics;
  - no PDB, and the current DLL-based tooling (map symbolizer, fail-fast
    debugger) stops working.

  A hand-written COFF loader for DolRecomp's small symbol set is possible, but
  it is a linker by another name. Not recommended now.
- **b3: interpreter or dynarec.** This discards the static-recompilation
  performance work. Rejected.
- **Rejected on legal grounds:** shipping a prebuilt DLL, shipping generated
  headers or objects, or shipping a "patch" or delta against the DOL. All of
  these are game-derived.

### Legal notes

- **Game-derived output stays on the player's PC.** Generated objects and
  headers, the identity header and the DLL are made there, from their files.
  `tools/release/audit_public_candidate.py` enforces this for every release
  folder.
- **Do not bundle wit.** Wiimms ISO Tools decrypts Wii partitions with the
  console key built into it, and it is GPL-2. Shipping it would make OpenSPM
  both a key distributor and subject to GPL source-offer terms. The player
  installs wit. As an alternative, the picker already accepts an extracted
  `sys\main.dol`, for example from Dolphin's "Extract Entire Disc".
- **DolRecomp's license must be confirmed before shipping `dolrecomp.exe`.**
  Its `cpu.c` is also compiled into every player's DLL. If upstream has no
  license, ask the author (ExpansionPak) for one. This blocks Stage 3.
- **Toolchain licenses:** LLVM/clang/lld are Apache-2.0 WITH LLVM-exception.
  Python is PSF. mingw-w64, if used, is ZPL/public domain.
- **Runtime licenses:** Aurora (MIT), Dawn (BSD-3-Clause), SDL3 (Zlib),
  abseil (Apache-2.0), fmt (MIT), xxHash (BSD-2-Clause), Dear ImGui (MIT) and
  DXC (`dxcompiler.dll`, NCSA/MIT). `dxil.dll` is a Microsoft binary under its
  own redistribution terms, so ship its license text. For the MSVC runtime,
  either link the host statically (`CMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded`,
  recommended for the release) or bundle `vcruntime140*.dll` under Microsoft's
  redistributable terms.
- **OpenSPM has no LICENSE file yet.** Choosing one is a release blocker, and
  the packager refuses to build a public zip without it. The choice must be
  compatible with MIT/BSD/Apache-2.0 dependencies. Apache-2.0 is incompatible
  with GPL-2-only.
- **Branding:** no Nintendo logos, box art or game text in the release.
  "Super Paper Mario" is used only to describe what the port runs.

## 3. Recommendation

Do **(a) with the freestanding module (way 4)** now, because it reuses the
proven pipeline unchanged except for the compiler. Evolve toward **b1** once the
zip ships.

| Stage | Work | Effort |
| --- | --- | --- |
| 0 (done) | `tools/release/make_release_zip.py` assembles an allowlisted, audited release folder and zip of the host runtime. The audit is hardened with content sniffing and PE export checks, and a host test runs in CI. | done |
| 1 | Build the LLVM module with clang-cl + lld-link from the pinned LLVM 20.1.8 archive, still with VS present for the SDK. This proves the objects and the bridge link and run. Record the per-player build time and compare against MSVC `/GL` in the Yold benchmark. **Spike done, opt-in `--toolchain clang` (section 5); needs a Windows run.** | 1-2 days left |
| 2 | Freestanding module: the CRT/Win32 shim or host-API calls, a kernel32 `.def` turned into an import lib by `llvm-dlltool`, and `/NODEFAULTLIB`. Build on a machine or CI runner without VS, including a Linux cross-link check in CI. **Spike done, opt-in `--toolchain clang-freestanding`, cross-linked on Linux (section 5); needs a Windows run and the payload census.** | 2-4 days left |
| 3 | **Zip + exe milestone.** CI builds `dolrecomp.exe`. The release gains `recompiler\`: dolrecomp, the clang/lld subset and builtin headers, embeddable Python, the `native_bridge` scripts and `tools/content` (the build finds relF's character-id patches with it, docs/CHARACTER-IDS.md). It has one entry point with no CMake, git or network. The release drops the known-good C module: the expected `main.dol` SHA-256 becomes a constant (a hash is not game code) and the transactional swap stays dev-only. OpenSPMNative gets the first-run progress window and the cache stamp. The packager allowlist and license table cover the recompiler. Needs the DolRecomp license and the project license. | 5-8 days |
| 4 | b1: prebuilt bridge plus DolRecomp-emitted tables, DolRecomp + lld as libraries in one recompiler exe, and the Python steps ported. Drops clang and Python from the zip and shrinks it. | 2-4 weeks |

The .cmd developer flow stays as it is throughout. The release path is additive.

## 4. The packager (Stage 0)

```
python tools/release/make_release_zip.py ^
    --build-dir .openspm\m14\windows-candidate ^
    --license-search .openspm\m14\deps\aurora ^
    --license-search .openspm\m14\windows-candidate ^
    --output dist\OpenSPM --zip
```

- **Reads from the build tree** only `OpenSPMNative.exe` and `runtime\`. The
  candidate's `User-OpenSPM\` (game, native module, saves) and any player
  `OpenSPM.ini` are never read.
- **In `runtime\`**, the required backend and renderer DLLs are taken, along
  with allowlisted third-party DLLs (SDL3, Dawn, DXC, the MSVC runtime).
  Game/private files there are reported as "rejected (never packaged)" and
  build by-products (`.pdb`, `.lib`, …) are skipped. Any other file stops the
  build until it is allowlisted with its license.
- **From the repo:** `OpenSPM.ini.example`, copied to both `OpenSPM.ini` and
  `OpenSPM.ini.example`, and refused if it carries a `[Game]` path. Also
  `docs\PLAYING.md`, `TEXTURE-PACKS.md`, `MODDING.md` and `MOD-PACKAGES.md`, and
  the project `LICENSE`, which is required.
- **Licenses** are found by component folder name under `--license-search`, or
  given with `--license NAME=PATH`. The packager writes `licenses\*.txt` and
  `THIRD-PARTY-NOTICES.txt`, and it fails when a bundled component has no
  license text.
- **Output** also includes `README.txt` (quick start), `MANIFEST.txt` (SHA-256
  of every file) and empty `mods\` and `wit\` folders.
- **Audit:** the finished folder goes through `audit_public_candidate.py
  --require-layout`. The audit rejects:
  - disc, DOL, REL, BIN, ARC, TPL and related extensions, and `.obj/.o/.bc/.ll`;
  - private folder names (`.openspm`, `User-OpenSPM`, `sys`, `files`,
    `generated`, `texturepacks`, …);
  - content sniffed as a Wii/GC disc header, WBFS/WIA/RVZ, Yaz0/U8/RARC/TPL, or
    a DOL or REL header;
  - `.exe/.dll` files that are not PE images;
  - **any PE exporting `openspm_native_module_query`**, which catches the local
    module under any name;
  - key-sized `*key*` files, files over 256 MB, and `.ini` files that carry a
    player path.

  The zip is written only after the audit passes, and its file list is compared
  with the audited folder.
- **Test:** `python3 tools/release/test_make_release_zip.py`. It builds a fake
  output tree that mixes real release files with every forbidden kind, including
  disguised payloads, and asserts that they are rejected.

## 5. Stages 1-2 spike: clang-cl + lld-link, then freestanding

Everything here was done on Linux with Ubuntu clang/lld **18.1.3**, not the
pinned 20.1.8. The inputs were real bridge sources, DolRecomp `cpu.c` at the
pin, and locally derived seam/GX/identity headers. `generated.h` came from the
C backend, because no LLVM-backend tree was available in the sandbox; the
bridge compiles against it the same way (`check_bridge_compile.py`). Nothing
game-derived is in the repo. Windows has not run any of it yet.

### Pinned LLVM has the whole toolchain

`prepare_dolrecomp_llvm.py` unpacks the official
`clang+llvm-20.1.8-x86_64-pc-windows-msvc.tar.xz` to
`.openspm\m14\llvm-candidate\dolrecomp-toolchain\llvm-20.1.8-windows-x64`.
`toolchain.json` records `llvm_dir` = `<package>\lib\cmake\llvm`, so the bin
directory is `llvm_dir.parents[2] / "bin"`. The archive listing (streamed here)
contains everything needed:

| File | Size |
| --- | --- |
| `bin\clang-cl.exe` (same binary as `clang.exe`) | 101.9 MB |
| `bin\lld-link.exe` | 67.5 MB |
| `bin\llvm-lib.exe`, `bin\llvm-dlltool.exe` (one binary) | 16.9 MB |
| `bin\llvm-nm.exe`, `bin\llvm-readobj.exe` | 17.7 / 6.9 MB |
| `lib\clang\20\include\` (stdint, stdatomic, intrin, xmmintrin, ...) | |
| `lib\clang\20\lib\windows\clang_rt.builtins-x86_64.lib` | 166 KB |

`run_m14_llvm_candidate.py` resolves these with `pinned_llvm_bin()`, and
`--llvm-bin` overrides it.

### Today's MSVC build: flags and dependencies

The compile goes through CMake + Ninja (`tools/native_bridge/CMakeLists.txt`,
Release):

- **Compile flags.** `cl /nologo /DWIN32 /D_WINDOWS /O2 /Ob2 /DNDEBUG /std:c11 -MD /O2
  /experimental:c11atomics /GL`. Then `-TC` for the `.c.in` bridge (source
  `LANGUAGE C`), plus `-DOPENSPM_M14_LLVM_CHUNK_INSTRUCTIONS=`,
  `-DOPENSPM_M14_LLVM_STATE_IN_MEMORY=`, `-DOPENSPM_M14_HEAVY_DIAGNOSTICS=` and
  `-Dopenspm_local_native_EXPORTS`.
- **Includes:** `include/`, `tools/native_bridge/`, the generated dir (`generated.h`,
  `openspm_local_v19_llvm_seams.h`, `openspm_local_gx_dl_semantics.h`), the
  staged `cpu_runtime/` (`cpu/cpu.h`, `common/types.h`) and `identity/`.
- **Translation units:** the bridge (`legacy_c_adapter_v42.c.in`, which includes
  the whole v4..v42 chain, about 636 KB) and the staged `cpu.c`. The about 14k
  `chunks/*.o` are `EXTERNAL_OBJECT`s from the validator manifest.
- **Link:** `link /dll /LTCG /INCREMENTAL:NO` with an objects response file. It
  adds CMake's default `kernel32 user32 gdi32 winspool shell32 ole32 oleaut32
  uuid comdlg32 advapi32`, of which only kernel32 is used, and the `/MD` CRT
  (`vcruntime140` + UCRT) via defaultlib directives.
- **Headers used:**
  - bridge: `stdint stddef stdlib string stdio stdatomic` and `windows.h`
    (`WIN32_LEAN_AND_MEAN`);
  - `cpu.c`: `stdio stdlib string math fenv stdbool`, and `intrin.h` +
    `xmmintrin.h` under `_MSC_VER`;
  - C-backend `generated.h`: `string.h math.h`.

**Symbol census.** Measured with `llvm-nm -u` on clang MSVC-mode objects and
mingw `-O2` objects:

| From | Uses |
| --- | --- |
| bridge, CRT | `snprintf` (131 sites; formats `%X %llu %u %s %d %.Nf %llX %lld %p %lu`), `calloc`, `free`, `getenv`, `strtoull`, `strcmp`, `memcmp`, `memset`, `memcpy`, `strlen`, `strcpy` |
| bridge, kernel32 | `AddVectoredExceptionHandler`, `RemoveVectoredExceptionHandler`, `CloseHandle`, `CreateEventW`, `SetEvent`, `WaitForSingleObject`, `CreateThread`, `GetCurrentThreadId`, `GetEnvironmentVariableA`, `GetModuleHandleExW`, `VirtualQuery`, `Sleep` |
| bridge, intrinsics | `Interlocked{CompareExchange,Exchange,ExchangeAdd,Increment}`, `YieldProcessor` |
| `cpu.c` | `calloc`, `free`, `fprintf(stderr)`, `fesetround`, `fma`, `fmod`, `ldexp`, `floor`, `ceil`, `trunc`, `memset`, `_fltused`; `_ReadWriteBarrier`/`_mm_mfence` (intrinsics) |
| payload (from DolRecomp's LLVM backend source, **not yet measured**) | `ppc_*` helpers in `cpu.c`, `openspm_m14_*` in the bridge, `fma` (constrained FMA libcall), `memcpy`/`memset`, `__chkstk` (frames over 4 KiB), `_fltused`. SJLJ escapes use `llvm.eh.sjlj.*` intrinsics on x86 (`intrinsic_escapes_`), so no `_setjmp`/`longjmp` import is expected |

No CRT object crosses `native_api.h`. `fprintf` goes to the process's UCRT
`stderr`, as it does today.

### Stage 1: `--toolchain clang` (opt-in, still uses the VS CRT/SDK)

- **Wiring.** `run_m14_llvm_candidate.py --toolchain clang`, also available as the 5th
  argument of `M14-LLVM-UPDATE-RUN.cmd` or the 2nd of `M14-VANILLA-RUN.cmd`,
  passes `--toolchain clang --llvm-bin <pinned>\bin` to
  `build_local_llvm_module.py`. That configures a separate CMake tree,
  `local-module-build\module-llvm-clang`, with:

  ```
  -DOPENSPM_LOCAL_TOOLCHAIN=clang -DCMAKE_C_COMPILER=<bin>/clang-cl.exe
  -DCMAKE_LINKER=<bin>/lld-link.exe -DCMAKE_AR=<bin>/llvm-lib.exe
  ```
- **The default stays MSVC.** It adds no arguments and uses the same `module-llvm`
  tree. `test_llvm_native_pipeline.py` asserts this.
- **What `CMakeLists.txt` does.** Its clang branch comes before `elseif(MSVC)`,
  because clang-cl also sets `MSVC`.
  - It requires `clang-cl` (`CMAKE_C_COMPILER_FRONTEND_VARIANT=MSVC`) and
    `lld-link`.
  - It drops `/experimental:c11atomics`, since clang has `<stdatomic.h>`.
  - It adds `/clang:-fno-strict-aliasing`. clang-cl already defaults to this
    for MSVC targets, and cl has no TBAA.
  - It puts `-flto=thin` on the bridge and `cpu.c` only, the same scope as
    `/GL`. The payload objects are final code in both cases.
- **Compile line** (from the Linux cross build; only the freestanding extras
  differ):

  ```
  clang-cl /nologo <defines> <includes> -TC /DWIN32 /D_WINDOWS /O2 /Ob2 /DNDEBUG /std:c11 -MD
           /O2 /clang:-fno-strict-aliasing -flto=thin -c legacy_c_adapter_v42.c.in
  lld-link /nologo <chunks/*.o> bridge.obj cpu.obj /out:openspm_local_native.dll
           /implib:... /dll /machine:x64 /INCREMENTAL:NO <standard libs>
  ```
- **Source compatibility is clean.** The whole bridge chain and `cpu.c` compile
  in MSVC mode (`_MSC_VER` defined, `-fms-extensions`) with zero errors, for
  every combination of `HEAVY_DIAGNOSTICS` and `STATE_IN_MEMORY`. The only
  warning is `-Wcomment`. The only MSVC-isms are `#pragma
  push_macro/pop_macro`, `__declspec(dllexport)` and cpu.c's
  `_ReadWriteBarrier`/`_mm_mfence`, and clang supports all of them.
  `check_bridge_compile.py` now runs this compile too
  (`clang-msvc-freestanding`).
- **Not proven here.** No MSVC headers or libs are available in the sandbox:
  `xwin` would mean accepting Microsoft's license, so it was not used. The
  hosted configure therefore stops at CMake's compiler probe on Linux, as
  expected. Under VsDevCmd, `INCLUDE`/`LIB` supply them, which is the same
  environment the MSVC build uses today.

### Stage 2: `--toolchain clang-freestanding` (no MSVC CRT, no Windows SDK)

**Files (all OpenSPM-authored, no Microsoft header content):**

- `tools/native_bridge/m14_freestanding_crt.h`: the whole CRT + Win32
  surface the module uses, and nothing more.
  - C prototypes, with `__declspec(dllimport)` on UCRT and kernel32 functions.
  - Builtin-based `isnan`/`fpclassify`/`INFINITY`.
  - `FE_*` rounding values.
  - The Win32 types used by the VEH/diagnostic thread, with
    `_Static_assert`s pinning the x64 layouts of `EXCEPTION_RECORD` (152) and
    `MEMORY_BASIC_INFORMATION` (48, checked by `VirtualQuery`).
  - `Interlocked*` mapped to clang's MSVC builtins.

  A new CRT/Win32 call in the bridge fails at compile time as an undeclared
  identifier.
- `tools/native_bridge/freestanding/include/{stdio,stdlib,string,math,fenv,windows,setjmp,malloc}.h`:
  one-line wrappers that include the shim. `setjmp.h` and `malloc.h` exist
  because clang's hosted `intrin.h`/`mm_malloc.h` include them.
- `tools/native_bridge/m14_freestanding_crt.c` provides:
  - `mem*`/`str*` (`rep movsb`/`stosb`);
  - `snprintf`/`vsnprintf`/`fprintf`, which forward to UCRT
    `__stdio_common_vsprintf/vfprintf` with the same option bits as MSVC's
    inline `snprintf`, so the output is identical;
  - `fesetround`/`fegetround`, on MXCSR and the x87 control word;
  - `__chkstk` (page probe, rsp unchanged);
  - `_fltused`;
  - a no-op `_DllMainCRTStartup`.

  It is compiled as a **native object, never ThinLTO bitcode**: codegen can
  emit `memset`/`__chkstk` calls after LTO has dropped bitcode definitions.
- `m14_freestanding_kernel32.def` (12 names) and `m14_freestanding_ucrtbase.def`
  (17 names). The pipeline test asserts that they match the header's dllimport
  declarations exactly.

**Build.** CMake runs `llvm-dlltool -m i386:x86-64 -d <def> -l <lib>` for both,
compiles with `/X /Zl /GS- -imsvc freestanding/include` (ignore `%INCLUDE%`,
no defaultlib directives, no security cookie), and links with `/NODEFAULTLIB`
and `CMAKE_C_STANDARD_LIBRARIES` cleared. `build_local_llvm_module.py` also
passes `-DCMAKE_TRY_COMPILE_TARGET_TYPE=STATIC_LIBRARY`, so the compiler probe
does not link a CRT exe.

**Proven on Linux (clang/lld 18):**

1. `cmake -G Ninja` of `tools/native_bridge` with
   `-DCMAKE_SYSTEM_NAME=Windows` and `OPENSPM_LOCAL_TOOLCHAIN=clang-freestanding`,
   against a one-object fake LLVM tree whose COFF object stubs the payload
   symbols. It configures, passes the validator, builds
   `openspm_local_native.dll` (148 KB), and its import table is exactly
   `kernel32.dll` + `ucrtbase.dll`. The generated link line:

   ```
   lld-link /nologo chunks/payload_stub.o legacy_c_adapter_v42.c.in.obj cpu.c.obj
            m14_freestanding_crt.c.obj /out:openspm_local_native.dll /dll /machine:x64
            /INCREMENTAL:NO /NODEFAULTLIB openspm_freestanding_kernel32.lib
            openspm_freestanding_ucrtbase.lib
   ```
2. Direct commands, bridge + `cpu.c` ThinLTO: compile + link took about 2 s. The
   only unresolved symbols were the 218 payload ones (`func_*`, `mem_*`,
   `dolrecomp_call_depth`, `ppc_host_call`). Nothing from the CRT or SDK was
   unresolved.
3. A tiny DLL using `snprintf %.3f`, `floor/fmod/fma`, `fesetround`,
   `CreateThread`, a 9000-byte frame (`__chkstk`) and `<stdatomic.h>` links the
   same way. `test_llvm_native_pipeline.py` repeats this on every Linux run and
   skips it on Windows, so step 2/7 on a player PC stays source-only.

### Blockers and open risks

- **LTCG vs ThinLTO.** Same scope (bridge + `cpu.c`), but a different optimizer.
  The bridge's hot paths (dispatch, WPAR, host calls) must be A/B'd in the Yold
  benchmark before `clang` can become the default. ThinLTO needs lld-link, and
  CMake enforces that. Full LTO (`-flto`) is a one-word change if ThinLTO
  loses.
- **Pinned 20.1.8 vs sandbox 18.1.3.** The flags used (`/X /Zl /GS- -imsvc
  /clang: -flto=thin`, `llvm-dlltool -m i386:x86-64`) all exist in both, but
  the first Windows run is the real check.
- **Payload census.** On Windows, run `<bin>\llvm-nm -u --no-sort` over
  `chunks\*.o`, drop the symbols defined by the bridge and `cpu.c`, and list
  what is left. Anything that is not `memcpy`/`memset`/`fma`/`__chkstk`/`_fltused`
  must be added to the shim. The freestanding link reports it as undefined in
  any case.
- **UCRT exports.** mingw's `libucrtbase.a` omits `floor/ceil/fmod/ldexp`
  because mingw ships its own copies. `ucrtbase.dll` should export them, but
  check with `llvm-readobj --coff-exports C:\Windows\System32\ucrtbase.dll`.
  Plain calls go through dlltool's thunks, and `__imp_` calls bind directly.
- **Never executed.** `__chkstk`, `fesetround` and the `__stdio_common_*`
  wrappers have been compiled and linked here but never run (no Windows or
  Wine). The first freestanding launch should compare the log text against the
  MSVC build.
- **Hot `memcpy`.** `rep movsb` costs a few tens of cycles to start. Check that
  the payload does not call `memcpy` for small copies in hot loops. If it does,
  use a small-size fast path.
- **Toolchain size in the zip (Stage 3).** clang-cl 102 MB + lld-link 68 MB +
  dlltool 17 MB + builtin headers, uncompressed.

### Stage 3 piece: the module without CMake

`tools/native_bridge/direct_module_build.py` builds the clang-freestanding
module with clang-cl, lld-link and llvm-dlltool alone: the two import
libraries, five compiles (ThinLTO on the bridge and `cpu.c`) and one
response-file link (around 14k payload objects exceed the Windows command
line). `build_local_llvm_module.py --toolchain clang-freestanding --llvm-bin
<bin> --no-cmake` uses it instead of configuring CMake. This is the build a
release runs, since the zip carries no CMake, Ninja or Visual Studio.

- `test_direct_module_build.py` configures this directory's CMake project for
  a Windows cross build and checks that every compile command and the link
  flags match the driver's, so the two routes can not drift. It also compiles
  the runtime sources with the driver's flags.
- On Linux (clang 18), the real bridge and `cpu.c`, compiled against headers
  derived from the player's DOL and linked with the driver's commands, gave a
  DLL importing only `kernel32.dll` and `ucrtbase.dll`. The only undefined
  symbols were the 218 payload functions the DolRecomp objects provide.
- Not yet run on Windows with a real payload.

### Stage 3 piece: the cache stamp

Every module build writes `openspm_local_native.stamp.json` next to the DLL
(`tools/native_bridge/module_stamp.py`). It records:

- the `main.dol` SHA-256;
- the DolRecomp pin;
- the OpenSPM and native ABI versions;
- one hash over every OpenSPM file that goes into the module (the bridge
  chain, the freestanding CRT, the transforms, the public headers, the audio
  sources, the host-target / hook / code-patch recipes; tests excluded, line
  endings normalised);
- the toolchain, arch and options.

`module_stamp.status(dir, expected(...))` answers current, or which of those
changed ("OpenSPM's module sources changed"), so the first-run step can
rebuild a stale module and say why. The developer .cmd flow ignores it
(Ninja rebuilds incrementally anyway); only the release reads it.

### Stage 3 piece: the first-run entry point

`tools/native_bridge/prepare_game.py` is the one step a release runs after
the exe's picker has extracted the game (`User-OpenSPM\game`):

1. It checks that `main.dol` is the supported disc (a SHA-256 constant, no
   known-good C module needed).
2. It asks the cache stamp whether the installed module is current, and
   stops there when it is.
3. Otherwise it runs `build_local_llvm_module.py --toolchain
   clang-freestanding --no-cmake` with the release folder's `recompiler\`
   tools (`dolrecomp.exe`, `dolrecomp-runtime\src`, `llvm\bin`) and installs
   into `User-OpenSPM\native`, where OpenSPMNative already looks.
4. It checks the result against the stamp.

It prints `OPENSPM-PROGRESS <step>/<steps> <text>` and one `OPENSPM-RESULT
ok|error <text>` line for the exe's progress window, which is still to be
written, like CI building `dolrecomp.exe` and the packager's `recompiler\`
entries. Tested with a fake build (test_prepare_game.py); not run with the
real tools yet.

### Trying it on Windows

From the repo root (VS Build Tools still present; `clang` uses its CRT/SDK):

```
M14-LLVM-UPDATE-RUN.cmd AUTO 256 1 0 clang
M14-LLVM-UPDATE-RUN.cmd AUTO 256 1 0 clang-freestanding
M14-VANILLA-RUN.cmd AUTO clang
```

Without CMake (Stage 3; the pinned LLVM's `bin` folder, any prompt):

```
python tools\native_bridge\build_local_llvm_module.py ... --toolchain clang-freestanding ^
    --llvm-bin <pinned LLVM>\bin --no-cmake
```

Or build only, without the host rebuild or launch (from a VS x64 prompt):

```
python tools\native_bridge\run_m14_llvm_candidate.py --cmake cmake --ctest ctest ^
    --skip-host-build --build-only --chunk-instructions 256 --state-in-memory-mode 1 ^
    --toolchain clang
```

The DLL lands in `.openspm\m14\llvm-candidate\native-output\`. To check it,
run `llvm-readobj --coff-imports` on it: `clang-freestanding` must show only
`kernel32.dll` and `ucrtbase.dll`. The log prints `module_toolchain=`. For the
Stage 1 build-time number, time step `[LLVM 5/7]` against an MSVC run. The
transaction restores the known-good C DLL exactly as it does with MSVC.
