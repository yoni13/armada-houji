/* Read-only Gamescope presentation timing. Re-arm each one-shot request only
 * after its response; never consume MangoApp's pipe or change compositor state. */
#include <errno.h>
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <wayland-client.h>
#include "gamescope-control-client-protocol.h"

static struct gamescope_control *control;
static uint32_t appid;
static double start;
static FILE *output;
static unsigned samples;
static double now(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + t.tv_nsec / 1e9;
}
static void feature(void *data, struct gamescope_control *c, uint32_t f, uint32_t version, uint32_t flags) {
    (void)data; (void)c; (void)f; (void)version; (void)flags;
}
static void display(void *data, struct gamescope_control *c, const char *connector,
                    const char *make, const char *model, uint32_t flags, struct wl_array *rates) {
    (void)data; (void)c; (void)connector; (void)make; (void)model; (void)flags; (void)rates;
}
static void screenshot(void *data, struct gamescope_control *c, const char *path) {
    (void)data; (void)c; (void)path;
}
static void performance(void *data, struct gamescope_control *c, uint32_t app, uint32_t lo, uint32_t hi) {
    (void)data;
    if (app != appid) return;
    uint64_t ns = ((uint64_t)hi << 32) | lo;
    if (ns) {
        fprintf(output, "%.6f,%.6f\n", now() - start, ns / 1e6);
        ++samples;
    }
    gamescope_control_request_app_performance_stats(c, appid);
}
static const struct gamescope_control_listener listener = {
    .feature_support = feature, .active_display_info = display,
    .screenshot_taken = screenshot, .app_performance_stats = performance,
};
static void global(void *data, struct wl_registry *registry, uint32_t name, const char *interface, uint32_t version) {
    (void)data;
    if (!strcmp(interface, "gamescope_control") && version >= 6) {
        control = wl_registry_bind(registry, name, &gamescope_control_interface, 6);
        gamescope_control_add_listener(control, &listener, NULL);
    }
}
static void removed(void *data, struct wl_registry *registry, uint32_t name) {
    (void)data; (void)registry; (void)name;
}
static const struct wl_registry_listener registry_listener = {global, removed};

int main(int argc, char **argv) {
    if (argc != 4) { fprintf(stderr, "Usage: record-frametimes APPID SECONDS OUTPUT.csv\n"); return 2; }
    appid = strtoul(argv[1], NULL, 10);
    double seconds = strtod(argv[2], NULL);
    if (!appid || seconds < 1 || seconds > 600) return 2;
    struct wl_display *d = wl_display_connect("gamescope-0");
    if (!d) { perror("Gamescope connection"); return 1; }
    struct wl_registry *registry = wl_display_get_registry(d);
    wl_registry_add_listener(registry, &registry_listener, NULL);
    if (wl_display_roundtrip(d) < 0 || !control) {
        fprintf(stderr, "Gamescope performance protocol v6 unavailable\n"); wl_display_disconnect(d); return 1;
    }
    output = fopen(argv[3], "wx");
    if (!output) { perror("output"); wl_display_disconnect(d); return 1; }
    fprintf(output, "elapsed_seconds,present_interval_ms\n");
    start = now();
    gamescope_control_request_app_performance_stats(control, appid);
    int result = 0;
    while (now() - start < seconds) {
        while (wl_display_prepare_read(d) != 0) {
            if (wl_display_dispatch_pending(d) < 0) { result = 1; goto done; }
        }
        if (wl_display_flush(d) < 0 && errno != EAGAIN) { wl_display_cancel_read(d); result = 1; break; }
        struct pollfd fd = {.fd = wl_display_get_fd(d), .events = POLLIN};
        double remaining = seconds - (now() - start);
        int wait = remaining > 1 ? 1000 : (remaining > 0 ? (int)(remaining * 1000) : 0);
        int polled = poll(&fd, 1, wait);
        if (polled > 0 && (fd.revents & POLLIN)) {
            if (wl_display_read_events(d) < 0 || wl_display_dispatch_pending(d) < 0) { result = 1; break; }
        } else {
            wl_display_cancel_read(d);
            if ((polled < 0 && errno != EINTR) || (polled > 0 && fd.revents & (POLLERR | POLLHUP))) { result = 1; break; }
        }
    }
done:
    printf("Recorded %u presentation-interval samples over %.2f seconds\n", samples, now() - start);
    if (fclose(output) || !samples) result = 1;
    gamescope_control_destroy(control);
    wl_registry_destroy(registry);
    wl_display_disconnect(d);
    return result;
}
