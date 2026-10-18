#!/usr/bin/env python3
"""Assemble the public OpenSPM release folder (and zip) from build outputs.

The release is built from an allowlist only: every file that ends up in it is
named here, and the finished folder is then run through
audit_public_candidate.py, which rejects anything game- or player-derived
(disc images, DOL/REL/BIN/ARC/TPL payloads, the per-player
openspm_local_native.dll - by name and by its export - generated LLVM objects,
.openspm/ and User-OpenSPM/ contents). The zip is written only after the audit
passes, and the zip's file list is checked against the audited folder.

    python tools/release/make_release_zip.py \
        --build-dir .openspm/m14/windows-candidate \
        --license-search .openspm/m14/deps/aurora \
        --license-search .openspm/m14/windows-candidate \
        --output dist/OpenSPM --zip

Inputs:
  --build-dir       a CMake build/candidate tree: OpenSPMNative.exe at its root
                    and runtime/*.dll. Only those two places are read; the
                    candidate's User-OpenSPM/ (player game, native module,
                    saves) and any OpenSPM.ini there are never looked at.
  repo (this checkout)  OpenSPM.ini.example, docs, the project LICENSE.
  --license-search  folders holding third-party sources/packages (the Aurora
                    checkout and its fetched deps, the build tree's _deps);
                    license files are found by component folder name.
  --license NAME=PATH   explicit license file for a component.

Layout produced:
  OpenSPMNative.exe
  OpenSPM.ini                 (from OpenSPM.ini.example, never a player's ini)
  OpenSPM.ini.example
  README.txt                  (quick start)
  LICENSE.txt                 (OpenSPM's own license)
  THIRD-PARTY-NOTICES.txt     (every bundled component, license id, source)
  licenses/<Component>.txt
  docs/*.md
  runtime/openspm_native_backend.dll, openspm_renderer.dll, allowlisted DLLs
  MANIFEST.txt                (SHA-256 of every file)
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import os
import pathlib
import re
import shutil
import subprocess
import sys
import zipfile

HERE = pathlib.Path(__file__).resolve().parent
REPO_DEFAULT = HERE.parents[1]
sys.path.insert(0, str(HERE))

import audit_public_candidate as audit  # noqa: E402

MANIFEST_HEADER = "OpenSPM release manifest"

ROOT_EXECUTABLES = ("OpenSPMNative.exe",)
REQUIRED_RUNTIME = ("openspm_native_backend.dll", "openspm_renderer.dll")

# Third-party runtime DLLs that may be bundled, lower-case name -> component
# whose license must travel with it. Anything else in runtime/ stops the build
# until it is added here together with its license.
THIRD_PARTY_RUNTIME = {
    "sdl3.dll": "SDL3",
    "webgpu_dawn.dll": "Dawn",
    "dxcompiler.dll": "DirectXShaderCompiler",
    "dxil.dll": "DirectXShaderCompiler",
    "vcruntime140.dll": "MSVC-Runtime",
    "vcruntime140_1.dll": "MSVC-Runtime",
    "msvcp140.dll": "MSVC-Runtime",
    "msvcp140_1.dll": "MSVC-Runtime",
    "msvcp140_2.dll": "MSVC-Runtime",
}

# Build by-products that are silently left out of the release.
IGNORED_SUFFIXES = {".pdb", ".ilk", ".exp", ".lib", ".map", ".manifest", ".log"}

DOCS = ("docs/PLAYING.md", "docs/TEXTURE-PACKS.md", "docs/MODDING.md", "docs/MOD-PACKAGES.md")
PROJECT_LICENSE_NAMES = ("LICENSE", "LICENSE.txt", "LICENSE.md", "COPYING", "COPYING.txt")
LICENSE_FILE_NAMES = (
    "LICENSE", "LICENSE.txt", "LICENSE.md", "LICENSE.TXT", "License.txt", "license.txt",
    "COPYING", "COPYING.txt", "LICENSE-MIT", "LICENSE.rst",
)


@dataclasses.dataclass(frozen=True)
class Component:
    name: str
    license_id: str
    source: str
    folder: str                      # regex matched against folder names (lower case)
    triggers: tuple[str, ...]        # runtime file names (lower case) that bundle it
    license_files: tuple[str, ...] = LICENSE_FILE_NAMES


# The renderer DLL links Aurora and, statically, its dependencies. Keep this in
# step with Aurora's CMake (extern/ and FetchContent) when the pin moves.
RENDERER = "openspm_renderer.dll"
COMPONENTS = (
    Component("Aurora", "MIT", "https://github.com/encounter/aurora", r"aurora", (RENDERER,)),
    Component("Dawn", "BSD-3-Clause", "https://dawn.googlesource.com/dawn",
              r"dawn(-.*)?", (RENDERER, "webgpu_dawn.dll")),
    Component("SDL3", "Zlib", "https://github.com/libsdl-org/SDL",
              r"sdl3?(-.*)?", (RENDERER, "sdl3.dll")),
    Component("abseil-cpp", "Apache-2.0", "https://github.com/abseil/abseil-cpp",
              r"abseil(-cpp)?(-.*)?|absl", (RENDERER,)),
    Component("fmt", "MIT", "https://github.com/fmtlib/fmt", r"fmt(-.*)?", (RENDERER,)),
    Component("xxHash", "BSD-2-Clause", "https://github.com/Cyan4973/xxHash",
              r"xxhash(-.*)?", (RENDERER,)),
    Component("Dear ImGui", "MIT", "https://github.com/ocornut/imgui", r"imgui(-.*)?", (RENDERER,)),
    Component("DirectXShaderCompiler", "NCSA AND MIT (see file)",
              "https://github.com/microsoft/DirectXShaderCompiler",
              r"dxc|directxshadercompiler(-.*)?", ("dxcompiler.dll", "dxil.dll"),
              ("LICENSE.TXT", "LICENSE.txt", "LICENSE-LLVM.txt", "LICENSE-MS.txt", "LICENSE")),
    Component("MSVC-Runtime", "Microsoft Visual C++ redistributable terms",
              "https://learn.microsoft.com/cpp/windows/redistributing-visual-cpp-files",
              r"vc_?redist|msvc-runtime", tuple(k for k, v in THIRD_PARTY_RUNTIME.items()
                                              if v == "MSVC-Runtime"),
              ("redist.txt", "LICENSE.txt", "LICENSE")),
)
COMPONENTS_BY_NAME = {c.name.lower(): c for c in COMPONENTS}

SEARCH_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".openspm", "user-openspm"}
SEARCH_MAX_DEPTH = 6


class ReleaseError(RuntimeError):
    pass


@dataclasses.dataclass
class Plan:
    files: dict[str, pathlib.Path] = dataclasses.field(default_factory=dict)   # release path -> source
    generated: dict[str, bytes] = dataclasses.field(default_factory=dict)      # release path -> content
    rejected: list[str] = dataclasses.field(default_factory=list)
    skipped: list[str] = dataclasses.field(default_factory=list)
    licenses: dict[str, pathlib.Path] = dataclasses.field(default_factory=dict)
    missing_licenses: list[str] = dataclasses.field(default_factory=list)


def _forbidden_reason(relative: pathlib.PurePath) -> str | None:
    name = relative.name.lower()
    if pathlib.PurePath(name).suffix in audit.PROHIBITED_EXTENSIONS:
        return "game/disc/generated extension"
    if name in audit.PROHIBITED_FILENAMES:
        return "player/game-derived file name"
    bad = {p.lower() for p in relative.parts[:-1]} & audit.PROHIBITED_DIRECTORY_NAMES
    if bad:
        return "private/extracted directory " + ", ".join(sorted(bad))
    return None


def collect_binaries(build: pathlib.Path, plan: Plan, include_debug_mod: bool) -> set[str]:
    """Allowlisted binaries from the build tree; returns lower-case runtime names."""
    for name in ROOT_EXECUTABLES:
        source = build / name
        if not source.is_file():
            raise ReleaseError(f"build output is missing {name}: {source}")
        plan.files[name] = source

    runtime = build / "runtime"
    if not runtime.is_dir():
        raise ReleaseError(f"build output has no runtime/ folder: {runtime}")
    bundled: set[str] = set()
    unknown: list[str] = []
    for source in sorted(runtime.rglob("*")):
        if not source.is_file():
            continue
        relative = pathlib.PurePath("runtime") / source.relative_to(runtime)
        reason = _forbidden_reason(relative)
        if reason:
            plan.rejected.append(f"{relative} ({reason})")
            print(f"[release] rejected (never packaged): {relative} ({reason})")
            continue
        if source.suffix.lower() in IGNORED_SUFFIXES:
            plan.skipped.append(str(relative))
            continue
        lower = source.name.lower()
        if len(relative.parts) != 2:
            unknown.append(str(relative))
            continue
        if source.name in REQUIRED_RUNTIME or lower in THIRD_PARTY_RUNTIME:
            plan.files[relative.as_posix()] = source
            bundled.add(lower)
            continue
        unknown.append(str(relative))
    missing = [n for n in REQUIRED_RUNTIME if n.lower() not in bundled]
    if missing:
        raise ReleaseError("build output is missing runtime file(s): " + ", ".join(missing))
    if unknown:
        raise ReleaseError(
            "runtime file(s) not on the release allowlist (add them to THIRD_PARTY_RUNTIME "
            "with their license, or remove them from the build): " + ", ".join(unknown)
        )

    if include_debug_mod:
        mod = build / "openspm_debug.dll"
        if not mod.is_file():
            raise ReleaseError(f"--include-debug-mod: {mod} was not built")
        plan.files["mods/openspm_debug.dll"] = mod
    return bundled


def collect_repo_files(repo: pathlib.Path, plan: Plan, allow_missing_project_license: bool) -> None:
    example = repo / "OpenSPM.ini.example"
    if not example.is_file():
        raise ReleaseError(f"settings template is missing: {example}")
    leaked = audit.ini_player_path(example.read_text(encoding="utf-8", errors="replace"))
    if leaked:
        raise ReleaseError(f"OpenSPM.ini.example carries a player path ({leaked}); ship it empty")
    plan.files["OpenSPM.ini.example"] = example
    plan.files["OpenSPM.ini"] = example   # a fresh settings file, never a player's

    for doc in DOCS:
        source = repo / doc
        if not source.is_file():
            raise ReleaseError(f"documentation file is missing: {source}")
        plan.files[doc] = source

    for name in PROJECT_LICENSE_NAMES:
        if (repo / name).is_file():
            plan.files["LICENSE.txt"] = repo / name
            break
    else:
        if not allow_missing_project_license:
            raise ReleaseError(
                "OpenSPM has no LICENSE file at the repository root; choose the project "
                "license before a public release (or pass --allow-missing-project-license "
                "for an internal test package)"
            )


def find_license(component: Component, roots: list[pathlib.Path]) -> pathlib.Path | None:
    pattern = re.compile(component.folder + r"$")
    best: tuple[int, str, pathlib.Path] | None = None
    for root in roots:
        root = root.resolve()
        if not root.is_dir():
            continue
        # The search root itself counts (e.g. --license-search <aurora checkout>).
        for current, dirs, _files in os.walk(root):
            here = pathlib.Path(current)
            depth = len(here.relative_to(root).parts)
            dirs[:] = sorted(d for d in dirs if d.lower() not in SEARCH_SKIP_DIRS)
            if depth >= SEARCH_MAX_DEPTH:
                dirs[:] = []
            if not pattern.fullmatch(here.name.lower()):
                continue
            for name in component.license_files:
                candidate = here / name
                if candidate.is_file():
                    key = (depth, str(candidate), candidate)
                    if best is None or key[:2] < best[:2]:
                        best = key
                    break
    return best[2] if best else None


def collect_licenses(bundled: set[str], roots: list[pathlib.Path], explicit: dict[str, pathlib.Path],
                     plan: Plan) -> list[Component]:
    needed = [c for c in COMPONENTS if any(t in bundled for t in c.triggers)]
    for name in explicit:
        if name.lower() not in COMPONENTS_BY_NAME:
            raise ReleaseError(f"--license names an unknown component: {name}")
    for component in needed:
        source = explicit.get(component.name.lower()) or find_license(component, roots)
        if source is None or not source.is_file():
            plan.missing_licenses.append(component.name)
            continue
        plan.licenses[component.name] = source
        plan.files[f"licenses/{_license_file_name(component)}"] = source
    return needed


def _license_file_name(component: Component) -> str:
    return re.sub(r"[^A-Za-z0-9.-]+", "-", component.name) + ".txt"


def notices_text(needed: list[Component], plan: Plan, version: str) -> str:
    lines = [
        f"OpenSPM {version} - third-party components",
        "",
        "This package contains OpenSPM's own code and the redistributable components",
        "listed below, each under its own license (full texts in licenses\\).",
        "It contains no Nintendo code or data: you supply your own copy of the game,",
        "and the native game module is created on your PC from that copy.",
        "",
    ]
    for component in needed:
        lines += [
            component.name,
            f"  License: {component.license_id}",
            f"  Source:  {component.source}",
            f"  Text:    licenses\\{_license_file_name(component)}",
            "",
        ]
    lines += [
        "Not included: Wiimms ISO Tools (wit). Install it yourself from wit.wiimm.de;",
        "OpenSPM runs your copy to read your disc image.",
        "",
    ]
    return "\r\n".join(lines)


def readme_text(version: str) -> str:
    return "\r\n".join([
        f"OpenSPM {version}",
        "",
        "A native PC port of Super Paper Mario (USA, R8PE01, disc revision 2).",
        "You need your own copy of the game; nothing from the game is included.",
        "",
        "1. Unpack this folder anywhere (about 5 GB free space are needed later).",
        "2. Install Wiimms ISO Tools (wit.wiimm.de) and put it in wit\\bin\\wit.exe",
        "   next to OpenSPMNative.exe, or on PATH.",
        "3. Start OpenSPMNative.exe and pick your .iso / .wbfs / .wia.",
        "",
        "Settings: OpenSPM.ini (or the F1 menu in game). Help: docs\\PLAYING.md.",
        "Licenses: LICENSE.txt, THIRD-PARTY-NOTICES.txt, licenses\\.",
        "",
    ])


def _git_revision(repo: pathlib.Path) -> str:
    try:
        out = subprocess.run(["git", "-C", str(repo), "rev-parse", "--short=12", "HEAD"],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, check=False)
        return out.stdout.strip() or "unknown"
    except OSError:
        return "unknown"


def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prepare_output(output: pathlib.Path, force: bool) -> None:
    if output.exists():
        manifest = output / "MANIFEST.txt"
        ours = manifest.is_file() and manifest.read_text(
            encoding="utf-8", errors="replace").startswith(MANIFEST_HEADER)
        if not force or not ours:
            raise ReleaseError(
                f"output folder exists: {output} "
                + ("(pass --force to replace it)" if ours else "(not a previous release folder; refusing)")
            )
        shutil.rmtree(output)
    output.mkdir(parents=True)


def write_release(plan: Plan, output: pathlib.Path, version: str, revision: str) -> list[str]:
    for relative, source in sorted(plan.files.items()):
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    for relative, content in sorted(plan.generated.items()):
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    (output / "mods").mkdir(exist_ok=True)
    (output / "wit").mkdir(exist_ok=True)

    entries = sorted(p.relative_to(output).as_posix() for p in output.rglob("*") if p.is_file())
    lines = [
        MANIFEST_HEADER,
        f"version={version}",
        f"source_revision={revision}",
        f"created_utc={dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        "",
    ]
    lines += [f"{_sha256(output / e)}  {e}" for e in entries]
    (output / "MANIFEST.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return entries + ["MANIFEST.txt"]


def write_zip(output: pathlib.Path, zip_path: pathlib.Path, entries: list[str]) -> None:
    top = output.name
    temporary = zip_path.with_suffix(zip_path.suffix + ".part")
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    temporary.unlink(missing_ok=True)
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for directory in ("mods", "wit"):
            archive.writestr(zipfile.ZipInfo(f"{top}/{directory}/"), b"")
        for entry in sorted(entries):
            archive.write(output / entry, f"{top}/{entry}")
    with zipfile.ZipFile(temporary) as archive:
        names = {n[len(top) + 1:] for n in archive.namelist() if not n.endswith("/")}
    if names != set(entries):
        temporary.unlink()
        raise ReleaseError("zip contents differ from the audited release folder")
    for name in names:
        reason = _forbidden_reason(pathlib.PurePath(name))
        if reason:
            temporary.unlink()
            raise ReleaseError(f"zip would contain {name} ({reason})")
    os.replace(temporary, zip_path)


def build_release(args: argparse.Namespace) -> dict[str, object]:
    repo = args.repo.resolve()
    build = args.build_dir.resolve()
    output = args.output.resolve()
    if output == build or build in output.parents:
        raise ReleaseError("--output must be outside the build tree")
    explicit: dict[str, pathlib.Path] = {}
    for item in args.license:
        if "=" not in item:
            raise ReleaseError(f"--license expects NAME=PATH, got {item!r}")
        name, path = item.split("=", 1)
        explicit[name.strip().lower()] = pathlib.Path(path).resolve()

    version = args.version or _git_revision(repo)
    plan = Plan()
    bundled = collect_binaries(build, plan, args.include_debug_mod)
    collect_repo_files(repo, plan, args.allow_missing_project_license)
    needed = collect_licenses(bundled, args.license_search, explicit, plan)
    if plan.missing_licenses and not args.allow_missing_licenses:
        raise ReleaseError(
            "no license text found for bundled component(s): " + ", ".join(plan.missing_licenses)
            + ". Point --license-search at their sources/packages or pass --license NAME=PATH."
        )
    plan.generated["THIRD-PARTY-NOTICES.txt"] = notices_text(needed, plan, version).encode("utf-8")
    plan.generated["README.txt"] = readme_text(version).encode("utf-8")

    prepare_output(output, args.force)
    entries = write_release(plan, output, version, _git_revision(repo))
    try:
        audit.audit(output, require_layout=True)
    except RuntimeError as exc:
        raise ReleaseError(f"release audit failed, nothing was zipped: {exc}") from exc

    zip_path = None
    if args.zip:
        zip_path = output.with_name(output.name + ".zip") if args.zip is True else pathlib.Path(args.zip).resolve()
        write_zip(output, zip_path, entries)

    for line in plan.skipped:
        print(f"[release] skipped build by-product: {line}")
    for name in plan.missing_licenses:
        print(f"[release] WARNING: no license text for {name} (--allow-missing-licenses)")
    print(f"[release] {len(entries)} files in {output}")
    if zip_path:
        print(f"[release] zip: {zip_path}")
    return {"output": output, "zip": zip_path, "entries": entries, "plan": plan}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--build-dir", type=pathlib.Path, required=True)
    parser.add_argument("--repo", type=pathlib.Path, default=REPO_DEFAULT)
    parser.add_argument("--output", type=pathlib.Path, required=True, help="release folder to create")
    parser.add_argument("--zip", nargs="?", const=True, default=None,
                        help="also write a zip (default: <output>.zip)")
    parser.add_argument("--version", help="release name (default: git revision)")
    parser.add_argument("--license-search", type=pathlib.Path, action="append", default=[])
    parser.add_argument("--license", action="append", default=[], metavar="NAME=PATH")
    parser.add_argument("--include-debug-mod", action="store_true",
                        help="ship mods/openspm_debug.dll (developer builds)")
    parser.add_argument("--allow-missing-licenses", action="store_true",
                        help="internal test packages only: continue without some license texts")
    parser.add_argument("--allow-missing-project-license", action="store_true",
                        help="internal test packages only: no OpenSPM LICENSE yet")
    parser.add_argument("--force", action="store_true", help="replace a previous release folder")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        build_release(parse_args(argv))
    except (ReleaseError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
