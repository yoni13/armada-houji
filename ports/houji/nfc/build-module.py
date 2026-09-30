#!/usr/bin/env python3
"""Build the NFC power-supply hold against this port's completed kernel."""
import argparse
from pathlib import Path
import shutil
import subprocess

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('kernel', type=Path)
p.add_argument('output', type=Path)
a = p.parse_args()
kernel, output = a.kernel.resolve(), a.output.resolve()
if not (kernel / 'Module.symvers').is_file():
    p.error('Build the kernel first; its matching Module.symvers is required')
output.mkdir(parents=True, exist_ok=True)
for name in ('Makefile', 'houji-nfc-power.c'):
    shutil.copy2(Path(__file__).resolve().parent / name, output / name)
subprocess.run(['make', '-C', str(kernel), 'ARCH=arm64',
                'CROSS_COMPILE=aarch64-linux-gnu-', f'M={output}', 'modules'], check=True)
