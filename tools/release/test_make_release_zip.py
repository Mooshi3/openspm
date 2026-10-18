#!/usr/bin/env python3
"""Host test for the release packager and the public-candidate audit.

Builds a fake build/candidate tree that mixes the real release files with
everything that must never ship (disc images, main.dol, relF.rel/.bin,
archives, textures, the per-player openspm_local_native.dll, generated LLVM
objects, .openspm/ and User-OpenSPM/ contents, a player's OpenSPM.ini) and
asserts that none of it reaches the release folder or zip, and that disguised
payloads (a DOL renamed to a DLL, a DLL exporting the local-module entry point
under an allowlisted name, a disc image renamed to .md) fail the build.
"""

from __future__ import annotations

import contextlib
import io
import pathlib
import shutil
import struct
import sys
import tempfile
import zipfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import audit_public_candidate as audit  # noqa: E402
import make_release_zip as release  # noqa: E402


def make_pe(exports: list[str] = ()) -> bytes:
    """Minimal PE32+ image with one section holding an export directory."""
    section_rva, raw_ptr = 0x1000, 0x200
    names = [n.encode() + b"\0" for n in exports]
    directory_size = 40
    names_array = directory_size
    strings = names_array + 4 * len(names)
    body = bytearray(directory_size + 4 * len(names))
    offset = strings
    string_blob = b""
    for i, name in enumerate(names):
        struct.pack_into("<I", body, names_array + 4 * i, section_rva + offset)
        string_blob += name
        offset += len(name)
    struct.pack_into("<I", body, 0x18, len(names))
    struct.pack_into("<I", body, 0x20, section_rva + names_array)
    body += string_blob
    body += b"\0" * (-len(body) % 0x200 or 0x200)

    image = bytearray(raw_ptr)
    image[0:2] = b"MZ"
    struct.pack_into("<I", image, 0x3C, 0x40)
    image[0x40:0x44] = b"PE\0\0"
    coff = 0x44
    struct.pack_into("<HHIIIHH", image, coff, 0x8664, 1, 0, 0, 0, 0xF0, 0x2022)
    optional = coff + 20
    struct.pack_into("<H", image, optional, 0x20B)
    struct.pack_into("<I", image, optional + 108, 16)            # NumberOfRvaAndSizes
    if names:
        struct.pack_into("<II", image, optional + 112, section_rva, len(body))
    section = optional + 0xF0
    image[section:section + 8] = b".edata\0\0"
    struct.pack_into("<IIII", image, section + 8, len(body), section_rva, len(body), raw_ptr)
    return bytes(image) + bytes(body)


def make_dol() -> bytes:
    data = bytearray(0x200)
    struct.pack_into(">I", data, 0x00, 0x100)          # text0 file offset
    struct.pack_into(">I", data, 0x48, 0x80004000)     # text0 address
    struct.pack_into(">I", data, 0x90, 0x100)          # text0 size
    struct.pack_into(">I", data, 0xE0, 0x80004000)     # entry point
    return bytes(data)


def make_rel() -> bytes:
    data = bytearray(0x100)
    struct.pack_into(">IIIII", data, 0, 2, 0, 0, 17, 0x4C)
    struct.pack_into(">I", data, 0x1C, 3)
    return bytes(data)


def wii_disc_header() -> bytes:
    data = bytearray(0x100)
    data[0:6] = b"R8PE01"
    data[7] = 2
    struct.pack_into(">I", data, 0x18, 0x5D1C9EA3)
    return bytes(data)


def write(path: pathlib.Path, data: bytes | str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode() if isinstance(data, str) else data)


FORBIDDEN_IN_RUNTIME = {
    "runtime/native/openspm_local_native.dll": make_pe(["openspm_native_module_query"]),
    "runtime/main.dol": make_dol(),
    "runtime/relF.rel": make_rel(),
    "runtime/relF.bin": b"\x10\x00\x10\x00" + b"\0" * 64,
    "runtime/game.wbfs": b"WBFS" + b"\0" * 64,
    "runtime/game.iso": wii_disc_header(),
    "runtime/map.arc": b"\x55\xAA\x38\x2D" + b"\0" * 64,
    "runtime/tex.tpl": b"\x00\x20\xAF\x30" + b"\0" * 64,
    "runtime/chunk_0001.obj": b"\x64\x86" + b"\0" * 64,
    "runtime/generated/chunk_0002.o": b"\x7fELF" + b"\0" * 64,
}

# Files in the candidate that the packager must never even look at.
PRIVATE_IN_CANDIDATE = {
    "User-OpenSPM/native/openspm_local_native.dll": make_pe(["openspm_native_module_query"]),
    "User-OpenSPM/game/sys/main.dol": make_dol(),
    "User-OpenSPM/game/sys/boot.bin": wii_disc_header(),
    "User-OpenSPM/game/files/rel/relF.bin": b"\x10" + b"\0" * 63,
    ".openspm/m14/llvm-candidate/native-output/openspm_local_native.dll": make_pe(),
    "texturepacks/SPM HD/tex1_64x64_0123.png": b"\x89PNG\r\n\x1a\n",
    "OpenSPM.ini": "[Game]\npath = C:\\Users\\player\\SPM.wbfs\n",
    "m14_synthetic_native_module.dll": make_pe(["openspm_native_module_query"]),
}


def build_fixture(base: pathlib.Path) -> dict[str, pathlib.Path]:
    repo = base / "repo"
    write(repo / "OpenSPM.ini.example", "; template\n[Game]\npath =\nwit =\n")
    for doc in release.DOCS:
        write(repo / doc, f"# {doc}\n")
    write(repo / "LICENSE", "OpenSPM test license\n")

    build = base / "build"
    write(build / "OpenSPMNative.exe", make_pe())
    write(build / "openspm_debug.dll", make_pe(["openspm_mod_query"]))
    write(build / "runtime/openspm_native_backend.dll", make_pe(["openspm_backend_query"]))
    write(build / "runtime/openspm_renderer.dll", make_pe(["openspm_renderer_query"]))
    write(build / "runtime/SDL3.dll", make_pe(["SDL_Init"]))
    write(build / "runtime/dxcompiler.dll", make_pe(["DxcCreateInstance"]))
    write(build / "runtime/dxil.dll", make_pe(["DxcCreateInstance"]))
    write(build / "runtime/openspm_renderer.pdb", b"Microsoft C/C++ MSF 7.00\r\n")
    for relative, data in {**FORBIDDEN_IN_RUNTIME, **PRIVATE_IN_CANDIDATE}.items():
        write(build / relative, data)

    deps = base / "deps"
    for folder, name in (
        ("aurora", "LICENSE"),
        ("aurora/extern/dawn", "LICENSE"),
        ("aurora/extern/SDL", "LICENSE.txt"),
        ("aurora/extern/abseil-cpp", "LICENSE"),
        ("aurora/extern/fmt", "LICENSE"),
        ("aurora/extern/xxHash", "LICENSE"),
        ("aurora/extern/imgui", "LICENSE.txt"),
        ("build/_deps/dxc", "LICENSE.TXT"),
    ):
        write(deps / folder / name, f"license text of {folder}\n")
    return {"repo": repo, "build": build, "deps": deps, "out": base / "dist" / "OpenSPM-test"}


def args_for(paths: dict[str, pathlib.Path], *extra: str) -> list[str]:
    return [
        "--repo", str(paths["repo"]), "--build-dir", str(paths["build"]),
        "--output", str(paths["out"]), "--version", "test",
        "--license-search", str(paths["deps"] / "aurora"),
        "--license-search", str(paths["deps"] / "build"),
        "--force", *extra,
    ]


def run_release(argv: list[str]) -> tuple[int, str]:
    stream = io.StringIO()
    with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
        rc = release.main(argv)
    return rc, stream.getvalue()


def expect_failure(argv: list[str], needle: str, label: str) -> None:
    rc, output = run_release(argv)
    assert rc != 0, f"{label}: packager accepted it\n{output}"
    assert needle in output, f"{label}: expected {needle!r} in\n{output}"


def test_clean_release(paths: dict[str, pathlib.Path]) -> None:
    rc, output = run_release(args_for(paths, "--zip"))
    assert rc == 0, output
    out = paths["out"]
    files = {p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()}
    expected = {
        "OpenSPMNative.exe", "OpenSPM.ini", "OpenSPM.ini.example", "README.txt", "LICENSE.txt",
        "THIRD-PARTY-NOTICES.txt", "MANIFEST.txt",
        "runtime/openspm_native_backend.dll", "runtime/openspm_renderer.dll",
        "runtime/SDL3.dll", "runtime/dxcompiler.dll", "runtime/dxil.dll",
        *release.DOCS,
        *(f"licenses/{n}.txt" for n in ("Aurora", "Dawn", "SDL3", "abseil-cpp", "fmt", "xxHash",
                                         "Dear-ImGui", "DirectXShaderCompiler")),
    }
    assert files == expected, f"unexpected release contents:\n+{sorted(files - expected)}\n-{sorted(expected - files)}"
    assert "path =\n" in (out / "OpenSPM.ini").read_text(), "OpenSPM.ini is not the empty template"
    assert "player" not in (out / "OpenSPM.ini").read_text()
    for relative in FORBIDDEN_IN_RUNTIME:
        assert f"rejected (never packaged): {relative}" in output, f"{relative} not reported as rejected"
    assert "skipped build by-product: runtime/openspm_renderer.pdb" in output
    notices = (out / "THIRD-PARTY-NOTICES.txt").read_text()
    for name in ("Aurora", "Dawn", "SDL3", "abseil-cpp", "fmt", "xxHash", "Dear ImGui"):
        assert name in notices, name
    assert (out / "licenses/SDL3.txt").read_text() == "license text of aurora/extern/SDL\n"
    manifest = (out / "MANIFEST.txt").read_text()
    assert manifest.startswith(release.MANIFEST_HEADER)
    assert "runtime/openspm_renderer.dll" in manifest

    zip_path = out.with_name(out.name + ".zip")
    with zipfile.ZipFile(zip_path) as archive:
        names = {n.split("/", 1)[1] for n in archive.namelist() if not n.endswith("/")}
    assert names == expected, "zip differs from the release folder"
    for name in names:
        lower = name.lower()
        assert not lower.endswith((".dol", ".rel", ".bin", ".wbfs", ".iso", ".arc", ".tpl", ".obj", ".o"))
        assert "openspm_local_native" not in lower and "user-openspm" not in lower
        assert ".openspm" not in lower and "texturepacks" not in lower


def test_debug_mod_option(paths: dict[str, pathlib.Path]) -> None:
    rc, output = run_release(args_for(paths, "--include-debug-mod"))
    assert rc == 0, output
    assert (paths["out"] / "mods/openspm_debug.dll").is_file()


def test_unknown_runtime_file(paths: dict[str, pathlib.Path]) -> None:
    extra = paths["build"] / "runtime/libsomething.dll"
    write(extra, make_pe())
    try:
        expect_failure(args_for(paths), "not on the release allowlist", "unknown runtime DLL")
    finally:
        extra.unlink()


def test_disguised_local_module(paths: dict[str, pathlib.Path]) -> None:
    sdl = paths["build"] / "runtime/SDL3.dll"
    original = sdl.read_bytes()
    write(sdl, make_pe(["SDL_Init", "openspm_native_module_query"]))
    try:
        expect_failure(args_for(paths), "player-local native game module", "renamed local module")
    finally:
        write(sdl, original)
    assert not paths["out"].with_name(paths["out"].name + ".zip.part").exists()


def test_disguised_dol(paths: dict[str, pathlib.Path]) -> None:
    dxil = paths["build"] / "runtime/dxil.dll"
    original = dxil.read_bytes()
    write(dxil, make_dol())
    try:
        expect_failure(args_for(paths), "DOL executable header", "DOL renamed to a DLL")
    finally:
        write(dxil, original)


def test_disguised_disc_in_docs(paths: dict[str, pathlib.Path]) -> None:
    doc = paths["repo"] / release.DOCS[0]
    original = doc.read_bytes()
    write(doc, wii_disc_header())
    try:
        expect_failure(args_for(paths), "Wii disc header", "disc image renamed to .md")
    finally:
        write(doc, original)


def test_missing_licenses(paths: dict[str, pathlib.Path]) -> None:
    argv = [a for a in args_for(paths) if a not in (str(paths["deps"] / "aurora"),)]
    argv.remove("--license-search")
    expect_failure(argv, "no license text found", "missing third-party licenses")
    rc, output = run_release(argv + ["--allow-missing-licenses"])
    assert rc == 0 and "WARNING: no license text for Aurora" in output, output
    # An explicit --license fills a gap.
    rc, output = run_release(argv + ["--allow-missing-licenses",
                                     "--license", f"SDL3={paths['deps'] / 'aurora/extern/SDL/LICENSE.txt'}"])
    assert rc == 0 and (paths["out"] / "licenses/SDL3.txt").is_file(), output


def test_missing_project_license(paths: dict[str, pathlib.Path]) -> None:
    license_file = paths["repo"] / "LICENSE"
    license_file.rename(license_file.with_name("LICENSE.off"))
    try:
        expect_failure(args_for(paths), "no LICENSE file", "missing project license")
        rc, output = run_release(args_for(paths, "--allow-missing-project-license"))
        assert rc == 0, output
    finally:
        license_file.with_name("LICENSE.off").rename(license_file)


def test_template_with_player_path(paths: dict[str, pathlib.Path]) -> None:
    example = paths["repo"] / "OpenSPM.ini.example"
    original = example.read_bytes()
    write(example, "[Game]\npath = D:\\Games\\SPM.iso\n")
    try:
        expect_failure(args_for(paths), "carries a player path", "template with a path")
    finally:
        write(example, original)


def test_output_safety(paths: dict[str, pathlib.Path]) -> None:
    stranger = paths["out"].parent / "not-a-release"
    write(stranger / "keep.txt", "mine")
    argv = args_for(paths)
    argv[argv.index("--output") + 1] = str(stranger)
    expect_failure(argv, "not a previous release folder", "foreign output folder")
    assert (stranger / "keep.txt").read_text() == "mine"
    argv[argv.index("--output") + 1] = str(paths["build"] / "dist")
    expect_failure(argv, "outside the build tree", "output inside build tree")


def test_audit_rejects_each_payload(base: pathlib.Path) -> None:
    good = base / "audit-good"
    write(good / "OpenSPMNative.exe", make_pe())
    write(good / "runtime/openspm_native_backend.dll", make_pe(["openspm_backend_query"]))
    write(good / "runtime/openspm_renderer.dll", make_pe(["openspm_renderer_query"]))
    write(good / "docs/PLAYING.md", "# playing\n")
    audit.audit(good, require_layout=True)

    payloads = {
        **FORBIDDEN_IN_RUNTIME,
        **PRIVATE_IN_CANDIDATE,
        "runtime/innocent.dll": make_pe(["openspm_native_module_query"]),
        "runtime/notpe.dll": b"not a PE at all",
        "docs/disc.txt": wii_disc_header(),
        "docs/image.dat": b"WBFS" + b"\0" * 64,
        "docs/archive.dat": b"Yaz0" + b"\0" * 64,
        "docs/main.txt": make_dol(),
        "docs/module.txt": make_rel(),
        "common-key.txt": b"\0" * 16,
        "Super Paper Mario.wia": b"WIA\x01" + b"\0" * 64,
        "SPM.rvz": b"RVZ\x01" + b"\0" * 64,
    }
    for relative, data in payloads.items():
        case = base / "audit-case"
        if case.exists():
            shutil.rmtree(case)
        shutil.copytree(good, case)
        write(case / relative, data)
        try:
            audit.audit(case, require_layout=True)
        except RuntimeError:
            continue
        raise AssertionError(f"audit accepted {relative}")

    missing = base / "audit-missing"
    shutil.copytree(good, missing)
    (missing / "runtime/openspm_renderer.dll").unlink()
    try:
        audit.audit(missing, require_layout=True)
    except RuntimeError:
        pass
    else:
        raise AssertionError("audit accepted a candidate without the renderer")


def test_pe_parser() -> None:
    assert audit.pe_export_names(make_pe()) == []
    assert audit.pe_export_names(make_pe(["a", "bc"])) == [b"a", b"bc"]
    assert audit.pe_export_names(b"MZ" + b"\0" * 10) is None


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="openspm-release-test-") as temp:
        base = pathlib.Path(temp)
        paths = build_fixture(base)
        test_pe_parser()
        test_clean_release(paths)
        test_debug_mod_option(paths)
        test_unknown_runtime_file(paths)
        test_disguised_local_module(paths)
        test_disguised_dol(paths)
        test_disguised_disc_in_docs(paths)
        test_missing_licenses(paths)
        test_missing_project_license(paths)
        test_template_with_player_path(paths)
        test_output_safety(paths)
        test_audit_rejects_each_payload(base)
    print("PASS: release packager allowlist + licenses + zip, public-candidate audit rejects "
          "disc images, DOL/REL/BIN/ARC/TPL, local native module (by name and export), "
          "generated objects, private folders and disguised payloads")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
