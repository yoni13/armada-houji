#!/usr/bin/env python3
"""Cross-build the pinned Houji Gamescope from the pinned Armada sysroot."""
from pathlib import Path
import argparse, concurrent.futures, hashlib, json, os, re, shutil, subprocess, urllib.request

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from buildlib import checkout, cross_file, run
port = Path(__file__).resolve().parent
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--work', type=Path, required=True)
p.add_argument('--sysroot', type=Path, required=True)
a = p.parse_args()
work=a.work.resolve(); work.mkdir(parents=True,exist_ok=True)
sysroot=a.sysroot.resolve()
pin = json.loads((port/'sources.json').read_text())['compositor']
source=checkout(pin['repository'], pin['revision'], work/'source')
for patch in pin['patches']:
    checkout(pin['repository'], pin['revision'], source, port/patch)
shutil.copy2(port/'frame-wake.h', source/'src/houji_frame_wake.h')
run('git', '-C', source, 'submodule', 'update', '--init', '--depth', '1',
    'subprojects/wlroots', 'subprojects/libliftoff', 'subprojects/vkroots',
    'src/reshade', 'thirdparty/SPIRV-Headers')

cross_file(work, sysroot)
opts = ['--reconfigure', '--clearcache'] if (work/'out/build.ninja').exists() else []
run('meson', 'setup', *opts, work/'out', source, '--cross-file', work/'cross.ini',
    '-Dcpp_link_args='+json.dumps(['-L'+str(sysroot/'usr/lib64'), '-Wl,-rpath-link,'+str(sysroot/'usr/lib64'), '-lmvec']),
    '--prefix=/usr', '--libdir=lib64', '-Dbuildtype=release', '-Dpipewire=disabled',
    '-Dsdl2_backend=disabled', '-Davif_screenshots=disabled', '-Dinput_emulation=enabled',
    '-Denable_openvr_support=false', '-Denable_gamescope_wsi_layer=false',
    '-Denable_tests=false', '-Dbenchmark=disabled', '-Dwlroots:color-management=disabled')
run('ninja', '-C', work/'out', '-j8', 'src/gamescope')
assert '#define XWAYLAND_PATH "/usr/bin/Xwayland"' in (work/'out/subprojects/wlroots/include/config.h').read_text()
shutil.copy2(work/'out/src/gamescope', work/'gamescope')
run('patchelf', '--remove-rpath', work/'gamescope')
run('aarch64-linux-gnu-strip', '--strip-unneeded', work/'gamescope')
print('Built', work/'gamescope')
