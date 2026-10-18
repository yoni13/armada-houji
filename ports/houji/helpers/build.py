#!/usr/bin/env python3
"""Cross-build small resident helpers with the pinned Armada runtime libraries."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent))
from buildlib import fetch

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--sysroot',type=Path,required=True)
p.add_argument('--output',type=Path,required=True)
a=p.parse_args();root=a.sysroot.resolve();output=a.output.resolve()
pin=json.loads((HERE/'sources.json').read_text())['nlohmann-json']
header=fetch(pin['url'],output/'include/nlohmann/json.hpp',pin['sha256'])
includes=[root/'usr/include/glib-2.0',root/'usr/lib64/glib-2.0/include',root/'usr/include/gio-unix-2.0']
libraries=[root/'usr/lib64'/name for name in ('libgio-2.0.so.0','libgobject-2.0.so.0','libglib-2.0.so.0')]
for path in [*includes,*libraries]:
    if not path.exists():p.error('Missing sysroot dependency: '+str(path))
subprocess.run(['make','-C',str(Path(__file__).resolve().parent),'CXX=aarch64-linux-gnu-g++',
                'OUT='+str(output),'GIO_CFLAGS='+' '.join('-I'+str(p) for p in includes),
                'JSON_CFLAGS=-I'+str(header.parents[1]),
                'GIO_LIBS='+' '.join(map(str,libraries))+' -Wl,--allow-shlib-undefined',
                'LDFLAGS=-Wl,-rpath-link,'+str(root/'usr/lib64')],check=True)
for name in ('houji-steam-power','houji-x2s-session','houji-gamescope-orientation',
             'houji-gyro','houji-thermal','houji-charge-policy','houji-license-relay','houji-cellular-control'):
    subprocess.run(['aarch64-linux-gnu-strip','--strip-unneeded',str(output/name)],check=True)
