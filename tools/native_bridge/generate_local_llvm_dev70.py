#!/usr/bin/env python3
"""Generate an OpenSPM player-local LLVM-native SPM module input tree.

This is a compiler pipeline only. It never launches or links ModernGekko,
Dolphin, or RecompCore. DolRecomp's LLVM backend is used to turn the verified
player-owned Broadway code image into host-native objects; OpenSPM remains the
runtime, platform layer and renderer.

The compiler input is a private codegen-only DOL made from the clean main DOL
plus the proven relocated relF executable section. Retail /rel/relF.bin is
Nintendo LZ10-compressed and is decoded locally before relocation. The clean
main DOL remains the source identity OpenSPM verifies and the image the runtime
boots.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import augment_llvm_bl_continuations  # noqa: E402
import augment_llvm_observed_indirect_entries  # noqa: E402
import build_relf_codegen_dol  # noqa: E402
import openspm_code_patches  # noqa: E402
import derive_gx_dl_semantics  # noqa: E402
import derive_v19_llvm_seams  # noqa: E402
import probe_dolrecomp_llvm  # noqa: E402
import relf_bss_relocation_census  # noqa: E402
import relf_input  # noqa: E402
import validate_llvm_generated  # noqa: E402


def run(
    command: list[str],
    *,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(
        command,
        check=True,
        env=env,
        cwd=str(cwd) if cwd is not None else None,
    )


def locate_generated(root: Path, game_id: str) -> Path:
    """Return the current generated tree and normalize Wii title-aware names.

    Pinned DolRecomp writes Wii output as
    `<game>_generated/<game>.h` + `<game>.c`, while the original titleless path
    writes `generated/generated.h` + `generated.c`. OpenSPM historically consumes
    the generic names. The one-time titleless->Wii directory migration preserves
    the old generic files beside the new title-aware files, so blindly locating
    `generated.h` can select a stale manifest after the Wii generation succeeds.

    Prefer the current title-aware artifacts whenever their directory exists,
    overwrite the generic aliases from those exact files, and only fall back to
    legacy generic discovery when no Wii title-aware tree exists at all.
    """
    title_dir = root / f"{game_id}_generated"
    if title_dir.is_dir() and (title_dir / "chunks").is_dir():
        title_header = title_dir / f"{game_id}.h"
        title_manifest = title_dir / f"{game_id}.c"
        missing = [
            path.name for path in (title_header, title_manifest)
            if not path.is_file()
        ]
        if missing:
            raise RuntimeError(
                "DolRecomp Wii title-aware output is incomplete; missing "
                + ", ".join(missing)
            )

        shutil.copy2(title_header, title_dir / "generated.h")
        shutil.copy2(title_manifest, title_dir / "generated.c")
        title_symbols = title_dir / f"{game_id}_symbols.h"
        if title_symbols.is_file():
            shutil.copy2(title_symbols, title_dir / "generated_symbols.h")
        print(
            "[OpenSPM] Normalized current DolRecomp Wii title-aware output: "
            f"{title_header.name}/{title_manifest.name} -> generated.h/generated.c",
            flush=True,
        )
        return title_dir

    direct = root / "generated"
    if direct.is_dir() and (direct / "generated.h").is_file():
        return direct
    matches = [
        path.parent for path in root.rglob("generated.h")
        if (path.parent / "chunks").is_dir()
    ]
    if len(matches) != 1:
        listing = ", ".join(str(path) for path in matches) or "none"
        raise RuntimeError(f"expected one DolRecomp generated tree, found {listing}")
    return matches[0]


def prepare_private_titles_database(work: Path, game_id: str) -> Path:
    """Keep DolRecomp in Wii/title-aware mode without a network dependency.

    Pinned DolRecomp treats a missing database/titles.txt as an instruction to
    force GameCube/titleless mode even when --cpu broadway is explicit. The
    database is only needed here to establish that a Wii title id is present
    and to provide a display name, so OpenSPM supplies a one-entry private file
    under the local build tree instead of downloading the full GameTDB list.
    """
    database = work / "database"
    database.mkdir(parents=True, exist_ok=True)
    titles = database / "titles.txt"
    contents = f"{game_id} = OpenSPM player-owned Wii title\n"
    titles.write_text(contents, encoding="utf-8", newline="\n")
    if titles.read_text(encoding="utf-8") != contents:
        raise RuntimeError("failed to verify private DolRecomp Wii title database")
    return titles


def migrate_titleless_generation(output_root: Path, game_id: str) -> None:
    """Preserve exact stamped objects when correcting GC/titleless naming.

    With database/titles.txt missing, pinned DolRecomp used output/generated.
    Correct Wii title-aware mode uses output/<game>_generated. Rename the old
    local tree once so DOLRECOMP_LLVM_RESUME can validate/reuse its existing
    object stamps rather than making the player regenerate thousands of chunks.
    """
    old = output_root / "generated"
    corrected = output_root / f"{game_id}_generated"
    if not old.is_dir() or corrected.exists():
        return
    old.rename(corrected)
    print(
        "[OpenSPM] Migrated prior titleless LLVM generation tree into Wii title-aware "
        f"location: {corrected}",
        flush=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dolrecomp", required=True, type=Path)
    parser.add_argument("--main-dol", required=True, type=Path)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--game-dir", type=Path,
        help="extracted player game; prefers retail relF.bin then relF.rel fallback",
    )
    source.add_argument("--relf", type=Path,
                        help="explicit relF.bin (LZ10) or uncompressed relF.rel")
    parser.add_argument("--work-dir", required=True, type=Path)
    parser.add_argument("--game-id", default="R8PE01")
    parser.add_argument(
        "--jobs", type=int,
        default=max(1, min(16, os.cpu_count() or 4)),
    )
    parser.add_argument("--skip-probe", action="store_true",
                        help="skip the synthetic LLVM capability probe")
    args = parser.parse_args()

    try:
        dolrecomp = args.dolrecomp.resolve()
        main_dol = args.main_dol.resolve()
        work = args.work_dir.resolve()
        if not dolrecomp.is_file():
            raise RuntimeError(f"DolRecomp executable does not exist: {dolrecomp}")
        if not main_dol.is_file():
            raise RuntimeError(f"main DOL does not exist: {main_dol}")
        if len(args.game_id) != 6 or not args.game_id.isalnum():
            raise RuntimeError("--game-id must contain six letters/numbers")
        if not 1 <= args.jobs <= 256:
            raise RuntimeError("--jobs must be 1..256")

        # Keep the generated tree across LLVM A/B iterations. DolRecomp stamps
        # every object with a hash covering instructions, ABI policy, target,
        # semantics, codegen fingerprint and OpenSPM's emitter-source digest.
        # Its DOLRECOMP_LLVM_RESUME path therefore reuses only exact-valid local
        # objects and regenerates anything stale. This avoids deleting/copying
        # ~14k proven objects for bridge-only diagnostics or relinks.
        work.mkdir(parents=True, exist_ok=True)
        titles = prepare_private_titles_database(work, args.game_id)

        if not args.skip_probe:
            probe_dir = work / "llvm-probe"
            if probe_dir.exists():
                shutil.rmtree(probe_dir)
            probe_dir.mkdir()
            if not probe_dolrecomp_llvm.probe(dolrecomp, probe_dir):
                raise RuntimeError(
                    "selected DolRecomp cannot emit LLVM native objects; "
                    "an LLVM-enabled DolRecomp 19/20 build is required"
                )

        relf = (
            relf_input.load_relf(args.relf)
            if args.relf
            else relf_input.load_from_game(args.game_dir)
        )
        rel_bytes = bytearray(relf.data)
        sections, rel_meta = build_relf_codegen_dol.parse_sections(rel_bytes)
        bss_stats = relf_bss_relocation_census.census(
            rel_bytes, sections, rel_meta
        )
        relocation_stats = build_relf_codegen_dol.relocate(
            rel_bytes, sections, rel_meta
        )
        exec_section = sections[build_relf_codegen_dol.REL_EXEC_INDEX]
        exec_bytes = bytes(
            rel_bytes[
                exec_section.file_offset:exec_section.file_offset + exec_section.size
            ]
        )
        clean_bytes = main_dol.read_bytes()
        augmented, _ = build_relf_codegen_dol.augment_dol(clean_bytes, exec_bytes)
        # OpenSPM content patches (state/openspm-code-patches.json): exact,
        # vanilla-neutral recipes the backend also applies to the boot image.
        augmented, code_patches = openspm_code_patches.apply(augmented)
        print(
            "[OpenSPM] content code patches: " + (", ".join(code_patches) or "none"),
            flush=True,
        )
        codegen_dol = work / "R8PE01-codegen-only-main-plus-relF.dol"
        codegen_dol.write_bytes(augmented)
        (work / "CODEGEN-ONLY-NOT-A-BOOT-IMAGE.txt").write_text(
            "Private compiler input generated from the player's clean main.dol + decoded relF.\n"
            "Do not boot, ship, upload, or commit this file. OpenSPM source identity remains the clean main.dol.\n",
            encoding="utf-8",
        )

        print(
            "[OpenSPM] relF executable->BSS relocation census: "
            f"count={bss_stats['exec_self_bss_relocations']} "
            f"patch_mask=0x{bss_stats['exec_self_bss_patch_section_mask']:X} "
            f"target_mask=0x{bss_stats['exec_self_bss_target_section_mask']:X}",
            flush=True,
        )

        seams = derive_v19_llvm_seams.scan(main_dol)
        seam_header = work / "openspm_local_v19_llvm_seams.h"
        seam_map = work / "openspm_local_llvm_partitions.map"
        derive_v19_llvm_seams.write_header(seam_header, seams)
        derive_v19_llvm_seams.write_map(seam_map, seams)

        # Player logs now prove two concrete computed-transfer cold-entry
        # frontiers. Keep the policy measured-only: dev54's first target is
        # accepted only after verifying its real direct-BL root relationship;
        # dev56's second target is accepted only after the clean DOL proves the
        # LR-implied caller is a legal linked BCCTR and the target is executable.
        augment_llvm_observed_indirect_entries.self_test()
        indirect_root_raw, indirect_common_raw = (
            augment_llvm_observed_indirect_entries.augment(main_dol, seam_map)
        )
        print(
            "[OpenSPM] LLVM dev56 observed indirect entries: "
            f"dev54_call=0x{augment_llvm_observed_indirect_entries.FILESELECT_INDIRECT_CALL:08X} "
            f"raw=0x{indirect_root_raw:08X} "
            f"target=0x{augment_llvm_observed_indirect_entries.FILESELECT_INDIRECT_TARGET:08X}; "
            f"dev56_call=0x{augment_llvm_observed_indirect_entries.COMMON_INDIRECT_CALL:08X} "
            f"raw=0x{indirect_common_raw:08X} "
            f"return=0x{augment_llvm_observed_indirect_entries.COMMON_INDIRECT_RETURN:08X} "
            f"target=0x{augment_llvm_observed_indirect_entries.COMMON_INDIRECT_TARGET:08X}",
            flush=True,
        )

        # Derive the CPU-side GX shadow contract from the same verified retail
        # DOL. The PC display-list implementation may replace FIFO transport,
        # but it must retain dirty-state flush + GXData save/restore semantics.
        gxdl_header = work / "openspm_local_gx_dl_semantics.h"
        gxdl_mode, gxdl_value, gxdl_dirty_helper = derive_gx_dl_semantics.derive(main_dol)
        derive_gx_dl_semantics.write_header(
            gxdl_header, gxdl_mode, gxdl_value, gxdl_dirty_helper
        )
        print(
            "[OpenSPM] LLVM GX display-list shadow contract: "
            f"slot={gxdl_mode}:0x{gxdl_value:08X} "
            f"dirty_helper=0x{gxdl_dirty_helper:08X}",
            flush=True,
        )

        # Audit the direct-BL cold-entry surface without widening it. The old
        # player log counted dynamic rescue executions, not distinct missing
        # continuations; adding a hard map entry after every call could destroy
        # LLVM region locality. Keep the exact runtime rescue until hot PCs are
        # measured, then partition only proven high-value continuations.
        augment_llvm_bl_continuations.self_test()
        bl_returns, bl_map_before, bl_added = augment_llvm_bl_continuations.augment(
            codegen_dol, seam_map
        )
        print(
            "[OpenSPM] LLVM direct-BL continuation audit: "
            f"already_partitioned={bl_returns} semantic_map_entries={bl_map_before} "
            f"added={bl_added}",
            flush=True,
        )

        output_root = work / "dolrecomp-output"
        migrate_titleless_generation(output_root, args.game_id)
        # Explicitly select the normal CPUState/RecompCore-style codegen ABI,
        # not DolRecomp's ModernGekko native-module runtime. The generated
        # objects are linked directly into OpenSPM's own local module DLL.
        command = [
            str(dolrecomp),
            "--cpu", "broadway",
            "--backend", "llvm",
            "--runtime", "recompcore",
            "--targets", "host",
            "--semantics", "exact",
            "--instrumentation", "none",
            "--native-abi", "unrestricted",
            "--map", str(seam_map),
            f"-j{args.jobs}",
            str(codegen_dol),
            args.game_id,
            str(output_root),
        ]
        env = os.environ.copy()
        # Upstream's generic LLVM campaign favored 64-instruction chunks for
        # register allocation, but OpenSPM's measured integration now pays a
        # much larger cost at artificial side exits: dev64 still produced
        # ~351M ordinary callbacks and billions of router comparisons while the
        # simulation remained ~30 Hz.  dev65 intentionally tests 128 so hot
        # retail loops/functions can remain native across the former boundary.
        # Indexed dispatch remains enabled for unavoidable exits.
        env["DOLRECOMP_LLVM_CHUNK_INSTRUCTIONS"] = "128"
        env["DOLRECOMP_DISPATCH_LOOKUP"] = "indexed"
        env["DOLRECOMP_LLVM_RESUME"] = "1"
        env["DOLRECOMP_GAMETDB_TITLES"] = str(titles)
        print(
            f"[OpenSPM] DolRecomp Wii title classification database: {titles} "
            f"(game={args.game_id}, offline/private)",
            flush=True,
        )
        run(command, env=env, cwd=work)

        generated = locate_generated(output_root, args.game_id)
        objects = validate_llvm_generated.validate(
            generated, require_openspm_wpar=True
        )
        shutil.copy2(seam_header, generated / seam_header.name)
        shutil.copy2(gxdl_header, generated / gxdl_header.name)
        shutil.copy2(seam_map, generated / seam_map.name)
        (generated / "openspm_llvm_codegen_provenance.txt").write_text(
            "backend=llvm\n"
            "runtime=openspm (DolRecomp CPUState object ABI only)\n"
            "moderngekko_runtime=0\n"
            "dolphin_runtime=0\n"
            "semantics=exact\n"
            "target=host\n"
            "cpu=broadway\n"
            "dolrecomp_title_mode=wii-private-database\n"
            "chunk_instructions=128\n"
            "dispatch_lookup=indexed\n"
            "resume_exact_stamped_objects=1\n"
            "openspm_inline_wpar_symbols=proven\n"
            "direct_bl_partition_policy=audit-only-hot-sites-must-be-proven\n"
            "observed_indirect_partition_policy=measured-and-player-verified\n"
            f"observed_indirect_call=0x{augment_llvm_observed_indirect_entries.FILESELECT_INDIRECT_CALL:08X}\n"
            f"observed_indirect_target=0x{augment_llvm_observed_indirect_entries.FILESELECT_INDIRECT_TARGET:08X}\n"
            f"observed_indirect_raw=0x{indirect_root_raw:08X}\n"
            f"observed_common_indirect_call=0x{augment_llvm_observed_indirect_entries.COMMON_INDIRECT_CALL:08X}\n"
            f"observed_common_indirect_return=0x{augment_llvm_observed_indirect_entries.COMMON_INDIRECT_RETURN:08X}\n"
            f"observed_common_indirect_target=0x{augment_llvm_observed_indirect_entries.COMMON_INDIRECT_TARGET:08X}\n"
            f"observed_common_indirect_raw=0x{indirect_common_raw:08X}\n"
            f"direct_bl_return_partitions_already_present={bl_returns}\n"
            f"direct_bl_return_partitions_added={bl_added}\n"
            f"gxdl_data_slot_mode={gxdl_mode}\n"
            f"gxdl_data_slot_value=0x{gxdl_value:08X}\n"
            f"gxdl_dirty_helper=0x{gxdl_dirty_helper:08X}\n"
            f"relf_storage={relf.storage}\n"
            f"relf_source={relf.path}\n"
            f"relf_source_sha256={relf.source_sha256}\n"
            f"relf_decoded_sha256={relf.decoded_sha256}\n"
            f"relf_relocations={relocation_stats['relocations']}\n"
            f"relf_exec_self_bss_relocations={bss_stats['exec_self_bss_relocations']}\n"
            f"relf_exec_self_bss_patch_section_mask=0x{bss_stats['exec_self_bss_patch_section_mask']:X}\n"
            f"relf_exec_self_bss_target_section_mask=0x{bss_stats['exec_self_bss_target_section_mask']:X}\n",
            encoding="utf-8",
        )
        print(
            "M14 OpenSPM LLVM generation ready: "
            f"objects={len(objects)} generated={generated} "
            "cpu=broadway title_mode=wii-private-database "
            "chunk=128 dispatch=indexed resume=exact-stamped openspm_wpar=proven "
            f"indirect_entry=0x{augment_llvm_observed_indirect_entries.FILESELECT_INDIRECT_TARGET:08X} "
            f"common_indirect_entry=0x{augment_llvm_observed_indirect_entries.COMMON_INDIRECT_TARGET:08X} "
            f"bl_audit={bl_returns}_already_partitioned/{bl_added}_new "
            f"gxdl_shadow={gxdl_mode}:0x{gxdl_value:08X}/0x{gxdl_dirty_helper:08X} "
            f"relf_storage={relf.storage} "
            f"relf_relocations={relocation_stats['relocations']} "
            f"relf_exec_self_bss={bss_stats['exec_self_bss_relocations']}"
        )
        return 0
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"error: OpenSPM LLVM generation failed: {exc}", file=sys.stderr)
        return exc.returncode if isinstance(exc, subprocess.CalledProcessError) else 1


if __name__ == "__main__":
    raise SystemExit(main())
