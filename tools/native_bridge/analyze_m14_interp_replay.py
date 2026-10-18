#!/usr/bin/env python3
"""Summarize OpenSPM M14 dev140 interpolation replay captures."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import statistics
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOG = ROOT / ".openspm" / "m14" / "home-test" / "launch.log"

NUMBER = r"-?\d+(?:\.\d+)?"
KV_RE = re.compile(rf"\b([A-Za-z][A-Za-z0-9_]*)=({NUMBER})")
PHASE_RE = re.compile(
    rf"frames=(\d+)/(\d+)ms.*?disp=({NUMBER})ms.*?main=({NUMBER})ms.*?done=({NUMBER})ms",
    re.IGNORECASE,
)


def kv(line: str) -> dict[str, float]:
    return {key: float(value) for key, value in KV_RE.findall(line)}


def parse_text(text: str, source: str = "<memory>") -> dict[str, Any]:
    prep: list[dict[str, float]] = []
    prep_skip: list[dict[str, float]] = []
    encode: list[dict[str, float]] = []
    phase_fps: list[float] = []
    presenter_summary: dict[str, float] | None = None
    visible: dict[str, float] = {}
    selected: dict[str, float] = {}
    active_prep = False
    active_encode = False
    replay_cadence: dict[str, float] | None = None
    renderer_size: tuple[int, int] | None = None

    for line in text.splitlines():
        if "M14 visible interpolation frames=" in line: visible = kv(line)
        if "M14 interpolated image selected=" in line: selected = kv(line)
        if ("M14 dev140 replay prep active:" in line or
                "M14 dev145 replay prep active:" in line):
            active_prep = True
        if "M14 dev140 replay cadence " in line:
            values = kv(line)
            if values:
                replay_cadence = values
        if "M14 dev140b offscreen replay encode active:" in line:
            active_encode = True

        if ("M14 dev140 replay prep interval=" in line or
                "M14 dev143 camera prep interval=" in line or
                "M14 dev145 world-camera replay interval=" in line):
            values = kv(line)
            if values:
                prep.append(values)
        elif "M14 dev140 replay prep skip interval=" in line:
            values = kv(line)
            if values:
                prep_skip.append(values)
        elif "M14 dev140b replay encode frame=" in line:
            values = kv(line)
            if values:
                encode.append(values)

        if "M14 dev139 presenter summary" in line:
            values = kv(line)
            if values:
                presenter_summary = values

        if "main-loop phases" in line:
            values = kv(line)
            if values.get("host_ms", 0) > 0 and "frames" in values:
                phase_fps.append(values["frames"] * 1000.0 / values["host_ms"])
            else:
                # Older human-readable logs used frames=N/Tms.
                match = PHASE_RE.search(line)
                if match:
                    frames, host_ms, *_ = match.groups()
                    host = float(host_ms)
                    if host > 0:
                        phase_fps.append(float(frames) * 1000.0 / host)

        size_match = re.search(
            r"Using framebuffer size\s+(\d+)x(\d+)", line, re.IGNORECASE
        )
        if size_match:
            renderer_size = (int(size_match.group(1)), int(size_match.group(2)))

    latest_prep = prep[-1] if prep else {}
    latest_encode = encode[-1] if encode else {}

    observed_prep = prep + prep_skip
    matched_uniforms = sum(row.get("matched_uniforms", 0.0) for row in prep)
    snapped_uniforms = sum(row.get("snapped_uniforms", 0.0) for row in prep)
    matched_draws = sum(row.get("matched_draws", 0.0) for row in observed_prep)
    unmatched_draws = sum(row.get("unmatched_draws", 0.0) for row in observed_prep)
    indexed_snaps = sum(row.get("indexed_snap", 0.0) for row in observed_prep)
    snapshot_rejects = sum(row.get("snapshot_reject", 0.0) for row in observed_prep)
    identity_misses = sum(row.get("identity_miss", 0.0) for row in observed_prep)
    discontinuity_snaps = sum(row.get("discontinuity_snap", 0.0) for row in observed_prep)
    duplicate_exhausted = sum(row.get("duplicate_exhausted", 0.0) for row in observed_prep)
    ambiguous_snaps = sum(row.get("ambiguous_snap", 0.0) for row in observed_prep)
    match_den = matched_draws + unmatched_draws
    match_pct = (
        matched_draws * 100.0 / match_den
        if match_den > 0
        else None
    )

    phase_rows = [
        int(row["phases"])
        for row in prep
        if "phases" in row
    ]
    max_staged = max(
        (row.get("staged_uniform_bytes", 0.0) for row in prep),
        default=0.0,
    )
    max_original = max(
        (row.get("original_uniform_bytes", 0.0) for row in prep),
        default=0.0,
    )

    encoded = sum(row.get("encoded", 0.0) for row in encode)
    requested = sum(row.get("requested_phases", 0.0) for row in encode)
    encode_pct = (encoded * 100.0 / requested) if requested > 0 else None
    rejected = sum(row.get("rejected", 0.0) for row in encode)
    rejection_breakdown = {
        key: int(sum(row.get(key, 0.0) for row in encode))
        for key in (
            "reject_layout",
            "reject_prior_load",
            "reject_custom",
            "reject_range",
        )
    }

    producer_median = statistics.median(phase_fps) if phase_fps else None
    producer_min = min(phase_fps) if phase_fps else None

    no_match_total = int(max(
        [row.get("no_match_total", 0.0) for row in prep] +
        [row.get("no_match_total", 0.0) for row in prep_skip] +
        [0.0]
    ))
    arena_exhausted_total = int(
        max([row.get("arena_exhausted_total", 0.0) for row in prep] + [0.0])
    )
    prepared_total = int(latest_prep.get("prepared_total", len(prep)))

    return {
        "source": source,
        "visible": visible,
        "selected": selected,
        "active_prep": active_prep,
        "active_encode": active_encode,
        "replay_cadence": replay_cadence,
        "prep_samples": len(prep),
        "prep_skip_samples": len(prep_skip),
        "prepared_total": prepared_total,
        "no_match_total": no_match_total,
        "arena_exhausted_total": arena_exhausted_total,
        "match_pct": match_pct,
        "matched_draws": int(matched_draws),
        "unmatched_draws": int(unmatched_draws),
        "matched_uniforms": int(matched_uniforms),
        "snapped_uniforms": int(snapped_uniforms),
        "indexed_snaps": int(indexed_snaps),
        "snapshot_rejects": int(snapshot_rejects),
        "identity_misses": int(identity_misses),
        "discontinuity_snaps": int(discontinuity_snaps),
        "duplicate_exhausted": int(duplicate_exhausted),
        "ambiguous_snaps": int(ambiguous_snaps),
        "phase_rows": phase_rows,
        "max_original_uniform_bytes": int(max_original),
        "max_staged_uniform_bytes": int(max_staged),
        "encode_samples": len(encode),
        "encode_pct": encode_pct,
        "rejected": int(rejected),
        "rejection_breakdown": rejection_breakdown,
        "producer_windows": len(phase_fps),
        "producer_slow_windows": sum(fps < 55.0 for fps in phase_fps),
        "prep_cost_samples": sum("prep_us" in row for row in prep),
        "prep_max_ms": max((row["prep_us"] / 1000.0 for row in prep if "prep_us" in row), default=None),
        "producer_median_fps": producer_median,
        "producer_min_fps": producer_min,
        "presenter_summary": presenter_summary,
        "renderer_size": renderer_size,
        "latest_prep": latest_prep,
        "latest_encode": latest_encode,
    }


def fmt(value: float | None, digits: int = 1) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def verdict(result: dict[str, Any]) -> tuple[str, list[str]]:
    problems: list[str] = []
    if not result["active_prep"]:
        problems.append("dev140a startup marker missing")
    if result["arena_exhausted_total"] > 0:
        problems.append(
            f"uniform arena exhausted {result['arena_exhausted_total']} time(s)"
        )
    if result["match_pct"] is not None and result["match_pct"] < 90.0:
        problems.append(
            f"observed matched-vs-snapped coverage only {result['match_pct']:.1f}%"
        )
    if (
        result["producer_median_fps"] is not None
        and result["producer_median_fps"] < 55.0
    ):
        problems.append(
            f"producer median fell to {result['producer_median_fps']:.1f} fps"
        )

    if result["producer_windows"] and result["producer_slow_windows"] >= 3:
        problems.append(
            f"{result['producer_slow_windows']}/{result['producer_windows']} producer windows below 55 fps"
        )

    if result["active_encode"]:
        if result["rejected"] > 0:
            problems.append(f"offscreen replay rejected {result['rejected']} phase(s)")
        if result["encode_pct"] is not None and result["encode_pct"] < 95.0:
            problems.append(
                f"only {result['encode_pct']:.1f}% of requested replay phases encoded"
            )

    if result["visible"] and result["visible"].get("accepted", 0) == 0:
        problems.append("visible replay has only fallen back; no interpolated scene was accepted")
    if problems:
        return "INVESTIGATE", problems
    if result["producer_median_fps"] is None:
        return "INSUFFICIENT DATA", ["producer timing missing; replay counters cannot establish performance"]
    if result["active_encode"] and not result["encode_samples"]:
        return "INSUFFICIENT DATA", ["offscreen encode samples missing"]
    if result["active_encode"] and result["encode_samples"]:
        return "DEV140B COUNTERS PASS; VISUAL VALIDATION REQUIRED", []
    if result["active_prep"] and result["prep_samples"]:
        return "DEV140A COUNTERS PASS; VISUAL VALIDATION REQUIRED", []
    return "INSUFFICIENT DATA", []


def print_report(result: dict[str, Any]) -> None:
    status, problems = verdict(result)
    print("OpenSPM M14 interpolation replay report")
    print(f"  source: {result['source']}")
    print(f"  verdict: {status}")
    if result["renderer_size"] is not None:
        w, h = result["renderer_size"]
        print(f"  Aurora render framebuffer: {w}x{h}")

    if result["replay_cadence"] is not None:
        cadence = result["replay_cadence"]
        print(
            "  replay cadence:"
            f" requested={fmt(cadence.get('requested_hz'))} Hz"
            f" effective={fmt(cadence.get('effective_hz'), 3)} Hz"
        )

    print(
        "  dev140a:"
        f" samples={result['prep_samples']}"
        f" prepared_total={result['prepared_total']}"
        f" no_match_total={result['no_match_total']}"
        f" arena_exhausted={result['arena_exhausted_total']}"
        f" draw_match={fmt(result['match_pct'])}%"
        f" matched_draws={result['matched_draws']}"
        f" unmatched_draws={result['unmatched_draws']}"
        f" indexed_snap={result['indexed_snaps']}"
        f" snapshot_reject={result['snapshot_rejects']}"
        f" identity_miss={result['identity_misses']}"
        f" discontinuity={result['discontinuity_snaps']}"
        f" duplicate_exhausted={result['duplicate_exhausted']}"
        f" ambiguous={result['ambiguous_snaps']}"
    )
    if result["phase_rows"]:
        preview = ",".join(str(value) for value in result["phase_rows"][:12])
        print(f"  observed prepared phase counts: {preview}")
    print(
        "  uniforms:"
        f" matched_unique={result['matched_uniforms']}"
        f" snapped_unique={result['snapped_uniforms']}"
        f" max_original={result['max_original_uniform_bytes']}"
        f" max_staged={result['max_staged_uniform_bytes']} bytes"
    )
    camera = result["latest_prep"]
    if "camera_valid" in camera:
        print(
            "  dev143 camera sensor:"
            f" valid={int(camera.get('camera_valid', 0))}"
            f" samples={int(camera.get('camera_samples', 0))}"
            f" consensus={int(camera.get('camera_consensus', 0))}"
            f" delta=({fmt(camera.get('camera_dx'), 3)},"
            f"{fmt(camera.get('camera_dy'), 3)},"
            f"{fmt(camera.get('camera_dz'), 3)})"
            f" screen_valid={int(camera.get('screen_valid', 0))}"
            f" ndc=({fmt(camera.get('camera_ndc_dx'), 5)},"
            f"{fmt(camera.get('camera_ndc_dy'), 5)})"
        )
    elif "camera_coherent" in camera:
        print(
            "  camera completion:"
            f" coherent={int(camera.get('camera_coherent', 0))}"
            f" patched_unique={int(camera.get('camera_patched', 0))}"
            f" original_snapped={int(camera.get('camera_original_snapped', 0))}"
            f" samples={int(camera.get('camera_samples', 0))}"
            f" consensus={int(camera.get('camera_consensus', 0))}"
            f" delta=({fmt(camera.get('camera_dx'), 3)},"
            f"{fmt(camera.get('camera_dy'), 3)},"
            f"{fmt(camera.get('camera_dz'), 3)})"
        )

    if result["active_encode"] or result["encode_samples"]:
        breakdown = result["rejection_breakdown"]
        print(
            "  dev140b:"
            f" samples={result['encode_samples']}"
            f" encoded={fmt(result['encode_pct'])}%"
            f" rejected={result['rejected']}"
            f" [layout={breakdown['reject_layout']}"
            f" prior_load={breakdown['reject_prior_load']}"
            f" custom={breakdown['reject_custom']}"
            f" range={breakdown['reject_range']}]"
        )

    print(
        "  producer:"
        f" median={fmt(result['producer_median_fps'])} fps"
        f" min_window={fmt(result['producer_min_fps'])} fps"
        f" below55={result['producer_slow_windows']}/{result['producer_windows']} windows"
    )

    print(
        f"  preparation: sampled_max={fmt(result['prep_max_ms'], 3)} ms"
        f" timing_samples={result['prep_cost_samples']} (sparse samples, not a percentile)"
    )
    if result["visible"]:
        v, chosen = result["visible"], result["selected"]
        print(f"  visible replay: accepted={int(v.get('accepted', 0))} fallback={int(v.get('fallback', 0))} "
              f"selected={int(chosen.get('selected', 0))} fractional={int(chosen.get('fractional', 0))}")
        if "partial_fallback" in v or "cadence_fallback" in v:
            print(
                "  dev141 guards:"
                f" partial_transform={int(v.get('partial_fallback', 0))}"
                f" source_cadence={int(v.get('cadence_fallback', 0))}"
                f" latest_gap_us={int(v.get('source_gap_us', -1))}"
                f" healthy_source={int(v.get('healthy_source', 0))}"
            )
        print("  Visible replay is enabled; confirm image correctness and smoothness in-game.")
    else:
        print("  Replay phases are offscreen/preparation only; this report does not verify visible smoothness.")
    if problems:
        for problem in problems:
            print(f"  ! {problem}")


def self_test() -> None:
    sample = """\
[OpenSPM] Using framebuffer size 2560x1080 scale 1
M14 dev140 replay cadence requested_hz=100 effective_hz=99.943000 precise=99943/1000 simulation_hz=60 rational_phase=on
M14 dev140 replay prep active: simulation_hz=60 phase_source=rational-display-grid one_frame_latency=on visual_replay=off
M14 dev140 replay prep interval=1 draws=100 gx_draws=102 matched_draws=95 unmatched_draws=5 indexed_snap=1 snapshot_reject=1 identity_miss=2 discontinuity_snap=2 duplicate_exhausted=1 ambiguous_snap=0 previous=100 matched_uniforms=90 snapped_uniforms=2 phases=2 original_uniform_bytes=1000 staged_uniform_bytes=3000 prepared_total=1 no_match_total=0 arena_exhausted_total=0
M14 dev140 replay prep skip interval=2 reason=no_matches draws=2 gx_draws=2 matched_draws=0 unmatched_draws=2 indexed_snap=0 snapshot_reject=0 identity_miss=2 discontinuity_snap=0 duplicate_exhausted=0 ambiguous_snap=0 previous=100 no_match_total=1
M14 main-loop phases frames=60/1000ms disp=5.0ms main=5.0ms done=5.0ms
M14 dev140b offscreen replay encode active: visual_present=off authoritative_efb_untouched=1
M14 dev140b replay encode frame=1 requested_phases=2 encoded=2 passes=2 gx_draws=190 other_draws=0 efb_copy_passes=0 rejected=0 reject_layout=0 reject_prior_load=0 reject_custom=0 reject_range=0
"""
    result = parse_text(sample)
    assert result["renderer_size"] == (2560, 1080)
    assert result["replay_cadence"]["requested_hz"] == 100.0
    assert result["replay_cadence"]["effective_hz"] == 99.943
    assert round(result["match_pct"], 3) == round(95.0 / 102.0 * 100.0, 3)
    assert result["matched_draws"] == 95
    assert result["unmatched_draws"] == 7
    assert result["matched_uniforms"] == 90
    assert result["snapped_uniforms"] == 2
    assert result["indexed_snaps"] == 1
    assert result["snapshot_rejects"] == 1
    assert result["identity_misses"] == 4
    assert result["discontinuity_snaps"] == 2
    assert result["duplicate_exhausted"] == 1
    assert result["ambiguous_snaps"] == 0
    assert result["encode_pct"] == 100.0
    assert result["producer_median_fps"] == 60.0
    assert verdict(result)[0] == "DEV140B COUNTERS PASS; VISUAL VALIDATION REQUIRED"
    modern = sample.replace(
        "frames=60/1000ms disp=5.0ms main=5.0ms done=5.0ms",
        "host_ms=1033 frames=31 before_avg_us=46 disp_avg_us=6095 main_avg_us=5989 done_avg_us=21165",
    )
    slow = parse_text(modern)
    assert abs(slow["producer_median_fps"] - 30.0096805) < 0.0001
    assert slow["producer_slow_windows"] == 1
    assert verdict(slow)[0] == "INVESTIGATE"
    missing = parse_text("\n".join(line for line in sample.splitlines() if "main-loop phases" not in line))
    assert verdict(missing)[0] == "INSUFFICIENT DATA"
    mixed = parse_text(sample + "\nM14 main-loop phases host_ms=1000 frames=30" * 3 +
                       "\nM14 main-loop phases host_ms=1000 frames=60" * 8)
    assert mixed["producer_median_fps"] == 60
    assert verdict(mixed)[0] == "INVESTIGATE"
    timing = parse_text(sample.replace("staged_uniform_bytes=3000", "staged_uniform_bytes=3000 prep_us=7684"))
    assert timing["prep_max_ms"] == 7.684
    assert timing["prep_cost_samples"] == 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "log",
        nargs="?",
        type=Path,
        default=DEFAULT_LOG,
        help=f"launch log (default: {DEFAULT_LOG})",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="run parser self-test and exit",
    )
    args = parser.parse_args()

    if args.self_test:
        self_test()
        print("dev140 replay analyzer self-test PASS")
        return 0

    if not args.log.is_file():
        print(f"error: log not found: {args.log}", file=sys.stderr)
        return 2
    result = parse_text(
        args.log.read_text(encoding="utf-8", errors="replace"),
        str(args.log),
    )
    print_report(result)
    status, _ = verdict(result)
    return 0 if "COUNTERS PASS" in status else 1


if __name__ == "__main__":
    raise SystemExit(main())

