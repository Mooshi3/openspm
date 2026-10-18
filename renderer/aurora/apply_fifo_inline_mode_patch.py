#!/usr/bin/env python3
"""Add a safe same-thread Aurora FIFO Inline/Drain runtime selector for M14.

The 2026-09-13 asynchronous Thread-mode experiment was correctly retired:
Aurora's worker could dereference mutable guest RAM after Broadway had already
reused it.  That does not apply to ProcessingMode::Inline, which consumes a
published command-complete prefix synchronously on the Broadway/runtime thread
before the guest can resume and mutate those dependencies.

This transform keeps Drain as the default, allows
OPENSPM_M14_FIFO_MODE=inline at runtime, and exposes raw::publish() so OpenSPM's
command-aware backend framer can split parser work across safe complete-command
boundaries. Thread mode is intentionally not exposed.

No presentation, guest completion, byte ordering, display-list, or GPU fence
semantics are changed by this transform.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
LOCK = HERE / "aurora.lock.json"
FIFO = pathlib.Path("lib/gx/fifo.cpp")
RAW_HEADER = pathlib.Path("include/aurora/gx_raw.hpp")
RAW_SOURCE = pathlib.Path("lib/gx/raw.cpp")

THREAD_LINE = "constexpr auto kProcessingMode = ProcessingMode::Thread;"
DRAIN_LINE = "constexpr auto kProcessingMode = ProcessingMode::Drain;"
MODE_SIGNATURE = 'OPENSPM_M14_FIFO_MODE'
MODE_MARKER = "OpenSPM M14 Aurora FIFO processing mode"
RAW_PUBLISH_DECL = "void publish() noexcept;"
RAW_PUBLISH_DEF = "void publish() noexcept { fifo::publish(); }"

INCLUDE_OLD = '#include "fifo.hpp"\n\n#include "../thread.hpp"\n'
INCLUDE_NEW = '#include "fifo.hpp"\n\n#include <cstdlib>\n#include <cstring>\n\n#include "../thread.hpp"\n'

SELECTOR = '''ProcessingMode m14_processing_mode_from_environment() noexcept {
  const char* mode = std::getenv("OPENSPM_M14_FIFO_MODE");
  if (mode != nullptr && std::strcmp(mode, "inline") == 0) {
    return ProcessingMode::Inline;
  }
  return ProcessingMode::Drain;
}
const auto kProcessingMode = m14_processing_mode_from_environment();'''

INIT_OLD = """void init() {
  stop_worker();

"""
INIT_NEW = """void init() {
  stop_worker();

  Log.info("OpenSPM M14 Aurora FIFO processing mode: {}",
           kProcessingMode == ProcessingMode::Inline ? "Inline" : "Drain");

"""


def run(*args: str, cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def transform(source: pathlib.Path, check_only: bool) -> str:
    fifo_path = source / FIFO
    raw_header_path = source / RAW_HEADER
    raw_source_path = source / RAW_SOURCE

    fifo_text = fifo_path.read_text(encoding="utf-8")
    raw_header = raw_header_path.read_text(encoding="utf-8")
    raw_source = raw_source_path.read_text(encoding="utf-8")

    original = (fifo_text, raw_header, raw_source)

    if MODE_SIGNATURE not in fifo_text:
        mode_count = fifo_text.count(THREAD_LINE) + fifo_text.count(DRAIN_LINE)
        if mode_count != 1:
            raise RuntimeError(
                "fifo.cpp: expected exactly one pinned Thread/Drain processing-mode line, "
                f"found thread={fifo_text.count(THREAD_LINE)} drain={fifo_text.count(DRAIN_LINE)}"
            )
        if fifo_text.count(INCLUDE_OLD) != 1:
            raise RuntimeError("fifo.cpp: include anchor drifted")
        fifo_text = fifo_text.replace(INCLUDE_OLD, INCLUDE_NEW, 1)
        if THREAD_LINE in fifo_text:
            fifo_text = fifo_text.replace(THREAD_LINE, SELECTOR, 1)
        else:
            fifo_text = fifo_text.replace(DRAIN_LINE, SELECTOR, 1)

        if fifo_text.count(INIT_OLD) != 1:
            raise RuntimeError("fifo.cpp: init marker anchor drifted")
        fifo_text = fifo_text.replace(INIT_OLD, INIT_NEW, 1)

    required_fifo = (
        'std::getenv("OPENSPM_M14_FIFO_MODE")',
        'std::strcmp(mode, "inline") == 0',
        "ProcessingMode::Inline",
        "ProcessingMode::Drain",
        MODE_MARKER,
    )
    missing = [fragment for fragment in required_fifo if fragment not in fifo_text]
    if missing:
        raise RuntimeError("fifo.cpp: Inline/Drain selector incomplete: " + ", ".join(missing))
    if THREAD_LINE in fifo_text or DRAIN_LINE in fifo_text:
        raise RuntimeError("fifo.cpp: stale fixed processing-mode line remains")

    if RAW_PUBLISH_DECL not in raw_header:
        anchor = "void append_fifo(const void* bytes, std::uint32_t size);\nvoid drain();\n"
        replacement = (
            "void append_fifo(const void* bytes, std::uint32_t size);\n"
            "// OpenSPM M14 same-thread command-complete prefix publication.\n"
            "void publish() noexcept;\n"
            "void drain();\n"
        )
        if raw_header.count(anchor) != 1:
            raise RuntimeError("gx_raw.hpp: publish declaration anchor drifted")
        raw_header = raw_header.replace(anchor, replacement, 1)

    if RAW_PUBLISH_DEF not in raw_source:
        anchor = (
            "void append_fifo(const void* bytes, std::uint32_t size) {\n"
            "  if (bytes == nullptr || size == 0u) return;\n"
            "  fifo::write_data(bytes, size);\n"
            "}\n"
            "\n"
            "void drain() { fifo::drain(); }\n"
        )
        replacement = (
            "void append_fifo(const void* bytes, std::uint32_t size) {\n"
            "  if (bytes == nullptr || size == 0u) return;\n"
            "  fifo::write_data(bytes, size);\n"
            "}\n"
            "\n"
            "void publish() noexcept { fifo::publish(); }\n"
            "\n"
            "void drain() { fifo::drain(); }\n"
        )
        if raw_source.count(anchor) != 1:
            raise RuntimeError("raw.cpp: publish definition anchor drifted")
        raw_source = raw_source.replace(anchor, replacement, 1)

    if raw_header.count(RAW_PUBLISH_DECL) != 1:
        raise RuntimeError("gx_raw.hpp: raw publish declaration is not unique")
    if raw_source.count(RAW_PUBLISH_DEF) != 1:
        raise RuntimeError("raw.cpp: raw publish definition is not unique")

    changed = original != (fifo_text, raw_header, raw_source)
    if not check_only and changed:
        fifo_path.write_text(fifo_text, encoding="utf-8", newline="\n")
        raw_header_path.write_text(raw_header, encoding="utf-8", newline="\n")
        raw_source_path.write_text(raw_source, encoding="utf-8", newline="\n")

    if check_only:
        return "verified"
    return "applied" if changed else "already-applied"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=pathlib.Path)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    source = args.source.resolve()

    expected = json.loads(LOCK.read_text(encoding="utf-8"))["commit"]
    try:
        actual = run("git", "rev-parse", "HEAD", cwd=source).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"error: not a usable git checkout: {source}: {exc}", file=sys.stderr)
        return 2
    if actual != expected:
        print(f"error: Aurora revision mismatch: expected {expected}, got {actual}", file=sys.stderr)
        return 3
    if not (source / "include/aurora/gx_guest_memory.hpp").exists():
        print("error: raw-GX adaptation is not applied", file=sys.stderr)
        return 4

    try:
        result = transform(source, args.check_only)
    except (OSError, RuntimeError) as exc:
        print(f"error: Aurora FIFO Inline-mode adaptation failed: {exc}", file=sys.stderr)
        return 5

    print({
        "verified": "Aurora FIFO Inline/Drain runtime selector verified",
        "already-applied": "Aurora FIFO Inline/Drain runtime selector already applied",
        "applied": "Aurora FIFO Inline/Drain runtime selector applied",
    }[result])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
