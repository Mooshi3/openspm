#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>

#include "openspm/backend_api.h"
#include "openspm/runtime_service.h"
#include "moderngekko/runtime.hpp"

#include <atomic>
#include <cstddef>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <memory>
#include <mutex>
#include <string>

namespace {
#define MEMBER_END(type, member) (offsetof(type, member) + sizeof(((type*)0)->member))

// Exact R8PE01/US2 simulation boundary verified against the generated module:
// spmarioMain dispatches seqMain at 0x8017B72C and resumes at 0x801A7ACC.
// These addresses are private adapter details and must never become SDK identity.
constexpr std::uint32_t R8PE01_TICK_BEGIN_PC = 0x8017B72Cu;
constexpr std::uint32_t R8PE01_TICK_END_PC = 0x801A7ACCu;

struct BackendContext {
    const openspm_host_api* host = nullptr;
    std::mutex mutex;
    moderngekko::Runtime* active_runtime = nullptr;
    std::atomic<std::uint64_t> tick_count{0};
};

BackendContext g_context;

bool HostHasRuntimeEventSink(const openspm_host_api* host) {
    return host &&
           host->header.struct_size >= MEMBER_END(openspm_host_api, runtime_event) &&
           host->runtime_event != nullptr;
}

void Log(openspm_log_level level, const std::string& message) {
    const openspm_host_api* host = g_context.host;
    if (host && host->log)
        host->log(host->context, level, message.c_str());
}

void EmitRuntimeEvent(uint32_t event_id, const void* event_data) {
    const openspm_host_api* host = g_context.host;
    if (HostHasRuntimeEventSink(host))
        host->runtime_event(host->context, event_id, event_data);
}

std::filesystem::path PathFromUtf8(const char* value) {
    if (!value || !*value)
        return {};
    const int chars = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, value, -1, nullptr, 0);
    if (chars <= 0)
        return {};
    std::wstring wide(static_cast<size_t>(chars), L'\0');
    if (!MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, value, -1, wide.data(), chars))
        return {};
    if (!wide.empty() && wide.back() == L'\0')
        wide.pop_back();
    return std::filesystem::path(std::move(wide));
}

void SetProcessEnvironment(const wchar_t* name, const wchar_t* value) {
    _wputenv_s(name, value);
    SetEnvironmentVariableW(name, (value && *value) ? value : nullptr);
}

void SetEnvironmentDefaults(const std::filesystem::path& runtime_root) {
    const wchar_t* native_helpers = _wgetenv(L"OPENSPM_NATIVE_HELPERS");
    if (!native_helpers || !*native_helpers)
        SetProcessEnvironment(L"OPENSPM_NATIVE_HELPERS", L"1");
    SetProcessEnvironment(L"M10_MMU", L"1");
    SetProcessEnvironment(L"MODERNGEKKO_STATICRECOMP", L"1");
    SetProcessEnvironment(L"OPENSPM_BUSYWAIT", L"0");
    SetProcessEnvironment(L"OPENSPM_IDLE_PC", L"1");
    SetProcessEnvironment(L"MODERNGEKKO_CACHE_AFFINITY", L"0");
    SetProcessEnvironment(L"OPENSPM_PERF", L"");
    SetProcessEnvironment(L"STATICRECOMP_DISPATCH_SAMPLES", L"");

    const std::filesystem::path sys = runtime_root / L"legacy" / L"Sys";
    SetProcessEnvironment(L"OPENSPM_SYS_DIR", sys.c_str());

    const wchar_t* idle_pc = _wgetenv(L"OPENSPM_IDLE_PC");
    Log(idle_pc && *idle_pc == L'1' ? OPENSPM_LOG_INFO : OPENSPM_LOG_ERROR,
        idle_pc && *idle_pc == L'1'
            ? "M12 runtime environment synchronized; OPENSPM_IDLE_PC=1."
            : "M12 runtime environment synchronization failed; OPENSPM_IDLE_PC is not visible to CRT getenv.");
}

void ObserverCoreRunning(void* opaque) {
    auto* context = static_cast<BackendContext*>(opaque);
    if (context)
        context->tick_count.store(0, std::memory_order_relaxed);
    EmitRuntimeEvent(OPENSPM_RUNTIME_EVENT_CORE_RUNNING, nullptr);
}

void ObserverPresent(void*, const moderngekko::RuntimePerformanceSnapshot* snapshot) {
    if (!snapshot)
        return;
    openspm_runtime_present_event event{};
    event.header.struct_size = sizeof(event);
    event.header.abi_version = OPENSPM_RUNTIME_SERVICE_VERSION;
    event.frame_count = snapshot->frame_count;
    event.present_count = snapshot->present_count;
    EmitRuntimeEvent(OPENSPM_RUNTIME_EVENT_PRESENT_END, &event);
}

void ObserverTickBegin(void* opaque) {
    auto* context = static_cast<BackendContext*>(opaque);
    if (!context)
        return;
    openspm_runtime_tick_event event{};
    event.header.struct_size = sizeof(event);
    event.header.abi_version = OPENSPM_RUNTIME_SERVICE_VERSION;
    event.tick_count = context->tick_count.fetch_add(1, std::memory_order_relaxed) + 1;
    EmitRuntimeEvent(OPENSPM_RUNTIME_EVENT_TICK_BEGIN, &event);
}

void ObserverTickEnd(void* opaque) {
    auto* context = static_cast<BackendContext*>(opaque);
    if (!context)
        return;
    openspm_runtime_tick_event event{};
    event.header.struct_size = sizeof(event);
    event.header.abi_version = OPENSPM_RUNTIME_SERVICE_VERSION;
    event.tick_count = context->tick_count.load(std::memory_order_relaxed);
    EmitRuntimeEvent(OPENSPM_RUNTIME_EVENT_TICK_END, &event);
}

void ObserverCoreStopped(void*) {
    EmitRuntimeEvent(OPENSPM_RUNTIME_EVENT_CORE_STOPPED, nullptr);
}

int OPENSPM_CALL RuntimeGetPerformance(void* runtime_context,
                                       openspm_runtime_perf_snapshot* out_snapshot) {
    auto* context = static_cast<BackendContext*>(runtime_context);
    if (!context || !out_snapshot ||
        out_snapshot->header.abi_version != OPENSPM_RUNTIME_SERVICE_VERSION ||
        out_snapshot->header.struct_size < sizeof(*out_snapshot))
        return OPENSPM_ERROR_ABI_MISMATCH;

    std::lock_guard lock(context->mutex);
    if (!context->active_runtime)
        return OPENSPM_ERROR_RUNTIME;

    const moderngekko::RuntimePerformanceSnapshot source =
        context->active_runtime->GetPerformanceSnapshot();
    const uint32_t caller_size = out_snapshot->header.struct_size;
    std::memset(out_snapshot, 0, caller_size);
    out_snapshot->header.struct_size = sizeof(*out_snapshot);
    out_snapshot->header.abi_version = OPENSPM_RUNTIME_SERVICE_VERSION;
    out_snapshot->frame_count = source.frame_count;
    out_snapshot->present_count = source.present_count;
    out_snapshot->fps = source.fps;
    out_snapshot->vps = source.vps;
    out_snapshot->speed = source.speed;
    out_snapshot->running = source.running ? 1u : 0u;
    out_snapshot->paused = source.paused ? 1u : 0u;
    return OPENSPM_OK;
}

int OPENSPM_CALL RuntimePause(void* runtime_context) {
    auto* context = static_cast<BackendContext*>(runtime_context);
    if (!context)
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    std::lock_guard lock(context->mutex);
    if (!context->active_runtime)
        return OPENSPM_ERROR_RUNTIME;
    return context->active_runtime->Pause() ? OPENSPM_ERROR_RUNTIME : OPENSPM_OK;
}

int OPENSPM_CALL RuntimeResume(void* runtime_context) {
    auto* context = static_cast<BackendContext*>(runtime_context);
    if (!context)
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    std::lock_guard lock(context->mutex);
    if (!context->active_runtime)
        return OPENSPM_ERROR_RUNTIME;
    return context->active_runtime->Resume() ? OPENSPM_ERROR_RUNTIME : OPENSPM_OK;
}

int OPENSPM_CALL RuntimeRequestStop(void* runtime_context) {
    auto* context = static_cast<BackendContext*>(runtime_context);
    if (!context)
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    std::lock_guard lock(context->mutex);
    if (!context->active_runtime)
        return OPENSPM_ERROR_RUNTIME;
    context->active_runtime->RequestStop();
    return OPENSPM_OK;
}

int OPENSPM_CALL BackendQueryService(void* backend_context,
                                     const char* service_id,
                                     uint32_t requested_version,
                                     void* out_service) {
    if (!backend_context || !service_id || !out_service)
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    if (std::strcmp(service_id, OPENSPM_RUNTIME_SERVICE_ID) != 0)
        return OPENSPM_ERROR_NOT_FOUND;
    if (requested_version != OPENSPM_RUNTIME_SERVICE_VERSION)
        return OPENSPM_ERROR_ABI_MISMATCH;

    auto* service = static_cast<openspm_runtime_service*>(out_service);
    if (service->header.abi_version != OPENSPM_RUNTIME_SERVICE_VERSION ||
        service->header.struct_size < sizeof(*service))
        return OPENSPM_ERROR_ABI_MISMATCH;

    const uint32_t caller_size = service->header.struct_size;
    std::memset(service, 0, caller_size);
    service->header.struct_size = sizeof(*service);
    service->header.abi_version = OPENSPM_RUNTIME_SERVICE_VERSION;
    service->capabilities = OPENSPM_RUNTIME_CAP_PERF_SNAPSHOT |
                            OPENSPM_RUNTIME_CAP_PAUSE_RESUME |
                            OPENSPM_RUNTIME_CAP_REQUEST_STOP |
                            OPENSPM_RUNTIME_CAP_CORE_LIFECYCLE_EVENTS |
                            OPENSPM_RUNTIME_CAP_PRESENT_EVENTS |
                            OPENSPM_RUNTIME_CAP_SIMULATION_TICK_EVENTS;
    service->runtime_context = backend_context;
    service->get_performance = RuntimeGetPerformance;
    service->pause = RuntimePause;
    service->resume = RuntimeResume;
    service->request_stop = RuntimeRequestStop;
    return OPENSPM_OK;
}

int OPENSPM_CALL InProcessRun(void* backend_context, const openspm_launch_desc* launch) {
    auto* context = static_cast<BackendContext*>(backend_context);
    if (!context || !launch || launch->header.abi_version != OPENSPM_ABI_VERSION ||
        launch->header.struct_size < MEMBER_END(openspm_launch_desc, flags))
        return OPENSPM_ERROR_ABI_MISMATCH;

    const std::filesystem::path runtime_root = PathFromUtf8(launch->runtime_root_utf8);
    const std::filesystem::path game_root = PathFromUtf8(launch->game_root_utf8);
    const std::filesystem::path user_root = PathFromUtf8(launch->user_root_utf8);
    const std::filesystem::path module_path = runtime_root / L"legacy" / L"gR8PE01_recomp.dll";
    if (runtime_root.empty() || game_root.empty() || user_root.empty() ||
        !std::filesystem::is_regular_file(module_path)) {
        Log(OPENSPM_LOG_ERROR, "In-process backend paths are incomplete or the R8PE01 module is missing.");
        return OPENSPM_ERROR_NOT_FOUND;
    }

    SetEnvironmentDefaults(runtime_root);

    moderngekko::RuntimeConfig config;
    config.game_root = game_root;
    config.user_directory = user_root;
    config.module = moderngekko::ModuleSource::DynamicPath(module_path);
    config.graphics.backend = launch->graphics_backend_utf8 ? launch->graphics_backend_utf8 : "OGL";
    config.window_title = std::string("OpenSPM");
    config.show_fps_in_title = true;
    config.allow_interpreter = false;
    config.observer.context = context;
    config.observer.on_core_running = ObserverCoreRunning;
    config.observer.on_present = ObserverPresent;
    config.observer.on_core_stopped = ObserverCoreStopped;
    config.observer.on_guest_tick_begin = ObserverTickBegin;
    config.observer.on_guest_tick_end = ObserverTickEnd;
    config.guest_tick_begin_pc = R8PE01_TICK_BEGIN_PC;
    config.guest_tick_end_pc = R8PE01_TICK_END_PC;

    Log(OPENSPM_LOG_INFO, "Creating ModernGekko runtime in-process behind the OpenSPM backend ABI.");
    moderngekko::RuntimeCreateResult created = moderngekko::Runtime::Create(std::move(config));
    if (!created) {
        const std::string reason = created.error ? created.error->message : "unknown runtime creation failure";
        Log(OPENSPM_LOG_ERROR, "In-process runtime creation failed: " + reason);
        return OPENSPM_ERROR_RUNTIME;
    }

    std::unique_ptr<moderngekko::Runtime> runtime = std::move(created.runtime);
    {
        std::lock_guard lock(context->mutex);
        context->active_runtime = runtime.get();
    }

    Log(OPENSPM_LOG_INFO, "Entering in-process ModernGekko runtime with live runtime and true simulation-tick services.");
    const moderngekko::RuntimeRunResult result = runtime->Run();

    {
        std::lock_guard lock(context->mutex);
        context->active_runtime = nullptr;
    }

    if (result.error) {
        Log(OPENSPM_LOG_ERROR, "In-process runtime stopped with error: " + result.error->message);
        return OPENSPM_ERROR_RUNTIME;
    }
    Log(OPENSPM_LOG_INFO, "In-process ModernGekko runtime exited cleanly.");
    return OPENSPM_OK;
}

void OPENSPM_CALL InProcessShutdown(void* backend_context) {
    RuntimeRequestStop(backend_context);
}
}  // namespace

extern "C" OPENSPM_EXPORT int OPENSPM_CALL openspm_backend_query(
    uint32_t requested_abi,
    const openspm_host_api* host,
    openspm_backend_api* out_api) {
    const size_t host_v1_size = MEMBER_END(openspm_host_api, profile_end);
    const size_t backend_v1_size = MEMBER_END(openspm_backend_api, shutdown);
    if (requested_abi != OPENSPM_ABI_VERSION || !host || !out_api ||
        host->header.abi_version != OPENSPM_ABI_VERSION || host->header.struct_size < host_v1_size ||
        out_api->header.struct_size < backend_v1_size)
        return OPENSPM_ERROR_ABI_MISMATCH;

    g_context.host = host;
    const uint32_t caller_size = out_api->header.struct_size;
    std::memset(out_api, 0, caller_size);
    out_api->header.struct_size = sizeof(*out_api);
    out_api->header.abi_version = OPENSPM_ABI_VERSION;
    out_api->backend_id = "moderngekko-inprocess";
    out_api->backend_version = "13.4-phase5-ticks";
    out_api->capabilities = OPENSPM_BACKEND_CAP_IN_PROCESS |
                            OPENSPM_BACKEND_CAP_PROFILING |
                            OPENSPM_BACKEND_CAP_RUNTIME_SERVICE;
    out_api->backend_context = &g_context;
    out_api->run = InProcessRun;
    out_api->shutdown = InProcessShutdown;
    if (caller_size >= MEMBER_END(openspm_backend_api, query_service))
        out_api->query_service = BackendQueryService;
    return OPENSPM_OK;
}
