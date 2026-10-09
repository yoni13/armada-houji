# SPDX-License-Identifier: GPL-2.0-or-later
"""SN220 wired eSE APDUs over HCI on NCI's static logical connection 1.

Xiaomi's firmware maintains APDU pipe 0x19 to host 0xc0, gate 0x30. Its state
is read via CORE_GET_CONFIG A023, as in NXP's Android HCI implementation. Do
not recreate/open/close that firmware-owned pipe, clear the HCI network, reset
VEN, or mistake the APDU-capable NDEF NFCEE 0x10 for the secure element.

No packets, APDUs, response contents or chip identifiers are logged here.
"""
from collections import deque
import time

HOST = 0xc0
PIPE = 0x19
ADMIN = 1
MAX_APDU = 32768
MAX_BATCH = 16


def validate_apdus(apdus):
    if not isinstance(apdus, (tuple, list)) or not 1 <= len(apdus) <= MAX_BATCH:
        raise ValueError('Supply between 1 and 16 APDUs per session.')
    result = []
    for apdu in apdus:
        if not isinstance(apdu, (bytes, bytearray, list, tuple)):
            raise ValueError('An APDU must be a byte array.')
        if any(type(value) is not int or not 0 <= value <= 255 for value in apdu):
            raise ValueError('Invalid APDU byte.')
        if not 4 <= len(apdu) <= MAX_APDU:
            raise ValueError('An APDU must contain 4 to 32768 bytes.')
        result.append(bytes(apdu))
    return result


class ProtocolError(RuntimeError):
    pass


class Link:
    def __init__(self, transport, clock=time.monotonic):
        self.transport = transport
        self.clock = clock
        self.credits = 0
        self.payload_size = 255
        self.fragments = {}
        self.messages = deque()
        self.responses = deque()

    def _dispatch(self, packet):
        if len(packet) < 3 or len(packet) != packet[2] + 3:
            raise ProtocolError('Invalid NCI packet length.')
        head, opcode = packet[:2]
        data = packet[3:]
        if (head, opcode) == (0x60, 6):
            if not data or len(data) != 1 + 2 * data[0]:
                raise ProtocolError('Invalid NCI credit notification.')
            for index in range(1, len(data), 2):
                if data[index] == 1 and self.credits != 255:
                    self.credits = min(254, self.credits + data[index + 1])
            return
        if head >> 5 == 0:
            if head & 15 != 1:
                return
            # NCI segmentation is not used for HCI. HCP has its own chain bit.
            if head & 0x10 or len(data) < 2:
                raise ProtocolError('Invalid HCI data packet.')
            pipe = data[0] & 127
            fragment = self.fragments.setdefault(pipe, bytearray())
            fragment.extend(data[1:])
            if len(fragment) > MAX_APDU + 3 or len(self.fragments) > 16:
                raise ProtocolError('HCI response exceeds supported size.')
            if not data[0] & 128:
                return
            message = bytes(self.fragments.pop(pipe))
            kind, instruction = message[0] >> 6, message[0] & 63
            if kind == 0:
                # The eSE can announce/open its connectivity pipe during init.
                if pipe == ADMIN and instruction in (0x12, 0x13, 0x15):
                    self.responses.append((pipe, 0x80, b''))
                elif instruction == 3:
                    self.responses.append((pipe, 0x80, b'\0'))
                else:
                    self.responses.append((pipe, 0x87, b''))
                if len(self.responses) > 32:
                    raise ProtocolError('Too many pending HCI commands.')
            elif kind == 1 and instruction in (0x11, 0x12):
                # WTX/ATR can precede an APDU response. Neither extends our
                # absolute deadline, and ATR contents are not exposed or logged.
                return
            else:
                self.messages.append(('hci', pipe, message[0], message[1:]))
        else:
            self.messages.append(('nci', head, opcode, data))
        if len(self.messages) > 128:
            raise ProtocolError('Too many unmatched controller messages.')

    def _write_hcp(self, pipe, data, final):
        packet = bytes((pipe | (128 if final else 0),)) + data
        self.transport.write(bytes((1, 0, len(packet))) + packet)
        if self.credits != 255:
            self.credits -= 1

    def _pump(self, deadline):
        remaining = deadline - self.clock()
        if remaining <= 0:
            raise TimeoutError('eSE response timed out.')
        while self.responses and self.credits:
            pipe, header, data = self.responses.popleft()
            self._write_hcp(pipe, bytes((header,)) + data, True)
        self._dispatch(self.transport.read(remaining))

    def _wait(self, domain, first, second, deadline):
        while True:
            # Responses can arrive while send_hci() waits for a credit. Always
            # check queued messages before waiting for another interrupt.
            for message in self.messages:
                if message[:3] == (domain, first, second):
                    self.messages.remove(message)
                    return message[3]
                if (domain == 'hci' and message[:2] == (domain, first)
                        and message[2] >> 6 == 2):
                    self.messages.remove(message)
                    raise ProtocolError(f'HCI pipe {first:02x} rejected the request '
                                        f'(status {message[2] & 63:02x}).')
            self._pump(deadline)

    def nci(self, group, opcode, data=b'', timeout=5):
        self.transport.write(bytes((0x20 | group, opcode, len(data))) + data)
        reply = self._wait('nci', 0x40 | group, opcode, self.clock() + timeout)
        if not reply or reply[0]:
            raise ProtocolError(f'NCI command {group:02x}/{opcode:02x} failed '
                                f'(status {reply[0] if reply else -1}).')
        return reply

    def notification(self, group, opcode, timeout=5):
        return self._wait('nci', 0x60 | group, opcode, self.clock() + timeout)

    def send_hci(self, pipe, header, data=b'', deadline=None):
        deadline = self.clock() + 5 if deadline is None else deadline
        message = bytes((header,)) + data
        width = self.payload_size - 1
        for offset in range(0, len(message), width):
            while not self.credits:
                self._pump(deadline)
            if self.clock() >= deadline:
                raise TimeoutError('eSE send timed out.')
            fragment = message[offset:offset + width]
            self._write_hcp(pipe, fragment, offset + len(fragment) == len(message))

    def hci(self, pipe, command, data=b''):
        deadline = self.clock() + 5
        self.send_hci(pipe, command, data, deadline)
        return self._wait('hci', pipe, 0x80, deadline)

    def exchange(self, apdu, deadline=None):
        deadline = self.clock() + 30 if deadline is None else deadline
        self.send_hci(PIPE, 0x50, apdu, deadline)
        result = self._wait('hci', PIPE, 0x50, deadline)
        if len(result) < 2:
            raise ProtocolError('eSE response has no status word.')
        return result


class Session:
    def __init__(self, transport):
        self.link = Link(transport)
        self.whitelist = None
        self.power_requested = False
        self.started = False
        self.deadline = None

    def open(self):
        link = self.link
        self.started = True
        link.nci(0, 0, b'\0')  # Keep configuration; never toggle shared VEN.
        reset = link.notification(0, 0)
        if len(reset) < 3 or reset[2] != 0x20:
            raise ProtocolError('The eSE transport requires NCI 2.0.')
        init = link.nci(0, 1, b'\0\0')
        if len(init) < 11 or init[9] < 2 or not init[10]:
            raise ProtocolError('No static HCI connection is available.')
        link.payload_size, link.credits = init[9:11]
        discovered = link.nci(2, 0)
        if len(discovered) != 2 or discovered[1] > 16:
            raise ProtocolError('Invalid NFCEE discovery response.')
        ese = False
        for _ in range(discovered[1]):
            info = link.notification(2, 0)
            ese |= len(info) >= 3 and info[0] == HOST
        if not ese:
            raise ProtocolError('The embedded secure element was not discovered.')
        link.hci(ADMIN, 3)
        whitelist = link.hci(ADMIN, 2, b'\x03')
        if HOST not in whitelist:
            self.whitelist = whitelist
            link.hci(ADMIN, 1, b'\x03' + whitelist + bytes((HOST,)))
        self.power_requested = True
        link.nci(2, 3, bytes((HOST, 3)))  # Power and link on during the session.
        link.nci(2, 1, bytes((HOST, 1)))
        if link.notification(2, 1, timeout=10) != b'\0':
            raise ProtocolError('The embedded secure element did not enable.')
        if HOST not in link.hci(ADMIN, 2, b'\x04'):
            raise ProtocolError('The eSE did not join the HCI network.')
        state = link.nci(0, 3, bytes.fromhex('02a023a022'))
        if (len(state) != 10 or state[:5] != bytes.fromhex('0002a02301')
                or state[6:9] != bytes.fromhex('a02201') or state[5] not in (1, 2)):
            raise ProtocolError('The firmware has no open wired-eSE APDU pipe.')
        maximum = link.hci(PIPE, 2, b'\x01')
        if len(maximum) != 2 or int.from_bytes(maximum, 'big') < 4:
            raise ProtocolError('Invalid eSE maximum APDU size.')
        self.maximum = min(MAX_APDU, int.from_bytes(maximum, 'big'))
        # Bound the APDU batch independently of controller setup. Individual
        # APDUs still have a 30-second deadline within this budget.
        self.deadline = link.clock() + 45
        return self

    def exchange(self, apdu):
        if len(apdu) > self.maximum:
            raise ValueError('APDU exceeds the eSE maximum size.')
        return self.link.exchange(apdu, min(self.deadline, self.link.clock() + 30))

    def close(self):
        steps = []
        if self.power_requested:
            # Release forced link activity, retaining power for the shared eUICC.
            steps.append(lambda: self.link.nci(2, 3, bytes((HOST, 1))))
        if self.whitelist is not None:
            steps.append(lambda: self.link.hci(ADMIN, 1, b'\x03' + self.whitelist))
        if self.started:
            steps.append(lambda: self.link.nci(0, 0, b'\0'))
        failed = False
        for step in steps:
            try:
                step()
            except (RuntimeError, OSError):
                failed = True
        if failed:
            raise ProtocolError('eSE session cleanup could not be verified.')
