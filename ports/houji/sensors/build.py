#!/usr/bin/env python3
"""Build Houji SSC userspace against the pinned Armada sysroot."""
from pathlib import Path
import argparse,json,shutil,subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from buildlib import checkout, cross_file, run
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--work', type=Path, required=True)
p.add_argument('--sysroot', type=Path, required=True)
a=p.parse_args()
source=Path(__file__).resolve().parent
work=a.work.resolve(); work.mkdir(parents=True,exist_ok=True)
sysroot=a.sysroot.resolve()
lock=json.loads((source/'sources.json').read_text())
for name in ['libssc','hexagonrpc','iio-sensor-proxy']:
 tree=work/'sources'/name
 checkout(lock[name]['source'], lock[name]['revision'], tree,
          source/'hexagonrpc-houji-registry.patch' if name=='hexagonrpc' else None)
cross_file(work, sysroot)
common=['--cross-file',work/'cross.ini','--prefix=/usr','--libdir=lib64']
for name,build,extra in [('libssc','libssc-build',[]),('hexagonrpc','hexagon-build',['-Dhexagonrpcd_verbose=false']),('iio-sensor-proxy','iio-build',['--libexecdir=libexec','-Dssc-support=enabled','-Dudevrulesdir=/usr/lib/udev/rules.d','-Dsystemdsystemunitdir=/usr/lib/systemd/system'])]:
 dest=work/build
 if not (dest/'build.ninja').exists():run('meson','setup',dest,work/'sources'/name,*common,*extra)
 if name=='hexagonrpc':run('ninja','-C',dest,'hexagonrpcd/hexagonrpcd')
 else:run('meson','compile','-C',dest)
 if name=='libssc':run('meson','install','-C',dest,'--destdir',sysroot)
run('make','-C',source/'gamescope','OUT='+str(work/'gamescope-build'),'WAYLAND_INCLUDE='+str(sysroot/'usr/include'),'WAYLAND_LIBDIR='+str(sysroot/'usr/lib64'),'LDFLAGS=-Wl,-rpath-link,'+str(sysroot/'usr/lib64'))
print('Built SSC, sensor proxy, registry service and Gamescope rotation helper in',work)
