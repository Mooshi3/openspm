#!/usr/bin/env python3
"""Add a one-shot SDL display-refresh cadence diagnostic to pinned Aurora.

dev131 is observation-only. It reports the current display mode associated with
Aurora's actual SDL window and compares it with integer multiples of the
authoritative 60 Hz Wii simulation rate. No presentation or timing behavior is
changed.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
LOCK = HERE / "aurora.lock.json"
TARGET = pathlib.Path("lib/window.cpp")
SIGNATURE = "M14 display cadence current="

GLOBAL_OLD = """std::atomic_bool g_backgrounded = false;
#if defined(SDL_PLATFORM_ANDROID)
"""

GLOBAL_NEW = """std::atomic_bool g_backgrounded = false;

void m14_log_display_cadence() noexcept {
  if (g_window == nullptr) {
    return;
  }

  const SDL_DisplayID display = SDL_GetDisplayForWindow(g_window);
  if (display == 0) {
    Log.warn("M14 display cadence unavailable: SDL_GetDisplayForWindow failed: {}", SDL_GetError());
    return;
  }

  const SDL_DisplayMode* mode = SDL_GetCurrentDisplayMode(display);
  if (mode == nullptr || mode->refresh_rate <= 0.0f) {
    Log.warn("M14 display cadence unavailable: SDL_GetCurrentDisplayMode failed/unspecified: {}", SDL_GetError());
    return;
  }

  const float ratio60 = mode->refresh_rate / 60.0f;
  int nearestMultiple = static_cast<int>(ratio60 + 0.5f);
  if (nearestMultiple < 1) {
    nearestMultiple = 1;
  }
  float errorHz = mode->refresh_rate - 60.0f * static_cast<float>(nearestMultiple);
  if (errorHz < 0.0f) {
    errorHz = -errorHz;
  }

  Log.info(
      "M14 display cadence current={}x{} refresh_hz={:.3f} precise={}/{} ratio60={:.4f} nearest_multiple={} error_hz={:.3f} integer_multiple={}",
      mode->w, mode->h, mode->refresh_rate,
      mode->refresh_rate_numerator, mode->refresh_rate_denominator,
      ratio60, nearestMultiple, errorHz, errorHz <= 0.25f);
}

#if defined(SDL_PLATFORM_ANDROID)
"""

CALL_OLD = """  g_window = SDL_CreateWindowWithProperties(props);
  if (g_window == nullptr) {
    Log.error("Failed to create window: {}", SDL_GetError());
    return false;
  }
  SDL_SetWindowMinimumSize(g_window, 640, 480);
"""

CALL_NEW = """  g_window = SDL_CreateWindowWithProperties(props);
  if (g_window == nullptr) {
    Log.error("Failed to create window: {}", SDL_GetError());
    return false;
  }
  m14_log_display_cadence();
  SDL_SetWindowMinimumSize(g_window, 640, 480);
"""


def run(*args: str, cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args, cwd=cwd, check=True, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )


def replace_once_or_present(
    text: str, old: str, new: str, label: str
) -> tuple[str, bool]:
    if new in text:
        return text, False
    if text.count(old) != 1:
        raise RuntimeError(f"{label}: expected exact old/new anchor once")
    return text.replace(old, new, 1), True


def transform(source: pathlib.Path, check_only: bool) -> str:
    path = source / TARGET
    text = path.read_text(encoding="utf-8")
    original = text

    text, _ = replace_once_or_present(
        text, GLOBAL_OLD, GLOBAL_NEW, "display-cadence helper"
    )
    text, _ = replace_once_or_present(
        text, CALL_OLD, CALL_NEW, "display-cadence call"
    )

    required = (
        SIGNATURE,
        "SDL_GetDisplayForWindow(g_window)",
        "SDL_GetCurrentDisplayMode(display)",
        "ratio60",
        "integer_multiple={}",
        "m14_log_display_cadence();",
    )
    missing = [item for item in required if item not in text]
    if missing:
        raise RuntimeError(
            "display-cadence transform incomplete: " + ", ".join(missing)
        )

    changed = text != original
    if not check_only and changed:
        path.write_text(text, encoding="utf-8", newline="\n")
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
        print(
            f"error: Aurora revision mismatch: expected {expected}, got {actual}",
            file=sys.stderr,
        )
        return 3

    try:
        result = transform(source, args.check_only)
    except (OSError, RuntimeError) as exc:
        print(
            f"error: Aurora display-cadence adaptation failed: {exc}",
            file=sys.stderr,
        )
        return 4

    print({
        "verified": "Aurora display-cadence diagnostic verified",
        "already-applied": "Aurora display-cadence diagnostic already applied",
        "applied": "Aurora display-cadence diagnostic applied",
    }[result])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
