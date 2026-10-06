#!/usr/bin/python3
"""Install pinned cellular runtime RPMs into a pristine, unstarted arm64 root."""
import argparse
import concurrent.futures
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from buildlib import fetch, run, sha

LOCK = Path(__file__).with_name('runtime-sources.json')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--work', type=Path, required=True)
    p.add_argument('--root', type=Path)
    a = p.parse_args()
    pins = json.loads(LOCK.read_text())
    cache = a.work.resolve()/'rpms'
    if not a.root:
        def download(item):
            name, (source, version, release, digest) = item
            arch = 'noarch' if name.endswith('.noarch.rpm') else 'aarch64'
            url = f'https://kojipkgs.fedoraproject.org/packages/{source}/{version}/{release}/{arch}/{name}'
            return fetch(url, cache/name, digest)
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(download, pins.items()))
        return
    root = a.root.resolve()
    temporary = root/'tmp/houji-runtime-packages'
    temporary.mkdir(parents=True, exist_ok=True)
    emulator = []
    if platform.machine() != 'aarch64':
        shutil.copy2(shutil.which('qemu-aarch64-static'), temporary/'qemu')
        emulator = ['/tmp/houji-runtime-packages/qemu']
    rpm = ['chroot', str(root), *emulator, '/usr/bin/rpm']
    # Fedora's RPM configuration determines dependency and install semantics,
    # so the target executable is used even on an x86 host. No package scriptlets
    # or services run while assembling this root filesystem.
    try:
        install = []
        for name, (_, _, _, digest) in pins.items():
            path = cache/name
            if sha(path) != digest:
                raise ValueError('Runtime package checksum mismatch: '+name)
            shutil.copy2(path, temporary/name)
            inside = '/tmp/houji-runtime-packages/'+name
            package = subprocess.check_output([*rpm, '-qp', '--qf', '%{NAME}', inside], text=True).strip()
            if subprocess.run([*rpm, '-q', package], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
                install.append(inside)
        if install:
            run(*rpm, '-i', '--noscripts', '--notriggers', *install)
    finally:
        shutil.rmtree(temporary)
    print('Cellular runtime packages verified in pristine root')


if __name__ == '__main__':
    main()
