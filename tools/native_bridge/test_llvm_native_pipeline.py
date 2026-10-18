#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Adapts DolRecomp (GPL-3.0) and carries parts of its source; see LICENSE.
"""Synthetic source-only contracts for the opt-in OpenSPM LLVM pipeline."""
from __future__ import annotations

import ast
import importlib.util
import os
from pathlib import Path
import struct
import tempfile

import analyze_m14_perf
import analyze_m14_interp_replay
import analyze_yold_npc_sweep
import analyze_yold_npc_groups
import augment_llvm_bl_continuations as bl_continuations
import augment_llvm_hp0_callback_entries as hp0_callback_entries
import augment_llvm_observed_indirect_entries as observed_indirect
import apply_llvm_hostcall_prefilter as hostcall_prefilter
import build_relf_codegen_dol as relf
import derive_v19_llvm_seams as seams
import relf_input
import validate_llvm_generated as validator
import test_scene_profile as scene_profile_tests

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def w32(data: bytearray, off: int, value: int) -> None:
    struct.pack_into(">I", data, off, value & 0xFFFFFFFF)


def fake_relf() -> bytearray:
    """Minimal revision-shaped REL with one text section and one BSS section."""
    data = bytearray(relf.REL_FILE_SIZE)
    section_table = 0x4C
    bss_index = 6
    w32(data, 0x00, relf.REL_MODULE_ID)
    w32(data, 0x0C, relf.REL_SECTION_COUNT)
    w32(data, 0x10, section_table)
    w32(data, 0x1C, relf.REL_VERSION)
    w32(data, 0x20, relf.REL_BSS_SIZE)
    w32(data, 0x24, 0x80000)
    w32(data, 0x28, 0x80000)
    w32(data, 0x2C, 0)
    w32(data, 0x44, 4)
    w32(data, 0x48, relf.REL_FILE_SIZE)
    w32(data, section_table + relf.REL_EXEC_INDEX * 8, relf.REL_EXEC_OFFSET | 1)
    w32(data, section_table + relf.REL_EXEC_INDEX * 8 + 4, relf.REL_EXEC_SIZE)
    w32(data, section_table + bss_index * 8, 0)
    w32(data, section_table + bss_index * 8 + 4, relf.REL_BSS_SIZE)
    return data


def fake_dol() -> bytes:
    data = bytearray(0x160)
    w32(data, 0x00, 0x100)
    w32(data, 0x04, 0x120)
    w32(data, 0x48, 0x80004000)
    w32(data, 0x4C, 0x8001D940)
    w32(data, 0x90, 0x20)
    w32(data, 0x94, 0x20)
    w32(data, 0xE0, 0x80006124)
    return bytes(data)


def repeat_a_lz10() -> bytes:
    size = relf_input.EXPECTED_RELF_SIZE
    tokens: list[tuple[bool, bytes]] = [(False, b"A")]
    remaining = size - 1
    while remaining:
        if remaining >= 3:
            length = min(18, remaining)
            tokens.append((True, bytes([((length - 3) << 4), 0])))
            remaining -= length
        else:
            tokens.append((False, b"A"))
            remaining -= 1
    encoded = bytearray([
        relf_input.LZ10_MAGIC,
        size & 0xFF,
        (size >> 8) & 0xFF,
        (size >> 16) & 0xFF,
    ])
    for first in range(0, len(tokens), 8):
        group = tokens[first:first + 8]
        flags = 0
        for index, (compressed, _payload) in enumerate(group):
            if compressed:
                flags |= 1 << (7 - index)
        encoded.append(flags)
        for _compressed, payload in group:
            encoded.extend(payload)
    return bytes(encoded)


def test_retail_lz10_input() -> None:
    compressed = repeat_a_lz10()
    decoded = relf_input.decompress_lz10(compressed)
    assert len(decoded) == relf_input.EXPECTED_RELF_SIZE
    assert decoded[:32] == b"A" * 32
    assert decoded[-32:] == b"A" * 32
    with tempfile.TemporaryDirectory(prefix="openspm-relf-input-") as temp_name:
        root = Path(temp_name)
        rel_dir = root / "rel"
        rel_dir.mkdir()
        compressed_path = rel_dir / "relF.bin"
        fallback_path = rel_dir / "relF.rel"
        compressed_path.write_bytes(compressed)
        fallback_path.write_bytes(b"B" * relf_input.EXPECTED_RELF_SIZE)
        assert relf_input.find_relf_source(root) == compressed_path.resolve()
        loaded = relf_input.load_from_game(root)
        assert loaded.storage == "retail-lz10"
        assert loaded.data[:32] == b"A" * 32
        compressed_path.unlink()
        fallback = relf_input.load_from_game(root)
        assert fallback.storage == "uncompressed-fallback"
        assert fallback.data[:32] == b"B" * 32


def test_relf_live_layout_and_augmentation() -> None:
    assert relf.REL_BASE == 0x80CE7220
    assert relf.REL_EXEC_ADDRESS == 0x80CE72FC
    assert relf.REL_BSS_BASE == 0x80CE6340
    assert relf.REL_BSS_SIZE == 0x00000EBC
    assert relf.REL_PROLOG_ADDRESS == 0x80D2533C
    image = fake_relf()
    sections, meta = relf.parse_sections(image)
    execute = sections[relf.REL_EXEC_INDEX]
    bss_sections = [section for section in sections if section.bss]
    assert execute.address == relf.REL_EXEC_ADDRESS
    assert execute.file_offset == relf.REL_EXEC_OFFSET
    assert execute.size == relf.REL_EXEC_SIZE
    assert len(bss_sections) == 1
    assert bss_sections[0].address == relf.REL_BSS_BASE
    assert bss_sections[0].size == relf.REL_BSS_SIZE
    assert meta["bss_size"] == relf.REL_BSS_SIZE
    assert [section.index for section in sections if section.size and section.executable] == [
        relf.REL_EXEC_INDEX
    ]
    stats = relf.relocate(image, sections, meta)
    assert stats == {"imports": 0, "relocations": 0}
    exec_bytes = bytes(image[execute.file_offset:execute.file_offset + execute.size])
    augmented, info = relf.augment_dol(fake_dol(), exec_bytes)
    assert struct.unpack_from(">I", augmented, 0x08)[0] == info["text2_file_offset"]
    assert struct.unpack_from(">I", augmented, 0x50)[0] == relf.REL_EXEC_ADDRESS
    assert struct.unpack_from(">I", augmented, 0x98)[0] == relf.REL_EXEC_SIZE
    assert augmented[info["text2_file_offset"]:][:relf.REL_EXEC_SIZE] == exec_bytes


def test_relf_rejects_layout_drift() -> None:
    image = fake_relf()
    section_table = 0x4C
    extra_index = 2
    extra_offset = relf.REL_EXEC_OFFSET + relf.REL_EXEC_SIZE
    w32(image, section_table + extra_index * 8, extra_offset | 1)
    w32(image, section_table + extra_index * 8 + 4, 4)
    try:
        relf.parse_sections(image)
    except RuntimeError as exc:
        assert "executable-section shape changed" in str(exc)
    else:
        raise AssertionError("relF with an omitted executable section unexpectedly passed")
    image = fake_relf()
    w32(image, 0x20, relf.REL_BSS_SIZE + 4)
    try:
        relf.parse_sections(image)
    except RuntimeError as exc:
        assert "BSS size mismatch" in str(exc)
    else:
        raise AssertionError("relF with changed BSS size unexpectedly passed")


def test_relocation_primitives() -> None:
    data = bytearray(0x80)
    section = relf.Section(1, 0x20, 0x40, True, False, 0x81000000)
    w32(data, 0x20, 0x48000000)
    relf.apply_one(data, section, 0, 0x81000000, relf.R_PPC_REL24, 0x81000010)
    assert struct.unpack_from(">I", data, 0x20)[0] == 0x48000010
    relf.apply_one(data, section, 4, 0x81000004, relf.R_PPC_ADDR32, 0x81234560)
    assert struct.unpack_from(">I", data, 0x24)[0] == 0x81234560


def test_seam_scanner() -> None:
    assert seams.RELF_ALLOC_CALL == 0x8023C978
    assert seams.RELF_ALLOC_RETURN == 0x8023C97C
    assert seams.RELF_ALLOC_TARGET == 0x801A59A8
    assert seams.RELF_ALLOC_RAW == 0x4BF69031
    seams.self_test()


def test_observed_indirect_entry() -> None:
    assert observed_indirect.FILESELECT_INDIRECT_CALL == 0x80032B48
    assert observed_indirect.FILESELECT_INDIRECT_RETURN == 0x80032B4C
    assert observed_indirect.FILESELECT_ROOT_TARGET == 0x8002F278
    assert observed_indirect.FILESELECT_INDIRECT_TARGET == 0x80032B94
    observed_indirect.self_test()


def test_hostcall_prefilter_helper() -> None:
    assert "OPENSPM_M14_DEV87_EXACT_HOSTCALL_PREFILTER" in hostcall_prefilter.SIGNATURE
    assert "OPENSPM_M14_DEV90_STRUCTURAL_NATIVE_FIRST" in hostcall_prefilter.DEV90_SIGNATURE
    assert hostcall_prefilter.DEV90_NATIVE_FIRST_ENTRIES == {
        0x80D1F414: "openspm_dev79_hot_bl_source_80d1f414",
        0x80086848: "openspm_dev79_hot_bl_source_80086848",
    }
    assert hostcall_prefilter.DEV129_DYNAMIC_IDLE_ENTRY == (
        0x8027551C, "openspm_scheduler_idle_reentry"
    )
    assert 0x802871E4 in hostcall_prefilter.EXTRA_MAY_HOST
    assert 0x80271E48 in hostcall_prefilter.EXTRA_MAY_HOST
    assert hostcall_prefilter.DEV103_SIGNATURE == "OPENSPM_M14_DEV103_ENTRYONLY_HOSTCALL_FILTER"
    assert hostcall_prefilter.DEV107_SIGNATURE == "OPENSPM_M14_DEV107_DIRECT_BL_NATIVE_FIRST"
    assert hostcall_prefilter.DEV108_SIGNATURE == "OPENSPM_M14_DEV108_PARTITION_ONLY_AB"
    assert "openspm_m14_dev107_direct_bl_native_first(address)" not in hostcall_prefilter.ADDRESS_REPLACEMENT
    assert "openspm_m14_dev107_direct_bl_native_first(alias)" not in hostcall_prefilter.ALIAS_REPLACEMENT
    assert hostcall_prefilter.ENTRY_ONLY_MAP_PREFIXES == (
        "openspm_dev57_addr_taken_",
    )
    assert hostcall_prefilter.map_rows_requiring_host({
        0x1000: "openspm_dev57_addr_taken_00001000",
        0x1004: "openspm_dev97_hp0_motion24_interval_00001004",
        0x1008: "semantic_entry",
    }) == {0x1004, 0x1008}
    hostcall_prefilter.self_test()


def test_direct_bl_partition_helpers() -> None:
    assert bl_continuations.DEV79_HOT_BL_SOURCES[0][0] == 0x80D1F414
    assert bl_continuations.DEV79_HOT_BL_SOURCES[0][1] == 0x802543F4
    assert bl_continuations.DEV79_HOT_BL_SOURCES[1][0] == 0x80086848
    assert bl_continuations.DEV79_HOT_BL_SOURCES[1][1] == 0x8012191C
    bl_continuations.self_test()


def _fake_object_magic() -> bytes:
    return b"\x64\x86\0\0" if os.name == "nt" else b"\x7fELF"


def _write_validator_tree(root: Path, payload: bytes) -> Path:
    chunks = root / "chunks"
    chunks.mkdir()
    (root / "generated.h").write_text(
        "#define DOLRECOMP_BACKEND_LLVM 1\n#define DOLRECOMP_MODULE_ABI_V4 1\n",
        encoding="utf-8",
    )
    (root / "generated.c").write_text("// object: chunks/chunk_0000_test.o\n", encoding="utf-8")
    obj = chunks / "chunk_0000_test.o"
    obj.write_bytes(_fake_object_magic() + payload + b"\0" * 60)
    return obj


def test_validator_shape() -> None:
    with tempfile.TemporaryDirectory(prefix="openspm-llvm-validator-") as temp_name:
        root = Path(temp_name)
        payload = b"\0".join(validator.OPENSPM_WPAR_SYMBOLS)
        _write_validator_tree(root, payload)
        objects = validator.validate(root, require_openspm_wpar=True)
        assert len(objects) == 1
        cmake_object = validator.cmake_path(objects[0])
        assert "\\" not in cmake_object
        assert "/" in cmake_object
        assert cmake_object.endswith("/chunks/chunk_0000_test.o")


def test_validator_rejects_unpatched_objects() -> None:
    with tempfile.TemporaryDirectory(prefix="openspm-llvm-validator-stock-") as temp_name:
        root = Path(temp_name)
        _write_validator_tree(root, b"stock-dolrecomp-object")
        assert len(validator.validate(root)) == 1
        try:
            validator.validate(root, require_openspm_wpar=True)
        except RuntimeError as exc:
            assert "does not prove the OpenSPM inline-WPAR emitter" in str(exc)
        else:
            raise AssertionError("stock LLVM object unexpectedly passed OpenSPM WPAR proof")


def _load_python_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load Python module contract: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_dev99_dispdraw_nested_indirect_transform() -> None:
    path = ROOT / "tools" / "native_bridge" / "apply_openspm_llvm_wpar_codegen_patch.py"
    dev99 = _load_python_module(path, "openspm_dev99_contract")
    with tempfile.TemporaryDirectory(prefix="openspm-dev99-") as temp_name:
        root = Path(temp_name)
        control = root / "src" / "backend" / "llvm" / "control_flow.cpp"
        control.parent.mkdir(parents=True)
        control.write_text(
            "// synthetic pinned control-flow prefix\\n"
            + dev99.CONTROL_FLOW_DISPDRAW_OLD
            + dev99.CONTROL_FLOW_CPUSTATE_OLD
            + "\\n// synthetic pinned control-flow suffix\\n",
            encoding="utf-8",
        )
        assert dev99._patch_dispdraw_nested_indirect(root, True) == "verified"
        assert dev99._patch_dispdraw_nested_indirect(root, False) == "applied"
        assert dev99._patch_dispdraw_cpustate_nested(root, True) == "verified"
        assert dev99._patch_dispdraw_cpustate_nested(root, False) == "applied"

        patched = control.read_text(encoding="utf-8")
        assert dev99.DISPDRAW_NESTED_SIGNATURE in patched
        assert dev99.DISPDRAW_CPUSTATE_SIGNATURE in patched
        assert "term.guest_pc >= 0x8005B028u" in patched
        assert "term.guest_pc < 0x8005B2F4u" in patched
        assert "term.guest_pc == 0x8005B29Cu" in patched
        assert '"openspm_m14_dev99_nested_dispatch"' in patched
        assert '"openspm_m14_dev105_cpustate_nested_dispatch"' in patched
        assert "materialize(target);" in patched
        assert "dispdraw_nested_resume" in patched
        assert "dispdraw_nested_escape" in patched
        assert "cpustate_dispdraw_resume" in patched
        assert "cpustate_dispdraw_escape" in patched
        assert "reloadUsedState();" in patched
        assert "builder_.CreateBr(blocks_[continuationBlock]);" in patched

        assert dev99._patch_dispdraw_nested_indirect(root, True) == "already-applied"
        assert dev99._patch_dispdraw_cpustate_nested(root, True) == "already-applied"

def test_dev106_phase_probe_upgrade() -> None:
    path = ROOT / "tools" / "native_bridge" / "apply_openspm_llvm_wpar_codegen_patch.py"
    dev106 = _load_python_module(path, "openspm_dev106_phase_contract")
    with tempfile.TemporaryDirectory(prefix="openspm-dev106-phase-") as temp_name:
        root = Path(temp_name)
        instructions = root / "src" / "backend" / "llvm" / "instructions.cpp"
        instructions.parent.mkdir(parents=True)
        instructions.write_text(
            "// synthetic pinned instructions prefix\n"
            + dev106.PHASE_PROFILE_PRE_DEV106
            + "\n// synthetic pinned instructions suffix\n",
            encoding="utf-8",
        )
        assert dev106._patch_native_phase_profile(
            root, True
        ) == "verified-dev106-upgrade"
        assert dev106._patch_native_phase_profile(
            root, False
        ) == "upgraded-dev106"
        patched = instructions.read_text(encoding="utf-8")
        assert '"openspm_m14_note_death_entry_native"' in patched
        assert "case 0x801615E4u:" in patched
        assert "case 0x8017B834u:" in patched
        assert dev106._patch_native_phase_profile(
            root, True
        ) == "already-applied"


def test_dev98_entry_only_cold_entry_transform() -> None:
    path = ROOT / "tools" / "native_bridge" / "apply_openspm_llvm_wpar_codegen_patch.py"
    dev98 = _load_python_module(path, "openspm_dev98_contract")
    with tempfile.TemporaryDirectory(prefix="openspm-dev98-") as temp_name:
        root = Path(temp_name)
        pipeline = root / "src" / "app" / "pipeline.c"
        pipeline.parent.mkdir(parents=True)
        symbol_loop = (
            "    if (symbols) {\n"
            "        for (u32 i = 0; i < symbols->count; i++) {\n"
            "            u32 address = symbols->symbols[i].address;\n"
            "            if (llvm_code_address(sections, section_count, address))\n"
            "                points[count++] = address;\n"
            "        }\n"
            "    }\n"
        )
        # Match the pinned DolRecomp layout: partition collector appears
        # before the later entry-point collector.
        source = (
            "static u32* collect_llvm_partition_points(const LoadedCodeSection* sections,\n"
            "                                          u32 section_count, u32 program_entry,\n"
            "                                          const DolRecompSymbolMap* symbols,\n"
            "                                          u32* result_count) {\n"
            + symbol_loop
            + "    return points;\n"
            "}\n\n"
            "static int between_collectors(void) { return 1; }\n\n"
            "static u32* collect_llvm_entry_points(const LoadedCodeSection* sections,\n"
            "                                      u32 section_count, u32 program_entry,\n"
            "                                      const DolRecompSymbolMap* symbols,\n"
            "                                      u32* result_count) {\n"
            + symbol_loop
            + "    return points;\n"
            "}\n\n"
            "static int after_entry(void) { return 1; }\n"
        )
        pipeline.write_text(source, encoding="utf-8")
        assert dev98._patch_entry_only_synthetic_cold_entries(root, True) == "verified"
        assert dev98._patch_entry_only_synthetic_cold_entries(root, False) == "applied"
        patched = pipeline.read_text(encoding="utf-8")
        assert dev98.ENTRY_ONLY_SIGNATURE in patched
        entry_start = patched.index("static u32* collect_llvm_entry_points(")
        partition_start = patched.index("static u32* collect_llvm_partition_points(")
        helper_start = patched.index(
            "static int openspm_m14_symbol_is_entry_only"
        )
        assert helper_start < partition_start < entry_start
        entry_end = patched.index("\nstatic ", entry_start + 1)
        entry_text = patched[entry_start:entry_end]
        partition_end = patched.index("\nstatic ", partition_start + 1)
        partition_text = patched[partition_start:partition_end]
        assert "openspm_dev57_addr_taken_" not in entry_text
        assert "openspm_dev97_hp0_motion24_interval_" not in entry_text
        assert "if (llvm_code_address(sections, section_count, address))" in entry_text
        assert "openspm_m14_symbol_is_entry_only(name)" in partition_text
        assert "const char* name = symbols->symbols[i].name;" in partition_text
        assert dev98._patch_entry_only_synthetic_cold_entries(root, True) == "already-applied"
        assert dev98._restore_synthetic_partition_boundaries(root, True) == "verified-restore"
        assert pipeline.read_text(encoding="utf-8") == patched
        assert dev98._restore_synthetic_partition_boundaries(root, False) == "restored"
        assert pipeline.read_text(encoding="utf-8") == source
        assert dev98._restore_synthetic_partition_boundaries(root, False) == "already-restored"



def test_dev91_staging_snapshot_transform() -> None:
    path = ROOT / "renderer" / "aurora" / "apply_guest_array_staging_snapshot_alias_patch.py"
    dev91 = _load_python_module(path, "openspm_dev91_contract")
    with tempfile.TemporaryDirectory(prefix="openspm-dev91-") as temp_name:
        root = Path(temp_name)
        gx = root / "lib" / "gx"
        gfx = root / "lib" / "gfx"
        gx.mkdir(parents=True)
        gfx.mkdir(parents=True)

        (gfx / "recording.hpp").write_text(
            "Range push_storage(const uint8_t* data, size_t length);\n"
            "template <typename T>\n"
            "Range push_storage(ArrayRef<T> data) {\n"
            "  return {};\n"
            "}\n",
            encoding="utf-8",
        )
        (gfx / "recording.cpp").write_text(
            "Range push_storage(const uint8_t* data, size_t length) {\n"
            "  ZoneScoped;\n"
            "  if (!check_recording(\"push_storage\")) {\n"
            "    return {};\n"
            "  }\n"
            "  return push(current_frame_packet().storage, data, length, resources().limits.minStorageBufferOffsetAlignment);\n"
            "}\n\n"
            "Range push_texture_data",
            encoding="utf-8",
        )
        (gx / "command_processor.cpp").write_text(
            "// M14 raw guest-array stage-head index + scan-plan active\n"
            "// M14 raw guest-array pooled exact snapshots single-copy append active\n"
            "struct M14GuestArrayStageEntry {\n"
            "  const void* data = nullptr;\n"
            "  u8 stride = 0;\n"
            "  u32 snapshotOffset = 0;\n"
            "  u32 snapshotSize = 0;\n"
            "  u32 previousBucketEntry = UINT32_MAX;\n"
            "  gfx::Range range{};\n"
            "};\n"
            "std::vector<u8> sM14GuestArraySnapshotBytes;\n"
            "bool sM14GuestArraySnapshotPoolReported = false;\n"
            "void reset() {\n"
            "  sM14GuestArraySnapshotBytes.clear();\n"
            "  if (sM14GuestArraySnapshotBytes.capacity() < 8u * 1024u * 1024u)\n"
            "    sM14GuestArraySnapshotBytes.reserve(8u * 1024u * 1024u);\n"
            "}\n"
            "void lookup() {\n"
            "    if (entry.data == source && entry.stride == array.stride &&\n"
            "        entry.snapshotSize >= uploadSize) {\n"
            "      AURORA_ASSERT(static_cast<size_t>(entry.snapshotOffset) + uploadSize <=\n"
            "                        sM14GuestArraySnapshotBytes.size(),\n"
            "                    \"raw GX pooled snapshot range exceeds frame pool\");\n"
            "      if (std::memcmp(sM14GuestArraySnapshotBytes.data() + entry.snapshotOffset,\n"
            "                      source, uploadSize) == 0) {\n"
            "        hit();\n"
            "      }\n"
            "    }\n"
            "}\n"
            "void miss() {\n"
            "  entry.data = source;\n"
            "  entry.stride = array.stride;\n"
            "  const size_t snapshotOffset = sM14GuestArraySnapshotBytes.size();\n"
            "  AURORA_ASSERT(snapshotOffset <= UINT32_MAX &&\n"
            "                uploadSize <= UINT32_MAX - static_cast<u32>(snapshotOffset),\n"
            "                \"raw GX pooled snapshot offset overflow\");\n"
            "  sM14GuestArraySnapshotBytes.insert(\n"
            "      sM14GuestArraySnapshotBytes.end(), source, source + uploadSize);\n"
            "  entry.snapshotOffset = static_cast<u32>(snapshotOffset);\n"
            "  entry.snapshotSize = uploadSize;\n"
            "  entry.range = range;\n"
            "  if (!sM14GuestArraySnapshotPoolReported) {\n"
            "    Log.info(\"M14 raw guest-array pooled exact snapshots single-copy append active; cache semantics remain byte-exact\");\n"
            "    sM14GuestArraySnapshotPoolReported = true;\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )

        assert dev91.transform(root) == "applied"
        cp = (gx / "command_processor.cpp").read_text(encoding="utf-8")
        rec_hpp = (gfx / "recording.hpp").read_text(encoding="utf-8")
        rec_cpp = (gfx / "recording.cpp").read_text(encoding="utf-8")
        assert dev91.SIGNATURE in cp
        assert "gfx::storage_range_data(entry.range)" in cp
        assert "std::vector<u8> sM14GuestArraySnapshotBytes;" not in cp
        assert "sM14GuestArraySnapshotBytes.insert(" not in cp
        assert "const uint8_t* storage_range_data(Range range) noexcept;" in rec_hpp
        assert "const uint8_t* storage_range_data(Range range) noexcept {" in rec_cpp
        assert dev91.transform(root, check_only=True) == "already-applied"


def test_dev93_duplicate_stage_bucket_repair() -> None:
    path = ROOT / "renderer" / "aurora" / "apply_guest_array_hotpath_v2_patch.py"
    dev89 = _load_python_module(path, "openspm_dev93_bucket_contract")
    with tempfile.TemporaryDirectory(prefix="openspm-dev93-bucket-") as temp_name:
        root = Path(temp_name)
        cp = root / "lib" / "gx" / "command_processor.cpp"
        cp.parent.mkdir(parents=True)
        bucket_decl = (
            "  const u32 stageBucket = m14_guest_array_stage_bucket(source);\n"
        )
        cp.write_text(
            "// M14 raw guest-array stage-head index + scan-plan active\n"
            "// M14 raw guest-array fused scan-plan active\n"
            "constexpr u32 kM14GuestArrayStageBuckets = 2048u;\n"
            "std::array<u32, kM14GuestArrayStageBuckets> sM14GuestArrayStageHeads{};\n"
            "struct Entry { u32 previousBucketEntry = UINT32_MAX; };\n"
            "void reset() { sM14GuestArrayStageHeads.fill(UINT32_MAX); }\n"
            "struct M14GuestIndexScan {};\n"
            "void scan() {\n"
            "    if (scan.indexCount == 1u) {\n"
            "      use(scan.minIndex);\n"
            "    }\n"
            "    if (vtxCount == 0u) continue;\n"
            "    for (u16 vertex = 0; vertex < vtxCount; ++vertex, vertexBase += recordBytes) {}\n"
            "}\n"
            "void log_marker() {\n"
            "  Log.info(\"M14 raw guest-array stage-head index + scan-plan active\");\n"
            "  Log.info(\"M14 raw guest-array fused scan-plan active\");\n"
            "}\n"
            "void stage() {\n"
            + bucket_decl
            + "  use(stageBucket);\n"
            + bucket_decl
            + "  use_again(stageBucket);\n"
            "}\n",
            encoding="utf-8",
        )
        assert dev89.transform(root) == "applied-repaired"
        text = cp.read_text(encoding="utf-8")
        assert text.count(bucket_decl) == 1
        assert "if (vtxCount == 0u) continue;" in text
        assert dev89.FUSED_SIGNATURE in text
        # Regression: the real dev109 fused scanner uses a 4-space semantic
        # zero-vertex guard, not dev89's older 6-space inner-loop guard.
        assert "    if (vtxCount == 0u) continue;\n" in text
        assert dev89.transform(root, check_only=True) == "already-applied"


def test_drawdone_retirement_successor_contract() -> None:
    path = ROOT / "renderer" / "aurora" / "apply_drawdone_substage_profile_patch.py"
    drawdone = _load_python_module(path, "openspm_drawdone_retirement_contract")
    with tempfile.TemporaryDirectory(prefix="openspm-drawdone-retired-") as temp_name:
        root = Path(temp_name)
        cp = root / "lib" / "gx" / "command_processor.cpp"
        cp.parent.mkdir(parents=True)
        cp.write_text(
            "// M14 DrawDone per-draw profiler retired for normal play\n"
            "static void handle_draw(u8 cmd, ByteReader& reader) noexcept {\n"
            "  // deliberately successor-shaped, not the old quiet anchor\n"
            "}\n",
            encoding="utf-8",
        )
        assert drawdone.transform(root) == "already-retired"
        assert drawdone.transform(root, check_only=True) == "already-retired"


def test_dev94_cpu_snapshot_restore_transform() -> None:
    path = ROOT / "renderer" / "aurora" / "apply_guest_array_staging_snapshot_rollback_patch.py"
    dev94 = _load_python_module(path, "openspm_dev94_contract")
    with tempfile.TemporaryDirectory(prefix="openspm-dev94-") as temp_name:
        root = Path(temp_name)
        gx = root / "lib" / "gx"
        gfx = root / "lib" / "gfx"
        gx.mkdir(parents=True)
        gfx.mkdir(parents=True)

        helper_decl = "const uint8_t* storage_range_data(Range range) noexcept;\n"
        helper_def = (
            "const uint8_t* storage_range_data(Range range) noexcept {\n"
            "  auto& storage = current_frame_packet().storage;\n"
            "  AURORA_ASSERT(static_cast<size_t>(range.offset) <= storage.size() &&\n"
            "                    static_cast<size_t>(range.size) <=\n"
            "                        storage.size() - static_cast<size_t>(range.offset),\n"
            "                \"storage range [{},{}) exceeds current mapped staging size {}\",\n"
            "                range.offset, static_cast<uint64_t>(range.offset) + range.size,\n"
            "                storage.size());\n"
            "  return storage.data() + range.offset;\n"
            "}\n\n"
        )
        (gfx / "recording.hpp").write_text(
            "Range push_storage(const uint8_t* data, size_t length);\n"
            + helper_decl
            + "template <typename T>\n",
            encoding="utf-8",
        )
        (gfx / "recording.cpp").write_text(
            helper_def + "Range push_texture_data\n",
            encoding="utf-8",
        )
        (gx / "command_processor.cpp").write_text(
            "// M14 raw guest-array stage-head index + scan-plan active\n"
            "struct M14GuestArrayStageEntry {\n"
            "  const void* data = nullptr;\n"
            "  u8 stride = 0;\n"
            "  u32 previousBucketEntry = UINT32_MAX;\n"
            "  gfx::Range range{};\n"
            "};\n"
            "bool sM14GuestArrayStagingSnapshotAliasReported = false;\n"
            "void reset() {\n"
            "  sM14GuestArrayStageHeads.fill(UINT32_MAX);\n"
            "}\n"
            "void lookup() {\n"
            "    if (entry.data == source && entry.stride == array.stride &&\n"
            "        entry.range.size >= uploadSize) {\n"
            "      const u8* snapshot = gfx::storage_range_data(entry.range);\n"
            "      if (std::memcmp(snapshot, source, uploadSize) == 0) {\n"
            "        hit();\n"
            "      }\n"
            "    }\n"
            "}\n"
            "void miss() {\n"
            "  entry.data = source;\n"
            "  entry.stride = array.stride;\n"
            "  entry.range = range;\n"
            "  if (!sM14GuestArrayStagingSnapshotAliasReported) {\n"
            "    Log.info(\"M14 raw guest-array staging bytes are exact snapshots; duplicate CPU snapshot copy retired\");\n"
            "    sM14GuestArrayStagingSnapshotAliasReported = true;\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )
        source_before = (gx / "command_processor.cpp").read_text(encoding="utf-8")
        assert source_before.count(dev94.ALIAS_SIGNATURE) == 1
        assert dev94.transform(root) == "restored"
        cp = (gx / "command_processor.cpp").read_text(encoding="utf-8")
        rec_hpp = (gfx / "recording.hpp").read_text(encoding="utf-8")
        rec_cpp = (gfx / "recording.cpp").read_text(encoding="utf-8")
        assert dev94.RESTORED_SIGNATURE in cp
        assert "std::vector<u8> sM14GuestArraySnapshotBytes;" in cp
        assert "sM14GuestArraySnapshotBytes.insert(" in cp
        assert "std::memcmp(sM14GuestArraySnapshotBytes.data() + entry.snapshotOffset" in cp
        assert dev94.ALIAS_SIGNATURE not in cp
        assert "storage_range_data" not in rec_hpp
        assert "storage_range_data" not in rec_cpp
        assert dev94.transform(root, check_only=True) == "already-restored"


def test_dev92_guest_timing_retirement_transform() -> None:
    path = ROOT / "renderer" / "aurora" / "apply_guest_array_timing_probe_retirement_patch.py"
    dev92 = _load_python_module(path, "openspm_dev92_contract")
    with tempfile.TemporaryDirectory(prefix="openspm-dev92-") as temp_name:
        root = Path(temp_name)
        cp = root / "lib" / "gx" / "command_processor.cpp"
        cp.parent.mkdir(parents=True)
        cp.write_text(
            "// M14 raw guest-array staging bytes are exact snapshots; duplicate CPU snapshot copy retired\n"
            "u64 sM14GuestArrayStagedBytes = 0;\n"
            "u64 sM14GuestArrayStageNanos = 0;\n"
            "u64 sM14GuestArrayStageCalls = 0;\n"
            "u64 sM14GuestArrayProfileFrames = 0;\n"
            "void reset() {\n"
            "  if (sM14GuestArrayHighWaterReported) {\n"
            "    const u64 sample = ++sM14GuestArrayProfileFrames;\n"
            "    if (sample <= 8u || (sample & (sample - 1u)) == 0u) {\n"
            "      Log.info(\"M14 raw guest-array stage profile sample={} frame={} staged={} entries={} calls={} hits={} misses={} cpu_us={}\",\n"
            "               sample, sM14GuestArrayStageFrame, sM14GuestArrayStagedBytes,\n"
            "               sM14GuestArrayStages.size(), sM14GuestArrayStageCalls,\n"
            "               sM14GuestArrayReuseHits, sM14GuestArrayReuseMisses,\n"
            "               sM14GuestArrayStageNanos / 1000u);\n"
            "    }\n"
            "  }\n"
            "  sM14GuestArrayStageNanos = 0;\n"
            "  sM14GuestArrayStageCalls = 0;\n"
            "}\n"
            "void stage() {\n"
            "  const auto profileStart = std::chrono::steady_clock::now();\n"
            "  const auto profileFinish = [&]() noexcept {\n"
            "    const auto elapsed = std::chrono::steady_clock::now() - profileStart;\n"
            "    sM14GuestArrayStageNanos += static_cast<u64>(\n"
            "        std::chrono::duration_cast<std::chrono::nanoseconds>(elapsed).count());\n"
            "    ++sM14GuestArrayStageCalls;\n"
            "  };\n"
            "\n"
            "  profileFinish();\n"
            "}\n",
            encoding="utf-8",
        )
        assert dev92.transform(root) == "retired"
        text = cp.read_text(encoding="utf-8")
        assert dev92.RETIRED_SIGNATURE in text
        assert dev92.PROFILE_SIGNATURE not in text
        assert "sM14GuestArrayStageNanos" not in text
        assert "sM14GuestArrayStageCalls" not in text
        assert "sM14GuestArrayProfileFrames" not in text
        assert "profileFinish" not in text
        assert dev92.transform(root, check_only=True) == "already-retired"


def test_cp_duplicate_pipeline_transform() -> None:
    path = ROOT / "renderer" / "aurora" / "apply_cp_duplicate_pipeline_patch.py"
    patch = _load_python_module(path, "openspm_cp_duplicate_pipeline_contract")
    with tempfile.TemporaryDirectory(prefix="openspm-cp-duplicate-") as temp_name:
        root = Path(temp_name)
        regs = root / "lib" / "gx" / "regs.cpp"
        regs.parent.mkdir(parents=True)
        regs.write_text(
            patch.TABLE_OLD + "\n"
            + patch.ENTRY_OLD
            + "  if (array.data == span.data && array.size == span.size && !array.le && array.guestMapped) {\n"
            + patch.DUPLICATE_OLD
            + "}\n",
            encoding="utf-8",
        )
        assert patch.transform(root, check_only=True) == "verified"
        assert patch.transform(root) == "applied"
        text = regs.read_text(encoding="utf-8")
        assert patch.SIGNATURE in text
        assert patch.TABLE_NEW in text
        assert patch.ENTRY_NEW in text
        assert patch.DUPLICATE_NEW in text
        assert patch.TABLE_OLD not in text
        assert patch.transform(root, check_only=True) == "already-applied"


def test_dev138_interp_identity_migration_contract() -> None:
    path = ROOT / "renderer" / "aurora" / "apply_transform_interpolation_identity_patch.py"
    dev138 = _load_python_module(path, "openspm_dev138_identity_contract")

    # Canonicalization must replace every cumulative interpolation implementation
    # before push_gx_draw with exactly one observation-only dev138 block.
    cumulative = """\
prefix
struct M14InterpProbeDraw {
  u64 identity = 0;
};
void old_dev136() {}
struct M14InterpProbeDraw {
  u64 identity = 0;
};
void stale_dev137() {}
static void push_gx_draw(int prim) {
  m14_interp_probe_record_draw(prim, fmt, vtxCount, cache.pipelineRef,
      cache.bindGroups.textureBindGroup);
  draw();
}
"""
    assert dev138.SIGNATURE in dev138.DEV138_CANONICAL
    assert "sampled two adjacent" in dev138.DEV138_CANONICAL
    canonical = dev138.canonicalize_interpolation_region(cumulative)
    assert canonical.count("struct M14InterpProbeDraw {") == 1
    assert dev138.SIGNATURE in canonical
    repeated_seed = """\
constexpr u64 M14InterpHashOffset = UINT64_C(1469598103934665603);
constexpr u64 M14InterpHashOffset = UINT64_C(1469598103934665603);
struct M14InterpProbeDraw {
  u64 identity = 0;
};
static void push_gx_draw(int prim) {
  draw();
}
"""
    canonical_seed = dev138.canonicalize_interpolation_region(repeated_seed)
    assert canonical_seed.count(
        "constexpr u64 M14InterpHashOffset = UINT64_C(1469598103934665603);"
    ) == 1
    assert dev138.canonicalize_interpolation_region(canonical_seed) == canonical_seed

    assert "old_dev136" not in canonical
    assert "stale_dev137" not in canonical
    assert "m14_interp_visual_enabled" not in canonical

    duplicate_boundaries = """\
void m14_interp_probe_finish_frame() noexcept {}
void m14_interp_probe_frame_boundary() noexcept { m14_interp_probe_finish_frame(); }

void m14_interp_probe_frame_boundary() noexcept { m14_interp_probe_finish_frame(); }

void clear_draw_cache() noexcept {
  clear();
}
"""
    repaired_boundaries = dev138.normalize_frame_boundary(duplicate_boundaries)
    assert repaired_boundaries.count("void m14_interp_probe_frame_boundary() noexcept") == 1

    duplicate_cp_h = """\
void clear_draw_cache() noexcept;
void m14_interp_probe_frame_boundary() noexcept;
void m14_interp_probe_frame_boundary() noexcept;
"""
    duplicate_regs = """\
#include "regs.hpp"
#include "command_processor.hpp"
#include "command_processor.hpp"
void copy() {
    raw::notify_display_copy(g_gxState.texCopyDest);
    m14_interp_probe_frame_boundary();
    m14_interp_probe_frame_boundary();
    m14_interp_probe_frame_boundary();
    m14_interp_probe_frame_boundary();
}
"""
    normalized_cp_h, normalized_regs = dev138.normalize_boundary_surfaces(
        duplicate_cp_h, duplicate_regs
    )
    assert normalized_cp_h.count("void m14_interp_probe_frame_boundary() noexcept;") == 1
    assert normalized_regs.count('#include "command_processor.hpp"') == 1
    assert normalized_regs.count("    m14_interp_probe_frame_boundary();\n") == 1
    normalized_cp_h2, normalized_regs2 = dev138.normalize_boundary_surfaces(
        normalized_cp_h, normalized_regs
    )
    assert normalized_cp_h2 == normalized_cp_h
    assert normalized_regs2 == normalized_regs

    broken_call = """\
static void push_gx_draw(int prim) {
  m14_interp_probe_record_draw(prim, fmt, vtxCount, cache.pipelineRef,
      cache.bindGroups.textureBindGroup, vertRange, idxRange)
  draw();
}
"""
    repaired_call = dev138.repair_record_call_semicolon(broken_call)
    assert "cache.bindGroups.textureBindGroup, vertRange, idxRange);" in repaired_call

    # Exact rejected dev137 push_gx_draw residue seen in the player's cached
    # Aurora tree: split m14InterpPrevious assignment, midpoint staging block,
    # and zCompLoc uniformRange ternary must all be removed in one pass.
    dev137_push = """\
static void push_gx_draw(int prim) {
  const auto* m14InterpPrevious =
      m14_interp_probe_record_draw(prim, fmt, vtxCount, cache.pipelineRef,
                                   cache.bindGroups.textureBindGroup);

  const bool uniformValid = ready;
  if (!uniformValid) {
    cache.uniformRange = build_uniform(cache.shaderInfo);
  }

  gfx::Range m14InterpUniformRange{};
  if (m14_interp_visual_enabled() && m14InterpPrevious != nullptr) {
    const auto savedProjection = state.proj;
    const auto savedMatrices = state.pnMtx;
    if (m14_interp_apply_visual(*m14InterpPrevious, state, m14_interp_visual_alpha())) {
      m14InterpUniformRange = build_uniform(cache.shaderInfo);
    }
    state.proj = savedProjection;
    state.pnMtx = savedMatrices;
  }

  const auto m14PushDraw = [&] {
    draw(DrawData{
        .uniformRange = (m14_interp_visual_enabled() && m14InterpUniformRange.size != 0)
                            ? m14InterpUniformRange
                            : cache.uniformRange,
    });
  };
}
"""
    cleaned_push = dev138.normalize_push_after_dev137(dev137_push)
    assert cleaned_push.count("m14_interp_probe_record_draw(") == 1
    assert "cache.bindGroups.textureBindGroup, vertRange, idxRange);" in cleaned_push
    assert "const auto* m14InterpPrevious" not in cleaned_push
    assert "m14InterpUniformRange" not in cleaned_push
    assert "m14_interp_visual_enabled" not in cleaned_push
    assert "m14_interp_apply_visual" not in cleaned_push
    assert ".uniformRange = cache.uniformRange," in cleaned_push
    assert dev138.normalize_push_after_dev137(cleaned_push) == cleaned_push

    push_source = """\
static void push_gx_draw(int prim) {
  m14_interp_probe_record_draw(prim, fmt, vtxCount, cache.pipelineRef,
      cache.bindGroups.textureBindGroup);
  draw();
}
"""
    patched_push = dev138.patch_record_call(push_source)
    assert "cache.bindGroups.textureBindGroup, vertRange, idxRange);" in patched_push
    assert dev138.patch_record_call(patched_push) == patched_push

    merge_source = """\
static void draw_prim(int prim) {
  if (canMerge) {
    lastDraw->vertRange.size += vertRange.size;
    lastDraw->idxRange.size += idxRange.size;
    lastDraw->vtxCount += vtxCount;
    lastDraw->indexCount += numIndices;
    gfx::detail::increment_merged_draw_count();
    return;
  }
}
"""
    patched_merge = dev138.patch_merge_refresh(merge_source)
    assert (
        "m14_interp_probe_extend_last_geometry(vertRange, "
        "idxRange, lastDraw->vtxCount);" in patched_merge
    )
    assert dev138.patch_merge_refresh(patched_merge) == patched_merge

    rec_h = """\
namespace aurora::gfx::detail {
struct FramePacket;
} // namespace aurora::gfx::detail

namespace aurora::gfx {
detail::FramePacket& current_frame_packet();
Range push_indices(const uint8_t* data, size_t length, size_t alignment);
}
"""
    normalized_h = dev138.normalize_recording_header(rec_h)
    assert "current_frame_packet" not in normalized_h
    assert normalized_h.count("m14_hash_recorded_vertex_range") == 1
    assert normalized_h.count("m14_hash_recorded_index_range") == 1
    assert dev138.normalize_recording_header(normalized_h) == normalized_h

    rec_cpp = """\
#include "recording.hpp"
#include "frame.hpp"
namespace aurora::gfx {
namespace {
FrameRecorder g_recorder;
}
 // filler
} // namespace

namespace detail {
}
}
"""
    normalized_cpp = dev138.normalize_recording_cpp(rec_cpp)
    assert '#include "hash.hpp"' in normalized_cpp
    assert "bool m14_hash_recorded_vertex_range(" in normalized_cpp
    assert "bool m14_hash_recorded_index_range(" in normalized_cpp

    normalized_cpp_again = dev138.normalize_recording_cpp(normalized_cpp)
    assert normalized_cpp_again == normalized_cpp
    assert normalized_cpp.count("bool m14_hash_recorded_range(") == 1
    assert normalized_cpp.count("bool m14_hash_recorded_vertex_range(") == 1
    assert normalized_cpp.count("bool m14_hash_recorded_index_range(") == 1

    include_source = "#include <cstdint>\n"
    include_source = dev138.ensure_cpp_include(include_source, "#include <limits>\n")
    include_source = dev138.ensure_cpp_include(include_source, "#include <cstring>\n")
    include_source = dev138.ensure_cpp_include(include_source, "#include <unordered_map>\n")
    assert include_source.count("#include <limits>\n") == 1
    assert include_source.count("#include <cstring>\n") == 1
    assert include_source.count("#include <unordered_map>\n") == 1

    source = path.read_text(encoding="utf-8")
    assert 'std::getenv("OPENSPM_M14_INTERP_PROBE_INTERVAL")' in dev138.DEV138_BASE
    assert "sM14InterpCaptureActive = true" in dev138.DEV138_BASE
    assert "sM14InterpCaptureCooldown" in dev138.DEV138_BASE
    assert "!sM14InterpCaptureActive" in dev138.DEV138_RECORD
    assert "M14 dev138 identity sample={}" in dev138.DEV138_FINISH
    assert "sM14InterpCaptureCooldown = m14_interp_probe_interval() - 2u;" in dev138.DEV138_FINISH
    assert "constexpr u64 M14InterpHashOffset = UINT64_C(1469598103934665603);" in dev138.DEV138_BASE
    assert dev138.DEV138_CANONICAL.index("M14InterpHashOffset") < dev138.DEV138_CANONICAL.index(
        "snapshot.vertexSignature = M14InterpHashOffset"
    )
    assert "current_frame_packet" in source  # only cleanup/guard text; never active GX API
    assert "dev137 source layer must be present before dev138" not in source
    assert "m14_interp_visual_enabled" not in dev138.DEV138_CANONICAL
    assert "m14InterpUniformRange" not in dev138.DEV138_CANONICAL
    assert "m14_hash_recorded_vertex_range" in dev138.DEV138_CANONICAL
    assert "m14_hash_recorded_index_range" in dev138.DEV138_CANONICAL


def test_dev139_async_presenter_migration_contract() -> None:
    path = ROOT / "renderer" / "aurora" / "apply_async_presenter_probe_patch.py"
    dev139 = _load_python_module(path, "openspm_dev139_presenter_contract")

    synthetic = """\
#include <magic_enum.hpp>
// OpenSPM M14 Wii display-copy XFB snapshot active
constexpr unsigned long long kM14PresentPacingBinUs = 500u;
gfx::TextureHandle openspmDisplayCopySnapshot;
void m14_record_successful_present();

AuroraInfo initialize(int argc, char* argv[], const AuroraConfig& config) noexcept {
  return {};
}

void shutdown() noexcept {
  gx::fifo::shutdown();
  gfx::render_worker::synchronize();
  m14_log_present_pacing("shutdown");
}

void finish_frame() {
  gfx::end_frame([rmlBindGroup = std::move(rmlBindGroup), rmlOverlay, viewport,
                  openspmDisplayCopySnapshot = std::move(openspmDisplayCopySnapshot),
                  imguiDrawData = std::move(imguiDrawData)](
                     wgpu::CommandEncoder& encoder, std::vector<gfx::AfterSubmitCallback> afterSubmitCallbacks) {
    finish_existing_frame();
  });
}
"""

    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp)
        target = source / "lib" / "aurora.cpp"
        texture_target = source / "lib" / "gfx" / "texture.cpp"
        target.parent.mkdir(parents=True)
        texture_target.parent.mkdir(parents=True)
        target.write_text(synthetic, encoding="utf-8")
        texture_target.write_text(
            "usage = wgpu::TextureUsage::TextureBinding | wgpu::TextureUsage::CopyDst | "
            "wgpu::TextureUsage::RenderAttachment,\n",
            encoding="utf-8",
        )

        assert dev139.transform(source, False) == "applied"
        first = target.read_text(encoding="utf-8")
        assert dev139.SIGNATURE in first
        assert first.count("struct M14PresenterImage {") == 1
        assert first.count("m14_encode_presenter_snapshot(encoder, openspmDisplayCopySnapshot)") == 1
        assert first.count("m14_stop_async_presenter();") == 1
        assert "g_m14PresenterQueueMutex" in first
        assert "g_m14PresenterWorkQueued" in first
        assert "gfx::render_worker::enqueue_work" in first
        assert "OpenSPM dev139 retained presenter must run on Aurora render worker" in first
        assert "webgpu_thread=render-worker" in first
        assert first.index("m14_stop_async_presenter();") < first.index("gfx::render_worker::synchronize();")
        assert "M14 dev139 presenter cadence presents={}" in first
        assert "presentCount != 0u && (presentCount & (presentCount - 1u)) == 0u" in first
        assert "M14 dev139 presenter summary source_publishes={}" in first
        first_texture = texture_target.read_text(encoding="utf-8")
        assert "wgpu::TextureUsage::CopySrc" in first_texture

        assert dev139.transform(source, False) == "already-applied"
        second = target.read_text(encoding="utf-8")
        second_texture = texture_target.read_text(encoding="utf-8")
        assert second == first
        assert second_texture == first_texture

        # Reproduce the already-installed first dev139 proof shape. The cached
        # source can have both a stale cadence body and the old retained-present
        # body that predates the render-worker ownership assertion.
        retained_start = dev139.STATE.index(
            "bool m14_present_retained_image(const M14PresenterImage& image) {"
        )
        retained_end = dev139.STATE.index(
            "\n\nvoid m14_presenter_main()", retained_start
        )
        canonical_retained = dev139.STATE[retained_start:retained_end]
        ownership_assert = """  AURORA_ASSERT(gfx::render_worker::is_worker_thread(),
                "OpenSPM dev139 retained presenter must run on Aurora render worker");
"""
        assert ownership_assert in canonical_retained
        legacy_retained = canonical_retained.replace(ownership_assert, "", 1)

        state_start = dev139.STATE.index("void m14_presenter_main() {")
        state_end = dev139.STATE.index(
            "\n\nvoid m14_start_async_presenter_locked()", state_start
        )
        canonical_presenter = dev139.STATE[state_start:state_end]
        legacy_stub = """void m14_presenter_main() {
  legacy_cross_thread_present();
}"""
        target.write_text(
            first.replace(canonical_retained, legacy_retained, 1)
                 .replace(canonical_presenter, legacy_stub, 1),
            encoding="utf-8",
        )
        assert dev139.transform(source, False) == "applied"
        migrated = target.read_text(encoding="utf-8")
        assert migrated == first
        assert migrated.count("struct M14PresenterImage {") == 1
        assert migrated.count(
            "bool m14_present_retained_image(const M14PresenterImage& image)"
        ) == 1
        assert migrated.count("void m14_presenter_main()") == 1
        assert (
            "OpenSPM dev139 retained presenter must run on Aurora render worker"
            in migrated
        )


def test_dev140_replay_transform_migration_contract() -> None:
    prep_path = ROOT / "renderer" / "aurora" / "apply_interpolation_replay_prep_patch.py"
    encode_path = ROOT / "renderer" / "aurora" / "apply_interpolation_replay_encode_patch.py"
    dev140a = _load_python_module(prep_path, "openspm_dev140a_replay_contract")
    dev140b = _load_python_module(encode_path, "openspm_dev140b_replay_contract")

    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp)
        for rel in (
            "lib/gfx/frame_packet.hpp",
            "lib/gfx/recording.hpp",
            "lib/gfx/recording.cpp",
            "lib/gfx/frame.hpp",
            "lib/gfx/frame.cpp",
            "lib/gfx/encoding.hpp",
            "lib/gfx/encoding.cpp",
            "lib/gx/pipeline.hpp",
            "lib/gx/command_processor.cpp",
            "lib/aurora.cpp",
        ):
            (source / rel).parent.mkdir(parents=True, exist_ok=True)

        (source / "lib/gfx/frame_packet.hpp").write_text(
            """\
struct DrawCommand {
  DrawEncoder encoder = nullptr;
  alignas(std::max_align_t) std::array<std::byte, InlineDrawPayloadSize> payload{};
};

enum class CommandType { Draw };

struct RenderPass {
  bool sealed = false;
};

struct FramePacket {
  AuroraStats stats{};
};
""",
            encoding="utf-8",
        )
        (source / "lib/gfx/recording.hpp").write_text(
            """\
#pragma once
#include <cstddef>

namespace aurora::gfx::detail {
void increment_merged_draw_count() noexcept;
}
""",
            encoding="utf-8",
        )
        (source / "lib/gfx/recording.cpp").write_text(
            """\
#include "frame.hpp"
#include <array>

FrameRecorder g_recorder;

std::string pass_label(std::string_view kind) {
  return std::string{kind};
}

void set_efb_targets(RenderPass& pass) {
  pass.sealed = false;
}

void synthetic_resume(const RenderPass& prevPass) {
  RenderPass a{
      .hasStencil = prevPass.hasStencil,
  };
  RenderPass b{
      .hasStencil = prevPass.hasStencil,
  };
}

template <>
void push_draw_command(gx::DrawData data) {
  push_draw_command(make_draw_command<gx::render>(data));
}

void increment_merged_draw_count() noexcept {
  ++g_recorder.mergedDrawCallCount;
}
""",
            encoding="utf-8",
        )
        (source / "lib/gfx/frame.hpp").write_text(
            """\
namespace aurora::gfx::detail {
const wgpu::Buffer& staging_buffer(size_t slot);
}
""",
            encoding="utf-8",
        )
        (source / "lib/gfx/frame.cpp").write_text(
            """\
namespace aurora::gfx {

size_t acquire_frame_slot() { return 0; }

void synthetic_end_frame() {
    auto afterSubmitCallbacks = std::move(packet.afterSubmitCallbacks);
    packet = {};
    g_resources.stats.drawCallCount = stats.drawCallCount;
    if (callback) {
      callback(encoder, std::move(afterSubmitCallbacks));
    }
    g_frameSlots.release(frameSlot);
}
}
""",
            encoding="utf-8",
        )
        (source / "lib/gfx/encoding.hpp").write_text(
            """\
#pragma once
#include "frame_packet.hpp"

namespace aurora::gfx {
namespace detail {
void encode_op();
}
bool bind_pipeline(PipelineRef ref, const wgpu::RenderPassEncoder& pass);
}
""",
            encoding="utf-8",
        )
        (source / "lib/gfx/encoding.cpp").write_text(
            """\
#include "encoding.hpp"
namespace aurora::gfx {
namespace detail {
void encode_op() {}
} // namespace detail

bool bind_pipeline(PipelineRef ref, const wgpu::RenderPassEncoder& pass) {
  return true;
}
}
""",
            encoding="utf-8",
        )
        (source / "lib/gx/pipeline.hpp").write_text(
            """\
struct DrawData {
  uint32_t dstAlpha;
};

constexpr uint32_t GXPipelineConfigVersion = 13;
""",
            encoding="utf-8",
        )
        (source / "lib/gx/command_processor.cpp").write_text(
            """\
void synthetic_push() {
  gfx::push_draw_command(DrawData{
      .dstAlpha = state.dstAlpha,
  });
}
""",
            encoding="utf-8",
        )
        (source / "lib/aurora.cpp").write_text(
            """\
#include "gfx/resources.hpp"
#include <magic_enum.hpp>

namespace aurora {
namespace {
bool g_initialFrame = false;

AuroraInfo initialize(int argc, char* argv[], const AuroraConfig& config) noexcept {
  return {};
}

void shutdown() noexcept {
  imgui::shutdown();
  gfx::shutdown();
}

void end_frame() noexcept {
  gfx::finish();
  auto imguiDrawData = imgui::freeze();

      const uint32_t m14PresenterSlot =
          m14_encode_presenter_snapshot(encoder, openspmDisplayCopySnapshot);
}
}
}
""",
            encoding="utf-8",
        )

        assert dev140a.transform(source, False) == "applied"
        prep_first = {
            rel: (source / rel).read_text(encoding="utf-8")
            for rel in (
                "lib/gfx/frame_packet.hpp",
                "lib/gfx/recording.cpp",
                "lib/gfx/frame.cpp",
                "lib/aurora.cpp",
                "lib/gx/pipeline.hpp",
                "lib/gx/command_processor.cpp",
            )
        }

        # Reproduce an interrupted/stale cached dev140a. The canonical design
        # keeps GX DrawData at Aurora's exact 128-byte inline payload and stores
        # replay metadata on DrawCommand. Simulate both a missing sidecar field
        # and the rejected earlier oversized-DrawData draft; migration must
        # converge without a cache wipe.
        stale_recording = prep_first["lib/gfx/recording.cpp"].replace(
            "uint64_t sM14ReplayNoMatchIntervals = 0;",
            "uint64_t sM14ReplayNoMatchIntervals = 999;",
            1,
        )
        stale_packet = prep_first["lib/gfx/frame_packet.hpp"].replace(
            "  bool m14ReplayIndexedMatrices = false;\n", "", 1
        )
        stale_pipeline = prep_first["lib/gx/pipeline.hpp"].replace(
            "  uint32_t dstAlpha;\n",
            "  uint32_t dstAlpha;\n"
            "  uint32_t m14ReplayUniformPrefixBytes = sizeof(float) * 4u;\n"
            "  bool m14ReplayIndexedMatrices = false;\n",
            1,
        )
        stale_cp = prep_first["lib/gx/command_processor.cpp"].replace(
            "      .dstAlpha = state.dstAlpha,\n",
            "      .dstAlpha = state.dstAlpha,\n"
            "      .m14ReplayUniformPrefixBytes = "
            "16u + (cache.lineMode != 0u ? 16u : 0u),\n"
            "      .m14ReplayIndexedMatrices = "
            "state.vtxDesc[GX_VA_PNMTXIDX] != GX_NONE,\n",
            1,
        )
        (source / "lib/gfx/recording.cpp").write_text(stale_recording, encoding="utf-8")
        (source / "lib/gfx/frame_packet.hpp").write_text(stale_packet, encoding="utf-8")
        (source / "lib/gx/pipeline.hpp").write_text(stale_pipeline, encoding="utf-8")
        (source / "lib/gx/command_processor.cpp").write_text(stale_cp, encoding="utf-8")
        assert dev140a.transform(source, False) == "applied"
        for rel, expected in prep_first.items():
            assert (source / rel).read_text(encoding="utf-8") == expected

        assert dev140a.transform(source, False) == "already-applied"
        for rel, expected in prep_first.items():
            assert (source / rel).read_text(encoding="utf-8") == expected

        prep_aurora = prep_first["lib/aurora.cpp"]
        assert prep_aurora.index("m14_prepare_interpolation_replay();") < prep_aurora.index(
            "gfx::finish();"
        ) < prep_aurora.index("imgui::freeze();")
        prep_frame = prep_first["lib/gfx/frame.cpp"]
        assert prep_frame.index("g_m14EndFramePacket = &packet;") < prep_frame.index(
            "callback(encoder, std::move(afterSubmitCallbacks))"
        ) < prep_frame.index("packet = {};")
        prep_packet = prep_first["lib/gfx/frame_packet.hpp"]
        assert prep_packet.count("m14ReplayUniformPrefixBytes = 16u") == 1
        assert prep_packet.count("m14ReplayIndexedMatrices = false") == 1
        assert prep_packet.count("m14ReplayGX = false") == 1
        assert "m14ReplayUniformPrefixBytes" not in prep_first["lib/gx/pipeline.hpp"]
        assert "m14ReplayIndexedMatrices" not in prep_first["lib/gx/pipeline.hpp"]
        assert prep_first["lib/gx/pipeline.hpp"].count(
            "OpenSPM dev140 must not grow GX DrawData beyond Aurora inline payload"
        ) == 1
        prep_cp = prep_first["lib/gx/command_processor.cpp"]
        assert prep_cp.count("gfx::detail::m14_push_gx_draw_command(DrawData{") == 1
        assert ".m14ReplayUniformPrefixBytes =" not in prep_cp
        assert ".m14ReplayIndexedMatrices =" not in prep_cp
        prep_recording = prep_first["lib/gfx/recording.cpp"]
        assert prep_recording.count("command.m14ReplayGX = true;") == 1

        assert dev140b.transform(source, False) == "applied"
        encode_first = {
            rel: (source / rel).read_text(encoding="utf-8")
            for rel in (
                "lib/gfx/frame_packet.hpp",
                "lib/gfx/recording.cpp",
                "lib/gfx/encoding.hpp",
                "lib/gfx/encoding.cpp",
                "lib/aurora.cpp",
            )
        }

        # Reproduce stale dev140b bodies from an earlier configure. Header,
        # encoder and Aurora state are independently canonicalized.
        stale_encoding_h = encode_first["lib/gfx/encoding.hpp"].replace(
            "  uint32_t priorLoadRejects = 0;",
            "  uint32_t oldPriorLoadRejects = 0;",
            1,
        )
        stale_encoding_cpp = encode_first["lib/gfx/encoding.cpp"].replace(
            "reject_prior_load={}", "reject_old_prior_load={}", 1
        )
        stale_aurora = encode_first["lib/aurora.cpp"].replace(
            "uint64_t g_m14ReplayEncodeFrames = 0;",
            "uint64_t g_m14ReplayEncodeFrames = 999;",
            1,
        )
        (source / "lib/gfx/encoding.hpp").write_text(stale_encoding_h, encoding="utf-8")
        (source / "lib/gfx/encoding.cpp").write_text(stale_encoding_cpp, encoding="utf-8")
        (source / "lib/aurora.cpp").write_text(stale_aurora, encoding="utf-8")
        assert dev140b.transform(source, False) == "applied"
        for rel, expected in encode_first.items():
            assert (source / rel).read_text(encoding="utf-8") == expected

        assert dev140b.transform(source, False) == "already-applied"
        for rel, expected in encode_first.items():
            assert (source / rel).read_text(encoding="utf-8") == expected

        packet = encode_first["lib/gfx/frame_packet.hpp"]
        assert packet.count("bool m14ReplayGX = false;") == 1
        assert packet.count("bool m14ReplayEfb = false;") == 1
        recording = encode_first["lib/gfx/recording.cpp"]
        assert recording.count("command.m14ReplayGX = true;") == 1
        assert recording.count("pass.m14ReplayEfb = true;") == 1
        assert recording.count(".m14ReplayEfb = prevPass.m14ReplayEfb,") == 2
        encoding = encode_first["lib/gfx/encoding.cpp"]
        assert encoding.count("bool m14_encode_interpolation_replay(") == 1
        aurora = encode_first["lib/aurora.cpp"]
        assert aurora.count("m14_encode_interpolation_replay_proof(encoder);") == 1
        assert aurora.index("m14_encode_interpolation_replay_proof(encoder);") < aurora.index(
            "m14_encode_presenter_snapshot(encoder, openspmDisplayCopySnapshot)"
        )
        assert aurora.count("m14_reset_replay_encode_targets();") == 1


def test_renderer_perf_transform_contracts() -> None:
    hotpath_path = ROOT / "renderer" / "aurora" / "apply_guest_array_hotpath_v2_patch.py"
    alias_path = ROOT / "renderer" / "aurora" / "apply_guest_array_staging_snapshot_alias_patch.py"
    rollback_path = ROOT / "renderer" / "aurora" / "apply_guest_array_staging_snapshot_rollback_patch.py"
    pool_path = ROOT / "renderer" / "aurora" / "apply_guest_array_snapshot_pool_patch.py"
    single_path = ROOT / "renderer" / "aurora" / "apply_guest_array_snapshot_single_copy_patch.py"
    drawdone_path = ROOT / "renderer" / "aurora" / "apply_drawdone_substage_profile_patch.py"
    worker_path = ROOT / "renderer" / "aurora" / "apply_render_worker_pacing_diag_patch.py"
    timing_path = ROOT / "renderer" / "aurora" / "apply_guest_array_timing_probe_patch.py"
    timing_retire_path = ROOT / "renderer" / "aurora" / "apply_guest_array_timing_probe_retirement_patch.py"
    cp_duplicate_path = ROOT / "renderer" / "aurora" / "apply_cp_duplicate_pipeline_patch.py"
    fifo_companion_path = ROOT / "renderer" / "aurora" / "apply_fifo_reentrancy_patch.py"
    bytebuffer_companion_path = ROOT / "renderer" / "aurora" / "apply_bytebuffer_abort_diag_patch.py"
    display_snapshot_path = ROOT / "renderer" / "aurora" / "apply_display_copy_snapshot_patch.py"
    fifo_inline_path = ROOT / "renderer" / "aurora" / "apply_fifo_inline_mode_patch.py"
    present_mode_path = ROOT / "renderer" / "aurora" / "apply_present_mode_override_patch.py"
    present_tail_path = ROOT / "renderer" / "aurora" / "apply_present_tail_diag_patch.py"
    display_cadence_path = ROOT / "renderer" / "aurora" / "apply_display_cadence_diag_patch.py"
    interp_probe_path = ROOT / "renderer" / "aurora" / "apply_transform_interpolation_probe_patch.py"
    interp_identity_path = ROOT / "renderer" / "aurora" / "apply_transform_interpolation_identity_patch.py"
    async_presenter_path = ROOT / "renderer" / "aurora" / "apply_async_presenter_probe_patch.py"
    interp_replay_prep_path = ROOT / "renderer" / "aurora" / "apply_interpolation_replay_prep_patch.py"
    interp_replay_encode_path = ROOT / "renderer" / "aurora" / "apply_interpolation_replay_encode_patch.py"
    root_cmake_path = ROOT / "CMakeLists.txt"

    hotpath = hotpath_path.read_text(encoding="utf-8")
    alias = alias_path.read_text(encoding="utf-8")
    rollback = rollback_path.read_text(encoding="utf-8")
    pool = pool_path.read_text(encoding="utf-8")
    single = single_path.read_text(encoding="utf-8")
    drawdone = drawdone_path.read_text(encoding="utf-8")
    worker = worker_path.read_text(encoding="utf-8")
    timing = timing_path.read_text(encoding="utf-8")
    timing_retire = timing_retire_path.read_text(encoding="utf-8")
    cp_duplicate = cp_duplicate_path.read_text(encoding="utf-8")
    fifo_companion = fifo_companion_path.read_text(encoding="utf-8")
    bytebuffer_companion = bytebuffer_companion_path.read_text(encoding="utf-8")
    display_snapshot = display_snapshot_path.read_text(encoding="utf-8")
    fifo_inline = fifo_inline_path.read_text(encoding="utf-8")
    present_mode = present_mode_path.read_text(encoding="utf-8")
    present_tail = present_tail_path.read_text(encoding="utf-8")
    display_cadence = display_cadence_path.read_text(encoding="utf-8")
    interp_probe = interp_probe_path.read_text(encoding="utf-8")
    interp_identity = interp_identity_path.read_text(encoding="utf-8")
    async_presenter = async_presenter_path.read_text(encoding="utf-8")
    interp_replay_prep = interp_replay_prep_path.read_text(encoding="utf-8")
    interp_replay_encode = interp_replay_encode_path.read_text(encoding="utf-8")
    root_cmake = root_cmake_path.read_text(encoding="utf-8")

    # Compile every active renderer transform as Python before touching the
    # player's cached Aurora checkout. This catches syntax drift in the exact
    # scripts CMake will execute during the automatic reconfigure.
    for path, source in (
        (hotpath_path, hotpath),
        (alias_path, alias),
        (rollback_path, rollback),
        (pool_path, pool),
        (single_path, single),
        (drawdone_path, drawdone),
        (worker_path, worker),
        (timing_path, timing),
        (timing_retire_path, timing_retire),
        (cp_duplicate_path, cp_duplicate),
        (fifo_companion_path, fifo_companion),
        (bytebuffer_companion_path, bytebuffer_companion),
        (display_snapshot_path, display_snapshot),
        (fifo_inline_path, fifo_inline),
        (present_mode_path, present_mode),
        (present_tail_path, present_tail),
        (display_cadence_path, display_cadence),
        (interp_probe_path, interp_probe),
        (interp_identity_path, interp_identity),
        (async_presenter_path, async_presenter),
        (interp_replay_prep_path, interp_replay_prep),
        (interp_replay_encode_path, interp_replay_encode),
    ):
        ast.parse(source, filename=str(path))

    assert "M14 raw guest-array stage-head index + scan-plan active" in hotpath
    assert "kM14GuestArrayStageBuckets = 2048u" in hotpath
    assert "previousBucketEntry" in hotpath
    assert "if (scan.indexCount == 1u)" in hotpath
    assert "if (vtxCount == 0u) continue;" in hotpath
    assert "M14 raw guest-array fused scan-plan active" in hotpath
    assert "for (u16 vertex = 0; vertex < vtxCount; ++vertex, vertexBase += recordBytes)" in hotpath
    assert "scan.minIndex = std::min(scan.minIndex, index);" in hotpath
    assert "scan.minByte = std::min(scan.minByte, static_cast<u32>(begin));" in hotpath
    assert "std::unordered_multimap<const void*, u32> sM14GuestArrayStageLookup;" in hotpath

    alias_signature = (
        "M14 raw guest-array staging bytes are exact snapshots; "
        "duplicate CPU snapshot copy retired"
    )
    assert alias_signature in alias
    assert "const uint8_t* storage_range_data(Range range) noexcept;" in alias
    assert "const uint8_t* storage_range_data(Range range) noexcept {" in alias
    assert "gfx::storage_range_data(entry.range)" in alias
    assert "std::memcmp(snapshot, source, uploadSize)" in alias
    assert '"std::vector<u8> sM14GuestArraySnapshotBytes;"' in alias
    assert '"sM14GuestArraySnapshotBytes.insert("' in alias
    assert "STAGING_ALIAS_SIGNATURE" in pool
    assert "STAGING_ALIAS_SIGNATURE" in single
    assert "M14 dev94 CPU-cached exact guest-array snapshots restored" in rollback
    assert "std::vector<u8> sM14GuestArraySnapshotBytes;" in rollback
    assert "sM14GuestArraySnapshotBytes.insert(" in rollback
    assert "gfx::storage_range_data(entry.range)" in rollback
    assert 'RETIRED_SIGNATURE = "M14 DrawDone per-draw profiler retired for normal play"' in drawdone
    assert 'return "already-retired"' in drawdone
    assert "apply_drawdone_substage_profile_patch.py" not in worker
    assert 'RETIRED_SIGNATURE = "M14 raw guest-array stage timing probe retired for normal play"' in timing
    assert 'return "already-retired"' in timing
    assert "M14 raw guest-array stage timing probe retired for normal play" in timing_retire
    assert "profileFinish" in timing_retire

    assert "M14 exact duplicate CP array-base pipeline reuse active" in cp_duplicate
    assert "dirtyBeforeBase" in cp_duplicate
    assert "g_gxState.dirty = dirtyBeforeBase;" in cp_duplicate
    assert "apply_cp_duplicate_pipeline_patch.py" in fifo_companion
    assert "apply_bytebuffer_abort_diag_patch.py" in fifo_companion
    assert "apply_display_copy_snapshot_patch.py" in bytebuffer_companion
    assert "OpenSPM M14 Wii display-copy XFB snapshot active" in display_snapshot
    assert "gfx::TextureHandle openspmDisplayCopySnapshot;" in display_snapshot
    assert "openspmDisplayCopySnapshot = it->second.handle;" in display_snapshot
    assert "openspmDisplayCopySnapshot = std::move(openspmDisplayCopySnapshot)" in display_snapshot
    assert "std::shared_ptr<TextureRef>" not in display_snapshot  # handle type remains Aurora-owned
    assert "copy-time resolve, not the live EFB" in display_snapshot
    assert 'std::getenv("OPENSPM_M14_FIFO_MODE")' in fifo_inline
    assert 'std::strcmp(mode, "inline") == 0' in fifo_inline
    assert "ProcessingMode::Inline" in fifo_inline
    assert "ProcessingMode::Drain" in fifo_inline
    assert "void publish() noexcept { fifo::publish(); }" in fifo_inline
    assert "apply_fifo_inline_mode_patch.py" in root_cmake
    assert "OPENSPM_AURORA_FIFO_THREAD_LINE" not in root_cmake
    assert "OPENSPM_AURORA_FIFO_DRAIN_LINE" not in root_cmake
    assert 'std::getenv("OPENSPM_M14_PRESENT_MODE")' in present_mode
    assert 'mode == "immediate"' in present_mode
    assert 'mode == "mailbox"' in present_mode
    assert 'mode == "fifo"' in present_mode
    assert "apply_present_mode_override_patch.py" in root_cmake
    assert "g_m14PresentTailCount" in present_tail
    assert "M14 host present tail n={}" in present_tail
    assert "intervalUs > 25000u" in present_tail
    assert "apply_present_tail_diag_patch.py" in root_cmake
    assert "M14 display cadence current=" in display_cadence
    assert "SDL_GetDisplayForWindow(g_window)" in display_cadence
    assert "SDL_GetCurrentDisplayMode(display)" in display_cadence
    assert "refresh_rate_numerator" in display_cadence
    assert "ratio60" in display_cadence
    assert "integer_multiple={}" in display_cadence
    assert "apply_display_cadence_diag_patch.py" in root_cmake

    assert "M14 dev136 GX transform interpolation probe active" in interp_probe
    assert 'SUCCESSOR_SIGNATURE = "M14 dev138 geometry-identity interpolation probe active"' in interp_probe
    assert "if SUCCESSOR_SIGNATURE in cp:" in interp_probe
    assert "return \"already-applied\"" in interp_probe
    assert 'std::getenv("OPENSPM_M14_INTERP_PROBE")' in interp_probe
    assert "std::array<PnMtx, MaxPnMtx> matrices" in interp_probe
    assert "m14_interp_probe_record_draw" in interp_probe
    assert "m14_interp_probe_frame_boundary" in interp_probe
    assert "safe_midpoints" in interp_probe
    assert "apply_transform_interpolation_probe_patch.py" in root_cmake

    assert "M14 dev138 geometry-identity interpolation probe active" in interp_identity
    assert 'OPENSPM_M14_INTERP_PROBE_INTERVAL' in interp_identity
    assert "normalize_boundary_surfaces" in interp_identity
    assert "m14_interp_geometry_signature" in interp_identity
    assert "m14_interp_probe_extend_last_geometry" in interp_identity
    assert "exactAmbiguous" in interp_identity
    assert "materialFallbackUnique" in interp_identity
    assert "sM14InterpCaptureActive" in interp_identity
    assert "M14 dev138 identity sample={}" in interp_identity
    assert '"M14 dev138 identity sample="' in interp_identity
    assert '"M14 dev138 identity frame="' not in interp_identity
    assert "build_uniform(" not in interp_identity
    assert "apply_transform_interpolation_identity_patch.py" in root_cmake
    assert root_cmake.index("apply_transform_interpolation_probe_patch.py") < root_cmake.index(
        "apply_transform_interpolation_identity_patch.py"
    )
    assert "apply_transform_interpolation_visual_patch.py" not in root_cmake

    assert "M14 dev139 decoupled host presenter active" in async_presenter
    assert 'state_sentinel = "struct M14PresenterImage {"' in async_presenter
    assert "if SIGNATURE not in text:" not in async_presenter
    assert 'std::getenv("OPENSPM_M14_ASYNC_PRESENTER")' in async_presenter
    assert 'std::getenv("OPENSPM_INTERPOLATION_TARGET_FPS")' in async_presenter
    assert "std::array<M14PresenterImage, 2>" in async_presenter
    assert "m14_encode_presenter_snapshot" in async_presenter
    assert "TEXTURE_COPY_SRC_NEW" in async_presenter
    assert "wgpu::TextureUsage::CopySrc" in async_presenter
    assert "m14_publish_presenter_slot(m14PresenterSlot);" in async_presenter
    assert "g_m14PresenterQueueMutex" in async_presenter
    assert "g_m14PresenterWorkQueued" in async_presenter
    assert "gfx::render_worker::enqueue_work" in async_presenter
    assert "OpenSPM dev139 retained presenter must run on Aurora render worker" in async_presenter
    assert "webgpu_thread=render-worker" in async_presenter
    assert "M14 dev139 presenter cadence presents={}" in async_presenter
    assert "presentCount != 0u && (presentCount & (presentCount - 1u)) == 0u" in async_presenter
    assert "M14 dev139 presenter summary source_publishes={}" in async_presenter

    assert "M14 dev145 replay prep active" in interp_replay_prep
    assert 'std::getenv("OPENSPM_M14_INTERP_REPLAY")' in interp_replay_prep
    assert "phase_source=rational-display-grid" in interp_replay_prep
    assert "M14 dev140 replay cadence requested_hz={}" in interp_replay_prep
    assert "SDL_GetCurrentDisplayMode" in interp_replay_prep
    assert "sM14ReplayCadencePhase" in interp_replay_prep
    assert "m14_prepare_interpolation_replay();" in interp_replay_prep
    assert "DrawData is exactly Aurora's 128-byte inline payload" in interp_replay_prep
    assert "DRAW_COMMAND_FIELDS" in interp_replay_prep
    assert "m14ReplayUniformPrefixBytes = 16u" in interp_replay_prep
    assert "m14ReplayIndexedMatrices = false" in interp_replay_prep
    assert "m14ReplayGX = false" in interp_replay_prep
    assert "gfx::detail::m14_push_gx_draw_command(DrawData{" in interp_replay_prep
    assert "16u + (cache.lineMode != 0u ? 16u : 0u)" in interp_replay_prep
    assert "state.vtxDesc[GX_VA_PNMTXIDX] != GX_NONE" in interp_replay_prep
    assert "if (indexedMatrices) return false;" in interp_replay_prep
    assert "replay metadata leaked into 128-byte GX DrawData" in interp_replay_prep
    assert "OpenSPM dev140 must not grow GX DrawData beyond Aurora inline payload" in interp_replay_prep
    assert "InlineDrawPayloadSize = 160" not in interp_replay_prep
    assert "g_m14EndFramePacket = &packet;" in interp_replay_prep
    assert "packet = {};\\n    g_frameSlots.release(frameSlot);" in interp_replay_prep
    assert "prepare_pos < finish_pos < freeze_pos" in interp_replay_prep
    assert "m14_replay_required_uniform_bytes(cur)" in interp_replay_prep
    assert "finishReserve = static_cast<size_t>(gx::MaxUniformSize)" in interp_replay_prep
    assert "Non-indexed draws use exactly currentPnMtx. Preserve the current basis" in interp_replay_prep
    assert "patch only that active matrix's translation" in interp_replay_prep
    assert "const uint32_t i = current.currentPnMtx;" in interp_replay_prep
    assert "Mat3x4<float> position{}" in interp_replay_prep
    assert "std::array<gx::PnMtx, gx::MaxPnMtx> matrices{}" not in interp_replay_prep
    assert "Projection stays authoritative/current in dev140." in interp_replay_prep
    assert "m14_replay_same_basis" in interp_replay_prep
    assert "Any projection change" in interp_replay_prep
    assert "std::memcmp(&previous.projection, &current.projection" in interp_replay_prep
    assert "m14_replay_lerp" not in interp_replay_prep
    assert "empty/transition/no-match frames cannot shift the display" in interp_replay_prep
    assert "if (frame.m14ReplayMatchedUniforms == 0u)" in interp_replay_prep
    assert "M14 dev140 replay prep skip interval=" in interp_replay_prep
    assert "matched_draws={}" in interp_replay_prep
    assert "unmatched_draws={}" in interp_replay_prep
    assert "indexed_snap={}" in interp_replay_prep
    assert "snapshot_reject={}" in interp_replay_prep
    assert "apply_interpolation_replay_prep_patch.py" in root_cmake

    assert "M14 dev140b offscreen replay encode active" in interp_replay_encode
    assert 'std::getenv("OPENSPM_M14_INTERP_REPLAY_ENCODE")' in interp_replay_encode
    assert "bool m14ReplayGX = false;" in interp_replay_encode
    assert "bool m14ReplayEfb = false;" in interp_replay_encode
    assert "command.m14ReplayGX = true;" in interp_replay_encode
    assert "pass.m14ReplayEfb = true;" in interp_replay_encode
    assert "m14_encode_interpolation_replay(" in interp_replay_encode
    assert "encoder.CopyBufferToBuffer(" in interp_replay_encode
    assert "staging high-water marks" in interp_replay_encode
    assert "gfx::detail::staging_buffer(frame->stagingBuffer)" in interp_replay_encode
    assert "gfx::detail::resources().uniformBuffer" in interp_replay_encode
    assert "authoritative_efb_untouched=1" in interp_replay_encode
    assert "m14_encode_interpolation_replay_proof(encoder);" in interp_replay_encode
    assert "m14_reset_replay_encode_targets();" in interp_replay_encode
    assert "efb_copy_passes={}" in interp_replay_encode
    assert "reject_layout={}" in interp_replay_encode
    assert "reject_prior_load={}" in interp_replay_encode
    assert "reject_custom={}" in interp_replay_encode
    assert "reject_range={}" in interp_replay_encode
    assert '("frame_cpp", "g_m14EndFramePacket = &packet;")' in interp_replay_encode
    assert '("aurora", "g_m14EndFramePacket = &packet;")' not in interp_replay_encode
    assert "apply_interpolation_replay_encode_patch.py" in root_cmake
    assert "interpolation=off" in async_presenter
    assert "apply_async_presenter_probe_patch.py" in root_cmake
    assert root_cmake.index("apply_transform_interpolation_identity_patch.py") < root_cmake.index(
        "apply_async_presenter_probe_patch.py"
    )
    assert root_cmake.index("apply_async_presenter_probe_patch.py") < root_cmake.index(
        "apply_interpolation_replay_prep_patch.py"
    )
    assert root_cmake.index("apply_interpolation_replay_prep_patch.py") < root_cmake.index(
        "apply_interpolation_replay_encode_patch.py"
    )

    assert "apply_guest_array_hotpath_v2_patch.py" in root_cmake
    assert "apply_guest_array_staging_snapshot_rollback_patch.py" in root_cmake
    assert "apply_guest_array_staging_snapshot_alias_patch.py" not in root_cmake
    assert "apply_guest_array_timing_probe_retirement_patch.py" in root_cmake
    assert root_cmake.index("apply_guest_array_hotpath_v2_patch.py") < root_cmake.index(
        "apply_guest_array_staging_snapshot_rollback_patch.py"
    ) < root_cmake.index("apply_guest_array_timing_probe_retirement_patch.py")


def test_dev140_replay_migration_contract() -> None:
    tests = _load_python_module(
        ROOT / "renderer" / "aurora" / "test_interpolation_replay_prep.py",
        "openspm_dev140_replay_tests")
    tests.test_transform()
    tests.test_snapshot_successor()
    tests.test_readback_successor()


def test_vanilla_display_pacing_contract() -> None:
    tests = _load_python_module(
        ROOT / "renderer" / "aurora" / "test_display_pacing.py",
        "openspm_display_pacing_tests")
    tests.test_policy()
    tests.test_contract()
    tests.test_finish_before_retrace_contract()
    tests.test_bbox_logical_contract()


def test_build_contract_text() -> None:
    cmake = (HERE / "CMakeLists.txt").read_text(encoding="utf-8")
    seam_bridge = (HERE / "legacy_c_adapter_v15.c.in").read_text(encoding="utf-8")
    wpar_bridge = (HERE / "legacy_c_adapter_v16.c.in").read_text(encoding="utf-8")
    inline_bridge = (HERE / "legacy_c_adapter_v17.c.in").read_text(encoding="utf-8")
    phase_bridge = (HERE / "legacy_c_adapter_v23.c.in").read_text(encoding="utf-8")
    dev68_bridge = (HERE / "legacy_c_adapter_v7.c.in").read_text(encoding="utf-8")
    dev13_bridge = (HERE / "legacy_c_adapter_v13.c.in").read_text(encoding="utf-8")
    dev79_bridge = (HERE / "legacy_c_adapter_v42_dev69.c.in").read_text(encoding="utf-8")
    active_v42_bridge = (HERE / "legacy_c_adapter_v42.c.in").read_text(encoding="utf-8")
    relf_tail_bridge = (HERE / "legacy_c_adapter_v24.c.in").read_text(encoding="utf-8")
    relf_bss_bridge = (HERE / "legacy_c_adapter_v25.c.in").read_text(encoding="utf-8")
    hostcall_bridge = (HERE / "legacy_c_adapter_v26.c.in").read_text(encoding="utf-8")
    hostcall_deriver = (HERE / "derive_hostcall_llvm_seams.py").read_text(encoding="utf-8")
    seam_deriver = (HERE / "derive_v19_llvm_seams.py").read_text(encoding="utf-8")
    generator = (HERE / "generate_local_llvm.py").read_text(encoding="utf-8")
    observed_indirect_text = (HERE / "augment_llvm_observed_indirect_entries.py").read_text(encoding="utf-8")
    bl_partition_text = (HERE / "augment_llvm_bl_continuations.py").read_text(encoding="utf-8")
    hostcall_prefilter_text = (HERE / "apply_llvm_hostcall_prefilter.py").read_text(encoding="utf-8")
    module_builder = (HERE / "build_local_llvm_module.py").read_text(encoding="utf-8")
    native_main = (ROOT / "launcher" / "native_main.c").read_text(encoding="utf-8")
    idle_wake_test = (HERE / "test_llvm_idle_wake.py").read_text(encoding="utf-8")
    rel_builder = (HERE / "build_relf_codegen_dol.py").read_text(encoding="utf-8")
    patcher = (HERE / "apply_openspm_llvm_wpar_codegen_patch.py").read_text(encoding="utf-8")
    active_patch_base = (HERE / "_apply_openspm_llvm_codegen_patch_base.py").read_text(encoding="utf-8")
    retained_dev71_base = (HERE / "_apply_openspm_llvm_codegen_patch_base_dev71.py").read_text(encoding="utf-8")
    preparer = (HERE / "prepare_dolrecomp_llvm.py").read_text(encoding="utf-8")
    validator_text = (HERE / "validate_llvm_generated.py").read_text(encoding="utf-8")
    runner = (HERE / "run_m14_llvm_candidate.py").read_text(encoding="utf-8")
    relf_loader = (HERE / "relf_input.py").read_text(encoding="utf-8")
    cmd = (ROOT / "M14-LLVM-UPDATE-RUN.cmd").read_text(encoding="utf-8")
    renderer = (
        ROOT / "renderer" / "aurora" / "openspm_renderer_aurora.cpp"
    ).read_text(encoding="utf-8")

    assert 'set(OPENSPM_GENERATED_BACKEND "c"' in cmake
    assert 'set(OPENSPM_ACTIVE_BRIDGE "${CMAKE_CURRENT_LIST_DIR}/legacy_c_adapter_v13.c.in")' in cmake
    assert 'set(OPENSPM_ACTIVE_BRIDGE "${CMAKE_CURRENT_LIST_DIR}/legacy_c_adapter_v26.c.in")' in cmake
    assert "m14-dev36-llvm-hostcall-direct-parity" in cmake
    assert 'set(OPENSPM_LLVM_CHUNK_INSTRUCTIONS "256"' in cmake
    assert 'set(OPENSPM_LLVM_STATE_IN_MEMORY "0"' in cmake
    assert 'set(OPENSPM_LLVM_HEAVY_DIAGNOSTICS "0"' in cmake
    assert "OPENSPM_M14_LLVM_CHUNK_INSTRUCTIONS=" in cmake
    assert "OPENSPM_M14_LLVM_STATE_IN_MEMORY=" in cmake
    assert "OPENSPM_M14_HEAVY_DIAGNOSTICS=" in cmake
    assert "OPENSPM_GENERATED_BACKEND STREQUAL \"llvm\"" in cmake
    assert "--print-cmake" in cmake
    assert "--require-openspm-wpar" in cmake
    assert "DOLRECOMP_BACKEND_LLVM" in seam_bridge
    assert "openspm_m14_display_list_begin_before_native_v19" in seam_bridge
    assert "openspm_m14_display_list_end_after_return_v19" in seam_bridge
    assert "openspm_m14_try_wg_write_llvm" in wpar_bridge
    assert "openspm_m14_llvm_wg_bytes_ptr" in inline_bridge
    assert "openspm_m14_flush_wg_llvm" in inline_bridge
    assert "M14 LLVM frame-phase telemetry is nonblocking:" in phase_bridge
    assert "M14 LLVM dev79 rescue cadence correlation active:" in dev79_bridge
    assert "g_m14_dev50_bl_rescues" in dev79_bridge
    assert "M14 LLVM dev81 native frame-phase profiler active:" in dev79_bridge
    assert "void openspm_m14_note_frame_phase_native(u32 phase)" in dev79_bridge
    assert "void openspm_m14_note_death_entry_native(u32 pc)" in dev79_bridge
    assert "native_onHp0=%llu(+%llu)" in dev79_bridge
    assert "native_seqSetSeq=%llu(+%llu)" in dev79_bridge
    assert "openspm_m14_note_frame_phase_v20(&instance->cpu, phase);" in dev79_bridge
    assert "if (phase == 2u)" in dev79_bridge
    assert "openspm_m14_note_spmario_main_v20(&instance->cpu);" in dev79_bridge
    assert "M14 LLVM dev85 guard-quantum A/B active:" in dev79_bridge
    assert "M14 LLVM dev113 cadence chunk=%u state_mem=%u host_ms=" in dev79_bridge
    assert "nested_steps_per_attempt_x1000=" in dev79_bridge
    assert "OPENSPM_M14_LLVM_CHUNK_INSTRUCTIONS" in dev79_bridge
    assert "OPENSPM_M14_LLVM_STATE_IN_MEMORY" in dev79_bridge
    assert "OPENSPM_M14_HEAVY_DIAGNOSTICS" in dev79_bridge
    assert "#if OPENSPM_M14_HEAVY_DIAGNOSTICS" in dev79_bridge
    assert "M14 LLVM dev121 quiet normal-play diagnostics mode:" in dev79_bridge
    assert "OPENSPM_M14_EXPERIMENT_VERSION" in dev79_bridge
    assert "m14-dev122-chunk" in dev79_bridge
    assert "M14 LLVM dev114 state-in-memory A/B selected=" in dev79_bridge
    assert "OPENSPM_M14_STRINGIFY" in dev79_bridge
    assert "ordinary_rate_per_s=" in dev79_bridge
    assert "budget_exit_rate_per_s=" in dev79_bridge
    assert "M14 LLVM dev129 scheduler-idle dynamic dispatch active:" in dev79_bridge
    assert "M14 LLVM dev90 structural native-first stats hits=" in dev79_bridge
    assert "M14 LLVM dev108 partition-only A/B active:" in dev79_bridge
    assert "M14 LLVM dev108 direct-BL A/B stats disabled_hits=" in dev79_bridge
    assert "openspm_m14_dev107_direct_bl_native_hits = 0u;" in dev79_bridge
    assert "openspm_m14_dev107_direct_bl_native_fallbacks = 0u;" in dev79_bridge
    assert "M14 LLVM dev96 offscreen/death truth probe active:" in dev79_bridge
    assert "OPENSPM_M14_DEV95_SEQ_SET_SEQ UINT32_C(0x8017B834)" in dev79_bridge
    assert "OPENSPM_M14_DEV95_SEQ_WORK    UINT32_C(0x804D0D60)" in dev79_bridge
    assert "OPENSPM_M14_DEV96_NOW_SEQ     UINT32_C(0x8056CAB8)" in dev79_bridge
    assert "OPENSPM_M14_DEV96_NEXT_SEQ    UINT32_C(0x8056CABC)" in dev79_bridge
    assert "OPENSPM_M14_DEV96_PREV_SEQ    UINT32_C(0x8056CAC0)" in dev79_bridge
    assert "OPENSPM_M14_DEV96_MARIO_WORK   UINT32_C(0x804CEE58)" in dev79_bridge
    assert "OPENSPM_M14_DEV96_POUCH_WORK   UINT32_C(0x804D0428)" in dev79_bridge
    assert "OPENSPM_M14_DEV96_POUCH_HP_OFF UINT32_C(0x0000000C)" in dev79_bridge
    assert "OPENSPM_M14_DEV96_MARIO_MOTION_OFF UINT32_C(0x0000002C)" in dev79_bridge
    assert "OPENSPM_M14_DEV96_MARIO_POS_OFF UINT32_C(0x0000005C)" in dev79_bridge
    assert "OPENSPM_M14_DEV96_BBOX_LEFT   UINT32_C(0xCC001010)" in dev79_bridge
    assert "OPENSPM_M14_DEV96_BBOX_BOTTOM UINT32_C(0xCC001016)" in dev79_bridge
    assert "M14 LLVM dev95 seqSetSeq request=" in dev79_bridge
    assert "M14 LLVM dev96 sequence truth where=" in dev79_bridge
    assert "M14 LLVM dev96 player state where=" in dev79_bridge
    assert "bridge_dev96_note_player_state(instance, \"spmarioMain\");" in dev79_bridge
    assert "M14 LLVM dev96 empty PE bbox n=" in dev79_bridge
    assert "hp=%d mario_flags=0x%08X disp_flags=0x%08X motion=%u pos=" in dev79_bridge
    assert "M14 LLVM dev96 sequence/bbox truth stats" in dev79_bridge
    assert "M14 LLVM dev99 native-ABI dispDraw nesting retained:" in dev79_bridge
    assert "openspm_m14_dev99_nested_dispatch" in dev79_bridge
    assert "dolrecomp_call_enter()" in dev79_bridge
    assert "handled = dolrecomp_call(cpu, target);" in dev79_bridge
    assert "instance->fallback_requested" in dev79_bridge
    assert "OPENSPM_M14_DEV105_CPUSTATE_NESTED_QUANTUM INT64_C(32768)" in dev79_bridge
    assert "openspm_m14_dev105_cpustate_nested_dispatch" in dev79_bridge
    assert "M14 LLVM dev111 NPC/display + Mario-motion probe active" in dev79_bridge
    assert "bridge_dev111_note_disp_target" in dev79_bridge
    assert "bridge_dev111_note_motion" in dev79_bridge
    assert "M14 LLVM dev112 NPC callback ownership probe active" in dev79_bridge
    assert "M14 LLVM dev115 map-range callback census active" in dev79_bridge
    assert "OPENSPM_M14_DEV115_MAP_TARGET UINT32_C(0x8008533C)" in dev79_bridge
    assert "bridge_dev115_note_map_callback" in dev79_bridge
    assert "unique_params=%u" in dev79_bridge
    assert "M14 LLVM dev113 chunk" in dev79_bridge
    assert "bridge_dev112_note_disp_owner" in dev79_bridge
    assert "param_in_table=" in dev79_bridge
    assert "OPENSPM_M14_DEV105_CPUSTATE_NESTED_MAX_STEPS 128u" in dev79_bridge
    assert "g_m14_dev105_nested_steps" in dev79_bridge
    assert "g_m14_dev105_step_limit_yields" in dev79_bridge
    assert "g_m14_dev105_no_progress_yields" in dev79_bridge
    assert "g_m14_dev117_same_pc_progress" in dev79_bridge
    assert "before_downcount = cpu->downcount;" in dev79_bridge
    assert "cpu->downcount < before_downcount" in dev79_bridge
    assert "++g_m14_dev117_same_pc_progress;" in dev79_bridge
    assert "M14 LLVM dev106 jump-death truth n=" in dev79_bridge
    assert "OPENSPM_M14_DEV105_ON_HP_EQUALS_0 UINT32_C(0x801615E4)" in dev79_bridge
    assert "bridge_dev105_note_hp0_motion24(instance);" in dev79_bridge
    assert "M14 LLVM dev106 jump-death stats" in dev79_bridge
    # dev105's multiblock walker guards the charge quantum at three exact
    # points: before entering the walk, at exact-continuation completion, and
    # before each additional generated chunk. dev104's single-step helper had
    # only two checks, so retaining ==2 here incorrectly rejects the new shape.
    assert dev79_bridge.count(
        "cpu->downcount <= -OPENSPM_M14_DEV105_CPUSTATE_NESTED_QUANTUM"
    ) == 3
    assert "M14 LLVM dev105 dispDraw nested stats" in dev79_bridge
    assert "M14 LLVM dev117 CPUState dispDraw progress-aware nesting active:" in dev79_bridge
    assert "M14 LLVM dev118 retrace pacing probe active:" in dev79_bridge
    assert "M14 LLVM dev122 tail attribution active over dev121 quiet path:" in dev79_bridge
    assert "OPENSPM_M14_DEV122_FRAME_TAIL_NS" in dev13_bridge
    assert "M14 dev122 frame tail n=" in dev13_bridge
    assert "M14 dev122 camera tail n=" in dev79_bridge
    assert "OPENSPM_M14_DEV122_CAM_PTR_TABLE" in dev79_bridge
    assert "#if !OPENSPM_M14_HEAVY_DIAGNOSTICS" in dev79_bridge
    assert "return g_m14_dev67_underlying_run(opaque, soft_cycle_budget, out_exit);" in dev79_bridge
    assert "dev67 per-slice clocks/histograms" in dev79_bridge
    assert "sequence/HP/bbox observation wrappers are not installed in normal play" in dev79_bridge
    assert "OPENSPM_M14_DEV118_FINE_WAIT_NS UINT64_C(2000000)" in dev68_bridge
    assert "OPENSPM_M14_PRECISE_IDLE_WAIT" in dev68_bridge
    assert "OPENSPM_M14_FINAL_IDLE_SPIN" in dev68_bridge
    assert "bridge_m14_dev126_final_spin_enabled" in dev68_bridge
    assert "YieldProcessor();" in dev68_bridge
    assert "M14 dev126 final-idle-spin A/B active:" in dev68_bridge
    assert "g_m14_dev128_phase_id" in dev68_bridge
    assert "g_m14_dev128_phase_enter_ns" in dev68_bridge
    assert "M14 dev128 busy-due owner n=" in dev68_bridge
    assert "OPENSPM_M14_DEV129_IDLE_PARK_DETAIL" in dev68_bridge
    assert "OPENSPM_M14_DEFAULT_RETRACE_PERIOD_NS UINT64_C(16666667)" in dev68_bridge
    assert "OPENSPM_M14_DEV132_HALF_99_943_PERIOD_NS UINT64_C(20011407)" in dev68_bridge
    assert "OPENSPM_M14_DIAG_HALF_99_943HZ" in dev68_bridge
    assert "bridge_m14_retrace_period_ns" in dev68_bridge
    assert "M14 dev132 diagnostic cadence active:" in dev68_bridge
    assert "bridge_m14_idle_runqueue_state" in dev68_bridge
    assert "g_m14_dev129_empty_idle_sideexits" in dev68_bridge
    assert "g_m14_dev129_runnable_idle_native" in dev68_bridge
    assert "segment.detail == OPENSPM_M14_DEV129_IDLE_PARK_DETAIL" in dev68_bridge
    assert "M14 LLVM dev129 scheduler-idle dynamic stats" in dev79_bridge
    assert "dev129's exact in-slice scheduler-idle decision" in idle_wake_test
    assert "bridge_host_call_v7(&i.cpu, OPENSPM_M14_SCHEDULER_IDLE_PC)" in idle_wake_test
    assert "OPENSPM_M14_DEV129_IDLE_PARK_DETAIL" in idle_wake_test
    assert "g_m14_dev129_runnable_idle_native == 1u" in idle_wake_test
    assert "g_m14_dev129_unknown_idle_native == 1u" in idle_wake_test
    assert "bridge_m14_dev128_log_busy_due(instance, now);" in dev68_bridge
    assert "g_m14_dev128_phase_id = (uint32_t)phase;" in dev13_bridge
    assert "g_m14_dev128_phase_enter_ns = now;" in dev13_bridge
    assert "bridge_m14_dev118_note_idle_ready(now);" in dev68_bridge
    assert "bridge_m14_dev118_note_delivery_begin(now);" in dev68_bridge
    assert "bridge_m14_dev118_note_delivery_end(bridge_m14_now_ns(instance));" in dev68_bridge
    assert "Sleep(0u);" in dev68_bridge
    assert "M14 dev118 pacing host_ms=" in dev79_bridge
    assert "spin2ms=%u" in dev79_bridge
    assert "from_idle=%llu" in dev79_bridge
    assert "busy_due=%llu" in dev79_bridge
    assert "sleep_cross=%llu" in dev79_bridge
    assert "M14 LLVM dev122 tail attribution active over dev121 quiet path:" in dev79_bridge
    assert 'uint32_t openspm_m14_scene_profile_active;' in dev79_bridge
    assert '"m14_scene_profile.h"' in dev79_bridge
    assert dev79_bridge.count("if (cpu->downcount <= 0)") == 2
    assert "scene_result = _scene_profile.retire(source_root, check_only)" in patcher
    assert '#include "legacy_c_adapter_v42_dev69.c.in"' in active_v42_bridge
    assert "out_api->module_version = OPENSPM_M14_EXPERIMENT_VERSION;" in active_v42_bridge
    assert "bridge_dev95_note_seq_state(instance, \"spmarioMain\");" in dev79_bridge
    assert "g_m14_dev96_underlying_external_read = instance->cpu.external_read;" in dev79_bridge
    assert "instance->cpu.external_read = bridge_external_read_dev96;" in dev79_bridge
    assert "instance->cpu.external_read = g_m14_dev96_underlying_external_read;" in dev79_bridge
    assert "value = g_m14_dev96_underlying_external_read(cpu, address, width);" in dev79_bridge
    assert "return value;" in dev79_bridge
    assert '"m14-dev122-chunk" OPENSPM_M14_STRINGIFY(OPENSPM_M14_LLVM_CHUNK_INSTRUCTIONS)' in dev79_bridge
    assert '"-sim0-yold"' in dev79_bridge
    assert '"-sim1-yold"' in dev79_bridge
    assert 'OPENSPM_M14_DIAGNOSTIC_SUFFIX "-profile"' in dev79_bridge
    assert 'OPENSPM_M14_DIAGNOSTIC_SUFFIX "-normal"' in dev79_bridge
    assert "openspm_m14_dev90_native_first_hits = 0u;" in dev79_bridge
    assert "openspm_m14_dev90_native_first_fallbacks = 0u;" in dev79_bridge
    assert "openspm_m14_llvm_budget_exit_counter_ptr" in dev79_bridge
    assert '#include "legacy_c_adapter_v23.c.in"' in relf_tail_bridge
    assert "M14 LLVM relF deterministic-tail allocation active:" in relf_tail_bridge
    assert 'out_api->module_version = "m14-dev34-llvm-relf-tail-proof";' in relf_tail_bridge
    assert '#include "legacy_c_adapter_v24.c.in"' in relf_bss_bridge
    assert "M14 LLVM relF BSS deterministic-tail allocation active:" in relf_bss_bridge
    assert 'out_api->module_version = "m14-dev35-llvm-relf-bss-tail-proof";' in relf_bss_bridge
    assert '#include "legacy_c_adapter_v25.c.in"' in hostcall_bridge
    assert "bridge_request_fallback(instance, seam->target, 12u);" in hostcall_bridge
    assert "cpu->lr = seam->return_pc;" in hostcall_bridge
    assert "cpu->pc = seam->target;" in hostcall_bridge
    assert 'out_api->module_version = "m14-dev36-llvm-hostcall-direct-parity";' in hostcall_bridge
    assert "g_m14_hostcall.handle" not in hostcall_bridge
    assert "REQUIRED_BOOT_TARGETS = {0x8029A500}" in hostcall_deriver
    assert "direct_calls.load_host_targets()" in hostcall_deriver
    assert "hostcall_seams.scan" in seam_deriver
    assert "OPENSPM_LOCAL_LLVM_HOSTCALL_SEAMS" in seam_deriver
    assert "hosted_calls=" in seam_deriver and "nand_check_calls=" in seam_deriver
    assert "REL_BASE = 0x80CE7220" in rel_builder
    assert "REL_BSS_BASE = 0x80CE6340" in rel_builder
    assert "REL_BSS_SIZE = 0x00000EBC" in rel_builder
    assert "REL_EXEC_ADDRESS = REL_BASE + REL_EXEC_OFFSET" in rel_builder
    assert "REL_PROLOG_ADDRESS = REL_BASE + REL_PROLOG_OFFSET" in rel_builder
    assert "BSS-section shape changed" in rel_builder
    assert "executable-section shape changed" in rel_builder

    assert "OPENSPM_M14_LLVM_WPAR_INLINE_GATHER" in patcher
    assert '"openspm_m14_llvm_wg_bytes_ptr"' in patcher
    assert '"openspm_m14_flush_wg_llvm"' in patcher
    assert "Value *openspmValue64 = builder_.CreateZExtOrTrunc(value, i64);" in patcher
    assert "materialize(current_pc_)" in patcher
    assert "OPENSPM_M14_LLVM_INDIRECT_BUDGET_GUARD" in patcher
    assert "void FunctionEmitter::emitBudgetGuard(Value *pc)" in patcher
    assert "emitBudgetGuard(target);" in patcher
    assert "OPENSPM_M14_LLVM_LEGACY_GUARD_QUANTUM_32768" in patcher
    assert "intrinsic_escapes_) ? 32768 : 256" in patcher
    assert "OPENSPM_M14_LLVM_BUDGET_EXIT_COUNTER" in patcher
    assert '"openspm_m14_llvm_budget_exit_counter_ptr"' in patcher
    assert "llvm/IR/Module.h" in patcher
    assert "LLVM_FUNCTION_INCLUDE + LLVM_MODULE_INCLUDE" in patcher
    assert "OPENSPM_M14_LLVM_ENTRY_ONLY_SYNTHETIC_COLD_ENTRIES" in patcher
    assert "OPENSPM_M14_LLVM_DISPDRAW_NESTED_INDIRECT" in patcher
    assert "DISPDRAW_US2_START = 0x8005B028" in patcher
    assert "DISPDRAW_US2_END = 0x8005B2F4" in patcher
    assert "OPENSPM_M14_LLVM_DISPDRAW_CPUSTATE_NESTED_MULTIBLOCK" in patcher
    assert "DISPDRAW_CPUSTATE_SITE = 0x8005B29C" in patcher
    assert "term.guest_pc == 0x8005B29Cu" in patcher
    assert "cpustate_dispdraw_resume" in patcher
    assert "cpustate_dispdraw_escape" in patcher
    assert "_patch_dispdraw_cpustate_nested" in patcher
    assert "_scene_profile.retire(source_root, check_only)" in patcher
    assert '"openspm_m14_dev99_nested_dispatch"' in patcher
    assert '"openspm_m14_dev105_cpustate_nested_dispatch"' in patcher
    assert "dispdraw_nested_resume" in patcher
    assert "dispdraw_nested_escape" in patcher
    assert '"openspm_dev57_addr_taken_"' in patcher
    assert '"openspm_dev97_hp0_motion24_interval_"' in patcher
    assert "if (openspm_m14_symbol_is_entry_only(name)) {" in patcher
    assert "OpenSPM entry-only synthetic symbols:" in patcher
    assert "OPENSPM_M14_LLVM_DEV107_ENTRY_ONLY_PERF_ENTRIES" in patcher
    assert '"openspm_dev107_direct_bl_source_"' in patcher
    assert "openspm_m14_dev107_entry_only_perf" in patcher
    assert "_patch_dev107_entry_only_perf_entries" in patcher
    assert "OPENSPM_M14_LLVM_NATIVE_FRAME_PHASE_PROFILE" in patcher
    assert '"openspm_m14_note_frame_phase_native"' in patcher
    assert "case 0x801A8A9Cu: openspmPhase = 0u;" in patcher
    assert "case 0x801A7D1Cu: openspmPhase = 1u;" in patcher
    assert "case 0x801A7AACu: openspmPhase = 2u;" in patcher
    assert "case 0x801A8B64u: openspmPhase = 3u;" in patcher
    assert '"openspm_m14_note_death_entry_native"' in patcher
    assert "case 0x801615E4u:" in patcher
    assert "case 0x8017B834u:" in patcher
    assert "builder_.CreateCall(deathFn, {builder_.getInt32(block.guest_address)});" in patcher
    assert "OPENSPM_M14_LLVM_RETIRE_FULL_STATE_CALLER_CHECKPOINT" in active_patch_base
    assert "import _apply_openspm_llvm_codegen_patch_base_dev71 as _legacy" in active_patch_base
    assert "import _apply_openspm_llvm_codegen_patch_base_dev72 as _legacy" not in active_patch_base
    assert "dev78-state-spills=retired" in active_patch_base
    assert "OPENSPM_M14_PORTABLE_POPCOUNT64" in retained_dev71_base
    assert "portablePopcount64" in retained_dev71_base
    assert "inst.op == PPC_OP_MTMSR" in retained_dev71_base
    assert "points[count++] = address + 4u;" in retained_dev71_base

    assert "apply_openspm_llvm_wpar_codegen_patch.transform(source)" in preparer
    assert "verify_state_in_memory_contract(source)" in preparer
    assert '"state_in_memory_contract": True' in preparer
    assert "CPUState-ABI-only aliasing + dirty/reload skips are present" in preparer
    assert 'OPENSPM_CODEGEN_PATCH = "llvm-inline-wpar-indirect-budget-v20-dev113-chunk256-yold"' in preparer
    assert '"openspm_codegen_patch": OPENSPM_CODEGEN_PATCH' in preparer
    assert '"runtime_source": str(source / "src")' in preparer
    assert "major in (19, 20)" in preparer
    assert 'BOOTSTRAP_LLVM_VERSION = "20.1.8"' in preparer
    assert "bootstrap_llvm_windows(cache)" in preparer
    assert 'EXPECTED_CODEGEN_PATCH = "llvm-inline-wpar-indirect-budget-v20-dev113-chunk256-yold"' in runner
    assert 'info.get("state_in_memory_contract") is not True' in runner
    assert "toolchain manifest does not prove pinned DolRecomp state-in-memory " in runner
    assert "semantics; rerun toolchain preparation with the current source audit" in runner

    assert 'runtime_source = tool_source / "src"' in module_builder
    assert "build_local_module.stage_cpu_runtime(runtime_source, staged_cpu)" in module_builder
    assert "def verify_codegen_provenance(" in module_builder
    assert "expected_state_in_memory: bool" in module_builder
    assert "generated, args.chunk_instructions, args.state_in_memory" in module_builder
    assert '"--chunk-instructions", str(args.chunk_instructions)' in module_builder
    assert 'command.append("--state-in-memory")' in module_builder
    assert "stale/wrong LLVM chunk geometry" in module_builder
    assert "stale/wrong LLVM state-memory mode" in module_builder
    assert 'f"-DOPENSPM_LLVM_CHUNK_INSTRUCTIONS={args.chunk_instructions}"' in module_builder
    assert 'f"-DOPENSPM_LLVM_STATE_IN_MEMORY={1 if args.state_in_memory else 0}"' in module_builder
    assert "require_openspm_wpar=True" in module_builder
    assert "def cmake_path" in validator_text
    assert "path.as_posix()" in validator_text
    assert "OPENSPM_WPAR_SYMBOLS" in validator_text
    assert "LZ10_MAGIC = 0x10" in relf_loader
    assert "retail-lz10" in relf_loader
    assert "load_from_game" in generator
    assert '"--runtime", "recompcore"' in generator
    assert '"--backend", "llvm"' in generator
    assert '"--instrumentation", "none"' in generator
    assert 'choices=(128, 256, 512)' in generator
    assert 'env["DOLRECOMP_LLVM_CHUNK_INSTRUCTIONS"] = str(args.chunk_instructions)' in generator
    assert 'f"chunk_instructions={args.chunk_instructions}\\n"' in generator
    assert 'f"state_in_memory={1 if args.state_in_memory else 0}\\n"' in generator
    assert '"--state-in-memory"' in generator
    assert 'command.insert(command.index("--map"), "--state-in-memory")' in generator
    assert 'env["DOLRECOMP_DISPATCH_LOOKUP"] = "indexed"' in generator
    assert "augment_llvm_observed_indirect_entries.augment" in generator
    assert "augment_llvm_hp0_callback_entries.augment" in generator
    assert "hp0_callback_partition_policy=dev97-player-derived-bounded-cold-entry-window" in generator
    assert "synthetic_cold_entry_partition_policy=dev107-dev57-plus-direct-bl-entry-only-dev97-partitioned" in generator
    assert "death_entry_truth_policy=dev106-native-onHpEquals0-plus-seqSetSeq" in generator
    assert "dev97_mot30_main=" in generator
    assert "observed_indirect_partition_policy=measured-and-player-verified" in generator
    assert "OPENSPM_M14_DEV87_EXACT_HOSTCALL_PREFILTER" in hostcall_prefilter_text
    assert "OPENSPM_M14_DEV90_STRUCTURAL_NATIVE_FIRST" in hostcall_prefilter_text
    assert "OPENSPM_M14_DEV103_ENTRYONLY_HOSTCALL_FILTER" in hostcall_prefilter_text
    assert "OPENSPM_M14_DEV107_DIRECT_BL_NATIVE_FIRST" in hostcall_prefilter_text
    assert "OPENSPM_M14_DEV108_PARTITION_ONLY_AB" in hostcall_prefilter_text
    assert "openspm_m14_dev107_direct_bl_native_first" in hostcall_prefilter_text
    assert "dev107_direct_bl_native_first" in hostcall_prefilter_text
    assert "dev107_direct_bl_fallback_excluded" in hostcall_prefilter_text
    assert "ENTRY_ONLY_MAP_PREFIXES" in hostcall_prefilter_text
    assert "map_rows_requiring_host" in hostcall_prefilter_text
    assert "DEV90_NATIVE_FIRST_ENTRIES" in hostcall_prefilter_text
    assert "0x8017B834" in hostcall_prefilter_text
    assert "dev95 observation-only game-over transition trace" in hostcall_prefilter_text
    assert "openspm_m14_dev90_structural_native_first" in hostcall_prefilter_text
    assert "openspm_m14_dev90_native_first_fallbacks" in hostcall_prefilter_text
    assert "legacy_policy.direct_bl_sources" in hostcall_prefilter_text
    assert "direct_calls.load_host_targets()" in hostcall_prefilter_text
    assert "openspm_m14_dev87_should_call_host" in hostcall_prefilter_text
    assert "Unknown/alias/out-of-image PCs always retain host-first routing." in hostcall_prefilter_text
    assert "apply_llvm_hostcall_prefilter.derive_candidates" in generator
    assert "apply_llvm_hostcall_prefilter.patch_dispatch_header" in generator
    assert "dispatch_hostcall_prefilter=dev103-dev57-entryonly-over-dev87-exact-conservative" in generator
    assert "entry_only_map=" in generator
    assert "dev103_entry_only_map_entries=" in generator
    assert "dispatch_structural_native_first=dev129-two-exact-map-entries-plus-dynamic-idle" in generator
    assert "dev129_dynamic_idle_entries=" in generator
    assert "direct_bl_native_first=dev108-disabled-ab-historical-rescue" in generator
    assert "dev108_direct_bl_native_first=disabled-ab" in generator
    assert "dispdraw_cpustate_nested_policy=dev117-progress-aware-128x32768-failclosed" in generator
    assert "dispdraw_cpustate_nested=dev117-progress-aware-128x32768-failclosed" in generator
    assert "dev90_structural_native_first_entries=" in generator
    assert "M14 LLVM dev87 exact host-call prefilter active:" in dev79_bridge
    assert "M14 LLVM dev103 entry-only host filter active:" in dev79_bridge
    assert "host-call prefilter stats bypassed=" in dev79_bridge
    assert "DEV79_HOT_BL_SOURCES" in bl_partition_text
    assert "DEV107_DIRECT_BL_SOURCE_PREFIX" in bl_partition_text
    assert "scan_direct_bl_sources" in bl_partition_text
    assert "augment_direct_bl_source_entries" in bl_partition_text
    assert "augment_llvm_bl_continuations.augment_direct_bl_source_entries" in generator
    assert "0x80D1F414" in bl_partition_text and "0x802543F4" in bl_partition_text
    assert "0x80086848" in bl_partition_text and "0x8012191C" in bl_partition_text
    assert "verify_direct_bl_source" in bl_partition_text
    assert "derive_hot_bl_source_entries" in bl_partition_text
    assert "direct_bl_partition_policy=dev107-all-source-legal-entry-without-partition-plus-dev79-bounded" in generator
    assert "LLVM dev79 direct-BL native-entry repair" in generator
    assert "FILESELECT_INDIRECT_TARGET = 0x80032B94" in observed_indirect_text
    assert "FILESELECT_ROOT_TARGET = 0x8002F278" in observed_indirect_text
    assert "_decode_direct_bl" in observed_indirect_text
    assert "moderngekko_runtime=0" in generator
    assert "dolphin_runtime=0" in generator
    assert "require_openspm_wpar=True" in generator
    assert "preflight_player_code" in runner
    assert 'args.cmake, "-S", str(root), "-B", str(candidate)' in runner
    assert "Reconfiguring patched Aurora" in runner
    assert "def verify_renderer_perf_candidate(candidate: Path)" in runner
    assert "Renderer performance candidate proof PASS:" in runner
    assert "dev94 CPU-cached exact snapshots" in runner
    assert "M14 raw guest-array pooled exact snapshots single-copy append active" in runner
    assert "M14 exact duplicate CP array-base pipeline reuse active" in runner
    assert "legacy CP array-base table still pre-dirties pipeline" in runner
    assert "stageBucket declaration count=" in runner
    assert "stage_bucket_count != 1" in runner
    assert "verify_renderer_perf_candidate(candidate)" in runner
    assert "relf_input.load_from_game(game_dir)" in runner
    assert "derive_v19_llvm_seams.scan(main_dol)" in runner
    assert "known_good_c_dll" in runner
    assert "finally:" in runner and "shutil.copy2(backup_dll, staged_dll)" in runner
    assert "M14-LLVM-UPDATE-RUN" in cmd or "LLVM-native transactional A/B" in cmd
    assert "run_m14_llvm_candidate.py" in cmd
    assert "--chunk-instructions" in runner
    assert '"--chunk-instructions", str(args.chunk_instructions)' in runner
    assert '"--state-in-memory-mode"' in runner
    assert '"--heavy-diagnostics-mode"' in runner
    assert "choices=(0, 1)" in runner
    assert "args.state_in_memory_mode == 1" in runner
    assert "args.heavy_diagnostics_mode == 1" in runner
    assert 'build.append("--state-in-memory")' in runner
    assert 'build.append("--heavy-diagnostics")' in runner
    assert '"--heavy-diagnostics"' in module_builder
    assert "OPENSPM_LLVM_HEAVY_DIAGNOSTICS=" in module_builder
    assert 'set "LLVM_CHUNK=256"' in cmd
    assert '"!LLVM_CHUNK!"=="512"' in cmd
    assert "128, 256, and experimental 512" in cmd
    assert "choices=(128, 256, 512)" in runner
    assert "choices=(128, 256, 512)" in module_builder
    assert 'PROPERTY STRINGS 128 256 512' in (
        ROOT / "tools" / "native_bridge" / "CMakeLists.txt"
    ).read_text(encoding="utf-8")
    assert "OPENSPM_M14_LLVM_CHUNK_INSTRUCTIONS != 512" in dev79_bridge
    assert 'set "LLVM_STATE_MEM=1"' in cmd
    assert 'set "LLVM_HEAVY_DIAG=0"' in cmd
    assert '--chunk-instructions "!LLVM_CHUNK!"' in cmd
    assert '--state-in-memory-mode "!LLVM_STATE_MEM!"' in cmd
    assert '--heavy-diagnostics-mode "!LLVM_HEAVY_DIAG!"' in cmd
    assert "M14-UPDATE-RUN.cmd" in cmd
    assert (ROOT / "M14-LLVM-CHUNK128-RUN.cmd").is_file()
    assert (ROOT / "M14-LLVM-CHUNK512-RUN.cmd").is_file()
    chunk512 = (ROOT / "M14-LLVM-CHUNK512-RUN.cmd").read_text(encoding="utf-8")
    assert 'set "OPENSPM_M14_VSYNC=0"' in chunk512
    assert "512 1 0" in chunk512
    assert (ROOT / "M14-LLVM-DEV117-RUN.cmd").is_file()
    dev117 = (ROOT / "M14-LLVM-DEV117-RUN.cmd").read_text(encoding="utf-8")
    assert 'set "OPENSPM_M14_VSYNC=0"' in dev117
    assert "256 1 0" in dev117
    assert "steps=128" in dev117
    assert "cycle quantum=32768" in dev117
    assert 'set "OPENSPM_M14_PRECISE_IDLE_WAIT=0"' in dev117
    assert (ROOT / "M14-LLVM-PACING-RUN.cmd").is_file()
    pacing = (ROOT / "M14-LLVM-PACING-RUN.cmd").read_text(encoding="utf-8")
    assert 'set "OPENSPM_M14_PRECISE_IDLE_WAIT=1"' in pacing
    assert 'set "OPENSPM_M14_VSYNC=0"' in pacing
    assert 'set "OPENSPM_M14_PRESENT_MODE=mailbox"' in pacing
    assert 'set "OPENSPM_M14_FIFO_MODE=drain"' in pacing
    assert "256 1 0" in pacing
    assert "Sleep(0)" in pacing
    assert (ROOT / "M14-LLVM-IMMEDIATE-RUN.cmd").is_file()
    immediate = (ROOT / "M14-LLVM-IMMEDIATE-RUN.cmd").read_text(encoding="utf-8")
    assert 'set "OPENSPM_M14_PRECISE_IDLE_WAIT=1"' in immediate
    assert 'set "OPENSPM_M14_VSYNC=0"' in immediate
    assert 'set "OPENSPM_M14_PRESENT_MODE=immediate"' in immediate
    assert 'set "OPENSPM_M14_FIFO_MODE=drain"' in immediate
    assert "256 1 0" in immediate
    assert (ROOT / "M14-LLVM-INLINE-RUN.cmd").is_file()
    inline_run = (ROOT / "M14-LLVM-INLINE-RUN.cmd").read_text(encoding="utf-8")
    assert 'set "OPENSPM_M14_PRECISE_IDLE_WAIT=1"' in inline_run
    assert 'set "OPENSPM_M14_VSYNC=0"' in inline_run
    assert 'set "OPENSPM_M14_PRESENT_MODE=immediate"' in inline_run
    assert 'set "OPENSPM_M14_FIFO_MODE=inline"' in inline_run
    assert "256 1 0" in inline_run
    assert "OPENSPM_M14_MAIN_PRIORITY" in native_main
    assert "SetThreadPriority(thread, desired)" in native_main
    assert "M14 dev123 producer scheduling A/B active:" in native_main
    assert "OPENSPM_M14_MAIN_QOS" in native_main
    assert 'GetProcAddress(kernel32, "SetThreadInformation")' in native_main
    assert "OPENSPM_THREAD_POWER_THROTTLING_CLASS 3" in native_main
    assert "OPENSPM_THREAD_POWER_THROTTLING_CURRENT_VERSION 1u" in native_main
    assert "OPENSPM_THREAD_POWER_THROTTLING_EXECUTION_SPEED 0x1u" in native_main
    assert "M14 dev124 producer HighQoS A/B active:" in native_main
    assert "OPENSPM_M14_MAIN_CPUSET" in native_main
    assert 'host_kernel32_proc("GetSystemCpuSetInformation")' in native_main
    assert 'host_kernel32_proc("SetThreadSelectedCpuSets")' in native_main
    assert 'host_kernel32_proc("GetThreadSelectedCpuSets")' in native_main
    assert "EfficiencyClass" in native_main
    assert "M14 dev127 fast-core CPU Set A/B active:" in native_main
    assert "host_restore_main_thread_cpuset(&main_cpu_set_restore);" in native_main
    assert "static LARGE_INTEGER g_host_qpc_frequency;" in native_main
    assert "host_init_monotonic_clock" in native_main
    assert native_main.count("QueryPerformanceFrequency(") == 1
    assert "M14 dev125 cached QPC frequency active:" in native_main
    monotonic_start = native_main.index("static uint64_t OPENSPM_CALL host_monotonic_ns")
    monotonic_end = native_main.index("static int OPENSPM_CALL host_query_service", monotonic_start)
    assert "QueryPerformanceFrequency(" not in native_main[monotonic_start:monotonic_end]
    assert "QueryPerformanceCounter(&counter)" in native_main[monotonic_start:monotonic_end]
    assert native_main.index("openspm_renderer_load_and_bind") < native_main.index("previous_main_priority = host_apply_main_thread_priority()")
    assert native_main.index("openspm_renderer_load_and_bind") < native_main.index("host_apply_main_thread_fast_cpuset(&main_cpu_set_restore)")
    assert (ROOT / "M14-LLVM-PRIORITY-RUN.cmd").is_file()
    priority = (ROOT / "M14-LLVM-PRIORITY-RUN.cmd").read_text(encoding="utf-8")
    assert 'set "OPENSPM_M14_MAIN_PRIORITY=above_normal"' in priority
    assert 'set "OPENSPM_M14_STICKY_PRESSED=1"' in priority
    assert 'set "OPENSPM_M14_PRESENT_MODE=immediate"' in priority
    assert 'set "OPENSPM_M14_FIFO_MODE=drain"' in priority
    assert "256 1 0" in priority
    assert (ROOT / "M14-LLVM-HIGHQOS-RUN.cmd").is_file()
    highqos = (ROOT / "M14-LLVM-HIGHQOS-RUN.cmd").read_text(encoding="utf-8")
    assert 'set "OPENSPM_M14_MAIN_QOS=high"' in highqos
    assert 'set "OPENSPM_M14_MAIN_PRIORITY=off"' in highqos
    assert 'set "OPENSPM_M14_STICKY_PRESSED=1"' in highqos
    assert 'set "OPENSPM_M14_PRESENT_MODE=immediate"' in highqos
    assert 'set "OPENSPM_M14_FIFO_MODE=drain"' in highqos
    assert "256 1 0" in highqos
    assert (ROOT / "M14-LLVM-100HZ-CADENCE-RUN.cmd").is_file()
    cadence100 = (ROOT / "M14-LLVM-100HZ-CADENCE-RUN.cmd").read_text(encoding="utf-8")
    assert 'set "OPENSPM_M14_DIAG_HALF_99_943HZ=1"' in cadence100
    assert 'set "OPENSPM_M14_FINAL_IDLE_SPIN=1"' in cadence100
    assert 'set "OPENSPM_M14_MAIN_CPUSET=fast"' in cadence100
    assert 'set "OPENSPM_M14_STICKY_PRESSED=1"' in cadence100
    assert 'set "OPENSPM_M14_MAIN_PRIORITY=off"' in cadence100
    assert 'set "OPENSPM_M14_MAIN_QOS=off"' in cadence100
    assert 'set "OPENSPM_M14_PRESENT_MODE=immediate"' in cadence100
    assert 'set "OPENSPM_M14_FIFO_MODE=drain"' in cadence100
    assert "256 1 0" in cadence100
    assert (ROOT / "M14-LLVM-100HZ-CADENCE-VSYNC-RUN.cmd").is_file()
    cadence100_vsync = (ROOT / "M14-LLVM-100HZ-CADENCE-VSYNC-RUN.cmd").read_text(encoding="utf-8")
    assert 'set "OPENSPM_M14_DIAG_HALF_99_943HZ=1"' in cadence100_vsync
    assert 'set "OPENSPM_M14_VSYNC=1"' in cadence100_vsync
    assert 'set "OPENSPM_M14_PRESENT_MODE=fifo"' in cadence100_vsync
    assert 'set "OPENSPM_M14_FIFO_MODE=drain"' in cadence100_vsync
    assert 'set "OPENSPM_M14_FINAL_IDLE_SPIN=1"' in cadence100_vsync
    assert 'set "OPENSPM_M14_MAIN_CPUSET=fast"' in cadence100_vsync
    assert "256 1 0" in cadence100_vsync
    assert (ROOT / "M14-LLVM-INTERP-MIDPOINT-RUN.cmd").is_file()
    interp_midpoint_run = (ROOT / "M14-LLVM-INTERP-MIDPOINT-RUN.cmd").read_text(encoding="utf-8")
    assert "RETIRED: dev137 live midpoint substitution is rejected" in interp_midpoint_run
    assert "M14-LLVM-INTERP-IDENTITY-RUN.cmd" in interp_midpoint_run
    assert 'OPENSPM_M14_INTERP_VISUAL=midpoint' not in interp_midpoint_run

    assert (ROOT / "M14-LLVM-INTERP-IDENTITY-RUN.cmd").is_file()
    assert (ROOT / "M14-LLVM-ASYNC-PRESENTER-RUN.cmd").is_file()
    async_presenter_run = (ROOT / "M14-LLVM-ASYNC-PRESENTER-RUN.cmd").read_text(encoding="utf-8")
    assert 'set "OPENSPM_M14_ASYNC_PRESENTER=1"' in async_presenter_run
    assert 'set "OPENSPM_INTERPOLATION_TARGET_FPS=%TARGET_FPS%"' in async_presenter_run
    assert 'set "OPENSPM_M14_INTERP_PROBE=0"' in async_presenter_run
    assert 'set "OPENSPM_M14_INTERP_VISUAL=off"' in async_presenter_run
    assert 'set "OPENSPM_M14_VSYNC=1"' in async_presenter_run
    assert 'set "OPENSPM_M14_PRESENT_MODE=fifo"' in async_presenter_run
    assert "256 1 0" in async_presenter_run

    assert (ROOT / "M14-LLVM-INTERP-REPLAY-PREP-RUN.cmd").is_file()
    interp_replay_prep_run = (
        ROOT / "M14-LLVM-INTERP-REPLAY-PREP-RUN.cmd"
    ).read_text(encoding="utf-8")
    assert 'set "OPENSPM_M14_INTERP_REPLAY=1"' in interp_replay_prep_run
    assert 'set "OPENSPM_M14_ASYNC_PRESENTER=1"' in interp_replay_prep_run
    assert 'set "OPENSPM_INTERPOLATION_TARGET_FPS=%TARGET_FPS%"' in interp_replay_prep_run
    assert 'set "OPENSPM_M14_INTERP_PROBE=0"' in interp_replay_prep_run
    assert 'set "OPENSPM_M14_INTERP_VISUAL=off"' in interp_replay_prep_run
    assert 'set "OPENSPM_M14_VSYNC=1"' in interp_replay_prep_run
    assert 'set "OPENSPM_M14_PRESENT_MODE=fifo"' in interp_replay_prep_run
    assert "Phase cadence follows the precise display ratio" in interp_replay_prep_run
    assert "100.000 Hz reduces to 2,2,1" in interp_replay_prep_run
    assert "256 1 0" in interp_replay_prep_run

    assert (ROOT / "M14-LLVM-INTERP-REPLAY-ENCODE-RUN.cmd").is_file()
    interp_replay_encode_run = (
        ROOT / "M14-LLVM-INTERP-REPLAY-ENCODE-RUN.cmd"
    ).read_text(encoding="utf-8")
    assert 'set "OPENSPM_M14_INTERP_REPLAY=1"' in interp_replay_encode_run
    assert 'set "OPENSPM_M14_INTERP_REPLAY_ENCODE=1"' in interp_replay_encode_run
    assert 'set "OPENSPM_M14_ASYNC_PRESENTER=1"' in interp_replay_encode_run
    assert 'set "OPENSPM_M14_INTERP_VISUAL=off"' in interp_replay_encode_run
    assert 'set "OPENSPM_M14_VSYNC=1"' in interp_replay_encode_run
    assert 'set "OPENSPM_M14_PRESENT_MODE=fifo"' in interp_replay_encode_run
    assert "Interpolated GX poses are rendered offscreen but NOT presented." in interp_replay_encode_run
    assert "256 1 0" in interp_replay_encode_run

    interp_identity_run = (ROOT / "M14-LLVM-INTERP-IDENTITY-RUN.cmd").read_text(encoding="utf-8")
    assert 'set "OPENSPM_M14_DIAG_HALF_99_943HZ=0"' in interp_identity_run
    assert 'set "OPENSPM_INTERPOLATION=on"' in interp_identity_run
    assert 'set "OPENSPM_INTERPOLATION_TARGET_FPS=0"' in interp_identity_run
    assert 'set "OPENSPM_M14_INTERP_PROBE=1"' in interp_identity_run
    assert 'set "OPENSPM_M14_INTERP_PROBE_INTERVAL=300"' in interp_identity_run
    assert 'set "OPENSPM_M14_INTERP_VISUAL=off"' in interp_identity_run
    assert 'set "OPENSPM_M14_VSYNC=1"' in interp_identity_run
    assert 'set "OPENSPM_M14_PRESENT_MODE=fifo"' in interp_identity_run
    assert "256 1 0" in interp_identity_run
    assert (ROOT / "M14-LLVM-INTERP-PROBE-RUN.cmd").is_file()
    interp_probe_run = (ROOT / "M14-LLVM-INTERP-PROBE-RUN.cmd").read_text(encoding="utf-8")
    assert 'set "OPENSPM_M14_DIAG_HALF_99_943HZ=0"' in interp_probe_run
    assert 'set "OPENSPM_M14_INTERP_PROBE=1"' in interp_probe_run
    assert 'set "OPENSPM_M14_VSYNC=1"' in interp_probe_run
    assert 'set "OPENSPM_M14_PRESENT_MODE=fifo"' in interp_probe_run
    assert 'set "OPENSPM_M14_FIFO_MODE=drain"' in interp_probe_run
    assert "256 1 0" in interp_probe_run
    assert (ROOT / "M14-LLVM-SMOOTH-RUN.cmd").is_file()
    smooth = (ROOT / "M14-LLVM-SMOOTH-RUN.cmd").read_text(encoding="utf-8")
    assert 'set "OPENSPM_M14_DIAG_HALF_99_943HZ=0"' in smooth
    assert 'set "OPENSPM_M14_FINAL_IDLE_SPIN=1"' in smooth
    assert 'set "OPENSPM_M14_MAIN_CPUSET=fast"' in smooth
    assert 'set "OPENSPM_M14_STICKY_PRESSED=1"' in smooth
    assert 'set "OPENSPM_M14_MAIN_PRIORITY=off"' in smooth
    assert 'set "OPENSPM_M14_MAIN_QOS=off"' in smooth
    assert 'set "OPENSPM_M14_PRESENT_MODE=immediate"' in smooth
    assert 'set "OPENSPM_M14_FIFO_MODE=drain"' in smooth
    assert "256 1 0" in smooth
    assert (ROOT / "M14-LLVM-FASTCORE-RUN.cmd").is_file()
    fastcore = (ROOT / "M14-LLVM-FASTCORE-RUN.cmd").read_text(encoding="utf-8")
    assert 'set "OPENSPM_M14_MAIN_CPUSET=fast"' in fastcore
    assert 'set "OPENSPM_M14_MAIN_PRIORITY=off"' in fastcore
    assert 'set "OPENSPM_M14_MAIN_QOS=off"' in fastcore
    assert 'set "OPENSPM_M14_FINAL_IDLE_SPIN=0"' in fastcore
    assert 'set "OPENSPM_M14_STICKY_PRESSED=1"' in fastcore
    assert 'set "OPENSPM_M14_PRESENT_MODE=immediate"' in fastcore
    assert 'set "OPENSPM_M14_FIFO_MODE=drain"' in fastcore
    assert "256 1 0" in fastcore
    assert (ROOT / "M14-LLVM-SPINWAIT-RUN.cmd").is_file()
    spinwait = (ROOT / "M14-LLVM-SPINWAIT-RUN.cmd").read_text(encoding="utf-8")
    assert 'set "OPENSPM_M14_PRECISE_IDLE_WAIT=1"' in spinwait
    assert 'set "OPENSPM_M14_FINAL_IDLE_SPIN=1"' in spinwait
    assert 'set "OPENSPM_M14_MAIN_PRIORITY=off"' in spinwait
    assert 'set "OPENSPM_M14_MAIN_QOS=off"' in spinwait
    assert 'set "OPENSPM_M14_STICKY_PRESSED=1"' in spinwait
    assert 'set "OPENSPM_M14_PRESENT_MODE=immediate"' in spinwait
    assert 'set "OPENSPM_M14_FIFO_MODE=drain"' in spinwait
    assert "256 1 0" in spinwait
    assert (ROOT / "M14-LLVM-NOSTICKY-INPUT-RUN.cmd").is_file()
    nosticky = (ROOT / "M14-LLVM-NOSTICKY-INPUT-RUN.cmd").read_text(encoding="utf-8")
    assert 'set "OPENSPM_M14_STICKY_PRESSED=0"' in nosticky
    assert 'set "OPENSPM_M14_MAIN_PRIORITY=off"' in nosticky
    assert 'set "OPENSPM_M14_PRESENT_MODE=immediate"' in nosticky
    assert 'set "OPENSPM_M14_FIFO_MODE=drain"' in nosticky
    assert "256 1 0" in nosticky
    assert (ROOT / "M14-LLVM-CAMERA-TAIL-RUN.cmd").is_file()
    camera_tail = (ROOT / "M14-LLVM-CAMERA-TAIL-RUN.cmd").read_text(encoding="utf-8")
    assert 'set "OPENSPM_M14_PRECISE_IDLE_WAIT=1"' in camera_tail
    assert 'set "OPENSPM_M14_VSYNC=0"' in camera_tail
    assert 'set "OPENSPM_M14_PRESENT_MODE=immediate"' in camera_tail
    assert 'set "OPENSPM_M14_FIFO_MODE=drain"' in camera_tail
    assert "256 1 0" in camera_tail
    assert (ROOT / "M14-LLVM-QUIET-RUN.cmd").is_file()
    quiet = (ROOT / "M14-LLVM-QUIET-RUN.cmd").read_text(encoding="utf-8")
    assert 'set "OPENSPM_M14_PRECISE_IDLE_WAIT=1"' in quiet
    assert 'set "OPENSPM_M14_VSYNC=0"' in quiet
    assert 'set "OPENSPM_M14_PRESENT_MODE=immediate"' in quiet
    assert 'set "OPENSPM_M14_FIFO_MODE=drain"' in quiet
    assert "256 1 0" in quiet
    assert (ROOT / "M14-LLVM-STATE-MEM-RUN.cmd").is_file()
    assert (ROOT / "M14-LLVM-STATE-MEM0-RUN.cmd").is_file()
    assert (ROOT / "M14-LLVM-PROFILE-RUN.cmd").is_file()
    assert '256 1 1' in (ROOT / "M14-LLVM-PROFILE-RUN.cmd").read_text(
        encoding="utf-8"
    )
    assert (ROOT / "M14-LLVM-NOVSYNC-RUN.cmd").is_file()
    novsync = (ROOT / "M14-LLVM-NOVSYNC-RUN.cmd").read_text(encoding="utf-8")
    assert 'set "OPENSPM_M14_VSYNC=0"' in novsync
    assert "256 1 0" in novsync
    assert 'std::getenv("OPENSPM_M14_VSYNC")' in renderer
    assert "config.vsync = m14_vsync_enabled_from_environment();" in renderer
    assert "vsync=off, Aurora prefers Mailbox then Immediate" in renderer
    assert '256 0' in (ROOT / "M14-LLVM-STATE-MEM0-RUN.cmd").read_text(
        encoding="utf-8"
    )
    assert '128 0' in (ROOT / "M14-LLVM-CHUNK128-RUN.cmd").read_text(
        encoding="utf-8"
    )
    assert (ROOT / "M14-PERF-REPORT.cmd").is_file()
    assert (ROOT / "M14-PERF-STATE-MEM.cmd").is_file()
    assert (ROOT / "M14-DEBUG-YOLD-BENCH.cmd").is_file()
    assert (ROOT / "M14-DEBUG-CAMERA-TRACE.cmd").is_file()
    assert (ROOT / "M14-DEBUG-YOLD-GROUP-SWEEP.cmd").is_file()
    assert (ROOT / "M14-DEBUG-YOLD-NPC-SWEEP.cmd").is_file()
    assert (ROOT / "M14-DEBUG-YOLD-SWEEP.cmd").is_file()



def test_yold_npc_sweep_analyzer_contract() -> None:
    analyze_yold_npc_sweep.self_test()
    source_path = HERE / "analyze_yold_npc_sweep.py"
    source = source_path.read_text(encoding="utf-8")
    ast.parse(source, filename=str(source_path))
    assert "SESSION_WINDOW_SECONDS" in source
    assert "NPC one-pulse=on ordinal=" in source
    assert "latest sweep is incomplete" in source
    assert "largest single-NPC signal" in source


def test_yold_npc_group_analyzer_contract() -> None:
    analyze_yold_npc_groups.self_test()
    source_path = HERE / "analyze_yold_npc_groups.py"
    source = source_path.read_text(encoding="utf-8")
    ast.parse(source, filename=str(source_path))
    assert "SESSION_WINDOW_SECONDS" in source
    assert "hot_8008533c_per_16_median" in source
    assert "active-NPC group threshold sweep" in source
    assert "threshold/nonlinear" in source
    assert "YOLD-GROUP-ACTIVE-BEGIN" in source
    assert "YOLD-GROUP-ACTIVE-END" in source
    assert "exact_active_window" in source


def test_m14_interp_replay_analyzer_contract() -> None:
    analyze_m14_interp_replay.self_test()
    source_path = HERE / "analyze_m14_interp_replay.py"
    source = source_path.read_text(encoding="utf-8")
    ast.parse(source, filename=str(source_path))
    assert "M14 dev140 replay prep interval=" in source
    assert "M14 dev140 replay prep skip interval=" in source
    assert "M14 dev140b replay encode frame=" in source
    assert "matched_draws" in source
    assert "unmatched_draws" in source
    assert "draw_match=" in source
    assert "M14 dev140 replay cadence " in source
    assert "replay cadence:" in source
    assert "reject_prior_load" in source
    assert "Using framebuffer size" in source
    assert (ROOT / "M14-INTERP-REPORT.cmd").is_file()


def test_m14_perf_analyzer_contract() -> None:
    analyze_m14_perf.self_test()
    source = (HERE / "analyze_m14_perf.py").read_text(encoding="utf-8")
    ast.parse(source, filename=str(HERE / "analyze_m14_perf.py"))
    assert "first_retrace_margin_ms" in source
    assert "nested_steps_per_attempt_x1000" in source
    assert "latest_capture" in source
    assert "latest_capture_for_config" in source
    assert "state_in_memory" in source
    assert "compare_latest_state_memory" in source
    assert "chunk_identity_error" in source
    assert "manifest_valid" in source
    assert "capture_sha256" in source
    assert "quality_issues" in source
    assert "regression_failures" in source
    assert "--quality-gate" in source
    assert "--regression-gate" in source


def test_live_debug_console_contract() -> None:
    backend = (ROOT / "backend" / "native" / "openspm_native_backend_v12.c").read_text(
        encoding="utf-8"
    )
    client_path = ROOT / "tools" / "native_bridge" / "m14_debug_console.py"
    gui_path = ROOT / "tools" / "native_bridge" / "m14_debug_gui.py"
    client = client_path.read_text(encoding="utf-8")
    gui = gui_path.read_text(encoding="utf-8")
    launcher = (ROOT / "M14-DEBUG-CONSOLE.cmd").read_text(encoding="utf-8")
    exi_test = (ROOT / "tests" / "m14_exi_diagnostic_test.c").read_text(
        encoding="utf-8"
    )
    docs = (ROOT / "docs" / "M14-LIVE-DEBUG-CONSOLE-2026-09-19.md").read_text(
        encoding="utf-8"
    )
    experiment_dir = ROOT / "tools" / "native_bridge" / "experiments"

    ast.parse(client, filename=str(client_path))
    ast.parse(gui, filename=str(gui_path))

    assert '#include "openspm/debug_ipc.h"' in backend
    assert 'out_api->backend_version = "14.0-dev29";' in backend
    assert "M14 dev29 live debug cockpit ready" in backend
    assert 'getenv("OPENSPM_M14_STICKY_PRESSED")' in backend
    assert "g_v12_sticky_pressed_enabled" in backend
    assert "M14 dev123 sticky-pressed A/B disabled:" in backend
    assert "g_v9_input_down | g_v12_input_repeat" in backend
    assert "g_v9_input_pressed & ~g_v12_input_observed_pressed" in backend
    assert "Local\\\\OpenSPM.Debug.%lu" in backend
    assert "v12_debug_poll(state);" in backend
    assert "v12_debug_apply_runtime_toggles();" in backend
    assert "OPENSPM_M14_HOST_EVENT_PRESENT_SAFEPOINT" in backend
    assert "R8PE01_DEBUG_MAP_DATA" in backend
    assert "R8PE01_DEBUG_MARIO_WORK" in backend
    assert "R8PE01_DEBUG_MARIO_MAX_HP" in backend
    assert "R8PE01_DEBUG_NPCDRV_WP" in backend
    assert "R8PE01_DEBUG_NPC_ENTRY_SIZE" in backend
    assert "R8PE01_DEBUG_NPC_ACTIVE" in backend
    assert 'strcmp(arg1, "list") == 0' in backend
    assert "active_npcs=" in backend
    assert "R8PE01_DEBUG_NPC_ENTRY_FLAG46C" in backend
    assert "R8PE01_DEBUG_NPC_ENTRY_HIDDEN_FROZEN" in backend
    assert "g_v12_debug_npc_snapshot_entries" in backend
    assert "g_v12_debug_npc_suspend_map" in backend
    assert "NPC suspend auto-disabled after map change" in backend
    assert "NPC suspend auto-disabled after NPC table replacement" in backend
    assert "Restore only the active bit we changed" in backend
    assert "NPC suspend refused" in backend
    assert "NPC isolation auto-disabled after map/table change" in backend
    assert "NPC active-bit pulse auto-restored" in backend
    assert 'strcmp(arg1, "pulseone") == 0' in backend
    assert "v12_debug_apply_npc_one_pulse" in backend
    assert "v12_debug_clear_npc_one_pulse" in backend
    assert "only flag8.active is suppressed and auto-restored" in backend
    assert "live debug command BEGIN n=" in backend
    assert "live debug command END n=" in backend
    assert "g_v12_debug_npc_suspend_pulse_frames" in backend
    assert "v12_debug_restore_npc_isolation" in backend
    assert "entries remain active structurally" in backend
    assert "v12_debug_reset_npc_controls();" in backend
    assert "v12_debug_reset_all_controls" in backend
    assert "queued semantic map change to %s" in backend

    for command in (
        "status", "map", "resetdebug", "story", "gsw", "warp",
        "nextroom", "prevroom", "reloadroom",
        "pos", "setpos", "nudge", "checkpoint", "hp", "speed", "moonjump",
        "npcs", "freeze8", "freeze16", "freeze32", "unfreeze", "freezes",
        "read8", "read16", "read32",
        "write8", "write16", "write32", "dump", "mark",
    ):
        assert command in backend

    assert 'PROCESS_NAME = "OpenSPMNative.exe"' in client
    assert "OpenFileMappingW" in client
    assert "CreateMutexW" in client
    assert "Local\\\\OpenSPM.Debug.Client." in client
    assert 'response_generation") == generation' in client
    assert "_send_lock = threading.RLock()" in client
    assert "--script" in client
    assert 'lower.startswith("defer ")' in client
    assert 'lower.startswith("require_map ")' in client
    assert "benchmark map mismatch:" in client
    assert 'lower.startswith("require_npcs ")' in client
    assert "benchmark NPC population mismatch:" in client
    assert 'lower.startswith("require_npc_names ")' in client
    assert "benchmark NPC identity mismatch:" in client
    assert 'lower.startswith("stable_npcs ")' in client
    assert "NPC stability check failed:" in client
    assert 'lower.startswith("npc_group_pulse ")' in client
    assert "NPC group pulse active:" in client
    assert "YOLD-GROUP-ACTIVE-BEGIN" in client
    assert "YOLD-GROUP-ACTIVE-END" in client
    # The diagnostic is intentionally split across adjacent Python string
    # literals in m14_debug_console.py for line length. Contract the stable
    # semantic fragments instead of requiring source-text whitespace to match.
    assert "selected NPC active bit re-enabled during " in client
    assert "measurement: " in client
    assert "NPC group pulse restoration failed:" in client
    assert "active_marker_started = False" in client
    assert "refusing stale-address restoration" in client
    assert 'lower.startswith("camera_trace")' in client
    assert "R8PE01_CAM_PTR_TABLE = 0x804C9F80" in client
    assert "CAM_ID_3D = 5" in client
    assert "CAM_ID_2D = 11" in client
    assert "saved camera trace:" in client
    assert "stalled_while_mario_moves=" in client
    assert 'lower.startswith("sample ")' in client
    assert "capture auto-stopped" in client
    assert "DEFAULT_CAPTURE_DIR" in client
    assert "capture_sha256" in client
    assert "repo_revision" in client
    assert "source_rotated_during_capture" in client

    assert "OpenSPM Live Debug Cockpit" in gui
    assert "NPC A/B template" in gui
    assert "Hide + freeze NPCs (safe structural isolation)" in gui
    assert "stable_npcs 2.0 0.20" in gui
    assert "npcs isolate hide 0" in gui
    assert "Run selection" in gui
    assert "Save position" in gui
    assert "Restore position" in gui
    assert "restore_defaults" in gui
    assert "save_transcript" in gui
    assert "execute_script_text(" in gui

    assert "m14_debug_gui.py" in launcher
    assert "m14_debug_console.py" in launcher
    assert '"%PY%"' in launcher
    assert "if errorlevel 1 exit /b 1" in launcher
    assert "exit /b %errorlevel%" not in launcher
    assert 'strcmp(backend.backend_version, "14.0-dev29") == 0' in exi_test
    assert "m14 dev29 wrapper" in exi_test
    for experiment in (
        "cockpit_smoke.spmdbg",
        "npc_performance_ab.spmdbg",
        "stop_cleanup_test.spmdbg",
        "yold_perf_benchmark.spmdbg",
        "yold_npc_active_sweep.spmdbg",
        "yold_npc_group_sweep.spmdbg",
        "camera_follow_trace.spmdbg",
    ):
        assert (experiment_dir / experiment).is_file()
    npc_ab = (experiment_dir / "npc_performance_ab.spmdbg").read_text(
        encoding="utf-8"
    )
    assert "stable_npcs 2.0 0.20" in npc_ab
    yold_bench = (experiment_dir / "yold_perf_benchmark.spmdbg").read_text(
        encoding="utf-8"
    )
    assert "require_map he2_03" in yold_bench
    assert "require_npcs 7" in yold_bench
    assert "require_npc_names mise,mura_1,mura_2,mura_3,mura_4,mura_5,son" in yold_bench
    assert "capture start yold-perf" in yold_bench
    yold_sweep = (experiment_dir / "yold_npc_active_sweep.spmdbg").read_text(
        encoding="utf-8"
    )
    assert "require_map he2_03" in yold_sweep
    assert "require_npcs 7" in yold_sweep
    assert "require_npc_names mise,mura_1,mura_2,mura_3,mura_4,mura_5,son" in yold_sweep
    assert "resetdebug" in yold_sweep
    assert "npcs pulseone 0 120" in yold_sweep
    assert "npcs pulseone 6 120" in yold_sweep
    assert "npcs pulseone off" in yold_sweep
    yold_groups = (experiment_dir / "yold_npc_group_sweep.spmdbg").read_text(
        encoding="utf-8"
    )
    assert "capture start yold-group-baseline" in yold_groups
    assert "npc_group_pulse 1.85 mura_1,mura_2" in yold_groups
    assert "npc_group_pulse 1.85 mura_1,mura_2,mura_3,mura_4" in yold_groups
    assert "npc_group_pulse 1.85 mura_1,mura_2,mura_3,mura_4,mura_5,son" in yold_groups
    assert "npc_group_pulse 1.25 mise,mura_1,mura_2,mura_3,mura_4,mura_5,son" in yold_groups
    assert yold_groups.count("reloadroom") >= 4
    assert "npcs isolate hide 0" not in npc_ab
    assert "npcs suspend pulse" not in npc_ab
    assert "npcs suspend on" not in npc_ab
    assert (ROOT / "M14-DEBUG-SMOKE.cmd").is_file()
    assert (ROOT / "M14-DEBUG-NPC-AB.cmd").is_file()
    assert (ROOT / "M14-DEBUG-YOLD-BENCH.cmd").is_file()
    perf_gate = (ROOT / "M14-PERF-GATE.cmd").read_text(encoding="utf-8")
    assert "--compare-latest-chunks" in perf_gate
    assert "--quality-gate" in perf_gate
    assert "--regression-gate" in perf_gate
    assert "guest-thread frame boundary" in docs
    assert "generated LLVM/x86 code" in docs
    assert "defer" in docs
    assert "debug-captures" in docs


def test_live_debug_client_behavior() -> None:
    client_path = ROOT / "tools" / "native_bridge" / "m14_debug_console.py"
    spec = importlib.util.spec_from_file_location(
        "openspm_m14_debug_console_contract", client_path
    )
    assert spec is not None and spec.loader is not None
    client = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(client)

    class FakeConnection:
        pid = 4242

        def __init__(self, launch_log: Path) -> None:
            self.launch_log = launch_log
            self.sent: list[str] = []

        def send(self, command: str) -> str:
            self.sent.append(command)
            if command == "emitlog":
                with self.launch_log.open("ab") as stream:
                    stream.write(b"phase-data\\n")
            if command == "map":
                return "map=he2_03 door=dokan_1 gp=0x80500000"
            if command == "npcs info":
                return "num=80 active=7 frozen=no suspended=no entries=0x8077A0A0"
            if command == "npcs list":
                return (
                    "active_npcs=#1:id=1,tribe=1,name=mise "
                    "#2:id=2,tribe=2,name=mura_1 "
                    "#3:id=3,tribe=3,name=mura_2 "
                    "#4:id=4,tribe=4,name=mura_3 "
                    "#5:id=5,tribe=5,name=mura_4 "
                    "#6:id=6,tribe=6,name=mura_5 "
                    "#7:id=7,tribe=7,name=son"
                )
            return f"ok:{command}"

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        launch_log = root / "launch.log"
        launch_log.write_bytes(b"old-data\\n")
        capture_dir = root / "captures"
        connection = FakeConnection(launch_log)
        capture = client.LaunchLogCapture(launch_log, capture_dir)
        emitted: list[str] = []

        script = """\
defer cleanup
require_map he2_03
require_npcs 7
require_npc_names mise,mura_1,mura_2,mura_3,mura_4,mura_5,son
capture start behavior
emitlog
sample 0 2 status
capture stop
"""
        assert client.execute_script_text(
            connection, script, emit=emitted.append, capture=capture
        )
        assert connection.sent == [
            "map",
            "npcs info",
            "npcs list",
            "mark CAPTURE-BEGIN behavior",
            "emitlog",
            "status",
            "status",
            "mark CAPTURE-END behavior",
            "cleanup",
        ]
        captures = list(capture_dir.glob("*-behavior.log"))
        assert len(captures) == 1
        data = captures[0].read_bytes()
        assert b"old-data" not in data
        assert b"phase-data" in data
        manifest_path = captures[0].with_suffix(".json")
        assert manifest_path.is_file()
        manifest = __import__("json").loads(
            manifest_path.read_text(encoding="utf-8")
        )
        assert manifest["schema_version"] == 1
        assert manifest["capture_file"] == captures[0].name
        assert manifest["captured_log_bytes"] > 0
        assert manifest["source_rotated_during_capture"] is False
        assert not capture.active

        # Wrong-room benchmark directives fail before capture/mutation.
        wrong_room_connection = FakeConnection(launch_log)
        wrong_room_emitted: list[str] = []
        assert not client.execute_script_text(
            wrong_room_connection,
            "require_map he2_02\n",
            emit=wrong_room_emitted.append,
        )
        assert wrong_room_connection.sent == ["map"]
        assert any("benchmark map mismatch" in row for row in wrong_room_emitted)

        # Camera trace parsing is source-only/read-only and can be validated
        # without a live mailbox or writing to the real capture directory.
        assert client._parse_read32_value(
            "[0x804C9F94] = 0x81234567 (2166572391)"
        ) == 0x81234567
        f32 = client._parse_dump_bytes("3F 80 00 00", 4)
        assert abs(client._guest_f32(f32, 0) - 1.0) < 1e-6
        assert client._parse_pos_response(
            "pos=(-12.500,3.250,99.000)"
        ) == (-12.5, 3.25, 99.0)
        camera_rows = [
            {
                "mario_x": 0.0,
                "cam11_target_x": 0.0, "cam11_pos_x": 0.0,
                "cam5_target_x": 0.0, "cam5_pos_x": 0.0,
            },
            {
                "mario_x": 1.0,
                "cam11_target_x": 0.0, "cam11_pos_x": 0.0,
                "cam5_target_x": 1.0, "cam5_pos_x": 1.0,
            },
            {
                "mario_x": 2.0,
                "cam11_target_x": 2.0, "cam11_pos_x": 2.0,
                "cam5_target_x": 2.0, "cam5_pos_x": 2.0,
            },
        ]
        summary = client._camera_trace_summary(camera_rows)
        assert any("camera 11:" in row for row in summary)
        assert any("stalled_while_mario_moves=1" in row for row in summary)

        # Wrong NPC population also fails before capture/mutation.
        class WrongNpcConnection(FakeConnection):
            def send(self, command: str) -> str:
                self.sent.append(command)
                if command == "npcs info":
                    return "num=80 active=6 frozen=no suspended=no entries=0x8077A0A0"
                return f"ok:{command}"

        wrong_npc_connection = WrongNpcConnection(launch_log)
        wrong_npc_emitted: list[str] = []
        assert not client.execute_script_text(
            wrong_npc_connection,
            "require_npcs 7\n",
            emit=wrong_npc_emitted.append,
        )
        assert wrong_npc_connection.sent == ["npcs info"]
        assert any(
            "benchmark NPC population mismatch" in row
            for row in wrong_npc_emitted
        )

        class WrongNpcIdentityConnection(FakeConnection):
            def send(self, command: str) -> str:
                self.sent.append(command)
                if command == "npcs list":
                    return (
                        "active_npcs=#1:id=1,tribe=1,name=mise "
                        "#2:id=2,tribe=2,name=mura_1 "
                        "#3:id=3,tribe=3,name=mura_2 "
                        "#4:id=4,tribe=4,name=mura_3 "
                        "#5:id=5,tribe=5,name=mura_4 "
                        "#6:id=6,tribe=6,name=mura_5 "
                        "#7:id=7,tribe=99,name=not_so"
                    )
                return f"ok:{command}"

        wrong_identity_connection = WrongNpcIdentityConnection(launch_log)
        wrong_identity_emitted: list[str] = []
        assert not client.execute_script_text(
            wrong_identity_connection,
            "require_npc_names mise,mura_1,mura_2,mura_3,mura_4,mura_5,son\n",
            emit=wrong_identity_emitted.append,
        )
        assert wrong_identity_connection.sent == ["npcs list"]
        assert any(
            "benchmark NPC identity mismatch" in row
            for row in wrong_identity_emitted
        )

        # Stop during a wait: deferred cleanup must run and the active capture
        # must auto-close, while commands after the wait never execute.
        launch_log.write_bytes(b"new-launch\\n")
        capture2 = client.LaunchLogCapture(launch_log, capture_dir)
        connection2 = FakeConnection(launch_log)
        calls = 0

        def stop_requested() -> bool:
            nonlocal calls
            calls += 1
            return calls >= 5

        stopped = client.execute_script_text(
            connection2,
            """\
defer cleanup-stop
capture start stopped
emitlog
wait 0.2
never
""",
            emit=emitted.append,
            stop_requested=stop_requested,
            capture=capture2,
        )
        assert not stopped
        assert "never" not in connection2.sent
        assert connection2.sent[-2:] == [
            "cleanup-stop",
            "mark CAPTURE-END stopped",
        ]
        stopped_captures = list(capture_dir.glob("*-stopped.log"))
        assert len(stopped_captures) == 1
        assert b"phase-data" in stopped_captures[0].read_bytes()
        assert not capture2.active


def test_draw_tags_bridge_contract() -> None:
    """Stage B draw tags: bridge-only, opt-in, at the exact dispDraw seams."""
    bridge = (HERE / "legacy_c_adapter_v42_dev69.c.in").read_text(encoding="utf-8")
    header = (HERE / "m14_draw_tags.h").read_text(encoding="utf-8")
    patcher = (HERE / "apply_openspm_llvm_wpar_codegen_patch.py").read_text(encoding="utf-8")
    # The generated-code seam is the existing dev105 helper; codegen is untouched.
    assert "term.guest_pc == 0x8005B29Cu" in patcher
    assert "draw_tag" not in patcher
    assert '#include "m14_draw_tags.h"' in bridge
    for address in ("0x8005B29C", "0x8005B2A0", "0x801A7D1C", "0x801A8B64", "0x8056D1C8",
                    "0x8056D1D0", "0x8056D1D4", "0x804C9F80", "0x00000158"):
        assert f"UINT32_C({address})" in bridge, address
    # Off = the dev105 walker unchanged (one cached env check), nothing emitted.
    wrapper = bridge[bridge.index("int openspm_m14_dev105_cpustate_nested_dispatch(CPUState *cpu,"):]
    assert wrapper.index("if (!bridge_m14_draw_tags_enabled())") < wrapper.index(
        "bridge_m14_draw_tags_begin(instance, cpu, (uint32_t)target);")
    assert "static int bridge_dev105_cpustate_nested_dispatch(CPUState *cpu," in bridge
    phase = bridge[bridge.index("static void bridge_m14_draw_tags_phase("):]
    assert phase.index("!bridge_m14_draw_tags_enabled()") < phase.index("bridge_m14_draw_tags_emit(")
    assert "bridge_m14_draw_tags_phase(instance, (uint32_t)phase);" in bridge
    # Never inside a display list; markers go through the dev16 gather in order.
    emit = bridge[bridge.index("static int bridge_m14_draw_tags_emit("):]
    assert emit.index("bridge_m14_draw_tags_in_display_list(cpu)") < emit.index(
        "g_m14_v8_wg_bytes[g_m14_v8_wg_size++] = bytes[i];")
    assert "OPENSPM_LOCAL_GXDL_ACTIVE_OFF" in bridge and "g_m14_v19_dl.recorder_active" in bridge
    # Owner identity excludes the (unstable) entry pointer; occurrences follow pool order.
    assert "m14_draw_tags_registered_occurrence(" in bridge
    assert "#define OPENSPM_M14_DRAW_TAG_OWNER_REG UINT32_C(0x7E)" in header
    assert "#define OPENSPM_M14_DRAW_TAG_CONTROL_REG UINT32_C(0x7F)" in header
    assert "M14 draw tags bridge: frames=" in bridge


def test_audio_bridge_contract() -> None:
    """Opt-in audio (docs/AUDIO-PLAN.md stage 2): bridge-only, off by default.

    OPENSPM_AUDIO unset: nothing is wrapped (MMIO, host-call service, run path
    and event handlers stay exactly as before) and the generated-C silent audio
    stubs are unchanged. No codegen/LLVM-object change: the device and the AX
    HLE are plain OpenSPM C sources linked into the module.
    """
    active = (HERE / "legacy_c_adapter_v42.c.in").read_text(encoding="utf-8")
    audio = (HERE / "m14_audio_bridge.h").read_text(encoding="utf-8")
    v7 = (HERE / "legacy_c_adapter_v7.c.in").read_text(encoding="utf-8")
    guard = (HERE / "guard_hosted_direct_entries.py").read_text(encoding="utf-8")
    cmake = (HERE / "CMakeLists.txt").read_text(encoding="utf-8")
    patcher = (HERE / "apply_openspm_llvm_wpar_codegen_patch.py").read_text(encoding="utf-8")
    settings = (ROOT / "launcher" / "native_settings.h").read_text(encoding="utf-8")
    vanilla = (ROOT / "M14-VANILLA-RUN.cmd").read_text(encoding="utf-8")

    assert '#include "m14_audio_bridge.h"' in active
    assert '#include "../../backend/audio/openspm_audio_device.h"' in audio
    # Off by default: the attach returns before any wrapper is installed.
    attach = audio[audio.index("static void bridge_m14_audio_attach("):]
    attach = attach[:attach.index("\n}\n")]
    off = attach.index("if (instance == NULL || !bridge_m14_audio_requested()) return;")
    for install in ("instance->cpu.external_read = bridge_m14_audio_external_read;",
                    "instance->cpu.external_write = bridge_m14_audio_external_write;",
                    "g_m14_hostcall.handle = bridge_m14_audio_hostcall;",
                    "g_m14_event_interrupt_handlers[0] = OPENSPM_M14_AUDIO_AID_HANDLER;",
                    "g_m14_audio.attached = 1;"):
        assert off < attach.index(install), install
    assert 'bridge_m14_read_env("OPENSPM_AUDIO", value, sizeof(value))' in audio
    service = audio[audio.index("static int bridge_m14_audio_service("):]
    assert service.index(
        "if (!g_m14_audio.attached || g_m14_audio.disabled || instance == NULL) return OPENSPM_OK;"
    ) < service.index("openspm_audio_device_poll(")
    # Hooked at the dev70 top-level safepoint, before the underlying run/retrace.
    run = active[active.index("static int OPENSPM_CALL bridge_run_dev70("):]
    assert run.index("result = bridge_m14_audio_service(instance);") < run.index(
        "result = g_m14_dev70_underlying_run(opaque, soft_cycle_budget, out_exit);")
    assert "bridge_m14_audio_attach((openspm_bridge_instance *)*out_instance);" in active
    assert "bridge_m14_audio_detach((openspm_bridge_instance *)opaque);" in active
    # Guest interrupts like the VI retrace: (interrupt, OS_CURRENT_CONTEXT), EE-gated.
    for fact in ("OPENSPM_M14_AUDIO_AID_HANDLER UINT32_C(0x8028521C)",
                 "OPENSPM_M14_AUDIO_DSP_HANDLER UINT32_C(0x80289B38)",
                 "OPENSPM_M14_AUDIO_DSP_AI_INTERRUPT UINT32_C(5)",
                 "OPENSPM_M14_AUDIO_DSP_DSP_INTERRUPT UINT32_C(7)",
                 "OPENSPM_M14_AUDIO_CHECK_MAIL_FROM_DSP UINT32_C(0x8028987C)",
                 "(instance->cpu.msr & OPENSPM_M14_MSR_EE) == 0u",
                 "bridge_run_m14_event(instance, OPENSPM_M14_AUDIO_AID_HANDLER, 2u, args)",
                 "bridge_run_m14_event(instance, OPENSPM_M14_AUDIO_DSP_HANDLER, 2u, args)",
                 "M14 audio: frames=%u(+%u) cmdlists=%u(+%u) voices_active=%u",
                 "underruns=%u(+%u) queued_ms=%u"):
        assert fact in audio, fact
    # The DSP answer is cleared before its handler runs (the handler may mail again).
    deliver = audio[audio.index("static int bridge_m14_audio_deliver("):]
    assert deliver.index("openspm_audio_device_dsp_delivered(&g_m14_audio.dev);") < deliver.index(
        "OPENSPM_M14_AUDIO_DSP_HANDLER, 2u, args")
    # 0x8028987C keeps the backend's historical answer unless the device's mail is pending.
    hostcall = audio[audio.index("static int OPENSPM_CALL bridge_m14_audio_hostcall("):]
    assert "openspm_audio_device_mail_pending(&g_m14_audio.dev)" in hostcall.split(
        "return g_m14_audio.underlying_handle")[0]
    # v7: the extra handler table is empty unless the audio layer registers one.
    assert "static uint32_t g_m14_event_interrupt_handlers[4];" in v7
    # v7: fast-forward "unlimited" skips only the scheduler-idle host wait, through the appended
    # host API field (older hosts: struct_size check, NULL or 0 answer = the historical wait).
    assert "static int bridge_m14_clock_skip_to(const openspm_bridge_instance *instance," in v7
    assert "goto m14_retrace_due;" in v7 and v7.count("m14_retrace_due:") == 1
    assert "offsetof(openspm_host_api, clock_skip_to)" in v7
    assert "if (target == OPENSPM_M14_VI_RETRACE_HANDLER) return 1;" in v7
    assert "!bridge_m14_event_is_interrupt_handler(event_target)" in v7
    # Generated-C (rollback) silent stubs unchanged; codegen never sees audio.
    assert 'INLINE_SILENT_AUDIO_VOID = "ctx->pc = ctx->lr; return;"' in guard
    for target in ("R8PE01_AI_INIT_DMA,", "R8PE01_AI_START_DMA,", "R8PE01_AX_OUT_INIT_DSP,"):
        assert target in guard, target
    assert "OPENSPM_AUDIO" not in guard and "audio" not in patcher.lower()
    assert '"${OPENSPM_ROOT}/backend/audio/openspm_audio_device.c"' in cmake
    assert '"${OPENSPM_ROOT}/backend/audio/openspm_ax_hle.c"' in cmake
    # Player switches: [Audio] enabled/volume and the launcher flag `audio`.
    assert '{"Audio", "enabled", "OPENSPM_AUDIO", "1|0"}' in settings
    assert '{"Audio", "volume", "OPENSPM_AUDIO_VOLUME", "#pct"}' in settings
    assert 'if /i "%%~A"=="audio" set "OPENSPM_AUDIO=1"' in vanilla
    assert 'set "OPENSPM_AUDIO=' not in vanilla.replace('set "OPENSPM_AUDIO=1"', "")


def _freestanding_import_names(header: str, crt_source: str) -> tuple[set[str], set[str]]:
    """Split the shim's dllimport declarations into kernel32 and ucrtbase."""
    import re

    kernel32: set[str] = set()
    ucrtbase: set[str] = set()
    for text in (header, crt_source):
        flat = " ".join(text.split())
        for match in re.finditer(r"OPENSPM_CRT_IMPORT ([^;{]*?)\(", flat):
            decl = match.group(1).split()
            name = decl[-1].lstrip("*")
            if name == "__declspec":  # the #define of OPENSPM_CRT_IMPORT itself
                continue
            (kernel32 if "WINAPI" in decl else ucrtbase).add(name)
    return kernel32, ucrtbase


def _def_exports(path: Path) -> tuple[str, set[str]]:
    library = ""
    names: set[str] = set()
    in_exports = False
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split(";", 1)[0].strip()
        if not line:
            continue
        if line.startswith("LIBRARY "):
            library = line.split(None, 1)[1]
        elif line == "EXPORTS":
            in_exports = True
        elif in_exports:
            names.add(line)
    return library, names


def _freestanding_link_smoke() -> str:
    """Cross-compile + link a tiny freestanding DLL when LLVM tools are present.

    Linux/CI only: step 2/7 on a player PC stays source-only. Proves the shim
    compiles in MSVC mode with no CRT/SDK headers and links with lld-link
    /NODEFAULTLIB against the llvm-dlltool import libraries alone.
    """
    import shutil
    import subprocess

    if os.name == "nt":
        return "skipped on Windows (source-only step)"
    tools = {}
    for name in ("clang", "lld-link", "llvm-dlltool", "llvm-readobj"):
        found = shutil.which(name)
        if found is None:
            return f"skipped ({name} not found)"
        tools[name] = found
    include = HERE / "freestanding" / "include"
    with tempfile.TemporaryDirectory(prefix="openspm-freestanding-") as temp_name:
        temp = Path(temp_name)
        probe = temp / "probe.c"
        probe.write_text(
            "#include <stdio.h>\n#include <stdlib.h>\n#include <string.h>\n"
            "#include <math.h>\n#include <fenv.h>\n#include <windows.h>\n"
            "#include <stdatomic.h>\n#include <xmmintrin.h>\n"
            "static volatile LONG n;\n"
            "static DWORD WINAPI worker(LPVOID p) { (void)p; InterlockedIncrement(&n); return 0; }\n"
            "__declspec(dllexport) int probe(char *out, size_t size, double x) {\n"
            "  char big[9000]; HANDLE t; memset(big, 'a', sizeof big); big[sizeof big - 1] = 0;\n"
            "  fesetround(FE_TOWARDZERO); x = floor(x) + fmod(x, 3.0) + fma(x, x, 1.0);\n"
            "  fesetround(FE_TONEAREST);\n"
            "  t = CreateThread(NULL, 0, worker, NULL, 0, NULL);\n"
            "  if (t) { WaitForSingleObject(t, INFINITE); CloseHandle(t); }\n"
            "  return snprintf(out, size, \"%s|%.3f|%llu|%lu\", getenv(\"PATH\") ? \"e\" : \"-\",\n"
            "                  x, (unsigned long long)strlen(big), (unsigned long)n);\n"
            "}\n",
            encoding="utf-8",
        )
        cc = [
            tools["clang"], "--target=x86_64-pc-windows-msvc", "-nostdlibinc",
            "-isystem", str(include), "-std=c11", "-O2", "-fno-stack-protector",
            "-Werror=implicit-function-declaration", "-c",
        ]
        for source, obj in ((HERE / "m14_freestanding_crt.c", "crt.obj"), (probe, "probe.obj")):
            subprocess.run(cc + [str(source), "-o", str(temp / obj)], check=True)
        for dll in ("kernel32", "ucrtbase"):
            subprocess.run([
                tools["llvm-dlltool"], "-m", "i386:x86-64",
                "-d", str(HERE / f"m14_freestanding_{dll}.def"),
                "-l", str(temp / f"{dll}.lib"),
            ], check=True)
        subprocess.run([
            tools["lld-link"], "/nologo", "/dll", "/nodefaultlib", "/machine:x64",
            f"/out:{temp / 'probe.dll'}", str(temp / "probe.obj"), str(temp / "crt.obj"),
            str(temp / "kernel32.lib"), str(temp / "ucrtbase.lib"),
        ], check=True)
        imports = subprocess.run(
            [tools["llvm-readobj"], "--coff-imports", str(temp / "probe.dll")],
            check=True, capture_output=True, text=True,
        ).stdout
        dlls = {line.split(":", 1)[1].strip().lower()
                for line in imports.splitlines() if line.strip().startswith("Name:")}
        assert dlls == {"kernel32.dll", "ucrtbase.dll"}, dlls
        assert "__stdio_common_vsprintf" in imports and "CreateThread" in imports
    return "linked (kernel32 + ucrtbase imports only)"


def test_native_module_toolchain_contract() -> None:
    """Stage 1/2 opt-in clang-cl + lld-link path; the default stays MSVC."""
    import build_local_llvm_module as module_builder

    cmake = (HERE / "CMakeLists.txt").read_text(encoding="utf-8")
    runner = (HERE / "run_m14_llvm_candidate.py").read_text(encoding="utf-8")
    builder = (HERE / "build_local_llvm_module.py").read_text(encoding="utf-8")
    cmd = (ROOT / "M14-LLVM-UPDATE-RUN.cmd").read_text(encoding="utf-8")
    header = (HERE / "m14_freestanding_crt.h").read_text(encoding="utf-8")
    crt_source = (HERE / "m14_freestanding_crt.c").read_text(encoding="utf-8")

    # Default = MSVC everywhere, and the MSVC branch keeps cl /GL + link /LTCG.
    assert 'set(OPENSPM_LOCAL_TOOLCHAIN "msvc" CACHE STRING' in cmake
    assert 'DEFAULT_NATIVE_MODULE_TOOLCHAIN = "msvc"' in runner
    assert "default=DEFAULT_NATIVE_MODULE_TOOLCHAIN" in runner
    assert "if args.toolchain != DEFAULT_NATIVE_MODULE_TOOLCHAIN:" in runner
    assert 'DEFAULT_LOCAL_TOOLCHAIN = "msvc"' in builder
    assert "default=DEFAULT_LOCAL_TOOLCHAIN" in builder
    assert 'set "LLVM_TOOLCHAIN=msvc"' in cmd
    assert '--toolchain "!LLVM_TOOLCHAIN!"' in cmd
    vanilla = (ROOT / "M14-VANILLA-RUN.cmd").read_text(encoding="utf-8")
    # Only a toolchain name selects the toolchain; any other 2nd argument is an
    # A/B flag (widescreen, notags, fullscreen, ...) and keeps the msvc default.
    assert 'set "NATIVE_TOOLCHAIN=msvc"' in vanilla and 'set "NATIVE_TOOLCHAIN=%~2"' not in vanilla
    assert 'if /i "%~2"=="clang" set "NATIVE_TOOLCHAIN=clang"' in vanilla
    assert 'if /i "%~2"=="clang-freestanding" set "NATIVE_TOOLCHAIN=clang-freestanding"' in vanilla
    assert '256 1 0 "%NATIVE_TOOLCHAIN%"' in vanilla
    assert module_builder.DEFAULT_LOCAL_TOOLCHAIN == "msvc"
    assert module_builder.toolchain_cmake_args("msvc", None) == []
    assert module_builder.module_build_dir_name("msvc") == "module-llvm"
    msvc_branch = cmake[cmake.index("elseif(MSVC)"):]
    assert "/experimental:c11atomics" in msvc_branch
    assert "check_ipo_supported(RESULT OPENSPM_LOCAL_IPO_SUPPORTED" in msvc_branch
    assert "INTERPROCEDURAL_OPTIMIZATION_RELEASE TRUE" in msvc_branch
    assert "MSVC /GL + /LTCG" in msvc_branch
    # clang-cl reports MSVC=1, so its branch must come first.
    clang_branch = cmake.index('if(OPENSPM_LOCAL_TOOLCHAIN MATCHES "^clang")\n    # clang-cl')
    assert clang_branch < cmake.index("elseif(MSVC)")

    # Opt-in clang path: clang-cl + lld-link + llvm-lib from one LLVM bin.
    with tempfile.TemporaryDirectory(prefix="openspm-llvm-bin-") as temp_name:
        fake_bin = Path(temp_name)
        for tool in ("clang-cl", "lld-link", "llvm-lib", "llvm-dlltool"):
            (fake_bin / f"{tool}.exe").write_bytes(b"")
        clang = module_builder.toolchain_cmake_args("clang", fake_bin)
        assert "-DOPENSPM_LOCAL_TOOLCHAIN=clang" in clang
        assert any(a.startswith("-DCMAKE_C_COMPILER=") and a.endswith("/clang-cl.exe") for a in clang)
        assert any(a.startswith("-DCMAKE_LINKER=") and a.endswith("/lld-link.exe") for a in clang)
        assert any(a.startswith("-DCMAKE_AR=") and a.endswith("/llvm-lib.exe") for a in clang)
        assert not any("DLLTOOL" in a or "TRY_COMPILE" in a for a in clang)
        free = module_builder.toolchain_cmake_args("clang-freestanding", fake_bin)
        assert "-DOPENSPM_LOCAL_TOOLCHAIN=clang-freestanding" in free
        assert any(a.endswith("/llvm-dlltool.exe") for a in free)
        assert "-DCMAKE_TRY_COMPILE_TARGET_TYPE=STATIC_LIBRARY" in free
        assert module_builder.module_build_dir_name("clang") == "module-llvm-clang"
        info = {"llvm_dir": str(fake_bin.parent / "pkg" / "lib" / "cmake" / "llvm")}
        runner_module = _load_python_module(HERE / "run_m14_llvm_candidate.py", "openspm_runner_contract")
        assert runner_module.pinned_llvm_bin(info, fake_bin) == fake_bin.resolve()
        try:
            runner_module.pinned_llvm_bin(info, None)
        except RuntimeError as exc:
            assert "missing clang-cl" in str(exc)
        else:
            raise AssertionError("pinned_llvm_bin accepted a bin without clang-cl")
    try:
        module_builder.toolchain_cmake_args("clang", None)
    except RuntimeError as exc:
        assert "requires --llvm-bin" in str(exc)
    else:
        raise AssertionError("clang toolchain accepted a missing --llvm-bin")
    assert "requires clang-cl" in cmake and "requires CMAKE_LINKER=<pinned LLVM>/bin/lld-link" in cmake
    assert "APPEND PROPERTY COMPILE_OPTIONS -flto=thin" in cmake
    assert "/clang:-fno-strict-aliasing" in cmake

    # Freestanding: wrappers, no LTO for the shim, /NODEFAULTLIB + .def import libs.
    for name in ("stdio", "stdlib", "string", "math", "fenv", "windows", "setjmp", "malloc"):
        wrapper = (HERE / "freestanding" / "include" / f"{name}.h").read_text(encoding="utf-8")
        assert '#include "../../m14_freestanding_crt.h"' in wrapper
    assert "/X /Zl /GS-" in cmake and "/NODEFAULTLIB" in cmake
    assert 'set(CMAKE_C_STANDARD_LIBRARIES "")' in cmake
    lto_start = cmake.index("set(OPENSPM_LOCAL_LTO_SOURCES")
    lto_sources = cmake[lto_start:cmake.index(")", lto_start)]
    assert "m14_freestanding_crt" not in lto_sources
    assert '"${CMAKE_CURRENT_LIST_DIR}/m14_freestanding_crt.c")' in cmake
    for symbol in ("__chkstk", "_fltused", "_DllMainCRTStartup", "__stdio_common_vsprintf",
                   "int fesetround(int mode)"):
        assert symbol in crt_source
    kernel32, ucrtbase = _freestanding_import_names(header, crt_source)
    k_lib, k_names = _def_exports(HERE / "m14_freestanding_kernel32.def")
    u_lib, u_names = _def_exports(HERE / "m14_freestanding_ucrtbase.def")
    assert k_lib == "kernel32.dll" and u_lib == "ucrtbase.dll"
    assert kernel32 == k_names, sorted(kernel32 ^ k_names)
    assert ucrtbase == u_names, sorted(ucrtbase ^ u_names)
    print(f"[OpenSPM] Freestanding module link smoke: {_freestanding_link_smoke()}")


def main() -> int:
    test_retail_lz10_input()
    test_relf_live_layout_and_augmentation()
    test_relf_rejects_layout_drift()
    test_relocation_primitives()
    test_seam_scanner()
    test_observed_indirect_entry()
    test_hostcall_prefilter_helper()
    test_direct_bl_partition_helpers()
    hp0_callback_entries.self_test()
    test_dev99_dispdraw_nested_indirect_transform()
    test_draw_tags_bridge_contract()
    test_audio_bridge_contract()
    test_dev106_phase_probe_upgrade()
    scene_profile_tests.test_transform()
    test_dev98_entry_only_cold_entry_transform()
    test_dev93_duplicate_stage_bucket_repair()
    test_cp_duplicate_pipeline_transform()
    test_dev138_interp_identity_migration_contract()
    test_dev139_async_presenter_migration_contract()
    test_dev140_replay_migration_contract()
    test_vanilla_display_pacing_contract()
    test_dev140_replay_transform_migration_contract()
    test_m14_interp_replay_analyzer_contract()
    test_renderer_perf_transform_contracts()
    test_m14_perf_analyzer_contract()
    test_yold_npc_sweep_analyzer_contract()
    test_yold_npc_group_analyzer_contract()
    test_live_debug_console_contract()
    test_live_debug_client_behavior()
    test_drawdone_retirement_successor_contract()
    test_dev91_staging_snapshot_transform()
    test_dev94_cpu_snapshot_restore_transform()
    test_dev92_guest_timing_retirement_transform()
    test_validator_shape()
    test_validator_rejects_unpatched_objects()
    test_build_contract_text()
    test_native_module_toolchain_contract()
    print(
        "PASS: OpenSPM LLVM native pipeline contracts; retail relF LZ10 decode, "
        "proven live relF/BSS layout + text2 augmentation, PPC relocation primitives, "
        "v19/relF/BSS allocation + hosted direct-call seams, measured indirect cold "
        "entry, dynamic indirect native budget guard, exact/CMake-safe object manifest, "
        "pinned Windows LLVM bootstrap, first-class dev113 chunk128/chunk256 plus experimental chunk512 indexed-dispatch A/B, inline SSA "
        "Broadway WPAR gather, dev79 measured hot-BL source partitions, dev81 native frame-phase profiler, dev85 exact budget-exit counter over dev84 32768-cycle x86 SJLJ yield quantum, dev87 exact conservative host-call prefilter, dev129 dynamic scheduler-idle dispatch + dev90 two-entry structural native-first fallback, dev97 player-derived HP-zero motion-bounded cold-entry repair, dev98 entry-only synthetic cold entries, dev99 bounded dispDraw indirect callback nesting, dev100 sampled scene ownership and nested-budget checks, dev88 retirement successor guard, dev89 hotpath, dev91 A/B archaeology, dev94 CPU-snapshot rollback, dev92 guest timing retirement, dev93 duplicate-bucket migration, exact duplicate CP pipeline reuse, dev137 cache-safe interpolation migration, dev96 native sequence truth + empty PE-bbox caller probe, revision preflight "
        "dev110/dev29 live debug cockpit + reversible experiment harness, scoped Yold benchmark/perf reporter, dev30 one-NPC live bisector, and transactional C-DLL rollback"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
