# SPDX-License-Identifier: GPL-2.0-or-later
"""Serialized reader and text-tag operations, without packet or payload logging."""
from collections import deque
import fcntl
import json
import os
from pathlib import Path
import subprocess
import time

from protocol import TextTag, validate
from transport import Transport
from ese import Session, validate_apdus

DRIVER = Path('/sys/bus/i2c/drivers/nxp-nci_i2c')
IFACE = 'org.neard.Adapter'


def command(*args, check=True):
    result = subprocess.run(args, capture_output=True, text=True, timeout=15)
    if check and result.returncode:
        # Never propagate raw D-Bus replies, tag contents, or command arguments.
        raise RuntimeError('NFC control operation failed; try turning NFC off and on.')
    return result


def bus(*args, check=True):
    return command('busctl', *args, check=check)


def adapter(start=False):
    if start:
        command('systemctl', 'start', 'neard')
    tree = bus('--auto-start=no', '--list', 'tree', 'org.neard', check=False)
    paths = [x.strip() for x in tree.stdout.splitlines()
             if x.strip().startswith('/org/neard/nfc') and x.strip().count('/') == 3]
    if len(paths) != 1:
        raise RuntimeError('NFC adapter unavailable. Restart NFC or reboot the phone.')
    return paths[0]


def properties(path):
    result = bus('--auto-start=no', '--json=short', 'call', 'org.neard', path,
                 'org.freedesktop.DBus.Properties', 'GetAll', 's', IFACE)
    value = json.loads(result.stdout)['data'][0]
    return {key: item['data'] for key, item in value.items()}


def reader_status(include_paths=False):
    try:
        path = adapter()
        props = properties(path)
        objects = bus('--auto-start=no', '--list', 'tree', 'org.neard').stdout.splitlines()
        tags = [x.strip() for x in objects if x.strip().startswith(path + '/tag')
                and x.strip().count('/') == 4]
        result = {'powered': bool(props.get('Powered')), 'polling': bool(props.get('Polling')),
                  'tags': len(tags)}
        if include_paths:
            result['tag_paths'] = tags
        return result
    except (RuntimeError, ValueError, KeyError, subprocess.TimeoutExpired):
        result = {'powered': False, 'polling': False, 'tags': 0}
        if include_paths:
            result['tag_paths'] = []
        return result


def reader(enabled, restart=False):
    path = adapter(start=True)
    props = properties(path)
    if props.get('Polling'):
        bus('call', 'org.neard', path, IFACE, 'StopPollLoop', check=False)
    if props.get('Powered'):
        bus('set-property', 'org.neard', path, IFACE, 'Powered', 'b', 'false')
    if restart:
        command('systemctl', 'restart', 'neard')
        path = adapter()
    if enabled:
        bus('set-property', 'org.neard', path, IFACE, 'Powered', 'b', 'true')
        bus('call', 'org.neard', path, IFACE, 'StartPollLoop', 's', 'Initiator')


def read_routes(transport):
    transport.command(1, 2)
    chunks = []
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        packet = transport.read(max(.01, deadline - time.monotonic()))
        if packet[:2] != b'\x61\x02':
            continue
        if len(packet) < 5:
            raise RuntimeError('Invalid controller routing response.')
        chunks.append(packet[3:])
        if not packet[3]:
            return chunks
        if len(chunks) > 16:
            break
    raise RuntimeError('Could not read controller routing.')


def device_name():
    devices = [p for p in Path('/sys/bus/i2c/devices').iterdir()
               if (p / 'of_node/compatible').is_file()
               and b'nxp,nxp-nci-i2c\0' in (p / 'of_node/compatible').read_bytes()]
    if len(devices) != 1:
        raise RuntimeError('Expected one Houji NFC controller.')
    return devices[0].name


def recover():
    """Emergency service cleanup; never restores an emulation payload."""
    if b'xiaomi,houji\0' not in Path('/sys/firmware/devicetree/base/compatible').read_bytes():
        return
    name = device_name()
    if not (Path('/sys/bus/i2c/devices') / name / 'driver').exists():
        (DRIVER / 'bind').write_text(name)
        # Opening through the normal driver resets the temporary NCI config.
        reader(True)
        reader(False)
    command('modprobe', '-r', 'houji_nfc_power', check=False)


def ese_exchange(apdus):
    """One wired-eSE session, keeping the NFC reader and shared eSIM power intact."""
    apdus = validate_apdus(apdus)
    if b'xiaomi,houji\0' not in Path('/sys/firmware/devicetree/base/compatible').read_bytes():
        raise RuntimeError('The wired eSE backend supports Xiaomi 14 (Houji).')
    lock = os.open('/run/armada-nfc/controller.lock', os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return _ese_exchange(apdus)
    finally:
        os.close(lock)


def _ese_exchange(apdus):
    name = device_name()
    previous_reader = reader_status()['powered']
    held = unbound = False
    transport = session = None
    cleanup_errors = []
    try:
        reader(False)
        command('systemctl', 'stop', 'neard')
        command('modprobe', 'houji_nfc_power')
        held = True
        (DRIVER / 'unbind').write_text(name)
        unbound = True
        transport = Transport(name.split('-')[0])
        session = Session(transport)
        session.open()
        return [session.exchange(apdu) for apdu in apdus]
    finally:
        # A protocol failure must not skip restoration of the kernel driver.
        def attempt(operation):
            try:
                operation()
            except Exception:
                cleanup_errors.append(True)

        if session:
            attempt(session.close)
        if transport:
            attempt(transport.close)
        if unbound:
            attempt(lambda: (DRIVER / 'bind').write_text(name))
        attempt(lambda: reader(previous_reader))
        if held:
            attempt(lambda: command('modprobe', '-r', 'houji_nfc_power'))
        if cleanup_errors:
            raise RuntimeError('eSE exchange ended, but NFC restoration could not be verified.')


def exchange(transport, tag, stop, changed):
    credits, max_payload, active = 0, 0, False
    fragments, pending = bytearray(), deque()
    while not stop.is_set():
        try:
            packet = transport.read(.25)
        except TimeoutError:
            continue
        head, opcode, _length = packet[:3]
        body = packet[3:]
        if head == 0x61 and opcode == 5:
            if len(body) < 6 or body[1:4] != b'\x02\x04\x80' or not body[4]:
                # RF interface, protocol, technology/mode, payload size and
                # credits are protocol settings, not card data.
                raise RuntimeError('Unsupported card-emulation activation: '
                                   + ' '.join(f'{b:02x}' for b in body[1:6]))
            max_payload, credits = body[4:6]
            active = True
            tag.reset()
            fragments.clear()
            pending.clear()
            changed('emulating', 'Reader connected', tag.reads)
        elif head == 0x61 and opcode == 6:
            active = False
            fragments.clear()
            pending.clear()
            changed('emulating', 'Ready to scan with another phone', tag.reads)
            if body and body[0] == 0:
                transport.command(1, 3, b'\x01\x80\x01')
        elif head == 0x60 and opcode == 6:
            if not body or len(body) != 1 + body[0] * 2:
                raise RuntimeError('Invalid controller credit notification.')
            for pos in range(1, len(body), 2):
                if body[pos] & 15 == 0 and credits != 255:
                    credits = min(254, credits + body[pos + 1])
        elif head >> 5 == 0 and head & 15 == 0 and active:
            fragments.extend(body)
            if len(fragments) > 4096:
                raise RuntimeError('Reader request exceeds supported limits.')
            if not head & 0x10:
                response = tag.respond(bytes(fragments))
                fragments.clear()
                for pos in range(0, len(response), max_payload):
                    chunk = response[pos:pos + max_payload]
                    more = pos + len(chunk) < len(response)
                    pending.append(bytes([0x10 if more else 0, 0, len(chunk)]) + chunk)
                if len(pending) > 512:
                    raise RuntimeError('Reader request exceeds supported limits.')
                changed('emulating', 'Text tag active', tag.reads)
        while pending and credits:
            transport.write(pending.popleft())
            if credits != 255:
                credits -= 1


def emulate(text, serial, stop, changed):
    text, uid = validate(text, serial)
    if b'xiaomi,houji\0' not in Path('/sys/firmware/devicetree/base/compatible').read_bytes():
        raise RuntimeError('This NFC backend supports Xiaomi 14 (Houji).')
    # A process-wide kernel lock prevents a second daemon taking the controller.
    lock = os.open('/run/armada-nfc/controller.lock', os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _emulate(text, uid, stop, changed)
    finally:
        os.close(lock)


def _emulate(text, uid, stop, changed):
    name = device_name()
    previous_reader = reader_status()['powered']
    held = unbound = configured = False
    transport = saved = None
    cleanup_failed = False
    try:
        reader(False)
        command('systemctl', 'stop', 'neard')
        command('modprobe', 'houji_nfc_power')
        held = True
        (DRIVER / 'unbind').write_text(name)
        unbound = True
        transport = Transport(name.split('-')[0])
        transport.initialize()
        saved = transport.command(0, 3, b'\x03\x31\x32\x33')[1:]
        if not saved or saved[0] != 3:
            raise RuntimeError('Could not save controller settings.')
        if any(chunk[1] for chunk in read_routes(transport)):
            raise RuntimeError('The controller already has card-emulation routes in use.')
        config = b'\x03\x31\x01\x00\x32\x01\x20\x33' + bytes([len(uid)]) + uid
        configured = True
        transport.command(0, 2, config)
        if uid and transport.command(0, 3, b'\x01\x33') != b'\x00\x01\x33\x04' + uid:
            raise RuntimeError('The controller did not accept the requested serial.')
        transport.command(1, 0, b'\x01\x04\x02\x02')
        transport.command(1, 1, b'\x00\x01\x01\x03\x00\x01\x04')
        transport.command(1, 3, b'\x01\x80\x01')
        changed('emulating', 'Ready to scan with another phone', 0)
        exchange(transport, TextTag(text), stop, changed)
    finally:
        changed('stopping', 'Restoring NFC reader', None)
        if transport:
            try:
                if configured:
                    transport.initialize(reset_config=True)
                    if any(chunk[1] for chunk in read_routes(transport)):
                        raise RuntimeError('Could not clear temporary routing.')
                if saved:
                    transport.command(0, 2, saved)
                    if transport.command(0, 3, b'\x03\x31\x32\x33')[1:] != saved:
                        raise RuntimeError('Controller settings did not restore.')
            except Exception:
                cleanup_failed = True
            finally:
                transport.close()
        try:
            if unbound:
                (DRIVER / 'bind').write_text(name)
            reader(previous_reader)
        finally:
            if held:
                command('modprobe', '-r', 'houji_nfc_power')
        if cleanup_failed:
            raise RuntimeError('NFC reader restored, but controller settings could not be verified. Restart NFC.')
