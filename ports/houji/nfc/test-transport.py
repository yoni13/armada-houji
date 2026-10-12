#!/usr/bin/python3
"""NCI header readiness: retry transient address NACKs, not ambiguous payload reads."""
import errno
import unittest
from unittest.mock import patch

import transport


class Clock:
    def __init__(self):
        self.now = 0.0

    def sleep(self, seconds):
        self.now += seconds


class Readiness(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.transport = transport.Transport.__new__(transport.Transport)
        self.transport.irq = 100
        self.transport.i2c = 101
        for name, value in [('monotonic', lambda: self.clock.now), ('sleep', self.clock.sleep)]:
            patcher = patch.object(transport.time, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_header_nack_then_valid_packet_and_next_packet(self):
        with patch.object(transport, 'get_value', return_value=True) as irq, \
                patch.object(transport.os, 'read', side_effect=[
                    OSError(errno.ENXIO, 'synthetic NACK'),
                    OSError(errno.ENXIO, 'synthetic NACK'),
                    b'\x61\x07\x01', b'\x01', b'\x60\x06\x03', b'\x01\x01\x01']) as read:
            self.assertEqual(self.transport.read(.25), b'\x61\x07\x01\x01')
            self.assertEqual(self.transport.read(.25), b'\x60\x06\x03\x01\x01\x01')
        self.assertEqual(read.call_count, 6)
        self.assertEqual(irq.call_count, 4)
        self.assertAlmostEqual(self.clock.now, .01)

    def test_irq_is_rechecked_after_nack(self):
        with patch.object(transport, 'get_value', side_effect=[True, False, False, True]), \
                patch.object(transport.os, 'read', side_effect=[
                    OSError(errno.ENXIO, 'synthetic NACK'), b'\x40\x00\x01', b'\0']) as read:
            self.assertEqual(self.transport.read(.25), b'\x40\x00\x01\0')
        self.assertEqual(read.call_count, 3)
        self.assertAlmostEqual(self.clock.now, .015)

    def test_persistent_nack_is_bounded_and_keeps_errno(self):
        with patch.object(transport, 'get_value', return_value=True), \
                patch.object(transport.os, 'read', side_effect=OSError(errno.ENXIO, 'synthetic NACK')) as read:
            with self.assertRaises(OSError) as caught:
                self.transport.read(3)
        self.assertEqual(caught.exception.errno, errno.ENXIO)
        self.assertLessEqual(read.call_count, 22)
        self.assertLess(self.clock.now, .11)

    def test_receive_deadline_still_applies(self):
        with patch.object(transport, 'get_value', return_value=True), \
                patch.object(transport.os, 'read', side_effect=OSError(errno.ENXIO, 'synthetic NACK')):
            with self.assertRaises(TimeoutError):
                self.transport.read(.01)
        self.assertAlmostEqual(self.clock.now, .01)

    def test_payload_nack_not_retried(self):
        with patch.object(transport, 'get_value', return_value=True), \
                patch.object(transport.os, 'read', side_effect=[
                    b'\x61\x07\x01', OSError(errno.ENXIO, 'payload NACK')]) as read:
            with self.assertRaises(OSError) as caught:
                self.transport.read(.25)
        self.assertEqual(caught.exception.errno, errno.ENXIO)
        self.assertEqual(read.call_count, 2)
        self.assertEqual(self.clock.now, 0)

    def test_other_errors_and_short_headers_are_not_retried(self):
        for result in [OSError(errno.EIO, 'I/O error'), b'\x61\x07']:
            with patch.object(transport, 'get_value', return_value=True), \
                    patch.object(transport.os, 'read', side_effect=[result]) as read:
                with self.assertRaises((OSError, RuntimeError)):
                    self.transport.read(.25)
            self.assertEqual(read.call_count, 1)

    def test_no_interrupt_waits_until_deadline_without_reading(self):
        with patch.object(transport, 'get_value', return_value=False), \
                patch.object(transport.os, 'read') as read:
            with self.assertRaises(TimeoutError):
                self.transport.read(.025)
            read.assert_not_called()


if __name__ == '__main__':
    unittest.main()
