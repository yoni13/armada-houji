#!/usr/bin/env python3
"""make-userdata.py builds the fresh-install filesystem from a bundle's root image.

These tests run the real mke2fs, debugfs, e2fsck and img2simg on a small image and
then read the result back, so they check what would actually be flashed.
"""
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from unittest import mock

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'ports/houji/install/make-userdata.py'
loader = importlib.machinery.SourceFileLoader('make_userdata', str(SCRIPT))
spec = importlib.util.spec_from_loader('make_userdata', loader)
mud = importlib.util.module_from_spec(spec)
loader.exec_module(mud)

HAVE_TOOLS = all(shutil.which(tool) for tool in (*mud.TOOLS, 'simg2img', 'dumpe2fs'))
BUILD_ID = 'aaaaaaaaaaaa-bbbbbbbbbbbb'
GIB = 1 << 30


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Workspace(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        patch = mock.patch('sys.stdout', io.StringIO())
        patch.start()
        self.addCleanup(patch.stop)

    def bundle(self, size=3 << 20, initial=128 << 20, content=None):
        content = content if content is not None else bytes(range(256)) * (size // 256)
        directory = self.root / 'bundle'
        directory.mkdir(exist_ok=True)
        (directory / 'rootfs.erofs').write_bytes(content)
        layout = {'directory': 'armada/' + BUILD_ID, 'build_id': BUILD_ID,
                  'filesystem_label': 'ARMADA_HOUJI_RW', 'filesystem_uuid': str(uuid.uuid4()),
                  'rootfs_sha256': sha(content), 'rootfs_size': len(content), 'initial_size': initial}
        (directory / 'images.json').write_text(json.dumps({'device': 'xiaomi,houji', 'layout': layout}))
        return directory, layout, content


class SizeTests(unittest.TestCase):
    def test_small_roots_get_the_twelve_gib_minimum(self):
        self.assertEqual(mud.initial_size(1), 12 * GIB)
        self.assertEqual(mud.initial_size(7554920448), 12 * GIB)  # the current root image

    def test_large_roots_get_two_gib_of_headroom_rounded_up(self):
        self.assertEqual(mud.initial_size(11 * GIB), 13 * GIB)
        self.assertEqual(mud.initial_size(11 * GIB + 1), 14 * GIB)

    def test_it_matches_the_size_published_in_a_real_bundle(self):
        manifest = ROOT / 'output/houji/images/images.json'
        if not manifest.is_file():
            self.skipTest('no built bundle here')
        layout = json.loads(manifest.read_text())['layout']
        self.assertEqual(mud.initial_size(layout['rootfs_size']), layout['initial_size'])


@unittest.skipUnless(HAVE_TOOLS, 'needs e2fsprogs and the Android sparse tools')
class BuildTests(Workspace):
    def inspect(self, bundle, layout):
        raw = self.root / 'raw.ext4'
        subprocess.run(['simg2img', str(bundle / 'userdata.img'), str(raw)], check=True)
        directory = layout['directory']

        def debugfs(command):
            return subprocess.run(['debugfs', '-R', command, str(raw)], capture_output=True, text=True,
                                  check=True).stdout
        return raw, debugfs

    def test_the_image_holds_the_root_image_in_the_expected_layout_owned_by_root(self):
        bundle, layout, content = self.bundle()
        image = mud.build_userdata(bundle, layout)
        self.assertEqual(image, bundle / 'userdata.img')
        raw, debugfs = self.inspect(bundle, layout)
        base = '/' + layout['directory']
        self.assertEqual(raw.stat().st_size, layout['initial_size'])
        self.assertEqual(debugfs('cat %s/build-id' % base).strip(), BUILD_ID)
        dumped = self.root / 'dumped'
        debugfs('dump %s/rootfs.erofs %s' % (base, dumped))
        self.assertEqual(dumped.read_bytes(), content)
        for name in ('rootfs.erofs', 'build-id', 'upper', 'work', ''):
            stat = debugfs('stat %s/%s' % (base, name)).replace('\n', ' ')
            self.assertRegex(stat, r'User:\s+0\s+Group:\s+0', name)
        header = subprocess.run(['dumpe2fs', '-h', str(raw)], capture_output=True, text=True).stdout
        self.assertIn('ARMADA_HOUJI_RW', header)
        self.assertIn(layout['filesystem_uuid'], header)
        self.assertEqual(subprocess.run(['e2fsck', '-fn', str(raw)], capture_output=True).returncode, 0)

    def test_the_output_is_android_sparse_of_the_declared_size(self):
        bundle, layout, _ = self.bundle()
        mud.build_userdata(bundle, layout)
        header = (bundle / 'userdata.img').read_bytes()[:28]
        import struct
        magic, major, minor, header_size, chunk_size, block, blocks, chunks, crc = struct.unpack('<I4H4I', header)
        self.assertEqual((magic, major, block, header_size, chunk_size), (0xED26FF3A, 1, 4096, 28, 12))
        self.assertEqual(blocks * block, layout['initial_size'])

    def test_the_sidecar_describes_exactly_what_was_built(self):
        bundle, layout, _ = self.bundle()
        mud.build_userdata(bundle, layout)
        info = json.loads((bundle / 'userdata.json').read_text())
        data = (bundle / 'userdata.img').read_bytes()
        self.assertEqual(info, {'size': len(data), 'sha256': sha(data), 'initial_size': layout['initial_size'],
                                'build_id': BUILD_ID, 'rootfs_sha256': layout['rootfs_sha256'],
                                'filesystem_uuid': layout['filesystem_uuid']})

    def test_no_scratch_files_are_left_in_the_bundle(self):
        bundle, layout, _ = self.bundle()
        mud.build_userdata(bundle, layout)
        self.assertEqual(sorted(p.name for p in bundle.iterdir()),
                         ['images.json', 'rootfs.erofs', 'userdata.img', 'userdata.json'])

    def test_the_root_image_is_not_modified_or_duplicated_by_hard_linking(self):
        bundle, layout, content = self.bundle()
        before = (bundle / 'rootfs.erofs').stat()
        mud.build_userdata(bundle, layout)
        after = (bundle / 'rootfs.erofs').stat()
        self.assertEqual((before.st_ino, after.st_nlink, (bundle / 'rootfs.erofs').read_bytes()),
                         (after.st_ino, 1, content))

    def test_it_still_works_when_hard_links_are_impossible(self):
        bundle, layout, content = self.bundle()
        with mock.patch.object(Path, 'hardlink_to', side_effect=OSError('cross-device')):
            mud.build_userdata(bundle, layout)
        raw, debugfs = self.inspect(bundle, layout)
        dumped = self.root / 'dumped'
        debugfs('dump /%s/rootfs.erofs %s' % (layout['directory'], dumped))
        self.assertEqual(dumped.read_bytes(), content)

    def test_running_it_again_replaces_the_previous_result(self):
        bundle, layout, _ = self.bundle()
        mud.build_userdata(bundle, layout)
        first = json.loads((bundle / 'userdata.json').read_text())
        layout['filesystem_uuid'] = str(uuid.uuid4())
        mud.build_userdata(bundle, layout)
        second = json.loads((bundle / 'userdata.json').read_text())
        self.assertNotEqual(first['sha256'], second['sha256'])
        self.assertEqual(sorted(p.name for p in bundle.iterdir()),
                         ['images.json', 'rootfs.erofs', 'userdata.img', 'userdata.json'])

    def test_a_failure_part_way_leaves_no_image_and_no_sidecar(self):
        bundle, layout, _ = self.bundle()
        mud.build_userdata(bundle, layout)  # an older, complete result exists

        def explode(*args):
            if args[0] == 'e2fsck':
                raise subprocess.CalledProcessError(4, args[0])
            mud.run(*args)
        with self.assertRaises(subprocess.CalledProcessError):
            mud.build_userdata(bundle, layout, runner=explode)
        self.assertEqual(sorted(p.name for p in bundle.iterdir()), ['images.json', 'rootfs.erofs'])

    def test_the_scratch_directory_option_is_used_and_cleaned(self):
        bundle, layout, _ = self.bundle()
        scratch = self.root / 'scratch'
        scratch.mkdir()
        seen = []
        original = mud.run

        def spy(*args):
            if args[0] == 'mke2fs':
                seen.append(any(scratch in Path(str(a)).parents for a in args))
            original(*args)
        mud.build_userdata(bundle, layout, runner=spy, scratch_dir=scratch)
        self.assertEqual(seen, [True])
        self.assertEqual(list(scratch.iterdir()), [])

    def test_the_command_line_builds_in_the_given_directory(self):
        bundle, layout, _ = self.bundle()
        with mock.patch('sys.argv', ['make-userdata.py', str(bundle)]):
            mud.main()
        self.assertTrue((bundle / 'userdata.img').is_file())


class RefusalTests(Workspace):
    def test_a_root_image_that_does_not_match_the_manifest_is_refused(self):
        bundle, layout, content = self.bundle()
        corrupt = bytearray(content)
        corrupt[10] ^= 0xFF
        (bundle / 'rootfs.erofs').write_bytes(bytes(corrupt))
        with self.assertRaisesRegex(ValueError, 'checksum'):
            mud.build_userdata(bundle, layout)
        self.assertFalse((bundle / 'userdata.img').exists())

    def test_a_truncated_or_missing_root_image_is_refused(self):
        bundle, layout, content = self.bundle()
        (bundle / 'rootfs.erofs').write_bytes(content[:-1])
        with self.assertRaisesRegex(ValueError, 'size'):
            mud.build_userdata(bundle, layout)
        (bundle / 'rootfs.erofs').unlink()
        with self.assertRaisesRegex(ValueError, 'missing'):
            mud.build_userdata(bundle, layout)

    def test_a_filesystem_too_small_for_the_root_image_is_refused(self):
        bundle, layout, _ = self.bundle(initial=(3 << 20) + (1 << 20))
        with self.assertRaisesRegex(ValueError, 'room'):
            mud.build_userdata(bundle, layout)

    def test_missing_tools_are_named(self):
        bundle, layout, _ = self.bundle()
        with mock.patch.object(shutil, 'which', side_effect=lambda t: None if t == 'img2simg' else '/bin/' + t):
            with self.assertRaisesRegex(ValueError, 'img2simg'):
                mud.build_userdata(bundle, layout)

    def test_a_bundle_for_another_device_is_refused(self):
        bundle, layout, _ = self.bundle()
        (bundle / 'images.json').write_text(json.dumps({'device': 'other', 'layout': layout}))
        with mock.patch('sys.argv', ['make-userdata.py', str(bundle)]):
            with self.assertRaisesRegex(ValueError, 'Wrong device'):
                mud.main()


if __name__ == '__main__':
    unittest.main()
