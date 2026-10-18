#!/usr/bin/env python3
"""OpenSPM fast-forward ("turbo") presentation hooks for pinned Aurora.

While turbo is engaged (docs/PLAYING.md "Fast-forward") the game produces N
times as many frames as the display shows. With FIFO vsync every present would
wait for a vblank and throttle the game back to the display rate. The renderer
(openspm_renderer_aurora_v4.cpp, m14_turbo_before_end_frame) therefore decides
per game frame whether it is shown (at most one image per display refresh) and
tells Aurora through one C function:

  void aurora_m14_set_turbo(uint32_t flags)
    AURORA_M14_TURBO_ACTIVE        turbo is engaged
    AURORA_M14_TURBO_SKIP_PRESENT  the next aurora_end_frame() is not shown
                                   (one-shot: cleared by that end_frame)

What this adds to lib/aurora.cpp:
- end_frame: the skip decision is queued in submission order (a frame may be
  submitted on the render worker after the game already decided the next one)
  and a skipped frame submits its GPU work exactly like the "window not
  presentable" path, without acquiring or presenting the swapchain;
- async presenter (dev139) and the present timeline (Stage A/C): while turbo
  is active they show the newest published image, never interpolated; the
  timeline takes no N x frame samples and re-anchors like a first publish at
  the first normal frame afterwards.

Nothing changes while the flags are 0: the renderer never sets them unless the
backend engaged turbo. Every edit only inserts lines next to an anchor, so the
earlier chain scripts (which canonicalize or refresh their own regions on every
configure) still find their text; this script runs last and re-inserts whatever
a refresh removed. Exact/idempotent; fails closed on unexpected source drift.
"""
from __future__ import annotations

import argparse
from pathlib import Path

SIGNATURE = "OpenSPM turbo (fast-forward)"

AURORA_CPP = Path("lib/aurora.cpp")
AURORA_H = Path("include/aurora/aurora.h")

STATE_ANCHOR = "bool g_m14PresenterWorkQueued = false;\n"
STATE = """// """ + SIGNATURE + """: aurora_m14_set_turbo() state. The game thread
// queues one present decision per end_frame; the render worker takes them in
// the same order (frames can be in flight on the worker).
std::atomic<uint32_t> g_m14TurboFlags{0};
std::array<std::atomic<uint8_t>, 64> g_m14TurboSkipRing{};
std::atomic<uint64_t> g_m14TurboSkipPushed{0};
uint64_t g_m14TurboSkipPopped = 0;  // render worker only

bool m14_turbo_active() noexcept { return (g_m14TurboFlags.load(std::memory_order_relaxed) & 1u) != 0u; }

void m14_turbo_push_frame() noexcept {  // SKIP_PRESENT is one-shot: only this frame
  const uint64_t n = g_m14TurboSkipPushed.load(std::memory_order_relaxed);
  const uint32_t flags = g_m14TurboFlags.fetch_and(~2u, std::memory_order_relaxed);
  g_m14TurboSkipRing[n & 63u].store((flags & 3u) == 3u ? 1u : 0u, std::memory_order_relaxed);
  g_m14TurboSkipPushed.store(n + 1u, std::memory_order_release);
}

bool m14_turbo_pop_skip() noexcept {
  if (g_m14TurboSkipPopped >= g_m14TurboSkipPushed.load(std::memory_order_acquire)) return false;
  return g_m14TurboSkipRing[g_m14TurboSkipPopped++ & 63u].load(std::memory_order_relaxed) != 0u;
}
"""

PUSH_ANCHOR = "  gfx::end_frame([rmlBindGroup = std::move(rmlBindGroup), rmlOverlay, viewport,\n"
PUSH = "  m14_turbo_push_frame();  // " + SIGNATURE + ": this frame's present decision\n"

SKIP_ANCHOR = "    wgpu::Texture currentTexture;\n    wgpu::TextureView currentView;\n"
SKIP = """    if (m14_turbo_pop_skip()) {
      // """ + SIGNATURE + """: this game frame is not shown (at most one image
      // per display refresh). Submit its work like the "window not
      // presentable" path, without acquiring or presenting the swapchain, so
      // FIFO vsync never makes the game wait.
      webgpu::gpu_prof::frame_end(encoder);
      const wgpu::CommandBufferDescriptor m14TurboDescriptor{.label = "OpenSPM turbo frame (not presented)"};
      const auto m14TurboBuffer = encoder.Finish(&m14TurboDescriptor);
      g_queue.Submit(1, &m14TurboBuffer);
      webgpu::gpu_prof::after_submit();
      for (auto& callback : afterSubmitCallbacks) {
        if (callback) callback();
      }
      gfx::after_submit();
      return;
    }

"""

DEV139_ANCHOR = "        image = m14_choose_interpolation_image(std::move(image));\n"
DEV139 = "        if (!m14_turbo_active())  // " + SIGNATURE + ": the newest image as is\n"

FIELD_ANCHOR = "  std::array<uint64_t, 4> slotFrame{};  // publish generation held by each slot\n"
FIELD = "  bool m14TurboReanchor = false;  // " + SIGNATURE + " ran: re-anchor at the next normal frame\n"

REANCHOR_ANCHOR = "  if (!state.configured || generation == 1u) {  // first publish (again after a stop)\n"
REANCHOR = """  if (state.m14TurboReanchor && !m14_turbo_active()) {  // """ + SIGNATURE + """ ended: start over
    state.configured = false;
    state.m14TurboReanchor = false;
  }
"""

SAMPLE_ANCHOR = "  state.timeline.on_frame(generation, m14_timeline_now_ns());\n"
SAMPLE = """  if (m14_turbo_active()) {  // """ + SIGNATURE + """: N x frames are no timeline samples
    state.m14TurboReanchor = true;
    ++state.framesIn;
    return;
  }
"""

TURBO_NOW_ANCHOR = "      const bool interp = m14_frame_interp_enabled();\n"
TURBO_NOW = "      const bool m14TurboNow = m14_turbo_active();  // " + SIGNATURE + "\n"

NEWEST_ANCHOR = "        if (!found) ++g_m14Timeline.evicted;\n"
NEWEST = """        if (m14TurboNow) {  // turbo: the newest publish
          slot = g_m14PresenterLatestSlot;
          found = true;
        }
"""

NO_INTERP_ANCHOR = "      if (interp && haveB) image = m14_frame_interp_choose(frameA, frameB, alpha, imageA, imageB);\n"
NO_INTERP = "      if (m14TurboNow) haveB = false;  // turbo: never interpolated\n"

API_ANCHOR = "void aurora_end_frame() { aurora::end_frame(); }\n"
API = """void aurora_m14_set_turbo(uint32_t flags) {  // """ + SIGNATURE + """
#ifdef AURORA_ENABLE_GX
  aurora::g_m14TurboFlags.store(flags, std::memory_order_relaxed);
#else
  (void)flags;
#endif
}
"""

HEADER_ANCHOR = "void aurora_end_frame();\n"
HEADER = """/**
 * """ + SIGNATURE + """ (OpenSPM): AURORA_M14_TURBO_ACTIVE while turbo is engaged
 * (the async presenter / present timeline show the newest image, never
 * interpolated); with AURORA_M14_TURBO_SKIP_PRESENT the next aurora_end_frame()
 * (only that one) submits its work without presenting. 0 = normal presentation.
 */
#define AURORA_M14_TURBO_ACTIVE 1u
#define AURORA_M14_TURBO_SKIP_PRESENT 2u
void aurora_m14_set_turbo(uint32_t flags);
"""

# (anchor, inserted text, after?)  after=True inserts behind the anchor.
CPP_EDITS = [
    (STATE_ANCHOR, STATE, True, "turbo state"),
    (PUSH_ANCHOR, PUSH, False, "end_frame push"),
    (SKIP_ANCHOR, SKIP, False, "end_frame skip"),
    (DEV139_ANCHOR, DEV139, False, "dev139 presenter"),
    (FIELD_ANCHOR, FIELD, True, "timeline state field"),
    (REANCHOR_ANCHOR, REANCHOR, False, "timeline re-anchor"),
    (SAMPLE_ANCHOR, SAMPLE, False, "timeline sample"),
    (TURBO_NOW_ANCHOR, TURBO_NOW, True, "timeline worker flag"),
    (NEWEST_ANCHOR, NEWEST, False, "timeline worker newest"),
    (NO_INTERP_ANCHOR, NO_INTERP, False, "timeline worker no interpolation"),
    (API_ANCHOR, API, True, "C API"),
]
H_EDITS = [(HEADER_ANCHOR, HEADER, True, "aurora.h declaration")]


def insert(text: str, anchor: str, block: str, after: bool, label: str) -> str:
    combined = anchor + block if after else block + anchor
    if combined in text:
        if text.count(block) != 1:
            raise RuntimeError(f"{label}: inserted block present {text.count(block)} times")
        return text
    if block in text:
        raise RuntimeError(f"{label}: block present but detached from its anchor")
    if text.count(anchor) != 1:
        raise RuntimeError(f"{label}: expected one pinned anchor, found {text.count(anchor)}")
    return text.replace(anchor, combined, 1)


def transform(root: Path, check_only: bool = False) -> str:
    files = {AURORA_CPP: CPP_EDITS, AURORA_H: H_EDITS}
    texts = {path: (root / path).read_text(encoding="utf-8") for path in files}
    updated = {}
    for path, edits in files.items():
        text = texts[path]
        for anchor, block, after, label in edits:
            text = insert(text, anchor, block, after, label)
        updated[path] = text
    if all(updated[p] == texts[p] for p in files):
        return "already active"
    if check_only:
        return "pending"
    for path in files:
        if updated[path] != texts[path]:
            (root / path).write_text(updated[path], encoding="utf-8", newline="\n")
    return "applied"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    try:
        result = transform(args.source.resolve(), args.check_only)
    except (OSError, RuntimeError) as exc:
        print(f"error: Aurora turbo presentation adaptation failed: {exc}")
        return 4
    print("Aurora turbo presentation hooks " + result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
