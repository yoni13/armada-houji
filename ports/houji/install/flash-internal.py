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

BOOT_NAMES={'boot.img','init_boot.img','vendor_boot.img','dtbo.img','vbmeta.img'}


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


def verify(root,boot_only=False):
    m=json.loads((root/'images.json').read_text())
    require(m.get('device')=='xiaomi,houji','Wrong device in manifest')
    require(m.get('root_transport')=='internal userdata ext4','Not an internal Armada image')
    require(m.get('automatic_reboot') is False,'Unexpected reboot timer')
    names=set(m['images'])
    require(names in [BOOT_NAMES,BOOT_NAMES|{'userdata.img'}],'Unexpected image list')
    require(boot_only or 'userdata.img' in names,'Fresh installation requires userdata.img')
    for name,info in m['images'].items():
        path=root/name
        require(path.stat().st_size==info['size'],name+' size mismatch')
        require(sha(path)==info['sha256'],name+' checksum mismatch')
    with (root/'boot.img').open('rb') as stream:header=stream.read(1584)
    require(header[:8]==b'ANDROID!' and struct.unpack_from('<I',header,40)[0]==4,'Not Android boot v4')
    args=header[44:1580].split(b'\0',1)[0].decode().split()
    for word in ['rdinit=/init','clk_ignore_unused','pd_ignore_unused','regulator_ignore_unused']:
        require(word in args,'Missing boot argument: '+word)
    require(struct.unpack_from('<I',header,16)[0]!=0,'Missing stock ABL version metadata')
    with (root/'init_boot.img').open('rb') as stream:header=stream.read(44)
    require(header[:8]==b'ANDROID!' and struct.unpack_from('<I',header,40)[0]==4,'Invalid init_boot')
    verify_initramfs((root/'init_boot.img').read_bytes())
    with (root/'vendor_boot.img').open('rb') as stream:header=stream.read(12)
    require(header[:8]==b'VNDRBOOT' and struct.unpack_from('<I',header,8)[0]==4,'Invalid vendor_boot')
    vbmeta=(root/'vbmeta.img').read_bytes()
    require(len(vbmeta)>=256 and vbmeta[:4]==b'AVB0','Invalid vbmeta header')
    auth_size,aux_size=struct.unpack_from('>QQ',vbmeta,12)
    require(256+auth_size+aux_size<=len(vbmeta),'Truncated vbmeta image')
    require(struct.unpack_from('>I',vbmeta,120)[0]&3==3,'vbmeta verification flags are not prepared')
    if 'userdata.img' in names:
        with (root/'userdata.img').open('rb') as stream:header=stream.read(28)
        magic,major,minor,hs,cs,bs,blocks,chunks,crc=struct.unpack('<I4H4I',header)
        require(magic==0xed26ff3a and major==1 and bs==4096 and hs==28 and cs==12,'Invalid Android sparse userdata')
        require(blocks*bs==m['layout']['initial_size'],'Sparse userdata size mismatch')
    return m


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--images-dir',type=Path,required=True)
    p.add_argument('--serial',required=True,help='Target shown by fastboot devices')
    mode=p.add_mutually_exclusive_group()
    mode.add_argument('--erase-userdata',action='store_true',help='Erase ALL user data for a fresh installation')
    mode.add_argument('--boot-only',action='store_true',help='Preserve userdata; requires a staged-update receipt')
    p.add_argument('--staged-receipt',type=Path)
    p.add_argument('--fastboot',default='fastboot')
    p.add_argument('--check-only',action='store_true')
    a=p.parse_args();root=a.images_dir.resolve();m=verify(root,a.boot_only)
    if a.boot_only:
        require(a.staged_receipt is not None,'Run stage-update.py on the phone before a boot-only update')
        receipt=json.loads(a.staged_receipt.read_text())
        require(receipt['build_id']==m['layout']['build_id'],'Staged build does not match boot images')
        require(receipt['rootfs_sha256']==m['layout']['rootfs_sha256'],'Staged root does not match manifest')
        require(receipt['serial_sha256']==hashlib.sha256(a.serial.encode()).hexdigest(),'Receipt belongs to another phone')
    require(a.check_only or a.boot_only or a.erase_userdata,'Choose --erase-userdata or --boot-only')
    fb=shutil.which(a.fastboot);require(fb is not None,'fastboot not found')
    def command(*args,timeout=90):
        result=subprocess.run([fb,'-s',a.serial,*args],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=timeout)
        print(result.stdout,end='',flush=True);result.check_returncode();return result.stdout
    # Product and bootloader-state checks precede every write, including erase.
    for args,expected in [(('getvar','product'),'product: houji'),(('getvar','is-userspace'),'is-userspace: no'),
                          (('getvar','snapshot-update-status'),'snapshot-update-status: none'),
                          (('oem','device-info'),'Device unlocked: true')]:
        require(expected in command(*args),'Bootloader preflight failed: '+args[-1])
    for name in sorted(BOOT_NAMES|({'userdata.img'} if not a.boot_only else set())):
        part='userdata' if name=='userdata.img' else name[:-4]+'_b'
        output=command('getvar','partition-size:'+part)
        match=re.search(r'partition-size:'+re.escape(part)+r':\s*((?:0x)?[0-9a-fA-F]+)\b',output)
        require(match is not None,'Missing partition capacity: '+part)
        size=int(match[1],16)
        needed=m['layout']['initial_size'] if part=='userdata' else m['images'][name]['size']
        require(size>=needed,part+' is too small')
    if a.check_only:
        print('Preflight passed; no storage written.');return
    if a.erase_userdata:
        command('erase','userdata',timeout=300)
        command('flash','userdata',str(root/'userdata.img'),timeout=1800)
    for name in ['boot','init_boot','vendor_boot','dtbo']:
        command('flash',name+'_b',str(root/(name+'.img')),timeout=180)
    command('flash','vbmeta_b',str(root/'vbmeta.img'))
    command('set_active','b');command('reboot')
    print('Flash commands succeeded. Verify the physical boot before calling the build tested.')


if __name__=='__main__':
    try:main()
    except (ValueError,OSError,KeyError,subprocess.SubprocessError) as error:
        raise SystemExit(str(error)) from None
