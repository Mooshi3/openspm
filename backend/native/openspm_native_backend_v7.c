/*
 * M14 standalone-native backend v7.
 *
 * v6 owns the persistent Wii bootstrap/MMIO and GX/PE renderer seam. This
 * layer promotes the successful diagnostic's semantic function side exits
 * into a reusable OpenSPM service so the player-local recompiled game can
 * return from PC-native GameFS/settings/input/save/cache/GX calls and continue
 * executing directly instead of terminating at FALLBACK_REQUIRED.
 *
 * Raw R8PE01 addresses are migration metadata, not the final mod API. Once the
 * native code generator emits named OpenSPM service calls directly this layer
 * can disappear without changing the platform services themselves.
 */

#define openspm_backend_query openspm_backend_query_v6_internal
#include "openspm_native_backend_v6.c"
#undef openspm_backend_query

#include "openspm/m14_hostcall_service.h"

#define R8PE01_MEM1_CACHED UINT32_C(0x80000000)
#define R8PE01_MEM1_UNCACHED UINT32_C(0xC0000000)
#define R8PE01_MEM2_CACHED UINT32_C(0x90000000)
#define R8PE01_MEM2_UNCACHED UINT32_C(0xD0000000)
#define R8PE01_WPAD_READY_WORD UINT32_C(0x804F245C)

#define R8PE01_BUTTON_PLUS UINT32_C(0x00000100)
#define R8PE01_BUTTON_A    UINT32_C(0x00000800)

typedef struct openspm_v7_hostcall_state {
    uint32_t keyboard_down;
    uint32_t ios_handle_counter;
    uint64_t handled_calls;
} openspm_v7_hostcall_state;

static openspm_v7_hostcall_state g_v7_hostcall;

static void *v7_guest_ptr(uint32_t address, uint32_t size) {
    uint32_t offset;
    if (size > OPENSPM_WII_MEM2_SIZE) return NULL;

    if (address >= R8PE01_MEM1_CACHED) {
        offset = address - R8PE01_MEM1_CACHED;
        if (offset <= OPENSPM_WII_MEM1_SIZE && size <= OPENSPM_WII_MEM1_SIZE - offset)
            return g_backend.mem1 + offset;
    }
    if (address >= R8PE01_MEM1_UNCACHED) {
        offset = address - R8PE01_MEM1_UNCACHED;
        if (offset <= OPENSPM_WII_MEM1_SIZE && size <= OPENSPM_WII_MEM1_SIZE - offset)
            return g_backend.mem1 + offset;
    }
    if (address >= R8PE01_MEM2_CACHED) {
        offset = address - R8PE01_MEM2_CACHED;
        if (offset <= OPENSPM_WII_MEM2_SIZE && size <= OPENSPM_WII_MEM2_SIZE - offset)
            return g_backend.mem2 + offset;
    }
    if (address >= R8PE01_MEM2_UNCACHED) {
        offset = address - R8PE01_MEM2_UNCACHED;
        if (offset <= OPENSPM_WII_MEM2_SIZE && size <= OPENSPM_WII_MEM2_SIZE - offset)
            return g_backend.mem2 + offset;
    }
    return NULL;
}

static int v7_read16(uint32_t address, uint16_t *out_value) {
    const uint8_t *p = (const uint8_t *)v7_guest_ptr(address, 2u);
    if (p == NULL || out_value == NULL) return 0;
    *out_value = (uint16_t)(((uint16_t)p[0] << 8) | p[1]);
    return 1;
}

static int v7_write16(uint32_t address, uint16_t value) {
    uint8_t *p = (uint8_t *)v7_guest_ptr(address, 2u);
    if (p == NULL) return 0;
    p[0] = (uint8_t)(value >> 8);
    p[1] = (uint8_t)value;
    return 1;
}

static int v7_read32(uint32_t address, uint32_t *out_value) {
    const uint8_t *p = (const uint8_t *)v7_guest_ptr(address, 4u);
    if (p == NULL || out_value == NULL) return 0;
    *out_value = ((uint32_t)p[0] << 24) |
                 ((uint32_t)p[1] << 16) |
                 ((uint32_t)p[2] << 8) |
                 (uint32_t)p[3];
    return 1;
}

static int v7_write32(uint32_t address, uint32_t value) {
    uint8_t *p = (uint8_t *)v7_guest_ptr(address, 4u);
    if (p == NULL) return 0;
    p[0] = (uint8_t)(value >> 24);
    p[1] = (uint8_t)(value >> 16);
    p[2] = (uint8_t)(value >> 8);
    p[3] = (uint8_t)value;
    return 1;
}

static int v7_guest_path(uint32_t file_info, char out_path[65]) {
    const uint8_t *guest;
    uint32_t i;

    if (file_info < R8PE01_MEM1_CACHED + 0x40u || out_path == NULL) return 0;
    guest = (const uint8_t *)v7_guest_ptr(file_info - 0x40u, 64u);
    if (guest == NULL) return 0;

    for (i = 0; i < 64u; ++i) {
        uint8_t ch = guest[i];
        if (ch == 0u) {
            if (i == 0u) return 0;
            out_path[i] = '\0';
            return 1;
        }
        if (ch < 0x20u || ch >= 0x7Fu) return 0;
        out_path[i] = (char)ch;
    }
    return 0;
}

static int v7_gamefs_read_common(openspm_m14_hostcall_state *state,
                                 int asynchronous) {
    uint32_t file_info = state->gpr[3];
    uint32_t dest_address = state->gpr[4];
    uint32_t length = state->gpr[5];
    uint32_t offset = state->gpr[6];
    uint32_t callback = asynchronous ? state->gpr[7] : 0u;
    openspm_gamefs_handle handle = OPENSPM_GAMEFS_INVALID_HANDLE;
    uint64_t file_size = 0u;
    uint64_t read = 0u;
    char path[65];
    void *destination;
    int io_result;
    int32_t game_result;

    if (length > UINT32_C(0x08000000) ||
        (asynchronous && callback == 0u) ||
        !v7_guest_path(file_info, path)) {
        return OPENSPM_ERROR_NOT_FOUND;
    }
    destination = v7_guest_ptr(dest_address, length);
    if (destination == NULL && length != 0u) return OPENSPM_ERROR_NOT_FOUND;
    if (length != 0u) memset(destination, 0, length);

    io_result = v3_gamefs_open(&g_v3, path, &handle, &file_size);
    if (io_result == OPENSPM_OK) {
        (void)file_size;
        io_result = v3_gamefs_read(&g_v3, handle, offset,
                                   destination, length, &read);
        (void)v3_gamefs_close(&g_v3, handle);
    }
    game_result = io_result == OPENSPM_OK ? (int32_t)length : -1;

    if (asynchronous) {
        state->gpr[3] = 1u; /* request accepted */
        state->event_target = callback;
        state->event_argc = 2u;
        state->event_args[0] = (uint32_t)game_result;
        state->event_args[1] = file_info;
    } else {
        state->gpr[3] = (uint32_t)game_result;
    }
    state->pc = state->lr;
    return OPENSPM_OK;
}

static uint32_t v7_poll_pressed_buttons(void) {
    uint32_t down = 0u;
    uint32_t pressed;

    /* M14 correctness mapping only. The public input API will replace this
       direct Win32 polling before controller/mod work: Space/Z -> A,
       Enter -> Plus/Start. These are the two masks proven by the title run. */
    if ((GetAsyncKeyState(VK_SPACE) & 0x8000) != 0 ||
        (GetAsyncKeyState('Z') & 0x8000) != 0) {
        down |= R8PE01_BUTTON_A;
    }
    if ((GetAsyncKeyState(VK_RETURN) & 0x8000) != 0) {
        down |= R8PE01_BUTTON_PLUS;
    }

    pressed = down & ~g_v7_hostcall.keyboard_down;
    g_v7_hostcall.keyboard_down = down;
    return pressed;
}

static int OPENSPM_CALL v7_handle_hostcall(void *context,
                                           uint32_t target,
                                           openspm_m14_hostcall_state *state) {
    uint16_t ready;
    uint32_t work_ptr_address;
    uint32_t work_ptr;
    uint32_t display_list_size;
    int result;
    (void)context;

    if (state == NULL || state->header.struct_size < OPENSPM_M14_HOSTCALL_STATE_V1_SIZE)
        return OPENSPM_ERROR_INVALID_ARGUMENT;
    state->event_target = 0u;
    state->event_argc = 0u;
    memset(state->event_args, 0, sizeof(state->event_args));
    ++g_v7_hostcall.handled_calls;

    switch (target) {
    case UINT32_C(0x8026DFA0):
    case UINT32_C(0x8026E028):
        /* Immutable generated code + coherent host RAM: call/return barrier. */
        state->pc = state->lr;
        return OPENSPM_OK;

    case UINT32_C(0x80279CDC):
        return v7_gamefs_read_common(state, 0);
    case UINT32_C(0x80279BF4):
        return v7_gamefs_read_common(state, 1);

    case UINT32_C(0x80291DC8): /* GXBeginDisplayList */
        /* Replace the Wii FIFO-switching implementation with the neutral GX
           platform recorder. r3/r4 are the caller-owned list buffer/capacity. */
        result = openspm_gx_platform_begin_display_list(
            &g_v6_gx, state->gpr[3], state->gpr[4]);
        if (result != OPENSPM_OK) return result;
        state->pc = state->lr;
        return OPENSPM_OK;

    case UINT32_C(0x80291E78): /* GXEndDisplayList */
        display_list_size = 0u;
        result = openspm_gx_platform_end_display_list(
            &g_v6_gx, &display_list_size);
        if (result != OPENSPM_OK) return result;
        state->gpr[3] = display_list_size;
        state->pc = state->lr;
        return OPENSPM_OK;

    case UINT32_C(0x8023CEF0):
        /* Fresh-PC save startup: the game's save slots already exist in RAM.
           No save file means a successful empty logical save set. */
        work_ptr_address = state->gpr[13] + (uint32_t)(int32_t)-32080;
        if (v7_read32(work_ptr_address, &work_ptr) && work_ptr != 0u) {
            (void)v7_write32(work_ptr + UINT32_C(0x000), 0u);
            (void)v7_write32(work_ptr + UINT32_C(0x1AC), 0u);
            (void)v7_write32(work_ptr + UINT32_C(0x1B0), 0u);
            (void)v7_write32(work_ptr + UINT32_C(0x1B4), 0u);
            (void)v7_write32(work_ptr + UINT32_C(0x1B8), 0u);
        }
        state->pc = state->lr;
        return OPENSPM_OK;

    case UINT32_C(0x8029A500):
        /* Native capacity check: enough host save space; completion callback
           carries the SDK-visible result and command pointer. */
        if (state->gpr[5] != 0u) (void)v7_write32(state->gpr[5], 0u);
        state->gpr[3] = 0u;
        state->pc = state->lr;
        if (state->gpr[6] != 0u) {
            state->event_target = state->gpr[6];
            state->event_argc = 2u;
            state->event_args[0] = 0u;
            state->event_args[1] = state->gpr[7];
        }
        return OPENSPM_OK;

    case UINT32_C(0x80236D00):
        ready = 0u;
        (void)v7_read16(R8PE01_WPAD_READY_WORD, &ready);
        ready = (uint16_t)(ready | UINT16_C(0x0008));
        state->gpr[3] = ready;
        state->pc = state->lr;
        return OPENSPM_OK;

    case UINT32_C(0x80235AB8):
        state->gpr[3] = v7_poll_pressed_buttons();
        state->pc = state->lr;
        return OPENSPM_OK;

    case UINT32_C(0x802CD5DC):
        state->gpr[3] = 3u;
        state->pc = state->lr;
        return OPENSPM_OK;

    case UINT32_C(0x802A0150):
        ready = 0u;
        (void)v7_read16(R8PE01_WPAD_READY_WORD, &ready);
        (void)v7_write16(R8PE01_WPAD_READY_WORD,
                         (uint16_t)(ready | UINT16_C(0x0008)));
        state->gpr[3] = 0u;
        state->pc = state->lr;
        return OPENSPM_OK;

    case UINT32_C(0x8029A7E0):
        state->pc = state->lr;
        return OPENSPM_OK;
    case UINT32_C(0x8029A880):
        state->gpr[3] = 0u;
        state->pc = state->lr;
        return OPENSPM_OK;

    case UINT32_C(0x80282A68):
        /* VIGetDTVStatus on Wii reports whether a component-capable output is
           present. The PC-native renderer is inherently progressive-capable,
           so answer the semantic capability query directly instead of
           inventing a Wii component-cable detect register. */
        state->gpr[3] = 1u;
        state->pc = state->lr;
        return OPENSPM_OK;

    case UINT32_C(0x8028325C):
        /* The Wii VI setup bit-bangs the physical A/V encoder over Hollywood
           GPIO/I2C. There is no Wii encoder in the PC-native runtime: Aurora
           already owns scanout. Treat the completed transaction as successful
           and keep the raw GPIO registers fail-closed so this obsolete device
           stack cannot silently creep back into the native architecture. */
        state->gpr[3] = 1u;
        state->pc = state->lr;
        return OPENSPM_OK;

    /* Temporary IOS compatibility boundaries observed during natural-title
       bring-up. These are deliberately narrow per-function shims, not a Wii
       IPC/device emulator. They should disappear as each caller is lifted to
       a named OpenSPM service. */
    case UINT32_C(0x802961B8): /* IOS_Open */
        state->gpr[3] = 10u + (g_v7_hostcall.ios_handle_counter++ & 31u);
        state->pc = state->lr;
        return OPENSPM_OK;
    case UINT32_C(0x802960A0): /* IOS_OpenAsync */
        state->gpr[3] = 0u;
        state->pc = state->lr;
        return OPENSPM_OK;
    case UINT32_C(0x80296748): /* IOS_Write */
        state->gpr[3] = state->gpr[5];
        state->pc = state->lr;
        return OPENSPM_OK;
    case UINT32_C(0x802962D8): /* IOS_CloseAsync */
    case UINT32_C(0x80296398): /* IOS_Close */
    case UINT32_C(0x80296440): /* IOS_ReadAsync */
    case UINT32_C(0x80296540): /* IOS_Read */
    case UINT32_C(0x80296648): /* IOS_WriteAsync */
    case UINT32_C(0x80296850): /* IOS_SeekAsync */
    case UINT32_C(0x80296930): /* IOS_IoctlAsync */
    case UINT32_C(0x80296A68): /* IOS_Ioctl */
    case UINT32_C(0x80296CD4): /* IOS_IoctlvAsync */
    case UINT32_C(0x80296DB8): /* IOS_Ioctlv */
        state->gpr[3] = 0u;
        state->pc = state->lr;
        return OPENSPM_OK;

    default:
        return OPENSPM_ERROR_NOT_FOUND;
    }
}

static int OPENSPM_CALL v7_query_service(void *backend_context,
                                         const char *service_id,
                                         uint32_t requested_version,
                                         void *out_service) {
    if (service_id != NULL &&
        strcmp(service_id, OPENSPM_M14_HOSTCALL_SERVICE_ID) == 0) {
        openspm_m14_hostcall_service *service =
            (openspm_m14_hostcall_service *)out_service;
        if (service == NULL ||
            requested_version != OPENSPM_M14_HOSTCALL_SERVICE_VERSION ||
            service->header.struct_size < sizeof(*service)) {
            return OPENSPM_ERROR_ABI_MISMATCH;
        }
        memset(service, 0, sizeof(*service));
        service->header.struct_size = sizeof(*service);
        service->header.abi_version = OPENSPM_ABI_VERSION;
        service->context = &g_v7_hostcall;
        service->handle = v7_handle_hostcall;
        return OPENSPM_OK;
    }

    return v6_query_service(backend_context, service_id,
                            requested_version, out_service);
}

static int OPENSPM_CALL v7_run(void *backend_context,
                               const openspm_launch_desc *launch) {
    memset(&g_v7_hostcall, 0, sizeof(g_v7_hostcall));
    return v6_run(backend_context, launch);
}

OPENSPM_EXPORT int OPENSPM_CALL openspm_backend_query(
    uint32_t requested_abi,
    const openspm_host_api *host,
    openspm_backend_api *out_api) {
    int result = openspm_backend_query_v6_internal(requested_abi, host, out_api);
    if (result != OPENSPM_OK) return result;
    /* Canonical public ID for the strict native backend. Older migration
       layers retain their historical IDs internally; the active exported v7
       contract is what OpenSPMNative and future tooling should key on. */
    out_api->backend_id = "openspm.native";
    out_api->backend_version = "14.0-dev13";
    out_api->run = v7_run;
    out_api->query_service = v7_query_service;
    return OPENSPM_OK;
}
