/*
 * M14 dev7 producer-boundary pacing diagnostic + completed-copy presentation
 * wrapper.
 *
 * Runtime 042836 restored spinner/door correctness after removing the unsafe
 * render-worker callback profiler. Real swapchain Present() remains cheap,
 * frame/staging acquisition and bbox readback remain cheap, yet scene-dependent
 * host-present p95 grows to ~30-35 ms while Wii retrace delivery stays ~60 Hz.
 *
 * Later profiling isolated a second architectural problem: ordinary PC
 * presentation was gated on the recompiled Revolution OS scheduler reaching its
 * no-runnable-thread idle PC. Busy scenes can keep guest threads runnable long
 * after a complete GXCopyDisp has already been parsed, so VI/retrace delivery can
 * remain 60 Hz while the host window receives frames at ~30 ms intervals.
 *
 * Keep the proven dev7 renderer body byte-for-byte in
 * openspm_renderer_aurora_dev7_base.cpp. The wrapped DrawDone still performs the
 * exact proven parser drain first. Only after that drain completes, inspect
 * Aurora's already-latched original-Wii display-copy flag. If there is no copy,
 * do nothing: intermediate DrawDone boundaries remain invisible exactly as dev4
 * intended. If a real display copy is ready, end/present that completed host
 * frame immediately instead of waiting for guest scheduler idle. The existing
 * scheduler-idle PRESENT_SAFEPOINT remains intact as a fallback for any display
 * copy not consumed here.
 *
 * This is deliberately not the rejected "present every DrawDone" behavior:
 * GXDrawDone alone still never selects a host frame; a parsed GXCopyDisp is
 * required. No FIFO ordering, parser, guest-memory, callback, simulation clock,
 * display-list or guest-array lifetime semantics change.
 */

#include <array>
#include <chrono>
#include <cstdint>
#include <cstdio>

#pragma push_macro("openspm_renderer_query")
#undef openspm_renderer_query
#define openspm_renderer_query openspm_renderer_query_dev7_syncdiag_internal
#include "openspm_renderer_aurora_dev7_base.cpp"
#undef openspm_renderer_query
#pragma pop_macro("openspm_renderer_query")

#include "display_pacing.hpp"
#include "output_aspect.hpp"

#include <SDL3/SDL_stdinc.h>
#include <SDL3/SDL_video.h>
#include <dolphin/gx/GXAurora.h>

#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

// OpenSPM M14 copy-triggered frame capture (defined in Aurora GXFrameBuffer.cpp,
// apply_efb_copy_ram_writeback_patch.py). OPENSPM_M14_COPY_CAPTURE=WxH arms the
// F8 GX capture after a frame that made a texture copy of that logical size.
namespace aurora::gx::raw {
bool m14_take_copy_capture_request() noexcept;
bool m14_save_screenshot(const char *path) noexcept;
// SPM snapshot slots (star door, flip card) refresh after every FIFO sync.
void m14_snapshot_sync() noexcept;
void m14_flush_snapshot_dumps() noexcept; // OPENSPM_M14_SNAPSHOT_DUMP=1
bool m14_take_capture_arm_request() noexcept; // OPENSPM_M14_CAPTURE_ON_SNAPSHOT=1
} // namespace aurora::gx::raw

#include <ctime>
#include <filesystem>

#include "openspm_overlay.hpp"

#include <SDL3/SDL_gamepad.h>
#include <SDL3/SDL_audio.h>
#include <SDL3/SDL_init.h>

#include <algorithm>

#include "openspm/audio_out.h"

/* flipcapture: the backend calls this when the flip button (Wii A) is first
   pressed in game; the flip starts, captures Mario's card and bursts bbox reads
   in the next frame. Arms one long capture (default 12000 draws, ~3 frames). */
extern "C" OPENSPM_EXPORT void OPENSPM_CALL openspm_renderer_arm_debug_capture(unsigned draws) {
    static bool armed = false;
    if (armed) return;
    armed = true;
    if (const char *value = std::getenv("OPENSPM_M14_CAPTURE_DRAWS"); value != nullptr && *value != '\0')
        draws = static_cast<unsigned>(std::strtoul(value, nullptr, 10));
    if (draws < 256u || draws > 60000u) draws = 12000u;
    aurora::gx::fifo::audit::arm_seqload_capture(draws);
    g_m14_bbox_trace_budget = 2048u;
    char line[160];
    std::snprintf(line, sizeof line, "M14 input-triggered GX capture armed (flip button); next %u draws.", draws);
    log_message(OPENSPM_LOG_INFO, line);
}

/* OpenSPM gamepad via SDL (PS4/PS5/Switch/Xbox and more, SDL's controller
   database). The backend reads the keyboard itself and polls this export once
   per game frame (backend/native/openspm_pad_xinput.h), falling back to XInput
   when it is missing. Buttons come back in XInput bit layout (A=south, B=east,
   X=west, Y=north). Sticks are -32768..32767 with up positive; triggers 0..255.
   Returns 1 when a gamepad is connected. Same thread as the SDL event pump. */
namespace {
SDL_Gamepad *g_m14_pad = nullptr;
bool g_m14_pad_rumbling = false;

/* The first connected gamepad, opened on demand (shared by the button poll,
   the right-stick pointer and rumble). */
SDL_Gamepad *m14_gamepad() {
    static bool logged = false;
    SDL_Gamepad *&pad = g_m14_pad;
    if (pad != nullptr && !SDL_GamepadConnected(pad)) {
        SDL_CloseGamepad(pad);
        pad = nullptr;
        logged = false;
        log_message(OPENSPM_LOG_INFO, "OpenSPM gamepad: SDL controller disconnected.");
    }
    if (pad == nullptr) {
        int count = 0;
        SDL_JoystickID *ids = SDL_GetGamepads(&count);
        if (ids != nullptr && count > 0) pad = SDL_OpenGamepad(ids[0]);
        SDL_free(ids);
        if (pad == nullptr) return nullptr;
    }
    if (!logged) {
        logged = true;
        g_m14_pad_rumbling = false;
        char line[256];
        const char *name = SDL_GetGamepadName(pad);
        std::snprintf(line, sizeof line, "OpenSPM gamepad: SDL controller connected: %s", name != nullptr ? name : "?");
        log_message(OPENSPM_LOG_INFO, line);
    }
    return pad;
}
} // namespace

extern "C" OPENSPM_EXPORT int OPENSPM_CALL openspm_renderer_gamepad_state(unsigned *buttons, int *lx, int *ly,
                                                                          unsigned *lt, unsigned *rt) {
    if (buttons == nullptr || lx == nullptr || ly == nullptr || lt == nullptr || rt == nullptr) return 0;
    *buttons = 0u;
    *lx = *ly = 0;
    *lt = *rt = 0u;
    SDL_Gamepad *pad = m14_gamepad();
    if (pad == nullptr) return 0;
    struct Map { SDL_GamepadButton sdl; unsigned xi; };
    static const Map maps[] = {
        {SDL_GAMEPAD_BUTTON_DPAD_UP, 0x0001u}, {SDL_GAMEPAD_BUTTON_DPAD_DOWN, 0x0002u},
        {SDL_GAMEPAD_BUTTON_DPAD_LEFT, 0x0004u}, {SDL_GAMEPAD_BUTTON_DPAD_RIGHT, 0x0008u},
        {SDL_GAMEPAD_BUTTON_START, 0x0010u}, {SDL_GAMEPAD_BUTTON_BACK, 0x0020u},
        {SDL_GAMEPAD_BUTTON_LEFT_STICK, 0x0040u}, {SDL_GAMEPAD_BUTTON_RIGHT_STICK, 0x0080u},
        {SDL_GAMEPAD_BUTTON_LEFT_SHOULDER, 0x0100u}, {SDL_GAMEPAD_BUTTON_RIGHT_SHOULDER, 0x0200u},
        {SDL_GAMEPAD_BUTTON_GUIDE, 0x0400u}, // Wii HOME -> the OpenSPM menu (never the game)
        {SDL_GAMEPAD_BUTTON_SOUTH, 0x1000u}, {SDL_GAMEPAD_BUTTON_EAST, 0x2000u},
        {SDL_GAMEPAD_BUTTON_WEST, 0x4000u}, {SDL_GAMEPAD_BUTTON_NORTH, 0x8000u},
    };
    for (const auto &m : maps)
        if (SDL_GetGamepadButton(pad, m.sdl)) *buttons |= m.xi;
    *lx = SDL_GetGamepadAxis(pad, SDL_GAMEPAD_AXIS_LEFTX);
    const int y = SDL_GetGamepadAxis(pad, SDL_GAMEPAD_AXIS_LEFTY); // SDL: down positive
    *ly = y == -32768 ? 32767 : -y;
    *lt = static_cast<unsigned>(std::max<int>(0, SDL_GetGamepadAxis(pad, SDL_GAMEPAD_AXIS_LEFT_TRIGGER))) / 128u;
    *rt = static_cast<unsigned>(std::max<int>(0, SDL_GetGamepadAxis(pad, SDL_GAMEPAD_AXIS_RIGHT_TRIGGER))) / 128u;
    return 1;
}

/* Wii Remote pointer and rumble (include/openspm/pointer_input.h). The game
   image rectangle inside the window: Aurora presents its EFB (AuroraGetRenderSize,
   already fitted to 4:3 / 16:9 by the aspect policy) scaled to fit the window
   surface and centred, the rest are black bars. Computed on every call, so it
   follows resizes, F11 and live aspect changes. */
extern "C" OPENSPM_EXPORT int OPENSPM_CALL openspm_renderer_image_rect(float *left, float *top, float *width,
                                                                      float *height) {
    if (left == nullptr || top == nullptr || width == nullptr || height == nullptr) return 0;
    SDL_Window *window = openspm_overlay::main_window();
    int surface_w = 0, surface_h = 0;
    u32 content_w = 0, content_h = 0;
    if (window == nullptr || !SDL_GetWindowSizeInPixels(window, &surface_w, &surface_h) || surface_w <= 0 ||
        surface_h <= 0)
        return 0;
    AuroraGetRenderSize(&content_w, &content_h);
    if (content_w == 0u || content_h == 0u) return 0;
    const openspm_image_rect rect = openspm_image_rect_fit(surface_w, surface_h, content_w, content_h);
    *left = rect.left;
    *top = rect.top;
    *width = rect.width;
    *height = rect.height;
    return 1;
}

/* openspm.overlay version 2 boxes: drawn over the game picture (the image
   rectangle above, as fractions of the window) after the overlay. */
static void m14_draw_mod_boxes() {
    float left = 0.f, top = 0.f, width = 0.f, height = 0.f;
    if (!openspm_renderer_image_rect(&left, &top, &width, &height)) return;
    const ImVec2 display = ImGui::GetIO().DisplaySize;
    openspm_overlay::draw_mod_boxes(ImVec2(left * display.x, top * display.y),
                                    ImVec2(width * display.x, height * display.y));
}

extern "C" OPENSPM_EXPORT int OPENSPM_CALL openspm_renderer_overlay_boxes(const char *layer_id,
                                                                         const openspm_overlay_box *boxes,
                                                                         uint32_t count) {
    return openspm_overlay::mod_overlay_set_boxes(layer_id, boxes, count);
}

extern "C" OPENSPM_EXPORT int OPENSPM_CALL openspm_renderer_pointer_stick(float *vx, float *vy) {
    if (vx == nullptr || vy == nullptr) return 0;
    *vx = *vy = 0.f;
    const openspm_overlay::ControllerTuning &tuning = openspm_overlay::controller_tuning();
    if (!tuning.stick_pointer) return 0;
    SDL_Gamepad *pad = m14_gamepad();
    if (pad == nullptr) return 0;
    const int rx = SDL_GetGamepadAxis(pad, SDL_GAMEPAD_AXIS_RIGHTX);
    const int ry = SDL_GetGamepadAxis(pad, SDL_GAMEPAD_AXIS_RIGHTY); // SDL: down positive
    return openspm_stick_pointer_velocity(rx, ry == -32768 ? 32767 : -ry, tuning.pointer_speed,
                                          tuning.pointer_deadzone, vx, vy);
}

/* Called once per game frame with the game's Wii Remote motor state. While on,
   the motors are re-armed for 250 ms each frame, so a stalled or paused game
   (the F1 menu pause holds the frame loop) stops rumbling on its own. */
extern "C" OPENSPM_EXPORT void OPENSPM_CALL openspm_renderer_gamepad_rumble(unsigned on) {
    static bool logged = false;
    const openspm_overlay::ControllerTuning &tuning = openspm_overlay::controller_tuning();
    const unsigned intensity = tuning.rumble ? openspm_rumble_intensity(tuning.rumble_strength) : 0u;
    const bool want = on != 0u && intensity != 0u && !openspm_overlay::g_open;
    if (!want && !g_m14_pad_rumbling) return;
    SDL_Gamepad *pad = m14_gamepad();
    if (pad == nullptr) {
        g_m14_pad_rumbling = false;
        return;
    }
    const Uint16 level = static_cast<Uint16>(want ? intensity : 0u);
    const bool ok = SDL_RumbleGamepad(pad, level, level, want ? 250u : 0u);
    if (want && !logged) {
        logged = true;
        log_message(OPENSPM_LOG_INFO, ok ? "OpenSPM gamepad: rumble active (the game's Wii Remote motor)."
                                         : "OpenSPM gamepad: this controller reports no rumble support.");
    }
    g_m14_pad_rumbling = want;
}

/* OpenSPM cheats (include/openspm/cheats.h): the backend copies the F1 menu's
   cheat table once per frame at its present safepoint and applies it there.
   Returns 0 when every cheat is off. The renderer never writes guest memory. */
extern "C" OPENSPM_EXPORT int OPENSPM_CALL openspm_renderer_cheats(openspm_cheat_table *out) {
    return openspm_overlay::copy_cheat_table(out);
}

/* OpenSPM audio output (include/openspm/audio_out.h, docs/AUDIO-PLAN.md): the
   native module pushes each latched Wii AI buffer (host-order s16, L then R,
   32 kHz) from the game thread. One SDL3 audio stream on the default playback
   device; SDL converts to the device format/rate. Never blocks: the pacer
   prebuffers, pauses on underrun (silence), drops on overflow and trims the
   playback rate by at most 0.2% so the device clock never drives the game.
   Opened lazily on the first push, so nothing happens unless OPENSPM_AUDIO=1
   made the module produce audio. [Audio] volume (0..100) -> OPENSPM_AUDIO_VOLUME. */
namespace {
SDL_AudioStream *g_m14_audio_stream = nullptr;
bool g_m14_audio_failed = false;
openspm_audio_pacer g_m14_audio_pacer;
/* Output watchdog: pushes since the pacer last started playing, SDL call
   failures, and how often the stream was reopened. */
uint32_t g_m14_audio_stall_pushes = 0u;
uint32_t g_m14_audio_reopens = 0u;
bool g_m14_audio_sdl_error_logged = false;

bool m14_audio_open(uint32_t rate) {
    if (g_m14_audio_stream != nullptr) return true;
    if (g_m14_audio_failed) return false;
    char line[256];
    if (!SDL_WasInit(SDL_INIT_AUDIO) && !SDL_InitSubSystem(SDL_INIT_AUDIO)) {
        g_m14_audio_failed = true;
        std::snprintf(line, sizeof line, "OpenSPM audio: SDL audio init failed: %s", SDL_GetError());
        log_message(OPENSPM_LOG_WARN, line);
        return false;
    }
    SDL_AudioSpec spec{};
    spec.format = SDL_AUDIO_S16;
    spec.channels = 2;
    spec.freq = static_cast<int>(rate);
    g_m14_audio_stream = SDL_OpenAudioDeviceStream(SDL_AUDIO_DEVICE_DEFAULT_PLAYBACK, &spec, nullptr, nullptr);
    if (g_m14_audio_stream == nullptr) {
        g_m14_audio_failed = true;
        openspm_overlay::g_audio_status.failed = true;
        std::snprintf(line, sizeof line, "OpenSPM audio: no playback device (%s); running without sound", SDL_GetError());
        log_message(OPENSPM_LOG_WARN, line);
        return false;
    }
    float gain = 1.0f;
    if (const char *value = std::getenv("OPENSPM_AUDIO_VOLUME"); value != nullptr && *value != '\0') {
        const long volume = std::strtol(value, nullptr, 10);
        gain = static_cast<float>(std::clamp<long>(volume, 0, 100)) / 100.0f;
    }
    SDL_SetAudioStreamGain(g_m14_audio_stream, gain);
    openspm_audio_pacer_init(&g_m14_audio_pacer, rate);
    SDL_AudioSpec device{};
    int device_frames = 0;
    (void)SDL_GetAudioDeviceFormat(SDL_GetAudioStreamDevice(g_m14_audio_stream), &device, &device_frames);
    std::snprintf(line, sizeof line,
                  "OpenSPM audio: SDL stream open (%u Hz stereo s16 -> device %d Hz, %d ch, %d-frame buffer, driver %s), "
                  "volume %d%%, prebuffer %u ms",
                  static_cast<unsigned>(rate), device.freq, device.channels, device_frames,
                  SDL_GetCurrentAudioDriver() != nullptr ? SDL_GetCurrentAudioDriver() : "?",
                  static_cast<int>(gain * 100.0f + 0.5f), OPENSPM_AUDIO_TARGET_MS);
    log_message(OPENSPM_LOG_INFO, line);
    return true; // the device starts paused: the pacer resumes it after the prebuffer
}

/* Turbo: the game makes N x the audio; play none of it (queue stays empty). */
void m14_audio_turbo_drop(openspm_audio_out_status *status, uint32_t rate) {
    if (g_m14_audio_stream != nullptr) {
        if (g_m14_audio_pacer.playing) SDL_PauseAudioStreamDevice(g_m14_audio_stream);
        SDL_ClearAudioStream(g_m14_audio_stream);
        if (g_m14_audio_pacer.trim_ppm != 0) SDL_SetAudioStreamFrequencyRatio(g_m14_audio_stream, 1.0f);
        openspm_audio_pacer_reset(&g_m14_audio_pacer); // after turbo: prebuffer again, then play
    }
    openspm_overlay::g_audio_status.queued_ms = 0u;
    if (status != nullptr) {
        status->queued_frames = 0u;
        status->rate = rate;
        status->underruns = g_m14_audio_pacer.underruns;
        status->resyncs = g_m14_audio_pacer.resyncs;
        status->dropped_frames = g_m14_audio_pacer.dropped_frames;
        status->trim_ppm = g_m14_audio_pacer.trim_ppm;
        status->playing = 0u;
    }
}

/* The menu paused the game: go silent now instead of playing the queued tail. */
void m14_audio_menu_pause() {
    if (g_m14_audio_stream == nullptr || !g_m14_audio_pacer.playing) return;
    SDL_PauseAudioStreamDevice(g_m14_audio_stream);
    SDL_ClearAudioStream(g_m14_audio_stream);
    openspm_audio_pacer_reset(&g_m14_audio_pacer);
    SDL_SetAudioStreamFrequencyRatio(g_m14_audio_stream, 1.0f);
}
} // namespace

extern "C" OPENSPM_EXPORT int OPENSPM_CALL openspm_renderer_audio_push(const int16_t *lr, uint32_t frames,
                                                                       uint32_t rate,
                                                                       openspm_audio_out_status *status) {
    if (rate == 0u) rate = 32000u;
    if (!m14_audio_open(rate)) return -1;
    if (g_m14_turbo_speed.load(std::memory_order_relaxed) != 0u) { // fast-forward: muted, nothing queued
        m14_audio_turbo_drop(status, rate);
        return 0;
    }
    const int queued_bytes = SDL_GetAudioStreamQueued(g_m14_audio_stream);
    if (queued_bytes < 0 && !g_m14_audio_sdl_error_logged) {
        g_m14_audio_sdl_error_logged = true;
        char line[256];
        std::snprintf(line, sizeof line, "OpenSPM audio: SDL_GetAudioStreamQueued failed: %s", SDL_GetError());
        log_message(OPENSPM_LOG_WARN, line);
    }
    const uint32_t queued = queued_bytes > 0 ? static_cast<uint32_t>(queued_bytes) / 4u : 0u;
    const auto now = std::chrono::steady_clock::now().time_since_epoch();
    const uint64_t now_ns = static_cast<uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(now).count());
    if (openspm_overlay::g_audio_volume_live >= 0) { // F1 menu -> Audio -> Volume
        SDL_SetAudioStreamGain(g_m14_audio_stream, static_cast<float>(openspm_overlay::g_audio_volume_live) / 100.0f);
        openspm_overlay::g_audio_volume_live = -1;
    }
    const unsigned flags =
        lr != nullptr && frames != 0u ? openspm_audio_pacer_push(&g_m14_audio_pacer, queued, frames, now_ns) : 0u;
    uint32_t after = queued;
    if (flags & OPENSPM_AUDIO_PACE_STOP) SDL_PauseAudioStreamDevice(g_m14_audio_stream);
    if (flags & OPENSPM_AUDIO_PACE_PUT) {
        if (SDL_PutAudioStreamData(g_m14_audio_stream, lr, static_cast<int>(frames * 4u))) {
            after += frames;
        } else if (!g_m14_audio_sdl_error_logged) {
            g_m14_audio_sdl_error_logged = true;
            char line[256];
            std::snprintf(line, sizeof line, "OpenSPM audio: SDL_PutAudioStreamData failed: %s", SDL_GetError());
            log_message(OPENSPM_LOG_WARN, line);
        }
    }
    if (flags & OPENSPM_AUDIO_PACE_START) SDL_ResumeAudioStreamDevice(g_m14_audio_stream);
    /* Watchdog: sound keeps arriving but the pacer never reaches its prebuffer
       (the queue stays empty: the device drains while "paused", or the stream
       stopped accepting data). Seen once after a star door: silence for the
       rest of the run. After 1 s of that, log the SDL state and reopen. */
    if (g_m14_audio_pacer.playing || lr == nullptr || frames == 0u) {
        g_m14_audio_stall_pushes = 0u;
    } else if (++g_m14_audio_stall_pushes * frames >= rate) { // 1 s of sound pushed, none played
        char line[320];
        std::snprintf(line, sizeof line,
                      "OpenSPM audio: output stalled (%u pushes without reaching the %u ms prebuffer; queued=%u "
                      "frames, device paused=%d, error=\"%s\"); reopening the audio stream (#%u)",
                      g_m14_audio_stall_pushes, OPENSPM_AUDIO_TARGET_MS, after,
                      SDL_AudioStreamDevicePaused(g_m14_audio_stream) ? 1 : 0, SDL_GetError(),
                      g_m14_audio_reopens + 1u);
        log_message(OPENSPM_LOG_WARN, line);
        SDL_DestroyAudioStream(g_m14_audio_stream);
        g_m14_audio_stream = nullptr;
        g_m14_audio_stall_pushes = 0u;
        g_m14_audio_sdl_error_logged = false;
        if (++g_m14_audio_reopens > 8u) {
            g_m14_audio_failed = true; // give up quietly rather than churn the device
            log_message(OPENSPM_LOG_WARN, "OpenSPM audio: output kept stalling; continuing without sound");
        }
        return 0;
    }
    if (flags & OPENSPM_AUDIO_PACE_TRIM)
        SDL_SetAudioStreamFrequencyRatio(g_m14_audio_stream,
                                         1.0f + static_cast<float>(g_m14_audio_pacer.trim_ppm) / 1.0e6f);
    openspm_overlay::g_audio_status.open = true;
    openspm_overlay::g_audio_status.queued_ms = after * 1000u / rate;
    openspm_overlay::g_audio_status.underruns = g_m14_audio_pacer.underruns;
    openspm_overlay::g_audio_status.resyncs = g_m14_audio_pacer.resyncs;
    openspm_overlay::g_audio_status.trim_ppm = g_m14_audio_pacer.trim_ppm;
    if (status != nullptr) {
        status->queued_frames = after;
        status->rate = rate;
        status->underruns = g_m14_audio_pacer.underruns;
        status->resyncs = g_m14_audio_pacer.resyncs;
        status->dropped_frames = g_m14_audio_pacer.dropped_frames;
        status->trim_ppm = g_m14_audio_pacer.trim_ppm;
        status->playing = g_m14_audio_pacer.playing ? 1u : 0u;
    }
    return 0;
}

/* OpenSPM menu control (include/openspm/menu_control.h): HOME toggles the menu
   and the backend holds the game at its frame safepoint while it is open,
   calling OPENSPM_MENU_OP_PAUSE_FRAME in a loop. Game thread only (the same
   thread that pumps the window and presents). */
namespace {
bool g_m14_menu_pause_deferred_logged = false;

int m14_menu_pause_frame() {
    renderer_state &state = g_renderer;
    if (!state.initialized || state.exit_requested) return 0;
    if (state.frame_open) {
        /* The game has already started its next frame in Aurora: in SPM that is
           every safepoint (it issues GX work for frame N+1 before the scheduler
           idles), so waiting for a closed frame never paused. The backend calls
           this only at PRESENT_SAFEPOINT, where Broadway cannot be halfway
           through a GX command: drain the parser and end the Aurora frame here
           (it presents the last display copy; the EFB persists across Aurora
           frames, as with a bbox readback submit), then run the menu frames.
           The rest of the guest frame records into a fresh Aurora frame after
           the pause. OPENSPM_MENU_PAUSE_SPLIT=0 restores the old deferral. */
        static const bool split = [] {
            const char *value = std::getenv("OPENSPM_MENU_PAUSE_SPLIT");
            return value == nullptr || value[0] != '0';
        }();
        if (!split) {
            if (!g_m14_menu_pause_deferred_logged) {
                g_m14_menu_pause_deferred_logged = true;
                log_message(OPENSPM_LOG_INFO,
                            "OpenSPM menu pause deferred: a game frame is open; retrying next frame.");
            }
            return 0;
        }
        aurora::gx::raw::drain();
        if (g_m14_turbo_speed.load(std::memory_order_relaxed) != 0u)
            aurora_m14_set_turbo(AURORA_M14_TURBO_ACTIVE); // shown, like every menu frame
        aurora_end_frame();
        state.frame_open = false;
        if (!g_m14_menu_pause_deferred_logged) {
            g_m14_menu_pause_deferred_logged = true;
            log_message(OPENSPM_LOG_INFO,
                        "OpenSPM menu pause: the open game frame was submitted at the safepoint; menu frames follow.");
        }
    }
    static auto s_last = std::chrono::steady_clock::now();
    m14_audio_menu_pause();
    pump_events(state);
    if (state.exit_requested) return 0;
    if (g_m14_overlay_toggle_requested) {
        g_m14_overlay_toggle_requested = false;
        openspm_overlay::toggle();
    }
    if (!openspm_overlay::g_open) return 0;
    if (aurora_begin_frame()) {
        /* No GX work: Aurora presents the last Wii display copy again, with
           the menu on top. */
        openspm_overlay::draw();
        m14_draw_mod_boxes();
        if (g_m14_turbo_speed.load(std::memory_order_relaxed) != 0u)
            aurora_m14_set_turbo(AURORA_M14_TURBO_ACTIVE); // a menu frame is always shown
        aurora_end_frame();
        openspm_overlay::after_frame();
    } else {
        std::this_thread::sleep_for(std::chrono::milliseconds(10)); // window minimized / surface lost
    }
    /* Vsync paces this loop; without it cap the menu at ~120 frames/s. */
    const auto now = std::chrono::steady_clock::now();
    if (now - s_last < std::chrono::milliseconds(8)) std::this_thread::sleep_until(s_last + std::chrono::milliseconds(8));
    s_last = std::chrono::steady_clock::now();
    return openspm_overlay::g_open && !state.exit_requested ? 1 : 0;
}
} // namespace

extern "C" OPENSPM_EXPORT int OPENSPM_CALL openspm_renderer_menu(unsigned op) {
    switch (op) {
    case OPENSPM_MENU_OP_STATE:
        return openspm_overlay::g_open ? OPENSPM_MENU_STATE_OPEN : 0;
    case OPENSPM_MENU_OP_HOME:
        g_m14_overlay_toggle_requested = true; // applied at the next presented frame
        return 1;
    case OPENSPM_MENU_OP_PAUSE_FRAME:
        return m14_menu_pause_frame();
    case OPENSPM_MENU_OP_KEYMAP_GEN:
        return openspm_overlay::keymap_generation();
    default:
        return 0;
    }
}

/* openspm.overlay (include/openspm/overlay_service.h): OpenSPMNative forwards
   mod panels and notifications here; any thread, copies the text. */
extern "C" OPENSPM_EXPORT int OPENSPM_CALL openspm_renderer_overlay(uint32_t op, const char *id, const char *text,
                                                                   uint32_t milliseconds) {
    switch (op) {
    case OPENSPM_RENDERER_OVERLAY_SET_PANEL:
        return openspm_overlay::mod_overlay_set_panel(id, text);
    case OPENSPM_RENDERER_OVERLAY_NOTIFY:
        return openspm_overlay::mod_overlay_notify(text, milliseconds);
    default:
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    }
}

extern "C" OPENSPM_EXPORT int OPENSPM_CALL openspm_renderer_keyboard_map(char *out, unsigned capacity) {
    return openspm_overlay::copy_keyboard_map(out, capacity);
}

/* Fast-forward (include/openspm/menu_control.h OPENSPM_RENDERER_TURBO_EXPORT):
   the backend calls this on the game thread whenever turbo engages, changes
   speed or is released (0). Never called during normal play. */
extern "C" OPENSPM_EXPORT int OPENSPM_CALL openspm_renderer_turbo(unsigned speed) {
    static std::uint64_t s_engages = 0u;
    const unsigned before = g_m14_turbo_speed.exchange(speed, std::memory_order_relaxed);
    openspm_overlay::g_turbo_speed.store(speed, std::memory_order_relaxed);
    if (speed == 0u) {
        aurora_m14_set_turbo(0u); // normal presentation from the next frame on
        return 1;
    }
    if (before == 0u) {
        g_m14_turbo_gate.reset(g_m14_display_period_ns);
        if (dev4_should_log_count(++s_engages)) {
            char line[224];
            std::snprintf(line, sizeof line,
                          "OpenSPM turbo presentation: at most one image per display refresh (%.3f Hz)%s; audio muted.",
                          1.0e9 / g_m14_turbo_gate.period_ns(),
                          m14_turbo_async_presenter() ? ", async presenter shows the newest image uninterpolated" : "");
            log_message(OPENSPM_LOG_INFO, line);
        }
    }
    return 1;
}

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#endif

namespace {

constexpr std::uint64_t kDev7SyncDiagWindow = 256u;
constexpr std::uint64_t kDev7SyncDiagBinUs = 250u;
constexpr std::size_t kDev7SyncDiagBins = 256u; /* clamp >=64 ms */

struct dev7_sync_diag_metric {
    std::array<std::uint64_t, kDev7SyncDiagBins> hist{};
    std::uint64_t window_calls = 0u;
    std::uint64_t total_calls = 0u;
    std::uint64_t total_us = 0u;
    std::uint64_t max_us = 0u;
};

dev7_sync_diag_metric g_dev7_sync_draw_done{};
dev7_sync_diag_metric g_dev7_sync_present{};
std::uint64_t g_dev7_drawdone_copy_presents = 0u;
std::uint64_t g_dev7_drawdone_no_copy = 0u;
bool g_dev7_logged_drawdone_copy_present = false;

std::uint64_t dev7_sync_diag_percentile(const dev7_sync_diag_metric &metric,
                                        std::uint64_t percentile) {
    if (metric.window_calls == 0u) return 0u;
    const std::uint64_t target =
        (metric.window_calls * percentile + 99u) / 100u;
    std::uint64_t cumulative = 0u;
    for (std::size_t i = 0u; i < metric.hist.size(); ++i) {
        cumulative += metric.hist[i];
        if (cumulative >= target)
            return (static_cast<std::uint64_t>(i) + 1u) * kDev7SyncDiagBinUs;
    }
    return static_cast<std::uint64_t>(metric.hist.size()) * kDev7SyncDiagBinUs;
}

void dev7_sync_diag_clear_window(dev7_sync_diag_metric &metric) {
    metric.hist.fill(0u);
    metric.window_calls = 0u;
    metric.total_us = 0u;
    metric.max_us = 0u;
}

void dev7_sync_diag_log_and_reset(dev7_sync_diag_metric &metric,
                                  const char *kind) {
    if (metric.window_calls == 0u) return;
    char message[384];
    const std::uint64_t avg_us = metric.total_us / metric.window_calls;
    std::snprintf(message, sizeof(message),
                  "M14 producer sync window kind=%s window_calls=%llu total_calls=%llu avg_us=%llu p50_us=%llu p95_us=%llu p99_us=%llu max_us=%llu.",
                  kind,
                  static_cast<unsigned long long>(metric.window_calls),
                  static_cast<unsigned long long>(metric.total_calls),
                  static_cast<unsigned long long>(avg_us),
                  static_cast<unsigned long long>(dev7_sync_diag_percentile(metric, 50u)),
                  static_cast<unsigned long long>(dev7_sync_diag_percentile(metric, 95u)),
                  static_cast<unsigned long long>(dev7_sync_diag_percentile(metric, 99u)),
                  static_cast<unsigned long long>(metric.max_us));
    log_message(OPENSPM_LOG_INFO, message);
    dev7_sync_diag_clear_window(metric);
}

void dev7_sync_diag_record(dev7_sync_diag_metric &metric,
                           const char *kind,
                           std::uint64_t elapsed_us) {
    ++metric.window_calls;
    ++metric.total_calls;
    metric.total_us += elapsed_us;
    if (elapsed_us > metric.max_us) metric.max_us = elapsed_us;
    std::uint64_t bin = elapsed_us / kDev7SyncDiagBinUs;
    if (bin >= metric.hist.size()) bin = metric.hist.size() - 1u;
    ++metric.hist[static_cast<std::size_t>(bin)];
    if (metric.window_calls == kDev7SyncDiagWindow)
        dev7_sync_diag_log_and_reset(metric, kind);
}

bool dev7_should_log_copy_count(std::uint64_t value) {
    return value <= 8u || (value != 0u && (value & (value - 1u)) == 0u);
}

void dev7_log_completed_copy_stats() {
    char message[320];
    std::snprintf(message, sizeof(message),
                  "M14 completed-copy presentation stats drawdone_presents=%llu drawdone_no_copy=%llu scheduler_safepoints=%llu total_display_copies=%llu.",
                  static_cast<unsigned long long>(g_dev7_drawdone_copy_presents),
                  static_cast<unsigned long long>(g_dev7_drawdone_no_copy),
                  static_cast<unsigned long long>(g_dev4_present_safepoints),
                  static_cast<unsigned long long>(g_dev4_present_copies));
    log_message(OPENSPM_LOG_INFO, message);
}

int dev7_present_completed_copy_after_drawdone(renderer_state &state) {
    if (!state.initialized) return OPENSPM_ERROR_RUNTIME;
    if (!state.frame_open) return OPENSPM_OK;

    /* close_frame_nonblocking() in dev4 has already drained the parser. Never
       drain again here: take only a display-copy event that is already fully
       parsed and latched. This keeps an asynchronous/mid-command CPU boundary
       out of the renderer while removing the unrelated guest-idle gate. */
    const bool display_copy = aurora::gx::raw::take_display_copy();
    if (!display_copy) {
        ++g_dev7_drawdone_no_copy;
        return OPENSPM_OK;
    }

    g_m14_bbox_frame_reads = 0u;
    ++g_dev7_drawdone_copy_presents;
    ++g_dev4_present_copies;
    ++state.display_copies;
    if (g_m14_overlay_toggle_requested) {
        g_m14_overlay_toggle_requested = false;
        openspm_overlay::toggle();
    }
    openspm_overlay::draw(); // ImGui, between Aurora's begin and end frame
    m14_draw_mod_boxes();    // mod boxes (openspm.overlay v2); nothing without them
    m14_turbo_before_end_frame(); // turbo only: at most one shown image per display refresh
    {
        const m14_cost_scope cost{m14_cost_op::present};
        aurora_end_frame();
    }
    state.frame_open = false;
    openspm_overlay::after_frame();

    if (state.capture_requested) {
        state.capture_requested = false;
        aurora::gx::fifo::audit::arm_seqload_capture(1024u);
        log_message(OPENSPM_LOG_INFO,
                    "M14 F8 GX capture armed after completed display copy; next 1024 draws, ending at DrawDone.");
    }
    {
        // Once per second: where renderer time went (inside the game's disp and
        // done phases). fifo_submit = GX parse/record, draw_done = DrawDone
        // drain, bbox = CPU bbox reads (submit + GPU wait).
        static auto s_budget_start = std::chrono::steady_clock::now();
        const auto now_budget = std::chrono::steady_clock::now();
        if (now_budget - s_budget_start >= std::chrono::seconds(1)) {
            const auto window_ms = std::chrono::duration_cast<std::chrono::milliseconds>(now_budget - s_budget_start).count();
            s_budget_start = now_budget;
            auto &fifo = g_m14_costs[static_cast<unsigned>(m14_cost_op::fifo_submit)];
            auto &drain = g_m14_costs[static_cast<unsigned>(m14_cost_op::draw_done)];
            auto &present = g_m14_costs[static_cast<unsigned>(m14_cost_op::present)];
            char line[384];
            std::snprintf(line, sizeof line,
                          "M14 renderer budget host_ms=%lld fifo_submit=%llu calls/%llu us draw_done=%llu calls/%llu us "
                          "present=%llu calls/%llu us bbox_maps=%llu/%llu us",
                          static_cast<long long>(window_ms), static_cast<unsigned long long>(fifo.window_calls),
                          static_cast<unsigned long long>(fifo.window_us),
                          static_cast<unsigned long long>(drain.window_calls),
                          static_cast<unsigned long long>(drain.window_us),
                          static_cast<unsigned long long>(present.window_calls),
                          static_cast<unsigned long long>(present.window_us),
                          static_cast<unsigned long long>(g_m14_bbox_window_maps),
                          static_cast<unsigned long long>(g_m14_bbox_window_us));
            log_message(OPENSPM_LOG_INFO, line);
            for (auto *counter : {&fifo, &drain, &present}) counter->window_us = counter->window_calls = 0u;
            g_m14_bbox_window_maps = g_m14_bbox_window_us = 0u;
            if (const int fast_mode = aurora::gx::raw::m14_bbox_fast_mode(); fast_mode != 0) {
                // OpenSPM bbox fast: in verify mode "fast" counts the reads the CPU
                // would have answered (all of them still went to the GPU).
                std::array<std::uint64_t, 4> draws{};
                aurora::gx::raw::m14_bbox_fast_take_draw_stats(draws);
                auto &w = g_m14_bbox_fast_window;
                if (w.reads != 0u) {
                    std::snprintf(line, sizeof line,
                                  "M14 bbox fast: mode=%s reads=%llu cached=%llu fast=%llu (no_draws=%llu "
                                  "offscreen=%llu degenerate=%llu) gpu=%llu verify_mismatches=%llu bbox_ms=%.2f "
                                  "draws_checked=%llu unprovable=%llu",
                                  fast_mode == 2 ? "verify" : "on", static_cast<unsigned long long>(w.reads),
                                  static_cast<unsigned long long>(w.cached),
                                  static_cast<unsigned long long>(w.fast[1] + w.fast[2] + w.fast[3]),
                                  static_cast<unsigned long long>(w.fast[1]), static_cast<unsigned long long>(w.fast[2]),
                                  static_cast<unsigned long long>(w.fast[3]), static_cast<unsigned long long>(w.gpu),
                                  static_cast<unsigned long long>(w.mismatches), static_cast<double>(w.us) / 1000.0,
                                  static_cast<unsigned long long>(draws[0] + draws[1] + draws[2] + draws[3]),
                                  static_cast<unsigned long long>(draws[0]));
                    log_message(fast_mode == 2 && w.mismatches != 0u ? OPENSPM_LOG_WARN : OPENSPM_LOG_INFO, line);
                }
                w = {};
            }
        }
    }
    if (g_m14_screenshot_requested) {
        g_m14_screenshot_requested = false;
        std::error_code ec;
        std::filesystem::create_directories("screenshots", ec);
        const std::time_t now = std::time(nullptr);
        std::tm local{};
#if defined(_WIN32)
        localtime_s(&local, &now);
#else
        localtime_r(&now, &local);
#endif
        char path[128];
        std::strftime(path, sizeof path, "screenshots/OpenSPM-%Y%m%d-%H%M%S.png", &local);
        const bool saved = aurora::gx::raw::m14_save_screenshot(path);
        const std::string shown = std::filesystem::absolute(path, ec).string();
        char line[640];
        std::snprintf(line, sizeof line, saved ? "M14 screenshot saved: %s" : "M14 screenshot failed: %s",
                      ec ? path : shown.c_str());
        log_message(saved ? OPENSPM_LOG_INFO : OPENSPM_LOG_WARN, line);
    }
    {
        static const bool s_snapshot_dump = [] {
            const char *value = std::getenv("OPENSPM_M14_SNAPSHOT_DUMP");
            const bool on = value != nullptr && value[0] == '1';
            if (on) {
                std::error_code ec;
                std::filesystem::create_directories("screenshots", ec);
            }
            return on;
        }();
        if (s_snapshot_dump) aurora::gx::raw::m14_flush_snapshot_dumps(); // after present: the frame wrote them
    }
    {
        static unsigned s_copy_capture_cooldown = 0u;
        static unsigned s_copy_capture_shots = 0u;
        if (s_copy_capture_cooldown != 0u) --s_copy_capture_cooldown;
        /* flipcapture: also arm mid-frame on the first bbox burst (the flip
           start). The card is captured only in that frame, right after the burst. */
        static const bool s_burst_hook_installed = [] {
            const char *value = std::getenv("OPENSPM_M14_CAPTURE_ON_BBOX_BURST");
            if (value != nullptr && value[0] == '1') {
                g_m14_bbox_burst_hook = [] {
                    static bool armed = false;
                    std::uint32_t seq = 0u;
                    if (armed || !openspm_overlay::read_guest_be32(0x804D0D60u, seq) || seq != 2u) return;
                    armed = true;
                    const char *env = std::getenv("OPENSPM_M14_CAPTURE_DRAWS");
                    unsigned draws = env != nullptr ? static_cast<unsigned>(std::strtoul(env, nullptr, 10)) : 0u;
                    if (draws < 256u || draws > 60000u) draws = 20000u;
                    aurora::gx::fifo::audit::arm_seqload_capture(draws);
                    g_m14_bbox_trace_budget = 2048u;
                    char line[160];
                    std::snprintf(line, sizeof line,
                                  "M14 bbox-burst GX capture armed mid-frame (flip/door start); next %u draws.", draws);
                    log_message(OPENSPM_LOG_INFO, line);
                };
            }
            return true;
        }();
        (void)s_burst_hook_installed;
        /* One long capture: flip frames exceed 1024 draws, and the card copy and
           its draws come late in the frame. OPENSPM_M14_COPY_CAPTURE_DRAWS
           overrides the draw count (default 6000, a few frames). */
        static const unsigned s_capture_draws = [] {
            const char *value = std::getenv("OPENSPM_M14_COPY_CAPTURE_DRAWS");
            const unsigned long parsed = value != nullptr ? std::strtoul(value, nullptr, 10) : 0ul;
            return parsed >= 256ul && parsed <= 50000ul ? static_cast<unsigned>(parsed) : 6000u;
        }();
        if (aurora::gx::raw::m14_take_copy_capture_request() && s_copy_capture_cooldown == 0u &&
            s_copy_capture_shots < 1u) {
            ++s_copy_capture_shots;
            s_copy_capture_cooldown = 60u;
            aurora::gx::fifo::audit::arm_seqload_capture(s_capture_draws);
            g_m14_bbox_trace_budget = 1024u;
            char line[192];
            std::snprintf(line, sizeof line,
                          "M14 copy-triggered GX capture armed (OPENSPM_M14_COPY_CAPTURE); next %u draws.",
                          s_capture_draws);
            log_message(OPENSPM_LOG_INFO, line);
        }
    }

    if (!g_dev7_logged_drawdone_copy_present) {
        log_message(OPENSPM_LOG_INFO,
                    "M14 completed Wii display-copy presentation active: parsed GXCopyDisp frames present at DrawDone; scheduler-idle remains fallback.");
        g_dev7_logged_drawdone_copy_present = true;
    }
    if (dev7_should_log_copy_count(g_dev7_drawdone_copy_presents))
        dev7_log_completed_copy_stats();
    return OPENSPM_OK;
}

void dev7_sync_diag_reset() {
    g_dev7_sync_draw_done = {};
    g_dev7_sync_present = {};
    g_dev7_drawdone_copy_presents = 0u;
    g_dev7_drawdone_no_copy = 0u;
    g_dev7_logged_drawdone_copy_present = false;
}

/* snapcapture: SPM requested an RGBA8 capture (flip start). It measures Mario
   with bbox reads a few frames later, so start the capture at the next bbox
   read: the Mario draws, the reads building the rect and the copy follow. */
void m14_start_snap_capture() {
    const char *env = std::getenv("OPENSPM_M14_CAPTURE_DRAWS");
    unsigned draws = env != nullptr ? static_cast<unsigned>(std::strtoul(env, nullptr, 10)) : 0u;
    if (draws < 256u || draws > 60000u) draws = 12000u;
    aurora::gx::fifo::audit::arm_seqload_capture(draws);
    g_m14_bbox_trace_budget = 4096u;
    char line[160];
    std::snprintf(line, sizeof line, "M14 capture-request GX capture started at the next bbox read; next %u draws.",
                  draws);
    log_message(OPENSPM_LOG_INFO, line);
}
void m14_arm_capture_if_requested() {
    if (!aurora::gx::raw::m14_take_capture_arm_request()) return;
    g_m14_bbox_next_read_hook = m14_start_snap_capture;
}

int OPENSPM_CALL sync_fifo_profiled_dev7(
    void *context,
    std::uint32_t reason) {
    dev7_sync_diag_metric *metric = nullptr;
    const char *kind = nullptr;
    if (reason == OPENSPM_GX_SYNC_DRAW_DONE) {
        metric = &g_dev7_sync_draw_done;
        kind = "draw_done";
    } else if (reason == OPENSPM_GX_SYNC_PRESENT_SAFEPOINT) {
        metric = &g_dev7_sync_present;
        kind = "present_safepoint";
    }

    if (metric == nullptr) {
        /* After the sync returns (e.g. SPM's draw-sync token wait after a capture
           copy): refresh the snapshot slots and arm a requested capture. */
        struct after_sync {
            ~after_sync() {
                aurora::gx::raw::m14_snapshot_sync();
                m14_arm_capture_if_requested();
            }
        } refresh;
        return sync_fifo_batched_v6(context, reason);
    }

    const auto begin = std::chrono::steady_clock::now();
    int result = sync_fifo_batched_v6(context, reason);
    if (result == OPENSPM_OK && reason == OPENSPM_GX_SYNC_DRAW_DONE) {
        auto *state = static_cast<renderer_state *>(context);
        if (state == nullptr) {
            result = OPENSPM_ERROR_INVALID_ARGUMENT;
        } else {
            /* OpenSPM M14 EFB copy RAM write-back (opt-in): DrawDone is the
               point where the paused game may next read copied pixels from
               RAM. The parser has drained, so split the frame like a bbox read
               and publish pending texture copies before the game resumes. */
            aurora::gx::raw::m14_snapshot_sync();
            if (aurora::gx::raw::efb_copy_writeback_pending()) {
                if (state->frame_open && !aurora::gx::raw::submit_for_readback()) {
                    state->frame_open = false;
                    log_message(OPENSPM_LOG_ERROR, "M14 EFB copy RAM write-back readback split failed");
                    result = OPENSPM_ERROR_RUNTIME;
                } else {
                    (void)aurora::gx::raw::flush_efb_copy_writeback();
                }
            }
            if (result == OPENSPM_OK)
                result = dev7_present_completed_copy_after_drawdone(*state);
        }
    } else if (result == OPENSPM_OK) {
        aurora::gx::raw::m14_snapshot_sync();
    }
    m14_arm_capture_if_requested();
    const auto end = std::chrono::steady_clock::now();
    const auto elapsed =
        std::chrono::duration_cast<std::chrono::microseconds>(end - begin).count();
    dev7_sync_diag_record(*metric, kind,
                          elapsed > 0 ? static_cast<std::uint64_t>(elapsed) : 0u);
    return result;
}

/* Display-locked vanilla pacing.
 *
 * Runs once, right after Aurora created the window and before the native game
 * module arms its first retrace. If vanilla 60 fits the window's display
 * (59.94/59.98/60 Hz or 119.88/120/240 Hz...), publish the matching simulation
 * period through OPENSPM_M14_RETRACE_PERIOD_NS; the game module then advances
 * exactly one simulated frame per k refreshes instead of beating against an
 * unrelated 60.000 Hz host timer. Otherwise log why vanilla 60 will judder and
 * which modes of this display would fix it.
 *
 * Controls: OPENSPM_M14_DISPLAY_SYNC=off keeps the fixed 60.000 Hz clock.
 * Explicit OPENSPM_M14_RETRACE_PERIOD_NS / dev132 diagnostics win. Interpolated
 * (async presenter) and vsync-off runs keep 60.000 Hz: the clock lock only
 * helps when each simulated frame is presented exactly once with FIFO vsync.
 * Exception: the Stage A present timeline (OPENSPM_M14_PRESENT_TIMELINE=1 with
 * the async presenter) keeps the lock and also receives the exact display
 * rational as OPENSPM_M14_DISPLAY_REFRESH=numerator/denominator.
 */
bool m14_env_is_off(const char *name) {
    const char *value = std::getenv(name);
    return value != nullptr && (ascii_iequals(value, "0") || ascii_iequals(value, "OFF") ||
                                ascii_iequals(value, "FALSE") || ascii_iequals(value, "NO"));
}

bool m14_env_is_on(const char *name) {
    const char *value = std::getenv(name);
    return value != nullptr && *value != '\0' && !m14_env_is_off(name);
}

void m14_publish_env(const char *name, const char *value) {
#ifdef _WIN32
    /* Process environment block: visible to the native module's
       GetEnvironmentVariableA regardless of which CRT copy it uses. */
    (void)SetEnvironmentVariableA(name, value);
    (void)_putenv_s(name, value);
#else
    (void)setenv(name, value, 1);
#endif
}

void m14_mode_rational(const SDL_DisplayMode &mode, uint64_t &numerator, uint64_t &denominator) {
    if (mode.refresh_rate_numerator > 0 && mode.refresh_rate_denominator > 0) {
        numerator = static_cast<uint64_t>(mode.refresh_rate_numerator);
        denominator = static_cast<uint64_t>(mode.refresh_rate_denominator);
    } else {
        openspm::pacing::rational_from_hz(mode.refresh_rate, numerator, denominator);
    }
}

/* Window mode: OPENSPM_WINDOW_MODE=windowed (default) | borderless | exclusive.
 * Exclusive fullscreen keeps the desktop resolution and switches the refresh
 * rate: OPENSPM_REFRESH_HZ=<Hz> picks the nearest offered rate, otherwise
 * ("auto") the highest refresh vanilla 60 locks to (59.94/60/120/240 Hz...).
 * If nothing fits, exclusive fullscreen keeps the desktop refresh. Returns the
 * mode now driving the display (the selected exclusive mode, or the desktop
 * mode); SDL restores the desktop mode when the window is destroyed. */
const char *m14_window_mode() {
    const char *value = std::getenv("OPENSPM_WINDOW_MODE");
    if (value == nullptr || *value == '\0' || ascii_iequals(value, "windowed")) return "windowed";
    if (ascii_iequals(value, "borderless")) return "borderless";
    if (ascii_iequals(value, "exclusive") || ascii_iequals(value, "fullscreen")) return "exclusive";
    return "windowed";
}

double m14_requested_refresh_hz() {
    const char *value = std::getenv("OPENSPM_REFRESH_HZ");
    if (value == nullptr || *value == '\0' || ascii_iequals(value, "auto")) return 0.0;
    char *end = nullptr;
    const double hz = std::strtod(value, &end);
    return end != value && hz >= 24.0 && hz <= 1000.0 ? hz : 0.0;
}

const SDL_DisplayMode *m14_apply_window_mode(SDL_Window *window, SDL_DisplayID display,
                                             const SDL_DisplayMode *desktop) {
    char message[512];
    const char *mode_name = m14_window_mode();
    if (std::strcmp(mode_name, "windowed") == 0) return desktop;

    const SDL_DisplayMode *selected = nullptr;
    if (std::strcmp(mode_name, "exclusive") == 0) {
        int mode_count = 0;
        SDL_DisplayMode **modes = SDL_GetFullscreenDisplayModes(display, &mode_count);
        std::vector<const SDL_DisplayMode *> same_size;
        std::vector<openspm::pacing::RefreshCandidate> candidates;
        for (int i = 0; modes != nullptr && i < mode_count; ++i) {
            const SDL_DisplayMode *mode = modes[i];
            if (mode == nullptr || mode->w != desktop->w || mode->h != desktop->h) continue;
            openspm::pacing::RefreshCandidate candidate;
            m14_mode_rational(*mode, candidate.numerator, candidate.denominator);
            same_size.push_back(mode);
            candidates.push_back(candidate);
        }
        const double requested = m14_requested_refresh_hz();
        const int choice = openspm::pacing::choose_exclusive_refresh(
            candidates.data(), candidates.size(), requested);
        static SDL_DisplayMode chosen{};
        if (choice >= 0) {
            chosen = *same_size[static_cast<size_t>(choice)];
        } else {
            chosen = *desktop;
            std::snprintf(message, sizeof(message),
                          "M14 window: no %s refresh offered at %dx%d; exclusive fullscreen keeps the desktop %.3f Hz.",
                          requested > 0.0 ? "requested" : "vanilla-60-compatible",
                          desktop->w, desktop->h, desktop->refresh_rate);
            log_message(OPENSPM_LOG_WARN, message);
        }
        SDL_free(modes);
        if (SDL_SetWindowFullscreenMode(window, &chosen) && SDL_SetWindowFullscreen(window, true)) {
            (void)SDL_SyncWindow(window);
            selected = &chosen;
        } else {
            std::snprintf(message, sizeof(message),
                          "M14 window: exclusive fullscreen failed (%s); falling back to borderless.",
                          SDL_GetError());
            log_message(OPENSPM_LOG_WARN, message);
            mode_name = "borderless";
        }
    }
    if (selected == nullptr) {
        if (!SDL_SetWindowFullscreenMode(window, nullptr) || !SDL_SetWindowFullscreen(window, true)) {
            std::snprintf(message, sizeof(message),
                          "M14 window: borderless fullscreen failed (%s); staying windowed.", SDL_GetError());
            log_message(OPENSPM_LOG_WARN, message);
            return desktop;
        }
        (void)SDL_SyncWindow(window);
        selected = desktop;
    }
    uint64_t n = 0u, d = 0u;
    m14_mode_rational(*selected, n, d);
    std::snprintf(message, sizeof(message), "M14 window: %s fullscreen %dx%d @ %.3f Hz.",
                  mode_name, selected->w, selected->h,
                  d != 0u ? static_cast<double>(n) / static_cast<double>(d) : 0.0);
    log_message(OPENSPM_LOG_INFO, message);
    return selected;
}

void m14_apply_display_pacing() {
    char message[512];
    int window_count = 0;
    SDL_Window **windows = SDL_GetWindows(&window_count);
    SDL_Window *window = windows != nullptr && window_count > 0 ? windows[0] : nullptr;
    SDL_free(windows);
    const SDL_DisplayID display = window != nullptr ? SDL_GetDisplayForWindow(window) : 0;
    const SDL_DisplayMode *desktop = display != 0 ? SDL_GetDesktopDisplayMode(display) : nullptr;
    if (desktop == nullptr) desktop = display != 0 ? SDL_GetCurrentDisplayMode(display) : nullptr;
    if (desktop == nullptr) {
        log_message(OPENSPM_LOG_INFO,
                    "M14 vanilla pacing: display mode unavailable; keeping fixed 60.000 Hz simulation clock.");
        return;
    }
    const SDL_DisplayMode *current = m14_apply_window_mode(window, display, desktop);
    m14_update_cursor_for_window(window);

    uint64_t numerator = 0u, denominator = 0u;
    m14_mode_rational(*current, numerator, denominator);
    const auto pacing = openspm::pacing::choose_vanilla_pacing(numerator, denominator);
    if (numerator != 0u && denominator != 0u) // turbo present gate: one image per refresh
        g_m14_display_period_ns = 1.0e9 * static_cast<double>(denominator) / static_cast<double>(numerator);

    /* Refresh rates this display offers at the current resolution: the
       future exclusive-fullscreen picker, and the advice in the log. */
    std::string offered, smooth;
    int mode_count = 0;
    SDL_DisplayMode **modes = SDL_GetFullscreenDisplayModes(display, &mode_count);
    std::vector<double> seen;
    for (int i = 0; modes != nullptr && i < mode_count; ++i) {
        const SDL_DisplayMode *mode = modes[i];
        if (mode == nullptr || mode->w != current->w || mode->h != current->h) continue;
        uint64_t n = 0u, d = 0u;
        m14_mode_rational(*mode, n, d);
        if (n == 0u || d == 0u) continue;
        const double hz = static_cast<double>(n) / static_cast<double>(d);
        bool duplicate = false;
        for (const double other : seen) duplicate = duplicate || std::fabs(other - hz) < 0.0005;
        if (duplicate) continue;
        seen.push_back(hz);
        char rate[32];
        std::snprintf(rate, sizeof(rate), "%s%.3f", offered.empty() ? "" : ", ", hz);
        offered += rate;
        if (openspm::pacing::choose_vanilla_pacing(n, d).locked) {
            std::snprintf(rate, sizeof(rate), "%s%.3f Hz", smooth.empty() ? "" : " or ", hz);
            smooth += rate;
        }
    }
    SDL_free(modes);
    std::snprintf(message, sizeof(message),
                  "M14 display: %dx%d @ %.3f Hz (%llu/%llu); refresh rates offered at this resolution: %s",
                  current->w, current->h, pacing.display_hz,
                  static_cast<unsigned long long>(numerator),
                  static_cast<unsigned long long>(denominator),
                  offered.empty() ? "unknown" : offered.c_str());
    log_message(OPENSPM_LOG_INFO, message);

    const char *timeline_env = std::getenv("OPENSPM_M14_PRESENT_TIMELINE");
    const bool present_timeline = m14_env_is_on("OPENSPM_M14_ASYNC_PRESENTER") && timeline_env != nullptr &&
                                  std::strcmp(timeline_env, "1") == 0;
    if (present_timeline && numerator != 0u && denominator != 0u) {
        char refresh[48];
        std::snprintf(refresh, sizeof(refresh), "%llu/%llu", static_cast<unsigned long long>(numerator),
                      static_cast<unsigned long long>(denominator));
        m14_publish_env("OPENSPM_M14_DISPLAY_REFRESH", refresh);
    }

    const char *skip = nullptr;
    if (m14_env_is_off("OPENSPM_M14_DISPLAY_SYNC")) skip = "OPENSPM_M14_DISPLAY_SYNC=off";
    else if (std::getenv("OPENSPM_M14_RETRACE_PERIOD_NS") != nullptr) skip = "explicit OPENSPM_M14_RETRACE_PERIOD_NS";
    else if (m14_env_is_on("OPENSPM_M14_DIAG_HALF_99_943HZ")) skip = "dev132 cadence diagnostic";
    else if (m14_env_is_on("OPENSPM_M14_ASYNC_PRESENTER") && !present_timeline) skip = "interpolated/async presenter run";
    else if (!m14_vsync_enabled_from_environment()) skip = "vsync off";
    if (skip != nullptr) {
        std::snprintf(message, sizeof(message),
                      "M14 vanilla pacing: display lock skipped (%s); simulation clock unchanged.", skip);
        log_message(OPENSPM_LOG_INFO, message);
        return;
    }

    if (!pacing.locked) {
        std::snprintf(message, sizeof(message),
                      "M14 vanilla pacing: %.3f Hz is not a whole multiple of 60, so vanilla 60 fps cannot be even on this display "
                      "(frames alternate between refresh counts = camera judder). Simulation stays 60.000 Hz. "
                      "For smooth vanilla play select %s, use VRR, or use an interpolated frame-rate mode.",
                      pacing.display_hz,
                      smooth.empty() ? "a 60/120/240 Hz display mode" : smooth.c_str());
        log_message(OPENSPM_LOG_WARN, message);
        return;
    }

    char period[32];
    std::snprintf(period, sizeof(period), "%llu", static_cast<unsigned long long>(pacing.period_ns));
    m14_publish_env("OPENSPM_M14_RETRACE_PERIOD_NS", period);
    std::snprintf(message, sizeof(message),
                  "M14 vanilla pacing: display-locked; display=%.3f Hz, %u refresh(es) per game frame, "
                  "simulation=%.4f Hz (%+.3f%% vs 60), period=%sns.",
                  pacing.display_hz, pacing.multiple, pacing.simulation_hz,
                  (pacing.simulation_hz / openspm::pacing::kVanillaHz - 1.0) * 100.0, period);
    log_message(OPENSPM_LOG_INFO, message);
}

/* Output aspect: OPENSPM_ASPECT=4:3 (default) | stretch, OPENSPM_WIDESCREEN=0|1.
 * Aurora sizes its EFB to the window unless a viewport policy is applied, so
 * on a 2560x1080 panel the game's 640x480 space was drawn 4.0x wide but only
 * 2.25x tall. AURORA_VIEWPORT_FIT sizes the EFB to the game's logical 4:3
 * aspect inside the window at full height; the existing present path then
 * centres it with black bars (pillarbox, or letterbox on tall windows).
 * Widescreen ([Display] widescreen = 1): the backend reports the Wii TV setting
 * SYSCONF IPL.AR = 16:9, so the game itself renders a wider anamorphic view into
 * its 4:3 EFB. The EFB is then stretched (AURORA_VIEWPORT_STRETCH) and, for
 * aspect = 4:3, fitted to 16:9 inside the window (AuroraSetStretchAspect) with
 * the same bars; the host EFB is 16:9, so no horizontal upscale (output_aspect.hpp).
 * True ultrawide (beyond 16:9) is a separate future option. */
void m14_apply_output_aspect() {
    const char *value = std::getenv("OPENSPM_ASPECT");
    const bool stretch = value != nullptr && ascii_iequals(value, "stretch");
    const bool widescreen = openspm::aspect::widescreen_value(std::getenv("OPENSPM_WIDESCREEN"));
    const openspm::aspect::Output output = openspm::aspect::choose_output(stretch, widescreen);
    AuroraSetStretchAspect(output.stretch_aspect);
    AuroraSetViewportPolicy(output.stretch_policy ? AURORA_VIEWPORT_STRETCH : AURORA_VIEWPORT_FIT);
    log_message(OPENSPM_LOG_INFO,
                widescreen ? (stretch ? "M14 output aspect: widescreen 16:9 game image stretched to the window "
                                        "(OPENSPM_WIDESCREEN=1, OPENSPM_ASPECT=stretch)."
                                      : "M14 output aspect: widescreen; the game's anamorphic 16:9 image is shown at 16:9 "
                                        "with black bars on non-16:9 windows (OPENSPM_WIDESCREEN=1).")
                : stretch  ? "M14 output aspect: stretch to window (OPENSPM_ASPECT=stretch)."
                           : "M14 output aspect: 4:3 fit; the game image keeps its aspect with black bars on wider or taller windows.");
}

openspm_renderer_initialize_fn g_display_pacing_underlying_initialize = nullptr;

int OPENSPM_CALL renderer_initialize_display_pacing(
    void *context,
    const openspm_launch_desc *launch) {
    if (g_display_pacing_underlying_initialize == nullptr) return OPENSPM_ERROR_RUNTIME;
    const int result = g_display_pacing_underlying_initialize(context, launch);
    if (result == OPENSPM_OK) {
        m14_apply_output_aspect();
        m14_apply_display_pacing();
    }
    return result;
}
} // namespace

extern "C" OPENSPM_EXPORT int OPENSPM_CALL openspm_renderer_query(
    std::uint32_t requested_version,
    const openspm_host_api *host,
    openspm_renderer_api *out_api) {
    const int result = openspm_renderer_query_dev7_syncdiag_internal(
        requested_version, host, out_api);
    if (result != OPENSPM_OK) return result;

    dev7_sync_diag_reset();
    /* Keep the public dev7 contract/version string unchanged. */
    out_api->gx_sink.sync = sync_fifo_profiled_dev7;
    g_display_pacing_underlying_initialize = out_api->initialize;
    out_api->initialize = renderer_initialize_display_pacing;
    return OPENSPM_OK;
}
