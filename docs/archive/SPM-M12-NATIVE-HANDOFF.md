# M12 native display and helper checkpoint

Date: 2026-09-08. Base: M11D2 `94a3d81f284a7e7e30f0beb1ab334b43a8100236`.
Goal remains non-stretched ultrawide and rendering above 60 FPS without speeding
simulation. This checkpoint implements an ultrawide candidate and one native
hot-function specialization. It does **not** implement high-refresh rendering.

## Concrete changes

`native/display.c` owns horizontal projection policy without Dolphin or Wii
headers. `native/r8pe01_display.c` adapts it to the existing renderer.

The old M11 injection only changed arguments in camDraw's conditional matrix
rebuild path. Exact generated source shows camera initialization and camera
callbacks also build matrices. The unbuilt M11E entry patch gated on the same
single caller, so it did not cover those other construction paths either.

M12 instead adjusts the compact projection packet at `GXSetProjection`, after
the source matrix was copied into GX state. Exact entry is `0x80292164`; the
hook is at `0x802921B8`, before the projection-dirty flag. It only accepts
camDraw's `LR=0x80055674`, checks `r3 == r29 + 0x94`, and checks camera ID/type.
Thus camera construction paths converge before the override. Arbitrary effect
projections remain unchanged. The input camera matrix and CPU state stay intact;
two horizontal coefficients in GX's cached projection change. This is rendering
state, **not a claim that every gameplay-readable guest byte remains unchanged**.

Both perspective and orthographic horizontal scale and offset are multiplied
by source-aspect / target-aspect. Vertical and depth coefficients are untouched.
Orthographic cameras 0, 1 and 13 use camera 11's stock display mode to retain a
centered safe area. Live visual validation is still required, particularly
game-side culling, effects, backgrounds, HUD and projection reuse.

`native/r8pe01_strcmp.c` specializes the exact generated strcmp entry at
`0x8025C9C8`. It uses local scalar state and direct bounded reads only for the
same cached/uncached MEM1 mappings already used by the pinned module CPU runtime.
It does not extend that mapping policy, bypass dispatch SMC checks, introduce
cross-chunk calls, or cache mutable strings. Unmapped/edge addresses and searches
exceeding its bounded span decline without changing state. Original generated
code handles the decline and all mid-function reentries.

Exact scratch GPRs, CR, XER carry, CTR, result magnitude, cycle charges and all
three loop-yield boundaries are retained. `OPENSPM_NATIVE_HELPERS=0` disables
the replacement; environment is read when the descriptor is obtained, not on
every dispatch. Normal helper execution does not log.

## Validation completed locally

- Recovered source ZIP matches Git blob `3f91b2e61f25321cb0534ef52054f96c3a904efd`.
- 600 projection comparisons execute original and patched generated SDK code.
  CPU state and camera bytes match; only two horizontal packet words may differ.
  Horizontal pixel scale remains invariant at equal viewport height. Invalid
  parameters, Auto, stock ratio, unrelated caller, both projection types and
  all 15 camera IDs are covered.
- 100,000 strcmp cases compare complete CPU state to the original generated
  function, including 55,184 yields. Explicit boundary/long-search declines
  leave state untouched. Bytes include high-bit values and embedded terminators;
  all source alignments and cached/uncached MEM1 combinations are exercised.
- Repeated-prefix 2,000,000-call host microbenchmark was about 4.8x faster in
  the initial GCC run. This is not a game FPS measurement or Windows result.
- Full Linux module: all 218 chunks compile and descriptor probe passes
  `abi=3 cpu=3 size=3528 game=R8PE01 entry=80006124 code=3 smc=27 chunks=218 rel=1`.
- Linux module SHA256:
  `8b56f56cd445dfe259094937d5df9f1956eddc5ad92412cc3764f932449a67b6`.
- Repeat build reuses all 223 objects. Original generated header is unchanged;
  only chunks 144 and 158 differ from M10. Preserve the incremental directory.
- Linked-module integration also passes: all 600 projection cases call the
  actual `func_80291940` in the complete shared library, and 20,000 string cases
  call `func_80259940` with the helper on/off, including loop yields and long
  searches that decline to the original generated implementation.

## Published checkpoint and Windows build

Isolated branch: `m12-native-display`. Implementation commit:
`8e274f57f3e8a15d9c2c99facd506f270a19f6e4`.
MSVC test-harness fix: `6f77505a10a85b261e771c74f5d4285272070852`.
Windows candidate run: `34176078842`, **green**, 2026-09-08 01:21 UTC.
Artifact `10037372383`: `spm-m12-native-display-windows-runtime`.
Downloaded ZIP and both packaged binary checksums were verified locally.

| File | SHA256 |
| --- | --- |
| Windows ZIP | `29c72d6b069617c14feb92ac10fa313e4fafa033c124eaad9307f855d1f99d75` |
| moderngekko-run.exe | `c953b63a436b1259797ca07a907d0b905b2459c339796e5e308354358f6722ba` |
| gR8PE01_recomp.dll | `f50dea152b0fff11c6ffdc30555ef33b8cb92828363a863f45a14d0f4a6ec4ce` |

Durable Windows candidate: `openspm-m12-windows-runtime.zip`, file identity
`libfile_5d69a7c8e37c81919936b5ec600abfb1`. The bundle includes PDBs, provenance,
SHA256SUMS, tests/build logs and launchers. MSVC microbenchmark: 158 ms original,
44 ms native, 3.59x for 2,000,000 comparisons; not a measured game FPS gain.

Durable Linux build cache: `openspm-m12-linux-build-checkpoint.tar.gz`, identity
`libfile_cef25e1754c88191832ed863c884ecb0`. Extract in `spm-recomp/` to restore
the completed module, all incremental objects/fingerprints, ABI3 test runtime,
test binaries and logs. Recover exact source as above; source is not in this
cache archive. Existing local paths remain preserved. Archive SHA256:
`08e924fea1a6610d8e51eebb01855b2ee22baddb06b5a36fcdec6733f82a1230`.

The first run `34175763848` failed in isolated test linking, before the DLL
build. MSVC retained the unused generated dispatcher's static tables, requiring
217 unrelated chunk symbols. The isolated tests now retain the exact generated
math helpers and omit that unused dispatcher. Production generated headers and
the DLL are unchanged by this harness fix. Both differential suites then passed
under MSVC in run `34176078842`, which proceeded to compile the complete DLL.
Linux and Windows visual/gameplay acceptance are distinct from these checks.

## Working paths and reproduction

Workspace root: `/workspace/scratch/207a63e772ee`.
Project scripts: `spm-recomp/`. Exact source: `spm-recomp/m10/`.
Persistent module objects: `spm-recomp/build/module-linux/`.
Differential test objects: `spm-recomp/build/native-tests/`.
Existing `ModernGekko/`, `RecompCore/` and their earlier modifications were
preserved. No earlier host build directory survived in this workspace.
Local tools include GCC; CMake and a complete host runtime build are unavailable.

From fresh M10 extraction into `m10/`, apply `ci/m12_native_display.py`, then
`ci/m12_native_strcmp.py`. Do not apply the M11 module patch or M11E entry patch.
Prepare ABI3 with `m10/scripts/prepare_dolrecomp_cpu.py --source ../DolRecomp
--dest build/native-tests/abi3` (use the actual pinned source root).
Then run `ci/test_m12_display.py`, `ci/test_m12_strcmp.py`, and
`ci/build_m12_local.py`, then `ci/test_m12_linked.py` for the full Linux module
integration checks. Scripts resolve their project root from their own path.

Windows workflow: `.github/workflows/build-m12-native-display.yml`.
It runs the differential tests with MSVC first, then compiles the module with
sccache and symbols, and reuses the exact M11D2 EXE/Sys from run `34156272495`.
It does not spend another frontend rebuild. The first green bundle's sccache
reported 224 non-cacheable calls (`multiple input files`). The small probe run
`34176668978` found the cause: Git Bash rewrote `/O2` inside CMake's flag string
into `C:/Program Files/Git/O2`, creating bogus compiler inputs. MSVC ignored
those bogus inputs and still used the separate target `/O2`, so the candidate
remains an optimized build, but nothing was cached.

Fix commit `899635a6e09784402451c93375aee6a85665b443` excludes the CMake compiler
and linker flag values from MSYS argument conversion. Probe run `34176780038`
then verified the exact production-style command: **2 requests, 1 miss, 1 hit,
0 non-cacheable calls**. It only compiles one small object twice. The production
workflow has the same fix; its next full build must populate the full cache.
No additional game DLL rebuild was triggered for this build-system fix. Keep
testing the exact candidate from run `34176078842` until gameplay evidence
justifies another module change. Linux's 223-object incremental reuse is also
verified and its cache is saved above.

## Next acceptance and architectural work

See `ci/M12-TESTING.md`. User is the Windows tester. Preserve old saves/binaries,
compare helper on/off at the same resolution/aspect, and capture 16:9 versus
21:9 at equal viewport height, including 2D/3D flip and dialogue. Retain logs.
Do not call this visually verified or claim full-speed gameplay before that.

Rendering above 60 requires a separate presentation path. Raising emulation
speed or replaying an unchanged completed frame does not satisfy that goal.
The current inherited renderer receives mutable GX state, draw commands and
framebuffer operations. A native renderer needs stable object/draw identities,
immutable render snapshots and an audited treatment of animation, camera cuts,
effects and framebuffer copies before between-tick interpolation is trustworthy.
The new display policy is reusable there, but no snapshot/interpolation consumer
has been implemented. Next expensive work should follow real scene profiles;
do not claim a 4.8x whole-game improvement from the strcmp microbenchmark.
