#!/usr/bin/env python3
from pathlib import Path


def replace_once(path: str, old: str, new: str, label: str) -> None:
    p = Path(path)
    s = p.read_text().replace("\r\n", "\n")
    count = s.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one anchor, found {count}")
    p.write_text(s.replace(old, new, 1))


hooks = "ModernGekko/vendor/dolphin/Source/Core/Core/PowerPC/StaticRecomp/StaticRecompCore_Hooks.cpp"
replace_once(
    hooks,
    '''namespace\n{\nconstexpr u32 LOCKED_CACHE_BASE = 0xE0000000u;\n}\n''',
    '''namespace\n{\nconstexpr u32 LOCKED_CACHE_BASE = 0xE0000000u;\n// DolRecomp CPU ABI native-region query protocol. Keep these local until the\n// pinned ModernGekko CPU header catches up with the already-pinned CPU runtime.\nconstexpr u32 OPENSPM_NATIVE_REGION_QUERY = 0xFFFFFFFCu;\nconstexpr u8 OPENSPM_NATIVE_REGION_PENDING = 0xFEu;\nconstexpr u8 OPENSPM_NATIVE_REGION_HANDLED = 0xFFu;\n}\n''',
    "native-region query constants",
)
replace_once(
    hooks,
    '''bool StaticRecompCore::HookHostCall(CPUState* cpu, u32 address)\n{\n  auto* core = static_cast<StaticRecompCore*>(cpu->external_user_data);\n  return core->m_module_source.host_call &&\n         core->m_module_source.host_call(cpu, address, core->m_module_source.host_call_user);\n}\n''',
    '''bool StaticRecompCore::HookHostCall(CPUState* cpu, u32 address)\n{\n  auto* core = static_cast<StaticRecompCore*>(cpu->external_user_data);\n  if (address == OPENSPM_NATIVE_REGION_QUERY &&\n      cpu->external_rid == OPENSPM_NATIVE_REGION_PENDING)\n  {\n    const u32 start = cpu->external_addr;\n    const u32 end = cpu->external_value;\n    cpu->external_rid = OPENSPM_NATIVE_REGION_HANDLED;\n\n    // The first user is a narrow DOL function inside one translated chunk.\n    // Requiring a single verified chunk makes the query cheap and exact:\n    // VerifyChunk hashes the whole chunk, and any I-cache invalidation resets\n    // its state before the next query. Forced fallback or guest interception\n    // anywhere in the requested range also blocks native bypass.\n    if (start >= end)\n      return true;\n    const int first_chunk = core->ChunkIndexOf(start);\n    const int last_chunk = core->ChunkIndexOf(end - 1u);\n    if (first_chunk < 0 || first_chunk != last_chunk || !core->DispatchableAt(start))\n      return true;\n\n    for (u32 pc = start; pc < end; pc += 4u)\n    {\n      if (core->IsForcedFallbackAddress(pc))\n        return true;\n    }\n\n    if (core->m_module_source.host_call_range_contains &&\n        core->m_module_source.host_call_range_contains(\n            start, end, core->m_module_source.host_call_user))\n      return true;\n    if (!core->m_module_source.host_call_range_contains &&\n        core->m_module_source.host_call_contains)\n    {\n      for (u32 pc = start; pc < end; pc += 4u)\n      {\n        if (core->IsHostCallAddress(pc))\n          return true;\n      }\n    }\n    return false;\n  }\n\n  return core->m_module_source.host_call &&\n         core->m_module_source.host_call(cpu, address, core->m_module_source.host_call_user);\n}\n''',
    "verified native-region host query",
)

core_cpp = "ModernGekko/vendor/dolphin/Source/Core/Core/PowerPC/StaticRecomp/StaticRecompCore.cpp"
replace_once(
    core_cpp,
    '  m_guest.host_call = m_module_source.host_call ? HookHostCall : nullptr;\n',
    '''  // Keep the lightweight thunk installed even when guest-mod interception\n  // is absent so generated/native helpers can query verified code regions.\n  // Run() still gates actual guest host-call address checks on module_source.\n  m_guest.host_call = HookHostCall;\n''',
    "always install native-region query thunk",
)

run_cpp = Path("ModernGekko/vendor/dolphin/Source/Core/Core/PowerPC/StaticRecomp/StaticRecompCore_Run.cpp")
s = run_cpp.read_text().replace("\r\n", "\n")
replacements = {
    '!m_guest.host_call': '!m_module_source.host_call',
    'm_guest.host_call && IsHostCallAddress(ppc.pc)':
        'm_module_source.host_call && IsHostCallAddress(ppc.pc)',
    'm_guest.host_call && IsHostCallAddress(m_guest.pc)':
        'm_module_source.host_call && IsHostCallAddress(m_guest.pc)',
}
for old, new in replacements.items():
    count = s.count(old)
    if count == 0:
        raise RuntimeError(f"run-loop host-call gate: missing {old!r}")
    s = s.replace(old, new)
run_cpp.write_text(s)

print("M13 Phase5 verified native-region query guard applied")
