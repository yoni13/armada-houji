#!/usr/bin/env python3
"""Stage a preserving update on the phone. Run as root with the new image bundle.

The old root and overlay remain available for rollback. Home, saved networks,
pairings and owner SSH settings stay on this phone, never in the build image.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time


def sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def copy_image(source, destination):
    """Throttle local userdata writes too; pause at the handset thermal limits."""
    with source.open('rb') as src, destination.open('xb') as dst:
        while True:
            hot = False
            for zone in Path('/sys/class/thermal').glob('thermal_zone*'):
                kind = (zone/'type').read_text().strip()
                if 'batt' in kind or kind.startswith(('cpu','soc','gpu')):
                    hot |= int((zone/'temp').read_text()) >= (41000 if 'batt' in kind else 75000)
            if hot:
                time.sleep(1)
                continue
            started = time.monotonic()
            block = src.read(1024*1024)
            if not block:
                break
            dst.write(block)
            time.sleep(max(0, len(block)/(15*1024*1024)-(time.monotonic()-started)))
        dst.flush()
        os.fsync(dst.fileno())


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('bundle',type=Path)
    p.add_argument('--serial',required=True,help='This handset\'s fastboot serial, used only as a hash in the receipt')
    a=p.parse_args()
    if os.getuid()!=0:raise ValueError('Run as root on the installed phone')
    if Path('/sys/firmware/devicetree/base/model').read_bytes().rstrip(b'\0')!=b'Xiaomi 14':raise ValueError('Wrong handset')
    m=json.loads((a.bundle/'images.json').read_text());layout=m['layout'];build_id=layout['build_id']
    if not re.fullmatch('[0-9a-f]{12}-[0-9a-f]{12}',build_id):raise ValueError('Invalid build identity')
    if m['device']!='xiaomi,houji' or layout['directory']!='armada/'+build_id:raise ValueError('Invalid image layout')
    image=a.bundle/'rootfs.erofs'
    if sha(image)!=layout['rootfs_sha256']:raise ValueError('Root image checksum mismatch')
    data=Path('/run/houji/data')
    if not data.is_mount():raise ValueError('Existing internal userdata mount is required')
    target=data/layout['directory']
    if not target.resolve().is_relative_to(data.resolve()):raise ValueError('Update path escapes userdata')
    if target.exists():raise ValueError('Update already staged; inspect it before retrying')
    if shutil.disk_usage(data).free<image.stat().st_size+(1<<30):raise ValueError('Insufficient userdata free space')
    target.mkdir(parents=True,mode=0o700)
    copy_image(image,target/'rootfs.erofs')
    if sha(target/'rootfs.erofs')!=layout['rootfs_sha256']:raise ValueError('Staged image verification failed')
    (target/'upper').mkdir();(target/'work').mkdir(mode=0o700)
    # Resolve the currently mounted overlay's backing home, without copying games.
    mounted_home=subprocess.check_output(['findmnt','-no','TARGET','-T','/var/home'],text=True).strip()
    options=subprocess.check_output(['findmnt','-no','OPTIONS','-T','/var/home'],text=True).strip().split(',')
    upper=Path(next(x.split('=',1)[1] for x in options if x.startswith('upperdir=')))
    home=(upper if mounted_home=='/var/home' else upper/'var/home').resolve()
    if not home.is_dir() or not home.is_relative_to(data.resolve()):raise ValueError('Cannot locate persistent home under userdata')
    (target/'shared-home').write_text(str(home.relative_to(data.resolve()))+'\n')
    # Copy settings, not old executable/module hotfixes, into the clean system overlay.
    for name in ['etc/NetworkManager/system-connections','var/lib/bluetooth','etc/ssh',
                 'var/lib/armada-nfc/settings.json',
                 'etc/armada/cellular-sim',
                 'etc/systemd/system/multi-user.target.wants/houji-cellular.service',
                 'etc/passwd','etc/shadow','etc/group','etc/gshadow',
                 'etc/systemd/system/houji-ssh.service',
                 'etc/systemd/system/multi-user.target.wants/houji-ssh.service',
                 'etc/systemd/system/multi-user.target.wants/sshd.service',
                 'etc/systemd/system/sockets.target.wants/sshd.socket',
                 'etc/systemd/system/sshd.service.d', 'etc/systemd/system/sshd.socket.d']:
        src=Path('/')/name;dst=target/'upper'/name
        if not src.exists():continue
        dst.parent.mkdir(parents=True,exist_ok=True)
        if src.is_symlink():dst.symlink_to(src.readlink())
        elif src.is_dir():shutil.copytree(src,dst,symlinks=True)
        else:shutil.copy2(src,dst)
    # This state is created locally from factory NV/secure storage and must
    # survive preserving updates, but must never be part of a distributable root.
    # Stage while the radio is off; never stop/restart MPSS just to stage files.
    private=Path('/var/lib/houji-modem')
    if private.exists():
        destination=target/'upper/var/lib/houji-modem'
        destination.mkdir(parents=True,mode=0o700)
        for name in ['efs','tee']:
            source=private/name
            if source.is_dir():
                shutil.copytree(source,destination/name,symlinks=True,copy_function=lambda src,dst: copy_image(Path(src),Path(dst)))
    (target/'build-id').write_text(build_id+'\n')
    receipt={'build_id':build_id,'rootfs_sha256':layout['rootfs_sha256'],
             'serial_sha256':hashlib.sha256(a.serial.encode()).hexdigest()}
    (a.bundle/'staged-receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    os.chmod(a.bundle/'staged-receipt.json',0o600)
    os.sync()
    print('Staged clean root and preserved home. Copy staged-receipt.json to the host, then run the installer.')


if __name__=='__main__':main()
