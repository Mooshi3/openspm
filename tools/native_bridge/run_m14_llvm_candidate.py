#!/usr/bin/env python3
"""Build, stage, launch, and restore the opt-in standalone LLVM M14 candidate.

This harness is intentionally transactional. It builds the LLVM-generated
OpenSPM module in a separate .openspm tree, validates it, repairs the candidate
staging slot from the independently built known-good C module when a prior
interrupted LLVM launch left that slot stale, swaps the LLVM DLL in only for one
launch, and restores the known-good C DLL in a finally block when control
returns to this process.

DolRecomp/LLVM are build-time tools only. The launched process is the same
OpenSPMNative + OpenSPM backend + Aurora renderer candidate used by the normal
M14 runner; no Dolphin/ModernGekko/RecompCore runtime is started.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DEFAULT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

import build_relf_codegen_dol  # noqa: E402
import derive_v19_llvm_seams  # noqa: E402
import relf_input  # noqa: E402


IDENTITY_SHA_RE = re.compile(
    r"OPENSPM_LOCAL_SOURCE_SHA256\s*\[\s*32\s*\]\s*=\s*\{(?P<body>.*?)\};",
    re.DOTALL,
)
HEX_BYTE_RE = re.compile(r"0x([0-9a-fA-F]{2})")
EXPECTED_CODEGEN_PATCH = "llvm-inline-wpar-indirect-budget-v20-dev113-chunk256-yold"


NATIVE_MODULE_TOOLCHAINS = ("msvc", "clang", "clang-freestanding")
DEFAULT_NATIVE_MODULE_TOOLCHAIN = "msvc"


def pinned_llvm_bin(info: dict, override: Path | None) -> Path:
    """Locate clang-cl/lld-link in the LLVM package DolRecomp was built with.

    toolchain.json records llvm_dir = <package>/lib/cmake/llvm. The official
    clang+llvm-20.1.8-x86_64-pc-windows-msvc archive that
    prepare_dolrecomp_llvm.py bootstraps is a full toolchain, so its bin/
    carries clang-cl.exe, lld-link.exe, llvm-lib.exe and llvm-dlltool.exe next
    to the developer libraries. Using that bin keeps the bridge and the
    payload objects on one LLVM version.
    """
    if override is not None:
        llvm_bin = override.resolve()
    else:
        llvm_dir = info.get("llvm_dir")
        if not llvm_dir:
            raise RuntimeError("toolchain manifest has no llvm_dir; pass --llvm-bin")
        llvm_bin = Path(llvm_dir).resolve().parents[2] / "bin"
    missing = [
        name for name in ("clang-cl", "lld-link", "llvm-lib")
        if not (llvm_bin / f"{name}.exe").is_file() and not (llvm_bin / name).is_file()
    ]
    if missing:
        raise RuntimeError(
            f"pinned LLVM bin is missing {', '.join(missing)}: {llvm_bin} "
            "(pass --llvm-bin to a full LLVM 20 toolchain)"
        )
    return llvm_bin


def run(command: list[str], *, cwd: Path | None = None) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=str(cwd) if cwd else None, check=True)


def verify_renderer_perf_candidate(candidate: Path) -> Path:
    """Prove CMake will compile the exact dev89+dev94+dev92 Aurora source."""
    cache = candidate / "CMakeCache.txt"
    if not cache.is_file():
        raise RuntimeError(f"candidate CMake cache is missing after configure: {cache}")
    aurora_value = None
    for line in cache.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("OPENSPM_AURORA_SOURCE_DIR:PATH="):
            aurora_value = line.split("=", 1)[1].strip()
            break
    if not aurora_value:
        raise RuntimeError(
            "configured candidate does not expose OPENSPM_AURORA_SOURCE_DIR"
        )
    aurora = Path(aurora_value).resolve()
    cp = aurora / "lib" / "gx" / "command_processor.cpp"
    regs = aurora / "lib" / "gx" / "regs.cpp"
    rec_hpp = aurora / "lib" / "gfx" / "recording.hpp"
    rec_cpp = aurora / "lib" / "gfx" / "recording.cpp"
    for path in (cp, regs, rec_hpp, rec_cpp):
        if not path.is_file():
            raise RuntimeError(f"configured Aurora source is incomplete: {path}")

    cp_text = cp.read_text(encoding="utf-8")
    regs_text = regs.read_text(encoding="utf-8")
    rec_hpp_text = rec_hpp.read_text(encoding="utf-8")
    rec_cpp_text = rec_cpp.read_text(encoding="utf-8")
    required_cp = (
        "M14 raw guest-array stage-head index + scan-plan active",
        "M14 raw guest-array pooled exact snapshots single-copy append active",
        "M14 raw guest-array stage timing probe retired for normal play",
        "kM14GuestArrayStageBuckets = 2048u",
        "previousBucketEntry",
        "if (vtxCount == 0u) continue;",
        "std::vector<u8> sM14GuestArraySnapshotBytes;",
        "u32 snapshotOffset = 0;",
        "u32 snapshotSize = 0;",
        "sM14GuestArraySnapshotBytes.insert(",
        "std::memcmp(sM14GuestArraySnapshotBytes.data() + entry.snapshotOffset",
    )
    missing = [marker for marker in required_cp if marker not in cp_text]
    forbidden = [
        marker for marker in (
            "M14 raw guest-array staging bytes are exact snapshots; duplicate CPU snapshot copy retired",
            "gfx::storage_range_data(entry.range)",
            "std::unordered_multimap<const void*, u32> sM14GuestArrayStageLookup;",
            "M14 raw guest-array stage profile",
            "sM14GuestArrayStageNanos",
            "sM14GuestArrayStageCalls",
            "sM14GuestArrayProfileFrames",
            "profileFinish",
        )
        if marker in cp_text
    ]
    if "const uint8_t* storage_range_data(Range range) noexcept;" in rec_hpp_text:
        forbidden.append("recording.hpp dev91 storage_range_data declaration")
    if "const uint8_t* storage_range_data(Range range) noexcept {" in rec_cpp_text:
        forbidden.append("recording.cpp dev91 storage_range_data definition")

    required_regs = (
        "M14 exact duplicate CP array-base pipeline reuse active",
        "regs[0xA0 + i] = {cp_arraybase, 0, /* alwaysHandle */ true};",
        "const auto dirtyBeforeBase = g_gxState.dirty;",
        "g_gxState.dirty = dirtyBeforeBase;",
    )
    missing.extend(marker for marker in required_regs if marker not in regs_text)
    if "regs[0xA0 + i] = {cp_arraybase, DirtyPipeline, /* alwaysHandle */ true};" in regs_text:
        forbidden.append("legacy CP array-base table still pre-dirties pipeline")

    stage_bucket_decl = (
        "const u32 stageBucket = m14_guest_array_stage_bucket(source);"
    )
    stage_bucket_count = cp_text.count(stage_bucket_decl)
    if stage_bucket_count != 1:
        forbidden.append(
            f"stageBucket declaration count={stage_bucket_count} (expected 1)"
        )
    if missing or forbidden:
        raise RuntimeError(
            "configured Aurora is not the dev89+dev94+dev92+exact-CP-reuse performance candidate; "
            "missing/residue: " + ", ".join(missing + forbidden)
        )
    print(
        "[OpenSPM] Renderer performance candidate proof PASS: dev89 fixed-bucket/scan-plan "
        "+ dev94 CPU-cached exact snapshots + dev92 timing retirement + exact duplicate CP pipeline reuse "
        "are present in configured Aurora.",
        flush=True,
    )
    return aurora


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_identity_sha256(path: Path) -> str:
    """Read the exact source-DOL hash embedded in the known-good C module build.

    The normal local-module builder writes openspm_local_identity.h from the
    player-owned main.dol at build time. That identity is a stronger A/B anchor
    than repo state: the LLVM candidate must use the exact DOL which produced
    the independently built C DLL that will be restored after the transaction.
    """
    if not path.is_file():
        raise RuntimeError(
            "known-good C module identity is missing; run M14-UPDATE-RUN.cmd once first: "
            f"{path}"
        )
    text = path.read_text(encoding="utf-8")
    match = IDENTITY_SHA_RE.search(text)
    if match is None:
        raise RuntimeError(
            "known-good C module identity does not contain OPENSPM_LOCAL_SOURCE_SHA256[32]; "
            "run M14-UPDATE-RUN.cmd once to refresh it"
        )
    values = HEX_BYTE_RE.findall(match.group("body"))
    if len(values) != 32:
        raise RuntimeError(
            "known-good C module identity SHA-256 is malformed: "
            f"expected 32 bytes, found {len(values)}"
        )
    return bytes(int(value, 16) for value in values).hex()


def repair_staged_c_module(staged_dll: Path, known_good_c_dll: Path) -> None:
    """Normalize the candidate staging slot to the independent normal-C build.

    An LLVM launch can be interrupted by a console control event strongly enough
    that Python never regains control to execute the inner finally block. In that
    case the staging slot may still contain the experimental LLVM DLL on the next
    invocation. That is not a reason to rebuild the known-good C module: the
    independent normal-C artifact is already the rollback authority.

    We only repair after the caller has verified that artifact's embedded source
    identity against the current player-owned DOL. The copy is then hash-checked
    before any LLVM build/staging work continues.
    """
    if not known_good_c_dll.is_file():
        raise RuntimeError(
            "known-good C player module cache is incomplete; run M14-UPDATE-RUN.cmd once first: "
            f"{known_good_c_dll}"
        )

    expected_hash = sha256_file(known_good_c_dll)
    needs_repair = not staged_dll.is_file()
    if staged_dll.is_file() and sha256_file(staged_dll) != sha256_file(known_good_c_dll):
        needs_repair = True

    if not needs_repair:
        return

    staged_dll.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(known_good_c_dll, staged_dll)
    if staged_dll.stat().st_size != known_good_c_dll.stat().st_size:
        raise RuntimeError("staged C module repair size mismatch")
    if sha256_file(staged_dll) != expected_hash:
        raise RuntimeError("staged C module repair hash mismatch")
    print(
        "[OpenSPM] Repaired stale/missing staged player DLL from the independently "
        "built known-good C module; no normal rebuild was required.",
        flush=True,
    )


def preflight_player_code(
    root: Path,
    game_dir: Path,
    expected_main_dol_sha256: str,
) -> tuple[Path, relf_input.RelFInput]:
    main_dol = game_dir / "sys" / "main.dol"
    if not main_dol.is_file():
        raise RuntimeError(f"extracted player game is missing sys/main.dol: {game_dir}")
    expected = expected_main_dol_sha256.lower()
    actual = sha256_file(main_dol).lower()
    if not expected or actual != expected:
        raise RuntimeError(
            "clean main.dol identity mismatch against known-good C module: "
            f"expected {expected or '<missing C identity>'}, got {actual}"
        )

    # Retail SPM stores the live module as /rel/relF.bin using Nintendo LZ10.
    # Mirror the game's own preference for relF.bin over relF.rel, decode it
    # locally, then apply the exact runtime-proven linked-layout fingerprint.
    relf = relf_input.load_from_game(game_dir)
    rel_bytes = bytearray(relf.data)
    sections, _meta = build_relf_codegen_dol.parse_sections(rel_bytes)
    execute = sections[build_relf_codegen_dol.REL_EXEC_INDEX]
    if (
        execute.address != build_relf_codegen_dol.REL_EXEC_ADDRESS
        or execute.file_offset != build_relf_codegen_dol.REL_EXEC_OFFSET
        or execute.size != build_relf_codegen_dol.REL_EXEC_SIZE
    ):
        raise RuntimeError("decoded relF executable section failed the proven M14 fingerprint")

    # Also prove the clean DOL still exposes exactly the v19 GX seam shape before
    # DolRecomp uses those addresses as forced LLVM partition boundaries.
    derive_v19_llvm_seams.scan(main_dol)
    print(
        "[OpenSPM] Player-code preflight PASS: clean DOL identity matches known-good C module; "
        f"R8PE01 + {relf.storage} relF + v19 seam shape. relF={relf.path}",
        flush=True,
    )
    return main_dol, relf


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT_DEFAULT)
    parser.add_argument("--cmake", required=True)
    parser.add_argument("--ctest", required=True)
    parser.add_argument("--git", default="git")
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--game-dir", type=Path)
    parser.add_argument("--graphics", default="AUTO")
    parser.add_argument("--llvm-dir", type=Path)
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument("--skip-host-build", action="store_true")
    parser.add_argument("--build-only", action="store_true")
    parser.add_argument(
        "--chunk-instructions",
        type=int,
        choices=(128, 256, 512),
        default=256,
        help="LLVM generic chunk ceiling; 256 is dev113, 128 baseline, 512 experimental transition-pressure A/B",
    )
    parser.add_argument(
        "--state-in-memory-mode",
        type=int,
        choices=(0, 1),
        default=0,
        help="DolRecomp CPUState-resident non-native state A/B: 0=off, 1=on",
    )
    parser.add_argument(
        "--heavy-diagnostics-mode",
        type=int,
        choices=(0, 1),
        default=0,
        help="dev111/dev112/dev115 callback profiling: 0=release/off, 1=profile/on",
    )
    parser.add_argument(
        "--toolchain",
        choices=NATIVE_MODULE_TOOLCHAINS,
        default=DEFAULT_NATIVE_MODULE_TOOLCHAIN,
        help="player-module compiler/linker: msvc (default: cl /GL + link /LTCG), "
             "clang (clang-cl ThinLTO + lld-link from the pinned LLVM, MSVC CRT/SDK) "
             "or clang-freestanding (no CRT/SDK headers or libraries; Stage 2 spike)",
    )
    parser.add_argument(
        "--llvm-bin",
        type=Path,
        help="override the pinned LLVM bin directory used by the clang toolchains",
    )
    args = parser.parse_args()

    root = args.root.resolve()
    candidate = (args.candidate or root / ".openspm" / "m14" / "windows-candidate").resolve()
    game_dir = (args.game_dir or root / ".openspm" / "m14" / "game").resolve()
    llvm_root = root / ".openspm" / "m14" / "llvm-candidate"
    toolchain = llvm_root / "dolrecomp-toolchain"
    toolchain_json = toolchain / "toolchain.json"
    module_build = llvm_root / "local-module-build"
    module_output = llvm_root / "native-output"
    generated_module = module_output / "openspm_local_native.dll"
    staged_native_dir = candidate / "User-OpenSPM" / "native"
    staged_dll = staged_native_dir / "openspm_local_native.dll"
    known_good_c_root = root / ".openspm" / "m14" / "local-module-build"
    known_good_c_dll = known_good_c_root / "module" / "openspm_local_native.dll"
    known_good_c_identity = (
        known_good_c_root / "stage" / "identity" / "openspm_local_identity.h"
    )
    backup_dir = llvm_root / "transaction-backup"
    backup_dll = backup_dir / "openspm_local_native.dll"

    if not 1 <= args.jobs <= 256:
        print("error: --jobs must be 1..256", file=sys.stderr)
        return 2
    if not (root / "LAUNCH-M14-CANDIDATE.cmd").is_file():
        print(f"error: OpenSPM root is missing LAUNCH-M14-CANDIDATE.cmd: {root}", file=sys.stderr)
        return 2
    if not (candidate / "build.ninja").is_file():
        print(
            "error: normal M14 candidate has not been configured; "
            "run RUN-M14-HOME-TEST.cmd once",
            file=sys.stderr,
        )
        return 2
    if not known_good_c_dll.is_file():
        print(
            "error: known-good C player module cache is incomplete; run M14-UPDATE-RUN.cmd once first",
            file=sys.stderr,
        )
        return 2

    try:
        expected_main_dol_sha256 = read_identity_sha256(known_good_c_identity)
        print(
            "[LLVM 1/7] Verifying player-owned DOL against known-good C identity, "
            "retail-relF revision and v19 seam shape...",
            flush=True,
        )
        main_dol, _relf = preflight_player_code(
            root, game_dir, expected_main_dol_sha256
        )

        # Recovery is deliberately after source-identity verification: the
        # independent C artifact must prove it belongs to the exact player DOL
        # before it is allowed to overwrite a stale experimental staging slot.
        repair_staged_c_module(staged_dll, known_good_c_dll)

        print("[LLVM 2/7] Running source-only native-pipeline contracts...", flush=True)
        run([sys.executable, str(SCRIPT_DIR / "test_llvm_native_pipeline.py")])

        if not args.skip_host_build:
            print(
                "[LLVM 3/7] Reconfiguring patched Aurora, then rebuilding OpenSPM host runtime + contracts...",
                flush=True,
            )
            # Do not rely solely on CMake's CONFIGURE_DEPENDS glob noticing a
            # newly added renderer transform. Reconfigure the already-created
            # candidate explicitly so dev88/dev89/dev91 run against the
            # player-owned cached pinned Aurora before any renderer object is
            # considered up to date. Existing generator/provider/cache options
            # remain authoritative in CMakeCache.txt.
            run([
                args.cmake, "-S", str(root), "-B", str(candidate),
            ])
            verify_renderer_perf_candidate(candidate)
            run([
                args.cmake, "--build", str(candidate), "--target",
                "OpenSPMNative", "openspm_native_backend", "openspm_renderer",
                "openspm_shell_crouch", "openspm_glock", "openspm_striker", "openspm_sfx_log",
                "openspm_hitbox_view", "openspm_francis", "openspm_flint_follower", "openspm_sample_level",
                "m14_wii_platform_test", "m14_wii_boot_platform_test",
                "m14_gx_platform_test", "m14_gx_renderer_service_test",
                "m14_exi_diagnostic_test", "-j2",
            ])
            run([
                args.ctest, "--test-dir", str(candidate), "--output-on-failure",
                "-R", "^m14_(wii(_boot)?_platform|gx_(platform|renderer_service)|exi_diagnostic)$",
            ])
        else:
            print("[LLVM 3/7] Host rebuild skipped by request.", flush=True)

        print("[LLVM 4/7] Preparing pinned LLVM-enabled DolRecomp compiler (build-time only)...", flush=True)
        prepare = [
            sys.executable, str(SCRIPT_DIR / "prepare_dolrecomp_llvm.py"),
            "--cache", str(toolchain),
            "--cmake", args.cmake,
            "--git", args.git,
            "--generator", "Ninja",
            "--jobs", str(args.jobs),
            "--json", str(toolchain_json),
        ]
        if args.llvm_dir:
            prepare += ["--llvm-dir", str(args.llvm_dir.resolve())]
        run(prepare)
        info = json.loads(toolchain_json.read_text(encoding="utf-8"))
        dolrecomp = Path(info["executable"])
        dolrecomp_source = Path(info["source"])
        runtime_source = Path(info.get("runtime_source", dolrecomp_source / "src"))
        if (
            not dolrecomp.is_file()
            or not (runtime_source / "cpu" / "cpu.c").is_file()
            or not (runtime_source / "common" / "types.h").is_file()
        ):
            raise RuntimeError(
                "prepared DolRecomp toolchain manifest points to an incomplete compiler/runtime checkout"
            )
        if info.get("runtime_dependency") is not False:
            raise RuntimeError("toolchain manifest unexpectedly claims a runtime dependency")
        if info.get("state_in_memory_contract") is not True:
            raise RuntimeError(
                "toolchain manifest does not prove pinned DolRecomp state-in-memory "
                "semantics; rerun toolchain preparation with the current source audit"
            )
        if info.get("openspm_codegen_patch") != EXPECTED_CODEGEN_PATCH:
            raise RuntimeError(
                "toolchain manifest does not contain the current OpenSPM LLVM codegen patch: "
                f"expected {EXPECTED_CODEGEN_PATCH}, got {info.get('openspm_codegen_patch')!r}"
            )

        print("[LLVM 5/7] Generating DOL+decoded live-relF native objects and linking OpenSPM module...", flush=True)
        if module_output.exists():
            shutil.rmtree(module_output)
        build = [
            sys.executable, str(SCRIPT_DIR / "build_local_llvm_module.py"),
            "--openspm-root", str(root),
            "--dolrecomp", str(dolrecomp),
            "--dolrecomp-source", str(dolrecomp_source),
            "--game-dir", str(game_dir),
            "--main-dol", str(main_dol),
            "--build-dir", str(module_build),
            "--output-dir", str(module_output),
            "--game-id", "R8PE01",
            "--cmake", args.cmake,
            "--generator", "Ninja",
            "--config", "Release",
            "--jobs", str(args.jobs),
            "--chunk-instructions", str(args.chunk_instructions),
            "--skip-llvm-probe",
        ]
        if (args.state_in_memory_mode == 1):
            build.append("--state-in-memory")
        if (args.heavy_diagnostics_mode == 1):
            build.append("--heavy-diagnostics")
        if args.toolchain != DEFAULT_NATIVE_MODULE_TOOLCHAIN:
            llvm_bin = pinned_llvm_bin(info, args.llvm_bin)
            print(
                f"[OpenSPM] Native module toolchain: {args.toolchain} "
                f"(clang-cl + lld-link from {llvm_bin})",
                flush=True,
            )
            build += ["--toolchain", args.toolchain, "--llvm-bin", str(llvm_bin)]
            # Release Stage 3 check: OPENSPM_M14_NO_CMAKE=1 builds the
            # clang-freestanding module with direct_module_build.py instead
            # of CMake (docs/TEST-LIST.md test 7).
            if args.toolchain == "clang-freestanding" and os.environ.get("OPENSPM_M14_NO_CMAKE") == "1":
                build.append("--no-cmake")
                print("[LLVM 5/7] OPENSPM_M14_NO_CMAKE=1: module built without CMake (direct_module_build.py).",
                      flush=True)
        run(build)
        if not generated_module.is_file() or generated_module.stat().st_size == 0:
            raise RuntimeError(f"LLVM local module was not produced: {generated_module}")

        print(f"[LLVM 6/7] LLVM module ready: {generated_module}", flush=True)
        if args.build_only:
            print(
                "[OpenSPM] Build-only requested; staged player DLL is normalized to the "
                "known-good C module and was not replaced by LLVM.",
                flush=True,
            )
            return 0

        print("[LLVM 7/7] Transactionally staging LLVM DLL for one strict-native launch...", flush=True)
        backup_dir.mkdir(parents=True, exist_ok=True)
        # Copy from the independently built normal C artifact, not from whatever
        # happens to be staged, so rollback never blesses a stale experimental DLL.
        shutil.copy2(known_good_c_dll, backup_dll)
        original_size = backup_dll.stat().st_size
        original_hash = sha256_file(backup_dll)
        try:
            shutil.copy2(generated_module, staged_dll)
            if staged_dll.stat().st_size != generated_module.stat().st_size:
                raise RuntimeError("transactional LLVM DLL copy size mismatch")
            if sha256_file(staged_dll) != sha256_file(generated_module):
                raise RuntimeError("transactional LLVM DLL copy hash mismatch")
            env = os.environ.copy()
            env["OPENSPM_REPO_REVISION"] = subprocess.run(
                [args.git, "rev-parse", "--short=12", "HEAD"],
                cwd=str(root), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, check=False,
            ).stdout.strip() or "unknown"
            env["OPENSPM_M14_EXECUTION_ENGINE"] = "llvm-native-objects"
            env["OPENSPM_M14_LLVM_CHUNK_INSTRUCTIONS"] = str(args.chunk_instructions)
            env["OPENSPM_M14_LLVM_STATE_IN_MEMORY"] = "1" if (args.state_in_memory_mode == 1) else "0"
            env["OPENSPM_M14_HEAVY_DIAGNOSTICS"] = "1" if (args.heavy_diagnostics_mode == 1) else "0"
            env["OPENSPM_M14_NATIVE_TOOLCHAIN"] = args.toolchain
            print(
                "[OpenSPM] Launching strict-native OpenSPM with LLVM-generated player objects "
                f"(chunk={args.chunk_instructions}, state_mem={1 if (args.state_in_memory_mode == 1) else 0}, "
                f"heavy_diag={1 if (args.heavy_diagnostics_mode == 1) else 0}). "
                "No Dolphin/ModernGekko/RecompCore runtime is involved.",
                flush=True,
            )
            completed = subprocess.run(
                ["cmd.exe", "/d", "/c", str(root / "LAUNCH-M14-CANDIDATE.cmd"), args.graphics],
                cwd=str(root), env=env,
            )
            return completed.returncode
        finally:
            if backup_dll.is_file():
                shutil.copy2(backup_dll, staged_dll)
                restored_hash = sha256_file(staged_dll)
                if restored_hash != original_hash or staged_dll.stat().st_size != original_size:
                    print(
                        "WARNING: restored C DLL does not match the independent normal-build backup",
                        file=sys.stderr,
                    )
                else:
                    print("[OpenSPM] Restored known-good C player module after LLVM launch.", flush=True)
    except (OSError, RuntimeError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        print(f"error: transactional LLVM candidate failed: {exc}", file=sys.stderr)
        return exc.returncode if isinstance(exc, subprocess.CalledProcessError) else 1


if __name__ == "__main__":
    raise SystemExit(main())

