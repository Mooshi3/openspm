#!/usr/bin/env python3
"""OpenSPM bbox fast path: answer provably empty PE bbox reads without the GPU.

Opt-in (OPENSPM_M14_BBOX_FAST=1, or =verify to check every CPU answer against
the GPU readback). With the variable unset every hook returns immediately and
every bbox read takes the GPU path exactly as before.

SPM's star door / flip effects clear the bbox, draw one object, and read the
box back, for every object (~1000 reads in the first frame); each read is a
submit + GPU map. Most boxes come back empty because the object produced no
pixel. The PE box only grows from fragments of GX draws, so when every draw
since the four bbox components were written is proven empty on the CPU the
stored box is exactly the written values (renderer/aurora/bbox_fast.hpp has the
proofs and the conversion; tests/m14_bbox_fast_test.cpp tests them).

What this adds to pinned Aurora:
- lib/gx/command_processor.cpp: the classifier (bbox_fast.hpp embedded between
  "// OpenSPM bbox fast BEGIN/END" markers inside aurora::gx::fifo, refreshed
  from the repository on every configure) and one hook per recorded draw in
  draw_prim (merged and unmerged). It reads each vertex position from the same
  bytes the vertex shader reads (the recorded vertex stream and the frame's
  staged array storage at the recorded draw's arrayStart), with the recorded
  draw's matrix slot, Aurora's position matrices and projection;
- lib/gx/bbox.cpp: queue_write() tells the window the host-pixel value it
  stores (FIFO BP and CPU register writes both end there, in GX order);
- lib/gx/raw.cpp: m14_bbox_fast_mode()/m14_bbox_fast_read() for the renderer,
  converting the stored values with the same scale/rounding as read_bbox();
- lib/gfx/recording.{hpp,cpp}: m14_bbox_fast_storage_bytes(), read-only access
  to the recording frame's staged storage bytes.

Runs last in the CMake chain. Exact/idempotent; fails closed on drift.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
LOCK = HERE / "aurora.lock.json"
CP = pathlib.Path("lib/gx/command_processor.cpp")
BBOX = pathlib.Path("lib/gx/bbox.cpp")
RAW = pathlib.Path("lib/gx/raw.cpp")
REC_H = pathlib.Path("lib/gfx/recording.hpp")
REC_CPP = pathlib.Path("lib/gfx/recording.cpp")

BEGIN = "// OpenSPM bbox fast BEGIN\n"
END = "// OpenSPM bbox fast END\n"
DECL_BEGIN = "// OpenSPM bbox fast declarations BEGIN\n"
DECL_END = "// OpenSPM bbox fast declarations END\n"

CP_ANCHOR = "static void draw_prim(GXPrimitive prim, GXVtxFmt fmt, u16 vtxCount, ByteReader& reader) noexcept {\n"
MERGE_OLD = "    m14_audit_draw(prim, fmt, vtxCount, vertRange, true, sDrawCache.shaderInfo);\n"
MERGE_NEW = ("    m14_bbox_fast_draw(prim, fmt, vtxCount, vertexData, lastDraw);  // OpenSPM bbox fast\n"
             + MERGE_OLD)
UNMERGED_OLD = "  handle_draw_unmerged(prim, fmt, vtxCount, vertRange, guestArrays);\n"
UNMERGED_NEW = (UNMERGED_OLD
                + "  m14_bbox_fast_draw(prim, fmt, vtxCount, vertexData, nullptr);  // OpenSPM bbox fast\n")

CP_GLUE = r'''
namespace {
struct M14BboxFastRuntime {
  int mode = openspm_bbox_fast::kModeOff;
  openspm_bbox_fast::Window window;
  std::array<u64, 4> draws{};  // classified draws by Reason (kNotEmpty = GPU needed)
};

M14BboxFastRuntime& m14_bbox_fast_runtime() noexcept {
  static M14BboxFastRuntime runtime = [] {
    M14BboxFastRuntime value;
    value.mode = openspm_bbox_fast::parse_mode(std::getenv("OPENSPM_M14_BBOX_FAST"));
    if (value.mode != openspm_bbox_fast::kModeOff) {
      Log.info("M14 bbox fast active (OPENSPM_M14_BBOX_FAST={}): reads after only provably empty draws "
               "(no draws, offscreen, degenerate) {}",
               value.mode == openspm_bbox_fast::kModeVerify ? "verify" : "1",
               value.mode == openspm_bbox_fast::kModeVerify ? "are compared with the GPU readback"
                                                            : "skip the GPU sync");
    }
    return value;
  }();
  return runtime;
}

// Everything the vertex shader reads to place this draw's vertices: the
// recorded vertex stream, the recorded draw's immediates (matrix slot and the
// position array start in the frame's staged storage), Aurora's position
// matrices and projection. Anything unusual is "not provably empty".
openspm_bbox_fast::Reason m14_bbox_fast_classify(GXPrimitive prim, GXVtxFmt fmt, u16 vtxCount,
                                                 std::span<const u8> vertexData, const DrawData& recorded) noexcept {
  using namespace openspm_bbox_fast;
  switch (prim) {
  case GX_TRIANGLES:
    // A non-indexed triangle list merges by concatenation: leftover vertices
    // would form triangles with the next draw's vertices.
    if (vtxCount % 3u != 0u) return kNotEmpty;
    break;
  case GX_QUADS:
  case GX_TRIANGLESTRIP:
  case GX_TRIANGLEFAN:
    break;
  default:
    return kNotEmpty;  // lines and points are widened in screen space
  }
  const auto& state = g_gxState;
  if (vtxCount == 0u || vertexData.size() % vtxCount != 0u) return kNotEmpty;
  const size_t vtxSize = vertexData.size() / vtxCount;
  const auto& attrs = state.vtxFmts[fmt].attrs;
  size_t offset = 0;
  size_t pnmtxOffset = SIZE_MAX;
  for (int i = GX_VA_PNMTXIDX; i < GX_VA_POS; ++i) {
    const auto desc = state.vtxDesc[i];
    if (desc == GX_NONE) continue;
    if (desc != GX_DIRECT) return kNotEmpty;
    if (i == GX_VA_PNMTXIDX) pnmtxOffset = offset;
    const auto attr = static_cast<GXAttr>(i);
    offset += comp_type_size(attr, attrs[i].type) * comp_cnt_count(attr, attrs[i].cnt);
  }
  const auto posDesc = state.vtxDesc[GX_VA_POS];
  const auto& pos = attrs[GX_VA_POS];
  const unsigned count = pos.cnt == GX_POS_XY ? 2u : pos.cnt == GX_POS_XYZ ? 3u : 0u;
  const unsigned componentSize = component_size(static_cast<unsigned>(pos.type));
  if (count == 0u || componentSize == 0u) return kNotEmpty;
  const u32 elementSize = count * componentSize;
  const u32 indexSize = posDesc == GX_DIRECT ? 0u : posDesc == GX_INDEX8 ? 1u : posDesc == GX_INDEX16 ? 2u : UINT32_MAX;
  if (indexSize == UINT32_MAX) return kNotEmpty;
  if (offset + (indexSize == 0u ? elementSize : indexSize) > vtxSize) return kNotEmpty;
  if (pnmtxOffset != SIZE_MAX && pnmtxOffset >= vtxSize) return kNotEmpty;
  const auto& array = state.arrays[GX_VA_POS];
  const bool le = indexSize == 0u ? false : array.le;
  const u32 arrayStart = recorded.immediateData.arrayStart[0];

  Mat44 proj{};
  static_assert(sizeof(proj) == sizeof(state.proj));
  std::memcpy(&proj, &state.proj, sizeof(proj));
  std::array<Mat34, MaxPnMtx> matrices{};
  static_assert(sizeof(Mat34) == sizeof(state.pnMtx[0].pos));
  for (u32 m = 0; m < MaxPnMtx; ++m) std::memcpy(&matrices[m], &state.pnMtx[m].pos, sizeof(Mat34));

  DrawClassifier classifier;
  for (u32 v = 0; v < vtxCount; ++v) {
    const u8* vtx = vertexData.data() + static_cast<size_t>(v) * vtxSize;
    const u32 matrix = pnmtxOffset != SIZE_MAX ? vtx[pnmtxOffset] / 3u : recorded.immediateData.currentPnMtx;
    if (matrix >= MaxPnMtx) return kNotEmpty;
    const u8* element = vtx + offset;
    if (indexSize != 0u) {
      const u32 index = indexSize == 1u ? vtx[offset] : (u32(vtx[offset]) << 8u) | vtx[offset + 1u];
      // Same u32 arithmetic as the shader's address (arrayStart + index * stride).
      const u32 address = arrayStart + index * static_cast<u32>(array.stride);
      element = gfx::m14_bbox_fast_storage_bytes(address, elementSize);
      if (element == nullptr) return kNotEmpty;
    }
    float p[3] = {0.f, 0.f, 0.f};
    for (unsigned c = 0; c < count; ++c) {
      if (!decode_component(element + c * componentSize, static_cast<unsigned>(pos.type), pos.frac, le, p[c]))
        return kNotEmpty;
    }
    classifier.add(p, matrix, matrices[matrix], proj);
    if (classifier.hopeless()) return kNotEmpty;
  }
  return classifier.result();
}
} // namespace

// After draw_prim recorded a draw (merged: the extended previous command).
void m14_bbox_fast_draw(GXPrimitive prim, GXVtxFmt fmt, u16 vtxCount, std::span<const u8> vertexData,
                        const DrawData* recorded) noexcept {
  auto& runtime = m14_bbox_fast_runtime();
  if (runtime.mode == openspm_bbox_fast::kModeOff || !runtime.window.wants_draw()) return;
  if (recorded == nullptr) recorded = gfx::get_last_draw_command<DrawData>();
  const auto reason = recorded == nullptr ? openspm_bbox_fast::kNotEmpty
                                          : m14_bbox_fast_classify(prim, fmt, vtxCount, vertexData, *recorded);
  ++runtime.draws[reason & 3u];
  runtime.window.note_draw(reason);
}

int m14_bbox_fast_mode() noexcept { return m14_bbox_fast_runtime().mode; }

// bbox::queue_write, in GX order: the host-pixel value now stored.
void m14_bbox_fast_note_write(std::uint32_t index, std::uint16_t hostValue) noexcept {
  auto& runtime = m14_bbox_fast_runtime();
  if (runtime.mode != openspm_bbox_fast::kModeOff) runtime.window.note_write(index, hostValue);
}

unsigned m14_bbox_fast_stored(std::array<std::uint16_t, 4>& host) noexcept {
  auto& runtime = m14_bbox_fast_runtime();
  if (runtime.mode == openspm_bbox_fast::kModeOff) return openspm_bbox_fast::kNotEmpty;
  return runtime.window.stored(host);
}

void m14_bbox_fast_take_draw_stats(std::array<std::uint64_t, 4>& out) noexcept {
  auto& runtime = m14_bbox_fast_runtime();
  for (size_t i = 0; i < out.size(); ++i) out[i] = runtime.draws[i];
  runtime.draws.fill(0u);
}
'''

FIFO_DECLS = r'''namespace aurora::gx::fifo {
int m14_bbox_fast_mode() noexcept;
unsigned m14_bbox_fast_stored(std::array<std::uint16_t, 4>& host) noexcept;
void m14_bbox_fast_take_draw_stats(std::array<std::uint64_t, 4>& out) noexcept;
} // namespace aurora::gx::fifo
'''

RAW_GLUE = r'''namespace aurora::gx::raw {
int m14_bbox_fast_mode() noexcept { return fifo::m14_bbox_fast_mode(); }

// The PE bbox without a GPU sync when no draw since the four components were
// written can have touched a pixel; converted exactly like read_bbox().
bool m14_bbox_fast_read(std::array<std::uint16_t, 4>& out, unsigned& reason) noexcept {
  std::array<std::uint16_t, 4> host{};
  reason = fifo::m14_bbox_fast_stored(host);
  if (reason == openspm_bbox_fast::kNotEmpty) return false;
  out = openspm_bbox_fast::logical_from_host(host, bbox_axis_scale(0), bbox_axis_scale(2), bbox_logical_enabled());
  return true;
}

void m14_bbox_fast_take_draw_stats(std::array<std::uint64_t, 4>& out) noexcept {
  fifo::m14_bbox_fast_take_draw_stats(out);
}
} // namespace aurora::gx::raw
'''

BBOX_DECLS = (DECL_BEGIN
              + "namespace aurora::gx::fifo {\n"
              + "void m14_bbox_fast_note_write(std::uint32_t index, std::uint16_t hostValue) noexcept;\n"
              + "} // namespace aurora::gx::fifo\n"
              + DECL_END)
BBOX_DECL_ANCHOR = "namespace aurora::gx::bbox {\n"
QUEUE_OLD = ("void queue_write(std::uint32_t index, std::uint16_t value) noexcept {\n"
             "  if (!sStorage || index >= 4u) return;\n")
QUEUE_NEW = (QUEUE_OLD
             + "  fifo::m14_bbox_fast_note_write(index, value);  // OpenSPM bbox fast\n")

CP_DECLS = (DECL_BEGIN
            + "namespace aurora::gfx {\n"
            + "const std::uint8_t* m14_bbox_fast_storage_bytes(std::uint32_t offset, std::uint32_t size) noexcept;\n"
            + "} // namespace aurora::gfx\n"
            + DECL_END)
REC_H_DECLS = CP_DECLS

REC_CPP_BLOCK = (BEGIN + r'''namespace aurora::gfx {
// Read-only view of bytes staged into the recording frame's storage buffer
// (what abuf holds when the frame's draws execute); nullptr when out of range.
const std::uint8_t* m14_bbox_fast_storage_bytes(std::uint32_t offset, std::uint32_t size) noexcept {
  if (!g_recorder.active()) return nullptr;
  const auto& storage = g_recorder.frame().storage;
  const size_t begin = offset;
  if (begin > storage.size() || size > storage.size() - begin) return nullptr;
  return storage.data() + begin;
}
} // namespace aurora::gfx
''' + END)

# Strings other chain scripts search for or count; embedded code must never contain them.
FOREIGN_ANCHORS = (
    "static void draw_prim(",
    "static void push_gx_draw(",
    "static void handle_draw",
    "struct M14InterpProbeDraw {",
    "void clear_draw_cache() noexcept {",
    "m14_interp_probe_",
    "current_frame_packet",
    "handle_draw_unmerged(",
    "increment_merged_draw_count",
    "m14_audit_draw(",
    "} // namespace aurora::gx::fifo\n\n",
    "namespace aurora::gx::bbox {",
    "} // namespace aurora::gx::bbox",
    "} // namespace aurora::gx::raw\n\n",
    "void write_bbox(",
    "bool read_bbox(",
    "bool read_sync(",
    "void queue_write(",
    "m14_bbox_memo",
    "bool m14_hash_recorded_",
    "FrameRecorder g_recorder;",
    "} // namespace\n\nnamespace detail {\n",
    "namespace aurora::gx::fifo {\nnamespace {\n",
)


def run(*args: str, cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def header_body() -> str:
    header = (HERE / "bbox_fast.hpp").read_text(encoding="utf-8")
    return "".join(line for line in header.splitlines(keepends=True)
                   if not line.startswith("#include") and line.strip() != "#pragma once")


def header_includes() -> str:
    header = (HERE / "bbox_fast.hpp").read_text(encoding="utf-8")
    return "".join(line for line in header.splitlines(keepends=True) if line.startswith("#include <"))


def check_foreign(block: str, what: str) -> str:
    for anchor in FOREIGN_ANCHORS:
        if anchor in block:
            raise RuntimeError(f"bbox fast {what} contains a foreign chain anchor: {anchor!r}")
    return block


def cp_block() -> str:
    body = header_body() + CP_GLUE
    if "#include" in body:
        raise RuntimeError("bbox fast command processor code must not contain #include lines")
    return check_foreign(BEGIN + body + END, "command processor code")


def raw_block() -> str:
    # Appended after raw.cpp's last namespace: global scope, so it can carry its
    # own includes and the pure model at file scope.
    return check_foreign(BEGIN + header_includes() + '#include "../internal.hpp"\n'
                         + header_body() + FIFO_DECLS + RAW_GLUE + END, "raw.cpp code")


def replace_once(text: str, old: str, new: str, what: str) -> str:
    if new in text:
        if text.count(new) != 1:
            raise RuntimeError(f"bbox fast {what} is not unique")
        return text
    if text.count(old) != 1:
        raise RuntimeError(f"bbox fast {what} anchor expected once, found {text.count(old)}")
    return text.replace(old, new, 1)


def replace_marked(text: str, begin: str, end: str, block: str, anchor: str | None, what: str) -> str:
    """Refresh a marked block in place, or insert it before anchor (None = append at EOF)."""
    if begin in text:
        if text.count(begin) != 1 or text.count(end) != 1:
            raise RuntimeError(f"bbox fast {what} markers are not unique")
        start = text.index(begin)
        stop = text.index(end, start) + len(end)
        return text[:start] + block + text[stop:]
    if end in text:
        raise RuntimeError(f"bbox fast {what} has an end marker without a begin marker")
    if anchor is None:
        if not text.endswith("\n"):
            text += "\n"
        return text + "\n" + block
    if text.count(anchor) != 1:
        raise RuntimeError(f"bbox fast {what} anchor expected once, found {text.count(anchor)}")
    at = text.index(anchor)
    return text[:at] + block + "\n" + text[at:]


def patch_cp(text: str) -> str:
    text = replace_marked(text, DECL_BEGIN, DECL_END, CP_DECLS, "namespace aurora::gx::raw {\n", "cp declarations")
    text = replace_marked(text, BEGIN, END, cp_block(), CP_ANCHOR, "command processor code")
    text = replace_once(text, MERGE_OLD, MERGE_NEW, "merged draw hook")
    text = replace_once(text, UNMERGED_OLD, UNMERGED_NEW, "unmerged draw hook")
    order = (text.index(DECL_BEGIN), text.index("namespace aurora::gx::fifo {\n"), text.index(BEGIN),
             text.index(CP_ANCHOR), text.index(MERGE_NEW), text.index(UNMERGED_NEW))
    if list(order) != sorted(order):
        raise RuntimeError("bbox fast command_processor.cpp edits are out of place")
    return text


def patch_bbox(text: str) -> str:
    text = replace_marked(text, DECL_BEGIN, DECL_END, BBOX_DECLS, BBOX_DECL_ANCHOR, "bbox declarations")
    text = replace_once(text, QUEUE_OLD, QUEUE_NEW, "queue_write hook")
    if not text.index(DECL_END) < text.index(BBOX_DECL_ANCHOR) < text.index(QUEUE_NEW):
        raise RuntimeError("bbox fast bbox.cpp edits are out of place")
    return text


def patch_raw(text: str) -> str:
    text = replace_marked(text, BEGIN, END, raw_block(), None, "raw.cpp code")
    last_namespace = text.rindex("} // namespace aurora::gx::raw\n", 0, text.index(BEGIN))
    if "bool read_bbox(std::array<std::uint16_t, 4>& out) noexcept {" not in text[:last_namespace]:
        raise RuntimeError("bbox fast raw.cpp code is out of place")
    if not text.endswith(END):
        raise RuntimeError("bbox fast raw.cpp code must be last")
    return text


def patch_rec_h(text: str) -> str:
    return replace_marked(text, DECL_BEGIN, DECL_END, REC_H_DECLS, None, "recording.hpp declarations")


def patch_rec_cpp(text: str) -> str:
    text = replace_marked(text, BEGIN, END, REC_CPP_BLOCK, None, "recording.cpp accessor")
    if not text.endswith(END):
        raise RuntimeError("bbox fast recording.cpp accessor must be last")
    return text


def transform(source: pathlib.Path, check_only: bool) -> str:
    files = {name: source / name for name in (CP, BBOX, RAW, REC_H, REC_CPP)}
    original = {name: path.read_text(encoding="utf-8") for name, path in files.items()}
    prerequisites = (
        (CP, CP_ANCHOR),
        (CP, "  auto* lastDraw = cleanState ? gfx::get_last_draw_command<DrawData>() : nullptr;\n"),
        (CP, "  const auto vertexData = reader.take(totalVtxBytes);\n"),
        (CP, "constexpr Module Log{\"aurora::gx::fifo\"};"),
        (BBOX, "bool m14_read_sync_worker("),               # bbox priority
        (RAW, "double bbox_axis_scale(std::uint32_t index) noexcept {"),  # bbox logical scale
        (RAW, "out[1] = static_cast<std::uint16_t>(std::min<std::uint32_t>(out[1] | 1u, 1023u));"),
        (REC_CPP, "FrameRecorder g_recorder;"),
        (REC_CPP, "g_recorder.frame()"),
    )
    missing = [needle for name, needle in prerequisites if needle not in original[name]]
    if missing:
        raise RuntimeError("bbox fast prerequisites missing: " + ", ".join(m[:60] for m in missing))
    updated = {
        CP: patch_cp(original[CP]),
        BBOX: patch_bbox(original[BBOX]),
        RAW: patch_raw(original[RAW]),
        REC_H: patch_rec_h(original[REC_H]),
        REC_CPP: patch_rec_cpp(original[REC_CPP]),
    }
    required = (
        (CP, "class DrawClassifier {"),
        (CP, 'std::getenv("OPENSPM_M14_BBOX_FAST")'),
        (CP, MERGE_NEW),
        (CP, UNMERGED_NEW),
        (BBOX, QUEUE_NEW),
        (RAW, "bool m14_bbox_fast_read("),
        (RAW, "inline std::array<std::uint16_t, 4> logical_from_host("),
        (REC_H, "m14_bbox_fast_storage_bytes("),
        (REC_CPP, "m14_bbox_fast_storage_bytes("),
    )
    missing = [needle for name, needle in required if needle not in updated[name]]
    if missing:
        raise RuntimeError("bbox fast migration incomplete: " + ", ".join(m[:40] for m in missing))
    changed = [name for name in files if updated[name] != original[name]]
    if check_only:
        return "verified" if not changed else "needs-apply"
    for name in changed:
        files[name].write_text(updated[name], encoding="utf-8", newline="\n")
    return "applied" if changed else "already-applied"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
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
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"error: Aurora bbox fast adaptation failed: {exc}", file=sys.stderr)
        return 4
    print({
        "verified": "Aurora M14 bbox fast verified",
        "needs-apply": "Aurora M14 bbox fast not yet applied",
        "applied": "Aurora M14 bbox fast applied",
        "already-applied": "Aurora M14 bbox fast already applied",
    }[result])
    return 0 if result != "needs-apply" else 5


if __name__ == "__main__":
    raise SystemExit(main())
