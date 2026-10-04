#!/usr/bin/env python3
"""Cross-build mangoapp from Armada's MangoHud package and its patches.

The pinned Armada image predates patches 0007 (firmware battery percentage)
and 0008 (one overlay render per app frame), so the port builds mangoapp,
Steam's performance overlay, until it catches up.
"""
from pathlib import Path
import argparse, hashlib, json, shutil, subprocess, tarfile

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from buildlib import PORT, REPO, cross_file, fetch, run

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--work', type=Path, required=True)
p.add_argument('--sysroot', type=Path, required=True)
a = p.parse_args()
work = a.work.resolve(); work.mkdir(parents=True, exist_ok=True)
sysroot = a.sysroot.resolve()
package = REPO/'packages/mangohud'
pin = json.loads((PORT/'sources.json').read_text())['mangohud']
base = dict(line.split('=', 1) for line in (package/'BASE.env').read_text().split() if '=' in line)
if base['VERSION'] != pin['version']:
    raise SystemExit(f"packages/mangohud is {base['VERSION']}, the port pins {pin['version']}")
archive = fetch(pin['url'], work/'downloads'/Path(pin['url']).name, pin['sha256'])
patches = sorted((package/'patches').glob('*.patch'))

# Re-extract only when the tarball or a patch changes, so rebuilds stay incremental.
stamp = hashlib.sha256(pin['sha256'].encode())
for patch in patches:
    stamp.update(patch.name.encode() + patch.read_bytes())
source = work/('MangoHud-' + pin['version'])
if not (work/'source.stamp').exists() or (work/'source.stamp').read_text() != stamp.hexdigest():
    cache = source/'subprojects/packagecache'
    kept = work/'packagecache'
    if cache.exists():
        shutil.rmtree(kept, ignore_errors=True)
        cache.rename(kept)
    shutil.rmtree(source, ignore_errors=True)
    with tarfile.open(archive) as tar:
        tar.extractall(work, filter='data')
    if kept.exists():
        kept.rename(cache)
    for patch in patches:
        run('patch', '-p1', '--forward', '--silent', '-d', source, '-i', patch)
    (work/'source.stamp').write_text(stamp.hexdigest())

cross_file(work, sysroot)
# MangoHud links libstdc++ statically, which needs glibc's libc_nonshared.a. The
# sysroot's libc.so is a plain symlink, not glibc's script that would add it.
nonshared = subprocess.check_output(['aarch64-linux-gnu-gcc', '-print-file-name=libc_nonshared.a'], text=True).strip()
links = ['-L'+str(sysroot/'usr/lib64'), '-Wl,-rpath-link,'+str(sysroot/'usr/lib64'), '-lmvec', nonshared]
opts = ['--reconfigure'] if (work/'out/build.ninja').exists() else []
run('meson', 'setup', *opts, work/'out', source, '--cross-file', work/'cross.ini',
    '-Dc_link_args='+json.dumps(links), '-Dcpp_link_args='+json.dumps(links),
    '--prefix=/usr', '--libdir=lib64', '-Dbuildtype=release', '--wrap-mode=default',
    '-Dmangoapp=true', '-Dmangohudctl=false', '-Dinclude_doc=false', '-Dmangoplot=disabled',
    '-Duse_system_spdlog=enabled', '-Dwith_x11=enabled', '-Dwith_wayland=enabled',
    '-Dwith_xnvctrl=disabled', '-Dwith_fex=true', '-Dtests=disabled')
run('ninja', '-C', work/'out', '-j8', 'src/mangoapp')
shutil.copy2(work/'out/src/mangoapp', work/'mangoapp')
run('patchelf', '--remove-rpath', work/'mangoapp')
run('aarch64-linux-gnu-strip', '--strip-unneeded', work/'mangoapp')
print('Built', work/'mangoapp')
