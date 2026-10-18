from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    text = text.replace("\r\n", "\n")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)


# Guest-side first-fault provenance at 0x80301430.
# Diagnostic only: the faulting lhz is deliberately left untouched.
chunk = Path("m10/module/generated/chunks/chunk_0185_text1_802FD940.c")
s = chunk.read_text()

s = replace_once(
    s,
    '#include "../generated.h"\n',
    '''#include "../generated.h"\n\n#include <stdio.h>\n\ntypedef struct SpmFirstFault80301430\n{\n    u32 outer;\n    u32 inner;\n    u32 destination;\n    u32 entry_r1;\n    u32 entry_lr;\n    u32 base;\n    u32 ref_type;\n    u32 ref_value;\n    int active;\n    int via_wrapper;\n    int reported;\n} SpmFirstFault80301430;\n\nstatic SpmFirstFault80301430 g_spm_first_fault_80301430 = {0};\n''',
    "guest stdio/state block",
)

s = replace_once(
    s,
    '''label_80300624:\n    ctx->pc = 0x80300624u;\n    ctx->downcount -= 2;\n    // 80300624: lwz     r3, 4(r3)\n''',
    '''label_80300624:\n    ctx->pc = 0x80300624u;\n    ctx->downcount -= 2;\n    g_spm_first_fault_80301430.outer = ctx->gpr[3];\n    g_spm_first_fault_80301430.destination = ctx->gpr[4];\n    g_spm_first_fault_80301430.active = 1;\n    g_spm_first_fault_80301430.via_wrapper = 1;\n    // 80300624: lwz     r3, 4(r3)\n''',
    "guest wrapper entry",
)

s = replace_once(
    s,
    '''        ctx->gpr[3] = mem_read32(ctx, ea);\n    }\n\nlabel_80300628:\n''',
    '''        ctx->gpr[3] = mem_read32(ctx, ea);\n    }\n    g_spm_first_fault_80301430.inner = ctx->gpr[3];\n\nlabel_80300628:\n''',
    "guest wrapper inner pointer",
)

s = replace_once(
    s,
    '''label_803013FC:\n    ctx->pc = 0x803013FCu;\n    ctx->downcount -= 9;\n    // 803013FC: stwu     r1, -16(r1)\n''',
    '''label_803013FC:\n    ctx->pc = 0x803013FCu;\n    ctx->downcount -= 9;\n    if (!g_spm_first_fault_80301430.active)\n    {\n        g_spm_first_fault_80301430.outer = 0;\n        g_spm_first_fault_80301430.inner = ctx->gpr[3];\n        g_spm_first_fault_80301430.destination = ctx->gpr[4];\n        g_spm_first_fault_80301430.active = 1;\n        g_spm_first_fault_80301430.via_wrapper = 0;\n    }\n    g_spm_first_fault_80301430.entry_r1 = ctx->gpr[1];\n    g_spm_first_fault_80301430.entry_lr = ctx->lr;\n    // 803013FC: stwu     r1, -16(r1)\n''',
    "guest resolver caller entry",
)

s = replace_once(
    s,
    '''        ctx->gpr[5] = mem_read32(ctx, ea);\n    }\n\nlabel_80301408:\n''',
    '''        ctx->gpr[5] = mem_read32(ctx, ea);\n    }\n    g_spm_first_fault_80301430.base = ctx->gpr[5];\n\nlabel_80301408:\n''',
    "guest base pointer capture",
)

s = replace_once(
    s,
    '''        ctx->gpr[3] = mem_read8(ctx, ea);\n    }\n\nlabel_80301418:\n''',
    '''        ctx->gpr[3] = mem_read8(ctx, ea);\n    }\n    g_spm_first_fault_80301430.ref_type = ctx->gpr[3];\n\nlabel_80301418:\n''',
    "guest reference type capture",
)

s = replace_once(
    s,
    '''        ctx->gpr[4] = mem_read32(ctx, ea);\n    }\n\nlabel_8030141C:\n''',
    '''        ctx->gpr[4] = mem_read32(ctx, ea);\n    }\n    g_spm_first_fault_80301430.ref_value = ctx->gpr[4];\n\nlabel_8030141C:\n''',
    "guest reference value capture",
)

s = replace_once(
    s,
    '''label_80301430:\n    ctx->pc = 0x80301430u;\n    ctx->downcount -= 15;\n    // 80301430: lhz     r0, 0(r3)\n''',
    '''label_80301430:\n    ctx->pc = 0x80301430u;\n    ctx->downcount -= 15;\n    if (g_spm_first_fault_80301430.active && !g_spm_first_fault_80301430.reported &&\n        ctx->gpr[3] == 0)\n    {\n        const u32 saved_lr = mem_read32(ctx, ctx->gpr[1] + 0x14u);\n        fprintf(stderr,\n                "[M10PTR] fault_pc=%08x current_r1=%08x r3=%08x r4=%08x r5=%08x "\n                "r31=%08x saved_lr=%08x entry_r1=%08x entry_lr=%08x outer=%08x "\n                "inner=%08x base=%08x ref_type=%08x ref_value=%08x dest=%08x "\n                "via_wrapper=%u\\n",\n                ctx->pc, ctx->gpr[1], ctx->gpr[3], ctx->gpr[4], ctx->gpr[5],\n                ctx->gpr[31], saved_lr, g_spm_first_fault_80301430.entry_r1,\n                g_spm_first_fault_80301430.entry_lr, g_spm_first_fault_80301430.outer,\n                g_spm_first_fault_80301430.inner, g_spm_first_fault_80301430.base,\n                g_spm_first_fault_80301430.ref_type, g_spm_first_fault_80301430.ref_value,\n                g_spm_first_fault_80301430.destination,\n                g_spm_first_fault_80301430.via_wrapper ? 1u : 0u);\n        fflush(stderr);\n        g_spm_first_fault_80301430.reported = 1;\n    }\n    // 80301430: lhz     r0, 0(r3)\n''',
    "guest first-fault report",
)

s = replace_once(
    s,
    '''label_8030147C:\n    ctx->pc = 0x8030147Cu;\n    // 8030147C: blr\n''',
    '''label_8030147C:\n    ctx->pc = 0x8030147Cu;\n    g_spm_first_fault_80301430.active = 0;\n    // 8030147C: blr\n''',
    "guest provenance reset",
)
chunk.write_text(s)


# RecompCore first DSI plus PC-state transition across SyncOut/CheckExceptions.
hooks = Path("ModernGekko/vendor/dolphin/Source/Core/Core/PowerPC/StaticRecomp/StaticRecompCore_Hooks.cpp")
h = hooks.read_text()
h = replace_once(
    h,
    '''u64 StaticRecompCore::HookExternalRead(CPUState* cpu, u32 ea, u8 size)\n{\n  auto* core = static_cast<StaticRecompCore*>(cpu->external_user_data);\n  ea = core->TranslateRelAddress(ea);\n  if (ea == 0)\n''',
    '''u64 StaticRecompCore::HookExternalRead(CPUState* cpu, u32 ea, u8 size)\n{\n  auto* core = static_cast<StaticRecompCore*>(cpu->external_user_data);\n  ea = core->TranslateRelAddress(ea);\n  auto& ppc = core->m_system.GetPPCState();\n  const u32 exceptions_before = ppc.Exceptions;\n  const u32 ppc_pc_before = ppc.pc;\n  if (ea == 0)\n''',
    "DSI hook entry",
)
h = replace_once(
    h,
    '''  default:\n    ERROR_LOG_FMT(POWERPC, "StaticRecomp: external read of bad size {} at 0x{:08X}", size, ea);\n    return 0;\n  }\n  if (core->m_lockstep_verifier->m_ls_journaling &&\n''',
    '''  default:\n    ERROR_LOG_FMT(POWERPC, "StaticRecomp: external read of bad size {} at 0x{:08X}", size, ea);\n    return 0;\n  }\n\n  static bool s_m10_first_dsi_reported = false;\n  if (!s_m10_first_dsi_reported && (exceptions_before & EXCEPTION_DSI) == 0 &&\n      (ppc.Exceptions & EXCEPTION_DSI) != 0)\n  {\n    std::fprintf(stderr,\n                 "[M10DSI] ea=%08x size=%u guest_pc=%08x guest_lr=%08x guest_r1=%08x "\n                 "guest_msr=%08x ppc_pc_before=%08x ppc_pc_after=%08x ppc_msr=%08x "\n                 "dar=%08x dsisr=%08x exceptions_before=%08x exceptions_after=%08x "\n                 "returned_value=%016llx\\n",\n                 ea, size, cpu->pc, cpu->lr, cpu->gpr[1], cpu->msr, ppc_pc_before, ppc.pc,\n                 ppc.msr.Hex, ppc.spr[SPR_DAR], ppc.spr[SPR_DSISR], exceptions_before,\n                 ppc.Exceptions, static_cast<unsigned long long>(value));\n    std::fflush(stderr);\n    s_m10_first_dsi_reported = true;\n  }\n  if (core->m_lockstep_verifier->m_ls_journaling &&\n''',
    "DSI callback report",
)
hooks.write_text(h)

run = Path("ModernGekko/vendor/dolphin/Source/Core/Core/PowerPC/StaticRecomp/StaticRecompCore_Run.cpp")
r = run.read_text()
r = replace_once(
    r,
    '''  const CPU::State* state_ptr = m_system.GetCPU().GetStatePtr();\n  FilePtr dispatch_trace = OpenDispatchTrace();\n\n  m_guest.ram = memory.GetRAM();\n''',
    '''  const CPU::State* state_ptr = m_system.GetCPU().GetStatePtr();\n  FilePtr dispatch_trace = OpenDispatchTrace();\n  static bool s_m10_dsi_boundary_reported = false;\n\n  m_guest.ram = memory.GetRAM();\n''',
    "DSI run setup",
)
r = replace_once(
    r,
    '''        } while (m_module_active && fast_dispatchable_at(m_guest.pc) &&\n                 !(m_guest.host_call && IsHostCallAddress(m_guest.pc)) && ppc.downcount > 0 &&\n                 *state_ptr == CPU::State::Running);\n        SyncOut();\n        if ((ppc.Exceptions & SYNC_EXCEPTION_MASK) != 0)\n          power_pc.CheckExceptions();\n        else if ((ppc.Exceptions & EXCEPTION_EXTERNAL_INT) != 0 && ppc.msr.EE &&\n                 after_mtmsr(ppc.pc))\n          power_pc.CheckExternalExceptions();\n''',
    '''        } while (m_module_active && fast_dispatchable_at(m_guest.pc) &&\n                 !(m_guest.host_call && IsHostCallAddress(m_guest.pc)) && ppc.downcount > 0 &&\n                 *state_ptr == CPU::State::Running);\n        const bool trace_m10_dsi =\n            !s_m10_dsi_boundary_reported && (ppc.Exceptions & EXCEPTION_DSI) != 0;\n        if (trace_m10_dsi)\n        {\n          std::fprintf(stderr,\n                       "[M10DSI-BEFORE-SYNC] guest_pc=%08x guest_lr=%08x guest_r1=%08x "\n                       "ppc_pc=%08x dar=%08x dsisr=%08x exceptions=%08x\\n",\n                       m_guest.pc, m_guest.lr, m_guest.gpr[1], ppc.pc, ppc.spr[SPR_DAR],\n                       ppc.spr[SPR_DSISR], ppc.Exceptions);\n          std::fflush(stderr);\n        }\n        SyncOut();\n        if (trace_m10_dsi)\n        {\n          std::fprintf(stderr,\n                       "[M10DSI-AFTER-SYNC] guest_pc=%08x ppc_pc=%08x ppc_npc=%08x "\n                       "dar=%08x dsisr=%08x exceptions=%08x\\n",\n                       m_guest.pc, ppc.pc, ppc.npc, ppc.spr[SPR_DAR], ppc.spr[SPR_DSISR],\n                       ppc.Exceptions);\n          std::fflush(stderr);\n        }\n        if ((ppc.Exceptions & SYNC_EXCEPTION_MASK) != 0)\n          power_pc.CheckExceptions();\n        else if ((ppc.Exceptions & EXCEPTION_EXTERNAL_INT) != 0 && ppc.msr.EE &&\n                 after_mtmsr(ppc.pc))\n          power_pc.CheckExternalExceptions();\n        if (trace_m10_dsi)\n        {\n          std::fprintf(stderr,\n                       "[M10DSI-AFTER-CHECK] ppc_pc=%08x ppc_npc=%08x srr0=%08x srr1=%08x "\n                       "dar=%08x dsisr=%08x exceptions=%08x\\n",\n                       ppc.pc, ppc.npc, ppc.spr[SPR_SRR0], ppc.spr[SPR_SRR1],\n                       ppc.spr[SPR_DAR], ppc.spr[SPR_DSISR], ppc.Exceptions);\n          std::fflush(stderr);\n          s_m10_dsi_boundary_reported = true;\n        }\n''',
    "DSI SyncOut/CheckExceptions boundary",
)
run.write_text(r)

print("M10 first-fault pointer + DSI diagnostics applied")
