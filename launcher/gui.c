#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <commctrl.h>
#include <shlobj.h>
#include <shellapi.h>

#include "config.h"
#include "gui.h"

#include <stdlib.h>
#include <string.h>
#include <wchar.h>

#define IDC_GAME_PATH       1001
#define IDC_BROWSE_GAME     1002
#define IDC_BOOT_TARGET     1003
#define IDC_MODS_MASTER     1004
#define IDC_MOD_LIST        1005
#define IDC_REFRESH_MODS    1006
#define IDC_OPEN_MODS       1007
#define IDC_DIAGNOSTICS     1008
#define IDC_PLAY            1009
#define IDC_STATUS          1010

static const wchar_t *k_window_class = L"OpenSPM.M13.Launcher";

typedef struct boot_target_entry {
    const wchar_t *label;
    const wchar_t *id;
} boot_target_entry;

static const boot_target_entry k_boot_targets[] = {
    { L"Normal boot", L"normal" },
    { L"Mario's House (debug target)", L"mario_house" },
    { L"Flipside Shop (debug target)", L"flipside_shop" },
    { L"Bowser Minion Stress Test (debug target)", L"bowser_minions" }
};

typedef struct gui_state {
    wchar_t *app_root;
    openspm_config config;
    HWND window;
    HWND game_path;
    HWND boot_target;
    HWND mods_master;
    HWND mod_list;
    HWND diagnostics;
    HWND status;
} gui_state;

static wchar_t *join_path(const wchar_t *left, const wchar_t *right) {
    size_t a = wcslen(left), b = wcslen(right);
    int slash = a > 0 && left[a - 1] != L'\\' && left[a - 1] != L'/';
    wchar_t *out = (wchar_t *)calloc(a + b + (size_t)slash + 1, sizeof(wchar_t));
    if (!out) return NULL;
    wcscpy_s(out, a + b + (size_t)slash + 1, left);
    if (slash) wcscat_s(out, a + b + (size_t)slash + 1, L"\\");
    wcscat_s(out, a + b + (size_t)slash + 1, right);
    return out;
}

static int replace_owned(wchar_t **target, const wchar_t *value) {
    wchar_t *copy = value && *value ? _wcsdup(value) : NULL;
    if (value && *value && !copy) return 0;
    free(*target);
    *target = copy;
    return 1;
}

static int file_exists(const wchar_t *path) {
    DWORD attrs = GetFileAttributesW(path);
    return attrs != INVALID_FILE_ATTRIBUTES && !(attrs & FILE_ATTRIBUTE_DIRECTORY);
}

static void set_font(HWND control) {
    SendMessageW(control, WM_SETFONT, (WPARAM)GetStockObject(DEFAULT_GUI_FONT), TRUE);
}

static void set_status(gui_state *state, const wchar_t *text) {
    if (state && state->status) SetWindowTextW(state->status, text ? text : L"");
}

static void browse_game(gui_state *state) {
    BROWSEINFOW bi;
    PIDLIST_ABSOLUTE pidl;
    wchar_t path[MAX_PATH];
    memset(&bi, 0, sizeof(bi));
    bi.hwndOwner = state->window;
    bi.lpszTitle = L"Select the extracted R8PE01 game folder";
    bi.ulFlags = BIF_RETURNONLYFSDIRS | BIF_NEWDIALOGSTYLE;
    pidl = SHBrowseForFolderW(&bi);
    if (!pidl) return;
    if (SHGetPathFromIDListW(pidl, path)) SetWindowTextW(state->game_path, path);
    CoTaskMemFree(pidl);
}

static void clear_mod_list(gui_state *state) {
    ListView_DeleteAllItems(state->mod_list);
}

static void refresh_mods(gui_state *state) {
    wchar_t *mods_dir = join_path(state->app_root, L"mods");
    wchar_t *pattern = NULL;
    WIN32_FIND_DATAW data;
    HANDLE find = INVALID_HANDLE_VALUE;
    int row = 0;
    if (!mods_dir) return;
    CreateDirectoryW(mods_dir, NULL);
    pattern = join_path(mods_dir, L"*.dll");
    if (!pattern) { free(mods_dir); return; }
    clear_mod_list(state);
    find = FindFirstFileW(pattern, &data);
    if (find != INVALID_HANDLE_VALUE) {
        do {
            LVITEMW item;
            int enabled;
            if (data.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) continue;
            memset(&item, 0, sizeof(item));
            item.mask = LVIF_TEXT;
            item.iItem = row;
            item.pszText = data.cFileName;
            if (ListView_InsertItem(state->mod_list, &item) >= 0) {
                enabled = openspm_config_mod_enabled(state->app_root, data.cFileName, 1);
                ListView_SetCheckState(state->mod_list, row, enabled ? TRUE : FALSE);
                ++row;
            }
        } while (FindNextFileW(find, &data));
        FindClose(find);
    }
    free(pattern);
    free(mods_dir);
}

static const wchar_t *selected_boot_target(gui_state *state) {
    LRESULT selected = SendMessageW(state->boot_target, CB_GETCURSEL, 0, 0);
    if (selected == CB_ERR || selected < 0 || selected >= (LRESULT)_countof(k_boot_targets)) return L"normal";
    return k_boot_targets[selected].id;
}

static void select_configured_boot_target(gui_state *state) {
    size_t i;
    const wchar_t *wanted = state->config.boot_target ? state->config.boot_target : L"normal";
    int selected = 0;
    for (i = 0; i < _countof(k_boot_targets); ++i) {
        SendMessageW(state->boot_target, CB_ADDSTRING, 0, (LPARAM)k_boot_targets[i].label);
        if (_wcsicmp(wanted, k_boot_targets[i].id) == 0) selected = (int)i;
    }
    SendMessageW(state->boot_target, CB_SETCURSEL, selected, 0);
}

static int save_gui_config(gui_state *state) {
    wchar_t game[32768];
    const wchar_t *target = selected_boot_target(state);
    int i, count;
    game[0] = L'\0';
    GetWindowTextW(state->game_path, game, (int)_countof(game));
    if (!*game) {
        MessageBoxW(state->window, L"Choose an extracted R8PE01 game folder first.", L"OpenSPM", MB_OK | MB_ICONWARNING);
        return 0;
    }
    {
        wchar_t *dol = join_path(game, L"sys\\main.dol");
        int valid = dol && file_exists(dol);
        free(dol);
        if (!valid) {
            MessageBoxW(state->window, L"That folder does not contain sys\\main.dol.", L"OpenSPM", MB_OK | MB_ICONERROR);
            return 0;
        }
    }
    if (!replace_owned(&state->config.game_root, game) ||
        !replace_owned(&state->config.boot_target, target) ||
        !replace_owned(&state->config.profile_id, _wcsicmp(target, L"normal") == 0 ? L"default" : L"debug")) {
        MessageBoxW(state->window, L"Could not update launcher configuration.", L"OpenSPM", MB_OK | MB_ICONERROR);
        return 0;
    }
    state->config.mods_enabled = Button_GetCheck(state->mods_master) == BST_CHECKED;
    if (!state->config.graphics && !replace_owned(&state->config.graphics, L"OGL")) return 0;
    if (!openspm_config_save(state->app_root, &state->config)) {
        MessageBoxW(state->window, L"Could not save OpenSPM.ini.", L"OpenSPM", MB_OK | MB_ICONERROR);
        return 0;
    }
    count = ListView_GetItemCount(state->mod_list);
    for (i = 0; i < count; ++i) {
        wchar_t filename[MAX_PATH];
        filename[0] = L'\0';
        ListView_GetItemText(state->mod_list, i, 0, filename, (int)_countof(filename));
        if (*filename) openspm_config_set_mod_enabled(state->app_root, filename, ListView_GetCheckState(state->mod_list, i) != 0);
    }
    return 1;
}

static void launch_game(gui_state *state) {
    wchar_t *exe;
    wchar_t *command;
    PROCESS_INFORMATION process;
    STARTUPINFOW startup;
    DWORD flags = 0;
    size_t chars;
    int diagnostics;
    if (!save_gui_config(state)) return;
    exe = join_path(state->app_root, L"OpenSPM.exe");
    if (!exe) return;
    chars = wcslen(exe) + 64;
    command = (wchar_t *)calloc(chars, sizeof(wchar_t));
    if (!command) { free(exe); return; }
    _snwprintf_s(command, chars, _TRUNCATE, L"\"%ls\" --launch", exe);
    diagnostics = Button_GetCheck(state->diagnostics) == BST_CHECKED;
    if (diagnostics) flags |= CREATE_NEW_CONSOLE;
    memset(&startup, 0, sizeof(startup));
    memset(&process, 0, sizeof(process));
    startup.cb = sizeof(startup);
    if (!CreateProcessW(exe, command, NULL, NULL, FALSE, flags, NULL, state->app_root, &startup, &process)) {
        wchar_t message[256];
        _snwprintf_s(message, _countof(message), _TRUNCATE, L"Could not launch OpenSPM (Win32 error %lu).", GetLastError());
        MessageBoxW(state->window, message, L"OpenSPM", MB_OK | MB_ICONERROR);
        set_status(state, L"Launch failed.");
    } else {
        CloseHandle(process.hThread);
        CloseHandle(process.hProcess);
        if (_wcsicmp(selected_boot_target(state), L"normal") == 0)
            set_status(state, L"Game launched.");
        else
            set_status(state, L"Debug boot target queued. The legacy backend will ignore it until the in-process game service lands.");
    }
    free(command);
    free(exe);
}

static void open_mods_folder(gui_state *state) {
    wchar_t *mods_dir = join_path(state->app_root, L"mods");
    if (!mods_dir) return;
    CreateDirectoryW(mods_dir, NULL);
    ShellExecuteW(state->window, L"open", mods_dir, NULL, NULL, SW_SHOWNORMAL);
    free(mods_dir);
}

static HWND make_control(DWORD ex_style, const wchar_t *cls, const wchar_t *text, DWORD style,
                         int x, int y, int w, int h, HWND parent, int id) {
    HWND control = CreateWindowExW(ex_style, cls, text, style,
                                   x, y, w, h, parent, (HMENU)(INT_PTR)id,
                                   GetModuleHandleW(NULL), NULL);
    if (control) set_font(control);
    return control;
}

static void build_ui(gui_state *state) {
    LVCOLUMNW column;
    HWND label;
    label = make_control(0, L"STATIC", L"Super Paper Mario game folder", WS_CHILD | WS_VISIBLE,
                         20, 18, 260, 20, state->window, 0);
    (void)label;
    state->game_path = make_control(WS_EX_CLIENTEDGE, L"EDIT", state->config.game_root ? state->config.game_root : L"",
                                    WS_CHILD | WS_VISIBLE | ES_AUTOHSCROLL,
                                    20, 42, 570, 25, state->window, IDC_GAME_PATH);
    make_control(0, L"BUTTON", L"Browse...", WS_CHILD | WS_VISIBLE | BS_PUSHBUTTON,
                 600, 41, 88, 27, state->window, IDC_BROWSE_GAME);

    make_control(0, L"STATIC", L"Startup target", WS_CHILD | WS_VISIBLE,
                 20, 83, 180, 20, state->window, 0);
    state->boot_target = make_control(0, L"COMBOBOX", L"", WS_CHILD | WS_VISIBLE | CBS_DROPDOWNLIST | WS_VSCROLL,
                                      20, 105, 350, 300, state->window, IDC_BOOT_TARGET);
    select_configured_boot_target(state);
    make_control(0, L"STATIC",
                 L"Debug targets are persisted now; actual scene-direct boot activates with the in-process game service.",
                 WS_CHILD | WS_VISIBLE,
                 390, 102, 298, 45, state->window, 0);

    state->mods_master = make_control(0, L"BUTTON", L"Enable OpenSPM mods",
                                      WS_CHILD | WS_VISIBLE | BS_AUTOCHECKBOX,
                                      20, 154, 220, 24, state->window, IDC_MODS_MASTER);
    Button_SetCheck(state->mods_master, state->config.mods_enabled ? BST_CHECKED : BST_UNCHECKED);

    state->mod_list = make_control(WS_EX_CLIENTEDGE, WC_LISTVIEWW, L"",
                                   WS_CHILD | WS_VISIBLE | LVS_REPORT | LVS_SINGLESEL | LVS_SHOWSELALWAYS,
                                   20, 184, 668, 190, state->window, IDC_MOD_LIST);
    ListView_SetExtendedListViewStyle(state->mod_list, LVS_EX_CHECKBOXES | LVS_EX_FULLROWSELECT | LVS_EX_DOUBLEBUFFER);
    memset(&column, 0, sizeof(column));
    column.mask = LVCF_TEXT | LVCF_WIDTH;
    column.pszText = L"Mod DLL";
    column.cx = 630;
    ListView_InsertColumn(state->mod_list, 0, &column);
    refresh_mods(state);

    make_control(0, L"BUTTON", L"Refresh", WS_CHILD | WS_VISIBLE | BS_PUSHBUTTON,
                 20, 383, 90, 28, state->window, IDC_REFRESH_MODS);
    make_control(0, L"BUTTON", L"Open mods folder", WS_CHILD | WS_VISIBLE | BS_PUSHBUTTON,
                 120, 383, 135, 28, state->window, IDC_OPEN_MODS);

    state->diagnostics = make_control(0, L"BUTTON", L"Show diagnostics console",
                                      WS_CHILD | WS_VISIBLE | BS_AUTOCHECKBOX,
                                      20, 430, 240, 24, state->window, IDC_DIAGNOSTICS);

    state->status = make_control(0, L"STATIC", L"Ready.", WS_CHILD | WS_VISIBLE,
                                 20, 470, 500, 42, state->window, IDC_STATUS);
    make_control(0, L"BUTTON", L"Play", WS_CHILD | WS_VISIBLE | BS_DEFPUSHBUTTON,
                 548, 455, 140, 48, state->window, IDC_PLAY);
}

static LRESULT CALLBACK launcher_wndproc(HWND window, UINT message, WPARAM wparam, LPARAM lparam) {
    gui_state *state = (gui_state *)GetWindowLongPtrW(window, GWLP_USERDATA);
    if (message == WM_NCCREATE) {
        CREATESTRUCTW *create = (CREATESTRUCTW *)lparam;
        state = (gui_state *)create->lpCreateParams;
        SetWindowLongPtrW(window, GWLP_USERDATA, (LONG_PTR)state);
        state->window = window;
    }
    if (!state) return DefWindowProcW(window, message, wparam, lparam);
    switch (message) {
        case WM_CREATE:
            build_ui(state);
            return 0;
        case WM_COMMAND:
            switch (LOWORD(wparam)) {
                case IDC_BROWSE_GAME: browse_game(state); return 0;
                case IDC_REFRESH_MODS: refresh_mods(state); return 0;
                case IDC_OPEN_MODS: open_mods_folder(state); return 0;
                case IDC_PLAY: launch_game(state); return 0;
                default: break;
            }
            break;
        case WM_CLOSE:
            DestroyWindow(window);
            return 0;
        case WM_DESTROY:
            PostQuitMessage(0);
            return 0;
        default:
            break;
    }
    return DefWindowProcW(window, message, wparam, lparam);
}

int openspm_gui_run(const wchar_t *app_root) {
    INITCOMMONCONTROLSEX controls;
    WNDCLASSEXW wc;
    gui_state state;
    MSG message;
    int result = 0;
    if (!app_root) return 1;
    memset(&state, 0, sizeof(state));
    openspm_config_init(&state.config);
    state.app_root = _wcsdup(app_root);
    if (!state.app_root) return 1;
    openspm_config_load(app_root, &state.config);

    CoInitializeEx(NULL, COINIT_APARTMENTTHREADED);
    memset(&controls, 0, sizeof(controls));
    controls.dwSize = sizeof(controls);
    controls.dwICC = ICC_LISTVIEW_CLASSES | ICC_STANDARD_CLASSES;
    InitCommonControlsEx(&controls);

    memset(&wc, 0, sizeof(wc));
    wc.cbSize = sizeof(wc);
    wc.lpfnWndProc = launcher_wndproc;
    wc.hInstance = GetModuleHandleW(NULL);
    wc.hCursor = LoadCursorW(NULL, IDC_ARROW);
    wc.hIcon = LoadIconW(NULL, IDI_APPLICATION);
    wc.hbrBackground = (HBRUSH)(COLOR_WINDOW + 1);
    wc.lpszClassName = k_window_class;
    if (!RegisterClassExW(&wc) && GetLastError() != ERROR_CLASS_ALREADY_EXISTS) {
        openspm_config_free(&state.config);
        free(state.app_root);
        CoUninitialize();
        return 1;
    }

    state.window = CreateWindowExW(0, k_window_class, L"OpenSPM Launcher - M13 Phase 3",
                                   WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX,
                                   CW_USEDEFAULT, CW_USEDEFAULT, 724, 560,
                                   NULL, NULL, GetModuleHandleW(NULL), &state);
    if (!state.window) {
        openspm_config_free(&state.config);
        free(state.app_root);
        CoUninitialize();
        return 1;
    }
    ShowWindow(state.window, SW_SHOW);
    UpdateWindow(state.window);
    while (GetMessageW(&message, NULL, 0, 0) > 0) {
        TranslateMessage(&message);
        DispatchMessageW(&message);
    }
    result = (int)message.wParam;
    openspm_config_free(&state.config);
    free(state.app_root);
    CoUninitialize();
    return result;
}
