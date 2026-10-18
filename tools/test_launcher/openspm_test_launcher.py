#!/usr/bin/env python3
"""OpenSPM test launcher: a small GUI around M14-VANILLA-RUN.cmd.

Start it with OPENSPM-TEST-LAUNCHER.cmd in the repository root. It does not
replace the launch scripts; it only picks their flags and settings for you:

- the test list comes from docs/TEST-LIST.md. Every "## N. Title" section is
  one test, and comment lines in it add one-click launches and log filters:
      <!-- launch: flags="snapcapture" -->
      <!-- launch: label="A/B old origin" env=OPENSPM_M14_VIEWPORT_ORIGIN=0 -->
      <!-- analyze: --bbox-wide 500 --limit 40 > flip7.txt -->
      <!-- grep: M14 capture-request|M14 snapshot -->
  so a future test needs only a new section, no change here;
- the flag checkboxes are the FLAGS table below (every name must be a flag
  M14-VANILLA-RUN.cmd understands; a host test checks this);
- the setting overrides are read from launcher/native_settings.h: each
  OpenSPM.ini key's environment variable, which wins over the file for one
  launch without editing OpenSPM.ini.

The launch runs M14-VANILLA-RUN.cmd in its own console window with the flags
in OPENSPM_LAUNCH_FLAGS (no limit on how many). When that window closes, the
run's launch.log is copied to test-results/logs/ so the next run does not
overwrite it. "Save result" appends the test, launch, your notes and the
filtered log lines to test-results/RESULTS.md: that one file is the report.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUN_CMD = ROOT / "M14-VANILLA-RUN.cmd"
TEST_LIST = ROOT / "docs" / "TEST-LIST.md"
SETTINGS_HEADER = ROOT / "launcher" / "native_settings.h"
LAUNCH_LOG = ROOT / ".openspm" / "m14" / "home-test" / "launch.log"
RESULTS_DIR = ROOT / "test-results"
STATE_FILE = ROOT / ".openspm" / "test_launcher.json"
ANALYZER = ROOT / "tools" / "diag" / "analyze_gx_capture.py"
# The game runs from the staged candidate folder; texturepacks/ and mods/ sit
# next to OpenSPMNative.exe there (OpenSPM.ini [TexturePacks] folder overrides).
CANDIDATE_DIR = ROOT / ".openspm" / "m14" / "windows-candidate"

GRAPHICS = ["AUTO", "D3D12", "VULKAN", "D3D11"]
TOOLCHAINS = ["msvc", "clang", "clang-freestanding"]

# (flag, group, description). Flags M14-VANILLA-RUN.cmd reads from its
# arguments / OPENSPM_LAUNCH_FLAGS. Add new launcher flags here.
FLAGS: list[tuple[str, str, str]] = [
    ("safe", "General", "turn off the newer default-on extras (menu pause, HOME menu, stick pointer, rumble, turbo, cheats, texture packs)"),
    ("widescreen", "General", "native 16:9 (SYSCONF IPL.AR = 16:9)"),
    ("audio", "General", "game audio (experimental AX HLE)"),
    ("fullscreen", "Window", "exclusive fullscreen at a refresh vanilla 60 locks to"),
    ("borderless", "Window", "borderless fullscreen at the desktop refresh"),
    ("nosync", "Pacing", "fixed 60.000 Hz clock instead of the display-locked clock"),
    ("oldorder", "Pacing", "legacy retrace ordering (the 33 ms dropped-frame bug)"),
    ("timeline", "Presentation", "Stage A present timeline, smooth mode"),
    ("timelinevanilla", "Presentation", "Stage A present timeline, vanilla mode"),
    ("drawtags", "Presentation", "Stage B draw tags (diagnostic, no visual change)"),
    ("interp", "Presentation", "Stage C frame interpolation (timeline + draw tags)"),
    ("interpdirect", "Presentation", "interp without the game's camera views"),
    ("notags", "Effects", "disable EFB copy tags (A/B)"),
    ("nopixelexact", "Effects", "legacy EFB copy source rectangle (A/B)"),
    ("bboxmemo", "Effects", "reuse bbox results for identical draws (experimental)"),
    ("bboxfast", "Effects", "answer provably empty bbox reads on the CPU"),
    ("bboxfastverify", "Effects", "bboxfast proofs, but still ask the GPU and log mismatches"),
    ("efbram", "Diagnostics", "write EFB copies back to guest RAM (slow)"),
    ("copytrace", "Diagnostics", "log EFB texture copies/binds"),
    ("snapdump", "Diagnostics", "save each door/flip snapshot image to screenshots"),
    ("snapcapture", "Diagnostics", "GX capture from the first full-screen snapshot (flip)"),
    ("flipcapture", "Diagnostics", "GX capture from the first flip press"),
    ("doorcapture", "Diagnostics", "GX capture from the first bbox burst (door/flip start)"),
]
FLAG_NAMES = {name for name, _, _ in FLAGS}


@dataclass
class Launch:
    label: str
    flags: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    graphics: str = "AUTO"
    toolchain: str = "msvc"


@dataclass
class TestItem:
    key: str
    title: str
    text: str
    launches: list[Launch] = field(default_factory=list)
    analyze: list[tuple[list[str], str]] = field(default_factory=list)
    grep: list[str] = field(default_factory=list)


@dataclass
class Setting:
    section: str
    key: str
    env: str
    values: str  # "a|b|c", "#pct", "#hz" or "*"


COMMENT = re.compile(r"^\s*<!--\s*(launch|analyze|grep):(.*?)-->\s*$")


def parse_launch(spec: str) -> Launch:
    launch = Launch(label="")
    for token in shlex.split(spec):
        key, sep, value = token.partition("=")
        if not sep:
            raise ValueError(f"launch option without '=': {token!r}")
        if key == "label":
            launch.label = value
        elif key == "flags":
            for flag in value.split():
                if flag in TOOLCHAINS:
                    launch.toolchain = flag
                else:
                    launch.flags.append(flag)
        elif key == "env":
            name, sep2, env_value = value.partition("=")
            if not sep2 or not name.startswith("OPENSPM_"):
                raise ValueError(f"env must be OPENSPM_NAME=value: {value!r}")
            launch.env[name] = env_value
        elif key == "graphics":
            launch.graphics = value
        else:
            raise ValueError(f"unknown launch option {key!r}")
    if not launch.label:
        parts = launch.flags + ([launch.toolchain] if launch.toolchain != "msvc" else [])
        parts += [f"{k}={v}" for k, v in launch.env.items()]
        launch.label = " ".join(parts) or "normal launch"
    return launch


def parse_analyze(spec: str) -> tuple[list[str], str]:
    args, sep, report = spec.partition(">")
    report = report.strip()
    if not sep or not report or "/" in report or "\\" in report:
        raise ValueError(f"analyze needs '<args> > report.txt': {spec!r}")
    return shlex.split(args), report


def parse_test_list(text: str) -> list[TestItem]:
    items: list[TestItem] = []
    current: TestItem | None = None
    body: list[str] = []

    def finish() -> None:
        if current is not None:
            current.text = "\n".join(body).strip("\n")
            items.append(current)

    for line in text.splitlines():
        heading = re.match(r"^## (\d+)\. (.+)$", line)
        if heading:
            finish()
            current = TestItem(key=heading.group(1), title=heading.group(2).strip(), text="")
            body = []
            continue
        if current is None:
            continue
        comment = COMMENT.match(line)
        if comment:
            kind, spec = comment.group(1), comment.group(2).strip()
            if kind == "launch":
                current.launches.append(parse_launch(spec))
            elif kind == "analyze":
                current.analyze.append(parse_analyze(spec))
            else:
                current.grep.extend(p.strip() for p in spec.split("|") if p.strip())
            continue
        body.append(line)
    finish()
    return items


def parse_settings(header_text: str) -> list[Setting]:
    pattern = re.compile(r'\{"(\w+)", "(\w+)", "(OPENSPM_\w+)", "([^"]*)"\}')
    return [Setting(*m.groups()) for m in pattern.finditer(header_text)]


def launcher_fixed_vars(cmd_text: str) -> set[str]:
    """Variables M14-VANILLA-RUN.cmd sets or clears on every launch (an
    override in the environment would be lost, so the GUI hides them)."""
    fixed = set()
    for line in cmd_text.splitlines():
        m = re.match(r'^set "(OPENSPM_\w+)=', line)  # top level only, not inside if blocks
        if m:
            fixed.add(m.group(1))
    return fixed


def launch_env(base: dict[str, str], flags: list[str], env: dict[str, str]) -> dict[str, str]:
    result = dict(base)
    result.pop("OPENSPM_LAUNCH_FLAGS", None)
    if flags:
        result["OPENSPM_LAUNCH_FLAGS"] = " ".join(flags)
    result.update(env)
    return result


def launch_args(graphics: str, toolchain: str) -> list[str]:
    args = ["cmd.exe", "/d", "/c", str(RUN_CMD), graphics]
    if toolchain != "msvc":
        args.append(toolchain)
    return args


def grep_lines(path: Path, patterns: list[str], limit: int = 400) -> list[str]:
    if not patterns or not path.exists():
        return []
    out: list[str] = []
    counts: dict[str, int] = {}
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            for pattern in patterns:
                if pattern in line:
                    # Keep the first lines of each pattern: first-time lines and
                    # a few per-second lines are what the tests ask for.
                    n = counts.get(pattern, 0)
                    if n < limit // max(1, len(patterns)):
                        out.append(line.rstrip("\n"))
                    counts[pattern] = n + 1
                    break
    return out


def default_analyzer_args(flags: list[str]) -> tuple[list[str], str]:
    """Analyzer arguments that fit the capture flag of the last launch."""
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    if "flipcapture" in flags:
        return ["--size", "72x88", "--passes"], f"flipcapture-{stamp}.txt"
    if "doorcapture" in flags:
        return ["--bbox-wide", "500", "--limit", "40", "--passes"], f"doorcapture-{stamp}.txt"
    return ["--bbox-wide", "500", "--limit", "40", "--passes"], f"capture-{stamp}.txt"


def open_path(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(str(path))  # noqa: S606 - opening a local file for the user
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError:
        pass


def main() -> int:
    import tkinter as tk
    from tkinter import filedialog, messagebox, simpledialog, ttk
    from tkinter.scrolledtext import ScrolledText

    tests = parse_test_list(TEST_LIST.read_text(encoding="utf-8")) if TEST_LIST.exists() else []
    free = TestItem(key="0", title="Free launch (no test)", text="Pick flags and settings below, then Launch.",
                    launches=[Launch(label="normal launch")])
    tests.insert(0, free)
    fixed = launcher_fixed_vars(RUN_CMD.read_text(encoding="utf-8", errors="replace"))
    settings = [s for s in parse_settings(SETTINGS_HEADER.read_text(encoding="utf-8")) if s.env not in fixed] \
        if SETTINGS_HEADER.exists() else []
    state = load_state()

    root = tk.Tk()
    root.title("OpenSPM test launcher")
    root.geometry("1200x860")

    status = tk.StringVar(value=f"Repository: {ROOT}")
    graphics = tk.StringVar(value=state.get("graphics", "AUTO"))
    toolchain = tk.StringVar(value=state.get("toolchain", "msvc"))
    flag_vars = {name: tk.BooleanVar(value=name in state.get("flags", [])) for name, _, _ in FLAGS}
    setting_vars = {s.env: tk.StringVar(value=state.get("settings", {}).get(s.env, "")) for s in settings}
    running: dict[str, object] = {"proc": None, "test": None, "launch": None}

    paned = ttk.PanedWindow(root, orient="horizontal")
    paned.pack(fill="both", expand=True)

    # Left: tests.
    left = ttk.Frame(paned, padding=4)
    paned.add(left, weight=1)
    ttk.Label(left, text="Tests (docs/TEST-LIST.md)").pack(anchor="w")
    test_list = tk.Listbox(left, exportselection=False, width=40)
    for item in tests:
        test_list.insert("end", item.title if item.key == "0" else f"{item.key}. {item.title}")
    test_list.pack(fill="both", expand=True)

    # Right: notebook.
    right = ttk.Frame(paned, padding=4)
    paned.add(right, weight=3)
    notebook = ttk.Notebook(right)
    notebook.pack(fill="both", expand=True)

    # Tab 1: test.
    tab_test = ttk.Frame(notebook, padding=4)
    notebook.add(tab_test, text="Test")
    launch_bar = ttk.LabelFrame(tab_test, text="Launch (applies the options and starts the game)", padding=4)
    launch_bar.pack(fill="x")
    info = ScrolledText(tab_test, wrap="word", height=18)
    info.pack(fill="both", expand=True, pady=4)
    tools_bar = ttk.Frame(tab_test)
    tools_bar.pack(fill="x")
    notes_frame = ttk.LabelFrame(tab_test, text="Your notes for this test (saved with Save result)", padding=4)
    notes_frame.pack(fill="x")
    notes = tk.Text(notes_frame, height=5, wrap="word")
    notes.pack(fill="x")
    result_bar = ttk.Frame(tab_test)
    result_bar.pack(fill="x", pady=2)

    # Tab 2: options.
    tab_opts = ttk.Frame(notebook, padding=4)
    notebook.add(tab_opts, text="Options")
    top = ttk.Frame(tab_opts)
    top.pack(fill="x")
    ttk.Label(top, text="Graphics").pack(side="left")
    ttk.Combobox(top, textvariable=graphics, values=GRAPHICS, width=10, state="readonly").pack(side="left", padx=4)
    ttk.Label(top, text="Native toolchain").pack(side="left", padx=(12, 0))
    ttk.Combobox(top, textvariable=toolchain, values=TOOLCHAINS, width=18, state="readonly").pack(side="left", padx=4)
    hint = tk.StringVar(value="Hover over a flag for what it does.")
    ttk.Label(tab_opts, textvariable=hint, foreground="#555").pack(fill="x", pady=2)
    flags_frame = ttk.Frame(tab_opts)
    flags_frame.pack(fill="x")
    groups: dict[str, ttk.LabelFrame] = {}
    for name, group, desc in FLAGS:
        if group not in groups:
            groups[group] = ttk.LabelFrame(flags_frame, text=group, padding=4)
            groups[group].pack(side="left", fill="y", padx=2, anchor="n")
        box = ttk.Checkbutton(groups[group], text=name, variable=flag_vars[name])
        box.pack(anchor="w")
        box.bind("<Enter>", lambda _e, n=name, d=desc: hint.set(f"{n}: {d}"))
    env_frame = ttk.LabelFrame(tab_opts, text="Extra environment, one OPENSPM_NAME=value per line (e.g. OPENSPM_M14_VIEWPORT_ORIGIN=0)", padding=4)
    env_frame.pack(fill="x", pady=4)
    extra_env = tk.Text(env_frame, height=3)
    extra_env.insert("1.0", state.get("extra_env", ""))
    extra_env.pack(fill="x")
    set_frame = ttk.LabelFrame(tab_opts, text="OpenSPM.ini overrides for this launch (empty = use OpenSPM.ini)", padding=4)
    set_frame.pack(fill="both", expand=True)
    set_canvas = tk.Canvas(set_frame, highlightthickness=0)
    set_scroll = ttk.Scrollbar(set_frame, orient="vertical", command=set_canvas.yview)
    set_inner = ttk.Frame(set_canvas)
    set_inner.bind("<Configure>", lambda _e: set_canvas.configure(scrollregion=set_canvas.bbox("all")))
    set_canvas.create_window((0, 0), window=set_inner, anchor="nw")
    set_canvas.configure(yscrollcommand=set_scroll.set)
    set_canvas.pack(side="left", fill="both", expand=True)
    set_scroll.pack(side="right", fill="y")
    set_canvas.bind("<Enter>", lambda _e: set_canvas.bind_all(
        "<MouseWheel>", lambda e: set_canvas.yview_scroll(-1 if e.delta > 0 else 1, "units")))
    set_canvas.bind("<Leave>", lambda _e: set_canvas.unbind_all("<MouseWheel>"))
    for row, s in enumerate(settings):
        ttk.Label(set_inner, text=f"[{s.section}] {s.key}").grid(row=row, column=0, sticky="w", padx=2)
        if "|" in s.values:
            widget = ttk.Combobox(set_inner, textvariable=setting_vars[s.env], values=[""] + s.values.split("|"), width=18)
        else:
            widget = ttk.Entry(set_inner, textvariable=setting_vars[s.env], width=20)
        widget.grid(row=row, column=1, sticky="w", padx=2)
        ttk.Label(set_inner, text=f"{s.env}   ({s.values})", foreground="#666").grid(row=row, column=2, sticky="w", padx=2)

    # Tab 3: log lines.
    tab_log = ttk.Frame(notebook, padding=4)
    notebook.add(tab_log, text="Log lines")
    grep_bar = ttk.Frame(tab_log)
    grep_bar.pack(fill="x")
    ttk.Label(grep_bar, text="Lines containing (separate with |):").pack(side="left")
    grep_var = tk.StringVar()
    ttk.Entry(grep_bar, textvariable=grep_var).pack(side="left", fill="x", expand=True, padx=4)
    log_text = ScrolledText(tab_log, wrap="none")
    log_text.pack(fill="both", expand=True, pady=4)

    ttk.Label(root, textvariable=status, relief="sunken", anchor="w").pack(fill="x", side="bottom")

    def current_test() -> TestItem:
        sel = test_list.curselection()
        return tests[sel[0]] if sel else tests[0]

    def selected_flags() -> list[str]:
        return [name for name, _, _ in FLAGS if flag_vars[name].get()]

    def selected_env() -> dict[str, str]:
        env: dict[str, str] = {}
        for env_name, var in setting_vars.items():
            if var.get().strip():
                env[env_name] = var.get().strip()
        for line in extra_env.get("1.0", "end").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            name, sep, value = line.partition("=")
            if sep and name.strip().startswith("OPENSPM_"):
                env[name.strip()] = value.strip()
        return env

    def remember() -> None:
        save_state({
            "graphics": graphics.get(), "toolchain": toolchain.get(), "flags": selected_flags(),
            "settings": {k: v.get() for k, v in setting_vars.items() if v.get().strip()},
            "extra_env": extra_env.get("1.0", "end").strip(),
            "test": current_test().key,
        })

    def apply_launch(launch: Launch) -> None:
        for name, var in flag_vars.items():
            var.set(name in launch.flags)
        for var in setting_vars.values():
            var.set("")
        extra = []
        for name, value in launch.env.items():
            if name in setting_vars:
                setting_vars[name].set(value)
            else:
                extra.append(f"{name}={value}")
        extra_env.delete("1.0", "end")
        extra_env.insert("1.0", "\n".join(extra))
        graphics.set(launch.graphics)
        toolchain.set(launch.toolchain)

    def poll() -> None:
        proc = running["proc"]
        if proc is not None and proc.poll() is not None:
            running["proc"] = None
            saved = keep_log(running["test"])
            status.set(f"Game window closed (exit {proc.returncode}). Log kept as {saved}" if saved
                       else f"Game window closed (exit {proc.returncode}); no launch.log found.")
            refresh_log()
        root.after(1000, poll)

    def keep_log(item: TestItem | None) -> Path | None:
        if not LAUNCH_LOG.exists():
            return None
        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        name = f"{stamp}-test{item.key if item else 0}.log"
        dest = RESULTS_DIR / "logs" / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(LAUNCH_LOG, dest)
        running["kept_log"] = dest
        return dest

    def do_launch() -> None:
        if running["proc"] is not None:
            if not messagebox.askyesno("OpenSPM", "A launch window is still open. Start another one anyway?"):
                return
        unknown = [f for f in selected_flags() if f not in FLAG_NAMES]
        if unknown:
            messagebox.showerror("OpenSPM", f"Unknown flags: {unknown}")
            return
        flags, env = selected_flags(), selected_env()
        args = launch_args(graphics.get(), toolchain.get())
        remember()
        if sys.platform != "win32":
            status.set("Would run: " + " ".join(args) + f"  OPENSPM_LAUNCH_FLAGS={' '.join(flags)} {env}")
            return
        running["proc"] = subprocess.Popen(args, cwd=str(ROOT), env=launch_env(dict(os.environ), flags, env),
                                           creationflags=subprocess.CREATE_NEW_CONSOLE)
        running["test"] = current_test()
        running["launch"] = {"graphics": graphics.get(), "toolchain": toolchain.get(), "flags": flags, "env": env}
        status.set(f"Launched: {graphics.get()} {toolchain.get()} flags=[{' '.join(flags)}] env={env}. "
                   "The log is kept when that window closes.")

    def refresh_log() -> None:
        patterns = [p.strip() for p in grep_var.get().split("|") if p.strip()]
        lines = grep_lines(LAUNCH_LOG, patterns) if patterns else []
        log_text.delete("1.0", "end")
        if not LAUNCH_LOG.exists():
            log_text.insert("1.0", f"No log yet: {LAUNCH_LOG}")
        elif not patterns:
            log_text.insert("1.0", "Type what to look for above (the test fills it in when it names log lines).")
        else:
            log_text.insert("1.0", "\n".join(lines) if lines else "(no matching lines in launch.log)")

    def copy_log_lines() -> None:
        root.clipboard_clear()
        root.clipboard_append(log_text.get("1.0", "end"))
        status.set("Log lines copied to the clipboard.")

    def pick_log() -> Path | None:
        """The latest launch.log, or (No) a log kept in test-results/logs."""
        kept = RESULTS_DIR / "logs"
        if LAUNCH_LOG.exists() and not messagebox.askyesno(
                "OpenSPM", "Analyze the latest launch.log?\n\nNo = pick a kept log from test-results\\logs."):
            picked = filedialog.askopenfilename(
                parent=root, title="Log to analyze", initialdir=str(kept if kept.exists() else ROOT),
                filetypes=[("Logs", "*.log"), ("All files", "*.*")])
            return Path(picked) if picked else None
        if LAUNCH_LOG.exists():
            return LAUNCH_LOG
        picked = filedialog.askopenfilename(parent=root, title="Log to analyze (no launch.log yet)",
                                            initialdir=str(kept if kept.exists() else ROOT),
                                            filetypes=[("Logs", "*.log"), ("All files", "*.*")])
        return Path(picked) if picked else None

    def run_analyzer(args: list[str], report: str) -> None:
        log = pick_log()
        if log is None:
            return
        out = RESULTS_DIR / report
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as handle:
            rc = subprocess.run([sys.executable, str(ANALYZER), str(log), *args], cwd=str(ROOT),
                                stdout=handle, stderr=subprocess.STDOUT).returncode
        status.set(f"Analyzer finished (exit {rc}) on {log.name}: {out}")
        open_path(out)

    def analyze_any() -> None:
        flags = (running.get("launch") or {}).get("flags") or selected_flags()
        args, report = default_analyzer_args(flags)
        text = simpledialog.askstring(
            "GX capture analyzer",
            "tools/diag/analyze_gx_capture.py (next: latest launch.log or a kept log).\n"
            "Arguments, then > report name (saved in test-results):",
            initialvalue=f"{' '.join(args)} > {report}", parent=root)
        if not text:
            return
        try:
            args, report = parse_analyze(text)
        except ValueError as error:
            messagebox.showerror("OpenSPM", str(error))
            return
        run_analyzer(args, report)

    def save_result(verdict: str) -> None:
        item = current_test()
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        launch = running.get("launch") or {"graphics": graphics.get(), "toolchain": toolchain.get(),
                                            "flags": selected_flags(), "env": selected_env()}
        patterns = [p.strip() for p in grep_var.get().split("|") if p.strip()]
        lines = grep_lines(LAUNCH_LOG, patterns)
        kept = running.get("kept_log")
        entry = [
            f"## {_dt.datetime.now():%Y-%m-%d %H:%M} - {item.key}. {item.title}: {verdict}",
            "",
            f"Launch: {launch['graphics']} {launch['toolchain']} flags=[{' '.join(launch['flags'])}] env={launch['env']}",
        ]
        if kept:
            entry.append(f"Log: {Path(kept).relative_to(ROOT)}")
        entry += ["", "Notes:", "", notes.get("1.0", "end").strip() or "(none)", ""]
        if lines:
            entry += [f"Log lines ({' | '.join(patterns)}):", "", "```", *lines, "```", ""]
        with (RESULTS_DIR / "RESULTS.md").open("a", encoding="utf-8") as handle:
            handle.write("\n".join(entry) + "\n")
        notes.delete("1.0", "end")
        status.set(f"Saved to {RESULTS_DIR / 'RESULTS.md'} ({verdict}). Send that file when you are done.")

    def open_ini() -> None:
        ini = ROOT / "OpenSPM.ini"
        if not ini.exists() and (ROOT / "OpenSPM.ini.example").exists():
            shutil.copy2(ROOT / "OpenSPM.ini.example", ini)
        open_path(ini)

    def open_screenshots() -> None:
        folder = ROOT / "screenshots"
        folder.mkdir(exist_ok=True)
        open_path(folder)

    def open_texture_packs() -> None:
        folder = CANDIDATE_DIR / "texturepacks"
        folder.mkdir(parents=True, exist_ok=True)
        open_path(folder)

    def open_results() -> None:
        RESULTS_DIR.mkdir(exist_ok=True)
        open_path(RESULTS_DIR)

    def open_log() -> None:
        if LAUNCH_LOG.exists():
            open_path(LAUNCH_LOG)
        else:
            messagebox.showinfo("OpenSPM", f"No log yet: {LAUNCH_LOG}")

    def show_test(_event=None) -> None:
        item = current_test()
        for child in launch_bar.winfo_children():
            child.destroy()
        for child in tools_bar.winfo_children():
            child.destroy()
        for launch in item.launches or [Launch(label="normal launch")]:
            ttk.Button(launch_bar, text=launch.label,
                       command=lambda l=launch: (apply_launch(l), do_launch())).pack(side="left", padx=2, pady=2)
        ttk.Button(launch_bar, text="Launch with Options tab", command=do_launch).pack(side="right", padx=2)
        for args, report in item.analyze:
            ttk.Button(tools_bar, text=f"Analyze log -> {report}",
                       command=lambda a=args, r=report: run_analyzer(a, r)).pack(side="left", padx=2, pady=2)
        for text, command in [("GX capture analyzer...", analyze_any), ("Open launch.log", open_log), ("Screenshots", open_screenshots),
                              ("OpenSPM.ini", open_ini), ("Texture packs", open_texture_packs),
                              ("Results folder", open_results),
                              ("TEST-LIST.md", lambda: open_path(TEST_LIST))]:
            ttk.Button(tools_bar, text=text, command=command).pack(side="left", padx=2, pady=2)
        info.configure(state="normal")
        info.delete("1.0", "end")
        info.insert("1.0", item.text)
        info.configure(state="disabled")
        grep_var.set(" | ".join(item.grep))
        refresh_log()

    for verdict in ("Works", "Problem", "Partly"):
        ttk.Button(result_bar, text=f"Save result: {verdict}", command=lambda v=verdict: save_result(v)).pack(side="left", padx=2)
    ttk.Button(grep_bar, text="Refresh", command=refresh_log).pack(side="left", padx=2)
    ttk.Button(grep_bar, text="Copy", command=copy_log_lines).pack(side="left", padx=2)

    test_list.bind("<<ListboxSelect>>", show_test)
    start = next((i for i, t in enumerate(tests) if t.key == state.get("test")), 0)
    test_list.selection_set(start)
    test_list.see(start)
    show_test()
    root.protocol("WM_DELETE_WINDOW", lambda: (remember(), root.destroy()))
    root.after(1000, poll)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
