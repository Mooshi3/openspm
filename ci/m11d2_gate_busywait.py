from pathlib import Path


def replace_once(path: str, old: str, new: str, label: str) -> None:
    p = Path(path)
    s = p.read_text().replace("\r\n", "\n")
    count = s.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one anchor, found {count}")
    p.write_text(s.replace(old, new, 1))


run_cpp = "ModernGekko/vendor/dolphin/Source/Core/Core/PowerPC/StaticRecomp/StaticRecompCore_Run.cpp"
core_cpp = "ModernGekko/vendor/dolphin/Source/Core/Core/PowerPC/StaticRecomp/StaticRecompCore.cpp"

# OPENSPM_BUSYWAIT=0 must add essentially zero work to the dispatcher.  M11D
# still entered IsBusyWaitLoop() on every self-returning dispatch and then did
# getenv() inside that helper, which was catastrophic in scheduler-heavy scenes.
replace_once(
    run_cpp,
    '''  u64 openspm_perf_last_cycles = m_charged_cycles;\n''',
    '''  const char* openspm_busywait_env = std::getenv("OPENSPM_BUSYWAIT");\n  const bool openspm_busywait_enabled =\n      openspm_busywait_env && *openspm_busywait_env == '1';\n  std::fprintf(stderr, "[OPENSPM-LAB] analyzer_busywait=%s\\n",\n               openspm_busywait_enabled ? "on" : "off");\n  std::fflush(stderr);\n\n  u64 openspm_perf_last_cycles = m_charged_cycles;\n''',
    "cache busywait enable once per Run",
)

replace_once(
    run_cpp,
    '''          const bool detected_idle =\n              !configured_idle && m_guest.pc == runtime_dispatch_address &&\n              IsBusyWaitLoop(runtime_dispatch_address);\n''',
    '''          const bool detected_idle =\n              openspm_busywait_enabled && !configured_idle &&\n              m_guest.pc == runtime_dispatch_address &&\n              IsBusyWaitLoop(runtime_dispatch_address);\n''',
    "zero-cost disabled busywait gate",
)

# Once the dispatcher has gated the experiment, do not pay getenv again for
# every candidate loop while the detector is enabled.
replace_once(
    core_cpp,
    '''  // Experimental path: OFF unless the launcher explicitly asks for it.\n  const char* enabled_env = std::getenv("OPENSPM_BUSYWAIT");\n  if (!enabled_env || *enabled_env != '1')\n    return false;\n\n  if ((address & 3u) != 0)\n''',
    '''  if ((address & 3u) != 0)\n''',
    "remove per-call getenv from analyzer",
)

print("M11D2: busywait enable hoisted out of dispatcher hot path")
