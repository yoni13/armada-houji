#!/usr/bin/env python3
"""Synthetic metadata: variable prefixes, location-only names and bad input."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import ipass
from ipass_metadata import Metadata, decode, load


def wire(number, data):
    assert len(data) < 128
    return bytes((number * 8 + 2, len(data))) + data


class Names(unittest.TestCase):
    def test_wallet_matching_rules(self):
        metadata = Metadata({'version': 1, 'entries': [
            ['31', '', '12', 'Rail', 'Broad station'],
            ['31', '', '1234', 'Rail', 'Specific station'],
            ['31', '', '1234', 'Rail', 'Duplicate must not replace first'],
            ['32', '01', '', '', 'Bus A'],
            ['32', '02', '', '', 'Bus B'],
            ['33', '', '', 'Partner shops', ''],
            ['34', '', '12', 'Default system', 'Default stop'],
            ['34', '09', '123456', 'Location system', 'Location stop'],
        ]})
        terminal = bytes.fromhex('12345678')
        self.assertEqual(metadata.lookup(0x31, 0xFF, terminal), 'Specific station')
        self.assertEqual(metadata.lookup(0x31, 0, bytes.fromhex('12FF0000')), 'Broad station')
        self.assertEqual(metadata.lookup(0x31, 0, bytes(4)), 'Rail')
        self.assertEqual(metadata.lookup(0x32, 1, terminal), 'Bus A')
        self.assertEqual(metadata.lookup(0x32, 2, terminal), 'Bus B')
        self.assertIsNone(metadata.lookup(0x32, 3, terminal))
        self.assertEqual(metadata.lookup(0x33, 7, terminal), 'Partner shops')
        self.assertEqual(metadata.lookup(0x34, 9, terminal), 'Location stop')
        self.assertEqual(metadata.lookup(0x34, 8, terminal), 'Default stop')
        # Once the location-specific group matches, Wallet falls back to that
        # group's operator rather than a terminal from the generic group.
        self.assertEqual(metadata.lookup(0x34, 9, bytes.fromhex('12FF0000')), 'Location system')
        self.assertIsNone(metadata.lookup(0xFF, 1, terminal))

    def test_protobuf_response_and_cache(self):
        entry = wire(1, wire(1, b'31') + wire(3, b'1234')) + wire(2, wire(1, b'Rail') + wire(2, '測試站'.encode()))
        response = wire(1, b'\x08\x00') + wire(2, entry)
        expected = {'version': 1, 'entries': [['31', '', '1234', 'Rail', '測試站']]}
        self.assertEqual(decode(response), expected)
        self.assertEqual(decode(wire(1, entry) + b'\x10\x01', 'cache'), expected)
        for bad in (response[:-1], b'\x12\xff', b'\0', b'\x0b', b'\xff' * 12, b''):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                decode(bad)
        with self.assertRaises(ValueError):
            decode(wire(2, wire(1, wire(1, b'GG'))))

    def test_optional_table_and_private_terminal(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'metadata.json'
            self.assertIsNone(load(path))
            path.write_text('broken')
            self.assertIsNone(load(path))
            path.write_text(json.dumps({'version': 1, 'entries': [['31', '', '123456', 'Rail', 'Station']]}))
            metadata = load(path)
            record = (b'\x01' + (1_700_000_000).to_bytes(4, 'little') + b'\x11'
                      + (25).to_bytes(2, 'little') + (50).to_bytes(2, 'little')
                      + bytes.fromhex('310912345678'))
            frame = lambda data: bytes((0x61, len(data))) + data + b'\x90\0'
            replies = [b'\x90\0', frame(bytes(48)), frame(record + bytes(32)), frame(bytes(48))]
            with patch.object(ipass, 'load_metadata', return_value=metadata):
                trip = ipass.card(replies)['trips'][0]
            self.assertEqual(trip['automatic_place'], 'Station')
            self.assertEqual(trip['station'], '1234')
            self.assertNotIn('12345678', json.dumps(trip))
            self.assertNotIn('terminal', trip)


if __name__ == '__main__':
    unittest.main()
