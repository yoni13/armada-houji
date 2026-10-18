// SPDX-License-Identifier: GPL-2.0-only
// SensorProxy orientation/lock bridge; gyro aiming is a separate consumer.
#include "bus.hpp"
#include <glib-unix.h>
#include <iostream>

using namespace houji;
static constexpr auto SENSOR = "net.hadess.SensorProxy";
static constexpr auto SENSOR_PATH = "/net/hadess/SensorProxy";
static const std::map<std::string, std::string> orientations = {
    {"normal", "normal"}, {"bottom-up", "upsidedown"}, {"left-up", "left"}, {"right-up", "right"}
};
struct Session {
    pid_t pid = 0;
    std::string socket;
    ino_t inode = 0;
    bool operator==(const Session &other) const { return pid == other.pid && socket == other.socket && inode == other.inode; }
};
class Orientation {
    Object<GDBusConnection> bus;
    Object<GDBusProxy> proxy;
    fs::path proc, runtime, lock, state;
    std::string helper, address, owner, pending, applied;
    bool claimed = false;
    Session session;
    Clock::time_point since{}, retry_at{};
    GMainLoop *loop;

    Session compositor() {
        for (const auto &item : fs::directory_iterator(proc)) {
            if (!digits(item.path().filename().string())) continue;
            struct stat st{};
            if (stat(item.path().c_str(), &st) || st.st_uid != getuid()) continue;
            try {
                const auto args = read(item.path() / "cmdline");
                if (args.substr(0, args.find('\0')) != "/usr/bin/gamescope") continue;
                if (!argument(args, "--force-composition-rotation")) continue;
                std::vector<fs::path> sockets;
                for (const auto &entry : fs::directory_iterator(runtime)) {
                    auto name = entry.path().filename().string();
                    if (name.rfind("gamescope-", 0) == 0 && digits(name.substr(10))) sockets.push_back(entry.path());
                }
                std::sort(sockets.begin(), sockets.end());
                for (const auto &path : sockets)
                    if (!stat(path.c_str(), &st) && S_ISSOCK(st.st_mode))
                        return {static_cast<pid_t>(std::stol(item.path().filename().string())), path.string(), st.st_ino};
            } catch (const std::exception &) {} // compositor exited or runtime not ready
        }
        return {};
    }
    std::string locked() {
        try {
            auto value = trim(read(lock));
            for (const auto &entry : orientations) if (entry.second == value) return value;
        } catch (const std::exception &) {}
        return "";
    }
    void sensor_call(const char *method, int timeout) {
        GError *error = nullptr;
        Variant result(g_dbus_proxy_call_sync(proxy.get(), method, nullptr,
                            G_DBUS_CALL_FLAGS_NO_AUTO_START, timeout, nullptr, &error));
        if (!result) throw bus_error(error);
    }
    void unclaim() {
        if (claimed && proxy) {
            try { sensor_call("ReleaseAccelerometer", 2000); } catch (const std::exception &) {}
        }
        claimed = false;
    }
    void release() { unclaim(); pending.clear(); applied.clear(); }
    void apply(const std::string &orientation) {
        run({helper, orientation}, 4, "WAYLAND_DISPLAY", session.socket.c_str());
        applied = orientation;
        try { atomic_write(state, orientation + '\n'); } catch (const std::exception &) {}
        std::cout << "Gamescope orientation: " << orientation << std::endl;
    }
    void connect() {
        if (!bus || g_dbus_connection_is_closed(bus.get())) bus = connect_bus(address);
        GError *error = nullptr;
        proxy.reset(g_dbus_proxy_new_sync(bus.get(), G_DBUS_PROXY_FLAGS_DO_NOT_AUTO_START, nullptr,
                                         SENSOR, SENSOR_PATH, SENSOR, nullptr, &error));
        if (!proxy) throw bus_error(error);
    }
public:
    explicit Orientation(const Options &options) :
        proc(options.get("--proc", "/proc")),
        runtime(options.get("--runtime", getenv("XDG_RUNTIME_DIR") ? getenv("XDG_RUNTIME_DIR") : "/run/user/" + std::to_string(getuid()))),
        lock(options.get("--lock", "/etc/armada/houji-rotation-lock")),
        state(options.get("--state", "/run/houji-orientation/current")),
        helper(options.get("--helper", "/usr/libexec/armada/houji-gamescope-rotate")),
        address(options.get("--bus-address", "")), loop(g_main_loop_new(nullptr, false)) {}
    ~Orientation() { release(); g_main_loop_unref(loop); }
    bool tick() {
        if (stopped) { g_main_loop_quit(loop); return false; }
        try {
            auto current = compositor();
            if (!(current == session)) {
                release(); session = current;
                std::cout << "Gamescope orientation: " << (current.pid ? "session ready" : "waiting for shader-rotation session") << std::endl;
            }
            if (!current.pid || Clock::now() < retry_at) return true;
            auto orientation = locked();
            if (!orientation.empty()) {
                unclaim(); pending.clear();
                if (orientation != applied) apply(orientation);
                return true;
            }
            if (!proxy) connect();
            char *name = g_dbus_proxy_get_name_owner(proxy.get());
            std::string current_owner = name ? name : ""; g_free(name);
            if (current_owner != owner) {
                claimed = false; owner = current_owner; applied.clear(); pending.clear();
            }
            if (owner.empty()) return true;
            if (!claimed) { sensor_call("ClaimAccelerometer", 3000); claimed = true; }
            Variant value(g_dbus_proxy_get_cached_property(proxy.get(), "AccelerometerOrientation"));
            auto found = value && g_variant_is_of_type(value.get(), G_VARIANT_TYPE_STRING)
                ? orientations.find(g_variant_get_string(value.get(), nullptr)) : orientations.end();
            if (found == orientations.end()) { pending.clear(); return true; }
            orientation = found->second;
            if (pending != orientation) { pending = orientation; since = Clock::now(); }
            else if (applied != orientation && Clock::now() - since >= std::chrono::milliseconds(750)) apply(orientation);
        } catch (const std::exception &e) {
            std::cerr << "Gamescope orientation retry: " << e.what() << '\n';
            release(); proxy.reset(); owner.clear(); retry_at = Clock::now() + std::chrono::seconds(5);
        }
        return true;
    }
    void execute(unsigned interval) {
        guint timer = g_timeout_add(interval, [](gpointer data) -> gboolean { return static_cast<Orientation *>(data)->tick(); }, this);
        g_main_loop_run(loop);
        // A signal makes tick remove itself; explicit cleanup releases the sensor.
        if (!stopped) g_source_remove(timer);
    }
};
int main(int argc, char **argv) {
    try {
        Options options(argc, argv, {"--proc", "--runtime", "--lock", "--state", "--helper", "--bus-address", "--interval-ms"});
        signals(); Orientation service(options);
        if (options.once) service.tick(); else service.execute(options.interval(500));
        return 0;
    } catch (const std::exception &e) { std::cerr << e.what() << '\n'; return 1; }
}
