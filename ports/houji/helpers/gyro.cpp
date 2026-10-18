// SPDX-License-Identifier: GPL-2.0-only
// libssc ABI bridge. Sensor objects intentionally outlive queued measurements.
#include "bus.hpp"
#include <array>
#include <cmath>
#include <dlfcn.h>
#include <iostream>
#include <set>
#include <sstream>

using namespace houji;
using Vector = std::array<double, 3>;
static Vector rotate(Vector v, const std::string &mode) {
    if (mode == "left") return {v[1], -v[0], v[2]};
    if (mode == "right") return {-v[1], v[0], v[2]};
    if (mode == "upsidedown") return {-v[0], -v[1], v[2]};
    return v;
}
static std::string sample(Vector accel, Vector gyro, const std::string &mode) {
    accel = rotate(accel, mode); gyro = rotate(gyro, mode);
    char line[256]; long long v[6];
    for (int i = 0; i < 6; ++i) {
        double x = i < 3 ? accel[i] * (16384 / 9.80665) : gyro[i-3] * (1e6 * 16.384 / 12);
        if (!std::isfinite(x) || std::abs(x) > 2147483647) throw std::runtime_error("Invalid motion sample");
        v[i] = static_cast<long long>(std::nearbyint(x));
    }
    snprintf(line, sizeof(line), "%lld %lld %lld %lld %lld %lld\n", v[0], v[1], v[2], v[3], v[4], v[5]);
    return line;
}
static constexpr auto IP = "org.shadowblip.InputPlumber";
static constexpr auto IP_ROOT = "/org/shadowblip/InputPlumber";
static constexpr auto COMPOSITE = "org.shadowblip.Input.CompositeDevice";
class Gyro {
    struct Sensor {
        using New = void *(*)(GCancellable *, GError **);
        using Control = gboolean (*)(void *, GCancellable *, GError **);
        void *object = nullptr;
        New create = nullptr; Control open = nullptr, close = nullptr;
        bool opened = false;
    } sensors[2];
    using Send = void (*)(void *, guint32, GBytes *, GCancellable *, GAsyncReadyCallback, gpointer);
    void *library = nullptr; Send send = nullptr;
    Object<GDBusConnection> bus;
    GMainLoop *loop = g_main_loop_new(nullptr, false);
    fs::path setting, statefile, lightsleep, rotation, iio;
    std::vector<fs::path> configs;
    std::string modprobe, library_path, address, state, mode = "normal";
    Vector accel{}, angular{};
    int fd = -1;
    Clock::time_point retry_at{};
    template<class T> T symbol(const std::string &name) {
        void *p = dlsym(library, name.c_str());
        if (!p) throw std::runtime_error("libssc ABI missing " + name);
        return reinterpret_cast<T>(p);
    }
    static void accel_sample(void *, float x, float y, float z, void *data) {
        static_cast<Gyro *>(data)->accel = {x,y,z};
    }
    static void gyro_sample(void *, float x, float y, float z, void *data) {
        auto *self = static_cast<Gyro *>(data); self->angular = {x,y,z};
        if (self->fd >= 0) {
            try {
                auto line = sample(self->accel, self->angular, self->mode);
                // sysfs sample is a complete record; retry only interrupted writes.
                while (write(self->fd, line.data(), line.size()) < 0 && errno == EINTR) {}
            } catch (const std::exception &) {}
        }
    }
    void initialize() {
        if (sensors[0].object && sensors[1].object) return;
        if (!library) library = dlopen(library_path.c_str(), RTLD_NOW | RTLD_LOCAL);
        if (!library) throw std::runtime_error("Cannot load libssc");
        send = symbol<Send>("ssc_sensor_send");
        const char *names[] = {"gyroscope", "accelerometer"};
        for (int i = 0; i < 2; ++i) {
            auto &s = sensors[i];
            const std::string prefix = std::string("ssc_sensor_") + names[i];
            s.create = symbol<Sensor::New>(prefix + "_new_sync");
            s.open = symbol<Sensor::Control>(prefix + "_open_sync");
            s.close = symbol<Sensor::Control>(prefix + "_close_sync");
            if (s.object) continue;
            GError *error = nullptr; s.object = s.create(nullptr, &error);
            if (!s.object) throw bus_error(error);
            g_signal_connect(s.object, "measurement", i == 0 ? G_CALLBACK(gyro_sample) : G_CALLBACK(accel_sample), this);
        }
    }
    std::set<std::string> controller_names() {
        std::set<std::string> names;
        for (const auto &dir : configs) {
            if (!fs::exists(dir)) continue;
            for (const auto &p : fs::directory_iterator(dir)) {
                if (p.path().extension() != ".yaml") continue;
                try {
                    auto text = read(p.path()); if (text.find("bmi-houji-imu") == std::string::npos) continue;
                    std::istringstream lines(text); std::string line;
                    while (std::getline(lines, line)) if (line.rfind("name:", 0) == 0) {
                        auto name = trim(line.substr(5));
                        if (name.size() >= 2 && (name.front() == '\'' || name.front() == '"') && name.back() == name.front()) name = name.substr(1, name.size()-2);
                        names.insert(name); break;
                    }
                } catch (const std::exception &) {}
            }
        }
        return names;
    }
    Variant query(const std::string &path, const char *iface, const char *method, GVariant *args, const GVariantType *type) {
        GError *error = nullptr;
        auto *result = g_dbus_connection_call_sync(bus.get(), IP, path.c_str(), iface, method,
            args, type, G_DBUS_CALL_FLAGS_NO_AUTO_START, 2000, nullptr, &error);
        if (!result) throw bus_error(error);
        return Variant(result);
    }
    bool connected() {
        try {
            if (!bus || g_dbus_connection_is_closed(bus.get())) bus = connect_bus(address);
            auto names = controller_names(); if (names.empty()) return false;
            auto xml = query(IP_ROOT, "org.freedesktop.DBus.Introspectable", "Introspect", nullptr, G_VARIANT_TYPE("(s)"));
            const char *text; g_variant_get(xml.get(), "(&s)", &text);
            GError *error = nullptr;
            auto *info = g_dbus_node_info_new_for_xml(text, &error);
            if (!info) throw bus_error(error);
            std::unique_ptr<GDBusNodeInfo, decltype(&g_dbus_node_info_unref)> node(info, g_dbus_node_info_unref);
            for (auto **child = info->nodes; child && *child; ++child) {
                const std::string name((*child)->path);
                if (name.rfind("CompositeDevice",0) != 0 || !digits(name.substr(15))) continue;
                try {
                    auto reply = query(std::string(IP_ROOT)+"/"+name, "org.freedesktop.DBus.Properties", "GetAll",
                        g_variant_new("(s)", COMPOSITE), G_VARIANT_TYPE("(a{sv})"));
                    Variant props(g_variant_get_child_value(reply.get(),0));
                    const char *label = nullptr;
                    if (!g_variant_lookup(props.get(),"Name","&s",&label) || !names.count(label)) continue;
                    auto has = [&](const char *key, bool source) {
                        Variant paths(g_variant_lookup_value(props.get(),key,G_VARIANT_TYPE("as")));
                        if (!paths) return false;
                        GVariantIter iter; const char *path; g_variant_iter_init(&iter,paths.get());
                        while (g_variant_iter_next(&iter,"&s",&path)) {
                            std::string p(path);
                            if (source ? (p.rfind("/dev/input/",0)==0 || p.rfind("/dev/hidraw",0)==0) :
                                         p.find("/target/gamepad")!=std::string::npos) return true;
                        }
                        return false;
                    };
                    if (has("SourceDevicePaths",true) && has("TargetDevices",false)) return true;
                } catch (const std::exception &) {}
            }
        } catch (const std::exception &) {} // Lost controller/bus must release motion.
        return false;
    }
    void stop() {
        // Callbacks can still be queued after SSC close; stop writes first.
        if (fd >= 0) { close(fd); fd = -1; }
        for (auto &sensor : sensors) if (sensor.opened) { sensor.close(sensor.object,nullptr,nullptr); sensor.opened = false; }
        accel = {}; angular = {};
        try { run({modprobe,"-r","houji_imu"},10,nullptr,nullptr,false); } catch (const std::exception &) {}
    }
    void start() {
        run({modprobe,"houji_imu"},10);
        fs::path path;
        for (int attempt = 0; attempt < 20 && !stopped; ++attempt) {
            for (const auto &device : fs::directory_iterator(iio)) {
                try { if (trim(read(device.path()/"name")) == "bmi-houji-imu") { path = device.path()/"sample"; break; } }
                catch (const std::exception &) {}
            }
            if (!path.empty()) break;
            pause(100);
        }
        if (path.empty()) throw std::runtime_error("houji-imu device did not appear");
        fd = open(path.c_str(),O_WRONLY|O_CLOEXEC);
        if (fd < 0) throw std::runtime_error("Cannot open IMU output");
        initialize();
        for (int i=0;i<2;++i) {
            GError *error=nullptr; auto &s=sensors[i];
            if (!s.open(s.object,nullptr,&error)) throw bus_error(error);
            s.opened=true;
            float rate=i==0?200.f:50.f; guint32 bits; memcpy(&bits,&rate,4); bits=GUINT32_TO_LE(bits);
            unsigned char payload[5]={0x0d}; memcpy(payload+1,&bits,4);
            auto *bytes=g_bytes_new(payload,sizeof(payload)); send(s.object,513,bytes,nullptr,nullptr,nullptr); g_bytes_unref(bytes);
        }
    }
    void publish(const std::string &value) {
        fs::create_directories(statefile.parent_path());
        atomic_write(statefile,"{\"state\": \""+value+"\"}\n");
    }
public:
    explicit Gyro(const Options &o) : setting(o.get("--setting","/etc/armada/houji-gyro-aim")),
        statefile(o.get("--state","/run/houji-gyro/state")),lightsleep(o.get("--light-sleep","/run/armada/fake-suspend.active")),
        rotation(o.get("--orientation","/run/houji-orientation/current")),iio(o.get("--iio","/sys/bus/iio/devices")),
        configs{o.get("--config-etc","/etc/inputplumber/devices.d"),o.get("--config-share","/usr/share/inputplumber/devices")},
        modprobe(o.get("--modprobe","/usr/sbin/modprobe")),library_path(o.get("--ssc-library","libssc.so.2")),address(o.get("--bus-address","")) {}
    ~Gyro() { stop(); try { publish("off"); } catch (...) {} g_main_loop_unref(loop); }
    bool tick() {
        if (stopped) { g_main_loop_quit(loop); return false; }
        try {
            std::string wanted = !fs::exists(setting)?"off":fs::exists(lightsleep)||!connected()?"waiting":"active";
            try { mode=trim(read(rotation)); } catch (...) { mode="normal"; }
            if (state=="error" && wanted!="off" && Clock::now()<retry_at) return true;
            if (wanted!=state) {
                try {
                    if (wanted=="active") start(); else if (state=="active" || state.empty()) stop();
                    state=wanted;
                } catch (const std::exception &e) { stop(); state="error"; retry_at=Clock::now()+std::chrono::seconds(30); std::cerr<<"Gyro aiming unavailable: "<<e.what()<<'\n'; }
                publish(state); std::cout<<"Gyro aiming: "<<state<<std::endl;
            }
        } catch (const std::exception &e) { std::cerr<<"Gyro state query failed: "<<e.what()<<'\n'; }
        return true;
    }
    void execute(unsigned interval) {
        tick(); g_timeout_add(interval,[](gpointer p)->gboolean{return static_cast<Gyro *>(p)->tick();},this);
        g_main_loop_run(loop);
    }
};
int main(int argc,char **argv) {
    try {
        if (argc==2 && std::string(argv[1])=="--samples") {
            std::string mode; Vector a,g;
            while (std::cin>>mode>>a[0]>>a[1]>>a[2]>>g[0]>>g[1]>>g[2]) std::cout<<sample(a,g,mode);
            return 0;
        }
        Options o(argc,argv,{"--setting","--state","--light-sleep","--orientation","--iio","--config-etc","--config-share","--modprobe","--ssc-library","--bus-address","--interval-ms"});
        signals(); Gyro gyro(o); if(o.once) gyro.tick(); else gyro.execute(o.interval(1000)); return 0;
    } catch(const std::exception &e) {std::cerr<<e.what()<<'\n';return 1;}
}
