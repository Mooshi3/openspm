from pathlib import Path


def replace_once(path: str, old: str, new: str, label: str) -> None:
    p = Path(path)
    s = p.read_text().replace("\r\n", "\n")
    count = s.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one anchor, found {count}")
    p.write_text(s.replace(old, new, 1))


# M11D is deliberately runtime-only: it reuses the exact M11C game DLL and
# turns the temporary RecompCore/ModernGekko chassis into an A/B lab.  The two
# idle optimizations are independent so one executable can measure baseline,
# analyzer-detected busy waits, the known R8PE01 scheduler idle PC, or both.

# ---------------------------------------------------------------------------
# 1. Optional known R8PE01 scheduler-idle PC.
# ---------------------------------------------------------------------------
runtime_cpp = "ModernGekko/src/runtime/dolphin_runtime.cpp"
replace_once(
    runtime_cpp,
    '#include "Core/Config/MainSettings.h"\n',
    '#include "Core/Config/MainSettings.h"\n#include "Core/Config/StaticRecompSettings.h"\n',
    "StaticRecomp settings include",
)
replace_once(
    runtime_cpp,
    '  Config::SetBase(Config::MAIN_CPU_CORE, SelectCPUCore());\n',
    '''  Config::SetBase(Config::MAIN_CPU_CORE, SelectCPUCore());\n\n  // R8PE01 0x8027551C is the no-runnable-thread spin inside Nintendo OS\n  // SelectThread. Keep it opt-in so the same executable is its own A/B control.\n  const char* openspm_idle_pc_env = std::getenv("OPENSPM_IDLE_PC");\n  const bool openspm_known_idle =\n      openspm_idle_pc_env && *openspm_idle_pc_env == '1';\n  Config::SetBase(Config::MAIN_STATICRECOMP_IDLE_PC,\n                  openspm_known_idle ? 0x8027551Cu : 0u);\n  std::fprintf(stderr, "[OPENSPM-LAB] known_idle_pc=%s pc=8027551c\\n",\n               openspm_known_idle ? "on" : "off");\n  std::fflush(stderr);\n''',
    "runtime-selectable R8PE01 scheduler idle PC",
)

# ---------------------------------------------------------------------------
# 2. Port RecompCore PR #19's analyzer-backed busy-wait detection, but make it
#    strictly opt-in with OPENSPM_BUSYWAIT=1 and add counters for our A/B.
# ---------------------------------------------------------------------------
core_h = "ModernGekko/vendor/dolphin/Source/Core/Core/PowerPC/StaticRecomp/StaticRecompCore.h"
replace_once(
    core_h,
    '''  bool IsHostCallAddress(u32 address) const;\n  bool ShouldYieldAt(u32 address);\n\n  void ClearCache() override;\n''',
    '''  bool IsHostCallAddress(u32 address) const;\n  bool ShouldYieldAt(u32 address);\n  bool IsBusyWaitLoop(u32 address);\n\n  void ClearCache() override;\n''',
    "busy-wait analyzer declaration",
)
replace_once(
    core_h,
    '''  u64 m_timebase_cycle_remainder = 0;\n  std::unordered_map<u32, u64> m_dispatch_samples;\n  u64 m_bursts = 0;          // SyncIn..SyncOut native runs (diagnostic)\n''',
    '''  u64 m_timebase_cycle_remainder = 0;\n  std::unordered_map<u32, u64> m_dispatch_samples;\n  // Analyzer result per loop head. Cleared whenever code/mappings can change.\n  std::unordered_map<u32, bool> m_busy_wait_cache;\n  u64 m_busy_wait_skips = 0;\n  u64 m_configured_idle_skips = 0;\n  u64 m_bursts = 0;          // SyncIn..SyncOut native runs (diagnostic)\n''',
    "busy-wait cache and counters",
)

core_cpp = "ModernGekko/vendor/dolphin/Source/Core/Core/PowerPC/StaticRecomp/StaticRecompCore.cpp"
replace_once(
    core_cpp,
    '''void StaticRecompCore::ClearCache()\n{\n''',
    '''bool StaticRecompCore::IsBusyWaitLoop(u32 address)\n{\n  // Experimental path: OFF unless the launcher explicitly asks for it.\n  const char* enabled_env = std::getenv("OPENSPM_BUSYWAIT");\n  if (!enabled_env || *enabled_env != '1')\n    return false;\n\n  if ((address & 3u) != 0)\n    return false;\n\n  // Analysis reads guest memory and is deliberately disabled while debugging\n  // or branch-watch is active; those modes require every block to execute.\n  if (IsDebuggingEnabled() || IsBranchWatchEnabled())\n    return false;\n\n  const auto cached = m_busy_wait_cache.find(address);\n  if (cached != m_busy_wait_cache.end())\n    return cached->second;\n\n  constexpr std::size_t max_loop_instructions = 64;\n  analyzer.Analyze(address, &code_block, &m_code_buffer,\n                   std::min(max_loop_instructions, m_code_buffer.size()));\n\n  const bool is_busy_wait =\n      !code_block.m_memory_exception &&\n      std::any_of(m_code_buffer.begin(),\n                  m_code_buffer.begin() + code_block.m_num_instructions,\n                  [address](const PPCAnalyst::CodeOp& op) {\n                    return op.branchIsIdleLoop && op.branchTo == address;\n                  });\n  m_busy_wait_cache.emplace(address, is_busy_wait);\n  if (is_busy_wait)\n  {\n    std::fprintf(stderr, "[OPENSPM-IDLE-DETECT] pc=%08x\\n", address);\n    std::fflush(stderr);\n  }\n  return is_busy_wait;\n}\n\nvoid StaticRecompCore::ClearCache()\n{\n  m_busy_wait_cache.clear();\n''',
    "analyzer-backed busy-wait detection",
)

smc_cpp = "ModernGekko/vendor/dolphin/Source/Core/Core/PowerPC/StaticRecomp/StaticRecompCore_SMC.cpp"
replace_once(
    smc_cpp,
    '''  m_active_rel_sections = std::move(discovered);\n  ++m_rel_mapping_generation;\n''',
    '''  m_active_rel_sections = std::move(discovered);\n  m_busy_wait_cache.clear();\n  ++m_rel_mapping_generation;\n''',
    "invalidate busy-wait cache on REL mapping change",
)

run_cpp = "ModernGekko/vendor/dolphin/Source/Core/Core/PowerPC/StaticRecomp/StaticRecompCore_Run.cpp"
replace_once(
    run_cpp,
    '''          // Idle loop skipping for configured target loops (e.g. Wii Menu OSIdleThread)\n          if (m_guest.pc == m_idle_pc && m_idle_pc != 0)\n          {\n            m_system.GetCoreTiming().Idle();\n          }\n''',
    '''          // Return pure guest busy-waits to CoreTiming instead of burning host CPU.\n          // The configured R8PE01 scheduler PC and analyzer detector stay separate\n          // so the same binary can benchmark each arm independently.\n          const bool configured_idle = m_idle_pc != 0 && m_guest.pc == m_idle_pc;\n          const bool detected_idle =\n              !configured_idle && m_guest.pc == runtime_dispatch_address &&\n              IsBusyWaitLoop(runtime_dispatch_address);\n          if (configured_idle || detected_idle)\n          {\n            if (configured_idle)\n              ++m_configured_idle_skips;\n            else\n              ++m_busy_wait_skips;\n            m_system.GetCoreTiming().Idle();\n          }\n''',
    "idle skip execution",
)

replace_once(
    run_cpp,
    '''  u64 openspm_perf_last_cycles = m_charged_cycles;\n''',
    '''  u64 openspm_perf_last_cycles = m_charged_cycles;\n  u64 openspm_perf_last_idle_auto = m_busy_wait_skips;\n  u64 openspm_perf_last_idle_known = m_configured_idle_skips;\n''',
    "idle perf baselines",
)
replace_once(
    run_cpp,
    '''        "fallback_jit=%llu fallback_steps=%llu host_calls=%llu hook_fb=%llu cycles=%llu\\n",\n''',
    '''        "fallback_jit=%llu fallback_steps=%llu host_calls=%llu hook_fb=%llu cycles=%llu "\n        "idle_auto=%llu idle_known=%llu\\n",\n''',
    "idle perf format",
)
replace_once(
    run_cpp,
    '''        static_cast<unsigned long long>(m_hook_fallback_instructions - openspm_perf_last_hook_fb),\n        static_cast<unsigned long long>(m_charged_cycles - openspm_perf_last_cycles));\n''',
    '''        static_cast<unsigned long long>(m_hook_fallback_instructions - openspm_perf_last_hook_fb),\n        static_cast<unsigned long long>(m_charged_cycles - openspm_perf_last_cycles),\n        static_cast<unsigned long long>(m_busy_wait_skips - openspm_perf_last_idle_auto),\n        static_cast<unsigned long long>(m_configured_idle_skips - openspm_perf_last_idle_known));\n''',
    "idle perf arguments",
)
replace_once(
    run_cpp,
    '''    openspm_perf_last_cycles = m_charged_cycles;\n    openspm_perf_last_fallback_jit = openspm_perf_fallback_jit;\n''',
    '''    openspm_perf_last_cycles = m_charged_cycles;\n    openspm_perf_last_idle_auto = m_busy_wait_skips;\n    openspm_perf_last_idle_known = m_configured_idle_skips;\n    openspm_perf_last_fallback_jit = openspm_perf_fallback_jit;\n''',
    "idle perf reset",
)

print("M11D runtime lab: busy-wait detector + known-idle A/B + counters staged")
