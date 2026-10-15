# SPDX-License-Identifier: GPL-2.0-or-later
"""IPASS: route NFC-A to the iPASS applet on the SN220 embedded secure element.

The applet must already be provisioned in Android; this module only activates
it and sets the controller's off-host routing. Confirmed working by the owner.
No APDU/RF payloads, chip identifiers or transaction data are logged.
"""
import json
import os
from pathlib import Path

from ese import HOST, PIPE, ProtocolError
from ipass_metadata import load as load_metadata

AID = bytes.fromhex('A0000006914D344D56434D0100')
CRS = bytes.fromhex('A00000015143525300')
RECOVERY = Path('/var/lib/armada-nfc/ipass-restore.json')
# Also retain the host Type-A configuration across the routing-clear reset.
CONFIG_IDS = (0x30, 0x31, 0x32, 0x33, 0x80, 0x81, 0x85)
NOTICE = 'IPASS ready. Works while the phone is asleep or awake.'


def tlvs(data):
    result, pos = [], 0
    try:
        while pos < len(data):
            tag = data[pos]
            pos += 1
            if tag & 31 == 31:
                while True:
                    byte = data[pos]
                    pos += 1
                    tag = tag << 8 | byte
                    if not byte & 128:
                        break
                    if tag > 0xffffff:
                        raise ValueError
            size = data[pos]
            pos += 1
            if size & 128:
                count = size & 127
                if not 1 <= count <= 3 or pos + count > len(data):
                    raise ValueError
                size = int.from_bytes(data[pos:pos + count], 'big')
                pos += count
            if pos + size > len(data):
                raise ValueError
            result.append((tag, data[pos:pos + size]))
            pos += size
    except (IndexError, ValueError):
        raise ProtocolError('Invalid contactless registry data.') from None
    return result


def apdu(header, data):
    return bytes.fromhex(header) + bytes((len(data),)) + data + b'\0'


SELECT = apdu('00a40400', CRS)
FILTER = bytes((0x4f, len(AID))) + AID
QUERY = apdu('80f24000', FILTER + bytes.fromhex('5c034f9f70'))


def checked(link, command):
    reply = link.exchange(command)
    if reply[-2:] != b'\x90\0':
        raise ProtocolError('CRS command failed (status ' + reply[-2:].hex().upper() + ').')
    return reply[:-2]


def lifecycle(link):
    checked(link, SELECT)
    states = []
    for tag, template in tlvs(checked(link, QUERY)):
        if tag == 0x61:
            fields = dict(tlvs(template))
            if fields.get(0x4f) == AID and len(fields.get(0x9f70, b'')) == 2:
                states.append(fields[0x9f70])
    if len(states) != 1:
        raise ProtocolError('The IPASS candidate is not in the contactless registry.')
    return states[0]


def activate(link, value):
    checked(link, SELECT)
    checked(link, apdu('80f001' + f'{value:02x}', FILTER))
    if lifecycle(link)[1] != value:
        raise ProtocolError('CRS activation read-back mismatch.')


def read_config(link):
    result = link.nci(0, 3, bytes((len(CONFIG_IDS),)) + bytes(CONFIG_IDS))[1:]
    if not result or result[0] != len(CONFIG_IDS) or set(dict(tlvs(result[1:]))) != set(CONFIG_IDS):
        raise ProtocolError('NFC configuration read-back mismatch.')
    return result


def read_routes(link):
    link.nci(1, 2)
    routes = []
    for _ in range(16):
        body = link.notification(1, 2)
        if len(body) < 2:
            raise ProtocolError('Truncated routing notification.')
        entries = tlvs(body[2:])
        if len(entries) != body[1]:
            raise ProtocolError('Routing entry count mismatch.')
        routes.extend(bytes((tag, len(data))) + data for tag, data in entries)
        if not body[0]:
            return routes
    raise ProtocolError('Too many routing fragments.')


# Card data, read the way Google Wallet does (MdacRequest and its parser in
# Wallet 26.37). The data applet pairs with AID: "M4MSM" in place of "M4MVCM",
# same instance number. Each reply is [tag, length, data, SW1 SW2]; values are
# little-endian. The balance reply ends with the last top-up record, and each
# trip reply holds three 16-byte records.
MDAC = AID[:8] + b'SM' + AID[-2:]
CARD_QUERY = [bytes.fromhex('00a404000c') + MDAC + b'\0'] + [bytes.fromhex(command + '00') for command in (
    '80B0000017E70FD0020003D10100D40101D503020007B004D5020007',
    '80B0000017E70FD0020005D10100D40101D503040007B004D5020007',
    '80B0000017E70FD0020006D10100D40101D503050007B004D5020007')]
# Record status byte, inferred from a card's history where each balance-after
# follows from the previous one; Wallet itself does not label these. Byte 10 is
# the system ID, byte 11 the location, and bytes 12-15 the terminal ID. Wallet's
# metadata determines which of them to match. Resolve inside this service so
# the full terminal ID never leaves it. The legacy two-byte code remains for
# user-defined names; it is not a universal station identifier.
TRIP_KINDS = {0x00: 'entry', 0x11: 'exit', 0x20: 'payment', 0x30: 'top-up'}


def card_frame(reply):
    if reply[-2:] != b'\x90\0':
        raise ProtocolError('The iPASS card refused the read (status ' + reply[-2:].hex().upper() + ').')
    data = reply[:-2]
    if len(data) != 50 or data[1] != 48:
        raise ProtocolError('Unexpected iPASS card reply.')
    return data[2:]


def trip(data):
    seconds = int.from_bytes(data[1:5], 'little')
    if not seconds:
        return None
    return (seconds, TRIP_KINDS.get(data[5], 'other'),
            int.from_bytes(data[6:8], 'little', signed=True),
            int.from_bytes(data[8:10], 'little', signed=True), data[10], data[11], data[12:16])


def card(replies):
    """Balance and trips, newest first, from the replies to CARD_QUERY."""
    if len(replies) != len(CARD_QUERY):
        raise ProtocolError('Incomplete iPASS card reply.')
    if replies[0][-2:] != b'\x90\0':
        raise ProtocolError('No iPASS card data on the secure element.')
    balance = card_frame(replies[1])
    records = [trip(balance[32:48])]
    for reply in replies[2:]:
        data = card_frame(reply)
        records += [trip(data[start:start + 16]) for start in (0, 16, 32)]
    trips = sorted({record for record in records if record}, reverse=True)
    metadata = load_metadata()
    named = []
    for seconds, kind, amount, after, system, location, terminal in trips:
        named.append(dict(time=seconds, kind=kind, amount=amount, balance=after,
                          system=system, station=terminal[:2].hex().upper(),
                          automatic_place=metadata.lookup(system, location, terminal) if metadata else None))
    return {'balance': int.from_bytes(balance[:4], 'little', signed=True),
            'trips': named}


def routes():
    # Switched-on screen states only, excluding battery-off and switched-off.
    return [bytes((2, 2 + len(AID), HOST, 0x39)) + AID,
            bytes((1, 3, HOST, 0x39, 4)),     # ISO-DEP protocol
            bytes((0, 3, HOST, 0x39, 0))]     # NFC-A technology


def save_recovery(path, activation, config):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Never overwrite a pending recovery, including after a service crash.
    temp = path.with_suffix('.tmp')
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump(dict(version=1, aid=AID.hex(), activation=activation, config=config.hex()), stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temp, path)
        directory = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temp.unlink(missing_ok=True)


class Offhost:
    def __init__(self, link, recovery=RECOVERY):
        self.link = link
        self.recovery = recovery
        self.discovery = False
        self.counts = dict(field_on=0, ese_actions=0)

    def start(self):
        if self.recovery.exists():
            raise ProtocolError('IPASS recovery is pending; restart the NFC service.')
        original = lifecycle(self.link)
        if original[1] not in (0, 1):
            raise ProtocolError('The IPASS candidate cannot be activated.')
        if read_routes(self.link):
            raise ProtocolError('Existing NFC routes are in use.')
        config = read_config(self.link)
        save_recovery(self.recovery, original[1], config)
        activate(self.link, 1)
        table = routes()
        # An empty SET_ROUTING fails on SN220; reset/init clears on cleanup.
        self.link.nci(1, 1, bytes((0, len(table))) + b''.join(table))
        if sorted(read_routes(self.link)) != sorted(table):
            raise ProtocolError('IPASS routing read-back mismatch.')
        self.link.nci(0, 2, bytes.fromhex('03800101810101850101'))
        flags = dict(tlvs(read_config(self.link)[1:]))
        if any(flags[key] != b'\x01' for key in (0x80, 0x81, 0x85)):
            raise ProtocolError('IPASS RF configuration did not apply.')
        # The controller retains this substate through AP suspend; mask 0x39
        # covers all switched-on screen states. RF detection was tested in s2idle.
        self.link.nci(0, 9, b'\0')
        self.link.nci(2, 3, bytes((HOST, 1)))
        self.link.send_hci(PIPE, 0x61)  # End wired APDU transaction, hand off to RF.
        self.link.messages.clear()
        # Set before the command: even a lost response requires deactivation.
        self.discovery = True
        self.link.nci(1, 3, b'\x01\x80\x01')

    def listen(self, stop, changed):
        changed('ipass', NOTICE, dict(self.counts))
        while not stop.is_set():
            try:
                self.link._pump(self.link.clock() + .25)
            except TimeoutError:
                continue
            while self.link.messages:
                domain, head, opcode, body = self.link.messages.popleft()
                if domain != 'nci':
                    continue
                if (head, opcode) == (0x60, 0):
                    raise ProtocolError('NFC controller reset during IPASS listening.')
                if (head, opcode) == (0x61, 6) and body and body[0] == 0:
                    if not stop.is_set():
                        self.link.nci(1, 3, b'\x01\x80\x01')
                updated = False
                if (head, opcode) == (0x61, 7) and body and body[0]:
                    self.counts['field_on'] += 1
                    updated = True
                elif (head, opcode) == (0x61, 9) and body and body[0] == HOST:
                    self.counts['ese_actions'] += 1
                    updated = True
                if updated:
                    changed('ipass', NOTICE, dict(self.counts))

    def restore(self):
        if not self.recovery.exists():
            return
        saved = json.loads(self.recovery.read_text())
        if (saved.get('version') != 1 or saved.get('aid') != AID.hex()
                or type(saved.get('activation')) is not int or saved['activation'] not in (0, 1)):
            raise ProtocolError('Invalid IPASS recovery record.')
        config = bytes.fromhex(saved['config'])
        if not config or config[0] != len(CONFIG_IDS) or set(dict(tlvs(config[1:]))) != set(CONFIG_IDS):
            raise ProtocolError('Invalid IPASS recovery configuration.')
        if self.discovery:
            self.link.nci(1, 6, b'\0')
            self.discovery = False
        self.link.nci(2, 3, bytes((HOST, 3)))
        self.link.nci(2, 1, bytes((HOST, 1)))
        if self.link.notification(2, 1) != b'\0':
            raise ProtocolError('eSE recovery mode did not enable.')
        if lifecycle(self.link)[1] != saved['activation']:
            activate(self.link, saved['activation'])
        if lifecycle(self.link)[1] != saved['activation']:
            raise ProtocolError('Original eSE activation state did not restore.')
        self.link.nci(0, 0, b'\x01')
        self.link.notification(0, 0)
        self.link.nci(0, 1, b'\0\0')
        if read_routes(self.link):
            raise ProtocolError('IPASS routes did not clear.')
        self.link.nci(0, 2, config)
        if dict(tlvs(read_config(self.link)[1:])) != dict(tlvs(config[1:])):
            raise ProtocolError('Original NFC configuration did not restore.')
        self.recovery.unlink()
