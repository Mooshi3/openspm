// OpenSPM.ini editing shared by the OpenSPM menu (renderer). Dependency-free
// so it is host unit-tested (tests/m14_ini_edit_test.cpp).
#pragma once

#include <array>
#include <sstream>
#include <string>
#include <vector>

namespace openspm_ini {

namespace detail {
inline std::string lower(std::string s) {
    for (auto &c : s) c = static_cast<char>(c >= 'A' && c <= 'Z' ? c - 'A' + 'a' : c);
    return s;
}
inline std::string trim(const std::string &s) {
    const auto b = s.find_first_not_of(" \t\r");
    const auto e = s.find_last_not_of(" \t\r");
    return b == std::string::npos ? std::string{} : s.substr(b, e - b + 1);
}
} // namespace detail

// Value of `key` in `[section]` (case-insensitive), trimmed and without an
// inline ; or # comment, parsed like launcher/native_settings.h. Empty when the
// key is missing or empty (both mean "default").
inline std::string get(const std::string &text, const std::string &section, const std::string &key) {
    const std::string want_section = detail::lower(section), want_key = detail::lower(key);
    std::istringstream in(text);
    std::string line, current;
    while (std::getline(in, line)) {
        if (current.empty() && line.size() >= 3 && static_cast<unsigned char>(line[0]) == 0xEF &&
            static_cast<unsigned char>(line[1]) == 0xBB && static_cast<unsigned char>(line[2]) == 0xBF)
            line.erase(0, 3); // UTF-8 BOM (Notepad)
        const std::string t = detail::trim(line);
        if (t.empty() || t.front() == ';' || t.front() == '#') continue;
        if (t.front() == '[') {
            const auto close = t.find(']');
            current = detail::lower(detail::trim(t.substr(1, close == std::string::npos ? std::string::npos : close - 1)));
            continue;
        }
        const auto eq = t.find('=');
        if (current != want_section || eq == std::string::npos || detail::lower(detail::trim(t.substr(0, eq))) != want_key)
            continue;
        std::string value = t.substr(eq + 1);
        const auto comment = value.find_first_of(";#");
        if (comment != std::string::npos) value.erase(comment);
        return detail::trim(value);
    }
    return {};
}

// Update `key` in `[section]` of an INI text, keeping every other line and
// comment; adds the key or the section when missing. Case-insensitive names.
inline std::string set(const std::string &text, const std::string &section, const std::string &key,
                           const std::string &value) {
    using detail::lower;
    using detail::trim;
    std::vector<std::string> lines;
    {
        std::istringstream in(text);
        std::string line;
        while (std::getline(in, line)) {
            if (!line.empty() && line.back() == '\r') line.pop_back();
            lines.push_back(line);
        }
    }
    const std::string want_section = lower(section), want_key = lower(key);
    std::string current;
    int section_end = -1; // index after the last line of the wanted section
    for (size_t i = 0; i < lines.size(); ++i) {
        std::string t = trim(lines[i]);
        if (i == 0 && t.size() >= 3 && static_cast<unsigned char>(t[0]) == 0xEF &&
            static_cast<unsigned char>(t[1]) == 0xBB && static_cast<unsigned char>(t[2]) == 0xBF)
            t = trim(t.substr(3)); // UTF-8 BOM (Notepad): kept in the output, ignored here
        if (!t.empty() && t.front() == '[') {
            const auto close = t.find(']');
            current = lower(trim(t.substr(1, close == std::string::npos ? std::string::npos : close - 1)));
            if (current == want_section) section_end = static_cast<int>(i) + 1;
            continue;
        }
        if (current != want_section) continue;
        if (!t.empty() && t.front() != ';' && t.front() != '#') section_end = static_cast<int>(i) + 1;
        const auto eq = t.find('=');
        if (eq != std::string::npos && lower(trim(t.substr(0, eq))) == want_key && t.front() != ';' && t.front() != '#') {
            lines[i] = key + " = " + value;
            section_end = -2;
            break;
        }
    }
    if (section_end == -1) {
        if (!lines.empty() && !trim(lines.back()).empty()) lines.emplace_back();
        lines.push_back("[" + section + "]");
        lines.push_back(key + " = " + value);
    } else if (section_end >= 0) {
        lines.insert(lines.begin() + section_end, key + " = " + value);
    }
    std::string out;
    for (const auto &line : lines) out += line + "\r\n";
    return out;
}

// [Controller] map: "name=value,..." gamepad buttons -> sideways Wii Remote.
// Mirrors openspm_pad_parse_map in backend/native/openspm_pad_xinput.h (the
// backend owns the real parse; tests/m14_ini_edit_test.cpp cross-checks both).
namespace pad {
inline constexpr int kSlotCount = 12;
inline constexpr std::array<const char *, kSlotCount> kSlots = {"a", "b", "x", "y", "start", "back",
                                                                "lb", "rb", "lt", "rt", "ls", "rs"};
inline constexpr std::array<const char *, 9> kValues = {"1",     "2",     "A",     "B",   "PLUS",
                                                        "MINUS", "TURBO", "SHAKE", "NONE"};
inline constexpr int kTurbo = 6; // fast-forward hotkey (never a game button)
inline constexpr int kShake = 7; // the Wii Remote shake (never a game button)
inline constexpr int kNone = 8;
using Map = std::array<int, kSlotCount>; // index into kValues per slot

inline Map default_map() { return {1, 3, 0, 2, 4, 5, kNone, kShake, kNone, kNone, kNone, kTurbo}; }

// Entries parse over the defaults; unknown names or values are ignored.
inline Map parse(const std::string &text) {
    Map map = default_map();
    std::istringstream in(text);
    std::string entry;
    while (std::getline(in, entry, ',')) {
        const auto eq = entry.find('=');
        if (eq == std::string::npos) continue;
        const std::string name = detail::lower(detail::trim(entry.substr(0, eq)));
        std::string value = detail::lower(detail::trim(entry.substr(eq + 1)));
        if (value == "+") value = "plus";
        if (value == "-") value = "minus";
        for (int slot = 0; slot < kSlotCount; ++slot) {
            if (name != kSlots[slot]) continue;
            for (int v = 0; v < static_cast<int>(kValues.size()); ++v)
                if (value == detail::lower(kValues[v])) map[slot] = v;
            break;
        }
    }
    return map;
}

// Every slot, explicitly (the file then reads the same whatever the defaults).
inline std::string format(const Map &map) {
    std::string out;
    for (int slot = 0; slot < kSlotCount; ++slot) {
        const int v = map[slot] >= 0 && map[slot] < static_cast<int>(kValues.size()) ? map[slot] : kNone;
        out += std::string(slot == 0 ? "" : ",") + kSlots[slot] + "=" + kValues[v];
    }
    return out;
}
} // namespace pad

// [Cheats]: infinite_hp = 0|1 (max_coins, retired, is ignored), and custom Gecko codes as
//   code1 = <0|1> | <name> | <hex words, space separated>
// up to code16. Everything is off when missing. Names drop the characters the
// INI format owns (| ; # and line breaks); code words stay free-form here and
// are validated by openspm_cheat_parse_gecko (include/openspm/cheats.h).
namespace cheats {
inline constexpr int kMaxCustom = 16;

struct Entry {
    bool enabled = false;
    std::string name;
    std::string codes; // words separated by whitespace (the menu shows one code per line)
    bool operator==(const Entry &) const = default;
};

inline std::string key(int index) { return "code" + std::to_string(index + 1); }

inline std::string clean_name(const std::string &name) {
    std::string out;
    for (char c : name)
        if (c != '|' && c != ';' && c != '#' && c != '\r' && c != '\n' && c != '\t') out += c;
    return detail::trim(out);
}

// Words joined by single spaces (what is stored), or two per line (the menu).
inline std::string join_words(const std::string &codes, bool pairs_per_line) {
    std::istringstream in(codes);
    std::string word, out;
    int n = 0;
    while (in >> word) {
        if (n > 0) out += pairs_per_line && n % 2 == 0 ? "\n" : " ";
        out += word;
        ++n;
    }
    return out;
}

// False for an empty or malformed value (the slot is then unused).
inline bool parse_entry(const std::string &value, Entry &out) {
    const auto a = value.find('|');
    if (a == std::string::npos) return false;
    const auto b = value.find('|', a + 1);
    if (b == std::string::npos) return false;
    const std::string flag = detail::trim(value.substr(0, a));
    if (flag != "0" && flag != "1") return false;
    out.enabled = flag == "1";
    out.name = clean_name(value.substr(a + 1, b - a - 1));
    out.codes = join_words(value.substr(b + 1), false);
    return true;
}

inline std::string format_entry(const Entry &entry) {
    return std::string(entry.enabled ? "1" : "0") + " | " + clean_name(entry.name) + " | " +
           join_words(entry.codes, false);
}

struct Settings {
    bool infinite_hp = false;
    std::vector<Entry> custom;
};

inline Settings load(const std::string &text) {
    Settings s;
    s.infinite_hp = get(text, "Cheats", "infinite_hp") == "1";
    for (int i = 0; i < kMaxCustom; ++i) {
        Entry entry;
        if (parse_entry(get(text, "Cheats", key(i)), entry)) s.custom.push_back(entry);
    }
    return s;
}
} // namespace cheats

} // namespace openspm_ini
