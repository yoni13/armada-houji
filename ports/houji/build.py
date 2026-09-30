#!/usr/bin/env python3
"""Build a standalone Houji image from this checkout and pinned public inputs."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from buildlib import PORT, REPO, kernel_source, run, sha


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,default=REPO/'output/houji')
    p.add_argument('--image',help='Arm64 Armada OCI image; defaults to the pinned official image')
    p.add_argument('--podman-root',type=Path,help='Optional existing rootless Podman store')
    p.add_argument('--podman-runroot',type=Path)
    p.add_argument('--skip-kernel',action='store_true',help='Reuse this output directory\'s completed kernel build')
    p.add_argument('--skip-userdata',action='store_true',help='Build a preserving update without a fresh userdata image')
    p.add_argument('--inside',choices=['libraries','rootfs'],help=argparse.SUPPRESS)
    p.add_argument('--container',help=argparse.SUPPRESS)
    a=p.parse_args();output=a.output.resolve();work=output/'work';work.mkdir(parents=True,exist_ok=True)
    kernel=kernel_source(work)
    pm=['podman']
    if bool(a.podman_root)!=bool(a.podman_runroot):p.error('Specify both Podman storage paths together')
    storage=[]
    if a.podman_root:
        pm+=['--root',str(a.podman_root.resolve()),'--runroot',str(a.podman_runroot.resolve()),'--storage-driver','overlay']
        storage=['--podman-root',str(a.podman_root.resolve()),'--podman-runroot',str(a.podman_runroot.resolve())]
    if a.inside:
        mount=Path(subprocess.check_output(pm+['mount',a.container],text=True).strip())
        try:
            if a.inside=='libraries':
                run('python3',PORT/'prepare-sysroot.py',work/'userspace','--container-root',mount)
            else:
                run('python3',PORT/'stage-root.py',mount,work)
                target=output/'rootfs.erofs';temp=output/'rootfs.erofs.tmp'
                run('mkfs.erofs','--workers=8','-zlz4hc,level=9','-T','1790424000','-L','ARMADA_HOUJI',temp,mount)
                run('fsck.erofs',temp);temp.replace(target)
        finally:run(*pm,'unmount',a.container)
        return
    firmware=PORT/'firmware';manifest=json.loads((firmware/'firmware.json').read_text())
    for name,digest in manifest['files'].items():
        if sha(firmware/name)!=digest:raise ValueError('Firmware checksum mismatch: '+name)
    if not a.skip_kernel:
        env=os.environ.copy();env.update(HOUJI_WORK_DIR=str(work),HOUJI_OUT_DIR=str(output/'build/kernel'))
        run('bash',PORT/'build-kernel.sh',env=env)
    run('python3',PORT/'touch/native/build-module.py',kernel,work/'touch-module')
    run('python3',PORT/'nfc/build-module.py',kernel,work/'nfc-module')
    run('python3',PORT/'prepare-sysroot.py',work/'userspace')
    image=a.image or json.loads((PORT/'sources.json').read_text())['armada_image']
    # An unstarted disposable container prevents any owner settings entering the image.
    container=subprocess.check_output(pm+['create','--platform','linux/arm64','--network=none',image],text=True).strip()
    try:
        run(*pm,'unshare','python3',Path(__file__),'--output',output,*storage,'--inside','libraries','--container',container)
        run('python3',PORT/'build-userspace.py',work/'userspace')
        run('python3',PORT/'gps/build.py','--work',work/'gps','--sysroot',work/'userspace/sysroot','--kernel',kernel)
        run(*pm,'unshare','python3',Path(__file__),'--output',output,*storage,'--inside','rootfs','--container',container)
    finally:run(*pm,'rm',container)
    run('python3',PORT/'install/assemble.py','--kernel',output/'build/kernel','--rootfs',output/'rootfs.erofs',
        '--busybox',work/'userspace/bin/busybox','--output',output/'images',*(['--skip-userdata'] if a.skip_userdata else []))
    print('Build finished. Use images/flash-internal.py for preflight and installation.')


if __name__=='__main__':main()
