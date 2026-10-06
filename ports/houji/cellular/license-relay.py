#!/usr/bin/python3
"""QTEELS 0x423: forward stock modem license checks to stock QTEE UID 119."""
import ctypes
import os
from pathlib import Path
import socket
import struct
import time
from qmi import AF_QIPCRTR, CTRL_PORT, decode_packet, packet, tlv


def request(values):
    if set(values) != {1, 2, 3, 4}:
        raise ValueError('Invalid request fields')
    if [len(values[x]) for x in (1, 2, 3)] != [4, 2, 1]:
        raise ValueError('Invalid fixed fields')
    secure = values[3][0]
    array = values[4]
    if secure > 1 or len(array) < 2 or len(array) > 1026:
        raise ValueError('Invalid request size')
    if struct.unpack_from('<H', array)[0] != len(array)-2:
        raise ValueError('Invalid array length')
    return values[2], secure, array[2:]


def indication(identifier, secure, status, data):
    if len(data) > 1024:
        raise ValueError('QTEE output too large')
    return (tlv(1, struct.pack('<I', status)) + tlv(2, identifier)
            + tlv(3, bytes([secure])) + tlv(4, struct.pack('<H', len(data)) + data))


class Qtee:
    def __init__(self):
        self.lib = ctypes.CDLL(str(Path(__file__).with_name('libhouji-qtee.so')))
        self.lib.houji_qtee_init.restype = ctypes.c_int
        self.lib.houji_qtee_ready.restype = ctypes.c_int
        self.lib.houji_qtee_check.argtypes = [ctypes.c_uint, ctypes.c_void_p, ctypes.c_size_t,
                                             ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
        self.lib.houji_qtee_check.restype = ctypes.c_int
        rc = self.lib.houji_qtee_init()
        if rc:
            raise RuntimeError('QTEE license manager open failed: %d' % rc)

    def ready(self):
        deadline = time.monotonic() + 30
        while True:
            rc = self.lib.houji_qtee_ready()
            if not rc:
                return
            if time.monotonic() >= deadline:
                raise RuntimeError('QTEE license storage unavailable: %d' % rc)
            time.sleep(.5)

    def check(self, secure, data):
        for attempt in range(5):
            output = ctypes.create_string_buffer(1024)
            size = ctypes.c_size_t(1024)
            rc = self.lib.houji_qtee_check(secure, data, len(data), output, ctypes.byref(size))
            if rc != -99:
                break
            time.sleep(.2 * (attempt+1))
        if size.value > 1024:
            raise RuntimeError('Invalid QTEE response length')
        return rc, output.raw[:size.value] if not rc else b''


def main():
    qtee = Qtee()
    qtee.ready()
    with socket.socket(AF_QIPCRTR, socket.SOCK_DGRAM) as sock:
        node, port = sock.getsockname()
        sock.sendto(struct.pack('<5I', 4, 0x423, 1, node, port), (node, CTRL_PORT))
        notify = os.environ.get('NOTIFY_SOCKET')
        if notify:
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as ns:
                ns.connect('\0'+notify[1:] if notify.startswith('@') else notify)
                ns.sendall(b'READY=1')
        print('QTEE license storage ready; QTEELS relay published', flush=True)
        while True:
            data, peer = sock.recvfrom(4096)
            if peer[0] != 0 or peer[1] == CTRL_PORT:
                continue
            try:
                kind, txn, msg, values = decode_packet(data)
                if kind != 0:
                    continue
                if msg != 0x20:
                    sock.sendto(packet(2, txn, msg, tlv(2, struct.pack('<HH', 1, 0x47))), peer)
                    continue
                identifier, secure, payload = request(values)
            except ValueError:
                # Malformed packets never reach the TEE.
                continue
            sock.sendto(packet(2, txn, msg, tlv(2, b'\0'*4) + tlv(0x10, b'\0'*4)), peer)
            rc, output = qtee.check(secure, payload)
            body = indication(identifier, secure, 6 if rc else 0, output)
            sock.sendto(packet(4, 0, 0x20, body), peer)
            print('License check: secure=%d result=%d' % (secure, rc), flush=True)


if __name__ == '__main__':
    main()
