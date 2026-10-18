#include "openspm/renderer_api.h"

#include <aurora/aurora.h>
#include <aurora/event.h>
#include <aurora/gfx.hpp>
#include <aurora/gx_guest_memory.hpp>
#include <aurora/gx_raw.hpp>

#include <array>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <cstdio>
#include <thread>

#include <SDL3/SDL_keyboard.h>
#include <SDL3/SDL_mouse.h>
#include <SDL3/SDL_video.h>

namespace aurora::gx::raw { // OpenSPM M14 bbox memo (GXFrameBuffer.cpp, OPENSPM_M14_BBOX_MEMO=1)
bool m14_bbox_memo_lookup(std::array<std::uint16_t, 4> &out) noexcept;
void m14_bbox_memo_store(const std::array<std::uint16_t, 4> &bounds) noexcept;
void m14_snapshot_sync() noexcept; // SPM snapshot slots (GXFrameBuffer.cpp)
extern std::uint64_t g_m14_audit_draw_ordinal; // GX capture draw number (command_processor.cpp)
// OpenSPM bbox fast (apply_bbox_fast_patch.py, OPENSPM_M14_BBOX_FAST=1|verify): 0 off, 1 on, 2 verify.
int m14_bbox_fast_mode() noexcept;
bool m14_bbox_fast_read(std::array<std::uint16_t, 4> &out, unsigned &reason) noexcept;
void m14_bbox_fast_take_draw_stats(std::array<std::uint64_t, 4> &out) noexcept;
} // namespace aurora::gx::raw

namespace aurora::window {
void request_frame_buffer_resize(); // lib/window.cpp: explicit graphics apply (FutureResize)
} // namespace aurora::window


namespace {

struct renderer_state {
    const openspm_host_api *host = nullptr;
    openspm_gx_guest_memory guest_memory{};
    bool initialized = false;
    bool frame_open = false;
    bool exit_requested = false;
    bool begin_frame_wait_reported = false;
    bool begin_frame_recovery_reported = false;
    uint64_t submitted_fifo_bytes = 0;
    uint64_t gpu_syncs = 0;
    uint64_t display_copies = 0;
    bool capture_requested = false;
    bool bounds_cached = false;
    openspm_gx_raster_bounds cached_bounds{};
    uint64_t bounds_reads = 0;
    uint64_t bounds_cache_hits = 0;
    uint64_t bounds_slow_reads = 0;
};

renderer_state g_renderer;

void log_message(openspm_log_level level, const char *message) {
    if (g_renderer.host != nullptr && g_renderer.host->log != nullptr) {
        g_renderer.host->log(g_renderer.host->context, level, message);
    }
}

enum class m14_cost_op : unsigned { begin_frame, fifo_submit, draw_done, present, count };
struct m14_cost_counter { uint64_t calls = 0, slow = 0, max_us = 0, window_us = 0, window_calls = 0; };
std::array<m14_cost_counter, static_cast<unsigned>(m14_cost_op::count)> g_m14_costs{};
struct m14_cost_scope {
    m14_cost_op op;
    std::chrono::steady_clock::time_point start = std::chrono::steady_clock::now();
    explicit m14_cost_scope(m14_cost_op value) : op(value) {}
    ~m14_cost_scope() {
        const auto elapsed = std::chrono::duration_cast<std::chrono::microseconds>(
            std::chrono::steady_clock::now() - start).count();
        auto& counter = g_m14_costs[static_cast<unsigned>(op)];
        ++counter.calls;
        ++counter.window_calls;
        counter.window_us += static_cast<uint64_t>(elapsed);
        if (elapsed < 16000) return;
        ++counter.slow;
        const bool new_max = static_cast<uint64_t>(elapsed) > counter.max_us;
        if (new_max) counter.max_us = static_cast<uint64_t>(elapsed);
        if (counter.slow > 8u && (counter.slow & (counter.slow - 1u)) != 0u && !new_max) return;
        constexpr const char* names[] = {"begin_frame", "fifo_submit", "draw_done_drain", "display_present"};
        char message[256];
        std::snprintf(message, sizeof(message),
            "M14 renderer cost op=%s packet=%u calls=%llu slow=%llu wall_us=%lld max_us=%llu",
            names[static_cast<unsigned>(op)], aurora::gfx::current_frame(),
            static_cast<unsigned long long>(counter.calls), static_cast<unsigned long long>(counter.slow),
            static_cast<long long>(elapsed), static_cast<unsigned long long>(counter.max_us));
        log_message(OPENSPM_LOG_INFO, message);
    }
};

char ascii_upper(char value) {
    return value >= 'a' && value <= 'z' ? static_cast<char>(value - ('a' - 'A')) : value;
}

bool ascii_iequals(const char *left, const char *right) {
    if (left == nullptr || right == nullptr) return false;
    while (*left != '\0' && *right != '\0') {
        if (ascii_upper(*left) != ascii_upper(*right)) return false;
        ++left;
        ++right;
    }
    return *left == '\0' && *right == '\0';
}

bool m14_vsync_enabled_from_environment() {
    const char *value = std::getenv("OPENSPM_M14_VSYNC");
    if (value == nullptr || *value == '\0') return true;
    if (ascii_iequals(value, "0") ||
        ascii_iequals(value, "OFF") ||
        ascii_iequals(value, "FALSE") ||
        ascii_iequals(value, "NO")) {
        return false;
    }
    return true;
}

bool parse_backend(const char *name, AuroraBackend &out) {
    if (name == nullptr || *name == '\0' || ascii_iequals(name, "AUTO")) {
        out = BACKEND_AUTO;
        return true;
    }
    if (ascii_iequals(name, "D3D12")) {
        out = BACKEND_D3D12;
        return true;
    }
    if (ascii_iequals(name, "D3D11")) {
        out = BACKEND_D3D11;
        return true;
    }
    if (ascii_iequals(name, "VULKAN")) {
        out = BACKEND_VULKAN;
        return true;
    }
    if (ascii_iequals(name, "WEBGPU")) {
        out = BACKEND_WEBGPU;
        return true;
    }
    return false;
}

bool map_guest_for_aurora(void *context,
                          std::uint32_t guest_address,
                          std::uint32_t min_size,
                          aurora::gx::guest_memory::Span *out_span) {
    auto *state = static_cast<renderer_state *>(context);
    openspm_gx_guest_span span{};
    if (state == nullptr || out_span == nullptr || state->guest_memory.map == nullptr) {
        return false;
    }
    if (state->guest_memory.map(state->guest_memory.context,
                                guest_address, min_size, &span) != OPENSPM_OK ||
        span.bytes == nullptr || span.size < min_size) {
        return false;
    }
    out_span->data = span.bytes;
    out_span->size = span.size;
    return true;
}

/* OpenSPM QoL: F11 toggles windowed <-> borderless fullscreen at
   runtime (exclusive fullscreen toggles back to windowed). The EFB follows the
   window size; the 4:3 policy keeps black bars. OPENSPM_FULLSCREEN_HOTKEY=0
   disables it. */
/* OpenSPM QoL: the raw-Wii resize patch defers live window resizes (dragging a
   window must not stall the game), leaving the swapchain at its old size. Apply
   the new size explicitly after a mode switch, and after a window drag once it
   has been still for 300 ms, so the 4:3 bars always fit the window.
   OPENSPM_LIVE_RESIZE=0 keeps the old behaviour (size fixed at startup). */
std::chrono::steady_clock::time_point g_m14_resize_pending_since{};
bool g_m14_resize_pending = false;

bool m14_live_resize_enabled() {
    static const bool enabled = [] {
        const char *value = std::getenv("OPENSPM_LIVE_RESIZE");
        return value == nullptr || value[0] != '0';
    }();
    return enabled;
}

void m14_request_graphics_resize() {
    if (!m14_live_resize_enabled()) return;
    aurora::window::request_frame_buffer_resize();
    g_m14_resize_pending = false;
}

void m14_note_window_resized(const AuroraWindowSize &size) {
    if (!m14_live_resize_enabled()) return;
    /* Applying a resize posts its own WINDOW_RESIZED; an event at the size
       already seen must not re-arm the apply (that looped every 300 ms). */
    static std::uint32_t s_width = 0, s_height = 0;
    if (size.native_fb_width == s_width && size.native_fb_height == s_height) return;
    s_width = size.native_fb_width;
    s_height = size.native_fb_height;
    g_m14_resize_pending = true;
    g_m14_resize_pending_since = std::chrono::steady_clock::now();
}

void m14_poll_pending_resize() {
    if (g_m14_resize_pending &&
        std::chrono::steady_clock::now() - g_m14_resize_pending_since >= std::chrono::milliseconds(300)) {
        m14_request_graphics_resize();
        log_message(OPENSPM_LOG_INFO, "M14 window resize applied (swapchain and 4:3 fit follow the window).");
    }
}

/* OpenSPM QoL: hide the mouse cursor while fullscreen (borderless or
   exclusive); show it in a window. OPENSPM_HIDE_CURSOR=0 disables. */
void m14_update_cursor_for_window(SDL_Window *window) {
    static const bool enabled = [] {
        const char *value = std::getenv("OPENSPM_HIDE_CURSOR");
        return value == nullptr || value[0] != '0';
    }();
    if (!enabled || window == nullptr) return;
    (void)SDL_SyncWindow(window); // fullscreen changes may apply asynchronously
    if ((SDL_GetWindowFlags(window) & SDL_WINDOW_FULLSCREEN) != 0) SDL_HideCursor();
    else SDL_ShowCursor();
}

void toggle_fullscreen_hotkey() {
    static const bool enabled = [] {
        const char *value = std::getenv("OPENSPM_FULLSCREEN_HOTKEY");
        return value == nullptr || value[0] != '0';
    }();
    if (!enabled) return;
    int count = 0;
    SDL_Window **windows = SDL_GetWindows(&count);
    SDL_Window *window = windows != nullptr && count > 0 ? windows[0] : nullptr;
    SDL_free(windows);
    if (window == nullptr) return;
    const bool fullscreen = (SDL_GetWindowFlags(window) & SDL_WINDOW_FULLSCREEN) != 0;
    bool ok;
    if (fullscreen) {
        ok = SDL_SetWindowFullscreen(window, false);
    } else {
        ok = SDL_SetWindowFullscreenMode(window, nullptr) && SDL_SetWindowFullscreen(window, true);
    }
    if (ok) {
        m14_update_cursor_for_window(window);
        m14_request_graphics_resize();
    }
    char message[192];
    std::snprintf(message, sizeof(message), "M14 fullscreen hotkey: %s -> %s%s%s", fullscreen ? "fullscreen" : "windowed",
                  fullscreen ? "windowed" : "borderless fullscreen", ok ? "" : " failed: ", ok ? "" : SDL_GetError());
    log_message(ok ? OPENSPM_LOG_INFO : OPENSPM_LOG_WARN, message);
}

/* OpenSPM QoL: F12 saves a screenshot of the next presented frame (v6). */
bool g_m14_screenshot_requested = false;
/* OpenSPM QoL: F1 toggles the OpenSPM menu preview (openspm_overlay.hpp, v6). */
bool g_m14_overlay_toggle_requested = false;

void pump_events(renderer_state &state) {
    m14_poll_pending_resize();
    const AuroraEvent *event = aurora_update();
    if (event == nullptr) return;
    while (event->type != AURORA_NONE) {
        if (event->type == AURORA_WINDOW_RESIZED) m14_note_window_resized(event->windowSize);
        if (event->type == AURORA_SDL_EVENT && event->sdl.type == SDL_EVENT_KEY_DOWN &&
            event->sdl.key.scancode == SDL_SCANCODE_F8 && !event->sdl.key.repeat) {
            state.capture_requested = true;
            log_message(OPENSPM_LOG_INFO, "M14 F8 GX capture requested; next display boundary arms a bounded frame.");
        }
        /* F11 only: the backend reads the keyboard globally and Enter is Wii
           Remote 2, so Alt+Enter would also press a game button. */
        if (event->type == AURORA_SDL_EVENT && event->sdl.type == SDL_EVENT_KEY_DOWN && !event->sdl.key.repeat &&
            event->sdl.key.scancode == SDL_SCANCODE_F11) {
            toggle_fullscreen_hotkey();
        }
        if (event->type == AURORA_SDL_EVENT && event->sdl.type == SDL_EVENT_KEY_DOWN && !event->sdl.key.repeat &&
            event->sdl.key.scancode == SDL_SCANCODE_F12) {
            g_m14_screenshot_requested = true;
        }
        if (event->type == AURORA_SDL_EVENT && event->sdl.type == SDL_EVENT_KEY_DOWN && !event->sdl.key.repeat &&
            event->sdl.key.scancode == SDL_SCANCODE_F1) {
            g_m14_overlay_toggle_requested = true;
        }
        if (event->type == AURORA_EXIT) {
            if (!state.exit_requested) {
                log_message(OPENSPM_LOG_INFO,
                            "M14 Aurora received host window exit/quit event; later renderer service=-6 results are shutdown fallout, not a GX diagnosis.");
            }
            state.exit_requested = true;
        }
        ++event;
    }
}

int ensure_frame(renderer_state &state) {
    const m14_cost_scope cost{m14_cost_op::begin_frame};
    bool waited_for_host_frame = false;
    if (!state.initialized || state.exit_requested) return OPENSPM_ERROR_RUNTIME;

    /* The first native GX frame can stay open through a long Nintendo SDK/game
       initialization sequence. Pump the host window on every FIFO submission,
       not only when opening a new frame, so Windows does not mark a healthy
       native bring-up as hung merely because no present has happened yet. */
    pump_events(state);
    if (state.exit_requested) return OPENSPM_ERROR_RUNTIME;
    if (state.frame_open) return OPENSPM_OK;

    /* Aurora's begin-frame contract is deliberately non-fatal: it returns false
       while the host window is paused/not-presentable, while a surface is being
       refreshed, or when a mapped staging slot must be retried. Its own example
       loop simply waits for a later host frame. OpenSPM cannot do the old
       equivalent of returning OPENSPM_ERROR_RUNTIME here because the caller is
       Broadway's GX FIFO transport: that makes the guest believe a 32-byte write
       failed and the untranslated continuation then consumes corrupt/stale GX
       state. Keep the guest write pending, continue pumping host events, and
       retry until Aurora accepts a frame or the user actually closes the window. */
    while (!aurora_begin_frame()) {
        waited_for_host_frame = true;
        if (!state.begin_frame_wait_reported) {
            log_message(OPENSPM_LOG_WARN,
                        "M14 Aurora begin-frame temporarily unavailable; retaining guest GX FIFO write and retrying instead of returning renderer runtime failure.");
            state.begin_frame_wait_reported = true;
        }
        pump_events(state);
        if (state.exit_requested) {
            log_message(OPENSPM_LOG_INFO,
                        "M14 Aurora begin-frame retry stopped because the OpenSPM window requested exit.");
            return OPENSPM_ERROR_RUNTIME;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    if (waited_for_host_frame && !state.begin_frame_recovery_reported) {
        log_message(OPENSPM_LOG_INFO,
                    "M14 Aurora begin-frame recovered; retained guest GX FIFO write is continuing normally.");
        state.begin_frame_recovery_reported = true;
    }
    state.frame_open = true;
    return OPENSPM_OK;
}

/* Submit all GX work accumulated in the current Aurora frame. Aurora's public
   end-frame path also presents; that is intentionally conservative for the
   first M14 renderer. Later we can split submit-without-present from display
   copy once correctness is established. */
int close_frame(renderer_state &state, bool wait_for_gpu) {
    if (!state.initialized) return OPENSPM_ERROR_RUNTIME;
    if (state.frame_open) {
        aurora::gx::raw::drain();
        aurora_end_frame();
        aurora::gfx::synchronize();
        state.frame_open = false;
    }

    if (aurora::gx::raw::take_display_copy()) ++state.display_copies;

    if (wait_for_gpu) {
        /* A four-word bbox copy is used as the first correctness-oriented GX
           completion fence. Because it is queued after the frame submission,
           successful synchronous mapping proves all preceding GX GPU work is
           complete before the recompiled game observes GXDrawDone. */
        std::array<std::uint16_t, 4> ignored{};
        if (!aurora::gx::raw::read_bbox(ignored)) {
            log_message(OPENSPM_LOG_ERROR, "Aurora GX completion fence failed");
            return OPENSPM_ERROR_RUNTIME;
        }
        ++state.gpu_syncs;
    }
    return OPENSPM_OK;
}

int OPENSPM_CALL submit_fifo(void *context,
                             const uint8_t *bytes,
                             uint32_t size,
                             const openspm_gx_guest_memory *guest_memory) {
    auto *state = static_cast<renderer_state *>(context);
    int result;
    if (state == nullptr || (size != 0u && bytes == nullptr)) {
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    }
    if (guest_memory != nullptr && guest_memory->map != nullptr) {
        state->guest_memory = *guest_memory;
        aurora::gx::guest_memory::set_mapper(state, map_guest_for_aurora);
    }
    result = ensure_frame(*state);
    if (result != OPENSPM_OK) return result;
    if (size != 0u) {
        state->bounds_cached = false;
        aurora::gx::raw::append_fifo(bytes, size);
        state->submitted_fifo_bytes += size;
    }
    return OPENSPM_OK;
}

int OPENSPM_CALL sync_fifo(void *context, uint32_t reason) {
    auto *state = static_cast<renderer_state *>(context);
    if (state == nullptr || reason != OPENSPM_GX_SYNC_DRAW_DONE) {
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    }
    /* DRAW_DONE is a complete-command boundary supplied by OpenSPM's neutral
       GX bridge. Do not ask Aurora to parse arbitrary FIFO fragments earlier. */
    return close_frame(*state, true);
}

// OpenSPM M14 bbox trace: armed with the copy-triggered GX capture (v6,
// OPENSPM_M14_COPY_CAPTURE); logs the next CPU bbox register writes and GPU
// bbox maps in logical coordinates (flip card diagnosis).
unsigned g_m14_bbox_trace_budget = 0u;
// Reads of the bbox register since the last present, and a hook (set by v6)
// called when a frame first bursts past 16 reads: the flip and door effects
// start with such a burst right before they capture the screen.
unsigned g_m14_bbox_frame_reads = 0u;
void (*g_m14_bbox_burst_hook)() = nullptr;
// One-shot hook (set by v6) run at the next bbox read, before it: snapcapture.
void (*g_m14_bbox_next_read_hook)() = nullptr;
// Per-second renderer budget window (reported by v6 after present).
uint64_t g_m14_bbox_window_maps = 0u, g_m14_bbox_window_us = 0u;
// OpenSPM bbox fast per-second window (reported by v6 as "M14 bbox fast:").
// fast[reason] counts CPU-answered reads (index 1 no_draws, 2 offscreen,
// 3 degenerate); in verify mode they are the reads the CPU would answer.
struct m14_bbox_fast_window {
    uint64_t reads = 0u, cached = 0u, gpu = 0u, mismatches = 0u, us = 0u;
    std::array<uint64_t, 4> fast{};
};
m14_bbox_fast_window g_m14_bbox_fast_window;
uint64_t g_m14_bbox_fast_mismatches_total = 0u;

void m14_bbox_fast_verify(const std::array<std::uint16_t, 4> &cpu, unsigned reason,
                          const std::array<std::uint16_t, 4> &gpu) {
    if (cpu == gpu) return;
    ++g_m14_bbox_fast_window.mismatches;
    const uint64_t n = ++g_m14_bbox_fast_mismatches_total;
    if (n <= 16u || (n & (n - 1u)) == 0u) {
        char message[256];
        std::snprintf(message, sizeof(message),
                      "M14 bbox fast verify MISMATCH n=%llu reason=%s cpu=%u,%u,%u,%u gpu=%u,%u,%u,%u draw=%llu",
                      static_cast<unsigned long long>(n),
                      reason == 1u ? "no_draws" : reason == 2u ? "offscreen" : "degenerate",
                      unsigned(cpu[0]), unsigned(cpu[1]), unsigned(cpu[2]), unsigned(cpu[3]),
                      unsigned(gpu[0]), unsigned(gpu[1]), unsigned(gpu[2]), unsigned(gpu[3]),
                      static_cast<unsigned long long>(aurora::gx::raw::g_m14_audit_draw_ordinal));
        log_message(OPENSPM_LOG_WARN, message);
    }
}

int OPENSPM_CALL read_bounds(void *context,
                             openspm_gx_raster_bounds *out_bounds) {
    auto *state = static_cast<renderer_state *>(context);
    std::array<std::uint16_t, 4> bounds{};
    if (state == nullptr || out_bounds == nullptr) {
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    }

    ++state->bounds_reads;
    const int fast_mode = aurora::gx::raw::m14_bbox_fast_mode();
    const auto fast_start = std::chrono::steady_clock::now();
    if (fast_mode != 0) ++g_m14_bbox_fast_window.reads;
    if (g_m14_bbox_next_read_hook != nullptr) {
        auto hook = g_m14_bbox_next_read_hook;
        g_m14_bbox_next_read_hook = nullptr;
        hook();
    }
    if (++g_m14_bbox_frame_reads == 16u && g_m14_bbox_burst_hook != nullptr) g_m14_bbox_burst_hook();
    // Any submitted FIFO byte or direct bbox write invalidates this snapshot.
    // Four consecutive CPU register reads need one fence, not four GPU maps.
    if (state->bounds_cached) {
        ++state->bounds_cache_hits;
        if (fast_mode != 0) ++g_m14_bbox_fast_window.cached;
        *out_bounds = state->cached_bounds;
        return OPENSPM_OK;
    }
    // The memo key must include every draw issued before this read: parse the
    // pending FIFO first (cheap; the GPU is not waited on).
    if (state->frame_open) {
        aurora::gx::raw::drain();
        aurora::gx::raw::m14_snapshot_sync(); // a bbox read is a sync point for SPM's captures
    }
    // OpenSPM bbox fast: no draw since the box was written can have touched a
    // pixel, so the box is the written value (exact; see bbox_fast.hpp).
    std::array<std::uint16_t, 4> fast_bounds{};
    unsigned fast_reason = 0u;
    const bool fast_hit = fast_mode != 0 && aurora::gx::raw::m14_bbox_fast_read(fast_bounds, fast_reason);
    if (fast_hit) ++g_m14_bbox_fast_window.fast[fast_reason & 3u];
    if (fast_hit && fast_mode == 1) {
        out_bounds->left = fast_bounds[0];
        out_bounds->right = fast_bounds[1];
        out_bounds->top = fast_bounds[2];
        out_bounds->bottom = fast_bounds[3];
        out_bounds->valid = 1u;
        state->cached_bounds = *out_bounds;
        state->bounds_cached = true;
        g_m14_bbox_fast_window.us += static_cast<uint64_t>(std::chrono::duration_cast<std::chrono::microseconds>(
            std::chrono::steady_clock::now() - fast_start).count());
        return OPENSPM_OK;
    }
    // Verify compares with the GPU itself, never with a remembered box.
    if (fast_mode != 2 && state->frame_open && aurora::gx::raw::m14_bbox_memo_lookup(bounds)) {
        out_bounds->left = bounds[0];
        out_bounds->right = bounds[1];
        out_bounds->top = bounds[2];
        out_bounds->bottom = bounds[3];
        out_bounds->valid = 1u;
        state->cached_bounds = *out_bounds;
        state->bounds_cached = true;
        return OPENSPM_OK;
    }
    const auto start = std::chrono::steady_clock::now();
    if (state->frame_open && !aurora::gx::raw::submit_for_readback()) {
        state->frame_open = false;
        log_message(OPENSPM_LOG_ERROR, "M14 submit-only bbox continuation failed");
        return OPENSPM_ERROR_RUNTIME;
    }
    const auto submitted = std::chrono::steady_clock::now();
    if (!aurora::gx::raw::read_bbox(bounds)) return OPENSPM_ERROR_RUNTIME;
    aurora::gx::raw::m14_bbox_memo_store(bounds);
    const auto finished = std::chrono::steady_clock::now();
    ++state->gpu_syncs;
    if (fast_mode != 0) {
        ++g_m14_bbox_fast_window.gpu;
        if (fast_hit) m14_bbox_fast_verify(fast_bounds, fast_reason, bounds);
        g_m14_bbox_fast_window.us += static_cast<uint64_t>(
            std::chrono::duration_cast<std::chrono::microseconds>(finished - fast_start).count());
    }
    out_bounds->left = bounds[0];
    out_bounds->right = bounds[1];
    out_bounds->top = bounds[2];
    out_bounds->bottom = bounds[3];
    out_bounds->valid = 1u;
    state->cached_bounds = *out_bounds;
    state->bounds_cached = true;
    const auto submit_us = std::chrono::duration_cast<std::chrono::microseconds>(submitted - start).count();
    const auto map_us = std::chrono::duration_cast<std::chrono::microseconds>(finished - submitted).count();
    ++g_m14_bbox_window_maps;
    g_m14_bbox_window_us += static_cast<uint64_t>(submit_us + map_us);
    const bool slow = submit_us + map_us >= 16000;
    if (slow) ++state->bounds_slow_reads;
    if (g_m14_bbox_trace_budget != 0u) {
        --g_m14_bbox_trace_budget;
        char trace[160];
        std::snprintf(trace, sizeof(trace), "M14 bbox trace read map=%llu draw=%llu l,r,t,b=%u,%u,%u,%u",
                      static_cast<unsigned long long>(state->gpu_syncs),
                      static_cast<unsigned long long>(aurora::gx::raw::g_m14_audit_draw_ordinal), unsigned(bounds[0]),
                      unsigned(bounds[1]), unsigned(bounds[2]), unsigned(bounds[3]));
        log_message(OPENSPM_LOG_INFO, trace);
    }
    const uint64_t sample = slow ? state->bounds_slow_reads : state->gpu_syncs;
    if (sample <= 8u || (sample & (sample - 1u)) == 0u) {
        char message[384];
        std::snprintf(message, sizeof(message),
            "M14 bbox submit-only reads=%llu maps=%llu hits=%llu submit_us=%lld map_us=%lld slow=%u bounds=%u,%u,%u,%u; EFB preserved, no present.",
            static_cast<unsigned long long>(state->bounds_reads),
            static_cast<unsigned long long>(state->gpu_syncs),
            static_cast<unsigned long long>(state->bounds_cache_hits),
            static_cast<long long>(submit_us), static_cast<long long>(map_us), slow ? 1u : 0u,
            unsigned(bounds[0]), unsigned(bounds[1]), unsigned(bounds[2]), unsigned(bounds[3]));
        log_message(OPENSPM_LOG_INFO, message);
    }
    return OPENSPM_OK;
}

int OPENSPM_CALL write_bound(void *context,
                             uint32_t address,
                             uint16_t value) {
    auto *state = static_cast<renderer_state *>(context);
    uint32_t index;
    int result;
    if (state == nullptr) return OPENSPM_ERROR_INVALID_ARGUMENT;
    if (address == OPENSPM_GX_PE_BBOX_LEFT) index = 0u;
    else if (address == OPENSPM_GX_PE_BBOX_RIGHT) index = 1u;
    else if (address == OPENSPM_GX_PE_BBOX_TOP) index = 2u;
    else if (address == OPENSPM_GX_PE_BBOX_BOTTOM) index = 3u;
    else return OPENSPM_ERROR_INVALID_ARGUMENT;

    // Record this CPU write after prior FIFO work, without submitting or
    // presenting. The same ordered encoder task handles FIFO BP bbox writes.
    result = ensure_frame(*state);
    if (result != OPENSPM_OK) return result;
    state->bounds_cached = false;
    if (g_m14_bbox_trace_budget != 0u) {
        --g_m14_bbox_trace_budget;
        char trace[128];
        std::snprintf(trace, sizeof(trace), "M14 bbox trace write draw=%llu index=%u value=%u",
                      static_cast<unsigned long long>(aurora::gx::raw::g_m14_audit_draw_ordinal), index,
                      unsigned(value));
        log_message(OPENSPM_LOG_INFO, trace);
    }
    aurora::gx::raw::drain();
    aurora::gx::raw::write_bbox(index, value);
    return OPENSPM_OK;
}

int OPENSPM_CALL renderer_initialize(void *context,
                                     const openspm_launch_desc *launch) {
    auto *state = static_cast<renderer_state *>(context);
    AuroraBackend desired_backend = BACKEND_AUTO;
    if (state == nullptr || launch == nullptr) return OPENSPM_ERROR_INVALID_ARGUMENT;
    if (!parse_backend(launch->graphics_backend_utf8, desired_backend)) {
        log_message(OPENSPM_LOG_ERROR,
                    "Unknown graphics backend; use AUTO, D3D12, D3D11, VULKAN, or WEBGPU");
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    }

    const openspm_host_api *host = state->host;
    *state = {};
    state->host = host;

    g_m14_costs = {};
    AuroraConfig config{};
    config.appName = "OpenSPM";
    config.userPath = launch->user_root_utf8;
    config.cachePath = launch->user_root_utf8;
    config.desiredBackend = desired_backend;
    config.vsync = m14_vsync_enabled_from_environment();
    config.windowWidth = 640u;
    config.windowHeight = 480u;
    /* OpenSPM owns MEM1/MEM2. Patched Aurora receives bounded guest spans only
       through gx_guest_memory; do not allocate a duplicate emulated RAM map. */
    config.mem1Size = 0u;
    config.mem2Size = 0u;

    char arg0[] = "OpenSPM";
    char *argv[] = {arg0, nullptr};
    const AuroraInfo info = aurora_initialize(1, argv, &config);
    if (info.window == nullptr) {
        log_message(OPENSPM_LOG_ERROR, "Aurora did not create a native presentation window");
        return OPENSPM_ERROR_RUNTIME;
    }
    state->initialized = true;
    state->exit_requested = false;
    state->frame_open = false;

    log_message(OPENSPM_LOG_INFO,
                config.vsync
                    ? "Aurora native GX renderer initialized (M14 correctness mode; vsync=on)"
                    : "Aurora native GX renderer initialized (M14 presentation A/B; vsync=off, Aurora prefers Mailbox then Immediate)");
    return OPENSPM_OK;
}

void OPENSPM_CALL renderer_shutdown(void *context) {
    auto *state = static_cast<renderer_state *>(context);
    if (state == nullptr || !state->initialized) return;
    (void)close_frame(*state, false);
    aurora::gx::guest_memory::clear_mapper();
    aurora_shutdown();
    state->initialized = false;
    state->frame_open = false;
}

} // namespace

extern "C" OPENSPM_EXPORT int OPENSPM_CALL openspm_renderer_query(
    uint32_t requested_version,
    const openspm_host_api *host,
    openspm_renderer_api *out_api) {
    if (out_api == nullptr || host == nullptr ||
        requested_version != OPENSPM_RENDERER_API_VERSION ||
        out_api->header.struct_size < sizeof(*out_api)) {
        return OPENSPM_ERROR_ABI_MISMATCH;
    }

    g_renderer = {};
    g_renderer.host = host;
    std::memset(out_api, 0, sizeof(*out_api));
    out_api->header.struct_size = sizeof(*out_api);
    out_api->header.abi_version = OPENSPM_RENDERER_API_VERSION;
    out_api->renderer_id = "openspm.aurora-gx";
    out_api->renderer_version = "14.0-dev3";
    out_api->capabilities = OPENSPM_RENDERER_CAP_GX_STREAM |
                            OPENSPM_RENDERER_CAP_RASTER_BOUNDS |
                            OPENSPM_RENDERER_CAP_PRESENT;
    out_api->renderer_context = &g_renderer;
    out_api->gx_sink.context = &g_renderer;
    out_api->gx_sink.submit_fifo = submit_fifo;
    out_api->gx_sink.sync = sync_fifo;
    out_api->gx_sink.read_bounds = read_bounds;
    out_api->gx_sink.write_bound = write_bound;
    out_api->initialize = renderer_initialize;
    out_api->shutdown = renderer_shutdown;
    return OPENSPM_OK;
}

