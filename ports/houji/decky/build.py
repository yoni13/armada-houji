#!/usr/bin/env python3
"""Build the Houji Settings Decky plugin the way Armada builds its own plugins:
npm ci from the committed lockfile and rollup, in the same Node 22 image.

The output directory holds exactly what goes into
/usr/share/decky-plugins/houji-settings in the image.
"""
import argparse
from pathlib import Path
import shutil
import subprocess

PLUGIN = Path(__file__).resolve().parent / 'houji-settings'
IMAGE = 'docker.io/library/node:22-slim'
# @decky/rollup reads plugin.json for the plugin's name.
SOURCES = ['plugin.json', 'package.json', 'package-lock.json', 'rollup.config.js', 'tsconfig.json', 'src']
SHIPPED = ['plugin.json', 'package.json', 'main.py']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--podman-root', type=Path)
    parser.add_argument('--podman-runroot', type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    work = output.with_name(output.name + '-build')
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    for name in SOURCES:
        source = PLUGIN / name
        (shutil.copytree if source.is_dir() else shutil.copy2)(source, work / name)
    podman = ['podman']
    if args.podman_root:
        podman += ['--root', str(args.podman_root.resolve()), '--runroot', str(args.podman_runroot.resolve()),
                   '--storage-driver', 'overlay']
    subprocess.run([*podman, 'run', '--rm', '-v', f'{work}:/build:Z', '-w', '/build', IMAGE,
                    'sh', '-c', 'npm ci --no-audit --no-fund && npm run build'], check=True)
    shutil.rmtree(output, ignore_errors=True)
    (output / 'dist').mkdir(parents=True)
    for name in SHIPPED:
        shutil.copy2(PLUGIN / name, output / name)
    shutil.copy2(work / 'dist/index.js', output / 'dist/index.js')
    shutil.rmtree(work)
    print('Houji Settings plugin:', output)


if __name__ == '__main__':
    main()
