#!/usr/bin/python3
"""IPv4-only Wi-Fi must not send IPv6 game downloads over the modem."""
import importlib.util
from pathlib import Path
import unittest

try:
    from gi.repository import GLib
except ImportError:
    GLib = None

spec = importlib.util.spec_from_file_location('priority', Path(__file__).with_name('wifi-priority.py'))
priority = importlib.util.module_from_spec(spec)
spec.loader.exec_module(priority)


class Priority(unittest.TestCase):
    def test_ipv4_only_wifi_suppresses_both_wwan_families(self):
        active = {'Type':'802-11-wireless', 'State':2}
        wifi = {'ipv4':{}, 'ipv6':{}}
        self.assertTrue(priority.has_wifi_route(active, wifi, [{'Gateway':'192.0.2.1'}, {}]))
        self.assertEqual(priority.routing_changes(wifi, wifi, True), {
            'ipv4': {'never-default':True, 'ignore-auto-dns':True},
            'ipv6': {'never-default':True, 'ignore-auto-dns':True}})

    def test_local_only_hotspot_or_disconnected_wifi_does_not_block_fallback(self):
        for active, settings, configs in (
                ({'Type':'802-11-wireless','State':1}, {}, [{'Gateway':'192.0.2.1'},{}]),
                ({'Type':'802-11-wireless','State':2}, {'802-11-wireless':{'mode':'ap'}}, [{'Gateway':'192.0.2.1'},{}]),
                ({'Type':'802-11-wireless','State':2}, {}, [{},{}]),
                ({'Type':'802-11-wireless','State':2}, {'ipv4':{'never-default':True}}, [{'Gateway':'192.0.2.1'},{}])):
            self.assertFalse(priority.has_wifi_route(active, settings, configs))

    def test_fallback_restores_saved_choices_and_reapply_is_idempotent(self):
        applied = {f:{'never-default':True, 'ignore-auto-dns':True, 'route-metric':700} for f in ('ipv4','ipv6')}
        saved = {'ipv4':{}, 'ipv6':{'never-default':True,'ignore-auto-dns':True}}
        self.assertEqual(priority.routing_changes(applied, saved, False),
                         {'ipv4':{'never-default':False,'ignore-auto-dns':False}})
        self.assertEqual(priority.routing_changes(applied, saved, True), {})
        self.assertEqual(applied['ipv4']['route-metric'],700)

    def test_ipv6_only_wifi_and_default_route_data(self):
        self.assertTrue(priority.has_wifi_route({'Type':'802-11-wireless','State':2}, {},
                                                [{},{'RouteData':[{'dest':'::','prefix':0}]}]))


@unittest.skipIf(GLib is None, 'PyGObject needed for typed D-Bus reapply replay')
class Reapply(unittest.TestCase):
    def test_wifi_arrival_and_departure_preserve_other_settings(self):
        class Bus:
            wifi = True
            version = 7
            changes = []
            saved = {
                'connection': {'type':GLib.Variant('s','gsm'), 'autoconnect':GLib.Variant('b',True)},
                'gsm': {'apn':GLib.Variant('s','test.invalid'), 'home-only':GLib.Variant('b',True)},
                'ipv4': {'method':GLib.Variant('s','auto'), 'route-metric':GLib.Variant('x',700)},
                'ipv6': {'method':GLib.Variant('s','auto'), 'route-metric':GLib.Variant('x',700)},
            }
            applied = {k:dict(v) for k,v in saved.items()}

            def call_sync(self, service, path, interface, method, args, *unused):
                if method == 'GetAll':
                    if path == '/org/freedesktop/NetworkManager':
                        props = {'ActiveConnections': GLib.Variant('ao', ['/cell']+(['/wifi'] if self.wifi else []))}
                    elif path in ('/cell', '/wifi'):
                        props = {'Type':GLib.Variant('s','gsm' if path=='/cell' else '802-11-wireless'),
                                 'State':GLib.Variant('u',2), 'Connection':GLib.Variant('o',path+'/saved'),
                                 'Devices':GLib.Variant('ao',['/device'] if path=='/cell' else []),
                                 'Ip4Config':GLib.Variant('o','/ip4'), 'Ip6Config':GLib.Variant('o','/')}
                    elif path == '/ip4':
                        props = {'Gateway':GLib.Variant('s','192.0.2.1')}
                    else:
                        raise AssertionError(path)
                    return GLib.Variant('(a{sv})',(props,))
                if method == 'GetSettings':
                    return GLib.Variant('(a{sa{sv}})',(self.saved if path=='/cell/saved' else {},))
                if method == 'GetAppliedConnection':
                    return GLib.Variant('(a{sa{sv}}t)',(self.applied,self.version))
                if method == 'Reapply':
                    self.changes.append(args)
                    self.applied = {k:{kk:vv for kk,vv in v.items()} for k,v in self.applied.items()}
                    decoded, version, flags = args.unpack()
                    if version != self.version or flags != 0:
                        raise AssertionError('Reapply version/flags changed')
                    for family in ('ipv4','ipv6'):
                        for key in ('never-default','ignore-auto-dns'):
                            self.applied[family][key] = GLib.Variant('b',decoded[family][key])
                    self.version += 1
                    return GLib.Variant('()',())
                raise AssertionError(method)

        bus = Bus()
        priority.run(bus)
        self.assertEqual(len(bus.changes), 1)
        settings = bus.changes[0].get_child_value(0)
        self.assertEqual(bus.changes[0].unpack()[0]['gsm'], {'apn':'test.invalid', 'home-only':True})
        # The original signed 64-bit metric signature must survive repacking.
        for i in range(settings.n_children()):
            group = settings.get_child_value(i)
            if group.get_child_value(0).get_string() == 'ipv6':
                values = group.get_child_value(1)
                for j in range(values.n_children()):
                    item = values.get_child_value(j)
                    if item.get_child_value(0).get_string() == 'route-metric':
                        self.assertEqual(item.get_child_value(1).get_variant().get_type_string(),'x')
        priority.run(bus)
        self.assertEqual(len(bus.changes), 1)  # no dispatcher reapply loop
        bus.wifi = False
        priority.run(bus)
        self.assertEqual(len(bus.changes), 2)
        for family in ('ipv4','ipv6'):
            self.assertFalse(bus.changes[-1].unpack()[0][family]['never-default'])
            self.assertFalse(bus.changes[-1].unpack()[0][family]['ignore-auto-dns'])
        self.assertNotIn('never-default', bus.saved['ipv4'])


if __name__ == '__main__':
    unittest.main()
