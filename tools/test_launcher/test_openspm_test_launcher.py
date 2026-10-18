#!/usr/bin/env python3
"""Host checks for the GUI test launcher (no display needed)."""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import openspm_test_launcher as tl  # noqa: E402

ROOT = tl.ROOT


def cmd_text() -> str:
    return tl.RUN_CMD.read_bytes().decode("utf-8")


def test_every_flag_is_a_launcher_flag() -> None:
    text = cmd_text()
    handled = set(re.findall(r'"%%~A"=="([\w-]+)"', text))
    assert tl.FLAG_NAMES <= handled, tl.FLAG_NAMES - handled
    # Every launcher flag the cmd knows is offered (toolchains are the combobox).
    assert handled - tl.FLAG_NAMES == set(tl.TOOLCHAINS) - {"msvc"}, handled - tl.FLAG_NAMES


def test_launch_flags_reach_every_flag_loop() -> None:
    text = cmd_text().replace("\r\n", "\n")
    assert "\r\n" in cmd_text() and "\n" not in cmd_text().replace("\r\n", "")
    loops = re.findall(r"for %%A in \(([^)]*)\) do \(\n(.*?)\n\)", text, re.S)
    assert len(loops) == 3, len(loops)
    covered = set()
    for items, body in loops:
        assert "%OPENSPM_LAUNCH_FLAGS%" in items
        covered |= set(re.findall(r'"%%~A"=="([\w-]+)"', body))
    assert tl.FLAG_NAMES | {"clang", "clang-freestanding"} <= covered
    # The per-argument flags are read before the presenter variables are cleared
    # and the loop that sets them runs after (same order as the %~2..%~4 lines).
    assert text.index('if /i "%%~A"=="snapcapture"') < text.index('set "OPENSPM_M14_DRAW_TAGS="')
    assert text.index('set "OPENSPM_M14_FINISH_BEFORE_RETRACE=1"') < text.index('if /i "%%~A"=="oldorder"')
    assert text.index('set "OPENSPM_M14_INTERP="') < text.index('for %%A in ("%~2" "%~3" "%~4" %OPENSPM_LAUNCH_FLAGS%)')


def test_test_list_presets_are_valid() -> None:
    items = tl.parse_test_list(tl.TEST_LIST.read_text(encoding="utf-8"))
    assert [i.key for i in items] == [str(n) for n in range(1, len(items) + 1)]
    settings = {s.env for s in tl.parse_settings(tl.SETTINGS_HEADER.read_text(encoding="utf-8"))}
    fixed = tl.launcher_fixed_vars(cmd_text().replace("\r\n", "\n"))
    sources = "\n".join(p.read_text(encoding="utf-8", errors="replace")
                        for d in ("renderer", "backend", "launcher", "tools/native_bridge")
                        for p in (ROOT / d).rglob("*") if p.suffix in {".c", ".h", ".cpp", ".hpp", ".py"})
    for item in items:
        assert item.launches, f"test {item.key} has no <!-- launch: --> line"
        assert "<!--" not in item.text, item.key
        for launch in item.launches:
            assert set(launch.flags) <= tl.FLAG_NAMES, (item.key, launch.flags)
            assert launch.graphics in tl.GRAPHICS and launch.toolchain in tl.TOOLCHAINS
            for name in launch.env:
                assert name not in fixed, (item.key, name, "cleared by M14-VANILLA-RUN.cmd")
                assert name in settings or name in sources, (item.key, name)
        for args, report in item.analyze:
            assert report.endswith(".txt") and args
    flip = items[0]
    assert flip.launches[0].flags == ["snapcapture"]
    assert flip.analyze[0] == (["--bbox-wide", "500", "--limit", "40", "--passes"], "flip8.txt")
    assert "M14 capture-request" in flip.grep
    clang = next(i for i in items if any(l.toolchain == "clang" for l in i.launches))
    assert all(not l.flags for l in clang.launches)


def test_settings_and_fixed_vars() -> None:
    settings = tl.parse_settings(tl.SETTINGS_HEADER.read_text(encoding="utf-8"))
    envs = {s.env: s for s in settings}
    assert envs["OPENSPM_AUDIO"].values == "1|0" and envs["OPENSPM_TURBO_SPEED"].section == "Game"
    fixed = tl.launcher_fixed_vars(cmd_text().replace("\r\n", "\n"))
    assert "OPENSPM_M14_INTERP_VIEW" in fixed and "OPENSPM_M14_DRAW_TAGS" in fixed
    assert "OPENSPM_AUDIO" not in fixed and "OPENSPM_FRAME_MODE" not in fixed


def test_parsers_and_launch_env() -> None:
    launch = tl.parse_launch('label="x y" flags="interp clang" env=OPENSPM_A=1 env=OPENSPM_B=2')
    assert launch.label == "x y" and launch.flags == ["interp"] and launch.toolchain == "clang"
    assert launch.env == {"OPENSPM_A": "1", "OPENSPM_B": "2"}
    assert tl.parse_launch('flags="audio"').label == "audio"
    for bad in ("flags", "env=PATH=1", "what=1"):
        try:
            tl.parse_launch(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(bad)
    env = tl.launch_env({"PATH": "p", "OPENSPM_LAUNCH_FLAGS": "old"}, [], {"OPENSPM_X": "1"})
    assert env == {"PATH": "p", "OPENSPM_X": "1"}
    assert tl.launch_env({}, ["audio", "safe"], {})["OPENSPM_LAUNCH_FLAGS"] == "audio safe"
    assert tl.launch_args("AUTO", "msvc")[-1] == "AUTO"
    assert tl.launch_args("AUTO", "clang")[-2:] == ["AUTO", "clang"]
    items = tl.parse_test_list("intro\n## 1. A\n<!-- grep: x | y -->\ntext\n## 2. B\nmore\n")
    assert items[0].grep == ["x", "y"] and items[0].text == "text" and items[1].text == "more"


def test_default_analyzer_args() -> None:
    args, report = tl.default_analyzer_args(["flipcapture"])
    assert args == ["--size", "72x88", "--passes"] and report.startswith("flipcapture-") and report.endswith(".txt")
    args, report = tl.default_analyzer_args(["doorcapture", "audio"])
    assert "--bbox-wide" in args and report.startswith("doorcapture-")
    assert tl.parse_analyze(" ".join(args) + " > " + report) == (args, report)
    assert tl.default_analyzer_args([])[1].startswith("capture-")


def test_grep_lines(tmp: Path | None = None) -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        log = Path(d) / "launch.log"
        log.write_text("a M14 audio: 1\nb\nOpenSPM audio: 2\nM14 audio: 3\n", encoding="utf-8")
        assert tl.grep_lines(log, ["M14 audio:", "OpenSPM audio"]) == ["a M14 audio: 1", "OpenSPM audio: 2", "M14 audio: 3"]
        assert tl.grep_lines(log, ["M14 audio:"], limit=1) == ["a M14 audio: 1"]
        assert tl.grep_lines(Path(d) / "missing", ["x"]) == []


def test_entry_points() -> None:
    launcher = (ROOT / "OPENSPM-TEST-LAUNCHER.cmd").read_bytes().decode("utf-8")
    assert "\r\n" in launcher and "\n" not in launcher.replace("\r\n", "")
    assert r'tools\test_launcher\openspm_test_launcher.py' in launcher
    assert "/test-results/" in (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "OPENSPM-TEST-LAUNCHER.cmd" in tl.TEST_LIST.read_text(encoding="utf-8")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
