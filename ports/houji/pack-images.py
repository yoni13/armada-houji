#!/usr/bin/env python3
"""Package experimental v4 boot images. Never communicates with a device."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

PORT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('stock', PORT / 'inspect-stock.py')
stock = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stock)
spec = importlib.util.spec_from_file_location('reservations', PORT / 'check-reservations.py')
reservations = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reservations)


def boot_version_args(report):
    firmware = json.loads((PORT / 'sources.json').read_text())['firmware']
    metadata = firmware['bootloader_compatibility']
    if (report['sha256'] != firmware['sha256'] or
            report['images']['boot.img']['sha256'] != metadata['stock_boot_sha256']):
        raise ValueError('boot version metadata requires the pinned stock boot image')
    # Stock ABL falls back to this field when our unsigned boot image has no
    # AVB properties. A zero version invalidates the slot even when unlocked.
    return ['--os_version', metadata['os_version'],
            '--os_patch_level', metadata['os_patch_level']]


def pack(work, stock_dir):
    report = json.loads((stock_dir / 'stock-report.json').read_text())
    manifest = json.loads((stock_dir.parent / 'firmware.json').read_text())
    for name, expected in manifest['files'].items():
        if stock.digest(stock_dir.parent / name) != expected:
            raise ValueError('Bundled firmware changed: ' + name)
    kernel = work / 'Image'
    dtb = work / 'sm8650-xiaomi-houji.dtb'
    ramdisk = work / 'initramfs.cpio.gz'
    with kernel.open('rb') as stream:
        if stream.read(64)[56:60] != b'ARM\x64':
            raise ValueError('expected an uncompressed ARM64 Linux Image')
    if subprocess.check_output(['fdtget', str(dtb), '/', 'compatible'], text=True).strip() != 'xiaomi,houji qcom,sm8650':
        raise ValueError('not the Houji Linux DTB')
    subprocess.run(['fdtget', '-p', str(dtb), '/__symbols__'], check=True,
                   stdout=subprocess.DEVNULL)
    timer = subprocess.check_output(
        ['fdtget', str(dtb), '/__symbols__', 'arch_timer'], text=True).strip()
    if subprocess.check_output(
            ['fdtget', str(dtb), timer, 'compatible'], text=True).strip() != 'arm,armv8-timer':
        raise ValueError('firmware arch_timer symbol must resolve to the ARM timer')
    subprocess.run(['fdtget', str(dtb), timer, 'phandle'], check=True,
                   stdout=subprocess.DEVNULL)
    version_args = boot_version_args(report)
    if not ramdisk.is_file() or not ramdisk.stat().st_size:
        raise ValueError('missing initramfs')
    checked = []
    overlay = stock_dir / 'stock-overlay.dtbo'
    if stock.digest(overlay) != report['overlay_sha256']:
        raise ValueError('extracted stock DTBO changed')
    for index in range(report['dtb_count']):
        reference = stock_dir / f'stock-{index}.dtb'
        if stock.digest(reference) != report['dtbs'][reference.name]:
            raise ValueError(f'extracted stock DTB changed: {reference.name}')
        ids = subprocess.check_output(['fdtget', '-t', 'x', str(reference), '/', 'qcom,msm-id'], text=True).split()
        if any(int(value, 16) == 557 for value in ids[::2]):
            reservations.check(dtb, reference)
            reservations.check_gpios(dtb, reference)
            with tempfile.TemporaryDirectory() as temp:
                merged = Path(temp) / 'merged.dtb'
                subprocess.run(['fdtoverlay', '-i', str(reference), '-o', str(merged),
                                str(overlay)], check=True)
                reservations.check(dtb, merged)
            checked.append(reference.name)
    if not checked:
        raise ValueError('stock archive has no SM8650 reference DTB')
    mkbootimg = PORT.parents[1] / 'build_files/vendor/mkbootimg/mkbootimg.py'
    vendor = report['images']['vendor_boot.img']
    cmdline = ('rdinit=/init console=tty0 consoleblank=0 fbcon=font:TER16x32 loglevel=4 '
               'clk_ignore_unused pd_ignore_unused regulator_ignore_unused panic=10')

    def run(*args):
        subprocess.run([sys.executable, str(mkbootimg), '--header_version', '4',
                        *map(str, args)], check=True)

    # Stage everything, then publish the manifest last. An old manifest must
    # never describe a partially rewritten image set.
    (work / 'images.json').unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(dir=work) as temp:
        output = Path(temp)
        run('--kernel', kernel, '--cmdline', cmdline, *version_args,
            '--output', output / 'boot.img')
        run('--ramdisk', ramdisk, '--output', output / 'init_boot.img')
        # No Android vendor init or Android modules belong in a mainline boot.
        run('--dtb', dtb, '--pagesize', vendor['page_size'], '--base', '0',
            '--kernel_offset', vendor['kernel_addr'],
            '--ramdisk_offset', vendor['ramdisk_addr'],
            '--dtb_offset', vendor['dtb_addr'], '--tags_offset', vendor['tags_addr'],
            '--vendor_boot', output / 'vendor_boot.img')

        overlay = output / 'empty.dtbo'
        subprocess.run(['dtc', '-@', '-I', 'dts', '-O', 'dtb', '-o', str(overlay),
                        str(PORT / 'empty-overlay.dts')], check=True)
        subprocess.run(['fdtget', '-p', str(overlay), '/__fixups__'], check=True,
                       stdout=subprocess.DEVNULL)
        dt = overlay.read_bytes()
        original = (stock_dir / 'dtbo.img').read_bytes()
        magic, total, hsize, esize, count, entry_offset, page, version = struct.unpack_from('>8I', original)
        if (magic != 0xd7b7ab1e or hsize != 32 or esize != 32 or
                version != 0 or not 0 < count <= 1024 or
                total > len(original) or entry_offset < hsize or
                entry_offset + count * esize > total):
            raise ValueError('unsupported stock DTBO table')
        # Retain every stock selector (id/rev/custom fields), replacing only
        # payloads with a no-op. Never apply Android overlays to a Linux DTB.
        payload_offset = 32 + count * 32
        entries = []
        for index in range(count):
            fields = struct.unpack_from('>8I', original, entry_offset + index * 32)
            entries.append(struct.pack('>8I', len(dt), payload_offset, *fields[2:]))
        image = (struct.pack('>8I', magic, payload_offset + len(dt), 32, 32,
                             count, 32, page, 0) + b''.join(entries) + dt)
        (output / 'dtbo.img').write_bytes(image)
        sizes = {'boot.img': 100663296, 'init_boot.img': 8388608,
                 'vendor_boot.img': 100663296, 'dtbo.img': 25165824}
        manifest = dict(status='built-not-hardware-tested', device='xiaomi,houji',
                        stock_archive_sha256=report['sha256'],
                        includes_armada_userspace=True,
                        bootloader_compatibility=json.loads((PORT / 'sources.json').read_text())['firmware']['bootloader_compatibility'],
                        display=dict(panel='N3 42-0d-0a', mode='1200x2670@120',
                                     hardware_validated=False),
                        reservation_checks=checked,
                        inputs={p.name: stock.digest(p) for p in [kernel, dtb, ramdisk]}, images={})
        for name, limit in sizes.items():
            path = output / name
            # The supplied stock DTBO file is smaller than the older Android
            # tree's limit. Use the smaller stock image length conservatively.
            limit = min(limit, report['images'][name]['size'])
            if path.stat().st_size > limit:
                raise ValueError(f'{name} exceeds the Houji partition size')
            manifest['images'][name] = dict(size=path.stat().st_size, sha256=stock.digest(path))
        for name in sizes:
            (output / name).replace(work / name)
    (work / 'images.json').write_text(json.dumps(manifest, indent=2) + '\n')
    (work / 'SHA256SUMS').write_text(''.join(
        f'{item["sha256"]}  {name}\n' for name, item in manifest['images'].items()))
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('work', type=Path, help='directory containing Image, DTB and initramfs')
    parser.add_argument('stock', type=Path, help='inspect-stock.py output directory')
    args = parser.parse_args()
    pack(args.work.resolve(), args.stock.resolve())
