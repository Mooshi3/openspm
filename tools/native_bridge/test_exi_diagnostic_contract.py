#!/usr/bin/env python3
"""Source-level contract for M14's bounded startup/platform seams.

The backend DLL's Wii platform and player FST are initialized by backend.run(),
so a pre-run DLL test cannot stage the live EXI/font stream or exercise a real
DVDFileInfo against the player's disc tree. This contract protects that runtime
wiring while the DLL/platform tests protect executable pre-run behavior.
"""

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend" / "native" / "openspm_native_backend_v8.c"
text = BACKEND.read_text(encoding="utf-8")

checks = {
    "dev21 provenance": 'out_api->backend_version = "14.0-dev21";' in text,
    "proven ANSI IPL base":
        "#define R8PE01_ANSI_FONT_ROM_BASE UINT32_C(0x001FCF00)" in text,
    "bounded ANSI IPL window":
        "#define R8PE01_ANSI_FONT_ROM_SIZE UINT32_C(0x00003000)" in text,
    "exact live EXI selection":
        "#define R8PE01_ANSI_FONT_SELECT UINT32_C(0x00000130)" in text,
    "exact write4 command shape":
        "#define R8PE01_EXI_IMM_WRITE4 UINT32_C(0x00000035)" in text,
    "exact DMA-read shape":
        "#define R8PE01_EXI_DMA_READ UINT32_C(0x00000003)" in text,
    "font stream decodes command address": bool(
        re.search(r"address\s*=\s*v8_exi_command_address\s*\(\s*command\s*\)\s*;", text)
    ),
    "font stream rejects outside exact window": bool(
        re.search(
            r"address\s*<\s*R8PE01_ANSI_FONT_ROM_BASE\s*\|\|\s*"
            r"address\s*>=\s*R8PE01_ANSI_FONT_ROM_BASE\s*\+\s*R8PE01_ANSI_FONT_ROM_SIZE",
            text,
            re.S,
        )
    ),
    "synthetic payload carries Yay0 magic": all(
        token in text for token in (
            "g_v8_ansi_font.rom[0] = 'Y';",
            "g_v8_ansi_font.rom[1] = 'a';",
            "g_v8_ansi_font.rom[2] = 'y';",
            "g_v8_ansi_font.rom[3] = '0';",
        )
    ),
    "font payload is OpenSPM-owned and generated":
        "v8_build_ansi_font_rom();" in text and
        "OpenSPM-owned decoded font" in text,
    "DMA destination remains MEM1 bounded": bool(
        re.search(
            r"g_backend\.mem1\s*==\s*NULL\s*\|\|\s*mar\s*>\s*OPENSPM_WII_MEM1_SIZE\s*\|\|\s*"
            r"length\s*>\s*OPENSPM_WII_MEM1_SIZE\s*-\s*mar",
            text,
            re.S,
        )
    ),
    "runtime-proven AI SRC target":
        "#define R8PE01_AI_SRC_INIT UINT32_C(0x80285328)" in text,
    "AI startup register addresses are exact": all(
        token in text for token in (
            "#define R8PE01_AI_CONTROL UINT32_C(0xCD006C00)",
            "#define R8PE01_AI_VOLUME UINT32_C(0xCD006C04)",
            "#define R8PE01_AI_SAMPLE_COUNTER UINT32_C(0xCD006C08)",
            "#define R8PE01_AI_INTERRUPT_TIMING UINT32_C(0xCD006C0C)",
        )
    ),
    "AI control is bounded to documented low bits":
        "#define R8PE01_AI_CONTROL_KNOWN_BITS" in text and
        "R8PE01_AI_CONTROL_ORDINARY_BITS" in text and
        "R8PE01_AI_CONTROL_STATUS_BIT" in text,
    "AI SRC hostcall returns through LR": bool(
        re.search(
            r"case\s+R8PE01_AI_SRC_INIT\s*:[\s\S]*?state->pc\s*=\s*state->lr\s*;[\s\S]*?return\s+OPENSPM_OK\s*;",
            text,
        )
    ),
    "AI sample counter remains unsynthesized":
        "do not synthesize R8PE01_AI_SAMPLE_COUNTER" in text,
    "AI state resets per run": "memset(&g_v8_ai, 0, sizeof(g_v8_ai));" in text,
    "exact GX write-gather empty target":
        "#define R8PE01_GX_WRITE_GATHER_EMPTY UINT32_C(0x8028986C)" in text,
    "GX write-gather empty defensive query returns through LR": bool(
        re.search(
            r"case\s+R8PE01_GX_WRITE_GATHER_EMPTY\s*:[\s\S]*?state->gpr\[3\]\s*=\s*1u\s*;[\s\S]*?state->pc\s*=\s*state->lr\s*;[\s\S]*?return\s+OPENSPM_OK\s*;",
            text,
        )
    ),
    "GX write-gather empty semantic documents synchronous handoff":
        "WG FIFO writes complete synchronously" in text,
    "exact GX write-gather setup target":
        "#define R8PE01_GX_ENABLE_WRITE_GATHER UINT32_C(0x8028987C)" in text,
    "GX write-gather setup is a void native semantic": bool(
        re.search(
            r"case\s+R8PE01_GX_ENABLE_WRITE_GATHER\s*:[\s\S]*?state->pc\s*=\s*state->lr\s*;[\s\S]*?return\s+OPENSPM_OK\s*;",
            text,
        )
    ),
    "GX write-gather semantic documents native FIFO ownership":
        "OpenSPM owns WG FIFO transport" in text,

    # dev21 GameFS: Nintendo's DVDFileInfo stores startAddr/length at +0x30/+0x34.
    # Match those against the player-local FST that disc_bootstrap publishes in
    # low memory; never require a title-specific object layout to recover a path.
    "DVDFileInfo metadata offsets are exact": all(
        token in text for token in (
            "#define R8PE01_DVD_FILE_START_OFFSET UINT32_C(0x30)",
            "#define R8PE01_DVD_FILE_LENGTH_OFFSET UINT32_C(0x34)",
        )
    ),
    "disc bootstrap FST globals are exact": all(
        token in text for token in (
            "#define R8PE01_BOOT_FST_ADDRESS UINT32_C(0x80000038)",
            "#define R8PE01_BOOT_FST_MAX_SIZE UINT32_C(0x8000003C)",
        )
    ),
    "FST resolver reads DVDFileInfo start and length": bool(
        re.search(
            r"v8_read32\(file_info\s*\+\s*R8PE01_DVD_FILE_START_OFFSET\s*,\s*&start_address\).*?"
            r"v8_read32\(file_info\s*\+\s*R8PE01_DVD_FILE_LENGTH_OFFSET\s*,\s*&file_length\)",
            text,
            re.S,
        )
    ),
    "FST resolver matches both file position and length": bool(
        re.search(
            r"v8_load_be32\(node\s*\+\s*4u\)\s*==\s*start_address\s*&&\s*"
            r"v8_load_be32\(node\s*\+\s*8u\)\s*==\s*file_length",
            text,
            re.S,
        )
    ),
    "FST ambiguity fails closed": "ambiguous source metadata" in text,
    "FST path is primary and DVDMgr path is fallback":
        text.find("if (v8_fst_path_for_file_info(file_info, out_path, capacity))") != -1 and
        text.find("if (v8_wrapper_path(file_info, out_path, capacity))") != -1 and
        text.find("if (v8_fst_path_for_file_info(file_info, out_path, capacity))") <
        text.find("if (v8_wrapper_path(file_info, out_path, capacity))"),
    "DVDMgr fallback accepts harmless leading dot slash": bool(
        re.search(
            r"guest\[start\]\s*==\s*'\.'\s*&&\s*\(guest\[start\s*\+\s*1u\]\s*==\s*'/'",
            text,
            re.S,
        )
    ),
    "ordinary SDK DVD handles have runtime proof marker":
        "DVDFileInfo->FST resolution active; ordinary SDK DVD handles supported" in text,
    "hosted DVD reads use the generalized resolver":
        "!v8_resolve_gamefs_path(file_info, path, sizeof(path))" in text,
    "disc paths are no longer capped to DVDMgr's 64 bytes":
        "#define OPENSPM_V8_DISC_PATH_MAX 512u" in text and
        "char path[OPENSPM_V8_DISC_PATH_MAX];" in text,
    "legacy fileInfo-minus-40 path recovery is fallback-only":
        text.count("file_info - UINT32_C(0x40)") == 1 and
        "DVDMgr embedded-path compatibility fallback" in text,

    "unknown traffic still delegates to lower bus": bool(
        re.search(r"result\s*=\s*v6_bus_write\s*\(\s*context\s*,\s*address\s*,\s*width\s*,\s*value\s*\)\s*;", text)
    ),
    "diagnostic format still carries control/csr/data/result":
        "M14 EXI0 rejected control=0x%08X csr=0x%08X data=0x%08X result=%d" in text,
    "diagnostic reads live EXI0 CSR": "g_v3.platform.exi_csr[0]" in text,
    "diagnostic reads live EXI0 DATA": "g_v3.platform.exi_data[0]" in text,
    "only rejected EXI0 CR writes trigger diagnostic": bool(
        re.search(
            r"result\s*!=\s*OPENSPM_OK\s*&&\s*address\s*==\s*R8PE01_EXI0_CR\s*&&\s*width\s*==\s*4u",
            text,
        )
    ),
    "withdrawn Barnacle semantic is absent": "R8PE01_EXI_BARNACLE_COMMAND" not in text,
    "withdrawn RTC semantic is absent": "R8PE01_EXI_RTC" not in text,
}

failed = [name for name, ok in checks.items() if not ok]
if failed:
    for name in failed:
        print(f"M14 startup/GameFS source contract FAIL: {name}")
    raise SystemExit(1)

print("M14 EXI/font/AI/GX/GameFS source contract: PASS")
