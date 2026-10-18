// SPDX-License-Identifier: GPL-2.0-only
// UPower remains the sole telemetry/policy source. Unknown time is -1, never 0.
#include "bus.hpp"
#include <cmath>
#include <iostream>
#include <sys/socket.h>
#include <sys/un.h>

using namespace houji;
static constexpr auto PROPS = "org.freedesktop.DBus.Properties";
static constexpr auto UPOWER = "org.freedesktop.UPower";
static void ready() {
    const char *name = getenv("NOTIFY_SOCKET");
    if (!name || !*name) return;
    sockaddr_un address{}; address.sun_family = AF_UNIX;
    const size_t len = strlen(name);
    if (len >= sizeof(address.sun_path)) throw std::runtime_error("Invalid notify socket");
    memcpy(address.sun_path, name, len + 1);
    if (name[0] == '@') address.sun_path[0] = '\0';
    int fd = socket(AF_UNIX, SOCK_DGRAM | SOCK_CLOEXEC, 0);
    if (fd < 0) throw std::runtime_error("Notify socket failed");
    ssize_t n = sendto(fd, "READY=1", 7, MSG_NOSIGNAL, reinterpret_cast<sockaddr *>(&address),
                       offsetof(sockaddr_un, sun_path) + len + (name[0] == '@' ? 0 : 1));
    close(fd);
    if (n != 7) throw std::runtime_error("Readiness notification failed");
}
static void stale(const fs::path &directory) {
    atomic_write(directory / "battery_status", "Unknown\n");
    atomic_write(directory / "secs_until_battery_full", "-1\n");
    atomic_write(directory / "secs_until_shutdown_request", "-1\n");
}
static void publish(GDBusConnection *bus, const fs::path &directory) {
    auto reply = call(bus, UPOWER, "/org/freedesktop/UPower/devices/DisplayDevice", PROPS,
                      "GetAll", g_variant_new("(s)", "org.freedesktop.UPower.Device"), G_VARIANT_TYPE("(a{sv})"));
    Variant props(g_variant_get_child_value(reply.get(), 0));
    gboolean present = false; double percent = 0; guint32 state = 0; gint64 full = 0, empty = 0;
    if (!g_variant_lookup(props.get(), "IsPresent", "b", &present) || !present ||
        !g_variant_lookup(props.get(), "Percentage", "d", &percent) || !std::isfinite(percent) || percent < 0 || percent > 100 ||
        !g_variant_lookup(props.get(), "State", "u", &state) ||
        !g_variant_lookup(props.get(), "TimeToFull", "x", &full) ||
        !g_variant_lookup(props.get(), "TimeToEmpty", "x", &empty))
        throw std::runtime_error("Battery telemetry unavailable or invalid");
    auto ac = call(bus, UPOWER, "/org/freedesktop/UPower", PROPS, "Get",
                   g_variant_new("(ss)", UPOWER, "OnBattery"), G_VARIANT_TYPE("(v)"));
    Variant wrapper(g_variant_get_child_value(ac.get(), 0));
    Variant value(g_variant_get_variant(wrapper.get()));
    if (!g_variant_is_of_type(value.get(), G_VARIANT_TYPE_BOOLEAN))
        throw std::runtime_error("Invalid AC telemetry");
    const char *status[] = {"Unknown", "Charging", "Discharging", "Discharging", "Full", "Not charging", "Discharging"};
    atomic_write(directory / "ac_status", g_variant_get_boolean(value.get()) ? "Disconnected\n" : "Connected\n");
    atomic_write(directory / "battery_status", std::string(state < 7 ? status[state] : "Unknown") + '\n');
    atomic_write(directory / "secs_until_battery_full", std::to_string(state == 1 && full > 0 ? full : -1) + '\n');
    atomic_write(directory / "secs_until_shutdown_request", std::to_string((state == 2 || state == 6) && empty > 0 ? empty : -1) + '\n');
    char number[G_ASCII_DTOSTR_BUF_SIZE];
    g_ascii_formatd(number, sizeof(number), "%g", percent);
    // Discovery file last; Steam must never discover a partial first snapshot.
    atomic_write(directory / "battery_percent", std::string(number) + '\n');
}
int main(int argc, char **argv) {
    try {
        Options options(argc, argv, {"--directory", "--bus-address", "--interval-ms"});
        const fs::path directory = options.get("--directory", "/run/vpower");
        const unsigned interval = options.interval(2000);
        fs::create_directories(directory);
        if (!fs::exists(directory / ".houji-upower") && !fs::is_empty(directory))
            throw std::runtime_error("vpower is already supplied by another provider");
        atomic_write(directory / ".houji-upower", "");
        signals(); bool notified = false; std::string previous_error;
        Object<GDBusConnection> bus;
        do {
            try {
                if (!bus) bus = connect_bus(options.get("--bus-address", ""));
                publish(bus.get(), directory);
                if (!notified) { ready(); notified = true; }
                previous_error.clear();
            } catch (const std::exception &e) {
                if (notified || fs::exists(directory / "battery_percent")) stale(directory);
                if (previous_error != e.what()) std::cerr << "UPower telemetry unavailable: " << e.what() << '\n';
                previous_error = e.what();
                if (bus && g_dbus_connection_is_closed(bus.get())) bus.reset();
                if (options.once) return 1;
            }
            if (options.once) break;
            pause(interval);
        } while (!stopped);
        return 0;
    } catch (const std::exception &e) { std::cerr << e.what() << '\n'; return 1; }
}
