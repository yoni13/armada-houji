"""Shared, host-side build helpers. Inputs are pinned; outputs stay under work/."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import urllib.request

PORT = Path(__file__).resolve().parent
REPO = PORT.parents[1]


def kernel_source(work):
    """Use the pinned kernel version consistently for all external modules."""
    version = json.loads((PORT / 'sources.json').read_text())['linux']['version']
    package = (REPO / 'packages/kernel/BASE.env').read_text().strip()
    if package != 'VERSION=' + version:
        raise ValueError('Houji Linux pin does not match packages/kernel/BASE.env')
    return Path(work) / 'kernel' / ('linux-' + version)


# Out-of-tree modules built against the same kernel tree: (work subdirectory, file).
EXTRA_MODULES = (('touch-module', 'houji-tcm-probe.ko'),
                 ('nfc-module', 'houji-nfc-power.ko'),
                 ('gps/module', 'houji-modem-overlay.ko'))


def stage_modules(work, release, modules):
    """Fill `modules` (a lib/modules directory) with one kernel's modules plus the
    port's out-of-tree modules. Used by the root image and by kernel updates, so
    both ship identical trees. Running depmod afterwards is the caller's job."""
    work, modules = Path(work), Path(modules)
    if modules.exists():
        shutil.rmtree(modules)
    shutil.copytree(work / ('kernel/staging-' + release) / 'lib/modules', modules, symlinks=True)
    extra = modules / release / 'extra'
    extra.mkdir(exist_ok=True)
    for folder, name in EXTRA_MODULES:
        destination = extra / name
        destination.unlink(missing_ok=True)
        shutil.copyfile(work / folder / name, destination)
        destination.chmod(0o644)
    for link in (modules / release).glob('*'):
        if link.name in ('build', 'source') and link.is_symlink():
            link.unlink()


def run(*args, **kwargs):
    return subprocess.run([str(arg) for arg in args], check=True, **kwargs)


def erofs_worker_options(help_text):
    """Threaded compression needs erofs-utils 1.8; older mkfs.erofs rejects the option and works single-threaded."""
    return ['--workers=8'] if '--workers' in help_text else []


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def fetch(url, path, digest):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        temporary = path.with_name(path.name + '.part')
        with urllib.request.urlopen(url, timeout=120) as src, temporary.open('wb') as dst:
            import shutil
            shutil.copyfileobj(src, dst)
        if sha(temporary) != digest:
            temporary.unlink()
            raise ValueError('Download checksum mismatch: ' + path.name)
        temporary.replace(path)
    if sha(path) != digest:
        raise ValueError('Cached input checksum mismatch: ' + path.name)
    return path


def checkout(url, revision, tree, patch=None):
    tree = Path(tree)
    if not tree.exists():
        tree.parent.mkdir(parents=True, exist_ok=True)
        run('git', 'clone', '--no-checkout', url, tree)
        run('git', '-C', tree, 'fetch', 'origin', revision)
        run('git', '-C', tree, 'checkout', '--detach', revision)
    actual = subprocess.check_output(['git', '-C', str(tree), 'rev-parse', 'HEAD'], text=True).strip()
    if actual != revision:
        raise ValueError('Source revision mismatch: ' + tree.name)
    if patch:
        if subprocess.run(['git', '-C', str(tree), 'apply', '--check', str(patch)], capture_output=True).returncode == 0:
            run('git', '-C', tree, 'apply', patch)
        else:
            run('git', '-C', tree, 'apply', '--reverse', '--check', patch)
    return tree


def cross_file(work, sysroot):
    work.mkdir(parents=True, exist_ok=True)
    wrapper = work / 'pkg-config'
    wrapper.write_text('''#!/usr/bin/env python3
import os, sys
if '--variable=xwayland' in sys.argv and 'xwayland' in sys.argv:
    print('/usr/bin/Xwayland'); sys.exit(0)
if any('gobject-introspection' in a for a in sys.argv): sys.exit(1)
root = %r
os.environ['PKG_CONFIG_LIBDIR'] = root+'/usr/lib64/pkgconfig:'+root+'/usr/share/pkgconfig'
os.environ['PKG_CONFIG_SYSROOT_DIR'] = root
os.environ.pop('PKG_CONFIG_PATH', None)
os.execv('/usr/bin/pkg-config', ['pkg-config']+sys.argv[1:])
''' % str(sysroot))
    wrapper.chmod(0o755)
    links = ['-L'+str(sysroot/'usr/lib64'), '-Wl,-rpath-link,'+str(sysroot/'usr/lib64'), '-lmvec']
    config = work/'cross.ini'
    config.write_text(f'''[binaries]
c = 'aarch64-linux-gnu-gcc'
cpp = 'aarch64-linux-gnu-g++'
ar = 'aarch64-linux-gnu-ar'
strip = 'aarch64-linux-gnu-strip'
pkg-config = '{wrapper}'
[host_machine]
system = 'linux'
cpu_family = 'aarch64'
cpu = 'aarch64'
endian = 'little'
[properties]
needs_exe_wrapper = true
[built-in options]
c_args = ['-I{sysroot}/usr/include']
cpp_args = ['-I{sysroot}/usr/include']
c_link_args = {links!r}
cpp_link_args = {links!r}
''')
    return config
