#!/usr/bin/python3
"""Houji Settings helper: replies hold no identifiers, and requests run the right commands."""
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('helper', Path(__file__).with_name('houji-settings.py'))
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)

# Synthetic identifiers; none may appear in a reply.
IMEI = '350000000000001'
ICCID_A = '8988000000000000001'
ICCID_B = '8988000000000000002'
SECRETS = [IMEI, ICCID_A, ICCID_B, '+15550100', 'secret-apn-password', 'internet.example',
           '466-92', '/org/freedesktop/ModemManager1/SIM/1', 'deadbeef-uuid-a', 'deadbeef-uuid-b',
           '89049000000000000000000000000001']
CODE = 'LPA:1$smdp.test.invalid$SYNTHETIC-ACTIVATION-CODE'

MMCLI = json.dumps({'modem': {
    'generic': {'state': 'connected', 'state-failed-reason': '--', 'equipment-identifier': IMEI,
                'own-numbers': ['+15550100'], 'sim': '/org/freedesktop/ModemManager1/SIM/1',
                'access-technologies': ['lte'], 'signal-quality': {'value': '67', 'recent': 'yes'},
                'device-identifier': '89049000000000000000000000000001'},
    '3gpp': {'imei': IMEI, 'operator-code': '466-92', 'operator-name': 'Test Carrier',
             'registration-state': 'roaming',
             'eps': {'initial-bearer': {'settings': {'apn': 'internet.example',
                                                     'password': 'secret-apn-password'}}}}}})
DEVICES = """GENERAL.DEVICE:wlp1s0
GENERAL.TYPE:wifi
GENERAL.STATE:100 (connected)
GENERAL.AUTOCONNECT:yes
GENERAL.CON-UUID:wifi-uuid
CONNECTIONS.AVAILABLE-CONNECTION-PATHS:/org/freedesktop/NetworkManager/Settings/4

GENERAL.DEVICE:qrtr0
GENERAL.TYPE:gsm
GENERAL.STATE:100 (connected)
GENERAL.AUTOCONNECT:yes
GENERAL.CON-UUID:deadbeef-uuid-b
CONNECTIONS.AVAILABLE-CONNECTION-PATHS:/org/freedesktop/NetworkManager/Settings/7 | /org/freedesktop/NetworkManager/Settings/9
"""
CONNECTIONS = {
    '/org/freedesktop/NetworkManager/Settings/7':
        'connection.uuid:deadbeef-uuid-a\nconnection.timestamp:100\nconnection.autoconnect:yes\ngsm.home-only:yes\n',
    '/org/freedesktop/NetworkManager/Settings/9':
        'connection.uuid:deadbeef-uuid-b\nconnection.timestamp:200\nconnection.autoconnect:yes\ngsm.home-only:no\n',
}


class FakeSystem:
    def __init__(self, devices=DEVICES, cellular_active=True):
        self.commands = []
        self.dbus_calls = []
        self.devices = devices
        self.cellular_active = cellular_active
        self.state = {'busy': False, 'message': 'Done', 'selection': 'esim', 'profiles': [
            {'iccid': ICCID_A, 'profileName': 'Travel', 'profileNickname': None,
             'serviceProviderName': 'Test Carrier', 'profileState': 'enabled'},
            {'iccid': ICCID_B, 'profileName': 'Home', 'profileNickname': 'Mine',
             'serviceProviderName': 'Other Carrier', 'profileState': 'disabled'}]}

    def run(self, *args, timeout=20):
        self.commands.append(args)
        out, code = '', 0
        if args[:2] == ('systemctl', 'is-active'):
            code = 0 if self.cellular_active else 3
        elif args[0] == 'mmcli':
            out = MMCLI
        elif args[-2:] == ('device', 'show'):
            out = self.devices
        elif args[-3:-1] == ('show', 'path'):
            out = CONNECTIONS[args[-1]]
        elif args[-2:] == ('radio', 'wwan'):
            out = 'enabled\n'
        return subprocess.CompletedProcess(args, code, out, '')

    def dbus(self, service, method, signature=None, args=(), reply='(s)', start=True):
        self.dbus_calls.append((service[0], method, args))
        if method == 'Status':
            return (json.dumps(self.state),)
        if method == 'GetStatus':
            return (json.dumps({'busy': False, 'message': 'NFC is off', 'mode': 'off', 'tags': 1}),)
        return ()


class Helper(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for name, relative in (('ROTATION_LOCK', 'etc/houji-rotation-lock'), ('ORIENTATION_STATE', 'run/houji-orientation/current'),
                               ('CHARGE_LIMIT', 'etc/houji-charge-limit'), ('CHARGE_STATE', 'run/charge-limit.json'),
                               ('HANDLE_KEY', 'run/settings-handle-key'), ('SIM_SELECTION', 'etc/cellular-sim'),
                               ('NFC_SETTINGS', 'nfc/settings.json'), ('BATTERY', 'battery')):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            patcher = mock.patch.object(helper, name, path)
            patcher.start()
            self.addCleanup(patcher.stop)
        (self.root / 'battery').mkdir()
        (self.root / 'battery/capacity').write_text('81\n')
        (self.root / 'battery/status').write_text('Not charging\n')
        (self.root / 'etc/cellular-sim').write_text('esim\n')

    def call(self, system, **request):
        return helper.handle(system, json.dumps(request))

    def test_status_reply_carries_no_identifiers(self):
        system = FakeSystem()
        reply = self.call(system, op='status')
        self.assertTrue(reply['ok'], reply)
        dumped = json.dumps(reply)
        for secret in SECRETS:
            self.assertNotIn(secret, dumped)
        cellular = reply['result']['cellular']
        self.assertEqual([p['name'] for p in cellular['profiles']], ['Travel', 'Mine'])
        for profile in cellular['profiles']:
            self.assertRegex(profile['handle'], r'^[0-9a-f]{16}$')
        self.assertEqual(cellular['modem'], {'state': 'connected', 'reason': None, 'signal': 67,
                                             'technology': 'lte', 'operator': 'Test Carrier',
                                             'registered': True, 'roaming': True})
        self.assertEqual(cellular['data'], {'modem': True, 'ready': True, 'profile': True, 'enabled': True,
                                            'connected': True, 'roaming_allowed': True})
        self.assertEqual(reply['result']['nfc'], {'enabled': False, 'emulating': False, 'busy': False,
                                                  'message': 'NFC is off'})
        # Reading NFC status must not start the service.
        self.assertIn(('org.armada.Nfc', 'GetStatus', ()), system.dbus_calls)

    def test_handles_are_per_key_and_resolve_to_the_profile(self):
        system = FakeSystem()
        handles = [p['handle'] for p in self.call(system, op='status')['result']['cellular']['profiles']]
        self.assertEqual(handles, [p['handle'] for p in self.call(system, op='status')['result']['cellular']['profiles']])
        self.assertEqual(oct(helper.HANDLE_KEY.stat().st_mode & 0o777), '0o600')
        reply = self.call(system, op='esim.profile', action='disable', handle=handles[0])
        self.assertTrue(reply['ok'], reply)
        self.assertIn(('org.armada.Cellular', 'Run', ('profile-disable', ICCID_A)), system.dbus_calls)
        self.assertNotIn(ICCID_A, json.dumps(reply))
        reply = self.call(system, op='esim.profile', action='nickname', handle=handles[1], nickname=' Work ')
        self.assertIn(('org.armada.Cellular', 'Run', ('profile-nickname', ICCID_B + ':Work')), system.dbus_calls)
        for bad in [dict(action='enable', handle='0' * 16), dict(action='format', handle=handles[0]),
                    dict(action='nickname', handle=handles[0], nickname='x' * 65),
                    dict(action='nickname', handle=handles[0], nickname='a\nb')]:
            reply = self.call(system, op='esim.profile', **bad)
            self.assertFalse(reply['ok'], bad)
        # A new boot means a new key, so old handles stop resolving.
        helper.HANDLE_KEY.unlink()
        self.assertFalse(self.call(system, op='esim.profile', action='enable', handle=handles[1])['ok'])

    def test_activation_code_goes_only_to_dbus(self):
        system = FakeSystem()
        reply = self.call(system, op='esim.download', code='  ' + CODE + '\n')
        self.assertTrue(reply['ok'], reply)
        self.assertIn(('org.armada.Cellular', 'Run', ('download', CODE)), system.dbus_calls)
        self.assertNotIn('ACTIVATION', json.dumps(reply))
        self.assertFalse(any('ACTIVATION' in ' '.join(c) for c in system.commands))
        for bad in ['', 'LPA:2$x', CODE + ' extra', 'LPA:1$' + 'x' * 2048, None, 7]:
            reply = self.call(system, op='esim.download', code=bad)
            self.assertFalse(reply['ok'])
            self.assertNotIn('SYNTHETIC', reply['error'])

        class Broken(FakeSystem):
            def dbus(self, *args, **kwargs):
                raise RuntimeError('GDBus.Error: rejected ' + CODE)
        reply = self.call(Broken(), op='esim.download', code=CODE)
        self.assertFalse(reply['ok'])
        self.assertNotIn('SYNTHETIC', json.dumps(reply))

    def test_profile_changes_need_esim_selected(self):
        helper.SIM_SELECTION.write_text('physical1\n')
        system = FakeSystem()
        for request in [dict(op='esim.refresh'), dict(op='esim.download', code=CODE),
                        dict(op='esim.profile', action='enable', handle='0' * 16)]:
            reply = self.call(system, **request)
            self.assertFalse(reply['ok'])
        self.assertFalse(any(call[1] == 'Run' for call in system.dbus_calls))
        self.assertTrue(self.call(system, op='sim.select', mode='esim')['ok'])
        self.assertIn(('org.armada.Cellular', 'Run', ('select', 'esim')), system.dbus_calls)
        self.assertFalse(self.call(system, op='sim.select', mode='slot9')['ok'])

    def nm_commands(self, system):
        return [c[1:] for c in system.commands if c[0] == 'nmcli' and 'show' not in c and c[-2:] != ('radio', 'wwan')]

    def test_mobile_data_follows_plasma_mobile_order(self):
        system = FakeSystem()
        self.assertTrue(self.call(system, op='data.set', enabled=True)['ok'])
        self.assertEqual(self.nm_commands(system), [
            ('device', 'set', 'qrtr0', 'autoconnect', 'no'),
            ('connection', 'modify', 'uuid', 'deadbeef-uuid-a', 'connection.autoconnect', 'no'),
            ('connection', 'modify', 'uuid', 'deadbeef-uuid-b', 'connection.autoconnect', 'yes'),
            ('--wait', '0', 'connection', 'up', 'uuid', 'deadbeef-uuid-b', 'ifname', 'qrtr0'),
            ('device', 'set', 'qrtr0', 'autoconnect', 'yes')])
        system = FakeSystem()
        self.assertTrue(self.call(system, op='data.set', enabled=False)['ok'])
        self.assertEqual(self.nm_commands(system), [
            ('device', 'set', 'qrtr0', 'autoconnect', 'no'),
            ('connection', 'modify', 'uuid', 'deadbeef-uuid-a', 'connection.autoconnect', 'no'),
            ('connection', 'modify', 'uuid', 'deadbeef-uuid-b', 'connection.autoconnect', 'no'),
            ('device', 'disconnect', 'qrtr0')])

    def test_roaming_changes_the_active_profile_and_reactivates_it(self):
        system = FakeSystem()
        self.assertTrue(self.call(system, op='roaming.set', allowed=False)['ok'])
        self.assertEqual(self.nm_commands(system), [
            ('connection', 'modify', 'uuid', 'deadbeef-uuid-b', 'gsm.home-only', 'yes'),
            ('--wait', '0', 'connection', 'up', 'uuid', 'deadbeef-uuid-b', 'ifname', 'qrtr0')])
        # Not connected: change the most recent profile and leave it down.
        system = FakeSystem(devices=DEVICES.replace('GENERAL.CON-UUID:deadbeef-uuid-b', 'GENERAL.CON-UUID:')
                            .replace('GENERAL.STATE:100 (connected)\nGENERAL.AUTOCONNECT:yes\nGENERAL.CON-UUID:\n',
                                     'GENERAL.STATE:30 (disconnected)\nGENERAL.AUTOCONNECT:yes\nGENERAL.CON-UUID:\n'))
        self.assertTrue(self.call(system, op='roaming.set', allowed=True)['ok'])
        self.assertEqual(self.nm_commands(system), [
            ('connection', 'modify', 'uuid', 'deadbeef-uuid-b', 'gsm.home-only', 'no')])

    def test_no_modem_or_profile(self):
        unavailable = DEVICES.split('\n\n')[0] + '\n\nGENERAL.DEVICE:qrtr0\nGENERAL.TYPE:gsm\n' \
            'GENERAL.STATE:20 (unavailable)\nGENERAL.AUTOCONNECT:yes\nGENERAL.CON-UUID:\n' \
            'CONNECTIONS.AVAILABLE-CONNECTION-PATHS:\n'
        system = FakeSystem(devices=unavailable)
        data = self.call(system, op='status')['result']['cellular']['data']
        self.assertEqual(data, {'modem': True, 'ready': False, 'profile': False, 'enabled': False,
                                'connected': False, 'roaming_allowed': None})
        reply = self.call(system, op='data.set', enabled=True)
        self.assertFalse(reply['ok'])
        self.assertIn('APN', reply['error'])
        system = FakeSystem(cellular_active=False)
        cellular = self.call(system, op='status')['result']['cellular']
        self.assertEqual((cellular['enabled'], cellular['profiles'], cellular['data']), (False, [], None))
        self.assertFalse(any(c[0] in ('mmcli', 'nmcli') for c in system.commands))

    def test_rotation_lock_file(self):
        system = FakeSystem()
        helper.ORIENTATION_STATE.parent.mkdir(parents=True, exist_ok=True)
        helper.ORIENTATION_STATE.write_text('left\n')
        self.assertEqual(self.call(system, op='status')['result']['rotation'], {'lock': None, 'current': 'left'})
        self.assertEqual(self.call(system, op='rotation.set', mode='left')['result']['lock'], 'left')
        self.assertEqual(helper.ROTATION_LOCK.read_text(), 'left\n')
        self.assertEqual(oct(helper.ROTATION_LOCK.stat().st_mode & 0o777), '0o644')
        self.assertTrue(self.call(system, op='rotation.set', mode='auto')['ok'])
        self.assertFalse(helper.ROTATION_LOCK.exists())
        for mode in ['left-up', '../x', None]:
            self.assertFalse(self.call(system, op='rotation.set', mode=mode)['ok'])

    def test_charge_limit_file_and_state(self):
        system = FakeSystem()
        reply = self.call(system, op='charge_limit.set', limit=80)
        self.assertEqual(reply['result'], {'limit': 80, 'holding': False, 'capacity': 81, 'status': 'Not charging'})
        self.assertEqual(helper.CHARGE_LIMIT.read_text(), '80\n')
        helper.CHARGE_STATE.write_text('{"limit": 80, "holding": true}\n')
        self.assertTrue(self.call(system, op='status')['result']['charging']['holding'])
        # A state file from an older limit is not shown as holding the new one.
        self.call(system, op='charge_limit.set', limit=90)
        self.assertFalse(self.call(system, op='status')['result']['charging']['holding'])
        for limit in [100, None]:
            self.assertTrue(self.call(system, op='charge_limit.set', limit=limit)['ok'])
            self.assertFalse(helper.CHARGE_LIMIT.exists())
        for limit in [49, 101, 80.5, '80', True]:
            self.assertFalse(self.call(system, op='charge_limit.set', limit=limit)['ok'], limit)

    def test_nfc_toggle_stops_emulation_first(self):
        helper.NFC_SETTINGS.write_text(json.dumps({'reader_enabled': False, 'emulation_enabled': True,
                                                   'text': 'private tag text', 'serial': '01:02:03:04'}))
        system = FakeSystem()
        reply = self.call(system, op='nfc.set', enabled=False)
        self.assertTrue(reply['ok'], reply)
        self.assertEqual([c[1] for c in system.dbus_calls][:2], ['StopEmulation', 'SetReader'])
        self.assertNotIn('private tag text', json.dumps(reply))
        self.assertNotIn('01:02:03:04', json.dumps(reply))
        self.assertFalse(self.call(system, op='nfc.set', enabled='yes')['ok'])

    def test_bad_requests_and_unexpected_errors(self):
        system = FakeSystem()
        for raw in ['', 'null', '[]', '{"op": "shell"}', '{"op": 1}']:
            self.assertEqual(helper.handle(system, raw)['ok'], False, raw)

        class Exploding(FakeSystem):
            def run(self, *args, **kwargs):
                raise RuntimeError('nmcli: connection "' + ICCID_A + '" failed')
        reply = self.call(Exploding(), op='data.set', enabled=True)
        self.assertEqual(reply, {'ok': False, 'error': 'The setting could not be changed.'})

    def test_terse_records(self):
        records = helper.terse_records(DEVICES)
        self.assertEqual([r['GENERAL.DEVICE'] for r in records], ['wlp1s0', 'qrtr0'])
        self.assertEqual(helper.SETTINGS_PATH.findall(records[1]['CONNECTIONS.AVAILABLE-CONNECTION-PATHS']),
                         list(CONNECTIONS))
        self.assertEqual(helper.terse_records('A:x\\:y\nA:z\n'), [{'A': 'x:y z'}])


if __name__ == '__main__':
    unittest.main()
