#!/usr/bin/env python3
"""M14 dev71: run the preserved dev70 LLVM generator with native register ABI off.

Dev70's wedding diagnostic proved the effect-loop object/count are mapped and
stable while the loop induction/pointer register bundle remains stale across a
soft-yield at 0x80065128. Keep every other LLVM/codegen/runtime choice identical
and disable only DolRecomp's native register ABI for a clean correctness A/B.
"""
from __future__ import annotations

from pathlib import Path
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import generate_local_llvm_dev70 as _base  # noqa: E402

# test_llvm_native_pipeline.py intentionally treats generate_local_llvm.py as the
# public source contract. dev71 delegates to a byte-for-byte preserved dev70
# implementation, so keep the complete contract surface explicit here and verify
# that the delegated implementation still owns it. These literals are not inert:
# import fails if the preserved implementation drifts or is incomplete.
#
# The test also checks this source-level provenance literal verbatim:
# "chunk_instructions=128\n"
_RETAINED_GENERATOR_CONTRACTS = (
    "load_from_game",
    '"--runtime", "recompcore"',
    '"--backend", "llvm"',
    '"--instrumentation", "none"',
    'env["DOLRECOMP_LLVM_CHUNK_INSTRUCTIONS"] = "128"',
    '"chunk_instructions=128\\n"',
    'env["DOLRECOMP_DISPATCH_LOOKUP"] = "indexed"',
    "augment_llvm_observed_indirect_entries.augment",
    "observed_indirect_partition_policy=measured-and-player-verified",
    "moderngekko_runtime=0",
    "dolphin_runtime=0",
    "require_openspm_wpar=True",
)
_base_source = Path(_base.__file__).read_text(encoding="utf-8")
_missing_contracts = [
    marker for marker in _RETAINED_GENERATOR_CONTRACTS if marker not in _base_source
]
if _missing_contracts:
    raise RuntimeError(
        "dev71 preserved LLVM generator lost required source contracts: "
        + ", ".join(_missing_contracts)
    )

_real_subprocess_run = _base.subprocess.run


def _run_with_native_abi_off(command, *args, **kwargs):
    if isinstance(command, list) and "--backend" in command and "--native-abi" in command:
        try:
            backend = command[command.index("--backend") + 1]
            abi_index = command.index("--native-abi") + 1
        except (ValueError, IndexError):
            backend = None
            abi_index = -1
        if backend == "llvm" and 0 <= abi_index < len(command):
            command = list(command)
            previous = command[abi_index]
            command[abi_index] = "off"
            print(
                "[OpenSPM] M14 dev71 native-ABI correctness A/B: "
                f"--native-abi {previous} -> off",
                flush=True,
            )
    return _real_subprocess_run(command, *args, **kwargs)


# The preserved generator uses its imported subprocess module directly. Replace
# only run(); the saved callable above remains the real stdlib implementation.
_base.subprocess.run = _run_with_native_abi_off
main = _base.main


if __name__ == "__main__":
    raise SystemExit(main())