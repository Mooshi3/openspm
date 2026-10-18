// OpenSPM output aspect policy ([Display] aspect + widescreen).
//
// aspect = 4:3 | stretch      how the game picture fills the window
// widescreen = 0 | 1          the game renders an anamorphic 16:9 image into its
//                             4:3 EFB (the backend reports the Wii TV setting
//                             SYSCONF IPL.AR = 16:9); read once at startup
//
// Without widescreen: 4:3 = Aurora's FIT policy (EFB at the logical 4:3 aspect,
// centred with bars), stretch = STRETCH (EFB fills the window).
// With widescreen the EFB is always stretched horizontally (STRETCH policy:
// independent X/Y logical-to-host scales). aspect = 4:3 ("keep the picture's
// aspect") then fits the host EFB to 16:9 inside the window, with pillarbox /
// letterbox bars on non-16:9 windows; stretch fills the window. Because the host
// EFB is itself 16:9 (e.g. 1920x1080), the anamorphic image is rendered at full
// horizontal resolution rather than upscaled from a 4:3 buffer.
//
// Pure policy (no Aurora/SDL types) so test_display_pacing.py compiles it.
#pragma once

namespace openspm::aspect {

constexpr float kWidescreenAspect = 16.f / 9.f;

struct Output {
    bool stretch_policy;  // AURORA_VIEWPORT_STRETCH (else AURORA_VIEWPORT_FIT)
    float stretch_aspect; // AuroraSetStretchAspect: 0 = fill the window
};

constexpr Output choose_output(bool aspect_stretch, bool widescreen) noexcept {
    if (!widescreen) return {aspect_stretch, 0.f};
    return {true, aspect_stretch ? 0.f : kWidescreenAspect};
}

// OPENSPM_WIDESCREEN is on only for exactly "1" (the backend uses the same rule).
constexpr bool widescreen_value(const char *value) noexcept {
    return value != nullptr && value[0] == '1' && value[1] == '\0';
}

} // namespace openspm::aspect
