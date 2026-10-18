#!/usr/bin/env python3
"""Prepare the pinned OpenSPM-specific DolRecomp LLVM compiler toolchain.

DolRecomp and LLVM are build-time tooling only. On Windows this helper can
bootstrap the pinned official LLVM 20.1.8 developer archive into .openspm.
The archive's exported LLVM targets contain a release-builder-specific absolute
DIA SDK path, so the private bootstrap copy is repaired to the active Visual
Studio DIA SDK before DolRecomp is configured.
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
import tarfile
import urllib.error
import urllib.request

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import apply_openspm_llvm_wpar_codegen_patch  # noqa: E402
import probe_dolrecomp_llvm  # noqa: E402

PIN = "40637c4683bd2820ac5b23607ee344720beb26df"
REPO = "https://github.com/ExpansionPak/DolRecomp.git"
MARKER = ".openspm-dolrecomp-llvm-owned"
OPENSPM_CODEGEN_PATCH = "llvm-inline-wpar-indirect-budget-v20-dev113-chunk256-yold"

BOOTSTRAP_LLVM_VERSION = "20.1.8"
BOOTSTRAP_LLVM_ARCHIVE = "clang+llvm-20.1.8-x86_64-pc-windows-msvc.tar.xz"
BOOTSTRAP_LLVM_URL = (
    "https://github.com/llvm/llvm-project/releases/download/llvmorg-20.1.8/"
    "clang%2Bllvm-20.1.8-x86_64-pc-windows-msvc.tar.xz"
)
BOOTSTRAP_LLVM_SHA256 = "f229769f11d6a6edc8ada599c0cda964b7dee6ab1a08c6cf9dd7f513e85b107f"

# The official Windows LLVM archives have historically exported
# LLVMDebugInfoPDB with an absolute path to the DIA SDK used on the release
# builder. Match only an absolute diaguids.lib path under a DIA SDK so unrelated
# CMake paths are never rewritten.
DIA_EXPORT_RE = re.compile(
    r'(?P<path>[A-Za-z]:[\\/][^";\r\n]*?DIA SDK[\\/]lib[\\/]amd64[\\/]diaguids[.]lib)',
    re.IGNORECASE,
)


def run(command: list[str], *, cwd: Path | None = None) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=str(cwd) if cwd else None, check=True)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _llvm_config_dir(executable: str) -> Path | None:
    try:
        completed = subprocess.run(
            [executable, "--cmakedir"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    path = Path(completed.stdout.strip())
    return path.resolve() if (path / "LLVMConfig.cmake").is_file() else None


def _llvm_package_major(candidate: Path) -> int | None:
    for name in ("LLVMConfig.cmake", "LLVMConfigVersion.cmake"):
        path = candidate / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for pattern in (
            r'LLVM_PACKAGE_VERSION\s+"?([0-9]+)(?:[.][0-9]+)*"?',
            r'PACKAGE_VERSION\s+"?([0-9]+)(?:[.][0-9]+)*"?',
        ):
            match = re.search(pattern, text)
            if match:
                return int(match.group(1))
    return None


def _bootstrap_package_root(cache: Path) -> Path:
    return cache / f"llvm-{BOOTSTRAP_LLVM_VERSION}-windows-x64"


def _bootstrap_llvm_dir(cache: Path) -> Path:
    return _bootstrap_package_root(cache) / "lib" / "cmake" / "llvm"


def _download_verified_llvm_archive(cache: Path) -> Path:
    downloads = cache / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    archive = downloads / BOOTSTRAP_LLVM_ARCHIVE
    partial = archive.with_suffix(archive.suffix + ".part")

    if archive.is_file():
        actual = sha256_file(archive)
        if actual == BOOTSTRAP_LLVM_SHA256:
            print(f"[OpenSPM] Reusing verified LLVM {BOOTSTRAP_LLVM_VERSION} archive: {archive}")
            return archive
        print(
            "[OpenSPM] Removing cached LLVM archive with wrong SHA-256: "
            f"expected {BOOTSTRAP_LLVM_SHA256}, got {actual}",
            flush=True,
        )
        archive.unlink()

    partial.unlink(missing_ok=True)
    print(
        "[OpenSPM] No LLVM 19/20 development package found; bootstrapping official "
        f"LLVM {BOOTSTRAP_LLVM_VERSION} developer archive (~940 MB) into .openspm.",
        flush=True,
    )
    print(f"[OpenSPM] Download: {BOOTSTRAP_LLVM_URL}", flush=True)
    request = urllib.request.Request(
        BOOTSTRAP_LLVM_URL,
        headers={"User-Agent": "OpenSPM-M14-LLVM-bootstrap/1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as sink:
            total_header = response.headers.get("Content-Length")
            total = int(total_header) if total_header and total_header.isdigit() else 0
            copied = 0
            next_report = 64 * 1024 * 1024
            while True:
                chunk = response.read(4 * 1024 * 1024)
                if not chunk:
                    break
                sink.write(chunk)
                copied += len(chunk)
                if copied >= next_report:
                    if total:
                        print(
                            f"[OpenSPM] LLVM download {copied // (1024 * 1024)} / "
                            f"{total // (1024 * 1024)} MiB",
                            flush=True,
                        )
                    else:
                        print(f"[OpenSPM] LLVM download {copied // (1024 * 1024)} MiB", flush=True)
                    next_report = copied + 64 * 1024 * 1024
    except (OSError, urllib.error.URLError) as exc:
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"failed to download pinned LLVM developer archive: {exc}") from exc

    actual = sha256_file(partial)
    if actual != BOOTSTRAP_LLVM_SHA256:
        partial.unlink(missing_ok=True)
        raise RuntimeError(
            "downloaded LLVM developer archive SHA-256 mismatch: "
            f"expected {BOOTSTRAP_LLVM_SHA256}, got {actual}"
        )
    partial.replace(archive)
    print(f"[OpenSPM] LLVM archive SHA-256 verified: {actual}", flush=True)
    return archive


def _extract_verified_llvm_archive(cache: Path, archive: Path) -> Path:
    package_root = _bootstrap_package_root(cache)
    llvm_dir = _bootstrap_llvm_dir(cache)
    if (llvm_dir / "LLVMConfig.cmake").is_file() and _llvm_package_major(llvm_dir) == 20:
        return llvm_dir

    extract_root = cache / f"llvm-{BOOTSTRAP_LLVM_VERSION}-extracting"
    shutil.rmtree(extract_root, ignore_errors=True)
    extract_root.mkdir(parents=True)
    print(f"[OpenSPM] Extracting LLVM {BOOTSTRAP_LLVM_VERSION} developer package...", flush=True)
    try:
        with tarfile.open(archive, mode="r:xz") as package:
            if sys.version_info >= (3, 12):
                package.extractall(extract_root, filter="data")
            else:
                resolved_root = extract_root.resolve()
                for member in package.getmembers():
                    destination = (extract_root / member.name).resolve()
                    if resolved_root != destination and resolved_root not in destination.parents:
                        raise RuntimeError(f"unsafe path in LLVM archive: {member.name}")
                package.extractall(extract_root)
    except (OSError, tarfile.TarError):
        shutil.rmtree(extract_root, ignore_errors=True)
        raise

    configs = [
        path
        for path in extract_root.rglob("LLVMConfig.cmake")
        if tuple(part.lower() for part in path.parts[-4:])
        == ("lib", "cmake", "llvm", "llvmconfig.cmake")
    ]
    if len(configs) != 1:
        shutil.rmtree(extract_root, ignore_errors=True)
        raise RuntimeError(
            "official LLVM archive did not contain exactly one "
            f"lib/cmake/llvm/LLVMConfig.cmake; found {len(configs)}"
        )
    extracted_package = configs[0].parents[3]
    extracted_llvm_dir = configs[0].parent
    if _llvm_package_major(extracted_llvm_dir) != 20:
        shutil.rmtree(extract_root, ignore_errors=True)
        raise RuntimeError("bootstrapped archive did not identify as LLVM 20")

    shutil.rmtree(package_root, ignore_errors=True)
    shutil.move(str(extracted_package), str(package_root))
    shutil.rmtree(extract_root, ignore_errors=True)

    llvm_dir = _bootstrap_llvm_dir(cache)
    if not (llvm_dir / "LLVMConfig.cmake").is_file():
        raise RuntimeError("LLVM bootstrap move completed but LLVMConfig.cmake is missing")

    manifest = {
        "version": BOOTSTRAP_LLVM_VERSION,
        "archive": BOOTSTRAP_LLVM_ARCHIVE,
        "sha256": BOOTSTRAP_LLVM_SHA256,
        "source": BOOTSTRAP_LLVM_URL,
        "llvm_dir": str(llvm_dir),
    }
    (package_root / ".openspm-bootstrap.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    archive.unlink(missing_ok=True)
    print(f"[OpenSPM] Bootstrapped LLVM development package: {llvm_dir}", flush=True)
    return llvm_dir


def bootstrap_llvm_windows(cache: Path) -> Path:
    if os.name != "nt":
        raise RuntimeError("automatic LLVM bootstrap is currently supported only on Windows x64")
    llvm_dir = _bootstrap_llvm_dir(cache)
    if (llvm_dir / "LLVMConfig.cmake").is_file() and _llvm_package_major(llvm_dir) == 20:
        print(f"[OpenSPM] Reusing bootstrapped LLVM 20 development package: {llvm_dir}")
        return llvm_dir
    archive = _download_verified_llvm_archive(cache)
    return _extract_verified_llvm_archive(cache, archive)


def _installed_llvm_candidates(explicit: Path | None, cache: Path | None) -> list[Path]:
    candidates: list[Path] = []
    if explicit:
        candidates.append(explicit.expanduser())
    if os.environ.get("LLVM_DIR"):
        candidates.append(Path(os.environ["LLVM_DIR"]))
    if cache is not None:
        candidates.append(_bootstrap_llvm_dir(cache))

    for name in ("llvm-config-20", "llvm-config-19", "llvm-config"):
        exe = shutil.which(name)
        if exe:
            found = _llvm_config_dir(exe)
            if found:
                candidates.append(found)

    if os.name == "nt":
        program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        pf86 = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
        candidates.append(program_files / "LLVM" / "lib" / "cmake" / "llvm")
        for year in ("2026", "2022", "2019"):
            for edition in ("BuildTools", "Community", "Professional", "Enterprise"):
                candidates.append(
                    pf86
                    / "Microsoft Visual Studio"
                    / year
                    / edition
                    / "VC"
                    / "Tools"
                    / "Llvm"
                    / "x64"
                    / "lib"
                    / "cmake"
                    / "llvm"
                )
                candidates.append(
                    program_files
                    / "Microsoft Visual Studio"
                    / year
                    / edition
                    / "VC"
                    / "Tools"
                    / "Llvm"
                    / "x64"
                    / "lib"
                    / "cmake"
                    / "llvm"
                )
        if os.environ.get("VSINSTALLDIR"):
            candidates.append(
                Path(os.environ["VSINSTALLDIR"])
                / "VC"
                / "Tools"
                / "Llvm"
                / "x64"
                / "lib"
                / "cmake"
                / "llvm"
            )
    return candidates


def find_llvm_dir(
    explicit: Path | None,
    *,
    cache: Path | None = None,
    allow_bootstrap: bool = True,
) -> Path:
    seen: set[str] = set()
    unknown_version: list[Path] = []
    unsupported: list[tuple[Path, int]] = []
    for candidate in _installed_llvm_candidates(explicit, cache):
        candidate = candidate.expanduser().resolve()
        key = str(candidate).lower() if os.name == "nt" else str(candidate)
        if key in seen:
            continue
        seen.add(key)
        if not (candidate / "LLVMConfig.cmake").is_file():
            continue
        major = _llvm_package_major(candidate)
        if major in (19, 20):
            print(f"[OpenSPM] Using LLVM {major} development package: {candidate}")
            return candidate
        if major is None:
            unknown_version.append(candidate)
        else:
            unsupported.append((candidate, major))

    if unknown_version:
        candidate = unknown_version[0]
        print(f"[OpenSPM] LLVM package version was not parseable; probing with CMake: {candidate}")
        return candidate
    if allow_bootstrap and cache is not None and os.name == "nt":
        return bootstrap_llvm_windows(cache)

    details = ""
    if unsupported:
        details = " Found unsupported package(s): " + "; ".join(
            f"LLVM {major} at {path}" for path, major in unsupported
        ) + "."
    raise RuntimeError(
        "LLVM 19/20 development files were not found."
        + details
        + " Set LLVM_DIR to the directory containing LLVMConfig.cmake."
    )


def _dia_sdk_candidates() -> list[Path]:
    candidates: list[Path] = []
    if os.environ.get("VSINSTALLDIR"):
        candidates.append(Path(os.environ["VSINSTALLDIR"]) / "DIA SDK")

    if os.environ.get("VCToolsInstallDir"):
        vc_tools = Path(os.environ["VCToolsInstallDir"]).resolve()
        parts = [part.lower() for part in vc_tools.parts]
        if "vc" in parts:
            vc_index = parts.index("vc")
            candidates.append(Path(*vc_tools.parts[:vc_index]) / "DIA SDK")

    pf86 = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
    program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    for base in (pf86, program_files):
        for year in ("2026", "2022", "2019"):
            for edition in ("BuildTools", "Community", "Professional", "Enterprise"):
                candidates.append(base / "Microsoft Visual Studio" / year / edition / "DIA SDK")
    return candidates


def find_dia_sdk() -> tuple[Path, Path]:
    if os.name != "nt":
        raise RuntimeError("DIA SDK lookup is Windows-only")
    seen: set[str] = set()
    for candidate in _dia_sdk_candidates():
        candidate = candidate.expanduser().resolve()
        key = str(candidate).lower()
        if key in seen:
            continue
        seen.add(key)
        header = candidate / "include" / "dia2.h"
        library = candidate / "lib" / "amd64" / "diaguids.lib"
        if header.is_file() and library.is_file():
            print(f"[OpenSPM] Using Visual Studio DIA SDK: {candidate}", flush=True)
            return candidate, library
    raise RuntimeError(
        "Visual Studio DIA SDK was not found. The LLVM Windows developer archive "
        "needs dia2.h and x64 diaguids.lib from the active Visual Studio C++ installation."
    )


def repair_bootstrap_dia_exports(cache: Path, llvm_dir: Path, dia_library: Path) -> int:
    """Repair the official archive's release-builder-specific DIA path in-place.

    Only the OpenSPM-owned bootstrapped package may be modified. System LLVM
    installations are never rewritten.
    """
    package_root = _bootstrap_package_root(cache).resolve()
    llvm_dir = llvm_dir.resolve()
    try:
        llvm_dir.relative_to(package_root)
    except ValueError:
        return 0

    replacement = dia_library.resolve().as_posix()
    rewrites = 0
    files_changed = 0
    for cmake_file in llvm_dir.rglob("*.cmake"):
        try:
            text = cmake_file.read_text(encoding="utf-8", errors="strict")
        except (OSError, UnicodeError):
            continue

        def replace(match: re.Match[str]) -> str:
            nonlocal rewrites
            old = match.group("path").replace("\\", "/")
            if old.lower() == replacement.lower():
                return match.group(0)
            rewrites += 1
            return replacement

        updated = DIA_EXPORT_RE.sub(replace, text)
        if updated != text:
            cmake_file.write_text(updated, encoding="utf-8", newline="\n")
            files_changed += 1

    stale: list[str] = []
    for cmake_file in llvm_dir.rglob("*.cmake"):
        try:
            text = cmake_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in DIA_EXPORT_RE.finditer(text):
            embedded = match.group("path").replace("\\", "/")
            if embedded.lower() != replacement.lower():
                stale.append(f"{cmake_file.name}: {embedded}")
    if stale:
        raise RuntimeError(
            "LLVM CMake exports still contain stale DIA SDK paths after repair: "
            + "; ".join(stale[:4])
        )

    if rewrites:
        print(
            f"[OpenSPM] Repaired LLVM DIA export: {rewrites} stale path reference(s) "
            f"across {files_changed} CMake file(s) -> {replacement}",
            flush=True,
        )
    else:
        print("[OpenSPM] LLVM DIA export already matches the active Visual Studio SDK.", flush=True)
    return rewrites


def verify_state_in_memory_contract(source: Path) -> None:
    """Fail closed if the pinned LLVM state-storage semantics drift.

    dev114 relies on state-in-memory affecting CPUState-ABI bodies only, with
    eligible slots directly aliasing CPUState and therefore skipped by dirty
    materialization/reload. These are compiler-source invariants, not runtime
    guesses, so verify them against the exact prepared checkout before build.
    """
    register_state = (
        source / "src" / "backend" / "llvm" / "register_state.cpp"
    ).read_text(encoding="utf-8")
    exits = (
        source / "src" / "backend" / "llvm" / "exits.cpp"
    ).read_text(encoding="utf-8")
    runtime = (
        source / "src" / "backend" / "llvm" / "runtime.cpp"
    ).read_text(encoding="utf-8")

    required = {
        "state-memory native-ABI exclusion": (
            register_state,
            "if (!state_in_memory_ || native_abi_)\n    return false;",
        ),
        "CPUState direct slot alias": (
            exits,
            "state_[slot] = bytePtr(stateOffset(stateSlot));",
        ),
        "dirty-state alias skip": (
            exits,
            "if (slotInMemory(stateSlot))\n      continue;",
        ),
        "reload alias skip": (
            runtime,
            "if (slotInMemory(slot))\n    return;",
        ),
        "reload-used-state path": (
            runtime,
            "reloadState(static_cast<DolIRStateSlot>(slot));",
        ),
    }
    missing = [
        label for label, (text, snippet) in required.items()
        if snippet not in text
    ]
    if missing:
        raise RuntimeError(
            "pinned DolRecomp state-in-memory semantics changed; "
            "refusing dev114 without re-audit: " + ", ".join(missing)
        )
    print(
        "[OpenSPM] DolRecomp state-in-memory contract PASS: "
        "CPUState-ABI-only aliasing + dirty/reload skips are present.",
        flush=True,
    )


def prepare_source(cache: Path, git: str) -> Path:
    source = cache / "source"
    marker = cache / MARKER
    cache.mkdir(parents=True, exist_ok=True)
    if source.exists() and not marker.is_file():
        raise RuntimeError(f"refusing to modify unowned DolRecomp directory: {source}")
    if not source.exists():
        source.mkdir(parents=True)
        marker.write_text(
            "OpenSPM-owned pinned DolRecomp compiler cache. Safe to reset.\n",
            encoding="utf-8",
        )
        run([git, "-C", str(source), "init"])
        run([git, "-C", str(source), "remote", "add", "origin", REPO])
    # The pinned commit is immutable: once it is in the cache, rebuilds need no
    # network (a failing fetch, e.g. a TLS-inspecting proxy, must not block them).
    have_pin = subprocess.run([git, "-C", str(source), "cat-file", "-e", PIN + "^{commit}"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    if have_pin:
        print(f"[OpenSPM] DolRecomp {PIN[:12]} already cached; skipping fetch.", flush=True)
    else:
        run([git, "-C", str(source), "fetch", "--depth", "1", "origin", PIN])
    run([git, "-C", str(source), "checkout", "--detach", PIN])
    run([git, "-C", str(source), "reset", "--hard", PIN])
    patch_result = apply_openspm_llvm_wpar_codegen_patch.transform(source)
    print(f"OpenSPM LLVM codegen adaptation: {patch_result}")
    return source


def locate_executable(build: Path) -> Path:
    candidates: list[Path] = []
    for name in ("dolrecomp.exe", "dolrecomp"):
        candidates.extend(build.rglob(name))
    candidates = [path for path in candidates if path.is_file()]
    if not candidates:
        raise RuntimeError("DolRecomp build completed but dolrecomp executable was not found")
    return max(candidates, key=lambda path: path.stat().st_mtime_ns)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", required=True, type=Path)
    parser.add_argument("--llvm-dir", type=Path)
    parser.add_argument(
        "--no-bootstrap-llvm",
        action="store_true",
        help="do not download the pinned official LLVM 20.1.8 Windows developer archive",
    )
    parser.add_argument("--cmake", default="cmake")
    parser.add_argument("--git", default="git")
    parser.add_argument("--generator", default="Ninja")
    parser.add_argument("--jobs", type=int, default=max(1, min(16, os.cpu_count() or 4)))
    parser.add_argument("--json", dest="json_path", type=Path)
    args = parser.parse_args()

    try:
        if not 1 <= args.jobs <= 256:
            raise RuntimeError("--jobs must be 1..256")
        git = shutil.which(args.git) or args.git
        cmake = shutil.which(args.cmake) or args.cmake
        cache = args.cache.resolve()
        llvm_dir = find_llvm_dir(
            args.llvm_dir,
            cache=cache,
            allow_bootstrap=not args.no_bootstrap_llvm,
        )

        dia_sdk: Path | None = None
        dia_rewrites = 0
        if os.name == "nt":
            dia_sdk, dia_library = find_dia_sdk()
            dia_rewrites = repair_bootstrap_dia_exports(cache, llvm_dir, dia_library)

        source = prepare_source(cache, git)
        verify_state_in_memory_contract(source)
        build = cache / "build"
        configure = [
            cmake,
            "-S",
            str(source),
            "-B",
            str(build),
            "-G",
            args.generator,
            "-DCMAKE_BUILD_TYPE=Release",
            "-DBUILD_TESTING=OFF",
            "-DDOLRECOMP_ENABLE_LLVM=ON",
            f"-DLLVM_DIR={llvm_dir}",
        ]
        if dia_sdk is not None:
            configure.append(f"-DMSVC_DIA_SDK_DIR={dia_sdk}")
        run(configure)
        run(
            [
                cmake,
                "--build",
                str(build),
                "--target",
                "dolrecomp",
                "--parallel",
                str(args.jobs),
            ]
        )
        exe = locate_executable(build)
        if not probe_dolrecomp_llvm.probe(exe):
            raise RuntimeError("newly built pinned DolRecomp failed the LLVM object probe")

        summary = {
            "schema_version": 5,
            "dolrecomp_commit": PIN,
            "openspm_codegen_patch": OPENSPM_CODEGEN_PATCH,
            "source": str(source),
            "runtime_source": str(source / "src"),
            "build": str(build),
            "executable": str(exe),
            "llvm_dir": str(llvm_dir),
            "llvm_major": _llvm_package_major(llvm_dir),
            "llvm_bootstrap_version": (
                BOOTSTRAP_LLVM_VERSION if _bootstrap_package_root(cache) in llvm_dir.parents else None
            ),
            "dia_sdk_dir": str(dia_sdk) if dia_sdk is not None else None,
            "dia_export_rewrites": dia_rewrites,
            "runtime_dependency": False,
            "state_in_memory_contract": True,
        }
        destination = args.json_path.resolve() if args.json_path else cache / "toolchain.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"M14 LLVM DolRecomp toolchain ready: {exe}")
        print(f"M14 matching patched DolRecomp runtime source: {source / 'src'}")
        print(f"M14 OpenSPM codegen patch: {OPENSPM_CODEGEN_PATCH}")
        return 0
    except (OSError, RuntimeError, subprocess.CalledProcessError, tarfile.TarError) as exc:
        print(f"error: LLVM DolRecomp preparation failed: {exc}", file=sys.stderr)
        return exc.returncode if isinstance(exc, subprocess.CalledProcessError) else 1


if __name__ == "__main__":
    raise SystemExit(main())

