#!/usr/bin/env python3
"""Build userdata.img for a fresh installation from a bundle's rootfs.erofs.

userdata.img is only a sparse ext4 filesystem holding rootfs.erofs, so it is
made here, next to the root image, instead of being downloaded as a second
7 GB file. Run this in the bundle directory before `flash-internal.py
--erase-userdata`. Preserving updates do not need it.

Needs mke2fs, debugfs, e2fsck (e2fsprogs) and img2simg (android-sdk-libsparse-utils
or android-tools), plus about the size of the root image in free scratch space.
Nothing here communicates with a device.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile

TOOLS = ('mke2fs', 'debugfs', 'e2fsck', 'img2simg')
GIB = 1 << 30


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def initial_size(rootfs_size):
    """Size of the new filesystem: the root image plus room, at least 12 GiB."""
    return max(12 * GIB, math.ceil((rootfs_size + 2 * GIB) / GIB) * GIB)


def run(*args):
    subprocess.run([str(a) for a in args], check=True)


def build_userdata(bundle, layout, runner=run, sidecar=True, scratch_dir=None):
    """Write bundle/userdata.img (and userdata.json) from bundle/rootfs.erofs."""
    bundle = Path(bundle)
    rootfs = bundle / 'rootfs.erofs'
    missing = [t for t in TOOLS if shutil.which(t) is None]
    if missing:
        raise ValueError('Missing tools: ' + ', '.join(missing))
    if not rootfs.is_file() or rootfs.stat().st_size != layout['rootfs_size']:
        raise ValueError('rootfs.erofs is missing or its size does not match images.json')
    if sha(rootfs) != layout['rootfs_sha256']:
        raise ValueError('rootfs.erofs does not match its checksum in images.json')
    size = layout.get('initial_size') or initial_size(layout['rootfs_size'])
    if size < layout['rootfs_size'] + (64 << 20):
        raise ValueError('initial_size leaves no room for the root image')
    data = bundle / 'data-root'
    final = bundle / 'userdata.img'
    partial = bundle / 'userdata.img.partial'
    for path in (data, partial):
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    final.unlink(missing_ok=True)
    (bundle / 'userdata.json').unlink(missing_ok=True)
    scratch_root = Path(scratch_dir or tempfile.gettempdir())
    free = shutil.disk_usage(scratch_root).free
    if free < layout['rootfs_size'] + (512 << 20):
        raise ValueError('%s has %.1f GB free but about %.1f GB is needed for the temporary filesystem; '
                         'pass --scratch DIR on a larger disk' % (
                             scratch_root, free / 1e9, (layout['rootfs_size'] + (512 << 20)) / 1e9))
    scratch = tempfile.TemporaryDirectory(prefix='houji-userdata-', dir=scratch_dir)
    try:
        base = data / layout['directory']
        base.mkdir(parents=True)
        try:
            (base / 'rootfs.erofs').hardlink_to(rootfs)
        except OSError:
            shutil.copyfile(rootfs, base / 'rootfs.erofs')
        (base / 'build-id').write_text(layout['build_id'] + '\n')
        (base / 'upper').mkdir()
        (base / 'work').mkdir()
        # A separate scratch filesystem avoids keeping two expanded images on
        # the output volume while converting to Android sparse format.
        raw = Path(scratch.name) / 'userdata.ext4'
        with raw.open('wb') as stream:
            stream.truncate(size)
        runner('mke2fs', '-t', 'ext4', '-F', '-b', '4096', '-m', '0', '-L', layout['filesystem_label'],
               '-U', layout['filesystem_uuid'], '-E', 'root_owner=0:0,lazy_itable_init=0,lazy_journal_init=0',
               '-d', data, raw)
        # mke2fs -d preserves input ownership; these build-generated files are root-owned.
        for path in sorted(data.rglob('*')):
            relative = '/' + str(path.relative_to(data))
            for field in ('uid', 'gid'):
                runner('debugfs', '-w', '-R', 'set_inode_field %s %s 0' % (relative, field), raw)
        runner('e2fsck', '-fn', raw)
        runner('img2simg', raw, partial)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    finally:
        scratch.cleanup()
        shutil.rmtree(data, ignore_errors=True)
    partial.replace(final)
    if sidecar:
        # Written last: its presence means the image above is complete.
        info = {'size': final.stat().st_size, 'sha256': sha(final), 'initial_size': size,
                'build_id': layout['build_id'], 'rootfs_sha256': layout['rootfs_sha256'],
                'filesystem_uuid': layout['filesystem_uuid']}
        (bundle / 'userdata.json').write_text(json.dumps(info, indent=2) + '\n')
    return final


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('bundle', type=Path, nargs='?', default=Path('.'),
                        help='bundle directory holding images.json and rootfs.erofs (default: here)')
    parser.add_argument('--scratch', type=Path,
                        help='directory for the temporary expanded filesystem (needs about the root image size)')
    args = parser.parse_args()
    # Let a termination request unwind through the cleanup above, so no 8 GB scratch file is left behind.
    signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(128 + signum))
    bundle = args.bundle.resolve()
    manifest = json.loads((bundle / 'images.json').read_text())
    if manifest.get('device') != 'xiaomi,houji':
        raise ValueError('Wrong device in manifest')
    layout = manifest['layout']
    if args.scratch is not None:
        args.scratch.mkdir(parents=True, exist_ok=True)
    print('Building userdata.img (%.1f GB root image) ...' % (layout['rootfs_size'] / 1e9), flush=True)
    image = build_userdata(bundle, layout, scratch_dir=args.scratch)
    print('Wrote %s (%.1f GB sparse). Next: flash-internal.py --erase-userdata.' % (
        image, image.stat().st_size / 1e9))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as error:
        raise SystemExit(str(error)) from None
