#!/usr/bin/env python3
"""Summarize or compare OpenSPM M14 performance captures.

The parser is intentionally tolerant of old/new diagnostic wording. It extracts
stable metrics by marker + key/value names rather than depending on timestamps
or logger prefixes. This makes historical captures useful while keeping dev113's
128-vs-256 chunk A/B machine-checkable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
import sys
import tempfile
from typing import Any

VI_BUDGET_MS = 1000.0 / 60.0
DEFAULT_MIN_PHASE_WINDOWS = 3
DEFAULT_MIN_CADENCE_WINDOWS = 2
DEFAULT_MAX_FPS_REGRESSION = 3.0
DEFAULT_MAX_CRITICAL_REGRESSION_MS = 0.75
DEFAULT_MAX_DRAWDONE_REGRESSION_MS = 0.75
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOG = ROOT / ".openspm" / "m14" / "home-test" / "launch.log"
DEFAULT_CAPTURE_DIR = ROOT / ".openspm" / "m14" / "debug-captures"

NUMBER = r"-?\d+(?:\.\d+)?"
KV_RE = re.compile(rf"\b([A-Za-z][A-Za-z0-9_]*)=({NUMBER})")
PHASE_COMPACT_RE = re.compile(
    rf"frames=(\d+)/(\d+)ms.*?disp=({NUMBER})ms.*?main=({NUMBER})ms.*?done=({NUMBER})ms",
    re.IGNORECASE,
)
CHUNK_RUNTIME_RE = re.compile(
    r"m14-dev(?:113|114|116|117|118)-chunk(128|256|512)-sim([01])-yold",
    re.IGNORECASE,
)
CHUNK_MARKER_RE = re.compile(
    r"M14 LLVM dev113 chunk(128|256|512)\b", re.IGNORECASE
)
CHUNK_READY_RE = re.compile(r"\bchunk=(128|256|512)\b")
STATE_MEM_RE = re.compile(r"\bstate_mem=([01])\b", re.IGNORECASE)
OBJECTS_RE = re.compile(r"\bobjects=(\d+)\b")


def _median(values: list[float]) -> float | None:
    finite = [value for value in values if math.isfinite(value)]
    return statistics.median(finite) if finite else None


def _mean(values: list[float]) -> float | None:
    finite = [value for value in values if math.isfinite(value)]
    return statistics.fmean(finite) if finite else None


def _kv(line: str) -> dict[str, float]:
    return {key: float(value) for key, value in KV_RE.findall(line)}


def _phase_row(line: str) -> dict[str, float] | None:
    if "main-loop phases" not in line:
        return None
    compact = PHASE_COMPACT_RE.search(line)
    if compact:
        frames, host_ms, disp_ms, main_ms, done_ms = compact.groups()
        return {
            "frames": float(frames),
            "host_ms": float(host_ms),
            "disp_ms": float(disp_ms),
            "main_ms": float(main_ms),
            "done_ms": float(done_ms),
        }

    values = _kv(line)
    if "frames" not in values:
        return None
    row: dict[str, float] = {"frames": values["frames"]}
    if "host_ms" in values:
        row["host_ms"] = values["host_ms"]
    for short, us_key in (
        ("before_ms", "before_avg_us"),
        ("disp_ms", "disp_avg_us"),
        ("main_ms", "main_avg_us"),
        ("done_ms", "done_avg_us"),
    ):
        if us_key in values:
            row[short] = values[us_key] / 1000.0
    if "retraces" in values:
        row["retraces"] = values["retraces"]
    required = ("host_ms", "disp_ms", "main_ms", "done_ms")
    return row if all(key in row for key in required) else None


def parse_text(text: str, source: str = "<memory>") -> dict[str, Any]:
    phases: list[dict[str, float]] = []
    drawdone: list[dict[str, float]] = []
    cadence: list[dict[str, float]] = []
    teardown: list[dict[str, float]] = []
    chunks: list[int] = []
    state_modes: list[int] = []
    object_counts: list[int] = []
    repo_revisions: list[str] = []

    for line in text.splitlines():
        phase = _phase_row(line)
        if phase is not None:
            phases.append(phase)

        if "producer sync window" in line and "kind=draw_done" in line:
            values = _kv(line)
            if values:
                drawdone.append(values)

        if "M14 LLVM dev113 cadence" in line:
            values = _kv(line)
            if values:
                cadence.append(values)

        if "M14 LLVM dev105 dispDraw nested stats" in line:
            values = _kv(line)
            if values:
                teardown.append(values)

        runtime_identity = CHUNK_RUNTIME_RE.search(line)
        if runtime_identity:
            chunks.append(int(runtime_identity.group(1)))
            state_modes.append(int(runtime_identity.group(2)))
        marker_identity = CHUNK_MARKER_RE.search(line)
        if marker_identity:
            chunks.append(int(marker_identity.group(1)))
        if "generation ready" in line or "chunk=" in line:
            match = CHUNK_READY_RE.search(line)
            if match:
                chunks.append(int(match.group(1)))
            state_match = STATE_MEM_RE.search(line)
            if state_match:
                state_modes.append(int(state_match.group(1)))
            objects = OBJECTS_RE.search(line)
            if objects:
                object_counts.append(int(objects.group(1)))

        revision = re.search(r"\brepo(?:_revision)?[=: ]+([0-9a-f]{7,40})\b", line, re.I)
        if revision:
            repo_revisions.append(revision.group(1))

    fps_values = [
        row["frames"] * 1000.0 / row["host_ms"]
        for row in phases
        if row.get("host_ms", 0.0) > 0.0
    ]
    disp_values = [row["disp_ms"] for row in phases if "disp_ms" in row]
    main_values = [row["main_ms"] for row in phases if "main_ms" in row]
    done_values = [row["done_ms"] for row in phases if "done_ms" in row]
    pre_done_values = [
        row["disp_ms"] + row["main_ms"]
        for row in phases
        if "disp_ms" in row and "main_ms" in row
    ]

    drawdone_avg_us = [
        row[key]
        for row in drawdone
        for key in ("avg_us", "avg_frame_us")
        if key in row
    ]
    drawdone_p95_us = [
        row["p95_us"] for row in drawdone if "p95_us" in row
    ]

    nested_ratios = [
        row["nested_steps_per_attempt_x1000"] / 1000.0
        for row in cadence
        if row.get("nested_attempts", 0.0) > 0.0
        and "nested_steps_per_attempt_x1000" in row
    ]
    if not nested_ratios:
        nested_ratios = [
            row["nested_steps"] / row["attempts"]
            for row in teardown
            if row.get("attempts", 0.0) > 0.0 and "nested_steps" in row
        ]

    chunk_set = sorted(set(chunks))
    chunk: int | None = chunk_set[0] if len(chunk_set) == 1 else None
    state_set = sorted(set(state_modes))
    state_mem: int | None = state_set[0] if len(state_set) == 1 else None
    identity_errors: list[str] = []
    if len(chunk_set) > 1:
        identity_errors.append(
            f"conflicting chunk identities in one capture: {chunk_set}"
        )
    if len(state_set) > 1:
        identity_errors.append(
            f"conflicting state-memory identities in one capture: {state_set}"
        )
    identity_error = "; ".join(identity_errors) if identity_errors else None

    disp_ms = _median(disp_values)
    main_ms = _median(main_values)
    pre_done_ms = _median(pre_done_values)
    drawdone_ms = None
    drawdone_avg = _median(drawdone_avg_us)
    if drawdone_avg is not None:
        drawdone_ms = drawdone_avg / 1000.0
    critical_ms = (
        pre_done_ms + drawdone_ms
        if pre_done_ms is not None and drawdone_ms is not None
        else pre_done_ms
    )
    margin_ms = VI_BUDGET_MS - critical_ms if critical_ms is not None else None

    return {
        "source": source,
        "chunk_instructions": chunk,
        "state_in_memory": state_mem,
        "chunk_identity_error": identity_error,
        "objects": object_counts[-1] if object_counts else None,
        "repo_revision": repo_revisions[-1] if repo_revisions else None,
        "phase_windows": len(phases),
        "fps_median": _median(fps_values),
        "fps_mean": _mean(fps_values),
        "disp_ms_median": disp_ms,
        "main_ms_median": main_ms,
        "done_ms_median": _median(done_values),
        "disp_plus_main_ms_median": pre_done_ms,
        "drawdone_ms_median": drawdone_ms,
        "drawdone_p95_ms_median": (
            _median(drawdone_p95_us) / 1000.0 if drawdone_p95_us else None
        ),
        "first_retrace_critical_ms": critical_ms,
        "first_retrace_margin_ms": margin_ms,
        "nested_steps_per_attempt_median": _median(nested_ratios),
        "cadence_windows": len(cadence),
        "teardown_samples": len(teardown),
    }


def parse_file(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    result = parse_text(
        raw.decode("utf-8", errors="replace"),
        source=str(path),
    )
    manifest_path = path.with_suffix(".json")
    result["manifest_path"] = str(manifest_path) if manifest_path.is_file() else None
    result["manifest_valid"] = None
    result["manifest_error"] = None
    if not manifest_path.is_file():
        return result

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != 1:
            raise ValueError(
                f"unsupported manifest schema {manifest.get('schema_version')!r}"
            )
        if manifest.get("capture_file") != path.name:
            raise ValueError(
                "manifest capture_file does not match log filename"
            )
        expected_sha = str(manifest.get("capture_sha256") or "").lower()
        actual_sha = hashlib.sha256(raw).hexdigest()
        if expected_sha != actual_sha:
            raise ValueError(
                f"capture SHA-256 mismatch: manifest={expected_sha or '<missing>'} "
                f"actual={actual_sha}"
            )
        manifest_repo = manifest.get("repo_revision")
        if (
            result.get("repo_revision") is None
            and isinstance(manifest_repo, str)
            and re.fullmatch(r"[0-9a-fA-F]{40}", manifest_repo)
        ):
            result["repo_revision"] = manifest_repo
        result["manifest_valid"] = True
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        result["manifest_valid"] = False
        result["manifest_error"] = str(exc)
    return result


def matching_captures(pattern: str) -> list[Path]:
    if not DEFAULT_CAPTURE_DIR.is_dir():
        raise OSError(f"capture directory does not exist: {DEFAULT_CAPTURE_DIR}")
    candidates = [
        path for path in DEFAULT_CAPTURE_DIR.glob("*.log")
        if pattern.lower() in path.name.lower()
    ]
    return sorted(
        candidates,
        key=lambda path: (path.stat().st_mtime_ns, path.name),
        reverse=True,
    )


def latest_capture(pattern: str) -> Path:
    candidates = matching_captures(pattern)
    if not candidates:
        raise OSError(
            f"no debug capture matching {pattern!r} in {DEFAULT_CAPTURE_DIR}"
        )
    return candidates[0]


def latest_capture_for_config(
    *,
    chunk: int | None = None,
    state_mem: int | None = None,
    pattern: str = "yold-perf",
) -> tuple[Path, dict[str, Any]]:
    for path in matching_captures(pattern):
        parsed = parse_file(path)
        if parsed.get("chunk_identity_error"):
            continue
        # Automatic pairing promises the newest *valid* capture. Historical
        # logs without a sidecar remain eligible, but a present sidecar that
        # fails its filename/SHA contract is skipped rather than poisoning the
        # A/B when an older valid capture exists.
        if parsed.get("manifest_valid") is False:
            continue
        if chunk is not None and parsed.get("chunk_instructions") != chunk:
            continue
        if state_mem is not None and parsed.get("state_in_memory") != state_mem:
            continue
        return path, parsed
    requested = []
    if chunk is not None:
        requested.append(f"chunk={chunk}")
    if state_mem is not None:
        requested.append(f"state_mem={state_mem}")
    raise OSError(
        f"no {pattern!r} capture self-identifies as {' '.join(requested)} "
        f"in {DEFAULT_CAPTURE_DIR}"
    )


def latest_capture_for_chunk(
    chunk: int, pattern: str = "yold-perf", *, state_mem: int | None = None
) -> tuple[Path, dict[str, Any]]:
    return latest_capture_for_config(
        chunk=chunk, state_mem=state_mem, pattern=pattern
    )


def _fmt(value: float | int | None, digits: int = 3) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, int):
        return str(value)
    return f"{value:.{digits}f}"


def render_summary(result: dict[str, Any]) -> str:
    lines = [f"OpenSPM performance summary: {result['source']}"]
    identity = (
        f"chunk={result['chunk_instructions']}"
        if result["chunk_instructions"] is not None
        else "chunk=unknown"
    )
    if result.get("state_in_memory") is not None:
        identity += f" state_mem={result['state_in_memory']}"
    if result.get("objects") is not None:
        identity += f" objects={result['objects']}"
    if result.get("repo_revision"):
        identity += f" repo={result['repo_revision']}"
    lines.append(identity)
    if result.get("chunk_identity_error"):
        lines.append("WARNING: " + result["chunk_identity_error"])
    if result.get("manifest_valid") is True:
        lines.append("capture manifest: verified")
    elif result.get("manifest_valid") is False:
        lines.append(
            "WARNING: capture manifest invalid: "
            + str(result.get("manifest_error") or "unknown error")
        )

    lines.append(
        "cadence: "
        f"median={_fmt(result['fps_median'], 2)} fps "
        f"mean={_fmt(result['fps_mean'], 2)} fps "
        f"windows={result['phase_windows']}"
    )
    lines.append(
        "phases: "
        f"disp={_fmt(result['disp_ms_median'])} ms "
        f"main={_fmt(result['main_ms_median'])} ms "
        f"disp+main={_fmt(result['disp_plus_main_ms_median'])} ms "
        f"done={_fmt(result['done_ms_median'])} ms"
    )
    lines.append(
        "renderer: "
        f"draw_done={_fmt(result['drawdone_ms_median'])} ms "
        f"p95={_fmt(result['drawdone_p95_ms_median'])} ms"
    )
    lines.append(
        "first-retrace model: "
        f"critical={_fmt(result['first_retrace_critical_ms'])} ms "
        f"budget={VI_BUDGET_MS:.3f} ms "
        f"margin={_fmt(result['first_retrace_margin_ms'])} ms"
    )
    margin = result.get("first_retrace_margin_ms")
    if margin is not None:
        if margin < 0:
            lines.append(
                "deadline: MISSED first VI by "
                f"{abs(margin):.3f} ms median; 30 Hz quantization is plausible"
            )
        else:
            lines.append(
                "deadline: first VI has "
                f"{margin:.3f} ms median headroom"
            )
    lines.append(
        "dev105: "
        f"nested_steps/attempt={_fmt(result['nested_steps_per_attempt_median'], 2)} "
        f"live_windows={result['cadence_windows']} teardown={result['teardown_samples']}"
    )
    return "\n".join(lines)


def _delta(candidate: dict[str, Any], baseline: dict[str, Any], key: str) -> float | None:
    left = candidate.get(key)
    right = baseline.get(key)
    if left is None or right is None:
        return None
    return float(left) - float(right)


def render_compare(candidate: dict[str, Any], baseline: dict[str, Any]) -> str:
    lines = [
        "OpenSPM performance A/B",
        "baseline: "
        f"{baseline['source']} (chunk={baseline.get('chunk_instructions')}, "
        f"state_mem={baseline.get('state_in_memory')})",
        "candidate: "
        f"{candidate['source']} (chunk={candidate.get('chunk_instructions')}, "
        f"state_mem={candidate.get('state_in_memory')})",
    ]
    rows = (
        ("median fps", "fps_median", "fps", 2),
        ("disp", "disp_ms_median", "ms", 3),
        ("main", "main_ms_median", "ms", 3),
        ("disp+main", "disp_plus_main_ms_median", "ms", 3),
        ("draw_done", "drawdone_ms_median", "ms", 3),
        ("critical", "first_retrace_critical_ms", "ms", 3),
        ("VI margin", "first_retrace_margin_ms", "ms", 3),
        ("nested steps/attempt", "nested_steps_per_attempt_median", "", 2),
    )
    for label, key, unit, digits in rows:
        base = baseline.get(key)
        cand = candidate.get(key)
        change = _delta(candidate, baseline, key)
        suffix = f" {unit}" if unit else ""
        lines.append(
            f"{label}: {_fmt(base, digits)} -> {_fmt(cand, digits)}{suffix} "
            f"(delta {_fmt(change, digits)}{suffix})"
        )

    fps_delta = _delta(candidate, baseline, "fps_median")
    critical_delta = _delta(candidate, baseline, "first_retrace_critical_ms")
    nested_delta = _delta(candidate, baseline, "nested_steps_per_attempt_median")
    if fps_delta is not None and critical_delta is not None:
        if fps_delta >= 10.0 and critical_delta < -0.5:
            lines.append("signal: strong cadence recovery with lower critical-path time")
        elif critical_delta < -0.5:
            lines.append("signal: critical path improved; check whether it crosses the 16.667 ms cliff")
        elif critical_delta > 0.5:
            lines.append("signal: candidate regressed measured critical-path time")
        else:
            lines.append("signal: no large critical-path movement")
    if nested_delta is not None:
        lines.append(
            "chunk-walk signal: "
            + ("improved" if nested_delta < -0.5 else "little change" if abs(nested_delta) <= 0.5 else "regressed")
        )
    return "\n".join(lines)


def quality_issues(
    result: dict[str, Any],
    *,
    min_phase_windows: int = DEFAULT_MIN_PHASE_WINDOWS,
    min_cadence_windows: int = DEFAULT_MIN_CADENCE_WINDOWS,
    require_drawdone: bool = False,
) -> list[str]:
    issues: list[str] = []
    if result.get("chunk_identity_error"):
        issues.append(str(result["chunk_identity_error"]))
    if result.get("manifest_valid") is False:
        issues.append(
            "invalid capture manifest: "
            + str(result.get("manifest_error") or "unknown manifest error")
        )
    if result.get("chunk_instructions") is None:
        issues.append("missing chunk_instructions identity")
    if result.get("state_in_memory") is None:
        issues.append("missing state_in_memory identity")
    if int(result.get("phase_windows") or 0) < min_phase_windows:
        issues.append(
            f"phase_windows={int(result.get('phase_windows') or 0)} "
            f"< required {min_phase_windows}"
        )
    if int(result.get("cadence_windows") or 0) < min_cadence_windows:
        issues.append(
            f"cadence_windows={int(result.get('cadence_windows') or 0)} "
            f"< required {min_cadence_windows}"
        )
    for key in (
        "fps_median",
        "disp_ms_median",
        "main_ms_median",
        "disp_plus_main_ms_median",
        "nested_steps_per_attempt_median",
    ):
        if result.get(key) is None:
            issues.append(f"missing metric {key}")
    if require_drawdone:
        for key in (
            "drawdone_ms_median",
            "first_retrace_critical_ms",
            "first_retrace_margin_ms",
        ):
            if result.get(key) is None:
                issues.append(f"missing metric {key}")
    return issues


def render_quality(
    result: dict[str, Any],
    *,
    min_phase_windows: int = DEFAULT_MIN_PHASE_WINDOWS,
    min_cadence_windows: int = DEFAULT_MIN_CADENCE_WINDOWS,
    require_drawdone: bool = False,
) -> str:
    issues = quality_issues(
        result,
        min_phase_windows=min_phase_windows,
        min_cadence_windows=min_cadence_windows,
        require_drawdone=require_drawdone,
    )
    if not issues:
        return (
            "benchmark quality: PASS "
            f"(phase_windows>={min_phase_windows}, "
            f"cadence_windows>={min_cadence_windows})"
        )
    return "benchmark quality: INVALID - " + "; ".join(issues)


def regression_failures(
    candidate: dict[str, Any],
    baseline: dict[str, Any],
    *,
    min_phase_windows: int = DEFAULT_MIN_PHASE_WINDOWS,
    min_cadence_windows: int = DEFAULT_MIN_CADENCE_WINDOWS,
    max_fps_regression: float = DEFAULT_MAX_FPS_REGRESSION,
    max_critical_regression_ms: float = DEFAULT_MAX_CRITICAL_REGRESSION_MS,
    max_drawdone_regression_ms: float = DEFAULT_MAX_DRAWDONE_REGRESSION_MS,
) -> list[str]:
    failures: list[str] = []
    for label, result in (("baseline", baseline), ("candidate", candidate)):
        issues = quality_issues(
            result,
            min_phase_windows=min_phase_windows,
            min_cadence_windows=min_cadence_windows,
            require_drawdone=True,
        )
        failures.extend(f"{label}: {issue}" for issue in issues)
    if failures:
        return failures

    fps_delta = _delta(candidate, baseline, "fps_median")
    critical_delta = _delta(candidate, baseline, "first_retrace_critical_ms")
    drawdone_delta = _delta(candidate, baseline, "drawdone_ms_median")

    if fps_delta is not None and fps_delta < -max_fps_regression:
        failures.append(
            f"median fps regressed by {abs(fps_delta):.2f} "
            f"(allowed {max_fps_regression:.2f})"
        )
    if (
        critical_delta is not None
        and critical_delta > max_critical_regression_ms
    ):
        failures.append(
            f"first-retrace critical path regressed by {critical_delta:.3f} ms "
            f"(allowed {max_critical_regression_ms:.3f} ms)"
        )
    if (
        drawdone_delta is not None
        and drawdone_delta > max_drawdone_regression_ms
    ):
        failures.append(
            f"DrawDone regressed by {drawdone_delta:.3f} ms "
            f"(allowed {max_drawdone_regression_ms:.3f} ms)"
        )
    return failures


def self_test() -> None:
    sample = """
M14 OpenSPM LLVM generation ready: objects=15001 generated=x dispatch=indexed
M14 main-loop phases frames=30/1000ms disp=7.200ms main=7.900ms done=18.100ms
M14 main-loop phases frames=31/1000ms disp=7.000ms main=8.100ms done=17.900ms
M14 producer sync window kind=draw_done calls=30 avg_us=2300 p95_us=2700
M14 LLVM dev113 cadence chunk=256 state_mem=0 host_ms=1000 nested_attempts=370 nested_steps=5180 nested_steps_per_attempt_x1000=14000 pc=0x80000000.
"""
    result = parse_text(sample)
    assert result["chunk_instructions"] == 256
    assert result["state_in_memory"] == 0
    dev118_identity = parse_text(
        "module=m14-dev118-chunk512-sim1-yold-normal\n"
    )
    assert dev118_identity["chunk_instructions"] == 512
    assert dev118_identity["state_in_memory"] == 1
    assert result["objects"] == 15001
    assert result["phase_windows"] == 2
    assert 30.4 < result["fps_median"] < 30.6
    assert abs(result["disp_plus_main_ms_median"] - 15.1) < 0.001
    assert abs(result["drawdone_ms_median"] - 2.3) < 0.001
    assert result["first_retrace_margin_ms"] < 0
    assert abs(result["nested_steps_per_attempt_median"] - 14.0) < 0.001
    assert quality_issues(
        result, min_phase_windows=2, min_cadence_windows=1,
        require_drawdone=True,
    ) == []

    regressed = dict(result)
    regressed["fps_median"] = float(result["fps_median"]) - 4.0
    regressed["first_retrace_critical_ms"] = (
        float(result["first_retrace_critical_ms"]) + 1.0
    )
    regressed["drawdone_ms_median"] = float(result["drawdone_ms_median"]) + 1.0
    failures = regression_failures(
        regressed,
        result,
        min_phase_windows=2,
        min_cadence_windows=1,
    )
    assert any("median fps regressed" in failure for failure in failures)
    assert any("critical path regressed" in failure for failure in failures)
    assert any("DrawDone regressed" in failure for failure in failures)

    older = """
M14 main-loop phases host_ms=1000 frames=60 before_avg_us=200 disp_avg_us=4000 main_avg_us=7000 done_avg_us=5500 retraces=60
M14 LLVM dev105 dispDraw nested stats attempts=100 handled=100 normal_returns=90 abnormal_returns=10 depth_fallbacks=0 lookup_misses=0 budget_yields=0 nested_steps=1200 step_limit_yields=0 no_progress_yields=0.
"""
    result = parse_text(older)
    assert result["phase_windows"] == 1
    assert abs(result["fps_median"] - 60.0) < 0.001
    assert abs(result["nested_steps_per_attempt_median"] - 12.0) < 0.001


    global DEFAULT_CAPTURE_DIR
    original_capture_dir = DEFAULT_CAPTURE_DIR
    with tempfile.TemporaryDirectory(prefix="openspm-perf-analyzer-") as temp_name:
        DEFAULT_CAPTURE_DIR = Path(temp_name)
        (DEFAULT_CAPTURE_DIR / "20260920-000001-yold-perf.log").write_text(
            "M14 LLVM dev113 cadence chunk=128 state_mem=0 host_ms=1000 "
            "nested_attempts=100 nested_steps=1900 "
            "nested_steps_per_attempt_x1000=19000 pc=0x80000000.\n",
            encoding="utf-8",
        )
        (DEFAULT_CAPTURE_DIR / "20260920-000002-yold-perf.log").write_text(
            "M14 LLVM dev113 cadence chunk=256 state_mem=1 host_ms=1000 "
            "nested_attempts=100 nested_steps=1100 "
            "nested_steps_per_attempt_x1000=11000 pc=0x80000000.\n",
            encoding="utf-8",
        )
        path128, parsed128 = latest_capture_for_chunk(128, state_mem=0)
        path256, parsed256 = latest_capture_for_chunk(256, state_mem=1)
        assert path128.name.startswith("20260920-000001")
        assert path256.name.startswith("20260920-000002")
        assert parsed128["chunk_instructions"] == 128
        assert parsed128["state_in_memory"] == 0
        assert parsed256["chunk_instructions"] == 256
        assert parsed256["state_in_memory"] == 1

        manifest_log = DEFAULT_CAPTURE_DIR / "manifest-yold-perf.log"
        manifest_log.write_text(
            "M14 LLVM dev113 cadence chunk=256 state_mem=0 host_ms=1000 "
            "nested_attempts=100 nested_steps=1200 "
            "nested_steps_per_attempt_x1000=12000 pc=0x80000000.\n",
            encoding="utf-8",
        )
        manifest_sha = hashlib.sha256(manifest_log.read_bytes()).hexdigest()
        manifest_log.with_suffix(".json").write_text(
            json.dumps({
                "schema_version": 1,
                "capture_file": manifest_log.name,
                "capture_sha256": manifest_sha,
                "repo_revision": "1" * 40,
            }),
            encoding="utf-8",
        )
        manifest_result = parse_file(manifest_log)
        assert manifest_result["manifest_valid"] is True
        assert manifest_result["repo_revision"] == "1" * 40
        manifest_log.write_text("tampered\n", encoding="utf-8")
        tampered = parse_file(manifest_log)
        assert tampered["manifest_valid"] is False
        assert "SHA-256 mismatch" in str(tampered["manifest_error"])

        # A newer corrupt capture must not shadow an older valid capture during
        # automatic configuration pairing.
        corrupt_newest = DEFAULT_CAPTURE_DIR / "99999999-999999-yold-perf.log"
        corrupt_newest.write_text(
            "M14 LLVM dev113 cadence chunk=128 state_mem=0 host_ms=1000 "
            "nested_attempts=100 nested_steps=999 "
            "nested_steps_per_attempt_x1000=9990 pc=0x80000000.\n",
            encoding="utf-8",
        )
        corrupt_newest.with_suffix(".json").write_text(
            json.dumps({
                "schema_version": 1,
                "capture_file": corrupt_newest.name,
                "capture_sha256": "0" * 64,
            }),
            encoding="utf-8",
        )
        selected_path, selected = latest_capture_for_config(
            chunk=128, state_mem=0
        )
        assert selected_path.name.startswith("20260920-000001")
        assert selected["manifest_valid"] is None
    DEFAULT_CAPTURE_DIR = original_capture_dir


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", nargs="?", type=Path, default=None)
    parser.add_argument(
        "--latest-capture",
        metavar="TEXT",
        help="analyze the newest debug-capture filename containing TEXT",
    )
    parser.add_argument(
        "--compare-latest-chunks",
        action="store_true",
        help="compare newest self-identified Yold chunk128 and chunk256 captures with state_mem=0",
    )
    parser.add_argument(
        "--compare-latest-state-memory",
        action="store_true",
        help="compare newest chunk256 Yold state_mem=0 and state_mem=1 captures",
    )
    parser.add_argument("--baseline", type=Path, help="optional baseline capture for A/B delta")
    parser.add_argument(
        "--quality-gate",
        action="store_true",
        help="fail with exit code 3 if the selected capture lacks canonical benchmark-quality data",
    )
    parser.add_argument(
        "--regression-gate",
        action="store_true",
        help="with a baseline/A-B mode, fail with exit code 4 on a material regression",
    )
    parser.add_argument(
        "--min-phase-windows",
        type=int,
        default=DEFAULT_MIN_PHASE_WINDOWS,
    )
    parser.add_argument(
        "--min-cadence-windows",
        type=int,
        default=DEFAULT_MIN_CADENCE_WINDOWS,
    )
    parser.add_argument(
        "--max-fps-regression",
        type=float,
        default=DEFAULT_MAX_FPS_REGRESSION,
    )
    parser.add_argument(
        "--max-critical-regression-ms",
        type=float,
        default=DEFAULT_MAX_CRITICAL_REGRESSION_MS,
    )
    parser.add_argument(
        "--max-drawdone-regression-ms",
        type=float,
        default=DEFAULT_MAX_DRAWDONE_REGRESSION_MS,
    )
    parser.add_argument("--json", action="store_true", help="emit structured JSON")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.min_phase_windows < 1 or args.min_cadence_windows < 1:
        parser.error("minimum window counts must be >= 1")
    for name in (
        "max_fps_regression",
        "max_critical_regression_ms",
        "max_drawdone_regression_ms",
    ):
        if getattr(args, name) < 0:
            parser.error(f"--{name.replace('_', '-')} must be >= 0")

    if args.self_test:
        self_test()
        print("M14 performance analyzer self-test passed")
        return 0

    try:
        if args.compare_latest_chunks or args.compare_latest_state_memory:
            if (
                args.compare_latest_chunks and args.compare_latest_state_memory
            ) or args.log is not None or args.latest_capture or args.baseline is not None:
                raise OSError(
                    "automatic compare modes are mutually exclusive and cannot "
                    "be combined with a log path, --latest-capture, or --baseline"
                )
            if args.compare_latest_chunks:
                baseline_path, baseline = latest_capture_for_config(
                    chunk=128, state_mem=0
                )
                candidate_path, candidate = latest_capture_for_config(
                    chunk=256, state_mem=0
                )
            else:
                baseline_path, baseline = latest_capture_for_config(
                    chunk=256, state_mem=0
                )
                candidate_path, candidate = latest_capture_for_config(
                    chunk=256, state_mem=1
                )
        else:
            if args.log is not None and args.latest_capture:
                raise OSError("use either a log path or --latest-capture, not both")
            log_path = (
                latest_capture(args.latest_capture)
                if args.latest_capture
                else (args.log or DEFAULT_LOG)
            )
            candidate = parse_file(log_path)
            baseline = parse_file(args.baseline) if args.baseline else None
    except OSError as exc:
        print(f"error: could not read performance log: {exc}", file=sys.stderr)
        return 2

    quality = quality_issues(
        candidate,
        min_phase_windows=args.min_phase_windows,
        min_cadence_windows=args.min_cadence_windows,
        require_drawdone=args.quality_gate or args.regression_gate,
    )
    regression: list[str] = []
    if args.regression_gate:
        if baseline is None:
            print(
                "error: --regression-gate requires --baseline or an automatic A/B compare mode",
                file=sys.stderr,
            )
            return 2
        regression = regression_failures(
            candidate,
            baseline,
            min_phase_windows=args.min_phase_windows,
            min_cadence_windows=args.min_cadence_windows,
            max_fps_regression=args.max_fps_regression,
            max_critical_regression_ms=args.max_critical_regression_ms,
            max_drawdone_regression_ms=args.max_drawdone_regression_ms,
        )

    if args.json:
        payload: dict[str, Any] = {
            "candidate": candidate,
            "quality_issues": quality,
        }
        if baseline is not None:
            payload["baseline"] = baseline
        if args.regression_gate:
            payload["regression_failures"] = regression
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(render_summary(candidate))
        print(
            render_quality(
                candidate,
                min_phase_windows=args.min_phase_windows,
                min_cadence_windows=args.min_cadence_windows,
                require_drawdone=args.quality_gate or args.regression_gate,
            )
        )
        if baseline is not None:
            print()
            print(render_compare(candidate, baseline))
        if args.regression_gate:
            print()
            if regression:
                print("regression gate: FAIL")
                for failure in regression:
                    print(f"  - {failure}")
            else:
                print("regression gate: PASS")

    if args.quality_gate and quality:
        return 3
    if args.regression_gate and regression:
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
