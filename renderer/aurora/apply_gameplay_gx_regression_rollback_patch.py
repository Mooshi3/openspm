#!/usr/bin/env python3
"""Roll back the regressive parts of the first gameplay-GX audit batch.

Player runtime at 52a439e showed globally worse textures and a new hard Bowser
cutscene stall before reaching the prior Tower frontier. Keep only the narrow
source-proven correctness fixes from that batch (zero/small primitive handling,
GXInvalidateVtxCache visibility barrier, NBT3 bounds, and fatal metadata).

This migration restores the previously runtime-proven behavior for:
* ordinary/indirect-simple texture coordinates (undo candidate SU scaling);
* CP array-base pipeline dirty policy;
* exact per-frame guest-array content-cache lookup/snapshot semantics.

It is intentionally an in-place migration for an already-patched player Aurora
checkout, so M14-UPDATE-RUN does not require deleting .openspm. Unknown source
shape fails closed.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
LOCK = HERE / "aurora.lock.json"
SIGNATURE = "M14 gameplay GX audit v1 regression rollback active"


def run(*args: str, cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected source fragment exactly once, found {count}")
    return text.replace(old, new, 1)


def transform(source: pathlib.Path, check_only: bool) -> str:
    cp_path = source / "lib/gx/command_processor.cpp"
    regs_path = source / "lib/gx/regs.cpp"
    shader_path = source / "lib/gx/shader.cpp"
    cp = cp_path.read_text(encoding="utf-8")
    regs = regs_path.read_text(encoding="utf-8")
    shader = shader_path.read_text(encoding="utf-8")

    if SIGNATURE in cp:
        return "already-applied"
    if "M14 gameplay GX audit v1: vertex visibility epoch active" not in cp:
        raise RuntimeError("gameplay GX audit v1 command-processor layer is not active")
    if "M14 gameplay GX audit v1: base-only CP writes do not dirty pipeline" not in regs:
        raise RuntimeError("gameplay GX audit v1 CP-base candidate is not active")
    if "M14 gameplay GX audit v1: ordinary SU scaling active" not in shader:
        raise RuntimeError("gameplay GX audit v1 SU candidate is not active")

    # Keep the explicit GXInvalidateVtxCache barrier, zero/small primitive repair,
    # NBT3 bounds and fatal metadata. Only restore the prior content-cache policy.
    cp = once(
        cp,
        "struct M14GuestArrayStageEntry {\n"
        "  const void* data = nullptr;\n"
        "  u8 stride = 0;\n"
        "  u64 visibilityEpoch = 0;\n"
        "  std::vector<u8> snapshot;\n"
        "  gfx::Range range{};\n"
        "};\n",
        "struct M14GuestArrayStageEntry {\n"
        "  const void* data = nullptr;\n"
        "  u8 stride = 0;\n"
        "  std::vector<u8> snapshot;\n"
        "  gfx::Range range{};\n"
        "};\n",
        "command_processor stage-entry rollback",
    )

    epoch_lookup = '''  const auto* source = static_cast<const u8*>(array.data);
  M14GuestArrayStageEntry* currentEntry = nullptr;
  for (auto& entry : sM14GuestArrayStages) {
    if (entry.data == array.data && entry.stride == array.stride) {
      currentEntry = &entry;
      break;
    }
  }
  if (currentEntry != nullptr && currentEntry->snapshot.size() >= uploadSize) {
    const bool sameVisibility = currentEntry->visibilityEpoch == sM14GuestArrayVisibilityEpoch;
    const bool sameBytes = sameVisibility || std::memcmp(currentEntry->snapshot.data(), source, uploadSize) == 0;
    if (sameBytes) {
      currentEntry->visibilityEpoch = sM14GuestArrayVisibilityEpoch;
      ++sM14GuestArrayReuseHits;
      if (!sM14GuestArrayReuseReported) {
        Log.info("M14 raw guest-array per-frame content/epoch reuse active");
        sM14GuestArrayReuseReported = true;
      }
      profileFinish();
      return currentEntry->range;
    }
  }

  ++sM14GuestArrayReuseMisses;
'''
    exact_lookup = '''  const auto* source = static_cast<const u8*>(array.data);
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
    cp = once(cp, epoch_lookup, exact_lookup, "command_processor cache lookup rollback")

    epoch_tail = '''  const gfx::Range range = gfx::push_storage(source, uploadSize);
  if (currentEntry != nullptr) {
    currentEntry->visibilityEpoch = sM14GuestArrayVisibilityEpoch;
    currentEntry->snapshot.assign(source, source + uploadSize);
    currentEntry->range = range;
  } else {
    M14GuestArrayStageEntry entry;
    entry.data = array.data;
    entry.stride = array.stride;
    entry.visibilityEpoch = sM14GuestArrayVisibilityEpoch;
    entry.snapshot.assign(source, source + uploadSize);
    entry.range = range;
    sM14GuestArrayStages.emplace_back(std::move(entry));
  }
  sM14GuestArrayStagedBytes += uploadSize;
  profileFinish();
  return range;
}
'''
    exact_tail = '''  const gfx::Range range = gfx::push_storage(source, uploadSize);
  M14GuestArrayStageEntry entry;
  entry.data = array.data;
  entry.stride = array.stride;
  entry.snapshot.assign(source, source + uploadSize);
  entry.range = range;
  sM14GuestArrayStages.emplace_back(std::move(entry));
  sM14GuestArrayStagedBytes += uploadSize;
  profileFinish();
  return range;
}
'''
    cp = once(cp, epoch_tail, exact_tail, "command_processor cache update rollback")

    # Marker lives in command_processor so repeated configure is idempotent.
    cp = once(
        cp,
        "// M14 gameplay GX audit v1: vertex visibility epoch active. The Wii's\n",
        "// M14 gameplay GX audit v1 regression rollback active. Risky SU/cache/base candidates restored.\n"
        "// M14 gameplay GX audit v1: vertex visibility epoch active. The Wii's\n",
        "command_processor rollback marker",
    )

    regs = once(
        regs,
        "    // M14 gameplay GX audit v1: base-only CP writes do not dirty pipeline.\n"
        "    // cp_arraybase itself still marks DirtyPipeline for an endian/layout transition.\n"
        "    regs[0xA0 + i] = {cp_arraybase, 0, /* alwaysHandle */ true};\n",
        "    regs[0xA0 + i] = {cp_arraybase, DirtyPipeline, /* alwaysHandle */ true};\n",
        "regs.cpp pipeline dirty rollback",
    )

    shader = once(
        shader,
        "      // M14 gameplay GX audit v1: ordinary SU scaling active. Keep the\n"
        "      // indirect path in texel space so SU scaling and wrap semantics are\n"
        "      // identical even when no indirect matrix/add-prev is selected.\n"
        "      const bool useSimpleCoords = false;\n",
        "      const bool useSimpleCoords = stage.indTexMtxId == GX_ITM_OFF && !stage.indTexAddPrev;\n",
        "shader simple-coordinate rollback",
    )
    shader = once(
        shader,
        "    if (uvIn.empty()) {\n"
        "      // No indirect texturing: GX SU registers convert the generated\n"
        "      // coordinate to texel space. Aurora samples normalized textures, so\n"
        "      // normalize that result by the logical image dimensions.\n"
        "      const u32 texCoordId = underlying(stage.texCoordId);\n"
        "      const u32 texMapId = underlying(stage.texMapId);\n"
        "      uvIn = fmt::format(\n"
        "          \"tex{0}_uv * ubuf.texcoord_scale[{0}].xy / ubuf.tex{1}_size_bias.xy\",\n"
        "          texCoordId, texMapId);\n"
        "    }\n",
        "    if (uvIn.empty()) {\n"
        "      // No indirect texturing\n"
        "      uvIn = fmt::format(\"tex{0}_uv\", underlying(stage.texCoordId));\n"
        "    }\n",
        "shader ordinary SU rollback",
    )

    if not check_only:
        cp_path.write_text(cp, encoding="utf-8", newline="\n")
        regs_path.write_text(regs, encoding="utf-8", newline="\n")
        shader_path.write_text(shader, encoding="utf-8", newline="\n")
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
        print(f"error: Aurora gameplay GX regression rollback failed: {exc}", file=sys.stderr)
        return 4
    print({
        "already-applied": "Aurora gameplay GX regression rollback already active",
        "verified": "Aurora gameplay GX regression rollback verified",
        "applied": "Aurora gameplay GX regression rollback applied",
    }[result])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
