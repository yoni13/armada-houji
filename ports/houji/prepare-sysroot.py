#!/usr/bin/env python3
"""Extract pinned development RPMs, then libraries from an unstarted Armada image."""
import argparse
import concurrent.futures
import json
from pathlib import Path
import re
import shutil
import subprocess
from buildlib import PORT, fetch, run

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('work', type=Path)
p.add_argument('--container-root', type=Path, help='Mounted pristine OCI root; run inside podman unshare')
a = p.parse_args()
work = a.work.resolve()
sysroot = work/'sysroot'
sysroot.mkdir(parents=True, exist_ok=True)
if not a.container_root:
    lock = {}
    for source in [PORT/'sensors/devel-sources.json', PORT/'sensors/gamescope/devel-sources.json']:
        lock.update(json.loads(source.read_text()))
    def download(meta):
        return fetch(meta['url'], work/'rpms'/meta['filename'], meta['sha256'])
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        archives = list(pool.map(download, lock.values()))
    for archive in archives:
        run('bsdtar', '-xf', archive, '-C', sysroot)
    raise SystemExit(0)
mount = a.container_root.resolve()
target = sysroot/'usr/lib64'
target.mkdir(parents=True, exist_ok=True)
names = json.loads((PORT/'sensors/gamescope/runtime-libraries.json').read_text())
names += ['libwayland-client.so.0', 'libglib-2.0.so.0', 'libgobject-2.0.so.0',
          'libgio-2.0.so.0', 'libqmi-glib.so.5', 'libqrtr-glib.so.0',
          'libprotobuf-c.so.1', 'libgudev-1.0.so.0', 'libpolkit-gobject-1.so.0', 'libudev.so.1']
copied = set()
while names:
    name = names.pop()
    if name in copied:
        continue
    src = mount/'usr/lib64'/name
    if not src.exists():
        src = mount/'usr/lib'/name
    src = src.resolve()
    if not src.is_relative_to(mount):
        raise ValueError('Library escaped OCI root: '+name)
    dest = target/name
    dest.unlink(missing_ok=True)
    shutil.copyfile(src, dest)
    copied.add(name)
    dynamic = subprocess.check_output(['aarch64-linux-gnu-readelf', '-d', str(src)], text=True)
    names += re.findall(r'\(NEEDED\).*\[(.*?)\]', dynamic)
for src in sorted(target.glob('*.so.*')):
    match = re.match(r'(.+\.so)\.[0-9]', src.name)
    if match:
        dest = target/match[1]
        if not dest.exists():
            dest.unlink(missing_ok=True)
            dest.symlink_to(src.name)
print('Prepared target libraries from pristine Armada:', len(copied))
