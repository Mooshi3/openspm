/*
 * M14 Aurora renderer dev4.
 *
 * dev3 closes every semantic GX frame with aurora::gfx::synchronize() before
 * returning to the recompiled CPU. The revision-2 runtime proved that this can
 * wedge the PPC thread inside the FIFO store that completes GXDrawDone. Native
 * CPU-visible observations (for example PE bbox reads) already carry their own
 * ordered readback fence, so a generic frame close/present must not force an
 * unconditional GPU-idle wait.
 *
 * Keep dev3 intact underneath this wrapper and replace the semantic sync
 * callback with nonblocking boundaries. DRAW_DONE remains an explicit parser
 * completion boundary, but it is NOT an XFB/presentation boundary on Wii.
 * PRESENT_SAFEPOINT is emitted only after SPM reaches the proven Revolution OS
 * scheduler idle, where no runnable guest thread can be halfway through a GX
 * command. At that safe point Aurora drains its parser and ends/recycles the
 * native frame only if the parsed stream actually contained an original-console
 * display copy.
 *
 * This distinction is important for SPM. Presenting at every GXDrawDone exposes
 * intermediate EFB construction to the PC window: complex scenes can appear to
 * flash one object at a time and 2D/3D flips can show a black frame followed by
 * geometry rebuilding piece-by-piece. The original machine does not swap XFBs
 * merely because GXDrawDone completed. Keep the recording open across DrawDone
 * and let the next scheduler-idle display-copy safepoint own presentation.
 *
 * Count scheduler-idle safepoints, open native frames, parsed display copies and
 * DrawDone boundaries so Windows runs can distinguish actual display production
 * from parser/completion traffic.
 *
 * Astra's GX audit adds one further M14-only diagnostic sync reason. It only arms
 * an effective-state/hash capture in Aurora after the backend has proven stable
 * SEQ_LOAD. The current diagnostic uses drawLimit==0 as a bounded sentinel for
 * one complete DrawDone-delimited frame. After draining that DrawDone, notify the
 * audit without recycling/presenting the host frame. Neither hook changes GX
 * state.
 */

#include "openspm/m14_gx_audit.h"

#include <aurora/gx_audit.hpp>

#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>

#include "turbo_pacing.hpp"

#pragma push_macro("openspm_renderer_query")
#undef openspm_renderer_query
#define openspm_renderer_query openspm_renderer_query_dev3_internal
#include "openspm_renderer_aurora.cpp"
#undef openspm_renderer_query
#pragma pop_macro("openspm_renderer_query")

namespace {

bool g_dev4_logged_draw_done_begin = false;
bool g_dev4_logged_draw_done_drained = false;
bool g_dev4_logged_present_safepoint = false;
bool g_dev4_logged_seqload_audit = false;
bool g_dev4_logged_fifo_publish = false;
std::uint64_t g_dev4_present_safepoints = 0u;
std::uint64_t g_dev4_present_open_frames = 0u;
std::uint64_t g_dev4_present_copies = 0u;
std::uint64_t g_dev4_present_no_copy = 0u;
std::uint64_t g_dev4_draw_done_syncs = 0u;
std::uint64_t g_dev4_fifo_publishes = 0u;

bool dev4_should_log_count(std::uint64_t value) {
    return value <= 8u || (value != 0u && (value & (value - 1u)) == 0u);
}

/* OpenSPM fast-forward ("turbo", docs/PLAYING.md): the backend reports the
   engaged speed through openspm_renderer_turbo (v6); 0 = normal play, and then
   nothing below runs. While engaged, every game frame that ends in a
   presentation asks the gate whether it is shown: at most one image per
   display refresh, the others are submitted without a swapchain present
   (Aurora apply_turbo_present_patch.py), so FIFO vsync never throttles the
   game. The async presenter / present timeline already present on their own
   cadence: they only switch to "newest image, no interpolation". */
std::atomic<unsigned> g_m14_turbo_speed{0u};
openspm::turbo::PresentGate g_m14_turbo_gate;
double g_m14_display_period_ns = 0.0; // display refresh, set by m14_apply_display_pacing (v6)

bool m14_turbo_async_presenter() {
    static const bool async = [] {
        const char *value = std::getenv("OPENSPM_M14_ASYNC_PRESENTER");
        return value != nullptr && value[0] == '1' && value[1] == '\0';
    }();
    return async;
}

/* Right before aurora_end_frame() for a game frame (game thread). */
void m14_turbo_before_end_frame() {
    if (g_m14_turbo_speed.load(std::memory_order_relaxed) == 0u) return;
    const auto now = std::chrono::steady_clock::now().time_since_epoch();
    const auto now_ns = static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(now).count());
    const bool show = m14_turbo_async_presenter() || g_m14_turbo_gate.should_present(now_ns);
    aurora_m14_set_turbo(AURORA_M14_TURBO_ACTIVE | (show ? 0u : AURORA_M14_TURBO_SKIP_PRESENT));
}

void log_dev4_presentation_stats() {
    char message[256];
    std::snprintf(message, sizeof(message),
                  "M14 Aurora presentation stats safepoints=%llu open=%llu copies=%llu no_copy=%llu draw_done=%llu publishes=%llu.",
                  static_cast<unsigned long long>(g_dev4_present_safepoints),
                  static_cast<unsigned long long>(g_dev4_present_open_frames),
                  static_cast<unsigned long long>(g_dev4_present_copies),
                  static_cast<unsigned long long>(g_dev4_present_no_copy),
                  static_cast<unsigned long long>(g_dev4_draw_done_syncs),
                  static_cast<unsigned long long>(g_dev4_fifo_publishes));
    log_message(OPENSPM_LOG_INFO, message);
}

int publish_fifo_inline(renderer_state &state) {
    if (!state.initialized) return OPENSPM_ERROR_RUNTIME;
    if (!state.frame_open) return OPENSPM_OK;

    /* dev120: this prefix is known to end at a complete original-Wii GX command.
       In ProcessingMode::Inline, raw::publish() parses it synchronously on the
       Broadway/runtime thread before guest RAM can be reused. In Drain mode the
       upstream publish call is intentionally a no-op. */
    aurora::gx::raw::publish();
    ++g_dev4_fifo_publishes;
    if (!g_dev4_logged_fifo_publish) {
        log_message(OPENSPM_LOG_INFO,
                    "M14 Aurora command-complete same-thread FIFO publish active; Inline parsing runs before Broadway resumes.");
        g_dev4_logged_fifo_publish = true;
    }
    return OPENSPM_OK;
}

int close_frame_nonblocking(renderer_state &state) {
    const m14_cost_scope cost{m14_cost_op::draw_done};
    if (!state.initialized) return OPENSPM_ERROR_RUNTIME;
    ++g_dev4_draw_done_syncs;
    if (state.frame_open) {
        /* GXDrawDone completes previously issued GX work on the original Wii,
           but it does not select/swap an XFB. The previous OpenSPM path called
           aurora_end_frame() here, which also blits Aurora's current EFB to the
           PC swapchain. That exposed intermediate scene construction whenever
           SPM used DrawDone inside one logical display frame.

           Keep DrawDone as a complete parser boundary, but leave the Aurora
           recording frame open. The scheduler-idle PRESENT_SAFEPOINT below is
           the only ordinary presentation path and consumes the original-Wii
           display-copy event before ending the host frame. Synchronous CPU-visible
           reads still use dev3's readback path, which can submit/fence explicitly. */
        if (!g_dev4_logged_draw_done_begin) {
            log_message(OPENSPM_LOG_INFO,
                        "M14 Aurora GXDrawDone parser boundary active; host presentation is deferred to Wii display-copy safepoints.");
            g_dev4_logged_draw_done_begin = true;
        }
        aurora::gx::raw::drain();
        /* Diagnostic-only: the deep SEQ_LOAD capture is DrawDone-delimited even
           though the host recording itself intentionally remains open. */
        aurora::gx::fifo::audit::finish_seqload_frame_capture();
        if (!g_dev4_logged_draw_done_drained) {
            log_message(OPENSPM_LOG_INFO,
                        "M14 Aurora GXDrawDone FIFO drain completed without presenting/recycling the host frame.");
            g_dev4_logged_draw_done_drained = true;
        }
    }

    /* Deliberately do NOT call take_display_copy() here. A display copy that
       occurred before DrawDone must remain latched until the scheduler-idle
       presentation safepoint consumes it. */
    return OPENSPM_OK;
}

int present_display_copy_at_safepoint(renderer_state &state) {
    bool display_copy;
    if (!state.initialized) return OPENSPM_ERROR_RUNTIME;

    ++g_dev4_present_safepoints;
    if (!state.frame_open) {
        if (dev4_should_log_count(g_dev4_present_safepoints))
            log_dev4_presentation_stats();
        return OPENSPM_OK;
    }
    ++g_dev4_present_open_frames;

    /* The bridge promises PRESENT_SAFEPOINT only at Revolution OS scheduler
       idle, after all runnable guest work has yielded. Unlike an asynchronous
       VI interrupt, that means Broadway cannot be halfway through writing a GX
       command. It is therefore safe to drain Aurora's parser here. */
    aurora::gx::raw::drain();
    display_copy = aurora::gx::raw::take_display_copy();
    if (!display_copy) {
        ++g_dev4_present_no_copy;
        /* Parsed work may legitimately update the EFB without copying it yet.
           Keep the Aurora frame open so its staging/recording state remains
           contiguous until the game actually requests an XFB/display copy. */
        if (dev4_should_log_count(g_dev4_present_safepoints))
            log_dev4_presentation_stats();
        return OPENSPM_OK;
    }

    ++g_dev4_present_copies;
    ++state.display_copies;
    m14_turbo_before_end_frame();
    {
        const m14_cost_scope cost{m14_cost_op::present};
        aurora_end_frame();
    }
    state.frame_open = false;
    if (state.capture_requested) {
        state.capture_requested = false;
        aurora::gx::fifo::audit::arm_seqload_capture(1024u);
        log_message(OPENSPM_LOG_INFO, "M14 F8 GX capture armed after display boundary; next 1024 draws, ending at DrawDone.");
    }
    if (!g_dev4_logged_present_safepoint) {
        log_message(OPENSPM_LOG_INFO,
                    "M14 Aurora scheduler-idle Wii display-copy presentation boundary active; DrawDone no longer presents intermediate EFB state.");
        g_dev4_logged_present_safepoint = true;
    }
    if (dev4_should_log_count(g_dev4_present_safepoints))
        log_dev4_presentation_stats();
    return OPENSPM_OK;
}

int OPENSPM_CALL sync_fifo_v4(void *context, uint32_t reason) {
    auto *state = static_cast<renderer_state *>(context);
    if (state == nullptr) return OPENSPM_ERROR_INVALID_ARGUMENT;
    if (reason == OPENSPM_GX_SYNC_FIFO_PUBLISH) {
        return publish_fifo_inline(*state);
    }
    if (reason == OPENSPM_GX_SYNC_DRAW_DONE) {
        return close_frame_nonblocking(*state);
    }
    if (reason == OPENSPM_GX_SYNC_PRESENT_SAFEPOINT) {
        return present_display_copy_at_safepoint(*state);
    }
    if (reason == OPENSPM_M14_GX_SYNC_SEQLOAD_AUDIT) {
        /* Diagnostic-only: do not create a FIFO/parser/frame boundary here. The
           backend calls this from an already-safe scheduler-idle epoch solely to
           tell Aurora which subsequent effective draws to describe. */
        aurora::gx::fifo::audit::arm_seqload_capture(OPENSPM_M14_GX_AUDIT_DRAW_LIMIT);
        if (!g_dev4_logged_seqload_audit) {
            log_message(OPENSPM_LOG_INFO,
                        "M14 Aurora DrawDone-bounded SEQ_LOAD deep GX audit trigger accepted; renderer semantics unchanged.");
            g_dev4_logged_seqload_audit = true;
        }
        return OPENSPM_OK;
    }
    return OPENSPM_ERROR_INVALID_ARGUMENT;
}

} // namespace

extern "C" OPENSPM_EXPORT int OPENSPM_CALL openspm_renderer_query(
    uint32_t requested_version,
    const openspm_host_api *host,
    openspm_renderer_api *out_api) {
    const int result = openspm_renderer_query_dev3_internal(
        requested_version, host, out_api);
    if (result != OPENSPM_OK) return result;

    g_dev4_present_safepoints = 0u;
    g_dev4_present_open_frames = 0u;
    g_dev4_present_copies = 0u;
    g_dev4_present_no_copy = 0u;
    g_dev4_draw_done_syncs = 0u;
    g_dev4_fifo_publishes = 0u;
    g_dev4_logged_fifo_publish = false;
    g_dev4_logged_seqload_audit = false;
    out_api->renderer_version = "14.0-dev4";
    out_api->gx_sink.sync = sync_fifo_v4;
    return OPENSPM_OK;
}

