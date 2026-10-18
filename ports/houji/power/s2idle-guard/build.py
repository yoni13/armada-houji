#!/usr/bin/env python3
"""Build the live guard against the exact, completed k20261010 release tree."""
import argparse
from pathlib import Path
import shutil
import subprocess

RELEASE='7.2.6-armada-houji-k20261010'
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('kernel',type=Path)
p.add_argument('output',type=Path)
a=p.parse_args();kernel=a.kernel.resolve();output=a.output.resolve()
if not (kernel/'Module.symvers').is_file():p.error('Completed matching kernel build required')
if (kernel/'include/config/kernel.release').read_text().strip()!=RELEASE:
    p.error('This live guard is only validated for '+RELEASE)
output.mkdir(parents=True,exist_ok=True)
for name in ('Makefile','houji-s2idle-guard.c'):
    shutil.copy2(Path(__file__).resolve().parent/name,output/name)
subprocess.run(['make','-C',str(kernel),'ARCH=arm64','CROSS_COMPILE=aarch64-linux-gnu-',
                'LOCALVERSION=-k20261010',f'M={output}','modules'],check=True)
vermagic=subprocess.check_output(['modinfo','-F','vermagic',str(output/'houji-s2idle-guard.ko')],text=True)
if vermagic.split()[0]!=RELEASE:raise RuntimeError('Wrong output module release')
print(output/'houji-s2idle-guard.ko')
