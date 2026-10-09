#!/usr/bin/python3
"""Replay SN220 HCI exchanges, including credit ordering and reader restoration."""
from collections import deque
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import controller
import ese


def nci(header, opcode, data=b''):
    return bytes((header, opcode, len(data))) + data


def hci(pipe, header, data=b''):
    return nci(1, 0, bytes((pipe | 128, header)) + data)


def credit():
    return nci(0x60, 6, b'\x01\x01\x01')


class Transport:
    def __init__(self, packets=()):
        self.packets = deque(packets)
        self.writes = []

    def write(self, packet):
        self.writes.append(packet)

    def read(self, timeout):
        if not self.packets:
            raise TimeoutError('No scripted response')
        return self.packets.popleft()


class Firmware(Transport):
    """The already-open firmware-owned eSE pipe observed on Houji."""
    def write(self, packet):
        super().write(packet)
        head, opcode, _ = packet[:3]
        body = packet[3:]
        if head == 0x20 and opcode == 0:
            self.packets.extend((nci(0x40, 0, b'\0'), nci(0x60, 0, b'\x02\x00\x20')))
        elif head == 0x20 and opcode == 1:
            self.packets.append(nci(0x40, 1, bytes.fromhex('001a3e0600010604ffff01ff')))
        elif head == 0x22 and opcode == 0:
            self.packets.append(nci(0x42, 0, b'\0\x03'))
            for ee in (0x80, 0xc0, 0x10):
                self.packets.append(nci(0x62, 0, bytes((ee, 1, 0))))
        elif head == 0x22:
            self.packets.append(nci(0x42, opcode, b'\0'))
            if opcode == 1:
                self.packets.append(nci(0x61, 0x0a, b'\x01\0\x03\xc0\x80\x04'))
                self.packets.append(nci(0x62, 1, b'\0'))
        elif head == 0x20 and opcode == 3:
            self.packets.append(nci(0x40, 3, bytes.fromhex('0002a0230101a0220102')))
        elif head == 1:
            pipe, command = body[0] & 127, body[1]
            if pipe == 1:
                if command == 3:
                    data = b''
                elif command == 2 and body[2] == 3:
                    data = b'\x02\x80\x81\xc0'
                elif command == 2 and body[2] == 4:
                    data = b'\0\xc0'
                else:
                    raise AssertionError('Unexpected admin command')
                self.packets.extend((hci(1, 0x80, data), credit()))
            elif pipe == 0x19 and command == 2:
                self.packets.extend((hci(pipe, 0x80, b'\x80\0'), credit()))
            elif pipe == 0x19 and command == 0x50:
                # Incoming APDU before returned credit is legal.
                answer = b'\x6f\x00\x90\x00' if body[2:] == bytes.fromhex('00a4040008a000000151000000') else b'\x6a\x82'
                self.packets.extend((hci(pipe, 0x50, answer), credit()))
            else:
                raise AssertionError('Attempted to recreate/open/close a firmware-owned pipe')
        else:
            raise AssertionError('Unexpected NCI command')


class Protocol(unittest.TestCase):
    def test_existing_pipe_and_repeated_apdus(self):
        transport = Firmware()
        session = ese.Session(transport)
        session.open()
        self.assertEqual(session.maximum, 32768)
        self.assertEqual(session.exchange(bytes.fromhex('00a4040008a000000151000000'))[-2:], b'\x90\0')
        self.assertEqual(session.exchange(bytes.fromhex('00a4040005f000000001')), b'\x6a\x82')
        session.close()
        # No RF discovery, NFC configuration changes, pipe deletion, or power-off.
        self.assertFalse(any(p[:2] in (b'\x21\x03', b'\x20\x02') for p in transport.writes))
        self.assertIn(bytes.fromhex('220302c001'), transport.writes)
        self.assertEqual(transport.writes[-1], bytes.fromhex('20000100'))

    def test_hci_error_after_credit_wait_is_not_timeout(self):
        transport = Transport([hci(0x19, 0x87), credit()])
        link = ese.Link(transport)
        # Covers the actual failure seen when opening firmware-owned pipe 0x19.
        with self.assertRaisesRegex(ese.ProtocolError, 'status 07'):
            link.hci(0x19, 3)

    def test_nci_and_hci_fragmentation_are_not_confused(self):
        transport = Transport([credit(), credit()])
        link = ese.Link(transport)
        link.credits, link.payload_size = 1, 5
        link.send_hci(0x19, 0x50, b'abcdefghij')
        self.assertEqual(transport.writes, [
            bytes.fromhex('0100051950616263'),
            bytes.fromhex('0100051964656667'),
            bytes.fromhex('0100049968696a')])
        self.assertEqual(link.credits, 0)
        link._dispatch(nci(1, 0, b'\x19\x50abcd'))
        link._dispatch(nci(1, 0, b'\x99ef\x90\0'))
        self.assertEqual(link._wait('hci', 0x19, 0x50, link.clock() + 1), b'abcdef\x90\0')

    def test_connectivity_notifications_are_acknowledged(self):
        transport = Transport([
            hci(1, 0x12, bytes.fromhex('c041014116')),
            credit(), hci(0x16, 3), credit(), hci(0x19, 0x50, b'\x90\0')])
        link = ese.Link(transport)
        link.credits = 1
        self.assertEqual(link._wait('hci', 0x19, 0x50, link.clock() + 2), b'\x90\0')
        self.assertEqual(transport.writes, [hci(1, 0x80), hci(0x16, 0x80, b'\0')])

    def test_response_before_credit_and_interleaved_notification(self):
        transport = Transport([hci(0x19, 0x50, b'\x6a\x82'), nci(0x61, 0x0a, b'\0'), credit()])
        link = ese.Link(transport)
        link.credits = 1
        self.assertEqual(link.exchange(b'\0\xa4\0\0'), b'\x6a\x82')
        transport.packets.append(hci(0x19, 0x50, b'\x90\0'))
        self.assertEqual(link.exchange(b'\0\xa4\0\0'), b'\x90\0')

    def test_wtx_and_atr_are_not_apdu_replies(self):
        transport = Transport([hci(0x19, 0x51, b'\x01'), hci(0x19, 0x52, b'private ATR'),
                               hci(0x19, 0x50, b'\x90\0')])
        link = ese.Link(transport)
        link.credits = 1
        self.assertEqual(link.exchange(b'\0\xa4\0\0'), b'\x90\0')
        self.assertFalse(link.messages)

    def test_absolute_deadline_and_malformed_data(self):
        link = ese.Link(Transport(), clock=lambda: 10)
        with self.assertRaises(TimeoutError):
            link._wait('hci', 0x19, 0x50, 9)
        for packet in [b'', b'\x01\0\x02\x99', nci(0x11, 0, b'\x99\x50'),
                       nci(0x60, 6, b'\x02\x01\x01')]:
            with self.subTest(packet=packet), self.assertRaises(ese.ProtocolError):
                link._dispatch(packet)

    def test_response_size_bounded(self):
        link = ese.Link(Transport())
        with self.assertRaises(ese.ProtocolError):
            for _ in range(200):
                link._dispatch(nci(1, 0, b'\x19' + b'a' * 254))

    def test_validation_does_not_echo_inputs(self):
        for apdus in [[], [b'abc'], [b'a' * 32769], ['sensitive text'], [[0, 164, 0, 256]],
                      [[False, 164, 0, 0]], [b'\0\xa4\0\0'] * 17]:
            with self.assertRaises(ValueError) as caught:
                ese.validate_apdus(apdus)
            self.assertNotIn('sensitive', str(caught.exception))
        self.assertEqual(ese.validate_apdus([[0, 164, 0, 0]]), [b'\0\xa4\0\0'])

    def test_cleanup_continues_when_power_release_fails(self):
        session = ese.Session(Transport())
        session.started = session.power_requested = True
        session.whitelist = b'\x02'
        session.link.nci = Mock(side_effect=[OSError(), b'\0'])
        session.link.hci = Mock()
        with self.assertRaises(ese.ProtocolError):
            session.close()
        session.link.hci.assert_called_once_with(1, 1, b'\x03\x02')
        self.assertEqual(session.link.nci.call_args_list[-1].args, (0, 0, b'\0'))

    def test_batch_timeout_does_not_extend_with_each_apdu(self):
        session = ese.Session(Firmware())
        session.open()
        with patch.object(session.link, 'clock', return_value=session.deadline + 1):
            with self.assertRaises(TimeoutError):
                session.exchange(b'\0\xa4\0\0')
        session.close()

    def test_driver_and_supply_restored_after_exchange_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            driver = Path(directory)
            for failure in ('open', 'exchange', 'close', None):
                session = Mock()
                if failure:
                    getattr(session, failure).side_effect = OSError('synthetic failure')
                session.exchange.return_value = b'\x90\0'
                transport = Mock()
                with patch.object(controller, 'DRIVER', driver), \
                        patch.object(controller, 'device_name', return_value='1-0028'), \
                        patch.object(controller, 'reader_status', return_value={'powered': True}), \
                        patch.object(controller, 'reader') as reader, \
                        patch.object(controller, 'command') as command, \
                        patch.object(controller, 'Transport', return_value=transport), \
                        patch.object(controller, 'Session', return_value=session):
                    try:
                        result = controller._ese_exchange([b'\0\xa4\0\0'])
                    except (OSError, RuntimeError):
                        self.assertIsNotNone(failure)
                    else:
                        self.assertIsNone(failure)
                        self.assertEqual(result, [b'\x90\0'])
                    self.assertEqual((driver / 'bind').read_text(), '1-0028')
                    reader.assert_any_call(False)
                    reader.assert_any_call(True)
                    command.assert_any_call('modprobe', '-r', 'houji_nfc_power')
                    transport.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
