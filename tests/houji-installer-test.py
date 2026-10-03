#!/usr/bin/env python3
"""Installer regression tests use a fake fastboot; they never access USB."""
import hashlib
import importlib.util
import stat
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

INSTALLER=Path(__file__).resolve().parents[1]/'ports/houji/install/flash-internal.py'


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.log=self.root/'commands.jsonl'
        boot=bytearray(4096);boot[:8]=b'ANDROID!';struct.pack_into('<I',boot,16,1);struct.pack_into('<I',boot,40,4)
        args=b'rdinit=/init clk_ignore_unused pd_ignore_unused regulator_ignore_unused'
        boot[44:44+len(args)]=args
        spec=importlib.util.spec_from_file_location('assemble',INSTALLER.with_name('assemble.py'))
        assemble=importlib.util.module_from_spec(spec);spec.loader.exec_module(assemble)
        elf=bytearray(64);elf[:6]=b'\x7fELF\x02\x01';struct.pack_into('<H',elf,18,183)
        self.ramdisk=assemble.cpio([('init',stat.S_IFREG|0o755,b'#!/bin/sh\n'),
                    ('bin/sh',stat.S_IFLNK|0o777,b'busybox'),('bin/busybox',stat.S_IFREG|0o755,bytes(elf))])
        init=bytearray(4096);init[:8]=b'ANDROID!';struct.pack_into('<I',init,40,4);struct.pack_into('<I',init,12,len(self.ramdisk));init.extend(self.ramdisk)
        vendor=b'VNDRBOOT'+struct.pack('<I',4)+bytes(4096)
        vbmeta=bytearray(256);vbmeta[:4]=b'AVB0';struct.pack_into('>I',vbmeta,120,3)
        files={'boot.img':boot,'init_boot.img':init,'vendor_boot.img':vendor,'dtbo.img':b'dtbo','vbmeta.img':vbmeta,
               'userdata.img':struct.pack('<I4H4I',0xed26ff3a,1,0,28,12,4096,1024,0,0)}
        self.manifest={'device':'xiaomi,houji','root_transport':'internal userdata ext4','automatic_reboot':False,
                       'layout':{'initial_size':4<<20,'build_id':'abc','rootfs_sha256':'def','filesystem_uuid':'uuid-1'},'images':{}}
        for name,data in files.items():
            (self.root/name).write_bytes(data)
            self.manifest['images'][name]={'size':len(data),'sha256':hashlib.sha256(data).hexdigest()}
        self.save()
        self.fb=self.root/'fastboot'
        self.fb.write_text('''#!/usr/bin/env python3
import json,os,sys
args=sys.argv[3:]
with open(os.environ['FAKE_LOG'],'a') as f:f.write(json.dumps(args)+'\\n')
key=' '.join(args)
values={'getvar product':'product: '+os.environ.get('FAKE_PRODUCT','houji'),
'getvar is-userspace':'is-userspace: no','getvar snapshot-update-status':'snapshot-update-status: none',
'oem device-info':'Device unlocked: '+os.environ.get('FAKE_UNLOCKED','true')}
if key in values:print(values[key])
elif key.startswith('getvar partition-size:'):print(args[1]+': '+os.environ.get('FAKE_SIZE','0x4000000000'))
else:print('OKAY')
''');self.fb.chmod(0o755)

    def save(self):(self.root/'images.json').write_text(json.dumps(self.manifest))

    def invoke(self,*args,**env):
        settings=os.environ.copy();settings.update(FAKE_LOG=str(self.log),**env)
        return subprocess.run([sys.executable,'-O',str(INSTALLER),'--images-dir',str(self.root),
                               '--serial','TEST','--fastboot',str(self.fb),*args],env=settings,capture_output=True,text=True)

    def commands(self):
        return [json.loads(x) for x in self.log.read_text().splitlines()] if self.log.exists() else []

    def no_writes(self):
        self.assertFalse(any(any(x in command for x in ['flash','erase','set_active','reboot']) for command in self.commands()))

    def test_check_only(self):
        r=self.invoke('--check-only');self.assertEqual(r.returncode,0,r.stderr);self.no_writes()

    def test_locked_refuses_erase(self):
        self.assertNotEqual(self.invoke('--erase-userdata',FAKE_UNLOCKED='false').returncode,0);self.no_writes()

    def test_wrong_device(self):
        self.assertNotEqual(self.invoke('--erase-userdata',FAKE_PRODUCT='other').returncode,0);self.no_writes()

    def test_small_partition(self):
        self.assertNotEqual(self.invoke('--erase-userdata',FAKE_SIZE='0x100').returncode,0);self.no_writes()

    def test_corrupt_image(self):
        with (self.root/'boot.img').open('ab') as f:f.write(b'bad')
        self.assertNotEqual(self.invoke('--erase-userdata').returncode,0);self.assertEqual(self.commands(),[])

    def test_missing_boot_argument_even_with_valid_hash(self):
        path=self.root/'boot.img';data=path.read_bytes().replace(b'rdinit=/init',b'noinit=/foo ');path.write_bytes(data)
        self.manifest['images']['boot.img']['sha256']=hashlib.sha256(data).hexdigest();self.save()
        self.assertNotEqual(self.invoke('--erase-userdata').returncode,0);self.assertEqual(self.commands(),[])

    def test_missing_ramdisk_interpreter(self):
        spec=importlib.util.spec_from_file_location('assemble',INSTALLER.with_name('assemble.py'))
        assemble=importlib.util.module_from_spec(spec);spec.loader.exec_module(assemble)
        packed=assemble.cpio([('init',stat.S_IFREG|0o755,b'#!/bin/sh\n')])
        path=self.root/'init_boot.img';data=bytearray(path.read_bytes()[:4096]);struct.pack_into('<I',data,12,len(packed));data.extend(packed);path.write_bytes(data)
        self.manifest['images']['init_boot.img']={'size':len(data),'sha256':hashlib.sha256(data).hexdigest()};self.save()
        self.assertNotEqual(self.invoke('--erase-userdata').returncode,0);self.assertEqual(self.commands(),[])

    def test_unprepared_vbmeta(self):
        path=self.root/'vbmeta.img';data=bytearray(path.read_bytes());struct.pack_into('>I',data,120,0);path.write_bytes(data)
        self.manifest['images']['vbmeta.img']['sha256']=hashlib.sha256(data).hexdigest();self.save()
        self.assertNotEqual(self.invoke('--erase-userdata').returncode,0);self.assertEqual(self.commands(),[])

    def test_fresh_write_order(self):
        r=self.invoke('--erase-userdata');self.assertEqual(r.returncode,0,r.stderr)
        writes=[c for c in self.commands() if c[0] in ['erase','flash','set_active','reboot']]
        self.assertEqual(writes[0],['erase','userdata']);self.assertEqual(writes[1][:2],['flash','userdata'])
        self.assertEqual(writes[-2:], [['set_active','b'],['reboot']])

    def test_update_requires_receipt(self):
        self.assertNotEqual(self.invoke('--boot-only').returncode,0);self.no_writes()

    def test_update_never_writes_userdata(self):
        receipt=self.root/'receipt.json';receipt.write_text(json.dumps({'build_id':'abc','rootfs_sha256':'def',
              'serial_sha256':hashlib.sha256(b'TEST').hexdigest()}))
        r=self.invoke('--boot-only','--staged-receipt',str(receipt));self.assertEqual(r.returncode,0,r.stderr)
        self.assertFalse(any('userdata' in c or 'erase' in c for c in self.commands()))

    def test_receipt_device_mismatch(self):
        receipt=self.root/'receipt.json';receipt.write_text(json.dumps({'build_id':'abc','rootfs_sha256':'def','serial_sha256':'wrong'}))
        self.assertNotEqual(self.invoke('--boot-only','--staged-receipt',str(receipt)).returncode,0);self.no_writes()


    def publish(self,**changes):
        """Make this a root-image-only bundle whose userdata.img was built locally."""
        data=(self.root/'userdata.img').read_bytes()
        del self.manifest['images']['userdata.img'];self.save()
        info={'size':len(data),'sha256':hashlib.sha256(data).hexdigest(),'initial_size':4<<20,
              'build_id':'abc','rootfs_sha256':'def','filesystem_uuid':'uuid-1'}
        info.update(changes);(self.root/'userdata.json').write_text(json.dumps(info))

    def test_published_bundle_needs_a_locally_built_userdata(self):
        self.publish();(self.root/'userdata.json').unlink()
        r=self.invoke('--erase-userdata');self.assertNotEqual(r.returncode,0)
        self.assertIn('make-userdata.py',r.stderr);self.assertEqual(self.commands(),[])

    def test_published_bundle_without_the_image_file_is_refused(self):
        self.publish();(self.root/'userdata.img').unlink()
        r=self.invoke('--erase-userdata');self.assertNotEqual(r.returncode,0)
        self.assertIn('make-userdata.py',r.stderr);self.assertEqual(self.commands(),[])

    def test_locally_built_userdata_is_flashed_in_the_usual_order(self):
        self.publish();r=self.invoke('--erase-userdata');self.assertEqual(r.returncode,0,r.stderr)
        writes=[c for c in self.commands() if c[0] in ['erase','flash','set_active','reboot']]
        self.assertEqual(writes[0],['erase','userdata']);self.assertEqual(writes[1],['flash','userdata',str(self.root/'userdata.img')])
        self.assertEqual(writes[-2:],[['set_active','b'],['reboot']])

    def test_userdata_built_from_another_root_image_is_refused(self):
        for change in ({'build_id':'other'},{'rootfs_sha256':'other'}):
            with self.subTest(change):
                self.publish(**change);r=self.invoke('--erase-userdata')
                self.assertNotEqual(r.returncode,0);self.assertIn('different root image',r.stderr);self.assertEqual(self.commands(),[])
                self.manifest['images']['userdata.img']={};self.save();(self.root/'userdata.img').write_bytes(
                    struct.pack('<I4H4I',0xed26ff3a,1,0,28,12,4096,1024,0,0))

    def test_userdata_with_another_or_missing_filesystem_identity_is_refused(self):
        for label,change in (('other',{'filesystem_uuid':'uuid-2'}),('missing',None)):
            with self.subTest(label):
                self.publish(**(change or {}))
                if change is None:
                    info=json.loads((self.root/'userdata.json').read_text());del info['filesystem_uuid']
                    (self.root/'userdata.json').write_text(json.dumps(info))
                r=self.invoke('--erase-userdata');self.assertNotEqual(r.returncode,0)
                self.assertIn('filesystem identity',r.stderr);self.assertEqual(self.commands(),[])
                self.manifest['images']['userdata.img']={};self.save()

    def test_corrupt_locally_built_userdata_is_refused_even_at_the_same_size(self):
        self.publish();path=self.root/'userdata.img';data=bytearray(path.read_bytes());data[-1]^=0xFF;path.write_bytes(data)
        r=self.invoke('--erase-userdata');self.assertNotEqual(r.returncode,0);self.assertIn('checksum',r.stderr);self.assertEqual(self.commands(),[])

    def test_truncated_locally_built_userdata_is_refused(self):
        self.publish();path=self.root/'userdata.img';path.write_bytes(path.read_bytes()[:-3])
        r=self.invoke('--erase-userdata');self.assertNotEqual(r.returncode,0);self.assertEqual(self.commands(),[])

    def test_locally_built_userdata_of_another_size_is_refused(self):
        self.publish(initial_size=8<<20);r=self.invoke('--erase-userdata')
        self.assertNotEqual(r.returncode,0);self.assertEqual(self.commands(),[])

    def test_update_of_a_published_bundle_needs_no_userdata_at_all(self):
        self.publish();(self.root/'userdata.json').unlink();(self.root/'userdata.img').unlink()
        receipt=self.root/'receipt.json';receipt.write_text(json.dumps({'build_id':'abc','rootfs_sha256':'def',
              'serial_sha256':hashlib.sha256(b'TEST').hexdigest()}))
        r=self.invoke('--boot-only','--staged-receipt',str(receipt));self.assertEqual(r.returncode,0,r.stderr)
        self.assertFalse(any('userdata' in c or 'erase' in c for c in self.commands()))


if __name__=='__main__':unittest.main()
