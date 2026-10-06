# SPDX-License-Identifier: GPL-2.0-or-later
"""Houji SN220 NCI transport through the standard GPIO and I2C APIs."""
import ctypes as C
import errno
import fcntl
import os
from pathlib import Path
import struct
import time


class Attr(C.Structure):
    _fields_ = [('id', C.c_uint32), ('padding', C.c_uint32), ('value', C.c_uint64)]

class ConfigAttr(C.Structure):
    _fields_ = [('attr', Attr), ('mask', C.c_uint64)]

class Config(C.Structure):
    _fields_ = [('flags', C.c_uint64), ('num_attrs', C.c_uint32),
                ('padding', C.c_uint32 * 5), ('attrs', ConfigAttr * 10)]

class Request(C.Structure):
    _fields_ = [('offsets', C.c_uint32 * 64), ('consumer', C.c_char * 32),
                ('config', Config), ('num_lines', C.c_uint32),
                ('event_buffer_size', C.c_uint32), ('padding', C.c_uint32 * 5),
                ('fd', C.c_int32)]

class Values(C.Structure):
    _fields_ = [('bits', C.c_uint64), ('mask', C.c_uint64)]


assert C.sizeof(Request) == 592 and C.sizeof(Values) == 16
libc = C.CDLL(None, use_errno=True)


def gpio_ioctl(fd, nr, obj):
    code = 0xc0000000 | (C.sizeof(obj) << 16) | (0xb4 << 8) | nr
    if libc.ioctl(fd, code, C.byref(obj)) < 0:
        err = C.get_errno()
        raise OSError(err, os.strerror(err))


def request(chip, pin, flags, high=False):
    req = Request()
    req.offsets[0] = pin
    req.consumer = b'armada-nfc-manager'
    req.num_lines = 1
    req.config.flags = flags
    if high:
        req.config.num_attrs = 1
        req.config.attrs[0].attr.id = 2  # GPIO_V2_LINE_ATTR_ID_OUTPUT_VALUES
        req.config.attrs[0].attr.value = 1
        req.config.attrs[0].mask = 1
    gpio_ioctl(chip, 7, req)
    return req.fd


def set_value(fd, value):
    gpio_ioctl(fd, 15, Values(value, 1))


def get_value(fd):
    value = Values(0, 1)
    gpio_ioctl(fd, 14, value)
    return bool(value.bits & 1)


def tlmm_chip():
    for p in sorted(Path('/dev').glob('gpiochip*')):
        fd = os.open(p, os.O_RDONLY | os.O_CLOEXEC)
        info = bytearray(68)
        try:
            fcntl.ioctl(fd, 0x8044b401, info, True)
            label = bytes(info[32:64]).split(b'\0')[0]
            if b'pinctrl' in label and struct.unpack_from('I', info, 64)[0] > 75:
                return fd
        except BaseException:
            os.close(fd)
            raise
        os.close(fd)
    raise RuntimeError('Qualcomm TLMM GPIO controller not found')


class Transport:
    def __init__(self, bus):
        self.fds = []
        self.i2c = self.ven = None
        self.shared_se_power = any(Path('/sys/firmware/devicetree/base').glob(
            'soc@0/**/nfc@28/nxp,shared-se-power'))
        try:
            chip = tlmm_chip()
            self.fds.append(chip)
            # The CLKREQ edge request also establishes the SoC wake route.
            self.fds.append(request(chip, 35, (1 << 2) | (1 << 4)))
            self.irq = request(chip, 75, 1 << 2)
            self.fds.append(self.irq)
            self.ven = request(chip, 34, 1 << 3, high=self.shared_se_power)
            self.fds.append(self.ven)
            set_value(self.ven, 1 if self.shared_se_power else 0)
            time.sleep(.02)
            self.i2c = os.open('/dev/i2c-' + bus, os.O_RDWR | os.O_CLOEXEC)
            self.fds.append(self.i2c)
            fcntl.ioctl(self.i2c, 0x0703, 0x28)
            set_value(self.ven, 1)
            time.sleep(.02)
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.ven is not None:
            try:
                set_value(self.ven, 1 if self.shared_se_power else 0)
            except OSError:
                pass
        for fd in reversed(self.fds):
            os.close(fd)
        self.fds.clear()
        self.ven = self.i2c = None

    def read(self, timeout=3):
        deadline = time.monotonic() + timeout
        while not get_value(self.irq):
            if time.monotonic() >= deadline:
                raise TimeoutError('NCI receive timeout')
            time.sleep(.005)
        head = os.read(self.i2c, 3)
        if len(head) != 3:
            raise RuntimeError('Short NCI header')
        body = os.read(self.i2c, head[2]) if head[2] else b''
        if len(body) != head[2]:
            raise RuntimeError('Short NCI payload')
        return head + body

    def write(self, packet):
        for attempt in range(2):
            try:
                if os.write(self.i2c, packet) != len(packet):
                    raise OSError(errno.EIO, 'Short NCI write')
                return
            except OSError:
                if attempt:
                    raise
                time.sleep(.11)

    def command(self, group, opcode, data=b''):
        if len(data) > 255:
            raise ValueError('NCI command too large')
        self.write(bytes([0x20 | group, opcode, len(data)]) + data)
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            p = self.read(max(.01, deadline - time.monotonic()))
            if p[0] == (0x40 | group) and p[1] == opcode:
                if not p[3:] or p[3] != 0:
                    raise RuntimeError(f'NCI command {group:02x}/{opcode:02x} status {p[3] if len(p) > 3 else -1:02x}')
                return p[3:]
        raise TimeoutError('NCI command response timeout')

    def initialize(self, reset_config=False):
        self.command(0, 0, bytes([int(reset_config)]))
        self.command(0, 1, b'\0\0')  # NCI 2.x CORE_INIT.
