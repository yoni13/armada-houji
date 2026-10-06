// SPDX-License-Identifier: LGPL-2.1-or-later
// Re-entrant observers must see the new cache, not the object being removed.
#include "manager_p.h"
#include "modemdevice_p.h"
#include "dbus/fakedbus.h"
#include <QCoreApplication>
#include <cstdio>
#include <cstdlib>

static void require(bool condition, const char *message)
{
    if (!condition) {
        std::fprintf(stderr, "%s\n", message);
        std::exit(1);
    }
}

int main(int argc, char **argv)
{
    QCoreApplication app(argc, argv);
    ModemManager::ModemManagerPrivate manager;
    const QString path = QString::fromLatin1(MMQT_DBUS_MODEM_PREFIX) + QStringLiteral("/999");
    manager.modemList.insert(path, ModemManager::ModemDevice::Ptr(new ModemManager::ModemDevice(path)));
    int removed = 0;
    QObject::connect(&manager, &ModemManager::Notifier::modemRemoved, &app, [&](const QString &id) {
        require(id == path, "Wrong modem removed");
        require(manager.modemDevices().isEmpty(), "Removal observer still sees obsolete modem");
        ++removed;
    }, Qt::DirectConnection);
    require(QMetaObject::invokeMethod(&manager, "onInterfacesRemoved", Qt::DirectConnection,
        Q_ARG(QDBusObjectPath, QDBusObjectPath(path)),
        Q_ARG(QStringList, QStringList{QString::fromLatin1(MMQT_DBUS_INTERFACE_MODEM)})), "Removal invocation failed");
    require(removed == 1, "Modem removal signal missing");

    manager.modemList.insert(path, ModemManager::ModemDevice::Ptr(new ModemManager::ModemDevice(path)));
    bool serviceGone = false;
    QObject::connect(&manager, &ModemManager::Notifier::serviceDisappeared, &app, [&] {
        require(manager.modemDevices().isEmpty(), "Daemon loss observer sees obsolete modem");
        serviceGone = true;
    }, Qt::DirectConnection);
    require(QMetaObject::invokeMethod(&manager, "daemonUnregistered", Qt::DirectConnection), "Unregister invocation failed");
    require(removed == 2 && serviceGone, "Daemon loss did not invalidate modem observers");

    ModemManager::ModemDevice device(path);
    ModemManager::ModemDevicePrivate state(path, &device);
    const QString simPath = QStringLiteral("/org/kde/fakemodem/SIM/999");
    int simsRemoved = 0;
    QObject::connect(&device, &ModemManager::ModemDevice::simRemoved, &app, [&](const QString &) {
        require(!state.simCard, "SIM removal observer still sees removed SIM");
        ++simsRemoved;
    }, Qt::DirectConnection);
    require(QMetaObject::invokeMethod(&state, "onSimPathChanged", Qt::DirectConnection,
        Q_ARG(QString, QStringLiteral("/")), Q_ARG(QString, simPath)), "SIM arrival invocation failed");
    require(state.simCard && state.simCard->uni() == simPath, "SIM arrival with empty cache failed");
    require(QMetaObject::invokeMethod(&state, "onSimPathChanged", Qt::DirectConnection,
        Q_ARG(QString, simPath), Q_ARG(QString, QStringLiteral("/"))), "SIM removal invocation failed");
    require(simsRemoved == 1 && !state.simCard, "D-Bus null path created a phantom SIM");
    std::puts("PASS: modem removal, daemon loss and SIM replacement are observer-consistent");
}
