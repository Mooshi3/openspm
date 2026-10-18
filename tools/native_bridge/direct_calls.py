#!/usr/bin/env python3
"""Rewrite old local DolRecomp C output so direct guest calls stay native.

This migration tool is intentionally game-data-free. It copies a player-local
``generated/`` tree and converts ordinary direct guest BL side exits into bounded
native C calls. Explicit host targets remain side exits so OpenSPM can provide PC
services (filesystem, input, timing, audio, renderer completion, cache barriers,
etc.) without routing every game-to-game call through a dispatcher.

DolRecomp can optimize a BL to a target in the same generated chunk into a local
``goto label_TARGET``. When that target is explicitly hosted, this tool reverses
that optimization for just that callsite and emits a real host side exit. This is
important: host-boundary behavior must not depend on how the generator happened
to partition functions into chunks.

Previously transformed trees are accepted too. If a call was converted to a
native C call by an older host-target manifest and that target is hosted now, the
transform turns it back into a side exit before applying the current manifest.
This makes saved/generated-direct trees safely normalizable as M14 promotes more
semantic host services.

When ``host_targets`` is omitted, the OpenSPM M14 target manifest is loaded from
``state/m14-host-targets.json``. Passing an explicit iterable (including an empty
one) disables that project default, which keeps the transform usable for focused
tests and future non-SPM callers.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path
from typing import Iterable

LABEL_RE = re.compile(r"^label_([0-9A-Fa-f]{8}):", re.MULTILINE)
FUNC_RE = re.compile(r"chunk_\d+_[^_]+_([0-9A-Fa-f]{8})\.c$")
DIRECT_BL_RE = re.compile(
    r"(?P<indent>^[ \t]*)// (?P<pc>[0-9A-Fa-f]{8}): bl[ \t]+0x(?P<target>[0-9A-Fa-f]{8})\n"
    r"(?P=indent)\{\n"
    r"(?P=indent)[ \t]+ctx->lr = 0x(?P<ret>[0-9A-Fa-f]{8})u;\n"
    r"(?P=indent)[ \t]+ctx->pc = 0x(?P=target)u;\n"
    r"(?P=indent)[ \t]+return;\n"
    r"(?P=indent)\}",
    re.MULTILINE,
)
SAME_CHUNK_BL_RE = re.compile(
    r"(?P<indent>^[ \t]*)// (?P<pc>[0-9A-Fa-f]{8}): bl[ \t]+0x(?P<target>[0-9A-Fa-f]{8})\n"
    r"(?P=indent)\{\n"
    r"(?P=indent)[ \t]+ctx->lr = 0x(?P<ret>[0-9A-Fa-f]{8})u;\n"
    r"(?P=indent)[ \t]+if \(ctx->downcount <= -\(s64\)DOLRECOMP_C_LOOP_CYCLE_BUDGET\) \{\n"
    r"(?P=indent)[ \t]+ctx->pc = 0x(?P=target)u;\n"
    r"(?P=indent)[ \t]+return;\n"
    r"(?P=indent)[ \t]+\}\n"
    r"(?P=indent)[ \t]+goto label_(?P=target);\n"
    r"(?P=indent)\}",
    re.MULTILINE,
)
NATIVE_DIRECT_BL_RE = re.compile(
    r"(?P<indent>^[ \t]*)// (?P<pc>[0-9A-Fa-f]{8}): bl[ \t]+0x(?P<target>[0-9A-Fa-f]{8})\n"
    r"(?P=indent)\{\n"
    r"(?P=indent)[ \t]+void func_(?P<func>[0-9A-Fa-f]{8})\(CPUState\* ctx\);\n"
    r"(?P=indent)[ \t]+ctx->lr = 0x(?P<ret>[0-9A-Fa-f]{8})u;\n"
    r"(?P=indent)[ \t]+ctx->pc = 0x(?P=target)u;\n"
    r"(?P=indent)[ \t]+if \(dolrecomp_call_enter\(\)\) \{\n"
    r"(?P=indent)[ \t]+func_(?P=func)\(ctx\);\n"
    r"(?P=indent)[ \t]+dolrecomp_call_leave\(\);\n"
    r"(?P=indent)[ \t]+if \(ctx->pc == 0x(?P=ret)u\) goto label_(?P=ret);\n"
    r"(?P=indent)[ \t]+\}\n"
    r"(?P=indent)[ \t]+return;\n"
    r"(?P=indent)\}",
    re.MULTILINE,
)

DEFAULT_HOST_TARGETS = (
    Path(__file__).resolve().parents[2] / "state" / "m14-host-targets.json"
)


def load_host_targets(path: Path = DEFAULT_HOST_TARGETS) -> set[int]:
    """Load and validate the checked-in semantic host-boundary manifest."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"could not load host-target manifest {path}: {exc}") from exc

    if document.get("schema_version") != 1:
        raise RuntimeError(f"unsupported host-target manifest schema in {path}")
    entries = document.get("targets")
    if not isinstance(entries, list) or not entries:
        raise RuntimeError(f"host-target manifest has no targets: {path}")

    targets: set[int] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or not isinstance(entry.get("address"), str):
            raise RuntimeError(f"invalid host-target entry #{index} in {path}")
        try:
            value = int(entry["address"], 0)
        except ValueError as exc:
            raise RuntimeError(
                f"invalid host-target address {entry['address']!r} in {path}"
            ) from exc
        if value < 0 or value > 0xFFFFFFFF:
            raise RuntimeError(f"host-target address out of range in {path}: {entry['address']}")
        if value in targets:
            raise RuntimeError(f"duplicate host-target address in {path}: 0x{value:08X}")
        targets.add(value)
    if path == DEFAULT_HOST_TARGETS:
        # OpenSPM hooks (state/openspm-hooks.json, docs/HOOKS.md): enabled
        # entries are host targets too, so every step that routes host targets
        # (both LLVM host-first sets included) sees them. None enabled: no change.
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "openspm_hooks", Path(__file__).resolve().with_name("openspm_hooks.py"))
        openspm_hooks = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(openspm_hooks)
        hooks = openspm_hooks.enabled_addresses()
        overlap = hooks & targets
        if overlap:
            raise RuntimeError(
                "hook registry entries are already platform host targets: "
                + ", ".join(f"0x{value:08X}" for value in sorted(overlap)))
        targets |= hooks
    return targets


def chunk_function(path: Path) -> int:
    match = FUNC_RE.search(path.name)
    if not match:
        raise ValueError(f"not a generated chunk filename: {path.name}")
    return int(match.group(1), 16)


def build_address_map(files: list[Path]) -> tuple[dict[int, int], dict[Path, set[int]]]:
    address_to_function: dict[int, int] = {}
    file_labels: dict[Path, set[int]] = {}
    for path in files:
        function = chunk_function(path)
        text = path.read_text(encoding="utf-8")
        labels = {int(value, 16) for value in LABEL_RE.findall(text)}
        file_labels[path] = labels
        for address in labels:
            previous = address_to_function.setdefault(address, function)
            if previous != function:
                raise RuntimeError(
                    f"guest address 0x{address:08X} appears in multiple chunks"
                )
    return address_to_function, file_labels


def _host_side_exit(match: re.Match[str]) -> str:
    indent = match.group("indent")
    pc = match.group("pc").upper()
    target = match.group("target").upper()
    ret = match.group("ret").upper()
    return (
        f"{indent}// {pc}: bl      0x{target}\n"
        f"{indent}{{\n"
        f"{indent}    ctx->lr = 0x{ret}u;\n"
        f"{indent}    ctx->pc = 0x{target}u;\n"
        f"{indent}    return;\n"
        f"{indent}}}"
    )


def rewrite_tree(
    source: Path,
    output: Path,
    host_targets: Iterable[int] | None = None,
) -> dict[str, int]:
    if not (source / "generated.h").is_file() or not (source / "chunks").is_dir():
        raise RuntimeError(f"not a generated source tree: {source}")
    if output.exists():
        shutil.rmtree(output)
    shutil.copytree(source, output)

    if host_targets is None:
        hosted = load_host_targets()
    else:
        hosted = {int(value) & 0xFFFFFFFF for value in host_targets}
    files = sorted((output / "chunks").glob("chunk_*.c"))
    address_to_function, file_labels = build_address_map(files)
    stats = {
        "chunks": len(files),
        "labels": len(address_to_function),
        "host_targets": len(hosted),
        "direct_calls": 0,
        "rehosted_direct_calls": 0,
        "hosted_cross_calls": 0,
        "hosted_same_chunk_calls": 0,
        "unmapped_calls": 0,
        "missing_return_labels": 0,
    }

    for path in files:
        original = path.read_text(encoding="utf-8")
        text = original
        caller_labels = file_labels[path]

        # A saved generated-direct tree may predate the current host manifest.
        # Re-host our own prior direct-call expansion first, so newly promoted
        # semantic services are not bypassed by stale generated source.
        def replace_native_direct(match: re.Match[str]) -> str:
            target = int(match.group("target"), 16)
            if target not in hosted:
                return match.group(0)
            stats["rehosted_direct_calls"] += 1
            return _host_side_exit(match)

        text = NATIVE_DIRECT_BL_RE.sub(replace_native_direct, text)

        # Same-chunk BLs have already been collapsed to gotos by DolRecomp, so
        # force explicitly hosted targets back to a true host side exit first.
        def replace_same_chunk(match: re.Match[str]) -> str:
            target = int(match.group("target"), 16)
            if target not in hosted:
                return match.group(0)
            stats["hosted_same_chunk_calls"] += 1
            return _host_side_exit(match)

        text = SAME_CHUNK_BL_RE.sub(replace_same_chunk, text)

        def replace_direct(match: re.Match[str]) -> str:
            target = int(match.group("target"), 16)
            return_pc = int(match.group("ret"), 16)
            if target in hosted:
                stats["hosted_cross_calls"] += 1
                return match.group(0)

            target_function = address_to_function.get(target)
            if target_function is None:
                stats["unmapped_calls"] += 1
                return match.group(0)
            if return_pc not in caller_labels:
                # A return continuation outside the caller chunk needs a more
                # general continuation mechanism. Leave it on the safe path.
                stats["missing_return_labels"] += 1
                return match.group(0)

            stats["direct_calls"] += 1
            indent = match.group("indent")
            pc = match.group("pc").upper()
            target_text = match.group("target").upper()
            return_text = match.group("ret").upper()
            return (
                f"{indent}// {pc}: bl      0x{target_text}\n"
                f"{indent}{{\n"
                f"{indent}    void func_{target_function:08X}(CPUState* ctx);\n"
                f"{indent}    ctx->lr = 0x{return_text}u;\n"
                f"{indent}    ctx->pc = 0x{target_text}u;\n"
                f"{indent}    if (dolrecomp_call_enter()) {{\n"
                f"{indent}        func_{target_function:08X}(ctx);\n"
                f"{indent}        dolrecomp_call_leave();\n"
                f"{indent}        if (ctx->pc == 0x{return_text}u) "
                f"goto label_{return_text};\n"
                f"{indent}    }}\n"
                f"{indent}    return;\n"
                f"{indent}}}"
            )

        rewritten = DIRECT_BL_RE.sub(replace_direct, text)
        if rewritten != original:
            path.write_text(rewritten, encoding="utf-8", newline="\n")

    return stats


def parse_guest_address(text: str) -> int:
    value = int(text, 0)
    if value < 0 or value > 0xFFFFFFFF:
        raise argparse.ArgumentTypeError(f"guest address out of range: {text}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("generated", type=Path, help="local generated/ input tree")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--host-target",
        action="append",
        default=None,
        type=parse_guest_address,
        help=(
            "guest function address that must remain a host side exit; repeatable. "
            "If omitted, load state/m14-host-targets.json"
        ),
    )
    args = parser.parse_args()

    stats = rewrite_tree(args.generated, args.output, args.host_target)
    print(" ".join(f"{key}={value}" for key, value in stats.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
