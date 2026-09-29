#!/usr/bin/env python3
"""Exercise the actual early-boot binary before packaging it. No device access."""
import argparse
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('busybox', type=Path)
a = p.parse_args()
command = [str(a.busybox.resolve())]
if platform.machine() not in ('aarch64', 'arm64'):
    emulator = shutil.which('qemu-aarch64-static') or shutil.which('qemu-aarch64')
    if not emulator:
        p.error('Install QEMU arm64 user emulation (qemu-aarch64-static or qemu-aarch64)')
    command.insert(0, emulator)


def execute(*args):
    return subprocess.check_output([*command, *map(str, args)], text=True, timeout=20).strip()


required = {'sh', 'mount', 'mkdir', 'tr', 'grep', 'seq', 'sleep', 'cat', 'stat',
            'realpath', 'cp', 'ln', 'switch_root'}
missing = required - set(execute('--list').splitlines())
if missing:
    raise SystemExit('Early-boot BusyBox is missing: ' + ', '.join(sorted(missing)))
execute('sh', '-n', Path(__file__).with_name('init'))
if execute('sh', '-c', 'test -d / && printf "%s" shell-ready') != 'shell-ready':
    raise SystemExit('Early-boot shell builtins failed')
with tempfile.TemporaryDirectory(prefix='houji-busybox-') as directory:
    root = Path(directory)
    image = root / 'large-file'
    with image.open('wb') as stream:
        stream.truncate((8 << 30) + 1)
    if execute('stat', '-c', '%s', image) != str(image.stat().st_size):
        raise SystemExit('Early-boot stat cannot handle the root image size')
    link = root / 'home-link'
    link.symlink_to(root, target_is_directory=True)
    if execute('realpath', link) != str(root.resolve()):
        raise SystemExit('Early-boot home path resolution failed')
print('Early-boot BusyBox applets, shell, large files and path resolution passed')
