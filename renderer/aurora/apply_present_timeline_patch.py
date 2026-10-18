#!/usr/bin/env python3
"""OpenSPM Stage A present timeline: a vblank-driven async presenter.

Opt-in (OPENSPM_M14_ASYNC_PRESENTER=1 + OPENSPM_M14_PRESENT_TIMELINE=1). With
the variables unset nothing new runs: the dev139 presenter keeps its two-slot
ring and integer-Hz cadence thread, and the default Vanilla 60 path (no async
presenter) is untouched.

What this adds to pinned Aurora's lib/aurora.cpp:
- present_timeline.hpp (pure pacing model, unit/scheduler tested by
  test_display_pacing.py) and present_timeline_presenter.inl (glue), embedded
  between "// M14 present timeline BEGIN/END" markers and refreshed from the
  repository on every configure (own-revision migration);
- forward declarations between their own markers before the dev139 state;
- a four-slot presenter ring (used only when the timeline is on);
- the frame-completion hook in m14_publish_presenter_slot();
- a dispatch at the top of m14_presenter_main(). The dev139 script
  canonicalizes that body on every configure, so this script re-adds it.

Runs after apply_interpolation_present_patch.py (last in the CMake chain).
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
TARGET = pathlib.Path("lib/aurora.cpp")

BEGIN = "// M14 present timeline BEGIN\n"
END = "// M14 present timeline END\n"
DECL_BEGIN = "// M14 present timeline declarations BEGIN\n"
DECL_END = "// M14 present timeline declarations END\n"
DECLARATIONS = (
    DECL_BEGIN
    + "bool m14_present_timeline_enabled() noexcept;\n"
    + "uint32_t m14_presenter_ring_slots() noexcept;\n"
    + "void m14_timeline_note_frame_locked(uint64_t generation, uint32_t slot);\n"
    + "void m14_timeline_presenter_main();\n"
    + DECL_END
)

INCLUDES = (
    "#include <algorithm>\n",
    "#include <array>\n",
    "#include <chrono>\n",
    "#include <cmath>\n",
    "#include <cstddef>\n",
    "#include <cstdint>\n",
    "#include <cstdlib>\n",
    "#include <string_view>\n",
    "#include <thread>\n",
)

RING_OLD = "std::array<M14PresenterImage, 2> g_m14PresenterImages;\n"
RING_NEW = "std::array<M14PresenterImage, 4> g_m14PresenterImages;\n"
SLOT_OLD = "  const uint32_t slot = static_cast<uint32_t>((g_m14PresenterGeneration + 1u) & 1u);\n"
SLOT_NEW = ("  const uint32_t slot =\n"
            "      static_cast<uint32_t>((g_m14PresenterGeneration + 1u) % m14_presenter_ring_slots());\n")
PUBLISH_OLD = "    ++g_m14PresenterGeneration;\n"
PUBLISH_NEW = PUBLISH_OLD + "    m14_timeline_note_frame_locked(g_m14PresenterGeneration, slot);\n"
MAIN_OLD = "void m14_presenter_main() {\n"
MAIN_NEW = MAIN_OLD + (
    "  if (m14_present_timeline_enabled()) {  // OpenSPM Stage A vblank-driven presenter\n"
    "    m14_timeline_presenter_main();\n"
    "    return;\n"
    "  }\n"
)
INIT_ANCHOR = "AuroraInfo initialize(int argc, char* argv[], const AuroraConfig& config) noexcept {"

# Strings other chain scripts anchor on; the embedded code must never contain
# them or those scripts would see a duplicate (or a false "already applied").
FOREIGN_ANCHORS = (
    "void m14_presenter_main()",
    "(void)m14_present_retained_image(image);",
    "if (g_m14PresenterStop || !g_m14PresenterHaveImage",
    "m14_stop_async_presenter();",
    "void m14_publish_presenter_slot(",
    "void m14_start_async_presenter_locked(",
    "m14_encode_presenter_snapshot(encoder",
    "struct M14PresenterImage {",
    "M14 visible interpolation helpers",
    INIT_ANCHOR,
)


def run(*args: str, cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def embedded_region() -> str:
    header = (HERE / "present_timeline.hpp").read_text(encoding="utf-8")
    glue = (HERE / "present_timeline_presenter.inl").read_text(encoding="utf-8")
    # Includes go to the top of aurora.cpp; the model lands inside Aurora's
    # namespace, where a nested #include would be ill-formed.
    lines = [line for line in header.splitlines(keepends=True)
             if not line.startswith("#include") and line.strip() != "#pragma once"]
    body = "".join(lines) + "\n" + glue
    for anchor in FOREIGN_ANCHORS:
        if anchor in body:
            raise RuntimeError(f"present timeline code contains a foreign chain anchor: {anchor}")
    if "#include" in body:
        raise RuntimeError("present timeline code must not contain #include lines")
    return BEGIN + "#ifdef AURORA_ENABLE_GX\n" + body + "#endif\n" + END


def ensure_include(text: str, include: str) -> str:
    if include in text:
        return text
    anchor = "#include <magic_enum.hpp>\n"
    if text.count(anchor) != 1:
        raise RuntimeError(f"include anchor missing for {include.strip()}")
    return text.replace(anchor, include + anchor, 1)


def replace_once(text: str, old: str, new: str, what: str) -> str:
    if new in text:
        if text.count(new) != 1:
            raise RuntimeError(f"present timeline {what} is not unique")
        return text
    if text.count(old) != 1:
        raise RuntimeError(f"present timeline {what} anchor expected once, found {text.count(old)}")
    return text.replace(old, new, 1)


def replace_marked(text: str, begin: str, end: str, block: str, anchor: str, what: str) -> str:
    if begin in text:
        if text.count(begin) != 1 or text.count(end) != 1:
            raise RuntimeError(f"present timeline {what} markers are not unique")
        start = text.index(begin)
        stop = text.index(end, start) + len(end)
        return text[:start] + block + text[stop:]
    if text.count(anchor) != 1:
        raise RuntimeError(f"present timeline {what} anchor expected once, found {text.count(anchor)}")
    at = text.index(anchor)
    return text[:at] + block + "\n" + text[at:]


def transform(source: pathlib.Path, check_only: bool) -> str:
    path = source / TARGET
    text = path.read_text(encoding="utf-8")
    original = text

    prerequisites = (
        "struct M14PresenterImage {",
        "bool m14_async_presenter_enabled() noexcept {",
        "bool m14_present_retained_image(const M14PresenterImage& image) {",
        MAIN_OLD,
        "void m14_publish_presenter_slot(uint32_t slot) {\n",
        "gx::bbox::readback_pending()",
        "g_m14PresenterWorkQueued",
        INIT_ANCHOR,
    )
    missing = [item for item in prerequisites if item not in text]
    if missing:
        raise RuntimeError("present timeline prerequisites missing: " + ", ".join(missing))

    for include in INCLUDES:
        text = ensure_include(text, include)
    text = replace_marked(text, DECL_BEGIN, DECL_END, DECLARATIONS,
                          "struct M14PresenterImage {", "declarations")
    text = replace_once(text, RING_OLD, RING_NEW, "presenter ring")
    text = replace_once(text, SLOT_OLD, SLOT_NEW, "presenter slot choice")
    publish_at = text.index("void m14_publish_presenter_slot(uint32_t slot) {\n")
    publish_end = text.index("\n}\n", publish_at)
    publish = text[publish_at:publish_end]
    publish = replace_once(publish, PUBLISH_OLD, PUBLISH_NEW, "frame-completion hook")
    text = text[:publish_at] + publish + text[publish_end:]
    text = replace_once(text, MAIN_OLD, MAIN_NEW, "presenter dispatch")
    text = replace_marked(text, BEGIN, END, embedded_region(), INIT_ANCHOR, "presenter code")

    # The embedded code must precede Aurora's initialize and follow the dev139 state.
    if not (text.index("struct M14PresenterImage {") < text.index(BEGIN) < text.index(INIT_ANCHOR)):
        raise RuntimeError("present timeline code is out of place")
    required = (
        "void m14_timeline_presenter_main() {",
        "class PresentTimeline {",
        'std::getenv("OPENSPM_M14_PRESENT_TIMELINE")',
        "M14 present timeline: mode={}",
        "g_m14Timeline.timeline.on_present(returnedNs);",
    )
    missing = [item for item in required if item not in text]
    if missing:
        raise RuntimeError("present timeline migration incomplete: " + ", ".join(missing))
    for unique in (MAIN_NEW, PUBLISH_NEW, SLOT_NEW, RING_NEW, BEGIN, DECL_BEGIN):
        if text.count(unique) != 1:
            raise RuntimeError(f"present timeline edit is not unique: {unique.strip()[:60]}")

    changed = text != original
    if check_only:
        return "verified" if not changed else "needs-apply"
    if changed:
        path.write_text(text, encoding="utf-8", newline="\n")
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
        print(f"error: Aurora present timeline adaptation failed: {exc}", file=sys.stderr)
        return 4
    print({
        "verified": "Aurora M14 present timeline verified",
        "needs-apply": "Aurora M14 present timeline not yet applied",
        "applied": "Aurora M14 present timeline applied",
        "already-applied": "Aurora M14 present timeline already applied",
    }[result])
    return 0 if result != "needs-apply" else 5


if __name__ == "__main__":
    raise SystemExit(main())
