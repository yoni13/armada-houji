// SPDX-License-Identifier: GPL-2.0-or-later
// Authorized, serialized SIM/eSIM settings broker; no direct modem restart.
#include "bus.hpp"
#include "capture.hpp"
#include <nlohmann/json.hpp>
#include <iostream>
#include <set>
#include <sstream>
#include <sys/file.h>

using namespace houji;
using Json = nlohmann::json;
static constexpr auto NAME = "org.armada.Cellular";
static constexpr auto PATH = "/org/armada/Cellular";
static constexpr auto IFACE = "org.armada.Cellular1";
static constexpr auto XML = R"(<node><interface name="org.armada.Cellular1">
<method name="Status"><arg type="s" direction="out"/></method>
<method name="Run"><arg type="s" direction="in"/><arg type="s" direction="in"/></method>
</interface></node>)";

static bool identifier(const std::string &value) {
    return value.size() >= 18 && value.size() <= 22 && digits(value);
}
static void validate(const std::string &operation, const std::string &value) {
    static const std::set<std::string> ops = {"enable", "select", "list", "download", "profile-enable",
        "profile-disable", "profile-delete", "profile-nickname"};
    if (!ops.count(operation) || !g_utf8_validate(value.data(), value.size(), nullptr))
        throw std::invalid_argument("Invalid operation");
    if (operation == "select") {
        if (value != "auto" && value != "physical1" && value != "physical2" && value != "esim")
            throw std::invalid_argument("Invalid SIM selection");
    } else if (operation == "profile-nickname") {
        auto colon = value.find(':');
        if (colon == std::string::npos || !identifier(value.substr(0, colon)))
            throw std::invalid_argument("Invalid profile identifier");
        auto nickname = value.substr(colon + 1);
        if (nickname.size() > 64) throw std::invalid_argument("Nickname too long");
        for (const char *p = nickname.c_str(); *p; p = g_utf8_next_char(p))
            if (!g_unichar_isprint(g_utf8_get_char(p))) throw std::invalid_argument("Invalid nickname");
        if (!nickname.empty() && (g_unichar_isspace(g_utf8_get_char(nickname.c_str())) ||
            g_unichar_isspace(g_utf8_get_char(g_utf8_find_prev_char(nickname.c_str(), nickname.c_str()+nickname.size())))))
            throw std::invalid_argument("Padded nickname");
    } else if (operation.rfind("profile-", 0) == 0) {
        if (!identifier(value)) throw std::invalid_argument("Invalid profile identifier");
    } else if (operation == "download") {
        auto length = g_utf8_strlen(value.c_str(), value.size());
        if (value.rfind("LPA:1$", 0) != 0 || length < 12 || length > 2048)
            throw std::invalid_argument("Invalid activation code");
        for (const char *p = value.c_str(); *p; p = g_utf8_next_char(p))
            if (g_unichar_isspace(g_utf8_get_char(p))) throw std::invalid_argument("Invalid activation code");
    } else if (!value.empty()) throw std::invalid_argument("Unexpected argument");
}

class Cellular {
    Object<GDBusConnection> bus;
    GMainLoop *loop = g_main_loop_new(nullptr, false);
    std::unique_ptr<GDBusNodeInfo, decltype(&g_dbus_node_info_unref)> node{nullptr, g_dbus_node_info_unref};
    fs::path root, selection, lockfile;
    std::string systemctl;
    unsigned timeout, owner = 0, registration = 0, timer = 0;
    bool busy = false, name_failed = false;
    std::string message;
    Json profiles = Json::array();
    std::thread worker;

    bool authorized(const char *sender) {
        GVariantBuilder details, subject;
        g_variant_builder_init(&subject, G_VARIANT_TYPE("a{sv}"));
        g_variant_builder_add(&subject, "{sv}", "name", g_variant_new_string(sender));
        g_variant_builder_init(&details, G_VARIANT_TYPE("a{ss}"));
        auto result = call(bus.get(), "org.freedesktop.PolicyKit1", "/org/freedesktop/PolicyKit1/Authority",
            "org.freedesktop.PolicyKit1.Authority", "CheckAuthorization",
            g_variant_new("((sa{sv})sa{ss}us)", "system-bus-name", &subject,
                          "org.armada.cellular.manage", &details, 0u, ""), G_VARIANT_TYPE("((bba{ss}))"), 3000);
        Variant tuple(g_variant_get_child_value(result.get(), 0));
        Variant allowed(g_variant_get_child_value(tuple.get(), 0));
        return g_variant_get_boolean(allowed.get());
    }
    std::string command(const std::vector<std::string> &args, unsigned seconds = 60,
                        const std::string &input = "", const std::vector<std::string> *env = nullptr,
                        bool cleanup = false) {
        return capture(args, timeout ? timeout : seconds, input, env, cleanup);
    }
    static std::vector<std::string> lpac_environment() {
        std::vector<std::string> env;
        for (char **p = environ; *p; ++p) {
            std::string entry(*p);
            if (entry.rfind("LIBEUICC_", 0) != 0 && entry.rfind("LPAC_", 0) != 0) env.push_back(entry);
        }
        for (const auto *entry : {"LPAC_APDU=qmi_qrtr", "LPAC_APDU_QMI_UIM_SLOT=2",
                                 "LPAC_CUSTOM_ES10X_MSS=255", "LPAC_HTTP=curl"}) env.emplace_back(entry);
        return env;
    }
    Json list_profiles(const std::vector<std::string> &env) {
        auto output = command({(root/"lpac").string(), "profile", "list"}, 60, "", &env);
        std::istringstream lines(output); std::string line; Json records = Json::array();
        while (std::getline(lines, line)) records.push_back(Json::parse(line));
        for (const auto &record : records) {
            if (record.value("type", "") != "lpa") continue;
            const auto &payload = record.at("payload");
            if (!payload.at("code").is_number_integer() || payload.at("code").get<int>() != 0 || !payload.at("data").is_array())
                throw std::runtime_error("Cannot list profiles");
            Json selected = Json::array();
            for (const auto &item : payload.at("data")) {
                if (!item.is_object()) throw std::runtime_error("Invalid profile response");
                Json p = Json::object();
                for (const char *key : {"iccid", "profileName", "profileNickname", "serviceProviderName", "profileState"})
                    p[key] = item.contains(key) ? item.at(key) : Json("");
                selected.push_back(std::move(p));
            }
            return selected;
        }
        throw std::runtime_error("No profile response");
    }
    void work(std::string operation, std::string value) {
        bool restart_mm = false;
        std::string outcome = "Done";
        Json updated; int lock = -1;
        try {
            fs::create_directories(lockfile.parent_path());
            lock = open(lockfile.c_str(), O_CREAT|O_RDWR|O_CLOEXEC, 0600);
            if (lock < 0) throw std::runtime_error("Operation lock unavailable");
            while (flock(lock, LOCK_EX|LOCK_NB)) {
                if (errno != EAGAIN && errno != EWOULDBLOCK) throw std::runtime_error("Operation lock failed");
                if (stopped) throw std::runtime_error("Operation cancelled");
                pause(100);
            }
            if (stopped) throw std::runtime_error("Operation cancelled");
            if (operation == "enable") {
                // Restore MM even if its stop command times out after taking effect.
                restart_mm = true;
                command({systemctl, "stop", "ModemManager"});
                command({systemctl, "enable", "--now", "houji-cellular.service"}, 120);
            } else {
                command({systemctl, "is-active", "--quiet", "houji-cellular.service"});
                restart_mm = true;
                command({systemctl, "stop", "ModemManager"});
                if (operation == "select") command({(root/"sim.py").string(), value, "--save"});
                else {
                    command({(root/"sim.py").string(), "esim", "--save"});
                    const auto env = lpac_environment();
                    std::vector<std::string> args{(root/"lpac").string(), "profile", "list"};
                    std::string input;
                    if (operation == "download") { args = {args[0], "profile", "download", "-a", "-"}; input = value+'\n'; }
                    else if (operation == "profile-nickname") {
                        auto colon = value.find(':'); args = {args[0], "profile", "nickname", value.substr(0,colon), value.substr(colon+1)};
                    } else if (operation.rfind("profile-",0)==0) args = {args[0], "profile", operation.substr(8), value};
                    command(args, 300, input, &env);
                    if (operation != "download" && operation != "list" && operation != "profile-nickname")
                        command({(root/"sim.py").string(), "refresh"});
                    if (operation != "list" && operation != "profile-nickname") {
                        try { command({(root/"lpac").string(), "notification", "process", "-a", "-r"}, 90, "", &env); }
                        catch (const std::exception &) { outcome = "Profile updated; carrier notification is pending."; }
                    }
                    updated = list_profiles(env);
                }
            }
            command({systemctl, "start", "ModemManager"}); restart_mm = false;
        } catch (const std::exception &) {
            // No command output, activation codes or profile IDs enter the log.
            outcome = "Operation failed. Check cellular readiness and Wi-Fi, then retry.";
        }
        if (restart_mm) {
            try { command({systemctl, "start", "ModemManager"}, 60, "", nullptr, true); }
            catch (const std::exception &) { outcome = "SIM operation ended; ModemManager needs to be started again."; }
        }
        if (lock >= 0) close(lock); // Hold serialization through MM restoration.
        struct Result { Cellular *self; std::string message; Json profiles; };
        g_idle_add([](gpointer p)->gboolean {
            std::unique_ptr<Result> result(static_cast<Result *>(p));
            auto *self = result->self;
            if (self->worker.joinable()) self->worker.join();
            self->message = std::move(result->message);
            if (!result->profiles.is_null()) self->profiles = std::move(result->profiles);
            self->busy = false;
            if (stopped) g_main_loop_quit(self->loop);
            return G_SOURCE_REMOVE;
        }, new Result{this, std::move(outcome), std::move(updated)});
    }
    void method(const char *sender, const char *method, GVariant *args, GDBusMethodInvocation *invocation) {
        try {
            if (stopped || !authorized(sender)) {
                g_dbus_method_invocation_return_dbus_error(invocation, "org.armada.Cellular.NotAuthorized", "Use Settings in the active local session.");
                return;
            }
            if (std::string(method) == "Status") {
                std::string chosen = fs::exists(selection) ? trim(read(selection)) : "auto";
                Json result = {{"busy",busy},{"message",message},{"profiles",profiles},{"selection",chosen}};
                g_dbus_method_invocation_return_value(invocation, g_variant_new("(s)", result.dump().c_str()));
                return;
            }
            const char *op, *value; g_variant_get(args, "(&s&s)", &op, &value);
            validate(op,value);
            if (busy) throw std::invalid_argument("Operation already running");
            busy=true; message="Working…";
            try { worker=std::thread(&Cellular::work,this,std::string(op),std::string(value)); }
            catch (...) { busy=false; message="Operation could not start."; throw; }
            g_dbus_method_invocation_return_value(invocation, g_variant_new("()"));
        } catch (const std::exception &) {
            g_dbus_method_invocation_return_dbus_error(invocation, "org.armada.Cellular.Failed", "Invalid request or service unavailable.");
        }
    }
public:
    explicit Cellular(const Options &o) : root(o.get("--root","/usr/libexec/houji-cellular")),
        selection(o.get("--selection","/etc/armada/cellular-sim")), lockfile(o.get("--lock","/run/houji/cellular-control.lock")),
        systemctl(o.get("--systemctl","/usr/bin/systemctl")), timeout(std::stoul(o.get("--command-timeout","0"))) {
        bus=connect_bus(o.get("--bus-address",""));
        GError *error=nullptr; node.reset(g_dbus_node_info_new_for_xml(XML,&error));
        if(!node) throw bus_error(error);
        static const GDBusInterfaceVTable table = {
            [](GDBusConnection *,const gchar *sender,const gchar *,const gchar *,const gchar *name,GVariant *args,GDBusMethodInvocation *inv,gpointer p){static_cast<Cellular *>(p)->method(sender,name,args,inv);},
            nullptr,nullptr,{nullptr}};
        registration=g_dbus_connection_register_object(bus.get(),PATH,node->interfaces[0],&table,this,nullptr,&error);
        if(!registration) throw bus_error(error);
        owner=g_bus_own_name_on_connection(bus.get(),NAME,G_BUS_NAME_OWNER_FLAGS_NONE,nullptr,
            [](GDBusConnection *,const gchar *,gpointer p){auto *self=static_cast<Cellular *>(p);self->name_failed=true;stopped=1;if(!self->busy)g_main_loop_quit(self->loop);},this,nullptr);
    }
    ~Cellular() {
        if(timer)g_source_remove(timer);
        if(worker.joinable())worker.join();
        if(owner)g_bus_unown_name(owner);
        if(registration)g_dbus_connection_unregister_object(bus.get(),registration);
        g_main_loop_unref(loop);
    }
    int execute() {
        timer=g_timeout_add(200,[](gpointer p)->gboolean {auto *s=static_cast<Cellular *>(p);if(stopped&&!s->busy)g_main_loop_quit(s->loop);return G_SOURCE_CONTINUE;},this);
        g_main_loop_run(loop);return name_failed?1:0;
    }
};
int main(int argc,char **argv) {
    try {
        if(argc==4 && std::string(argv[1])=="--validate") {validate(argv[2],argv[3]);return 0;}
        Options o(argc,argv,{"--root","--selection","--lock","--systemctl","--bus-address","--command-timeout"});
        signals(); signal(SIGPIPE,SIG_IGN);Cellular service(o);return service.execute();
    } catch(const std::exception &) {std::cerr<<"Cellular settings broker unavailable\n";return 1;}
}
