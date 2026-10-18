#!/usr/bin/env python3
"""Verify the v7 native-dispatch target mirror matches the M14 manifest."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "state" / "m14-host-targets.json"
MIRROR = Path(__file__).resolve().with_name("m14_host_targets.inc")
ENTRY_RE = re.compile(r"OPENSPM_M14_HOST_TARGET\((0x[0-9A-Fa-f]+)u\)")


def main() -> int:
    document = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest = [int(entry["address"], 0) for entry in document["targets"]]
    mirror = [int(value, 16) for value in ENTRY_RE.findall(MIRROR.read_text(encoding="utf-8"))]

    if len(manifest) != len(set(manifest)):
        raise AssertionError("duplicate target in state/m14-host-targets.json")
    if len(mirror) != len(set(mirror)):
        raise AssertionError("duplicate target in m14_host_targets.inc")
    if set(manifest) != set(mirror):
        missing = sorted(set(manifest) - set(mirror))
        extra = sorted(set(mirror) - set(manifest))
        raise AssertionError(
            "host-target mirror drift: missing="
            + ",".join(f"0x{x:08X}" for x in missing)
            + " extra="
            + ",".join(f"0x{x:08X}" for x in extra)
        )

    print(f"M14 host-target sync: PASS ({len(manifest)} targets)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
