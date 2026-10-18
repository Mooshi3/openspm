from pathlib import Path


def replace_once(path: str, old: str, new: str, label: str) -> None:
    p = Path(path)
    text = p.read_text().replace("\r\n", "\n")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one anchor, found {count}")
    p.write_text(text.replace(old, new, 1))


# ---------------------------------------------------------------------------
# Windows launcher UI: development-only live command box.
# ---------------------------------------------------------------------------
gui = "launcher/gui.c"

replace_once(
    gui,
    '#include "gui.h"\n',
    '#include "gui.h"\n#include "openspm/debug_ipc.h"\n',
    "GUI debug IPC include",
)

replace_once(
    gui,
    "#define IDC_STATUS          1010\n",
    """#define IDC_STATUS          1010
#define IDC_DEBUG_COMMAND   1011
#define IDC_DEBUG_EXECUTE   1012
#define IDC_DEBUG_OUTPUT    1013
#define IDT_DEBUG_RESPONSE  1101
""",
    "GUI debug control IDs",
)

replace_once(
    gui,
    """    HWND diagnostics;
    HWND status;
} gui_state;
""",
    """    HWND diagnostics;
    HWND status;
    HWND debug_command;
    HWND debug_output;
    HANDLE game_process;
    DWORD game_pid;
    LONG pending_debug_generation;
} gui_state;
""",
    "GUI debug state",
)

replace_once(
    gui,
    """static void set_status(gui_state *state, const wchar_t *text) {
    if (state && state->status) SetWindowTextW(state->status, text ? text : L"");
}

static void browse_game(gui_state *state) {
""",
    r'''static void set_status(gui_state *state, const wchar_t *text) {
    if (state && state->status) SetWindowTextW(state->status, text ? text : L"");
}

static void debug_mailbox_name(DWORD pid, wchar_t *out, size_t out_count) {
    if (!out || !out_count) return;
    _snwprintf_s(out, out_count, _TRUNCATE, L"Local\\OpenSPM.Debug.%lu", (unsigned long)pid);
}

static openspm_debug_mailbox *open_debug_mailbox(gui_state *state, HANDLE *mapping_out) {
    wchar_t name[96];
    HANDLE mapping;
    void *view;
    if (mapping_out) *mapping_out = NULL;
    if (!state || !state->game_pid) return NULL;
    debug_mailbox_name(state->game_pid, name, _countof(name));
    mapping = OpenFileMappingW(FILE_MAP_READ | FILE_MAP_WRITE, FALSE, name);
    if (!mapping) return NULL;
    view = MapViewOfFile(mapping, FILE_MAP_READ | FILE_MAP_WRITE, 0, 0, sizeof(openspm_debug_mailbox));
    if (!view) {
        CloseHandle(mapping);
        return NULL;
    }
    if (mapping_out) *mapping_out = mapping;
    return (openspm_debug_mailbox *)view;
}

static void set_debug_output(gui_state *state, const wchar_t *text) {
    if (state && state->debug_output) SetWindowTextW(state->debug_output, text ? text : L"");
}

static void poll_debug_response(gui_state *state) {
    HANDLE mapping = NULL;
    openspm_debug_mailbox *mailbox;
    LONG response_generation;
    wchar_t wide[OPENSPM_DEBUG_RESPONSE_BYTES];
    int chars;
    if (!state || state->pending_debug_generation <= 0) return;
    mailbox = open_debug_mailbox(state, &mapping);
    if (!mailbox) return;
    response_generation = InterlockedCompareExchange((volatile LONG *)&mailbox->response_generation, 0, 0);
    if (response_generation >= state->pending_debug_generation) {
        chars = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, mailbox->response, -1,
                                    wide, (int)_countof(wide));
        if (chars > 0) set_debug_output(state, wide);
        else set_debug_output(state, L"(debug response was not valid UTF-8)");
        state->pending_debug_generation = 0;
    }
    UnmapViewOfFile(mailbox);
    CloseHandle(mapping);
}

static void send_debug_command(gui_state *state) {
    wchar_t command_w[OPENSPM_DEBUG_COMMAND_BYTES];
    char command[OPENSPM_DEBUG_COMMAND_BYTES];
    HANDLE mapping = NULL;
    openspm_debug_mailbox *mailbox;
    LONG generation;
    if (!state || !state->game_pid) {
        set_debug_output(state, L"Launch the game first. The debug console talks to the running OpenSPM process.");
        return;
    }
    if (state->game_process && WaitForSingleObject(state->game_process, 0) == WAIT_OBJECT_0) {
        set_debug_output(state, L"The last OpenSPM game process has already exited.");
        return;
    }
    command_w[0] = L'\0';
    GetWindowTextW(state->debug_command, command_w, (int)_countof(command_w));
    if (!command_w[0]) return;
    if (!WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, command_w, -1,
                             command, (int)sizeof(command), NULL, NULL)) {
        set_debug_output(state, L"Could not encode debug command as UTF-8.");
        return;
    }
    mailbox = open_debug_mailbox(state, &mapping);
    if (!mailbox || mailbox->version != OPENSPM_DEBUG_IPC_VERSION) {
        if (mailbox) UnmapViewOfFile(mailbox);
        if (mapping) CloseHandle(mapping);
        set_debug_output(state,
            L"Debug channel is not ready yet. Wait until the game window/runtime has started, then try again.");
        return;
    }
    strncpy_s(mailbox->command, sizeof(mailbox->command), command, _TRUNCATE);
    MemoryBarrier();
    generation = InterlockedIncrement((volatile LONG *)&mailbox->command_generation);
    state->pending_debug_generation = generation;
    set_debug_output(state, L"Command queued on the next safe SPM tick...");
    UnmapViewOfFile(mailbox);
    CloseHandle(mapping);
}

static void browse_game(gui_state *state) {
''',
    "GUI debug command helpers",
)

replace_once(
    gui,
    """    } else {
        CloseHandle(process.hThread);
        CloseHandle(process.hProcess);
        if (_wcsicmp(selected_boot_target(state), L"normal") == 0)
            set_status(state, L"Game launched.");
        else
            set_status(state, L"Debug boot target queued. The legacy backend will ignore it until the in-process game service lands.");
    }
""",
    """    } else {
        CloseHandle(process.hThread);
        if (state->game_process) CloseHandle(state->game_process);
        state->game_process = process.hProcess;
        state->game_pid = process.dwProcessId;
        state->pending_debug_generation = 0;
        SetTimer(state->window, IDT_DEBUG_RESPONSE, 100, NULL);
        if (_wcsicmp(selected_boot_target(state), L"normal") == 0)
            set_status(state, L"Game launched. Live debug commands become available when the runtime starts.");
        else
            set_status(state, L"Debug boot target queued. Live debug commands become available when the runtime starts.");
    }
""",
    "GUI retain child process for debug IPC",
)

replace_once(
    gui,
    """    state->diagnostics = make_control(0, L"BUTTON", L"Show diagnostics console",
                                      WS_CHILD | WS_VISIBLE | BS_AUTOCHECKBOX,
                                      20, 430, 240, 24, state->window, IDC_DIAGNOSTICS);

    state->status = make_control(0, L"STATIC", L"Ready.", WS_CHILD | WS_VISIBLE,
                                 20, 470, 500, 42, state->window, IDC_STATUS);
    make_control(0, L"BUTTON", L"Play", WS_CHILD | WS_VISIBLE | BS_DEFPUSHBUTTON,
                 548, 455, 140, 48, state->window, IDC_PLAY);
""",
    """    state->diagnostics = make_control(0, L"BUTTON", L"Show diagnostics console",
                                      WS_CHILD | WS_VISIBLE | BS_AUTOCHECKBOX,
                                      20, 430, 240, 24, state->window, IDC_DIAGNOSTICS);

    make_control(0, L"STATIC", L"Live debug command (development only)", WS_CHILD | WS_VISIBLE,
                 20, 462, 320, 20, state->window, 0);
    state->debug_command = make_control(WS_EX_CLIENTEDGE, L"EDIT", L"help",
                                        WS_CHILD | WS_VISIBLE | ES_AUTOHSCROLL,
                                        20, 484, 530, 25, state->window, IDC_DEBUG_COMMAND);
    make_control(0, L"BUTTON", L"Execute", WS_CHILD | WS_VISIBLE | BS_PUSHBUTTON,
                 560, 483, 128, 27, state->window, IDC_DEBUG_EXECUTE);
    state->debug_output = make_control(WS_EX_CLIENTEDGE, L"EDIT",
                                       L"Commands: help, seq, map, gsw <n> [value], warp <map>, perf",
                                       WS_CHILD | WS_VISIBLE | ES_MULTILINE | ES_AUTOVSCROLL | ES_READONLY | WS_VSCROLL,
                                       20, 520, 668, 72, state->window, IDC_DEBUG_OUTPUT);

    state->status = make_control(0, L"STATIC", L"Ready.", WS_CHILD | WS_VISIBLE,
                                 20, 612, 500, 42, state->window, IDC_STATUS);
    make_control(0, L"BUTTON", L"Play", WS_CHILD | WS_VISIBLE | BS_DEFPUSHBUTTON,
                 548, 602, 140, 48, state->window, IDC_PLAY);
""",
    "GUI live debug controls",
)

replace_once(
    gui,
    """                case IDC_OPEN_MODS: open_mods_folder(state); return 0;
                case IDC_PLAY: launch_game(state); return 0;
                default: break;
""",
    """                case IDC_OPEN_MODS: open_mods_folder(state); return 0;
                case IDC_DEBUG_EXECUTE: send_debug_command(state); return 0;
                case IDC_PLAY: launch_game(state); return 0;
                default: break;
""",
    "GUI debug execute command",
)

replace_once(
    gui,
    """            break;
        case WM_CLOSE:
            DestroyWindow(window);
            return 0;
        case WM_DESTROY:
            PostQuitMessage(0);
            return 0;
""",
    """            break;
        case WM_TIMER:
            if (wparam == IDT_DEBUG_RESPONSE) {
                poll_debug_response(state);
                return 0;
            }
            break;
        case WM_CLOSE:
            DestroyWindow(window);
            return 0;
        case WM_DESTROY:
            KillTimer(window, IDT_DEBUG_RESPONSE);
            if (state->game_process) {
                CloseHandle(state->game_process);
                state->game_process = NULL;
            }
            PostQuitMessage(0);
            return 0;
""",
    "GUI debug response timer and cleanup",
)

replace_once(
    gui,
    """    state.window = CreateWindowExW(0, k_window_class, L"OpenSPM Launcher - M13 Phase 3",
                                   WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX,
                                   CW_USEDEFAULT, CW_USEDEFAULT, 724, 560,
""",
    """    state.window = CreateWindowExW(0, k_window_class, L"OpenSPM Launcher - M13 Phase 5 Debug",
                                   WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX,
                                   CW_USEDEFAULT, CW_USEDEFAULT, 724, 700,
""",
    "GUI phase5 debug window",
)

# ---------------------------------------------------------------------------
# In-process R8PE01 adapter: private mailbox + safe tick command execution.
# This script runs AFTER m13_phase5_direct_boot.py so it reuses the verified
# guest-memory and semantic map-change helpers without exposing them as SDK ABI.
# ---------------------------------------------------------------------------
backend = "backend/inprocess/moderngekko_backend.cpp"

replace_once(
    backend,
    '#include "openspm/runtime_service.h"\n',
    '#include "openspm/runtime_service.h"\n#include "openspm/debug_ipc.h"\n',
    "backend debug IPC include",
)

replace_once(
    backend,
    """#include <filesystem>
#include <memory>
""",
    """#include <filesystem>
#include <memory>
#include <cstdio>
""",
    "backend debug parser include",
)

replace_once(
    backend,
    """constexpr std::uint32_t R8PE01_SEQ_GAME = 2u;
constexpr std::uint32_t R8PE01_SEQ_MAPCHANGE = 3u;
""",
    """constexpr std::uint32_t R8PE01_SEQ_GAME = 2u;
constexpr std::uint32_t R8PE01_SEQ_MAPCHANGE = 3u;
// Address of the R8PE01/US2 spmario::gp pointer variable. The pointed-to
// SpmarioGlobals contains mapName at +0x44, gsw0 at +0x140, gsw[] at +0x544.
constexpr std::uint32_t R8PE01_SPMARIO_GP_PTR = 0x8056CB78u;
""",
    "backend exact US2 globals pointer",
)

replace_once(
    backend,
    """    std::uint64_t debug_boot_game_ticks = 0;
    std::uint64_t debug_boot_next_resolve_tick = R8PE01_DEBUG_BOOT_DELAY_TICKS;
};
""",
    """    std::uint64_t debug_boot_game_ticks = 0;
    std::uint64_t debug_boot_next_resolve_tick = R8PE01_DEBUG_BOOT_DELAY_TICKS;

    HANDLE debug_mapping = nullptr;
    openspm_debug_mailbox* debug_mailbox = nullptr;
    LONG debug_processed_generation = 0;
};
""",
    "backend debug mailbox state",
)

replace_once(
    backend,
    """std::uint32_t ResolveMapNamePointer(moderngekko::Runtime* runtime, const std::string& wanted) {
""",
    r'''bool ReadGuestU8(moderngekko::Runtime* runtime, std::uint32_t address, std::uint8_t* out) {
    unsigned char byte = 0;
    if (!runtime || !out || !runtime->CpuThreadReadGuestMemory(address, &byte, 1))
        return false;
    *out = byte;
    return true;
}

bool WriteGuestU8(moderngekko::Runtime* runtime, std::uint32_t address, std::uint8_t value) {
    const unsigned char byte = value;
    return runtime && runtime->CpuThreadWriteGuestMemory(address, &byte, 1);
}

bool ReadGuestCString(moderngekko::Runtime* runtime, std::uint32_t address,
                      char* out, std::size_t out_size) {
    if (!runtime || !out || out_size < 2 || !address)
        return false;
    for (std::size_t i = 0; i + 1 < out_size; ++i) {
        unsigned char ch = 0;
        if (!runtime->CpuThreadReadGuestMemory(address + static_cast<std::uint32_t>(i), &ch, 1))
            return false;
        out[i] = static_cast<char>(ch);
        if (!ch) return true;
    }
    out[out_size - 1] = '\0';
    return true;
}

bool ResolveSpmarioGlobals(moderngekko::Runtime* runtime, std::uint32_t* out) {
    std::uint32_t value = 0;
    if (!out || !ReadGuestU32BE(runtime, R8PE01_SPMARIO_GP_PTR, &value) || !value)
        return false;
    *out = value;
    return true;
}

std::uint32_t ResolveMapNamePointer(moderngekko::Runtime* runtime, const std::string& wanted) {
''',
    "backend guest semantic debug helpers",
)

replace_once(
    backend,
    """void ConfigureDebugBoot(BackendContext* context, const openspm_launch_desc* launch) {
""",
    r'''void DebugMailboxName(wchar_t* out, std::size_t out_count) {
    if (!out || !out_count) return;
    _snwprintf_s(out, out_count, _TRUNCATE, L"Local\\OpenSPM.Debug.%lu",
                 static_cast<unsigned long>(GetCurrentProcessId()));
}

void CloseDebugMailbox(BackendContext* context) {
    if (!context) return;
    if (context->debug_mailbox) {
        UnmapViewOfFile(context->debug_mailbox);
        context->debug_mailbox = nullptr;
    }
    if (context->debug_mapping) {
        CloseHandle(context->debug_mapping);
        context->debug_mapping = nullptr;
    }
    context->debug_processed_generation = 0;
}

void OpenDebugMailbox(BackendContext* context) {
    wchar_t name[96];
    if (!context) return;
    CloseDebugMailbox(context);
    DebugMailboxName(name, _countof(name));
    context->debug_mapping = CreateFileMappingW(INVALID_HANDLE_VALUE, nullptr, PAGE_READWRITE,
                                                 0, sizeof(openspm_debug_mailbox), name);
    if (!context->debug_mapping) {
        Log(OPENSPM_LOG_WARN, "Could not create live debug-command mailbox.");
        return;
    }
    context->debug_mailbox = static_cast<openspm_debug_mailbox*>(
        MapViewOfFile(context->debug_mapping, FILE_MAP_READ | FILE_MAP_WRITE,
                      0, 0, sizeof(openspm_debug_mailbox)));
    if (!context->debug_mailbox) {
        Log(OPENSPM_LOG_WARN, "Could not map live debug-command mailbox.");
        CloseDebugMailbox(context);
        return;
    }
    std::memset(context->debug_mailbox, 0, sizeof(*context->debug_mailbox));
    context->debug_mailbox->version = OPENSPM_DEBUG_IPC_VERSION;
    Log(OPENSPM_LOG_INFO,
        "Live development debug console is ready (help, seq, map, gsw <n> [value], warp <map>, perf).");
}

std::string ExecuteDebugCommand(BackendContext* context, const std::string& raw) {
    moderngekko::Runtime* runtime = context ? context->active_runtime : nullptr;
    char response[OPENSPM_DEBUG_RESPONSE_BYTES]{};
    char token[128]{};
    char extra[128]{};
    int index = -1;
    int value = 0;
    int consumed = 0;
    if (!runtime) return "runtime is not active";

    std::string command = raw;
    while (!command.empty() && (command.back() == ' ' || command.back() == '\t' || command.back() == '\r' || command.back() == '\n'))
        command.pop_back();
    std::size_t first = command.find_first_not_of(" \t\r\n");
    if (first == std::string::npos) return "empty command";
    if (first) command.erase(0, first);

    if (command == "help" || command == "?") {
        return "help | seq | map | gsw <n> [value] | gsw(<n>)[=<value>] | story [value] | warp <map> | perf";
    }
    if (command == "seq") {
        std::uint32_t seq = 0, stage = 0;
        if (!ReadGuestU32BE(runtime, R8PE01_SEQ_WORK, &seq) ||
            !ReadGuestU32BE(runtime, R8PE01_SEQ_WORK + 4u, &stage))
            return "could not read R8PE01 seqWork";
        std::snprintf(response, sizeof(response), "seq=%u stage=%u tick=%llu",
                      seq, stage, static_cast<unsigned long long>(context->tick_count.load(std::memory_order_relaxed)));
        return response;
    }
    if (command == "map") {
        std::uint32_t gp = 0;
        char map[64]{};
        char door[64]{};
        if (!ResolveSpmarioGlobals(runtime, &gp) ||
            !ReadGuestCString(runtime, gp + 0x44u, map, sizeof(map)) ||
            !ReadGuestCString(runtime, gp + 0x64u, door, sizeof(door)))
            return "could not read current R8PE01 map/door";
        std::snprintf(response, sizeof(response), "map=%s door=%s", map, door);
        return response;
    }
    if (command == "perf") {
        const moderngekko::RuntimePerformanceSnapshot p = runtime->GetPerformanceSnapshot();
        std::snprintf(response, sizeof(response),
                      "fps=%.1f vps=%.1f speed=%.1f%% frame=%llu present=%llu",
                      p.fps, p.vps, p.speed * 100.0,
                      static_cast<unsigned long long>(p.frame_count),
                      static_cast<unsigned long long>(p.present_count));
        return response;
    }
    if (std::sscanf(command.c_str(), "warp %127s %127s%n", token, extra, &consumed) >= 2) {
        return "door-specific warp is not implemented in the development console yet; use: warp <map>";
    }
    if (std::sscanf(command.c_str(), "warp %127s%n", token, &consumed) == 1 &&
        static_cast<std::size_t>(consumed) == command.size()) {
        const std::uint32_t map_name = ResolveMapNamePointer(runtime, token);
        if (!map_name) return std::string("map not found in R8PE01 mapData: ") + token;
        if (!WriteGuestU32BE(runtime, R8PE01_NEXT_P0, map_name) ||
            !WriteGuestU32BE(runtime, R8PE01_NEXT_P1, 0u) ||
            !WriteGuestU32BE(runtime, R8PE01_NEXT_SEQ, R8PE01_SEQ_MAPCHANGE))
            return "could not queue semantic R8PE01 map change";
        return std::string("queued map change to ") + token;
    }

    bool story_alias = false;
    if (command.rfind("story", 0) == 0) {
        story_alias = true;
        std::string rest = command.substr(5);
        command = "gsw 0" + rest;
    }

    bool have_index = false;
    bool have_value = false;
    if (std::sscanf(command.c_str(), "gsw(%d)=%d%n", &index, &value, &consumed) == 2 &&
        static_cast<std::size_t>(consumed) == command.size()) {
        have_index = true; have_value = true;
    } else if (std::sscanf(command.c_str(), "gsw(%d) %d%n", &index, &value, &consumed) == 2 &&
               static_cast<std::size_t>(consumed) == command.size()) {
        have_index = true; have_value = true;
    } else if (std::sscanf(command.c_str(), "gsw(%d)%n", &index, &consumed) == 1 &&
               static_cast<std::size_t>(consumed) == command.size()) {
        have_index = true;
    } else if (std::sscanf(command.c_str(), "gsw %d %d%n", &index, &value, &consumed) == 2 &&
               static_cast<std::size_t>(consumed) == command.size()) {
        have_index = true; have_value = true;
    } else if (std::sscanf(command.c_str(), "gsw %d%n", &index, &consumed) == 1 &&
               static_cast<std::size_t>(consumed) == command.size()) {
        have_index = true;
    }

    if (have_index) {
        std::uint32_t gp = 0;
        if (index < 0 || index >= 2048) return "GSW index must be 0..2047";
        if (!ResolveSpmarioGlobals(runtime, &gp)) return "could not resolve R8PE01 SpmarioGlobals";
        if (index == 0) {
            if (have_value && !WriteGuestU32BE(runtime, gp + 0x140u, static_cast<std::uint32_t>(value)))
                return "could not write GSW(0)";
            std::uint32_t current = 0;
            if (!ReadGuestU32BE(runtime, gp + 0x140u, &current)) return "could not read GSW(0)";
            std::snprintf(response, sizeof(response), "%sGSW(0)=%d",
                          story_alias ? "story / " : "", static_cast<std::int32_t>(current));
            return response;
        }
        if (have_value) {
            if (value < -128 || value > 127) return "GSW(n>0) value must fit signed byte (-128..127)";
            if (!WriteGuestU8(runtime, gp + 0x544u + static_cast<std::uint32_t>(index),
                              static_cast<std::uint8_t>(static_cast<std::int8_t>(value))))
                return "could not write GSW byte";
        }
        std::uint8_t current = 0;
        if (!ReadGuestU8(runtime, gp + 0x544u + static_cast<std::uint32_t>(index), &current))
            return "could not read GSW byte";
        std::snprintf(response, sizeof(response), "GSW(%d)=%d", index,
                      static_cast<int>(static_cast<std::int8_t>(current)));
        return response;
    }

    return std::string("unknown debug command: ") + raw + " (type help)";
}

void AdvanceDebugMailbox(BackendContext* context) {
    if (!context || !context->debug_mailbox) return;
    const LONG generation = InterlockedCompareExchange(
        reinterpret_cast<volatile LONG*>(&context->debug_mailbox->command_generation), 0, 0);
    if (generation <= 0 || generation == context->debug_processed_generation)
        return;

    char command[OPENSPM_DEBUG_COMMAND_BYTES]{};
    strncpy_s(command, sizeof(command), context->debug_mailbox->command, _TRUNCATE);
    const std::string response = ExecuteDebugCommand(context, command);
    strncpy_s(context->debug_mailbox->response, sizeof(context->debug_mailbox->response),
              response.c_str(), _TRUNCATE);
    MemoryBarrier();
    InterlockedExchange(reinterpret_cast<volatile LONG*>(&context->debug_mailbox->response_generation), generation);
    context->debug_processed_generation = generation;
    Log(OPENSPM_LOG_DEBUG, std::string("debug command: ") + command + " -> " + response);
}

void ConfigureDebugBoot(BackendContext* context, const openspm_launch_desc* launch) {
''',
    "backend live debug mailbox and commands",
)

replace_once(
    backend,
    """    AdvanceDebugBoot(context);
    openspm_runtime_tick_event event{};
""",
    """    AdvanceDebugMailbox(context);
    AdvanceDebugBoot(context);
    openspm_runtime_tick_event event{};
""",
    "backend execute command on safe tick",
)

replace_once(
    backend,
    """    {
        std::lock_guard lock(context->mutex);
        context->active_runtime = runtime.get();
    }

    Log(OPENSPM_LOG_INFO, "Entering in-process ModernGekko runtime with live services, true SPM ticks and startup-profile map routing.");
    const moderngekko::RuntimeRunResult result = runtime->Run();

    {
        std::lock_guard lock(context->mutex);
        context->active_runtime = nullptr;
    }
""",
    """    {
        std::lock_guard lock(context->mutex);
        context->active_runtime = runtime.get();
    }
    OpenDebugMailbox(context);

    Log(OPENSPM_LOG_INFO, "Entering in-process ModernGekko runtime with live services, true SPM ticks, startup-profile routing and live debug commands.");
    const moderngekko::RuntimeRunResult result = runtime->Run();

    CloseDebugMailbox(context);
    {
        std::lock_guard lock(context->mutex);
        context->active_runtime = nullptr;
    }
""",
    "backend debug mailbox runtime lifetime",
)

replace_once(
    backend,
    '    out_api->backend_version = "13.5-phase5-directboot-us2";\n',
    '    out_api->backend_version = "13.6-phase5-live-debug";\n',
    "backend live debug version",
)

print("M13 phase5: live development debug console staged")
