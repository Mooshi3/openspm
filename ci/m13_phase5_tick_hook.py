from pathlib import Path


def replace_once(path: str, old: str, new: str, label: str) -> None:
    p = Path(path)
    text = p.read_text().replace("\r\n", "\n")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one anchor, found {count}")
    p.write_text(text.replace(old, new, 1))


# Phase5 runtime-services patch runs before this script. Extend that neutral
# observer with a pair of guest-dispatch boundaries. These remain compatibility
# chassis details; OpenSPM's public API only receives semantic tick events.
header = "ModernGekko/include/moderngekko/runtime.hpp"
replace_once(
    header,
    """struct RuntimeObserver
{
  void* context = nullptr;
  void (*on_core_running)(void* context) = nullptr;
  void (*on_present)(void* context, const RuntimePerformanceSnapshot* snapshot) = nullptr;
  void (*on_core_stopped)(void* context) = nullptr;
};
""",
    """struct RuntimeObserver
{
  void* context = nullptr;
  void (*on_core_running)(void* context) = nullptr;
  void (*on_present)(void* context, const RuntimePerformanceSnapshot* snapshot) = nullptr;
  void (*on_core_stopped)(void* context) = nullptr;
  void (*on_guest_tick_begin)(void* context) = nullptr;
  void (*on_guest_tick_end)(void* context) = nullptr;
};
""",
    "runtime observer guest tick callbacks",
)
replace_once(
    header,
    """  AutomationSettings automation;
  RuntimeObserver observer;
};
""",
    """  AutomationSettings automation;
  RuntimeObserver observer;
  std::uint32_t guest_tick_begin_pc = 0;
  std::uint32_t guest_tick_end_pc = 0;
};
""",
    "runtime config guest tick addresses",
)

# RecompCore owns the CPU-thread dispatcher. Install an intentionally tiny,
# address-filtered callback seam there so tick callbacks run on the emulated
# CPU thread at the real game boundary rather than on the presentation thread.
core_header = "ModernGekko/vendor/dolphin/Source/Core/Core/PowerPC/StaticRecomp/StaticRecompCore.h"
replace_once(
    core_header,
    """extern StaticRecompCore* g_static_recomp_core;
u32 StaticRecompShouldYieldAt(u32 address);
""",
    """using StaticRecompDispatchHook = void (*)(void* context);
void StaticRecompConfigureDispatchHooks(u32 begin_address, u32 end_address, void* context,
                                        StaticRecompDispatchHook begin_hook,
                                        StaticRecompDispatchHook end_hook);
void StaticRecompClearDispatchHooks();

extern StaticRecompCore* g_static_recomp_core;
u32 StaticRecompShouldYieldAt(u32 address);
""",
    "static recomp dispatch hook declarations",
)

core_run = "ModernGekko/vendor/dolphin/Source/Core/Core/PowerPC/StaticRecomp/StaticRecompCore_Run.cpp"
replace_once(
    core_run,
    """FilePtr OpenDispatchTrace()
{
  const char* path = std::getenv(\"STATICRECOMP_TRACE_FILE\");
  if (!path || !*path)
    return {};

  FilePtr file(std::fopen(path, \"w\"));
  if (file)
  {
    std::fprintf(file.get(), \"dispatch,pc,lr,ctr,cr,timebase,ppc_downcount\\n\");
    std::fflush(file.get());
  }
  return file;
}
}

void StaticRecompCore::Run()
""",
    """FilePtr OpenDispatchTrace()
{
  const char* path = std::getenv(\"STATICRECOMP_TRACE_FILE\");
  if (!path || !*path)
    return {};

  FilePtr file(std::fopen(path, \"w\"));
  if (file)
  {
    std::fprintf(file.get(), \"dispatch,pc,lr,ctr,cr,timebase,ppc_downcount\\n\");
    std::fflush(file.get());
  }
  return file;
}

struct DispatchHooks
{
  u32 begin_address = 0;
  u32 end_address = 0;
  void* context = nullptr;
  StaticRecompDispatchHook begin_hook = nullptr;
  StaticRecompDispatchHook end_hook = nullptr;
};
DispatchHooks s_dispatch_hooks;
}

void StaticRecompConfigureDispatchHooks(u32 begin_address, u32 end_address, void* context,
                                        StaticRecompDispatchHook begin_hook,
                                        StaticRecompDispatchHook end_hook)
{
  s_dispatch_hooks.begin_address = begin_address;
  s_dispatch_hooks.end_address = end_address;
  s_dispatch_hooks.context = context;
  s_dispatch_hooks.begin_hook = begin_hook;
  s_dispatch_hooks.end_hook = end_hook;
}

void StaticRecompClearDispatchHooks()
{
  s_dispatch_hooks = {};
}

void StaticRecompCore::Run()
""",
    "static recomp dispatch hook storage",
)
replace_once(
    core_run,
    """          const u32 runtime_dispatch_address = m_guest.pc;
          u32 linked_dispatch_address = runtime_dispatch_address;
""",
    """          const u32 runtime_dispatch_address = m_guest.pc;
          if (runtime_dispatch_address == s_dispatch_hooks.begin_address &&
              s_dispatch_hooks.begin_hook)
            s_dispatch_hooks.begin_hook(s_dispatch_hooks.context);
          if (runtime_dispatch_address == s_dispatch_hooks.end_address &&
              s_dispatch_hooks.end_hook)
            s_dispatch_hooks.end_hook(s_dispatch_hooks.context);
          u32 linked_dispatch_address = runtime_dispatch_address;
""",
    "static recomp dispatch hook invocation",
)

runtime = "ModernGekko/src/runtime/dolphin_runtime.cpp"
replace_once(
    runtime,
    """#include \"Core/PowerPC/StaticRecomp/StaticRecompModuleSource.h\"
""",
    """#include \"Core/PowerPC/StaticRecomp/StaticRecompCore.h\"
#include \"Core/PowerPC/StaticRecomp/StaticRecompModuleSource.h\"
""",
    "dolphin runtime static recomp core include",
)
# Anchor only on the unique BootCore call line. Several earlier M10/M11
# instrumentation patches may adjust continuation-line whitespace, and tick
# configuration should not depend on that formatting.
replace_once(
    runtime,
    """  if (!BootManager::BootCore(Core::System::GetInstance(), std::move(boot),
""",
    """  StaticRecompConfigureDispatchHooks(
      m_impl->config.guest_tick_begin_pc, m_impl->config.guest_tick_end_pc,
      m_impl->config.observer.context, m_impl->config.observer.on_guest_tick_begin,
      m_impl->config.observer.on_guest_tick_end);
  if (!BootManager::BootCore(Core::System::GetInstance(), std::move(boot),
""",
    "configure guest tick dispatch hooks before boot",
)
replace_once(
    runtime,
    """    m_impl->running = false;
    return {RuntimeExitReason::BootFailed,
            RuntimeError{RuntimeErrorCode::BootFailed,
                         \"Dolphin could not boot sys/main.dol\"}};
""",
    """    StaticRecompClearDispatchHooks();
    m_impl->running = false;
    return {RuntimeExitReason::BootFailed,
            RuntimeError{RuntimeErrorCode::BootFailed,
                         \"Dolphin could not boot sys/main.dol\"}};
""",
    "clear guest tick dispatch hooks on boot failure",
)
replace_once(
    runtime,
    """  Core::Shutdown(Core::System::GetInstance());
  m_impl->booted = false;
  m_impl->running = false;
  if (m_impl->config.observer.on_core_stopped)
""",
    """  Core::Shutdown(Core::System::GetInstance());
  StaticRecompClearDispatchHooks();
  m_impl->booted = false;
  m_impl->running = false;
  if (m_impl->config.observer.on_core_stopped)
""",
    "clear guest tick dispatch hooks after shutdown",
)

print("M13 phase5: true CPU-thread guest tick seam staged")
