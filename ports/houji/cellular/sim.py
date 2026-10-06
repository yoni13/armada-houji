#!/usr/bin/python3
"""Select one primary data SIM; slot 2 is shared by nano-SIM and eUICC."""
import argparse
import fcntl
from pathlib import Path
import re
import struct
import subprocess
import time
from qmi import Client, tlv

ESIM_PATH = b'/nv/item_files/modem/uim/uimdrv/esim_enable'
CONFIG = Path('/etc/armada/cellular-sim')


def efs_payload(enabled=None):
    payload = bytearray(544)
    payload[0] = 4 if enabled is None else 5
    payload[1] = 1
    if enabled is not None:
        struct.pack_into('<H', payload, 2, 255)
        struct.pack_into('<3I', payload, 0x6c, 4, 4, 0)
        struct.pack_into('<I', payload, 0x78, int(enabled))
    payload[4] = len(ESIM_PATH)
    payload[5:5+len(ESIM_PATH)] = ESIM_PATH
    return bytes(payload)


def esim_switch(enabled=None):
    client = Client(0xffe4)
    try:
        payload = efs_payload(enabled)
        result = client.call(2, tlv(1, struct.pack('<I', 2))
                             + tlv(2, struct.pack('<I', len(payload)))
                             + tlv(0x10, payload.ljust(550, b'\0')))
    finally:
        client.close()
    if result.get(1) != b'\0'*4 or len(result.get(0x10, b'')) < 24:
        raise RuntimeError('Xiaomi SIM-switch request failed')
    response = result[0x10]
    if struct.unpack_from('<i', response, 16)[0] != 0:
        raise RuntimeError('Xiaomi SIM-switch EFS operation failed')
    if enabled is None:
        if struct.unpack_from('<II', response, 4) != (4, 4):
            raise RuntimeError('Unexpected SIM-switch EFS size')
        value = struct.unpack_from('<I', response, 20)[0]
        if value not in (0, 1):
            raise RuntimeError('Unexpected SIM-switch value')
        return bool(value)


def power(slot, enabled):
    client = Client(11)
    try:
        body = tlv(1, bytes([slot]))
        if enabled:
            body += tlv(0x10, b'\x01')  # ignore_hot_swap_switch, as on stock
        result = client.call(0x31 if enabled else 0x30, body)
        if result.get(2) != b'\0'*4:
            raise RuntimeError('UICC power request failed')
    finally:
        client.close()


def qmi(*args):
    proc = subprocess.run(['qmicli', '-d', 'qrtr://0', *args],
                          capture_output=True, text=True, timeout=30)
    if proc.returncode:
        raise RuntimeError('QMI operation failed: '+args[0].split('=')[0])
    return proc.stdout


def applications(text):
    """Return USIM AIDs by physical slot, without logging subscriber IDs."""
    found = {}
    for slot, section in re.findall(r'Slot \[(\d+)\]:(.*?)(?=Slot \[|\Z)', text, re.S):
        for block in re.split(r'Application \[\d+\]:', section):
            if re.search(r"Application type:\s+'usim \(2\)'", block):
                match = re.search(r'Application ID:\s*\n\s*([0-9A-Fa-f:]+)', block)
                if match and re.fullmatch(r'(?:[0-9A-Fa-f]{2}:)*[0-9A-Fa-f]{2}', match[1]):
                    found[int(slot)] = match[1]
    return found


def select(mode):
    if mode == 'auto':
        cards = applications(qmi('--uim-get-card-status'))
        if 1 in cards:
            mode = 'physical1'
        elif 2 in cards and not esim_switch():
            mode = 'physical2'
        else:
            mode = 'esim'
    slot = 1 if mode == 'physical1' else 2
    if slot == 2:
        wanted = mode == 'esim'
        if wanted and not any(Path('/sys/firmware/devicetree/base').glob(
                'soc@0/**/nfc@28/nxp,shared-se-power')):
            raise RuntimeError('eSIM requires the shared-NFC-power kernel update')
        if esim_switch() != wanted:
            power(2, False)
            try:
                esim_switch(wanted)
                if esim_switch() != wanted:
                    raise RuntimeError('SIM-switch readback failed')
            finally:
                power(2, True)
    deadline = time.monotonic() + 15
    while True:
        cards = applications(qmi('--uim-get-card-status'))
        if slot in cards:
            break
        if time.monotonic() >= deadline:
            print('No enabled USIM application in selected slot; profile management is available.')
            return
        time.sleep(.5)
    qmi('--uim-change-provisioning-session=session-type=primary-gw-provisioning,'
        'activate=yes,slot=%d,aid=%s' % (slot, cards[slot]))
    print('Primary data subscription selected: '+mode)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode', choices=['auto', 'physical1', 'physical2', 'esim', 'refresh'])
    p.add_argument('--save', action='store_true')
    args = p.parse_args()
    Path('/run/houji').mkdir(exist_ok=True)
    with open('/run/houji/sim.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        mode = args.mode
        if mode == 'refresh':
            power(2, False)
            power(2, True)
            mode = CONFIG.read_text().strip() if CONFIG.exists() else 'auto'
        if mode not in ('auto', 'physical1', 'physical2', 'esim'):
            raise ValueError('Invalid saved SIM selection')
        select(mode)
        if args.save:
            CONFIG.parent.mkdir(parents=True, exist_ok=True)
            tmp = CONFIG.with_suffix('.tmp')
            tmp.write_text(mode+'\n')
            tmp.replace(CONFIG)


if __name__ == '__main__':
    main()
