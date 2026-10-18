#!/usr/bin/env python3
"""OpenSPM M14 live runtime debug cockpit."""

from __future__ import annotations

import os
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from m14_debug_console import (
    DEFAULT_CAPTURE_DIR,
    DebugConnection,
    LaunchLogCapture,
    connect,
    execute_script_text,
)


BG = "#16191f"
PANEL = "#1f242c"
PANEL_2 = "#282e38"
TEXT = "#e8edf3"
MUTED = "#9aa7b4"
ACCENT = "#6fb3ff"
GOOD = "#74d99f"
WARN = "#ffcb6b"
BAD = "#ff7b86"


class DebugCockpit:
    def __init__(self, root: tk.Tk, pid: int | None = None) -> None:
        self.root = root
        self.pid = pid
        self.connection: DebugConnection | None = None
        self.lock = threading.RLock()
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.script_stop = threading.Event()
        self.script_thread: threading.Thread | None = None
        self.capture = LaunchLogCapture()

        self.root.title("OpenSPM • Live Debug Cockpit")
        self.root.geometry("1280x820")
        self.root.minsize(1050, 700)
        self.root.configure(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self._configure_styles()
        self._build_ui()
        self.root.after(50, self._drain_events)
        self.connect_async()

    def _configure_styles(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", background=BG, foreground=TEXT, fieldbackground=PANEL)
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("Panel2.TFrame", background=PANEL_2)
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("Panel.TLabel", background=PANEL, foreground=TEXT)
        style.configure("Muted.TLabel", background=PANEL, foreground=MUTED)
        style.configure("Title.TLabel", background=BG, foreground=TEXT, font=("Segoe UI Semibold", 17))
        style.configure("Section.TLabel", background=PANEL, foreground=ACCENT, font=("Segoe UI Semibold", 10))
        style.configure("TButton", padding=(10, 6), background=PANEL_2, foreground=TEXT)
        style.map("TButton", background=[("active", "#343c49"), ("pressed", "#11151a")])
        style.configure("Accent.TButton", background="#315f91", foreground="white")
        style.map("Accent.TButton", background=[("active", "#3f74ac")])
        style.configure("Danger.TButton", background="#713640", foreground="white")
        style.map("Danger.TButton", background=[("active", "#8b4350")])
        style.configure("TCheckbutton", background=PANEL, foreground=TEXT)
        style.map("TCheckbutton", background=[("active", PANEL)])
        style.configure("TEntry", fieldbackground="#10141a", foreground=TEXT, insertcolor=TEXT)
        style.configure("TCombobox", fieldbackground="#10141a", foreground=TEXT)
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=PANEL, foreground=MUTED, padding=(12, 6))
        style.map("TNotebook.Tab", background=[("selected", PANEL_2)], foreground=[("selected", TEXT)])

    def _panel(self, parent: tk.Misc, title: str) -> ttk.Frame:
        frame = ttk.Frame(parent, style="Panel.TFrame", padding=12)
        ttk.Label(frame, text=title, style="Section.TLabel").pack(anchor="w", pady=(0, 8))
        return frame

    def _build_ui(self) -> None:
        header = ttk.Frame(self.root, padding=(14, 10))
        header.pack(fill="x")
        ttk.Label(header, text="OpenSPM Live Debug Cockpit", style="Title.TLabel").pack(side="left")
        self.conn_label = ttk.Label(header, text="● connecting…", foreground=WARN)
        self.conn_label.pack(side="right", padx=(10, 0))
        ttk.Button(header, text="Reconnect", command=self.connect_async).pack(side="right")

        body = ttk.Panedwindow(self.root, orient="horizontal")
        body.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        left = ttk.Frame(body)
        center = ttk.Frame(body)
        right = ttk.Frame(body)
        body.add(left, weight=0)
        body.add(center, weight=3)
        body.add(right, weight=2)

        self._build_left(left)
        self._build_center(center)
        self._build_right(right)

    def _build_left(self, parent: ttk.Frame) -> None:
        scene = self._panel(parent, "SCENE")
        scene.pack(fill="x", pady=(0, 8))
        row = ttk.Frame(scene, style="Panel.TFrame")
        row.pack(fill="x")
        ttk.Button(row, text="◀ Prev room", command=lambda: self.send_async("prevroom")).pack(side="left", expand=True, fill="x")
        ttk.Button(row, text="↻ Reload", command=lambda: self.send_async("reloadroom")).pack(side="left", padx=4)
        ttk.Button(row, text="Next room ▶", command=lambda: self.send_async("nextroom")).pack(side="left", expand=True, fill="x")
        self.map_var = tk.StringVar()
        row = ttk.Frame(scene, style="Panel.TFrame")
        row.pack(fill="x", pady=(8, 0))
        ttk.Entry(row, textvariable=self.map_var).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Warp", command=lambda: self.send_async(f"warp {self.map_var.get().strip()}")).pack(side="left", padx=(6, 0))
        ttk.Button(scene, text="Read current map / door", command=lambda: self.send_async("map")).pack(fill="x", pady=(6, 0))

        player = self._panel(parent, "PLAYER")
        player.pack(fill="x", pady=(0, 8))
        speedrow = ttk.Frame(player, style="Panel.TFrame")
        speedrow.pack(fill="x")
        ttk.Label(speedrow, text="Move speed", style="Panel.TLabel").pack(side="left")
        self.speed_var = tk.StringVar(value="1.0")
        speed = ttk.Combobox(speedrow, textvariable=self.speed_var, values=("0.5", "1.0", "1.5", "2.0", "3.0", "5.0"), width=6)
        speed.pack(side="left", padx=6)
        ttk.Button(speedrow, text="Apply", command=lambda: self.send_async(f"speed {self.speed_var.get()}")).pack(side="left")
        ttk.Button(speedrow, text="Reset", command=lambda: self.send_async("speed 1")).pack(side="left", padx=(5, 0))

        nudge = ttk.Frame(player, style="Panel.TFrame")
        nudge.pack(fill="x", pady=(8, 0))
        ttk.Label(nudge, text="Nudge", style="Panel.TLabel").grid(row=0, column=0, sticky="w")
        self.nudge_var = tk.StringVar(value="50")
        ttk.Entry(nudge, textvariable=self.nudge_var, width=7).grid(row=0, column=1, padx=4)
        ttk.Button(nudge, text="← X", command=lambda: self._nudge(-1, 0, 0)).grid(row=1, column=0, sticky="ew", pady=3)
        ttk.Button(nudge, text="X →", command=lambda: self._nudge(1, 0, 0)).grid(row=1, column=1, sticky="ew", pady=3)
        ttk.Button(nudge, text="↑ Y", command=lambda: self._nudge(0, 1, 0)).grid(row=2, column=0, sticky="ew", pady=3)
        ttk.Button(nudge, text="↓ Y", command=lambda: self._nudge(0, -1, 0)).grid(row=2, column=1, sticky="ew", pady=3)
        ttk.Button(nudge, text="Z +", command=lambda: self._nudge(0, 0, 1)).grid(row=3, column=0, sticky="ew", pady=3)
        ttk.Button(nudge, text="Z −", command=lambda: self._nudge(0, 0, -1)).grid(row=3, column=1, sticky="ew", pady=3)
        ttk.Button(player, text="Read position", command=lambda: self.send_async("pos")).pack(fill="x", pady=(6, 0))
        checkpoint = ttk.Frame(player, style="Panel.TFrame")
        checkpoint.pack(fill="x", pady=(6, 0))
        ttk.Button(checkpoint, text="Save position", command=lambda: self.send_async("checkpoint save")).pack(side="left", expand=True, fill="x")
        ttk.Button(checkpoint, text="Restore position", command=lambda: self.send_async("checkpoint load")).pack(side="left", expand=True, fill="x", padx=(5, 0))

        hp = ttk.Frame(player, style="Panel.TFrame")
        hp.pack(fill="x", pady=(8, 0))
        self.hp_var = tk.StringVar(value="10")
        ttk.Entry(hp, textvariable=self.hp_var, width=8).pack(side="left")
        ttk.Button(hp, text="Set HP/max", command=lambda: self.send_async(f"hp {self.hp_var.get()}")).pack(side="left", padx=5)
        ttk.Button(hp, text="Read HP", command=lambda: self.send_async("hp")).pack(side="left")

        self.moon_var = tk.BooleanVar(value=False)
        moon = ttk.Checkbutton(player, text="Moonjump ×2 (reserved; exact jump parameter pending)", variable=self.moon_var, command=self._moonjump)
        moon.pack(anchor="w", pady=(8, 0))

        npcs = self._panel(parent, "NPC PERFORMANCE A/B")
        npcs.pack(fill="x", pady=(0, 8))
        ttk.Button(npcs, text="NPC info", command=lambda: self.send_async("npcs info")).pack(fill="x")
        self.npc_freeze_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(npcs, text="Freeze NPC logic only", variable=self.npc_freeze_var, command=self._toggle_npc_freeze).pack(anchor="w", pady=(6, 0))
        self.npc_suspend_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(npcs, text="Hide + freeze NPCs (safe structural isolation)", variable=self.npc_suspend_var, command=self._toggle_npc_suspend).pack(anchor="w", pady=(4, 0))
        ttk.Label(
            npcs,
            text="Freeze keeps NPCs rendered. Hide+freeze keeps entries active structurally while removing their update/display work. Destructive active-bit suspend remains CLI-only.",
            style="Muted.TLabel",
            wraplength=250,
            justify="left",
        ).pack(anchor="w", pady=(6, 0))

        misc = self._panel(parent, "QUICK")
        misc.pack(fill="x")
        ttk.Button(misc, text="Snapshot", command=self.snapshot).pack(side="left", expand=True, fill="x")
        ttk.Button(misc, text="Status", command=lambda: self.send_async("status")).pack(side="left", expand=True, fill="x", padx=4)
        ttk.Button(misc, text="Restore", style="Danger.TButton", command=self.restore_defaults).pack(side="left", expand=True, fill="x")

    def _build_center(self, parent: ttk.Frame) -> None:
        scripts = self._panel(parent, "EXPERIMENT SCRIPT")
        scripts.pack(fill="both", expand=True, padx=(8, 4))
        toolbar = ttk.Frame(scripts, style="Panel.TFrame")
        toolbar.pack(fill="x", pady=(0, 6))
        ttk.Button(toolbar, text="▶ Run", style="Accent.TButton", command=self.run_script).pack(side="left")
        ttk.Button(toolbar, text="Run selection", command=self.run_selection).pack(side="left", padx=(5, 0))
        ttk.Button(toolbar, text="■ Stop", style="Danger.TButton", command=self.stop_script).pack(side="left", padx=5)
        ttk.Button(toolbar, text="Open…", command=self.load_script).pack(side="left")
        ttk.Button(toolbar, text="Save…", command=self.save_script).pack(side="left", padx=5)
        ttk.Button(toolbar, text="NPC A/B template", command=self.template_npc_ab).pack(side="right")

        self.script_text = tk.Text(
            scripts,
            bg="#10141a",
            fg=TEXT,
            insertbackground=TEXT,
            selectbackground="#315f91",
            relief="flat",
            undo=True,
            font=("Cascadia Mono", 10),
            wrap="none",
        )
        self.script_text.pack(fill="both", expand=True)
        self.script_text.bind("<Control-Return>", lambda _e: (self.run_script(), "break")[1])
        self.script_text.bind("<Control-Shift-Return>", lambda _e: (self.run_selection(), "break")[1])
        self.script_text.insert(
            "1.0",
            "# Commands run against the live game. No rebuild required.\n"
            "status\n"
            "map\n"
            "\n"
            "# Example timed capture. defer runs even if you press Stop.\n"
            "# capture start example\n"
            "# defer npcs freeze off\n"
            "# npcs freeze on\n"
            "# wait 5\n"
            "# capture stop\n",
        )

        cmd = ttk.Frame(scripts, style="Panel.TFrame")
        cmd.pack(fill="x", pady=(8, 0))
        self.command_var = tk.StringVar()
        entry = ttk.Entry(cmd, textvariable=self.command_var)
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda _e: self.send_command_entry())
        ttk.Button(cmd, text="Send", command=self.send_command_entry).pack(side="left", padx=(6, 0))

    def _build_right(self, parent: ttk.Frame) -> None:
        capture = self._panel(parent, "LOG CAPTURE")
        capture.pack(fill="x", padx=(4, 0), pady=(0, 8))
        self.capture_name = tk.StringVar(value="experiment")
        ttk.Entry(capture, textvariable=self.capture_name).pack(fill="x")
        row = ttk.Frame(capture, style="Panel.TFrame")
        row.pack(fill="x", pady=(6, 0))
        ttk.Button(row, text="Start capture", command=self.capture_start).pack(side="left", expand=True, fill="x")
        ttk.Button(row, text="Stop + save", command=self.capture_stop).pack(side="left", expand=True, fill="x", padx=(5, 0))
        ttk.Button(capture, text="Open captures folder", command=self.open_capture_folder).pack(fill="x", pady=(6, 0))

        mem = self._panel(parent, "MEMORY LAB")
        mem.pack(fill="x", padx=(4, 0), pady=(0, 8))
        mem_grid = ttk.Frame(mem, style="Panel.TFrame")
        mem_grid.pack(fill="x")
        self.addr_var = tk.StringVar(value="0x804CEE58")
        self.value_var = tk.StringVar(value="0")
        self.length_var = tk.StringVar(value="64")
        ttk.Label(mem_grid, text="Address", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Entry(mem_grid, textvariable=self.addr_var).grid(row=0, column=1, columnspan=3, sticky="ew", padx=(6, 0))
        ttk.Label(mem_grid, text="Value", style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(5, 0))
        ttk.Entry(mem_grid, textvariable=self.value_var).grid(row=1, column=1, columnspan=3, sticky="ew", padx=(6, 0), pady=(5, 0))
        ttk.Button(mem_grid, text="Read32", command=lambda: self.send_async(f"read32 {self.addr_var.get()}")).grid(row=2, column=0, sticky="ew", pady=5)
        ttk.Button(mem_grid, text="Write32", command=lambda: self.send_async(f"write32 {self.addr_var.get()} {self.value_var.get()}")).grid(row=2, column=1, sticky="ew", padx=4, pady=5)
        ttk.Button(mem_grid, text="Freeze32", command=lambda: self.send_async(f"freeze32 {self.addr_var.get()} {self.value_var.get()}")).grid(row=2, column=2, sticky="ew", pady=5)
        ttk.Button(mem_grid, text="Unfreeze", command=lambda: self.send_async(f"unfreeze {self.addr_var.get()}")).grid(row=2, column=3, sticky="ew", padx=(4, 0), pady=5)
        ttk.Label(mem_grid, text="Dump bytes", style="Muted.TLabel").grid(row=3, column=0, sticky="w")
        ttk.Entry(mem_grid, textvariable=self.length_var, width=7).grid(row=3, column=1, sticky="ew", padx=4)
        ttk.Button(mem_grid, text="Dump", command=lambda: self.send_async(f"dump {self.addr_var.get()} {self.length_var.get()}")).grid(row=3, column=2, columnspan=2, sticky="ew")
        for column in range(4):
            mem_grid.columnconfigure(column, weight=1)

        out = self._panel(parent, "TRANSCRIPT")
        out.pack(fill="both", expand=True, padx=(4, 0))
        self.output = tk.Text(
            out,
            bg="#0c1015",
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            state="disabled",
            font=("Cascadia Mono", 9),
            wrap="word",
        )
        self.output.pack(fill="both", expand=True)
        out_buttons = ttk.Frame(out, style="Panel.TFrame")
        out_buttons.pack(fill="x", pady=(6, 0))
        ttk.Button(out_buttons, text="Save transcript…", command=self.save_transcript).pack(side="left", expand=True, fill="x")
        ttk.Button(out_buttons, text="Clear", command=self.clear_output).pack(side="left", expand=True, fill="x", padx=(5, 0))

    def _emit(self, text: object) -> None:
        self.events.put(("log", str(text)))

    def _drain_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self.output.configure(state="normal")
                    self.output.insert("end", str(payload) + "\n")
                    self.output.see("end")
                    self.output.configure(state="disabled")
                elif kind == "connected":
                    pid = int(payload)
                    self.conn_label.configure(text=f"● connected • PID {pid}", foreground=GOOD)
                elif kind == "error":
                    self.conn_label.configure(text="● disconnected", foreground=BAD)
                    self._emit(payload)
        except queue.Empty:
            pass
        self.root.after(50, self._drain_events)

    def connect_async(self) -> None:
        def worker() -> None:
            try:
                new = connect(self.pid, 3.0)
                with self.lock:
                    old = self.connection
                    self.connection = new
                    if old is not None:
                        old.close()
                self.events.put(("connected", new.pid))
                self._emit("Connected. Runtime commands execute at the safe guest-frame boundary.")
            except Exception as exc:
                self.events.put(("error", f"Connection failed: {exc}"))
        threading.Thread(target=worker, daemon=True).start()

    def _with_connection(self) -> DebugConnection:
        if self.connection is None:
            raise RuntimeError("not connected to OpenSPMNative")
        return self.connection

    def send_async(self, command: str) -> None:
        command = command.strip()
        if not command:
            return
        self._emit(f"> {command}")

        def worker() -> None:
            try:
                with self.lock:
                    connection = self._with_connection()
                result = connection.send(command)
                self._emit(result)
            except Exception as exc:
                self._emit(f"! {exc}")
        threading.Thread(target=worker, daemon=True).start()

    def send_command_entry(self) -> None:
        command = self.command_var.get().strip()
        if command:
            self.command_var.set("")
            self.send_async(command)

    def _nudge(self, x: int, y: int, z: int) -> None:
        try:
            amount = float(self.nudge_var.get())
        except ValueError:
            messagebox.showerror("Invalid nudge", "Nudge amount must be numeric.")
            return
        self.send_async(f"nudge {x * amount:g} {y * amount:g} {z * amount:g}")

    def _toggle_npc_freeze(self) -> None:
        self.send_async(f"npcs freeze {'on' if self.npc_freeze_var.get() else 'off'}")

    def _toggle_npc_suspend(self) -> None:
        self.send_async("npcs isolate hide 0" if self.npc_suspend_var.get() else "npcs isolate off")

    def _moonjump(self) -> None:
        if self.moon_var.get():
            self.send_async("moonjump 2")
            self.root.after(400, lambda: self.moon_var.set(False))
        else:
            self.send_async("moonjump off")

    def run_script(self) -> None:
        if self.script_thread is not None and self.script_thread.is_alive():
            messagebox.showinfo("Script running", "Stop the current experiment before starting another.")
            return
        script = self.script_text.get("1.0", "end-1c")
        self.script_stop.clear()

        def worker() -> None:
            try:
                with self.lock:
                    connection = self._with_connection()
                ok = execute_script_text(
                    connection,
                    script,
                    emit=self._emit,
                    stop_requested=self.script_stop.is_set,
                    capture=self.capture,
                )
                self._emit(f"script {'completed' if ok else 'stopped/failed'}")
            except Exception as exc:
                self._emit(f"! script error: {exc}")
        self.script_thread = threading.Thread(target=worker, daemon=True)
        self.script_thread.start()

    def run_selection(self) -> None:
        try:
            script = self.script_text.get("sel.first", "sel.last")
        except tk.TclError:
            messagebox.showinfo("No selection", "Select one or more script lines first.")
            return
        if self.script_thread is not None and self.script_thread.is_alive():
            messagebox.showinfo("Script running", "Stop the current experiment before starting another.")
            return
        self.script_stop.clear()

        def worker() -> None:
            try:
                with self.lock:
                    connection = self._with_connection()
                ok = execute_script_text(
                    connection,
                    script,
                    emit=self._emit,
                    stop_requested=self.script_stop.is_set,
                    capture=self.capture,
                )
                self._emit(f"selection {'completed' if ok else 'stopped/failed'}")
            except Exception as exc:
                self._emit(f"! selection error: {exc}")
        self.script_thread = threading.Thread(target=worker, daemon=True)
        self.script_thread.start()

    def stop_script(self) -> None:
        self.script_stop.set()
        self._emit("stop requested; deferred cleanup will run before capture closes")

    def template_npc_ab(self) -> None:
        template = (
            "# Stable-population NPC A/B.\n"
            "# Rejects rooms whose active NPC count changes during preflight.\n"
            "# Compare baseline vs logic-only freeze; display/active identity stays live.\n"
            "defer npcs isolate off\n"
            "defer npcs freeze off\n"
            "defer npcs suspend off\n"
            "defer speed 1\n"
            "stable_npcs 2.0 0.20\n"
            "capture start npc-baseline\n"
            "npcs freeze off\n"
            "wait 3\n"
            "capture stop\n\n"
            "stable_npcs 1.0 0.20\n"
            "capture start npc-logic-frozen\n"
            "npcs freeze on\n"
            "wait 3\n"
            "npcs freeze off\n"
            "capture stop\n"
        )
        self.script_text.delete("1.0", "end")
        self.script_text.insert("1.0", template)

    def capture_start(self) -> None:
        name = self.capture_name.get().strip() or "capture"

        def worker() -> None:
            try:
                with self.lock:
                    connection = self._with_connection()
                result = self.capture.start(connection, name)
                self._emit(result)
            except Exception as exc:
                self._emit(f"! capture: {exc}")
        threading.Thread(target=worker, daemon=True).start()

    def capture_stop(self) -> None:
        def worker() -> None:
            try:
                with self.lock:
                    connection = self._with_connection()
                output = self.capture.stop(connection)
                self._emit(f"saved capture: {output}")
            except Exception as exc:
                self._emit(f"! capture: {exc}")
        threading.Thread(target=worker, daemon=True).start()

    def snapshot(self) -> None:
        commands = ("status", "map", "pos", "hp", "npcs info", "freezes")
        self._emit("> snapshot")

        def worker() -> None:
            try:
                with self.lock:
                    connection = self._with_connection()
                for command in commands:
                    self._emit(f"[{command}] {connection.send(command)}")
            except Exception as exc:
                self._emit(f"! snapshot: {exc}")
        threading.Thread(target=worker, daemon=True).start()

    def restore_defaults(self) -> None:
        commands = ("resetdebug",)
        self.npc_freeze_var.set(False)
        self.npc_suspend_var.set(False)
        self.speed_var.set("1.0")
        self.moon_var.set(False)
        self._emit("> restore safe debug defaults")

        def worker() -> None:
            try:
                with self.lock:
                    connection = self._with_connection()
                for command in commands:
                    self._emit(f"[{command}] {connection.send(command)}")
            except Exception as exc:
                self._emit(f"! restore: {exc}")
        threading.Thread(target=worker, daemon=True).start()

    def save_transcript(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Save OpenSPM debug transcript",
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            return
        text = self.output.get("1.0", "end-1c")
        Path(path).write_text(text + ("\n" if text else ""), encoding="utf-8")

    def open_capture_folder(self) -> None:
        DEFAULT_CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(DEFAULT_CAPTURE_DIR)  # type: ignore[attr-defined]
        except OSError as exc:
            self._emit(f"! could not open captures folder: {exc}")

    def load_script(self) -> None:
        path = filedialog.askopenfilename(
            title="Open OpenSPM experiment script",
            filetypes=[("OpenSPM scripts", "*.txt *.spmdbg"), ("All files", "*.*")],
        )
        if not path:
            return
        self.script_text.delete("1.0", "end")
        self.script_text.insert("1.0", Path(path).read_text(encoding="utf-8"))

    def save_script(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Save OpenSPM experiment script",
            defaultextension=".spmdbg",
            filetypes=[("OpenSPM scripts", "*.spmdbg"), ("Text files", "*.txt")],
        )
        if not path:
            return
        Path(path).write_text(self.script_text.get("1.0", "end-1c") + "\n", encoding="utf-8")

    def clear_output(self) -> None:
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        self.output.configure(state="disabled")

    def close(self) -> None:
        self.script_stop.set()
        with self.lock:
            connection = self.connection
            self.connection = None

        if connection is not None:
            # Closing the cockpit must not strand a live game in a diagnostic
            # state. Best-effort restore is synchronous; DebugConnection
            # serializes against any final in-flight script command.
            try:
                connection.send("resetdebug")
            except Exception:
                pass
            if self.capture.active:
                try:
                    self.capture.stop(connection)
                except Exception:
                    pass
            connection.close()
        self.root.destroy()


def main() -> int:
    root = tk.Tk()
    DebugCockpit(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
