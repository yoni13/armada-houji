#!/usr/bin/env python3
"""Package a kernel-only update: a new kernel and DTB plus its matching modules.

A kernel update replaces boot_b (kernel) and vendor_boot_b (device tree) and adds
one module tree to the installed system. It reuses the installed init_boot,
dtbo, vbmeta and userdata, so it is about 100 MB instead of the 7 GB root image.
It cannot carry userspace changes: those live in the read-only root image.

The packaging step refuses a kernel that the installed initramfs could not boot
(boot-critical drivers must be built in) or whose modules do not match it.
Nothing here communicates with a device.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from buildlib import PORT, kernel_source, run, sha, stage_modules

# The initramfs mounts userdata, the root image and the firmware partitions
# without loading any module, so each of these must be built into the kernel.
BOOT_CRITICAL = (
    # What init mounts and runs: filesystems, the loop device, partition tables, the ramdisk.
    'EXT4_FS', 'EROFS_FS', 'EROFS_FS_ZIP', 'OVERLAY_FS', 'DEVTMPFS', 'BLK_DEV_LOOP', 'VFAT_FS',
    'TMPFS', 'PROC_FS', 'SYSFS', 'EFI_PARTITION', 'NLS_ISO8859_1', 'NLS_CODEPAGE_437',
    'BINFMT_SCRIPT', 'BINFMT_ELF', 'BLK_DEV_INITRD', 'RD_GZIP', 'VT', 'VT_CONSOLE',
    # The UFS storage stack that holds userdata, and what it needs to power up.
    'SCSI', 'BLK_DEV_SD', 'SCSI_UFSHCD', 'SCSI_UFSHCD_PLATFORM', 'SCSI_UFS_QCOM',
    'PHY_QCOM_QMP', 'PHY_QCOM_QMP_UFS', 'ARCH_QCOM', 'ARM_GIC_V3', 'COMMON_CLK_QCOM', 'SM_GCC_8650',
    'PINCTRL_MSM', 'PINCTRL_SM8650', 'INTERCONNECT_QCOM_RPMH', 'INTERCONNECT_QCOM_SM8650',
    'QCOM_COMMAND_DB', 'QCOM_RPMH', 'QCOM_RPMHPD', 'REGULATOR_QCOM_RPMH', 'ARM_SMMU')
KERNEL_IMAGES = ('boot.img', 'vendor_boot.img')


def boot_critical_problems(config_text):
    """Names of the boot-critical options that are not built in."""
    enabled = {m[1] for m in re.finditer(r'^CONFIG_([A-Z0-9_]+)=y$', config_text, re.M)}
    return [name for name in BOOT_CRITICAL if name not in enabled]


def image_is_release(image, release):
    """True when the Image's version banner names exactly this release (not a longer one)."""
    return b'Linux version ' + release.encode() + b' (' in image


def prepare_output(output):
    """Empty `output`, but only if it is missing, empty or an earlier bundle."""
    if output.exists():
        if any(output.iterdir()) and not (output / 'kernel-update.json').is_file():
            raise ValueError('%s exists and is not a kernel update bundle; choose another --output' % output)
        shutil.rmtree(output)
    output.mkdir(parents=True)


def module_files(tree):
    return sorted(p for p in Path(tree).rglob('*.ko') if p.is_file())


def vermagics(paths):
    """First word of each module's vermagic, in order."""
    result = []
    for start in range(0, len(paths), 200):
        out = subprocess.check_output(['modinfo', '-F', 'vermagic', *map(str, paths[start:start + 200])],
                                      text=True)
        result += [line.split()[0] for line in out.splitlines() if line.strip()]
    if len(result) != len(paths):
        raise ValueError('modinfo did not report a vermagic for every module')
    return result


def check_modules(tree, release):
    modules = module_files(tree)
    if not modules:
        raise ValueError('The module tree contains no modules')
    wrong = [p.name for p, v in zip(modules, vermagics(modules)) if v != release]
    if wrong:
        raise ValueError('Modules not built for %s: %s' % (release, ', '.join(wrong[:5])))
    for required in ('modules.dep', 'modules.dep.bin', 'modules.alias.bin'):
        if not (Path(tree) / required).is_file():
            raise ValueError('depmod output is missing: ' + required)
    return len(modules)


def make_archive(parent, archive):
    """Deterministic tar.zst of parent/modules."""
    subprocess.run(['tar', '--sort=name', '--mtime=@0', '--owner=0', '--group=0', '--numeric-owner',
                    '--use-compress-program=zstd -19 -T0', '-cf', str(archive),
                    '-C', str(parent), 'modules'], check=True)


def pack_boot_images(kernel_dir, scratch, output):
    """boot.img and vendor_boot.img through the same packer as a full build."""
    def load(name, filename):
        spec = importlib.util.spec_from_file_location(name, PORT / filename)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    assemble = load('assemble', 'install/assemble.py')
    pack = load('pack', 'pack-images.py')
    work = scratch / 'pack'
    work.mkdir()
    shutil.copy2(kernel_dir / 'Image', work / 'Image')
    shutil.copy2(kernel_dir / 'sm8650-xiaomi-houji.dtb', work / 'sm8650-xiaomi-houji.dtb')
    # pack() always writes init_boot too. The installed one is kept, so this
    # placeholder ramdisk is built only to satisfy it and is not shipped.
    (work / 'initramfs.cpio.gz').write_bytes(assemble.cpio([]))
    pack.pack(work, PORT / 'firmware/stock')
    for name in KERNEL_IMAGES:
        shutil.copy2(work / name, output / name)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--work', type=Path, required=True,
                        help='build work directory (kernel tree, kernel/staging-*, module builds)')
    parser.add_argument('--kernel', type=Path, required=True,
                        help='build-kernel.sh output directory (Image, DTB, kernel.config)')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source-commit', default='', help='recorded in the manifest')
    args = parser.parse_args()
    work, kernel, output = args.work.resolve(), args.kernel.resolve(), args.output.resolve()

    release = (kernel_source(work) / 'include/config/kernel.release').read_text().strip()
    problems = boot_critical_problems((kernel / 'kernel.config').read_text())
    if problems:
        raise ValueError('The installed initramfs could not boot this kernel; not built in: ' +
                         ', '.join('CONFIG_' + p for p in problems))
    image = (kernel / 'Image').read_bytes()
    if not image_is_release(image, release):
        raise ValueError('The kernel Image is not release ' + release)
    prepare_output(output)
    with tempfile.TemporaryDirectory(prefix='houji-kernel-update-') as scratch:
        scratch = Path(scratch)
        stage_modules(work, release, scratch / 'lib/modules')
        run('depmod', '-b', scratch, release)
        count = check_modules(scratch / 'lib/modules' / release, release)
        (scratch / 'archive').mkdir()
        shutil.move(scratch / 'lib/modules', scratch / 'archive/modules')
        make_archive(scratch / 'archive', output / 'modules.tar.zst')
        # The phone applies stricter rules than tar does. Hold the archive to them here,
        # with the installer's own reader, so a bad one fails in CI rather than on the phone.
        installer = importlib.util.spec_from_file_location('installer', PORT / 'install/install-kernel-update.py')
        reader = importlib.util.module_from_spec(installer)
        installer.loader.exec_module(reader)
        reader.read_archive(output / 'modules.tar.zst', release)
        pack_boot_images(kernel, scratch, output)
    files = {name: {'size': (output / name).stat().st_size, 'sha256': sha(output / name)}
             for name in (*KERNEL_IMAGES, 'modules.tar.zst')}
    manifest = {'format': 1, 'kind': 'kernel-update', 'device': 'xiaomi,houji',
                'kernel_release': release, 'kernel_image_sha256': sha(kernel / 'Image'),
                'module_count': count, 'source_commit': args.source_commit,
                'files': files,
                'keeps': ['init_boot.img', 'dtbo.img', 'vbmeta.img', 'userdata']}
    (output / 'kernel-update.json').write_text(json.dumps(manifest, indent=2) + '\n')
    (output / 'SHA256SUMS').write_text(''.join(v['sha256'] + '  ' + n + '\n' for n, v in files.items()))
    for name in ('install-kernel-update.py', 'flash-internal.py'):
        shutil.copy2(PORT / 'install' / name, output / name)
    print('Kernel update %s: %d modules, %.0f MB' % (
        release, count, sum(v['size'] for v in files.values()) / 1e6))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        raise SystemExit(str(error)) from None
