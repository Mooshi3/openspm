/* OpenSPM.ini presentation/effects settings for OpenSPMNative.
 *
 * The renderer, bridge and Aurora patches are configured through environment
 * variables (OPENSPM_*). This maps a small, validated set of player settings in
 * OpenSPM.ini onto those variables at startup, before the backend and renderer
 * DLLs load. A variable that is already set (launcher argument, console `set`)
 * always wins, so A/B launchers keep working. The future OpenSPM menu edits the
 * same file.
 *
 *   [Display]
 *   window_mode = windowed | borderless | exclusive
 *   refresh_hz = auto | <Hz>             ; exclusive fullscreen refresh
 *   aspect = 4:3 | stretch               ; 4:3 = keep the picture's aspect (16:9 with widescreen)
 *   widescreen = 0 | 1                   ; the game renders 16:9 (Wii TV setting); applies on restart
 *   display_sync = auto | off            ; lock the vanilla clock to the display
 *   frame_mode = vanilla | smooth | vanilla_timeline   ; see openspm_frame_modes
 *   interp_camera_views = 1 | 0          ; smooth: interpolate the game's camera views
 *   [Effects]
 *   copy_tags = 1 | 0                    ; CPU-moved EFB copies (star doors)
 *   copy_pixel_exact = 1 | 0             ; pixel-exact EFB copy rect
 *   bbox_logical = 1 | 0                 ; PE bbox in logical coords (pipes)
 *   [Controller]
 *   enabled = 1 | 0                      ; XInput gamepad
 *   map = a=2,b=B,x=1,y=A,start=PLUS,back=MINUS   ; see openspm_pad_xinput.h
 *   home_opens_menu = 1 | 0              ; HOME (Guide / home key) opens the F1 menu
 *   stick_pointer = 1 | 0                ; right stick moves the Wii pointer (openspm/pointer_input.h)
 *   pointer_speed = 1..100               ; percent, default 50
 *   pointer_deadzone = 0..100            ; percent of full tilt (clamped to 90), default 20
 *   rumble = 1 | 0                       ; gamepad rumbles with the game's Wii Remote motor
 *   rumble_strength = 0..100             ; percent, default 70
 *   [Keyboard]
 *   map = up=Up/W,...,home=Home          ; see include/openspm/keymap.h
 *   [Overlay]
 *   show_fps = 0 | 1                     ; FPS counter (F1 menu -> Debug)
 *   pause_game = 1 | 0                   ; the game (and its clock) stops while the menu is open
 *   [Game]                               ; (path / wit: launcher/native_game.h)
 *   turbo = 1 | 0                        ; fast-forward hotkey ([Keyboard] turbo=, gamepad rs=TURBO)
 *   turbo_speed = 2 | 3 | 4 | unlimited  ; game speed while turbo is engaged
 *   turbo_mode = hold | toggle           ; hold the hotkey, or press it to switch on/off
 *   [Mods]
 *   enabled = 0 | 1                      ; load <exe dir>\mods\*.dll (docs/MODDING.md)
 *   [Audio]                              ; experimental (docs/AUDIO-PLAN.md)
 *   enabled = 0 | 1                      ; game audio through the AX HLE (OPENSPM_AUDIO)
 *   volume = 0..100                      ; output volume in percent (default 100)
 *   [TexturePacks]                       ; Dolphin HD packs (docs/TEXTURE-PACKS.md)
 *   enabled = 1 | 0                      ; index <folder>\<pack>\ at startup
 *   folder = <path>                      ; default texturepacks (next to the exe)
 *   cache_mb = 512 | 1024 | 2048 | 4096  ; GPU cache for decoded pack textures
 *   dump = 0 | 1                         ; write unreplaced textures as tex1_ PNGs
 *   (order = A|B and pack.<name> = 0|1 are read from the file by the renderer.)
 *
 * Portable C (no Windows headers) so it is unit-tested on the host. */
#ifndef OPENSPM_NATIVE_SETTINGS_H
#define OPENSPM_NATIVE_SETTINGS_H

#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct openspm_setting {
    const char *section;
    const char *key;
    const char *env;
    const char *allowed; /* '|'-separated, case-insensitive; "#hz" = auto or 24..1000,
                            "#pct" = integer 0..100 */
} openspm_setting;

static const openspm_setting openspm_settings_table[] = {
    {"Display", "window_mode", "OPENSPM_WINDOW_MODE", "windowed|borderless|exclusive"},
    {"Display", "refresh_hz", "OPENSPM_REFRESH_HZ", "#hz"},
    {"Display", "aspect", "OPENSPM_ASPECT", "4:3|stretch"},
    {"Display", "widescreen", "OPENSPM_WIDESCREEN", "0|1"},
    {"Display", "display_sync", "OPENSPM_M14_DISPLAY_SYNC", "auto|off"},
    {"Display", "frame_mode", "OPENSPM_FRAME_MODE", "vanilla|smooth|vanilla_timeline"},
    {"Display", "interp_camera_views", "OPENSPM_M14_INTERP_VIEW", "1|0"},
    {"Effects", "copy_tags", "OPENSPM_M14_COPY_TAGS", "1|0"},
    {"Effects", "copy_pixel_exact", "OPENSPM_M14_COPY_PIXEL_EXACT", "1|0"},
    {"Effects", "bbox_logical", "OPENSPM_M14_BBOX_LOGICAL", "1|0"},
    {"Controller", "enabled", "OPENSPM_PAD", "1|0"},
    {"Controller", "map", "OPENSPM_PAD_MAP", "*"},
    {"Controller", "home_opens_menu", "OPENSPM_HOME_MENU", "1|0"},
    {"Controller", "stick_pointer", "OPENSPM_STICK_POINTER", "1|0"},
    {"Controller", "pointer_speed", "OPENSPM_POINTER_SPEED", "#pct"},
    {"Controller", "pointer_deadzone", "OPENSPM_POINTER_DEADZONE", "#pct"},
    {"Controller", "rumble", "OPENSPM_RUMBLE", "1|0"},
    {"Controller", "rumble_strength", "OPENSPM_RUMBLE_STRENGTH", "#pct"},
    {"Keyboard", "map", "OPENSPM_KEY_MAP", "*"},
    {"Overlay", "show_fps", "OPENSPM_SHOW_FPS", "1|0"},
    {"Overlay", "pause_game", "OPENSPM_PAUSE_GAME", "1|0"},
    {"Game", "turbo", "OPENSPM_TURBO", "1|0"},
    {"Game", "turbo_speed", "OPENSPM_TURBO_SPEED", "2|3|4|unlimited"},
    {"Game", "turbo_mode", "OPENSPM_TURBO_MODE", "hold|toggle"},
    {"Mods", "enabled", "OPENSPM_MODS", "1|0"},
    {"Debug", "profile", "OPENSPM_PROFILE", "1|0"},
    {"Audio", "enabled", "OPENSPM_AUDIO", "1|0"},
    {"Audio", "volume", "OPENSPM_AUDIO_VOLUME", "#pct"},
    {"TexturePacks", "enabled", "OPENSPM_TEXTURE_PACKS", "1|0"},
    {"TexturePacks", "folder", "OPENSPM_TEXTURE_PACK_DIR", "*"},
    {"TexturePacks", "cache_mb", "OPENSPM_TEXTURE_PACK_CACHE_MB", "512|1024|2048|4096"},
    {"TexturePacks", "dump", "OPENSPM_TEXTURE_DUMP", "1|0"},
};

/* [Display] frame_mode is one player choice that sets a group of presenter
   variables (the same ones M14-VANILLA-RUN.cmd sets for its timeline /
   timelinevanilla / interp flags). The group is all-or-nothing: when any of
   its variables is already set, a launcher flag chose the presenter and the
   whole group is left alone, so a flag never mixes with half a preset.
     vanilla          nothing extra (one image per game frame, FIFO vsync)
     smooth           timeline + draw tags + frame interpolation ("interp")
     vanilla_timeline present timeline in vanilla mode ("timelinevanilla") */
#define OPENSPM_FRAME_MODE_VARS 5
static const char *const openspm_frame_mode_env[OPENSPM_FRAME_MODE_VARS] = {
    "OPENSPM_M14_ASYNC_PRESENTER", "OPENSPM_M14_PRESENT_TIMELINE", "OPENSPM_M14_PRESENT_TIMELINE_MODE",
    "OPENSPM_M14_DRAW_TAGS", "OPENSPM_M14_INTERP"};

typedef struct openspm_frame_mode {
    const char *name;
    const char *values[OPENSPM_FRAME_MODE_VARS]; /* NULL = leave unset (consumer default: off) */
} openspm_frame_mode;

static const openspm_frame_mode openspm_frame_modes[] = {
    {"vanilla", {NULL, NULL, NULL, NULL, NULL}},
    {"smooth", {"1", "1", "smooth", "1", "1"}},
    {"vanilla_timeline", {"1", "1", "vanilla", NULL, NULL}},
};

typedef struct openspm_settings_io {
    int (*env_is_set)(void *user, const char *name);
    int (*set_env)(void *user, const char *name, const char *value);
    void (*log)(void *user, const char *line);
    void *user;
} openspm_settings_io;

static int openspm_settings_iequal(const char *a, const char *b, size_t blen) {
    size_t i;
    if (strlen(a) != blen) return 0;
    for (i = 0; i < blen; ++i)
        if (tolower((unsigned char)a[i]) != tolower((unsigned char)b[i])) return 0;
    return 1;
}

static int openspm_settings_value_ok(const char *allowed, const char *value) {
    const char *p = allowed;
    if (strcmp(allowed, "*") == 0) return strlen(value) < 200u; /* free-form, checked by its consumer */
    if (strcmp(allowed, "#pct") == 0) {
        char *end = NULL;
        long pct = strtol(value, &end, 10);
        return end != value && *end == '\0' && pct >= 0 && pct <= 100;
    }
    if (strcmp(allowed, "#hz") == 0) {
        char *end = NULL;
        double hz;
        if (openspm_settings_iequal(value, "auto", 4)) return 1;
        hz = strtod(value, &end);
        return end != value && *end == '\0' && hz >= 24.0 && hz <= 1000.0;
    }
    while (*p) {
        const char *bar = strchr(p, '|');
        size_t len = bar ? (size_t)(bar - p) : strlen(p);
        if (openspm_settings_iequal(value, p, len)) return 1;
        if (!bar) break;
        p = bar + 1;
    }
    return 0;
}

static const openspm_frame_mode *openspm_frame_mode_find(const char *value) {
    size_t i;
    for (i = 0; i < sizeof openspm_frame_modes / sizeof openspm_frame_modes[0]; ++i)
        if (openspm_settings_iequal(value, openspm_frame_modes[i].name, strlen(openspm_frame_modes[i].name)))
            return &openspm_frame_modes[i];
    return NULL;
}

/* Sets the presenter group for a validated frame_mode unless a launcher flag
   already set part of it. Returns the number of variables set. */
static int openspm_settings_apply_frame_mode(const char *value, const openspm_settings_io *io) {
    const openspm_frame_mode *mode = openspm_frame_mode_find(value);
    char msg[256];
    int i, applied = 0;
    if (!mode) return 0;
    for (i = 0; i < OPENSPM_FRAME_MODE_VARS; ++i) {
        if (io->env_is_set && io->env_is_set(io->user, openspm_frame_mode_env[i])) {
            snprintf(msg, sizeof msg, "OpenSPM.ini [Display] frame_mode=%s overridden by %s already set", value,
                     openspm_frame_mode_env[i]);
            if (io->log) io->log(io->user, msg);
            return 0;
        }
    }
    snprintf(msg, sizeof msg, "OpenSPM.ini [Display] frame_mode=%s:", mode->name);
    for (i = 0; i < OPENSPM_FRAME_MODE_VARS; ++i) {
        if (!mode->values[i] || !io->set_env || !io->set_env(io->user, openspm_frame_mode_env[i], mode->values[i]))
            continue;
        ++applied;
        snprintf(msg + strlen(msg), sizeof msg - strlen(msg), " %s=%s", openspm_frame_mode_env[i], mode->values[i]);
    }
    if (applied && io->log) io->log(io->user, msg);
    return applied;
}

static char *openspm_settings_trim(char *s) {
    char *end;
    while (*s && isspace((unsigned char)*s)) ++s;
    end = s + strlen(s);
    while (end > s && isspace((unsigned char)end[-1])) --end;
    *end = '\0';
    return s;
}

/* Applies `text` (the file contents, NUL-terminated). Returns the number of
   variables set; unknown keys and invalid values are logged and skipped. */
static int openspm_settings_apply(const char *text, const openspm_settings_io *io) {
    char section[64] = "";
    char line[512], msg[640];
    const char *cursor = text;
    int applied = 0;
    if (!text || !io) return 0;
    if ((unsigned char)cursor[0] == 0xEF && (unsigned char)cursor[1] == 0xBB && (unsigned char)cursor[2] == 0xBF)
        cursor += 3; /* UTF-8 BOM (Notepad) */
    while (*cursor) {
        size_t len = strcspn(cursor, "\r\n");
        char *s, *eq, *key, *value, *comment;
        size_t i;
        int known = 0;
        if (len >= sizeof line) len = sizeof line - 1;
        memcpy(line, cursor, len);
        line[len] = '\0';
        cursor += strcspn(cursor, "\r\n");
        while (*cursor == '\r' || *cursor == '\n') ++cursor;
        s = openspm_settings_trim(line);
        if (*s == '\0' || *s == ';' || *s == '#') continue;
        if (*s == '[') {
            char *close = strchr(s, ']');
            if (close) {
                *close = '\0';
                snprintf(section, sizeof section, "%s", openspm_settings_trim(s + 1));
            }
            continue;
        }
        eq = strchr(s, '=');
        if (!eq) continue;
        *eq = '\0';
        key = openspm_settings_trim(s);
        value = eq + 1;
        comment = strpbrk(value, ";#");
        if (comment) *comment = '\0';
        value = openspm_settings_trim(value);
        for (i = 0; i < sizeof openspm_settings_table / sizeof openspm_settings_table[0]; ++i) {
            const openspm_setting *setting = &openspm_settings_table[i];
            if (!openspm_settings_iequal(section, setting->section, strlen(setting->section)) ||
                !openspm_settings_iequal(key, setting->key, strlen(setting->key)))
                continue;
            known = 1;
            if (*value == '\0') break; /* empty = default */
            if (!openspm_settings_value_ok(setting->allowed, value)) {
                snprintf(msg, sizeof msg, "OpenSPM.ini [%s] %s=%s ignored (allowed: %s)", setting->section,
                         setting->key, value, strcmp(setting->allowed, "#hz") == 0 ? "auto|24..1000" : setting->allowed);
                if (io->log) io->log(io->user, msg);
            } else if (io->env_is_set && io->env_is_set(io->user, setting->env)) {
                snprintf(msg, sizeof msg, "OpenSPM.ini [%s] %s=%s overridden by %s already set", setting->section,
                         setting->key, value, setting->env);
                if (io->log) io->log(io->user, msg);
            } else if (io->set_env && io->set_env(io->user, setting->env, value)) {
                ++applied;
                snprintf(msg, sizeof msg, "OpenSPM.ini [%s] %s=%s -> %s", setting->section, setting->key, value,
                         setting->env);
                if (io->log) io->log(io->user, msg);
                if (strcmp(setting->env, "OPENSPM_FRAME_MODE") == 0)
                    applied += openspm_settings_apply_frame_mode(value, io);
            }
            break;
        }
        /* Only sections this table owns; [OpenSPM] belongs to the OpenSPM.exe launcher. */
        if (!known && io->log && (openspm_settings_iequal(section, "Display", 7) ||
                                  openspm_settings_iequal(section, "Effects", 7) ||
                                  openspm_settings_iequal(section, "Controller", 10) ||
                                  openspm_settings_iequal(section, "Keyboard", 8) ||
                                  openspm_settings_iequal(section, "Overlay", 7))) {
            snprintf(msg, sizeof msg, "OpenSPM.ini [%s] %s: unknown setting ignored", section, key);
            io->log(io->user, msg);
        }
    }
    return applied;
}

#endif
