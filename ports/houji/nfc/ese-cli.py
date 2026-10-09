#!/usr/bin/python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Exchange APDUs with Houji's embedded secure element through the NFC service.

Commands come from stdin (hexadecimal, one per line), never argv. The default
output contains lengths and status words only; --output saves full responses
in a new owner-only file. --probe sends a read-only SELECT of the GlobalPlatform
issuer security domain and a SELECT of a deliberately absent test application.
"""
import argparse
import os
from pathlib import Path
import sys

from ese import MAX_APDU, MAX_BATCH, validate_apdus

SELECT_ISD = bytes.fromhex('00a4040008a000000151000000')
# Private-use AID, deliberately absent: tests transport of a negative response.
SELECT_ABSENT = bytes.fromhex('00a4040008f0484f554a49544553')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probe', action='store_true', help='send two read-only SELECTs')
    parser.add_argument('--output', type=Path, help='new private file for full hex responses')
    args = parser.parse_args()
    descriptor = None
    try:
        if args.probe:
            apdus = [SELECT_ISD, SELECT_ABSENT]
        else:
            lines = sys.stdin.read((MAX_APDU * 3 + 2) * MAX_BATCH + 1)
            if len(lines) > (MAX_APDU * 3 + 2) * MAX_BATCH:
                raise ValueError('APDU input exceeds the session limit.')
            try:
                apdus = [bytes.fromhex(line) for line in lines.splitlines() if line.strip()]
            except ValueError:
                raise ValueError('APDU input must be hexadecimal, one command per line.') from None
        apdus = validate_apdus(apdus)
        if args.output:
            descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
        from gi.repository import Gio, GLib
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        result = bus.call_sync('org.armada.Nfc', '/org/armada/Nfc', 'org.armada.Nfc1',
                              'TransceiveEse', GLib.Variant('(aay)', (apdus,)),
                              GLib.VariantType.new('(aay)'), Gio.DBusCallFlags.NONE,
                              150000, None)
        responses = [bytes(response) for response in result.unpack()[0]]
        if len(responses) != len(apdus) or any(len(response) < 2 for response in responses):
            raise ValueError('Invalid eSE service reply.')
        if descriptor is not None:
            with os.fdopen(descriptor, 'w') as stream:
                descriptor = None
                stream.write(''.join(response.hex().upper() + '\n' for response in responses))
        for index, response in enumerate(responses, 1):
            print(f'APDU {index}: {len(response)} bytes, SW={response[-2:].hex().upper()}')
        if args.probe and [r[-2:] for r in responses] != [b'\x90\0', b'\x6a\x82']:
            raise ValueError('eSE replied, but the probe returned unexpected status words.')
        return 0
    except (OSError, ValueError) as error:
        # File paths/errors can contain private data; fixed parser errors are safe.
        print(str(error) if isinstance(error, ValueError) else 'Could not access the service or output file.', file=sys.stderr)
        return 1
    except Exception:
        print('eSE exchange failed. Stop NFC tag emulation if it is active; use sudo over SSH. '
              'NFC Manager shows the service status.', file=sys.stderr)
        return 1
    finally:
        if descriptor is not None:
            os.close(descriptor)


if __name__ == '__main__':
    sys.exit(main())
