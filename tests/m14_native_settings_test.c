#include "../launcher/native_settings.h"

#include <assert.h>
#include <stdio.h>
#include <string.h>

typedef struct fake_env {
    char names[32][64];
    char values[32][64];
    int count;
    int logs;
} fake_env;

static int env_is_set(void *user, const char *name) {
    fake_env *env = (fake_env *)user;
    int i;
    for (i = 0; i < env->count; ++i)
        if (strcmp(env->names[i], name) == 0) return 1;
    return 0;
}
static int set_env(void *user, const char *name, const char *value) {
    fake_env *env = (fake_env *)user;
    snprintf(env->names[env->count], sizeof env->names[0], "%s", name);
    snprintf(env->values[env->count], sizeof env->values[0], "%s", value);
    ++env->count;
    return 1;
}
static void log_line(void *user, const char *line) {
    ((fake_env *)user)->logs++;
    printf("  %s\n", line);
}
static const char *get(fake_env *env, const char *name) {
    int i;
    for (i = 0; i < env->count; ++i)
        if (strcmp(env->names[i], name) == 0) return env->values[i];
    return NULL;
}

int main(void) {
    fake_env env;
    openspm_settings_io io = {env_is_set, set_env, log_line, &env};
    const char *ini =
        "\xEF\xBB\xBF; OpenSPM settings\r\n"
        "[OpenSPM]\r\ngame_root=C:\\SPM\r\n"
        "[Display]\r\n"
        "window_mode = Exclusive   ; fullscreen\r\n"
        "refresh_hz=59.978\r\n"
        "aspect = sideways\r\n"
        "display_sync=\r\n"
        "[effects]\n"
        "copy_tags=0\n"
        "bogus=1\n"
        "[Controller]\n"
        "map = a=1, x=2 ; swap\n";
    memset(&env, 0, sizeof env);
    snprintf(env.names[0], sizeof env.names[0], "OPENSPM_M14_COPY_TAGS"); /* launcher arg wins */
    snprintf(env.values[0], sizeof env.values[0], "1");
    env.count = 1;
    assert(openspm_settings_apply(ini, &io) == 3);
    assert(strcmp(get(&env, "OPENSPM_PAD_MAP"), "a=1, x=2") == 0);
    assert(strcmp(get(&env, "OPENSPM_WINDOW_MODE"), "Exclusive") == 0);
    assert(strcmp(get(&env, "OPENSPM_REFRESH_HZ"), "59.978") == 0);
    assert(get(&env, "OPENSPM_ASPECT") == NULL);            /* invalid value */
    assert(get(&env, "OPENSPM_M14_DISPLAY_SYNC") == NULL);  /* empty = default */
    assert(strcmp(get(&env, "OPENSPM_M14_COPY_TAGS"), "1") == 0);
    assert(env.logs == 6); /* 3 applied + invalid + overridden + unknown */
    assert(openspm_settings_value_ok("#hz", "auto") && !openspm_settings_value_ok("#hz", "10") &&
           !openspm_settings_value_ok("#hz", "60x"));

    /* frame_mode=smooth sets the same group as M14-VANILLA-RUN.cmd `interp`. */
    memset(&env, 0, sizeof env);
    assert(openspm_settings_apply("[Display]\nframe_mode = Smooth\ninterp_camera_views=0\n", &io) == 7);
    assert(strcmp(get(&env, "OPENSPM_FRAME_MODE"), "Smooth") == 0);
    assert(strcmp(get(&env, "OPENSPM_M14_ASYNC_PRESENTER"), "1") == 0);
    assert(strcmp(get(&env, "OPENSPM_M14_PRESENT_TIMELINE"), "1") == 0);
    assert(strcmp(get(&env, "OPENSPM_M14_PRESENT_TIMELINE_MODE"), "smooth") == 0);
    assert(strcmp(get(&env, "OPENSPM_M14_DRAW_TAGS"), "1") == 0);
    assert(strcmp(get(&env, "OPENSPM_M14_INTERP"), "1") == 0);
    assert(strcmp(get(&env, "OPENSPM_M14_INTERP_VIEW"), "0") == 0);

    /* vanilla_timeline = `timelinevanilla`: no draw tags, no interpolation. */
    memset(&env, 0, sizeof env);
    assert(openspm_settings_apply("[Display]\nframe_mode=vanilla_timeline\n", &io) == 4);
    assert(strcmp(get(&env, "OPENSPM_M14_PRESENT_TIMELINE_MODE"), "vanilla") == 0);
    assert(get(&env, "OPENSPM_M14_DRAW_TAGS") == NULL && get(&env, "OPENSPM_M14_INTERP") == NULL);

    /* vanilla leaves every presenter variable unset (consumer default: off). */
    memset(&env, 0, sizeof env);
    assert(openspm_settings_apply("[Display]\nframe_mode=vanilla\n", &io) == 1);
    assert(get(&env, "OPENSPM_M14_ASYNC_PRESENTER") == NULL && get(&env, "OPENSPM_M14_INTERP") == NULL);

    /* A launcher flag (here `drawtags`) owns the presenter: the whole group is skipped. */
    memset(&env, 0, sizeof env);
    snprintf(env.names[0], sizeof env.names[0], "OPENSPM_M14_DRAW_TAGS");
    snprintf(env.values[0], sizeof env.values[0], "1");
    env.count = 1;
    env.logs = 0;
    assert(openspm_settings_apply("[Display]\nframe_mode=smooth\n", &io) == 1);
    assert(get(&env, "OPENSPM_M14_INTERP") == NULL && get(&env, "OPENSPM_M14_ASYNC_PRESENTER") == NULL);
    assert(env.logs == 2); /* frame_mode applied + group overridden */

    /* [Display] widescreen = 0|1 -> OPENSPM_WIDESCREEN (backend SYSCONF IPL.AR + renderer 16:9). */
    memset(&env, 0, sizeof env);
    assert(openspm_settings_apply("[Display]\nwidescreen = 1\naspect=4:3\n", &io) == 2);
    assert(strcmp(get(&env, "OPENSPM_WIDESCREEN"), "1") == 0);
    assert(strcmp(get(&env, "OPENSPM_ASPECT"), "4:3") == 0);
    memset(&env, 0, sizeof env);
    assert(openspm_settings_apply("[Display]\nwidescreen=16:9\n", &io) == 0 && env.count == 0); /* invalid */
    memset(&env, 0, sizeof env);
    snprintf(env.names[0], sizeof env.names[0], "OPENSPM_WIDESCREEN"); /* launcher `widescreen` wins */
    snprintf(env.values[0], sizeof env.values[0], "1");
    env.count = 1;
    assert(openspm_settings_apply("[Display]\nwidescreen=0\n", &io) == 0);
    assert(strcmp(get(&env, "OPENSPM_WIDESCREEN"), "1") == 0);

    /* Invalid frame modes set nothing. */
    memset(&env, 0, sizeof env);
    assert(openspm_settings_apply("[Display]\nframe_mode=120fps\n", &io) == 0 && env.count == 0);

    /* OpenSPM menu: keyboard map, HOME and pause switches. */
    memset(&env, 0, sizeof env);
    assert(openspm_settings_apply("[Keyboard]\nmap = up=W/Up, home=Tab ; custom\n[Overlay]\npause_game = 0\n"
                                  "[Controller]\nhome_opens_menu=2\n", &io) == 2);
    assert(strcmp(get(&env, "OPENSPM_KEY_MAP"), "up=W/Up, home=Tab") == 0);
    assert(strcmp(get(&env, "OPENSPM_PAUSE_GAME"), "0") == 0);
    assert(get(&env, "OPENSPM_HOME_MENU") == NULL); /* invalid value */
    /* [Audio]: off unless enabled; volume is an integer percentage. */
    memset(&env, 0, sizeof env);
    assert(openspm_settings_apply("[Audio]\nenabled = 1\nvolume = 70\n", &io) == 2);
    assert(strcmp(get(&env, "OPENSPM_AUDIO"), "1") == 0);
    assert(strcmp(get(&env, "OPENSPM_AUDIO_VOLUME"), "70") == 0);
    memset(&env, 0, sizeof env);
    assert(openspm_settings_apply("[Audio]\nenabled =\nvolume = 150\n", &io) == 0 && env.count == 0);
    assert(openspm_settings_value_ok("#pct", "0") && openspm_settings_value_ok("#pct", "100") &&
           !openspm_settings_value_ok("#pct", "-1") && !openspm_settings_value_ok("#pct", "50%"));
    /* [Game] fast-forward: next to the launcher's path/wit keys, which stay the launcher's. */
    memset(&env, 0, sizeof env);
    assert(openspm_settings_apply("[Game]\npath = C:\\spm.iso\nturbo = 0\nturbo_speed = Unlimited\nturbo_mode=toggle\n",
                                  &io) == 3);
    assert(strcmp(get(&env, "OPENSPM_TURBO"), "0") == 0);
    assert(strcmp(get(&env, "OPENSPM_TURBO_SPEED"), "Unlimited") == 0); /* the backend compares case-insensitively */
    assert(strcmp(get(&env, "OPENSPM_TURBO_MODE"), "toggle") == 0);
    memset(&env, 0, sizeof env);
    assert(openspm_settings_apply("[Game]\nturbo_speed = 8\nturbo_mode = press\nturbo = yes\n", &io) == 0 &&
           env.count == 0);
    puts("PASS: OpenSPM.ini settings parser");
    return 0;
}
