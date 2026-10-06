#!/usr/bin/python3
"""Prepare private modem storage. Never open stock NV partitions for writing."""
import os
from pathlib import Path
import shutil
import subprocess
import time

BASE = Path('/var/lib/houji-modem')
PARTITIONS = {'modemst1': 'modem_fs1', 'modemst2': 'modem_fs2',
              'fsg': 'modem_fsg', 'fsc': 'modem_fsc'}


def copy_partition(source, target):
    with source.open('rb', buffering=0) as src, target.open('xb', buffering=0) as dst:
        while True:
            hot = False
            for zone in Path('/sys/class/thermal').glob('thermal_zone*'):
                kind = (zone/'type').read_text().strip()
                limit = 41000 if 'batt' in kind else 75000
                if 'batt' in kind or kind.startswith(('cpu', 'soc', 'gpu')):
                    hot |= int((zone/'temp').read_text()) >= limit
            if hot:
                time.sleep(1)
                continue
            start = time.monotonic()
            block = src.read(1024*1024)
            if not block:
                break
            dst.write(block)
            time.sleep(max(0, len(block)/(15*1024*1024)-(time.monotonic()-start)))
        os.fsync(dst.fileno())


def main():
    if b'xiaomi,houji\0' not in Path('/sys/firmware/devicetree/base/compatible').read_bytes():
        raise RuntimeError('This service is for Houji only')
    os.umask(0o077)
    BASE.mkdir(mode=0o700, exist_ok=True)
    if not (BASE/'efs').exists():
        temp = BASE/'efs.new'
        if temp.exists():
            shutil.rmtree(temp)
        temp.mkdir(mode=0o700)
        for label, name in PARTITIONS.items():
            copy_partition(Path('/dev/disk/by-partlabel')/label, temp/name)
        temp.rename(BASE/'efs')
    for name in PARTITIONS.values():
        path = BASE/'efs'/name
        if not path.is_file() or path.is_symlink() or path.stat().st_size < 512:
            raise RuntimeError('Invalid private modem storage: '+name)
    secure = BASE/'tee'
    if not secure.exists():
        temp = BASE/'tee.new'
        if temp.exists():
            shutil.rmtree(temp)
        (temp/'persist').mkdir(parents=True, mode=0o700)
        shutil.copytree('/run/houji/persist/data', temp/'persist/data', symlinks=True)
        (temp/'data').mkdir(mode=0o700)
        temp.rename(secure)
    os.sync()
    mount = Path('/run/houji/modemfw')
    mount.mkdir(parents=True, exist_ok=True)
    if subprocess.run(['mountpoint', '-q', str(mount)]).returncode:
        subprocess.run(['mount', '-t', 'vfat', '-o', 'ro,nodev,nosuid,noexec',
                        '/dev/disk/by-partlabel/modemfirmware_b', str(mount)], check=True)
    if not (mount/'image/modem.mdt').is_file():
        raise RuntimeError('Stock modem firmware not found')


if __name__ == '__main__':
    main()
