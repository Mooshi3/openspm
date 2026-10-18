#ifndef OPENSPM_RUNTIME_SERVICE_H
#define OPENSPM_RUNTIME_SERVICE_H

#include "openspm/abi.h"

#ifdef __cplusplus
extern "C" {
#endif

#define OPENSPM_RUNTIME_SERVICE_ID "openspm.runtime"
#define OPENSPM_RUNTIME_SERVICE_VERSION 1u

enum {
    OPENSPM_RUNTIME_CAP_PERF_SNAPSHOT = UINT64_C(1) << 0,
    OPENSPM_RUNTIME_CAP_PAUSE_RESUME = UINT64_C(1) << 1,
    OPENSPM_RUNTIME_CAP_REQUEST_STOP = UINT64_C(1) << 2,
    OPENSPM_RUNTIME_CAP_CORE_LIFECYCLE_EVENTS = UINT64_C(1) << 3,
    OPENSPM_RUNTIME_CAP_PRESENT_EVENTS = UINT64_C(1) << 4,
    OPENSPM_RUNTIME_CAP_SIMULATION_TICK_EVENTS = UINT64_C(1) << 5
};

typedef enum openspm_runtime_event {
    OPENSPM_RUNTIME_EVENT_CORE_RUNNING = 1,
    OPENSPM_RUNTIME_EVENT_PRESENT_END = 2,
    OPENSPM_RUNTIME_EVENT_CORE_STOPPED = 3,
    OPENSPM_RUNTIME_EVENT_TICK_BEGIN = 4,
    OPENSPM_RUNTIME_EVENT_TICK_END = 5
} openspm_runtime_event;

typedef struct openspm_runtime_present_event {
    openspm_abi_header header;
    uint64_t frame_count;
    uint64_t present_count;
} openspm_runtime_present_event;

typedef struct openspm_runtime_tick_event {
    openspm_abi_header header;
    uint64_t tick_count;
} openspm_runtime_tick_event;

typedef struct openspm_runtime_perf_snapshot {
    openspm_abi_header header;
    uint64_t frame_count;
    uint64_t present_count;
    double fps;
    double vps;
    double speed;
    uint32_t running;
    uint32_t paused;
} openspm_runtime_perf_snapshot;

typedef int (OPENSPM_CALL *openspm_runtime_get_perf_fn)(
    void *runtime_context,
    openspm_runtime_perf_snapshot *out_snapshot);
typedef int (OPENSPM_CALL *openspm_runtime_control_fn)(void *runtime_context);

typedef struct openspm_runtime_service {
    openspm_abi_header header;
    uint64_t capabilities;
    void *runtime_context;
    openspm_runtime_get_perf_fn get_performance;
    openspm_runtime_control_fn pause;
    openspm_runtime_control_fn resume;
    openspm_runtime_control_fn request_stop;
} openspm_runtime_service;

#ifdef __cplusplus
}
#endif

#endif
