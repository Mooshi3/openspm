from pathlib import Path


def replace_once(path: str, old: str, new: str, label: str) -> None:
    p = Path(path)
    text = p.read_text().replace("\r\n", "\n")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one anchor, found {count}")
    p.write_text(text.replace(old, new, 1))


header = "ModernGekko/include/moderngekko/runtime.hpp"
replace_once(
    header,
    "#include <filesystem>\n",
    "#include <cstdint>\n#include <filesystem>\n",
    "runtime.hpp cstdint include",
)
replace_once(
    header,
    """struct AutomationSettings
{
  std::filesystem::path directory;
};
""",
    """struct AutomationSettings
{
  std::filesystem::path directory;
};

// OpenSPM's transitional backend consumes these neutral observations instead
// of including Dolphin/Core/Video headers directly. This patch lives only in
// the pinned compatibility chassis; it is not part of OpenSPM's public ABI.
struct RuntimePerformanceSnapshot
{
  std::uint64_t frame_count = 0;
  std::uint64_t present_count = 0;
  double fps = 0.0;
  double vps = 0.0;
  double speed = 0.0;
  bool running = false;
  bool paused = false;
};

struct RuntimeObserver
{
  void* context = nullptr;
  void (*on_core_running)(void* context) = nullptr;
  void (*on_present)(void* context, const RuntimePerformanceSnapshot* snapshot) = nullptr;
  void (*on_core_stopped)(void* context) = nullptr;
};
""",
    "neutral runtime observer declarations",
)
replace_once(
    header,
    """  AutomationSettings automation;
};
""",
    """  AutomationSettings automation;
  RuntimeObserver observer;
};
""",
    "runtime config observer",
)
replace_once(
    header,
    """  std::optional<RuntimeError> Pause();
  std::optional<RuntimeError> Resume();

  const RuntimeConfig& GetConfig() const;
""",
    """  std::optional<RuntimeError> Pause();
  std::optional<RuntimeError> Resume();
  RuntimePerformanceSnapshot GetPerformanceSnapshot() const;

  const RuntimeConfig& GetConfig() const;
""",
    "runtime performance snapshot method",
)

runtime = "ModernGekko/src/runtime/dolphin_runtime.cpp"
replace_once(
    runtime,
    """  m_impl->booted = true;
  m_impl->present_hook =
""",
    """  m_impl->booted = true;
  if (m_impl->config.observer.on_core_running)
    m_impl->config.observer.on_core_running(m_impl->config.observer.context);
  m_impl->present_hook =
""",
    "core-running observer",
)
replace_once(
    runtime,
    """        m_impl->automation_state.present_count.store(info.present_count,
                                                     std::memory_order_relaxed);
      });
""",
    """        m_impl->automation_state.present_count.store(info.present_count,
                                                     std::memory_order_relaxed);
        if (m_impl->config.observer.on_present)
        {
          const RuntimePerformanceSnapshot snapshot = GetPerformanceSnapshot();
          m_impl->config.observer.on_present(m_impl->config.observer.context, &snapshot);
        }
      });
""",
    "present observer",
)
replace_once(
    runtime,
    """  Core::Shutdown(Core::System::GetInstance());
  m_impl->booted = false;
  m_impl->running = false;
  return {};
}
""",
    """  Core::Shutdown(Core::System::GetInstance());
  m_impl->booted = false;
  m_impl->running = false;
  if (m_impl->config.observer.on_core_stopped)
    m_impl->config.observer.on_core_stopped(m_impl->config.observer.context);
  return {};
}
""",
    "core-stopped observer",
)
replace_once(
    runtime,
    """const RuntimeConfig &Runtime::GetConfig() const { return m_impl->config; }
""",
    """RuntimePerformanceSnapshot Runtime::GetPerformanceSnapshot() const
{
  RuntimePerformanceSnapshot snapshot;
  snapshot.frame_count =
      m_impl->automation_state.frame_count.load(std::memory_order_relaxed);
  snapshot.present_count =
      m_impl->automation_state.present_count.load(std::memory_order_relaxed);
  snapshot.running = m_impl->running.load(std::memory_order_relaxed);
  if (m_impl->booted)
  {
    const auto state = Core::GetState(Core::System::GetInstance());
    snapshot.paused = state == Core::State::Paused;
    const auto& perf = Core::System::GetInstance().GetPerfMetrics();
    snapshot.fps = perf.GetFPS();
    snapshot.vps = perf.GetVPS();
    snapshot.speed = perf.GetSpeed();
  }
  return snapshot;
}

const RuntimeConfig &Runtime::GetConfig() const { return m_impl->config; }
""",
    "runtime performance snapshot implementation",
)

print("M13 phase5: neutral runtime observer/performance seam staged")
