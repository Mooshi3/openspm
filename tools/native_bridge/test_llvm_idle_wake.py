#!/usr/bin/env python3
"""Execute the actual retrace service with a deterministic host/guest fixture.

No game code/assets are needed. Compile the production C functions, including
the fixed-phase wrapper, with a small fake clock, RAM, Sleep and guest event.
Also compile without LLVM enabled to reproduce the preserved PC-only behavior.
The two-wait fixture models a thread doing 5 ms of work, sleeping on GX finish,
being awakened by PE, and then waiting for VI. It is a regression test, not a
measurement or prediction of real gameplay performance.
"""
from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent


def function(source: str, name: str) -> str:
    # The definition, not a forward declaration (a prototype ends in ";"
    # before any "{").
    match = None
    for candidate in re.finditer(r"^static [^\n]*\b" + name + r"\(", source, re.M):
        brace = source.find("{", candidate.end())
        semicolon = source.find(";", candidate.end())
        if brace != -1 and (semicolon == -1 or brace < semicolon):
            match = candidate
            break
    if match is None:
        raise AssertionError(f"missing production function {name}")
    start = match.start()
    body = source.index("{", match.end())
    depth = 1
    end = body + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end] + "\n"


PRELUDE = r'''
/* The fixture copies production helpers; which of them the current retrace
   path still calls changes over time. */
#pragma GCC diagnostic ignored "-Wunused-function"
#pragma GCC diagnostic ignored "-Wunused-variable"
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stddef.h>
#include <stdbool.h>
#include <stdlib.h>
#include "openspm/native_api.h"
#include "openspm/m14_hostcall_service.h"
#define GC_RAM_BASE UINT32_C(0x80000000)
#define WII_MEM2_BASE UINT32_C(0x90000000)
typedef struct CPUState {
    uint32_t pc, msr, lr, gpr[32], ram_size, mem2_size;
    int32_t downcount;
    uint32_t external_addr, external_value, external_rid;
    void *mem2;
} CPUState;
#define PPC_HOST_CALL_NATIVE_REGION_QUERY UINT32_C(0xFFFFFFFE)
#define PPC_NATIVE_REGION_QUERY_HANDLED UINT32_C(0x4F53504D)
typedef struct openspm_bridge_instance {
    CPUState cpu;
    uint32_t fallback_requested;
    uint32_t fallback_detail;
    uint32_t fallback_address;
} openspm_bridge_instance;
static uint64_t now_ns;
static unsigned sleeps, events, reads, stage, frames;
static uint32_t runqueue;
static uint64_t g_m14_dev129_empty_idle_sideexits;
static uint64_t g_m14_dev129_runnable_idle_native;
static uint64_t g_m14_dev129_unknown_idle_native;
static uint32_t g_m14_retrace_armed, g_m14_retrace_ticks;
static uint32_t g_m14_last_event_transfer_context, g_m14_last_event_transfer_pc;
static uint64_t g_m14_next_retrace_ns;
static uint32_t g_m14_dev132_cadence_checked;
static uint32_t g_m14_dev132_half_monitor_cadence;
static openspm_native_run_fn g_m14_v20_underlying_run;
static uint64_t g_m14_v20_expired_deadlines, g_m14_v20_rephased_deliveries;
typedef uint32_t u32;
static uint64_t g_m14_display_synced_period_ns;
/* Features added to the retrace path after this fixture: all off here (no
   GX-finish hook before the retrace, no host clock skip), which is the path
   this regression pins. */
static int (*g_m14_pre_retrace_finish_hook)(openspm_bridge_instance *instance);
static uint64_t g_m14_finish_before_retrace;
static uint32_t g_m14_finish_since_retrace;
static uint32_t g_m14_finish_before_retrace_checked = 1u;
static uint32_t g_m14_finish_before_retrace_disabled = 1u;
static uint64_t g_m14_finish_holds, g_m14_finish_hold_max_ns, g_m14_finish_hold_expired;
#define YieldProcessor() ((void)0)
#ifdef DOLRECOMP_BACKEND_LLVM
static int bridge_m14_idle_runqueue_state(CPUState *cpu); /* defined in the dev68 block */
#endif
static int bridge_m14_hook_bypassed(const CPUState *cpu, uint32_t address) {
    (void)cpu; (void)address; return 0; /* no host hook re-entry in the fixture */
}
static int bridge_m14_clock_skip_to(const openspm_bridge_instance *instance,
                                    uint64_t target_ns, uint64_t *io_now) {
    (void)instance; (void)target_ns; (void)io_now; return 0;
}
/* No environment in the fixture: the default retrace period. */
static int bridge_m14_read_env(const char *name, char *out, size_t size) {
    (void)name; (void)out; (void)size; return 0;
}
static uint64_t bridge_m14_now_ns(const openspm_bridge_instance *i) {
    (void)i; return now_ns;
}
static void bridge_log_m14_retrace(openspm_bridge_instance *i, const char *m) {
    (void)i; (void)m;
}
static void bridge_log_v20_phase(openspm_bridge_instance *i, uint64_t a,
                                  uint64_t b, uint64_t c) {
    (void)i; (void)a; (void)b; (void)c;
}
static void Sleep(unsigned ms) { ++sleeps; now_ns += ms * UINT64_C(1000000); }
static int bridge_m14_host_range_blocked(uint32_t start, uint32_t end) {
    (void)start; (void)end; return 0;
}
static int bridge_is_m14_host_target(uint32_t address) {
    (void)address; return 0;
}
static openspm_bridge_instance *bridge_from_cpu(CPUState *cpu) {
    return (openspm_bridge_instance *)((char *)cpu -
        offsetof(openspm_bridge_instance, cpu));
}
static void bridge_request_fallback(openspm_bridge_instance *instance,
                                    uint32_t address,
                                    uint32_t detail) {
    instance->fallback_requested = 1u;
    instance->fallback_detail = detail;
    instance->fallback_address = address;
}
static uint32_t mem_read32(const CPUState *cpu, uint32_t addr) {
    (void)cpu; ++reads;
    if (addr == UINT32_C(0x800000D4)) return UINT32_C(0x80002000);
    assert(addr == UINT32_C(0x80001000));
    return runqueue;
}
static int bridge_run_m14_event(openspm_bridge_instance *i, uint32_t target,
                                uint32_t argc, const uint32_t *args) {
    (void)i; (void)target; (void)argc; (void)args;
    ++events;
    /* Only the real VI waiter is awakened by this fixture's VI event. */
    if (stage == 0) runqueue = UINT32_C(0x8000);
    return OPENSPM_OK;
}
'''

HARNESS = r'''
static openspm_bridge_instance reset(void) {
    openspm_bridge_instance i;
    memset(&i, 0, sizeof(i));
    i.cpu.pc = OPENSPM_M14_SCHEDULER_IDLE_PC;
    i.cpu.msr = OPENSPM_M14_MSR_EE;
    i.cpu.gpr[13] = UINT32_C(0x80001000) + UINT32_C(29552);
    i.cpu.ram_size = UINT32_C(0x20000);
    now_ns = UINT64_C(1000000000);
    g_m14_retrace_armed = 1;
    g_m14_retrace_ticks = 0;
    g_m14_dev132_cadence_checked = 1u;
    g_m14_dev132_half_monitor_cadence = 0u;
    g_m14_next_retrace_ns = now_ns + OPENSPM_M14_RETRACE_PERIOD_NS;
    sleeps = events = reads = stage = frames = runqueue = 0;
    g_m14_dev129_empty_idle_sideexits = 0u;
    g_m14_dev129_runnable_idle_native = 0u;
    g_m14_dev129_unknown_idle_native = 0u;
#ifdef DOLRECOMP_BACKEND_LLVM
    g_m14_dev68_runnable_resumes = g_m14_dev68_idle_polls = 0;
    g_m14_dev68_idle_sleep_ns = 0;
#endif
    return i;
}

static int OPENSPM_CALL fixture_run(void *p, uint64_t budget,
                                    openspm_native_exit *out) {
    openspm_bridge_instance *i = p;
    int wait = 0;
    (void)budget; (void)out;
    assert(bridge_m14_service_retrace(i, &wait) == OPENSPM_OK);
    if (!wait && runqueue) {
        if (stage == 0) {
            ++frames;
            now_ns += UINT64_C(5000000); /* finite guest work, below 16.7 ms */
            stage = 1; /* guest sleeps on GX; finish event makes it runnable */
            runqueue = UINT32_C(0x8000);
        } else {
            stage = 0; /* native scheduler resumes it; next wait is VI */
            runqueue = 0;
        }
    }
    return OPENSPM_OK;
}

int main(void) {
    openspm_bridge_instance i = reset();
    openspm_native_exit out = {0};
    uint64_t deadline = g_m14_next_retrace_ns;
    int wait = -1;
    assert(bridge_m14_context_address_valid(&i.cpu, GC_RAM_BASE, 4u));
#ifdef DOLRECOMP_BACKEND_LLVM
    /* dev129's exact in-slice scheduler-idle decision:
       empty -> private side exit; runnable -> native SelectThread; unknown ->
       fail-open to Nintendo. */
    runqueue = 0u;
    i.fallback_requested = i.fallback_detail = i.fallback_address = 0u;
    assert(bridge_host_call_v7(&i.cpu, OPENSPM_M14_SCHEDULER_IDLE_PC));
    assert(i.fallback_requested == 1u);
    assert(i.fallback_detail == OPENSPM_M14_DEV129_IDLE_PARK_DETAIL);
    assert(i.fallback_address == OPENSPM_M14_SCHEDULER_IDLE_PC);
    assert(i.cpu.pc == OPENSPM_M14_SCHEDULER_IDLE_PC);
    assert(g_m14_dev129_empty_idle_sideexits == 1u);

    i = reset();
    runqueue = UINT32_C(0x8000);
    assert(!bridge_host_call_v7(&i.cpu, OPENSPM_M14_SCHEDULER_IDLE_PC));
    assert(i.fallback_requested == 0u);
    assert(g_m14_dev129_runnable_idle_native == 1u);

    i = reset();
    i.cpu.gpr[13] = UINT32_C(0xFFFFFFFF);
    runqueue = UINT32_C(0x8000);
    assert(!bridge_host_call_v7(&i.cpu, OPENSPM_M14_SCHEDULER_IDLE_PC));
    assert(i.fallback_requested == 0u);
    assert(g_m14_dev129_unknown_idle_native == 1u);

    i = reset();
    deadline = g_m14_next_retrace_ns;
#endif
    /* Empty queue really is idle; preserve host yielding and VI deadline. */
    assert(bridge_m14_service_retrace(&i, &wait) == OPENSPM_OK);
    assert(wait == 1 && sleeps == 1 && events == 0);
    assert(g_m14_next_retrace_ns == deadline);
    /* PE completion changed guest RAM, not the persistent idle PC. */
    runqueue = UINT32_C(0x8000);
    assert(bridge_m14_service_retrace(&i, &wait) == OPENSPM_OK);
#ifdef DOLRECOMP_BACKEND_LLVM
    assert(wait == 0 && sleeps == 1 && g_m14_dev68_runnable_resumes == 1);
    assert(g_m14_dev68_idle_sleep_ns == UINT64_C(1000000));
#else
    assert(wait == 1 && sleeps == 2); /* reproduce dev67 bug */
#endif
    assert(events == 0 && runqueue == UINT32_C(0x8000));
    assert(i.cpu.pc == OPENSPM_M14_SCHEDULER_IDLE_PC);
    assert(g_m14_next_retrace_ns == deadline && g_m14_retrace_ticks == 0);
    /* Due VI is still serviced even with runnable work. */
    now_ns = deadline;
    assert(bridge_m14_service_retrace(&i, &wait) == OPENSPM_OK);
    assert(events == 1 && g_m14_retrace_ticks == 1 && wait == 0);
    /* EE still gates an overdue interrupt. */
    i.cpu.msr = 0;
    now_ns = g_m14_next_retrace_ns;
    assert(bridge_m14_service_retrace(&i, &wait) == OPENSPM_OK);
    assert(events == 1 && wait == 0);
    /* Bad/unaligned/out-of-range r13 is not dereferenced or guessed. */
    {
        const uint32_t invalid[] = {0, 4, UINT32_C(0x80008371),
                                   UINT32_C(0x81000000), UINT32_C(0xFFFFFFFF)};
        unsigned n;
        for (n = 0; n < sizeof(invalid)/sizeof(invalid[0]); ++n) {
            i = reset(); i.cpu.gpr[13] = invalid[n]; runqueue = 1;
            assert(bridge_m14_service_retrace(&i, &wait) == OPENSPM_OK);
            assert(wait == 1 && reads == 0);
        }
    }
    i = reset(); i.cpu.pc = UINT32_C(0x80010000); runqueue = 1;
    assert(bridge_m14_service_retrace(&i, &wait) == OPENSPM_OK);
    assert(wait == 0 && sleeps == 0 && reads == 0);
    /* First arrival arms the clock normally. */
    i = reset(); g_m14_retrace_armed = 0; g_m14_next_retrace_ns = 0;
    assert(bridge_m14_service_retrace(&i, &wait) == OPENSPM_OK);
    assert(g_m14_retrace_armed && wait && !events);
    /* Use the production fixed-grid wrapper around the two-wait fixture. */
    i = reset(); g_m14_v20_underlying_run = fixture_run;
    while (now_ns < UINT64_C(5000000000)) {
        assert(bridge_run_v20(&i, 250000, &out) == OPENSPM_OK);
        now_ns += UINT64_C(100000);
    }
#ifdef DOLRECOMP_BACKEND_LLVM
    assert(frames >= 235 && frames <= 241);
    assert(g_m14_dev68_runnable_resumes >= 235);
    printf("LLVM runnable-wake fixture: %u frames / 4s; guards passed\n", frames);
#else
    assert(frames >= 117 && frames <= 121);
    printf("Preserved PC-only baseline fixture: %u frames / 4s\n", frames);
#endif
    return 0;
}
'''


def main() -> None:
    v7 = (HERE / "legacy_c_adapter_v7.c.in").read_text()
    v12 = (HERE / "legacy_c_adapter_v12.c.in").read_text()
    defines = "\n".join(line for line in v7.splitlines()
                        if re.match(r"#define OPENSPM_M14_\w+\s", line)) + "\n"
    helper_start = v7.index("#ifdef DOLRECOMP_BACKEND_LLVM\n/* dev68:")
    helper_end = v7.index("\n#endif", helper_start) + len("\n#endif")
    source = PRELUDE + defines + function(v7, "bridge_m14_retrace_period_ns")
    source += function(v7, "bridge_m14_context_address_valid")
    source += function(v7, "bridge_host_call_v7")
    source += function(v7, "bridge_m14_should_log_tick")
    source += v7[helper_start:helper_end] + "\n"
    source += function(v7, "bridge_m14_service_retrace")
    source += function(v12, "bridge_run_v20") + HARNESS
    compiler = os.environ.get("CC") or shutil.which("cc") or shutil.which("clang")
    if not compiler:
        raise SystemExit("A C11 compiler is required for the executable regression")
    with tempfile.TemporaryDirectory(prefix="openspm-idle-") as directory:
        root = Path(directory)
        c = root / "idle.c"
        c.write_text(source)
        for llvm in (False, True):
            executable = root / ("idle-llvm" if llvm else "idle-baseline")
            command = [compiler, "-std=c11", "-Wall", "-Wextra", "-Werror",
                       "-D_WIN32", "-I", str(HERE.parents[1] / "include")]
            # native_api's Windows calling convention is handled by GCC on
            # Linux via this test-only definition; no Windows SDK is needed.
            command += ["-D__cdecl=", "-D__declspec(x)="]
            if llvm:
                command.append("-DDOLRECOMP_BACKEND_LLVM")
            subprocess.run(command + [str(c), "-o", str(executable)], check=True)
            subprocess.run([str(executable)], check=True)


if __name__ == "__main__":
    main()
