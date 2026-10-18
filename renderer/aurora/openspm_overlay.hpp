// OpenSPM menu: an ImGui overlay drawn on top of the presented frame.
//
// F1 toggles it (Esc also closes it). It is the seed of the OpenSPM menu that
// will replace the HOME menu. Pages: Display (window, aspect, widescreen, frame rate),
// Graphics (effect fixes), Texture Packs (Dolphin HD packs), Controls (keyboard +
// gamepad), Debug, Cheats, Mods.
// Every OpenSPM.ini setting (launcher/native_settings.h) is editable here;
// changes are written to OpenSPM.ini at once through openspm_ini.hpp. Settings
// read only at startup are marked "applies on restart"; aspect, the FPS
// counter, windowed/borderless and the window size apply live.
//
// It replaces the Wii HOME menu: HOME (gamepad Guide, [Keyboard] home key,
// default Home) opens and closes it too ([Controller] home_opens_menu), and
// HOME never reaches the game. While it is open the game is paused at the frame
// safepoint ([Overlay] pause_game, default on; include/openspm/menu_control.h)
// and receives no buttons and no pointer motion either way.
//
// Input: the menu is mouse-driven; Aurora enables no ImGui keyboard
// navigation. The Controls tab rebinds the keyboard ([Keyboard] map, live):
// "Set" captures the next key press. The menu adds no hotkeys besides Esc (not
// bindable). While closed it costs one flag check per frame; an optional FPS
// counter can stay on screen. OPENSPM_OVERLAY=0 disables the F1 key.
//
// Included once by openspm_renderer_aurora_v6.cpp after the dev7 base, so it
// can use log_message, the F8/F12 request flags and the SDL window helpers.
#pragma once

#include <imgui.h>

#include <aurora/gx_guest_memory.hpp>

#include "openspm_ini.hpp"
#include "output_aspect.hpp"
#include "openspm/cheats.h"
#include "openspm/keymap.h"
#include "openspm/menu_control.h"
#include "openspm/mod_manifest.h"
#include "openspm/pointer_input.h"
#include "openspm_texture_packs.hpp"

#include <SDL3/SDL_filesystem.h>
#include <SDL3/SDL_gamepad.h>
#include <SDL3/SDL_misc.h>
#include <SDL3/SDL_video.h>

#include "openspm/overlay_service.h"
#include <algorithm>
#include <array>
#include <map>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <mutex>
#include <sstream>
#include <string>
#include <system_error>
#include <vector>

namespace openspm_overlay {

inline bool g_open = false;
inline int g_pending_policy = -1; // AuroraViewportPolicy to apply after end_frame
inline float g_pending_stretch_aspect = 0.f; // 16:9 fit (widescreen), applied with g_pending_policy
inline bool g_show_fps = [] {
    const char *value = std::getenv("OPENSPM_SHOW_FPS");
    return value != nullptr && value[0] == '1';
}();
inline std::string g_status;
inline std::array<float, 240> g_frame_ms{};
inline unsigned g_frame_count = 0u;
inline std::chrono::steady_clock::time_point g_last_present{};

inline bool enabled() {
    static const bool on = [] {
        const char *value = std::getenv("OPENSPM_OVERLAY");
        return value == nullptr || value[0] != '0';
    }();
    return on;
}

// ---- OpenSPM.ini ---------------------------------------------------------

inline std::string utf8(const std::filesystem::path &path) {
    const std::u8string text = path.u8string();
    return std::string(text.begin(), text.end());
}

// Folder of OpenSPMNative.exe (the launcher's `app`): mods, default settings.
inline const std::filesystem::path &app_dir() {
    static const std::filesystem::path dir = [] {
        const char *base = SDL_GetBasePath(); // UTF-8, owned by SDL
        return base != nullptr ? std::filesystem::path(std::u8string(reinterpret_cast<const char8_t *>(base)))
                               : std::filesystem::path{};
    }();
    return dir;
}

struct IniFile {
    std::filesystem::path path;
    std::string text; // as last read or written
};

inline std::string read_text(const std::filesystem::path &path) {
    std::ifstream in(path, std::ios::binary);
    if (!in) return {};
    std::ostringstream buffer;
    buffer << in.rdbuf();
    return buffer.str();
}

// The file the launcher applied: OPENSPM_SETTINGS (M14-VANILLA-RUN.cmd sets it
// next to the .cmd files), otherwise OpenSPM.ini next to the exe.
inline IniFile &settings() {
    static IniFile file = [] {
        const char *env = std::getenv("OPENSPM_SETTINGS");
        IniFile f;
        f.path = env != nullptr && *env != '\0' ? std::filesystem::path(env) : app_dir() / "OpenSPM.ini";
        return f;
    }();
    return file;
}

// Per-mod toggles ([Mods] <file>.dll) live in the same settings file; the
// native launcher reads them there (launcher/native_main.c).
inline IniFile &mod_settings() { return settings(); }

inline void reload_settings() {
    settings().text = read_text(settings().path);
}

inline bool save_to(IniFile &file, const char *section, const char *key, const std::string &value) {
    const std::string updated = openspm_ini::set(read_text(file.path), section, key, value);
    bool ok = false;
    {
        std::ofstream out(file.path, std::ios::binary | std::ios::trunc);
        if (out) {
            out << updated;
            ok = static_cast<bool>(out);
        }
    }
    reload_settings(); // both files: they may be the same one
    char line[768];
    std::snprintf(line, sizeof line, ok ? "Saved [%s] %s = %s to %s" : "Could not write [%s] %s = %s to %s", section,
                  key, value.c_str(), utf8(file.path).c_str());
    g_status = line;
    log_message(ok ? OPENSPM_LOG_INFO : OPENSPM_LOG_WARN, line);
    return ok;
}

inline bool save_setting(const char *section, const char *key, const std::string &value) {
    return save_to(settings(), section, key, value);
}

inline std::string env_value(const char *name) {
    const char *value = std::getenv(name);
    return value != nullptr ? value : "";
}

inline bool iequal(const std::string &a, const std::string &b) {
    return a.size() == b.size() && ascii_iequals(a.c_str(), b.c_str());
}

// ---- widgets ---------------------------------------------------------------

constexpr ImVec4 kPendingColor{1.0f, 0.78f, 0.3f, 1.0f};

inline void restart_marker(bool pending) {
    ImGui::SameLine();
    if (pending) ImGui::TextColored(kPendingColor, "restart to apply");
    else ImGui::TextDisabled("applies on restart");
    ImGui::SetItemTooltip("Saved to OpenSPM.ini; takes effect the next time OpenSPM starts.\n"
                          "If this still says \"restart to apply\" after a restart, a launcher argument\n"
                          "or a console variable overrides OpenSPM.ini.");
}

inline void live_marker() {
    ImGui::SameLine();
    ImGui::TextDisabled("live");
    ImGui::SetItemTooltip("Applies immediately and is saved to OpenSPM.ini.");
}

struct Choice {
    std::string value; // written to OpenSPM.ini
    std::string label;
};

// A setting read at startup: shows the saved value (or, when the file leaves
// it empty, the running one) and saves every change.
inline void choice_setting(const char *label, const char *section, const char *key, const char *env,
                           const std::vector<Choice> &choices, int fallback, const char *tooltip) {
    auto index_of = [&](std::string value) {
        if (iequal(value, "fullscreen")) value = "exclusive"; // window_mode alias
        for (size_t i = 0; i < choices.size(); ++i)
            if (iequal(value, choices[i].value)) return static_cast<int>(i);
        return fallback;
    };
    const int running = index_of(env_value(env));
    const std::string saved = openspm_ini::get(settings().text, section, key);
    const int selected = saved.empty() ? running : index_of(saved);
    ImGui::SetNextItemWidth(ImGui::GetFontSize() * 14.f);
    if (ImGui::BeginCombo(label, choices[static_cast<size_t>(selected)].label.c_str())) {
        for (size_t i = 0; i < choices.size(); ++i) {
            if (ImGui::Selectable(choices[i].label.c_str(), static_cast<int>(i) == selected) &&
                static_cast<int>(i) != selected)
                save_setting(section, key, choices[i].value);
        }
        ImGui::EndCombo();
    }
    ImGui::SetItemTooltip("%s", tooltip);
    restart_marker(selected != running);
}

// A 1|0 setting read at startup. `default_on` mirrors its consumer: default-on
// switches are off only for "0", default-off switches are on only for "1".
inline void toggle_setting(const char *label, const char *section, const char *key, const char *env,
                           bool default_on, const char *tooltip) {
    auto parse = [&](const std::string &value) {
        if (value.empty()) return default_on;
        return default_on ? value[0] != '0' : value[0] == '1';
    };
    const bool running = parse(env_value(env));
    const std::string saved = openspm_ini::get(settings().text, section, key);
    bool selected = saved.empty() ? running : parse(saved);
    if (ImGui::Checkbox(label, &selected)) save_setting(section, key, selected ? "1" : "0");
    ImGui::SetItemTooltip("%s", tooltip);
    restart_marker(selected != running);
}

// ---- window and frame helpers ----------------------------------------------

inline SDL_Window *main_window() {
    int count = 0;
    SDL_Window **windows = SDL_GetWindows(&count);
    SDL_Window *window = windows != nullptr && count > 0 ? windows[0] : nullptr;
    SDL_free(windows);
    return window;
}

inline double mode_hz(const SDL_DisplayMode &mode) {
    return mode.refresh_rate_numerator > 0 && mode.refresh_rate_denominator > 0
               ? static_cast<double>(mode.refresh_rate_numerator) / static_cast<double>(mode.refresh_rate_denominator)
               : static_cast<double>(mode.refresh_rate);
}

inline bool fits_vanilla_60(const SDL_DisplayMode &mode) {
    std::uint64_t n = 0u, d = 0u;
    if (mode.refresh_rate_numerator > 0 && mode.refresh_rate_denominator > 0) {
        n = static_cast<std::uint64_t>(mode.refresh_rate_numerator);
        d = static_cast<std::uint64_t>(mode.refresh_rate_denominator);
    } else {
        openspm::pacing::rational_from_hz(mode.refresh_rate, n, d);
    }
    return n != 0u && d != 0u && openspm::pacing::choose_vanilla_pacing(n, d).locked;
}

// Exclusive-fullscreen refresh choices: "auto" plus the rates the display
// offers at the desktop resolution (what m14_apply_window_mode picks from).
inline std::vector<Choice> g_refresh_choices;

inline void scan_refresh_rates() {
    g_refresh_choices = {{"auto", "auto (best for 60 fps)"}};
    SDL_Window *window = main_window();
    const SDL_DisplayID display = window != nullptr ? SDL_GetDisplayForWindow(window) : 0;
    const SDL_DisplayMode *desktop = display != 0 ? SDL_GetDesktopDisplayMode(display) : nullptr;
    if (desktop == nullptr) return;
    int count = 0;
    SDL_DisplayMode **modes = SDL_GetFullscreenDisplayModes(display, &count);
    std::vector<double> seen;
    for (int i = 0; modes != nullptr && i < count; ++i) {
        const SDL_DisplayMode *mode = modes[i];
        if (mode == nullptr || mode->w != desktop->w || mode->h != desktop->h) continue;
        const double hz = mode_hz(*mode);
        if (hz < 24.0 || hz > 1000.0) continue;
        if (std::any_of(seen.begin(), seen.end(), [&](double other) { return std::fabs(other - hz) < 0.0005; }))
            continue;
        seen.push_back(hz);
        char value[32], label[64];
        std::snprintf(value, sizeof value, "%.3f", hz);
        std::snprintf(label, sizeof label, "%.3f Hz%s", hz, fits_vanilla_60(*mode) ? " (smooth 60 fps)" : "");
        g_refresh_choices.push_back({value, label});
    }
    SDL_free(modes);
}

// Maps a saved/running refresh value onto a listed choice (numeric match);
// adds an entry for a rate this display does not list.
inline std::vector<Choice> refresh_choices_with(const std::string &saved, const std::string &running) {
    std::vector<Choice> choices = g_refresh_choices;
    for (const std::string &value : {saved, running}) {
        char *end = nullptr;
        const double hz = std::strtod(value.c_str(), &end);
        if (value.empty() || end == value.c_str() || iequal(value, "auto")) continue;
        bool listed = false;
        for (Choice &choice : choices) {
            if (std::fabs(std::strtod(choice.value.c_str(), nullptr) - hz) < 0.0005) {
                choice.value = value; // exact text, so the combo selects it
                listed = true;
            }
        }
        if (!listed) choices.push_back({value, value + " Hz (not offered by this display)"});
    }
    return choices;
}

inline void note_present() {
    const auto now = std::chrono::steady_clock::now();
    if (g_last_present.time_since_epoch().count() != 0) {
        const float ms = std::chrono::duration<float, std::milli>(now - g_last_present).count();
        g_frame_ms[g_frame_count % g_frame_ms.size()] = ms;
        ++g_frame_count;
    }
    g_last_present = now;
}

inline void frame_stats(float &avg_ms, float &max_ms, unsigned samples = 60u) {
    const unsigned n = std::min<unsigned>(samples, std::min<unsigned>(g_frame_count, g_frame_ms.size()));
    avg_ms = max_ms = 0.f;
    for (unsigned i = 0; i < n; ++i) {
        const float v = g_frame_ms[(g_frame_count - 1u - i) % g_frame_ms.size()];
        avg_ms += v;
        max_ms = std::max(max_ms, v);
    }
    if (n != 0u) avg_ms /= static_cast<float>(n);
}

// Fast-forward (docs/PLAYING.md "Fast-forward"): the backend reports the
// engaged speed through openspm_renderer_turbo (0 = normal play, N x, or
// OPENSPM_TURBO_UNLIMITED); the indicator sits under the FPS counter.
inline std::atomic<unsigned> g_turbo_speed{0u};

inline void draw_fps_counter() {
    const unsigned turbo = g_turbo_speed.load(std::memory_order_relaxed);
    if (!g_show_fps && turbo == 0u) return;
    float avg = 0.f, peak = 0.f;
    frame_stats(avg, peak);
    ImGui::SetNextWindowPos(ImVec2(8.f, 8.f), ImGuiCond_Always);
    ImGui::SetNextWindowBgAlpha(0.45f);
    const ImGuiWindowFlags flags = ImGuiWindowFlags_NoDecoration | ImGuiWindowFlags_AlwaysAutoResize |
                                   ImGuiWindowFlags_NoSavedSettings | ImGuiWindowFlags_NoFocusOnAppearing |
                                   ImGuiWindowFlags_NoNav | ImGuiWindowFlags_NoInputs;
    if (ImGui::Begin("##openspm_fps", nullptr, flags)) {
        if (g_show_fps) ImGui::Text("%.1f fps  %.2f ms (max %.1f)", avg > 0.f ? 1000.f / avg : 0.f, avg, peak);
        if (turbo == OPENSPM_TURBO_UNLIMITED) ImGui::TextColored(ImVec4(1.f, 0.8f, 0.2f, 1.f), ">> Turbo unlimited");
        else if (turbo != 0u) ImGui::TextColored(ImVec4(1.f, 0.8f, 0.2f, 1.f), ">> Turbo %ux", turbo);
    }
    ImGui::End();
}

// ---- Mod overlay (openspm.overlay, include/openspm/overlay_service.h) -----
// Panels and notifications set by mods from any thread; drawn every frame,
// top-right (panels) and bottom-centre (notifications), never taking input.
struct ModPanel {
    std::string id, text;
};
struct ModNote {
    std::string text;
    std::chrono::steady_clock::time_point until;
};
inline std::mutex g_mod_overlay_mutex;
inline std::vector<ModPanel> g_mod_panels;
inline std::vector<ModNote> g_mod_notes;

inline std::string mod_overlay_clip(const char *text, std::size_t limit) {
    std::string out = text != nullptr ? text : "";
    if (out.size() > limit) {
        out.resize(limit);
        while (!out.empty() && (static_cast<unsigned char>(out.back()) & 0xC0u) == 0x80u) out.pop_back();
        if (!out.empty() && (static_cast<unsigned char>(out.back()) & 0x80u) != 0u) out.pop_back(); // cut lead byte
    }
    return out;
}

inline int mod_overlay_set_panel(const char *id, const char *text) {
    if (id == nullptr || *id == '\0' || std::strlen(id) > OPENSPM_OVERLAY_MAX_ID) return OPENSPM_ERROR_INVALID_ARGUMENT;
    std::lock_guard lock{g_mod_overlay_mutex};
    auto it = std::find_if(g_mod_panels.begin(), g_mod_panels.end(), [&](const ModPanel &p) { return p.id == id; });
    if (text == nullptr || *text == '\0') {
        if (it != g_mod_panels.end()) g_mod_panels.erase(it);
        return OPENSPM_OK;
    }
    if (it != g_mod_panels.end()) {
        it->text = mod_overlay_clip(text, OPENSPM_OVERLAY_MAX_TEXT);
        return OPENSPM_OK;
    }
    if (g_mod_panels.size() >= OPENSPM_OVERLAY_MAX_PANELS) return OPENSPM_ERROR_RUNTIME;
    g_mod_panels.push_back({id, mod_overlay_clip(text, OPENSPM_OVERLAY_MAX_TEXT)});
    return OPENSPM_OK;
}

inline int mod_overlay_notify(const char *text, std::uint32_t ms) {
    if (text == nullptr || *text == '\0') return OPENSPM_ERROR_INVALID_ARGUMENT;
    std::lock_guard lock{g_mod_overlay_mutex};
    if (g_mod_notes.size() >= 6u) g_mod_notes.erase(g_mod_notes.begin());
    g_mod_notes.push_back({mod_overlay_clip(text, 256u),
                           std::chrono::steady_clock::now() + std::chrono::milliseconds(ms != 0u ? std::min(ms, 30000u) : 2500u)});
    return OPENSPM_OK;
}

// Boxes (overlay version 2): per layer, in fractions of the game picture.
struct ModBoxLayer {
    std::string id;
    std::vector<openspm_overlay_box> boxes;
};
inline std::vector<ModBoxLayer> g_mod_box_layers; // under g_mod_overlay_mutex

inline int mod_overlay_set_boxes(const char *id, const openspm_overlay_box *boxes, std::uint32_t count) {
    if (id == nullptr || *id == '\0' || std::strlen(id) > OPENSPM_OVERLAY_MAX_ID || (count != 0u && boxes == nullptr))
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    count = std::min<std::uint32_t>(count, OPENSPM_OVERLAY_MAX_BOXES);
    std::lock_guard lock{g_mod_overlay_mutex};
    auto it = std::find_if(g_mod_box_layers.begin(), g_mod_box_layers.end(),
                           [&](const ModBoxLayer &l) { return l.id == id; });
    if (count == 0u) {
        if (it != g_mod_box_layers.end()) g_mod_box_layers.erase(it);
        return OPENSPM_OK;
    }
    if (it == g_mod_box_layers.end()) {
        if (g_mod_box_layers.size() >= OPENSPM_OVERLAY_MAX_BOX_LAYERS) return OPENSPM_ERROR_RUNTIME;
        g_mod_box_layers.push_back({id, {}});
        it = std::prev(g_mod_box_layers.end());
    }
    it->boxes.assign(boxes, boxes + count);
    return OPENSPM_OK;
}

// image: the game picture inside the window, in ImGui coordinates.
inline void draw_mod_boxes(const ImVec2 &image_pos, const ImVec2 &image_size) {
    std::vector<openspm_overlay_box> boxes;
    {
        std::lock_guard lock{g_mod_overlay_mutex};
        for (const auto &l : g_mod_box_layers) boxes.insert(boxes.end(), l.boxes.begin(), l.boxes.end());
    }
    if (boxes.empty() || image_size.x <= 0.f || image_size.y <= 0.f) return;
    ImDrawList *dl = ImGui::GetForegroundDrawList();
    dl->PushClipRect(image_pos, ImVec2(image_pos.x + image_size.x, image_pos.y + image_size.y), true);
    for (const auto &b : boxes) {
        if (!std::isfinite(b.x0) || !std::isfinite(b.y0) || !std::isfinite(b.x1) || !std::isfinite(b.y1)) continue;
        const ImVec2 a(image_pos.x + std::min(b.x0, b.x1) * image_size.x, image_pos.y + std::min(b.y0, b.y1) * image_size.y);
        const ImVec2 c(image_pos.x + std::max(b.x0, b.x1) * image_size.x, image_pos.y + std::max(b.y0, b.y1) * image_size.y);
        const ImU32 col = IM_COL32((b.rgba >> 24) & 0xFFu, (b.rgba >> 16) & 0xFFu, (b.rgba >> 8) & 0xFFu, b.rgba & 0xFFu);
        dl->AddRect(a, c, col, 0.f, 0, 2.f);
    }
    dl->PopClipRect();
}

inline void draw_mod_overlay() {
    std::vector<ModPanel> panels;
    std::vector<ModNote> notes;
    const auto now = std::chrono::steady_clock::now();
    {
        std::lock_guard lock{g_mod_overlay_mutex};
        if (g_mod_panels.empty() && g_mod_notes.empty()) return;
        g_mod_notes.erase(std::remove_if(g_mod_notes.begin(), g_mod_notes.end(),
                                         [&](const ModNote &n) { return n.until <= now; }),
                          g_mod_notes.end());
        panels = g_mod_panels;
        notes = g_mod_notes;
    }
    const ImGuiViewport *vp = ImGui::GetMainViewport();
    const ImGuiWindowFlags flags = ImGuiWindowFlags_NoDecoration | ImGuiWindowFlags_AlwaysAutoResize |
                                   ImGuiWindowFlags_NoSavedSettings | ImGuiWindowFlags_NoFocusOnAppearing |
                                   ImGuiWindowFlags_NoNav | ImGuiWindowFlags_NoInputs;
    float y = vp->WorkPos.y + 8.f;
    for (std::size_t i = 0; i < panels.size(); ++i) {
        ImGui::SetNextWindowPos(ImVec2(vp->WorkPos.x + vp->WorkSize.x - 8.f, y), ImGuiCond_Always, ImVec2(1.f, 0.f));
        ImGui::SetNextWindowBgAlpha(0.45f);
        const std::string name = "##openspm_mod_panel_" + panels[i].id;
        if (ImGui::Begin(name.c_str(), nullptr, flags)) {
            ImGui::TextUnformatted(panels[i].text.c_str());
            y += ImGui::GetWindowHeight() + 6.f;
        }
        ImGui::End();
    }
    if (!notes.empty()) {
        ImGui::SetNextWindowPos(ImVec2(vp->WorkPos.x + vp->WorkSize.x * 0.5f, vp->WorkPos.y + vp->WorkSize.y - 24.f),
                                ImGuiCond_Always, ImVec2(0.5f, 1.f));
        ImGui::SetNextWindowBgAlpha(0.6f);
        if (ImGui::Begin("##openspm_mod_notes", nullptr, flags)) {
            for (const auto &n : notes) {
                const float left = std::chrono::duration<float>(n.until - now).count();
                ImGui::PushStyleVar(ImGuiStyleVar_Alpha, std::clamp(left / 0.4f, 0.f, 1.f));
                ImGui::TextUnformatted(n.text.c_str());
                ImGui::PopStyleVar();
            }
        }
        ImGui::End();
    }
}

inline void draw_frame_readout() {
    float avg = 0.f, peak = 0.f;
    frame_stats(avg, peak);
    ImGui::Text("%.1f fps   avg %.2f ms   max %.1f ms (last 60 frames)", avg > 0.f ? 1000.f / avg : 0.f, avg, peak);
    const char *period = std::getenv("OPENSPM_M14_RETRACE_PERIOD_NS");
    if (period != nullptr && *period != '\0') {
        const double ns = std::strtod(period, nullptr);
        ImGui::Text("Game clock locked to the display: %.3f Hz", ns > 0.0 ? 1e9 / ns : 0.0);
    } else {
        ImGui::Text("Game clock: fixed 60.000 Hz");
    }
}

inline void set_show_fps(bool on) {
    g_show_fps = on;
    save_setting("Overlay", "show_fps", on ? "1" : "0");
}

// ---- Display ---------------------------------------------------------------

inline void apply_window_size(SDL_Window *window, int w, int h) {
    const SDL_DisplayID display = SDL_GetDisplayForWindow(window);
    const bool ok = SDL_SetWindowSize(window, w, h) &&
                    SDL_SetWindowPosition(window, SDL_WINDOWPOS_CENTERED_DISPLAY(display),
                                          SDL_WINDOWPOS_CENTERED_DISPLAY(display));
    (void)SDL_SyncWindow(window);
    m14_request_graphics_resize();
    char line[160];
    std::snprintf(line, sizeof line, ok ? "OpenSPM menu: window size %dx%d." : "OpenSPM menu: window size %dx%d failed.",
                  w, h);
    log_message(ok ? OPENSPM_LOG_INFO : OPENSPM_LOG_WARN, line);
}

// [Display] frame_mode (launcher/native_settings.h expands it into the presenter
// variables before the DLLs load). The running mode is read back from those
// variables, so an M14-VANILLA-RUN.cmd flag shows as what actually runs.
inline bool env_is_one(const char *name) { return env_value(name) == "1"; }

inline int running_frame_mode() { // 0 vanilla, 1 smooth, 2 vanilla_timeline, 3 other (launcher flag)
    const bool timeline = env_is_one("OPENSPM_M14_ASYNC_PRESENTER") && env_is_one("OPENSPM_M14_PRESENT_TIMELINE");
    const bool vanilla_mode = env_value("OPENSPM_M14_PRESENT_TIMELINE_MODE") == "vanilla";
    if (!timeline) return 0;
    if (vanilla_mode) return 2;
    return env_is_one("OPENSPM_M14_INTERP") ? 1 : 3;
}

inline void draw_frame_mode() {
    static const std::vector<Choice> kModes = {{"vanilla", "Vanilla 60"},
                                               {"smooth", "Smooth (interpolated) (experimental)"},
                                               {"vanilla_timeline", "Vanilla 60 via present timeline (diagnostic)"}};
    const int running = running_frame_mode();
    const std::string saved = openspm_ini::get(settings().text, "Display", "frame_mode");
    int selected = running < 3 ? running : 0;
    if (!saved.empty())
        for (size_t i = 0; i < kModes.size(); ++i)
            if (iequal(saved, kModes[i].value)) selected = static_cast<int>(i);
    ImGui::SetNextItemWidth(ImGui::GetFontSize() * 18.f);
    if (ImGui::BeginCombo("Frame mode", kModes[static_cast<size_t>(selected)].label.c_str())) {
        for (size_t i = 0; i < kModes.size(); ++i)
            if (ImGui::Selectable(kModes[i].label.c_str(), static_cast<int>(i) == selected) &&
                static_cast<int>(i) != selected)
                save_setting("Display", "frame_mode", kModes[i].value);
        ImGui::EndCombo();
    }
    ImGui::SetItemTooltip(
        "The game always simulates 60 frames per second; this only changes how frames reach the screen.\n"
        "Vanilla 60: one image per game frame with vsync, exactly like the console (default).\n"
        "Smooth: shows a new image on every display refresh, blending camera and object motion between\n"
        "the last two game frames (present timeline + draw tags + frame interpolation). It only makes a\n"
        "difference on a display faster than 60 Hz (100 / 120 / 144 / 240 Hz); at 60 Hz it looks like\n"
        "Vanilla 60. Experimental: effects the renderer cannot replay safely show the plain game frame.\n"
        "Vanilla 60 via present timeline: the Smooth presenter without interpolation (for comparisons).\n"
        "Applies on restart: the presenter is chosen when OpenSPM starts.");
    restart_marker(selected != running);

    // Read-only: what runs now (startup variables; the renderer publishes the display clock).
    static const char *kRunning[] = {"Vanilla 60 (one image per game frame, vsync)",
                                     "Smooth: present timeline + draw tags + interpolation",
                                     "Vanilla 60 through the present timeline",
                                     "present timeline without interpolation (launcher flag)"};
    ImGui::TextDisabled("Running: %s", kRunning[running]);
    if (running != 0) {
        const std::string refresh = env_value("OPENSPM_M14_DISPLAY_REFRESH"); // "numerator/denominator"
        const size_t slash = refresh.find('/');
        const double num = slash != std::string::npos ? std::strtod(refresh.c_str(), nullptr) : 0.0;
        const double den = slash != std::string::npos ? std::strtod(refresh.c_str() + slash + 1, nullptr) : 0.0;
        if (num > 0.0 && den > 0.0) {
            const double hz = num / den;
            ImGui::TextDisabled("Presenter clock: %.3f Hz display%s", hz,
                                running == 1 && hz < 61.0 ? " (60 Hz: Smooth looks like Vanilla 60 here)" : "");
        } else {
            ImGui::TextDisabled("Presenter clock: display refresh not reported yet");
        }
        if (running == 1 && !env_is_one("OPENSPM_M14_DRAW_TAGS"))
            ImGui::TextColored(kPendingColor, "Draw tags are off: every frame shows without interpolation.");
    }
    if (ImGui::TreeNode("Advanced (Smooth)")) {
        toggle_setting("Interpolate with the game's camera views", "Display", "interp_camera_views",
                       "OPENSPM_M14_INTERP_VIEW", true,
                       "On (default): Smooth blends the game's own camera views, then each object's motion.\n"
                       "Off: blends the final object matrices directly (the launcher's \"interpdirect\");\n"
                       "only for comparing if the camera looks wrong in Smooth.");
        ImGui::TreePop();
    }
    ImGui::TextDisabled("Not available yet: frame caps, unlimited (VRR) and vsync off with interpolation.");
}

inline void draw_display_tab() {
    SDL_Window *window = main_window();
    const bool fullscreen = window != nullptr && (SDL_GetWindowFlags(window) & SDL_WINDOW_FULLSCREEN) != 0;
    const SDL_DisplayMode *exclusive = window != nullptr ? SDL_GetWindowFullscreenMode(window) : nullptr;
    const SDL_DisplayID display = window != nullptr ? SDL_GetDisplayForWindow(window) : 0;
    const SDL_DisplayMode *current = display != 0 ? SDL_GetCurrentDisplayMode(display) : nullptr;

    ImGui::SeparatorText("Window");
    int w = 0, h = 0;
    if (window != nullptr) SDL_GetWindowSize(window, &w, &h);
    ImGui::Text("Now: %s, %dx%d", !fullscreen ? "windowed" : exclusive != nullptr ? "exclusive fullscreen"
                                                                                   : "borderless fullscreen", w, h);
    if (current != nullptr) {
        ImGui::SameLine();
        ImGui::TextDisabled("(display %dx%d @ %.3f Hz)", current->w, current->h, mode_hz(*current));
    }
    if (ImGui::Button("Windowed") && window != nullptr) {
        SDL_SetWindowFullscreen(window, false);
        m14_update_cursor_for_window(window);
        m14_request_graphics_resize();
    }
    ImGui::SetItemTooltip("Switch to a window now (F11 toggles too).");
    ImGui::SameLine();
    if (ImGui::Button("Borderless fullscreen") && window != nullptr) {
        SDL_SetWindowFullscreenMode(window, nullptr);
        SDL_SetWindowFullscreen(window, true);
        m14_update_cursor_for_window(window);
        m14_request_graphics_resize();
    }
    ImGui::SetItemTooltip("Fill the screen at the desktop resolution and refresh now (F11 toggles too).");

    ImGui::BeginDisabled(fullscreen || window == nullptr);
    {
        static const int kSizes[][2] = {{640, 480},   {960, 720},  {1280, 960},  {1440, 1080}, {1920, 1440},
                                        {1280, 720},  {1600, 900}, {1920, 1080}, {2560, 1440}};
        SDL_Rect usable{};
        const bool bounded = display != 0 && SDL_GetDisplayUsableBounds(display, &usable);
        char preview[32];
        std::snprintf(preview, sizeof preview, "%dx%d", w, h);
        ImGui::SetNextItemWidth(ImGui::GetFontSize() * 14.f);
        if (ImGui::BeginCombo("Window size", preview)) {
            for (const auto &size : kSizes) {
                if (bounded && (size[0] > usable.w || size[1] > usable.h)) continue; // would not fit
                char label[48];
                std::snprintf(label, sizeof label, "%dx%d (%s)", size[0], size[1], size[0] * 3 == size[1] * 4 ? "4:3" : "16:9");
                if (ImGui::Selectable(label, size[0] == w && size[1] == h)) apply_window_size(window, size[0], size[1]);
            }
            ImGui::EndCombo();
        }
        ImGui::SetItemTooltip("Resize and centre the window now. Dragging the window edges works too.\n"
                              "4:3 sizes fit the game without black bars (16:9 sizes with widescreen on).");
        live_marker();
    }
    ImGui::EndDisabled();

    static const std::vector<Choice> kWindowModes = {
        {"windowed", "windowed"}, {"borderless", "borderless fullscreen"}, {"exclusive", "exclusive fullscreen"}};
    choice_setting("Start in", "Display", "window_mode", "OPENSPM_WINDOW_MODE", kWindowModes, 0,
                   "How the window opens at launch. Exclusive fullscreen can switch the display to a refresh\n"
                   "rate that suits 60 fps (see below); borderless keeps the desktop mode.");
    {
        const std::string saved = openspm_ini::get(settings().text, "Display", "refresh_hz");
        const std::string running = env_value("OPENSPM_REFRESH_HZ");
        choice_setting("Exclusive refresh", "Display", "refresh_hz", "OPENSPM_REFRESH_HZ",
                       refresh_choices_with(saved, running), 0,
                       "Refresh rate for exclusive fullscreen (only used when \"Start in\" is exclusive).\n"
                       "auto picks the highest rate that 60 fps divides evenly (59.94 / 60 / 120 / 240 Hz ...).\n"
                       "Rates marked \"smooth\" lock the game clock to the display: no judder.");
    }

    ImGui::SeparatorText("Image");
    // Widescreen is read at startup (the game reads the console's TV setting at
    // boot), so the aspect choices follow the running value, not the saved one.
    static const bool widescreen = openspm::aspect::widescreen_value(std::getenv("OPENSPM_WIDESCREEN"));
    static int aspect = [] {
        const char *value = std::getenv("OPENSPM_ASPECT");
        return value != nullptr && ascii_iequals(value, "stretch") ? 1 : 0;
    }();
    const int before = aspect;
    ImGui::RadioButton(widescreen ? "16:9 (black bars)" : "4:3 (black bars)", &aspect, 0);
    ImGui::SetItemTooltip(widescreen ? "Show the game's 16:9 picture at 16:9; black bars fill the rest of a 4:3 or\n"
                                       "ultrawide window."
                                     : "Keep the game's 4:3 picture; black bars fill the rest of a wide window.");
    ImGui::SameLine();
    ImGui::RadioButton("Stretch", &aspect, 1);
    ImGui::SetItemTooltip(widescreen ? "Stretch the 16:9 picture to the whole window (distorted unless it is 16:9)."
                                     : "Stretch the 4:3 picture to the whole window (distorted on wide screens).");
    live_marker();
    if (aspect != before) {
        const openspm::aspect::Output output = openspm::aspect::choose_output(aspect == 1, widescreen);
        g_pending_policy = output.stretch_policy ? AURORA_VIEWPORT_STRETCH : AURORA_VIEWPORT_FIT;
        g_pending_stretch_aspect = output.stretch_aspect;
        save_setting("Display", "aspect", aspect == 1 ? "stretch" : "4:3");
    }
    toggle_setting("Widescreen 16:9", "Display", "widescreen", "OPENSPM_WIDESCREEN", false,
                   "The game renders its own wider 16:9 view, as on a Wii set to a 16:9 TV (OpenSPM\n"
                   "reports that console setting to the game). The picture is shown at 16:9: black bars\n"
                   "on 4:3 or ultrawide windows, or Stretch to fill. Off: the original 4:3 picture.\n"
                   "Launcher flag: widescreen.");

    ImGui::SeparatorText("Frame rate");
    draw_frame_readout();
    static const std::vector<Choice> kSync = {{"auto", "auto (lock when it fits)"}, {"off", "off (fixed 60.000 Hz)"}};
    choice_setting("Display sync", "Display", "display_sync", "OPENSPM_M14_DISPLAY_SYNC", kSync, 0,
                   "auto: when the display refresh is a multiple of ~60 Hz (59.94 / 59.978 / 60 / 120 / 240),\n"
                   "the game clock follows the display exactly, so motion is judder-free. The game then runs\n"
                   "at the display's rate (e.g. 59.94 instead of 60.00, 0.1% slower).\n"
                   "off: always a fixed 60.000 Hz clock.");
    bool show_fps = g_show_fps;
    if (ImGui::Checkbox("FPS counter (top-left)", &show_fps)) set_show_fps(show_fps);
    ImGui::SetItemTooltip("Frames per second and frame time in the corner, also while this menu is closed.");
    live_marker();
    draw_frame_mode();
}

// ---- Graphics --------------------------------------------------------------

inline void draw_graphics_tab() {
    ImGui::TextWrapped("Fixes for specific Super Paper Mario effects. All are on by default; turn one off only "
                       "to compare, or to work around a problem (and please report it).");
    ImGui::SeparatorText("Effects");
    toggle_setting("Snapshot effects (EFB copy tracking)", "Effects", "copy_tags", "OPENSPM_M14_COPY_TAGS", true,
                   "Resolves screen copies the game moves with the CPU to HD textures, so star doors and\n"
                   "similar \"frozen picture\" effects show the right image.");
    toggle_setting("Pixel-exact screen copies", "Effects", "copy_pixel_exact", "OPENSPM_M14_COPY_PIXEL_EXACT", true,
                   "Copy rectangles follow the game's pixel grid exactly (keeps door swings aligned).");
    toggle_setting("Bounding boxes in game units", "Effects", "bbox_logical", "OPENSPM_M14_BBOX_LOGICAL", true,
                   "Reports the GPU bounding box in the game's 640x480 coordinates at any window size\n"
                   "(pipe and chunk effects rely on it).");
    ImGui::SeparatorText("Planned");
    ImGui::TextDisabled("Internal resolution, anti-aliasing, texture filtering, ultrawide.");
}

// ---- Texture Packs ---------------------------------------------------------

inline void open_folder(const std::filesystem::path &folder) {
    std::error_code ec;
    std::filesystem::create_directories(folder, ec);
    std::string url = "file:///" + utf8(std::filesystem::absolute(folder, ec));
    std::replace(url.begin(), url.end(), '\\', '/');
    if (!SDL_OpenURL(url.c_str())) g_status = std::string("Could not open ") + url + ": " + SDL_GetError();
}

// The pack list as OpenSPM.ini orders and enables it (what "Apply now" loads).
inline std::vector<openspm_texpacks::Pack> g_pack_view;
inline bool g_pack_files_counted = false;

inline void refresh_pack_view() {
    namespace tp = openspm_texpacks;
    g_pack_view = tp::discover(tp::state().folder, settings().text);
    for (tp::Pack &pack : g_pack_view) {
        for (const tp::Pack &live : tp::state().packs) {
            if (live.name != pack.name) continue;
            pack.files = live.files;
            pack.indexed = live.indexed;
            pack.loaded = live.loaded;
        }
    }
}

// Counting walks every pack once (the first time the tab is shown, and on Rescan).
inline void count_pack_files() {
    namespace tp = openspm_texpacks;
    for (tp::Pack &pack : g_pack_view) {
        pack.files = tp::count_files(pack);
        for (tp::Pack &live : tp::state().packs)
            if (live.name == pack.name) live.files = pack.files;
    }
    g_pack_files_counted = true;
}

inline void draw_texture_packs_tab() {
    namespace tp = openspm_texpacks;
    tp::State &state = tp::state();
    ImGui::TextWrapped("Dolphin HD texture packs (\"Load Custom Textures\"). Every subfolder of the texture packs "
                       "folder is one pack; a Dolphin Load\\Textures\\%s folder works as it is. Packs higher in "
                       "the list win. See docs/TEXTURE-PACKS.md.", tp::kGameId);

    ImGui::SeparatorText("Packs");
    bool master = tp::master_enabled(settings().text);
    if (ImGui::Checkbox("Load texture packs", &master)) {
        save_setting("TexturePacks", "enabled", master ? "1" : "0");
        refresh_pack_view();
    }
    ImGui::SetItemTooltip("Saved as [TexturePacks] enabled. Off: no pack is indexed and textures are never\n"
                          "hashed for replacement.");
    if (ImGui::Button("Open texture packs folder")) open_folder(state.folder);
    ImGui::SetItemTooltip("%s", utf8(state.folder).c_str());
    ImGui::SameLine();
    if (ImGui::Button("Rescan")) {
        refresh_pack_view();
        count_pack_files();
    }
    ImGui::SetItemTooltip("Look for new pack folders and count their textures.");
    if (!g_pack_files_counted) count_pack_files();

    bool changed = false;
    int swap_with = -1; // priority arrows: swap row swap_at with swap_with after the table
    size_t swap_at = 0;
    constexpr ImGuiTableFlags kTable =
        ImGuiTableFlags_RowBg | ImGuiTableFlags_BordersInnerV | ImGuiTableFlags_SizingFixedFit;
    if (g_pack_view.empty()) {
        ImGui::TextWrapped("No packs in %s. Put each pack in its own subfolder, then press Rescan.",
                           utf8(state.folder).c_str());
    } else if (ImGui::BeginTable("##texpacks", 4, kTable)) {
        ImGui::TableSetupColumn("On");
        ImGui::TableSetupColumn("Pack", ImGuiTableColumnFlags_WidthStretch);
        ImGui::TableSetupColumn("Textures");
        ImGui::TableSetupColumn("Priority");
        ImGui::TableHeadersRow();
        for (size_t i = 0; i < g_pack_view.size(); ++i) {
            tp::Pack &pack = g_pack_view[i];
            ImGui::PushID(static_cast<int>(i));
            ImGui::TableNextRow();
            ImGui::TableSetColumnIndex(0);
            const bool storable = tp::name_storable(pack.name);
            bool on = pack.enabled;
            ImGui::BeginDisabled(!storable);
            if (ImGui::Checkbox("##on", &on)) {
                save_setting("TexturePacks", tp::pack_key(pack.name).c_str(), on ? "1" : "0");
                changed = true;
            }
            ImGui::EndDisabled();
            if (storable) ImGui::SetItemTooltip("Saved as [TexturePacks] pack.%s", pack.name.c_str());
            else ImGui::SetItemTooltip("Rename this folder (without = ; # [ ] |) to be able to turn it off.");
            ImGui::TableSetColumnIndex(1);
            ImGui::TextUnformatted(pack.name.c_str());
            ImGui::SetItemTooltip("%s", utf8(pack.dir).c_str());
            ImGui::TableSetColumnIndex(2);
            if (pack.loaded) ImGui::Text("%zu loaded", pack.indexed);
            else if (pack.files >= 0) ImGui::TextDisabled("%lld files", pack.files);
            else ImGui::TextDisabled("-");
            ImGui::TableSetColumnIndex(3);
            ImGui::BeginDisabled(i == 0 || !storable);
            if (ImGui::ArrowButton("##up", ImGuiDir_Up)) { swap_with = static_cast<int>(i) - 1; swap_at = i; }
            ImGui::EndDisabled();
            ImGui::SetItemTooltip("Higher priority: wins over the packs below it.");
            ImGui::SameLine();
            ImGui::BeginDisabled(i + 1 >= g_pack_view.size() || !storable);
            if (ImGui::ArrowButton("##down", ImGuiDir_Down)) { swap_with = static_cast<int>(i) + 1; swap_at = i; }
            ImGui::EndDisabled();
            ImGui::PopID();
        }
        ImGui::EndTable();
    }
    if (swap_with >= 0 && static_cast<size_t>(swap_with) < g_pack_view.size()) {
        std::swap(g_pack_view[swap_at], g_pack_view[static_cast<size_t>(swap_with)]);
        save_setting("TexturePacks", "order", tp::order_string(g_pack_view));
        changed = true;
    }
    if (changed) refresh_pack_view();
    if (state.loose_files != 0)
        ImGui::TextColored(kPendingColor, "%zu texture file(s) lie directly in the folder and are ignored: "
                                          "move them into a pack subfolder.", state.loose_files);

    const bool pending = tp::signature(settings().text, g_pack_view) != state.applied_signature;
    if (pending) {
        ImGui::TextColored(kPendingColor, "Changes not applied yet.");
        ImGui::SameLine();
        if (ImGui::Button("Apply now")) {
            try { // std::filesystem throws on unusual paths; keep the render loop alive
                tp::apply(settings().text);
                refresh_pack_view();
                g_status = "Texture packs applied: " + std::to_string(state.registered) + " textures indexed.";
            } catch (const std::exception &e) {
                g_status = std::string("Texture packs could not be applied: ") + e.what();
            }
        }
        ImGui::SetItemTooltip("Re-index the packs now (a moment for large packs; replaced textures reload from\n"
                              "disk). Restarting OpenSPM applies them too.");
    }

    ImGui::SeparatorText("Status");
    const auto stats = aurora::texture::openspm_replacement_stats();
    ImGui::Text("Indexed %llu textures; %llu replaced so far this session.",
                static_cast<unsigned long long>(stats.registered), static_cast<unsigned long long>(stats.replaced));
    ImGui::Text("On the GPU: %llu (%.0f of %.0f MB)%s", static_cast<unsigned long long>(stats.resident),
                static_cast<double>(stats.residentBytes) / 1048576.0,
                static_cast<double>(stats.budgetBytes) / 1048576.0, stats.pendingLoads != 0 ? "  loading..." : "");
    if (stats.failed != 0)
        ImGui::TextColored(kPendingColor, "%llu file(s) failed to load (see the log).",
                           static_cast<unsigned long long>(stats.failed));
    static const std::vector<Choice> kCache = {
        {"512", "512 MB"}, {"1024", "1 GB"}, {"2048", "2 GB (default)"}, {"4096", "4 GB"}};
    choice_setting("GPU cache", "TexturePacks", "cache_mb", "OPENSPM_TEXTURE_PACK_CACHE_MB", kCache, 2,
                   "Video memory for decoded pack textures. Beyond it the least recently used ones are\n"
                   "dropped and reload from disk when the game needs them again.");
    {
        static char folder[512] = {};
        static bool loaded = false;
        const std::string saved = openspm_ini::get(settings().text, "TexturePacks", "folder");
        if (!loaded) {
            std::snprintf(folder, sizeof folder, "%s", saved.c_str());
            loaded = true;
        }
        ImGui::SetNextItemWidth(ImGui::GetFontSize() * 14.f);
        if (ImGui::InputTextWithHint("Folder", "texturepacks", folder, sizeof folder,
                                     ImGuiInputTextFlags_EnterReturnsTrue))
            save_setting("TexturePacks", "folder", folder);
        ImGui::SetItemTooltip("Texture packs folder, relative to OpenSPMNative.exe or absolute (Enter saves).\n"
                              "Empty: texturepacks. In use: %s", utf8(state.folder).c_str());
        restart_marker(tp::folder_from_setting(saved) != state.folder);
    }

    ImGui::SeparatorText("Make a pack");
    toggle_setting("Dump textures", "TexturePacks", "dump", "OPENSPM_TEXTURE_DUMP", false,
                   "Write every texture the game shows that no pack replaces, once, as a Dolphin-named\n"
                   "tex1_... PNG (the name Dolphin would dump it under). Upscale or edit it, keep the\n"
                   "name, and put it in a pack. Slows scene loads while on.");
    if (ImGui::Button("Open dumps folder")) open_folder(state.dump_folder);
    ImGui::SetItemTooltip("%s", utf8(state.dump_folder).c_str());
}

// ---- Controls --------------------------------------------------------------

// ---- Controls: keyboard map ---------------------------------------------

// The live [Keyboard] map for the backend (openspm_renderer_keyboard_map):
// generation 0 = nothing changed since startup (the backend keeps
// OPENSPM_KEY_MAP); every save from the menu publishes the text and bumps it.
inline std::mutex g_keymap_mutex;
inline std::string g_keymap_live;   // guarded by g_keymap_mutex
inline int g_keymap_generation = 0; // guarded by g_keymap_mutex
inline int g_key_capture_slot = -1; // slot waiting for a key press, -1 = none
inline bool g_key_capture_add = false;

inline int keymap_generation() {
    std::lock_guard<std::mutex> lock(g_keymap_mutex);
    return g_keymap_generation;
}

// Renderer export body: copies the live map, returns its generation.
inline int copy_keyboard_map(char *out, unsigned capacity) {
    std::lock_guard<std::mutex> lock(g_keymap_mutex);
    if (out != nullptr && capacity != 0u) std::snprintf(out, capacity, "%s", g_keymap_live.c_str());
    return g_keymap_generation;
}

// The map the backend uses now: the last one published, else the startup one.
inline openspm_keymap running_keymap() {
    openspm_keymap map;
    std::string text;
    int generation;
    {
        std::lock_guard<std::mutex> lock(g_keymap_mutex);
        text = g_keymap_live;
        generation = g_keymap_generation;
    }
    (void)openspm_keymap_parse(generation != 0 ? text.c_str() : env_value("OPENSPM_KEY_MAP").c_str(), &map);
    return map;
}

// Saves [Keyboard] map and hands it to the backend (applies at the next frame).
inline bool save_keymap(const openspm_keymap &map) {
    char text[512];
    const size_t length = openspm_keymap_format(&map, text, sizeof text);
    if (length >= 200u) { // native_settings.h free-form limit
        g_status = "Too many keys for one [Keyboard] map; clear some bindings first.";
        return false;
    }
    if (!save_setting("Keyboard", "map", text)) return false;
    std::lock_guard<std::mutex> lock(g_keymap_mutex);
    g_keymap_live = text;
    ++g_keymap_generation;
    return true;
}

inline bool keymap_equal(const openspm_keymap &a, const openspm_keymap &b) {
    return std::memcmp(&a, &b, sizeof a) == 0;
}

// While a slot waits for a key: the first bindable key pressed this frame.
// Esc cancels. Mouse and gamepad "keys" are ignored.
inline void poll_key_capture(openspm_keymap &map, bool &changed) {
    if (g_key_capture_slot < 0) return;
    if (ImGui::IsKeyPressed(ImGuiKey_Escape, false)) {
        g_key_capture_slot = -1;
        return;
    }
    for (int k = ImGuiKey_NamedKey_BEGIN; k < ImGuiKey_NamedKey_END; ++k) {
        const ImGuiKey key = static_cast<ImGuiKey>(k);
        if (!ImGui::IsKeyPressed(key, false)) continue;
        const char *name = ImGui::GetKeyName(key);
        const std::string text = name != nullptr ? name : "";
        if (text.rfind("Mouse", 0) == 0 || text.rfind("Gamepad", 0) == 0 || text.rfind("Mod", 0) == 0) continue;
        const unsigned vk = openspm_key_find(text.c_str(), text.size());
        if (vk == 0u) {
            g_status = text + " cannot be bound (OpenSPM hotkey or unsupported key).";
            continue;
        }
        std::uint8_t *keys = map.keys[g_key_capture_slot];
        if (!g_key_capture_add) std::memset(keys, 0, OPENSPM_KEYMAP_MAX_KEYS);
        bool present = false;
        for (int i = 0; i < OPENSPM_KEYMAP_MAX_KEYS; ++i) present |= keys[i] == vk;
        for (int i = 0; i < OPENSPM_KEYMAP_MAX_KEYS && !present; ++i) {
            if (keys[i] == 0u) {
                keys[i] = static_cast<std::uint8_t>(vk);
                present = true;
            }
        }
        if (!present) g_status = "A button takes up to 4 keys.";
        // The fast-forward and shake keys are never also a game key (or each
        // other's): binding a key to one side takes it from the other.
        const bool private_slot =
            g_key_capture_slot == OPENSPM_KEY_SLOT_TURBO || g_key_capture_slot == OPENSPM_KEY_SLOT_SHAKE;
        if (present && private_slot) {
            for (int slot = 0; slot < OPENSPM_KEY_SLOT_COUNT; ++slot)
                if (slot != g_key_capture_slot) openspm_keymap_unbind(&map, slot, vk);
        } else if (present) {
            openspm_keymap_unbind(&map, OPENSPM_KEY_SLOT_TURBO, vk);
            openspm_keymap_unbind(&map, OPENSPM_KEY_SLOT_SHAKE, vk);
        }
        g_key_capture_slot = -1;
        changed = true;
        return;
    }
}

inline void draw_keyboard_map() {
    ImGui::SeparatorText("Keyboard (Wii Remote held sideways)");
    const openspm_keymap running = running_keymap();
    openspm_keymap map = running;
    bool changed = false;
    poll_key_capture(map, changed);
    static const char *const kSlotLabels[OPENSPM_KEY_SLOT_COUNT] = {
        "Up",        "Down",      "Left",      "Right",     "A",    "B", "1",
        "2 (jump)", "- (Minus)", "+ (Plus)", "HOME (OpenSPM menu)", "Turbo (fast-forward)",
        "Shake (Wii Remote shake)"};
    constexpr ImGuiTableFlags kTable = ImGuiTableFlags_RowBg | ImGuiTableFlags_BordersInnerV | ImGuiTableFlags_SizingFixedFit;
    if (ImGui::BeginTable("##keys", 3, kTable)) {
        for (int slot = 0; slot < OPENSPM_KEY_SLOT_COUNT; ++slot) {
            ImGui::PushID(slot);
            ImGui::TableNextRow();
            ImGui::TableSetColumnIndex(0);
            ImGui::TextUnformatted(kSlotLabels[slot]);
            ImGui::TableSetColumnIndex(1);
            if (g_key_capture_slot == slot) {
                ImGui::TextColored(kPendingColor, g_key_capture_add ? "press a key to add (Esc cancels)"
                                                                    : "press a key (Esc cancels)");
            } else {
                char keys[96];
                openspm_keymap_format_slot(&map, slot, keys, sizeof keys);
                std::string shown = keys;
                for (size_t at = shown.find('/'); at != std::string::npos; at = shown.find('/', at + 3))
                    shown.replace(at, 1, " / ");
                ImGui::TextUnformatted(shown.c_str());
            }
            ImGui::TableSetColumnIndex(2);
            if (ImGui::SmallButton("Set")) {
                g_key_capture_slot = slot;
                g_key_capture_add = false;
            }
            ImGui::SetItemTooltip("Replace this button's keys with the next key you press.");
            ImGui::SameLine();
            if (ImGui::SmallButton("Add")) {
                g_key_capture_slot = slot;
                g_key_capture_add = true;
            }
            ImGui::SetItemTooltip("Add another key for this button (up to 4).");
            ImGui::SameLine();
            if (ImGui::SmallButton("Clear")) {
                std::memset(map.keys[slot], 0, OPENSPM_KEYMAP_MAX_KEYS);
                changed = true;
            }
            ImGui::PopID();
        }
        ImGui::TableNextRow();
        ImGui::TableSetColumnIndex(0);
        ImGui::TextUnformatted("Pointer");
        ImGui::TableSetColumnIndex(1);
        ImGui::TextUnformatted("Mouse");
        ImGui::EndTable();
    }
    if (changed && !keymap_equal(map, running)) (void)save_keymap(map);
    openspm_keymap defaults;
    openspm_keymap_default(&defaults);
    if (ImGui::Button("Default keys") && !keymap_equal(running, defaults)) (void)save_keymap(defaults);
    ImGui::SetItemTooltip("Arrows / WASD move, Space / Z = A, C = B, X = 1, Enter = 2, Backspace = -, Home = HOME,\n"
                          "Tab = turbo (fast-forward).");
    live_marker();
    ImGui::TextDisabled("Saved as [Keyboard] map. Keys count only while the OpenSPM window is focused;");
    ImGui::TextDisabled("the game gets no input while this menu is open. F1, F8, F11, F12 and Esc stay OpenSPM keys.");
}

// ---- Controls: gamepad pointer + rumble (include/openspm/pointer_input.h) ---
//
// [Controller] stick_pointer / pointer_speed / pointer_deadzone / rumble /
// rumble_strength: read from the environment (OpenSPM.ini) at startup, then
// edited live here; the renderer's pointer-stick and rumble exports read these.

struct ControllerTuning {
    bool stick_pointer = true;
    int pointer_speed = OPENSPM_POINTER_SPEED_DEFAULT;
    int pointer_deadzone = OPENSPM_POINTER_DEADZONE_DEFAULT;
    bool rumble = true;
    int rumble_strength = OPENSPM_RUMBLE_STRENGTH_DEFAULT;
};

inline int env_percent(const char *name, int fallback) {
    const std::string value = env_value(name);
    if (value.empty()) return fallback;
    char *end = nullptr;
    const long pct = std::strtol(value.c_str(), &end, 10);
    if (end == value.c_str() || *end != '\0' || pct < 0 || pct > 100) return fallback;
    return static_cast<int>(pct);
}

inline ControllerTuning &controller_tuning() {
    static ControllerTuning tuning = [] {
        ControllerTuning t;
        const bool pad_off = env_value("OPENSPM_PAD") == "0"; // [Controller] enabled = 0
        t.stick_pointer = !pad_off && env_value("OPENSPM_STICK_POINTER") != "0";
        t.pointer_speed = env_percent("OPENSPM_POINTER_SPEED", OPENSPM_POINTER_SPEED_DEFAULT);
        t.pointer_deadzone = env_percent("OPENSPM_POINTER_DEADZONE", OPENSPM_POINTER_DEADZONE_DEFAULT);
        t.rumble = !pad_off && env_value("OPENSPM_RUMBLE") != "0";
        t.rumble_strength = env_percent("OPENSPM_RUMBLE_STRENGTH", OPENSPM_RUMBLE_STRENGTH_DEFAULT);
        return t;
    }();
    return tuning;
}

// A live percent slider saved to OpenSPM.ini when released.
inline void percent_slider(const char *label, const char *section, const char *key, int &value, int min,
                           const char *tooltip) {
    ImGui::SetNextItemWidth(ImGui::GetFontSize() * 14.f);
    ImGui::SliderInt(label, &value, min, 100, "%d%%");
    if (ImGui::IsItemDeactivatedAfterEdit()) save_setting(section, key, std::to_string(value));
    ImGui::SetItemTooltip("%s", tooltip);
    live_marker();
}

inline void draw_pointer_and_rumble(SDL_Gamepad *pad) {
    ControllerTuning &tuning = controller_tuning();
    ImGui::SeparatorText("Wii pointer");
    ImGui::TextDisabled("The mouse points over the game image; the black bars are off-screen (like pointing past the TV).");
    if (ImGui::Checkbox("Right stick moves the pointer", &tuning.stick_pointer))
        save_setting("Controller", "stick_pointer", tuning.stick_pointer ? "1" : "0");
    ImGui::SetItemTooltip("The gamepad's right stick moves the Wii Remote pointer (Tippi) from where it is;\n"
                          "moving the mouse hands the pointer back to the mouse. Saved as [Controller] stick_pointer.");
    live_marker();
    ImGui::BeginDisabled(!tuning.stick_pointer);
    percent_slider("Pointer speed", "Controller", "pointer_speed", tuning.pointer_speed, 1,
                   "How fast the pointer moves at full tilt (50% crosses the screen in about 0.8 s).\n"
                   "Saved as [Controller] pointer_speed.");
    percent_slider("Stick deadzone", "Controller", "pointer_deadzone", tuning.pointer_deadzone, 0,
                   "How far the right stick must move before the pointer does (0..90% of full tilt).\n"
                   "Saved as [Controller] pointer_deadzone.");
    ImGui::EndDisabled();

    ImGui::SeparatorText("Rumble");
    if (ImGui::Checkbox("Rumble", &tuning.rumble)) save_setting("Controller", "rumble", tuning.rumble ? "1" : "0");
    ImGui::SetItemTooltip("The gamepad rumbles when the game rumbles the Wii Remote. Saved as [Controller] rumble.");
    live_marker();
    ImGui::BeginDisabled(!tuning.rumble);
    percent_slider("Rumble strength", "Controller", "rumble_strength", tuning.rumble_strength, 0,
                   "Motor strength while the game rumbles. Saved as [Controller] rumble_strength.");
    ImGui::EndDisabled();
    ImGui::BeginDisabled(pad == nullptr);
    if (ImGui::Button("Test rumble") && pad != nullptr) {
        const Uint16 level = static_cast<Uint16>(openspm_rumble_intensity(std::max(tuning.rumble_strength, 10)));
        if (!SDL_RumbleGamepad(pad, level, level, 500u)) g_status = "This gamepad reports no rumble support.";
    }
    ImGui::EndDisabled();
    ImGui::SetItemTooltip("Half a second at the strength above (at least 10%%).");
}

inline void draw_controls_tab() {
    draw_keyboard_map();

    ImGui::SeparatorText("OpenSPM menu");
    toggle_setting("HOME opens this menu", "Controller", "home_opens_menu", "OPENSPM_HOME_MENU", true,
                   "HOME (the gamepad's Guide / PS / Home button, or the keyboard's HOME key above) opens and\n"
                   "closes this menu like the Wii HOME Menu. HOME never reaches the game; off: HOME does nothing.\n"
                   "F1 always works.");
    toggle_setting("Pause the game while this menu is open", "Overlay", "pause_game", "OPENSPM_PAUSE_GAME", true,
                   "The game, its clock, music timing and timers stop while the menu is open and continue\n"
                   "exactly where they were. Off: the game keeps running behind the menu (without input).");

    ImGui::SeparatorText("Fast-forward (turbo)");
    toggle_setting("Turbo hotkey", "Game", "turbo", "OPENSPM_TURBO", true,
                   "Hold the turbo key (Tab, see the keyboard table above) or click the right stick\n"
                   "(gamepad TURBO) to run the game faster; release it for normal speed. The game's clock\n"
                   "runs faster too and continues without a jump. Audio is muted while turbo runs.");
    static const std::vector<Choice> kTurboSpeeds = {
        {"2", "2x"}, {"3", "3x"}, {"4", "4x"}, {"unlimited", "unlimited (as fast as the PC can)"}};
    choice_setting("Turbo speed", "Game", "turbo_speed", "OPENSPM_TURBO_SPEED", kTurboSpeeds, 0,
                   "How fast the game runs while turbo is engaged.");
    static const std::vector<Choice> kTurboModes = {{"hold", "hold the key"}, {"toggle", "press to switch on/off"}};
    choice_setting("Turbo key mode", "Game", "turbo_mode", "OPENSPM_TURBO_MODE", kTurboModes, 0,
                   "hold: turbo while the key is held. toggle: one press starts, the next stops.");

    ImGui::SeparatorText("Gamepad");
    constexpr ImGuiTableFlags kTable = ImGuiTableFlags_RowBg | ImGuiTableFlags_BordersInnerV | ImGuiTableFlags_SizingFixedFit;
    int count = 0;
    SDL_JoystickID *ids = SDL_GetGamepads(&count);
    const SDL_JoystickID first = ids != nullptr && count > 0 ? ids[0] : 0;
    SDL_free(ids);
    SDL_Gamepad *pad = first != 0 ? SDL_GetGamepadFromID(first) : nullptr; // opened by the input poll
    const bool pad_off = !env_value("OPENSPM_PAD").empty() && env_value("OPENSPM_PAD")[0] == '0';
    if (first == 0) {
        ImGui::TextUnformatted("No gamepad detected. Connect one any time (Xbox, PlayStation, Switch Pro ...).");
    } else {
        const char *name = SDL_GetGamepadNameForID(first);
        ImGui::Text("Connected: %s%s", name != nullptr ? name : "gamepad", count > 1 ? " (first of several; used)" : "");
    }
    if (pad_off) ImGui::TextColored(kPendingColor, "The gamepad is disabled for this session.");
    toggle_setting("Use gamepad", "Controller", "enabled", "OPENSPM_PAD", true,
                   "Read the first connected gamepad as the Wii Remote, together with the keyboard.");

    const std::string saved = openspm_ini::get(settings().text, "Controller", "map");
    const std::string running_text = env_value("OPENSPM_PAD_MAP");
    const openspm_ini::pad::Map running = openspm_ini::pad::parse(running_text);
    openspm_ini::pad::Map map = openspm_ini::pad::parse(saved.empty() ? running_text : saved);
    ImGui::TextUnformatted("Buttons -> Wii Remote (D-pad and left stick always move):");
    restart_marker(map != running);
    static const char *const kSlotLabels[openspm_ini::pad::kSlotCount] = {
        "South (Xbox A, PS Cross)", "East (Xbox B, PS Circle)", "West (Xbox X, PS Square)",
        "North (Xbox Y, PS Triangle)", "Start / Options", "Back / Share / View", "LB / L1", "RB / R1",
        "LT / L2", "RT / R2", "Left stick click", "Right stick click"};
    static const char *const kValueLabels[] = {"1", "2 (jump)", "A", "B", "+ (Plus)", "- (Minus)",
                                               "Turbo (fast-forward)", "Shake (Wii Remote shake)", "none"};
    static_assert(std::size(kValueLabels) == openspm_ini::pad::kValues.size());
    struct Live { SDL_GamepadButton button; SDL_GamepadAxis axis; };
    static const Live kLive[openspm_ini::pad::kSlotCount] = {
        {SDL_GAMEPAD_BUTTON_SOUTH, SDL_GAMEPAD_AXIS_INVALID},         {SDL_GAMEPAD_BUTTON_EAST, SDL_GAMEPAD_AXIS_INVALID},
        {SDL_GAMEPAD_BUTTON_WEST, SDL_GAMEPAD_AXIS_INVALID},          {SDL_GAMEPAD_BUTTON_NORTH, SDL_GAMEPAD_AXIS_INVALID},
        {SDL_GAMEPAD_BUTTON_START, SDL_GAMEPAD_AXIS_INVALID},         {SDL_GAMEPAD_BUTTON_BACK, SDL_GAMEPAD_AXIS_INVALID},
        {SDL_GAMEPAD_BUTTON_LEFT_SHOULDER, SDL_GAMEPAD_AXIS_INVALID}, {SDL_GAMEPAD_BUTTON_RIGHT_SHOULDER, SDL_GAMEPAD_AXIS_INVALID},
        {SDL_GAMEPAD_BUTTON_INVALID, SDL_GAMEPAD_AXIS_LEFT_TRIGGER},  {SDL_GAMEPAD_BUTTON_INVALID, SDL_GAMEPAD_AXIS_RIGHT_TRIGGER},
        {SDL_GAMEPAD_BUTTON_LEFT_STICK, SDL_GAMEPAD_AXIS_INVALID},    {SDL_GAMEPAD_BUTTON_RIGHT_STICK, SDL_GAMEPAD_AXIS_INVALID},
    };
    bool changed = false;
    if (ImGui::BeginTable("##padmap", 3, kTable)) {
        for (int slot = 0; slot < openspm_ini::pad::kSlotCount; ++slot) {
            ImGui::PushID(slot);
            ImGui::TableNextRow();
            ImGui::TableSetColumnIndex(0);
            bool held = false;
            if (pad != nullptr) {
                held = kLive[slot].axis != SDL_GAMEPAD_AXIS_INVALID
                           ? SDL_GetGamepadAxis(pad, kLive[slot].axis) > 100 * 128 // backend: trigger > 100/255
                           : SDL_GetGamepadButton(pad, kLive[slot].button);
            }
            if (held) ImGui::TextColored(kPendingColor, "%s", kSlotLabels[slot]);
            else ImGui::TextUnformatted(kSlotLabels[slot]);
            ImGui::TableSetColumnIndex(1);
            ImGui::SetNextItemWidth(ImGui::GetFontSize() * 7.f);
            if (ImGui::BeginCombo("##value", kValueLabels[map[slot]])) {
                for (int v = 0; v < static_cast<int>(std::size(kValueLabels)); ++v) {
                    if (ImGui::Selectable(kValueLabels[v], v == map[slot]) && v != map[slot]) {
                        map[slot] = v;
                        changed = true;
                    }
                }
                ImGui::EndCombo();
            }
            ImGui::TableSetColumnIndex(2);
            if (map[slot] != running[slot]) ImGui::TextDisabled("(now %s)", kValueLabels[running[slot]]);
            ImGui::PopID();
        }
        ImGui::EndTable();
    }
    if (changed) save_setting("Controller", "map", openspm_ini::pad::format(map));
    if (ImGui::Button("Default buttons") && map != openspm_ini::pad::default_map())
        save_setting("Controller", "map", openspm_ini::pad::format(openspm_ini::pad::default_map()));
    ImGui::SetItemTooltip("a=2 (jump), b=B, x=1, y=A, start=+, back=-, right stick click = turbo");
    ImGui::TextDisabled("A held button lights up above. Saved as [Controller] map.");
    ImGui::TextDisabled("Guide / PS / Home button = HOME (this menu). Windows may give it to the Xbox Game Bar or Steam.");

    draw_pointer_and_rumble(pad);
}

// ---- Debug -----------------------------------------------------------------

// Read-only guest state (R8PE01 rev 2 addresses already used by the backend
// and bridge diagnostics). Reads go through Aurora's bounded guest mapper.
inline bool read_guest_be32(std::uint32_t address, std::uint32_t &out) {
    aurora::gx::guest_memory::Span span;
    if (!aurora::gx::guest_memory::map(address, 4u, &span) || span.data == nullptr || span.size < 4u) return false;
    out = (std::uint32_t(span.data[0]) << 24) | (std::uint32_t(span.data[1]) << 16) |
          (std::uint32_t(span.data[2]) << 8) | std::uint32_t(span.data[3]);
    return true;
}
inline bool read_guest_f32(std::uint32_t address, float &out) {
    std::uint32_t bits = 0;
    if (!read_guest_be32(address, bits)) return false;
    std::memcpy(&out, &bits, sizeof out);
    return true;
}

inline void draw_game_state() {
    constexpr std::uint32_t kSeqWork = 0x804D0D60u;   // +0 sequence, +4 stage
    constexpr std::uint32_t kGlobalPtr = 0x8056CB78u; // gp; +4 game frame rate
    constexpr std::uint32_t kMarioPos = 0x804CEE58u + 0x5Cu;
    std::uint32_t seq = 0, stage = 0, gp = 0, fps = 0;
    const bool haveSeq = read_guest_be32(kSeqWork, seq) && read_guest_be32(kSeqWork + 4u, stage);
    if (haveSeq) ImGui::Text("Sequence %u, stage %u%s", seq, stage, seq == 2u ? " (in game)" : "");
    else ImGui::TextDisabled("Sequence: not readable yet");
    if (read_guest_be32(kGlobalPtr, gp) && gp != 0u && read_guest_be32(gp + 4u, fps))
        ImGui::Text("Game frame rate setting: %u", fps);
    float x = 0.f, y = 0.f, z = 0.f;
    if (read_guest_f32(kMarioPos, x) && read_guest_f32(kMarioPos + 4u, y) && read_guest_f32(kMarioPos + 8u, z))
        ImGui::Text("Mario position: %.2f, %.2f, %.2f", x, y, z);
}

inline void draw_debug_tab() {
    ImGui::SeparatorText("Performance");
    draw_frame_readout();
    std::array<float, 240> ordered{};
    const unsigned n = std::min<unsigned>(g_frame_count, ordered.size());
    for (unsigned i = 0; i < n; ++i)
        ordered[i] = g_frame_ms[(g_frame_count - n + i) % g_frame_ms.size()];
    ImGui::PlotLines("##frametimes", ordered.data(), static_cast<int>(n), 0, "frame time (ms)", 0.f, 50.f,
                     ImVec2(-1.f, 90.f));
    bool show_fps = g_show_fps;
    if (ImGui::Checkbox("FPS counter (top-left)", &show_fps)) set_show_fps(show_fps);
    live_marker();

    ImGui::SeparatorText("Capture");
    if (ImGui::Button("Screenshot (F12)")) g_m14_screenshot_requested = true;
    ImGui::SetItemTooltip("Saves the next game frame as a PNG in the screenshots folder.");
    ImGui::SameLine();
    if (ImGui::Button("Open screenshots folder")) {
        std::error_code ec;
        std::filesystem::create_directories("screenshots", ec);
        std::string url = "file:///" + utf8(std::filesystem::absolute("screenshots", ec));
        std::replace(url.begin(), url.end(), '\\', '/');
        if (!SDL_OpenURL(url.c_str())) g_status = std::string("Could not open ") + url + ": " + SDL_GetError();
    }
    if (ImGui::Button("GX frame capture (F8)")) {
        g_renderer.capture_requested = true;
        log_message(OPENSPM_LOG_INFO, "M14 GX capture requested from the OpenSPM menu.");
        g_status = "GX frame capture armed: the next frame's draws go to the log.";
    }
    ImGui::SetItemTooltip("Writes the next frame's GX draw list to the log, for renderer bug reports.");

    ImGui::SeparatorText("Game state (read-only)");
    draw_game_state();

    ImGui::SeparatorText("Files");
    ImGui::TextWrapped("Settings: %s", utf8(settings().path).c_str());
}

// ---- Cheats / Mods / About -------------------------------------------------

// Cheats never write guest memory from here. The menu edits OpenSPM.ini
// [Cheats] and publishes one openspm_cheat_table (include/openspm/cheats.h)
// under g_cheat_mutex; the backend copies it through the renderer export
// openspm_renderer_cheats() and applies it at its once-per-frame present
// safepoint, while the game is paused between frames.
inline std::mutex g_cheat_mutex;
inline openspm_cheat_table g_cheat_table{}; // guarded by g_cheat_mutex
inline bool g_cheat_table_ready = false;    // guarded by g_cheat_mutex

struct CheatUiEntry {
    bool enabled = false;
    std::array<char, 64> name{};
    std::array<char, 2048> codes{};
    std::string error; // parse error; the entry is not applied while set
    std::uint32_t count = 0u;
};

inline bool g_cheat_ui_loaded = false;
inline bool g_cheat_infinite_hp = false;
inline std::uint32_t g_cheat_gift_serial = 0u; // "Give Gold Bar x3" presses (never saved)
inline std::vector<CheatUiEntry> g_cheat_entries;
inline int g_cheat_saved_slots = 0; // codeN keys written so far (to blank removed ones)

// Pure: settings -> table. Entries whose codes do not parse are skipped (and
// reported in `errors` when given); so are entries past the write budget.
inline openspm_cheat_table build_cheat_table(const openspm_ini::cheats::Settings &s,
                                             std::vector<std::string> *errors = nullptr,
                                             std::vector<std::uint32_t> *counts = nullptr) {
    openspm_cheat_table table{};
    table.struct_size = sizeof table;
    if (s.infinite_hp) table.flags |= OPENSPM_CHEAT_INFINITE_HP;
    for (const auto &entry : s.custom) {
        openspm_cheat_write writes[OPENSPM_CHEAT_MAX_WRITES];
        std::uint32_t count = 0u;
        char error[128] = "";
        bool ok = openspm_cheat_parse_gecko(entry.codes.c_str(), writes, OPENSPM_CHEAT_MAX_WRITES, &count, error,
                                            sizeof error) != 0;
        if (ok && entry.enabled && table.write_count + count > OPENSPM_CHEAT_MAX_WRITES) {
            ok = false;
            std::snprintf(error, sizeof error, "over the %u-code total for enabled cheats", OPENSPM_CHEAT_MAX_WRITES);
        }
        if (errors != nullptr) errors->push_back(ok ? std::string{} : std::string(error));
        if (counts != nullptr) counts->push_back(ok ? count : 0u);
        if (!ok || !entry.enabled) continue;
        for (std::uint32_t i = 0; i < count; ++i) table.writes[table.write_count++] = writes[i];
    }
    return table;
}

inline void publish_cheat_table(const openspm_cheat_table &table) {
    std::lock_guard<std::mutex> lock(g_cheat_mutex);
    const std::uint32_t gift = g_cheat_table.gift_serial;
    g_cheat_table = table;
    g_cheat_table.gift_serial = gift > table.gift_serial ? gift : table.gift_serial;
    g_cheat_table_ready = true;
}

// "Give Gold Bar x3": one more press for the backend (it adds the item once).
inline void request_cheat_gift() {
    std::lock_guard<std::mutex> lock(g_cheat_mutex);
    g_cheat_table.gift_serial = ++g_cheat_gift_serial;
}

// Renderer export body (any thread). The first call loads OpenSPM.ini itself,
// so saved cheats apply without opening the menu.
inline int copy_cheat_table(openspm_cheat_table *out) {
    if (out == nullptr) return 0;
    std::lock_guard<std::mutex> lock(g_cheat_mutex);
    if (!g_cheat_table_ready) {
        g_cheat_table = build_cheat_table(openspm_ini::cheats::load(read_text(settings().path)));
        g_cheat_table_ready = true;
        if (g_cheat_table.flags != 0u || g_cheat_table.write_count != 0u) {
            char line[160];
            std::snprintf(line, sizeof line, "OpenSPM cheats loaded from OpenSPM.ini: flags=0x%X, %u custom writes.",
                          static_cast<unsigned>(g_cheat_table.flags), static_cast<unsigned>(g_cheat_table.write_count));
            log_message(OPENSPM_LOG_INFO, line);
        }
    }
    if (g_cheat_table.flags == 0u && g_cheat_table.write_count == 0u && g_cheat_table.gift_serial == 0u) return 0;
    *out = g_cheat_table;
    return 1;
}

inline openspm_ini::cheats::Settings cheat_settings_from_ui() {
    openspm_ini::cheats::Settings s;
    s.infinite_hp = g_cheat_infinite_hp;
    for (const auto &e : g_cheat_entries)
        s.custom.push_back({e.enabled, std::string(e.name.data()), std::string(e.codes.data())});
    return s;
}

// Revalidates every entry and publishes the table the backend applies.
inline void refresh_cheats() {
    std::vector<std::string> errors;
    std::vector<std::uint32_t> counts;
    const openspm_cheat_table table = build_cheat_table(cheat_settings_from_ui(), &errors, &counts);
    for (size_t i = 0; i < g_cheat_entries.size(); ++i) {
        g_cheat_entries[i].error = errors[i];
        g_cheat_entries[i].count = counts[i];
    }
    publish_cheat_table(table);
}

inline void load_cheat_ui() {
    const auto s = openspm_ini::cheats::load(settings().text);
    g_cheat_infinite_hp = s.infinite_hp;
    g_cheat_entries.clear();
    for (const auto &entry : s.custom) {
        CheatUiEntry e;
        e.enabled = entry.enabled;
        std::snprintf(e.name.data(), e.name.size(), "%s", entry.name.c_str());
        std::snprintf(e.codes.data(), e.codes.size(), "%s",
                      openspm_ini::cheats::join_words(entry.codes, true).c_str());
        g_cheat_entries.push_back(e);
    }
    g_cheat_saved_slots = static_cast<int>(g_cheat_entries.size());
    g_cheat_ui_loaded = true;
    refresh_cheats();
}

inline void save_custom_cheats() {
    const int slots = std::max<int>(g_cheat_saved_slots, static_cast<int>(g_cheat_entries.size()));
    for (int i = 0; i < slots; ++i) {
        const std::string value =
            i < static_cast<int>(g_cheat_entries.size())
                ? openspm_ini::cheats::format_entry({g_cheat_entries[i].enabled, g_cheat_entries[i].name.data(),
                                                     g_cheat_entries[i].codes.data()})
                : std::string{};
        save_setting("Cheats", openspm_ini::cheats::key(i).c_str(), value);
    }
    g_cheat_saved_slots = static_cast<int>(g_cheat_entries.size());
    refresh_cheats();
}

inline void draw_cheats_tab() {
    if (!g_cheat_ui_loaded) load_cheat_ui();
    ImGui::TextWrapped("Cheats change the game's memory once per frame, between frames. Everything is off by "
                       "default and does nothing while unchecked. They change your save if you save with them on.");
    ImGui::SeparatorText("Built-in");
    if (ImGui::Checkbox("Infinite HP", &g_cheat_infinite_hp)) {
        save_setting("Cheats", "infinite_hp", g_cheat_infinite_hp ? "1" : "0");
        refresh_cheats();
    }
    ImGui::SetItemTooltip("Refills HP to max HP every frame in game. A hit that takes all your HP at once still "
                          "counts (HP 0 is left to the game).");
    live_marker();
    if (ImGui::Button("Give Gold Bar x3")) request_cheat_gift();
    ImGui::SetItemTooltip("Adds one Gold Bar x3 to your items (in a level), the game's own way: sell it in a shop "
                          "for coins. Nothing else about coins is changed, so shops keep working.");
    {
        constexpr std::uint32_t kPouch = OPENSPM_CHEAT_POUCH;
        std::uint32_t seq = 0, hp = 0, max_hp = 0, coins = 0;
        if (read_guest_be32(OPENSPM_CHEAT_SEQ_WORK, seq) && seq == OPENSPM_CHEAT_SEQ_GAME &&
            read_guest_be32(kPouch + OPENSPM_CHEAT_POUCH_HP, hp) &&
            read_guest_be32(kPouch + OPENSPM_CHEAT_POUCH_MAX_HP, max_hp) &&
            read_guest_be32(kPouch + OPENSPM_CHEAT_POUCH_COINS, coins))
            ImGui::TextDisabled("Now: HP %d/%d, coins %d", static_cast<int>(hp), static_cast<int>(max_hp),
                                static_cast<int>(coins));
        else ImGui::TextDisabled("Built-in cheats act only in game (not on the title or file select).");
    }

    ImGui::SeparatorText("Custom codes (Gecko 04)");
    ImGui::TextWrapped("One code per line: 04XXXXXX YYYYYYYY writes YYYYYYYY at 80XXXXXX every frame "
                       "(05 for 81XXXXXX). Only data writes work: this is a recompiled game, so codes that "
                       "patch game code do nothing. R8PE01 rev 2 (US) addresses only.");
    bool save = false;
    int remove = -1;
    for (size_t i = 0; i < g_cheat_entries.size(); ++i) {
        CheatUiEntry &e = g_cheat_entries[i];
        ImGui::PushID(static_cast<int>(i));
        ImGui::Separator();
        if (ImGui::Checkbox("##on", &e.enabled)) save = true;
        ImGui::SetItemTooltip("Saved as [Cheats] %s", openspm_ini::cheats::key(static_cast<int>(i)).c_str());
        ImGui::SameLine();
        ImGui::SetNextItemWidth(ImGui::GetContentRegionAvail().x - ImGui::GetFontSize() * 5.f);
        ImGui::InputTextWithHint("##name", "name", e.name.data(), e.name.size());
        if (ImGui::IsItemDeactivatedAfterEdit()) save = true;
        ImGui::SameLine();
        if (ImGui::SmallButton("Remove")) remove = static_cast<int>(i);
        ImGui::InputTextMultiline("##codes", e.codes.data(), e.codes.size(),
                                  ImVec2(-1.f, ImGui::GetTextLineHeight() * 4.f), ImGuiInputTextFlags_CharsUppercase);
        if (ImGui::IsItemDeactivatedAfterEdit()) save = true;
        if (!e.error.empty()) ImGui::TextColored(kPendingColor, "Not applied: %s", e.error.c_str());
        else if (e.count == 0u) ImGui::TextDisabled("No codes yet.");
        else ImGui::TextDisabled("%u code%s%s", static_cast<unsigned>(e.count), e.count == 1u ? "" : "s",
                                 e.enabled ? ", active" : ", off");
        ImGui::PopID();
    }
    if (remove >= 0) {
        g_cheat_entries.erase(g_cheat_entries.begin() + remove);
        save = true;
    }
    if (static_cast<int>(g_cheat_entries.size()) < openspm_ini::cheats::kMaxCustom) {
        if (ImGui::Button("Add code")) {
            CheatUiEntry e;
            std::snprintf(e.name.data(), e.name.size(), "Code %d", static_cast<int>(g_cheat_entries.size()) + 1);
            g_cheat_entries.push_back(e);
            save = true;
        }
    } else {
        ImGui::TextDisabled("%d codes is the limit.", openspm_ini::cheats::kMaxCustom);
    }
    if (save) save_custom_cheats();
}

inline std::vector<std::string> g_mod_files; // mods/*.dll, scanned when the menu opens
// mods/<dll name>.toml (include/openspm/mod_manifest.h), by DLL file name:
// parsed info, or why it does not parse (the loader refuses such a mod).
struct ModInfo {
    bool present = false, ok = false;
    openspm_mod_manifest manifest{};
    std::string why;
};
inline std::map<std::string, ModInfo> g_mod_info;

inline void scan_mods() {
    g_mod_files.clear();
    g_mod_info.clear();
    std::error_code ec;
    for (std::filesystem::directory_iterator it(app_dir() / "mods", ec), end; !ec && it != end; it.increment(ec)) {
        const std::filesystem::path &path = it->path();
        std::string ext = utf8(path.extension());
        if (!it->is_regular_file(ec) || !iequal(ext, ".dll")) continue;
        const std::string name = utf8(path.filename());
        g_mod_files.push_back(name);
        ModInfo info;
        std::filesystem::path toml = path;
        toml.replace_extension(".toml");
        std::error_code exists_ec;
        if (std::filesystem::is_regular_file(toml, exists_ec)) {
            std::string text = read_text(toml);
            if (text.size() > 16384u) text.resize(16384u);
            char why[256] = {};
            info.present = true;
            info.ok = openspm_manifest_parse(text.c_str(), &info.manifest, why, sizeof why) != 0;
            info.why = why;
        }
        g_mod_info[name] = info;
    }
    std::sort(g_mod_files.begin(), g_mod_files.end());
}

inline void draw_mods_tab() {
    toggle_setting("Load mods", "Mods", "enabled", "OPENSPM_MODS", false,
                   "Load the mod DLLs from the mods folder next to OpenSPMNative.exe (docs/MODDING.md).\n"
                   "Mods run native code: only install mods you trust.");
    const std::filesystem::path folder = app_dir() / "mods";
    ImGui::SeparatorText("Installed");
    if (g_mod_files.empty()) {
        ImGui::TextWrapped("No mod DLLs in %s", utf8(folder).c_str());
    } else {
        for (const std::string &name : g_mod_files) {
            bool on = openspm_ini::get(mod_settings().text, "Mods", name) != "0";
            const ModInfo &info = g_mod_info[name];
            std::string label = name;
            if (info.ok && info.manifest.name[0] != '\0')
                label = std::string(info.manifest.name) + (info.manifest.version[0] ? " " : "") + info.manifest.version +
                        "##" + name;
            if (ImGui::Checkbox(label.c_str(), &on)) save_to(mod_settings(), "Mods", name.c_str(), on ? "1" : "0");
            ImGui::SetItemTooltip("%s\nSaved as [Mods] %s in %s", name.c_str(), name.c_str(), utf8(mod_settings().path).c_str());
            ImGui::SameLine();
            ImGui::TextDisabled("applies on restart");
            ImGui::Indent();
            if (info.ok) {
                if (info.manifest.author[0] != '\0') ImGui::TextDisabled("by %s", info.manifest.author);
                if (info.manifest.description[0] != '\0') ImGui::TextWrapped("%s", info.manifest.description);
            } else if (info.present) {
                ImGui::TextColored(ImVec4(1.f, 0.5f, 0.3f, 1.f), "Info file problem (the mod will not load): %s",
                                   info.why.c_str());
            } else {
                ImGui::TextDisabled("no info file (%s)", name.c_str());
            }
            ImGui::Unindent();
        }
    }
    if (ImGui::SmallButton("Rescan")) scan_mods();
    ImGui::SameLine();
    ImGui::TextDisabled("%s", utf8(folder).c_str());
}

// ---- Audio (experimental, docs/AUDIO-PLAN.md) ------------------------------
// The stream lives in openspm_renderer_aurora_v6.cpp (openspm_renderer_audio_push);
// it applies a volume change from here at its next push and reports its state.
struct AudioStatus {
    bool open = false;
    bool failed = false;
    unsigned queued_ms = 0;
    unsigned underruns = 0;
    unsigned resyncs = 0;
    int trim_ppm = 0;
};
inline AudioStatus g_audio_status;
inline int g_audio_volume_live = -1; // 0..100 when the slider moved, else -1

inline int audio_volume_setting() {
    std::string value = openspm_ini::get(settings().text, "Audio", "volume");
    if (value.empty()) value = env_value("OPENSPM_AUDIO_VOLUME");
    if (value.empty()) return 100;
    return std::clamp(std::atoi(value.c_str()), 0, 100);
}

inline void draw_audio_tab() {
    toggle_setting("Game audio (experimental)", "Audio", "enabled", "OPENSPM_AUDIO", false,
                   "Music and sound effects through OpenSPM's AX HLE mixer (docs/AUDIO-PLAN.md).\n"
                   "Off (default): the game runs silent exactly as before. Launcher flag: audio.");
    static int volume = -1;
    if (volume < 0) volume = audio_volume_setting();
    ImGui::SetNextItemWidth(ImGui::GetFontSize() * 14.f);
    ImGui::SliderInt("Volume", &volume, 0, 100, "%d%%");
    if (ImGui::IsItemEdited()) g_audio_volume_live = volume;
    if (ImGui::IsItemDeactivatedAfterEdit()) save_setting("Audio", "volume", std::to_string(volume));
    ImGui::SetItemTooltip("Output volume. Saved as [Audio] volume.");
    live_marker();
    ImGui::SeparatorText("Status");
    if (g_audio_status.failed)
        ImGui::TextColored(kPendingColor, "No audio device could be opened (see the log).");
    else if (!g_audio_status.open)
        ImGui::TextDisabled(env_value("OPENSPM_AUDIO") == "1" ? "Waiting for the game's first audio frame."
                                                              : "Audio is off.");
    else
        ImGui::Text("Playing: %u ms queued, %u underruns, %u resyncs, rate trim %+d ppm", g_audio_status.queued_ms,
                    g_audio_status.underruns, g_audio_status.resyncs, g_audio_status.trim_ppm);
}

inline void draw_about_tab() {
    ImGui::TextUnformatted("OpenSPM: a native PC port of Super Paper Mario.");
    ImGui::TextUnformatted("Bring your own game; no Nintendo code or assets are included.");
    ImGui::SeparatorText("Hotkeys");
    ImGui::TextUnformatted("F1 / HOME  OpenSPM menu (Esc closes)   F11  fullscreen");
    ImGui::TextUnformatted("F12 screenshot                  F8   GX frame capture (debug)");
}

// ---- frame hooks -----------------------------------------------------------

inline void toggle() {
    if (!enabled()) return;
    g_open = !g_open;
    if (g_open) {
        reload_settings();
        scan_refresh_rates();
        scan_mods();
        refresh_pack_view();
    }
    log_message(OPENSPM_LOG_INFO, g_open ? "OpenSPM menu opened (F1)." : "OpenSPM menu closed (F1).");
}

// ImGui's SDL backend re-shows the OS cursor every frame, which undid the
// fullscreen cursor hiding (m14_update_cursor_for_window). Let ImGui manage the
// cursor only while the menu is open; on close restore the window's rule.
inline void sync_cursor(SDL_Window *window) {
    static bool s_was_open = false;
    ImGuiIO &io = ImGui::GetIO();
    if (g_open) io.ConfigFlags &= ~ImGuiConfigFlags_NoMouseCursorChange;
    else io.ConfigFlags |= ImGuiConfigFlags_NoMouseCursorChange;
    if (g_open && !s_was_open) SDL_ShowCursor();
    if (!g_open && s_was_open) m14_update_cursor_for_window(window);
    s_was_open = g_open;
}

// Controller / keyboard navigation (D-pad or stick, A, B; arrows, Enter,
// Space, Esc): only while the menu is open AND the game is paused behind it
// ([Overlay] pause_game, default on). With pause off the game keeps reading
// the same keys and buttons, so the menu stays mouse-only then.
inline bool g_nav_active = false;
inline int g_tab = 0, g_tab_request = -1;
inline constexpr int kTabCount = 9;

inline bool menu_pauses_game() {
    const std::string v = env_value("OPENSPM_PAUSE_GAME");
    return v.empty() || v[0] != '0';
}

inline void sync_navigation() {
    static bool s_was_open = false;
    ImGuiIO &io = ImGui::GetIO();
    const ImGuiConfigFlags nav = ImGuiConfigFlags_NavEnableGamepad | ImGuiConfigFlags_NavEnableKeyboard;
    g_nav_active = g_open && menu_pauses_game();
    if (g_nav_active) io.ConfigFlags |= nav;
    else io.ConfigFlags &= ~nav;
    if (g_open && !s_was_open && g_nav_active) ImGui::SetNextWindowFocus(); // the pad drives it at once
    s_was_open = g_open;
    if (!g_nav_active) return;
    // LB / RB: previous / next tab; Start: close.
    if (ImGui::IsKeyPressed(ImGuiKey_GamepadL1, false)) g_tab_request = (g_tab + kTabCount - 1) % kTabCount;
    if (ImGui::IsKeyPressed(ImGuiKey_GamepadR1, false)) g_tab_request = (g_tab + 1) % kTabCount;
    if (ImGui::IsKeyPressed(ImGuiKey_GamepadStart, false)) {
        g_open = false;
        log_message(OPENSPM_LOG_INFO, "OpenSPM menu closed (gamepad Start).");
    }
}

// A tab of the menu; remembers which is showing so LB / RB can move from it.
inline bool begin_tab(const char *label, int index) {
    const ImGuiTabItemFlags flags = g_tab_request == index ? ImGuiTabItemFlags_SetSelected : 0;
    if (!ImGui::BeginTabItem(label, nullptr, flags)) return false;
    g_tab = index;
    if (g_tab_request == index) g_tab_request = -1;
    return true;
}

// Call after aurora_end_frame: applies settings that must not change mid-frame.
inline void after_frame() {
    if (g_pending_policy < 0) return;
    AuroraSetStretchAspect(g_pending_stretch_aspect);
    AuroraSetViewportPolicy(static_cast<AuroraViewportPolicy>(g_pending_policy));
    log_message(OPENSPM_LOG_INFO, g_pending_stretch_aspect > 0.f ? "OpenSPM menu: aspect 16:9 (widescreen)."
                                  : g_pending_policy == AURORA_VIEWPORT_STRETCH ? "OpenSPM menu: aspect stretch."
                                                                                : "OpenSPM menu: aspect 4:3.");
    g_pending_policy = -1;
}

// Call once per presented frame, between Aurora's begin and end frame.
inline void draw() {
    note_present();
    draw_fps_counter(); // FPS ([Overlay] show_fps) and/or the turbo indicator; nothing when both are off
    draw_mod_overlay(); // mod panels / notifications (openspm.overlay); nothing without mods
    SDL_Window *window = main_window();
    if (g_open && ImGui::IsKeyPressed(ImGuiKey_Escape, false)) {
        g_open = false;
        log_message(OPENSPM_LOG_INFO, "OpenSPM menu closed (Esc).");
    }
    sync_cursor(window);
    sync_navigation();
    if (!g_open) return;
    int h = 720;
    if (window != nullptr) SDL_GetWindowSize(window, nullptr, &h);
    // Text grows with the window height but never shrinks below 1 (smaller is
    // unreadable); in a small window the menu is kept inside it every frame
    // and scrolls. Its remembered size/position may come from a bigger window.
    ImGui::GetIO().FontGlobalScale = std::clamp(static_cast<float>(h) / 720.0f, 1.0f, 4.0f);
    const float scale = ImGui::GetIO().FontGlobalScale;
    const ImGuiViewport *viewport = ImGui::GetMainViewport();
    const ImVec2 area(std::max(160.f, viewport->WorkSize.x - 16.f), std::max(120.f, viewport->WorkSize.y - 16.f));
    ImGui::SetNextWindowPos(ImVec2(viewport->WorkPos.x + viewport->WorkSize.x * 0.5f,
                                   viewport->WorkPos.y + viewport->WorkSize.y * 0.5f),
                            ImGuiCond_FirstUseEver, ImVec2(0.5f, 0.5f));
    ImGui::SetNextWindowSize(ImVec2(std::min(620.f * scale, area.x), std::min(520.f * scale, area.y)),
                             ImGuiCond_FirstUseEver);
    ImGui::SetNextWindowSizeConstraints(ImVec2(std::min(240.f, area.x), std::min(160.f, area.y)), area);
    bool open = g_open;
    const bool began = ImGui::Begin("OpenSPM###openspm_menu", &open, ImGuiWindowFlags_NoCollapse);
    {
        const ImVec2 pos = ImGui::GetWindowPos(), size = ImGui::GetWindowSize();
        const float min_x = viewport->WorkPos.x + 8.f, min_y = viewport->WorkPos.y + 8.f;
        const float max_x = std::max(min_x, viewport->WorkPos.x + viewport->WorkSize.x - 8.f - size.x);
        const float max_y = std::max(min_y, viewport->WorkPos.y + viewport->WorkSize.y - 8.f - size.y);
        const ImVec2 fitted(std::clamp(pos.x, min_x, max_x), std::clamp(pos.y, min_y, max_y));
        if (fitted.x != pos.x || fitted.y != pos.y) ImGui::SetWindowPos(fitted);
    }
    if (began) {
        if (g_nav_active)
            ImGui::TextDisabled("Controller: D-pad/stick move, A select, B back, LB/RB tabs, Start closes.");
        if (ImGui::BeginTabBar("##openspm_tabs")) {
            if (begin_tab("Display", 0)) { draw_display_tab(); ImGui::EndTabItem(); }
            if (begin_tab("Graphics", 1)) { draw_graphics_tab(); ImGui::EndTabItem(); }
            if (begin_tab("Texture Packs", 2)) { draw_texture_packs_tab(); ImGui::EndTabItem(); }
            if (begin_tab("Controls", 3)) { draw_controls_tab(); ImGui::EndTabItem(); }
            if (begin_tab("Debug", 4)) { draw_debug_tab(); ImGui::EndTabItem(); }
            if (begin_tab("Cheats", 5)) { draw_cheats_tab(); ImGui::EndTabItem(); }
            if (begin_tab("Audio", 6)) { draw_audio_tab(); ImGui::EndTabItem(); }
            if (begin_tab("Mods", 7)) { draw_mods_tab(); ImGui::EndTabItem(); }
            if (begin_tab("About", 8)) { draw_about_tab(); ImGui::EndTabItem(); }
            ImGui::EndTabBar();
        }
        if (!g_status.empty()) {
            ImGui::Separator();
            ImGui::TextWrapped("%s", g_status.c_str());
        }
    }
    ImGui::End();
    if (!open) {
        g_open = false;
        log_message(OPENSPM_LOG_INFO, "OpenSPM menu closed.");
    }
}

} // namespace openspm_overlay
