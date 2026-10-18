#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Adapts DolRecomp (GPL-3.0) and carries parts of its source; see LICENSE.
"""dev100 transform checks and compiled host-only behavioral regression tests.

Run with --cc gcc (or clang) for the actual C runtime tests. No game inputs.
"""
from pathlib import Path
import argparse
import subprocess
import tempfile

import apply_llvm_scene_profile as patch

HERE = Path(__file__).resolve().parent


def test_transform():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = root / patch.TARGET
        path.parent.mkdir(parents=True)
        path.write_text(patch.ANCHOR)
        assert patch.transform(root, True) == "verified"
        assert path.read_text() == patch.ANCHOR
        assert patch.transform(root) == "applied"
        assert patch.transform(root) == "already-applied"
        assert path.read_text() == patch.REPLACEMENT
        assert "native_abi_ &&" not in patch.REPLACEMENT
        assert patch.retire(root, True) == "verified-retire"
        assert path.read_text() == patch.REPLACEMENT
        assert patch.retire(root) == "retired"
        assert path.read_text() == patch.ANCHOR
        assert patch.retire(root) == "already-retired"
        assert patch.transform(root) == "applied"
        assert path.read_text() == patch.REPLACEMENT
        path.write_text(patch.OLD_REPLACEMENT)
        assert patch.transform(root, True) == "verified-upgrade"
        assert path.read_text() == patch.OLD_REPLACEMENT
        assert patch.transform(root) == "upgraded"
        assert path.read_text() == patch.REPLACEMENT
        path.write_text(patch.REPLACEMENT.replace("builder_.CreateCall(marker,", "BROKEN("))
        try:
            patch.transform(root)
        except RuntimeError:
            pass
        else:
            raise AssertionError("partial transform accepted")


HARNESS = r'''
#include <assert.h>
#include <stdio.h>
#include "m14_scene_profile.h"
typedef uint32_t u32;
typedef struct { int64_t downcount; u32 pc, exception, value; } CPUState;
typedef struct { int fallback_requested; CPUState cpu; } openspm_bridge_instance;
static openspm_bridge_instance instance;
static int mode, entered, left, dispatched, depth_allowed = 1;
static openspm_bridge_instance *bridge_from_cpu(CPUState *cpu) {
    return cpu->value == 99u ? NULL : &instance;
}
static int dolrecomp_call_enter(void) { ++entered; return depth_allowed; }
static void dolrecomp_call_leave(void) { ++left; }
static int dolrecomp_call(CPUState *cpu, u32 target) {
    ++dispatched;
    cpu->pc = target + 4u;
    ++cpu->value;
    cpu->downcount -= 10;
    if (mode == 1) cpu->pc += 4u;
    if (mode == 2) cpu->exception = 1u;
    if (mode == 3) instance.fallback_requested = 1;
    return mode != 4;
}
/* NESTED_DISPATCH */

#define OPENSPM_M14_FRAME_PHASE_COUNT 4u
static openspm_bridge_instance *g_m14_dev67_instance = &instance;
static uint64_t fake_now;
static unsigned clock_calls, log_calls;
static uint64_t bridge_m14_now_ns(openspm_bridge_instance *p) {
    (void)p; ++clock_calls; return fake_now;
}
static void bridge_v44_log(openspm_bridge_instance *p, const char *text) {
    (void)p; assert(strstr(text, "M14 LLVM dev100 scene") != NULL); ++log_calls;
}
static void openspm_m14_note_frame_phase_v20(CPUState *p, u32 phase) {
    (void)p; (void)phase;
}
static void openspm_m14_note_spmario_main_v20(CPUState *p) { (void)p; }
static void bridge_dev95_note_seq_state(openspm_bridge_instance *p, const char *s) {
    (void)p; (void)s;
}
static void bridge_dev96_note_player_state(openspm_bridge_instance *p, const char *s) {
    (void)p; (void)s;
}
/* SCENE_BRIDGE */

int main(void) {
    m14_scene_profile p = {0};
    CPUState cpu;
    uint64_t sum;
    unsigned i;
    m14_scene_phase(&p, 2u, 1u); /* startup main without a display frame */
    assert(!p.active);
    m14_scene_phase(&p, 1u, 100u);
    assert(p.active && p.sampled[1] == 1u);
    m14_scene_boundary(&p, 1u, 0x10u, 0x100u, 110u);
    /* No notifications during cold escape/reentry: all elapsed time retained. */
    m14_scene_boundary(&p, 1u, 0x10u, 0x200u, 510u);
    m14_scene_phase(&p, 2u, 600u);
    m14_scene_boundary(&p, 2u, 0x20u, 0x300u, 620u);
    m14_scene_boundary(&p, 1u, 0x10u, 0x400u, 800u); /* wrong phase ignored */
    m14_scene_phase(&p, 3u, 1000u);
    assert(!p.active && p.total_ns[1] == 500u && p.total_ns[2] == 400u);
    sum = 0u;
    for (i = 0u; i < M14_SCENE_SLOTS; ++i) sum += p.slots[i].ns;
    assert(sum == 900u);
    m14_scene_drain(&p, 1000u);
    assert(p.frame == 1u && p.report_ns == 1000u && p.total_ns[1] == 0u);
    for (i = 1u; i < 64u; ++i) {
        m14_scene_phase(&p, 1u, 0u);
        assert(!p.active);
        m14_scene_boundary(&p, 1u, 1u, 1u, 9999u);
        m14_scene_phase(&p, 2u, 0u);
        m14_scene_phase(&p, 3u, 0u);
    }
    assert(p.total_ns[1] == 0u && p.sampled[1] == 0u);
    m14_scene_phase(&p, 1u, 1000u);
    assert(p.active);
    for (i = 0u; i < 200u; ++i)
        m14_scene_boundary(&p, 1u, 4u * i, 0x1000u + 4u * i, 1000u + i);
    m14_scene_phase(&p, 3u, 1200u);
    sum = p.overflow_ns[1];
    for (i = 0u; i < M14_SCENE_SLOTS; ++i) sum += p.slots[i].ns;
    assert(p.overflow_ns[1] > 0u && sum == 200u && p.total_ns[1] == 200u);
    /* Backwards clock never wraps elapsed time to a huge unsigned number. */
    p.frame = 0u;
    m14_scene_phase(&p, 1u, 5000u);
    m14_scene_phase(&p, 3u, 4000u);
    assert(p.total_ns[1] == 200u);

    cpu = (CPUState){100, 0, 0, 0};
    assert(openspm_m14_dev99_nested_dispatch(&cpu, 1000u, 1004u) == 1);
    assert(cpu.value == 1u && cpu.downcount == 90 && entered == left);
    cpu = (CPUState){5, 0, 0, 0};
    assert(openspm_m14_dev99_nested_dispatch(&cpu, 1000u, 1004u) == 0);
    assert(cpu.pc == 1004u && cpu.value == 1u && cpu.downcount == -5);
    assert(g_m14_dev100_nested_budget_yields == 1u && entered == left);
    i = (unsigned)dispatched;
    assert(openspm_m14_dev99_nested_dispatch(&cpu, 1000u, 1004u) == 0);
    assert((unsigned)dispatched == i && g_m14_dev100_nested_budget_yields == 2u);
    for (mode = 1; mode <= 4; ++mode) {
        cpu = (CPUState){100, 0, 0, 0};
        instance.fallback_requested = 0;
        i = openspm_m14_dev99_nested_dispatch(&cpu, 1000u, 1004u);
        /* The LLVM caller checks exception/PC as well as the handled result. */
        assert(!(i && cpu.pc == 1004u && cpu.exception == 0u));
        assert(entered == left);
    }
    depth_allowed = 0;
    cpu = (CPUState){100, 0, 0, 0};
    i = (unsigned)dispatched;
    assert(openspm_m14_dev99_nested_dispatch(&cpu, 1000u, 1004u) == 0);
    assert((unsigned)dispatched == i && cpu.value == 0u);
    assert(openspm_m14_dev99_nested_dispatch(NULL, 1000u, 1004u) == 0);

    fake_now = 100u;
    openspm_m14_note_frame_phase_native(1u);
    assert(openspm_m14_scene_profile_active);
    fake_now = 200u;
    openspm_m14_scene_interval(1u, 0x10u, 0x100u);
    fake_now = 300u;
    openspm_m14_note_frame_phase_native(2u);
    fake_now = 400u;
    openspm_m14_note_frame_phase_native(3u);
    assert(!openspm_m14_scene_profile_active && clock_calls == 4u);
    assert(g_m14_scene_profile.total_ns[1] == 200u);
    assert(g_m14_scene_profile.total_ns[2] == 100u);
    bridge_dev100_report(&instance, 500u, 1);
    assert(log_calls == 4u && g_m14_scene_profile.frame == 1u);
    i = clock_calls;
    openspm_m14_note_frame_phase_native(1u);
    openspm_m14_scene_interval(1u, 0x10u, 0x100u);
    openspm_m14_note_frame_phase_native(2u);
    openspm_m14_note_frame_phase_native(3u);
    assert(clock_calls == i); /* zero extra clocks in unsampled frames */
    puts("PASS: dev100 sampled interval accounting and nested callback budget behavior");
    return 0;
}
'''


def test_c_runtime(cc):
    bridge = (HERE / "legacy_c_adapter_v42_dev69.c.in").read_text()
    start = bridge.index("static uint64_t g_m14_dev99_nested_attempts;")
    end = bridge.index("static float bridge_dev96_float_from_bits", start)
    source = HARNESS.replace("/* NESTED_DISPATCH */", bridge[start:end])
    start = bridge.index("/* dev100 samples whole display/main intervals")
    end = bridge.index("static uint64_t bridge_dev67_elapsed_us", start)
    source = source.replace("/* SCENE_BRIDGE */", bridge[start:end])
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "scene_test.c"
        exe = Path(tmp) / "scene_test"
        path.write_text(source)
        subprocess.run([cc, "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
                        "-I", str(HERE), str(path), "-o", str(exe)], check=True)
        subprocess.run([str(exe)], check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cc", help="GCC-compatible C compiler for runtime tests")
    args = parser.parse_args()
    test_transform()
    print("PASS: dev100 transform/check-only/idempotence/reject-partial contracts")
    if args.cc:
        test_c_runtime(args.cc)
