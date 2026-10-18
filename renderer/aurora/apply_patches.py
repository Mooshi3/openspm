#!/usr/bin/env python3
"""Apply OpenSPM's audited raw-GX adaptations to a local Aurora checkout.

The Aurora checkout is third-party source and is not vendored into OpenSPM.
Instead of a line-number-sensitive diff, this tool performs exact source
replacements against one pinned upstream commit. If upstream text differs at
all, it refuses to modify the checkout.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
LOCK = HERE / "aurora.lock.json"

GUEST_HEADER = r'''#pragma once

#include <cstdint>

namespace aurora::gx::guest_memory {

struct Span {
  const std::uint8_t* data = nullptr;
  std::uint32_t size = 0;
};

using MapFn = bool (*)(void* context, std::uint32_t guestAddress,
                       std::uint32_t minSize, Span* outSpan);

// Optional bridge for consumers feeding Aurora an original console FIFO.
// Source ports that emit Aurora host-pointer metadata leave this unset.
void set_mapper(void* context, MapFn map) noexcept;
void clear_mapper() noexcept;
bool has_mapper() noexcept;
bool map(std::uint32_t guestAddress, std::uint32_t minSize, Span* outSpan) noexcept;

} // namespace aurora::gx::guest_memory
'''

GUEST_SOURCE = r'''#include <aurora/gx_guest_memory.hpp>

namespace aurora::gx::guest_memory {
namespace {
void* sContext = nullptr;
MapFn sMap = nullptr;
}

void set_mapper(void* context, MapFn mapFn) noexcept {
  sContext = context;
  sMap = mapFn;
}

void clear_mapper() noexcept {
  sContext = nullptr;
  sMap = nullptr;
}

bool has_mapper() noexcept { return sMap != nullptr; }

bool map(std::uint32_t guestAddress, std::uint32_t minSize, Span* outSpan) noexcept {
  if (outSpan == nullptr) return false;
  *outSpan = {};
  if (sMap == nullptr || !sMap(sContext, guestAddress, minSize, outSpan)) return false;
  return outSpan->data != nullptr && outSpan->size >= minSize;
}

} // namespace aurora::gx::guest_memory
'''

TRANSFORMS: dict[str, list[tuple[str, str]]] = {
    "cmake/aurora_gx.cmake": [
        (
            "        lib/gx/command_processor.cpp\n        lib/gx/regs.cpp\n",
            "        lib/gx/command_processor.cpp\n        lib/gx/guest_memory.cpp\n        lib/gx/regs.cpp\n",
        ),
    ],
    "lib/gx/regs.cpp": [
        (
            '#include "regs.hpp"\n\n#include "gx_fmt.hpp"\n',
            '#include "regs.hpp"\n\n#include <aurora/gx_guest_memory.hpp>\n\n#include "gx_fmt.hpp"\n\n#include <algorithm>\n',
        ),
        (
            "// Copy clear color (0x4F/0x50) and depth (0x51)\n",
            "// Original-console EFB copy source/destination registers. Aurora's\n"
            "// source-port API normally emits host-pointer metadata instead, but\n"
            "// SPM writes these hardware BP registers directly. 0x4B stores a\n"
            "// physical destination address shifted right by five bits; map it\n"
            "// through OpenSPM's bounded guest resolver so later Image3 mapping\n"
            "// obtains the same host identity and can find the copy texture.\n"
            "void bp_copy_source(u8 reg, u32 value) noexcept {\n"
            "  if (reg == 0x49u) {\n"
            "    g_gxState.texCopySrc.x = static_cast<s32>(reg_get(value, 10, 0));\n"
            "    g_gxState.texCopySrc.y = static_cast<s32>(reg_get(value, 10, 10));\n"
            "  } else {\n"
            "    g_gxState.texCopySrc.width = static_cast<s32>(reg_get(value, 10, 0) + 1u);\n"
            "    g_gxState.texCopySrc.height = static_cast<s32>(reg_get(value, 10, 10) + 1u);\n"
            "  }\n"
            "}\n\n"
            "void bp_copy_destination(u8, u32 value) noexcept {\n"
            "  if (!guest_memory::has_mapper()) return;\n"
            "  const u32 guestAddress = (value & 0x001FFFFFu) << 5;\n"
            "  guest_memory::Span span;\n"
            "  if (!guest_memory::map(guestAddress, 1u, &span)) {\n"
            "    g_gxState.texCopyDest = nullptr;\n"
            "    Log.error(\"Failed to map GX EFB-copy destination 0x{:08X}\", guestAddress);\n"
            "    return;\n"
            "  }\n"
            "  g_gxState.texCopyDest = static_cast<const void*>(span.data);\n"
            "}\n\n"
            "// Copy clear color (0x4F/0x50) and depth (0x51)\n",
        ),
        (
            "    slot.loadTlut0 = g_gxState.bpRegCache[0x64];\n"
            "    slot.numEntries = static_cast<u16>(reg_get(value, 10, 10) + 1);\n",
            "    slot.loadTlut0 = g_gxState.bpRegCache[0x64];\n"
            "    slot.numEntries = static_cast<u16>(reg_get(value, 10, 10) + 1);\n"
            "    if (guest_memory::has_mapper()) {\n"
            "      guest_memory::Span span;\n"
            "      const u32 guestAddress = (slot.loadTlut0 & 0x00FFFFFFu) << 5;\n"
            "      const u32 sourceBytes = static_cast<u32>(slot.numEntries) * sizeof(u16);\n"
            "      slot.data = guest_memory::map(guestAddress, sourceBytes, &span)\n"
            "                      ? static_cast<const void*>(span.data)\n"
            "                      : nullptr;\n"
            "    }\n",
        ),
        (
            "  case 5: // Image3\n    slot.image3 = value;\n    break;\n",
            "  case 5: // Image3\n"
            "    slot.image3 = value;\n"
            "    if (guest_memory::has_mapper()) {\n"
            "      guest_memory::Span span;\n"
            "      const u32 guestAddress = (value & 0x00FFFFFFu) << 5;\n"
            "      const size_t wanted = texture::texture_source_size(\n"
            "          slot.format(), slot.width(), slot.height(), slot.mip_count());\n"
            "      const u32 minSize = wanted != 0 && wanted <= UINT32_MAX\n"
            "                              ? static_cast<u32>(wanted)\n"
            "                              : 1u;\n"
            "      slot.data = guest_memory::map(guestAddress, minSize, &span)\n"
            "                      ? static_cast<const void*>(span.data)\n"
            "                      : nullptr;\n"
            "    }\n"
            "    break;\n",
        ),
        (
            "void cp_arraybase_unsupported(u8, u32) noexcept {\n"
            '  Log.error("CP_REG_ARRAYBASE_ID is not supported. Use GX_AURORA_LOAD_ARRAYBASE instead.");\n'
            "}\n",
            "void cp_arraybase(u8 addr, u32 value) noexcept {\n"
            "  if (!guest_memory::has_mapper()) {\n"
            '    Log.error("CP_REG_ARRAYBASE_ID is not supported. Use GX_AURORA_LOAD_ARRAYBASE instead.");\n'
            "    return;\n"
            "  }\n\n"
            "  const u32 attrIdx = addr - 0xA0 + GX_VA_POS;\n"
            "  if (attrIdx >= g_gxState.arrays.size()) return;\n\n"
            "  guest_memory::Span span;\n"
            "  auto& array = g_gxState.arrays[attrIdx];\n"
            "  if (!guest_memory::map(value, 1u, &span)) {\n"
            "    array.data = nullptr;\n"
            "    array.size = 0;\n"
            "    array.cachedRange = {};\n"
            '    Log.error("Failed to map CP array base 0x{:08X} for attr {}", value, attrIdx);\n'
            "    return;\n"
            "  }\n\n"
            "  if (array.le) g_gxState.dirty |= DirtyPipeline;\n"
            "  array.data = span.data;\n"
            "  array.size = span.size;\n"
            "  array.le = false;\n"
            "  array.cachedRange = {};\n"
            "  g_gxState.dirty |= DirtyImmediates;\n"
            "}\n",
        ),
        (
            "  regs[0x43] = {bp_pe_ctrl};\n"
            "  regs[0x4F] = {bp_clear_ra};\n",
            "  regs[0x43] = {bp_pe_ctrl};\n"
            "  regs[0x49] = {bp_copy_source};\n"
            "  regs[0x4A] = {bp_copy_source};\n"
            "  regs[0x4B] = {bp_copy_destination, 0, /* alwaysHandle */ true};\n"
            "  regs[0x4F] = {bp_clear_ra};\n",
        ),
        (
            "    regs[0xA0 + i] = {cp_arraybase_unsupported, 0, /* alwaysHandle */ true};\n",
            "    regs[0xA0 + i] = {cp_arraybase, DirtyPipeline, /* alwaysHandle */ true};\n",
        ),
    ],
    "lib/gx/command_processor.cpp": [
        (
            '#include "command_processor.hpp"\n\n#include "../gfx/depth_peek.hpp"\n',
            '#include "command_processor.hpp"\n\n#include <aurora/gx_guest_memory.hpp>\n\n#include "../gfx/depth_peek.hpp"\n',
        ),
        (
            "DrawCache sDrawCache;\n",
            "DrawCache sDrawCache;\nthread_local u32 sDisplayListDepth = 0;\nconstexpr u32 MaxDisplayListDepth = 32;\n",
        ),
        (
            "    case CP_CMD_CALL_DL: {\n"
            "      // Call display list: 8 bytes (address + size)\n"
            '      Log.warn("Ignoring nested GX_CMD_CALL_DL");\n'
            "      reader.skip(8);\n"
            "      break;\n"
            "    }\n",
            "    case CP_CMD_CALL_DL: {\n"
            "      const u32 guestAddress = reader.read<u32>();\n"
            "      const u32 displayListSize = reader.read<u32>();\n"
            "      if (!guest_memory::has_mapper()) {\n"
            '        Log.warn("Ignoring nested GX_CMD_CALL_DL");\n'
            "        break;\n"
            "      }\n"
            "      if (sDisplayListDepth >= MaxDisplayListDepth) {\n"
            '        FATAL("GX_CMD_CALL_DL nesting exceeded {}", MaxDisplayListDepth);\n'
            "      }\n\n"
            "      guest_memory::Span span;\n"
            "      if (!guest_memory::map(guestAddress, displayListSize, &span)) {\n"
            '        FATAL("GX_CMD_CALL_DL references unmapped guest memory: address=0x{:08X}, size={}",\n'
            "              guestAddress, displayListSize);\n"
            "      }\n\n"
            "      ++sDisplayListDepth;\n"
            "      const ProcessResult nested = process(span.data, displayListSize);\n"
            "      --sDisplayListDepth;\n"
            "      if (nested.drawDone) {\n"
            "        return {static_cast<u32>(reader.offset()), true};\n"
            "      }\n"
            "      break;\n"
            "    }\n",
        ),
    ],
}

NEW_FILES = {
    "include/aurora/gx_guest_memory.hpp": GUEST_HEADER,
    "lib/gx/guest_memory.cpp": GUEST_SOURCE,
}


def run(*args: str, cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def transform(source: pathlib.Path, check_only: bool) -> None:
    pending: dict[pathlib.Path, str] = {}

    for relative, replacements in TRANSFORMS.items():
        path = source / relative
        text = path.read_text(encoding="utf-8")
        for old, new in replacements:
            count = text.count(old)
            if count != 1:
                raise RuntimeError(
                    f"{relative}: expected source fragment exactly once, found {count}"
                )
            text = text.replace(old, new, 1)
        pending[path] = text

    for relative, contents in NEW_FILES.items():
        path = source / relative
        if path.exists():
            raise RuntimeError(f"{relative}: expected file to be absent at pinned revision")
        pending[path] = contents

    if check_only:
        return

    for path, contents in pending.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=pathlib.Path, help="local encounter/aurora checkout")
    parser.add_argument("--check-only", action="store_true", help="verify exact transforms without modifying the checkout")
    parser.add_argument("--allow-dirty", action="store_true", help="allow unrelated local changes in the Aurora checkout")
    args = parser.parse_args()

    source = args.source.resolve()
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    expected = lock["commit"]

    try:
        actual = run("git", "rev-parse", "HEAD", cwd=source).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"error: not a usable git checkout: {source}: {exc}", file=sys.stderr)
        return 2

    if actual != expected:
        print(f"error: Aurora revision mismatch: expected {expected}, got {actual}", file=sys.stderr)
        return 3

    if not args.allow_dirty:
        dirty = run("git", "status", "--porcelain", cwd=source).stdout.strip()
        if dirty:
            print("error: Aurora checkout is dirty; use --allow-dirty only if intentional", file=sys.stderr)
            return 4

    try:
        transform(source, args.check_only)
    except (OSError, RuntimeError) as exc:
        print(f"error: Aurora adaptation contract failed: {exc}", file=sys.stderr)
        return 5

    print("Aurora raw-GX transforms verified" if args.check_only else "Aurora raw-GX transforms applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
