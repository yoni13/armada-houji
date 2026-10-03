#!/usr/bin/env python3
"""Install a kernel update's module tree on the phone. Run as root on the installed system.

This is the first half of a kernel-only update. It puts the new kernel's
modules beside the running ones (a different release name, so the running
kernel is unaffected) and writes kernel-receipt.json. Copy that file back to
the host, boot the phone to bootloader fastboot and run:

    flash-internal.py --kernel-only --images-dir BUNDLE --serial SERIAL \\
        --kernel-receipt kernel-receipt.json

The old kernel stays flashable from the previous bundle and its modules stay
installed, so the update can be undone. Userspace changes need a full root
image update instead (stage-update.py).
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile

MODEL = '/sys/firmware/devicetree/base/model'
DATA = '/run/houji/data'
MODULES = '/usr/lib/modules'
LOWER = '/run/houji/lower'   # the read-only root image, as mounted by the initramfs
FILES = ('boot.img', 'vendor_boot.img', 'modules.tar.zst')
RELEASE = re.compile(r'[0-9]+\.[0-9]+\.[0-9]+[A-Za-z0-9._+-]*')
STAGING = '.houji-kernel-update-'
MAX_UNPACKED = 2 << 30
SPARE = 512 << 20            # free space to keep beyond the unpacked modules


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def tree_digest(root):
    """Digest of every relative path and file content below root."""
    digest = hashlib.sha256()
    root = Path(root)
    for path in sorted(root.rglob('*')):
        digest.update(str(path.relative_to(root)).encode() + b'\0')
        if path.is_file() and not path.is_symlink():
            digest.update(sha(path).encode())
    return digest.hexdigest()


def running_release():
    return os.uname().release


def system_root():
    """The root holding lib/modules; '/' on the phone."""
    return Path(MODULES).parents[2]


def verify_bundle(bundle):
    manifest = json.loads((bundle / 'kernel-update.json').read_text())
    require(manifest.get('format') == 1 and manifest.get('kind') == 'kernel-update',
            'Not a kernel update bundle')
    require(manifest.get('device') == 'xiaomi,houji', 'Wrong device in manifest')
    release = manifest.get('kernel_release', '')
    require(RELEASE.fullmatch(release) is not None, 'Invalid kernel release')
    require(set(manifest.get('files', {})) == set(FILES), 'Unexpected file list')
    for name, info in manifest['files'].items():
        path = bundle / name
        require(path.is_file() and path.stat().st_size == info['size'], name + ' size mismatch')
        require(sha(path) == info['sha256'], name + ' checksum mismatch')
    return manifest


def read_archive(archive, release, destination=None):
    """Validate every member of the module archive; extract it if asked.

    Only plain files and directories inside modules/<release> are accepted:
    no links, devices, absolute paths or traversal. Returns the unpacked size.
    """
    prefix = 'modules/' + release
    unpacked = 0
    decompress = subprocess.Popen(['zstd', '-dc', '--', str(archive)], stdout=subprocess.PIPE)
    try:
        with tarfile.open(fileobj=decompress.stdout, mode='r|') as tar:
            for member in tar:
                name = member.name.rstrip('/')
                parts = Path(name).parts
                require(not name.startswith('/') and '..' not in parts, 'Unsafe path in archive: ' + name)
                require(name in ('modules', prefix) or name.startswith(prefix + '/'),
                        'Unexpected path in archive: ' + name)
                require(member.isreg() or member.isdir(), 'Unexpected entry type in archive: ' + name)
                unpacked += max(member.size, 0)
                require(unpacked <= MAX_UNPACKED, 'Archive unpacks to more than expected')
                if destination is not None:
                    tar.extract(member, destination, filter='data')
    finally:
        decompress.stdout.close()
        status = decompress.wait()
    require(status == 0, 'Module archive is corrupt')
    require(unpacked > 0, 'Module archive is empty')
    return unpacked


def normalize_modes(tree):
    """Directories 0755 and files 0644, whatever umask this ran under."""
    tree = Path(tree)
    for path in (tree, *tree.rglob('*')):
        os.chmod(path, 0o755 if path.is_dir() else 0o644)


def vermagic(release, name, base=None):
    command = ['modinfo', *(['-b', str(base)] if base else []), '-k', release, '-F', 'vermagic', name]
    return subprocess.check_output(command, text=True).split()[0]


def check_tree(release, directory, base=None):
    """Every module's vermagic matches and depmod's index resolves.

    A tree that is not installed yet is looked up through `base`, a directory
    holding lib/modules/<release>; modinfo cannot find it by release alone.
    """
    modules = sorted(Path(directory).rglob('*.ko'))
    require(modules, 'The module tree contains no modules')
    for start in range(0, len(modules), 200):
        out = subprocess.check_output(['modinfo', '-F', 'vermagic', *map(str, modules[start:start + 200])],
                                      text=True)
        require(all(line.split()[0] == release for line in out.splitlines() if line.strip()),
                'A module was not built for ' + release)
    for required in ('modules.dep', 'modules.dep.bin', 'modules.alias.bin'):
        require((Path(directory) / required).is_file(), 'depmod output is missing: ' + required)
    require(vermagic(release, 'houji-tcm-probe', base) == release, 'The module index does not resolve')


def clean_leftovers():
    """Remove what an interrupted earlier run left, and finish an interrupted swap."""
    for stale in Path(MODULES).parent.glob(STAGING + '*'):
        shutil.rmtree(stale, ignore_errors=True)
    for old in Path(MODULES).glob('*.replaced'):
        original = old.with_name(old.name[:-len('.replaced')])
        if original.exists():
            shutil.rmtree(old)          # the swap finished; this is the surplus old tree
        else:
            os.rename(old, original)    # stopped between the two renames: put it back


def install_tree(release, fresh, staging, replace):
    target = Path(MODULES) / release
    if not target.exists():
        os.rename(fresh, target)
        try:
            check_tree(release, target, base=system_root())
        except BaseException:
            shutil.rmtree(target, ignore_errors=True)
            raise
        return
    if tree_digest(target) == tree_digest(fresh):
        print('Modules for %s are already installed.' % release)
        return
    require(replace, '%s already holds different modules. Re-run with --replace to swap them.' % target)
    require(release != running_release(), 'Refusing to replace the modules of the running kernel (%s); '
            'use a different kernel release.' % release)
    require(not (Path(LOWER) / 'usr/lib/modules' / release).exists(),
            '%s belongs to the root image; use a different kernel release.' % release)
    old = target.with_name(release + '.replaced')
    if old.exists():
        shutil.rmtree(old)
    os.rename(target, old)
    try:
        os.rename(fresh, target)
        check_tree(release, target, base=system_root())
    except BaseException:
        shutil.rmtree(target, ignore_errors=True)
        os.rename(old, target)
        raise
    shutil.rmtree(old)


def write_receipt(path, receipt):
    """Create the receipt without following a link someone left in its place."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(json.dumps(receipt, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('bundle', type=Path)
    parser.add_argument('--serial', required=True,
                        help="This handset's fastboot serial, used only as a hash in the receipt")
    parser.add_argument('--replace', action='store_true',
                        help='Replace an installed module tree of the same release that differs '
                             '(never the running kernel\'s or the root image\'s)')
    parser.add_argument('--check-only', action='store_true',
                        help='Verify the bundle and archive without installing anything')
    args = parser.parse_args()
    bundle = args.bundle.resolve()
    require(os.getuid() == 0, 'Run as root on the installed phone')
    require(Path(MODEL).read_bytes().rstrip(b'\0') == b'Xiaomi 14', 'Wrong handset')
    require(os.path.ismount(DATA), 'An installed Armada system is required')
    manifest = verify_bundle(bundle)
    release = manifest['kernel_release']
    unpacked = read_archive(bundle / 'modules.tar.zst', release)
    print('Bundle verified: kernel %s, %d modules' % (release, manifest['module_count']))
    if args.check_only:
        print('Nothing installed.')
        return
    clean_leftovers()
    free = shutil.disk_usage(Path(MODULES).parent).free
    require(free >= unpacked + SPARE, 'Not enough free space: %d MB needed, %d MB free' % (
        (unpacked + SPARE) >> 20, free >> 20))
    staging = Path(MODULES).parent / (STAGING + release)
    staging.mkdir()
    try:
        read_archive(bundle / 'modules.tar.zst', release, staging / 'lib')
        fresh = staging / 'lib/modules' / release
        require(fresh.is_dir(), 'The archive has no module tree')
        normalize_modes(fresh)
        check_tree(release, fresh, base=staging)
        install_tree(release, fresh, staging, args.replace)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    receipt = {'kernel_release': release,
               'boot_sha256': manifest['files']['boot.img']['sha256'],
               'vendor_boot_sha256': manifest['files']['vendor_boot.img']['sha256'],
               'modules_sha256': manifest['files']['modules.tar.zst']['sha256'],
               'serial_sha256': hashlib.sha256(args.serial.encode()).hexdigest()}
    write_receipt(bundle / 'kernel-receipt.json', receipt)
    os.sync()
    print('Installed modules for %s. Copy kernel-receipt.json back to the host, then run '
          'flash-internal.py --kernel-only from bootloader fastboot.' % release)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError, subprocess.SubprocessError, tarfile.TarError) as error:
        raise SystemExit(str(error)) from None
