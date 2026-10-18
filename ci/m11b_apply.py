from pathlib import Path


def replace_once(path: str, old: str, new: str, label: str) -> None:
    p = Path(path)
    s = p.read_text().replace("\r\n", "\n")
    count = s.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one anchor, found {count}")
    p.write_text(s.replace(old, new, 1))


# OpenSPM semantic fast-forward state. The frontend asks for fast-forward;
# this temporary RecompCore adapter translates it to the current speed backend.
settings_h = "ModernGekko/vendor/dolphin/Source/Core/Core/Config/OpenSPMSettings.h"
replace_once(
    settings_h,
    "AspectRatio GetAspectRatio();\nvoid SetAspectRatio(AspectRatio mode);\n}  // namespace OpenSPM\n",
    "AspectRatio GetAspectRatio();\nvoid SetAspectRatio(AspectRatio mode);\n\nbool GetFastForward();\nvoid SetFastForward(bool enabled);\n}  // namespace OpenSPM\n",
    "OpenSPM fast-forward declarations",
)

settings_cpp = "ModernGekko/vendor/dolphin/Source/Core/Core/Config/OpenSPMSettings.cpp"
replace_once(
    settings_cpp,
    '#include "Core/Config/OpenSPMSettings.h"\n\n',
    '#include "Core/Config/OpenSPMSettings.h"\n\n#include <atomic>\n\n',
    "OpenSPM atomic include",
)
replace_once(
    settings_cpp,
    '#include "Core/Config/GraphicsSettings.h"\n',
    '#include "Core/Config/GraphicsSettings.h"\n#include "Core/Config/MainSettings.h"\n',
    "OpenSPM main settings include",
)
replace_once(
    settings_cpp,
    "namespace\n{\nAspectRatio NormalizeAspectRatio(u32 raw)\n",
    "namespace\n{\nstd::atomic_bool s_fast_forward{false};\n\nAspectRatio NormalizeAspectRatio(u32 raw)\n",
    "OpenSPM fast-forward state",
)
replace_once(
    settings_cpp,
    "}  // namespace OpenSPM\n",
    '''bool GetFastForward()\n{\n  return s_fast_forward.load(std::memory_order_relaxed);\n}\n\nvoid SetFastForward(bool enabled)\n{\n  s_fast_forward.store(enabled, std::memory_order_relaxed);\n  Config::SetCurrent(Config::MAIN_EMULATION_SPEED, enabled ? 2.0f : 1.0f);\n}\n\n}  // namespace OpenSPM\n''',
    "OpenSPM fast-forward implementation",
)

# The actual ModernGekko Windows runner uses DolphinNoGUI/PlatformWin32, not DolphinQt.
# Put the OpenSPM controls on the menu that is really instantiated.
win32 = "ModernGekko/vendor/dolphin/Source/Core/DolphinNoGUI/PlatformWin32.cpp"
replace_once(
    win32,
    '#include "Core/Config/MainSettings.h"\n',
    '#include "Core/Config/MainSettings.h"\n#include "Core/Config/OpenSPMSettings.h"\n',
    "Win32 OpenSPM include",
)
replace_once(
    win32,
    '''constexpr UINT ID_FULLSCREEN = 41004;\nconstexpr UINT ID_LOAD_STATE_FIRST = 41100;\nconstexpr UINT ID_LOAD_STATE_LAST = 41199;\n''',
    '''constexpr UINT ID_FULLSCREEN = 41004;\nconstexpr UINT ID_LOAD_STATE_FIRST = 41100;\nconstexpr UINT ID_LOAD_STATE_LAST = 41199;\nconstexpr UINT ID_OPENSPM_ASPECT_AUTO = 41200;\nconstexpr UINT ID_OPENSPM_ASPECT_4_3 = 41201;\nconstexpr UINT ID_OPENSPM_ASPECT_16_9 = 41202;\nconstexpr UINT ID_OPENSPM_ASPECT_21_9 = 41203;\nconstexpr UINT ID_OPENSPM_ASPECT_32_9 = 41204;\nconstexpr UINT ID_OPENSPM_FAST_FORWARD = 41210;\n''',
    "Win32 OpenSPM command ids",
)
replace_once(
    win32,
    '''  void SaveStateToStatesDirectory();\n  void ToggleFullscreen();\n  void UpdateWindowPosition();\n''',
    '''  void SaveStateToStatesDirectory();\n  void ApplyFastForward();\n  void ToggleFullscreen();\n  void UpdateWindowPosition();\n''',
    "Win32 fast-forward method declaration",
)
replace_once(
    win32,
    '''  HMENU m_view_menu{};\n  // Parallel to the Load State menu entries, rebuilt whenever it opens.\n''',
    '''  HMENU m_view_menu{};\n  HMENU m_openspm_menu{};\n  HMENU m_openspm_aspect_menu{};\n  bool m_fast_forward_hold = false;\n  bool m_fast_forward_toggle = false;\n  // Parallel to the Load State menu entries, rebuilt whenever it opens.\n''',
    "Win32 OpenSPM menu state",
)
replace_once(
    win32,
    '''  m_view_menu = CreatePopupMenu();\n  if (!m_menu || !m_file_menu || !m_load_menu || !m_view_menu)\n    return false;\n''',
    '''  m_view_menu = CreatePopupMenu();\n  m_openspm_menu = CreatePopupMenu();\n  m_openspm_aspect_menu = CreatePopupMenu();\n  if (!m_menu || !m_file_menu || !m_load_menu || !m_view_menu || !m_openspm_menu ||\n      !m_openspm_aspect_menu)\n    return false;\n''',
    "Win32 OpenSPM menu creation",
)
replace_once(
    win32,
    '''  AppendMenuW(m_view_menu, MF_STRING, ID_FULLSCREEN, L"&Fullscreen\\tAlt+Enter");\n  AppendMenuW(m_view_menu, MF_STRING, ID_MUTE, L"&Mute Audio");\n\n  AppendMenuW(m_menu, MF_POPUP, reinterpret_cast<UINT_PTR>(m_file_menu), L"&File");\n  AppendMenuW(m_menu, MF_POPUP, reinterpret_cast<UINT_PTR>(m_view_menu), L"&View");\n  return SetMenu(m_hwnd, m_menu) != FALSE;\n''',
    '''  AppendMenuW(m_view_menu, MF_STRING, ID_FULLSCREEN, L"&Fullscreen\\tAlt+Enter");\n  AppendMenuW(m_view_menu, MF_STRING, ID_MUTE, L"&Mute Audio");\n\n  AppendMenuW(m_openspm_aspect_menu, MF_STRING, ID_OPENSPM_ASPECT_AUTO, L"Auto");\n  AppendMenuW(m_openspm_aspect_menu, MF_STRING, ID_OPENSPM_ASPECT_4_3, L"4:3");\n  AppendMenuW(m_openspm_aspect_menu, MF_STRING, ID_OPENSPM_ASPECT_16_9, L"16:9");\n  AppendMenuW(m_openspm_aspect_menu, MF_STRING, ID_OPENSPM_ASPECT_21_9, L"21:9");\n  AppendMenuW(m_openspm_aspect_menu, MF_STRING, ID_OPENSPM_ASPECT_32_9, L"32:9");\n  AppendMenuW(m_openspm_menu, MF_POPUP, reinterpret_cast<UINT_PTR>(m_openspm_aspect_menu),\n              L"&Aspect Ratio");\n  AppendMenuW(m_openspm_menu, MF_SEPARATOR, 0, nullptr);\n  AppendMenuW(m_openspm_menu, MF_STRING, ID_OPENSPM_FAST_FORWARD,\n              L"Fast Forward (Toggle)");\n\n  AppendMenuW(m_menu, MF_POPUP, reinterpret_cast<UINT_PTR>(m_file_menu), L"&File");\n  AppendMenuW(m_menu, MF_POPUP, reinterpret_cast<UINT_PTR>(m_view_menu), L"&View");\n  AppendMenuW(m_menu, MF_POPUP, reinterpret_cast<UINT_PTR>(m_openspm_menu), L"&OpenSPM");\n  return SetMenu(m_hwnd, m_menu) != FALSE;\n''',
    "Win32 OpenSPM menu entries",
)
replace_once(
    win32,
    '''  if (menu == m_view_menu)\n  {\n    CheckMenuItem(m_view_menu, ID_MUTE,\n                  MF_BYCOMMAND |\n                      (Config::Get(Config::MAIN_AUDIO_MUTED) ? MF_CHECKED : MF_UNCHECKED));\n    CheckMenuItem(m_view_menu, ID_FULLSCREEN,\n                  MF_BYCOMMAND | (m_fullscreen ? MF_CHECKED : MF_UNCHECKED));\n    return;\n  }\n\n  if (menu != m_load_menu)\n''',
    '''  if (menu == m_view_menu)\n  {\n    CheckMenuItem(m_view_menu, ID_MUTE,\n                  MF_BYCOMMAND |\n                      (Config::Get(Config::MAIN_AUDIO_MUTED) ? MF_CHECKED : MF_UNCHECKED));\n    CheckMenuItem(m_view_menu, ID_FULLSCREEN,\n                  MF_BYCOMMAND | (m_fullscreen ? MF_CHECKED : MF_UNCHECKED));\n    return;\n  }\n\n  if (menu == m_openspm_menu)\n  {\n    CheckMenuItem(m_openspm_menu, ID_OPENSPM_FAST_FORWARD,\n                  MF_BYCOMMAND | (m_fast_forward_toggle ? MF_CHECKED : MF_UNCHECKED));\n    return;\n  }\n\n  if (menu == m_openspm_aspect_menu)\n  {\n    const UINT selected = ID_OPENSPM_ASPECT_AUTO +\n                          static_cast<UINT>(OpenSPM::GetAspectRatio());\n    CheckMenuRadioItem(m_openspm_aspect_menu, ID_OPENSPM_ASPECT_AUTO,\n                       ID_OPENSPM_ASPECT_32_9, selected, MF_BYCOMMAND);\n    return;\n  }\n\n  if (menu != m_load_menu)\n''',
    "Win32 OpenSPM menu refresh",
)
replace_once(
    win32,
    "void PlatformWin32::ToggleFullscreen()\n{\n",
    '''void PlatformWin32::ApplyFastForward()\n{\n  OpenSPM::SetFastForward(m_fast_forward_toggle || m_fast_forward_hold);\n}\n\nvoid PlatformWin32::ToggleFullscreen()\n{\n''',
    "Win32 fast-forward adapter",
)
replace_once(
    win32,
    '''    else if (wParam == VK_SPACE)\n    {\n      Config::SetCurrent(Config::MAIN_EMULATION_SPEED, FAST_FORWARD_SPEED);\n      return 0;\n    }\n''',
    '''    else if (wParam == VK_SPACE)\n    {\n      platform->m_fast_forward_hold = true;\n      platform->ApplyFastForward();\n      return 0;\n    }\n''',
    "Win32 hold fast-forward keydown",
)
replace_once(
    win32,
    '''  case WM_KEYUP:\n    if (wParam == VK_SPACE)\n    {\n      Config::SetCurrent(Config::MAIN_EMULATION_SPEED, 1.0f);\n      return 0;\n    }\n    break;\n\n  case WM_KILLFOCUS:\n    // Never leave emulation running fast because Space was released while\n    // another window had focus and the key-up went elsewhere.\n    Config::SetCurrent(Config::MAIN_EMULATION_SPEED, 1.0f);\n    break;\n''',
    '''  case WM_KEYUP:\n    if (wParam == VK_SPACE)\n    {\n      platform->m_fast_forward_hold = false;\n      platform->ApplyFastForward();\n      return 0;\n    }\n    break;\n\n  case WM_KILLFOCUS:\n    // Never leave the hold source active because Space was released elsewhere.\n    platform->m_fast_forward_hold = false;\n    platform->ApplyFastForward();\n    break;\n''',
    "Win32 hold fast-forward release",
)
replace_once(
    win32,
    '''    else if (command == ID_FULLSCREEN)\n    {\n      platform->ToggleFullscreen();\n    }\n    else if (command >= ID_LOAD_STATE_FIRST && command <= ID_LOAD_STATE_LAST)\n''',
    '''    else if (command == ID_FULLSCREEN)\n    {\n      platform->ToggleFullscreen();\n    }\n    else if (command >= ID_OPENSPM_ASPECT_AUTO && command <= ID_OPENSPM_ASPECT_32_9)\n    {\n      OpenSPM::SetAspectRatio(static_cast<OpenSPM::AspectRatio>(\n          command - ID_OPENSPM_ASPECT_AUTO));\n    }\n    else if (command == ID_OPENSPM_FAST_FORWARD)\n    {\n      platform->m_fast_forward_toggle = !platform->m_fast_forward_toggle;\n      platform->ApplyFastForward();\n    }\n    else if (command >= ID_LOAD_STATE_FIRST && command <= ID_LOAD_STATE_LAST)\n''',
    "Win32 OpenSPM commands",
)

# Static recomp performance telemetry. Keep this diagnostic-only and local to Run();
# no OpenSPM feature API depends on RecompCore counters.
run_cpp = "ModernGekko/vendor/dolphin/Source/Core/Core/PowerPC/StaticRecomp/StaticRecompCore_Run.cpp"
replace_once(
    run_cpp,
    "#include <cstdio>\n",
    "#include <chrono>\n#include <cstdio>\n",
    "perf chrono include",
)
replace_once(
    run_cpp,
    '''  const bool lockstep_enabled = m_lockstep_verifier->IsEnabled();\n  const auto fast_dispatchable_at = [this](u32 address) {\n''',
    '''  const bool lockstep_enabled = m_lockstep_verifier->IsEnabled();\n\n  using OpenSPMPerfClock = std::chrono::steady_clock;\n  const bool openspm_perf_enabled = std::getenv("OPENSPM_PERF") != nullptr;\n  auto openspm_perf_last = OpenSPMPerfClock::now();\n  u64 openspm_perf_last_native = m_native_dispatches;\n  u64 openspm_perf_last_bursts = m_bursts;\n  u64 openspm_perf_last_fallback_steps = m_fallback_steps;\n  u64 openspm_perf_last_hook_fb = m_hook_fallback_instructions;\n  u64 openspm_perf_last_cycles = m_charged_cycles;\n  u64 openspm_perf_fallback_jit = 0;\n  u64 openspm_perf_last_fallback_jit = 0;\n  u64 openspm_perf_host_calls = 0;\n  u64 openspm_perf_last_host_calls = 0;\n  const auto report_openspm_perf = [&] {\n    if (!openspm_perf_enabled)\n      return;\n    const auto now = OpenSPMPerfClock::now();\n    const double seconds = std::chrono::duration<double>(now - openspm_perf_last).count();\n    if (seconds < 1.0)\n      return;\n    const u64 native = m_native_dispatches - openspm_perf_last_native;\n    const u64 bursts = m_bursts - openspm_perf_last_bursts;\n    std::fprintf(\n        stderr,\n        "[OPENSPM-PERF] dt=%.3f native=%llu bursts=%llu dispatch_per_burst=%.2f "\n        "fallback_jit=%llu fallback_steps=%llu host_calls=%llu hook_fb=%llu cycles=%llu\\n",\n        seconds, static_cast<unsigned long long>(native), static_cast<unsigned long long>(bursts),\n        bursts ? static_cast<double>(native) / static_cast<double>(bursts) : 0.0,\n        static_cast<unsigned long long>(openspm_perf_fallback_jit -\n                                        openspm_perf_last_fallback_jit),\n        static_cast<unsigned long long>(m_fallback_steps - openspm_perf_last_fallback_steps),\n        static_cast<unsigned long long>(openspm_perf_host_calls - openspm_perf_last_host_calls),\n        static_cast<unsigned long long>(m_hook_fallback_instructions - openspm_perf_last_hook_fb),\n        static_cast<unsigned long long>(m_charged_cycles - openspm_perf_last_cycles));\n    std::fflush(stderr);\n    openspm_perf_last = now;\n    openspm_perf_last_native = m_native_dispatches;\n    openspm_perf_last_bursts = m_bursts;\n    openspm_perf_last_fallback_steps = m_fallback_steps;\n    openspm_perf_last_hook_fb = m_hook_fallback_instructions;\n    openspm_perf_last_cycles = m_charged_cycles;\n    openspm_perf_last_fallback_jit = openspm_perf_fallback_jit;\n    openspm_perf_last_host_calls = openspm_perf_host_calls;\n  };\n\n  const auto fast_dispatchable_at = [this](u32 address) {\n''',
    "perf setup",
)
replace_once(
    run_cpp,
    '''  while (*state_ptr == CPU::State::Running)\n  {\n    core_timing.Advance();\n''',
    '''  while (*state_ptr == CPU::State::Running)\n  {\n    core_timing.Advance();\n    report_openspm_perf();\n''',
    "perf periodic report",
)
replace_once(
    run_cpp,
    '''        if (m_guest.host_call && IsHostCallAddress(ppc.pc))\n        {\n          SyncIn();\n''',
    '''        if (m_guest.host_call && IsHostCallAddress(ppc.pc))\n        {\n          ++openspm_perf_host_calls;\n          SyncIn();\n''',
    "perf host-call counter",
)
replace_once(
    run_cpp,
    '''        else if (m_fallback_jit)\n        {\n          m_fallback_jit->Run();\n        }\n''',
    '''        else if (m_fallback_jit)\n        {\n          ++openspm_perf_fallback_jit;\n          m_fallback_jit->Run();\n        }\n''',
    "perf fallback-JIT counter",
)

print("M11B OpenSPM Win32 menu + portable fast-forward + perf telemetry applied")
