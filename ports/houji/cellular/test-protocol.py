#!/usr/bin/python3
"""Malformed traffic must never reach QTEE; SIM parsing must select the right AID."""
import importlib.util
from pathlib import Path
import struct
import unittest
import qmi
import sim

spec = importlib.util.spec_from_file_location('relay', Path(__file__).with_name('license-relay.py'))
relay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relay)


class Protocol(unittest.TestCase):
    def test_stock_qteels_shape(self):
        # Fixed fields + variable array use the stock service's exact TLV sizes.
        body = bytes.fromhex('010400000000000202003412030100010405000300aabbcc')
        kind, txn, msg, fields = qmi.decode_packet(qmi.packet(0, 7, 32, body))
        self.assertEqual((kind, txn, msg), (0, 7, 32))
        self.assertEqual(relay.request(fields), (b'\x34\x12', 1, b'\xaa\xbb\xcc'))
        response = qmi.decode_tlvs(relay.indication(b'\x34\x12', 1, 0, b'\xdd'))
        self.assertEqual(response, {1: b'\0'*4, 2: b'\x34\x12', 3: b'\x01', 4: b'\x01\0\xdd'})

    def test_framing_rejects_truncation_duplicates_and_trailing_bytes(self):
        valid = qmi.packet(0, 1, 32, qmi.tlv(1, b'abcd'))
        for packet in (valid[:6], valid[:-1], valid+b'x', valid[:5]+b'\xff\xff'+valid[7:]):
            with self.subTest(packet=packet), self.assertRaises(ValueError):
                qmi.decode_packet(packet)
        for body in (b'\x01', b'\x01\x05\0abc', qmi.tlv(1,b'')*2):
            with self.assertRaises(ValueError):
                qmi.decode_tlvs(body)

    def test_invalid_license_input(self):
        good = {1: b'\0'*4, 2: b'\0'*2, 3: b'\x01', 4: b'\x01\0x'}
        for key, value in ((1, b''), (2, b'x'), (3, b'\x02'), (4, b'\x02\0x'),
                           (4, b'\x01\x04'+b'x'*1025)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                relay.request(dict(good, **{}) | {key: value})
        with self.assertRaises(ValueError):
            relay.request(good | {17: b'unknown'})

    def test_sim_switch_is_confined_to_one_nv_path(self):
        for enabled, operation in ((None, 4), (False, 5), (True, 5)):
            payload = sim.efs_payload(enabled)
            self.assertEqual(len(payload), 544)
            self.assertEqual(payload[:2], bytes([operation, 1]))
            self.assertEqual(payload[5:5+payload[4]], sim.ESIM_PATH)
            if enabled is not None:
                self.assertEqual(struct.unpack_from('<3I', payload, 0x6c), (4,4,0))
                self.assertEqual(struct.unpack_from('<I', payload, 0x78)[0], int(enabled))

    def test_aid_does_not_leak_from_another_slot_or_application(self):
        text = '''Slot [1]:
  Application [1]:
    Application type:  'sim (1)'
    Application ID:
      A0:00:01
Slot [2]:
  Application [1]:
    Application type:  'usim (2)'
    Application ID:
      A0:00:00:00:87:10:02
  Application [2]:
    Application type:  'isim (5)'
    Application ID:
      A0:00:00:00:87:10:04
'''
        self.assertEqual(sim.applications(text), {2:'A0:00:00:00:87:10:02'})
        self.assertEqual(sim.applications('Slot [1]:\n Card state: absent'), {})


if __name__ == '__main__':
    unittest.main()
