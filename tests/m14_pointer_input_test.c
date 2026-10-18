/* Wii pointer mapping over the game image, right-stick pointer, rumble request
   (include/openspm/pointer_input.h). */
#include "openspm/pointer_input.h"

#include <assert.h>
#include <math.h>
#include <stdio.h>

static int near(double a, double b) { return fabs(a - b) < 1e-4; }

static void test_fit(void) {
    /* 4:3 image in a 16:9 window: pillarbox bars of 1/8 on each side. */
    openspm_image_rect r = openspm_image_rect_fit(1920, 1080, 640, 480);
    assert(near(r.top, 0.0) && near(r.height, 1.0));
    assert(near(r.width, 0.75) && near(r.left, 0.125));
    /* 16:9 widescreen host EFB in a 4:3 window: letterbox. */
    r = openspm_image_rect_fit(1024, 768, 1920, 1080);
    assert(near(r.left, 0.0) && near(r.width, 1.0));
    assert(near(r.height, 0.75) && near(r.top, 0.125));
    /* 16:9 image in a 21:9 window (ultrawide) and in a tall window. */
    r = openspm_image_rect_fit(2560, 1080, 1920, 1080);
    assert(near(r.height, 1.0) && near(r.width, 1920.0 / 2560.0) && near(r.left, (1.0 - 1920.0 / 2560.0) / 2));
    r = openspm_image_rect_fit(600, 1200, 640, 480);
    assert(near(r.width, 1.0) && near(r.height, 450.0 / 1200.0));
    /* Stretch (content matches the window) and invalid sizes: whole window. */
    r = openspm_image_rect_fit(1280, 720, 1280, 720);
    assert(near(r.left, 0) && near(r.top, 0) && near(r.width, 1) && near(r.height, 1));
    r = openspm_image_rect_fit(0, 720, 640, 480);
    assert(near(r.width, 1) && near(r.height, 1));
}

static void test_mapping(void) {
    const openspm_image_rect pillar = openspm_image_rect_fit(1920, 1080, 640, 480); /* image x 240..1680 */
    const openspm_image_rect letter = openspm_image_rect_fit(1024, 768, 1920, 1080); /* image y 96..672 */
    float x, y;
    /* Centre of the window is the centre of the screen. */
    assert(openspm_pointer_from_client(&pillar, 959.5, 539.5, 1920, 1080, &x, &y) == 1 && near(x, 0) && near(y, 0));
    /* The image's left edge is -1, not the window's. */
    assert(openspm_pointer_from_client(&pillar, 240, 540, 1920, 1080, &x, &y) == 1 && x < -0.99f && x > -1.0f);
    assert(openspm_pointer_from_client(&pillar, 1679, 540, 1920, 1080, &x, &y) == 1 && x > 0.99f && x < 1.0f);
    /* Over the bars: off-screen, clamped to the edge. */
    assert(openspm_pointer_from_client(&pillar, 100, 540, 1920, 1080, &x, &y) == 0 && near(x, -1.0));
    assert(openspm_pointer_from_client(&pillar, 1800, 10, 1920, 1080, &x, &y) == 0 && near(x, 1.0));
    /* Letterbox: vertical bars excluded, horizontal extent is the whole width. */
    assert(openspm_pointer_from_client(&letter, 0, 384, 1024, 768, &x, &y) == 1 && x < -0.99f);
    assert(openspm_pointer_from_client(&letter, 512, 50, 1024, 768, &x, &y) == 0 && near(y, -1.0));
    assert(openspm_pointer_from_client(&letter, 512, 96, 1024, 768, &x, &y) == 1 && y < -0.99f);
    assert(openspm_pointer_from_client(&letter, 512, 700, 1024, 768, &x, &y) == 0 && near(y, 1.0));
    /* Mapping is resolution-independent: the same rect on a scaled client (DPI). */
    assert(openspm_pointer_from_client(&pillar, 479.5, 269.5, 960, 540, &x, &y) == 1 && near(x, 0) && near(y, 0));
    /* No rect (renderer export missing): the whole window, as before the fix. */
    assert(openspm_pointer_from_client(NULL, 0, 0, 640, 480, &x, &y) == 1 && near(x, 1.0 / 640 - 1) &&
           near(y, 1.0 / 480 - 1));
    assert(openspm_pointer_from_client(NULL, 5, 5, 0, 480, &x, &y) == 0);
}

static void test_stick(void) {
    float vx, vy;
    /* Deadzone (radial). */
    assert(openspm_stick_pointer_velocity(5000, 0, 50, 20, &vx, &vy) == 0 && vx == 0.f && vy == 0.f);
    assert(openspm_stick_pointer_velocity(4000, 4000, 50, 20, &vx, &vy) == 0);
    /* Full tilt right: speed * 0.0008 per frame, no vertical motion. */
    assert(openspm_stick_pointer_velocity(32767, 0, 50, 20, &vx, &vy) == 1 && near(vx, 0.04) && near(vy, 0));
    assert(openspm_stick_pointer_velocity(-32768, 0, 100, 20, &vx, &vy) == 1 && near(vx, -0.08));
    /* Stick up (positive) moves the pointer up (negative y). */
    assert(openspm_stick_pointer_velocity(0, 32767, 50, 20, &vx, &vy) == 1 && near(vy, -0.04) && near(vx, 0));
    /* Squared response: halfway past the deadzone gives a quarter of the speed. */
    assert(openspm_stick_pointer_velocity((int)(32767 * 0.6), 0, 50, 20, &vx, &vy) == 1 && near(vx, 0.01));
    /* Diagonal full tilt keeps the full speed along the diagonal. */
    assert(openspm_stick_pointer_velocity(23170, -23170, 50, 0, &vx, &vy) == 1 &&
           near(sqrt(vx * vx + vy * vy), 0.04 * (23170 * sqrt(2.0) / 32767) * (23170 * sqrt(2.0) / 32767)));
    assert(vx > 0 && vy > 0);
    /* Out-of-range settings are clamped (speed 0 still moves, deadzone <= 90%). */
    assert(openspm_stick_pointer_velocity(32767, 0, 0, 200, &vx, &vy) == 1 && vx > 0.f);
}

static void test_arbiter(void) {
    openspm_pointer_arbiter a = {0};
    float x, y;
    int frame;
    /* Mouse first: the mouse pointer passes through (and off-screen stays off). */
    assert(openspm_pointer_arbitrate(&a, 1, 100, 100, 1, 0.25f, -0.5f, 0, 0, 0, &x, &y) == 1 && x == 0.25f &&
           y == -0.5f);
    assert(openspm_pointer_arbitrate(&a, 1, 100, 100, 0, -1.f, -0.5f, 0, 0, 0, &x, &y) == 0);
    /* Stick takes over from the last mouse position and integrates. */
    assert(openspm_pointer_arbitrate(&a, 1, 100, 100, 1, 0.25f, -0.5f, 1, 0.1f, 0.f, &x, &y) == 1 && near(x, 0.35));
    for (frame = 0; frame < 30; ++frame)
        (void)openspm_pointer_arbitrate(&a, 1, 100, 100, 1, 0.25f, -0.5f, 1, 0.1f, 0.05f, &x, &y);
    assert(near(x, 1.0) && near(y, 1.0)); /* clamped to the screen */
    /* Stick released, mouse still: the pointer stays where the stick left it. */
    assert(openspm_pointer_arbitrate(&a, 1, 100.4, 100, 0, 0.f, 0.f, 0, 0, 0, &x, &y) == 1 && near(x, 1.0));
    /* Unknown cursor (another window in front) does not steal it either. */
    assert(openspm_pointer_arbitrate(&a, 0, 0, 0, 0, 0.f, 0.f, 0, 0, 0, &x, &y) == 1 && near(x, 1.0));
    /* Mouse moves: back to the mouse. */
    assert(openspm_pointer_arbitrate(&a, 1, 102, 100, 1, 0.1f, 0.2f, 0, 0, 0, &x, &y) == 1 && x == 0.1f && y == 0.2f);
    /* Stick with no valid mouse pointer starts from the centre. */
    assert(openspm_pointer_arbitrate(&a, 1, 102, 100, 0, -1.f, 0.f, 1, 0.f, -0.1f, &x, &y) == 1 && near(x, 0) &&
           near(y, -0.1));
}

static void test_rumble(void) {
    assert(openspm_rumble_intensity(0) == 0u && openspm_rumble_intensity(100) == 0xFFFFu);
    assert(openspm_rumble_intensity(50) == 0x7FFFu && openspm_rumble_intensity(-5) == 0u);
    assert(openspm_rumble_intensity(OPENSPM_RUMBLE_STRENGTH_DEFAULT) > 0x8000u);
    assert(openspm_rumble_requested(1u, 0u, 0) == 1);
    assert(openspm_rumble_requested(1u, 1u, 0) == 0); /* suppressed: wpadMain forces the motor off */
    assert(openspm_rumble_requested(0u, 0u, 0) == 0 && openspm_rumble_requested(2u, 0u, 0) == 0);
    assert(openspm_rumble_requested(0u, 0u, 4) == 1 && openspm_rumble_requested(0u, 0u, -10) == 0);
}

int main(void) {
    test_fit();
    test_mapping();
    test_stick();
    test_arbiter();
    test_rumble();
    puts("PASS: OpenSPM pointer input");
    return 0;
}
