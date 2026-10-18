#!/usr/bin/env python3
"""Index exact per-frame raw guest-array cache lookups by guest pointer.

Runtime profiling in complex SPM scenes shows ~850 exact-content staging misses per
heavy Aurora frame and 20-32 ms elapsed inside m14_stage_guest_array. The
runtime-proven cache is intentionally conservative: every reusable range owns an
immutable byte snapshot and reuse requires an exact memcmp, preserving same-frame
guest mutation correctness.

The remaining lookup path scans every historical entry in reverse for every
request even though almost all entries belong to different guest pointers. At
~850 misses this can perform hundreds of thousands of irrelevant metadata checks.
This migration adds an unordered multimap from guest pointer to stage-entry index,
then performs the same stride/size/exact-memcmp validation only on candidates for
that pointer. It also reserves a 1024-entry working set to avoid repeated vector
and hash-table growth in the runtime-proven heavy-scene range.

No hashes replace memcmp, no visibility epoch bypass is reintroduced, and staged
bytes/ranges are unchanged. This is a host lookup/allocation optimization only.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
LOCK = HERE / "aurora.lock.json"
SIGNATURE = "M14 raw guest-array pointer-indexed exact lookup active"
ROLLBACK_MARKER = "M14 gameplay GX audit v1 regression rollback active"


def run(*args: str, cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected source fragment exactly once, found {count}")
    return text.replace(old, new, 1)


def transform(source: pathlib.Path, check_only: bool) -> str:
    path = source / "lib/gx/command_processor.cpp"
    text = path.read_text(encoding="utf-8")
    if SIGNATURE in text:
        return "already-applied"
    if ROLLBACK_MARKER not in text:
        raise RuntimeError("gameplay GX regression rollback must be active first")
    if "M14 raw guest-array stage profile" not in text:
        raise RuntimeError("guest-array timing profile must be active first")

    text = once(
        text,
        "#include <span>\n#include <utility>\n",
        "#include <span>\n#include <unordered_map>\n#include <utility>\n",
        "command_processor unordered-map include",
    )

    text = once(
        text,
        "std::vector<M14GuestArrayStageEntry> sM14GuestArrayStages;\n"
        "u32 sM14GuestArrayStageFrame = UINT32_MAX;\n",
        "std::vector<M14GuestArrayStageEntry> sM14GuestArrayStages;\n"
        "std::unordered_multimap<const void*, u32> sM14GuestArrayStageLookup;\n"
        "u32 sM14GuestArrayStageFrame = UINT32_MAX;\n",
        "command_processor indexed-cache global",
    )

    text = once(
        text,
        "  sM14GuestArrayStages.clear();\n"
        "  sM14GuestArrayStageFrame = frame;\n",
        "  sM14GuestArrayStages.clear();\n"
        "  if (sM14GuestArrayStages.capacity() < 1024u) sM14GuestArrayStages.reserve(1024u);\n"
        "  sM14GuestArrayStageLookup.clear();\n"
        "  sM14GuestArrayStageLookup.reserve(1024u);\n"
        "  sM14GuestArrayStageFrame = frame;\n",
        "command_processor indexed-cache frame reset",
    )

    lookup_old = '''  const auto* source = static_cast<const u8*>(array.data);
  for (auto it = sM14GuestArrayStages.rbegin(); it != sM14GuestArrayStages.rend(); ++it) {
    if (it->data != array.data || it->stride != array.stride || it->snapshot.size() < uploadSize) continue;
    if (std::memcmp(it->snapshot.data(), source, uploadSize) != 0) continue;
    ++sM14GuestArrayReuseHits;
    if (!sM14GuestArrayReuseReported) {
      Log.info("M14 raw guest-array per-frame content reuse active");
      sM14GuestArrayReuseReported = true;
    }
    profileFinish();
    return it->range;
  }

  ++sM14GuestArrayReuseMisses;
'''
    lookup_new = '''  const auto* source = static_cast<const u8*>(array.data);
  const auto candidateRange = sM14GuestArrayStageLookup.equal_range(array.data);
  for (auto lookupIt = candidateRange.first; lookupIt != candidateRange.second; ++lookupIt) {
    const auto& entry = sM14GuestArrayStages[lookupIt->second];
    if (entry.stride != array.stride || entry.snapshot.size() < uploadSize) continue;
    if (std::memcmp(entry.snapshot.data(), source, uploadSize) != 0) continue;
    ++sM14GuestArrayReuseHits;
    if (!sM14GuestArrayReuseReported) {
      Log.info("M14 raw guest-array pointer-indexed exact lookup active");
      sM14GuestArrayReuseReported = true;
    }
    profileFinish();
    return entry.range;
  }

  ++sM14GuestArrayReuseMisses;
'''
    text = once(text, lookup_old, lookup_new, "command_processor exact-cache indexed lookup")

    tail_old = '''  entry.snapshot.assign(source, source + uploadSize);
  entry.range = range;
  sM14GuestArrayStages.emplace_back(std::move(entry));
  sM14GuestArrayStagedBytes += uploadSize;
  profileFinish();
  return range;
}
'''
    tail_new = '''  entry.snapshot.assign(source, source + uploadSize);
  entry.range = range;
  sM14GuestArrayStages.emplace_back(std::move(entry));
  sM14GuestArrayStageLookup.emplace(array.data, static_cast<u32>(sM14GuestArrayStages.size() - 1u));
  sM14GuestArrayStagedBytes += uploadSize;
  profileFinish();
  return range;
}
'''
    text = once(text, tail_old, tail_new, "command_processor exact-cache indexed insert")

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
        print(f"error: Aurora guest-array lookup-index adaptation failed: {exc}", file=sys.stderr)
        return 4
    print({
        "already-applied": "Aurora guest-array pointer-indexed exact lookup already active",
        "verified": "Aurora guest-array pointer-indexed exact lookup verified",
        "applied": "Aurora guest-array pointer-indexed exact lookup applied",
    }[result])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
