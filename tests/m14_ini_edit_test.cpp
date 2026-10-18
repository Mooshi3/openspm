#include "../renderer/aurora/openspm_ini.hpp"
#include "../backend/native/openspm_pad_xinput.h" // the backend's parser, to cross-check the menu's
#include "openspm/cheats.h" // Gecko parser for the [Cheats] round trip (needs -Iinclude)
#include "openspm/keymap.h" // [Keyboard] map: the menu's editor uses the backend's parser from C++

#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>

static openspm_pad_map round_trip_unused{}; // keeps openspm_pad_translate referenced
static openspm_ini::cheats::Entry round_entry;

int main() {
    const std::string ini = "; top comment\r\n[OpenSPM]\r\ngame_root=C:\\SPM\r\n\r\n[Display]\r\n"
                            "; windowed | borderless\r\nwindow_mode =\r\naspect = 4:3\r\n\r\n[Effects]\r\ncopy_tags =\r\n";
    const auto a = openspm_ini::set(ini, "Display", "window_mode", "exclusive");
    assert(a.find("window_mode = exclusive\r\n") != std::string::npos);
    assert(a.find("; windowed | borderless") != std::string::npos && a.find("game_root=C:") != std::string::npos);
    const auto b = openspm_ini::set(a, "display", "Refresh_hz", "59.978");   // new key, same section
    assert(b.find("Refresh_hz = 59.978\r\n\r\n[Effects]") != std::string::npos);
    const auto c = openspm_ini::set(b, "Overlay", "show_fps", "1");          // new section
    assert(c.find("[Overlay]\r\nshow_fps = 1\r\n") != std::string::npos);
    assert(openspm_ini::set("", "Display", "aspect", "stretch") == "[Display]\r\naspect = stretch\r\n");
    const auto e = openspm_ini::set(c, "Display", "aspect", "stretch");
    assert(e.find("aspect = stretch") != std::string::npos && e.find("aspect = 4:3") == std::string::npos);
    const auto f = openspm_ini::set("[Display]\n;aspect = stretch\n", "Display", "aspect", "4:3");  // comment kept
    assert(f.find(";aspect = stretch") != std::string::npos && f.find("aspect = 4:3") != std::string::npos);

    // get(): the value the launcher would apply (trimmed, inline comment dropped).
    const std::string g = "\xEF\xBB\xBF[Display]\r\n; aspect = stretch\r\nAspect = Stretch ; wide\r\nwindow_mode=\r\n"
                          "[Mods]\r\nenabled = 1\r\nfoo.dll = 0\r\n";
    assert(openspm_ini::get(g, "display", "aspect") == "Stretch");
    assert(openspm_ini::get(g, "Display", "window_mode").empty() && openspm_ini::get(g, "Display", "refresh_hz").empty());
    assert(openspm_ini::get(g, "Mods", "FOO.DLL") == "0" && openspm_ini::get(g, "Effects", "enabled").empty());
    const auto h = openspm_ini::set(g, "Display", "aspect", "4:3"); // BOM file: edits in place, keeps the BOM
    assert(openspm_ini::get(h, "Display", "aspect") == "4:3" && h.rfind("\xEF\xBB\xBF[Display]", 0) == 0);
    assert(h.find("[Display]", 4) == std::string::npos);

    // Gamepad map: the menu's parse/format agrees with the backend's parser.
    namespace pad = openspm_ini::pad;
    static_assert(pad::kSlotCount == OPENSPM_PAD_SLOT_COUNT);
    const std::uint32_t bits[] = {OPENSPM_PAD_WPAD_1, OPENSPM_PAD_WPAD_2, OPENSPM_PAD_WPAD_A, OPENSPM_PAD_WPAD_B,
                                  OPENSPM_PAD_WPAD_PLUS, OPENSPM_PAD_WPAD_MINUS, OPENSPM_PAD_WPAD_TURBO,
                                  OPENSPM_PAD_WPAD_SHAKE, 0u};
    const char *maps[] = {"", "a=2,b=B,x=1,y=A,start=PLUS,back=MINUS", " a = 1 , rt=2,lb=minus, x=none, bogus=1, y=Q",
                          "start=+,back=-,ls=A,rs=b,lt=Plus", "a=,=2,,b", "rs=none,lb=Turbo", "rb=none,lt=shake"};
    for (const char *text : maps) {
        openspm_pad_map backend;
        (void)openspm_pad_parse_map(text, &backend);
        const pad::Map menu = pad::parse(text);
        for (int slot = 0; slot < pad::kSlotCount; ++slot) {
            assert(openspm_pad_slot_names[slot] == std::string(pad::kSlots[slot]));
            assert(backend.bits[slot] == bits[menu[slot]]);
        }
        openspm_pad_map round_trip; // what the backend reads back from a map the menu saved
        assert(openspm_pad_parse_map(pad::format(menu).c_str(), &round_trip) == 0);
        for (int slot = 0; slot < pad::kSlotCount; ++slot) assert(round_trip.bits[slot] == backend.bits[slot]);
    }
    assert(pad::parse("") == pad::default_map());
    assert(pad::format(pad::default_map()).size() < 200u); // native_settings.h free-form limit
    assert(openspm_pad_translate(&round_trip_unused, 0, 0, 0, 0, 0) == 0u);

    // [Keyboard] map: what the menu saves through openspm_ini::set reads back
    // unchanged with openspm_ini::get (no ; or # in key names) and parses.
    {
        openspm_keymap keys, back;
        char text[512];
        (void)openspm_keymap_parse("up=Semicolon/Apostrophe,a=Slash/Backslash,home=Tab", &keys);
        (void)openspm_keymap_format(&keys, text, sizeof text);
        const std::string saved = openspm_ini::get(openspm_ini::set("", "Keyboard", "map", text), "Keyboard", "map");
        assert(saved == text && openspm_keymap_parse(saved.c_str(), &back) == 0);
        assert(std::memcmp(&keys, &back, sizeof keys) == 0);
    }

    // [Cheats]: off unless 1; custom entries round-trip and parse as Gecko codes.
    namespace ch = openspm_ini::cheats;
    const auto none = ch::load("[Display]\r\naspect = 4:3\r\n");
    assert(!none.infinite_hp && none.custom.empty());
    std::string cheats_ini = openspm_ini::set("", "Cheats", "infinite_hp", "1");
    cheats_ini = openspm_ini::set(cheats_ini, "Cheats", "max_coins", "1"); // retired key: ignored
    const ch::Entry coins{true, "99 | coins; # now", "044d0444 000003E7\n044D0448\t00000001"};
    cheats_ini = openspm_ini::set(cheats_ini, "Cheats", ch::key(0), ch::format_entry(coins));
    cheats_ini = openspm_ini::set(cheats_ini, "Cheats", ch::key(1), "garbage");
    cheats_ini = openspm_ini::set(cheats_ini, "Cheats", ch::key(2), "0 | Off | ");
    cheats_ini = openspm_ini::set(cheats_ini, "Cheats", ch::key(3), ""); // removed slot
    const auto loaded = ch::load(cheats_ini);
    assert(loaded.infinite_hp && loaded.custom.size() == 2u);
    assert(loaded.custom[0].enabled && loaded.custom[0].name == "99  coins  now");
    assert(loaded.custom[0].codes == "044d0444 000003E7 044D0448 00000001");
    assert(ch::join_words(loaded.custom[0].codes, true) == "044d0444 000003E7\n044D0448 00000001");
    assert(!loaded.custom[1].enabled && loaded.custom[1].name == "Off" && loaded.custom[1].codes.empty());
    assert(ch::parse_entry(ch::format_entry(loaded.custom[0]), round_entry) && round_entry == loaded.custom[0]);
    openspm_cheat_write writes[4];
    std::uint32_t count = 0;
    assert(openspm_cheat_parse_gecko(loaded.custom[0].codes.c_str(), writes, 4u, &count, nullptr, 0u) && count == 2u);
    assert(writes[0].address == 0x804D0444u && writes[0].value == 999u && writes[1].address == 0x804D0448u);
    std::puts("PASS: OpenSPM.ini editor");
    return 0;
}
