#!/usr/bin/env python3
"""Reproduce and resume the current M14 standalone-native lab milestone.

The script is intentionally a front door for future sessions. It records the
current game identity and lab recipe in the repository, while all Nintendo-
derived inputs/outputs stay under a gitignored local cache.

Typical use with an already extracted game and generated source:

    python tools/m14_lab/resume_m14.py \
        --game-dir /path/to/extracted-r8pe01 \
        --generated /path/to/generated \
        --generated-is-direct \
        --cpu-runtime /path/to/dolrecomp-runtime

Or start from a player-owned WBFS (requires `wit` and `dolrecomp` locally):

    python tools/m14_lab/resume_m14.py \
        --wbfs /path/to/SPM.wbfs \
        --wit /path/to/wit \
        --dolrecomp /path/to/dolrecomp \
        --cpu-runtime /path/to/dolrecomp-runtime
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[1]
NATIVE_BRIDGE = ROOT / "tools" / "native_bridge"
sys.path.insert(0, str(NATIVE_BRIDGE))
sys.path.insert(0, str(SCRIPT_DIR))

import direct_calls  # type: ignore  # noqa: E402
import probe_generated  # type: ignore  # noqa: E402

OWNED_MARKER = ".openspm-m14-owned-cache"
RESULT_PREFIX = "OPENSPM_RUN_RESULT "


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_state(path: Path) -> dict[str, Any]:
    state = json.loads(path.read_text(encoding="utf-8"))
    if state.get("schema_version") != 1:
        raise RuntimeError(f"unsupported M14 state schema in {path}")
    return state


def find_executable(explicit: Path | None, env_name: str, fallback: str) -> str | None:
    if explicit is not None:
        path = explicit.expanduser().resolve()
        if not path.is_file():
            raise RuntimeError(f"{fallback} executable does not exist: {path}")
        return str(path)
    env_value = os.environ.get(env_name)
    if env_value:
        path = Path(env_value).expanduser().resolve()
        if not path.is_file():
            raise RuntimeError(f"{env_name} points to a missing file: {path}")
        return str(path)
    return shutil.which(fallback)


def run(command: list[str], *, cwd: Path | None = None,
        stdout=None, stderr=None) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(command), flush=True)
    return subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        check=True,
        text=True,
        stdout=stdout,
        stderr=stderr,
    )


def mark_owned(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / OWNED_MARKER).write_text(
        "OpenSPM M14 local cache. Safe for resume_m14.py to recreate.\n",
        encoding="utf-8",
    )


def reset_owned_directory(directory: Path) -> None:
    if directory.exists():
        marker = directory / OWNED_MARKER
        if not marker.is_file():
            raise RuntimeError(
                f"refusing to replace unmarked directory {directory}; "
                f"delete/move it yourself or choose another --cache"
            )
        shutil.rmtree(directory)
    mark_owned(directory)


def validate_game_dir(game_dir: Path, state: dict[str, Any]) -> Path:
    main_dol = game_dir / "sys" / "main.dol"
    if not main_dol.is_file():
        raise RuntimeError(f"extracted game is missing sys/main.dol: {game_dir}")
    actual = sha256_file(main_dol)
    expected = state["game"]["main_dol_sha256"].lower()
    if actual.lower() != expected:
        raise RuntimeError(
            "main.dol identity mismatch: expected " + expected + ", got " + actual
        )
    return main_dol


def extract_wbfs(wbfs: Path, game_dir: Path, wit: str, state: dict[str, Any]) -> Path:
    wbfs = wbfs.expanduser().resolve()
    if not wbfs.is_file():
        raise RuntimeError(f"WBFS does not exist: {wbfs}")

    existing = game_dir / "sys" / "main.dol"
    if existing.is_file():
        try:
            return validate_game_dir(game_dir, state)
        except RuntimeError:
            reset_owned_directory(game_dir)
    elif not game_dir.exists():
        mark_owned(game_dir)
    elif not (game_dir / OWNED_MARKER).is_file():
        raise RuntimeError(f"cache game directory is not OpenSPM-owned: {game_dir}")

    run([wit, "extract", "--psel", "DATA", "--pmode", "NONE",
         str(wbfs), "-d", str(game_dir)])
    return validate_game_dir(game_dir, state)


def locate_generated(root: Path) -> Path:
    if (root / "generated.h").is_file() and (root / "chunks").is_dir():
        return root
    matches = [header.parent for header in root.rglob("generated.h")
               if (header.parent / "chunks").is_dir()]
    if len(matches) != 1:
        listing = ", ".join(str(path) for path in matches) or "none"
        raise RuntimeError(f"expected one generated tree under {root}; found {listing}")
    return matches[0]


def generate_source(main_dol: Path, work_dir: Path, dolrecomp: str,
                    jobs: int, game_id: str) -> Path:
    script = NATIVE_BRIDGE / "generate_local_source.py"
    run([
        sys.executable, str(script),
        "--dolrecomp", dolrecomp,
        "--main-dol", str(main_dol),
        "--work-dir", str(work_dir),
        "--game-id", game_id,
        "--jobs", str(jobs),
    ])
    return locate_generated(work_dir / "dolrecomp-output")


def stage_generated(source: Path, output: Path, already_direct: bool) -> dict[str, Any]:
    if output.exists():
        shutil.rmtree(output)
    if already_direct:
        shutil.copytree(source, output)
        return {
            "mode": "already-direct",
            "chunks": len(list((output / "chunks").glob("chunk_*.c"))),
        }
    stats = direct_calls.rewrite_tree(source, output)
    return {"mode": "direct-calls", **stats}


def validate_chunk_count(generated: Path, state: dict[str, Any], allow_mismatch: bool) -> int:
    count = len(list((generated / "chunks").glob("chunk_*.c")))
    expected = int(state["game"]["expected_generated_chunks"])
    if count != expected and not allow_mismatch:
        raise RuntimeError(
            f"generated chunk count mismatch: expected {expected}, got {count}; "
            "use --allow-chunk-count-mismatch only for an intentional codegen experiment"
        )
    return count


def build_lab_runner(generated: Path, cpu_runtime: Path, build_dir: Path,
                     cmake: str, config: str, jobs: int) -> Path:
    cpu_runtime = cpu_runtime.expanduser().resolve()
    if not (cpu_runtime / "cpu" / "cpu.c").is_file():
        raise RuntimeError(
            f"--cpu-runtime must contain cpu/cpu.c; got {cpu_runtime}"
        )

    run([
        cmake,
        "-S", str(SCRIPT_DIR),
        "-B", str(build_dir),
        f"-DOPENSPM_GENERATED_DIR={generated}",
        f"-DOPENSPM_CPU_RUNTIME_DIR={cpu_runtime}",
        f"-DCMAKE_BUILD_TYPE={config}",
    ])
    build_command = [cmake, "--build", str(build_dir), "--config", config,
                     "--target", "openspm_m14_lab", "--parallel", str(jobs)]
    run(build_command)

    candidates = list(build_dir.rglob("openspm_m14_lab")) + list(
        build_dir.rglob("openspm_m14_lab.exe"))
    candidates = [path for path in candidates if path.is_file()]
    if not candidates:
        raise RuntimeError("lab build succeeded but openspm_m14_lab was not found")
    return max(candidates, key=lambda path: path.stat().st_mtime_ns)


def parse_runner_result(stdout_text: str) -> dict[str, Any] | None:
    for line in reversed(stdout_text.splitlines()):
        if line.startswith(RESULT_PREFIX):
            try:
                return json.loads(line[len(RESULT_PREFIX):])
            except json.JSONDecodeError:
                return {"parse_error": line}
    return None


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument("--wbfs", type=Path, help="player-owned SPM WBFS")
    inputs.add_argument("--game-dir", type=Path, help="already extracted SPM directory")
    parser.add_argument("--generated", type=Path,
                        help="existing local generated source; omit to regenerate")
    parser.add_argument("--generated-is-direct", action="store_true",
                        help="--generated already has OpenSPM direct-call conversion")
    parser.add_argument("--dolrecomp", type=Path)
    parser.add_argument("--wit", type=Path)
    parser.add_argument("--cpu-runtime", type=Path,
                        default=Path(os.environ["OPENSPM_CPU_RUNTIME"])
                        if os.environ.get("OPENSPM_CPU_RUNTIME") else None)
    parser.add_argument("--cmake", default=os.environ.get("OPENSPM_CMAKE", "cmake"))
    parser.add_argument("--cache", type=Path, default=ROOT / ".openspm" / "m14")
    parser.add_argument("--state", type=Path, default=ROOT / "state" / "m14.json")
    parser.add_argument("--jobs", type=int, default=max(1, min(16, os.cpu_count() or 4)))
    parser.add_argument("--config", default="Release")
    parser.add_argument("--probe", choices=["safety-a", "none"], default="safety-a")
    parser.add_argument("--no-build", action="store_true")
    parser.add_argument("--no-run", action="store_true")
    parser.add_argument("--doctor", action="store_true",
                        help="verify state/input identity and print resolved tools; do not generate/build/run")
    parser.add_argument("--allow-chunk-count-mismatch", action="store_true")
    parser.add_argument("--max-yields", type=int, default=5_000_000)
    parser.add_argument("--stuck-limit", type=int, default=100_000)
    args = parser.parse_args()

    if args.generated_is_direct and args.generated is None:
        parser.error("--generated-is-direct requires --generated")
    if not 1 <= args.jobs <= 256:
        parser.error("--jobs must be 1..256")
    if args.max_yields < 1 or args.stuck_limit < 1:
        parser.error("--max-yields and --stuck-limit must be positive")

    state_path = args.state.expanduser().resolve()
    state = load_state(state_path)
    cache = args.cache.expanduser().resolve()
    cache.mkdir(parents=True, exist_ok=True)
    (cache / OWNED_MARKER).write_text("OpenSPM M14 cache root.\n", encoding="utf-8")

    wit = find_executable(args.wit, "OPENSPM_WIT", "wit")
    dolrecomp = find_executable(args.dolrecomp, "OPENSPM_DOLRECOMP", "dolrecomp")
    cmake = shutil.which(args.cmake) or (str(Path(args.cmake).resolve()) if Path(args.cmake).is_file() else None)

    if args.game_dir:
        game_dir = args.game_dir.expanduser().resolve()
        main_dol = validate_game_dir(game_dir, state)
    elif args.wbfs:
        if not wit:
            raise RuntimeError("WBFS supplied but wit was not found; pass --wit or OPENSPM_WIT")
        game_dir = cache / "game"
        main_dol = extract_wbfs(args.wbfs, game_dir, wit, state)
    else:
        game_dir = cache / "game"
        main_dol = validate_game_dir(game_dir, state)

    identity = sha256_file(main_dol)
    summary: dict[str, Any] = {
        "schema_version": 1,
        "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "state_file": str(state_path),
        "state_sha256": sha256_text(state_path),
        "game_id": state["game"]["id"],
        "main_dol": str(main_dol),
        "main_dol_sha256": identity,
        "tools": {"wit": wit, "dolrecomp": dolrecomp, "cmake": cmake},
        "phase": "verified",
    }

    if args.doctor:
        write_json(cache / "latest-run.json", summary)
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    if args.generated:
        generated_source = locate_generated(args.generated.expanduser().resolve())
    else:
        if not dolrecomp:
            raise RuntimeError(
                "no --generated tree supplied and dolrecomp was not found; "
                "pass --dolrecomp or OPENSPM_DOLRECOMP"
            )
        generated_source = generate_source(
            main_dol, cache / "generate", dolrecomp, args.jobs, state["game"]["id"]
        )

    stage = cache / "stage" / "generated-direct"
    stage_stats = stage_generated(generated_source, stage, args.generated_is_direct)
    chunk_count = validate_chunk_count(stage, state, args.allow_chunk_count_mismatch)
    summary["generated_source"] = str(generated_source)
    summary["staged_generated"] = str(stage)
    summary["stage_stats"] = stage_stats
    summary["chunk_count"] = chunk_count
    summary["phase"] = "staged"

    if args.probe == "safety-a":
        probe_args = probe_generated.load_probe_from_state(state_path)
        summary["probe"] = probe_generated.apply_safety_a_probe(stage, **probe_args)
    else:
        summary["probe"] = {"probe": "none"}

    if args.no_build:
        summary["phase"] = "prepared"
        write_json(cache / "latest-run.json", summary)
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    if args.cpu_runtime is None:
        raise RuntimeError("building the lab runner requires --cpu-runtime or OPENSPM_CPU_RUNTIME")
    if not cmake:
        raise RuntimeError("cmake was not found; pass --cmake or OPENSPM_CMAKE")

    runner = build_lab_runner(stage, args.cpu_runtime, cache / "build" / "lab",
                              cmake, args.config, args.jobs)
    summary["runner"] = str(runner)
    summary["runner_sha256"] = sha256_file(runner)
    summary["phase"] = "built"

    if args.no_run:
        write_json(cache / "latest-run.json", summary)
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + identity[:12]
    run_dir = cache / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    command = [
        str(runner),
        "--main-dol", str(main_dol),
        "--max-yields", str(args.max_yields),
        "--stuck-limit", str(args.stuck_limit),
    ]
    print("+", " ".join(command), flush=True)
    completed = subprocess.run(command, text=True, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, check=False)
    (run_dir / "stdout.log").write_text(completed.stdout, encoding="utf-8")
    (run_dir / "stderr.log").write_text(completed.stderr, encoding="utf-8")
    summary["run"] = {
        "id": run_id,
        "directory": str(run_dir),
        "command": command,
        "returncode": completed.returncode,
        "result": parse_runner_result(completed.stdout),
        "stdout": str(run_dir / "stdout.log"),
        "stderr": str(run_dir / "stderr.log"),
    }
    summary["phase"] = "ran"
    write_json(run_dir / "run.json", summary)
    write_json(cache / "latest-run.json", summary)

    if completed.stderr:
        tail = completed.stderr.splitlines()[-40:]
        print("\n".join(tail), file=sys.stderr)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return completed.returncode


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        print(f"command failed with exit code {exc.returncode}", file=sys.stderr)
        raise SystemExit(exc.returncode)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
