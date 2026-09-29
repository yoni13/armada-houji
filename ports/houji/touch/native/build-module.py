#!/usr/bin/env python3
"""Build the touch transport against the kernel built by this checkout."""
import argparse
from pathlib import Path
import shutil
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kernel', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    kernel, output = args.kernel.resolve(), args.output.resolve()
    if not (kernel / 'Module.symvers').is_file():
        parser.error('Build the kernel first; its matching Module.symvers is required')
    output.mkdir(parents=True, exist_ok=True)
    for name in ('houji-tcm-probe.c', 'Makefile'):
        shutil.copy2(Path(__file__).resolve().parent.parent / name, output / name)
    subprocess.run(['make', '-C', str(kernel), 'ARCH=arm64',
                    'CROSS_COMPILE=aarch64-linux-gnu-', f'M={output}',
                    'modules'], check=True)


if __name__ == '__main__':
    main()
