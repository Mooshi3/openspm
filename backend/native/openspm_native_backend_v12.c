/*
 * M14 standalone-native backend dev29 PC NAND/input + live experiment layer.
 *
 * Runtime proof through Chapter 1-1 exposed that dev25 hosted the wrong layer:
 * R8PE01/US2 0x8023CEF0 is nandWriteBannerLoadAllSaves(), not a platform
 * bootstrap. Hosting it suppressed boot-time save loading, while polling
 * NandWork at presentation safepoints could lose a race to the real NAND SDK
 * wrapper and produce NAND_CODE_FATAL (-128).
 *
 * dev26 keeps SPM's nandmgr state machine, task numbering, checksum validation
 * and callbacks guest-native. Only the actual RVL NAND SDK transport functions
 * are host exits, backed by files under User-OpenSPM/saves/R8PE01.
 *
 * dev27 also makes pressed-button delivery simulation-consumption-safe. The PC
 * keyboard is still sampled once per native 60 Hz presentation/retrace epoch,
 * but a newly pressed bit is no longer discarded merely because the next host
 * retrace arrives before SPM's slower simulation calls wpadGetButtonsPressed().
 * A pending edge remains frame-stable until that real guest getter observes it;
 * it is retired at the following safepoint, so one physical tap still produces
 * one guest edge rather than a duration-based synthetic hold.
 *
 * KPAD button fidelity from dev26 remains active: the PC D-pad exposes
 * buttonsReleased and the SDK repeat marker used by wpadGetButtonsHeldRepeat,
 * matching SPM's 0.375 s initial / 0.125 s repeat configuration.
 *
 * dev28 added the development-only safe-frame mailbox. dev29 extends that
 * transport into a reversible runtime experiment harness (room navigation,
 * player-state controls, NPC isolation and persistent memory freezes). These
 * controls remain private diagnostics and are not part of the public mod ABI.
 */

#define v10_run v10_run_base
#define v10_handle_hostcall v10_handle_hostcall_base
#define v10_query_service v10_query_service_base
#define openspm_backend_query_v10 openspm_backend_query_v10_base
#include "openspm_native_backend_v10.c"
#undef openspm_backend_query_v10

#include "openspm/debug_ipc.h"
#include "openspm_sysconf.h"

#include <math.h>
#include <stdarg.h>
#include <stdlib.h>
#undef v10_query_service
#undef v10_handle_hostcall
#undef v10_run

#define R8PE01_NAND_CREATE_ASYNC      UINT32_C(0x8029778C)
#define R8PE01_NAND_DELETE_ASYNC      UINT32_C(0x8029792C)
#define R8PE01_NAND_READ_ASYNC        UINT32_C(0x80297B2C)
#define R8PE01_NAND_WRITE_ASYNC       UINT32_C(0x80297C0C)
#define R8PE01_NAND_SEEK_ASYNC        UINT32_C(0x80297C84)
#define R8PE01_NAND_SAFE_OPEN_ASYNC   UINT32_C(0x8029868C)
#define R8PE01_NAND_SAFE_CLOSE_ASYNC  UINT32_C(0x80298BE8)
#define R8PE01_NAND_CHANGE_DIR_ASYNC  UINT32_C(0x80299B34)
#define R8PE01_NAND_GET_HOME_DIR      UINT32_C(0x80299D48)
#define R8PE01_NAND_CHECK_ASYNC       UINT32_C(0x8029A500)

/* RVL SC (system configuration) library, R8PE01/US2. SCInit/SCCheckStatus are
   hosted ("settings" in state/m14-host-targets.json), so the SC cache that
   SCInit would fill from /shared2/sys/SYSCONF stays empty unless the backend
   publishes one. SCGetAspectRatio (0x8029C144) = SCFindU8Item(0x8029B93C, id 1
   "IPL.AR"); its game callers pick the 16:9 camera extents (0x80054B10 ortho,
   0x80054D6C perspective, 0x801A7F88). The SC globals are r13-relative
   (r13 = 0x80574900). */
#define R8PE01_SC_INIT                UINT32_C(0x8029A7E0)
#define R8PE01_SC_CACHE               UINT32_C(0x80524360) /* 0x4000 bytes */
#define R8PE01_SC_FREE_BYTES          UINT32_C(0x8056DA1C) /* r13-28388 */
#define R8PE01_SC_ITEM_COUNT          UINT32_C(0x8056DA20) /* r13-28384 */
#define R8PE01_SC_KNOWN_IDS           UINT32_C(0x8056DA24) /* r13-28380 */
#define R8PE01_SC_INDEX_OFFSET        UINT32_C(0x8056DA28) /* r13-28376 */
#define R8PE01_SC_KNOWN_ID_COUNT      UINT32_C(35)
#define R8PE01_SC_ID_IPL_AR           UINT32_C(1)

#define OPENSPM_NAND_CODE_OK        INT32_C(0)
#define OPENSPM_NAND_CODE_EXISTS    INT32_C(-6)
#define OPENSPM_NAND_CODE_NOT_EXIST INT32_C(-12)
#define OPENSPM_NAND_CODE_FATAL     INT32_C(-128)
#define OPENSPM_NAND_SAVE_FILE_SIZE UINT32_C(0x25B8)

#define OPENSPM_WPAD_BTN_REPEAT UINT32_C(0x80000000)
#define OPENSPM_KPAD_REPEAT_INITIAL_FRAMES UINT32_C(23)
#define OPENSPM_KPAD_REPEAT_INTERVAL_FRAMES UINT32_C(8)
#define OPENSPM_V12_GX_INLINE_PUBLISH_INTERVAL UINT32_C(16384)

/* dev28 live-debug console: exact R8PE01/US2 semantic state. These remain
   development-only adapter facts, never public mod ABI. */
#define R8PE01_DEBUG_NEXT_SEQ       UINT32_C(0x8056CABC)
#define R8PE01_DEBUG_NEXT_P0        UINT32_C(0x8056D318)
#define R8PE01_DEBUG_NEXT_P1        UINT32_C(0x8056D31C)
#define R8PE01_DEBUG_MAP_DATA       UINT32_C(0x803C5098)
#define R8PE01_DEBUG_MAP_DATA_COUNT UINT32_C(0x1D4)
#define R8PE01_DEBUG_SEQ_GAME       UINT32_C(2)
#define R8PE01_DEBUG_SEQ_MAPCHANGE  UINT32_C(3)
#define R8PE01_DEBUG_GSW0_OFFSET    UINT32_C(0x140)
#define R8PE01_DEBUG_GSW_OFFSET     UINT32_C(0x544)
#define R8PE01_DEBUG_MAP_OFFSET     UINT32_C(0x44)
#define R8PE01_DEBUG_DOOR_OFFSET    UINT32_C(0x64)

/* Player-owned R8PE01/US2 work layouts documented by the public SPM headers
   and independently consistent with dev96's existing player-state probe. */
#define R8PE01_DEBUG_MARIO_WORK       UINT32_C(0x804CEE58)
#define R8PE01_DEBUG_MARIO_POUCH      UINT32_C(0x804D0428)
#define R8PE01_DEBUG_MARIO_MOTION     UINT32_C(0x002C)
#define R8PE01_DEBUG_MARIO_CHARACTER  UINT32_C(0x0038)
#define R8PE01_DEBUG_MARIO_POSITION   UINT32_C(0x005C)
#define R8PE01_DEBUG_MARIO_XZ_SPEED   UINT32_C(0x0148)
#define R8PE01_DEBUG_MARIO_WALK_SPEED UINT32_C(0x014C)
#define R8PE01_DEBUG_MARIO_DASH_SPEED UINT32_C(0x0150)
#define R8PE01_DEBUG_MARIO_HP         UINT32_C(0x000C)
#define R8PE01_DEBUG_MARIO_MAX_HP     UINT32_C(0x0010)

/* npcdrv_wp is an R8PE01/US2 pointer to NPCWork. NPCWork::flag bit 0 freezes
   NPC logic globally. NPCEntry::flag8 bit 0 is the documented active bit. */
#define R8PE01_DEBUG_NPCDRV_WP        UINT32_C(0x8056CB88)
#define R8PE01_DEBUG_NPC_WORK_FLAG    UINT32_C(0x0000)
#define R8PE01_DEBUG_NPC_WORK_NUM     UINT32_C(0x0004)
#define R8PE01_DEBUG_NPC_WORK_ENTRIES UINT32_C(0x0008)
#define R8PE01_DEBUG_NPC_ENTRY_SIZE   UINT32_C(0x0748)
#define R8PE01_DEBUG_NPC_ENTRY_ID     UINT32_C(0x0000)
#define R8PE01_DEBUG_NPC_ENTRY_FLAG8    UINT32_C(0x0008)
#define R8PE01_DEBUG_NPC_ENTRY_NAME     UINT32_C(0x0024)
#define R8PE01_DEBUG_NPC_ENTRY_POSITION UINT32_C(0x02A0)
#define R8PE01_DEBUG_NPC_ENTRY_FLAG46C  UINT32_C(0x046C)
#define R8PE01_DEBUG_NPC_ENTRY_TRIBE_ID UINT32_C(0x049C)
#define R8PE01_DEBUG_NPC_FREEZE_ALL     UINT32_C(0x00000001)
#define R8PE01_DEBUG_NPC_ACTIVE         UINT32_C(0x00000001)
/* Public NPCEntry layout: flag46C bit 31 freezes an entry while preserving
   its active/render identity; bit 17 hides and freezes it. These are safer
   live A/B controls than clearing the active bit out from under scripts. */
#define R8PE01_DEBUG_NPC_ENTRY_FROZEN        UINT32_C(0x80000000)
#define R8PE01_DEBUG_NPC_ENTRY_HIDDEN_FROZEN UINT32_C(0x00020000)
#define R8PE01_DEBUG_NPC_ENTRY_CONTROL_MASK  \
    (R8PE01_DEBUG_NPC_ENTRY_FROZEN | R8PE01_DEBUG_NPC_ENTRY_HIDDEN_FROZEN)

#define OPENSPM_DEBUG_FREEZE_SLOTS 64u
#define OPENSPM_DEBUG_NPC_SNAPSHOT_MAX 256u

typedef struct openspm_v12_nand_file_state {
    uint32_t guest_file_info;
    uint32_t offset;
    uint32_t open;
    wchar_t leaf[32];
} openspm_v12_nand_file_state;

static wchar_t *g_v12_save_root;
static openspm_v12_nand_file_state g_v12_nand_file;
static uint64_t g_v12_nand_sdk_calls;
static uint32_t g_v12_input_prev_down;
static uint32_t g_v12_input_released;
static uint32_t g_v12_input_repeat;
static uint32_t g_v12_input_hold_frames;
static uint32_t g_v12_input_observed_pressed;
static uint64_t g_v12_input_carried_frames;
static uint32_t g_v12_sticky_pressed_enabled;
static uint32_t g_v12_fifo_inline_enabled;
static uint32_t g_v12_fifo_since_publish;
static uint64_t g_v12_fifo_inline_publishes;

static HANDLE g_v12_debug_mapping;
static openspm_debug_mailbox *g_v12_debug_mailbox;
static LONG g_v12_debug_processed_generation;
static uint64_t g_v12_debug_commands;

typedef struct openspm_debug_freeze_slot {
    uint32_t address;
    uint32_t value;
    uint8_t width;
    uint8_t active;
} openspm_debug_freeze_slot;

typedef struct openspm_debug_npc_snapshot {
    uint32_t address;
    uint32_t id;
    uint32_t flag8;
    uint32_t flag46c;
    uint8_t valid;
} openspm_debug_npc_snapshot;

typedef struct openspm_debug_checkpoint {
    char map[32];
    float x;
    float y;
    float z;
    uint8_t valid;
} openspm_debug_checkpoint;

static openspm_debug_freeze_slot g_v12_debug_freezes[OPENSPM_DEBUG_FREEZE_SLOTS];
static openspm_debug_npc_snapshot g_v12_debug_npc_snapshot[OPENSPM_DEBUG_NPC_SNAPSHOT_MAX];
static uint32_t g_v12_debug_npc_snapshot_count;
static uint32_t g_v12_debug_npc_snapshot_entries;
static char g_v12_debug_npc_suspend_map[32];
static uint8_t g_v12_debug_npc_suspended;
static uint32_t g_v12_debug_npc_suspend_pulse_frames;

/* dev30 live NPC bisector: one independently tracked active-bit pulse.
 *
 * This is deliberately separate from the older all-NPC suspend/isolation
 * snapshot array so a single passive actor can be A/B tested repeatedly in one
 * running session. Only NPCEntry::flag8 bit 0 is modified/restored. */
static openspm_debug_npc_snapshot g_v12_debug_npc_one_pulse;
static uint32_t g_v12_debug_npc_one_pulse_entries;
static uint32_t g_v12_debug_npc_one_pulse_frames;
static uint32_t g_v12_debug_npc_one_pulse_active_ordinal;
static uint32_t g_v12_debug_npc_one_pulse_table_index;
static char g_v12_debug_npc_one_pulse_map[32];
static char g_v12_debug_npc_one_pulse_name[24];

static uint8_t g_v12_debug_npc_isolation_mode;
static uint32_t g_v12_debug_npc_isolation_keep;
static uint8_t g_v12_debug_speed_active;
static float g_v12_debug_speed_multiplier;
static float g_v12_debug_speed_walk_base;
static float g_v12_debug_speed_dash_base;
static uint8_t g_v12_debug_speed_character;
static openspm_debug_checkpoint g_v12_debug_checkpoint;

static int v12_is_directory(const wchar_t *path) {
    DWORD attr;
    if (path == NULL) return 0;
    attr = GetFileAttributesW(path);
    return attr != INVALID_FILE_ATTRIBUTES &&
           (attr & FILE_ATTRIBUTE_DIRECTORY) != 0u;
}

static int v12_ensure_directory(const wchar_t *path) {
    if (path == NULL || *path == L'\0') return 0;
    if (CreateDirectoryW(path, NULL)) return 1;
    return GetLastError() == ERROR_ALREADY_EXISTS && v12_is_directory(path);
}

static int v12_prepare_save_root(const openspm_launch_desc *launch) {
    char *saves_utf8 = NULL;
    char *game_utf8 = NULL;
    wchar_t *saves = NULL;
    wchar_t *game = NULL;
    int ok = 0;

    free(g_v12_save_root);
    g_v12_save_root = NULL;
    if (launch == NULL || launch->user_root_utf8 == NULL ||
        *launch->user_root_utf8 == '\0') return 0;

    saves_utf8 = join_utf8_path(launch->user_root_utf8, "saves");
    if (saves_utf8 == NULL) goto cleanup;
    game_utf8 = join_utf8_path(saves_utf8, OPENSPM_NATIVE_GAME_ID);
    if (game_utf8 == NULL) goto cleanup;
    saves = wide_from_utf8(saves_utf8);
    game = wide_from_utf8(game_utf8);
    if (saves == NULL || game == NULL) goto cleanup;
    if (!v12_ensure_directory(saves) || !v12_ensure_directory(game)) goto cleanup;
    g_v12_save_root = game;
    game = NULL;
    ok = 1;
cleanup:
    free(game);
    free(saves);
    free(game_utf8);
    free(saves_utf8);
    return ok;
}

static wchar_t *v12_save_path(const wchar_t *leaf) {
    size_t root_len;
    size_t leaf_len;
    wchar_t *path;
    if (g_v12_save_root == NULL || leaf == NULL || *leaf == L'\0') return NULL;
    root_len = wcslen(g_v12_save_root);
    leaf_len = wcslen(leaf);
    if (root_len > SIZE_MAX - leaf_len - 2u) return NULL;
    path = (wchar_t *)calloc(root_len + leaf_len + 2u, sizeof(*path));
    if (path == NULL) return NULL;
    memcpy(path, g_v12_save_root, root_len * sizeof(*path));
    if (root_len != 0u && path[root_len - 1u] != L'\\' &&
        path[root_len - 1u] != L'/') path[root_len++] = L'\\';
    memcpy(path + root_len, leaf, (leaf_len + 1u) * sizeof(*path));
    return path;
}

static int32_t v12_create_leaf(const wchar_t *leaf) {
    wchar_t *path = v12_save_path(leaf);
    HANDLE file;
    DWORD error;
    if (path == NULL) return OPENSPM_NAND_CODE_FATAL;
    file = CreateFileW(path, GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ,
                       NULL, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, NULL);
    if (file != INVALID_HANDLE_VALUE) {
        CloseHandle(file);
        free(path);
        return OPENSPM_NAND_CODE_OK;
    }
    error = GetLastError();
    free(path);
    if (error == ERROR_FILE_EXISTS || error == ERROR_ALREADY_EXISTS)
        return OPENSPM_NAND_CODE_EXISTS;
    return OPENSPM_NAND_CODE_FATAL;
}

static int v12_leaf_has_size(const wchar_t *leaf, uint32_t expected) {
    wchar_t *path = v12_save_path(leaf);
    HANDLE file = INVALID_HANDLE_VALUE;
    LARGE_INTEGER length;
    int match = 0;
    if (path == NULL) return 0;
    file = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING,
                       FILE_ATTRIBUTE_NORMAL, NULL);
    if (file != INVALID_HANDLE_VALUE && GetFileSizeEx(file, &length) &&
        length.QuadPart >= 0 && (uint64_t)length.QuadPart == expected) match = 1;
    if (file != INVALID_HANDLE_VALUE) CloseHandle(file);
    free(path);
    return match;
}

static int32_t v12_recreate_leaf(const wchar_t *leaf) {
    wchar_t *path = v12_save_path(leaf);
    HANDLE file;
    if (path == NULL) return OPENSPM_NAND_CODE_FATAL;
    file = CreateFileW(path, GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ,
                       NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    free(path);
    if (file == INVALID_HANDLE_VALUE) return OPENSPM_NAND_CODE_FATAL;
    CloseHandle(file);
    return OPENSPM_NAND_CODE_OK;
}

static int32_t v12_create_save_leaf(const wchar_t *leaf) {
    int32_t result = v12_create_leaf(leaf);
    if (result != OPENSPM_NAND_CODE_EXISTS) return result;
    if (v12_leaf_has_size(leaf, OPENSPM_NAND_SAVE_FILE_SIZE))
        return OPENSPM_NAND_CODE_EXISTS;
    /* Heal only zero/partial placeholders made by old dev25 builds. */
    return v12_recreate_leaf(leaf);
}

static int32_t v12_open_leaf(const wchar_t *leaf) {
    wchar_t *path = v12_save_path(leaf);
    DWORD attr;
    DWORD error;
    if (path == NULL) return OPENSPM_NAND_CODE_FATAL;
    attr = GetFileAttributesW(path);
    if (attr == INVALID_FILE_ATTRIBUTES) {
        error = GetLastError();
        free(path);
        return (error == ERROR_FILE_NOT_FOUND || error == ERROR_PATH_NOT_FOUND)
                   ? OPENSPM_NAND_CODE_NOT_EXIST : OPENSPM_NAND_CODE_FATAL;
    }
    free(path);
    return (attr & FILE_ATTRIBUTE_DIRECTORY) != 0u
               ? OPENSPM_NAND_CODE_FATAL : OPENSPM_NAND_CODE_OK;
}

static int32_t v12_delete_leaf(const wchar_t *leaf) {
    wchar_t *path = v12_save_path(leaf);
    DWORD error;
    if (path == NULL) return OPENSPM_NAND_CODE_FATAL;
    if (DeleteFileW(path)) {
        free(path);
        return OPENSPM_NAND_CODE_OK;
    }
    error = GetLastError();
    free(path);
    if (error == ERROR_FILE_NOT_FOUND || error == ERROR_PATH_NOT_FOUND)
        return OPENSPM_NAND_CODE_NOT_EXIST;
    return OPENSPM_NAND_CODE_FATAL;
}

static int32_t v12_write_leaf(const wchar_t *leaf, const uint8_t *bytes,
                              uint32_t size) {
    wchar_t *path = NULL;
    wchar_t *temp = NULL;
    HANDLE file = INVALID_HANDLE_VALUE;
    uint32_t offset = 0u;
    int32_t code = OPENSPM_NAND_CODE_FATAL;
    size_t path_len;
    if ((size != 0u && bytes == NULL) || g_v12_save_root == NULL) return code;
    path = v12_save_path(leaf);
    if (path == NULL) goto cleanup;
    path_len = wcslen(path);
    if (path_len > SIZE_MAX - 5u) goto cleanup;
    temp = (wchar_t *)calloc(path_len + 5u, sizeof(*temp));
    if (temp == NULL) goto cleanup;
    memcpy(temp, path, path_len * sizeof(*temp));
    memcpy(temp + path_len, L".tmp", 5u * sizeof(*temp));
    file = CreateFileW(temp, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS,
                       FILE_ATTRIBUTE_NORMAL | FILE_FLAG_SEQUENTIAL_SCAN, NULL);
    if (file == INVALID_HANDLE_VALUE) goto cleanup;
    while (offset < size) {
        DWORD written = 0u;
        DWORD wanted = size - offset;
        if (!WriteFile(file, bytes + offset, wanted, &written, NULL) || written == 0u)
            goto cleanup;
        offset += written;
    }
    if (!FlushFileBuffers(file)) goto cleanup;
    CloseHandle(file);
    file = INVALID_HANDLE_VALUE;
    if (!MoveFileExW(temp, path, MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH))
        goto cleanup;
    code = OPENSPM_NAND_CODE_OK;
cleanup:
    if (file != INVALID_HANDLE_VALUE) CloseHandle(file);
    if (code != OPENSPM_NAND_CODE_OK && temp != NULL) DeleteFileW(temp);
    free(temp);
    free(path);
    return code;
}

static int32_t v12_read_leaf(const wchar_t *leaf, uint8_t *bytes,
                             uint32_t capacity, uint32_t offset,
                             uint32_t *out_read) {
    wchar_t *path = NULL;
    HANDLE file = INVALID_HANDLE_VALUE;
    LARGE_INTEGER length;
    uint32_t available;
    uint32_t wanted;
    DWORD got = 0u;
    int32_t result = OPENSPM_NAND_CODE_FATAL;
    if (out_read != NULL) *out_read = 0u;
    if (leaf == NULL || (capacity != 0u && bytes == NULL)) return result;
    path = v12_save_path(leaf);
    if (path == NULL) goto cleanup;
    file = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING,
                       FILE_ATTRIBUTE_NORMAL | FILE_FLAG_SEQUENTIAL_SCAN, NULL);
    if (file == INVALID_HANDLE_VALUE) {
        DWORD error = GetLastError();
        result = (error == ERROR_FILE_NOT_FOUND || error == ERROR_PATH_NOT_FOUND)
                     ? OPENSPM_NAND_CODE_NOT_EXIST : OPENSPM_NAND_CODE_FATAL;
        goto cleanup;
    }
    if (!GetFileSizeEx(file, &length) || length.QuadPart < 0 ||
        (uint64_t)length.QuadPart > UINT32_MAX || offset > (uint32_t)length.QuadPart)
        goto cleanup;
    available = (uint32_t)length.QuadPart - offset;
    wanted = capacity < available ? capacity : available;
    if (offset != 0u) {
        DWORD pos = SetFilePointer(file, (LONG)offset, NULL, FILE_BEGIN);
        if (pos == INVALID_SET_FILE_POINTER && GetLastError() != NO_ERROR) goto cleanup;
    }
    if (wanted != 0u && (!ReadFile(file, bytes, wanted, &got, NULL) || got != wanted))
        goto cleanup;
    if (out_read != NULL) *out_read = got;
    result = OPENSPM_NAND_CODE_OK;
cleanup:
    if (file != INVALID_HANDLE_VALUE) CloseHandle(file);
    free(path);
    return result;
}

static int v12_guest_leaf(uint32_t address, wchar_t out_leaf[32]) {
    char path[64];
    const char *leaf;
    size_t i;
    if (out_leaf == NULL || address == 0u) return 0;
    v9_copy_guest_string(address, path, sizeof(path));
    if (path[0] == '\0') return 0;
    leaf = strrchr(path, '/');
    if (leaf == NULL) leaf = strrchr(path, '\\');
    leaf = leaf != NULL ? leaf + 1 : path;
    if (strcmp(leaf, "banner.bin") != 0 &&
        (strlen(leaf) != 10u || strncmp(leaf, "wiimario", 8u) != 0 ||
         leaf[8] < '0' || leaf[8] > '9' || leaf[9] < '0' || leaf[9] > '9'))
        return 0;
    for (i = 0u; i + 1u < 32u && leaf[i] != '\0'; ++i)
        out_leaf[i] = (wchar_t)(unsigned char)leaf[i];
    out_leaf[i] = L'\0';
    return i != 0u && leaf[i] == '\0';
}

static int v12_is_save_leaf(const wchar_t *leaf) {
    return leaf != NULL && wcslen(leaf) == 10u &&
           wcsncmp(leaf, L"wiimario", 8u) == 0;
}

static void v12_async_return(openspm_m14_hostcall_state *state,
                             uint32_t callback, uint32_t command_block,
                             int32_t callback_result) {
    state->gpr[3] = (uint32_t)OPENSPM_NAND_CODE_OK;
    state->pc = state->lr;
    state->event_target = callback;
    state->event_argc = callback != 0u ? 2u : 0u;
    memset(state->event_args, 0, sizeof(state->event_args));
    if (callback != 0u) {
        state->event_args[0] = (uint32_t)callback_result;
        state->event_args[1] = command_block;
    }
}

static void v12_log_nand(const char *operation, int32_t result,
                         const wchar_t *leaf) {
    char message[256];
    ++g_v12_nand_sdk_calls;
    if (g_v12_nand_sdk_calls > 24u &&
        (g_v12_nand_sdk_calls & (g_v12_nand_sdk_calls - 1u)) != 0u) return;
    _snprintf_s(message, sizeof(message), _TRUNCATE,
                "M14 native NAND SDK n=%llu op=%s result=%d%s%s.",
                (unsigned long long)g_v12_nand_sdk_calls,
                operation, result, leaf != NULL ? " file=" : "",
                leaf != NULL && wcscmp(leaf, L"banner.bin") == 0 ? "banner.bin" :
                    (leaf != NULL ? "wiimarioXX" : ""));
    backend_log(result == OPENSPM_NAND_CODE_FATAL ? OPENSPM_LOG_WARN : OPENSPM_LOG_INFO,
                message);
}

static int v12_handle_nand(uint32_t target, openspm_m14_hostcall_state *state) {
    wchar_t leaf[32];
    uint8_t *bytes;
    uint32_t callback;
    uint32_t block;
    uint32_t count = 0u;
    int32_t result = OPENSPM_NAND_CODE_OK;
    if (state == NULL || state->header.struct_size < OPENSPM_M14_HOSTCALL_STATE_V1_SIZE)
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    leaf[0] = L'\0';

    switch (target) {
    case R8PE01_NAND_GET_HOME_DIR: {
        static const char home[] = "/title/00010000/52385050/data";
        uint8_t *dest = (uint8_t *)v8_guest_ptr(state->gpr[3], (uint32_t)sizeof(home));
        if (dest == NULL) return OPENSPM_ERROR_NOT_FOUND;
        memcpy(dest, home, sizeof(home));
        state->gpr[3] = (uint32_t)OPENSPM_NAND_CODE_OK;
        state->pc = state->lr;
        v12_log_nand("get-home-dir", 0, NULL);
        return OPENSPM_OK;
    }
    case R8PE01_NAND_CHANGE_DIR_ASYNC:
        callback = state->gpr[4]; block = state->gpr[5];
        v12_async_return(state, callback, block, 0);
        v12_log_nand("change-dir", 0, NULL);
        return OPENSPM_OK;
    case R8PE01_NAND_CHECK_ASYNC:
        callback = state->gpr[6]; block = state->gpr[7];
        if (state->gpr[5] != 0u) (void)v8_write32(state->gpr[5], 0u);
        v12_async_return(state, callback, block, 0);
        v12_log_nand("check", 0, NULL);
        return OPENSPM_OK;
    case R8PE01_NAND_CREATE_ASYNC:
        callback = state->gpr[6]; block = state->gpr[7];
        if (!v12_guest_leaf(state->gpr[3], leaf)) result = OPENSPM_NAND_CODE_FATAL;
        else result = v12_is_save_leaf(leaf) ? v12_create_save_leaf(leaf)
                                             : v12_create_leaf(leaf);
        v12_async_return(state, callback, block, result);
        v12_log_nand("create", result, leaf[0] ? leaf : NULL);
        return OPENSPM_OK;
    case R8PE01_NAND_DELETE_ASYNC:
        callback = state->gpr[4]; block = state->gpr[5];
        if (!v12_guest_leaf(state->gpr[3], leaf)) result = OPENSPM_NAND_CODE_FATAL;
        else result = v12_delete_leaf(leaf);
        v12_async_return(state, callback, block, result);
        v12_log_nand("delete", result, leaf[0] ? leaf : NULL);
        return OPENSPM_OK;
    case R8PE01_NAND_SAFE_OPEN_ASYNC:
        callback = state->gpr[8]; block = state->gpr[9];
        if (!v12_guest_leaf(state->gpr[3], leaf)) result = OPENSPM_NAND_CODE_FATAL;
        else result = v12_open_leaf(leaf);
        if (result == OPENSPM_NAND_CODE_OK) {
            memset(&g_v12_nand_file, 0, sizeof(g_v12_nand_file));
            g_v12_nand_file.guest_file_info = state->gpr[4];
            g_v12_nand_file.open = 1u;
            wcsncpy_s(g_v12_nand_file.leaf, 32u, leaf, _TRUNCATE);
        }
        v12_async_return(state, callback, block, result);
        v12_log_nand("open", result, leaf[0] ? leaf : NULL);
        return OPENSPM_OK;
    case R8PE01_NAND_SAFE_CLOSE_ASYNC:
        callback = state->gpr[4]; block = state->gpr[5];
        if (!g_v12_nand_file.open || g_v12_nand_file.guest_file_info != state->gpr[3])
            result = OPENSPM_NAND_CODE_FATAL;
        else {
            wcscpy_s(leaf, 32u, g_v12_nand_file.leaf);
            memset(&g_v12_nand_file, 0, sizeof(g_v12_nand_file));
        }
        v12_async_return(state, callback, block, result);
        v12_log_nand("close", result, leaf[0] ? leaf : NULL);
        return OPENSPM_OK;
    case R8PE01_NAND_READ_ASYNC:
        callback = state->gpr[6]; block = state->gpr[7];
        if (!g_v12_nand_file.open || g_v12_nand_file.guest_file_info != state->gpr[3])
            result = OPENSPM_NAND_CODE_FATAL;
        else {
            bytes = (uint8_t *)v8_guest_ptr(state->gpr[4], state->gpr[5]);
            if (bytes == NULL && state->gpr[5] != 0u) result = OPENSPM_NAND_CODE_FATAL;
            else result = v12_read_leaf(g_v12_nand_file.leaf, bytes, state->gpr[5],
                                        g_v12_nand_file.offset, &count);
            if (result == 0) g_v12_nand_file.offset += count;
            wcscpy_s(leaf, 32u, g_v12_nand_file.leaf);
        }
        v12_async_return(state, callback, block, result == 0 ? (int32_t)count : result);
        v12_log_nand("read", result, leaf[0] ? leaf : NULL);
        return OPENSPM_OK;
    case R8PE01_NAND_WRITE_ASYNC:
        callback = state->gpr[6]; block = state->gpr[7]; count = state->gpr[5];
        if (!g_v12_nand_file.open || g_v12_nand_file.guest_file_info != state->gpr[3] ||
            g_v12_nand_file.offset != 0u) result = OPENSPM_NAND_CODE_FATAL;
        else {
            bytes = (uint8_t *)v8_guest_ptr(state->gpr[4], count);
            if (bytes == NULL && count != 0u) result = OPENSPM_NAND_CODE_FATAL;
            else result = v12_write_leaf(g_v12_nand_file.leaf, bytes, count);
            if (result == 0) g_v12_nand_file.offset += count;
            wcscpy_s(leaf, 32u, g_v12_nand_file.leaf);
        }
        v12_async_return(state, callback, block, result == 0 ? (int32_t)count : result);
        v12_log_nand("write", result, leaf[0] ? leaf : NULL);
        return OPENSPM_OK;
    case R8PE01_NAND_SEEK_ASYNC:
        callback = state->gpr[6]; block = state->gpr[7];
        if (!g_v12_nand_file.open || g_v12_nand_file.guest_file_info != state->gpr[3] ||
            state->gpr[5] != 0u) result = OPENSPM_NAND_CODE_FATAL;
        else {
            g_v12_nand_file.offset = state->gpr[4];
            count = g_v12_nand_file.offset;
            wcscpy_s(leaf, 32u, g_v12_nand_file.leaf);
        }
        v12_async_return(state, callback, block, result == 0 ? (int32_t)count : result);
        v12_log_nand("seek", result, leaf[0] ? leaf : NULL);
        return OPENSPM_OK;
    default:
        return OPENSPM_ERROR_NOT_FOUND;
    }
}

/* [Display] widescreen / OPENSPM_WIDESCREEN=1: the console's TV aspect
   setting (SYSCONF IPL.AR) reads 16:9, so the game renders its own anamorphic
   16:9 image (wider camera extents) and the renderer presents it at 16:9. */
static int v12_widescreen_requested(void) {
    const char *value = getenv("OPENSPM_WIDESCREEN");
    return value != NULL && value[0] == '1' && value[1] == '\0';
}

/* Runs after the hosted SCInit: fills the SC cache exactly as a completed SC
   load + parse leaves it (image, lookup index, globals). Only the items OpenSPM
   sets are present; every other SC getter keeps its not-found default. */
static void v12_publish_sysconf(void) {
    static const openspm_sc_item_id ids[] = {{"IPL.AR", R8PE01_SC_ID_IPL_AR}};
    openspm_sc_runtime rt;
    uint8_t *cache;
    if (!v12_widescreen_requested()) return;
    cache = (uint8_t *)v8_guest_ptr(R8PE01_SC_CACHE, OPENSPM_SC_BUFFER_SIZE);
    if (cache == NULL ||
        !openspm_sysconf_init_empty(cache, OPENSPM_SC_BUFFER_SIZE) ||
        !openspm_sysconf_set_u8(cache, OPENSPM_SC_BUFFER_SIZE, "IPL.AR", OPENSPM_SC_ASPECT_16_9) ||
        !openspm_sysconf_build_index(cache, OPENSPM_SC_BUFFER_SIZE, ids,
                                     (uint32_t)(sizeof(ids) / sizeof(ids[0])), &rt) ||
        !v8_write32(R8PE01_SC_FREE_BYTES, rt.free_bytes) ||
        !v8_write32(R8PE01_SC_ITEM_COUNT, rt.item_count) ||
        !v8_write32(R8PE01_SC_INDEX_OFFSET, rt.index_offset) ||
        !v8_write32(R8PE01_SC_KNOWN_IDS, R8PE01_SC_KNOWN_ID_COUNT)) {
        backend_log(OPENSPM_LOG_WARN,
                    "M14 SYSCONF: widescreen requested but the SC cache could not be published; the game stays 4:3.");
        return;
    }
    backend_log(OPENSPM_LOG_INFO,
                "M14 SYSCONF: IPL.AR=1 (16:9 TV) published to the SC cache (OPENSPM_WIDESCREEN=1); "
                "SCGetAspectRatio reports widescreen.");
}

static void v12_log_input_carry(uint32_t carried) {
    char message[192];
    if (carried == 0u) return;
    ++g_v12_input_carried_frames;
    if (g_v12_input_carried_frames > 16u &&
        (g_v12_input_carried_frames & (g_v12_input_carried_frames - 1u)) != 0u)
        return;
    _snprintf_s(message, sizeof(message), _TRUNCATE,
                "M14 dev29 pending input edge retained n=%llu bits=0x%08X until guest consumption.",
                (unsigned long long)g_v12_input_carried_frames,
                (unsigned)carried);
    backend_log(OPENSPM_LOG_INFO, message);
}

static void v12_latch_menu_input(void) {
    uint32_t down = g_v9_input_down;
    uint32_t pressed = g_v9_input_pressed;
    g_v12_input_released = g_v12_input_prev_down & ~down;
    g_v12_input_repeat = 0u;
    if (down == 0u) g_v12_input_hold_frames = 0u;
    else if (down != g_v12_input_prev_down) g_v12_input_hold_frames = 1u;
    else if (g_v12_input_hold_frames != UINT32_MAX) ++g_v12_input_hold_frames;

    /* A real KPAD repeat stream marks the initial edge so a tap moves a menu
       once, then repeats after SPM's configured 0.375/0.125 second cadence. */
    if (pressed != 0u ||
        (down != 0u && g_v12_input_hold_frames >= OPENSPM_KPAD_REPEAT_INITIAL_FRAMES &&
         ((g_v12_input_hold_frames - OPENSPM_KPAD_REPEAT_INITIAL_FRAMES) %
          OPENSPM_KPAD_REPEAT_INTERVAL_FRAMES) == 0u))
        g_v12_input_repeat = OPENSPM_WPAD_BTN_REPEAT;
    g_v12_input_prev_down = down;
}

#define OPENSPM_KPAD_ACC 0x0Cu
#define OPENSPM_KPAD_ACC_VALUE 0x18u
#define OPENSPM_KPAD_ACC_SPEED 0x1Cu
#define OPENSPM_V12_SHAKE_G 2.5f
static uint32_t g_v12_shake_logged;

static void v12_patch_kpad_status(openspm_m14_hostcall_state *state) {
    uint8_t *status;
    if (state == NULL || state->gpr[3] == 0u) return;
    status = (uint8_t *)v8_guest_ptr(state->gpr[4], OPENSPM_KPAD_STATUS_SIZE);
    if (status == NULL) return;
    v8_store_be32(status + OPENSPM_KPAD_BUTTONS_HELD,
                  g_v9_input_down | g_v12_input_repeat);
    v8_store_be32(status + OPENSPM_KPAD_BUTTONS_RELEASED, g_v12_input_released);
    if (g_v9_shake_frames != 0u && !g_v9_menu_open) {
        /* Shake: KPADStatus acc (+0x0C, 3 x f32 in g), acc_value (+0x18) and
           acc_speed (+0x1C). Mario's stylish-move check (0x8012DD74) averages
           acc over 5 frames of WpadWork's 60-frame history, so the swing is a
           slow one: 8 frames hard one way, 8 the other, gravity on z. */
        static float prev[3];
        const float s = (g_v9_shake_phase / 8u) % 2u ? -OPENSPM_V12_SHAKE_G : OPENSPM_V12_SHAKE_G;
        const float acc[3] = {s, s, 1.0f};
        float value = 0.0f, speed = 0.0f;
        unsigned i;
        for (i = 0; i < 3u; ++i) {
            uint32_t bits;
            value += acc[i] * acc[i];
            speed += (acc[i] - prev[i]) * (acc[i] - prev[i]);
            prev[i] = acc[i];
            memcpy(&bits, &acc[i], sizeof bits);
            v8_store_be32(status + OPENSPM_KPAD_ACC + 4u * i, bits);
        }
        value = sqrtf(value);
        speed = sqrtf(speed);
        {
            uint32_t bits;
            memcpy(&bits, &value, sizeof bits);
            v8_store_be32(status + OPENSPM_KPAD_ACC_VALUE, bits);
            memcpy(&bits, &speed, sizeof bits);
            v8_store_be32(status + OPENSPM_KPAD_ACC_SPEED, bits);
        }
        if (!g_v12_shake_logged) {
            g_v12_shake_logged = 1u;
            backend_log(OPENSPM_LOG_INFO, "OpenSPM input: Wii Remote shake (accelerometer swing into KPAD status).");
        }
    }
}


static int v12_debug_read8(uint32_t address, uint8_t *out_value) {
    const uint8_t *p = (const uint8_t *)v8_guest_ptr(address, 1u);
    if (p == NULL || out_value == NULL) return 0;
    *out_value = *p;
    return 1;
}

static int v12_debug_write8(uint32_t address, uint8_t value) {
    uint8_t *p = (uint8_t *)v8_guest_ptr(address, 1u);
    if (p == NULL) return 0;
    *p = value;
    return 1;
}

static int v12_debug_read_cstring(uint32_t address, char *out, size_t capacity) {
    size_t i;
    if (out == NULL || capacity < 2u || address == 0u) return 0;
    for (i = 0u; i + 1u < capacity; ++i) {
        uint8_t ch = 0u;
        if (!v12_debug_read8(address + (uint32_t)i, &ch)) return 0;
        out[i] = (char)ch;
        if (ch == 0u) return 1;
    }
    out[capacity - 1u] = '\0';
    return 1;
}

static int v12_debug_parse_u32(const char *text, uint32_t *out_value) {
    char *end = NULL;
    unsigned long long value;
    if (text == NULL || *text == '\0' || out_value == NULL) return 0;
    value = _strtoui64(text, &end, 0);
    if (end == text || *end != '\0' || value > UINT32_MAX) return 0;
    *out_value = (uint32_t)value;
    return 1;
}


static uint32_t v12_debug_spmario_globals(void);
static uint32_t v12_debug_resolve_map(const char *wanted);

static int v12_debug_parse_f32(const char *text, float *out_value) {
    char *end = NULL;
    double value;
    if (text == NULL || *text == '\0' || out_value == NULL) return 0;
    value = strtod(text, &end);
    if (end == text || *end != '\0' || value < -1000000.0 || value > 1000000.0)
        return 0;
    *out_value = (float)value;
    return 1;
}

static int v12_debug_read_f32(uint32_t address, float *out_value) {
    union {
        uint32_t u;
        float f;
    } value;
    if (out_value == NULL || !v8_read32(address, &value.u)) return 0;
    *out_value = value.f;
    return 1;
}

static int v12_debug_write_f32(uint32_t address, float value) {
    union {
        uint32_t u;
        float f;
    } bits;
    bits.f = value;
    return v8_write32(address, bits.u);
}

static int v12_debug_get_current_map(char *out, size_t capacity) {
    uint32_t gp = v12_debug_spmario_globals();
    if (gp == 0u) return 0;
    return v12_debug_read_cstring(gp + R8PE01_DEBUG_MAP_OFFSET, out, capacity);
}

static int v12_debug_queue_map_change(const char *map) {
    uint32_t map_name = v12_debug_resolve_map(map);
    if (map_name == 0u) return 0;
    return v8_write32(R8PE01_DEBUG_NEXT_P0, map_name) &&
           v8_write32(R8PE01_DEBUG_NEXT_P1, 0u) &&
           v8_write32(R8PE01_DEBUG_NEXT_SEQ, R8PE01_DEBUG_SEQ_MAPCHANGE);
}

static int v12_debug_adjacent_room(int direction, char out[32]) {
    char current[32];
    char *underscore;
    char *digits;
    char *end = NULL;
    long room;
    long candidate;
    size_t prefix_len;
    if (direction != -1 && direction != 1) return 0;
    if (!v12_debug_get_current_map(current, sizeof(current))) return 0;
    underscore = strrchr(current, '_');
    if (underscore == NULL || underscore == current) return 0;
    digits = underscore + 1;
    if (*digits == '\0') return 0;
    room = strtol(digits, &end, 10);
    if (end == digits || *end != '\0') return 0;
    prefix_len = (size_t)(underscore - current);
    if (prefix_len + 4u >= 32u) return 0;

    /* "Next room" should skip holes such as intentionally missing map numbers
       rather than failing at the first absent sibling. Keep the same map prefix
       and scan only the bounded 00..99 numeric namespace. */
    for (candidate = room + direction;
         candidate >= 0 && candidate <= 99;
         candidate += direction) {
        _snprintf_s(out, 32u, _TRUNCATE, "%.*s_%02ld",
                    (int)prefix_len, current, candidate);
        if (v12_debug_resolve_map(out) != 0u) return 1;
    }
    return 0;
}

static int v12_debug_npc_work(uint32_t *out_work, uint32_t *out_num,
                              uint32_t *out_entries, uint32_t *out_flags) {
    uint32_t work = 0u;
    uint32_t num = 0u;
    uint32_t entries = 0u;
    uint32_t flags = 0u;
    if (!v8_read32(R8PE01_DEBUG_NPCDRV_WP, &work) || work == 0u ||
        !v8_read32(work + R8PE01_DEBUG_NPC_WORK_NUM, &num) ||
        !v8_read32(work + R8PE01_DEBUG_NPC_WORK_ENTRIES, &entries) ||
        !v8_read32(work + R8PE01_DEBUG_NPC_WORK_FLAG, &flags))
        return 0;
    if (num > 4096u) return 0;
    if (out_work != NULL) *out_work = work;
    if (out_num != NULL) *out_num = num;
    if (out_entries != NULL) *out_entries = entries;
    if (out_flags != NULL) *out_flags = flags;
    return 1;
}

static void v12_debug_restore_npcs(void) {
    uint32_t i;
    for (i = 0u; i < g_v12_debug_npc_snapshot_count; ++i) {
        uint32_t current_id = 0u;
        openspm_debug_npc_snapshot *snap = &g_v12_debug_npc_snapshot[i];
        if (!snap->valid || snap->address == 0u) continue;
        if (!v8_read32(snap->address + R8PE01_DEBUG_NPC_ENTRY_ID, &current_id))
            continue;
        if (current_id != snap->id) continue;
        {
            uint32_t current_flags = 0u;
            if (!v8_read32(snap->address + R8PE01_DEBUG_NPC_ENTRY_FLAG8,
                           &current_flags))
                continue;
            /* Restore only the active bit we changed. Other NPC flags are live
               game state and may legitimately change during the experiment. */
            current_flags =
                (current_flags & ~R8PE01_DEBUG_NPC_ACTIVE) |
                (snap->flag8 & R8PE01_DEBUG_NPC_ACTIVE);
            (void)v8_write32(snap->address + R8PE01_DEBUG_NPC_ENTRY_FLAG8,
                             current_flags);
        }
    }
    memset(g_v12_debug_npc_snapshot, 0, sizeof(g_v12_debug_npc_snapshot));
    g_v12_debug_npc_snapshot_count = 0u;
    g_v12_debug_npc_snapshot_entries = 0u;
}

static void v12_debug_clear_npc_one_pulse(int restore) {
    openspm_debug_npc_snapshot *snap = &g_v12_debug_npc_one_pulse;
    if (restore && snap->valid && snap->address != 0u) {
        uint32_t current_id = 0u;
        uint32_t current_flags = 0u;
        uint32_t entries = 0u;
        char current_map[32] = {0};
        int same_map =
            g_v12_debug_npc_one_pulse_map[0] != '\0' &&
            v12_debug_get_current_map(current_map, sizeof(current_map)) &&
            strcmp(current_map, g_v12_debug_npc_one_pulse_map) == 0;
        int same_table =
            v12_debug_npc_work(NULL, NULL, &entries, NULL) &&
            entries == g_v12_debug_npc_one_pulse_entries;
        if (same_map && same_table &&
            v8_read32(snap->address + R8PE01_DEBUG_NPC_ENTRY_ID, &current_id) &&
            current_id == snap->id &&
            v8_read32(snap->address + R8PE01_DEBUG_NPC_ENTRY_FLAG8,
                      &current_flags)) {
            current_flags =
                (current_flags & ~R8PE01_DEBUG_NPC_ACTIVE) |
                (snap->flag8 & R8PE01_DEBUG_NPC_ACTIVE);
            (void)v8_write32(
                snap->address + R8PE01_DEBUG_NPC_ENTRY_FLAG8, current_flags);
        }
    }
    memset(&g_v12_debug_npc_one_pulse, 0,
           sizeof(g_v12_debug_npc_one_pulse));
    g_v12_debug_npc_one_pulse_entries = 0u;
    g_v12_debug_npc_one_pulse_frames = 0u;
    g_v12_debug_npc_one_pulse_active_ordinal = 0u;
    g_v12_debug_npc_one_pulse_table_index = 0u;
    memset(g_v12_debug_npc_one_pulse_map, 0,
           sizeof(g_v12_debug_npc_one_pulse_map));
    memset(g_v12_debug_npc_one_pulse_name, 0,
           sizeof(g_v12_debug_npc_one_pulse_name));
}

static void v12_debug_apply_npc_one_pulse(void) {
    openspm_debug_npc_snapshot *snap = &g_v12_debug_npc_one_pulse;
    uint32_t entries = 0u;
    uint32_t current_id = 0u;
    uint32_t current_flags = 0u;
    char current_map[32] = {0};

    if (!snap->valid || g_v12_debug_npc_one_pulse_frames == 0u) return;

    if (g_v12_debug_npc_one_pulse_map[0] == '\0' ||
        !v12_debug_get_current_map(current_map, sizeof(current_map)) ||
        strcmp(current_map, g_v12_debug_npc_one_pulse_map) != 0 ||
        !v12_debug_npc_work(NULL, NULL, &entries, NULL) ||
        entries != g_v12_debug_npc_one_pulse_entries ||
        !v8_read32(snap->address + R8PE01_DEBUG_NPC_ENTRY_ID, &current_id) ||
        current_id != snap->id ||
        !v8_read32(snap->address + R8PE01_DEBUG_NPC_ENTRY_FLAG8,
                   &current_flags)) {
        backend_log(
            OPENSPM_LOG_WARN,
            "M14 dev30 one-NPC active pulse discarded after map/table/id change.");
        v12_debug_clear_npc_one_pulse(0);
        return;
    }

    (void)v8_write32(
        snap->address + R8PE01_DEBUG_NPC_ENTRY_FLAG8,
        current_flags & ~R8PE01_DEBUG_NPC_ACTIVE);

    --g_v12_debug_npc_one_pulse_frames;
    if (g_v12_debug_npc_one_pulse_frames == 0u) {
        char message[256];
        _snprintf_s(
            message, sizeof(message), _TRUNCATE,
            "M14 dev30 one-NPC active pulse auto-restored ordinal=%u index=%u id=%u name=%s.",
            (unsigned)g_v12_debug_npc_one_pulse_active_ordinal,
            (unsigned)g_v12_debug_npc_one_pulse_table_index,
            (unsigned)snap->id,
            g_v12_debug_npc_one_pulse_name[0] != '\0'
                ? g_v12_debug_npc_one_pulse_name : "(unnamed)");
        v12_debug_clear_npc_one_pulse(1);
        backend_log(OPENSPM_LOG_INFO, message);
    }
}

static void v12_debug_restore_npc_isolation(void) {
    uint32_t i;
    for (i = 0u; i < g_v12_debug_npc_snapshot_count; ++i) {
        uint32_t current_id = 0u;
        uint32_t current_flags = 0u;
        openspm_debug_npc_snapshot *snap = &g_v12_debug_npc_snapshot[i];
        if (!snap->valid || snap->address == 0u) continue;
        if (!v8_read32(snap->address + R8PE01_DEBUG_NPC_ENTRY_ID, &current_id) ||
            current_id != snap->id ||
            !v8_read32(snap->address + R8PE01_DEBUG_NPC_ENTRY_FLAG46C,
                       &current_flags))
            continue;
        /* Restore only the two diagnostic control bits. NPC state outside
           those bits remains owned by the live game. */
        current_flags =
            (current_flags & ~R8PE01_DEBUG_NPC_ENTRY_CONTROL_MASK) |
            (snap->flag46c & R8PE01_DEBUG_NPC_ENTRY_CONTROL_MASK);
        (void)v8_write32(snap->address + R8PE01_DEBUG_NPC_ENTRY_FLAG46C,
                         current_flags);
    }
    memset(g_v12_debug_npc_snapshot, 0, sizeof(g_v12_debug_npc_snapshot));
    g_v12_debug_npc_snapshot_count = 0u;
    g_v12_debug_npc_snapshot_entries = 0u;
    g_v12_debug_npc_isolation_mode = 0u;
    g_v12_debug_npc_isolation_keep = 0u;
}

static void v12_debug_reset_npc_controls(void) {
    uint32_t work = 0u;
    uint32_t flags = 0u;
    g_v12_debug_npc_suspend_pulse_frames = 0u;
    if (g_v12_debug_npc_one_pulse.valid)
        v12_debug_clear_npc_one_pulse(1);
    if (g_v12_debug_npc_suspended || g_v12_debug_npc_isolation_mode != 0u) {
        char current_map[32] = {0};
        int same_map =
            g_v12_debug_npc_suspend_map[0] != '\0' &&
            v12_debug_get_current_map(current_map, sizeof(current_map)) &&
            strcmp(current_map, g_v12_debug_npc_suspend_map) == 0;
        if (g_v12_debug_npc_suspended) {
            g_v12_debug_npc_suspended = 0u;
            if (same_map) v12_debug_restore_npcs();
        } else if (same_map) {
            v12_debug_restore_npc_isolation();
        }
        if (!same_map) {
            /* The game may reuse the same NPC table allocation across maps.
               Never restore old entry bits after the semantic room changed. */
            memset(g_v12_debug_npc_snapshot, 0,
                   sizeof(g_v12_debug_npc_snapshot));
            g_v12_debug_npc_snapshot_count = 0u;
            g_v12_debug_npc_snapshot_entries = 0u;
            g_v12_debug_npc_isolation_mode = 0u;
            g_v12_debug_npc_isolation_keep = 0u;
        }
        memset(g_v12_debug_npc_suspend_map, 0,
               sizeof(g_v12_debug_npc_suspend_map));
    }
    if (v12_debug_npc_work(&work, NULL, NULL, &flags) &&
        (flags & R8PE01_DEBUG_NPC_FREEZE_ALL) != 0u) {
        (void)v8_write32(work + R8PE01_DEBUG_NPC_WORK_FLAG,
                         flags & ~R8PE01_DEBUG_NPC_FREEZE_ALL);
    }
}

static void v12_debug_apply_npc_suspend(void) {
    uint32_t num = 0u;
    uint32_t entries = 0u;
    uint32_t i;
    if (!g_v12_debug_npc_suspended) return;
    {
        char current_map[32] = {0};
        if (g_v12_debug_npc_suspend_map[0] != '\0' &&
            v12_debug_get_current_map(current_map, sizeof(current_map)) &&
            strcmp(current_map, g_v12_debug_npc_suspend_map) != 0) {
            memset(g_v12_debug_npc_snapshot, 0,
                   sizeof(g_v12_debug_npc_snapshot));
            g_v12_debug_npc_snapshot_count = 0u;
            g_v12_debug_npc_snapshot_entries = 0u;
            memset(g_v12_debug_npc_suspend_map, 0,
                   sizeof(g_v12_debug_npc_suspend_map));
            g_v12_debug_npc_suspended = 0u;
            backend_log(OPENSPM_LOG_INFO,
                        "M14 dev29 NPC suspend auto-disabled after map change.");
            return;
        }
    }
    if (!v12_debug_npc_work(NULL, &num, &entries, NULL) || entries == 0u) return;

    if (g_v12_debug_npc_snapshot_entries != 0u &&
        entries != g_v12_debug_npc_snapshot_entries) {
        /* A natural map transition can replace the table without using a
           cockpit warp. Never apply/restore stale entry addresses into the new
           room: discard the old snapshots and disable the experiment. */
        memset(g_v12_debug_npc_snapshot, 0, sizeof(g_v12_debug_npc_snapshot));
        g_v12_debug_npc_snapshot_count = 0u;
        g_v12_debug_npc_snapshot_entries = 0u;
        memset(g_v12_debug_npc_suspend_map, 0,
               sizeof(g_v12_debug_npc_suspend_map));
        g_v12_debug_npc_suspended = 0u;
        backend_log(OPENSPM_LOG_INFO,
                    "M14 dev29 NPC suspend auto-disabled after NPC table replacement.");
        return;
    }

    if (num > OPENSPM_DEBUG_NPC_SNAPSHOT_MAX) {
        g_v12_debug_npc_suspended = 0u;
        v12_debug_restore_npcs();
        memset(g_v12_debug_npc_suspend_map, 0,
               sizeof(g_v12_debug_npc_suspend_map));
        backend_log(OPENSPM_LOG_WARN,
                    "M14 dev29 NPC suspend auto-disabled after NPC table exceeded reversible snapshot capacity.");
        return;
    }

    for (i = 0u; i < num; ++i) {
        uint32_t address = entries + i * R8PE01_DEBUG_NPC_ENTRY_SIZE;
        uint32_t id = 0u;
        uint32_t flag8 = 0u;
        uint32_t j;
        int found = 0;
        if (!v8_read32(address + R8PE01_DEBUG_NPC_ENTRY_ID, &id) ||
            !v8_read32(address + R8PE01_DEBUG_NPC_ENTRY_FLAG8, &flag8))
            continue;
        if ((flag8 & R8PE01_DEBUG_NPC_ACTIVE) == 0u) continue;

        for (j = 0u; j < g_v12_debug_npc_snapshot_count; ++j) {
            if (g_v12_debug_npc_snapshot[j].valid &&
                g_v12_debug_npc_snapshot[j].address == address &&
                g_v12_debug_npc_snapshot[j].id == id) {
                found = 1;
                break;
            }
        }
        if (!found && g_v12_debug_npc_snapshot_count < OPENSPM_DEBUG_NPC_SNAPSHOT_MAX) {
            openspm_debug_npc_snapshot *snap =
                &g_v12_debug_npc_snapshot[g_v12_debug_npc_snapshot_count++];
            snap->address = address;
            snap->id = id;
            snap->flag8 = flag8;
            snap->valid = 1u;
        }
        (void)v8_write32(address + R8PE01_DEBUG_NPC_ENTRY_FLAG8,
                         flag8 & ~R8PE01_DEBUG_NPC_ACTIVE);
    }
}

static void v12_debug_guard_npc_isolation(void) {
    uint32_t entries = 0u;
    char current_map[32] = {0};
    if (g_v12_debug_npc_isolation_mode == 0u) return;
    if ((g_v12_debug_npc_suspend_map[0] != '\0' &&
         v12_debug_get_current_map(current_map, sizeof(current_map)) &&
         strcmp(current_map, g_v12_debug_npc_suspend_map) != 0) ||
        !v12_debug_npc_work(NULL, NULL, &entries, NULL) ||
        (g_v12_debug_npc_snapshot_entries != 0u &&
         entries != g_v12_debug_npc_snapshot_entries)) {
        /* Do not write stale entry addresses after a natural room/table
           replacement. The old room is already gone, so discard snapshots. */
        memset(g_v12_debug_npc_snapshot, 0, sizeof(g_v12_debug_npc_snapshot));
        g_v12_debug_npc_snapshot_count = 0u;
        g_v12_debug_npc_snapshot_entries = 0u;
        memset(g_v12_debug_npc_suspend_map, 0,
               sizeof(g_v12_debug_npc_suspend_map));
        g_v12_debug_npc_isolation_mode = 0u;
        g_v12_debug_npc_isolation_keep = 0u;
        backend_log(OPENSPM_LOG_INFO,
                    "M14 dev29 NPC isolation auto-disabled after map/table change.");
    }
}

static void v12_debug_apply_freezes(void) {
    uint32_t i;
    for (i = 0u; i < OPENSPM_DEBUG_FREEZE_SLOTS; ++i) {
        openspm_debug_freeze_slot *slot = &g_v12_debug_freezes[i];
        if (!slot->active) continue;
        if (slot->width == 1u) (void)v12_debug_write8(slot->address, (uint8_t)slot->value);
        else if (slot->width == 2u) (void)v8_write16(slot->address, (uint16_t)slot->value);
        else if (slot->width == 4u) (void)v8_write32(slot->address, slot->value);
    }
}

static void v12_debug_apply_speed(void) {
    uint8_t character = 0u;
    if (!g_v12_debug_speed_active) return;
    if (!v12_debug_read8(
            R8PE01_DEBUG_MARIO_WORK + R8PE01_DEBUG_MARIO_CHARACTER,
            &character))
        return;
    if (character != g_v12_debug_speed_character) {
        /* Character properties differ. Stop enforcing the old character's
           captured speeds rather than contaminating Peach/Bowser/Luigi with
           Mario's baseline. The game owns the new character's property load. */
        g_v12_debug_speed_active = 0u;
        g_v12_debug_speed_multiplier = 1.0f;
        return;
    }
    (void)v12_debug_write_f32(
        R8PE01_DEBUG_MARIO_WORK + R8PE01_DEBUG_MARIO_WALK_SPEED,
        g_v12_debug_speed_walk_base * g_v12_debug_speed_multiplier);
    (void)v12_debug_write_f32(
        R8PE01_DEBUG_MARIO_WORK + R8PE01_DEBUG_MARIO_DASH_SPEED,
        g_v12_debug_speed_dash_base * g_v12_debug_speed_multiplier);
}

static void v12_debug_apply_runtime_toggles(void) {
    v12_debug_apply_freezes();
    v12_debug_apply_speed();
    v12_debug_apply_npc_suspend();
    v12_debug_apply_npc_one_pulse();
    if (g_v12_debug_npc_suspended &&
        g_v12_debug_npc_suspend_pulse_frames != 0u) {
        --g_v12_debug_npc_suspend_pulse_frames;
        if (g_v12_debug_npc_suspend_pulse_frames == 0u) {
            g_v12_debug_npc_suspended = 0u;
            v12_debug_restore_npcs();
            memset(g_v12_debug_npc_suspend_map, 0,
                   sizeof(g_v12_debug_npc_suspend_map));
            backend_log(OPENSPM_LOG_INFO,
                        "M14 dev29 NPC active-bit pulse auto-restored.");
        }
    }
    v12_debug_guard_npc_isolation();
}

static void v12_debug_reset_all_controls(void) {
    uint8_t character = 0u;

    v12_debug_reset_npc_controls();

    if (g_v12_debug_speed_active &&
        v12_debug_read8(
            R8PE01_DEBUG_MARIO_WORK + R8PE01_DEBUG_MARIO_CHARACTER,
            &character) &&
        character == g_v12_debug_speed_character) {
        (void)v12_debug_write_f32(
            R8PE01_DEBUG_MARIO_WORK + R8PE01_DEBUG_MARIO_WALK_SPEED,
            g_v12_debug_speed_walk_base);
        (void)v12_debug_write_f32(
            R8PE01_DEBUG_MARIO_WORK + R8PE01_DEBUG_MARIO_DASH_SPEED,
            g_v12_debug_speed_dash_base);
    }
    g_v12_debug_speed_active = 0u;
    g_v12_debug_speed_multiplier = 1.0f;
    g_v12_debug_speed_walk_base = 0.0f;
    g_v12_debug_speed_dash_base = 0.0f;

    memset(g_v12_debug_freezes, 0, sizeof(g_v12_debug_freezes));
    memset(&g_v12_debug_checkpoint, 0, sizeof(g_v12_debug_checkpoint));
}

static uint32_t v12_debug_spmario_globals(void) {
    uint32_t gp = 0u;
    if (!v8_read32(R8PE01_M14_GP_PTR, &gp)) return 0u;
    return gp;
}

static uint32_t v12_debug_resolve_map(const char *wanted) {
    uint32_t i;
    size_t wanted_len;
    if (wanted == NULL || *wanted == '\0') return 0u;
    wanted_len = strlen(wanted);
    if (wanted_len >= 32u) return 0u;
    for (i = 0u; i < R8PE01_DEBUG_MAP_DATA_COUNT; ++i) {
        uint32_t map_data = 0u;
        uint32_t name_ptr = 0u;
        char name[32];
        if (!v8_read32(R8PE01_DEBUG_MAP_DATA + i * 4u, &map_data) ||
            map_data == 0u ||
            !v8_read32(map_data, &name_ptr) ||
            name_ptr == 0u)
            continue;
        memset(name, 0, sizeof(name));
        if (!v12_debug_read_cstring(name_ptr, name, sizeof(name))) continue;
        if (strcmp(name, wanted) == 0) return name_ptr;
    }
    return 0u;
}

static void v12_debug_close_mailbox(void) {
    if (g_v12_debug_mailbox != NULL) {
        UnmapViewOfFile(g_v12_debug_mailbox);
        g_v12_debug_mailbox = NULL;
    }
    if (g_v12_debug_mapping != NULL) {
        CloseHandle(g_v12_debug_mapping);
        g_v12_debug_mapping = NULL;
    }
    g_v12_debug_processed_generation = 0;
}

static void v12_debug_open_mailbox(void) {
    wchar_t name[96];
    v12_debug_close_mailbox();
    _snwprintf_s(name, _countof(name), _TRUNCATE,
                 L"Local\\OpenSPM.Debug.%lu",
                 (unsigned long)GetCurrentProcessId());
    g_v12_debug_mapping = CreateFileMappingW(
        INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE, 0,
        (DWORD)sizeof(openspm_debug_mailbox), name);
    if (g_v12_debug_mapping == NULL) {
        backend_log(OPENSPM_LOG_WARN,
                    "M14 dev29 live debug cockpit could not create shared mailbox.");
        return;
    }
    g_v12_debug_mailbox = (openspm_debug_mailbox *)MapViewOfFile(
        g_v12_debug_mapping, FILE_MAP_READ | FILE_MAP_WRITE, 0, 0,
        sizeof(openspm_debug_mailbox));
    if (g_v12_debug_mailbox == NULL) {
        backend_log(OPENSPM_LOG_WARN,
                    "M14 dev29 live debug cockpit could not map shared mailbox.");
        v12_debug_close_mailbox();
        return;
    }
    memset(g_v12_debug_mailbox, 0, sizeof(*g_v12_debug_mailbox));
    g_v12_debug_mailbox->version = OPENSPM_DEBUG_IPC_VERSION;
    backend_log(OPENSPM_LOG_INFO,
                "M14 dev29 live debug cockpit ready: M14-DEBUG-CONSOLE.cmd opens GUI by default; CLI/scripts remain available.");
}

static void v12_debug_reply(char *response, size_t capacity,
                            const char *format, ...) {
    va_list args;
    if (response == NULL || capacity == 0u) return;
    va_start(args, format);
    _vsnprintf_s(response, capacity, _TRUNCATE, format, args);
    va_end(args);
}

static void v12_debug_execute(const char *raw,
                              const openspm_m14_hostcall_state *state,
                              char response[OPENSPM_DEBUG_RESPONSE_BYTES]) {
    char command[OPENSPM_DEBUG_COMMAND_BYTES];
    char op[32] = {0};
    char arg1[96] = {0};
    char arg2[96] = {0};
    char arg3[96] = {0};
    int count;
    uint32_t seq = UINT32_MAX;
    uint32_t stage = UINT32_MAX;
    uint32_t gp;
    uint32_t address;
    uint32_t value;
    uint32_t length;
    uint32_t i;

    response[0] = '\0';
    if (raw == NULL) {
        v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES, "empty command");
        return;
    }
    strncpy_s(command, sizeof(command), raw, _TRUNCATE);
    while (command[0] == ' ' || command[0] == '\t')
        memmove(command, command + 1, strlen(command));
    for (i = (uint32_t)strlen(command); i != 0u; --i) {
        char ch = command[i - 1u];
        if (ch != ' ' && ch != '\t' && ch != '\r' && ch != '\n') break;
        command[i - 1u] = '\0';
    }
    if (command[0] == '\0') {
        v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES, "empty command");
        return;
    }

    if (strcmp(command, "help") == 0 || strcmp(command, "?") == 0) {
        v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
            "help | status | seq | map | resetdebug | nextroom | prevroom | reloadroom | "
            "pos | setpos x y z | nudge dx dy dz | checkpoint save/load/clear | "
            "hp [current [max]] | speed [mult] | "
            "npcs info/list/entry/freeze/isolate/pulseone/suspend | story [n] | gsw <n> [v] | warp <map> | "
            "freeze8/16/32 <addr> <value> | unfreeze <addr|all> | freezes | "
            "read8/16/32 <addr> | write8/16/32 <addr> <value> | dump <addr> <len<=128> | mark <text>");
        return;
    }

    (void)v8_read32(R8PE01_M14_SEQ_WORK + 0u, &seq);
    (void)v8_read32(R8PE01_M14_SEQ_WORK + 4u, &stage);

    if (strcmp(command, "status") == 0 || strcmp(command, "perf") == 0) {
        v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
            "seq=%u stage=%u input_frame=%llu pc=0x%08X lr=0x%08X accounted_cycles=%llu",
            (unsigned)seq, (unsigned)stage,
            (unsigned long long)g_v9_input_frame,
            state != NULL ? (unsigned)state->pc : 0u,
            state != NULL ? (unsigned)state->lr : 0u,
            (unsigned long long)g_backend.accounted_cycles);
        return;
    }

    if (strcmp(command, "resetdebug") == 0) {
        v12_debug_reset_all_controls();
        v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                        "debug controls restored: NPC controls off, speed normal, memory freezes/checkpoint cleared");
        return;
    }

    if (strcmp(command, "seq") == 0) {
        v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                        "seq=%u stage=%u", (unsigned)seq, (unsigned)stage);
        return;
    }

    if (strcmp(command, "map") == 0) {
        char map[64] = {0};
        char door[64] = {0};
        gp = v12_debug_spmario_globals();
        if (gp == 0u ||
            !v12_debug_read_cstring(gp + R8PE01_DEBUG_MAP_OFFSET,
                                    map, sizeof(map)) ||
            !v12_debug_read_cstring(gp + R8PE01_DEBUG_DOOR_OFFSET,
                                    door, sizeof(door))) {
            v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                            "could not read current map/door");
            return;
        }
        v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                        "map=%s door=%s gp=0x%08X", map, door, (unsigned)gp);
        return;
    }

    count = sscanf(command, "%31s %95s %95s %95s", op, arg1, arg2, arg3);

    if (strcmp(op, "mark") == 0) {
        char message[OPENSPM_DEBUG_COMMAND_BYTES + 64u];
        const char *label = command + 4;
        while (*label == ' ' || *label == '\t') ++label;
        _snprintf_s(message, sizeof(message), _TRUNCATE,
                    "M14 DEBUG MARK frame=%llu %s",
                    (unsigned long long)g_v9_input_frame,
                    *label != '\0' ? label : "(unnamed)");
        backend_log(OPENSPM_LOG_INFO, message);
        v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES, "%s", message);
        return;
    }

    if (strcmp(op, "nextroom") == 0 || strcmp(op, "prevroom") == 0 ||
        strcmp(op, "reloadroom") == 0) {
        char target[32] = {0};
        int ok = 0;
        if (count != 1 || seq != R8PE01_DEBUG_SEQ_GAME) {
            v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                            "%s refused: requires gameplay seq=2", op);
            return;
        }
        if (strcmp(op, "reloadroom") == 0)
            ok = v12_debug_get_current_map(target, sizeof(target));
        else
            ok = v12_debug_adjacent_room(
                strcmp(op, "nextroom") == 0 ? 1 : -1, target);
        if (ok) v12_debug_reset_all_controls();
        if (!ok || !v12_debug_queue_map_change(target)) {
            v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                            "%s could not resolve a valid numeric sibling room", op);
            return;
        }
        v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                        "%s -> %s", op, target);
        return;
    }

    if (strcmp(op, "pos") == 0 || strcmp(op, "setpos") == 0 ||
        strcmp(op, "nudge") == 0) {
        float x = 0.0f, y = 0.0f, z = 0.0f;
        const uint32_t base =
            R8PE01_DEBUG_MARIO_WORK + R8PE01_DEBUG_MARIO_POSITION;
        if (!v12_debug_read_f32(base + 0u, &x) ||
            !v12_debug_read_f32(base + 4u, &y) ||
            !v12_debug_read_f32(base + 8u, &z)) {
            v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                            "could not read Mario position");
            return;
        }
        if (strcmp(op, "pos") != 0) {
            float a, b, c;
            if (count != 4 ||
                !v12_debug_parse_f32(arg1, &a) ||
                !v12_debug_parse_f32(arg2, &b) ||
                !v12_debug_parse_f32(arg3, &c)) {
                v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                                "usage: %s x y z", op);
                return;
            }
            if (strcmp(op, "setpos") == 0) {
                x = a; y = b; z = c;
            } else {
                x += a; y += b; z += c;
            }
            if (!v12_debug_write_f32(base + 0u, x) ||
                !v12_debug_write_f32(base + 4u, y) ||
                !v12_debug_write_f32(base + 8u, z)) {
                v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                                "could not write Mario position");
                return;
            }
        }
        v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                        "pos=(%.3f,%.3f,%.3f)", x, y, z);
        return;
    }

    if (strcmp(op, "checkpoint") == 0) {
        char current_map[32] = {0};
        const uint32_t base =
            R8PE01_DEBUG_MARIO_WORK + R8PE01_DEBUG_MARIO_POSITION;
        if (count != 2) {
            v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                            "usage: checkpoint save|load|clear");
            return;
        }
        if (strcmp(arg1, "clear") == 0) {
            memset(&g_v12_debug_checkpoint, 0, sizeof(g_v12_debug_checkpoint));
            v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                            "checkpoint cleared");
            return;
        }
        if (strcmp(arg1, "save") == 0) {
            if (!v12_debug_get_current_map(
                    g_v12_debug_checkpoint.map,
                    sizeof(g_v12_debug_checkpoint.map)) ||
                !v12_debug_read_f32(base + 0u, &g_v12_debug_checkpoint.x) ||
                !v12_debug_read_f32(base + 4u, &g_v12_debug_checkpoint.y) ||
                !v12_debug_read_f32(base + 8u, &g_v12_debug_checkpoint.z)) {
                memset(&g_v12_debug_checkpoint, 0,
                       sizeof(g_v12_debug_checkpoint));
                v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                                "could not save checkpoint");
                return;
            }
            g_v12_debug_checkpoint.valid = 1u;
            v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                            "checkpoint saved map=%s pos=(%.3f,%.3f,%.3f)",
                            g_v12_debug_checkpoint.map,
                            g_v12_debug_checkpoint.x,
                            g_v12_debug_checkpoint.y,
                            g_v12_debug_checkpoint.z);
            return;
        }
        if (strcmp(arg1, "load") == 0) {
            if (!g_v12_debug_checkpoint.valid ||
                !v12_debug_get_current_map(current_map, sizeof(current_map))) {
                v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                                "no valid checkpoint");
                return;
            }
            if (strcmp(current_map, g_v12_debug_checkpoint.map) != 0) {
                v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                                "checkpoint refused: saved map=%s current map=%s",
                                g_v12_debug_checkpoint.map, current_map);
                return;
            }
            if (!v12_debug_write_f32(base + 0u, g_v12_debug_checkpoint.x) ||
                !v12_debug_write_f32(base + 4u, g_v12_debug_checkpoint.y) ||
                !v12_debug_write_f32(base + 8u, g_v12_debug_checkpoint.z)) {
                v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                                "could not restore checkpoint");
                return;
            }
            v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                            "checkpoint restored map=%s pos=(%.3f,%.3f,%.3f)",
                            current_map,
                            g_v12_debug_checkpoint.x,
                            g_v12_debug_checkpoint.y,
                            g_v12_debug_checkpoint.z);
            return;
        }
        v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                        "usage: checkpoint save|load|clear");
        return;
    }

    if (strcmp(op, "hp") == 0) {
        uint32_t hp = 0u, max_hp = 0u;
        const uint32_t hp_addr =
            R8PE01_DEBUG_MARIO_POUCH + R8PE01_DEBUG_MARIO_HP;
        const uint32_t max_hp_addr =
            R8PE01_DEBUG_MARIO_POUCH + R8PE01_DEBUG_MARIO_MAX_HP;
        if (count >= 2) {
            if (!v12_debug_parse_u32(arg1, &hp) || hp > 999u) {
                v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                                "usage: hp [current 0..999 [max 1..999]]");
                return;
            }
            /* The cockpit's one-value Set HP control means "make this my
               health capacity", not a temporary over-heal that the game will
               clamp back to the old max on the next damage event. */
            max_hp = hp;
            if (count >= 3 &&
                (!v12_debug_parse_u32(arg2, &max_hp) ||
                 max_hp == 0u || max_hp > 999u)) {
                v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                                "usage: hp [current 0..999 [max 1..999]]");
                return;
            }
            if (hp > max_hp || !v8_write32(max_hp_addr, max_hp) ||
                !v8_write32(hp_addr, hp)) {
                v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                                "HP write refused: require current <= max <= 999");
                return;
            }
        }
        if (!v8_read32(hp_addr, &hp) || !v8_read32(max_hp_addr, &max_hp)) {
            v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                            "could not read Mario HP/max HP");
            return;
        }
        v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                        "hp=%u/%u", (unsigned)hp, (unsigned)max_hp);
        return;
    }

    if (strcmp(op, "speed") == 0) {
        float walk = 0.0f, dash = 0.0f, multiplier = 1.0f;
        if (count >= 2) {
            if (!v12_debug_parse_f32(arg1, &multiplier) ||
                multiplier < 0.1f || multiplier > 10.0f) {
                v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                                "usage: speed [0.1..10.0], use 1 to restore");
                return;
            }
            if (multiplier == 1.0f) {
                if (g_v12_debug_speed_active) {
                    (void)v12_debug_write_f32(
                        R8PE01_DEBUG_MARIO_WORK + R8PE01_DEBUG_MARIO_WALK_SPEED,
                        g_v12_debug_speed_walk_base);
                    (void)v12_debug_write_f32(
                        R8PE01_DEBUG_MARIO_WORK + R8PE01_DEBUG_MARIO_DASH_SPEED,
                        g_v12_debug_speed_dash_base);
                }
                g_v12_debug_speed_active = 0u;
                g_v12_debug_speed_multiplier = 1.0f;
            } else {
                if (!g_v12_debug_speed_active) {
                    if (!v12_debug_read_f32(
                            R8PE01_DEBUG_MARIO_WORK + R8PE01_DEBUG_MARIO_WALK_SPEED,
                            &g_v12_debug_speed_walk_base) ||
                        !v12_debug_read_f32(
                            R8PE01_DEBUG_MARIO_WORK + R8PE01_DEBUG_MARIO_DASH_SPEED,
                            &g_v12_debug_speed_dash_base) ||
                        !v12_debug_read8(
                            R8PE01_DEBUG_MARIO_WORK + R8PE01_DEBUG_MARIO_CHARACTER,
                            &g_v12_debug_speed_character)) {
                        v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                                        "could not capture Mario base movement speeds");
                        return;
                    }
                }
                g_v12_debug_speed_multiplier = multiplier;
                g_v12_debug_speed_active = 1u;
                v12_debug_apply_speed();
            }
        }
        (void)v12_debug_read_f32(
            R8PE01_DEBUG_MARIO_WORK + R8PE01_DEBUG_MARIO_WALK_SPEED, &walk);
        (void)v12_debug_read_f32(
            R8PE01_DEBUG_MARIO_WORK + R8PE01_DEBUG_MARIO_DASH_SPEED, &dash);
        v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                        "speed=%s multiplier=%.2f walk=%.3f dash=%.3f",
                        g_v12_debug_speed_active ? "on" : "off",
                        g_v12_debug_speed_active ? g_v12_debug_speed_multiplier : 1.0f,
                        walk, dash);
        return;
    }

    if (strcmp(op, "moonjump") == 0) {
        v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                        "moonjump hook is reserved but not armed: exact US2 jump/fall parameters are not documented yet; use nudge 0 <dy> 0 meanwhile");
        return;
    }

    if (strcmp(op, "npcs") == 0) {
        uint32_t work = 0u, num = 0u, entries = 0u, flags = 0u;
        uint32_t active = 0u;
        if (!v12_debug_npc_work(&work, &num, &entries, &flags)) {
            v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                            "NPCWork is not available yet");
            return;
        }
        if (count == 1 || strcmp(arg1, "info") == 0) {
            for (i = 0u; i < num; ++i) {
                uint32_t flag8 = 0u;
                if (v8_read32(entries + i * R8PE01_DEBUG_NPC_ENTRY_SIZE +
                              R8PE01_DEBUG_NPC_ENTRY_FLAG8, &flag8) &&
                    (flag8 & R8PE01_DEBUG_NPC_ACTIVE) != 0u)
                    ++active;
            }
            v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                            "npcs work=0x%08X num=%u active=%u global_freeze=%s suspended=%s isolate=%s keep=%u onepulse=%s ordinal=%u index=%u ticks=%u entries=0x%08X",
                            (unsigned)work, (unsigned)num, (unsigned)active,
                            (flags & R8PE01_DEBUG_NPC_FREEZE_ALL) ? "on" : "off",
                            g_v12_debug_npc_suspended ? "on" : "off",
                            g_v12_debug_npc_isolation_mode == 1u ? "freeze" :
                            (g_v12_debug_npc_isolation_mode == 2u ? "hide" : "off"),
                            (unsigned)g_v12_debug_npc_isolation_keep,
                            g_v12_debug_npc_one_pulse.valid ? "on" : "off",
                            (unsigned)g_v12_debug_npc_one_pulse_active_ordinal,
                            (unsigned)g_v12_debug_npc_one_pulse_table_index,
                            (unsigned)g_v12_debug_npc_one_pulse_frames,
                            (unsigned)entries);
            return;
        }
        if (strcmp(arg1, "list") == 0) {
            size_t used = 0u;
            uint32_t listed = 0u;
            int written;
            written = _snprintf_s(
                response, OPENSPM_DEBUG_RESPONSE_BYTES, _TRUNCATE,
                "active_npcs=");
            if (written < 0) written = 0;
            used = (size_t)written;
            for (i = 0u; i < num; ++i) {
                uint32_t address =
                    entries + i * R8PE01_DEBUG_NPC_ENTRY_SIZE;
                uint32_t id = 0u, flag8 = 0u, tribe = 0u;
                char name[24] = {0};
                if (!v8_read32(address + R8PE01_DEBUG_NPC_ENTRY_ID, &id) ||
                    !v8_read32(address + R8PE01_DEBUG_NPC_ENTRY_FLAG8, &flag8) ||
                    (flag8 & R8PE01_DEBUG_NPC_ACTIVE) == 0u)
                    continue;
                (void)v8_read32(
                    address + R8PE01_DEBUG_NPC_ENTRY_TRIBE_ID, &tribe);
                (void)v12_debug_read_cstring(
                    address + R8PE01_DEBUG_NPC_ENTRY_NAME,
                    name, sizeof(name));
                written = _snprintf_s(
                    response + used, OPENSPM_DEBUG_RESPONSE_BYTES - used,
                    _TRUNCATE,
                    "%s#%u:id=%u,tribe=%u,name=%s",
                    listed == 0u ? "" : " ",
                    (unsigned)i, (unsigned)id, (unsigned)tribe,
                    name[0] != '\0' ? name : "(unnamed)");
                if (written < 0) break;
                used += (size_t)written;
                ++listed;
                if (used + 72u >= OPENSPM_DEBUG_RESPONSE_BYTES) break;
            }
            if (listed == 0u)
                v12_debug_reply(
                    response, OPENSPM_DEBUG_RESPONSE_BYTES,
                    "active_npcs=(none)");
            return;
        }
        if (strcmp(arg1, "entry") == 0) {
            uint32_t index = 0u, address, id = 0u, flag8 = 0u, flag46c = 0u;
            uint32_t tribe = 0u;
            float x = 0.0f, y = 0.0f, z = 0.0f;
            char name[40] = {0};
            if (count < 3 || !v12_debug_parse_u32(arg2, &index) ||
                index >= num) {
                v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                                "usage: npcs entry <index 0..%u>",
                                num != 0u ? (unsigned)(num - 1u) : 0u);
                return;
            }
            address = entries + index * R8PE01_DEBUG_NPC_ENTRY_SIZE;
            if (!v8_read32(address + R8PE01_DEBUG_NPC_ENTRY_ID, &id) ||
                !v8_read32(address + R8PE01_DEBUG_NPC_ENTRY_FLAG8, &flag8) ||
                !v8_read32(address + R8PE01_DEBUG_NPC_ENTRY_FLAG46C, &flag46c) ||
                !v8_read32(address + R8PE01_DEBUG_NPC_ENTRY_TRIBE_ID, &tribe)) {
                v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                                "could not read NPC entry %u", (unsigned)index);
                return;
            }
            (void)v12_debug_read_cstring(
                address + R8PE01_DEBUG_NPC_ENTRY_NAME, name, sizeof(name));
            (void)v12_debug_read_f32(
                address + R8PE01_DEBUG_NPC_ENTRY_POSITION + 0u, &x);
            (void)v12_debug_read_f32(
                address + R8PE01_DEBUG_NPC_ENTRY_POSITION + 4u, &y);
            (void)v12_debug_read_f32(
                address + R8PE01_DEBUG_NPC_ENTRY_POSITION + 8u, &z);
            v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                            "npc[%u] id=%u tribe=%u active=%s flag8=0x%08X flag46c=0x%08X name=%s pos=(%.1f,%.1f,%.1f)",
                            (unsigned)index, (unsigned)id, (unsigned)tribe,
                            (flag8 & R8PE01_DEBUG_NPC_ACTIVE) ? "yes" : "no",
                            (unsigned)flag8, (unsigned)flag46c,
                            name[0] != '\0' ? name : "(unnamed)", x, y, z);
            return;
        }
        if (strcmp(arg1, "freeze") == 0) {
            int enable;
            if (count < 3 ||
                (strcmp(arg2, "on") != 0 && strcmp(arg2, "off") != 0)) {
                v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                                "usage: npcs freeze on|off");
                return;
            }
            enable = strcmp(arg2, "on") == 0;
            if (enable) flags |= R8PE01_DEBUG_NPC_FREEZE_ALL;
            else flags &= ~R8PE01_DEBUG_NPC_FREEZE_ALL;
            if (!v8_write32(work + R8PE01_DEBUG_NPC_WORK_FLAG, flags)) {
                v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                                "could not write NPCWork freeze flag");
                return;
            }
            v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                            "NPC logic freeze=%s (render/active state unchanged)",
                            enable ? "on" : "off");
            return;
        }
        if (strcmp(arg1, "isolate") == 0) {
            uint32_t keep = 0u;
            uint32_t isolate_flag = 0u;
            uint32_t active_ordinal = 0u;
            uint32_t changed = 0u;
            if (count < 3) {
                v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                                "usage: npcs isolate off|freeze|hide [keep-active-count]");
                return;
            }
            if (strcmp(arg2, "off") == 0) {
                if (g_v12_debug_npc_isolation_mode != 0u)
                    v12_debug_reset_npc_controls();
                v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                                "NPC isolation=off");
                return;
            }
            if (strcmp(arg2, "freeze") == 0)
                isolate_flag = R8PE01_DEBUG_NPC_ENTRY_FROZEN;
            else if (strcmp(arg2, "hide") == 0)
                isolate_flag = R8PE01_DEBUG_NPC_ENTRY_HIDDEN_FROZEN;
            else {
                v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                                "usage: npcs isolate off|freeze|hide [keep-active-count]");
                return;
            }
            if (count >= 4 &&
                (!v12_debug_parse_u32(arg3, &keep) || keep > num)) {
                v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                                "isolate keep count must be 0..%u",
                                (unsigned)num);
                return;
            }
            if (g_v12_debug_npc_one_pulse.valid) {
                v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                                "NPC isolation refused while one-NPC pulse is active; use npcs pulseone off first");
                return;
            }
            if (g_v12_debug_npc_suspended) {
                v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                                "NPC isolation refused while destructive suspend is active; use npcs suspend off first");
                return;
            }
            if (num > OPENSPM_DEBUG_NPC_SNAPSHOT_MAX) {
                v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                                "NPC isolation refused: num=%u exceeds snapshot limit=%u",
                                (unsigned)num,
                                (unsigned)OPENSPM_DEBUG_NPC_SNAPSHOT_MAX);
                return;
            }
            if (g_v12_debug_npc_isolation_mode != 0u)
                v12_debug_reset_npc_controls();
            memset(g_v12_debug_npc_snapshot, 0,
                   sizeof(g_v12_debug_npc_snapshot));
            g_v12_debug_npc_snapshot_count = 0u;
            g_v12_debug_npc_snapshot_entries = entries;
            memset(g_v12_debug_npc_suspend_map, 0,
                   sizeof(g_v12_debug_npc_suspend_map));
            if (!v12_debug_get_current_map(
                    g_v12_debug_npc_suspend_map,
                    sizeof(g_v12_debug_npc_suspend_map))) {
                g_v12_debug_npc_snapshot_entries = 0u;
                v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                                "NPC isolation refused: current map identity unavailable");
                return;
            }
            g_v12_debug_npc_isolation_mode =
                isolate_flag == R8PE01_DEBUG_NPC_ENTRY_FROZEN ? 1u : 2u;
            g_v12_debug_npc_isolation_keep = keep;
            for (i = 0u; i < num; ++i) {
                uint32_t address =
                    entries + i * R8PE01_DEBUG_NPC_ENTRY_SIZE;
                uint32_t id = 0u, flag8 = 0u, flag46c = 0u;
                openspm_debug_npc_snapshot *snap;
                if (!v8_read32(address + R8PE01_DEBUG_NPC_ENTRY_ID, &id) ||
                    !v8_read32(address + R8PE01_DEBUG_NPC_ENTRY_FLAG8, &flag8) ||
                    !v8_read32(address + R8PE01_DEBUG_NPC_ENTRY_FLAG46C,
                               &flag46c))
                    continue;
                if ((flag8 & R8PE01_DEBUG_NPC_ACTIVE) == 0u) continue;
                if (g_v12_debug_npc_snapshot_count >=
                    OPENSPM_DEBUG_NPC_SNAPSHOT_MAX)
                    break;
                snap =
                    &g_v12_debug_npc_snapshot[g_v12_debug_npc_snapshot_count++];
                snap->address = address;
                snap->id = id;
                snap->flag8 = flag8;
                snap->flag46c = flag46c;
                snap->valid = 1u;
                if (active_ordinal++ < keep) continue;
                if (!v8_write32(address + R8PE01_DEBUG_NPC_ENTRY_FLAG46C,
                                flag46c | isolate_flag)) {
                    v12_debug_restore_npc_isolation();
                    memset(g_v12_debug_npc_suspend_map, 0,
                           sizeof(g_v12_debug_npc_suspend_map));
                    v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                                    "NPC isolation failed while writing entry %u",
                                    (unsigned)i);
                    return;
                }
                ++changed;
            }
            v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                            "NPC isolation=%s keep=%u affected=%u active=%u; entries remain active structurally",
                            g_v12_debug_npc_isolation_mode == 1u ? "freeze" : "hide",
                            (unsigned)keep, (unsigned)changed,
                            (unsigned)active_ordinal);
            return;
        }
        if (strcmp(arg1, "pulseone") == 0) {
            uint32_t wanted_ordinal = 0u;
            uint32_t pulse_frames = 0u;
            uint32_t active_ordinal = 0u;
            uint32_t chosen_index = UINT32_MAX;
            uint32_t chosen_address = 0u;
            uint32_t chosen_id = 0u;
            uint32_t chosen_flag8 = 0u;
            char chosen_name[24] = {0};

            if (count == 3 && strcmp(arg2, "off") == 0) {
                if (g_v12_debug_npc_one_pulse.valid)
                    v12_debug_clear_npc_one_pulse(1);
                v12_debug_reply(
                    response, OPENSPM_DEBUG_RESPONSE_BYTES,
                    "NPC one-pulse=off");
                return;
            }
            if (count != 4 ||
                !v12_debug_parse_u32(arg2, &wanted_ordinal) ||
                !v12_debug_parse_u32(arg3, &pulse_frames) ||
                pulse_frames < 1u || pulse_frames > 120u) {
                v12_debug_reply(
                    response, OPENSPM_DEBUG_RESPONSE_BYTES,
                    "usage: npcs pulseone <active-ordinal> <1..120 safe-frame ticks> | npcs pulseone off");
                return;
            }
            if (g_v12_debug_npc_suspended ||
                g_v12_debug_npc_isolation_mode != 0u) {
                v12_debug_reply(
                    response, OPENSPM_DEBUG_RESPONSE_BYTES,
                    "NPC one-pulse refused while suspend/isolation is active");
                return;
            }
            if (g_v12_debug_npc_one_pulse.valid) {
                v12_debug_reply(
                    response, OPENSPM_DEBUG_RESPONSE_BYTES,
                    "NPC one-pulse refused: ordinal=%u index=%u still has %u ticks remaining; use 'npcs pulseone off' first",
                    (unsigned)g_v12_debug_npc_one_pulse_active_ordinal,
                    (unsigned)g_v12_debug_npc_one_pulse_table_index,
                    (unsigned)g_v12_debug_npc_one_pulse_frames);
                return;
            }

            for (i = 0u; i < num; ++i) {
                uint32_t address =
                    entries + i * R8PE01_DEBUG_NPC_ENTRY_SIZE;
                uint32_t id = 0u;
                uint32_t flag8 = 0u;
                if (!v8_read32(address + R8PE01_DEBUG_NPC_ENTRY_ID, &id) ||
                    !v8_read32(address + R8PE01_DEBUG_NPC_ENTRY_FLAG8, &flag8) ||
                    (flag8 & R8PE01_DEBUG_NPC_ACTIVE) == 0u)
                    continue;
                if (active_ordinal == wanted_ordinal) {
                    chosen_index = i;
                    chosen_address = address;
                    chosen_id = id;
                    chosen_flag8 = flag8;
                    (void)v12_debug_read_cstring(
                        address + R8PE01_DEBUG_NPC_ENTRY_NAME,
                        chosen_name, sizeof(chosen_name));
                    break;
                }
                ++active_ordinal;
            }
            if (chosen_index == UINT32_MAX) {
                v12_debug_reply(
                    response, OPENSPM_DEBUG_RESPONSE_BYTES,
                    "NPC one-pulse ordinal %u not found; active population=%u",
                    (unsigned)wanted_ordinal, (unsigned)active_ordinal);
                return;
            }
            if (!v12_debug_get_current_map(
                    g_v12_debug_npc_one_pulse_map,
                    sizeof(g_v12_debug_npc_one_pulse_map))) {
                memset(g_v12_debug_npc_one_pulse_map, 0,
                       sizeof(g_v12_debug_npc_one_pulse_map));
                v12_debug_reply(
                    response, OPENSPM_DEBUG_RESPONSE_BYTES,
                    "NPC one-pulse refused: current map identity unavailable");
                return;
            }

            memset(&g_v12_debug_npc_one_pulse, 0,
                   sizeof(g_v12_debug_npc_one_pulse));
            g_v12_debug_npc_one_pulse.address = chosen_address;
            g_v12_debug_npc_one_pulse.id = chosen_id;
            g_v12_debug_npc_one_pulse.flag8 = chosen_flag8;
            g_v12_debug_npc_one_pulse.valid = 1u;
            g_v12_debug_npc_one_pulse_entries = entries;
            g_v12_debug_npc_one_pulse_frames = pulse_frames;
            g_v12_debug_npc_one_pulse_active_ordinal = wanted_ordinal;
            g_v12_debug_npc_one_pulse_table_index = chosen_index;
            strncpy_s(
                g_v12_debug_npc_one_pulse_name,
                sizeof(g_v12_debug_npc_one_pulse_name),
                chosen_name, _TRUNCATE);

            v12_debug_apply_npc_one_pulse();
            v12_debug_reply(
                response, OPENSPM_DEBUG_RESPONSE_BYTES,
                "NPC one-pulse=on ordinal=%u index=%u id=%u name=%s ticks=%u; only flag8.active is suppressed and auto-restored",
                (unsigned)wanted_ordinal,
                (unsigned)chosen_index,
                (unsigned)chosen_id,
                chosen_name[0] != '\0' ? chosen_name : "(unnamed)",
                (unsigned)g_v12_debug_npc_one_pulse_frames);
            return;
        }

        if (strcmp(arg1, "suspend") == 0 ||
            strcmp(arg1, "unload") == 0 ||
            strcmp(arg1, "load") == 0) {
            int enable;
            uint32_t pulse_frames = 0u;
            if (strcmp(arg1, "unload") == 0) enable = 1;
            else if (strcmp(arg1, "load") == 0) enable = 0;
            else {
                if (count >= 3 && strcmp(arg2, "pulse") == 0) {
                    if (count < 4 ||
                        !v12_debug_parse_u32(arg3, &pulse_frames) ||
                        pulse_frames < 1u || pulse_frames > 120u) {
                        v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                                        "usage: npcs suspend pulse <1..120 safe-frame ticks>");
                        return;
                    }
                    enable = 1;
                } else {
                    if (count < 3 ||
                        (strcmp(arg2, "on") != 0 && strcmp(arg2, "off") != 0)) {
                        v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                                        "usage: npcs suspend on|off | npcs suspend pulse <1..120>");
                        return;
                    }
                    enable = strcmp(arg2, "on") == 0;
                }
            }
            if (enable && g_v12_debug_npc_one_pulse.valid) {
                v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                                "NPC suspend refused while one-NPC pulse is active; use npcs pulseone off first");
                return;
            }
            if (enable && g_v12_debug_npc_isolation_mode != 0u) {
                v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                                "NPC suspend refused while safe isolation is active; use npcs isolate off first");
                return;
            }
            if (enable && num > OPENSPM_DEBUG_NPC_SNAPSHOT_MAX) {
                v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                                "NPC suspend refused: num=%u exceeds reversible snapshot limit=%u",
                                (unsigned)num,
                                (unsigned)OPENSPM_DEBUG_NPC_SNAPSHOT_MAX);
                return;
            }
            if (enable && !g_v12_debug_npc_suspended) {
                memset(g_v12_debug_npc_snapshot, 0,
                       sizeof(g_v12_debug_npc_snapshot));
                g_v12_debug_npc_snapshot_count = 0u;
                g_v12_debug_npc_snapshot_entries = entries;
                memset(g_v12_debug_npc_suspend_map, 0,
                       sizeof(g_v12_debug_npc_suspend_map));
                if (!v12_debug_get_current_map(
                        g_v12_debug_npc_suspend_map,
                        sizeof(g_v12_debug_npc_suspend_map))) {
                    g_v12_debug_npc_snapshot_entries = 0u;
                    v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                                    "NPC suspend refused: current map identity unavailable");
                    return;
                }
                g_v12_debug_npc_suspended = 1u;
                g_v12_debug_npc_suspend_pulse_frames = pulse_frames;
                v12_debug_apply_npc_suspend();
            } else if (!enable && g_v12_debug_npc_suspended) {
                g_v12_debug_npc_suspend_pulse_frames = 0u;
                g_v12_debug_npc_suspended = 0u;
                v12_debug_restore_npcs();
                memset(g_v12_debug_npc_suspend_map, 0,
                       sizeof(g_v12_debug_npc_suspend_map));
            }
            v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                            "NPC suspend=%s snapshots=%u pulse_remaining=%u; active-bit suppression is invasive and auto-pulse is preferred",
                            g_v12_debug_npc_suspended ? "on" : "off",
                            (unsigned)g_v12_debug_npc_snapshot_count,
                            (unsigned)g_v12_debug_npc_suspend_pulse_frames);
            return;
        }
        v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                        "usage: npcs [info] | npcs list | npcs entry <index> | npcs freeze on|off | npcs isolate off|freeze|hide [keep] | npcs pulseone <active-ordinal> <ticks>|off | npcs suspend on|off|pulse <ticks>");
        return;
    }

    if (strcmp(op, "freeze8") == 0 || strcmp(op, "freeze16") == 0 ||
        strcmp(op, "freeze32") == 0) {
        uint8_t width = strcmp(op, "freeze8") == 0 ? 1u :
                        (strcmp(op, "freeze16") == 0 ? 2u : 4u);
        uint32_t slot;
        if (count != 3 ||
            !v12_debug_parse_u32(arg1, &address) ||
            !v12_debug_parse_u32(arg2, &value) ||
            (width == 1u && value > UINT8_MAX) ||
            (width == 2u && value > UINT16_MAX)) {
            v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                            "usage: %s <address> <value>", op);
            return;
        }
        for (slot = 0u; slot < OPENSPM_DEBUG_FREEZE_SLOTS; ++slot) {
            if (g_v12_debug_freezes[slot].active &&
                g_v12_debug_freezes[slot].address == address) break;
        }
        if (slot == OPENSPM_DEBUG_FREEZE_SLOTS) {
            for (slot = 0u; slot < OPENSPM_DEBUG_FREEZE_SLOTS; ++slot)
                if (!g_v12_debug_freezes[slot].active) break;
        }
        if (slot == OPENSPM_DEBUG_FREEZE_SLOTS) {
            v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                            "freeze table full (%u slots)",
                            (unsigned)OPENSPM_DEBUG_FREEZE_SLOTS);
            return;
        }
        g_v12_debug_freezes[slot].address = address;
        g_v12_debug_freezes[slot].value = value;
        g_v12_debug_freezes[slot].width = width;
        g_v12_debug_freezes[slot].active = 1u;
        v12_debug_apply_freezes();
        v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                        "freeze%u [0x%08X] = 0x%08X slot=%u",
                        (unsigned)(width * 8u), (unsigned)address,
                        (unsigned)value, (unsigned)slot);
        return;
    }

    if (strcmp(op, "unfreeze") == 0) {
        uint32_t removed = 0u;
        if (count != 2) {
            v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                            "usage: unfreeze <address|all>");
            return;
        }
        if (strcmp(arg1, "all") == 0) {
            for (i = 0u; i < OPENSPM_DEBUG_FREEZE_SLOTS; ++i) {
                if (g_v12_debug_freezes[i].active) ++removed;
                memset(&g_v12_debug_freezes[i], 0,
                       sizeof(g_v12_debug_freezes[i]));
            }
        } else {
            if (!v12_debug_parse_u32(arg1, &address)) {
                v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                                "usage: unfreeze <address|all>");
                return;
            }
            for (i = 0u; i < OPENSPM_DEBUG_FREEZE_SLOTS; ++i) {
                if (g_v12_debug_freezes[i].active &&
                    g_v12_debug_freezes[i].address == address) {
                    memset(&g_v12_debug_freezes[i], 0,
                           sizeof(g_v12_debug_freezes[i]));
                    ++removed;
                }
            }
        }
        v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                        "unfreeze removed=%u", (unsigned)removed);
        return;
    }

    if (strcmp(op, "freezes") == 0) {
        size_t used = 0u;
        uint32_t listed = 0u;
        for (i = 0u; i < OPENSPM_DEBUG_FREEZE_SLOTS; ++i) {
            int written;
            if (!g_v12_debug_freezes[i].active) continue;
            written = _snprintf_s(
                response + used, OPENSPM_DEBUG_RESPONSE_BYTES - used,
                _TRUNCATE, "%s#%u:%u@%08X=%08X",
                listed == 0u ? "" : " ",
                (unsigned)i,
                (unsigned)(g_v12_debug_freezes[i].width * 8u),
                (unsigned)g_v12_debug_freezes[i].address,
                (unsigned)g_v12_debug_freezes[i].value);
            if (written < 0) break;
            used += (size_t)written;
            ++listed;
            if (used + 48u >= OPENSPM_DEBUG_RESPONSE_BYTES) break;
        }
        if (listed == 0u)
            v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                            "no persistent freezes");
        return;
    }

    if (strcmp(op, "story") == 0) {
        gp = v12_debug_spmario_globals();
        if (gp == 0u) {
            v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                            "could not resolve SpmarioGlobals");
            return;
        }
        if (count >= 2) {
            if (!v12_debug_parse_u32(arg1, &value) ||
                !v8_write32(gp + R8PE01_DEBUG_GSW0_OFFSET, value)) {
                v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                                "usage: story [integer]");
                return;
            }
        }
        if (!v8_read32(gp + R8PE01_DEBUG_GSW0_OFFSET, &value)) {
            v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                            "could not read story/GSW(0)");
            return;
        }
        v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                        "story / GSW(0)=%d", (int32_t)value);
        return;
    }

    if (strcmp(op, "gsw") == 0) {
        uint32_t index;
        uint8_t byte_value;
        if (count < 2 || !v12_debug_parse_u32(arg1, &index) || index >= 2048u) {
            v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                            "usage: gsw <0..2047> [value]");
            return;
        }
        gp = v12_debug_spmario_globals();
        if (gp == 0u) {
            v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                            "could not resolve SpmarioGlobals");
            return;
        }
        if (index == 0u) {
            if (count >= 3) {
                if (!v12_debug_parse_u32(arg2, &value) ||
                    !v8_write32(gp + R8PE01_DEBUG_GSW0_OFFSET, value)) {
                    v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                                    "could not write GSW(0)");
                    return;
                }
            }
            if (!v8_read32(gp + R8PE01_DEBUG_GSW0_OFFSET, &value)) {
                v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                                "could not read GSW(0)");
                return;
            }
            v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                            "GSW(0)=%d", (int32_t)value);
            return;
        }
        if (count >= 3) {
            char *end = NULL;
            long signed_value = strtol(arg2, &end, 0);
            if (end == arg2 || *end != '\0' ||
                signed_value < -128 || signed_value > 127 ||
                !v12_debug_write8(gp + R8PE01_DEBUG_GSW_OFFSET + index,
                                  (uint8_t)(int8_t)signed_value)) {
                v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                                "GSW(n>0) value must fit signed byte (-128..127)");
                return;
            }
        }
        if (!v12_debug_read8(gp + R8PE01_DEBUG_GSW_OFFSET + index, &byte_value)) {
            v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                            "could not read GSW(%u)", (unsigned)index);
            return;
        }
        v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                        "GSW(%u)=%d", (unsigned)index, (int)(int8_t)byte_value);
        return;
    }

    if (strcmp(op, "warp") == 0) {
        uint32_t map_name;
        if (count != 2) {
            v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                            "usage: warp <map>");
            return;
        }
        if (seq != R8PE01_DEBUG_SEQ_GAME) {
            v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                            "warp refused: current seq=%u, wait until gameplay seq=2",
                            (unsigned)seq);
            return;
        }
        map_name = v12_debug_resolve_map(arg1);
        if (map_name == 0u) {
            v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                            "map not found in R8PE01 mapData: %s", arg1);
            return;
        }
        v12_debug_reset_all_controls();
        if (!v8_write32(R8PE01_DEBUG_NEXT_P0, map_name) ||
            !v8_write32(R8PE01_DEBUG_NEXT_P1, 0u) ||
            !v8_write32(R8PE01_DEBUG_NEXT_SEQ, R8PE01_DEBUG_SEQ_MAPCHANGE)) {
            v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                            "could not queue semantic map change");
            return;
        }
        v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                        "queued semantic map change to %s", arg1);
        return;
    }

    if (strcmp(op, "read8") == 0 || strcmp(op, "read16") == 0 ||
        strcmp(op, "read32") == 0) {
        if (count != 2 || !v12_debug_parse_u32(arg1, &address)) {
            v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                            "usage: %s <address>", op);
            return;
        }
        if (strcmp(op, "read8") == 0) {
            uint8_t v8;
            if (!v12_debug_read8(address, &v8)) goto bad_read;
            v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                            "[0x%08X] = 0x%02X (%u)",
                            (unsigned)address, (unsigned)v8, (unsigned)v8);
            return;
        }
        if (strcmp(op, "read16") == 0) {
            uint16_t v16;
            if (!v8_read16(address, &v16)) goto bad_read;
            v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                            "[0x%08X] = 0x%04X (%u)",
                            (unsigned)address, (unsigned)v16, (unsigned)v16);
            return;
        }
        if (!v8_read32(address, &value)) goto bad_read;
        v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                        "[0x%08X] = 0x%08X (%u)",
                        (unsigned)address, (unsigned)value, (unsigned)value);
        return;
bad_read:
        v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                        "guest address is not readable: 0x%08X",
                        (unsigned)address);
        return;
    }

    if (strcmp(op, "write8") == 0 || strcmp(op, "write16") == 0 ||
        strcmp(op, "write32") == 0) {
        int ok = 0;
        if (count != 3 ||
            !v12_debug_parse_u32(arg1, &address) ||
            !v12_debug_parse_u32(arg2, &value)) {
            v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                            "usage: %s <address> <value>", op);
            return;
        }
        if (strcmp(op, "write8") == 0 && value <= UINT8_MAX)
            ok = v12_debug_write8(address, (uint8_t)value);
        else if (strcmp(op, "write16") == 0 && value <= UINT16_MAX)
            ok = v8_write16(address, (uint16_t)value);
        else if (strcmp(op, "write32") == 0)
            ok = v8_write32(address, value);
        if (!ok) {
            v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                            "write failed or value out of range");
            return;
        }
        v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                        "%s [0x%08X] <- 0x%08X",
                        op, (unsigned)address, (unsigned)value);
        return;
    }

    if (strcmp(op, "dump") == 0) {
        size_t used = 0u;
        if (count != 3 ||
            !v12_debug_parse_u32(arg1, &address) ||
            !v12_debug_parse_u32(arg2, &length) ||
            length == 0u || length > 128u) {
            v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                            "usage: dump <address> <length 1..128>");
            return;
        }
        for (i = 0u; i < length; ++i) {
            uint8_t byte_value;
            int written;
            if (!v12_debug_read8(address + i, &byte_value)) {
                v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                                "dump crossed unreadable guest address at 0x%08X",
                                (unsigned)(address + i));
                return;
            }
            written = _snprintf_s(
                response + used, OPENSPM_DEBUG_RESPONSE_BYTES - used,
                _TRUNCATE, "%s%02X", (i == 0u ? "" : " "),
                (unsigned)byte_value);
            if (written < 0) break;
            used += (size_t)written;
            if (used + 4u >= OPENSPM_DEBUG_RESPONSE_BYTES) break;
        }
        return;
    }

    v12_debug_reply(response, OPENSPM_DEBUG_RESPONSE_BYTES,
                    "unknown command: %s (type help)", command);
}

static void v12_debug_poll(const openspm_m14_hostcall_state *state) {
    LONG generation;
    char command[OPENSPM_DEBUG_COMMAND_BYTES];
    char response[OPENSPM_DEBUG_RESPONSE_BYTES];
    if (g_v12_debug_mailbox == NULL) return;
    generation = InterlockedCompareExchange(
        (volatile LONG *)&g_v12_debug_mailbox->command_generation, 0, 0);
    if (generation <= 0 || generation == g_v12_debug_processed_generation)
        return;

    strncpy_s(command, sizeof(command), g_v12_debug_mailbox->command, _TRUNCATE);
    ++g_v12_debug_commands;
    if (g_v12_debug_commands <= 64u ||
        (g_v12_debug_commands & (g_v12_debug_commands - 1u)) == 0u) {
        char begin_message[256];
        _snprintf_s(begin_message, sizeof(begin_message), _TRUNCATE,
                    "M14 dev29 live debug command BEGIN n=%llu '%s'",
                    (unsigned long long)g_v12_debug_commands, command);
        backend_log(OPENSPM_LOG_INFO, begin_message);
    }

    v12_debug_execute(command, state, response);

    strncpy_s(g_v12_debug_mailbox->response,
              sizeof(g_v12_debug_mailbox->response), response, _TRUNCATE);
    MemoryBarrier();
    InterlockedExchange(
        (volatile LONG *)&g_v12_debug_mailbox->response_generation, generation);
    g_v12_debug_processed_generation = generation;
    if (g_v12_debug_commands <= 64u ||
        (g_v12_debug_commands & (g_v12_debug_commands - 1u)) == 0u) {
        char end_message[256];
        _snprintf_s(end_message, sizeof(end_message), _TRUNCATE,
                    "M14 dev29 live debug command END n=%llu '%s' -> '%s'",
                    (unsigned long long)g_v12_debug_commands,
                    command, response);
        backend_log(OPENSPM_LOG_INFO, end_message);
    }
}

static int v12_sticky_pressed_requested(void) {
    const char *value = getenv("OPENSPM_M14_STICKY_PRESSED");
    if (value == NULL || *value == '\0') return 1;
    if (strcmp(value, "0") == 0 ||
        strcmp(value, "off") == 0 || strcmp(value, "OFF") == 0 ||
        strcmp(value, "false") == 0 || strcmp(value, "FALSE") == 0)
        return 0;
    return 1;
}

static int v12_fifo_inline_mode_requested(void) {
    const char *value = getenv("OPENSPM_M14_FIFO_MODE");
    return value != NULL && strcmp(value, "inline") == 0;
}

static int v12_submit_gx_segment_inline(const uint8_t *bytes, uint32_t size) {
    int result = v9_submit_gx_segment(bytes, size);
    if (result != OPENSPM_OK) return result;
    if (UINT32_MAX - g_v12_fifo_since_publish < size)
        g_v12_fifo_since_publish = UINT32_MAX;
    else
        g_v12_fifo_since_publish += size;
    return OPENSPM_OK;
}

static int v12_publish_renderer_fifo_inline(void) {
    int result = OPENSPM_OK;
    char message[224];

    if (!g_v12_fifo_inline_enabled || g_v12_fifo_since_publish == 0u)
        return OPENSPM_OK;

    if (g_v6_gx.renderer.sync != NULL) {
        result = g_v6_gx.renderer.sync(
            g_v6_gx.renderer.context, OPENSPM_GX_SYNC_FIFO_PUBLISH);
        if (result != OPENSPM_OK) return result;
    }

    ++g_v12_fifo_inline_publishes;
    if (v9_should_log_count(g_v12_fifo_inline_publishes)) {
        snprintf(message, sizeof(message),
                 "M14 inline GX FIFO publish n=%llu bytes=%u; complete prefix parsed synchronously before Broadway resumes.",
                 (unsigned long long)g_v12_fifo_inline_publishes,
                 (unsigned)g_v12_fifo_since_publish);
        backend_log(OPENSPM_LOG_INFO, message);
    }
    g_v12_fifo_since_publish = 0u;
    return OPENSPM_OK;
}

/* dev120 same-thread variant of dev23's proven command-aware GX transport.
 * Drain mode delegates byte-for-byte to v9. Inline mode additionally cuts a
 * prefix around every 16 KiB only when the framer is back at OPCODE state,
 * submits that complete prefix, then asks the renderer to parse it synchronously.
 * Broadway cannot resume or mutate referenced MEM1/MEM2 until parsing returns.
 * DrawDone/readback/presentation remain the existing draining boundaries. */
static int OPENSPM_CALL v12_submit_gx_fifo_inline(
    void *context, const uint8_t *bytes, uint32_t size) {
    uint32_t i;
    uint32_t segment_start = 0u;
    int result = OPENSPM_OK;
    (void)context;

    if (!g_v12_fifo_inline_enabled)
        return v9_submit_gx_fifo(context, bytes, size);
    if (size != 0u && bytes == NULL) return OPENSPM_ERROR_INVALID_ARGUMENT;
    if (size == 0u) return OPENSPM_OK;

    if (g_v6_gx.display_list_active)
        return v9_submit_gx_fifo(context, bytes, size);

    if (!g_v9_fifo_framer_reported) {
        backend_log(OPENSPM_LOG_INFO,
                    "M14 command-aware GX FIFO framing active; DrawDone/token are recognized only at parsed BP command boundaries.");
        g_v9_fifo_framer_reported = 1u;
    }

    for (i = 0u; i < size; ++i) {
        uint32_t draw_done_count = 0u;

        g_v6_gx.fifo_shift =
            ((g_v6_gx.fifo_shift << 8u) | bytes[i]) & UINT64_C(0xFFFFFFFFFF);

        result = v9_framer_feed_byte(
            &g_v9_gx_framer, bytes[i], &draw_done_count);
        if (result != OPENSPM_OK) {
            char message[320];
            snprintf(message, sizeof(message),
                     "M14 GX FIFO framer rejected stream result=%d byte=0x%02X index=%u phase=%u opcode=0x%02X header=%u remaining=%u fifo_total=%llu.",
                     result, (unsigned)bytes[i], (unsigned)i,
                     (unsigned)g_v9_gx_framer.phase,
                     (unsigned)g_v9_gx_framer.opcode,
                     (unsigned)g_v9_gx_framer.header_used,
                     (unsigned)g_v9_gx_framer.remaining,
                     (unsigned long long)g_v6_gx.fifo_bytes);
            backend_log(OPENSPM_LOG_ERROR, message);
            return result;
        }

        if (draw_done_count != 0u) {
            const uint32_t segment_size = i + 1u - segment_start;
            uint32_t available = UINT32_MAX - g_v6_gx.pending_draw_done;
            uint32_t add = draw_done_count < available
                               ? draw_done_count : available;
            g_v6_gx.pending_draw_done += add;
            g_v9_fifo_semantic_draw_done += draw_done_count;

            result = v12_submit_gx_segment_inline(
                bytes + segment_start, segment_size);
            if (result != OPENSPM_OK) return result;

            if (g_v6_gx.renderer.sync != NULL) {
                result = g_v6_gx.renderer.sync(
                    g_v6_gx.renderer.context, OPENSPM_GX_SYNC_DRAW_DONE);
                if (result != OPENSPM_OK) return result;
            }
            g_v12_fifo_since_publish = 0u;
            segment_start = i + 1u;
            continue;
        }

        if (g_v9_gx_framer.phase == V9_GX_FRAME_OPCODE) {
            const uint32_t segment_size = i + 1u - segment_start;
            if (g_v12_fifo_since_publish >=
                    OPENSPM_V12_GX_INLINE_PUBLISH_INTERVAL ||
                segment_size >=
                    OPENSPM_V12_GX_INLINE_PUBLISH_INTERVAL -
                        g_v12_fifo_since_publish) {
                result = v12_submit_gx_segment_inline(
                    bytes + segment_start, segment_size);
                if (result != OPENSPM_OK) return result;
                result = v12_publish_renderer_fifo_inline();
                if (result != OPENSPM_OK) return result;
                segment_start = i + 1u;
            }
        }
    }

    if (segment_start < size) {
        result = v12_submit_gx_segment_inline(
            bytes + segment_start, size - segment_start);
    }
    return result;
}

static int OPENSPM_CALL v12_run(void *backend_context,
                                const openspm_launch_desc *launch) {
    int result;
    memset(&g_v12_nand_file, 0, sizeof(g_v12_nand_file));
    g_v12_nand_sdk_calls = 0u;
    g_v12_input_prev_down = 0u;
    g_v12_input_released = 0u;
    g_v12_input_repeat = 0u;
    g_v12_input_hold_frames = 0u;
    g_v12_input_observed_pressed = 0u;
    g_v12_input_carried_frames = 0u;
    g_v12_sticky_pressed_enabled =
        v12_sticky_pressed_requested() ? 1u : 0u;
    g_v12_fifo_inline_enabled =
        v12_fifo_inline_mode_requested() ? 1u : 0u;
    g_v12_fifo_since_publish = 0u;
    g_v12_fifo_inline_publishes = 0u;
    g_v12_debug_commands = 0u;
    memset(g_v12_debug_freezes, 0, sizeof(g_v12_debug_freezes));
    memset(g_v12_debug_npc_snapshot, 0, sizeof(g_v12_debug_npc_snapshot));
    g_v12_debug_npc_snapshot_count = 0u;
    g_v12_debug_npc_snapshot_entries = 0u;
    memset(g_v12_debug_npc_suspend_map, 0,
           sizeof(g_v12_debug_npc_suspend_map));
    g_v12_debug_npc_suspended = 0u;
    g_v12_debug_npc_suspend_pulse_frames = 0u;
    memset(&g_v12_debug_npc_one_pulse, 0,
           sizeof(g_v12_debug_npc_one_pulse));
    g_v12_debug_npc_one_pulse_entries = 0u;
    g_v12_debug_npc_one_pulse_frames = 0u;
    g_v12_debug_npc_one_pulse_active_ordinal = 0u;
    g_v12_debug_npc_one_pulse_table_index = 0u;
    memset(g_v12_debug_npc_one_pulse_map, 0,
           sizeof(g_v12_debug_npc_one_pulse_map));
    memset(g_v12_debug_npc_one_pulse_name, 0,
           sizeof(g_v12_debug_npc_one_pulse_name));
    g_v12_debug_npc_isolation_mode = 0u;
    g_v12_debug_npc_isolation_keep = 0u;
    g_v12_debug_speed_active = 0u;
    g_v12_debug_speed_multiplier = 1.0f;
    g_v12_debug_speed_walk_base = 0.0f;
    g_v12_debug_speed_dash_base = 0.0f;
    g_v12_debug_speed_character = 0u;
    memset(&g_v12_debug_checkpoint, 0, sizeof(g_v12_debug_checkpoint));
    if (!v12_prepare_save_root(launch)) {
        backend_log(OPENSPM_LOG_ERROR,
                    "M14 native NAND could not prepare writable user save directory.");
        return OPENSPM_ERROR_LOAD_FAILED;
    }
    if (g_v12_sticky_pressed_enabled) {
        backend_log(OPENSPM_LOG_INFO,
                    "M14 dev29 NAND/input + live experiment transport ready: pressed edges remain pending until SPM consumes them; held input remains physical frame-latched state.");
    } else {
        backend_log(OPENSPM_LOG_INFO,
                    "M14 dev123 sticky-pressed A/B disabled: buttonsPressed edges expire at the next native input latch; buttonsHeld remains unchanged physical frame-latched state.");
    }
    if (g_v12_fifo_inline_enabled) {
        backend_log(OPENSPM_LOG_INFO,
                    "M14 dev120 same-thread GX Inline pacing active: complete command prefixes publish around 16 KiB; mutable guest RAM remains producer-thread synchronous.");
    }
    v12_debug_open_mailbox();
    result = v10_run_base(backend_context, launch);
    v12_debug_close_mailbox();
    free(g_v12_save_root);
    g_v12_save_root = NULL;
    return result;
}

static int OPENSPM_CALL v12_handle_hostcall(void *context, uint32_t target,
                                            openspm_m14_hostcall_state *state) {
    int result;
    if (v9_hooks_dispatch(target, state)) return OPENSPM_OK; /* OpenSPM hooks (docs/HOOKS.md) */
    result = v12_handle_nand(target, state);
    if (result != OPENSPM_ERROR_NOT_FOUND) return result;

    if (target == R8PE01_SC_INIT) {
        result = v10_handle_hostcall_base(context, target, state);
        if (result == OPENSPM_OK) v12_publish_sysconf();
        return result;
    }
    if (target == R8PE01_KPAD_READ) {
        result = v10_handle_hostcall_base(context, target, state);
        if (result == OPENSPM_OK) v12_patch_kpad_status(state);
        return result;
    }
    if (target == R8PE01_M14_INPUT_PRESSED_TARGET) {
        result = v10_handle_hostcall_base(context, target, state);
        if (result == OPENSPM_OK && state != NULL)
            g_v12_input_observed_pressed |= state->gpr[3];
        return result;
    }
    if (target == OPENSPM_M14_HOST_EVENT_PRESENT_SAFEPOINT) {
        const uint32_t carried = g_v12_sticky_pressed_enabled
            ? (g_v9_input_pressed & ~g_v12_input_observed_pressed)
            : 0u;
        /* Safe once-per-frame guest-thread boundary: launcher/CLI never writes
           MEM1/MEM2 directly. Commands are consumed here while Broadway is
           paused, before the next simulation frame can observe mutations. */
        v12_debug_poll(state);
        v12_debug_apply_runtime_toggles();
        result = v10_handle_hostcall_base(context, target, state);
        if (result == OPENSPM_OK) {
            /* PRESENT_SAFEPOINT drains any remaining parser tail before the
               next frame, so the incremental publication byte count restarts. */
            g_v12_fifo_since_publish = 0u;
            /* v9 sampled the new physical frame above and replaced its edge
               mask. Re-publish only older edges that no guest pressed-getter
               observed. New edges remain naturally present. */
            if (g_v12_sticky_pressed_enabled) {
                g_v9_input_pressed |= carried;
                v12_log_input_carry(carried);
            }
            g_v12_input_observed_pressed = 0u;
            v12_latch_menu_input();
        }
        return result;
    }
    return v10_handle_hostcall_base(context, target, state);
}

static int OPENSPM_CALL v12_query_service(void *backend_context,
                                          const char *service_id,
                                          uint32_t requested_version,
                                          void *out_service) {
    int result = v10_query_service_base(backend_context, service_id,
                                        requested_version, out_service);
    if (result != OPENSPM_OK) return result;
    if (service_id != NULL &&
        strcmp(service_id, OPENSPM_M14_HOSTCALL_SERVICE_ID) == 0) {
        openspm_m14_hostcall_service *service =
            (openspm_m14_hostcall_service *)out_service;
        service->handle = v12_handle_hostcall;
    }
    if (service_id != NULL &&
        strcmp(service_id, OPENSPM_GX_FIFO_SERVICE_ID) == 0) {
        openspm_gx_fifo_service *service =
            (openspm_gx_fifo_service *)out_service;
        service->submit = v12_submit_gx_fifo_inline;
    }
    return OPENSPM_OK;
}

OPENSPM_EXPORT int OPENSPM_CALL openspm_backend_query_v10(
    uint32_t requested_abi, const openspm_host_api *host,
    openspm_backend_api *out_api) {
    int result = openspm_backend_query_v10_base(requested_abi, host, out_api);
    if (result != OPENSPM_OK) return result;
    out_api->backend_version = "14.0-dev29";
    out_api->run = v12_run;
    out_api->query_service = v12_query_service;
    return OPENSPM_OK;
}
