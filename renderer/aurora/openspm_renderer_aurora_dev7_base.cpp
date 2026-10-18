/*
 * M14 Aurora renderer dev7 (build entry retained at the historical v6 path).
 *
 * dev6 batches the Broadway GX byte stream into 16 KiB host submissions. The
 * 010442 Windows run proved that the scary late nested-CALL_DL fatal was not a
 * gameplay frontier: Aurora first received a host window exit event, then the
 * guest happened to keep executing the remainder of its translated write-
 * gather chunk. dev6 consequently retried the same failed buffered submission
 * and its shutdown path later drained a partial command tail.
 *
 * Keep dev6's successful transport semantics, but make a renderer service
 * failure terminal for the current host run. Never replay the same failed FIFO
 * batch: latch the first error, discard the uncommitted batch, and make later
 * sink calls return that error without touching Aurora again. On intentional
 * host-window exit, shutdown abandons the open parser frame instead of asking
 * Aurora to parse/present a command stream that Broadway was interrupted while
 * producing.
 *
 * The old automatic SEQ_LOAD deep-audit sync is also retired from ordinary
 * player runs. That sync reason is diagnostic-only by contract and must not
 * drain/present/change guest behavior, so dev7 acknowledges it without flushing
 * the transport. Manual bounded capture remains available through the renderer's
 * separate F8 path.
 *
 * Runtime 021126 additionally proved that rare 70-155 ms begin-frame stalls live
 * entirely inside Aurora's SDL event pump, not GX parsing, frame-slot ownership,
 * staging maps, or begin-frame recording. The base renderer historically called
 * aurora_update() on every FIFO submission as an early bring-up watchdog. Now
 * that gameplay is live, throttle redundant host event pumps to a 16 ms cadence.
 *
 * Runtime 030834 then exposed the more important ordering contract: Aurora's
 * SDL resize handler synchronizes the render worker, clears graphics caches and
 * replaces framebuffer/depth resources immediately inside aurora_update(). The
 * upstream Aurora loop always calls update before begin_frame; OpenSPM had been
 * polling even while a recording frame was open. Defer host event processing
 * while g_renderer.frame_open so resize/surface mutation only occurs at a host
 * frame boundary. Begin-frame retry/startup paths still pump normally.
 */

#include <aurora/aurora.h>
#include <aurora/event.h>

#include "openspm/m14_gx_audit.h"

#include <array>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>

// Dolphin HD texture packs (openspm_texture_packs.hpp, included later by the
// OpenSPM menu): indexed once, right after Aurora is up.
namespace openspm_texpacks {
void startup() noexcept;
} // namespace openspm_texpacks

namespace {

const AuroraEvent *openspm_aurora_update_throttled();

const char *openspm_aurora_log_level(AuroraLogLevel level) {
    switch (level) {
    case LOG_DEBUG: return "debug";
    case LOG_INFO: return "info";
    case LOG_WARNING: return "warning";
    case LOG_ERROR: return "error";
    case LOG_FATAL: return "fatal";
    default: return "unknown";
    }
}

void openspm_aurora_log_capture(AuroraLogLevel level,
                                const char *module,
                                const char *message,
                                unsigned int len) {
    const char *safe_module = module != nullptr ? module : "";
    std::fprintf(stderr, "[%s] [%s] ", openspm_aurora_log_level(level), safe_module);
    if (message != nullptr && len != 0u) {
        (void)std::fwrite(message, 1u, static_cast<std::size_t>(len), stderr);
    }
    std::fputc('\n', stderr);
    std::fflush(stderr);
}

AuroraInfo openspm_aurora_initialize_logged(int argc,
                                            char **argv,
                                            const AuroraConfig *config) {
    if (config == nullptr) return aurora_initialize(argc, argv, config);
    AuroraConfig captured = *config;
    captured.logCallback = openspm_aurora_log_capture;
    captured.logLevel = LOG_DEBUG;
    // [TexturePacks] dump=1: Aurora writes each unreplaced texture once (Dolphin-named PNG).
    const char *dump = std::getenv("OPENSPM_TEXTURE_DUMP");
    captured.allowTextureDumps = dump != nullptr && dump[0] == '1';
    const AuroraInfo info = aurora_initialize(argc, argv, &captured);
    if (info.window != nullptr) openspm_texpacks::startup();
    return info;
}

} // namespace

#pragma push_macro("aurora_initialize")
#undef aurora_initialize
#define aurora_initialize openspm_aurora_initialize_logged

#pragma push_macro("aurora_update")
#undef aurora_update
#define aurora_update openspm_aurora_update_throttled

#pragma push_macro("openspm_renderer_query")
#undef openspm_renderer_query
#define openspm_renderer_query openspm_renderer_query_dev5_internal
#include "openspm_renderer_aurora_v5.cpp"
#undef openspm_renderer_query
#pragma pop_macro("openspm_renderer_query")

#undef aurora_update
#pragma pop_macro("aurora_update")

#undef aurora_initialize
#pragma pop_macro("aurora_initialize")

namespace {

constexpr std::size_t kDev6FifoBatchCapacity = 16u * 1024u;
constexpr auto kDev7HostEventPumpInterval = std::chrono::milliseconds{16};
std::array<std::uint8_t, kDev6FifoBatchCapacity> g_dev6_fifo_batch{};
std::uint32_t g_dev6_fifo_batch_size = 0u;
openspm_gx_guest_memory g_dev6_guest_memory{};
bool g_dev6_have_guest_memory = false;
openspm_gx_renderer_sink g_dev6_underlying_sink{};
openspm_renderer_initialize_fn g_dev6_underlying_initialize = nullptr;
openspm_renderer_shutdown_fn g_dev6_underlying_shutdown = nullptr;
std::uint64_t g_dev6_submit_calls = 0u;
std::uint64_t g_dev6_flushes = 0u;
std::uint64_t g_dev6_flushed_bytes = 0u;
bool g_dev6_logged_batching = false;
int g_dev6_latched_fifo_error = OPENSPM_OK;
bool g_dev6_logged_latched_error = false;
bool g_dev6_logged_shutdown_discard = false;
bool g_dev6_logged_auto_audit_retired = false;
std::chrono::steady_clock::time_point g_dev7_last_host_event_pump{};
std::uint64_t g_dev7_host_event_pumps = 0u;
std::uint64_t g_dev7_host_event_skips = 0u;
std::uint64_t g_dev7_host_event_frame_defers = 0u;
bool g_dev7_logged_event_throttle = false;
bool g_dev7_logged_event_frame_defer = false;
bool g_dev7_logged_event_stats = false;

const AuroraEvent *openspm_aurora_update_throttled() {
    static const AuroraEvent no_events{.type = AURORA_NONE};

    /* Aurora processes WINDOW_PIXEL_SIZE_CHANGED synchronously inside update(),
       where it can recreate swapchain/EFB/depth resources. Upstream always does
       that before begin_frame(). Do not mutate those resources while OpenSPM is
       intentionally keeping Aurora's recording frame open across GXDrawDone. */
    if (g_renderer.frame_open) {
        ++g_dev7_host_event_frame_defers;
        if (!g_dev7_logged_event_frame_defer) {
            log_message(OPENSPM_LOG_INFO,
                        "M14 Aurora host events deferred while a recording frame is open; resize/surface mutation runs only at host-frame boundaries.");
            g_dev7_logged_event_frame_defer = true;
        }
        return &no_events;
    }

    const auto now = std::chrono::steady_clock::now();
    if (g_dev7_last_host_event_pump.time_since_epoch().count() != 0 &&
        now - g_dev7_last_host_event_pump < kDev7HostEventPumpInterval) {
        ++g_dev7_host_event_skips;
        if (!g_dev7_logged_event_throttle) {
            log_message(OPENSPM_LOG_INFO,
                        "M14 Aurora redundant boundary host event pumps throttled to <=1 per 16 ms; startup/pause/input/resize/exit processing remains active.");
            g_dev7_logged_event_throttle = true;
        }
        return &no_events;
    }

    g_dev7_last_host_event_pump = now;
    ++g_dev7_host_event_pumps;
    return aurora_update();
}

void log_dev7_event_pump_stats() {
    if (g_dev7_logged_event_stats ||
        (g_dev7_host_event_pumps == 0u && g_dev7_host_event_skips == 0u &&
         g_dev7_host_event_frame_defers == 0u)) {
        return;
    }
    char message[320];
    std::snprintf(message, sizeof(message),
                  "M14 Aurora host event pump stats real=%llu cadence_skipped=%llu frame_deferred=%llu cadence_ms=16.",
                  static_cast<unsigned long long>(g_dev7_host_event_pumps),
                  static_cast<unsigned long long>(g_dev7_host_event_skips),
                  static_cast<unsigned long long>(g_dev7_host_event_frame_defers));
    log_message(OPENSPM_LOG_INFO, message);
    g_dev7_logged_event_stats = true;
}

void reset_dev6_batch_state() {
    g_dev6_fifo_batch_size = 0u;
    g_dev6_guest_memory = {};
    g_dev6_have_guest_memory = false;
    g_dev6_submit_calls = 0u;
    g_dev6_flushes = 0u;
    g_dev6_flushed_bytes = 0u;
    g_dev6_logged_batching = false;
    g_dev6_latched_fifo_error = OPENSPM_OK;
    g_dev6_logged_latched_error = false;
    g_dev6_logged_shutdown_discard = false;
    g_dev6_logged_auto_audit_retired = false;
    g_dev7_last_host_event_pump = {};
    g_dev7_host_event_pumps = 0u;
    g_dev7_host_event_skips = 0u;
    g_dev7_host_event_frame_defers = 0u;
    g_dev7_logged_event_throttle = false;
    g_dev7_logged_event_frame_defer = false;
    g_dev7_logged_event_stats = false;
}

int latch_dev6_fifo_error(int result) {
    if (result == OPENSPM_OK) return OPENSPM_OK;
    if (g_dev6_latched_fifo_error == OPENSPM_OK) {
        g_dev6_latched_fifo_error = result;
        /* A non-OK sink result is terminal for this strict-native host run.
           Keeping the bytes would replay a stream the underlying renderer may
           already have partially consumed before reporting failure. */
        g_dev6_fifo_batch_size = 0u;
        if (!g_dev6_logged_latched_error) {
            log_message(OPENSPM_LOG_INFO,
                        "M14 Aurora terminal FIFO error latched; failed batch discarded and will not be replayed.");
            g_dev6_logged_latched_error = true;
        }
    }
    return g_dev6_latched_fifo_error;
}

int flush_dev6_fifo() {
    int result;
    if (g_dev6_latched_fifo_error != OPENSPM_OK)
        return g_dev6_latched_fifo_error;
    if (g_dev6_fifo_batch_size == 0u) return OPENSPM_OK;
    if (g_dev6_underlying_sink.submit_fifo == nullptr)
        return latch_dev6_fifo_error(OPENSPM_ERROR_RUNTIME);

    const m14_cost_scope cost{m14_cost_op::fifo_submit};
    result = g_dev6_underlying_sink.submit_fifo(
        g_dev6_underlying_sink.context,
        g_dev6_fifo_batch.data(),
        g_dev6_fifo_batch_size,
        g_dev6_have_guest_memory ? &g_dev6_guest_memory : nullptr);
    if (result != OPENSPM_OK) return latch_dev6_fifo_error(result);

    ++g_dev6_flushes;
    g_dev6_flushed_bytes += g_dev6_fifo_batch_size;
    g_dev6_fifo_batch_size = 0u;

    if (!g_dev6_logged_batching) {
        log_message(OPENSPM_LOG_INFO,
                    "M14 Aurora GX transport batching active (16 KiB; semantic-boundary flushes preserved).");
        g_dev6_logged_batching = true;
    }
    return OPENSPM_OK;
}

int OPENSPM_CALL submit_fifo_batched_v6(
    void *context,
    const std::uint8_t *bytes,
    std::uint32_t size,
    const openspm_gx_guest_memory *guest_memory) {
    std::uint32_t consumed = 0u;
    (void)context;
    if (size != 0u && bytes == nullptr) return OPENSPM_ERROR_INVALID_ARGUMENT;
    if (g_dev6_latched_fifo_error != OPENSPM_OK)
        return g_dev6_latched_fifo_error;

    ++g_dev6_submit_calls;
    if (guest_memory != nullptr && guest_memory->map != nullptr) {
        g_dev6_guest_memory = *guest_memory;
        g_dev6_have_guest_memory = true;
    }

    while (consumed < size) {
        const std::uint32_t available =
            static_cast<std::uint32_t>(kDev6FifoBatchCapacity) - g_dev6_fifo_batch_size;
        const std::uint32_t remaining = size - consumed;
        const std::uint32_t chunk = remaining < available ? remaining : available;

        if (chunk != 0u) {
            std::memcpy(g_dev6_fifo_batch.data() + g_dev6_fifo_batch_size,
                        bytes + consumed,
                        chunk);
            g_dev6_fifo_batch_size += chunk;
            consumed += chunk;
        }

        if (g_dev6_fifo_batch_size == kDev6FifoBatchCapacity) {
            const int result = flush_dev6_fifo();
            if (result != OPENSPM_OK) return result;
        }
    }
    return OPENSPM_OK;
}

int OPENSPM_CALL sync_fifo_batched_v6(void *context, std::uint32_t reason) {
    int result;
    (void)context;
    if (g_dev6_latched_fifo_error != OPENSPM_OK)
        return g_dev6_latched_fifo_error;

    if (reason == OPENSPM_M14_GX_SYNC_SEQLOAD_AUDIT) {
        if (!g_dev6_logged_auto_audit_retired) {
            log_message(OPENSPM_LOG_INFO,
                        "M14 automatic SEQ_LOAD deep GX audit retired for normal runs; diagnostic sync acknowledged without drain/present.");
            g_dev6_logged_auto_audit_retired = true;
        }
        return OPENSPM_OK;
    }

    result = flush_dev6_fifo();
    if (result != OPENSPM_OK) return result;
    if (g_dev6_underlying_sink.sync == nullptr)
        return latch_dev6_fifo_error(OPENSPM_ERROR_RUNTIME);
    return latch_dev6_fifo_error(
        g_dev6_underlying_sink.sync(g_dev6_underlying_sink.context, reason));
}

int OPENSPM_CALL read_bounds_batched_v6(
    void *context,
    openspm_gx_raster_bounds *out_bounds) {
    int result;
    (void)context;
    if (g_dev6_latched_fifo_error != OPENSPM_OK)
        return g_dev6_latched_fifo_error;
    result = flush_dev6_fifo();
    if (result != OPENSPM_OK) return result;
    if (g_dev6_underlying_sink.read_bounds == nullptr)
        return latch_dev6_fifo_error(OPENSPM_ERROR_RUNTIME);
    return latch_dev6_fifo_error(g_dev6_underlying_sink.read_bounds(
        g_dev6_underlying_sink.context, out_bounds));
}

int OPENSPM_CALL write_bound_batched_v6(
    void *context,
    std::uint32_t address,
    std::uint16_t value) {
    int result;
    (void)context;
    if (g_dev6_latched_fifo_error != OPENSPM_OK)
        return g_dev6_latched_fifo_error;
    result = flush_dev6_fifo();
    if (result != OPENSPM_OK) return result;
    if (g_dev6_underlying_sink.write_bound == nullptr)
        return latch_dev6_fifo_error(OPENSPM_ERROR_RUNTIME);
    return latch_dev6_fifo_error(g_dev6_underlying_sink.write_bound(
        g_dev6_underlying_sink.context, address, value));
}

int OPENSPM_CALL renderer_initialize_batched_v6(
    void *context,
    const openspm_launch_desc *launch) {
    reset_dev6_batch_state();
    if (g_dev6_underlying_initialize == nullptr) return OPENSPM_ERROR_RUNTIME;
    return g_dev6_underlying_initialize(context, launch);
}

void OPENSPM_CALL renderer_shutdown_batched_v6(void *context) {
    /* Window exit can interrupt Broadway midway through a translated write-
       gather sequence. The normal base shutdown close_frame() drains the raw
       parser and therefore must not see that incomplete tail. Likewise, after
       any terminal sink error the renderer may already have partially consumed
       the rejected submission. Abandon both the host batch and open parser
       frame; aurora_shutdown still owns destruction of GPU/window resources. */
    log_dev7_event_pump_stats();
    if (g_renderer.exit_requested || g_dev6_latched_fifo_error != OPENSPM_OK) {
        const std::uint32_t discarded = g_dev6_fifo_batch_size;
        g_dev6_fifo_batch_size = 0u;
        g_renderer.frame_open = false;
        if (!g_dev6_logged_shutdown_discard) {
            char message[256];
            std::snprintf(message, sizeof(message),
                          "M14 Aurora shutdown abandoning interrupted GX tail bytes=%u exit=%u fifo_error=%d; no parser drain.",
                          static_cast<unsigned>(discarded),
                          g_renderer.exit_requested ? 1u : 0u,
                          g_dev6_latched_fifo_error);
            log_message(OPENSPM_LOG_INFO, message);
            g_dev6_logged_shutdown_discard = true;
        }
        if (g_dev6_underlying_shutdown != nullptr)
            g_dev6_underlying_shutdown(context);
        reset_dev6_batch_state();
        return;
    }

    (void)flush_dev6_fifo();
    if (g_dev6_underlying_shutdown != nullptr)
        g_dev6_underlying_shutdown(context);
    reset_dev6_batch_state();
}

} // namespace

extern "C" OPENSPM_EXPORT int OPENSPM_CALL openspm_renderer_query(
    uint32_t requested_version,
    const openspm_host_api *host,
    openspm_renderer_api *out_api) {
    const int result = openspm_renderer_query_dev5_internal(
        requested_version, host, out_api);
    if (result != OPENSPM_OK) return result;

    g_dev6_underlying_sink = out_api->gx_sink;
    g_dev6_underlying_initialize = out_api->initialize;
    g_dev6_underlying_shutdown = out_api->shutdown;
    reset_dev6_batch_state();

    out_api->renderer_version = "14.0-dev7";
    out_api->gx_sink.submit_fifo = submit_fifo_batched_v6;
    out_api->gx_sink.sync = sync_fifo_batched_v6;
    out_api->gx_sink.read_bounds = read_bounds_batched_v6;
    out_api->gx_sink.write_bound = write_bound_batched_v6;
    out_api->initialize = renderer_initialize_batched_v6;
    out_api->shutdown = renderer_shutdown_batched_v6;
    return OPENSPM_OK;
}
