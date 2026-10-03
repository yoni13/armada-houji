#!/usr/bin/env python3
"""Build the modem device-tree overlay module against the kernel built by this checkout.

Needs only the kernel tree and dtc; the GPS userspace tools are built separately.
"""
import argparse
from pathlib import Path
import shutil
import subprocess

ASSETS = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kernel', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    kernel, module = args.kernel.resolve(), args.output.resolve()
    if not (kernel / 'Module.symvers').is_file():
        parser.error('Build the kernel first; its matching Module.symvers is required')
    module.mkdir(parents=True, exist_ok=True)
    for name in ('Makefile', 'houji-modem-overlay.c'):
        shutil.copy2(ASSETS / name, module / name)
    subprocess.run(['dtc', '-@', '-I', 'dts', '-O', 'dtb', '-o', str(module / 'modem.dtbo'),
                    str(ASSETS / 'modem.dtso')], check=True)
    blob = (module / 'modem.dtbo').read_bytes()
    (module / 'modem-overlay.h').write_text(
        'static const unsigned char modem_dtbo[] = {\n' +
        ','.join(f'0x{x:02x}' for x in blob) + '\n};\n')
    subprocess.run(['make', '-C', str(kernel), 'ARCH=arm64', 'CROSS_COMPILE=aarch64-linux-gnu-',
                    f'M={module}', 'modules'], check=True)


if __name__ == '__main__':
    main()
