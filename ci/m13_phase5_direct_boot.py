from pathlib import Path


def replace_once(path: str, old: str, new: str, label: str) -> None:
    p = Path(path)
    text = p.read_text().replace("\r\n", "\n")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one anchor, found {count}")
    p.write_text(text.replace(old, new, 1))


# Guard against repeating the region-mixup that the first direct-boot candidate
# exposed on Windows. The original experimental addresses came from EU0's
# small-data region while the executable/module is R8PE01 US2. Static seqdrv
# globals retain the same gp-relative offsets, so derive and assert the US2
# values explicitly here in addition to checking seqWork from the US2 map.
EU0_GP = 0x805AE178
US2_GP = 0x8056CB78
assert US2_GP + (0x805AE0BC - EU0_GP) == 0x8056CABC  # next_seq
assert US2_GP + (0x805AE918 - EU0_GP) == 0x8056D318  # next_p0
assert US2_GP + (0x805AE91C - EU0_GP) == 0x8056D31C  # next_p1

backend = "backend/inprocess/moderngekko_backend.cpp"

replace_once(
    backend,
    """constexpr std::uint32_t R8PE01_TICK_BEGIN_PC = 0x8017B72Cu;
constexpr std::uint32_t R8PE01_TICK_END_PC = 0x801A7ACCu;

struct BackendContext {
""",
    """constexpr std::uint32_t R8PE01_TICK_BEGIN_PC = 0x8017B72Cu;
constexpr std::uint32_t R8PE01_TICK_END_PC = 0x801A7ACCu;

// Exact R8PE01 US2 sequence state. seqWork is the live public sequence work
// object from the US2 symbol map. seqSetSeq's private globals are in the small-
// data region and are derived from US2 gp=0x8056CB78 using the independently
// known EU0 gp-relative offsets. Never mix absolute addresses across revisions.
constexpr std::uint32_t R8PE01_SEQ_WORK = 0x804D0D60u;
constexpr std::uint32_t R8PE01_NEXT_SEQ = 0x8056CABCu;
constexpr std::uint32_t R8PE01_NEXT_P0 = 0x8056D318u;
constexpr std::uint32_t R8PE01_NEXT_P1 = 0x8056D31Cu;

// mapDataPtr at 0x80029364 constructs 0x803C5098 on R8PE01 US2.
// The semantic layout is MapData*[0x1D4], with MapData::name at +0x00.
constexpr std::uint32_t R8PE01_MAP_DATA = 0x803C5098u;
constexpr std::uint32_t R8PE01_MAP_DATA_COUNT = 0x1D4u;
constexpr std::uint32_t R8PE01_SEQ_GAME = 2u;
constexpr std::uint32_t R8PE01_SEQ_MAPCHANGE = 3u;

// Independently measured by the Bleck SPM tooling: asking for a map change too
// early can leave the loader at stage 11. Its proven boot-map path starts on the
// first gameplay frame and waits 120 frames before using the game's map-change
// sequence. Mirror that timing rather than inventing an OpenSPM shortcut.
constexpr std::uint64_t R8PE01_DEBUG_BOOT_DELAY_TICKS = 120u;

struct BackendContext {
""",
    "direct boot exact R8PE01 constants",
)

replace_once(
    backend,
    """    moderngekko::Runtime* active_runtime = nullptr;
    std::atomic<std::uint64_t> tick_count{0};
};
""",
    """    moderngekko::Runtime* active_runtime = nullptr;
    std::atomic<std::uint64_t> tick_count{0};

    // Startup-profile state. Written before Runtime::Run and then owned by the
    // emulated CPU thread through ObserverTickBegin; no cross-thread mutation.
    std::string debug_boot_target;
    std::string debug_boot_map;
    bool debug_boot_seen_game = false;
    bool debug_boot_done = false;
    bool debug_boot_resolve_warned = false;
    std::uint64_t debug_boot_game_ticks = 0;
    std::uint64_t debug_boot_next_resolve_tick = R8PE01_DEBUG_BOOT_DELAY_TICKS;
};
""",
    "direct boot backend state",
)

replace_once(
    backend,
    """void EmitRuntimeEvent(uint32_t event_id, const void* event_data) {
    const openspm_host_api* host = g_context.host;
    if (HostHasRuntimeEventSink(host))
        host->runtime_event(host->context, event_id, event_data);
}

std::filesystem::path PathFromUtf8(const char* value) {
""",
    """void EmitRuntimeEvent(uint32_t event_id, const void* event_data) {
    const openspm_host_api* host = g_context.host;
    if (HostHasRuntimeEventSink(host))
        host->runtime_event(host->context, event_id, event_data);
}

bool ReadGuestU32BE(moderngekko::Runtime* runtime, std::uint32_t address, std::uint32_t* out) {
    unsigned char bytes[4];
    if (!runtime || !out || !runtime->CpuThreadReadGuestMemory(address, bytes, sizeof(bytes)))
        return false;
    *out = (static_cast<std::uint32_t>(bytes[0]) << 24) |
           (static_cast<std::uint32_t>(bytes[1]) << 16) |
           (static_cast<std::uint32_t>(bytes[2]) << 8) |
           static_cast<std::uint32_t>(bytes[3]);
    return true;
}

bool WriteGuestU32BE(moderngekko::Runtime* runtime, std::uint32_t address, std::uint32_t value) {
    const unsigned char bytes[4] = {
        static_cast<unsigned char>((value >> 24) & 0xFFu),
        static_cast<unsigned char>((value >> 16) & 0xFFu),
        static_cast<unsigned char>((value >> 8) & 0xFFu),
        static_cast<unsigned char>(value & 0xFFu),
    };
    return runtime && runtime->CpuThreadWriteGuestMemory(address, bytes, sizeof(bytes));
}

std::uint32_t ResolveMapNamePointer(moderngekko::Runtime* runtime, const std::string& wanted) {
    if (!runtime || wanted.empty() || wanted.size() >= 32)
        return 0;

    char name[32]{};
    const std::size_t bytes_to_read = wanted.size() + 1;
    for (std::uint32_t i = 0; i < R8PE01_MAP_DATA_COUNT; ++i) {
        std::uint32_t map_data = 0;
        std::uint32_t name_ptr = 0;
        if (!ReadGuestU32BE(runtime, R8PE01_MAP_DATA + i * 4u, &map_data) || !map_data)
            continue;
        if (!ReadGuestU32BE(runtime, map_data, &name_ptr) || !name_ptr)
            continue;
        std::memset(name, 0, sizeof(name));
        if (!runtime->CpuThreadReadGuestMemory(name_ptr, name, bytes_to_read))
            continue;
        if (std::memcmp(name, wanted.c_str(), bytes_to_read) == 0)
            return name_ptr;
    }
    return 0;
}

void ConfigureDebugBoot(BackendContext* context, const openspm_launch_desc* launch) {
    if (!context)
        return;
    context->debug_boot_target.clear();
    context->debug_boot_map.clear();
    context->debug_boot_seen_game = false;
    context->debug_boot_done = false;
    context->debug_boot_resolve_warned = false;
    context->debug_boot_game_ticks = 0;
    context->debug_boot_next_resolve_tick = R8PE01_DEBUG_BOOT_DELAY_TICKS;

    const char* target = nullptr;
    if (launch && launch->header.struct_size >= MEMBER_END(openspm_launch_desc, boot_target_utf8))
        target = launch->boot_target_utf8;
    if (!target || !*target || std::strcmp(target, "normal") == 0)
        return;

    context->debug_boot_target = target;
    if (context->debug_boot_target == "mario_house")
        context->debug_boot_map = "aa1_01";
    else if (context->debug_boot_target == "flipside_shop")
        context->debug_boot_map = "mac_09";
    else if (context->debug_boot_target == "bowser_minions")
        context->debug_boot_map = "aa2_02";
    else {
        Log(OPENSPM_LOG_WARN, "Unknown debug boot target '" + context->debug_boot_target +
                              "'; continuing with normal boot.");
        context->debug_boot_target.clear();
        return;
    }

    Log(OPENSPM_LOG_INFO, "Debug startup profile '" + context->debug_boot_target +
                          "' armed for semantic map target " + context->debug_boot_map + ".");
}

void AdvanceDebugBoot(BackendContext* context) {
    if (!context || context->debug_boot_map.empty() || context->debug_boot_done)
        return;

    // active_runtime is published before Runtime::Run starts the CPU thread and
    // remains valid until Run returns, after this callback can no longer fire.
    moderngekko::Runtime* runtime = context->active_runtime;
    if (!runtime)
        return;

    std::uint32_t sequence = 0;
    if (!ReadGuestU32BE(runtime, R8PE01_SEQ_WORK, &sequence))
        return;
    if (sequence != R8PE01_SEQ_GAME)
        return;

    if (!context->debug_boot_seen_game) {
        context->debug_boot_seen_game = true;
        context->debug_boot_game_ticks = 0;
        Log(OPENSPM_LOG_INFO, "First R8PE01 gameplay sequence reached; waiting 120 SPM ticks before debug map change.");
    }

    ++context->debug_boot_game_ticks;
    if (context->debug_boot_game_ticks < R8PE01_DEBUG_BOOT_DELAY_TICKS ||
        context->debug_boot_game_ticks < context->debug_boot_next_resolve_tick)
        return;

    const std::uint32_t map_name = ResolveMapNamePointer(runtime, context->debug_boot_map);
    if (!map_name) {
        context->debug_boot_next_resolve_tick = context->debug_boot_game_ticks + 60u;
        if (!context->debug_boot_resolve_warned) {
            Log(OPENSPM_LOG_WARN, "Debug boot map is not resolvable in R8PE01 mapData yet; will retry during gameplay.");
            context->debug_boot_resolve_warned = true;
        }
        return;
    }

    // Exact semantic equivalent of seqSetSeq(SEQ_MAPCHANGE, map, nullptr): write
    // parameters first, then publish next_seq last. seqMain is about to run on
    // this same CPU thread and consumes the request normally.
    if (!WriteGuestU32BE(runtime, R8PE01_NEXT_P0, map_name) ||
        !WriteGuestU32BE(runtime, R8PE01_NEXT_P1, 0u) ||
        !WriteGuestU32BE(runtime, R8PE01_NEXT_SEQ, R8PE01_SEQ_MAPCHANGE)) {
        Log(OPENSPM_LOG_ERROR, "Could not write the R8PE01 semantic debug map-change request; normal gameplay continues.");
        context->debug_boot_done = true;
        return;
    }

    context->debug_boot_done = true;
    Log(OPENSPM_LOG_INFO, "Debug startup target '" + context->debug_boot_target +
                          "' requested R8PE01 map change to " + context->debug_boot_map + ".");
}

std::filesystem::path PathFromUtf8(const char* value) {
""",
    "direct boot semantic adapter helpers",
)

replace_once(
    backend,
    """void ObserverTickBegin(void* opaque) {
    auto* context = static_cast<BackendContext*>(opaque);
    if (!context)
        return;
    openspm_runtime_tick_event event{};
""",
    """void ObserverTickBegin(void* opaque) {
    auto* context = static_cast<BackendContext*>(opaque);
    if (!context)
        return;
    AdvanceDebugBoot(context);
    openspm_runtime_tick_event event{};
""",
    "direct boot on safe tick boundary",
)

replace_once(
    backend,
    """    SetEnvironmentDefaults(runtime_root);

    moderngekko::RuntimeConfig config;
""",
    """    SetEnvironmentDefaults(runtime_root);
    ConfigureDebugBoot(context, launch);

    moderngekko::RuntimeConfig config;
""",
    "direct boot profile configuration",
)

replace_once(
    backend,
    """    Log(OPENSPM_LOG_INFO, "Entering in-process ModernGekko runtime with live runtime and true simulation-tick services.");
""",
    """    Log(OPENSPM_LOG_INFO, "Entering in-process ModernGekko runtime with live services, true SPM ticks and startup-profile map routing.");
""",
    "direct boot runtime log",
)

replace_once(
    backend,
    """    out_api->backend_version = "13.4-phase5-ticks";
""",
    """    out_api->backend_version = "13.5-phase5-directboot-us2";
""",
    "direct boot backend version",
)

print("M13 phase5: semantic R8PE01 US2 debug direct boot staged")
