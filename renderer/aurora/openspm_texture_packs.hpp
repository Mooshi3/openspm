// OpenSPM texture packs: Dolphin HD texture packs ("Load Custom Textures").
//
// Folder: [TexturePacks] folder (OPENSPM_TEXTURE_PACK_DIR), default
// texturepacks\ next to OpenSPMNative.exe. Every subfolder is one pack. Inside a
// pack, Dolphin's layouts work as they are: loose tex1_*.png/.dds files in any
// subfolders, or a Load\Textures\R8PE01\ (or R8P\) tree - when a pack contains
// folders named after the game ID (or Dolphin's R8PE01.txt / R8P.txt / all.txt
// marker files), only those folders are indexed, so a whole Dolphin Load folder
// can be dropped in. docs/TEXTURE-PACKS.md is the player guide.
//
// Startup indexes the enabled packs once (file names only; Aurora decodes a
// file on a worker thread the first time the game uses that texture and keeps
// decoded textures in a GPU cache of [TexturePacks] cache_mb, least recently
// used evicted). Packs earlier in [TexturePacks] order win when two replace the
// same texture. No pack folder, no packs, or enabled=0: nothing is registered
// and Aurora's texture path skips the Dolphin hash entirely.
//
// Aurora's replacement registry is thread safe (its GX cache clear is an atomic
// request), so the F1 menu can re-apply the pack list while the game runs.
//
// Included once by openspm_overlay.hpp (after the renderer base, so it can use
// log_message); openspm_renderer_aurora_dev7_base.cpp calls startup() right
// after aurora_initialize.
#pragma once

#include <dolphin/gx/GXStruct.h> // GXTexObj / GXTlutObj, used by aurora/texture.hpp
#include <aurora/texture.hpp>

#include "openspm_ini.hpp"
#include "openspm_texture_pack_names.hpp"

#include <SDL3/SDL_filesystem.h>

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <sstream>
#include <string>
#include <system_error>
#include <vector>

namespace openspm_texpacks {

inline constexpr const char *kGameId = "R8PE01";
inline constexpr const char *kSection = "TexturePacks";

struct Pack {
    std::string name;                         // folder name, UTF-8
    std::filesystem::path dir;
    std::vector<std::filesystem::path> roots; // what gets indexed (game-ID folders or the pack itself)
    bool enabled = true;                      // [TexturePacks] pack.<name> (default on)
    long long files = -1;                     // tex1_ base files, counted by the menu (-1 = not yet)
    std::size_t indexed = 0;                  // registered in the running session
    bool loaded = false;
    aurora::texture::ReplacementGroup group;
};

struct State {
    bool started = false;
    bool active = false;   // packs registered in this session ([TexturePacks] enabled)
    bool dumping = false;  // [TexturePacks] dump=1 at startup
    std::filesystem::path folder;
    std::filesystem::path dump_folder;
    std::vector<Pack> packs; // priority order (first wins)
    std::size_t registered = 0;
    std::size_t loose_files = 0; // tex1_ files directly in the folder (not in a pack subfolder)
    std::string applied_signature; // enabled + order + pack.* as applied
};

inline State &state() {
    static State s;
    return s;
}

// ---- paths and settings ------------------------------------------------------

inline std::string utf8(const std::filesystem::path &path) {
    const std::u8string text = path.u8string();
    return std::string(text.begin(), text.end());
}
inline std::filesystem::path from_utf8(const std::string &text) {
    return std::filesystem::path(std::u8string(text.begin(), text.end()));
}

inline const std::filesystem::path &exe_dir() {
    static const std::filesystem::path dir = [] {
        const char *base = SDL_GetBasePath(); // UTF-8, owned by SDL
        return base != nullptr ? from_utf8(base) : std::filesystem::path{};
    }();
    return dir;
}

inline std::string env(const char *name) {
    const char *value = std::getenv(name);
    return value != nullptr ? value : "";
}

// [TexturePacks] folder -> OPENSPM_TEXTURE_PACK_DIR; relative to the exe folder.
inline std::filesystem::path folder_from_setting(const std::string &value) {
    if (value.empty()) return exe_dir() / "texturepacks";
    std::filesystem::path path = from_utf8(value);
    return path.is_absolute() ? path : exe_dir() / path;
}

inline std::filesystem::path default_dump_folder() { return exe_dir() / "texturedumps" / kGameId; }

// The settings file the launcher applied (same rule as openspm_overlay::settings()).
inline std::filesystem::path settings_path() {
    const std::string value = env("OPENSPM_SETTINGS");
    return !value.empty() ? std::filesystem::path(value) : exe_dir() / "OpenSPM.ini";
}

inline std::string read_text(const std::filesystem::path &path) {
    std::ifstream in(path, std::ios::binary);
    if (!in) return {};
    std::ostringstream buffer;
    buffer << in.rdbuf();
    return buffer.str();
}

// INI keys cannot carry these; such a pack loads (default on) but has no toggle.
inline bool name_storable(const std::string &name) {
    return !name.empty() && name.find_first_of("=;#[]|\r\n") == std::string::npos && name.front() != ' ' &&
           name.back() != ' ';
}
inline std::string pack_key(const std::string &name) { return "pack." + name; }

inline bool master_enabled(const std::string &ini) {
    const std::string value = openspm_ini::get(ini, kSection, "enabled");
    return value.empty() || value[0] != '0';
}

inline bool pack_enabled(const std::string &ini, const std::string &name) {
    return !name_storable(name) || openspm_ini::get(ini, kSection, pack_key(name)) != "0";
}

inline std::vector<std::string> order_list(const std::string &ini) {
    std::vector<std::string> out;
    std::istringstream in(openspm_ini::get(ini, kSection, "order"));
    std::string item;
    while (std::getline(in, item, '|')) {
        const auto b = item.find_first_not_of(" \t");
        const auto e = item.find_last_not_of(" \t");
        if (b != std::string::npos) out.push_back(item.substr(b, e - b + 1));
    }
    return out;
}

inline std::string order_string(const std::vector<Pack> &packs) {
    std::string out;
    for (const Pack &pack : packs) {
        if (!name_storable(pack.name)) continue;
        out += (out.empty() ? "" : "|") + pack.name;
    }
    return out;
}

inline std::string lower(std::string s) {
    for (char &c : s) c = static_cast<char>(c >= 'A' && c <= 'Z' ? c - 'A' + 'a' : c);
    return s;
}

// ---- discovery ------------------------------------------------------------------

// Dolphin's GetTextureDirectoriesWithGameId inside one pack: folders named
// R8PE01 / R8P, or holding an R8PE01.txt / R8P.txt / all.txt marker. Without
// any, the whole pack is indexed.
inline std::vector<std::filesystem::path> game_roots(const std::filesystem::path &dir) {
    const std::string id = lower(kGameId), region_free = id.substr(0, 3);
    std::vector<std::filesystem::path> roots;
    std::error_code ec;
    auto it = std::filesystem::recursive_directory_iterator(
        dir, std::filesystem::directory_options::skip_permission_denied, ec);
    for (const auto end = std::filesystem::recursive_directory_iterator(); !ec && it != end; it.increment(ec)) {
        if (it.depth() > 4) {
            it.disable_recursion_pending();
            continue;
        }
        const std::string name = lower(utf8(it->path().filename()));
        std::error_code type_ec;
        if (it->is_directory(type_ec)) {
            if (name == id || name == region_free) {
                roots.push_back(it->path());
                it.disable_recursion_pending(); // everything below belongs to this root
            }
        } else if (name == id + ".txt" || name == region_free + ".txt" || name == "all.txt") {
            roots.push_back(it->path().parent_path());
        }
    }
    std::sort(roots.begin(), roots.end());
    roots.erase(std::unique(roots.begin(), roots.end()), roots.end());
    // Drop roots nested in another root (Aurora walks each root recursively).
    std::vector<std::filesystem::path> kept;
    for (const auto &root : roots) {
        const bool nested = std::any_of(kept.begin(), kept.end(), [&](const std::filesystem::path &outer) {
            const auto rel = root.lexically_relative(outer);
            return !rel.empty() && rel.native()[0] != '.';
        });
        if (!nested) kept.push_back(root);
    }
    if (kept.empty()) kept.push_back(dir);
    return kept;
}

// Subfolders of the pack folder, in [TexturePacks] order, then by name.
inline std::vector<Pack> discover(const std::filesystem::path &folder, const std::string &ini,
                                  std::size_t *loose_files = nullptr) {
    std::vector<Pack> packs;
    std::size_t loose = 0;
    std::error_code ec;
    for (std::filesystem::directory_iterator it(folder, ec), end; !ec && it != end; it.increment(ec)) {
        std::error_code type_ec;
        const std::string name = utf8(it->path().filename());
        if (it->is_directory(type_ec)) {
            if (name.empty() || name[0] == '.') continue;
            Pack pack;
            pack.name = name;
            pack.dir = it->path();
            pack.enabled = pack_enabled(ini, name);
            packs.push_back(std::move(pack));
        } else if (openspm_texpack::parse(name)) {
            ++loose;
        }
    }
    const std::vector<std::string> order = order_list(ini);
    auto rank = [&](const Pack &pack) {
        const auto found = std::find(order.begin(), order.end(), pack.name);
        return found == order.end() ? order.size() : static_cast<std::size_t>(found - order.begin());
    };
    std::sort(packs.begin(), packs.end(), [&](const Pack &a, const Pack &b) {
        const std::size_t ra = rank(a), rb = rank(b);
        if (ra != rb) return ra < rb;
        const std::string la = lower(a.name), lb = lower(b.name);
        return la != lb ? la < lb : a.name < b.name;
    });
    if (loose_files != nullptr) *loose_files = loose;
    return packs;
}

// tex1_ base files (not _mip sidecars) under a pack's roots.
inline long long count_files(const Pack &pack) {
    long long count = 0;
    for (const auto &root : pack.roots.empty() ? game_roots(pack.dir) : pack.roots) {
        std::error_code ec;
        auto it = std::filesystem::recursive_directory_iterator(
            root, std::filesystem::directory_options::skip_permission_denied, ec);
        for (const auto end = std::filesystem::recursive_directory_iterator(); !ec && it != end; it.increment(ec)) {
            std::error_code type_ec;
            if (!it->is_regular_file(type_ec)) continue;
            const auto parsed = openspm_texpack::parse(utf8(it->path().filename()));
            if (parsed && parsed->mip_level == 0) ++count;
        }
    }
    return count;
}

// ---- registration ---------------------------------------------------------------

inline std::string signature(const std::string &ini, const std::vector<Pack> &packs) {
    std::string out = master_enabled(ini) ? "on" : "off";
    for (const Pack &pack : packs) out += "|" + pack.name + (pack.enabled ? "=1" : "=0");
    return out;
}

inline void unload_all() {
    State &s = state();
    for (Pack &pack : s.packs) {
        if (pack.loaded) aurora::texture::unregister_replacements(pack.group);
        pack.group = {};
        pack.loaded = false;
        pack.indexed = 0;
    }
    s.registered = 0;
    s.active = false;
}

// Registers the enabled packs of `ini` (priority = list order) and replaces
// whatever this session registered before. Safe while the game runs.
inline void apply(const std::string &ini) {
    State &s = state();
    unload_all();
    std::vector<Pack> packs = discover(s.folder, ini, &s.loose_files);
    // Keep file counts the menu already made for packs that are still there.
    for (Pack &pack : packs) {
        for (const Pack &old : s.packs)
            if (old.name == pack.name && old.dir == pack.dir) pack.files = old.files;
    }
    s.packs = std::move(packs);
    s.applied_signature = signature(ini, s.packs);
    if (!master_enabled(ini)) {
        log_message(OPENSPM_LOG_INFO, "OpenSPM texture packs: off ([TexturePacks] enabled=0).");
        return;
    }
    s.active = true;
    const int count = static_cast<int>(s.packs.size());
    char line[768];
    for (int i = 0; i < count; ++i) {
        Pack &pack = s.packs[static_cast<std::size_t>(i)];
        pack.roots = game_roots(pack.dir);
        if (!pack.enabled) continue;
        aurora::texture::ReplacementOptions options;
        options.priority = count - i; // earlier in the list wins
        for (const auto &root : pack.roots) {
            aurora::texture::ReplacementGroup group = aurora::texture::load_replacement_directory(root, options);
            pack.group.registrations.insert(pack.group.registrations.end(), group.registrations.begin(),
                                            group.registrations.end());
        }
        pack.loaded = true;
        pack.indexed = pack.group.registrations.size();
        s.registered += pack.indexed;
        std::snprintf(line, sizeof line, "OpenSPM texture pack %d \"%s\": %zu textures indexed from %s%s", i + 1,
                      pack.name.c_str(), pack.indexed, utf8(pack.roots.front()).c_str(),
                      pack.roots.size() > 1 ? " (and more game-ID folders)" : "");
        log_message(OPENSPM_LOG_INFO, line);
    }
    std::snprintf(line, sizeof line, "OpenSPM texture packs: %zu textures from %d pack(s) in %s", s.registered,
                  count, utf8(s.folder).c_str());
    log_message(OPENSPM_LOG_INFO, line);
    if (s.loose_files != 0) {
        std::snprintf(line, sizeof line,
                      "OpenSPM texture packs: %zu tex1_ file(s) directly in %s are ignored; put each pack in its own "
                      "subfolder (docs/TEXTURE-PACKS.md).",
                      s.loose_files, utf8(s.folder).c_str());
        log_message(OPENSPM_LOG_WARN, line);
    }
}

// Once, right after aurora_initialize (before the first frame).
inline void startup_impl() {
    State &s = state();
    s.folder = folder_from_setting(env("OPENSPM_TEXTURE_PACK_DIR"));
    s.dump_folder = default_dump_folder();
    s.dumping = env("OPENSPM_TEXTURE_DUMP") == "1";
    aurora::texture::openspm_set_texture_dump_directory(s.dump_folder);
    if (s.dumping) {
        std::error_code ec;
        std::filesystem::create_directories(s.dump_folder, ec);
        log_message(OPENSPM_LOG_INFO, ("OpenSPM texture dump on: unreplaced textures go to " + utf8(s.dump_folder) +
                                       " as Dolphin tex1_ PNGs").c_str());
    }
    const std::string cache = env("OPENSPM_TEXTURE_PACK_CACHE_MB");
    unsigned long mb = cache.empty() ? 2048ul : std::strtoul(cache.c_str(), nullptr, 10);
    if (mb < 128ul || mb > 65536ul) mb = 2048ul;
    aurora::texture::openspm_set_replacement_cache_budget(static_cast<std::uint64_t>(mb) << 20);
    // The master switch comes from the environment (launcher / OpenSPM.ini);
    // pack toggles and order are read from the settings file itself.
    std::string ini = read_text(settings_path());
    const std::string master = env("OPENSPM_TEXTURE_PACKS");
    if (!master.empty()) ini = openspm_ini::set(ini, kSection, "enabled", master[0] == '0' ? "0" : "1");
    std::error_code ec;
    if (!std::filesystem::is_directory(s.folder, ec)) {
        s.applied_signature = signature(ini, s.packs);
        s.active = master_enabled(ini);
        log_message(OPENSPM_LOG_INFO, ("OpenSPM texture packs: no folder " + utf8(s.folder) +
                                       " (create it and add packs; docs/TEXTURE-PACKS.md).").c_str());
        return;
    }
    apply(ini);
}

// Path conversions (e.g. a non-UTF-8 [TexturePacks] folder) and file walks may
// throw; none of that may take the game down at startup.
void startup() noexcept {
    State &s = state();
    if (s.started) return;
    s.started = true;
    try {
        startup_impl();
    } catch (...) {
        log_message(OPENSPM_LOG_WARN, "OpenSPM texture packs: indexing failed; continuing without packs.");
    }
}

} // namespace openspm_texpacks
