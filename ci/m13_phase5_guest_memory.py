from pathlib import Path


def replace_once(path: str, old: str, new: str, label: str) -> None:
    p = Path(path)
    text = p.read_text().replace("\r\n", "\n")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one anchor, found {count}")
    p.write_text(text.replace(old, new, 1))


# Transitional, backend-private bridge only. These methods are intentionally
# named for their threading contract: the caller MUST already be executing in
# ModernGekko's emulated CPU thread (our true tick observer). They do not take a
# CPUThreadGuard, because attempting to acquire one from that callback can
# deadlock. They are not exposed through any OpenSPM public service.
header = "ModernGekko/include/moderngekko/runtime.hpp"
replace_once(
    header,
    "#include <cstdint>\n",
    "#include <cstddef>\n#include <cstdint>\n",
    "runtime guest-memory size include",
)
replace_once(
    header,
    """  RuntimePerformanceSnapshot GetPerformanceSnapshot() const;

  const RuntimeConfig& GetConfig() const;
""",
    """  RuntimePerformanceSnapshot GetPerformanceSnapshot() const;

  // Compatibility-chassis methods. Valid only while a RuntimeObserver guest
  // tick callback is executing on the emulated CPU thread. OpenSPM must wrap
  // these in semantic R8PE01 adapter operations rather than publish addresses.
  bool CpuThreadReadGuestMemory(std::uint32_t address, void* out, std::size_t size) const;
  bool CpuThreadWriteGuestMemory(std::uint32_t address, const void* data, std::size_t size);

  const RuntimeConfig& GetConfig() const;
""",
    "runtime CPU-thread guest-memory methods",
)

runtime = "ModernGekko/src/runtime/dolphin_runtime.cpp"
replace_once(
    runtime,
    "#include <cstddef>\n",
    "#include <cstddef>\n#include <cstring>\n",
    "runtime guest-memory memcpy include",
)
replace_once(
    runtime,
    """const RuntimeConfig &Runtime::GetConfig() const { return m_impl->config; }
""",
    """bool Runtime::CpuThreadReadGuestMemory(std::uint32_t address, void* out,
                                             std::size_t size) const
{
  if (!out && size != 0)
    return false;
  if (size == 0)
    return true;
  auto& memory = Core::System::GetInstance().GetMemory();
  const u8* source = memory.GetPointerForRange(address, size);
  if (!source)
    return false;
  std::memcpy(out, source, size);
  return true;
}

bool Runtime::CpuThreadWriteGuestMemory(std::uint32_t address, const void* data,
                                        std::size_t size)
{
  if (!data && size != 0)
    return false;
  if (size == 0)
    return true;
  auto& memory = Core::System::GetInstance().GetMemory();
  if (!memory.GetPointerForRange(address, size))
    return false;
  memory.CopyToEmu(address, static_cast<const u8*>(data), size);
  return true;
}

const RuntimeConfig &Runtime::GetConfig() const { return m_impl->config; }
""",
    "runtime CPU-thread guest-memory implementation",
)

print("M13 phase5: private CPU-thread guest memory bridge staged")
