#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Import a Wallet metadata response or cached .pb into the offline name table."""
import argparse
import json
import os
from pathlib import Path
import tempfile

from ipass_metadata import MAX_BYTES, METADATA, decode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('--format', choices=('response', 'cache'), default='response')
    parser.add_argument('--output', type=Path, default=METADATA)
    args = parser.parse_args()
    with args.input.open('rb') as source:
        document = decode(source.read(MAX_BYTES + 1), args.format)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=args.output.parent,
                                         delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(document, stream, ensure_ascii=False, separators=(',', ':'))
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(args.output)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
    print(f"Imported {len(document['entries'])} metadata entries")


if __name__ == '__main__':
    main()
