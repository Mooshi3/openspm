/*
 * M14 dev26 NAND hotfix: banner.bin creation is idempotent.
 *
 * Exact R8PE01 uses NAND_CODE_EXISTS while walking the wiimario slot-create
 * loop, but banner.bin is immediately refreshed from authoritative guest
 * bytes. Returning EXISTS for an already-present banner sends nandmgr back
 * around the create path forever.
 *
 * Keep dev26's public ABI and SDK-level NAND transport completely intact.
 * During this translation unit only, interpose CreateFileW so CREATE_NEW on an
 * already-existing banner.bin reopens the file and therefore makes
 * v12_create_leaf() report success. Every other Win32 file operation is passed
 * through unchanged. This avoids wrapping/redefining openspm_backend_query_v10
 * and keeps the hotfix below the guest NAND state machine.
 */

#include <windows.h>
#include <wchar.h>

static int openspm_v13_is_banner_path(const wchar_t *path) {
    static const wchar_t suffix[] = L"banner.bin";
    size_t path_len;
    size_t suffix_len = (sizeof(suffix) / sizeof(suffix[0])) - 1u;
    if (path == NULL) return 0;
    path_len = wcslen(path);
    return path_len >= suffix_len &&
           wcscmp(path + path_len - suffix_len, suffix) == 0;
}

static HANDLE WINAPI openspm_v13_CreateFileW(
    LPCWSTR file_name,
    DWORD desired_access,
    DWORD share_mode,
    LPSECURITY_ATTRIBUTES security_attributes,
    DWORD creation_disposition,
    DWORD flags_and_attributes,
    HANDLE template_file) {
    HANDLE file = CreateFileW(file_name, desired_access, share_mode,
                              security_attributes, creation_disposition,
                              flags_and_attributes, template_file);
    if (file == INVALID_HANDLE_VALUE &&
        creation_disposition == CREATE_NEW &&
        openspm_v13_is_banner_path(file_name)) {
        const DWORD error = GetLastError();
        if (error == ERROR_FILE_EXISTS || error == ERROR_ALREADY_EXISTS) {
            file = CreateFileW(file_name, desired_access, share_mode,
                               security_attributes, OPEN_EXISTING,
                               flags_and_attributes, template_file);
        }
    }
    return file;
}

#define CreateFileW openspm_v13_CreateFileW
#include "openspm_native_backend_v12.c"
#undef CreateFileW
