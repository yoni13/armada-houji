#!/usr/bin/env python3
"""Require Linux to reserve all fixed-address regions in a stock SoC DTB.

This checks static ranges only. Bootloader fixups and runtime overlays still
need comparison with a live device tree during hardware bring-up.
"""
import argparse
from pathlib import Path
import subprocess


def fdtget(path, *args):
    return subprocess.check_output(['fdtget', str(path), *args], text=True,
                                   stderr=subprocess.DEVNULL).split()


def reservations(path):
    address_cells = int(fdtget(path, '/reserved-memory', '#address-cells')[0])
    size_cells = int(fdtget(path, '/reserved-memory', '#size-cells')[0])
    width = address_cells + size_cells
    regions = []
    nodes = subprocess.check_output(['fdtget', '-l', str(path), '/reserved-memory'], text=True).split()
    for node in nodes:
        try:
            cells = [int(v, 16) for v in fdtget(path, '-t', 'x', f'/reserved-memory/{node}', 'reg')]
        except subprocess.CalledProcessError:
            continue  # A size/alloc-ranges-only node has no fixed address.
        if len(cells) % width:
            raise ValueError(f'invalid reg property in {node}')
        for offset in range(0, len(cells), width):
            start = size = 0
            for value in cells[offset:offset+address_cells]:
                start = start << 32 | value
            for value in cells[offset+address_cells:offset+width]:
                size = size << 32 | value
            if size:
                regions.append((start, start + size, node))
    return sorted(regions)


def check(linux, stock):
    linux_regions = reservations(linux)
    missing = []
    for start, end, name in reservations(stock):
        cursor = start
        for left, right, _ in linux_regions:
            if left <= cursor < right:
                cursor = right
            if cursor >= end:
                break
        if cursor < end:
            missing.append(f'{name}: {start:#x}..{end:#x}')
    if missing:
        raise ValueError('stock fixed regions not covered by Linux:\n' + '\n'.join(missing))
    print(f'{stock.name}: all fixed reserved-memory ranges covered')


def check_gpios(linux, stock):
    """Exclude every stock-reserved TLMM line before mainline GPIO probing."""
    expected = {int(v, 16) for v in fdtget(
        stock, '-t', 'x', '/soc/pinctrl@f000000', 'qcom,gpios-reserved')}
    cells = [int(v, 16) for v in fdtget(
        linux, '-t', 'x', '/soc@0/pinctrl@f100000', 'gpio-reserved-ranges')]
    if len(cells) % 2 or any(count == 0 for count in cells[1::2]):
        raise ValueError('invalid TLMM gpio-reserved-ranges')
    missing = [pin for pin in sorted(expected)
               if not any(start <= pin < start + count
                          for start, count in zip(cells[::2], cells[1::2]))]
    if missing:
        raise ValueError(f'stock reserved GPIOs not excluded by Linux: {missing}')
    print(f'{stock.name}: all {len(expected)} stock reserved GPIOs excluded')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('linux', type=Path)
    parser.add_argument('stock', type=Path, nargs='+')
    args = parser.parse_args()
    for stock in args.stock:
        check(args.linux, stock)
