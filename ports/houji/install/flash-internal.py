#!/usr/bin/env python3
"""Install Houji images with stock ABL. Fresh installation destroys userdata."""
import argparse
import hashlib
import gzip
import io
import stat
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import time

BOOT_NAMES={'boot.img','init_boot.img','vendor_boot.img','dtbo.img','vbmeta.img'}
# Xiaomi's flash_all script loads a CRC list into the bootloader session; until the bootloader restarts it
# refuses every image that is not on the list, with the UEFI CRC error code 0x1B.
CRC_REFUSAL=re.compile(r'Error flashing partition\s*:\s*0*1B\b')
CRC_HINT=('The bootloader refused an image with a CRC error (0x1B), so that image was not written. This happens in the same '
          'fastboot session as Xiaomi\'s flash_all script, which loads Xiaomi\'s CRC list. Run "fastboot reboot-bootloader" '
          '(it restarts into fastboot and does not boot Android), then run this installer again.')
STALL_NOTE=('The phone acknowledged the reboot but is still in fastboot. After a large userdata write the Xiaomi bootloader '
            'can stall. If the phone has not restarted by itself, hold the Power button alone for 10-15 seconds (not Volume '
            'Down). Slot B is active, so it boots Armada.')


def require(condition,message):
    if not condition:raise ValueError(message)


def sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def verify_initramfs(image):
    header=image[:4096]
    size=struct.unpack_from('<I',header,12)[0]
    require(0<size<=len(image)-4096,'Invalid init_boot ramdisk length')
    with gzip.GzipFile(fileobj=io.BytesIO(image[4096:4096+size])) as stream:
        archive=stream.read((64<<20)+1)
    require(len(archive)<=64<<20,'Ramdisk exceeds expected unpacked size')
    members={};offset=0
    while True:
        require(offset+110<=len(archive) and archive[offset:offset+6]==b'070701','Invalid initramfs CPIO')
        fields=[int(archive[offset+6+i*8:offset+14+i*8],16) for i in range(13)]
        length=fields[11]
        require(0<length<4096 and offset+110+length<=len(archive),'Invalid CPIO name')
        name=archive[offset+110:offset+110+length-1].decode()
        offset=(offset+110+length+3)&~3
        require(offset+fields[6]<=len(archive),'Truncated CPIO payload')
        payload=archive[offset:offset+fields[6]];offset=(offset+fields[6]+3)&~3
        if name=='TRAILER!!!':break
        members[name]=(fields[1],payload)
    require('init' in members and members['init'][0]&0o111 and members['init'][1].startswith(b'#!/bin/sh\n'),'Missing executable init')
    require('bin/sh' in members and stat.S_ISLNK(members['bin/sh'][0]) and members['bin/sh'][1]==b'busybox','Missing early shell interpreter')
    require('bin/busybox' in members and members['bin/busybox'][0]&0o111,'Missing executable BusyBox')
    elf=members['bin/busybox'][1]
    require(len(elf)>=64 and elf[:6]==b'\x7fELF\x02\x01' and struct.unpack_from('<H',elf,18)[0]==183,'BusyBox is not arm64 ELF')
    phoff=struct.unpack_from('<Q',elf,32)[0];entsize,count=struct.unpack_from('<HH',elf,54)
    require(phoff+entsize*count<=len(elf) and (not count or entsize>=56),'Invalid BusyBox ELF headers')
    require(all(struct.unpack_from('<I',elf,phoff+i*entsize)[0]!=3 for i in range(count)),'Early BusyBox must be statically linked')


def check_boot_image(path):
    with path.open('rb') as stream:header=stream.read(1584)
    require(header[:8]==b'ANDROID!' and struct.unpack_from('<I',header,40)[0]==4,'Not Android boot v4')
    args=header[44:1580].split(b'\0',1)[0].decode().split()
    for word in ['rdinit=/init','clk_ignore_unused','pd_ignore_unused','regulator_ignore_unused']:
        require(word in args,'Missing boot argument: '+word)
    require(struct.unpack_from('<I',header,16)[0]!=0,'Missing stock ABL version metadata')


def check_vendor_boot(path):
    with path.open('rb') as stream:header=stream.read(12)
    require(header[:8]==b'VNDRBOOT' and struct.unpack_from('<I',header,8)[0]==4,'Invalid vendor_boot')


def verify_kernel_update(root):
    m=json.loads((root/'kernel-update.json').read_text())
    require(m.get('format')==1 and m.get('kind')=='kernel-update','Not a kernel update bundle')
    require(m.get('device')=='xiaomi,houji','Wrong device in manifest')
    require(set(m.get('files',{}))=={'boot.img','vendor_boot.img','modules.tar.zst'},'Unexpected file list')
    for name,info in m['files'].items():
        path=root/name
        require(path.is_file() and path.stat().st_size==info['size'],name+' size mismatch')
        require(sha(path)==info['sha256'],name+' checksum mismatch')
    check_boot_image(root/'boot.img');check_vendor_boot(root/'vendor_boot.img')
    # The manifest's release name is what the receipt and the installed modules are tied to.
    require(b'Linux version '+m['kernel_release'].encode()+b' (' in (root/'boot.img').read_bytes(),
            'boot.img is not kernel release '+m['kernel_release'])
    return m


def verify(root,boot_only=False):
    m=json.loads((root/'images.json').read_text())
    require(m.get('device')=='xiaomi,houji','Wrong device in manifest')
    require(m.get('root_transport')=='internal userdata ext4','Not an internal Armada image')
    require(m.get('automatic_reboot') is False,'Unexpected reboot timer')
    names=set(m['images'])
    require(names in [BOOT_NAMES,BOOT_NAMES|{'userdata.img'}],'Unexpected image list')
    local_userdata=None
    if not boot_only and 'userdata.img' not in names:
        # A root-image-only bundle: userdata.img must have been built here by
        # make-userdata.py, from this bundle's own root image.
        local_userdata=json.loads((root/'userdata.json').read_text()) if (root/'userdata.json').is_file() else None
        require(local_userdata is not None and (root/'userdata.img').is_file(),
                'Fresh installation requires userdata.img; run make-userdata.py in this directory first')
        layout=m['layout']
        require(local_userdata['build_id']==layout['build_id'] and local_userdata['rootfs_sha256']==layout['rootfs_sha256'],
                'userdata.img was built from a different root image; run make-userdata.py again')
        require((root/'userdata.img').stat().st_size==local_userdata['size'],'userdata.img size mismatch')
        require(sha(root/'userdata.img')==local_userdata['sha256'],'userdata.img checksum mismatch; run make-userdata.py again')
        require(local_userdata['initial_size']==layout.get('initial_size',local_userdata['initial_size']),
                'userdata.img does not match this bundle layout')
        require(local_userdata.get('filesystem_uuid')==layout.get('filesystem_uuid'),
                'userdata.img was built for a different filesystem identity; run make-userdata.py again')
    for name,info in m['images'].items():
        path=root/name
        require(path.stat().st_size==info['size'],name+' size mismatch')
        require(sha(path)==info['sha256'],name+' checksum mismatch')
    check_boot_image(root/'boot.img')
    with (root/'init_boot.img').open('rb') as stream:header=stream.read(44)
    require(header[:8]==b'ANDROID!' and struct.unpack_from('<I',header,40)[0]==4,'Invalid init_boot')
    verify_initramfs((root/'init_boot.img').read_bytes())
    check_vendor_boot(root/'vendor_boot.img')
    vbmeta=(root/'vbmeta.img').read_bytes()
    require(len(vbmeta)>=256 and vbmeta[:4]==b'AVB0','Invalid vbmeta header')
    auth_size,aux_size=struct.unpack_from('>QQ',vbmeta,12)
    require(256+auth_size+aux_size<=len(vbmeta),'Truncated vbmeta image')
    require(struct.unpack_from('>I',vbmeta,120)[0]&3==3,'vbmeta verification flags are not prepared')
    if 'userdata.img' in names or local_userdata is not None:
        with (root/'userdata.img').open('rb') as stream:header=stream.read(28)
        magic,major,minor,hs,cs,bs,blocks,chunks,crc=struct.unpack('<I4H4I',header)
        require(magic==0xed26ff3a and major==1 and bs==4096 and hs==28 and cs==12,'Invalid Android sparse userdata')
        expected=m['layout']['initial_size'] if local_userdata is None else local_userdata['initial_size']
        require(blocks*bs==expected,'Sparse userdata size mismatch')
        m['layout'].setdefault('initial_size',expected)
    return m


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--images-dir',type=Path,required=True)
    p.add_argument('--serial',required=True,help='Target shown by fastboot devices')
    mode=p.add_mutually_exclusive_group()
    mode.add_argument('--erase-userdata',action='store_true',help='Erase ALL user data for a fresh installation')
    mode.add_argument('--boot-only',action='store_true',help='Preserve userdata; requires a staged-update receipt')
    mode.add_argument('--kernel-only',action='store_true',
                      help='Replace only the kernel and device tree; requires a kernel-update receipt')
    p.add_argument('--staged-receipt',type=Path)
    p.add_argument('--kernel-receipt',type=Path,help='kernel-receipt.json from install-kernel-update.py')
    p.add_argument('--fastboot',default='fastboot')
    p.add_argument('--check-only',action='store_true')
    p.add_argument('--reboot-wait',type=float,default=30,
                   help='Seconds to wait for the phone to leave fastboot after the reboot command; 0 skips the check')
    a=p.parse_args();root=a.images_dir.resolve()
    m=verify_kernel_update(root) if a.kernel_only else verify(root,a.boot_only)
    if a.kernel_only:
        require(a.kernel_receipt is not None,'Run install-kernel-update.py on the phone before a kernel-only update')
        receipt=json.loads(a.kernel_receipt.read_text())
        require(receipt['kernel_release']==m['kernel_release'],'Receipt is for another kernel release')
        for key,name in [('boot_sha256','boot.img'),('vendor_boot_sha256','vendor_boot.img'),('modules_sha256','modules.tar.zst')]:
            require(receipt[key]==m['files'][name]['sha256'],'Receipt does not match '+name)
        require(receipt['serial_sha256']==hashlib.sha256(a.serial.encode()).hexdigest(),'Receipt belongs to another phone')
    elif a.boot_only:
        require(a.staged_receipt is not None,'Run stage-update.py on the phone before a boot-only update')
        receipt=json.loads(a.staged_receipt.read_text())
        require(receipt['build_id']==m['layout']['build_id'],'Staged build does not match boot images')
        require(receipt['rootfs_sha256']==m['layout']['rootfs_sha256'],'Staged root does not match manifest')
        require(receipt['serial_sha256']==hashlib.sha256(a.serial.encode()).hexdigest(),'Receipt belongs to another phone')
    require(a.check_only or a.boot_only or a.kernel_only or a.erase_userdata,
            'Choose --erase-userdata, --boot-only or --kernel-only')
    fb=shutil.which(a.fastboot);require(fb is not None,'fastboot not found')
    def command(*args,timeout=90):
        result=subprocess.run([fb,'-s',a.serial,*args],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=timeout)
        print(result.stdout,end='',flush=True)
        if result.returncode and CRC_REFUSAL.search(result.stdout):raise ValueError(CRC_HINT)
        result.check_returncode();return result.stdout
    def reboot():
        try:command('reboot',timeout=40)
        except subprocess.TimeoutExpired:
            print(STALL_NOTE,flush=True);return
        if a.reboot_wait<=0:return
        deadline=time.monotonic()+a.reboot_wait
        while True:
            listing=subprocess.run([fb,'devices'],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=15).stdout
            if a.serial not in listing:return
            if time.monotonic()>=deadline:break
            time.sleep(2)
        print(STALL_NOTE,flush=True)
    # Product and bootloader-state checks precede every write, including erase.
    for args,expected in [(('getvar','product'),'product: houji'),(('getvar','is-userspace'),'is-userspace: no'),
                          (('getvar','snapshot-update-status'),'snapshot-update-status: none'),
                          (('oem','device-info'),'Device unlocked: true')]:
        require(expected in command(*args),'Bootloader preflight failed: '+args[-1])
    if a.kernel_only:
        # Only slot B holds an installed Armada; its init_boot is left untouched.
        require('current-slot: b' in command('getvar','current-slot'),'Bootloader preflight failed: current-slot')
        targets=[(name[:-4]+'_b',m['files'][name]['size']) for name in ('boot.img','vendor_boot.img')]
    else:
        targets=[('userdata' if name=='userdata.img' else name[:-4]+'_b',
                  m['layout']['initial_size'] if name=='userdata.img' else m['images'][name]['size'])
                 for name in sorted(BOOT_NAMES|({'userdata.img'} if not a.boot_only else set()))]
    for part,needed in targets:
        output=command('getvar','partition-size:'+part)
        match=re.search(r'partition-size:'+re.escape(part)+r':\s*((?:0x)?[0-9a-fA-F]+)\b',output)
        require(match is not None,'Missing partition capacity: '+part)
        require(int(match[1],16)>=needed,part+' is too small')
    if a.check_only:
        print('Preflight passed; no storage written.');return
    if a.kernel_only:
        written=[]
        try:
            for name in ('boot','vendor_boot'):
                command('flash',name+'_b',str(root/(name+'.img')),timeout=180)
                written.append(name+'_b')
        except (subprocess.SubprocessError,OSError):
            if written:
                print('WARNING: '+', '.join(written)+' was written but the next flash failed, so the phone now has a new '
                      'kernel with the old device tree. Run this command again, or restore both images from the previous bundle.',flush=True)
            raise
        reboot()
        print('Kernel flashed. Verify the physical boot; the previous kernel remains flashable from its bundle.')
        return
    for name in ['boot','init_boot','vendor_boot','dtbo']:
        command('flash',name+'_b',str(root/(name+'.img')),timeout=180)
    command('flash','vbmeta_b',str(root/'vbmeta.img'))
    command('set_active','b')
    if a.erase_userdata:
        # userdata goes last. The Xiaomi bootloader has stalled right after this large write, so no command that
        # matters may follow it, and slot B is already active: a forced restart after a stall boots Armada, not Android.
        try:
            command('erase','userdata',timeout=300)
            command('flash','userdata',str(root/'userdata.img'),timeout=1800)
        except (subprocess.SubprocessError,OSError):
            print('WARNING: slot B is active but userdata was not completely written, so Armada cannot boot yet. '
                  'Put the phone in bootloader fastboot and run this command again.',flush=True)
            raise
    reboot()
    print('Flash commands succeeded. Verify the physical boot before calling the build tested.')


if __name__=='__main__':
    try:main()
    except (ValueError,OSError,KeyError,subprocess.SubprocessError) as error:
        raise SystemExit(str(error)) from None
