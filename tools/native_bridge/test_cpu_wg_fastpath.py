#!/usr/bin/env python3
"""Contract test for the player-local DolRecomp WPAR store fastpath."""

from pathlib import Path
import tempfile

import stage_cpu_wg_fastpath as patch


FIXTURE = """#include \"cpu.h\"\n\nPPCMemWriteJournal g_mem_write_journal = NULL;\n\nvoid mem_write64(CPUState* cpu, u32 addr, u64 value) {\n    u32 avail;\n}\n\nvoid mem_write32(CPUState* cpu, u32 addr, u32 value) {\n    u32 avail;\n}\n\nvoid mem_write16(CPUState* cpu, u32 addr, u16 value) {\n    u32 avail;\n}\n\nvoid mem_write8(CPUState* cpu, u32 addr, u8 value) {\n    u32 avail;\n}\n"""


with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    cpu = root / "cpu"
    cpu.mkdir()
    target = cpu / "cpu.c"
    target.write_text(FIXTURE, encoding="utf-8", newline="\n")

    assert patch.transform(root, check_only=True) == "verified"
    assert target.read_text(encoding="utf-8") == FIXTURE
    assert patch.transform(root) == "applied"
    text = target.read_text(encoding="utf-8")
    assert text.count("OPENSPM_M14_DIRECT_WPAR_STORE_HOOK") == 1
    assert text.count("(addr & 0xFFFFFFE0u) == 0xCC008000u") == 4
    for width in (8, 4, 2, 1):
        assert f"openspm_m14_try_wg_write(cpu, addr, value, {width})" in text
    assert patch.transform(root) == "already-applied"

print("M14 staged CPU WPAR fastpath contract: PASS")
