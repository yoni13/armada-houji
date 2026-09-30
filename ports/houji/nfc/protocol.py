# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded, read-only NFC Forum Type 4 text tag. No storage or packet logging."""
import re

MAX_TEXT_BYTES = 200


def validate(text, serial):
    if not isinstance(text, str) or not text.strip():
        raise ValueError('Enter some text for the tag.')
    if '\x00' in text or len(text.encode('utf-8')) > MAX_TEXT_BYTES:
        raise ValueError('Tag text must be at most 200 UTF-8 bytes and contain no NULs.')
    if not isinstance(serial, str) or len(serial) > 32:
        raise ValueError('Enter a four-byte serial, such as 12:34:56:78.')
    if not serial.strip():
        return text, b''
    serial = re.sub(r'[: -]', '', serial)
    if not re.fullmatch(r'[0-9a-fA-F]{8}', serial):
        raise ValueError('Enter exactly four hexadecimal bytes, such as 12:34:56:78.')
    return text, bytes.fromhex(serial)


class TextTag:
    def __init__(self, text):
        validate(text, '')
        payload = b'\x02en' + text.encode('utf-8')
        record = bytes([0xd1, 1, len(payload), 0x54]) + payload
        self.ndef = len(record).to_bytes(2, 'big') + record
        self.cc = bytes.fromhex('000f20003b00340406e10400ff00ff')
        self.reads = 0
        self.reset()

    def reset(self):
        self.application = False
        self.selected = None

    def respond(self, apdu):
        if len(apdu) < 4:
            return b'\x67\x00'
        cla, ins, p1, p2 = apdu[:4]
        if cla != 0:
            return b'\x6e\x00'
        if ins == 0xa4:
            if len(apdu) < 5:
                return b'\x67\x00'
            length = apdu[4]
            if len(apdu) not in (5 + length, 6 + length):
                return b'\x67\x00'
            body = apdu[5:5 + length]
            if p1 == 4 and p2 in (0, 0x0c) and body == bytes.fromhex('d2760000850101'):
                self.application = True
                self.selected = None
                return b'\x90\x00'
            if not self.application:
                return b'\x69\x86'
            if p1 == 0 and p2 in (0, 0x0c) and body in (b'\xe1\x03', b'\xe1\x04'):
                self.selected = self.cc if body == b'\xe1\x03' else self.ndef
                return b'\x90\x00'
            return b'\x6a\x82'
        if ins == 0xb0:
            if len(apdu) != 5:
                return b'\x67\x00'
            if self.selected is None:
                return b'\x69\x86'
            offset = (p1 << 8) | p2
            if offset > len(self.selected):
                return b'\x6b\x00'
            value = self.selected[offset:offset + (apdu[4] or 256)]
            if self.selected is self.ndef and offset + len(value) > 2:
                self.reads += 1
            return value + b'\x90\x00'
        return b'\x6d\x00'
