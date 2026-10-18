#!/usr/bin/env python3
"""OpenSPM Stage B draw tags: latch the game-provided draw owner per GX draw.

Opt-in (OPENSPM_M14_DRAW_TAGS=1). The LLVM bridge writes owner markers into
the GX FIFO at SPM's dispDraw callback boundaries (BP 0x7E/0x7F, see
tools/native_bridge/m14_draw_tags.h). With the variable unset the bridge emits
nothing, these handlers never run and every draw is recorded exactly as before.

What this adds to pinned Aurora:
- lib/gx/regs.cpp: BP 0x7E/0x7F handlers (both unassigned on hardware and
  previously bp_unhandled) that feed the owner latch; when the owner changes
  the next draw may not merge into the previous draw command. BP 0x7C/0x7D
  carry Stage C camera view records (never an owner change);
- lib/gfx/frame_packet.hpp: an 8-byte per-draw sidecar on DrawCommand
  (m14DrawOwner, m14DrawMeta, m14DrawView) placed in existing padding, so the
  command size is unchanged, and FramePacket::m14DrawViews (the camera view
  records the frame's draws refer to);
- lib/gfx/recording.cpp: renderer/aurora/draw_tags.hpp (pure decoder and
  coverage statistics, unit tested by test_display_pacing.py) embedded between
  "// OpenSPM draw tags BEGIN/END" markers and refreshed from the repository on
  every configure (own-revision migration), the stamp in push_draw_command()
  and the once-per-second "M14 draw tags:" log line.

Revision 2 (Stage C) adds the view records; caches patched by revision 1 are
migrated in place (sidecar, BP table and stamp call are upgraded).

Runs after apply_present_timeline_patch.py (last in the CMake chain).
Exact/idempotent; fails closed on drift.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
LOCK = HERE / "aurora.lock.json"
REGS = pathlib.Path("lib/gx/regs.cpp")
RECORDING = pathlib.Path("lib/gfx/recording.cpp")
PACKET = pathlib.Path("lib/gfx/frame_packet.hpp")

BEGIN = "// OpenSPM draw tags BEGIN\n"
END = "// OpenSPM draw tags END\n"
DECL_BEGIN = "// OpenSPM draw tags declarations BEGIN\n"
DECL_END = "// OpenSPM draw tags declarations END\n"
HANDLER_BEGIN = "// OpenSPM draw tags handler BEGIN\n"
HANDLER_END = "// OpenSPM draw tags handler END\n"

PACKET_ANCHOR = ("  DrawEncoder encoder = nullptr;\n"
                 "  alignas(std::max_align_t) std::array<std::byte, InlineDrawPayloadSize> payload{};\n")
PACKET_FIELDS_V1 = ("  uint32_t m14DrawOwner = 0;  // OpenSPM draw tags: dispDraw owner, 0 = none\n"
                    "  uint16_t m14DrawMeta = 0;   // OpenSPM draw tags: camera | ortho << 4 | tagged << 5\n")
PACKET_FIELDS = (PACKET_FIELDS_V1
                 + "  uint16_t m14DrawView = 0;   // OpenSPM draw tags: 1-based FramePacket::m14DrawViews index\n")
FRAME_ANCHOR = "  StagingHighWater copied;\n  AuroraStats stats{};\n"
FRAME_FIELDS = ("  std::vector<std::array<float, 12>> m14DrawViews;  "
                "// OpenSPM draw tags: camera view records (world->view 3x4)\n")

RECORDING_ANCHOR = "namespace aurora::gfx {\nusing namespace detail;\n"
STAMP_OLD = ("void push_draw_command(DrawCommand data) {\n"
             "  push_command(CommandType::Draw, Command::Data{.draw = data});\n")
STAMP_V1 = ("void push_draw_command(DrawCommand data) {\n"
            "  m14_draw_tag_stamp(data);  // OpenSPM draw tags (no-op unless enabled)\n"
            "  push_command(CommandType::Draw, Command::Data{.draw = data});\n")
STAMP_NEW = ("void push_draw_command(DrawCommand data) {\n"
             "  m14_draw_tag_stamp(data, g_recorder.frame());  // OpenSPM draw tags (no-op unless enabled)\n"
             "  push_command(CommandType::Draw, Command::Data{.draw = data});\n")
RECORDING_INCLUDES = ("#include <cstdint>\n", "#include <cstdio>\n")

GLUE = r'''
namespace {
constexpr Module M14DrawTagLog{"aurora::gfx::drawtags"};

struct M14DrawTagRuntime {
  bool enabled = false;
  bool reported = false;
  openspm_draw_tags::Tracker tracker;
  // Stage C camera views: the latest record per camera in this game frame and
  // its index in the recording packet (0 = not yet added to that packet).
  std::array<std::array<float, 12>, openspm_draw_tags::kCameraSlots> view{};
  std::uint32_t viewValid = 0;
  std::uint64_t viewFrameSerial = 0;
  std::uint64_t viewPacket = 0;
  std::array<std::uint16_t, openspm_draw_tags::kCameraSlots> viewIndex{};
};

M14DrawTagRuntime& m14_draw_tag_runtime() noexcept {
  static M14DrawTagRuntime runtime = [] {
    M14DrawTagRuntime value;
    const char* env = std::getenv("OPENSPM_M14_DRAW_TAGS");
    value.enabled = env != nullptr && env[0] == '1';
    return value;
  }();
  return runtime;
}
} // namespace

// BP 0x7E/0x7F from the raw FIFO (regs.cpp). Returns true when the owner of
// subsequent draws changed, so the caller blocks merging across it.
bool m14_draw_tag_bp(std::uint8_t reg, std::uint32_t value) noexcept {
  auto& runtime = m14_draw_tag_runtime();
  if (!runtime.enabled) {
    return false;
  }
  if (!runtime.reported) {
    runtime.reported = true;
    M14DrawTagLog.info("M14 draw tags renderer active: BP 0x7E/0x7F owner markers latched per draw "
                       "(DrawCommand m14DrawOwner/m14DrawMeta); no draw merges across an owner change");
  }
  const bool changed = runtime.tracker.on_bp(reg, value);
  if (runtime.tracker.frame_serial() != runtime.viewFrameSerial) {  // views are per game frame
    runtime.viewFrameSerial = runtime.tracker.frame_serial();
    runtime.viewValid = 0;
  }
  std::array<float, 12> view{};
  if (const int camera = runtime.tracker.take_view(view); camera >= 0) {
    runtime.view[static_cast<std::size_t>(camera)] = view;
    runtime.viewValid |= 1u << camera;
    runtime.viewIndex[static_cast<std::size_t>(camera)] = 0;
  }
  if (runtime.tracker.frame_ended()) {
    const auto now = static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count());
    openspm_draw_tags::Window window;
    if (runtime.tracker.take_report(now, UINT64_C(1000000000), window)) {
      M14DrawTagLog.info("{}", openspm_draw_tags::Tracker::format(window));
    }
  }
  return changed;
}

void m14_draw_tag_stamp(DrawCommand& command, FramePacket& packet) noexcept {
  auto& runtime = m14_draw_tag_runtime();
  if (!runtime.enabled) {
    return;
  }
  const auto& tag = runtime.tracker.current();
  command.m14DrawOwner = tag.owner;
  command.m14DrawMeta = openspm_draw_tags::pack_meta(tag);
  command.m14DrawView = 0;
  if (tag.owner != 0 && tag.camera < openspm_draw_tags::kCameraSlots && ((runtime.viewValid >> tag.camera) & 1u) != 0) {
    // Every draw refers to the view record that precedes it in the stream,
    // copied into the packet the draw is recorded in.
    if (runtime.viewPacket != packet.frameId) {
      runtime.viewPacket = packet.frameId;
      runtime.viewIndex.fill(0);
    }
    auto& index = runtime.viewIndex[tag.camera];
    if (index == 0 && packet.m14DrawViews.size() < 0xFFFFu) {
      packet.m14DrawViews.push_back(runtime.view[tag.camera]);
      index = static_cast<std::uint16_t>(packet.m14DrawViews.size());
    }
    command.m14DrawView = index;
  }
  if (command.m14ReplayGX) {
    runtime.tracker.on_draw();
  }
}
'''

DECLARATIONS = (
    DECL_BEGIN
    + "namespace aurora::gfx::detail {\n"
    + "bool m14_draw_tag_bp(std::uint8_t reg, std::uint32_t value) noexcept;\n"
    + "} // namespace aurora::gfx::detail\n"
    + DECL_END
)
REGS_DECL_ANCHOR = "namespace aurora::gx::fifo {\nnamespace {\n"

HANDLER = (
    HANDLER_BEGIN
    + "void bp_m14_draw_tag(u8 reg, u32 value) noexcept {\n"
    + "  if (::aurora::gfx::detail::m14_draw_tag_bp(reg, value & 0x00FFFFFFu)) {\n"
    + "    // A new owner starts here: never merge the next draw into the previous one.\n"
    + "    g_gxState.dirty |= DirtyImmediates;\n"
    + "  }\n"
    + "}\n"
    + HANDLER_END
)
HANDLER_ANCHOR = "void bp_unhandled(u8 reg, u32 value) noexcept {"

TABLE_OLD = ("    regs[r] = {bp_ksel, DirtyPipeline};\n"
             "  }\n"
             "  return regs;\n"
             "}();\n")
TABLE_V1 = ("    regs[r] = {bp_ksel, DirtyPipeline};\n"
            "  }\n"
            "  regs[0x7E] = {bp_m14_draw_tag, 0, /* alwaysHandle */ true}; // OpenSPM draw tags\n"
            "  regs[0x7F] = {bp_m14_draw_tag, 0, /* alwaysHandle */ true}; // OpenSPM draw tags\n"
            "  return regs;\n"
            "}();\n")
TABLE_NEW = ("    regs[r] = {bp_ksel, DirtyPipeline};\n"
             "  }\n"
             "  regs[0x7C] = {bp_m14_draw_tag, 0, /* alwaysHandle */ true}; // OpenSPM draw tags: view record\n"
             "  regs[0x7D] = {bp_m14_draw_tag, 0, /* alwaysHandle */ true}; // OpenSPM draw tags: view record\n"
             "  regs[0x7E] = {bp_m14_draw_tag, 0, /* alwaysHandle */ true}; // OpenSPM draw tags\n"
             "  regs[0x7F] = {bp_m14_draw_tag, 0, /* alwaysHandle */ true}; // OpenSPM draw tags\n"
             "  return regs;\n"
             "}();\n")

# Strings other chain scripts anchor on; embedded code must never contain them.
FOREIGN_ANCHORS = (
    "FrameRecorder g_recorder;",
    "struct M14ReplaySnapshot {",
    "void m14_push_gx_draw_command(",
    "void m14_prepare_interpolation_replay() noexcept {",
    "template <>\nvoid push_draw_command(gx::DrawData data) {",
    "m14ReplayGX = false",
    "namespace aurora::gfx {\n",
)


def run(*args: str, cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def embedded_region() -> str:
    header = (HERE / "draw_tags.hpp").read_text(encoding="utf-8")
    lines = [line for line in header.splitlines(keepends=True)
             if not line.startswith("#include") and line.strip() != "#pragma once"]
    body = "".join(lines) + GLUE
    for anchor in FOREIGN_ANCHORS:
        if anchor in body:
            raise RuntimeError(f"draw tags code contains a foreign chain anchor: {anchor!r}")
    if "#include" in body:
        raise RuntimeError("draw tags code must not contain #include lines")
    return (BEGIN + "namespace aurora::gfx::detail {\n" + body
            + "} // namespace aurora::gfx::detail\n" + END)


def ensure_include(text: str, include: str) -> str:
    if include in text:
        return text
    anchor = "#include <array>\n"
    if text.count(anchor) != 1:
        raise RuntimeError(f"draw tags include anchor missing for {include.strip()}")
    return text.replace(anchor, include + anchor, 1)


def replace_once(text: str, old: str, new: str, what: str) -> str:
    if new in text:
        if text.count(new) != 1:
            raise RuntimeError(f"draw tags {what} is not unique")
        return text
    if text.count(old) != 1:
        raise RuntimeError(f"draw tags {what} anchor expected once, found {text.count(old)}")
    return text.replace(old, new, 1)


def replace_marked(text: str, begin: str, end: str, block: str, anchor: str, what: str) -> str:
    if begin in text:
        if text.count(begin) != 1 or text.count(end) != 1:
            raise RuntimeError(f"draw tags {what} markers are not unique")
        start = text.index(begin)
        stop = text.index(end, start) + len(end)
        return text[:start] + block + text[stop:]
    if text.count(anchor) != 1:
        raise RuntimeError(f"draw tags {what} anchor expected once, found {text.count(anchor)}")
    at = text.index(anchor)
    return text[:at] + block + "\n" + text[at:]


def patch_packet(text: str) -> str:
    if PACKET_FIELDS in text:
        if text.count(PACKET_FIELDS) != 1:
            raise RuntimeError("draw tags DrawCommand sidecar is not unique")
    elif text.count(PACKET_ANCHOR + PACKET_FIELDS_V1) == 1 and "m14DrawView" not in text:
        text = text.replace(PACKET_ANCHOR + PACKET_FIELDS_V1, PACKET_ANCHOR + PACKET_FIELDS, 1)  # revision 1
    elif "m14DrawOwner" in text or "m14DrawMeta" in text or "m14DrawView" in text:
        raise RuntimeError("draw tags DrawCommand sidecar has an unknown shape")
    else:
        text = replace_once(text, PACKET_ANCHOR, PACKET_ANCHOR + PACKET_FIELDS, "DrawCommand sidecar")
    if "> m14DrawViews;" in text and FRAME_ANCHOR + FRAME_FIELDS not in text:
        raise RuntimeError("draw tags FramePacket view records have an unknown shape")
    return replace_once(text, FRAME_ANCHOR, FRAME_ANCHOR + FRAME_FIELDS, "FramePacket view records")


def patch_recording(text: str) -> str:
    for include in RECORDING_INCLUDES:
        text = ensure_include(text, include)
    text = replace_marked(text, BEGIN, END, embedded_region(), RECORDING_ANCHOR, "recording code")
    if not text.index(BEGIN) < text.index(RECORDING_ANCHOR):
        raise RuntimeError("draw tags recording code is out of place")
    if STAMP_NEW not in text and text.count(STAMP_V1) == 1:
        text = text.replace(STAMP_V1, STAMP_NEW, 1)  # revision 1
    return replace_once(text, STAMP_OLD, STAMP_NEW, "draw stamp")


def patch_regs(text: str) -> str:
    text = replace_marked(text, DECL_BEGIN, DECL_END, DECLARATIONS, REGS_DECL_ANCHOR, "declarations")
    text = replace_marked(text, HANDLER_BEGIN, HANDLER_END, HANDLER, HANDLER_ANCHOR, "BP handler")
    if TABLE_NEW not in text and text.count(TABLE_V1) == 1:
        text = text.replace(TABLE_V1, TABLE_NEW, 1)  # revision 1
    text = replace_once(text, TABLE_OLD, TABLE_NEW, "BP table")
    order = (text.index(DECL_BEGIN), text.index(REGS_DECL_ANCHOR), text.index(HANDLER_BEGIN),
             text.index(HANDLER_ANCHOR), text.index(TABLE_NEW))
    if list(order) != sorted(order):
        raise RuntimeError("draw tags regs.cpp edits are out of place")
    return text


def transform(source: pathlib.Path, check_only: bool) -> str:
    files = {name: source / name for name in (REGS, RECORDING, PACKET)}
    original = {name: path.read_text(encoding="utf-8") for name, path in files.items()}
    prerequisites = (
        (RECORDING, "command.m14ReplayGX = true;"),
        (RECORDING, STAMP_OLD.splitlines()[0]),
        (PACKET, "bool m14ReplayGX = false;"),
        (REGS, "void handle_bp(u32 value) noexcept {"),
        (REGS, "g_gxState.dirty |= reg.dirty;"),
    )
    missing = [needle for name, needle in prerequisites if needle not in original[name]]
    if missing:
        raise RuntimeError("draw tags prerequisites missing: " + ", ".join(missing))
    updated = {
        PACKET: patch_packet(original[PACKET]),
        RECORDING: patch_recording(original[RECORDING]),
        REGS: patch_regs(original[REGS]),
    }
    required = (
        (RECORDING, "class Tracker {"),
        (RECORDING, 'std::getenv("OPENSPM_M14_DRAW_TAGS")'),
        (RECORDING, "M14 draw tags: frames="),
        (RECORDING, STAMP_NEW),
        (REGS, "regs[0x7F] = {bp_m14_draw_tag"),
        (REGS, "regs[0x7C] = {bp_m14_draw_tag"),
        (PACKET, PACKET_FIELDS),
        (PACKET, FRAME_FIELDS),
        (RECORDING, "command.m14DrawView = index;"),
    )
    missing = [needle for name, needle in required if needle not in updated[name]]
    if missing:
        raise RuntimeError("draw tags migration incomplete: " + ", ".join(m[:40] for m in missing))
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
        print(f"error: Aurora draw tags adaptation failed: {exc}", file=sys.stderr)
        return 4
    print({
        "verified": "Aurora M14 draw tags verified",
        "needs-apply": "Aurora M14 draw tags not yet applied",
        "applied": "Aurora M14 draw tags applied",
        "already-applied": "Aurora M14 draw tags already applied",
    }[result])
    return 0 if result != "needs-apply" else 5


if __name__ == "__main__":
    raise SystemExit(main())
