/*
 * M14 Aurora renderer dev5.
 *
 * dev4 proved the recompiled CPU reaches the native DRAW_DONE sync seam, but
 * Aurora's threaded FIFO drain never returned. The root build for dev5 forces
 * the pinned, OpenSPM-owned Aurora checkout to its built-in ProcessingMode::Drain
 * correctness mode before compiling Aurora: no FIFO worker thread is started,
 * and the exact buffered GX stream is decoded synchronously at drain().
 *
 * The first dev5 Windows run then changed the failure from an indefinite worker
 * wait into an immediate host fail-fast inside the same first drain. Keep the
 * renderer semantics unchanged and shadow the exact FIFO bytes submitted before
 * that boundary. Dump them directly to stderr (with an explicit flush) before
 * entering Aurora so the command stream survives even if Aurora aborts before
 * its own fatal logger can flush.
 */

#include <cstdio>
#include <vector>

/* Preserve an outer wrapper's query rename (dev6+) while privately renaming
 * dev4 below. Without push/pop, including this file from a later renderer
 * revision would undefine the outer rename and emit a second public query
 * symbol. */
#pragma push_macro("openspm_renderer_query")
#undef openspm_renderer_query
#define openspm_renderer_query openspm_renderer_query_dev4_internal
#include "openspm_renderer_aurora_v4.cpp"
#undef openspm_renderer_query
#pragma pop_macro("openspm_renderer_query")

namespace {

std::vector<uint8_t> g_dev5_fifo_shadow;
bool g_dev5_first_sync_dumped = false;

void dump_first_fifo_batch_v5() {
    if (g_dev5_first_sync_dumped) return;
    g_dev5_first_sync_dumped = true;

    std::fprintf(stderr,
                 "[OpenSPM:AuroraFIFO] first-sync bytes=%llu begin\n",
                 static_cast<unsigned long long>(g_dev5_fifo_shadow.size()));
    for (std::size_t offset = 0; offset < g_dev5_fifo_shadow.size(); offset += 16u) {
        const std::size_t end =
            (offset + 16u < g_dev5_fifo_shadow.size()) ? offset + 16u : g_dev5_fifo_shadow.size();
        std::fprintf(stderr, "[OpenSPM:AuroraFIFO] %04llX:",
                     static_cast<unsigned long long>(offset));
        for (std::size_t i = offset; i < end; ++i) {
            std::fprintf(stderr, " %02X", static_cast<unsigned>(g_dev5_fifo_shadow[i]));
        }
        std::fputc('\n', stderr);
    }
    std::fprintf(stderr, "[OpenSPM:AuroraFIFO] first-sync end\n");
    std::fflush(stderr);
}

int OPENSPM_CALL submit_fifo_v5_diag(
    void *context,
    const uint8_t *bytes,
    uint32_t size,
    const openspm_gx_guest_memory *guest_memory) {
    if (!g_dev5_first_sync_dumped && bytes != nullptr && size != 0u) {
        g_dev5_fifo_shadow.insert(g_dev5_fifo_shadow.end(), bytes, bytes + size);
    }
    return submit_fifo(context, bytes, size, guest_memory);
}

int OPENSPM_CALL sync_fifo_v5_diag(void *context, uint32_t reason) {
    dump_first_fifo_batch_v5();
    return sync_fifo_v4(context, reason);
}

} // namespace

extern "C" OPENSPM_EXPORT int OPENSPM_CALL openspm_renderer_query(
    uint32_t requested_version,
    const openspm_host_api *host,
    openspm_renderer_api *out_api) {
    const int result = openspm_renderer_query_dev4_internal(
        requested_version, host, out_api);
    if (result != OPENSPM_OK) return result;

    g_dev5_fifo_shadow.clear();
    g_dev5_fifo_shadow.reserve(4096u);
    g_dev5_first_sync_dumped = false;

    out_api->renderer_version = "14.0-dev5";
    out_api->gx_sink.submit_fifo = submit_fifo_v5_diag;
    out_api->gx_sink.sync = sync_fifo_v5_diag;
    return OPENSPM_OK;
}
