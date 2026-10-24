#!/usr/bin/env python3
"""Check the built dispatcher exposes actual x64 hybrid patch thunks.

PE machine 0x8664 is not sufficient to identify ARM64EC. Verify CHPE metadata,
all six public dispatcher symbols, their x64 ranges, and ARM64EC jump targets.
This is specific to the pinned llvm-mingw build's thunk encoding.
"""
import argparse
from pathlib import Path
import struct
import subprocess


def verify(path, nm):
    data = Path(path).read_bytes()
    u16 = lambda off: struct.unpack_from('<H', data, off)[0]
    u32 = lambda off: struct.unpack_from('<I', data, off)[0]
    u64 = lambda off: struct.unpack_from('<Q', data, off)[0]
    pe = u32(60)
    assert data[:2] == b'MZ' and data[pe:pe + 4] == b'PE\0\0'
    opt = pe + 24
    assert u16(opt) == 0x20b, 'Expected PE32+'
    base = u64(opt + 24)
    sections = []
    for i in range(u16(pe + 6)):
        off = opt + u16(pe + 20) + i * 40
        size, rva, raw_size, raw = struct.unpack_from('<IIII', data, off + 8)
        sections.append((rva, max(size, raw_size), raw, raw_size))

    def offset(rva, size):
        for start, _, raw, raw_size in sections:
            if start <= rva and rva + size <= start + raw_size:
                return raw + rva - start
        raise AssertionError(f'RVA {rva:#x} has no file data')

    load_rva, load_size = struct.unpack_from('<II', data, opt + 112 + 10 * 8)
    assert load_size >= 208, 'Missing load configuration'
    chpe = u64(offset(load_rva, 208) + 200)
    assert chpe, 'Pure x64 DLL: no CHPE metadata'
    meta = offset(chpe - base, 12)
    version, table, count = struct.unpack_from('<III', data, meta)
    assert version == 1
    ranges = []
    for i in range(count):
        start, size = struct.unpack_from('<II', data, offset(table + i * 8, 8))
        ranges.append((start & ~3, size, start & 3))

    def code_kind(rva):
        return next((kind for start, size, kind in ranges if start <= rva < start + size), None)

    symbols = {}
    for line in subprocess.check_output([nm, '--defined-only', str(path)], text=True).splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3:
            symbols[parts[2]] = int(parts[0], 16)
    for method in ('Present', 'Present1', 'ResizeBuffers', 'ResizeBuffers1',
                   'ResizeTarget', 'SetFullscreenState'):
        prefix = f'_ZN4dxvk23DxgiSwapChainDispatcher{len(method)}{method}E'
        matches = [(name, addr) for name, addr in symbols.items() if name.startswith(prefix)]
        assert len(matches) == 1, f'{method}: missing/ambiguous public symbol'
        name, address = matches[0]
        rva = address - base
        code = offset(rva, 16)
        assert code_kind(rva) == 2, f'{method}: public entry is not x64 code'
        assert data[code:code + 10] == bytes.fromhex('48 8b c4 48 89 58 20 55 5d e9'), method
        dest = rva + 14 + struct.unpack_from('<i', data, code + 10)[0]
        assert code_kind(dest) == 1, f'{method}: target is not ARM64EC code'
        assert symbols['#' + name + '$hp_target'] == base + dest, method
        print(f'{method}: x64 thunk {rva:#x} -> ARM64EC body {dest:#x}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dll', type=Path)
    parser.add_argument('--nm', default='llvm-nm')
    args = parser.parse_args()
    verify(args.dll, args.nm)
