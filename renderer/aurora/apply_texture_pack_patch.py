#!/usr/bin/env python3
"""Dolphin HD texture packs ("Load Custom Textures") on Aurora's replacement cache.

Pinned Aurora already carries a Dolphin-compatible texture replacement cache
(lib/gfx/texture_replacement.cpp): it parses tex1_<w>x<h>[_m]_<hash>[_<tlut>|_$]
_<fmt>[_arb].png|.dds names (and _mip<N> sidecars), keys each runtime texture
by XXH64 of its base level plus the referenced TLUT range exactly like Dolphin,
loads files lazily on worker threads, and samples a larger replacement through
normalized coordinates (shaders keep the original texture size). texture.cpp
consults it in resolve_static_texture / resolve_static_palette_texture, after
the EFB copy tag lookup (apply_efb_copy_ram_writeback_patch.py), so no GX hook
is added here; this script only verifies that path is still in place.

OpenSPM adds, for the F1 menu's Texture Packs tab (renderer/aurora/
openspm_texture_packs.hpp registers the packs at startup):
  * aurora::texture::openspm_replacement_stats(): indexed / replaced / resident
    counts and bytes, failed files, pending loads;
  * openspm_set_replacement_cache_budget(): GPU cache budget ([TexturePacks]
    cache_mb) instead of the fixed 4 GB;
  * openspm_set_texture_dump_directory() and Dolphin-named PNG dumps
    (tex1_..._m_... names, the "_m" flag from TEX_MODE0 like Dolphin) instead of
    Aurora's DDS dumps, so dumped textures can be edited straight into a pack.
    The naming/PNG code is renderer/aurora/openspm_texture_pack_names.hpp,
    embedded between markers (re-embedded on every configure, so edits to the
    header migrate a player's cached Aurora tree).

Off-switch: nothing changes unless the renderer registers a pack or enables
dumps ([TexturePacks] enabled=0 / no pack folder => no registrations, and the
lookup stays a mutex + counter check). Exact/idempotent; fails closed on
source drift.
"""
from __future__ import annotations

import argparse
from pathlib import Path

HERE = Path(__file__).resolve().parent
SIGNATURE = "OpenSPM texture packs"
REPLACEMENT = Path("lib/gfx/texture_replacement.cpp")
HEADER = Path("include/aurora/texture.hpp")
GX_TEXTURE = Path("lib/gx/texture.cpp")
NAMES = HERE / "openspm_texture_pack_names.hpp"

BEGIN = "// OpenSPM texture packs: Dolphin naming BEGIN (renderer/aurora/openspm_texture_pack_names.hpp)\n"
END = "// OpenSPM texture packs: Dolphin naming END\n"
EMBED_ANCHOR = '\nnamespace aurora::texture {\nnamespace {\nconstexpr Module Log{"aurora::texture"};\n'

BUDGET_OLD = "constexpr uint64_t kReplacementCacheBudgetBytes = 4294967296; // 4GB\n"
BUDGET_NEW = ("uint64_t kReplacementCacheBudgetBytes = 4294967296; // 4GB; " + SIGNATURE +
              ": openspm_set_replacement_cache_budget\n")

GLOBALS_OLD = "uint32_t s_sourceEntryCount = 0;\n"
GLOBALS_NEW = GLOBALS_OLD + (
    "// " + SIGNATURE + ": F1 menu counters and the dump folder.\n"
    "absl::flat_hash_set<uint64_t> s_openspmReplacedIds;\n"
    "std::filesystem::path s_openspmDumpRoot;\n"
    "std::filesystem::path openspm_dump_root() {\n"
    "  return s_openspmDumpRoot.empty() ? io::fs_path_from_string(g_config.cachePath) / \"texture_dumps\"\n"
    "                                   : s_openspmDumpRoot;\n"
    "}\n")

HIT_OLD = ("find_replacement_for_key_locked(const ReplacementKey& key) noexcept {\n"
           "  const auto* entry = find_selected_entry_locked(key);\n"
           "  if (entry == nullptr) {\n"
           "    return std::nullopt;\n"
           "  }\n")
HIT_NEW = HIT_OLD + "  s_openspmReplacedIds.insert(entry->id); // " + SIGNATURE + ": replaced-texture count\n"

DUMP_OLD = ('  const auto dumpRoot = io::fs_path_from_string(g_config.cachePath) / "texture_dumps";\n'
            "  const auto path = dumpRoot / format_replacement_filename(key);\n"
            "  return gfx::dds::write_rgba8_dds(path, texWidth, texHeight, pixels.data);\n")
DUMP_NEW = ("  // " + SIGNATURE + ": Dolphin-named PNG (tex1_<w>x<h>[_m]_...), ready to edit into a pack.\n"
            "  const auto dumpRoot = openspm_dump_root();\n"
            "  if (!io::create_directories(dumpRoot)) {\n"
            "    return false;\n"
            "  }\n"
            "  const bool mipmapped = GXTexObj_::get_bits(obj.mode0, 2, 5) != 0; // Dolphin: TEX_MODE0 mipmap_filter\n"
            "  const auto png = openspm_texpack::encode_png_rgba8(texWidth, texHeight, pixels.data.data());\n"
            "  const auto path = dumpRoot / (openspm_texpack::name(texWidth, texHeight, mipmapped, key.textureHash,\n"
            "                                                      key.hasTlut, key.tlutHash, key.format) + \".png\");\n"
            "  return io::write_file(path, {png.data(), png.size()});\n")

LOAD_OLD = ('  const auto dumpRoot = io::fs_path_from_string(g_config.cachePath) / "texture_dumps";\n'
            "  if (g_config.allowTextureDumps && !io::create_directories(dumpRoot)) {\n")
LOAD_NEW = ("  const auto dumpRoot = openspm_dump_root(); // " + SIGNATURE + "\n"
            "  if (g_config.allowTextureDumps && !io::create_directories(dumpRoot)) {\n")

API_ANCHOR = "} // namespace aurora::texture\n\nnamespace aurora::gfx::texture_replacement {\n"
API_CODE = r'''// OpenSPM texture packs: F1 menu counters, cache budget and dump folder.
OpenSpmReplacementStats openspm_replacement_stats() noexcept {
  OpenSpmReplacementStats stats;
  {
    std::lock_guard lk(s_registryMutex);
    stats.registered = s_sourceEntryCount;
    stats.replaced = s_openspmReplacedIds.size();
    stats.resident = s_cacheByKey.size();
    stats.residentBytes = s_replacementCacheBytes;
    stats.budgetBytes = kReplacementCacheBudgetBytes;
    stats.failed = s_failedIds.size();
  }
  stats.pendingLoads = pending_full_load_count();
  return stats;
}

void openspm_set_replacement_cache_budget(uint64_t bytes) noexcept {
  std::lock_guard lk(s_registryMutex);
  kReplacementCacheBudgetBytes = std::max<uint64_t>(bytes, 64ull << 20);
}

void openspm_set_texture_dump_directory(const std::filesystem::path& dir) noexcept {
  std::lock_guard lk(s_registryMutex);
  s_openspmDumpRoot = dir;
}
'''

DECL_ANCHOR = "bool has_replacement(const GXTexObj* obj, const GXTlutObj* tlut = nullptr);\n"
DECL_NEW = DECL_ANCHOR + r'''
/// OpenSPM texture packs: counters for the F1 menu (thread safe).
struct OpenSpmReplacementStats {
  uint64_t registered = 0;    ///< replacement files indexed (source keys)
  uint64_t replaced = 0;      ///< distinct replacements a game texture has matched
  uint64_t resident = 0;      ///< replacements currently held on the GPU
  uint64_t residentBytes = 0; ///< their size
  uint64_t budgetBytes = 0;   ///< GPU cache budget (least recently used evicted beyond it)
  uint64_t failed = 0;        ///< files that failed to load or decode
  uint64_t pendingLoads = 0;  ///< decodes queued on the worker threads
};
OpenSpmReplacementStats openspm_replacement_stats() noexcept;
/// Call before the first frame (startup), like load_replacement_directory.
void openspm_set_replacement_cache_budget(uint64_t bytes) noexcept;
void openspm_set_texture_dump_directory(const std::filesystem::path& dir) noexcept;
'''

# Chain inputs this relies on (read only): Aurora's replacement lookups in both
# static resolve paths, reached after the EFB copy tag lookup.
GX_REQUIRED = {
    "use_replacement(gfx::texture_replacement::find_source_replacement(obj, *keys->sourceKey), handle, replacementId);": 2,
    "if (!handle) handle = texture::resolve_static_texture(obj);": 1,
    "handle = texture::resolve_static_palette_texture(obj, tlut);": 1,
}


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    if text.count(old) != 1:
        raise RuntimeError(f"{label}: expected one pinned fragment, found {text.count(old)}")
    return text.replace(old, new, 1)


def embed(text: str) -> str:
    names = NAMES.read_text(encoding="utf-8")
    if not names.endswith("\n"):
        names += "\n"
    block = BEGIN + names + END
    if BEGIN in text:
        start = text.index(BEGIN)
        end = text.find(END, start)
        if end < 0:
            raise RuntimeError("texture_replacement.cpp: naming block has no END marker")
        return text[:start] + block + text[end + len(END):]
    if text.count(EMBED_ANCHOR) != 1:
        raise RuntimeError("texture_replacement.cpp: namespace anchor drifted")
    return text.replace(EMBED_ANCHOR, "\n" + block + EMBED_ANCHOR, 1)


def transform(root: Path, check_only: bool = False) -> str:
    gx = (root / GX_TEXTURE).read_text(encoding="utf-8")
    for fragment, count in GX_REQUIRED.items():
        if gx.count(fragment) != count:
            raise RuntimeError(f"lib/gx/texture.cpp: replacement lookup drifted ({fragment[:60]}...)")
    original_cpp = (root / REPLACEMENT).read_text(encoding="utf-8")
    original_hpp = (root / HEADER).read_text(encoding="utf-8")
    cpp = embed(original_cpp)
    cpp = replace_once(cpp, BUDGET_OLD, BUDGET_NEW, "replacement cache budget")
    cpp = replace_once(cpp, GLOBALS_OLD, GLOBALS_NEW, "replacement globals")
    cpp = replace_once(cpp, HIT_OLD, HIT_NEW, "replacement hit")
    cpp = replace_once(cpp, DUMP_OLD, DUMP_NEW, "texture dump")
    cpp = replace_once(cpp, LOAD_OLD, LOAD_NEW, "replacement directory dump root")
    cpp = replace_once(cpp, API_ANCHOR, API_CODE + "\n" + API_ANCHOR, "OpenSPM stats API")
    hpp = replace_once(original_hpp, DECL_ANCHOR, DECL_NEW, "aurora/texture.hpp declarations")
    if (cpp.count("void openspm_set_texture_dump_directory(") != 1 or cpp.count(BEGIN) != 1
            or hpp.count("struct OpenSpmReplacementStats {") != 1):
        raise RuntimeError("texture pack adaptation is not unique after patching")
    if cpp == original_cpp and hpp == original_hpp:
        return "already active"
    if check_only:
        return "pending"
    if cpp != original_cpp:
        (root / REPLACEMENT).write_text(cpp, encoding="utf-8")
    if hpp != original_hpp:
        (root / HEADER).write_text(hpp, encoding="utf-8")
    return "applied"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    print("Aurora OpenSPM texture packs " + transform(args.source, args.check_only))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
