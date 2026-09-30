#!/usr/bin/env python3
"""Build the port's userspace from pinned sources against the prepared OCI sysroot."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
from buildlib import PORT, REPO, checkout, fetch, run

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('work', type=Path)
a=p.parse_args(); work=a.work.resolve(); work.mkdir(parents=True,exist_ok=True)
sysroot=work/'sysroot'; out=work/'bin';out.mkdir(exist_ok=True)
pins=json.loads((PORT/'sources.json').read_text())
# Use Fedora's NFC daemon, pinned like the other binary dependencies.
pin=pins['neard']
archive=fetch(pin['url'],work/'downloads/neard.rpm',pin['sha256'])
neard=work/'neard';neard.mkdir(exist_ok=True)
run('bsdtar','-xf',archive,'-C',neard)
run('python3', PORT/'sensors/build.py','--work',work/'sensors','--sysroot',sysroot)
run('python3', PORT/'sensors/gamescope/build.py','--work',work/'gamescope','--sysroot',sysroot)
run('make','-C',PORT/'touch/native','OUT='+str(out))
run('sh',PORT/'charging/build-stock-auth.sh',out/'houji-stock-auth')
for name in ['audioreach-topology','qbootctl']:
    pin=pins['repositories'][name]
    checkout(pin['url'],pin['commit'],work/'sources'/name)
run('python3', PORT/'audio/build-topology.py', PORT/'audio/Xiaomi-14.m4',
    work/'sources/audioreach-topology',out/'Xiaomi-14-tplg.bin')
qboot=work/'sources/qbootctl'
run('aarch64-linux-gnu-gcc','-static','-O2','-o',out/'qbootctl',
    *(qboot/n for n in ['qbootctl.c','bootctrl_impl.c','gpt-utils.c','ufs-bsg.c','crc32.c']))
# BusyBox provides only the early-boot shell and filesystem tools.
pin=pins['busybox']; archive=fetch(pin['url'],work/'downloads/busybox.tar.bz2',pin['sha256'])
source=work/('busybox-'+pin['version'])
if not source.exists():run('tar','-xf',archive,'-C',work)
shutil.copy2(PORT/'install/busybox.config',source/'.config')
run('make','-C',source,'ARCH=arm64','CROSS_COMPILE=aarch64-linux-gnu-','oldconfig',stdin=subprocess.DEVNULL)
run('make','-C',source,'ARCH=arm64','CROSS_COMPILE=aarch64-linux-gnu-','-j8')
shutil.copy2(source/'busybox',out/'busybox')
# Build all responder patches, including SuperSpeed endpoint descriptors.
base={}
for line in (REPO/'packages/umtp-responder/BASE.env').read_text().splitlines():
    if '=' in line and not line.startswith('#'):
        key,value=line.split('=',1);base[key]=value.strip('"\'')
source=checkout('https://github.com/viveris/uMTP-Responder.git',base['COMMIT'],work/'sources/umtp-responder')
for patch in sorted((REPO/'packages/umtp-responder/patches').glob('*.patch')):
    if subprocess.run(['git','-C',str(source),'apply','--check',str(patch)],capture_output=True).returncode==0:
        run('git','-C',source,'apply',patch)
    else:run('git','-C',source,'apply','--reverse','--check',patch)
run('make','-C',source,'CC=aarch64-linux-gnu-gcc',
    'CFLAGS=-O2 -I./inc -Wall -DSYSTEMD_NOTIFY -I'+str(sysroot/'usr/include'),
    'LDFLAGS=-lpthread -lrt '+str(sysroot/'usr/lib64/libsystemd.so.0')+' -Wl,--allow-shlib-undefined')
shutil.copy2(source/'umtprd',out/'umtprd')
for path in out.iterdir():
    if path.is_file() and path.read_bytes()[:4]==b'\x7fELF':
        run('aarch64-linux-gnu-strip','--strip-unneeded',path)
print('Userspace build complete:',work)
