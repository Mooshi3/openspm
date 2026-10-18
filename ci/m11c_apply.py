from pathlib import Path


def replace_once(path: str, old: str, new: str, label: str) -> None:
    p = Path(path)
    s = p.read_text().replace("\r\n", "\n")
    count = s.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one anchor, found {count}")
    p.write_text(s.replace(old, new, 1))


# ---------------------------------------------------------------------------
# 1. Do not install the guest-mod interception callback when there is nothing
#    to intercept. M11B recorded host_calls=0 for the entire 24-minute run, so
#    making every native dispatch query two empty hash tables is pure overhead.
# ---------------------------------------------------------------------------
runtime_cpp = "ModernGekko/src/runtime/dolphin_runtime.cpp"
replace_once(
    runtime_cpp,
    '''  recomp_source.host_call = &ModManager::HostCall;\n  recomp_source.host_call_contains = &ModManager::HostCallContains;\n  recomp_source.host_call_range_contains =\n      &ModManager::HostCallRangeContains;\n  recomp_source.host_call_user = impl->mods.get();\n  jit.SetStaticRecompModuleSource(std::move(recomp_source));\n''',
    '''  const bool guest_interception_active =\n      impl->mods && impl->mods->HasGuestInterception();\n  if (guest_interception_active)\n  {\n    recomp_source.host_call = &ModManager::HostCall;\n    recomp_source.host_call_contains = &ModManager::HostCallContains;\n    recomp_source.host_call_range_contains = &ModManager::HostCallRangeContains;\n    recomp_source.host_call_user = impl->mods.get();\n  }\n  std::fprintf(stderr, "[OPENSPM-RUNTIME] guest_mod_interception=%s\\n",\n               guest_interception_active ? "on" : "off");\n  std::fflush(stderr);\n  jit.SetStaticRecompModuleSource(std::move(recomp_source));\n''',
    "disable dormant guest-mod interception",
)


# ---------------------------------------------------------------------------
# 2. Aspect selection is made on the Win32 UI thread. The previous adapter
#    queued a RunOnCPUThread callback, but StaticRecompCore::Run owns the CPU
#    thread for the lifetime of play. Queue the semantic request atomically and
#    let the hot loop apply it on the CPU thread instead.
# ---------------------------------------------------------------------------
settings_cpp = "ModernGekko/vendor/dolphin/Source/Core/Core/Config/OpenSPMSettings.cpp"
replace_once(
    settings_cpp,
    '''  auto& system = Core::System::GetInstance();\n  if (!Core::IsUninitialized(system))\n  {\n    Core::RunOnCPUThread(system, [mode] {\n      if (g_static_recomp_core)\n        g_static_recomp_core->SetOpenSPMAspectRatio(static_cast<u32>(mode));\n    });\n  }\n''',
    '''  // RecompCore owns the emulated CPU thread while running. Queue only a\n  // backend request here; StaticRecompCore applies it from its CPU loop.\n  if (g_static_recomp_core)\n    g_static_recomp_core->SetOpenSPMAspectRatio(static_cast<u32>(mode));\n''',
    "queue aspect request without RunOnCPUThread",
)

core_h = "ModernGekko/vendor/dolphin/Source/Core/Core/PowerPC/StaticRecomp/StaticRecompCore.h"
replace_once(
    core_h,
    '''#include <string>\n''',
    '''#include <atomic>\n#include <string>\n''',
    "StaticRecomp atomic include",
)
replace_once(
    core_h,
    '''  void SetOpenSPMAspectRatio(u32 mode);\n  bool DispatchableAt(u32 address);\n''',
    '''  void SetOpenSPMAspectRatio(u32 mode);\n  bool DispatchableAt(u32 address);\n''',
    "confirm OpenSPM public setter",
)
replace_once(
    core_h,
    '''  bool ResolveNativeAddress(u32 runtime_address, u32* linked_address, u32* rel_section_index);\n''',
    '''  void ApplyOpenSPMAspectRatio();\n  bool ResolveNativeAddress(u32 runtime_address, u32* linked_address, u32* rel_section_index);\n''',
    "declare CPU-thread aspect apply",
)
replace_once(
    core_h,
    '''  u64 m_bursts = 0;          // SyncIn..SyncOut native runs (diagnostic)\n  u64 m_charged_cycles = 0;  // cycles flushed from module charges (diagnostic)\n''',
    '''  u64 m_bursts = 0;          // SyncIn..SyncOut native runs (diagnostic)\n  u64 m_charged_cycles = 0;  // cycles flushed from module charges (diagnostic)\n\n  std::atomic<u32> m_openspm_aspect_requested{0};\n  u32 m_openspm_aspect_applied = 0xffffffffu;\n''',
    "add aspect request state",
)

core_cpp = "ModernGekko/vendor/dolphin/Source/Core/Core/PowerPC/StaticRecomp/StaticRecompCore.cpp"
replace_once(
    core_cpp,
    '''void StaticRecompCore::SetOpenSPMAspectRatio(u32 mode)\n{\n  using SetAspectRatioFn = void (*)(u32);\n  SetAspectRatioFn setter = nullptr;\n  if (m_library.IsOpen() && m_library.GetSymbol("openspm_set_aspect_ratio", &setter))\n    setter(mode);\n}\n''',
    '''void StaticRecompCore::SetOpenSPMAspectRatio(u32 mode)\n{\n  m_openspm_aspect_requested.store(mode, std::memory_order_relaxed);\n}\n\nvoid StaticRecompCore::ApplyOpenSPMAspectRatio()\n{\n  const u32 mode = m_openspm_aspect_requested.load(std::memory_order_relaxed);\n  if (mode == m_openspm_aspect_applied)\n    return;\n\n  using SetAspectRatioFn = void (*)(u32);\n  SetAspectRatioFn setter = nullptr;\n  if (m_library.IsOpen() && m_library.GetSymbol("openspm_set_aspect_ratio", &setter))\n  {\n    setter(mode);\n    m_openspm_aspect_applied = mode;\n    std::fprintf(stderr, "[OPENSPM-ASPECT] module_mode=%u applied\\n", mode);\n  }\n  else\n  {\n    std::fprintf(stderr, "[OPENSPM-ASPECT] module_mode=%u setter_missing\\n", mode);\n  }\n  std::fflush(stderr);\n}\n''',
    "make aspect setter an atomic request",
)


# ---------------------------------------------------------------------------
# 3. Hot dispatch fast path. SPM has a translated REL, so M11B forced every
#    block through generic REL-aware address resolution. Most execution is DOL
#    code whose lookup-table entry already proves it is non-REL. Resolve those
#    addresses in O(1) locally and only enter the generic resolver for actual
#    REL/unmapped addresses.
# ---------------------------------------------------------------------------
run_cpp = "ModernGekko/vendor/dolphin/Source/Core/Core/PowerPC/StaticRecomp/StaticRecompCore_Run.cpp"
replace_once(
    run_cpp,
    '''  const auto fast_dispatchable_at = [this](u32 address) {\n    if (m_has_rel_modules || !m_forced_fallback_ranges.empty())\n      return FastDispatchableAt(address);\n    if (!m_module_active || m_chunk_lookup_table.empty())\n      return false;\n\n    int lookup_index = -1;\n    if (address >= 0x80000000u && address < 0x80000000u + m_lookup_ram_size)\n    {\n      lookup_index = static_cast<int>((address - 0x80000000u) >> 2);\n    }\n    else if (address >= 0x90000000u && address < 0x90000000u + m_lookup_exram_size)\n    {\n      lookup_index = static_cast<int>((m_lookup_ram_size >> 2) + ((address - 0x90000000u) >> 2));\n    }\n    if (lookup_index < 0 || lookup_index >= static_cast<int>(m_chunk_lookup_table.size()))\n      return false;\n    const int chunk = m_chunk_lookup_table[lookup_index];\n    return chunk >= 0 && m_chunk_state[chunk] == CHUNK_VERIFIED;\n  };\n''',
    '''  const auto direct_non_rel_chunk_at = [this](u32 address) {\n    if (!m_module_active || m_chunk_lookup_table.empty())\n      return -1;\n\n    int lookup_index = -1;\n    if (address >= 0x80000000u && address < 0x80000000u + m_lookup_ram_size)\n      lookup_index = static_cast<int>((address - 0x80000000u) >> 2);\n    else if (address >= 0x90000000u && address < 0x90000000u + m_lookup_exram_size)\n      lookup_index = static_cast<int>((m_lookup_ram_size >> 2) +\n                                      ((address - 0x90000000u) >> 2));\n\n    if (lookup_index < 0 || lookup_index >= static_cast<int>(m_chunk_lookup_table.size()))\n      return -1;\n    const int chunk = m_chunk_lookup_table[lookup_index];\n    if (chunk < 0 || chunk >= static_cast<int>(m_chunk_rel_sections.size()) ||\n        m_chunk_rel_sections[chunk] >= 0)\n      return -1;\n    return chunk;\n  };\n\n  const auto fast_dispatchable_at = [this, &direct_non_rel_chunk_at](u32 address) {\n    if (!m_forced_fallback_ranges.empty() && IsForcedFallbackAddress(address))\n      return false;\n    const int direct_chunk = direct_non_rel_chunk_at(address);\n    if (direct_chunk >= 0)\n      return m_chunk_state[direct_chunk] == CHUNK_VERIFIED;\n    return FastDispatchableAt(address);\n  };\n\n  if (m_has_rel_modules)\n  {\n    std::fprintf(stderr, "[OPENSPM-FASTPATH] direct DOL dispatch enabled\\n");\n    std::fflush(stderr);\n  }\n''',
    "install direct DOL dispatch lookup",
)
replace_once(
    run_cpp,
    '''          const u32 runtime_dispatch_address = m_guest.pc;\n          u32 linked_dispatch_address = runtime_dispatch_address;\n          if (m_has_rel_modules)\n            ResolveNativeAddress(runtime_dispatch_address, &linked_dispatch_address, nullptr);\n          m_guest.pc = linked_dispatch_address;\n          m_module->dispatch(&m_guest, linked_dispatch_address);\n          if (m_has_rel_modules)\n            m_guest.pc = TranslateRelAddress(m_guest.pc);\n          ++m_native_dispatches;\n''',
    '''          const u32 runtime_dispatch_address = m_guest.pc;\n          u32 linked_dispatch_address = runtime_dispatch_address;\n          if (m_has_rel_modules && direct_non_rel_chunk_at(runtime_dispatch_address) < 0)\n            ResolveNativeAddress(runtime_dispatch_address, &linked_dispatch_address, nullptr);\n          m_guest.pc = linked_dispatch_address;\n          m_module->dispatch(&m_guest, linked_dispatch_address);\n          if (m_has_rel_modules && direct_non_rel_chunk_at(m_guest.pc) < 0)\n            m_guest.pc = TranslateRelAddress(m_guest.pc);\n          ++m_native_dispatches;\n''',
    "bypass generic REL translation for DOL blocks",
)
replace_once(
    run_cpp,
    '''  while (*state_ptr == CPU::State::Running)\n  {\n    core_timing.Advance();\n    report_openspm_perf();\n''',
    '''  while (*state_ptr == CPU::State::Running)\n  {\n    ApplyOpenSPMAspectRatio();\n    core_timing.Advance();\n    report_openspm_perf();\n''',
    "apply aspect request from CPU loop",
)

print("M11C runtime fastpaths + aspect handshake applied")
