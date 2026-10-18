// SPDX-License-Identifier: GPL-2.0-only
#pragma once
#include "common.hpp"
#include <gio/gio.h>
#include <memory>

namespace houji {
struct Unref { template<class T> void operator()(T *p) const { if (p) g_object_unref(p); } };
template<class T> using Object = std::unique_ptr<T, Unref>;
struct VariantUnref { void operator()(GVariant *p) const { if (p) g_variant_unref(p); } };
using Variant = std::unique_ptr<GVariant, VariantUnref>;
inline std::runtime_error bus_error(GError *error) {
    std::string message = error ? error->message : "D-Bus failure";
    if (error) g_error_free(error);
    return std::runtime_error(message);
}
inline Object<GDBusConnection> connect_bus(const std::string &address) {
    GError *error = nullptr;
    auto *connection = address.empty() ? g_bus_get_sync(G_BUS_TYPE_SYSTEM, nullptr, &error) :
        g_dbus_connection_new_for_address_sync(address.c_str(),
            static_cast<GDBusConnectionFlags>(G_DBUS_CONNECTION_FLAGS_AUTHENTICATION_CLIENT |
                                              G_DBUS_CONNECTION_FLAGS_MESSAGE_BUS_CONNECTION), nullptr, nullptr, &error);
    if (!connection) throw bus_error(error);
    return Object<GDBusConnection>(connection);
}
inline Variant call(GDBusConnection *bus, const char *name, const char *path,
                    const char *interface, const char *method, GVariant *args,
                    const GVariantType *reply, int timeout = 5000) {
    GError *error = nullptr;
    auto *value = g_dbus_connection_call_sync(bus, name, path, interface, method,
                    args, reply, G_DBUS_CALL_FLAGS_NONE, timeout, nullptr, &error);
    if (!value) throw bus_error(error);
    return Variant(value);
}
}
