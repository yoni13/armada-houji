#!/usr/bin/env python3
"""Build opt-in GPS tools and the matching modem overlay. Does not start GPS."""
import argparse
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from buildlib import checkout, run, cross_file
ASSETS=Path(__file__).resolve().parent
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--work',type=Path,required=True)
p.add_argument('--sysroot',type=Path,required=True)
p.add_argument('--kernel',type=Path,required=True)
a=p.parse_args();out=a.work.resolve();out.mkdir(parents=True,exist_ok=True);sysroot=a.sysroot.resolve();kernel=a.kernel.resolve()
pins=json.loads((ASSETS/'sources.json').read_text())
for name,revision in pins.items():
    checkout('https://github.com/linux-msm/'+name+'.git',revision,out/'sources'/name,
             ASSETS/'tqftpserv-mbnconfig.patch' if name=='tqftpserv' else None)
qrtr=out/'sources/qrtr';rmtfs=out/'sources/rmtfs';tftp=out/'sources/tqftpserv'
cc=['aarch64-linux-gnu-gcc','-O2','-Wall','-I'+str(qrtr/'include'),'-I'+str(sysroot/'usr/include'),
    '-Wl,-rpath-link,'+str(sysroot/'usr/lib64')]
shared=[qrtr/'lib'/n for n in ['qrtr.c','qmi.c','logging.c']]
run(*cc,*shared,*(rmtfs/n for n in ['qmi_rmtfs.c','rmtfs.c','rproc.c','sharedmem.c','storage.c','util.c']),
    sysroot/'usr/lib64/libudev.so.1','-lpthread','-o',out/'rmtfs')
run(*cc,*shared,qrtr/'src/lookup.c','-o',out/'qrtr-lookup')
run(*cc,'-D_GNU_SOURCE',tftp/'tqftpserv.c',tftp/'translate.c',*shared,'-o',out/'tqftpserv')
cross_file(out,sysroot)
flags=shlex.split(subprocess.check_output([str(out/'pkg-config'),'--cflags','--libs','qmi-glib','qrtr-glib','gio-unix-2.0'],text=True))
run(*cc,'-Werror=implicit-function-declaration',ASSETS/'houji-loc-test.c','-o',out/'houji-loc-test',*flags)
module=out/'module';module.mkdir(exist_ok=True)
for name in ['Makefile','houji-modem-overlay.c']:shutil.copy2(ASSETS/name,module/name)
run('dtc','-@','-I','dts','-O','dtb','-o',module/'modem.dtbo',ASSETS/'modem.dtso')
blob=(module/'modem.dtbo').read_bytes()
(module/'modem-overlay.h').write_text('static const unsigned char modem_dtbo[] = {\n'+','.join(f'0x{x:02x}' for x in blob)+'\n};\n')
run('make','-C',kernel,'ARCH=arm64','CROSS_COMPILE=aarch64-linux-gnu-','M='+str(module),'modules')
