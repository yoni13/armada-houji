"""Strict QRTR/QMI framing shared by the modem and license helpers."""
import socket
import struct
import time

AF_QIPCRTR = 42
CTRL_PORT = 0xfffffffe


def tlv(kind, value):
    return struct.pack('<BH', kind, len(value)) + value


def decode_tlvs(data):
    result = {}
    while data:
        if len(data) < 3:
            raise ValueError('Truncated TLV header')
        kind, size = struct.unpack_from('<BH', data)
        if size > len(data) - 3 or kind in result:
            raise ValueError('Truncated or duplicate TLV')
        result[kind] = data[3:3+size]
        data = data[3+size:]
    return result


def packet(kind, transaction, message, body):
    return struct.pack('<BHHH', kind, transaction, message, len(body)) + body


def decode_packet(data):
    if len(data) < 7:
        raise ValueError('Truncated QMI header')
    kind, transaction, message, size = struct.unpack_from('<BHHH', data)
    if size != len(data) - 7:
        raise ValueError('Incorrect QMI length')
    return kind, transaction, message, decode_tlvs(data[7:])


class Client:
    def __init__(self, service, timeout=10, modem=True):
        self.sock = socket.socket(AF_QIPCRTR, socket.SOCK_DGRAM)
        self.timeout = timeout
        self.transaction = 0
        local_node = self.sock.getsockname()[0]
        wanted_node = 0 if modem else local_node
        self.sock.sendto(struct.pack('<5I', 10, service, 0, 0, 0), (local_node, CTRL_PORT))
        deadline = time.monotonic() + timeout
        try:
            while True:
                self.sock.settimeout(max(.01, deadline-time.monotonic()))
                data, source = self.sock.recvfrom(4096)
                if source[1] != CTRL_PORT or len(data) != 20:
                    continue
                cmd, sid, instance, node, port = struct.unpack('<5I', data)
                if cmd == 4 and sid == service and node == wanted_node:
                    self.peer = (node, port)
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError('Modem QMI service unavailable')
        except BaseException:
            self.sock.close()
            raise

    def call(self, message, body):
        self.transaction = self.transaction % 65535 + 1
        self.sock.sendto(packet(0, self.transaction, message, body), self.peer)
        deadline = time.monotonic() + self.timeout
        while True:
            self.sock.settimeout(max(.01, deadline-time.monotonic()))
            data, peer = self.sock.recvfrom(65536)
            if peer == self.peer:
                kind, txn, msg, values = decode_packet(data)
                if (kind, txn, msg) == (2, self.transaction, message):
                    return values
            if time.monotonic() >= deadline:
                raise TimeoutError('QMI response timed out')

    def close(self):
        self.sock.close()
