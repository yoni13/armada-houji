#!/usr/bin/python3
"""Off-host lifecycle/route recovery, settings migration and serialized mode switching."""
from collections import deque
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import Mock, patch

import ipass
from ese import ProtocolError

gi = types.ModuleType('gi')
repo = types.ModuleType('gi.repository')
repo.Gio = types.SimpleNamespace()
repo.GLib = types.SimpleNamespace()
sys.modules['gi'] = gi
sys.modules['gi.repository'] = repo
spec = importlib.util.spec_from_file_location('service', Path(__file__).with_name('service.py'))
service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(service)


class Link:
    def __init__(self, activation=0):
        self.activation = activation
        self.config = {0x30: b'\x04', 0x31: b'\0', 0x32: b'\x20', 0x33: b'\x12\x34\x56\x78',
                       0x80: b'\0', 0x81: b'\x01', 0x85: b'\0'}
        self.original = dict(self.config)
        self.table = []
        self.messages = deque()
        self.calls = []
        self.reply = b'\0'
        self.discovering = False

    def exchange(self, apdu):
        self.calls.append(('apdu', apdu))
        if apdu == ipass.SELECT:
            return b'\x90\0'
        if apdu == ipass.QUERY:
            fields = ipass.FILTER + b'\x9f\x70\x02\x3f' + bytes((self.activation,))
            return bytes((0x61, len(fields))) + fields + b'\x90\0'
        if apdu[:3] == bytes.fromhex('80f001'):
            self.activation = apdu[3]
            return b'\x90\0'
        raise AssertionError('Unexpected APDU')

    def nci(self, group, opcode, data=b''):
        self.calls.append(('nci', group, opcode, data))
        if (group, opcode) == (0, 3):
            return b'\0' + bytes((len(ipass.CONFIG_IDS),)) + b''.join(
                bytes((key, len(self.config[key]))) + self.config[key] for key in data[1:])
        if (group, opcode) == (0, 2):
            self.config.update(dict(ipass.tlvs(data[1:])))
        elif (group, opcode) == (0, 0):
            self.table = []
            self.config = {k: b'\0' for k in ipass.CONFIG_IDS}
        elif (group, opcode) == (1, 1):
            self.table = [bytes((tag, len(value))) + value for tag, value in ipass.tlvs(data[2:])]
        elif (group, opcode) == (1, 2):
            self.reply = bytes((0, len(self.table))) + b''.join(self.table)
        elif (group, opcode) == (1, 3):
            self.discovering = True
        elif (group, opcode) == (1, 6):
            self.discovering = False
        return b'\0'

    def notification(self, group, opcode, timeout=5):
        return self.reply if (group, opcode) == (1, 2) else b'\0'

    def send_hci(self, *args):
        self.calls.append(('hci', *args))


class Protocol(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.recovery = Path(directory.name) / 'restore.json'

    def test_start_restores_original_activation_config_and_routes(self):
        for original in (0, 1):
            link = Link(original)
            offhost = ipass.Offhost(link, self.recovery)
            offhost.start()
            self.assertEqual(link.activation, 1)
            self.assertEqual(link.table, ipass.routes())
            self.assertTrue(link.discovering)
            self.assertEqual(link.config[0x85], b'\x01')
            self.assertIn(('hci', 0x19, 0x61), link.calls)
            self.assertEqual(self.recovery.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(self.recovery.read_text())['activation'], original)
            offhost.restore()
            self.assertEqual(link.activation, original)
            self.assertFalse(link.discovering)
            self.assertFalse(link.table)
            self.assertEqual(link.config, link.original)
            self.assertFalse(self.recovery.exists())

    def test_recovery_survives_failure_and_fresh_process(self):
        link = Link()
        offhost = ipass.Offhost(link, self.recovery)
        offhost.start()
        with patch.object(link, 'exchange', side_effect=TimeoutError):
            with self.assertRaises(TimeoutError):
                offhost.restore()
        self.assertTrue(self.recovery.exists())
        # A service restart brings NCI to idle before loading the saved record.
        link.discovering = False
        ipass.Offhost(link, self.recovery).restore()
        self.assertEqual(link.activation, 0)
        self.assertFalse(self.recovery.exists())

    def test_no_activation_until_recovery_saved_and_no_overwrite(self):
        link = Link()
        offhost = ipass.Offhost(link, self.recovery)
        with patch.object(ipass, 'save_recovery', side_effect=OSError):
            with self.assertRaises(OSError): offhost.start()
        self.assertEqual(link.activation, 0)
        self.recovery.write_text('existing recovery')
        with self.assertRaises(ProtocolError): offhost.start()
        self.assertEqual(self.recovery.read_text(), 'existing recovery')

    def test_existing_routes_and_nonactivatable_target_are_untouched(self):
        link = Link(0x80)
        with self.assertRaises(ProtocolError): ipass.Offhost(link, self.recovery).start()
        link = Link()
        link.table = [b'\x00\x03\x00\x01\x00']
        with self.assertRaises(ProtocolError): ipass.Offhost(link, self.recovery).start()
        self.assertEqual(link.activation, 0)
        self.assertFalse(self.recovery.exists())

    def test_tlv_bounds(self):
        for value in (b'\x9f', b'\x61', b'\x61\x80', b'\x61\x82\x01', b'\x61\x02\0'):
            with self.subTest(value=value), self.assertRaises(ProtocolError): ipass.tlvs(value)


def card_reply(payload):
    return bytes((0x61, len(payload))) + payload + b'\x90\0'


def trip_record(seconds, status, amount, after):
    return (b'\x01' + seconds.to_bytes(4, 'little') + bytes((status,))
            + amount.to_bytes(2, 'little', signed=True) + after.to_bytes(2, 'little', signed=True)
            + b'\x03\x2a' + b'\x12\xab\x00\x01')


class Card(unittest.TestCase):
    def test_query_selects_the_data_applet_of_the_ipass_instance(self):
        self.assertEqual(ipass.MDAC.hex().upper(), 'A0000006914D344D534D0100')
        self.assertEqual(ipass.CARD_QUERY[0].hex().upper(), '00A404000CA0000006914D344D534D010000')
        self.assertTrue(all(command.endswith(b'\0') for command in ipass.CARD_QUERY))

    def test_balance_and_trips(self):
        empty = b'\0' * 16
        balance = card_reply((-5).to_bytes(4, 'little', signed=True) + b'\0' * 28
                             + trip_record(1_700_000_300, 0x30, 200, 170))
        first = card_reply(trip_record(1_700_000_100, 0x00, 0, 7)
                           + trip_record(1_700_000_200, 0x11, 37, -30) + empty)
        second = card_reply(trip_record(1_700_000_400, 0x20, 25, 145)
                            + trip_record(1_700_000_300, 0x30, 200, 170) + trip_record(1_700_000_500, 0x77, 1, 144))
        result = ipass.card([b'\x6f\x00\x90\0', balance, first, second])
        self.assertEqual(result['balance'], -5)
        self.assertEqual([t['time'] for t in result['trips']],
                         [1_700_000_500, 1_700_000_400, 1_700_000_300, 1_700_000_200, 1_700_000_100])
        self.assertEqual(result['trips'][0]['kind'], 'other')
        self.assertEqual(result['trips'][3], {'time': 1_700_000_200, 'kind': 'exit', 'amount': 37, 'balance': -30, 'system': 3, 'station': '12AB', 'automatic_place': None})

    def test_refusals_and_bad_frames(self):
        good = card_reply(b'\0' * 48)
        with self.assertRaises(ProtocolError): ipass.card([b'\x6a\x82', good, good, good])
        with self.assertRaises(ProtocolError): ipass.card([b'\x90\0', b'\x6d\x00', good, good])
        with self.assertRaises(ProtocolError): ipass.card([b'\x90\0', good[:-3] + b'\x90\0', good, good])
        with self.assertRaises(ProtocolError): ipass.card([b'\x90\0', good, good])


class Modes(unittest.TestCase):
    def setUp(self):
        self.service = service.Service.__new__(service.Service)
        self.service.settings = service.DEFAULTS.copy()
        self.service.state = {'mode': 'ipass', 'busy': True}
        self.service.lock = threading.Lock()
        self.service.worker = Mock()
        self.service.worker.is_alive.return_value = True
        self.service.emulation = True
        self.service.mode_pending = False
        self.service.card_request = None
        self.service.closing = False
        self.service.seen_tags = set()
        self.service.stop = threading.Event()
        self.service.save = Mock()
        self.service.start_desired_mode = Mock()

    def test_switch_waits_for_ipass_cleanup(self):
        s = self.service
        worker = s.worker
        s.request_mode('reader')
        self.assertTrue(s.stop.is_set())
        self.assertTrue(s.mode_pending)
        self.assertEqual(service.desired_mode(s.settings), 'reader')
        s.start_desired_mode.assert_not_called()
        s.completed(worker, True)
        s.start_desired_mode.assert_called_once()
        self.assertFalse(s.emulation)

    def test_cleanup_failure_blocks_next_mode(self):
        s = self.service
        s.request_mode('emulation')
        s.completed(s.worker, False)
        s.start_desired_mode.assert_not_called()

    def test_cleanup_keeps_last_rf_counters_for_diagnosis(self):
        s = self.service
        s.state.update(ipass_field_on=12, ipass_ese_actions=7)
        s.ipass_changed('stopping', 'Restoring eSE activation and NFC settings', {})
        self.assertEqual(s.state['ipass_field_on'], 12)
        self.assertEqual(s.state['ipass_ese_actions'], 7)

    def test_ese_operations_cannot_be_interrupted_by_mode_change(self):
        s = self.service
        s.emulation = False
        with self.assertRaises(ValueError): s.request_mode('ipass')
        s.save.assert_not_called()

    def test_card_read_pauses_ipass_then_restarts_it(self):
        s = self.service
        s.settings['ipass_enabled'] = True
        s.start_card_read = Mock()
        invocation = object()
        s.read_ipass_card(invocation)
        self.assertTrue(s.stop.is_set())
        self.assertTrue(s.mode_pending)
        s.start_card_read.assert_not_called()
        # Another request while pausing is refused.
        with self.assertRaises(ValueError): s.read_ipass_card(object())
        s.completed(s.worker, True)
        s.start_card_read.assert_called_once()
        s.start_desired_mode.assert_not_called()
        reader = s.worker = Mock()
        reply = Mock()
        with patch.object(service.GLib, 'Variant', Mock(), create=True):
            s.card_read(reader, reply, '{"balance": 1, "trips": []}', None)
        reply.return_value.assert_called_once()
        s.start_desired_mode.assert_called_once()
        self.assertIsNone(s.card_request)

    def test_card_read_refused_during_other_work(self):
        s = self.service
        s.emulation = False
        with self.assertRaises(ValueError): s.read_ipass_card(object())
        s.emulation = True
        s.settings['emulation_enabled'] = True   # text tag, not IPASS
        with self.assertRaises(ValueError): s.read_ipass_card(object())

    def test_old_settings_migrate_without_enabling_ipass(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            old = {k: v for k, v in service.DEFAULTS.items() if k != 'ipass_enabled'}
            old['emulation_enabled'] = True
            path.write_text(json.dumps(old))
            with patch.object(service, 'SETTINGS', path):
                value = service.load_settings()
                self.assertFalse(value['ipass_enabled'])
                self.assertTrue(value['emulation_enabled'])
                value['ipass_enabled'] = True
                path.write_text(json.dumps(value))
                with self.assertRaises(ValueError): service.load_settings()


if __name__ == '__main__':
    unittest.main()
