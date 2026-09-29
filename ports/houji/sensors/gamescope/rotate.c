/* SPDX-License-Identifier: MIT
 * Rotate the internal Gamescope output through its version 2 control protocol.
 * The compositor also transforms touch coordinates; do not rotate uinput again.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <wayland-client.h>
#include "gamescope-control-client-protocol.h"

static struct gamescope_control *control;
static void feature(void *data, struct gamescope_control *c, uint32_t f,
                    uint32_t v, uint32_t flags) { (void)data;(void)c;(void)f;(void)v;(void)flags; }
static void display(void *data, struct gamescope_control *c, const char *name,
                    const char *make, const char *model, uint32_t flags,
                    struct wl_array *rates) { (void)data;(void)c;(void)name;(void)make;(void)model;(void)flags;(void)rates; }
static const struct gamescope_control_listener listener = {
    .feature_support = feature, .active_display_info = display,
};
static void global(void *data, struct wl_registry *registry, uint32_t name,
                   const char *interface, uint32_t version)
{
    (void)data;
    if (!strcmp(interface, "gamescope_control") && version >= 2) {
        control = wl_registry_bind(registry, name, &gamescope_control_interface, 2);
        gamescope_control_add_listener(control, &listener, NULL);
    }
}
static void removed(void *data, struct wl_registry *r, uint32_t name)
{ (void)data; (void)r; (void)name; }
static const struct wl_registry_listener registry_listener = { global, removed };
int main(int argc, char **argv)
{
    const char *names[] = {"normal", "left", "right", "upsidedown"};
    unsigned orientation;
    if (argc != 2) return 2;
    for (orientation = 0; orientation < 4; orientation++)
        if (!strcmp(argv[1], names[orientation])) break;
    if (orientation == 4) return 2;
    alarm(3);
    struct wl_display *d = wl_display_connect(NULL);
    if (!d) { perror("Gamescope connection"); return 1; }
    struct wl_registry *registry = wl_display_get_registry(d);
    wl_registry_add_listener(registry, &registry_listener, NULL);
    if (wl_display_roundtrip(d) < 0 || !control) {
        fprintf(stderr, "Gamescope control protocol v2 unavailable\n");
        wl_display_disconnect(d); return 1;
    }
    gamescope_control_rotate_display(control, orientation + 1,
                                     GAMESCOPE_CONTROL_DISPLAY_TARGET_TYPE_INTERNAL);
    int ret = wl_display_roundtrip(d);
    gamescope_control_destroy(control);
    wl_registry_destroy(registry);
    wl_display_disconnect(d);
    return ret < 0;
}
