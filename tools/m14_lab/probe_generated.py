#!/usr/bin/env python3
"""Deterministic diagnostic probes for player-local M14 generated source.

This file contains only OpenSPM development logic and R8PE01 addresses. It does
not contain Nintendo code or assets. Probes are applied to a local generated C
tree after direct-call conversion and are deliberately never committed back as
generated game source.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

PROBE_BEGIN = "/* OPENSPM_M14_PROBE_BEGIN:safety_a */"
PROBE_END = "/* OPENSPM_M14_PROBE_END:safety_a */"


def _hex_u32(value: str | int) -> str:
    if isinstance(value, str):
        text = value.strip()
        if text.lower().startswith("0x"):
            number = int(text, 16)
        elif text and all(ch in "0123456789abcdefABCDEF" for ch in text):
            number = int(text, 16)
        else:
            number = int(text, 0)
    else:
        number = value
    if not 0 <= number <= 0xFFFFFFFF:
        raise ValueError(f"not a u32: {value!r}")
    return f"{number:08X}"


def locate_callsite(generated: Path, callsite: str | int, getter: str | int) -> Path:
    callsite_hex = _hex_u32(callsite)
    getter_hex = _hex_u32(getter)
    needle = f"// {callsite_hex}: bl      0x{getter_hex}"
    matches: list[Path] = []
    for path in sorted((generated / "chunks").glob("chunk_*.c")):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise RuntimeError(f"generated source is not UTF-8: {path}") from exc
        if needle in text:
            matches.append(path)
    if len(matches) != 1:
        listing = ", ".join(str(path) for path in matches) or "none"
        raise RuntimeError(
            f"expected one generated callsite {callsite_hex} -> {getter_hex}; found {len(matches)}: {listing}"
        )
    return matches[0]


def apply_safety_a_probe(
    generated: Path,
    *,
    callsite: str | int,
    getter: str | int,
    return_address: str | int,
    button: str | int,
    inject_on_poll: int,
) -> dict[str, object]:
    """Inject one synthetic normalized A press at the safety-screen callsite.

    The probe bypasses only this one game-level `buttons pressed this frame`
    call. It does not fake USB/Bluetooth/Wiimote hardware and it fires once.
    """
    if inject_on_poll < 1:
        raise ValueError("inject_on_poll must be >= 1")

    generated = generated.resolve()
    if not (generated / "generated.h").is_file() or not (generated / "chunks").is_dir():
        raise RuntimeError(f"not a generated source tree: {generated}")

    callsite_hex = _hex_u32(callsite)
    getter_hex = _hex_u32(getter)
    return_hex = _hex_u32(return_address)
    button_hex = _hex_u32(button)
    if int(return_hex, 16) != (int(callsite_hex, 16) + 4) & 0xFFFFFFFF:
        raise RuntimeError("probe return address must be callsite + 4")

    path = locate_callsite(generated, callsite_hex, getter_hex)
    text = path.read_text(encoding="utf-8")

    if PROBE_BEGIN in text:
        if text.count(PROBE_BEGIN) != 1 or text.count(PROBE_END) != 1:
            raise RuntimeError(f"malformed existing safety-A probe in {path}")
        return {
            "probe": "safety_a",
            "changed": False,
            "existing_format": "marked",
            "file": str(path),
            "callsite": f"0x{callsite_hex}",
            "button": f"0x{button_hex}",
            "inject_on_poll": inject_on_poll,
        }

    # The rescued 218-chunk archive predates the durable marker format but
    # already contains the same one-shot proof. Recognize it so a future
    # session can safely reuse that archive without creating duplicate statics.
    legacy_tokens = (
        "openspm_diag_safety_input_polls",
        "openspm_diag_safety_a_fired",
        "synthetic A at safety poll",
    )
    legacy_hits = [token in text for token in legacy_tokens]
    if all(legacy_hits):
        return {
            "probe": "safety_a",
            "changed": False,
            "existing_format": "legacy-rescued",
            "file": str(path),
            "callsite": f"0x{callsite_hex}",
            "button": f"0x{button_hex}",
            "inject_on_poll": inject_on_poll,
        }
    if any(legacy_hits):
        raise RuntimeError(f"partial/colliding legacy safety-A probe in {path}")

    declaration_anchor = '#include "generated.h"\n'
    if declaration_anchor not in text:
        raise RuntimeError(f"generated chunk lacks expected include anchor: {path}")

    declarations = (
        '#include "generated.h"\n'
        '#include <stdio.h>\n\n'
        'static unsigned openspm_diag_safety_input_polls;\n'
        'static int openspm_diag_safety_a_fired;\n'
    )
    text = text.replace(declaration_anchor, declarations, 1)

    label_anchor = (
        f"label_{callsite_hex}:\n"
        f"    ctx->pc = 0x{callsite_hex}u;\n"
    )
    if label_anchor not in text:
        raise RuntimeError(f"generated chunk lacks expected callsite label anchor: {path}")

    probe = (
        f"{label_anchor}"
        f"    {PROBE_BEGIN}\n"
        "    /* Diagnostic only: inject one normalized button press at the\n"
        "       wrist-strap screen's own input boundary. No fake controller. */\n"
        f"    if (!openspm_diag_safety_a_fired && ++openspm_diag_safety_input_polls == {inject_on_poll}u) {{\n"
        "        openspm_diag_safety_a_fired = 1;\n"
        f"        ctx->gpr[3] = 0x{button_hex}u;\n"
        f"        ctx->lr = 0x{return_hex}u;\n"
        f"        ctx->pc = 0x{return_hex}u;\n"
        f"        fprintf(stderr, \"[openspm-diag] synthetic A at safety poll %u\\n\",\n"
        "                openspm_diag_safety_input_polls);\n"
        f"        goto label_{return_hex};\n"
        "    }\n"
        f"    {PROBE_END}\n"
    )
    text = text.replace(label_anchor, probe, 1)
    path.write_text(text, encoding="utf-8", newline="\n")

    return {
        "probe": "safety_a",
        "changed": True,
        "file": str(path),
        "callsite": f"0x{callsite_hex}",
        "button": f"0x{button_hex}",
        "inject_on_poll": inject_on_poll,
    }


def load_probe_from_state(state_path: Path) -> dict[str, object]:
    state = json.loads(state_path.read_text(encoding="utf-8"))
    probe = state["input_probe"]
    return {
        "callsite": probe["safety_callsite"],
        "getter": probe["getter_address"],
        "return_address": probe["return_address"],
        "button": probe["a_button"],
        "inject_on_poll": int(probe["inject_on_poll"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("generated", type=Path)
    parser.add_argument("--state", type=Path,
                        default=Path(__file__).resolve().parents[2] / "state" / "m14.json")
    args = parser.parse_args()

    result = apply_safety_a_probe(args.generated, **load_probe_from_state(args.state))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
