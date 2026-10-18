from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    text = text.replace("\r\n", "\n")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)


# The generic M11B patch owns portable OpenSPM state/menu behavior and broad runtime counters.
# This transform owns only the transitional REL resolver optimization and its diagnostics, because
# StaticRecompCore_SMC.cpp is already modified by the older portable REL-discovery patch.

header = Path("ModernGekko/vendor/dolphin/Source/Core/Core/PowerPC/StaticRecomp/StaticRecompCore.h")
h = header.read_text()
h = replace_once(
    h,
    """  bool m_openspm_perf_enabled = false;\n  u64 m_fallback_jit_runs = 0;\n  u64 m_host_call_runs = 0;\n\n  // D4 guard state""",
    """  bool m_openspm_perf_enabled = false;\n  u64 m_fallback_jit_runs = 0;\n  u64 m_host_call_runs = 0;\n  u64 m_rel_refreshes = 0;\n  u64 m_rel_resolve_native_calls = 0;\n  mutable u64 m_rel_resolve_runtime_calls = 0;\n  mutable u64 m_rel_section_checks = 0;\n  mutable int m_last_active_rel_section = -1;\n\n  // D4 guard state""",
    "REL diagnostic fields",
)
header.write_text(h)

core = Path("ModernGekko/vendor/dolphin/Source/Core/Core/PowerPC/StaticRecomp/StaticRecompCore.cpp")
c = core.read_text()
c = replace_once(
    c,
    """               \"fallback_jit_runs=%llu host_calls=%llu\\n\",\n               (unsigned long long)m_native_dispatches, (unsigned long long)m_fallback_steps,\n               (unsigned long long)m_native_exceptions,\n               (unsigned long long)m_hook_fallback_instructions, m_failed_chunks,\n               (unsigned long long)m_verifications, (unsigned long long)m_reverify_events,\n               (unsigned long long)m_bursts, (unsigned long long)m_charged_cycles,\n               (unsigned long long)m_fallback_jit_runs, (unsigned long long)m_host_call_runs);\n""",
    """               \"fallback_jit_runs=%llu host_calls=%llu rel_refreshes=%llu rel_native=%llu \"\n               \"rel_runtime=%llu rel_checks=%llu\\n\",\n               (unsigned long long)m_native_dispatches, (unsigned long long)m_fallback_steps,\n               (unsigned long long)m_native_exceptions,\n               (unsigned long long)m_hook_fallback_instructions, m_failed_chunks,\n               (unsigned long long)m_verifications, (unsigned long long)m_reverify_events,\n               (unsigned long long)m_bursts, (unsigned long long)m_charged_cycles,\n               (unsigned long long)m_fallback_jit_runs, (unsigned long long)m_host_call_runs,\n               (unsigned long long)m_rel_refreshes, (unsigned long long)m_rel_resolve_native_calls,\n               (unsigned long long)m_rel_resolve_runtime_calls,\n               (unsigned long long)m_rel_section_checks);\n""",
    "shutdown REL totals",
)
core.write_text(c)

run = Path("ModernGekko/vendor/dolphin/Source/Core/Core/PowerPC/StaticRecomp/StaticRecompCore_Run.cpp")
r = run.read_text()
r = replace_once(
    r,
    """  u64 perf_last_host_calls = m_host_call_runs;\n  u64 perf_last_hook_fallback = m_hook_fallback_instructions;\n  const auto report_openspm_perf = [&](bool force) {\n""",
    """  u64 perf_last_host_calls = m_host_call_runs;\n  u64 perf_last_hook_fallback = m_hook_fallback_instructions;\n  u64 perf_last_rel_refreshes = m_rel_refreshes;\n  u64 perf_last_rel_native = m_rel_resolve_native_calls;\n  u64 perf_last_rel_runtime = m_rel_resolve_runtime_calls;\n  u64 perf_last_rel_checks = m_rel_section_checks;\n  const auto report_openspm_perf = [&](bool force) {\n""",
    "perf REL baselines",
)
r = replace_once(
    r,
    """    std::fprintf(stderr,\n                 \"[OPENSPM-PERF] dt=%.3f native=%llu bursts=%llu dispatch_per_burst=%.2f \"\n                 \"fallback_jit=%llu fallback_steps=%llu host_calls=%llu hook_fb=%llu\\n\",\n                 seconds, static_cast<unsigned long long>(native),\n                 static_cast<unsigned long long>(bursts), dispatches_per_burst,\n                 static_cast<unsigned long long>(m_fallback_jit_runs - perf_last_fallback_jit),\n                 static_cast<unsigned long long>(m_fallback_steps - perf_last_fallback_steps),\n                 static_cast<unsigned long long>(m_host_call_runs - perf_last_host_calls),\n                 static_cast<unsigned long long>(m_hook_fallback_instructions - perf_last_hook_fallback));\n""",
    """    std::fprintf(stderr,\n                 \"[OPENSPM-PERF] dt=%.3f native=%llu bursts=%llu dispatch_per_burst=%.2f \"\n                 \"fallback_jit=%llu fallback_steps=%llu host_calls=%llu hook_fb=%llu \"\n                 \"rel_refresh=%llu rel_native=%llu rel_runtime=%llu rel_checks=%llu\\n\",\n                 seconds, static_cast<unsigned long long>(native),\n                 static_cast<unsigned long long>(bursts), dispatches_per_burst,\n                 static_cast<unsigned long long>(m_fallback_jit_runs - perf_last_fallback_jit),\n                 static_cast<unsigned long long>(m_fallback_steps - perf_last_fallback_steps),\n                 static_cast<unsigned long long>(m_host_call_runs - perf_last_host_calls),\n                 static_cast<unsigned long long>(m_hook_fallback_instructions - perf_last_hook_fallback),\n                 static_cast<unsigned long long>(m_rel_refreshes - perf_last_rel_refreshes),\n                 static_cast<unsigned long long>(m_rel_resolve_native_calls - perf_last_rel_native),\n                 static_cast<unsigned long long>(m_rel_resolve_runtime_calls - perf_last_rel_runtime),\n                 static_cast<unsigned long long>(m_rel_section_checks - perf_last_rel_checks));\n""",
    "perf REL sample fields",
)
r = replace_once(
    r,
    """    perf_last_host_calls = m_host_call_runs;\n    perf_last_hook_fallback = m_hook_fallback_instructions;\n  };\n""",
    """    perf_last_host_calls = m_host_call_runs;\n    perf_last_hook_fallback = m_hook_fallback_instructions;\n    perf_last_rel_refreshes = m_rel_refreshes;\n    perf_last_rel_native = m_rel_resolve_native_calls;\n    perf_last_rel_runtime = m_rel_resolve_runtime_calls;\n    perf_last_rel_checks = m_rel_section_checks;\n  };\n""",
    "perf REL sample rollover",
)
run.write_text(r)

smc = Path("ModernGekko/vendor/dolphin/Source/Core/Core/PowerPC/StaticRecomp/StaticRecompCore_SMC.cpp")
s = smc.read_text()

# The existing portable REL-discovery patch already gates and bounds expensive discovery scans.
# Count actual dirty scans without replacing that mechanism.
s = replace_once(
    s,
    """  if (!m_rel_discovery_dirty)\n    return;\n\n  std::vector<ActiveRelSection> discovered;\n""",
    """  if (!m_rel_discovery_dirty)\n    return;\n  if (m_openspm_perf_enabled)\n    ++m_rel_refreshes;\n\n  std::vector<ActiveRelSection> discovered;\n""",
    "REL discovery counter",
)

s = replace_once(
    s,
    """  if (!changed)\n    return;\n  m_active_rel_sections = std::move(discovered);\n  ++m_rel_mapping_generation;\n""",
    """  if (!changed)\n    return;\n  m_active_rel_sections = std::move(discovered);\n  m_last_active_rel_section = -1;\n  ++m_rel_mapping_generation;\n""",
    "REL locality cache reset",
)

old_resolvers = """bool StaticRecompCore::ResolveNativeAddress(u32 runtime_address, u32* linked_address,\n                                            u32* rel_section_index)\n{\n  const auto resolve_active = [&]() {\n    for (u32 i = 0; i < m_active_rel_sections.size(); ++i)\n    {\n      const ActiveRelSection& section = m_active_rel_sections[i];\n      if (runtime_address >= section.runtime_start &&\n          static_cast<u64>(runtime_address) < static_cast<u64>(section.runtime_start) + section.size)\n      {\n        *linked_address = section.linked_start + (runtime_address - section.runtime_start);\n        if (rel_section_index)\n          *rel_section_index = i;\n        return true;\n      }\n    }\n    return false;\n  };\n  if (resolve_active())\n    return true;\n  const int direct_index = GetAddressLookupIndex(runtime_address);\n  if (direct_index >= 0 && direct_index < static_cast<int>(m_chunk_lookup_table.size()))\n  {\n    const int chunk = m_chunk_lookup_table[direct_index];\n    if (chunk >= 0 && m_chunk_rel_sections[chunk] < 0)\n    {\n      *linked_address = runtime_address;\n      if (rel_section_index)\n        *rel_section_index = 0xffffffffu;\n      return true;\n    }\n  }\n  RefreshRelSections(runtime_address);\n  return resolve_active();\n}\n\nbool StaticRecompCore::ResolveRuntimeAddress(u32 linked_address, u32* runtime_address) const\n{\n  for (const ActiveRelSection& section : m_active_rel_sections)\n  {\n    if (linked_address >= section.linked_start &&\n        static_cast<u64>(linked_address) < static_cast<u64>(section.linked_start) + section.size)\n    {\n      *runtime_address = section.runtime_start + (linked_address - section.linked_start);\n      return true;\n    }\n  }\n  *runtime_address = linked_address;\n  return true;\n}\n"""

new_resolvers = """bool StaticRecompCore::ResolveNativeAddress(u32 runtime_address, u32* linked_address,\n                                            u32* rel_section_index)\n{\n  if (m_openspm_perf_enabled)\n    ++m_rel_resolve_native_calls;\n\n  // Main-executable addresses dominate normal gameplay. Resolve them with the O(1) table before\n  // touching the active REL vector. A runtime REL section cannot overlap live DOL code.\n  const int direct_index = GetAddressLookupIndex(runtime_address);\n  if (direct_index >= 0 && direct_index < static_cast<int>(m_chunk_lookup_table.size()))\n  {\n    const int chunk = m_chunk_lookup_table[direct_index];\n    if (chunk >= 0 && m_chunk_rel_sections[chunk] < 0)\n    {\n      *linked_address = runtime_address;\n      if (rel_section_index)\n        *rel_section_index = 0xffffffffu;\n      return true;\n    }\n  }\n\n  const auto resolve_section = [&](u32 i) {\n    if (m_openspm_perf_enabled)\n      ++m_rel_section_checks;\n    const ActiveRelSection& section = m_active_rel_sections[i];\n    if (runtime_address >= section.runtime_start &&\n        static_cast<u64>(runtime_address) < static_cast<u64>(section.runtime_start) + section.size)\n    {\n      *linked_address = section.linked_start + (runtime_address - section.runtime_start);\n      if (rel_section_index)\n        *rel_section_index = i;\n      m_last_active_rel_section = static_cast<int>(i);\n      return true;\n    }\n    return false;\n  };\n\n  if (m_last_active_rel_section >= 0 &&\n      m_last_active_rel_section < static_cast<int>(m_active_rel_sections.size()) &&\n      resolve_section(static_cast<u32>(m_last_active_rel_section)))\n    return true;\n\n  for (u32 i = 0; i < m_active_rel_sections.size(); ++i)\n  {\n    if (static_cast<int>(i) != m_last_active_rel_section && resolve_section(i))\n      return true;\n  }\n\n  // Keep the existing invalidation-gated, runtime-hinted discovery policy. If it was dirty, a scan\n  // may have changed the active section set, so retry against the newly discovered map once.\n  const bool discovery_was_dirty = m_rel_discovery_dirty;\n  RefreshRelSections(runtime_address);\n  if (discovery_was_dirty)\n  {\n    for (u32 i = 0; i < m_active_rel_sections.size(); ++i)\n    {\n      if (resolve_section(i))\n        return true;\n    }\n  }\n  return false;\n}\n\nbool StaticRecompCore::ResolveRuntimeAddress(u32 linked_address, u32* runtime_address) const\n{\n  if (m_openspm_perf_enabled)\n    ++m_rel_resolve_runtime_calls;\n\n  const int direct_index = GetAddressLookupIndex(linked_address);\n  if (direct_index >= 0 && direct_index < static_cast<int>(m_chunk_lookup_table.size()))\n  {\n    const int chunk = m_chunk_lookup_table[direct_index];\n    if (chunk >= 0 && m_chunk_rel_sections[chunk] < 0)\n    {\n      *runtime_address = linked_address;\n      return true;\n    }\n  }\n\n  const auto resolve_section = [&](u32 i) {\n    if (m_openspm_perf_enabled)\n      ++m_rel_section_checks;\n    const ActiveRelSection& section = m_active_rel_sections[i];\n    if (linked_address >= section.linked_start &&\n        static_cast<u64>(linked_address) < static_cast<u64>(section.linked_start) + section.size)\n    {\n      *runtime_address = section.runtime_start + (linked_address - section.linked_start);\n      m_last_active_rel_section = static_cast<int>(i);\n      return true;\n    }\n    return false;\n  };\n\n  if (m_last_active_rel_section >= 0 &&\n      m_last_active_rel_section < static_cast<int>(m_active_rel_sections.size()) &&\n      resolve_section(static_cast<u32>(m_last_active_rel_section)))\n    return true;\n\n  for (u32 i = 0; i < m_active_rel_sections.size(); ++i)\n  {\n    if (static_cast<int>(i) != m_last_active_rel_section && resolve_section(i))\n      return true;\n  }\n  *runtime_address = linked_address;\n  return true;\n}\n"""

s = replace_once(s, old_resolvers, new_resolvers, "REL resolver fast path")
smc.write_text(s)

print("M11B REL resolver locality fast path and diagnostics applied")
