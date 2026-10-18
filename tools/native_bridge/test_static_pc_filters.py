#!/usr/bin/env python3
"""Execute production filtered lookups; collisions must preserve exact answers."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
from test_llvm_idle_wake import function

HERE = Path(__file__).resolve().parent


def main() -> None:
    seam = (HERE / "legacy_c_adapter_v26.c.in").read_text()
    host = (HERE / "legacy_c_adapter_v39.c.in").read_text()
    source = r'''
#include <assert.h>
#include <stddef.h>
#include <stdio.h>
#include "m14_static_pc_filter.h"
typedef struct {uint32_t call_pc, target, return_pc;} openspm_local_llvm_hostcall_seam;
#define OPENSPM_LOCAL_LLVM_HOSTCALL_SEAM_COUNT 383u
static openspm_local_llvm_hostcall_seam OPENSPM_LOCAL_LLVM_HOSTCALL_SEAMS[383];
static uint32_t g_m14_dev69_seam_filter[OPENSPM_M14_PC_FILTER_WORDS];
static uint32_t g_m14_dev69_seam_filter_ready;
static uint64_t g_m14_dev69_seam_rejects;
static uint64_t g_m14_v36_lookup_calls, g_m14_v36_lookup_steps;
#define OPENSPM_M14_HOST_TARGET(value) (uint32_t)(value),
static uint32_t g_m14_dev50_host_targets[] = {
#include "m14_host_targets.inc"
};
#undef OPENSPM_M14_HOST_TARGET
#define OPENSPM_M14_DEV50_HOST_TARGET_COUNT (sizeof(g_m14_dev50_host_targets)/sizeof(uint32_t))
static uint32_t g_m14_dev69_host_filter[OPENSPM_M14_PC_FILTER_WORDS];
static uint32_t g_m14_dev69_host_filter_ready;
static uint64_t g_m14_dev69_host_rejects;
static uint64_t g_m14_dev50_host_lookup_calls, g_m14_dev50_host_lookup_steps;
'''
    source += function(seam, "bridge_v36_find_seam")
    for name in ("bridge_dev50_sort_host_targets", "bridge_dev50_host_lower_bound",
                 "bridge_dev50_host_target_exact", "bridge_dev50_host_target_in_range"):
        source += function(host, name)
    source += r'''
static void check(uint32_t pc) {
    uint32_t offset = pc - UINT32_C(0x80001000);
    int expected_seam = offset < 383u * 64u && offset % 64u == 0u;
    int expected_host = 0;
    unsigned i;
    const openspm_local_llvm_hostcall_seam *s = bridge_v36_find_seam(pc);
    assert((s != NULL) == expected_seam);
    if (s) assert(s->call_pc == pc && s->return_pc == pc + 4);
    for (i = 0; i < OPENSPM_M14_DEV50_HOST_TARGET_COUNT; ++i)
        expected_host |= g_m14_dev50_host_targets[i] == pc;
    assert(bridge_dev50_host_target_exact(pc) == expected_host);
}
int main(void) {
    uint32_t i, random = 17;
    for (i = 0; i < 383; ++i) {
        OPENSPM_LOCAL_LLVM_HOSTCALL_SEAMS[i].call_pc = 0x80001000u + i * 64u;
        OPENSPM_LOCAL_LLVM_HOSTCALL_SEAMS[i].return_pc = 0x80001004u + i * 64u;
    }
    bridge_dev50_sort_host_targets();
    for (i = 0; i < 0x400000u; i += 4) check(0x80000000u + i);
    for (i = 0; i < 100000; ++i) {
        random = random * 1664525u + 1013904223u; check(random);
    }
    /* Same-word unaligned PCs deliberately collide in the filter. */
    for (i = 0; i < 383; ++i) {
        uint32_t pc = OPENSPM_LOCAL_LLVM_HOSTCALL_SEAMS[i].call_pc;
        check(pc); check(pc+1); check(pc+2); check(pc+3);
    }
    for (i = 0; i < OPENSPM_M14_DEV50_HOST_TARGET_COUNT; ++i) {
        uint32_t pc = g_m14_dev50_host_targets[i];
        check(pc); check(pc+1); check(pc-1);
        assert(bridge_dev50_host_target_in_range(pc, pc+1));
        assert(!bridge_dev50_host_target_in_range(pc, pc));
    }
    check(0); check(UINT32_MAX);
    assert(g_m14_dev69_seam_rejects > 1000000u);
    assert(g_m14_dev69_host_rejects > 1000000u);
    puts("PASS: production seam/host lookups match reference for >1M PCs, all targets, forced collisions and boundary cases; range queries unchanged");
}
'''
    compiler = os.environ.get("CC") or shutil.which("cc") or shutil.which("clang")
    if not compiler:
        raise SystemExit("C11 compiler required")
    with tempfile.TemporaryDirectory(prefix="openspm-filter-") as directory:
        p = Path(directory)
        (p / "test.c").write_text(source)
        subprocess.run([compiler, "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
                        "-I", str(HERE), str(p / "test.c"), "-o", str(p / "test")], check=True)
        subprocess.run([str(p / "test")], check=True)


if __name__ == "__main__":
    main()
