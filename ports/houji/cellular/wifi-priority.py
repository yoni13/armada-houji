#!/usr/bin/python3
"""Prefer a routed Wi-Fi connection over WWAN, across address families.

Metrics only compare routes within one address family. An IPv4-only Wi-Fi
connection must also suppress the modem's IPv6 default route. Reapply changes
only the active modem connection; saved APNs, roaming and autoconnect choices
are left to NetworkManager/KDE. On Wi-Fi loss, restore the saved IP preferences.
"""
import fcntl
from pathlib import Path
import sys

NM = 'org.freedesktop.NetworkManager'
DEVICE = NM+'.Device'
ACTIVE = NM+'.Connection.Active'
SETTINGS = NM+'.Settings.Connection'
PROPERTIES = 'org.freedesktop.DBus.Properties'


def has_wifi_route(connection, settings, ip_configs):
    if connection.get('Type') != '802-11-wireless' or connection.get('State') != 2:
        return False
    if settings.get('802-11-wireless', {}).get('mode', 'infrastructure') != 'infrastructure':
        return False
    for family, config in zip(('ipv4', 'ipv6'), ip_configs):
        if settings.get(family, {}).get('never-default', False):
            continue
        if config.get('Gateway') or any(route.get('prefix') == 0 for route in config.get('RouteData', [])):
            return True
    return False


def routing_changes(applied, saved, prefer_wifi):
    """Only manage default routes and automatic DNS on the active WWAN bearer."""
    changes = {}
    for family in ('ipv4', 'ipv6'):
        if family not in applied:
            continue
        for key in ('never-default', 'ignore-auto-dns'):
            wanted = True if prefer_wifi else saved.get(family, {}).get(key, False)
            if applied[family].get(key, False) != wanted:
                changes.setdefault(family, {})[key] = wanted
    return changes


def run(bus=None):
    from gi.repository import Gio, GLib
    if bus is None:
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)

    def call(path, interface, method, args=None):
        return bus.call_sync(NM, path, interface, method, args, None,
                             Gio.DBusCallFlags.NONE, 10000, None)

    def props(path, interface):
        if path == '/':
            return {}
        return call(path, PROPERTIES, 'GetAll', GLib.Variant('(s)', (interface,))).unpack()[0]

    manager = props('/org/freedesktop/NetworkManager', NM)
    connections = []
    prefer_wifi = False
    for path in manager.get('ActiveConnections', []):
        try:
            active = props(path, ACTIVE)
            if active.get('State') != 2 or active.get('Type') not in ('gsm', 'cdma', '802-11-wireless'):
                continue
            saved = call(active['Connection'], SETTINGS, 'GetSettings').unpack()[0]
            connections.append((active, saved))
            if active['Type'] == '802-11-wireless':
                configs = [props(active.get('Ip4Config', '/'), NM+'.IP4Config'),
                           props(active.get('Ip6Config', '/'), NM+'.IP6Config')]
                prefer_wifi |= has_wifi_route(active, saved, configs)
        except GLib.Error:
            # Objects may disappear during disconnect/suspend. The next event
            # reconciles the resulting topology; never print private settings.
            continue

    for active, saved in connections:
        if active['Type'] not in ('gsm', 'cdma'):
            continue
        for device in active.get('Devices', []):
            try:
                reply = call(device, DEVICE, 'GetAppliedConnection', GLib.Variant('(u)', (0,)))
                applied, version = reply.unpack()
                changes = routing_changes(applied, saved, prefer_wifi)
                if not changes:
                    continue
                # Preserve every original variant's D-Bus signature, including
                # IP addresses/routes and settings not understood by this helper.
                typed = {}
                data = reply.get_child_value(0)
                for i in range(data.n_children()):
                    entry = data.get_child_value(i)
                    group = entry.get_child_value(0).get_string()
                    values = entry.get_child_value(1)
                    typed[group] = {}
                    for j in range(values.n_children()):
                        item = values.get_child_value(j)
                        typed[group][item.get_child_value(0).get_string()] = item.get_child_value(1).get_variant()
                for family, values in changes.items():
                    for key, value in values.items():
                        typed[family][key] = GLib.Variant('b', value)
                call(device, DEVICE, 'Reapply', GLib.Variant('(a{sa{sv}}tu)', (typed, version, 0)))
                print('Wi-Fi priority: '+('cellular defaults suppressed' if prefer_wifi else 'cellular defaults restored'), flush=True)
            except GLib.Error:
                print('Wi-Fi priority: device changed during reapply; next network event will retry', file=sys.stderr)


def main():
    lockdir = Path('/run/houji')
    lockdir.mkdir(exist_ok=True)
    with (lockdir/'wifi-priority.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        run()


if __name__ == '__main__':
    main()
