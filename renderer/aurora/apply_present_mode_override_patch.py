#!/usr/bin/env python3
"""Add an OpenSPM-only Aurora present-mode override for M14 pacing A/B.

Pinned Aurora normally chooses Mailbox whenever vsync is disabled on non-Metal
backends, falling back to Immediate only when Mailbox is unavailable. dev118
proved the Wii-side fixed VI scheduler is generally ready on time while desktop
Present cadence still has a ~31 ms p99 tail. This transform adds a single
environment override so Mailbox vs Immediate can be compared without further
source edits after one rebuild.

Supported values:
  OPENSPM_M14_PRESENT_MODE=immediate
  OPENSPM_M14_PRESENT_MODE=mailbox
  OPENSPM_M14_PRESENT_MODE=fifo

If the requested mode is unsupported, Aurora logs a warning and retains its
historical automatic selection. With the variable unset, upstream behavior is
bit-for-bit equivalent at the selection-policy level.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
LOCK = HERE / "aurora.lock.json"
TARGET = pathlib.Path("lib/webgpu/gpu.cpp")
SIGNATURE = "OPENSPM_M14_PRESENT_MODE"

INCLUDE_OLD = """#include <cstdint>\n#include <span>\n"""
INCLUDE_NEW = """#include <cstdint>\n#include <cstdlib>\n#include <span>\n"""

SELECT_OLD = r'''wgpu::PresentMode select_present_mode(const wgpu::SurfaceCapabilities& capabilities) noexcept {
  const auto supports = [&capabilities](const wgpu::PresentMode candidate) {
    for (size_t i = 0; i < capabilities.presentModeCount; ++i) {
      if (capabilities.presentModes[i] == candidate) {
        return true;
      }
    }
    return false;
  };
  if (vsync_enabled()) {
    if (supports(wgpu::PresentMode::FifoRelaxed)) {
      return wgpu::PresentMode::FifoRelaxed;
    }
  } else {
    // Dawn only disables CAMetalLayer displaySyncEnabled for Immediate on Metal
    if (g_backendType != wgpu::BackendType::Metal && supports(wgpu::PresentMode::Mailbox)) {
      return wgpu::PresentMode::Mailbox;
    }
    if (supports(wgpu::PresentMode::Immediate)) {
      return wgpu::PresentMode::Immediate;
    }
  }
  return wgpu::PresentMode::Fifo;
}
'''

SELECT_NEW = r'''wgpu::PresentMode select_present_mode(const wgpu::SurfaceCapabilities& capabilities) noexcept {
  const auto supports = [&capabilities](const wgpu::PresentMode candidate) {
    for (size_t i = 0; i < capabilities.presentModeCount; ++i) {
      if (capabilities.presentModes[i] == candidate) {
        return true;
      }
    }
    return false;
  };

  // OpenSPM M14 pacing A/B. This is intentionally below the public Aurora
  // config surface and defaults to historical behavior when the environment
  // variable is absent.
  if (const char* forced = std::getenv("OPENSPM_M14_PRESENT_MODE");
      forced != nullptr && *forced != '\0') {
    const std::string_view mode{forced};
    if (mode == "immediate") {
      if (supports(wgpu::PresentMode::Immediate)) {
        return wgpu::PresentMode::Immediate;
      }
      Log.warn("OPENSPM_M14_PRESENT_MODE=immediate requested but unsupported; using automatic present-mode selection");
    } else if (mode == "mailbox") {
      if (supports(wgpu::PresentMode::Mailbox)) {
        return wgpu::PresentMode::Mailbox;
      }
      Log.warn("OPENSPM_M14_PRESENT_MODE=mailbox requested but unsupported; using automatic present-mode selection");
    } else if (mode == "fifo") {
      if (supports(wgpu::PresentMode::Fifo)) {
        return wgpu::PresentMode::Fifo;
      }
      Log.warn("OPENSPM_M14_PRESENT_MODE=fifo requested but unsupported; using automatic present-mode selection");
    } else {
      Log.warn("Unknown OPENSPM_M14_PRESENT_MODE='{}'; using automatic present-mode selection", mode);
    }
  }

  if (vsync_enabled()) {
    if (supports(wgpu::PresentMode::FifoRelaxed)) {
      return wgpu::PresentMode::FifoRelaxed;
    }
  } else {
    // Dawn only disables CAMetalLayer displaySyncEnabled for Immediate on Metal
    if (g_backendType != wgpu::BackendType::Metal && supports(wgpu::PresentMode::Mailbox)) {
      return wgpu::PresentMode::Mailbox;
    }
    if (supports(wgpu::PresentMode::Immediate)) {
      return wgpu::PresentMode::Immediate;
    }
  }
  return wgpu::PresentMode::Fifo;
}
'''


def run(*args: str, cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def transform(source: pathlib.Path, check_only: bool) -> str:
    path = source / TARGET
    text = path.read_text(encoding="utf-8")

    if SIGNATURE in text:
        required = (
            'std::getenv("OPENSPM_M14_PRESENT_MODE")',
            'mode == "immediate"',
            'mode == "mailbox"',
            'mode == "fifo"',
        )
        missing = [fragment for fragment in required if fragment not in text]
        if missing:
            raise RuntimeError("present-mode override signature is incomplete: " + ", ".join(missing))
        return "already-applied"

    for old, _new, label in (
        (INCLUDE_OLD, INCLUDE_NEW, "cstdlib include"),
        (SELECT_OLD, SELECT_NEW, "present-mode selector"),
    ):
        count = text.count(old)
        if count != 1:
            raise RuntimeError(f"{TARGET}: {label}: expected source fragment exactly once, found {count}")

    text = text.replace(INCLUDE_OLD, INCLUDE_NEW, 1)
    text = text.replace(SELECT_OLD, SELECT_NEW, 1)
    if not check_only:
        path.write_text(text, encoding="utf-8", newline="\n")
    return "verified" if check_only else "applied"


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

    try:
        result = transform(source, args.check_only)
    except (OSError, RuntimeError) as exc:
        print(f"error: Aurora present-mode adaptation failed: {exc}", file=sys.stderr)
        return 4

    print({
        "already-applied": "Aurora M14 present-mode override already applied",
        "verified": "Aurora M14 present-mode override transform verified",
        "applied": "Aurora M14 present-mode override applied",
    }[result])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
