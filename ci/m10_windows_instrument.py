from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    # Git for Windows may materialize the fetched upstream source as CRLF.
    # Normalize before matching literal source anchors.
    text = text.replace("\r\n", "\n")
    if old not in text:
        raise RuntimeError(f"missing instrumentation anchor: {label}")
    return text.replace(old, new, 1)


# ModernGekko runtime breadcrumbs: boot result, core state changes, and whether
# the Win32 platform loop itself returns.
p = Path("ModernGekko/src/runtime/dolphin_runtime.cpp")
s = p.read_text()
if "#include <iostream>\n" not in s:
    s = replace_once(s, "#include <fstream>\n", "#include <fstream>\n#include <iostream>\n", "iostream include")
old = """  m_impl->state_hook =
      Core::AddOnStateChangedCallback([this](Core::State state) {
        if (state == Core::State::Uninitialized && m_impl->platform)
          m_impl->platform->Stop();
      });
  if (!BootManager::BootCore(Core::System::GetInstance(), std::move(boot),
                             m_impl->platform->GetWindowSystemInfo())) {"""
new = """  std::cerr << "[M10DIAG] installing core-state hook\\n" << std::flush;
  m_impl->state_hook =
      Core::AddOnStateChangedCallback([this](Core::State state) {
        std::cerr << "[M10DIAG] core-state=" << static_cast<int>(state)
                  << "\\n" << std::flush;
        if (state == Core::State::Uninitialized && m_impl->platform) {
          std::cerr << "[M10DIAG] stopping platform: core uninitialized\\n"
                    << std::flush;
          m_impl->platform->Stop();
        }
      });
  std::cerr << "[M10DIAG] calling BootCore\\n" << std::flush;
  if (!BootManager::BootCore(Core::System::GetInstance(), std::move(boot),
                             m_impl->platform->GetWindowSystemInfo())) {"""
s = replace_once(s, old, new, "ModernGekko state hook / BootCore")

old = """    m_impl->running = false;
    return {RuntimeExitReason::BootFailed,"""
new = """    std::cerr << "[M10DIAG] BootCore returned false\\n" << std::flush;
    m_impl->running = false;
    return {RuntimeExitReason::BootFailed,"""
s = replace_once(s, old, new, "BootCore failure")

s = replace_once(
    s,
    "  m_impl->booted = true;",
    '  std::cerr << "[M10DIAG] BootCore accepted; booted flag set\\n" << std::flush;\n  m_impl->booted = true;',
    "runtime booted flag",
)

s = replace_once(
    s,
    "  m_impl->platform->MainLoop();",
    '  std::cerr << "[M10DIAG] entering platform MainLoop\\n" << std::flush;\n'
    "  m_impl->platform->MainLoop();\n"
    '  std::cerr << "[M10DIAG] platform MainLoop returned\\n" << std::flush;',
    "platform MainLoop",
)
p.write_text(s)


# RecompCore breadcrumbs inside EmuThread. stderr is flushed after every line
# so even an immediate early return leaves a useful last-known checkpoint.
p = Path("ModernGekko/vendor/dolphin/Source/Core/Core/Core.cpp")
s = p.read_text()
if "#include <cstdio>\n" not in s:
    s = replace_once(s, "#include <atomic>\n", "#include <atomic>\n#include <cstdio>\n", "cstdio include")

pairs = [
    (
        """static void EmuThread(Core::System& system, std::unique_ptr<BootParameters> boot,
                      WindowSystemInfo wsi)
{
  NotifyStateChanged(State::Starting);""",
        """static void EmuThread(Core::System& system, std::unique_ptr<BootParameters> boot,
                      WindowSystemInfo wsi)
{
  std::fprintf(stderr, "[M10DIAG] EmuThread entered\\n"); std::fflush(stderr);
  NotifyStateChanged(State::Starting);""",
        "EmuThread entry",
    ),
    (
        """  HW::Init(system,
           NetPlay::IsNetPlayRunning() ? &(boot_session_data.GetNetplaySettings()->sram) : nullptr);""",
        """  std::fprintf(stderr, "[M10DIAG] before HW::Init\\n"); std::fflush(stderr);
  HW::Init(system,
           NetPlay::IsNetPlayRunning() ? &(boot_session_data.GetNetplaySettings()->sram) : nullptr);
  std::fprintf(stderr, "[M10DIAG] after HW::Init\\n"); std::fflush(stderr);""",
        "HW init",
    ),
    (
        "  const auto video_guard = GetInitializedVideoGuard(system, wsi);",
        """  std::fprintf(stderr, "[M10DIAG] before video init\\n"); std::fflush(stderr);
  const auto video_guard = GetInitializedVideoGuard(system, wsi);
  std::fprintf(stderr, "[M10DIAG] after video init guard=%d\\n", video_guard ? 1 : 0); std::fflush(stderr);""",
        "video init",
    ),
    (
        """  if (!system.GetDSP().GetDSPEmulator()->Initialize(system.IsWii(),
                                                    Config::Get(Config::MAIN_DSP_THREAD)))""",
        """  std::fprintf(stderr, "[M10DIAG] before DSP Initialize\\n"); std::fflush(stderr);
  if (!system.GetDSP().GetDSPEmulator()->Initialize(system.IsWii(),
                                                    Config::Get(Config::MAIN_DSP_THREAD)))""",
        "DSP init",
    ),
    (
        "  AudioCommon::PostInitSoundStream(system);",
        """  std::fprintf(stderr, "[M10DIAG] DSP Initialize succeeded\\n"); std::fflush(stderr);
  AudioCommon::PostInitSoundStream(system);
  std::fprintf(stderr, "[M10DIAG] after audio PostInit\\n"); std::fflush(stderr);""",
        "audio post init",
    ),
    (
        """    if (!CBoot::BootUp(system, guard, std::move(boot)))
      return;""",
        """    std::fprintf(stderr, "[M10DIAG] before CBoot::BootUp\\n"); std::fflush(stderr);
    if (!CBoot::BootUp(system, guard, std::move(boot))) {
      std::fprintf(stderr, "[M10DIAG] CBoot::BootUp returned false\\n"); std::fflush(stderr);
      return;
    }
    std::fprintf(stderr, "[M10DIAG] CBoot::BootUp succeeded\\n"); std::fflush(stderr);""",
        "CBoot BootUp",
    ),
    (
        "  system.GetFifo().Prepare();",
        """  std::fprintf(stderr, "[M10DIAG] before FIFO Prepare\\n"); std::fflush(stderr);
  system.GetFifo().Prepare();
  std::fprintf(stderr, "[M10DIAG] after FIFO Prepare\\n"); std::fflush(stderr);""",
        "FIFO prepare",
    ),
    (
        """  // Become the CPU thread.
  cpu_thread_func(system, savestate_path, delete_savestate);""",
        """  // Become the CPU thread.
  std::fprintf(stderr, "[M10DIAG] entering CPU thread function\\n"); std::fflush(stderr);
  cpu_thread_func(system, savestate_path, delete_savestate);
  std::fprintf(stderr, "[M10DIAG] CPU thread function returned\\n"); std::fflush(stderr);""",
        "CPU thread entry/return",
    ),
]

for old, new, label in pairs:
    s = replace_once(s, old, new, label)
p.write_text(s)

print("M10 Windows diagnostic instrumentation applied")
