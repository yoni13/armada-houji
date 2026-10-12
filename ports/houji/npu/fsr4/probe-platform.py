#!/usr/bin/env python3
"""Read-only prerequisite inventory; never reports account or modem identifiers."""
import argparse
import json
import os
from pathlib import Path
import platform


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, help='Directory containing the native Linux QNN libraries')
    args = parser.parse_args()
    remoteprocs = []
    for p in sorted(Path('/sys/class/remoteproc').glob('remoteproc*')):
        try:
            remoteprocs.append({'name': (p/'name').read_text().strip(),
                                'state': (p/'state').read_text().strip()})
        except OSError:
            pass
    devices = {}
    for path in ('/dev/fastrpc-cdsp', '/dev/dma_heap/system'):
        devices[path] = {'present': Path(path).exists(),
                         'read_write': os.access(path, os.R_OK | os.W_OK)}
    libraries = {}
    if args.runtime:
        for name in ('libcdsprpc.so', 'libQnnHtp.so', 'libQnnSystem.so', 'libQnnHtpV75Stub.so'):
            libraries[name] = (args.runtime/name).is_file()
    report = {'machine': platform.machine(), 'kernel': platform.release(),
              'remoteprocs': remoteprocs, 'devices': devices, 'libraries': libraries,
              'npu_inference_verified': False}
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
