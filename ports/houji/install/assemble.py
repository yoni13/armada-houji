#!/usr/bin/env python3
"""Package stock-ABL boot images and a new internal userdata filesystem."""
import argparse
import gzip
import importlib.util
import json
from pathlib import Path
import shutil
import stat
import struct
import tempfile
import sys
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from buildlib import PORT, run, sha


def cpio(entries):
    result=bytearray()
    for number,(name,mode,data) in enumerate([*entries,('TRAILER!!!',0,b'')],1):
        fields=[number,mode,0,0,1,0,len(data),0,0,0,0,len(name.encode())+1,0]
        result.extend(b'070701'+''.join(f'{v:08x}' for v in fields).encode()+name.encode()+b'\0')
        result.extend(b'\0'*(-len(result)%4));result.extend(data);result.extend(b'\0'*(-len(result)%4))
    return gzip.compress(result,mtime=0)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--kernel',type=Path,required=True,help='build-kernel.sh output directory')
    p.add_argument('--rootfs',type=Path,required=True)
    p.add_argument('--busybox',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--skip-userdata',action='store_true',help='Package a preserving update only')
    a=p.parse_args();out=a.output.resolve();out.mkdir(parents=True,exist_ok=True)
    # Remove the manifest first so a failed rebuild cannot look complete.
    (out/'images.json').unlink(missing_ok=True)
    run('python3',PORT/'install/check-busybox.py',a.busybox)
    root_hash=sha(a.rootfs)
    build_id=sha(a.kernel/'Image')[:12]+'-'+root_hash[:12]
    layout_path='armada/'+build_id
    layout={'directory':layout_path,'build_id':build_id,'filesystem_label':'ARMADA_HOUJI_RW',
            'filesystem_uuid':str(uuid.uuid4()),'rootfs_sha256':root_hash,
            'rootfs_size':a.rootfs.stat().st_size}
    for name in ['Image','sm8650-xiaomi-houji.dtb','kernel.config']:
        shutil.copy2(a.kernel/name,out/name)
    init=(PORT/'install/init').read_text().replace('@LAYOUT@',layout_path).replace('@BUILD_ID@',build_id).replace('@ROOT_SIZE@',str(layout['rootfs_size']))
    (out/'init').write_text(init);run('sh','-n',out/'init')
    entries=[]
    directories={'bin','sbin','usr','usr/bin','usr/sbin','proc','sys','dev','run','newroot','lib','lib/firmware'}
    files=[('init',0o755,init.encode()),('bin/busybox',0o755,a.busybox.read_bytes())]
    firmware=PORT/'firmware/root/usr/lib/firmware'
    for src in firmware.rglob('*'):
        if src.is_file() and ('/qcom/' in str(src) or '/ath12k/' in str(src)):
            name='lib/firmware/'+str(src.relative_to(firmware));files.append((name,0o644,src.read_bytes()))
            directories.update(str(x) for x in Path(name).parents if str(x)!='.')
    for name in sorted(directories,key=lambda x:(x.count('/'),x)):entries.append((name,stat.S_IFDIR|0o755,b''))
    # The kernel resolves the shebang before /init can install BusyBox applets.
    entries.append(('bin/sh',stat.S_IFLNK|0o777,b'busybox'))
    entries += [(name,stat.S_IFREG|mode,data) for name,mode,data in files]
    (out/'initramfs.cpio.gz').write_bytes(cpio(entries))
    spec=importlib.util.spec_from_file_location('pack',PORT/'pack-images.py')
    pack=importlib.util.module_from_spec(spec);spec.loader.exec_module(pack)
    pack.pack(out,PORT/'firmware/stock')
    # Match fastboot's --disable-verity/--disable-verification transformation
    # while packaging, so host fastboot versions need not rewrite this buffer.
    vbmeta=bytearray((PORT/'firmware/stock/vbmeta.img').read_bytes())
    if len(vbmeta)<256 or vbmeta[:4]!=b'AVB0':raise ValueError('Invalid stock AVB header')
    struct.pack_into('>I',vbmeta,120,struct.unpack_from('>I',vbmeta,120)[0]|3)
    (out/'vbmeta.img').write_bytes(vbmeta)
    # Keep a separately named root image for preserving updates over an owner's SSH.
    dest=out/'rootfs.erofs'
    if dest.resolve()!=a.rootfs.resolve():
        dest.unlink(missing_ok=True)
        try:dest.hardlink_to(a.rootfs.resolve())
        except OSError:shutil.copy2(a.rootfs,dest)
    if not a.skip_userdata:
        initial=max(12<<30,((layout['rootfs_size']+(2<<30)+(1<<30)-1)//(1<<30))*(1<<30))
        layout['initial_size']=initial
        data=out/'data-root'
        if data.exists():shutil.rmtree(data)
        base=data/layout_path;base.mkdir(parents=True)
        (base/'rootfs.erofs').hardlink_to(dest)
        (base/'build-id').write_text(build_id+'\n');(base/'upper').mkdir();(base/'work').mkdir()
        # A separate scratch filesystem avoids keeping two expanded images on
        # the output volume while converting to Android sparse format.
        scratch=tempfile.TemporaryDirectory(prefix='houji-userdata-')
        raw=Path(scratch.name)/'userdata.ext4'
        with raw.open('wb') as stream:stream.truncate(initial)
        run('mke2fs','-t','ext4','-F','-b','4096','-m','0','-L',layout['filesystem_label'],
            '-U',layout['filesystem_uuid'],'-E','root_owner=0:0,lazy_itable_init=0,lazy_journal_init=0','-d',data,raw)
        # mke2fs -d preserves input ownership; these build-generated files are root-owned.
        for path in [*data.rglob('*')]:
            relative='/'+str(path.relative_to(data))
            for field in ['uid','gid']:run('debugfs','-w','-R','set_inode_field '+relative+' '+field+' 0',raw)
        run('e2fsck','-fn',raw);run('img2simg',raw,out/'userdata.img')
        scratch.cleanup();shutil.rmtree(data)
    manifest=json.loads((out/'images.json').read_text())
    names=['boot.img','init_boot.img','vendor_boot.img','dtbo.img','vbmeta.img']
    if not a.skip_userdata:names.append('userdata.img')
    manifest.update(layout=layout,root_transport='internal userdata ext4',automatic_reboot=False,
                    programmed_return_seconds=None,validation={'physical_boot_verified':False})
    manifest['images']={name:{'size':(out/name).stat().st_size,'sha256':sha(out/name)} for name in names}
    (out/'images.json').write_text(json.dumps(manifest,indent=2)+'\n')
    (out/'SHA256SUMS').write_text(''.join(v['sha256']+'  '+n+'\n' for n,v in manifest['images'].items())+root_hash+'  rootfs.erofs\n')
    shutil.copy2(PORT/'install/flash-internal.py',out/'flash-internal.py')
    print('Images ready for installer preflight:',out)


if __name__=='__main__':main()
