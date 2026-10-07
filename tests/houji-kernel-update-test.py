#!/usr/bin/env python3
"""Kernel-only updates: packaging, the phone-side installer and the host flasher.

Nothing here touches a device, USB or the real /usr/lib/modules. Archives are
built with the real tar and zstd. The install tests that matter most run the real
modinfo and depmod on genuine ELF module objects (compiled here), because mocking
them once hid a bug that broke every first install. fastboot is replaced.
"""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
PORT = ROOT / 'ports/houji'
sys.path.insert(0, str(PORT))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


buildlib = load('buildlib_under_test', PORT / 'buildlib.py')
package = load('package_kernel_update', PORT / 'install/package-kernel-update.py')
phone = load('install_kernel_update', PORT / 'install/install-kernel-update.py')
FLASH = PORT / 'install/flash-internal.py'
RELEASE = '7.2.6-armada-houji-k1234abcd'
BASE_RELEASE = '7.2.6-armada-houji'
REAL_TOOLS = all(shutil.which(tool) for tool in ('gcc', 'depmod', 'modinfo'))


def sha(data):
    return hashlib.sha256(data).hexdigest()


def real_module(path, release, name='x'):
    """A genuine ELF relocatable object with a .modinfo section, as modinfo and depmod read."""
    source = path.with_suffix('.c')
    source.write_text('''
__attribute__((section(".modinfo"), used)) static const char a[] = "name=%s";
__attribute__((section(".modinfo"), used)) static const char b[] = "vermagic=%s SMP preempt mod_unload aarch64";
''' % (name, release))
    subprocess.run(['gcc', '-c', '-o', str(path), str(source)], check=True)
    source.unlink()


def module_tree(parent, release=RELEASE, content=b'module', real=False):
    """parent/modules/<release> with modules and depmod output."""
    if real:
        # depmod -b wants <base>/lib/modules/<release>; archives hold modules/<release>.
        tree = parent / 'lib/modules' / release
        (tree / 'kernel/drivers').mkdir(parents=True)
        (tree / 'extra').mkdir()
        real_module(tree / 'kernel/drivers/a.ko', release, 'a')
        real_module(tree / 'extra/houji-tcm-probe.ko', release, 'houji_tcm_probe')
        with (tree / 'kernel/drivers/a.ko').open('ab') as stream:   # distinguishes builds; ELF ignores trailing bytes
            stream.write(content)
        subprocess.run(['depmod', '-b', str(parent), release], check=True, capture_output=True)
        shutil.move(str(parent / 'lib/modules'), str(parent / 'modules'))
        return parent / 'modules' / release
    tree = parent / 'modules' / release
    (tree / 'kernel/drivers').mkdir(parents=True)
    (tree / 'extra').mkdir()
    (tree / 'kernel/drivers/a.ko').write_bytes(content + b'a')
    (tree / 'extra/houji-tcm-probe.ko').write_bytes(content + b'tcm')
    for name in ('modules.dep', 'modules.dep.bin', 'modules.alias.bin'):
        (tree / name).write_bytes(b'index')
    return tree


class Scratch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        patch = mock.patch('sys.stdout', io.StringIO())
        patch.start()
        self.addCleanup(patch.stop)


class StageModulesTests(Scratch):
    def make_work(self, release):
        work = self.root / 'work'
        tree = work / 'kernel' / ('staging-' + release) / 'lib/modules' / release
        (tree / 'kernel').mkdir(parents=True)
        (tree / 'kernel/in-tree.ko').write_bytes(b'x')
        (tree / 'build').symlink_to('/nonexistent/build')
        (tree / 'source').symlink_to('/nonexistent/source')
        for folder, name in buildlib.EXTRA_MODULES:
            (work / folder).mkdir(parents=True, exist_ok=True)
            (work / folder / name).write_bytes(name.encode())
        return work

    def test_in_tree_and_port_modules_are_staged_without_dangling_links(self):
        work = self.make_work(RELEASE)
        destination = self.root / 'out/lib/modules'
        buildlib.stage_modules(work, RELEASE, destination)
        tree = destination / RELEASE
        self.assertTrue((tree / 'kernel/in-tree.ko').is_file())
        for _, name in buildlib.EXTRA_MODULES:
            self.assertEqual((tree / 'extra' / name).read_bytes(), name.encode())
            self.assertEqual((tree / 'extra' / name).stat().st_mode & 0o777, 0o644)
        self.assertFalse((tree / 'build').exists() or (tree / 'build').is_symlink())
        self.assertFalse((tree / 'source').is_symlink())

    def test_an_existing_destination_is_replaced_not_merged(self):
        work = self.make_work(RELEASE)
        destination = self.root / 'out/lib/modules'
        (destination / 'stale').mkdir(parents=True)
        buildlib.stage_modules(work, RELEASE, destination)
        self.assertFalse((destination / 'stale').exists())

    def test_a_missing_port_module_is_an_error(self):
        work = self.make_work(RELEASE)
        (work / 'nfc-module/houji-nfc-power.ko').unlink()
        with self.assertRaises(FileNotFoundError):
            buildlib.stage_modules(work, RELEASE, self.root / 'out')


class PackagingTests(Scratch):
    def test_boot_critical_options_must_be_built_in(self):
        good = ''.join('CONFIG_%s=y\n' % name for name in package.BOOT_CRITICAL)
        self.assertEqual(package.boot_critical_problems(good), [])
        as_module = good.replace('CONFIG_EROFS_FS=y', 'CONFIG_EROFS_FS=m')
        missing = good.replace('CONFIG_SCSI_UFS_QCOM=y\n', '# CONFIG_SCSI_UFS_QCOM is not set\n')
        self.assertEqual(package.boot_critical_problems(as_module), ['EROFS_FS'])
        self.assertEqual(package.boot_critical_problems(missing), ['SCSI_UFS_QCOM'])

    def test_a_missing_ufs_phy_or_ramdisk_decompressor_is_caught(self):
        good = ''.join('CONFIG_%s=y\n' % name for name in package.BOOT_CRITICAL)
        for option in ('PHY_QCOM_QMP_UFS', 'RD_GZIP', 'EROFS_FS_ZIP', 'VT_CONSOLE', 'BLK_DEV_INITRD'):
            with self.subTest(option):
                broken = good.replace('CONFIG_%s=y\n' % option, '')
                self.assertEqual(package.boot_critical_problems(broken), [option])

    def test_every_filesystem_the_init_script_mounts_is_covered(self):
        init = (PORT / 'install/init').read_text()
        mounted = set(re.findall(r'mount -t (\w+)', init))
        covered = {'proc': 'PROC_FS', 'sysfs': 'SYSFS', 'devtmpfs': 'DEVTMPFS', 'tmpfs': 'TMPFS',
                   'ext4': 'EXT4_FS', 'erofs': 'EROFS_FS', 'overlay': 'OVERLAY_FS', 'vfat': 'VFAT_FS',
                   'devpts': None}  # devpts needs no option of its own here
        self.assertEqual(mounted - set(covered), set(), 'init mounts a filesystem this test does not know')
        for kind in mounted:
            if covered[kind]:
                self.assertIn(covered[kind], package.BOOT_CRITICAL, kind)
        self.assertIn('loop', init)
        self.assertIn('BLK_DEV_LOOP', package.BOOT_CRITICAL)

    def test_the_list_passes_on_a_real_known_good_kernel_config(self):
        for candidate in ('output/houji/build/kernel/kernel.config',
                          'output/houji-20260926/build/kernel/kernel.config'):
            config = ROOT / candidate
            if config.is_file():
                self.assertEqual(package.boot_critical_problems(config.read_text()), [])
                return
        self.skipTest('no built kernel config here')

    def test_an_image_must_name_exactly_its_release(self):
        image = b'\0Linux version ' + RELEASE.encode() + b' (builder@host) #1 SMP\0'
        self.assertTrue(package.image_is_release(image, RELEASE))
        self.assertFalse(package.image_is_release(image, RELEASE + 'x'))
        self.assertFalse(package.image_is_release(image, RELEASE[:-1]))  # a prefix must not match
        self.assertFalse(package.image_is_release(image, BASE_RELEASE))

    def test_output_is_only_ever_replaced_when_it_is_a_bundle_or_empty(self):
        precious = self.root / 'precious'
        precious.mkdir()
        (precious / 'thesis.txt').write_text('keep me')
        with self.assertRaisesRegex(ValueError, 'not a kernel update bundle'):
            package.prepare_output(precious)
        self.assertEqual((precious / 'thesis.txt').read_text(), 'keep me')
        bundle = self.root / 'bundle'
        bundle.mkdir()
        (bundle / 'kernel-update.json').write_text('{}')
        (bundle / 'old.bin').write_text('x')
        package.prepare_output(bundle)
        self.assertEqual(list(bundle.iterdir()), [])
        empty = self.root / 'empty'
        empty.mkdir()
        package.prepare_output(empty)
        package.prepare_output(self.root / 'new/place')
        self.assertTrue((self.root / 'new/place').is_dir())

    def test_modules_built_for_another_release_are_rejected(self):
        tree = module_tree(self.root)
        with mock.patch.object(package, 'vermagics', lambda paths: [RELEASE, '7.2.6-other'][:len(paths)] +
                               [RELEASE] * max(0, len(paths) - 2)):
            with self.assertRaisesRegex(ValueError, 'not built for'):
                package.check_modules(tree, RELEASE)

    def test_matching_modules_are_counted(self):
        tree = module_tree(self.root)
        with mock.patch.object(package, 'vermagics', lambda paths: [RELEASE] * len(paths)):
            self.assertEqual(package.check_modules(tree, RELEASE), 2)

    @unittest.skipUnless(REAL_TOOLS, 'needs gcc, depmod and modinfo')
    def test_the_packagers_module_check_works_with_the_real_modinfo(self):
        tree = module_tree(self.root, real=True)
        self.assertEqual(package.check_modules(tree, RELEASE), 2)
        with self.assertRaisesRegex(ValueError, 'not built for'):
            package.check_modules(tree, RELEASE + '-other')

    def test_missing_depmod_output_is_rejected(self):
        tree = module_tree(self.root)
        (tree / 'modules.dep.bin').unlink()
        with mock.patch.object(package, 'vermagics', lambda paths: [RELEASE] * len(paths)):
            with self.assertRaisesRegex(ValueError, 'modules.dep.bin'):
                package.check_modules(tree, RELEASE)

    def test_a_tree_without_modules_is_rejected(self):
        (self.root / 'empty').mkdir()
        with self.assertRaisesRegex(ValueError, 'no modules'):
            package.check_modules(self.root / 'empty', RELEASE)

    def test_the_archive_is_reproducible(self):
        module_tree(self.root / 'a')
        shutil.copytree(self.root / 'a', self.root / 'b')
        os.utime(self.root / 'b' / 'modules' / RELEASE / 'kernel/drivers/a.ko', (12345, 12345))
        package.make_archive(self.root / 'a', self.root / 'a.tar.zst')
        package.make_archive(self.root / 'b', self.root / 'b.tar.zst')
        self.assertEqual(sha((self.root / 'a.tar.zst').read_bytes()), sha((self.root / 'b.tar.zst').read_bytes()))

    def test_what_the_packager_builds_the_phone_accepts(self):
        module_tree(self.root / 'tree')
        package.make_archive(self.root / 'tree', self.root / 'm.tar.zst')
        self.assertGreater(phone.read_archive(self.root / 'm.tar.zst', RELEASE), 0)


def write_archive(path, members, padding=0):
    """members: [(name, type, data)] -> .tar.zst. Types: file, dir, symlink, hardlink, chardev, fifo.

    padding adds that many zero bytes after the end-of-archive marker, as tar does when it fills a block."""
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode='w') as tar:
        for name, kind, data in members:
            info = tarfile.TarInfo(name)
            if kind == 'dir':
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                tar.addfile(info)
            elif kind in ('symlink', 'hardlink'):
                info.type = tarfile.SYMTYPE if kind == 'symlink' else tarfile.LNKTYPE
                info.linkname = data
                tar.addfile(info)
            elif kind in ('chardev', 'fifo'):
                info.type = tarfile.CHRTYPE if kind == 'chardev' else tarfile.FIFOTYPE
                tar.addfile(info)
            else:
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    subprocess.run(['zstd', '-q', '-f', '-o', str(path)], input=raw.getvalue() + bytes(padding), check=True)


class ArchiveReaderTests(Scratch):
    def read(self, members, destination=None):
        archive = self.root / 'm.tar.zst'
        write_archive(archive, members)
        return phone.read_archive(archive, RELEASE, destination)

    def good(self):
        return [('modules', 'dir', None), ('modules/' + RELEASE, 'dir', None),
                ('modules/%s/a.ko' % RELEASE, 'file', b'data')]

    def test_a_proper_archive_extracts_and_reports_its_size(self):
        destination = self.root / 'out'
        destination.mkdir()
        self.assertEqual(self.read(self.good(), destination), 4)
        self.assertEqual((destination / 'modules' / RELEASE / 'a.ko').read_bytes(), b'data')

    def test_padding_after_the_end_marker_is_not_corruption(self):
        # tarfile stops at the end marker. If zstd is still writing the padding behind it
        # (more than a pipe buffer's worth here), it must not turn into "corrupt".
        archive = self.root / 'm.tar.zst'
        write_archive(archive, self.good(), padding=8 << 20)
        for _ in range(5):
            self.assertEqual(phone.read_archive(archive, RELEASE), 4)
        destination = self.root / 'out'
        destination.mkdir()
        self.assertEqual(phone.read_archive(archive, RELEASE, destination), 4)

    def test_hostile_archives_are_rejected_and_never_create_the_hostile_path(self):
        cases = {
            'traversal': [('modules/%s/../../etc/passwd' % RELEASE, 'file', b'x')],
            'absolute': [('/etc/passwd', 'file', b'x')],
            'another release': [('modules/%s/a.ko' % BASE_RELEASE, 'file', b'x')],
            'outside modules': [('etc/shadow', 'file', b'x')],
            'a symlink': [('modules/%s/link' % RELEASE, 'symlink', '/etc/shadow')],
            'a hard link': [('modules/%s/link' % RELEASE, 'hardlink', 'modules/%s/a.ko' % RELEASE)],
            'a prefix lookalike': [('modules/%s-evil/a.ko' % RELEASE, 'file', b'x')],
            'a device node': [('modules/%s/null' % RELEASE, 'chardev', None)],
            'a fifo': [('modules/%s/pipe' % RELEASE, 'fifo', None)],
        }
        hostile = ('passwd', 'shadow', 'link', 'null', 'pipe')
        for label, members in cases.items():
            with self.subTest(label):
                destination = self.root / ('out-' + label.replace(' ', '-'))
                destination.mkdir()
                with self.assertRaises(ValueError):
                    self.read(self.good()[:2] + members, destination)
                self.assertEqual([p for p in self.root.rglob('*') if p.name in hostile or p.name.endswith('-evil')], [])

    def test_an_empty_archive_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'empty'):
            self.read([('modules', 'dir', None), ('modules/' + RELEASE, 'dir', None)])

    def test_a_truncated_archive_is_rejected(self):
        archive = self.root / 'm.tar.zst'
        write_archive(archive, self.good() + [('modules/%s/b.ko' % RELEASE, 'file', os.urandom(300000))])
        archive.write_bytes(archive.read_bytes()[:-2000])
        with self.assertRaises((ValueError, tarfile.TarError, OSError)):
            phone.read_archive(archive, RELEASE)

    def test_unpacked_size_is_capped(self):
        with mock.patch.object(phone, 'MAX_UNPACKED', 3):
            with self.assertRaisesRegex(ValueError, 'more than expected'):
                self.read(self.good())

    def test_extracted_files_never_take_the_archives_owner_or_special_bits(self):
        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode='w') as tar:
            for name, kind in (('modules', 'dir'), ('modules/' + RELEASE, 'dir')):
                info = tarfile.TarInfo(name)
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            info = tarfile.TarInfo('modules/%s/a.ko' % RELEASE)
            info.size = 1
            info.mode = 0o4777
            info.uid = info.gid = 12345
            tar.addfile(info, io.BytesIO(b'x'))
        archive = self.root / 'm.tar.zst'
        subprocess.run(['zstd', '-q', '-f', '-o', str(archive)], input=raw.getvalue(), check=True)
        destination = self.root / 'out'
        destination.mkdir()
        phone.read_archive(archive, RELEASE, destination)
        stat = (destination / 'modules' / RELEASE / 'a.ko').stat()
        self.assertEqual(stat.st_mode & 0o7000, 0)
        self.assertNotEqual(stat.st_uid, 12345)


def kernel_images(root, release=RELEASE, boot_args=b'rdinit=/init clk_ignore_unused pd_ignore_unused regulator_ignore_unused'):
    boot = bytearray(4096)
    boot[:8] = b'ANDROID!'
    struct.pack_into('<I', boot, 16, 1)
    struct.pack_into('<I', boot, 40, 4)
    boot[44:44 + len(boot_args)] = boot_args
    boot += b'\0Linux version ' + release.encode() + b' (builder@host) #1 SMP\0'
    (root / 'boot.img').write_bytes(bytes(boot))
    (root / 'vendor_boot.img').write_bytes(b'VNDRBOOT' + struct.pack('<I', 4) + bytes(4096))


def make_bundle(root, release=RELEASE, content=b'module', boot_args=None, real=False, boot_release=None):
    root.mkdir(parents=True, exist_ok=True)
    kernel_images(root, boot_release or release, **({'boot_args': boot_args} if boot_args else {}))
    staging = root.parent / ('stage-' + root.name)
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir()
    module_tree(staging, release, content, real=real)
    package.make_archive(staging, root / 'modules.tar.zst')
    files = {n: {'size': (root / n).stat().st_size, 'sha256': sha((root / n).read_bytes())}
             for n in ('boot.img', 'vendor_boot.img', 'modules.tar.zst')}
    manifest = {'format': 1, 'kind': 'kernel-update', 'device': 'xiaomi,houji',
                'kernel_release': release, 'module_count': 2, 'files': files}
    (root / 'kernel-update.json').write_text(json.dumps(manifest))
    return manifest


class PhoneBase(Scratch):
    """A sandboxed phone: patched paths, root user, an installed base kernel tree."""
    real = False

    def setUp(self):
        super().setUp()
        self.bundle = self.root / 'bundle'
        self.manifest = make_bundle(self.bundle, real=self.real)
        self.sandbox = self.root / 'phone'
        self.lib = self.sandbox / 'usr/lib'
        (self.lib / 'modules' / BASE_RELEASE).mkdir(parents=True)  # the running kernel's tree
        os.symlink('usr/lib', self.sandbox / 'lib')                 # as on the phone, so modinfo -b / works
        (self.sandbox / 'lower').mkdir()
        self.model = self.root / 'model'
        self.model.write_bytes(b'Xiaomi 14\0')
        self.data = self.root / 'data'
        self.data.mkdir()
        patches = [mock.patch.object(phone, 'MODEL', str(self.model)),
                   mock.patch.object(phone, 'DATA', str(self.data)),
                   mock.patch.object(phone, 'MODULES', str(self.lib / 'modules')),
                   mock.patch.object(phone, 'LOWER', str(self.sandbox / 'lower')),
                   mock.patch.object(phone, 'running_release', return_value=BASE_RELEASE),
                   mock.patch.object(phone.os, 'getuid', return_value=0),
                   mock.patch.object(phone.os.path, 'ismount', return_value=True),
                   mock.patch.object(phone.os, 'sync')]
        if not self.real:
            patches.append(mock.patch.object(phone, 'check_tree'))
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_install(self, *extra, serial='ABC123', bundle=None):
        with mock.patch('sys.argv', ['x', str(bundle or self.bundle), '--serial', serial, *extra]):
            phone.main()

    def fails(self, *extra, **kwargs):
        try:
            self.run_install(*extra, **kwargs)
        except (ValueError, OSError) as error:
            return str(error)
        self.fail('the install was expected to be refused')

    def installed(self, release=RELEASE):
        return self.lib / 'modules' / release

    def leftovers(self):
        return sorted(p.name for p in self.lib.iterdir()) + sorted(
            p.name for p in (self.lib / 'modules').iterdir() if p.name != BASE_RELEASE and p.name.endswith('.replaced'))


class PhoneInstallTests(PhoneBase):
    def test_a_fresh_install_adds_the_tree_and_a_receipt(self):
        self.run_install()
        self.assertEqual((self.installed() / 'extra/houji-tcm-probe.ko').read_bytes(), b'moduletcm')
        receipt = json.loads((self.bundle / 'kernel-receipt.json').read_text())
        self.assertEqual(receipt['kernel_release'], RELEASE)
        self.assertEqual(receipt['boot_sha256'], self.manifest['files']['boot.img']['sha256'])
        self.assertEqual(receipt['vendor_boot_sha256'], self.manifest['files']['vendor_boot.img']['sha256'])
        self.assertEqual(receipt['modules_sha256'], self.manifest['files']['modules.tar.zst']['sha256'])
        self.assertEqual(receipt['serial_sha256'], hashlib.sha256(b'ABC123').hexdigest())
        self.assertNotIn('ABC123', (self.bundle / 'kernel-receipt.json').read_text())
        self.assertEqual((self.bundle / 'kernel-receipt.json').stat().st_mode & 0o777, 0o600)

    def test_the_running_kernels_modules_are_never_touched(self):
        marker = self.lib / 'modules' / BASE_RELEASE / 'keep'
        marker.write_text('x')
        self.run_install()
        self.assertEqual(marker.read_text(), 'x')

    def test_nothing_is_left_behind(self):
        self.run_install()
        self.assertEqual(self.leftovers(), ['modules'])

    def test_modes_are_normal_whatever_the_umask(self):
        previous = os.umask(0o077)
        try:
            self.run_install()
        finally:
            os.umask(previous)
        tree = self.installed()
        modes = {oct(p.stat().st_mode & 0o777) for p in tree.rglob('*') if p.is_dir()} | {oct(tree.stat().st_mode & 0o777)}
        self.assertEqual(modes, {'0o755'})
        self.assertEqual({oct(p.stat().st_mode & 0o777) for p in tree.rglob('*') if p.is_file()}, {'0o644'})

    def test_running_it_twice_is_harmless(self):
        self.run_install()
        before = phone.tree_digest(self.installed())
        self.run_install()
        self.assertEqual(phone.tree_digest(self.installed()), before)

    def test_check_only_installs_nothing(self):
        self.run_install('--check-only')
        self.assertFalse(self.installed().exists())
        self.assertFalse((self.bundle / 'kernel-receipt.json').exists())

    def test_a_modified_archive_is_refused(self):
        with (self.bundle / 'modules.tar.zst').open('ab') as stream:
            stream.write(b'tamper')
        self.assertIn('size mismatch', self.fails())
        self.assertFalse(self.installed().exists())

    def test_same_size_corruption_is_caught_by_the_checksum(self):
        archive = self.bundle / 'modules.tar.zst'
        data = bytearray(archive.read_bytes())
        data[len(data) // 2] ^= 0xFF
        archive.write_bytes(bytes(data))
        self.assertEqual(archive.stat().st_size, self.manifest['files']['modules.tar.zst']['size'])
        self.assertIn('checksum mismatch', self.fails())
        self.assertFalse(self.installed().exists())

    def test_wrong_handset_and_wrong_user_are_refused(self):
        self.model.write_bytes(b'Pixel 7a\0')
        self.assertIn('Wrong handset', self.fails())
        self.model.write_bytes(b'Xiaomi 14\0')
        with mock.patch.object(phone.os, 'getuid', return_value=1000):
            self.assertIn('root', self.fails())

    def test_a_bad_release_name_cannot_escape_the_modules_directory(self):
        manifest = json.loads((self.bundle / 'kernel-update.json').read_text())
        manifest['kernel_release'] = '../../etc'
        (self.bundle / 'kernel-update.json').write_text(json.dumps(manifest))
        self.assertIn('Invalid kernel release', self.fails())

    def test_a_failed_verification_leaves_nothing_installed(self):
        phone.check_tree.side_effect = ValueError('A module was not built')
        self.assertIn('not built', self.fails())
        self.assertFalse(self.installed().exists())
        self.assertEqual(self.leftovers(), ['modules'])
        self.assertFalse((self.bundle / 'kernel-receipt.json').exists())

    def test_a_failure_after_the_rename_removes_the_new_tree_again(self):
        calls = []

        def second_check_fails(release, directory, base=None):
            calls.append(base)
            if len(calls) == 2:
                raise ValueError('index does not resolve after install')
        phone.check_tree.side_effect = second_check_fails
        self.assertIn('does not resolve', self.fails())
        self.assertFalse(self.installed().exists())
        self.assertEqual(self.leftovers(), ['modules'])

    def test_the_staged_tree_is_checked_through_its_own_base(self):
        self.run_install()
        staged, final = phone.check_tree.call_args_list
        self.assertTrue(str(staged.kwargs['base']).startswith(str(self.lib)))   # looked up where it is staged
        self.assertEqual(final.kwargs['base'], self.sandbox)                    # then where it was installed

    def test_not_enough_free_space_is_refused_before_anything_is_extracted(self):
        usage = shutil.disk_usage(self.lib)
        with mock.patch.object(phone.shutil, 'disk_usage', return_value=usage._replace(free=1024)):
            self.assertIn('Not enough free space', self.fails())
        self.assertFalse(self.installed().exists())
        self.assertEqual(self.leftovers(), ['modules'])

    def test_staging_left_by_an_interrupted_run_is_cleaned_up(self):
        stale = self.lib / (phone.STAGING + '7.2.6-armada-houji-kold')
        (stale / 'lib/modules').mkdir(parents=True)
        (stale / 'lib/modules/junk').write_text('x' * 1000)
        self.run_install()
        self.assertFalse(stale.exists())

    def test_the_receipt_is_never_written_through_a_planted_link(self):
        victim = self.root / 'victim'
        victim.write_text('precious')
        (self.bundle / 'kernel-receipt.json').symlink_to(victim)
        self.fails()
        self.assertEqual(victim.read_text(), 'precious')

    def test_tree_digest_notices_content_and_name_changes(self):
        a, b, c = self.root / 'a', self.root / 'b', self.root / 'c'
        for tree, name, data in ((a, 'x', b'1'), (b, 'x', b'2'), (c, 'y', b'1')):
            tree.mkdir()
            (tree / name).write_bytes(data)
        self.assertEqual(len({phone.tree_digest(t) for t in (a, b, c)}), 3)


class ReplaceTests(PhoneBase):
    def setUp(self):
        super().setUp()
        self.run_install()
        self.other = self.root / 'other'
        make_bundle(self.other, content=b'changed')

    def read(self):
        return (self.installed() / 'kernel/drivers/a.ko').read_bytes()

    def test_different_modules_under_the_same_release_need_replace(self):
        self.assertIn('--replace', self.fails(bundle=self.other))
        self.assertEqual(self.read(), b'modulea')
        self.run_install('--replace', bundle=self.other)
        self.assertEqual(self.read(), b'changeda')
        self.assertEqual(self.leftovers(), ['modules'])

    def test_the_running_kernels_own_modules_are_never_replaced(self):
        with mock.patch.object(phone, 'running_release', return_value=RELEASE):
            self.assertIn('running kernel', self.fails('--replace', bundle=self.other))
        self.assertEqual(self.read(), b'modulea')

    def test_a_release_owned_by_the_root_image_is_never_replaced(self):
        (self.sandbox / 'lower/usr/lib/modules' / RELEASE).mkdir(parents=True)
        self.assertIn('root image', self.fails('--replace', bundle=self.other))
        self.assertEqual(self.read(), b'modulea')

    def test_a_failed_replacement_puts_the_old_tree_back(self):
        def reject_new(release, directory, base=None):
            if base == self.sandbox and self.read().endswith(b'a') and (self.installed() / 'kernel/drivers/a.ko').read_bytes() == b'changeda':
                raise ValueError('new tree rejected')
        phone.check_tree.side_effect = reject_new
        self.assertIn('rejected', self.fails('--replace', bundle=self.other))
        self.assertEqual(self.read(), b'modulea')
        self.assertEqual(self.leftovers(), ['modules'])

    def test_a_swap_interrupted_between_the_two_renames_is_finished_on_the_next_run(self):
        os.rename(self.installed(), self.installed().with_name(RELEASE + '.replaced'))
        self.assertFalse(self.installed().exists())
        self.run_install()                                   # same bundle again
        self.assertEqual(self.read(), b'modulea')
        self.assertEqual(self.leftovers(), ['modules'])

    def test_a_finished_swaps_surplus_old_tree_is_removed(self):
        surplus = self.installed().with_name(RELEASE + '.replaced')
        surplus.mkdir()
        (surplus / 'old').write_text('x')
        self.run_install()
        self.assertFalse(surplus.exists())
        self.assertEqual(self.read(), b'modulea')


@unittest.skipUnless(REAL_TOOLS, 'needs gcc, depmod and modinfo')
class RealToolInstallTests(PhoneBase):
    """modinfo and depmod run for real, on genuine ELF module objects."""
    real = True

    def test_the_first_install_of_a_release_that_is_not_installed_yet_works(self):
        # This is the case that mocking hid: modinfo -k <release> cannot find a tree that is not installed yet.
        self.assertFalse(self.installed().exists())
        self.run_install()
        self.assertTrue((self.installed() / 'extra/houji-tcm-probe.ko').is_file())
        self.assertTrue((self.bundle / 'kernel-receipt.json').is_file())
        self.assertEqual(self.leftovers(), ['modules'])

    def test_installing_it_again_is_a_no_op(self):
        self.run_install()
        self.run_install()
        self.assertEqual(self.leftovers(), ['modules'])

    def test_modules_built_for_another_release_are_refused(self):
        other = self.root / 'wrong'
        make_bundle(other, real=True)
        staging = self.root / 'stage-wrong' / 'modules' / RELEASE / 'extra/houji-tcm-probe.ko'
        real_module(staging, '7.2.6-someone-else', 'houji_tcm_probe')
        package.make_archive(self.root / 'stage-wrong', other / 'modules.tar.zst')
        manifest = json.loads((other / 'kernel-update.json').read_text())
        manifest['files']['modules.tar.zst'] = {'size': (other / 'modules.tar.zst').stat().st_size,
                                                'sha256': sha((other / 'modules.tar.zst').read_bytes())}
        (other / 'kernel-update.json').write_text(json.dumps(manifest))
        self.assertIn('not built for', self.fails(bundle=other))
        self.assertFalse(self.installed().exists())

    def test_a_replacement_whose_index_is_broken_is_rolled_back(self):
        self.run_install()
        broken = self.root / 'broken'
        make_bundle(broken, real=True, content=b'v2')
        staging = self.root / 'stage-broken' / 'modules' / RELEASE
        (staging / 'modules.dep.bin').unlink()
        package.make_archive(self.root / 'stage-broken', broken / 'modules.tar.zst')
        manifest = json.loads((broken / 'kernel-update.json').read_text())
        manifest['files']['modules.tar.zst'] = {'size': (broken / 'modules.tar.zst').stat().st_size,
                                                'sha256': sha((broken / 'modules.tar.zst').read_bytes())}
        (broken / 'kernel-update.json').write_text(json.dumps(manifest))
        before = phone.tree_digest(self.installed())
        self.assertIn('modules.dep.bin', self.fails('--replace', bundle=broken))
        self.assertEqual(phone.tree_digest(self.installed()), before)
        self.assertEqual(self.leftovers(), ['modules'])


FAKE_FASTBOOT = '''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[3:]
with open(os.environ['FAKE_LOG'], 'a') as f:
    f.write(json.dumps(args) + '\\n')
key = ' '.join(args)
values = {'getvar product': 'product: ' + os.environ.get('FAKE_PRODUCT', 'houji'),
          'getvar is-userspace': 'is-userspace: no',
          'getvar snapshot-update-status': 'snapshot-update-status: none',
          'getvar current-slot': 'current-slot: ' + os.environ.get('FAKE_SLOT', 'b'),
          'oem device-info': 'Device unlocked: ' + os.environ.get('FAKE_UNLOCKED', 'true')}
if key in values:
    print(values[key])
elif key.startswith('getvar partition-size:'):
    print(args[1] + ': ' + os.environ.get('FAKE_SIZE', '0x6000000'))
elif key.startswith('flash ') and args[1] == os.environ.get('FAKE_FAIL_FLASH'):
    print('FAILED (remote: flash failed)')
    sys.exit(1)
else:
    print('OKAY')
'''


class FlashTests(Scratch):
    def setUp(self):
        super().setUp()
        self.bundle = self.root / 'bundle'
        self.manifest = make_bundle(self.bundle)
        self.log = self.root / 'commands.jsonl'
        self.fastboot = self.root / 'fastboot'
        self.fastboot.write_text(FAKE_FASTBOOT)
        self.fastboot.chmod(0o755)
        self.receipt = self.root / 'receipt.json'
        self.write_receipt()

    def write_receipt(self, **changes):
        files = self.manifest['files']
        data = {'kernel_release': self.manifest['kernel_release'], 'boot_sha256': files['boot.img']['sha256'],
                'vendor_boot_sha256': files['vendor_boot.img']['sha256'],
                'modules_sha256': files['modules.tar.zst']['sha256'],
                'serial_sha256': hashlib.sha256(b'TEST').hexdigest()}
        data.update(changes)
        self.receipt.write_text(json.dumps(data))

    def flash(self, *args, receipt=True, mode='--kernel-only', **env):
        settings = os.environ.copy()
        settings.update(FAKE_LOG=str(self.log), **env)
        command = [sys.executable, '-O', str(FLASH), '--images-dir', str(self.bundle), '--serial', 'TEST',
                   '--fastboot', str(self.fastboot), '--reboot-wait', '0', mode, *args]
        if receipt:
            command += ['--kernel-receipt', str(self.receipt)]
        return subprocess.run(command, env=settings, capture_output=True, text=True)

    def commands(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def writes(self):
        return [c for c in self.commands() if c[0] in ('flash', 'erase', 'set_active', 'reboot', 'format')]

    def test_only_the_kernel_and_device_tree_are_written(self):
        result = self.flash()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(self.writes(), [['flash', 'boot_b', str(self.bundle / 'boot.img')],
                                         ['flash', 'vendor_boot_b', str(self.bundle / 'vendor_boot.img')],
                                         ['reboot']])

    def test_userdata_and_the_other_boot_partitions_are_never_touched(self):
        self.flash()
        touched = ' '.join(' '.join(c) for c in self.commands())
        for name in ('userdata', 'init_boot', 'dtbo', 'vbmeta', 'erase', 'set_active'):
            self.assertNotIn(name, touched)

    def test_check_only_writes_nothing(self):
        result = self.flash('--check-only')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.writes(), [])
        self.assertIn('Preflight passed', result.stdout)

    def test_a_receipt_is_required(self):
        result = self.flash(receipt=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('install-kernel-update.py', result.stderr)
        self.assertEqual(self.commands(), [])

    def test_a_receipt_for_anything_else_is_refused(self):
        for change, message in [({'kernel_release': '7.2.6-other'}, 'another kernel'),
                                ({'boot_sha256': '0' * 64}, 'boot.img'),
                                ({'vendor_boot_sha256': '0' * 64}, 'vendor_boot.img'),
                                ({'modules_sha256': '0' * 64}, 'modules.tar.zst'),
                                ({'serial_sha256': hashlib.sha256(b'OTHER').hexdigest()}, 'another phone')]:
            with self.subTest(change):
                self.write_receipt(**change)
                result = self.flash()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
                self.assertEqual(self.writes(), [])

    def test_slot_a_is_refused(self):
        result = self.flash(FAKE_SLOT='a')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('current-slot', result.stderr)
        self.assertEqual(self.writes(), [])

    def test_locked_bootloader_wrong_product_and_small_partition_are_refused(self):
        for env, message in [({'FAKE_UNLOCKED': 'false'}, 'device-info'), ({'FAKE_PRODUCT': 'other'}, 'product'),
                             ({'FAKE_SIZE': '0x1000'}, 'too small')]:
            with self.subTest(env):
                result = self.flash(**env)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
                self.assertEqual(self.writes(), [])

    def test_a_corrupt_or_wrong_image_is_refused_before_any_command(self):
        (self.bundle / 'boot.img').write_bytes(b'corrupt')
        result = self.flash()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.commands(), [])

    def test_a_kernel_without_the_required_boot_arguments_is_refused(self):
        self.manifest = make_bundle(self.bundle, boot_args=b'console=tty0')
        self.write_receipt()
        result = self.flash()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Missing boot argument', result.stderr)
        self.assertEqual(self.commands(), [])

    def test_a_boot_image_that_is_not_the_declared_release_is_refused(self):
        self.manifest = make_bundle(self.bundle, boot_release=BASE_RELEASE)   # manifest says RELEASE, image says another
        self.write_receipt()
        result = self.flash()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('is not kernel release', result.stderr)
        self.assertEqual(self.commands(), [])

    def test_a_boot_image_for_a_longer_release_name_does_not_pass_as_a_prefix(self):
        self.manifest = make_bundle(self.bundle, boot_release=RELEASE + 'x')
        self.write_receipt()
        self.assertNotEqual(self.flash().returncode, 0)
        self.assertEqual(self.commands(), [])

    def test_a_full_image_bundle_is_not_a_kernel_update(self):
        (self.bundle / 'kernel-update.json').unlink()
        (self.bundle / 'images.json').write_text('{}')
        self.assertNotEqual(self.flash().returncode, 0)
        self.assertEqual(self.commands(), [])

    def test_a_failed_second_flash_says_the_phone_is_half_updated(self):
        result = self.flash(FAKE_FAIL_FLASH='vendor_boot_b')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('boot_b was written', result.stdout + result.stderr)
        self.assertIn('old device tree', result.stdout + result.stderr)
        self.assertNotIn(['reboot'], self.commands())

    def test_a_failed_first_flash_makes_no_such_claim(self):
        result = self.flash(FAKE_FAIL_FLASH='boot_b')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('was written', result.stdout + result.stderr)
        self.assertEqual([c for c in self.commands() if c[:1] == ['flash']], [['flash', 'boot_b', str(self.bundle / 'boot.img')]])

    def test_it_cannot_be_combined_with_a_userdata_erase(self):
        result = self.flash('--erase-userdata')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.commands(), [])


if __name__ == '__main__':
    unittest.main()
