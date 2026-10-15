# SPDX-License-Identifier: GPL-2.0-or-later
"""Offline iPASS names from Wallet's transaction metadata, without account data.

Wallet 26.37: bmht response field 2 / bmst cache field 1 contains bmim rows.
Each row is {1: bmil key, 2: bmij names}; key fields are system, location,
terminal prefix, and name fields are operator, terminal, transit enum.
The matching below follows acry/acse (including location-only bus entries).
"""
import json
from pathlib import Path
import re

METADATA = Path('/var/lib/armada-nfc/ipass-transaction-metadata.json')
MAX_BYTES = 4 * 1024 * 1024
MAX_ROWS = 20000


def fields(data):
    """Bounded protobuf wire reader; unknown fixed-width fields can be skipped."""
    pos = 0

    def take(size):
        nonlocal pos
        if size < 0 or size > len(data) - pos:
            raise ValueError('Truncated metadata protobuf')
        value = data[pos:pos + size]
        pos += size
        return value

    def varint():
        value = 0
        for shift in range(0, 70, 7):
            byte = take(1)[0]
            if shift == 63 and byte > 1:
                break
            value |= (byte & 127) << shift
            if byte < 128:
                return value
        raise ValueError('Invalid metadata varint')

    while pos < len(data):
        key = varint()
        number, wire = key >> 3, key & 7
        if not number:
            raise ValueError('Invalid metadata field')
        if wire == 0:
            value = varint()
        elif wire == 2:
            value = take(varint())
        elif wire in (1, 5):
            value = take(8 if wire == 1 else 4)
        else:
            raise ValueError('Unsupported metadata wire type')
        yield number, value


def decode(data, source='response'):
    """Import only lookup keys/names, not the response envelope or cache owner."""
    if source not in ('response', 'cache') or len(data) > MAX_BYTES:
        raise ValueError('Invalid metadata input')
    rows = []
    for number, value in fields(data):
        if number != (2 if source == 'response' else 1):
            continue
        if not isinstance(value, bytes):
            raise ValueError('Invalid metadata entry')
        entry = dict(fields(value))
        key, names = dict(fields(entry.get(1, b''))), dict(fields(entry.get(2, b'')))
        row = [key.get(n, b'') for n in (1, 2, 3)] + [names.get(n, b'') for n in (1, 2)]
        if not all(isinstance(v, bytes) for v in row):
            raise ValueError('Invalid metadata strings')
        rows.append([v.decode('utf-8') for v in row])
        if len(rows) > MAX_ROWS:
            raise ValueError('Too many metadata entries')
    result = {'version': 1, 'entries': rows}
    Metadata(result)  # Validate before writing anything.
    return result


class Metadata:
    def __init__(self, document):
        if not isinstance(document, dict) or document.get('version') != 1:
            raise ValueError('Unsupported metadata format')
        rows = document.get('entries')
        if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_ROWS:
            raise ValueError('Invalid metadata entries')
        self.systems, self.terminals = {}, {}
        for row in rows:
            if not isinstance(row, list) or len(row) != 5 or not all(isinstance(v, str) for v in row):
                raise ValueError('Invalid metadata row')
            system, location, prefix, operator, terminal = row
            if (not re.fullmatch(r'[0-9A-Fa-f]{2}', system)
                    or not re.fullmatch(r'(?:[0-9A-Fa-f]{2})?', location)
                    or not re.fullmatch(r'(?:[0-9A-Fa-f]{2}){0,4}', prefix)
                    or any(len(name) > 256 or any(ord(c) < 32 for c in name)
                           for name in (operator, terminal))):
                raise ValueError('Invalid metadata key or name')
            system, location, prefix = system.upper(), location.upper(), prefix.upper()
            # Location+prefix selects a system-specific subgroup. Location-only
            # entries (e.g. bus companies) share the system index but resolve
            # through the location with a zero-byte terminal prefix.
            group = (system, location if location and prefix else '')
            info = self.systems.setdefault(group, {
                'operator': operator, 'use_location': bool(location), 'lengths': set()})
            if info['use_location'] == bool(location):
                info['lengths'].add(len(prefix))
            # Wallet retains the first name when the server sends duplicate keys.
            self.terminals.setdefault((system, location, prefix), terminal)

    def lookup(self, system, location, terminal):
        system, location, terminal = f'{system:02X}', f'{location:02X}', terminal.hex().upper()
        info = self.systems.get((system, location)) or self.systems.get((system, ''))
        if info is None:
            return None
        location_key = location if info['use_location'] else ''
        for length in sorted(info['lengths'], reverse=True):
            if length > len(terminal):
                continue
            key = (system, location_key, terminal[:length])
            if key in self.terminals:
                # The server's terminalName is already a full label (e.g.
                # 台鐵新竹站); concatenating operator would repeat 台鐵.
                return self.terminals[key] or info['operator'] or None
        return info['operator'] or None


def load(path=METADATA):
    try:
        with path.open('rb') as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            return None
        return Metadata(json.loads(raw))
    except (OSError, ValueError, TypeError):
        # Balance/history still work without a name table.
        return None
