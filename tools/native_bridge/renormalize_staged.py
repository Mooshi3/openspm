#!/usr/bin/env python3
"""Re-normalize an existing player-local generated tree in place.

The expensive 218-chunk local module is intentionally cached between M14
bring-up iterations. Host-target changes therefore need migration against the
already-staged generated source before Ninja decides what to rebuild.

The failed local-module bridge v18 experiment injected transient post-call hooks
around native GXBeginDisplayList/GXEndDisplayList. Retire those exact historical
hooks first. The current host manifest then promotes Begin/End back to ordinary
native guest calls, the reversible semantic-label guard removes their old hosted
entry guards, and v19 installs continuation-safe pre-call/guest-return hooks that
bracket only OpenSPM's neutral physical display-list recorder.

No player cache or saves need to be deleted.
"""

from __future__ import annotations

import argparse
import filecmp
import shutil
import tempfile
from pathlib import Path

import direct_calls
import display_list_return_hooks_v19 as native_dl_v19
import guard_hosted_direct_entries as hosted_guard
import retire_misclassified_nand_guard as retired_nand
import retire_native_display_list_hooks as retired_native_dl

# Runtime-proven semantic boundaries required in the exact player-local R8PE01
# revision-2 tree. GXBeginDisplayList/GXEndDisplayList are deliberately absent:
# v19 executes their CPU-side SDK semantics natively and uses durable return-label
# hooks for the physical recorder seam.
REQUIRED_HOSTED_TARGETS = {
    0x80284FE8,
    0x80285064,
    0x80285328,
    0x8028711C,
    0x8028986C,
    0x8028987C,
    0x8028D8A8,
    0x802CE890,
    0x802DF4F0,
}


def file_set(root: Path) -> set[Path]:
    return {path.relative_to(root) for path in root.rglob("*") if path.is_file()}


def compare_trees(left: Path, right: Path) -> list[Path]:
    left_files = file_set(left)
    right_files = file_set(right)
    if left_files != right_files:
        missing = sorted(str(path) for path in left_files - right_files)
        extra = sorted(str(path) for path in right_files - left_files)
        raise RuntimeError(
            "normalizer changed staged file set: missing="
            + ",".join(missing)
            + " extra="
            + ",".join(extra)
        )
    return [
        relative
        for relative in sorted(left_files, key=str)
        if not filecmp.cmp(left / relative, right / relative, shallow=False)
    ]


def normalize_from(source: Path, prepared: Path, output: Path):
    shutil.copytree(source, prepared)

    # Historical v18 cleanup must precede ordinary direct-call normalization;
    # retire_native_display_list_hooks.py recognizes only the exact old wrapper.
    dl_retired = retired_native_dl.retire_tree(prepared)

    # With Begin/End absent from today's host manifest, direct_calls promotes
    # their old v17 side exits into native calls while preserving every other
    # semantic host boundary.
    stats = direct_calls.rewrite_tree(prepared, output)
    retired_stats = retired_nand.retire_tree(output)

    # guard_tree is intentionally reversible: it removes exact first-statement
    # host guards from Begin/End now that they are no longer hosted.
    guard_stats = hosted_guard.guard_tree(
        output, require_targets=REQUIRED_HOSTED_TARGETS
    )

    # Apply this last. The transform expects the final native-direct/same-chunk
    # call shape and inserts durable guest return-label hooks. It fails closed
    # unless the exact R8PE01 3 Begin + 3 End callsites are all represented and
    # none of their continuation labels has another guest branch predecessor.
    v19_begin, v19_end, v19_changed = native_dl_v19.transform_tree(output)
    return (
        dl_retired,
        stats,
        retired_stats,
        guard_stats,
        (v19_begin, v19_end, v19_changed),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--generated",
        required=True,
        type=Path,
        help="existing staged generated/ directory containing generated.h and chunks/",
    )
    args = parser.parse_args()

    source = args.generated.resolve()
    if not (source / "generated.h").is_file() or not (source / "chunks").is_dir():
        parser.error("--generated must contain generated.h and chunks/")

    temp_root = Path(
        tempfile.mkdtemp(prefix="openspm-renormalize-", dir=str(source.parent))
    )
    prepared = temp_root / "prepared"
    temporary = temp_root / "generated"
    verify_prepared = temp_root / "verify-prepared"
    verify = temp_root / "verify"
    try:
        (
            dl_retired,
            stats,
            retired_stats,
            guard_stats,
            v19_stats,
        ) = normalize_from(source, prepared, temporary)
        changed_files = compare_trees(source, temporary)

        for relative in changed_files:
            shutil.copy2(temporary / relative, source / relative)

        # A second pass must be byte-identical. In particular, no v18 hook may
        # survive, no Begin/End call may be re-hosted, and every v19 continuation
        # hook must already be present exactly once.
        (
            verify_dl_retired,
            verify_stats,
            verify_retired_stats,
            verify_guard_stats,
            verify_v19_stats,
        ) = normalize_from(source, verify_prepared, verify)
        unstable = compare_trees(source, verify)
        if unstable:
            raise RuntimeError(
                "staged normalization is not stable: "
                + ",".join(str(path) for path in unstable[:16])
            )
        if any(verify_dl_retired.values()):
            raise RuntimeError(
                "retired native GX display-list v18 hook reappeared after normalization: "
                + ",".join(
                    f"{key}={value}" for key, value in verify_dl_retired.items()
                )
            )
        if (
            verify_stats["rehosted_direct_calls"] != 0
            or verify_stats["hosted_same_chunk_calls"] != 0
        ):
            raise RuntimeError(
                "hosted callsites remained after normalization: "
                f"rehosted_direct_calls={verify_stats['rehosted_direct_calls']} "
                f"hosted_same_chunk_calls={verify_stats['hosted_same_chunk_calls']}"
            )
        if verify_retired_stats["removed_guards"] != 0:
            raise RuntimeError(
                "retired NAND host guard reappeared after normalization: "
                f"removed_guards={verify_retired_stats['removed_guards']}"
            )
        if verify_guard_stats["guarded_entries"] != 0:
            raise RuntimeError(
                "hosted label guard was not stable: "
                f"guarded_entries={verify_guard_stats['guarded_entries']}"
            )
        if verify_guard_stats.get("retired_host_guards_removed", 0) != 0:
            raise RuntimeError(
                "retired generic host guard reappeared after normalization: "
                f"retired_host_guards_removed={verify_guard_stats['retired_host_guards_removed']}"
            )
        if verify_guard_stats["deprecated_inline_removed"] != 0:
            raise RuntimeError(
                "deprecated inline cleanup was not stable: "
                f"deprecated_inline_removed={verify_guard_stats['deprecated_inline_removed']}"
            )
        if verify_v19_stats != (3, 3, 0):
            raise RuntimeError(
                "continuation-safe GX display-list v19 hooks were not stable: "
                f"verify={verify_v19_stats}"
            )

        print(
            "staged-renormalize changed_files="
            + str(len(changed_files))
            + " retired_native_dl="
            + ",".join(f"{key}={value}" for key, value in dl_retired.items())
            + " "
            + " ".join(f"{key}={value}" for key, value in stats.items())
            + " retired_nand="
            + ",".join(f"{key}={value}" for key, value in retired_stats.items())
            + " hosted_guard="
            + ",".join(f"{key}={value}" for key, value in guard_stats.items())
            + f" v19=begin:{v19_stats[0]},end:{v19_stats[1]},changed:{v19_stats[2]}"
            + " verify=stable"
        )
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"error: {exc}")
        raise SystemExit(1)
