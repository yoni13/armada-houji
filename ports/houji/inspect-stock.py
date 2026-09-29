#!/usr/bin/env python3
"""Extract selected stock images without running Xiaomi's flashing scripts."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import struct
import tarfile

WANTED = {'boot.img', 'init_boot.img', 'vendor_boot.img', 'dtbo.img',
          'vbmeta.img', 'vbmeta_system.img', 'vbmeta_vendor.img',
          'NON-HLOS.bin', 'super.img'}
REQUIRED = {'boot.img', 'init_boot.img', 'vendor_boot.img', 'dtbo.img',
            'vbmeta.img', 'NON-HLOS.bin', 'super.img'}


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def header(data):
    if data[:8] == b'ANDROID!':
        version = struct.unpack_from('<I', data, 40)[0]
        if version != 4:
            raise ValueError(f'expected Android boot v4, got {version}')
        kernel_size, ramdisk_size, os_version, header_size = struct.unpack_from('<4I', data, 8)
        return dict(kind='boot', header_version=version, kernel_size=kernel_size,
                    ramdisk_size=ramdisk_size, os_version=os_version,
                    header_size=header_size, page_size=4096,
                    cmdline=data[44:1580].split(b'\0')[0].decode())
    if data[:8] == b'VNDRBOOT':
        version, page, kernel_addr, ramdisk_addr, ramdisk_size = struct.unpack_from('<5I', data, 8)
        if version != 4 or page not in (2048, 4096, 8192, 16384):
            raise ValueError('expected vendor boot v4 with a valid page size')
        header_size, dtb_size, dtb_addr = struct.unpack_from('<IIQ', data, 2096)
        table_size, table_entries, table_entry_size, bootconfig_size = struct.unpack_from('<4I', data, 2112)
        return dict(kind='vendor_boot', header_version=version, page_size=page,
                    kernel_addr=kernel_addr, ramdisk_addr=ramdisk_addr,
                    ramdisk_size=ramdisk_size, dtb_size=dtb_size, dtb_addr=dtb_addr,
                    tags_addr=struct.unpack_from('<I', data, 2076)[0],
                    header_size=header_size, table_size=table_size,
                    table_entries=table_entries, table_entry_size=table_entry_size,
                    bootconfig_size=bootconfig_size,
                    cmdline=data[28:2076].split(b'\0')[0].decode())
    raise ValueError('unrecognized boot image magic')


def split_dtbs(data, out):
    offset = 0
    count = 0
    while offset < len(data):
        if not any(data[offset:]):
            break
        magic, size = struct.unpack_from('>II', data, offset)
        if magic != 0xd00dfeed or size < 40 or offset + size > len(data):
            raise ValueError(f'invalid DTB at offset {offset}')
        (out / f'stock-{count}.dtb').write_bytes(data[offset:offset + size])
        offset += size
        count += 1
    return count


def inspect(archive, out):
    archive_digest = digest(archive)
    expected = json.loads((Path(__file__).with_name('sources.json')).read_text())['firmware']['sha256']
    if archive_digest != expected:
        raise ValueError('firmware archive does not match the pinned SHA-256')
    out.mkdir(parents=True, exist_ok=True)
    found = set()
    with tarfile.open(archive, 'r|gz') as tar:
        for entry in tar:
            parts = PurePosixPath(entry.name).parts
            if len(parts) != 3 or parts[1] != 'images' or parts[2] not in WANTED:
                continue
            if not entry.isfile() or parts[2] in found:
                raise ValueError(f'non-regular or duplicate image: {entry.name}')
            if not parts[0].startswith('houji_tw_global_images_OS3.0.303.0.WNCTWXM_'):
                raise ValueError('unexpected firmware product/version')
            found.add(parts[2])
            with tar.extractfile(entry) as source, (out / parts[2]).open('wb') as dest:
                shutil.copyfileobj(source, dest)
    if REQUIRED - found:
        raise ValueError(f'missing stock images: {sorted(REQUIRED - found)}')
    report = {'archive': archive.name, 'sha256': archive_digest, 'images': {}}
    for name in sorted(found):
        path = out / name
        item = dict(size=path.stat().st_size, sha256=digest(path))
        if name in ('boot.img', 'init_boot.img', 'vendor_boot.img'):
            with path.open('rb') as stream:
                item.update(header(stream.read(4096)))
        report['images'][name] = item
    vendor = report['images']['vendor_boot.img']
    page = vendor['page_size']
    align = lambda size: (size + page - 1) // page * page
    offset = align(vendor['header_size']) + align(vendor['ramdisk_size'])
    with (out / 'vendor_boot.img').open('rb') as stream:
        stream.seek(offset)
        dtbs = stream.read(vendor['dtb_size'])
    report['dtb_count'] = split_dtbs(dtbs, out)
    report['dtbs'] = {f'stock-{index}.dtb': digest(out / f'stock-{index}.dtb')
                      for index in range(report['dtb_count'])}
    raw = (out / 'dtbo.img').read_bytes()
    magic, total, hsize, esize, count, offset, _, version = struct.unpack_from('>8I', raw)
    if (magic != 0xd7b7ab1e or hsize != 32 or esize != 32 or count != 1 or
            version != 0 or offset < 32 or offset + 32 > total or total > len(raw)):
        raise ValueError('expected the pinned firmware single-entry DTBO table')
    size, payload = struct.unpack_from('>II', raw, offset)
    if payload < offset + esize or payload + size > total:
        raise ValueError('stock DTBO payload outside table bounds')
    overlay = out / 'stock-overlay.dtbo'
    overlay.write_bytes(raw[payload:payload + size])
    report['overlay_sha256'] = digest(overlay)
    (out / 'stock-report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('out', type=Path)
    args = parser.parse_args()
    inspect(args.archive, args.out)
