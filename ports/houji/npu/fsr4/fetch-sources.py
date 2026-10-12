#!/usr/bin/env python3
"""Fetch pinned, small model sources. QAIRT is installed separately."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('work', type=Path)
    args = parser.parse_args()
    pins = json.loads(Path(__file__).with_name('sources.json').read_text())
    hashes = {}
    for component in ('amd', 'hexagon_reference'):
        pin = pins[component]
        paths = list(pin['paths'])
        if component == 'amd':
            tree_path = args.work/'amd-tree.json'
            tree_path.parent.mkdir(parents=True, exist_ok=True)
            url = 'https://api.github.com/repos/{}/git/trees/{}?recursive=1'.format(pin['repository'],pin['revision'])
            subprocess.run(['curl','-fLsS','--retry','3','--max-time','120','--limit-rate','15000000',url,'-o',str(tree_path)],check=True)
            tree = json.loads(tree_path.read_text())
            if tree.get('truncated'):
                raise RuntimeError('Incomplete AMD source tree')
            prefixes = ('Kits/FidelityFX/upscalers/fsr4/dx12/',
                        'Kits/FidelityFX/upscalers/fsr4/include/',
                        'Kits/FidelityFX/api/internal/gpu/')
            paths += [x['path'] for x in tree['tree'] if x['type']=='blob' and x['path'].startswith(prefixes)]
        for name in sorted(set(paths)):
            destination = args.work/component/name
            destination.parent.mkdir(parents=True, exist_ok=True)
            url = 'https://raw.githubusercontent.com/{}/{}/{}'.format(
                pin['repository'], pin['revision'], name)
            temp = destination.with_name(destination.name+'.part')
            if not destination.exists():
                subprocess.run(['curl', '-fLsS', '--retry', '3', '--max-time', '120',
                                '--limit-rate', '15000000', '--proto', '=https',
                                url, '-o', str(temp)], check=True)
                temp.replace(destination)
            hashes[str(destination.relative_to(args.work))] = hashlib.sha256(destination.read_bytes()).hexdigest()
    (args.work/'source-hashes.json').write_text(json.dumps(hashes, indent=2)+'\n')
    print('Pinned model sources fetched; hashes saved to source-hashes.json')


if __name__ == '__main__':
    main()
